"""Snapshot integrity checks run before any format emits.

Rules (from the roadmap):
- incomplete provenance exports *visibly* incomplete — gaps are data,
  not silence; validation REPORTS them in the manifest, never drops.
- no secrets in the crate — a token/key pattern in any string that
  would be emitted is a hard failure, not a warning.
- no absolute deployment paths — host paths stay home.
"""

from __future__ import annotations

import re

from .snapshot import Snapshot

# Hard-fail patterns: anything matching one of these in an emitted
# field means a deployment secret is about to leak into the crate.
_SECRET_PATTERNS = [
    re.compile(r"ML_\w*_(TOKEN|SECRET|PASSWORD|API_KEY)\s*=", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


class ValidationError(RuntimeError):
    """Raised when a snapshot would leak secrets — export aborts."""


_ABS_PATH = re.compile(r"(?<![:/\w])/(?:[\w.\-~]+/)+[\w.\-]*")


def redact(text: str) -> str:
    """Scrub absolute host paths from strings emitted as crate metadata.

    Applied to JSON-LD metadata fields only — payload bytes (sealed
    code, artifacts) are the experiment record and travel verbatim;
    the paths inside them are what the run actually used.
    """
    return _ABS_PATH.sub("<host-path>", text)


def _scan_secrets(name: str, value: str) -> None:
    for pat in _SECRET_PATTERNS:
        if pat.search(value):
            raise ValidationError(
                f"secret-shaped content in {name} — export aborted "
                "before any byte was written"
            )


def _iter_strings(snapshot: Snapshot):
    """Every string the crate could carry. Paths are checked here."""
    for p in snapshot.programmes:
        yield ("programme.goal", p.goal)
    for h in snapshot.hypotheses:
        yield ("hypothesis.statement", h.statement)
        yield ("hypothesis.failure_criterion", h.failure_criterion)
    for t in snapshot.trials:
        yield ("trial.config", str(t.config))
    for c in snapshot.conclusions:
        yield ("conclusion.evidence_summary", c.evidence_summary)
        yield ("conclusion.verdict", c.verdict)
    for s in snapshot.code_snippets:
        yield ("code_snippet.code_text", s.code_text)
    for cl in snapshot.claims:
        yield ("claim.content", cl.content)


def check(snapshot: Snapshot) -> list[str]:
    """Return non-fatal issues; raise ValidationError on secret leaks."""
    issues: list[str] = []
    ids = snapshot.entity_ids()
    for name, value in _iter_strings(snapshot):
        _scan_secrets(name, value)
        if isinstance(value, str) and "/" in value:
            # ANY absolute host path must not travel; the issue names
            # the FIELD only — recording the path would re-leak it
            if _ABS_PATH.search(value):
                issues.append(f"absolute path in {name} (redacted)")

    # Dangling provenance: refs pointing outside the snapshot. Reported
    # (exported visibly incomplete), not fatal.
    for b in snapshot.bundles:
        if b.code_ref not in {s.code_hash for s in snapshot.code_snippets}:
            issues.append(f"bundle {b.id}: code_ref {b.code_ref} not in snapshot")
    for t in snapshot.trials:
        if t.bundle_id and t.bundle_id not in {b.id for b in snapshot.bundles}:
            issues.append(f"trial {t.id}: bundle_id {t.bundle_id} not in snapshot")
    for ta_trial, ta_hash in snapshot.trial_artifacts:
        if ta_hash not in {a.content_hash for a in snapshot.artifacts}:
            issues.append(f"trial_artifact {ta_trial}: blob {ta_hash} missing")
    for e in snapshot.claim_edges:
        if e.to_ref not in ids:
            issues.append(
                f"claim_edge {e.id}: to_ref {e.to_ref} external to snapshot"
            )
    for c in snapshot.conclusions:
        if c.evidence_ref not in ids:
            issues.append(
                f"conclusion {c.id}: evidence_ref {c.evidence_ref} external to snapshot"
            )
    return issues
