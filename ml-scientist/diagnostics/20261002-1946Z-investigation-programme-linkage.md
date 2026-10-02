You are driving a scientific-experiment lab exposed as five MCP servers
(episteme :38080, zetesis :38070, arete :38060, anamnesis :38090,
agora :38050). Your standing duty: consult status before acting and act
on what it tells you.

This session exercises the investigation→programme linkage
(plan-20261002-1929Z): declared obligation, validated link, discharge
path, and the two new invariant checks —
`unlinked_empirical_investigations` and `dangling_programme_links`.

Marker tag: `diag-link`. Report every refusal verbatim — errors are
data. Read tool schemas before calling.

PART A — declared obligation and the flag

1. zetesis open_investigation(question="does linking work?", scope={},
   requires_programme=true) → returns investigation_id AND a `next`
   hint `{action: create_programme, then: link_programme}`. Open a
   second investigation without the flag — no hint.
2. zetesis check_invariants → `unlinked_empirical_investigations`
   flags the first investigation (status open); the second is absent.
   Note `dangling_programme_links` — skipped or ok, never a phantom
   violation on a store with no links.
3. zetesis link_programme(investigation_id, programme_id="prog-deadbeef")
   → refused verbatim: the programme does not resolve upstream. A link
   is provenance; fabricated ids are rejected.

PART B — the satisfied link

4. episteme create_programme(goal="linkage battery programme",
   constraints={max_parameter_count: 1000000}, allowed_variables=["x"],
   budget={max_trials: 1, max_wall_time_hours: 0.1},
   metric_direction="minimize",
   investigation_id=<inv-id>) → returns programme_id AND echoes
   investigation_id. list_programmes shows the investigation_id on the
   row; assess_programme reports it too.
5. zetesis link_programme(investigation_id, programme_id=<prog-id>) →
   linked: true, plus an evidence_ref_id recording the validation
   consult (tool assess_programme, args {programme_id, validate: true}).
6. zetesis check_invariants → `unlinked_empirical_investigations`
   clears for this investigation.
7. Re-link same id → idempotent ok. Re-link a *different* resolving
   programme id → refused verbatim (a resolving link cannot be
   re-pointed silently).

PART C — discharge and dangling

8. Open a third obliged investigation; conclude_investigation with
   verdict null_result and NO programme_discharge → the concluded row
   stays flagged (status concluded in the violation payload).
9. Open a fourth obliged investigation; conclude_investigation with
   verdict null_result and programme_discharge="question dissolved —
   no experiment needed" → clears; the discharge is recorded, not
   silently dropped.
10. Dangling probe: link a programme on an open investigation, then
    point the link at a programme id that resolves nowhere (edit via a
    re-link attempt is refused — instead verify the check on a store
    where the upstream programme is gone, or accept the unit-test
    coverage): `dangling_programme_links` flags only ids unresolvable
    in BOTH live and archived states — an archived programme must NOT
    flag.

PART D — surfaces

11. search://status digest → `unlinked_investigations` count reflects
    current debt; the recommendations include link_programme for open
    obliged rows.
12. The investigations GUI list shows the Programme column (obliged /
    linked id / discharged / unlinked); the detail page shows the
    obligation line.

Exit: state the per-part results, quote the refusal texts verbatim,
and note any check that flagged a state it should not have.

DELIVERABLE — produce an exportable artifact.
1. Write your findings into
   /srv/lab/exchange/diagnostics-out/investigation-programme-linkage/ —
   report.md (narrative: what you did, every refusal verbatim, what
   resolved it) plus any evidence files (JSON snapshots, check output).
2. Stage it for host retrieval:
      labloop-export /srv/lab/exchange/diagnostics-out/investigation-programme-linkage
   There is no ./labloop in the VM — that is the host-side repo
   launcher; use labloop-export instead. Only if labloop-export is
   missing: tar.gz your out dir into /srv/lab/exchange/, sha256sum it,
   and label the result a substitute.
3. Report the staged export path, byte count and sha256 verbatim.
