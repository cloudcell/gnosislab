"""Emit a Snapshot as a W3C PROV-JSON document.

Mapping (one line per class, mirroring the crate mapping):

    programme  → prov:Entity (collection of hypotheses)
    hypothesis → prov:Entity + prov:Plan        (what the trial tested)
    code       → prov:Entity + prov:Plan        (sealed bundle bytes)
    data_ref   → prov:Entity                    (inputs)
    artifact   → prov:Entity                    (outputs)
    observation→ prov:Entity                    (recorded evidence)
    verdict    → prov:Entity                    (conclusion product)
    claim      → prov:Entity                    (anamnesis memory)
    trial      → prov:Activity                  (the execution)
    conclusion → prov:Activity                  (the assessment)
    gnosislab  → prov:Agent (SoftwareAgent)

Relations: ``used`` (inputs/plans/evidence), ``wasGeneratedBy``
(outputs), ``wasDerivedFrom`` / ``wasInfluencedBy`` (claim edges),
``wasAssociatedWith`` / ``wasAttributedTo`` (agent responsibility),
``hadMember`` (programme → hypotheses).

Dangling references become ``gl:external/`` entities so incomplete
provenance stays visible, never dropped — same rule as the crate.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from ..common.payload import write_payload
from ..common.snapshot import Snapshot
from ..common.validation import check as validate_snapshot, redact
from ..common.paths import artifact_path, code_path, results_path

NS = "https://github.com/cloudcell/gnosislab/ns#"

PREFIX = {
    "prov": "http://www.w3.org/ns/prov#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "gl": NS,
}

_QNAME = "prov:QUALIFIED_NAME"


def _t(qname: str) -> dict:
    """Typed literal for a qualified-name value."""
    return {"$": qname, "type": _QNAME}


def _ts(iso: str) -> dict:
    return {"$": iso, "type": "xsd:dateTime"}


def _entity_id(ref: str, snapshot: Snapshot) -> str:
    """Resolve a stored id to its prov entity qname, else external."""
    for coll, kind in (
        (snapshot.programmes, "programme"),
        (snapshot.hypotheses, "hypothesis"),
        (snapshot.trials, "trial"),
        (snapshot.observations, "observation"),
        (snapshot.conclusions, "conclusion"),
        (snapshot.bundles, "bundle"),
        (snapshot.data_refs, "dataref"),
        (snapshot.claims, "claim"),
    ):
        if any(x.id == ref for x in coll):
            return f"gl:{kind}/{ref}"
    for s in snapshot.code_snippets:
        if s.code_hash == ref:
            return f"gl:code/{ref}"
    for a in snapshot.artifacts:
        if a.content_hash == ref:
            return f"gl:artifact/{ref}"
    return f"gl:external/{ref}"


def build_document(snapshot: Snapshot, issues: list[str]) -> dict:
    entity: dict[str, dict] = {}
    activity: dict[str, dict] = {}
    used: dict[str, dict] = {}
    generated: dict[str, dict] = {}
    derived: dict[str, dict] = {}
    influenced: dict[str, dict] = {}
    associated: dict[str, dict] = {}
    attributed: dict[str, dict] = {}
    member: dict[str, dict] = {}
    rel_n = 0

    def rel(table: dict[str, dict], **props) -> None:
        nonlocal rel_n
        rel_n += 1
        table[f"gl:rel/{rel_n}"] = props

    agent = "gl:agent/gnosislab"

    for p in snapshot.programmes:
        entity[f"gl:programme/{p.id}"] = {
            "prov:type": _t("gl:Programme"),
            "prov:label": f"programme {p.id}",
            "gl:goal": redact(p.goal),
            "gl:status": p.status,
            "prov:generatedAtTime": _ts(p.created_at),
        }
        members = [
            f"gl:hypothesis/{h.id}"
            for h in snapshot.hypotheses
            if h.programme_id == p.id
        ]
        if members:
            member[f"gl:members/{p.id}"] = {
                "prov:collection": f"gl:programme/{p.id}",
                "prov:entity": members if len(members) > 1 else members[0],
            }

    for h in snapshot.hypotheses:
        entity[f"gl:hypothesis/{h.id}"] = {
            "prov:type": [_t("gl:Hypothesis"), _t("prov:Plan")],
            "prov:label": f"hypothesis {h.id}",
            "prov:value": redact(h.statement),
            "gl:failure_criterion": redact(h.failure_criterion),
            "gl:status": h.status,
            "prov:generatedAtTime": _ts(h.created_at),
        }

    for s in snapshot.code_snippets:
        entity[f"gl:code/{s.code_hash}"] = {
            "prov:type": [_t("gl:CodeBundle"), _t("prov:Plan")],
            "prov:label": f"code bundle {s.code_hash[:12]}",
            "gl:sha256": s.code_hash,
            "gl:language": s.language,
            "gl:payload_path": code_path(s.code_hash, s.language),
            "prov:generatedAtTime": _ts(s.captured_at),
        }

    for d in snapshot.data_refs:
        e = {
            "prov:type": _t("gl:DataRef"),
            "prov:label": f"data ref {d.id} ({d.split}/{d.regime})",
            "gl:split": d.split,
            "gl:regime": d.regime,
            "prov:generatedAtTime": _ts(d.created_at),
        }
        if d.source_uri:
            if d.source_uri.startswith("file://"):
                # a file:// URI is an absolute host path — host paths
                # stay home; the sha256 hash carries the real identity
                e["gl:sourceLocation"] = "local file (host path redacted)"
            else:
                e["prov:atLocation"] = d.source_uri
        if d.content_hash:
            e["gl:sha256"] = d.content_hash
        entity[f"gl:dataref/{d.id}"] = e

    for a in snapshot.artifacts:
        entity[f"gl:artifact/{a.content_hash}"] = {
            "prov:type": _t("gl:Artifact"),
            "prov:label": a.filename,
            "gl:sha256": a.content_hash,
            "gl:payload_path": artifact_path(a.content_hash, a.filename),
            "prov:generatedAtTime": _ts(a.captured_at),
        }

    for o in snapshot.observations:
        entity[f"gl:observation/{o.id}"] = {
            "prov:type": _t("gl:Observation"),
            "prov:label": f"observation {o.id}",
            "gl:payload_path": results_path(o.trial_id),
            "prov:generatedAtTime": _ts(o.created_at),
        }
        rel(
            generated,
            **{
                "prov:entity": f"gl:observation/{o.id}",
                "prov:activity": f"gl:trial/{o.trial_id}",
            },
        )

    for trial_id, chash in snapshot.trial_artifacts:
        rel(
            generated,
            **{
                "prov:entity": f"gl:artifact/{chash}",
                "prov:activity": f"gl:trial/{trial_id}",
            },
        )

    bundle_by_trial = {b.trial_id: b for b in snapshot.bundles}
    code_hashes = {s.code_hash for s in snapshot.code_snippets}
    dr_ids = {d.id for d in snapshot.data_refs}

    for t in snapshot.trials:
        act = {
            "prov:type": _t("gl:Trial"),
            "prov:label": f"trial {t.id}",
            "gl:status": t.status,
            "gl:config": redact(json.dumps(t.config)),
        }
        if t.started_at:
            act["prov:startTime"] = _ts(t.started_at)
        if t.finished_at:
            act["prov:endTime"] = _ts(t.finished_at)
        activity[f"gl:trial/{t.id}"] = act

        rel(
            used,
            **{
                "prov:activity": f"gl:trial/{t.id}",
                "prov:entity": f"gl:hypothesis/{t.hypothesis_id}",
                "prov:hadRole": _t("gl:hypothesis"),
            },
        )
        b = bundle_by_trial.get(t.id)
        if b and b.code_ref in code_hashes:
            rel(
                used,
                **{
                    "prov:activity": f"gl:trial/{t.id}",
                    "prov:entity": f"gl:code/{b.code_ref}",
                    "prov:hadRole": _t("gl:plan"),
                },
            )
            rel(
                used,
                **{
                    "prov:activity": f"gl:trial/{t.id}",
                    "prov:entity": f"gl:bundle/{b.id}",
                    "prov:hadRole": _t("gl:bundle"),
                },
            )
            entity[f"gl:bundle/{b.id}"] = {
                "prov:type": _t("gl:Bundle"),
                "prov:label": f"bundle {b.id}",
                "gl:code_ref": b.code_ref,
                "gl:env_ref": b.env_ref,
                "gl:seeds": json.dumps(b.seeds),
                "prov:generatedAtTime": _ts(b.created_at),
            }
        for d in dr_ids:
            rel(
                used,
                **{
                    "prov:activity": f"gl:trial/{t.id}",
                    "prov:entity": f"gl:dataref/{d}",
                    "prov:hadRole": _t("gl:input-data"),
                },
            )
        rel(
            associated,
            **{
                "prov:activity": f"gl:trial/{t.id}",
                "prov:agent": agent,
                "prov:hadRole": _t("gl:executor"),
            },
        )

    for c in snapshot.conclusions:
        activity[f"gl:conclusion/{c.id}"] = {
            "prov:type": _t("gl:Conclusion"),
            "prov:label": f"conclusion {c.id}: {redact(c.verdict)}",
            "prov:endTime": _ts(c.created_at),
            "gl:verdict": redact(c.verdict),
            "gl:evidence_summary": redact(c.evidence_summary),
        }
        entity[f"gl:verdict/{c.id}"] = {
            "prov:type": _t("gl:Verdict"),
            "prov:label": f"verdict {c.id}: {redact(c.verdict)}",
            "prov:value": redact(c.evidence_summary),
            "prov:generatedAtTime": _ts(c.created_at),
        }
        rel(
            used,
            **{
                "prov:activity": f"gl:conclusion/{c.id}",
                "prov:entity": _entity_id(c.evidence_ref, snapshot),
                "prov:hadRole": _t("gl:evidence"),
            },
        )
        rel(
            generated,
            **{
                "prov:entity": f"gl:verdict/{c.id}",
                "prov:activity": f"gl:conclusion/{c.id}",
            },
        )
        rel(
            associated,
            **{
                "prov:activity": f"gl:conclusion/{c.id}",
                "prov:agent": agent,
                "prov:hadRole": _t("gl:assessor"),
            },
        )
        rel(
            attributed,
            **{
                "prov:entity": f"gl:verdict/{c.id}",
                "prov:agent": agent,
            },
        )

    ids = snapshot.entity_ids()
    for cl in snapshot.claims:
        e = {
            "prov:type": _t("gl:Claim"),
            "prov:label": f"claim {cl.id}",
            "prov:value": redact(cl.content),
            "gl:claim_type": cl.type,
            "gl:sha256": cl.content_hash,
            "prov:generatedAtTime": _ts(cl.created_at),
        }
        if cl.confidence is not None:
            e["gl:confidence"] = cl.confidence
        entity[f"gl:claim/{cl.id}"] = e
        rel(
            attributed,
            **{"prov:entity": f"gl:claim/{cl.id}", "prov:agent": agent},
        )

    # claim edges → derivation/influence; dangling ends stay visible
    for e in snapshot.claim_edges:
        target = _entity_id(e.to_ref, snapshot)
        if e.to_ref not in ids:
            entity.setdefault(
                target,
                {
                    "prov:type": _t("gl:ExternalRef"),
                    "prov:label": e.to_ref,
                    "gl:note": (
                        "Referenced entity outside this export's scope "
                        "(incomplete provenance, recorded not dropped)."
                    ),
                },
            )
        if e.relation in ("derived_from", "tested_by"):
            rel(
                derived,
                **{
                    "prov:generatedEntity": f"gl:claim/{e.from_claim}",
                    "prov:usedEntity": target,
                    "prov:hadRole": _t(f"gl:{e.relation}"),
                },
            )
        else:
            rel(
                influenced,
                **{
                    "prov:influencee": f"gl:claim/{e.from_claim}",
                    "prov:influencer": target,
                    "prov:hadRole": _t(f"gl:{e.relation}"),
                },
            )

    doc: dict = {"prefix": PREFIX}
    doc["agent"] = {
        agent: {
            "prov:type": _t("prov:SoftwareAgent"),
            "prov:label": "gnosislab (ml-episteme)",
            "gl:url": "https://github.com/cloudcell/gnosislab",
        }
    }
    if issues:
        doc["agent"][agent]["gl:integrity_notes"] = list(issues)
    for name, table in (
        ("entity", entity),
        ("activity", activity),
        ("wasGeneratedBy", generated),
        ("used", used),
        ("wasDerivedFrom", derived),
        ("wasInfluencedBy", influenced),
        ("wasAssociatedWith", associated),
        ("wasAttributedTo", attributed),
        ("hadMember", member),
    ):
        if table:
            doc[name] = table
    return doc


def export_prov(
    snapshot: Snapshot, out: str | Path, zip_it: bool = False
) -> Path:
    issues = validate_snapshot(snapshot)
    doc = build_document(snapshot, issues)

    out = Path(out)
    root = out if out.suffix != ".zip" else out.with_suffix("")
    if root.exists():
        shutil.rmtree(root)

    write_payload(snapshot, root, issues)
    (root / "prov.json").write_text(json.dumps(doc, indent=2))

    if zip_it:
        zpath = out.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(root.rglob("*")):
                z.write(f, f.relative_to(root))
        return zpath
    return root
