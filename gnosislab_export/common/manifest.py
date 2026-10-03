"""Entity manifest: counts, hashes, and provenance of a snapshot.

The manifest answers "what did this export contain and where did it
come from" without naming deployment details — source DBs are basenames
only, never absolute paths.
"""

from __future__ import annotations

from .snapshot import Snapshot


def build_manifest(snapshot: Snapshot) -> dict:
    return {
        "captured_at": snapshot.captured_at,
        "source_dbs": list(snapshot.source_dbs),  # basenames only
        "counts": {
            "programmes": len(snapshot.programmes),
            "hypotheses": len(snapshot.hypotheses),
            "trials": len(snapshot.trials),
            "observations": len(snapshot.observations),
            "conclusions": len(snapshot.conclusions),
            "bundles": len(snapshot.bundles),
            "data_refs": len(snapshot.data_refs),
            "code_snippets": len(snapshot.code_snippets),
            "artifacts": len(snapshot.artifacts),
            "claims": len(snapshot.claims),
            "claim_edges": len(snapshot.claim_edges),
        },
        "code_hashes": sorted(c.code_hash for c in snapshot.code_snippets),
        "artifact_hashes": sorted(a.content_hash for a in snapshot.artifacts),
        "claim_hashes": sorted(c.content_hash for c in snapshot.claims),
    }
