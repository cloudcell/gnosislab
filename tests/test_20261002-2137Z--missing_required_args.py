"""Missing-required-argument refusals at dispatch.

The strict-args wrapper previously checked only *extra* keys; a call
missing a required argument fell through to framework validation and
surfaced a bare "Missing key" that never named the tool's declared
parameter set (rank-5 finding, inv-21a1eda3 follow-up). These tests pin
the refusal to name the missing key(s) and the declared parameters, on
both mirrors (episteme + zetesis) — the same wrapper shape is installed
on all four gated servers.
"""

import json

import pytest


async def _call(mcp, name, args):
    result = await mcp.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


@pytest.fixture
def episteme_server(tmp_path):
    from ml_episteme_mcp.server import create_server
    from ml_episteme_mcp.state.store import StateStore

    store = StateStore(str(tmp_path / "s.db"))
    store.connect()
    mcp = create_server(store, enforcement_config={
        "status_freshness_seconds": 0})
    yield mcp
    store.close()


@pytest.fixture
def zetesis_server(tmp_path):
    from ml_zetesis_mcp.server import create_server
    from ml_zetesis_mcp.state.store import SearchStore

    store = SearchStore(str(tmp_path / "z.db"))
    store.connect()
    mcp = create_server(store, enforcement_config={
        "status_freshness_seconds": 0})
    yield mcp
    store.close()


async def test_missing_required_names_key_and_declared_set(
        episteme_server):
    """The user's live repro: programme_id where contract_id is
    required — the refusal must name contract_id, not a bare
    'Missing key'."""
    r = await _call(episteme_server, "get_evaluation_contract",
                    {"programme_id": "prog-x"})
    assert "error" in r, r
    assert "missing required" in r["error"], r["error"]
    assert "contract_id" in r["error"], r["error"]
    assert "Missing key" not in r["error"], r["error"]
    assert "declared parameters" in r["error"], r["error"]


async def test_missing_required_zetesis_mirror(zetesis_server):
    r = await _call(zetesis_server, "conclude_investigation", {})
    assert "error" in r, r
    assert "missing required" in r["error"], r["error"]
    for key in ("investigation_id", "verdict", "summary"):
        assert key in r["error"], r["error"]


async def test_missing_required_beats_framework_error(
        episteme_server):
    """arguments=None reaches the same refusal — the framework's
    validation must not fire first."""
    r = await _call(episteme_server, "get_evaluation_contract", None)
    assert "error" in r, r
    assert "missing required" in r["error"], r["error"]
    assert "contract_id" in r["error"], r["error"]


async def test_undeclared_still_refused_and_lists_declared(
        episteme_server):
    r = await _call(episteme_server, "get_evaluation_contract",
                    {"contract_id": "c-x", "bogus": 1})
    assert "error" in r, r
    assert "undeclared" in r["error"], r["error"]
    assert "bogus" in r["error"], r["error"]
    assert "declared parameters" in r["error"], r["error"]


async def test_missing_checked_before_undeclared(episteme_server):
    """A call both missing a required key and carrying an undeclared
    one reports the missing key first — the more useful diagnostic."""
    r = await _call(episteme_server, "get_evaluation_contract",
                    {"bogus": 1})
    assert "error" in r, r
    assert "missing required" in r["error"], r["error"]
    assert "contract_id" in r["error"], r["error"]


async def test_valid_shape_passes_through(episteme_server):
    """Complete args reach the tool — refusal is about shape, not
    domain errors."""
    r = await _call(episteme_server, "get_evaluation_contract",
                    {"contract_id": "contract-nonexistent"})
    assert "error" in r, r
    assert "missing required" not in r["error"], r["error"]
    assert "undeclared" not in r["error"], r["error"]


async def test_no_required_args_no_missing_refusal(episteme_server):
    """list_programmes declares no required keys — an empty call is
    a real call, not a refusal."""
    r = await _call(episteme_server, "list_programmes", {})
    assert "missing required" not in json.dumps(r), r
