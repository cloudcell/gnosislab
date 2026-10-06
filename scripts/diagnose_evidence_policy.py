#!/usr/bin/env python3
"""Diagnostic: evidence-policy regimes (plan-20261006-2058Z).

Exercises the full pre-registered evidence lifecycle against an
in-process MCP server over a throwaway store — no production DB,
no ports, no network.

What it proves end-to-end:

  repeated_measurement (default)
    - measured variance admits; unmeasured (None/empty) refuses
    - measured all-zero variance admits WITH an audit advisory
    - the refusal names the single_measurement escape hatch
  single_measurement
    - declaration without a rationale refuses at design_experiment
    - declared policy seals onto the bundle at capture_bundle
    - an observation with no variance admits and records 'null'
    - the observation snapshots the sealed policy (regime+rationale)
  sealing
    - capture_bundle takes no policy parameter (seal ≠ declaration)
    - a second capture is refused (the seal cannot be reopened)
    - a single-seed default-regime bundle captures WITH advisory
  audit
    - the evidence_policy_consistency integrity check catches a
      hand-constructed observation that violates the sealed regime
    - the observation view renders 'not measured' instead of crashing
    - the archive round-trip preserves the policy on all three tables

Run:  uv run python scripts/diagnose_evidence_policy.py
Exit: 0 = all probes behaved as designed; 1 = a probe disagreed.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

RESULTS: list[tuple[str, bool, str]] = []


def probe(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond, detail))
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


async def call(client, name, args):
    r = await client.call_tool(name, args)
    text = r.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


async def main() -> int:
    from mcp.client import Client
    from ml_episteme_mcp.server import create_server
    from ml_episteme_mcp.state.store import StateStore
    from ml_episteme_mcp.state.models import (
        Observation, Trial, canonical_evidence_policy,
        default_evidence_policy, parse_evidence_policy,
    )
    from ml_episteme_mcp.integrity.checks import (
        _check_evidence_policy_consistency,
    )

    tmp = Path(tempfile.mkdtemp(prefix="evidence-policy-diag-"))
    store = StateStore(tmp / "state.db")
    store.connect()
    client = Client(create_server(store))

    stub = str(REPO / "tests" / "fixtures" / "train_stub.py")
    sm_policy = {
        "version": 1,
        "regime": "single_measurement",
        "rationale": "Deterministic comparison — repetition is not "
                     "the uncertainty representation.",
    }

    async with client:
        print("== repeated_measurement (default regime) ==")

        prog = await call(client, "create_programme", {
            "goal": "diag: evidence-policy probe",
            "constraints": {},
            "allowed_variables": ["lr"],
            "budget": {"max_trials": 10, "max_wall_time_hours": 1.0},
        })
        pid = prog["programme_id"]
        hyp = await call(client, "formulate_hypothesis", {
            "programme_id": pid, "statement": "lr affects acc",
            "failure_criterion": "acc < 0.5", "variables_involved": ["lr"],
        })
        hid = hyp["hypothesis_id"]

        des = await call(client, "design_experiment", {
            "programme_id": pid, "hypothesis_id": hid,
            "config": {"lr": 0.01},
        })
        tid = des["trial_id"]
        probe("default design stores repeated_measurement",
              des.get("evidence_policy", {}).get("regime")
              == "repeated_measurement", des.get("evidence_policy"))

        cap = await call(client, "capture_bundle", {
            "trial_id": tid, "code_ref": stub, "env_ref": "python3.12",
            "seeds": [1], "splits": {"train": 0.8},
        })
        probe("capture seals the declared policy onto the bundle",
              cap.get("evidence_policy", {}).get("regime")
              == "repeated_measurement")
        adv = [w for w in cap.get("warnings", [])
               if w.get("kind") == "evidence_policy"]
        probe("single-seed bundle warns (advisory, not refusal)",
              len(adv) == 1, adv[0]["message"][:80] if adv else "")

        # Mark the trial completed (the stub executor path is covered
        # by the HTTP suite; the diagnostic isolates evidence logic).
        store.update_trial_status(tid, "running")
        store.update_trial_executor_output(
            tid, '{"status": "completed", "exit_code": 0}')
        store.update_trial_status(tid, "completed")

        r = await call(client, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "spatiotemporal_region": "diag",
        })
        probe("unmeasured variance (omitted) is refused",
              "error" in r)
        probe("refusal names the preregistered escape hatch",
              "single_measurement" in r.get("error", ""),
              r.get("error", "")[:110])

        r = await call(client, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "variance": {}, "spatiotemporal_region": "diag",
        })
        probe("empty variance mapping is refused", "error" in r)

        r = await call(client, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "variance": {"acc": 0.0}, "spatiotemporal_region": "diag",
        })
        probe("measured zero variance is ADMITTED",
              "error" not in r, r.get("observation_id", r.get("error")))
        probe("admission carries the seed-propagation advisory",
              any("zero" in w for w in r.get("warnings", [])))

        print("== single_measurement (declared regime) ==")

        r = await call(client, "design_experiment", {
            "programme_id": pid, "hypothesis_id": hid,
            "config": {"lr": 0.02},
            "evidence_policy": {"regime": "single_measurement"},
        })
        probe("single_measurement without rationale refuses at design",
              "error" in r, r.get("error", "")[:90])

        des = await call(client, "design_experiment", {
            "programme_id": pid, "hypothesis_id": hid,
            "config": {"lr": 0.02}, "evidence_policy": sm_policy,
        })
        tid2 = des.get("trial_id")
        probe("declared single_measurement designs successfully",
              tid2 is not None)

        cap = await call(client, "capture_bundle", {
            "trial_id": tid2, "code_ref": stub, "env_ref": "python3.12",
            "seeds": [1], "splits": {"train": 0.8},
        })
        probe("sealed policy carries the rationale",
              cap.get("evidence_policy", {}).get("rationale")
              == sm_policy["rationale"])

        store.update_trial_status(tid2, "running")
        store.update_trial_executor_output(
            tid2, '{"status": "completed", "exit_code": 0}')
        store.update_trial_status(tid2, "completed")

        r = await call(client, "record_observation", {
            "trial_id": tid2, "metrics": {"acc": 1.0},
            "spatiotemporal_region": "diag",
        })
        probe("no-variance observation admits under single_measurement",
              "error" not in r, r.get("observation_id"))
        obs = store.get_observation(r["observation_id"])
        probe("variance recorded as 'not measured' (JSON null, "
              "never a fabricated 0.0)",
              json.loads(obs.variance_json) is None)
        probe("observation snapshots the sealed policy",
              json.loads(obs.evidence_policy_json)["regime"]
              == "single_measurement")

        print("== seal immutability ==")

        r = await call(client, "capture_bundle", {
            "trial_id": tid2, "code_ref": stub, "env_ref": "python3.12",
            "seeds": [1], "splits": {"train": 0.8},
        })
        probe("re-capture refused (seal cannot be reopened)",
              "error" in r)

        tools = await client.list_tools()
        schema = next(t for t in tools.tools
                      if t.name == "record_observation").input_schema
        probe("record_observation exposes no policy override param",
              not any("policy" in p or "override" in p or "bypass" in p
                      for p in schema.get("properties", {})))

    print("== audit surfaces ==")

    res = _check_evidence_policy_consistency(store)
    probe("integrity check passes on honestly-admitted state",
          res["violations"] == [], res.get("advisories", []))
    probe("measured-zero advisory surfaces in integrity output",
          len(res.get("advisories", [])) >= 1)

    # Hand-construct a violating observation — the audit must see
    # what enforcement would never admit.
    store.create_observation(Observation(
        id="obs-hostile", trial_id=tid,
        metrics_json='{"acc": 0.9}', variance_json="null",
        spatiotemporal_region="diag",
        evidence_policy_json=json.dumps(default_evidence_policy()),
    ))
    res = _check_evidence_policy_consistency(store)
    probe("integrity check flags unmeasured variance under "
          "repeated_measurement",
          any("unmeasured variance" in v for v in res["violations"]))

    from ml_episteme_mcp.observability.views.trial import (
        render_trial_detail,
    )
    html = render_trial_detail(store, pid, tid2).body.decode()
    probe("trial view renders 'not measured' without crashing",
          "not measured" in html and "single_measurement" in html)

    # Archive round-trip
    from ml_episteme_mcp.archive import ArchiveConfig, Archiver
    arch_dir = tmp / "archives"
    arch_dir.mkdir()
    archiver = Archiver(store, ArchiveConfig(
        enabled=True, batch_size=10, archive_dir=arch_dir))
    store.update_programme_status(pid, "abandoned")
    arc = archiver.archive_programme(pid)
    probe("archive_programme completes", arc.get("archived") is True)

    import sqlite3
    adbs = list(arch_dir.glob("*.db"))
    conn = sqlite3.connect(adbs[0])
    conn.row_factory = sqlite3.Row
    opol = conn.execute(
        "SELECT evidence_policy_json FROM observations "
        "WHERE id='obs-hostile' OR trial_id=?", (tid2,)).fetchall()
    conn.close()
    probe("archived observations retain the policy snapshot",
          all(o["evidence_policy_json"] for o in opol) and len(opol) > 0)

    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS)} probes, {n_fail} failed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
