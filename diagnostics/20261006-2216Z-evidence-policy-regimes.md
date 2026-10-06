You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session exercises the evidence-policy regimes
(plan-20261006-2058Z, review-20261006-2134Z): the declared evidence
regime rides the trial at design_experiment, seals onto the bundle at
capture_bundle, and snapshots onto each admitted observation. The
default repeated-measurement regime still refuses unmeasured variance
— but admits a MEASURED zero — and single_measurement (with a sealed
rationale) admits an observation with no variance at all.

Marker tag: `diag-epol`. Report every refusal verbatim — errors are
data. Read tool schemas before calling.

PART A — the default regime (repeated_measurement)

1. Write a minimal trainer to a scratch path under your out dir
   (run_training(config) -> {"metrics": ..., "variance": ...}) and use
   it as code_ref throughout.
2. episteme create_programme + formulate_hypothesis, then
   design_experiment with NO evidence_policy → the response echoes
   evidence_policy {version: 1, regime: repeated_measurement}.
3. capture_bundle with seeds=[7] (single seed) → succeeds, but the
   warnings carry an evidence_policy advisory: a single-seed bundle
   may be unable to satisfy the repeated-measurement policy unless the
   code replicates internally. Record it verbatim.
4. run_trial → completed. record_observation with metrics but NO
   variance → refused; the refusal names single_measurement as the
   predeclared escape hatch — quote it. record_observation with
   variance={} → refused. record_observation with variance={"acc": 0.0}
   → ACCEPTED, response carries a zero-variance audit advisory.
   record_observation with variance={"acc": 0.01} → accepted.

PART B — single_measurement (declared at design)

5. design_experiment with evidence_policy={"regime":
   "single_measurement"} and NO rationale → refused at DESIGN time,
   verbatim. Same with rationale="" and rationale="  " — three refusals.
   Same with {"version": 2} and {"regime": "bogus"} — five refusals.
6. design_experiment with a real rationale ("deterministic comparison
   of sealed extraction substrates — repetition is not the uncertainty
   representation") → accepted; response echoes the policy.
7. capture_bundle → response's evidence_policy carries the rationale;
   single-seed advisory does NOT fire (the regime doesn't require
   repeated measurement).
8. run_trial → completed. record_observation with metrics and NO
   variance → ACCEPTED, response echoes evidence_regime:
   single_measurement.

PART C — seal immutability and the absent bypass

9. Re-call capture_bundle on the sealed trial → refused verbatim.
10. Try passing evidence_policy to record_observation → the tool
    schema has no such parameter; verify via list_tools that no
    policy/override/bypass field exists. The caller supplies evidence,
    never the rule.
11. programme://<pid>/trials resource → each trial's declared
    evidence_policy (regime + rationale) is visible.
    trial://<tid>/observations → each admitted observation carries its
    own admission-time policy snapshot; a single_measurement
    observation shows variance: null — never a fabricated 0.0.
    The episteme observability GUI (:38081) trial page renders the
    same "not measured".

PART D — audit surfaces

12. episteme check_invariants → evidence_policy_consistency: the
    measured-zero observation from PART A surfaces as an ADVISORY
    (seed-propagation suspicion), never a violation; the admitted
    single_measurement observation produces no violation.
13. Close the programme (conclude + close_programme) → the archive
    round-trip preserves evidence_policy_json on trial, bundle, and
    observation — verify via the archived trial's observability view
    or list_archives.
14. Host-side cross-check (report only if you cannot run it): the
    repo carries scripts/diagnose_evidence_policy.py — an in-process
    22-probe battery covering this same surface — and
    tests/test_20261006-2156Z--evidence-policy-regimes.py.

Exit: state the per-part results, quote every refusal verbatim, and
note any check that flagged a state it should not have (or failed to
flag one it should).

DELIVERABLE — produce an exportable artifact.

1. Write your findings into
   /srv/lab/exchange/diagnostics-out/evidence-policy-regimes/ —
   report.md (narrative: what you did, every refusal verbatim, what
   the advisories said) plus evidence files (tool payloads, the
   trainer stub, check_invariants output, GUI excerpt if reachable).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/evidence-policy-regimes
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
