"""Investigation→programme linkage tests (plan-20261002-1929Z).

Covers: declared obligation at open (hint + flag), link_programme's
validation/refusal/idempotency/dangling-relink matrix, the discharge
path on conclude, both invariant checks (unlinked store-only +
dangling cross-server two-probe), and the episteme-side
investigation_id round-trip.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ml_zetesis_mcp.integrity.checks import run_checks
from ml_zetesis_mcp.state.models import (
    Investigation,
    InvestigationVerdict,
)
from .conftest import call_tool, open_inv


class _EvidenceFake:
    """Per-id evidence channel — resolve sets keyed by probe tool.

    assess_programme resolves ids in `live`; get_archived_programme
    resolves ids in `archived`. Anything else errors — mirroring the
    upstream fail() surface, which the adaptor layer raises/returns
    as an error payload.
    """

    def __init__(self, live=(), archived=()):
        self.live = set(live)
        self.archived = set(archived)
        self.calls: list[tuple[str, dict]] = []

    async def pull(self, tool: str, args: dict):
        self.calls.append((tool, args))
        pid = args.get("programme_id")
        pool = self.live if tool == "assess_programme" else self.archived
        if pid in pool:
            return json.dumps({"programme_id": pid})
        return json.dumps({"error": f"{tool}: programme not found"})


def _check(payload, name):
    return next(c for c in payload["checks"] if c["name"] == name)


def _obliged_inv(iid="inv-ob1", **kw):
    return Investigation(
        id=iid,
        question="does linking work?",
        scope={"claim_types": ["empirical"]},
        requires_programme=True,
        **kw,
    )


# --- open_investigation: declared obligation ---


async def test_open_requires_programme_returns_next_hint(
    zetesis_server,
):
    res = await call_tool(
        zetesis_server, "open_investigation",
        {"question": "empirical?", "scope": {},
         "requires_programme": True},
    )
    assert res["status"] == "open"
    assert res["next"] == {
        "action": "create_programme", "then": "link_programme",
    }


async def test_open_without_obligation_has_no_hint(zetesis_server):
    res = await call_tool(
        zetesis_server, "open_investigation",
        {"question": "survey?", "scope": {}},
    )
    assert res["status"] == "open"
    assert "next" not in res


async def test_requires_programme_string_coerced(zetesis_server):
    res = await call_tool(
        zetesis_server, "open_investigation",
        {"question": "coerced?", "scope": {},
         "requires_programme": "true"},
    )
    assert res["status"] == "open"
    assert res["next"]["action"] == "create_programme"


# --- link_programme matrix ---


async def test_link_programme_happy_path(zetesis_server, adaptors):
    adaptors.evidence = _EvidenceFake(live={"prog-aaa111"})
    iid = await open_inv(zetesis_server, requires_programme=True)
    res = await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-aaa111"},
    )
    assert res["linked"] is True
    assert res["linked_programme_id"] == "prog-aaa111"
    # The validation pull is a consult — logged as an evidence_ref.
    assert res["evidence_ref_id"].startswith("eref-")
    g = await call_tool(
        zetesis_server, "get_investigation",
        {"investigation_id": iid},
    )
    inv = g["investigation"]
    assert inv["linked_programme_id"] == "prog-aaa111"
    assert inv["requires_programme"] is True


async def test_link_programme_refuses_non_open(
    zetesis_server, search_store, adaptors,
):
    adaptors.evidence = _EvidenceFake(live={"prog-aaa111"})
    iid = await open_inv(zetesis_server, requires_programme=True)
    await call_tool(
        zetesis_server, "conclude_investigation",
        {"investigation_id": iid, "verdict": "null_result",
         "summary": "done"},
    )
    res = await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-aaa111"},
    )
    assert "error" in res
    assert "not open" in res["error"] or "open" in res["error"]


async def test_link_programme_refuses_missing_adaptor(
    zetesis_server, adaptors,
):
    adaptors.evidence = None
    iid = await open_inv(zetesis_server, requires_programme=True)
    res = await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-aaa111"},
    )
    assert "error" in res
    assert "adaptor" in res["error"]


async def test_link_programme_refuses_unresolvable(
    zetesis_server, adaptors,
):
    adaptors.evidence = _EvidenceFake()  # nothing resolves
    iid = await open_inv(zetesis_server, requires_programme=True)
    res = await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-nope999"},
    )
    assert "error" in res
    assert "does not resolve" in res["error"]


async def test_link_programme_relink_same_idempotent(
    zetesis_server, adaptors,
):
    adaptors.evidence = _EvidenceFake(live={"prog-aaa111"})
    iid = await open_inv(zetesis_server, requires_programme=True)
    await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-aaa111"},
    )
    res = await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-aaa111"},
    )
    assert res["linked"] is True
    assert res.get("idempotent") is True


async def test_link_programme_relink_resolving_refused(
    zetesis_server, adaptors,
):
    adaptors.evidence = _EvidenceFake(
        live={"prog-aaa111", "prog-bbb222"}
    )
    iid = await open_inv(zetesis_server, requires_programme=True)
    await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-aaa111"},
    )
    res = await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-bbb222"},
    )
    assert "error" in res
    assert "already linked" in res["error"]


async def test_link_programme_relink_dangling_allowed(
    zetesis_server, adaptors,
):
    """The sanctioned repair: the existing link no longer resolves
    (prog-a vanished upstream), so re-pointing to a validated new id
    is allowed on an open investigation."""
    ev = _EvidenceFake(live={"prog-aaa111"})
    adaptors.evidence = ev
    iid = await open_inv(zetesis_server, requires_programme=True)
    await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-aaa111"},
    )
    # prog-a disappears upstream; prog-b exists.
    ev.live = {"prog-bbb222"}
    res = await call_tool(
        zetesis_server, "link_programme",
        {"investigation_id": iid, "programme_id": "prog-bbb222"},
    )
    assert res["linked"] is True
    assert res["linked_programme_id"] == "prog-bbb222"


# --- unlinked_empirical_investigations check ---


async def test_check_open_unlinked_flagged(search_store):
    search_store.create_investigation(_obliged_inv())
    payload = await run_checks(search_store)
    c = _check(payload, "unlinked_empirical_investigations")
    assert [v["investigation_id"] for v in c["violations"]] == [
        "inv-ob1"
    ]
    assert c["violations"][0]["status"] == "open"


async def test_check_linked_clears(search_store):
    search_store.create_investigation(_obliged_inv())
    search_store.link_programme("inv-ob1", "prog-aaa111")
    payload = await run_checks(search_store)
    c = _check(payload, "unlinked_empirical_investigations")
    assert c["violations"] == []


async def test_check_concluded_unlinked_flagged(search_store):
    search_store.create_investigation(_obliged_inv())
    search_store.conclude_investigation(
        "inv-ob1", InvestigationVerdict.null_result, "done", None,
    )
    payload = await run_checks(search_store)
    c = _check(payload, "unlinked_empirical_investigations")
    assert len(c["violations"]) == 1
    assert c["violations"][0]["status"] == "concluded"


async def test_check_concluded_discharged_clears(search_store):
    search_store.create_investigation(_obliged_inv())
    search_store.conclude_investigation(
        "inv-ob1", InvestigationVerdict.null_result, "done", None,
        obligation_discharge="question dissolved — no experiment",
    )
    payload = await run_checks(search_store)
    c = _check(payload, "unlinked_empirical_investigations")
    assert c["violations"] == []


async def test_check_obligation_free_untouched(search_store):
    search_store.create_investigation(
        Investigation(id="inv-free", question="survey?", scope={})
    )
    payload = await run_checks(search_store)
    c = _check(payload, "unlinked_empirical_investigations")
    assert c["violations"] == []


# --- dangling_programme_links check ---


async def test_dangling_skipped_without_channel(search_store):
    search_store.create_investigation(
        _obliged_inv(linked_programme_id="prog-aaa111")
    )
    payload = await run_checks(search_store, evidence=None)
    c = _check(payload, "dangling_programme_links")
    assert c["ok"] is True
    assert c["detail"].startswith("skipped")


async def test_dangling_live_resolves_ok(search_store):
    search_store.create_investigation(
        _obliged_inv(linked_programme_id="prog-aaa111")
    )
    ev = _EvidenceFake(live={"prog-aaa111"})
    payload = await run_checks(search_store, evidence=ev)
    c = _check(payload, "dangling_programme_links")
    assert c["violations"] == []
    assert ("assess_programme", {"programme_id": "prog-aaa111"}) in \
        ev.calls


async def test_dangling_archived_resolves_ok(search_store):
    """Archived is a legitimate terminal state — never a violation."""
    search_store.create_investigation(
        _obliged_inv(linked_programme_id="prog-aaa111")
    )
    ev = _EvidenceFake(archived={"prog-aaa111"})
    payload = await run_checks(search_store, evidence=ev)
    c = _check(payload, "dangling_programme_links")
    assert c["violations"] == []


async def test_dangling_unresolvable_flagged(search_store):
    search_store.create_investigation(
        _obliged_inv(linked_programme_id="prog-gone999")
    )
    ev = _EvidenceFake()
    payload = await run_checks(search_store, evidence=ev)
    c = _check(payload, "dangling_programme_links")
    assert len(c["violations"]) == 1
    assert (
        c["violations"][0]["linked_programme_id"] == "prog-gone999"
    )


# --- conclude_investigation discharge path ---


async def test_conclude_discharge_recorded(
    zetesis_server, search_store,
):
    iid = await open_inv(zetesis_server, requires_programme=True)
    res = await call_tool(
        zetesis_server, "conclude_investigation",
        {"investigation_id": iid, "verdict": "null_result",
         "summary": "nothing to run",
         "programme_discharge": "no empirical work needed"},
    )
    assert res["status"] == "concluded"
    assert (
        res["obligation_discharge"] == "no empirical work needed"
    )
    inv = search_store.get_investigation(iid)
    assert (
        inv.obligation_discharge == "no empirical work needed"
    )


# --- episteme side: investigation_id round-trip ---


def test_episteme_programme_investigation_id_roundtrip():
    import tempfile
    from ml_episteme_mcp.state.models import Programme
    from ml_episteme_mcp.state.store import StateStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = StateStore(path)
    store.connect()
    try:
        store.create_programme(Programme(
            id="prog-inv1",
            goal="linkage round-trip",
            constraints={},
            allowed_variables=["x"],
            budget_max_trials=1,
            budget_max_wall_time_hours=0.1,
            investigation_id="inv-ob1",
        ))
        p = store.get_programme("prog-inv1")
        assert p.investigation_id == "inv-ob1"
        rows = store.list_programmes_paginated()
        assert rows[0]["investigation_id"] == "inv-ob1"
    finally:
        store.close()
        Path(path).unlink(missing_ok=True)
