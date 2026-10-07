"""RO-Crate graph-closure regression tests.

A local RO-Crate application reported the exported crate as invalid:
`hasPart`/`isBasedOn`/`object` referenced entities that were never
emitted (`#observation/*`, `#external/bundle-*`, `#evidence/<text>`,
`payload/MANIFEST.json`), actions carried `agent`/`publisher` values
outside their schema.org ranges, and the root dataset lacked the
mandatory `license`/`datePublished`. These tests assert the invariants
directly: every crate-relative reference resolves to a graph entity,
and reference targets satisfy the property ranges a validator checks.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gnosislab_export.common.snapshot import load_snapshot  # noqa: E402
from gnosislab_export.ro_crate.exporter import export_rocrate  # noqa: E402
from export_fixtures import seed_stores  # noqa: E402


@pytest.fixture
def stores(tmp_path):
    return seed_stores(tmp_path)


def _graph(crate):
    descriptor = json.loads((crate / "ro-crate-metadata.json").read_text())
    return {e["@id"]: e for e in descriptor["@graph"]}, descriptor


def _referenced_ids(node):
    """Every @id referenced by an entity's properties (nested + lists)."""
    refs = []

    def walk(v):
        if isinstance(v, dict):
            if "@id" in v:
                refs.append(v["@id"])
            else:
                for inner in v.values():
                    walk(inner)
        elif isinstance(v, list):
            for inner in v:
                walk(inner)

    for k, v in node.items():
        if k != "@id":
            walk(v)
    return refs


def _crate_relative(ref):
    return ref.startswith("#") or ref in ("./", "ro-crate-metadata.json") or (
        ref.startswith("payload/")
    )


def test_every_crate_relative_reference_resolves(stores, tmp_path):
    epi, ana = stores
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    graph, _ = _graph(crate)

    unresolved = []
    for e in graph.values():
        for ref in _referenced_ids(e):
            if _crate_relative(ref) and ref not in graph:
                unresolved.append((e["@id"], ref))
    assert unresolved == []


def test_observation_and_bundle_entities_emitted(stores, tmp_path):
    epi, ana = stores
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    graph, _ = _graph(crate)

    obs = graph["#observation/obs-t1"]
    assert "Dataset" in obs["@type"]
    assert obs["isPartOf"] == {"@id": "payload/results/trl-t1.json"}

    bundle = graph["#bundle/bnd-t1"]
    assert "CreativeWork" in bundle["@type"]

    manifest = graph["payload/MANIFEST.json"]
    assert manifest["@type"] == "File"


def test_root_dataset_required_fields(stores, tmp_path):
    epi, ana = stores
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    graph, _ = _graph(crate)

    root = graph["./"]
    assert "license" in root  # spec MUST — declared or explicit refusal
    assert "datePublished" in root
    # a SoftwareApplication is not a valid publisher/agent
    assert root.get("publisher") != {"@id": "#gnosislab"}


def test_actions_use_instrument_not_agent(stores, tmp_path):
    epi, ana = stores
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    graph, _ = _graph(crate)

    for eid, e in graph.items():
        for prop in ("agent", "publisher"):
            for ref in _referenced_ids({prop: e.get(prop)}):
                target = graph.get(ref)
                if target is None:
                    continue
                assert "SoftwareApplication" not in (
                    target["@type"]
                    if isinstance(target["@type"], list)
                    else [target["@type"]]
                ), f"{eid}: {prop} → SoftwareApplication"


def test_free_text_evidence_ref_becomes_stub(stores, tmp_path):
    epi, ana = stores
    conn = sqlite3.connect(epi)
    conn.execute(
        "UPDATE conclusions SET evidence_ref=? WHERE id='cnc-t1'",
        ("obs from sealed trial — narrative only",),
    )
    conn.commit()
    conn.close()
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    graph, _ = _graph(crate)

    concl = graph["#conclusion/cnc-t1"]
    assert concl["object"] == {"@id": "#evidence/cnc-t1"}
    stub = graph["#evidence/cnc-t1"]
    assert "narrative only" in stub["text"]


def test_internal_evidence_ref_resolves_to_entity(stores, tmp_path):
    epi, ana = stores
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    graph, _ = _graph(crate)

    # evidence_ref='trl-t1' resolves to the trial's results payload
    assert graph["#conclusion/cnc-t1"]["object"] == {
        "@id": "payload/results/trl-t1.json"
    }


def test_every_entity_has_a_name(stores, tmp_path):
    epi, ana = stores
    conn = sqlite3.connect(ana)
    conn.execute(
        "INSERT INTO claim_edges VALUES "
        "('edg-x','clm-t1','obs-gone','observation','derived_from','src',"
        "'2026-10-01T00:05:03Z')"
    )
    conn.commit()
    conn.close()
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    graph, _ = _graph(crate)

    unnamed = [eid for eid, e in graph.items() if "name" not in e]
    assert unnamed == []
    # dangling ref emits a CreativeWork stub that still resolves
    assert "#external/obs-gone" in graph


def test_claim_edge_to_internal_bundle_resolves(stores, tmp_path):
    epi, ana = stores
    conn = sqlite3.connect(ana)
    conn.execute(
        "INSERT INTO claim_edges VALUES "
        "('edg-b','clm-t1','bnd-t1','bundle','derived_from','src',"
        "'2026-10-01T00:05:04Z')"
    )
    conn.commit()
    conn.close()
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    crate = export_rocrate(snap, tmp_path / "crate")
    graph, _ = _graph(crate)

    claim = graph["#claim/clm-t1"]
    based = claim["isBasedOn"]
    targets = based if isinstance(based, list) else [based]
    refs = {t["@id"] for t in targets}
    assert "#bundle/bnd-t1" in refs
    assert all(r in graph for r in refs)
