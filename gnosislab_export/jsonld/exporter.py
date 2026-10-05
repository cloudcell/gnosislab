"""Emit a Snapshot as a bare JSON-LD document — the fallback format.

Reuses the RO-Crate schema.org mapping verbatim: those nodes are
format-agnostic. The only crate-specific entities — the metadata
descriptor and the ``./`` root Dataset — are dropped, so the output is
a flat JSON-LD graph plus the same payload/ the crate carries. The
export directory keeps the identical payload layout so relative paths
resolve the same way in either format.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from ..common.payload import write_payload
from ..common.snapshot import Snapshot
from ..common.validation import check as validate_snapshot
from ..ro_crate.exporter import build_graph

# schema.org is a resolvable remote context; gl: marks gnosislab-local
# terms should any appear without a schema.org equivalent.
CONTEXT = ["https://schema.org", {"gl": "urn:gnosislab:"}]

# Nodes that exist only to satisfy the RO-Crate spec, not provenance.
_CRATE_ONLY_IDS = {"ro-crate-metadata.json", "./"}


def export_jsonld(
    snapshot: Snapshot, out: str | Path, zip_it: bool = False
) -> Path:
    issues = validate_snapshot(snapshot)
    graph = [
        n for n in build_graph(snapshot, issues)
        if n["@id"] not in _CRATE_ONLY_IDS
    ]
    doc = {"@context": CONTEXT, "@graph": graph}

    out = Path(out)
    root = out if out.suffix != ".zip" else out.with_suffix("")
    if root.exists():
        shutil.rmtree(root)

    write_payload(snapshot, root, issues)
    (root / "export.jsonld").write_text(json.dumps(doc, indent=2))

    if zip_it:
        zpath = out.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(root.rglob("*")):
                z.write(f, f.relative_to(root))
        return zpath
    return root
