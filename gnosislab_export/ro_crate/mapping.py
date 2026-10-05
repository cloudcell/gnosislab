"""Snapshot entity → RO-Crate JSON-LD node (pure functions).

Mapping table per docs/d-roadmaps/roadmap-20261003-2201Z:
trial → CreateAction, code bundle → SoftwareSourceCode File,
artifact → File, claim → CreativeWork + isBasedOn, programme →
Collection, conclusion → AssessAction, executor → SoftwareApplication.
"""

from __future__ import annotations

from ..common.paths import artifact_path, code_path, results_path
from ..common.validation import redact
from ..common.snapshot import (
    Artifact,
    Claim,
    Conclusion,
    DataRef,
    Hypothesis,
    Observation,
    Programme,
    Snapshot,
    Trial,
)

_STATUS_TO_ACTION_STATUS = {
    "completed": "http://schema.org/CompletedActionStatus",
    "failed": "http://schema.org/FailedActionStatus",
    "running": "http://schema.org/ActiveActionStatus",
    "in_progress": "http://schema.org/ActiveActionStatus",
    "planned": "http://schema.org/PotentialActionStatus",
    "pending": "http://schema.org/PotentialActionStatus",
}


def map_programme(p: Programme) -> dict:
    return {
        "@id": f"#programme/{p.id}",
        "@type": "Collection",
        "name": f"programme {p.id}",
        "description": redact(p.goal),
        "identifier": p.id,
        "dateCreated": p.created_at,
        "additionalProperty": [
            {"@type": "PropertyValue", "name": "status", "value": p.status},
            {
                "@type": "PropertyValue",
                "name": "budget_max_trials",
                "value": p.budget_max_trials,
            },
            {
                "@type": "PropertyValue",
                "name": "metric_direction",
                "value": p.metric_direction,
            },
        ],
    }


def map_hypothesis(h: Hypothesis) -> dict:
    return {
        "@id": f"#hypothesis/{h.id}",
        "@type": "CreativeWork",
        "name": f"hypothesis {h.id}",
        "text": redact(h.statement),
        "identifier": h.id,
        "dateCreated": h.created_at,
        "additionalProperty": [
            {
                "@type": "PropertyValue",
                "name": "failure_criterion",
                "value": redact(h.failure_criterion),
            },
            {"@type": "PropertyValue", "name": "status", "value": h.status},
        ],
    }


def map_code(snippet_hash: str, language: str, captured_at: str, size: int) -> dict:
    return {
        "@id": code_path(snippet_hash, language),
        "@type": ["File", "SoftwareSourceCode"],
        "name": f"code bundle {snippet_hash[:12]}",
        "programmingLanguage": language,
        "contentSize": size,
        "dateCreated": captured_at,
        "identifier": f"sha256:{snippet_hash}",
    }


def map_artifact(a: Artifact) -> dict:
    e = {
        "@id": artifact_path(a.content_hash, a.filename),
        "@type": "File",
        "name": a.filename,
        "contentSize": a.size_bytes,
        "dateCreated": a.captured_at,
        "identifier": f"sha256:{a.content_hash}",
    }
    if a.content_type:
        e["encodingFormat"] = a.content_type
    return e


def map_results_file(trial: Trial, obs: list[Observation]) -> dict:
    return {
        "@id": results_path(trial.id),
        "@type": "File",
        "name": f"results {trial.id}",
        "encodingFormat": "application/json",
        "about": {"@id": f"#trial/{trial.id}"},
    }


def map_trial(
    trial: Trial,
    code_hash: str | None,
    code_language: str,
    artifact_entities: list[dict],
    has_results: bool,
    data_ref_ids: list[str],
) -> dict:
    obj: list[dict] = []
    if code_hash:
        obj.append({"@id": code_path(code_hash, code_language)})
    obj += [{"@id": f"#dataref/{d}"} for d in data_ref_ids]
    result: list[dict] = [{"@id": a["@id"]} for a in artifact_entities]
    if has_results:
        result.append({"@id": results_path(trial.id)})
    action = {
        "@id": f"#trial/{trial.id}",
        "@type": "CreateAction",
        "name": f"trial {trial.id}",
        "agent": {"@id": "#gnosislab"},
        "object": obj,
        "result": result,
        "actionStatus": {
            "@id": _STATUS_TO_ACTION_STATUS.get(
                trial.status, "http://schema.org/PotentialActionStatus"
            )
        },
        "identifier": trial.id,
        "description": redact(f"config: {trial.config}"),
    }
    if trial.started_at:
        action["startTime"] = trial.started_at
    if trial.finished_at:
        action["endTime"] = trial.finished_at
    if code_hash:
        # the sealed bundle IS the instrument that ran (spec: scripts
        # are referenced as SoftwareSourceCode instruments)
        action["instrument"] = {"@id": code_path(code_hash, code_language)}
    return action


def map_conclusion(c: Conclusion, evidence_internal: bool) -> dict:
    obj = (
        {"@id": f"#trial/{c.evidence_ref}"}
        if evidence_internal
        else {"@id": f"#evidence/{c.evidence_ref}"}
    )
    return {
        "@id": f"#conclusion/{c.id}",
        "@type": "AssessAction",
        "name": f"conclusion {c.id}: {redact(c.verdict)}",
        "agent": {"@id": "#gnosislab"},
        "object": obj,
        "result": {
            "@id": f"#verdict/{c.id}",
        },
        "endTime": c.created_at,
        "identifier": c.id,
        "additionalProperty": [
            {"@type": "PropertyValue", "name": "verdict", "value": redact(c.verdict)},
            {
                "@type": "PropertyValue",
                "name": "evidence_summary",
                "value": redact(c.evidence_summary),
            },
            {
                "@type": "PropertyValue",
                "name": "evidence_ref",
                "value": c.evidence_ref,
            },
        ],
    }


def map_verdict(c: Conclusion) -> dict:
    return {
        "@id": f"#verdict/{c.id}",
        "@type": "CreativeWork",
        "name": f"verdict {c.id}: {redact(c.verdict)}",
        "text": redact(c.evidence_summary),
        "identifier": c.id,
        "dateCreated": c.created_at,
    }


def map_dataref(d: DataRef) -> dict:
    e = {
        "@id": f"#dataref/{d.id}",
        "@type": "Dataset",
        "name": f"data ref {d.id} ({d.split}/{d.regime})",
        "identifier": d.id,
        "dateCreated": d.created_at,
        "additionalProperty": [
            {"@type": "PropertyValue", "name": "split", "value": d.split},
            {"@type": "PropertyValue", "name": "regime", "value": d.regime},
        ],
    }
    if d.source_uri:
        e["isBasedOn"] = {"@id": d.source_uri}
        e["url"] = d.source_uri
    if d.content_hash:
        e["identifier"] = f"sha256:{d.content_hash}"
    if d.size_bytes is not None:
        e["contentSize"] = d.size_bytes
    return e


def map_claim(c: Claim, edges_for: list) -> dict:
    e = {
        "@id": f"#claim/{c.id}",
        "@type": "CreativeWork",
        "text": redact(c.content),
        "identifier": c.id,
        "dateCreated": c.created_at,
        "additionalProperty": [
            {"@type": "PropertyValue", "name": "claim_type", "value": c.type},
        ],
    }
    if c.confidence is not None:
        e["additionalProperty"].append(
            {
                "@type": "PropertyValue",
                "name": "confidence",
                "value": c.confidence,
            }
        )
    based = [{"@id": ed["@id"]} for ed in edges_for]
    if based:
        e["isBasedOn"] = based if len(based) > 1 else based[0]
    return e
