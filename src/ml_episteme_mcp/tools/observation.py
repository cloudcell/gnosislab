"""Observation tool handlers — record_observation."""

from __future__ import annotations

from typing import Annotated
from pydantic import Field

import json
import uuid

from ..state.models import (
    EvidenceRegime,
    Observation,
    parse_evidence_policy,
)
from ..state.store import StateStore
from ..clients.adaptor import MCPAdaptor
from ..enforcement.commitments import (
    check_belief_has_metrics,
    check_evidence_policy,
    check_observation_prerequisites,
    check_programme_active,
    check_uncertainty_requirements,
)
from .schemas import coerce_json, fail, ok, RecordObservationOut
from mcp.types import CallToolResult


def register(mcp, store: StateStore, adaptor: MCPAdaptor) -> None:
    """Register observation-related tools on the MCP server."""

    @mcp.tool()
    async def record_observation(
        trial_id: Annotated[str, Field(description='ID of the target trial.')],
        metrics: Annotated[dict[str, float] | str, Field(description='Measured values {metric_name: float}; may be a JSON-encoded string.')],
        spatiotemporal_region: Annotated[str, Field(description='Where/when the observation was produced (run footprint tag).')],
        variance: Annotated[dict[str, float] | str | None, Field(description="Variance per metric {metric: float} — REQUIRED under the default repeated-measurement regime (a measured zero is valid); omit under a declared single_measurement regime, where it is recorded as 'not measured' rather than fabricated. May be JSON-encoded.")] = None,
    ) -> Annotated[CallToolResult, RecordObservationOut]:
        """Record an observation (data item about a quality).

        metrics and variance may be sent as JSON-encoded strings.

        Only completed trials can be observed — a failed trial produced
        no measurement; its failure lives in its status, executor_output,
        and artifacts, not here. If nothing is concludable, close the
        programme 'abandoned' rather than fabricating evidence.

        What counts as admissible evidence is fixed by the trial's
        sealed evidence policy (declared at design_experiment, sealed
        at capture_bundle) — the caller cannot choose a regime here.
        Under the default repeated-measurement regime, measured variance
        per metric is required; an all-zero mapping is accepted as a
        measured value (and flagged for audit — identical outcomes can
        mean unpropagated seeds). Under a declared single_measurement
        regime, variance may be absent and is recorded as 'not
        measured' — never as a fabricated 0.0.

        Enforcement: commitment 5 — reproducibility is the price of
        admission (a-00 §5.7); the evidence regime is part of the
        predeclared, sealed experimental design.
        """
        try:
            metrics = coerce_json(metrics, dict, "metrics")
            if variance is not None:
                variance = coerce_json(variance, dict, "variance")
            # Enforcement: commitments 6 + 1 — trial must exist, have a bundle, and be completed
            trial = store.get_trial(trial_id)
            if trial is not None:
                # Enforcement: commitment 1 — a closed programme is immutable
                err = check_programme_active(
                    store.get_programme(trial.programme_id)
                )
                if err:
                    return fail(json.dumps({"error": err}))
            err = check_observation_prerequisites(trial)
            if err:
                return fail(json.dumps({"error": err}))

            # Commitment 5 / a-00 §5.7: the sealed bundle's evidence
            # policy decides what this observation must carry — the
            # caller supplies the evidence, not the rule.
            bundle = store.get_bundle(trial.bundle_id)
            try:
                policy = parse_evidence_policy(
                    bundle.evidence_policy_json if bundle else None
                )
            except ValueError:
                return fail(json.dumps({
                    "error": (
                        "The sealed bundle carries a malformed "
                        "evidence_policy_json — refusing to admit an "
                        "observation under an unreadable evidence "
                        "contract."
                    ),
                }))
            err = check_evidence_policy(policy)
            if err:
                return fail(json.dumps({"error": err}))
            regime = policy["regime"]

            warnings: list[str] = []
            if regime == EvidenceRegime.repeated_measurement.value:
                err = check_uncertainty_requirements(variance)
                if err:
                    return fail(json.dumps({"error": err}))
                variance_json = json.dumps(variance)
                if all(v == 0 for v in variance.values()):
                    warnings.append(
                        "All-zero variance recorded as a measured value "
                        "(a deterministic computation legitimately "
                        "produces 0.0 across seeds); flagged for audit "
                        "— if the seeds were expected to matter, check "
                        "that they reached the computation."
                    )
            else:
                # single_measurement: variance is not required, but
                # the observation must still carry measured values —
                # metrics are the whole of the evidence here.
                err = check_belief_has_metrics(metrics)
                if err:
                    return fail(json.dumps({"error": err}))
                # Supplied variance is stored verbatim (instrument
                # uncertainty etc.); absent variance is recorded as
                # "not measured" (JSON null) — never a fabricated 0.0.
                variance_json = json.dumps(variance) if variance else "null"

            observation = Observation(
                id=f"obs-{uuid.uuid4().hex[:8]}",
                trial_id=trial_id,
                metrics_json=json.dumps(metrics),
                variance_json=variance_json,
                spatiotemporal_region=spatiotemporal_region,
                # Snapshot the sealed policy so the observation is
                # self-describing: which regime governed, and why.
                evidence_policy_json=json.dumps(policy),
            )
            store.create_observation(observation)

            payload = {
                "observation_id": observation.id,
                "status": "recorded",
                "evidence_regime": regime,
            }
            if warnings:
                payload["warnings"] = warnings
            return ok(payload)
        except Exception as e:
            return fail(json.dumps({"error": str(e)}))
