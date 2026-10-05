"""Read lab state into a canonical, format-agnostic snapshot.

The ONLY module in gnosislab_export that knows the server schemas.
Everything downstream sees frozen dataclasses, never sqlite rows.

Stores are opened strictly read-only:

    file:<path>?mode=ro&immutable=1

``immutable=1`` additionally tells sqlite the file cannot change, so
no journal/WAL side-effects occur even under a concurrent writer.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_EPISTEME_DB = "~/.ml-episteme/state.db"
DEFAULT_ANAMNESIS_DB = "~/.ml-anamnesis/memory.db"

# Tables snapshot.py depends on. The boundary test + a startup probe
# fail loudly when a required table is missing (schema-drift tripwire).
REQUIRED_EPISTEME_TABLES = (
    "programmes",
    "hypotheses",
    "trials",
    "observations",
    "conclusions",
    "bundles",
    "data_refs",
    "code_snippets",
    "artifact_files",
    "trial_artifacts",
)
REQUIRED_ANAMNESIS_TABLES = ("claims", "claim_edges")


@dataclass(frozen=True)
class Programme:
    id: str
    goal: str
    status: str
    created_at: str
    budget_max_trials: int
    metric_direction: str


@dataclass(frozen=True)
class Hypothesis:
    id: str
    programme_id: str
    statement: str
    failure_criterion: str
    status: str
    created_at: str


@dataclass(frozen=True)
class Trial:
    id: str
    programme_id: str
    hypothesis_id: str
    config: dict
    bundle_id: str | None
    status: str
    duration_seconds: float | None
    started_at: str | None
    finished_at: str | None
    created_at: str


@dataclass(frozen=True)
class Observation:
    id: str
    trial_id: str
    metrics: dict
    variance: dict
    created_at: str


@dataclass(frozen=True)
class Conclusion:
    id: str
    hypothesis_id: str
    programme_id: str
    verdict: str
    evidence_ref: str
    evidence_summary: str
    created_at: str


@dataclass(frozen=True)
class Bundle:
    id: str
    trial_id: str
    code_ref: str
    env_ref: str
    seeds: list
    splits: list
    created_at: str


@dataclass(frozen=True)
class DataRef:
    id: str
    split: str
    regime: str
    source_uri: str | None
    content_hash: str | None
    version: str | None
    size_bytes: int | None
    created_at: str


@dataclass(frozen=True)
class CodeSnippet:
    code_hash: str
    code_text: str
    language: str
    captured_at: str
    size_bytes: int


@dataclass(frozen=True)
class Artifact:
    content_hash: str
    filename: str
    content: bytes
    content_type: str | None
    size_bytes: int
    captured_at: str


@dataclass(frozen=True)
class Claim:
    id: str
    content: str
    type: str
    confidence: float | None
    content_hash: str
    created_at: str


@dataclass(frozen=True)
class ClaimEdge:
    id: str
    from_claim: str
    to_ref: str
    ref_type: str
    relation: str


@dataclass(frozen=True)
class Snapshot:
    programmes: tuple[Programme, ...] = ()
    hypotheses: tuple[Hypothesis, ...] = ()
    trials: tuple[Trial, ...] = ()
    observations: tuple[Observation, ...] = ()
    conclusions: tuple[Conclusion, ...] = ()
    bundles: tuple[Bundle, ...] = ()
    data_refs: tuple[DataRef, ...] = ()
    code_snippets: tuple[CodeSnippet, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    trial_artifacts: tuple[tuple[str, str], ...] = ()  # (trial_id, content_hash)
    claims: tuple[Claim, ...] = ()
    claim_edges: tuple[ClaimEdge, ...] = ()
    captured_at: str = ""
    source_dbs: tuple[str, ...] = ()

    def entity_ids(self) -> set[str]:
        ids: set[str] = set()
        for group in (
            self.programmes,
            self.hypotheses,
            self.trials,
            self.observations,
            self.conclusions,
            self.bundles,
            self.data_refs,
            self.claims,
        ):
            ids.update(e.id for e in group)
        ids.update(c.code_hash for c in self.code_snippets)
        ids.update(a.content_hash for a in self.artifacts)
        return ids


def _json(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def open_ro(path: str | Path) -> sqlite3.Connection:
    """Open a store strictly read-only. Fails loudly when absent."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"state DB not found: {p}")
    uri = f"file:{p}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _require_tables(conn: sqlite3.Connection, tables: tuple[str, ...], db: str) -> None:
    have = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = [t for t in tables if t not in have]
    if missing:
        raise RuntimeError(
            f"schema drift: {db} is missing tables {missing} — "
            "update common/snapshot.py to match the new schema"
        )


def load_snapshot(
    episteme_db: str | Path = DEFAULT_EPISTEME_DB,
    anamnesis_db: str | Path | None = DEFAULT_ANAMNESIS_DB,
    programme_id: str | None = None,
    trial_id: str | None = None,
    full: bool = False,
) -> Snapshot:
    """Read state into a Snapshot.

    Scope: --trial pulls one trial + its closure; --programme pulls a
    programme + everything under it; --full exports the whole store.
    Anamnesis claims are pulled one hop: claims whose edges reference
    an exported entity id, plus the edges themselves.
    """
    epi_path = Path(episteme_db).expanduser()
    epi = open_ro(epi_path)
    try:
        return _load(
            epi, epi_path,
            Path(anamnesis_db).expanduser() if anamnesis_db else None,
            programme_id, trial_id, full,
        )
    finally:
        epi.close()


def _load(epi, epi_path: Path, ana_path: Path | None,
          programme_id: str | None, trial_id: str | None,
          full: bool) -> Snapshot:
    """The load_snapshot body over an already-open episteme
    connection — the caller owns closing it."""
    _require_tables(epi, REQUIRED_EPISTEME_TABLES, str(epi_path))

    prog_ids: set[str] = set()
    trial_ids: set[str] = set()

    if trial_id:
        rows = epi.execute("SELECT * FROM trials WHERE id=?", (trial_id,)).fetchall()
        if not rows:
            raise ValueError(f"trial not found: {trial_id}")
        prog_ids = {r["programme_id"] for r in rows}
        trial_ids = {r["id"] for r in rows}
    elif programme_id:
        if not epi.execute(
            "SELECT 1 FROM programmes WHERE id=?", (programme_id,)
        ).fetchone():
            raise ValueError(f"programme not found: {programme_id}")
        prog_ids = {programme_id}
        trial_ids = {
            r[0]
            for r in epi.execute(
                "SELECT id FROM trials WHERE programme_id=?", (programme_id,)
            )
        }
    elif full:
        prog_ids = {r[0] for r in epi.execute("SELECT id FROM programmes")}
        trial_ids = {r[0] for r in epi.execute("SELECT id FROM trials")}
    else:
        raise ValueError("scope required: --programme, --trial, or --full")

    def ph(ids: set[str]) -> str:
        return ",".join("?" * len(ids)) or "''"

    p_ids = sorted(prog_ids)
    t_ids = sorted(trial_ids)

    programmes = tuple(
        Programme(
            id=r["id"], goal=r["goal"], status=r["status"],
            created_at=r["created_at"],
            budget_max_trials=r["budget_max_trials"],
            metric_direction=r["metric_direction"],
        )
        for r in epi.execute(
            f"SELECT * FROM programmes WHERE id IN ({ph(prog_ids)})", p_ids
        )
    )
    hypotheses = tuple(
        Hypothesis(
            id=r["id"], programme_id=r["programme_id"],
            statement=r["statement"],
            failure_criterion=r["failure_criterion"],
            status=r["status"], created_at=r["created_at"],
        )
        for r in epi.execute(
            f"SELECT * FROM hypotheses WHERE programme_id IN ({ph(prog_ids)})",
            p_ids,
        )
    )
    trials = tuple(
        Trial(
            id=r["id"], programme_id=r["programme_id"],
            hypothesis_id=r["hypothesis_id"],
            config=_json(r["config_json"], {}),
            bundle_id=r["bundle_id"], status=r["status"],
            duration_seconds=r["duration_seconds"],
            started_at=r["started_at"], finished_at=r["finished_at"],
            created_at=r["created_at"],
        )
        for r in epi.execute(
            f"SELECT * FROM trials WHERE id IN ({ph(trial_ids)})", t_ids
        )
    )
    observations = tuple(
        Observation(
            id=r["id"], trial_id=r["trial_id"],
            metrics=_json(r["metrics_json"], {}),
            variance=_json(r["variance_json"], {}),
            created_at=r["created_at"],
        )
        for r in epi.execute(
            f"SELECT * FROM observations WHERE trial_id IN ({ph(trial_ids)})",
            t_ids,
        )
    )
    conclusions = tuple(
        Conclusion(
            id=r["id"], hypothesis_id=r["hypothesis_id"],
            programme_id=r["programme_id"], verdict=r["verdict"],
            evidence_ref=r["evidence_ref"],
            evidence_summary=r["evidence_summary"],
            created_at=r["created_at"],
        )
        for r in epi.execute(
            f"SELECT * FROM conclusions WHERE programme_id IN ({ph(prog_ids)})",
            p_ids,
        )
    )
    bundles = tuple(
        Bundle(
            id=r["id"], trial_id=r["trial_id"], code_ref=r["code_ref"],
            env_ref=r["env_ref"], seeds=_json(r["seeds_json"], []),
            splits=_json(r["splits_json"], []), created_at=r["created_at"],
        )
        for r in epi.execute(
            f"SELECT * FROM bundles WHERE trial_id IN ({ph(trial_ids)})", t_ids
        )
    )

    code_refs = sorted({b.code_ref for b in bundles})
    code_snippets = tuple(
        CodeSnippet(
            code_hash=r["code_hash"], code_text=r["code_text"],
            language=r["language"], captured_at=r["captured_at"],
            size_bytes=r["size_bytes"],
        )
        for r in epi.execute(
            f"SELECT * FROM code_snippets WHERE code_hash IN ({ph(set(code_refs))})",
            code_refs,
        )
    )

    ta_rows = epi.execute(
        f"SELECT trial_id, content_hash FROM trial_artifacts "
        f"WHERE trial_id IN ({ph(trial_ids)})",
        t_ids,
    ).fetchall()
    trial_artifacts = tuple((r["trial_id"], r["content_hash"]) for r in ta_rows)
    art_hashes = sorted({r["content_hash"] for r in ta_rows})
    artifacts = tuple(
        Artifact(
            content_hash=r["content_hash"], filename=r["filename"],
            content=r["content"], content_type=r["content_type"],
            size_bytes=r["size_bytes"], captured_at=r["captured_at"],
        )
        for r in epi.execute(
            f"SELECT * FROM artifact_files WHERE content_hash IN ({ph(set(art_hashes))})",
            art_hashes,
        )
    )

    # data_refs referenced by bundles' data_refs_json
    dr_ids: set[str] = set()
    for b_row in epi.execute(
        f"SELECT data_refs_json FROM bundles WHERE trial_id IN ({ph(trial_ids)})",
        t_ids,
    ):
        for ref in _json(b_row["data_refs_json"], []) or []:
            if isinstance(ref, str):
                dr_ids.add(ref)
            elif isinstance(ref, dict) and ref.get("id"):
                dr_ids.add(ref["id"])
    data_refs = tuple(
        DataRef(
            id=r["id"], split=r["split"], regime=r["regime"],
            source_uri=r["source_uri"], content_hash=r["content_hash"],
            version=r["version"], size_bytes=r["size_bytes"],
            created_at=r["created_at"],
        )
        for r in epi.execute(
            f"SELECT * FROM data_refs WHERE id IN ({ph(dr_ids)})",
            sorted(dr_ids),
        )
    )

    # Anamnesis: one-hop claims — claims whose edges point at an
    # exported entity id, plus their edges.
    claims: tuple[Claim, ...] = ()
    claim_edges: tuple[ClaimEdge, ...] = ()
    source_dbs = [epi_path.name]
    if ana_path and ana_path.is_file():
        ana = open_ro(ana_path)
        try:
            _require_tables(ana, REQUIRED_ANAMNESIS_TABLES, str(ana_path))
            exported_ids = set(prog_ids) | set(trial_ids) | {
                h.id for h in hypotheses
            } | {o.id for o in observations} | {c.id for c in conclusions}
            edge_rows = ana.execute(
                f"SELECT * FROM claim_edges WHERE to_ref IN ({ph(exported_ids)})",
                sorted(exported_ids),
            ).fetchall()
            claim_ids = {r["from_claim"] for r in edge_rows}
            # also pull edges FROM those claims to anything (full edge set
            # for included claims — validation marks external refs)
            edge_rows += ana.execute(
                f"SELECT * FROM claim_edges WHERE from_claim IN ({ph(claim_ids)})",
                sorted(claim_ids),
            ).fetchall()
            seen = set()
            claim_edges = tuple(
                e for e in (
                    ClaimEdge(
                        id=r["id"], from_claim=r["from_claim"],
                        to_ref=r["to_ref"], ref_type=r["ref_type"],
                        relation=r["relation"],
                    )
                    for r in edge_rows
                )
                if not (e.id in seen or seen.add(e.id))
            )
            claims = tuple(
                Claim(
                    id=r["id"], content=r["content"], type=r["type"],
                    confidence=r["confidence"], content_hash=r["content_hash"],
                    created_at=r["created_at"],
                )
                for r in ana.execute(
                    f"SELECT * FROM claims WHERE id IN ({ph(claim_ids)})",
                    sorted(claim_ids),
                )
            )
        finally:
            ana.close()
        source_dbs.append(ana_path.name)

    return Snapshot(
        programmes=programmes,
        hypotheses=hypotheses,
        trials=trials,
        observations=observations,
        conclusions=conclusions,
        bundles=bundles,
        data_refs=data_refs,
        code_snippets=code_snippets,
        artifacts=artifacts,
        trial_artifacts=trial_artifacts,
        claims=claims,
        claim_edges=claim_edges,
        captured_at=datetime.now(timezone.utc).isoformat(),
        source_dbs=tuple(source_dbs),
    )
