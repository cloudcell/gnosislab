"""Write the payload bytes every export format describes.

The metadata document differs per format (ro-crate-metadata.json,
prov.json, export.jsonld); the payload it points at does not — sealed
code, artifact blobs, per-trial results, and the integrity manifest
always land at the same relative paths so the same content-addressed
references resolve in every export.
"""

from __future__ import annotations

import json
from pathlib import Path

from .manifest import build_manifest
from .paths import artifact_path, code_path, results_path
from .snapshot import Snapshot


def write_payload(
    snapshot: Snapshot, root: str | Path, issues: list[str] | None = None
) -> None:
    """Write payload/ + MANIFEST.json under an existing-or-new root dir."""
    root = Path(root)
    (root / "payload" / "code").mkdir(parents=True, exist_ok=True)
    (root / "payload" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "payload" / "results").mkdir(parents=True, exist_ok=True)

    for s in snapshot.code_snippets:
        (root / code_path(s.code_hash, s.language)).write_text(s.code_text)
    for a in snapshot.artifacts:
        (root / artifact_path(a.content_hash, a.filename)).write_bytes(
            a.content
        )
    result_trials = {o.trial_id for o in snapshot.observations}
    for tid in result_trials:
        obs = [o for o in snapshot.observations if o.trial_id == tid]
        (root / results_path(tid)).write_text(
            json.dumps(
                {
                    "trial_id": tid,
                    "observations": [
                        {
                            "id": o.id,
                            "metrics": o.metrics,
                            "variance": o.variance,
                            "created_at": o.created_at,
                        }
                        for o in obs
                    ],
                },
                indent=2,
            )
        )

    manifest = build_manifest(snapshot)
    manifest["integrity_issues"] = list(issues or [])
    # Inside payload/, not the top level — keeps the export root
    # spec-clean for RO-Crate and uniform for the other formats.
    (root / "payload" / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2)
    )
