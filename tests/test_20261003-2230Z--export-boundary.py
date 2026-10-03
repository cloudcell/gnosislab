"""Boundary and emission tests for gnosislab_export.

The package is deliberately outside src/: it reads the servers' stores
read-only and never crosses the boundary in either direction. These
tests enforce the boundary mechanically (import scan), prove the
read-only claim (mtime/journal), and check the RO-Crate shape against
a minimal fixture store.
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gnosislab_export.common.snapshot import load_snapshot  # noqa: E402
from gnosislab_export.common.validation import ValidationError, check  # noqa: E402
from gnosislab_export.ro_crate.exporter import export_rocrate  # noqa: E402

SERVER_PKGS = sorted((ROOT / "src").glob("ml_*_mcp"))
EXPORT_PKG = ROOT / "gnosislab_export"

# ---------------------------------------------------------------- boundary


def test_servers_never_reference_export_package():
    for pkg in SERVER_PKGS:
        for f in pkg.rglob("*.py"):
            assert "gnosislab_export" not in f.read_text(), f


def test_export_package_never_imports_server_internals():
    for f in EXPORT_PKG.rglob("*.py"):
        text = f.read_text()
        assert not re.search(r"(?:import|from)\s+ml_\w+_mcp", text), f


# ---------------------------------------------------------------- fixtures

EPI_SCHEMA = """
CREATE TABLE programmes (
    id TEXT PRIMARY KEY, goal TEXT NOT NULL, constraints_json TEXT NOT NULL,
    allowed_variables_json TEXT NOT NULL, budget_max_trials INTEGER NOT NULL,
    budget_max_wall_time_hours REAL NOT NULL,
    metric_direction TEXT NOT NULL DEFAULT 'maximize',
    candidate_version_id TEXT, investigation_id TEXT,
    status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE hypotheses (
    id TEXT PRIMARY KEY, programme_id TEXT NOT NULL, statement TEXT NOT NULL,
    failure_criterion TEXT NOT NULL, variables_involved_json TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL,
    abandon_rationale TEXT, abandoned_by TEXT, abandoned_at TEXT);
CREATE TABLE trials (
    id TEXT PRIMARY KEY, programme_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL, config_json TEXT NOT NULL,
    bundle_id TEXT, status TEXT NOT NULL, duration_seconds REAL,
    artifact_path TEXT, executor_output_json TEXT,
    started_at TEXT, finished_at TEXT, retry_reason TEXT,
    created_at TEXT NOT NULL);
CREATE TABLE observations (
    id TEXT PRIMARY KEY, trial_id TEXT NOT NULL, metrics_json TEXT NOT NULL,
    variance_json TEXT NOT NULL, spatiotemporal_region TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE beliefs (
    id TEXT PRIMARY KEY, programme_id TEXT NOT NULL, state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE conclusions (
    id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL,
    programme_id TEXT NOT NULL, verdict TEXT NOT NULL,
    evidence_ref TEXT NOT NULL, evidence_summary TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE bundles (
    id TEXT PRIMARY KEY, trial_id TEXT NOT NULL, code_ref TEXT NOT NULL,
    env_ref TEXT NOT NULL, seeds_json TEXT NOT NULL, splits_json TEXT NOT NULL,
    data_refs_json TEXT, baseline_ref TEXT, created_at TEXT NOT NULL);
CREATE TABLE data_refs (
    id TEXT PRIMARY KEY, split TEXT NOT NULL, regime TEXT NOT NULL,
    generator_code_ref TEXT, generator_seed INTEGER,
    generator_params_json TEXT, source_uri TEXT, content_hash TEXT,
    version TEXT, capture_window_start TEXT, capture_window_end TEXT,
    capture_source_metadata_json TEXT, schema_hash TEXT, size_bytes INTEGER,
    num_samples INTEGER, storage_uri TEXT,
    reproducibility_risk TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL);
CREATE TABLE code_snippets (
    code_hash TEXT PRIMARY KEY, code_text TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'python', captured_at TEXT NOT NULL,
    original_path TEXT, size_bytes INTEGER NOT NULL);
CREATE TABLE archive_registry (
    archive_id TEXT PRIMARY KEY, archive_path TEXT NOT NULL,
    created_at TEXT NOT NULL, sealed_at TEXT, batch_size INTEGER NOT NULL,
    programme_count INTEGER NOT NULL DEFAULT 0, sealed INTEGER NOT NULL DEFAULT 0,
    archive_hash TEXT, archive_size_bytes INTEGER);
CREATE TABLE archive_entries (
    id TEXT PRIMARY KEY, archive_id TEXT NOT NULL, programme_id TEXT NOT NULL,
    programme_goal TEXT NOT NULL, archived_at TEXT NOT NULL,
    row_count INTEGER NOT NULL, verified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE artifact_files (
    content_hash TEXT PRIMARY KEY, filename TEXT NOT NULL,
    content BLOB NOT NULL, content_type TEXT, size_bytes INTEGER NOT NULL,
    compressed_size_bytes INTEGER NOT NULL, captured_at TEXT NOT NULL,
    original_path TEXT);
CREATE TABLE trial_artifacts (
    id TEXT PRIMARY KEY, trial_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    filename TEXT NOT NULL, artifact_type TEXT, created_at TEXT NOT NULL);
"""

ANA_SCHEMA = """
CREATE TABLE claims (
    id TEXT PRIMARY KEY, content TEXT NOT NULL, type TEXT NOT NULL,
    confidence REAL, valid_from TEXT NOT NULL, valid_until TEXT,
    supersedes_id TEXT, content_hash TEXT NOT NULL UNIQUE,
    source_id TEXT, confidence_basis TEXT, confidence_computation TEXT,
    created_at TEXT NOT NULL);
CREATE TABLE claim_edges (
    id TEXT PRIMARY KEY, from_claim TEXT NOT NULL, to_ref TEXT NOT NULL,
    ref_type TEXT NOT NULL, relation TEXT NOT NULL, source_id TEXT,
    created_at TEXT NOT NULL);
"""


def _seed_episteme(db: Path, hyp_statement: str = "depth helps") -> None:
    conn = sqlite3.connect(db)
    conn.executescript(EPI_SCHEMA)
    conn.executescript(
        f"""
        INSERT INTO programmes VALUES
          ('prog-t1','find best depth','{{}}','[]',10,4.0,'maximize',
           NULL,NULL,'active','2026-10-01T00:00:00Z');
        INSERT INTO hypotheses VALUES
          ('hyp-t1','prog-t1','{hyp_statement}','acc does not improve',
           '[]','active','2026-10-01T00:01:00Z',NULL,NULL,NULL);
        INSERT INTO code_snippets VALUES
          ('aabbcc','x=1','python','2026-10-01T00:02:00Z',NULL,3);
        INSERT INTO trials VALUES
          ('trl-t1','prog-t1','hyp-t1','{{"depth":4}}','bnd-t1','completed',
           12.5,NULL,NULL,'2026-10-01T00:03:00Z','2026-10-01T00:03:12Z',
           NULL,'2026-10-01T00:02:30Z');
        INSERT INTO bundles VALUES
          ('bnd-t1','trl-t1','aabbcc','env-1','[42]','["train"]',NULL,NULL,
           '2026-10-01T00:02:45Z');
        INSERT INTO observations VALUES
          ('obs-t1','trl-t1','{{"acc":0.91}}','{{}}','now',
           '2026-10-01T00:03:12Z');
        INSERT INTO artifact_files VALUES
          ('ffff','plot.png',X'89504E47','image/png',4,4,
           '2026-10-01T00:03:15Z',NULL);
        INSERT INTO trial_artifacts VALUES
          ('ta-t1','trl-t1','ffff','plot.png','figure','2026-10-01T00:03:16Z');
        INSERT INTO conclusions VALUES
          ('cnc-t1','hyp-t1','prog-t1','supported','trl-t1',
           'acc improved over baseline','2026-10-01T00:04:00Z');
        """
    )
    conn.commit()
    conn.close()


def _seed_anamnesis(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(ANA_SCHEMA)
    conn.executescript(
        """
        INSERT INTO claims VALUES
          ('clm-t1','depth 4 improves accuracy','finding',0.9,
           '2026-10-01T00:05:00Z',NULL,NULL,'hh-t1','src',
           NULL,NULL,'2026-10-01T00:05:00Z');
        INSERT INTO claim_edges VALUES
          ('edg-t1','clm-t1','trl-t1','trial','derived_from','src',
           '2026-10-01T00:05:01Z');
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def stores(tmp_path):
    epi = tmp_path / "state.db"
    ana = tmp_path / "memory.db"
    _seed_episteme(epi)
    _seed_anamnesis(ana)
    return epi, ana


# ---------------------------------------------------------------- snapshot


def test_snapshot_scoped_to_programme(stores):
    epi, ana = stores
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    assert len(snap.programmes) == 1
    assert len(snap.trials) == 1
    assert snap.trials[0].config == {"depth": 4}
    assert len(snap.observations) == 1
    assert len(snap.code_snippets) == 1
    assert len(snap.artifacts) == 1
    assert len(snap.claims) == 1
    assert len(snap.claim_edges) == 1


def test_snapshot_read_only_no_side_effects(stores, tmp_path):
    epi, ana = stores
    before = epi.stat().st_mtime_ns
    load_snapshot(epi, ana, programme_id="prog-t1")
    assert epi.stat().st_mtime_ns == before
    assert not (tmp_path / "state.db-wal").exists()
    assert not (tmp_path / "state.db-journal").exists()


def test_scope_required(stores):
    epi, ana = stores
    with pytest.raises(ValueError):
        load_snapshot(epi, ana)


def test_secret_in_state_aborts_export(tmp_path, stores):
    epi, ana = stores
    conn = sqlite3.connect(epi)
    conn.execute(
        "UPDATE hypotheses SET statement=? WHERE id='hyp-t1'",
        ("token is ML_EPISTEME_INGEST_TOKEN=abc123",),
    )
    conn.commit()
    conn.close()
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    with pytest.raises(ValidationError):
        check(snap)


# ---------------------------------------------------------------- crate


def test_rocrate_structure(stores, tmp_path):
    epi, ana = stores
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")

    descriptor = json.loads((crate / "ro-crate-metadata.json").read_text())
    assert descriptor["@context"] == "https://w3id.org/ro/crate/1.3/context"
    graph = {e["@id"]: e for e in descriptor["@graph"]}

    assert "ro-crate-metadata.json" in graph
    assert graph["./"]["@type"] == "Dataset"
    assert "#gnosislab" in graph

    # trial → CreateAction with instrument/object/result
    action = graph["#trial/trl-t1"]
    assert action["@type"] == "CreateAction"
    assert action["agent"] == {"@id": "#gnosislab"}
    assert action["actionStatus"]["@id"].endswith("CompletedActionStatus")

    # code, artifact, results payload written + referenced
    code_ids = [i for i in graph if i.startswith("payload/code/")]
    assert code_ids and (crate / code_ids[0]).read_text() == "x=1"
    art_ids = [i for i in graph if i.startswith("payload/artifacts/")]
    assert art_ids and (crate / art_ids[0]).read_bytes() == b"\x89PNG"
    assert (crate / "payload/results/trl-t1.json").exists()

    # claim + isBasedOn → the trial entity
    claim = graph["#claim/clm-t1"]
    assert claim["isBasedOn"] == {"@id": "#trial/trl-t1"}

    # conclusion → AssessAction → verdict CreativeWork
    assert graph["#conclusion/cnc-t1"]["@type"] == "AssessAction"
    assert "#verdict/cnc-t1" in graph

    manifest = json.loads((crate / "payload/MANIFEST.json").read_text())
    assert manifest["counts"]["trials"] == 1
    assert manifest["integrity_issues"] == []


def test_rocrate_zip(stores, tmp_path):
    epi, ana = stores
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    zpath = export_rocrate(snap, tmp_path / "crate.zip", zip_crate=True)
    assert zpath.suffix == ".zip" and zpath.exists()
    import zipfile

    names = zipfile.ZipFile(zpath).namelist()
    assert "ro-crate-metadata.json" in names
    assert any(n.startswith("payload/code/") for n in names)


def test_host_paths_redacted_in_metadata(stores, tmp_path):
    epi, ana = stores
    conn = sqlite3.connect(epi)
    conn.execute(
        "UPDATE trials SET config_json=? WHERE id='trl-t1'",
        ('{"data": "/data/research/train.csv"}',),
    )
    conn.commit()
    conn.close()
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    descriptor = (crate / "ro-crate-metadata.json").read_text()
    assert "/data/research" not in descriptor
    assert "<host-path>" in descriptor
    # the scrub is recorded by field name — never the path itself
    manifest = json.loads((crate / "payload/MANIFEST.json").read_text())
    assert manifest["integrity_issues"] == [
        "absolute path in trial.config (redacted)"
    ]
    assert "/data" not in (crate / "payload/MANIFEST.json").read_text()


def test_external_edge_stub_visible(stores, tmp_path):
    epi, ana = stores
    conn = sqlite3.connect(ana)
    conn.execute(
        "INSERT INTO claim_edges VALUES "
        "('edg-x','clm-t1','ext-9','evidence','derived_from','src',"
        "'2026-10-01T00:05:02Z')"
    )
    conn.commit()
    conn.close()
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    graph = {
        e["@id"]: e
        for e in json.loads(
            (crate / "ro-crate-metadata.json").read_text()
        )["@graph"]
    }
    assert "#external/ext-9" in graph  # dangling ref visible, not dropped
    manifest = json.loads((crate / "payload/MANIFEST.json").read_text())
    assert any("ext-9" in i for i in manifest["integrity_issues"])
