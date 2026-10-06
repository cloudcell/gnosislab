"""Emit a Snapshot as an RO-Crate 1.3 directory or zip.

Layout:

    <out>/
      ro-crate-metadata.json
      payload/
        code/<sha256>.<ext>
        artifacts/<sha256>_<filename>
        results/<trial-id>.json
        MANIFEST.json          (gnosislab manifest — not part of the spec,
                                kept inside payload so the crate stays
                                spec-clean at the top level)
"""

from __future__ import annotations

import json
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path

from ..common.payload import write_payload
from ..common.snapshot import Snapshot
from ..common.validation import check as validate_snapshot
from . import mapping as m
from .profile import CONFORMS_TO, GNOSISLAB_AGENT

CONTEXT = "https://w3id.org/ro/crate/1.3/context"


def _edge_targets(snapshot: Snapshot) -> dict[str, list[dict]]:
    """Resolve claim-edge to_refs to graph nodes.

    A ref pointing into the snapshot links to the mapped entity; a ref
    pointing outside becomes a stub contextual entity so the edge is
    visible (incomplete provenance stays visible, never dropped).
    """
    ids = snapshot.entity_ids()
    by_from: dict[str, list[dict]] = defaultdict(list)
    for e in snapshot.claim_edges:
        if e.to_ref in ids or e.ref_type == "claim":
            node = {"@id": f"#claim/{e.to_ref}"} if e.ref_type == "claim" else None
            if node is None:
                node = _entity_node_id(e.to_ref, snapshot)
            by_from[e.from_claim].append(node)
        else:
            by_from[e.from_claim].append({"@id": f"#external/{e.to_ref}"})
    return by_from


def _entity_node_id(ref: str, snapshot: Snapshot) -> dict:
    for coll, prefix in (
        (snapshot.trials, "#trial/"),
        (snapshot.observations, "#observation/"),
        (snapshot.programmes, "#programme/"),
        (snapshot.hypotheses, "#hypothesis/"),
        (snapshot.conclusions, "#conclusion/"),
        (snapshot.data_refs, "#dataref/"),
    ):
        if any(x.id == ref for x in coll):
            return {"@id": f"{prefix}{ref}"}
    return {"@id": f"#external/{ref}"}


def build_graph(
    snapshot: Snapshot, issues: list[str], license_uri: str | None = None
) -> list[dict]:
    graph: list[dict] = []

    payload_ids: list[str] = []
    for s in snapshot.code_snippets:
        payload_ids.append(m.code_path(s.code_hash, s.language))
    for a in snapshot.artifacts:
        payload_ids.append(m.artifact_path(a.content_hash, a.filename))
    result_trials = {o.trial_id for o in snapshot.observations}
    for tid in sorted(result_trials):
        payload_ids.append(m.results_path(tid))
    payload_ids.append("payload/MANIFEST.json")

    # descriptor + root dataset (spec-required first two entities)
    graph.append(
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "conformsTo": [{"@id": c} for c in CONFORMS_TO],
            "about": {"@id": "./"},
            # the descriptor is generated metadata — always CC0 so a
            # published crate's description is unambiguously reusable;
            # the dataset's own license stays user-declared (see
            # export_rocrate's license_uri)
            "license": {"@id": "https://spdx.org/licenses/CC0-1.0"},
        }
    )
    root = {
        "@id": "./",
        "@type": "Dataset",
        "name": "gnosislab provenance export",
        "description": (
            "Hypothesis → experiment → evidence → conclusion record "
            "exported from a gnosislab deployment."
        ),
        "dateCreated": snapshot.captured_at,
        "hasPart": [{"@id": p} for p in payload_ids],
        "mentions": [{"@id": "#gnosislab"}]
        + [{"@id": f"#programme/{p.id}"} for p in snapshot.programmes],
        "publisher": {"@id": "#gnosislab"},
    }
    if license_uri:
        # payload is the user's experiment record — the exporter only
        # stamps a data license when the user declares one
        root["license"] = {"@id": license_uri}
    if issues:
        root["additionalProperty"] = [
            {
                "@type": "PropertyValue",
                "name": "export_integrity_note",
                "value": issue,
            }
            for issue in issues
        ]
    graph.append(root)

    graph.append(GNOSISLAB_AGENT)
    graph += [m.map_programme(p) for p in snapshot.programmes]
    graph += [m.map_hypothesis(h) for h in snapshot.hypotheses]
    graph += [
        m.map_code(s.code_hash, s.language, s.captured_at, s.size_bytes)
        for s in snapshot.code_snippets
    ]
    graph += [m.map_artifact(a) for a in snapshot.artifacts]
    for tid in sorted(result_trials):
        obs = [o for o in snapshot.observations if o.trial_id == tid]
        trial = next(t for t in snapshot.trials if t.id == tid)
        graph.append(m.map_results_file(trial, obs))

    code_by_hash = {s.code_hash: s for s in snapshot.code_snippets}
    arts_by_trial: dict[str, list[dict]] = defaultdict(list)
    art_entities = {a.content_hash: m.map_artifact(a) for a in snapshot.artifacts}
    for trial_id, chash in snapshot.trial_artifacts:
        if chash in art_entities:
            arts_by_trial[trial_id].append(art_entities[chash])
    datarefs_by_trial: dict[str, list[str]] = defaultdict(list)
    dr_ids = {d.id for d in snapshot.data_refs}
    for b in snapshot.bundles:
        datarefs_by_trial[b.trial_id] += [
            d for d in dr_ids  # bundle-level; fine-grained split refs
        ] if dr_ids else []
    for t in snapshot.trials:
        code_ref = next(
            (b.code_ref for b in snapshot.bundles if b.trial_id == t.id), None
        )
        lang = (
            code_by_hash[code_ref].language
            if code_ref in code_by_hash
            else "python"
        )
        graph.append(
            m.map_trial(
                t,
                code_ref if code_ref in code_by_hash else None,
                lang,
                arts_by_trial.get(t.id, []),
                t.id in result_trials,
                datarefs_by_trial.get(t.id, []),
            )
        )

    ids = snapshot.entity_ids()
    for c in snapshot.conclusions:
        graph.append(m.map_conclusion(c, c.evidence_ref in ids))
        graph.append(m.map_verdict(c))

    graph += [m.map_dataref(d) for d in snapshot.data_refs]

    edge_targets = _edge_targets(snapshot)
    graph += [
        m.map_claim(c, edge_targets.get(c.id, [])) for c in snapshot.claims
    ]
    # external-ref stubs keep dangling edges visible
    for e in snapshot.claim_edges:
        if e.to_ref not in ids:
            graph.append(
                {
                    "@id": f"#external/{e.to_ref}",
                    "@type": "Thing",
                    "identifier": e.to_ref,
                    "description": (
                        "Referenced entity outside this export's scope "
                        "(incomplete provenance, recorded not dropped)."
                    ),
                }
            )
    return graph


def export_rocrate(
    snapshot: Snapshot,
    out: str | Path,
    zip_it: bool = False,
    license_uri: str | None = None,
) -> Path:
    issues = validate_snapshot(snapshot)
    graph = build_graph(snapshot, issues, license_uri)
    descriptor = {"@context": CONTEXT, "@graph": graph}

    out = Path(out)
    crate = out if out.suffix != ".zip" else out.with_suffix("")
    if crate.exists():
        shutil.rmtree(crate)

    write_payload(snapshot, crate, issues)
    (crate / "ro-crate-metadata.json").write_text(
        json.dumps(descriptor, indent=2)
    )

    if zip_it:
        zpath = out.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(crate.rglob("*")):
                z.write(f, f.relative_to(crate))
        return zpath
    return crate
