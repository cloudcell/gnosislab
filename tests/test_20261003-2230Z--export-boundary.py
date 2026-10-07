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
from export_fixtures import seed_stores  # noqa: E402

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


@pytest.fixture
def stores(tmp_path):
    return seed_stores(tmp_path)


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


def test_missing_scope_target_fails(stores):
    # A miss must not export an empty crate that looks like success.
    epi, ana = stores
    with pytest.raises(ValueError, match="programme not found"):
        load_snapshot(epi, ana, programme_id="prog-nonexistent")
    with pytest.raises(ValueError, match="trial not found"):
        load_snapshot(epi, ana, trial_id="trial-nonexistent")


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
    # schema.org agent is Person|Organization — the executor software
    # is an instrument, not an agent
    assert {"@id": "#gnosislab"} in action["instrument"]
    assert {"@id": "payload/code/aabbcc.py"} in action["instrument"]
    assert action["actionStatus"]["@id"].endswith("CompletedActionStatus")

    # code, artifact, results payload written + referenced
    code_ids = [i for i in graph if i.startswith("payload/code/")]
    assert code_ids and (crate / code_ids[0]).read_text() == "x=1"
    art_ids = [i for i in graph if i.startswith("payload/artifacts/")]
    assert art_ids and (crate / art_ids[0]).read_bytes() == b"\x89PNG"
    assert (crate / "payload/results/trl-t1.json").exists()

    # claim + isBasedOn → the trial's results payload (the evidence the
    # claim rests on; the CreateAction node is not isBasedOn-compatible)
    claim = graph["#claim/clm-t1"]
    assert claim["isBasedOn"] == {"@id": "payload/results/trl-t1.json"}

    # conclusion → AssessAction → verdict CreativeWork
    assert graph["#conclusion/cnc-t1"]["@type"] == "AssessAction"
    assert "#verdict/cnc-t1" in graph

    manifest = json.loads((crate / "payload/MANIFEST.json").read_text())
    assert manifest["counts"]["trials"] == 1
    assert manifest["integrity_issues"] == []


def test_rocrate_zip(stores, tmp_path):
    epi, ana = stores
    snap = load_snapshot(epi, ana, programme_id="prog-t1")
    zpath = export_rocrate(snap, tmp_path / "crate.zip", zip_it=True)
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
