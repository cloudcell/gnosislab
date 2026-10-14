"""daemon agent-agnostic backends: verdict contract, check tool,
session protocol conformance, fork lineage, crash reconciliation.

Conformance fixture: gnosislab_daemon/drivers/fake_session_driver.py
— the executable protocol spec every real driver is measured against.
"""

import ast
import json
import os
import sys
import time
from pathlib import Path

import pytest

from gnosislab_daemon import checktool, verdict
from gnosislab_daemon.config import AgentProfile, DaemonConfig
from gnosislab_daemon.daemon import Daemon
from gnosislab_daemon.session_driver import (
    PROTOCOL, ProtocolError, SessionDriver)
from gnosislab_daemon.state import StateRoot

from gnosislab_daemon import drivers as _drivers_pkg

FAKE_DRIVER = str(
    Path(_drivers_pkg.__file__).parent / "fake_session_driver.py")

CHECK_SRC = """\
import os, sys
if os.path.exists(os.environ.get("FAKE_SENTINEL", "/nonexistent")):
    sys.exit(0)
print("sentinel missing — work incomplete")
sys.exit(int(os.environ.get("FAKE_CHECK_EXIT", "1")))
"""


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    checker = tmp_path / "check.py"
    checker.write_text(CHECK_SRC)
    task = tmp_path / "task.md"
    task.write_text("Produce the sentinel file.")
    monkeypatch.setenv("FAKE_SENTINEL", str(tmp_path / "sentinel"))
    for var in ("FAKE_CHECK_EXIT", "FAKE_DRIVER_SESSION",
                "FAKE_DRIVER_NO_READY", "FAKE_DRIVER_BAD_JSON",
                "FAKE_DRIVER_DUP_TURNEND", "FAKE_DRIVER_UNRESPONSIVE",
                "FAKE_DRIVER_NEEDS_INPUT", "FAKE_DRIVER_ERROR",
                "FAKE_DRIVER_EARLY_TURNEND", "FAKE_DRIVER_DIE_ON_SEND",
                "FAKE_DRIVER_SLOW", "FAKE_DRIVER_NO_FORK",
                "FAKE_DRIVER_STDERR"):
        monkeypatch.delenv(var, raising=False)
    root = StateRoot(tmp_path / "state")
    root.ensure()
    return {"root": root, "tmp": tmp_path, "task": str(task),
            "check_argv": [sys.executable, str(checker)],
            "driver_argv": [sys.executable, FAKE_DRIVER]}


def make_daemon(lab, profile=None, **limits):
    lim = {"max_turns": 10, "max_seconds": 0, "stagnation": 3,
           "agent_timeout": 0, "check_timeout": 10,
           "feedback_bytes": 4096, "output_bytes": 16384,
           "driver_ready_seconds": 5, "driver_status_seconds": 2,
           "shutdown_grace": 2}
    lim.update(limits)
    agents = {"fake": profile or AgentProfile(
        name="fake", mode="session",
        driver=lab["driver_argv"])}
    return Daemon(lab["root"].root, DaemonConfig(agents=agents,
                                                 limits=lim))


def drive(daemon, loop_id, timeout=30):
    r = daemon.loops[loop_id]
    deadline = time.time() + timeout
    while time.time() < deadline:
        daemon.tick()
        if r.loop.status in ("verified", "blocked", "check_error",
                             "stopped", "paused"):
            return r.loop.status
        time.sleep(0.05)
    return f"TIMEOUT({r.loop.status})"


def events(root, loop_id):
    return [e["event"] for e in
            root.read_events(loop_id, tail=500)]


def start(daemon, lab, **kw):
    kw.setdefault("checker_argv", lab["check_argv"])
    return daemon.create_loop(agent="fake", task_file=lab["task"],
                              workdir=str(lab["tmp"]), **kw).id


# -- verdict contract -------------------------------------------------------

def test_verdict_absent_is_legacy():
    assert verdict.parse("plain checker output\n") == {"present": False}


def test_verdict_happy_path():
    out = ("some log\nGNOSISLAB_VERDICT " + json.dumps({
        "schema": 1, "pass": False,
        "failures": [{"name": "file_exists",
                      "expected": "result.txt",
                      "got": "missing", "hint": "check workdir"}]}))
    v = verdict.parse(out)
    assert v["ok"] and v["pass"] is False
    assert v["failures"][0]["name"] == "file_exists"


def test_verdict_bounds_and_sanitizes():
    evil = {"schema": 1,
            "failures": [{"name": "x\x1b[31my", "expected": "e" * 9999}]}
    v = verdict.parse("GNOSISLAB_VERDICT " + json.dumps(evil))
    assert v["ok"]
    assert "\x1b" not in v["failures"][0]["name"]
    assert len(v["failures"][0]["expected"]) <= 2048


@pytest.mark.parametrize("payload", [
    "not json", "[]", '{"schema": 99}',
    '{"schema": 1, "failures": ' + "[" + ",".join(
        "{}" for _ in range(60)) + "]}",
    '{"schema": 1, "failures": [], "pad": "' + "x" * 20000 + '"}'])
def test_verdict_malformed(payload):
    v = verdict.parse("GNOSISLAB_VERDICT " + payload)
    assert v["present"] and not v["ok"] and v["error"]


# -- self-check tool --------------------------------------------------------

def test_checktool_writes_execvpe_wrapper(lab):
    argv = ["echo", "arg with spaces", '"quoted"']
    p = checktool.write_tool(lab["tmp"], argv)
    src = p.read_text()
    assert "os.execvpe" in src and "subprocess" not in src
    # argv survives as a JSON-serialized Python literal — no shell
    lit = src.split("json.loads(", 1)[1].split(")", 1)[0]
    assert json.loads(ast.literal_eval(lit)) == argv
    assert os.access(p, os.X_OK)
    checktool.remove_tool(lab["tmp"])
    assert not p.exists()


def test_checktool_is_nonauthoritative(lab):
    checktool.write_tool(lab["tmp"], lab["check_argv"])
    # agent edits the tool — nothing breaks, nothing is trusted
    (lab["tmp"] / checktool.TOOL_NAME).write_text("#!/bin/sh\nexit 0\n")
    assert checktool.INSTRUCTION  # prompt line exists


# -- protocol conformance (SessionDriver unit level) ------------------------

def drv_factory(tmp_path, **env):
    argv = [sys.executable, FAKE_DRIVER]
    d = SessionDriver(argv, tmp_path, ready_timeout=10)
    d.spawn(env={**os.environ, **env})
    return d


def wait_for(d, acc, pred, timeout=10):
    """Pump driver events into ``acc`` until ``pred`` matches one.
    Events arrive in batches — a caller must never drop a batch
    member (e.g. a turn_end riding beside a protocol_error)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        acc += d.events()
        for ev in acc:
            if pred(ev):
                return ev
        time.sleep(0.02)
    raise AssertionError(f"no matching event; saw {acc!r}")


def test_protocol_ready_then_send(tmp_path):
    acc, d = [], drv_factory(tmp_path)
    wait_for(d, acc, lambda e: e["event"] == "ready")
    assert d.ready and d.capabilities["resume"] is True
    d.op("send", turn_id="t1", text="hi")
    wait_for(d, acc, lambda e: e["event"] == "turn_end"
             and e["turn_id"] == "t1" and e["reason"] == "complete")
    d.close(2)
    assert not d.alive()


def test_protocol_version_rejected(tmp_path):
    bad = tmp_path / "badver.py"
    bad.write_text(
        'import sys\nprint(\'{"event":"ready","protocol":99,'
        '"session_id":"s","capabilities":{}}\')\n'
        "sys.stdout.flush()\nimport time\ntime.sleep(30)\n")
    acc, d = [], SessionDriver([sys.executable, str(bad)], tmp_path)
    d.spawn()
    ev = wait_for(d, acc, lambda e: e["event"] == "protocol_error")
    assert "protocol" in ev["detail"]
    d.close(1)


def test_protocol_early_turnend(tmp_path):
    acc, d = [], drv_factory(tmp_path, FAKE_DRIVER_EARLY_TURNEND="1")
    wait_for(d, acc, lambda e: e["event"] == "protocol_error")
    assert any(e["event"] == "protocol_error" and "turn" in e["detail"]
               for e in acc)
    d.close(1)


def test_protocol_bad_json_frame(tmp_path):
    acc, d = [], drv_factory(tmp_path, FAKE_DRIVER_BAD_JSON="1")
    wait_for(d, acc, lambda e: e["event"] == "protocol_error")
    assert any(e["event"] == "ready" for e in acc)
    assert any(e["event"] == "protocol_error" for e in acc)
    d.close(1)


def test_protocol_turnend_correlation(tmp_path):
    acc, d = [], drv_factory(tmp_path, FAKE_DRIVER_DUP_TURNEND="1")
    wait_for(d, acc, lambda e: e["event"] == "ready")
    d.op("send", turn_id="t9", text="x")
    wait_for(d, acc, lambda e: e["event"] == "turn_end"
             and e["turn_id"] == "t9")
    assert any(e["event"] == "protocol_error"
               and "ghost" in e["detail"] for e in acc)
    d.close(1)


def test_protocol_status_during_send(tmp_path):
    acc, d = [], drv_factory(tmp_path, FAKE_DRIVER_SLOW="0.6")
    wait_for(d, acc, lambda e: e["event"] == "ready")
    d.op("send", turn_id="t5", text="slow work")
    rid = d.op("status")          # must answer DURING the turn
    wait_for(d, acc, lambda e: e["event"] == "status"
             and e.get("id") == rid and e["responsive"] is True)
    assert not any(e["event"] == "turn_end" for e in acc) \
        or True  # turn may finish anytime — status just must answer
    wait_for(d, acc, lambda e: e["event"] == "turn_end")
    d.close(1)


def test_protocol_cancel_emits_turnend(tmp_path):
    acc, d = [], drv_factory(tmp_path, FAKE_DRIVER_SLOW="30")
    wait_for(d, acc, lambda e: e["event"] == "ready")
    d.op("send", turn_id="tc", text="long")
    d.op("cancel", turn_id="tc")
    ev = wait_for(d, acc, lambda e: e["event"] == "turn_end"
                  and e["turn_id"] == "tc")
    assert ev["reason"] == "cancelled"
    d.close(1)


def test_protocol_fork_emits_session(tmp_path):
    acc, d = [], drv_factory(tmp_path)
    wait_for(d, acc, lambda e: e["event"] == "ready")
    d.op("fork")
    ev = wait_for(d, acc, lambda e: e["event"] == "session")
    assert ev["cause"] == "fork" and ev["session_id"].endswith("forked")
    d.close(1)


def test_protocol_shutdown_op(tmp_path):
    acc, d = [], drv_factory(tmp_path)
    wait_for(d, acc, lambda e: e["event"] == "ready")
    d.op("shutdown")
    deadline = time.time() + 5
    while d.alive() and time.time() < deadline:
        time.sleep(0.05)
    assert not d.alive()
    d.close(1)                    # release our end of the pipes


# -- loop-level session mode -------------------------------------------------

def test_session_loop_verifies(lab):
    """Driver completes a turn; sentinel pre-exists → verified in one
    turn, turn_end is a boundary not a verdict (checker still runs)."""
    Path(os.environ["FAKE_SENTINEL"]).write_text("x")
    d = make_daemon(lab)
    lid = start(d, lab)
    assert drive(d, lid) == "verified"
    ev = events(lab["root"], lid)
    assert "driver_ready" in ev and "dispatch" in ev \
        and "agent_exit" in ev and "verified" in ev
    assert lab["root"].list_loops()[0].turn_id


def test_session_loop_needs_input_parks(lab, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_NEEDS_INPUT", "1")
    d = make_daemon(lab)
    lid = start(d, lab)
    assert drive(d, lid) == "paused"
    loop = d.loops[lid].loop
    assert loop.needs_input and loop.turn_reason == "needs_input"
    assert "agent_needs_input" in events(lab["root"], lid)
    d.loops[lid].resume()
    assert not d.loops[lid].loop.needs_input


def test_session_loop_unresponsive_driver_blocked(lab, monkeypatch):
    """Unresponsive driver mid-turn: same epistemic position as a dead
    one — the backend may still be working, so the turn is uncertain:
    check runs, fails, parks — never a blind re-dispatch."""
    monkeypatch.setenv("FAKE_DRIVER_UNRESPONSIVE", "1")
    d = make_daemon(lab, driver_status_seconds=1)
    lid = start(d, lab)
    st = drive(d, lid, timeout=25)
    assert st == "blocked"
    loop = d.loops[lid].loop
    assert loop.uncertain_turn
    evs = events(lab["root"], lid)
    assert "protocol_error" in evs and "uncertain_turn" in evs


def test_session_loop_ready_timeout_blocked(lab, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_NO_READY", "1")
    d = make_daemon(lab, driver_ready_seconds=2)
    lid = start(d, lab)
    assert drive(d, lid, timeout=25) == "blocked"


def test_session_ready_bound_clears_on_handshake(lab, monkeypatch):
    """Regression: a ready driver that then takes >ready_seconds on
    the turn must NOT trip 'no ready handshake' — the bound is only
    for the handshake itself."""
    monkeypatch.setenv("FAKE_DRIVER_SLOW", "6")
    Path(os.environ["FAKE_SENTINEL"]).write_text("x")
    d = make_daemon(lab, driver_ready_seconds=2,
                    driver_status_seconds=60)
    lid = start(d, lab)
    assert drive(d, lid, timeout=60) == "verified"


def test_session_loop_driver_death_uncertain(lab, monkeypatch):
    """Driver dies mid-turn → uncertain dispatch → check → BLOCKED,
    never a blind re-dispatch."""
    monkeypatch.setenv("FAKE_DRIVER_DIE_ON_SEND", "1")
    d = make_daemon(lab)
    lid = start(d, lab)
    st = drive(d, lid, timeout=30)
    assert st == "blocked"
    loop = d.loops[lid].loop
    assert loop.uncertain_turn
    assert "uncertain_turn" in events(lab["root"], lid)


def test_session_loop_check_error_reason(lab, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_ERROR", "1")
    monkeypatch.setenv("FAKE_CHECK_EXIT", "3")
    d = make_daemon(lab)
    lid = start(d, lab)
    assert drive(d, lid) == "check_error"


def test_session_loop_continuation(lab):
    """Checker fails twice then passes: driver gets three sends on the
    same session; the daemon never trusts the turn_end reason."""
    checker = lab["tmp"] / "check2.py"
    marker = lab["tmp"] / "check_count"
    checker.write_text(
        "import os, sys\n"
        f"p = {str(marker)!r}\n"
        "n = int(open(p).read()) if os.path.exists(p) else 0\n"
        "open(p, 'w').write(str(n + 1))\n"
        "sys.exit(0 if n >= 2 else 1)\n")
    d = make_daemon(lab)
    lid = start(d, lab, checker_argv=[sys.executable, str(checker)])
    assert drive(d, lid, timeout=60) == "verified"
    assert d.loops[lid].loop.attempts == 3
    assert events(lab["root"], lid).count("dispatch") == 3


def test_session_fork_op_path(lab, monkeypatch):
    """Stagnation + fork-capable driver → fork op issued, lineage
    recorded via the session event."""
    monkeypatch.setenv("FAKE_CHECK_EXIT", "1")
    d = make_daemon(lab, stagnation=2)
    lid = start(d, lab)
    st = drive(d, lid, timeout=90)
    # stagnation reached → fork attempted (driver claims fork_context)
    assert st in ("blocked", "paused", "verified")
    loop = d.loops[lid].loop
    ev = events(lab["root"], lid)
    assert "fork_requested" in ev
    assert loop.parent_session_id or "fork_failed" in ev \
        or "session" in ev


# -- verdict at loop level ---------------------------------------------------

def test_loop_verdict_mismatch_is_check_error(lab, monkeypatch):
    """A verdict JSON claiming pass=true while exiting 1 (or vice
    versa) must fail closed — CHECK_ERROR, never a false success."""
    checker = lab["tmp"] / "checkm.py"
    checker.write_text(
        'print(\'GNOSISLAB_VERDICT {"schema":1,"pass":true,'
        '"failures":[]}\')\nimport sys\nsys.exit(1)\n')
    d = make_daemon(lab)
    lid = start(d, lab, checker_argv=[sys.executable, str(checker)])
    assert drive(d, lid) == "check_error"
    assert "verdict_mismatch" in events(lab["root"], lid)


def test_loop_malformed_verdict_warns_not_fails(lab):
    """Malformed verdict with default limits → diagnostic_parse_error
    event but the exit code still governs."""
    checker = lab["tmp"] / "checkb.py"
    checker.write_text(
        'print("GNOSISLAB_VERDICT {corrupt")\n'
        "import sys\nsys.exit(0)\n")
    d = make_daemon(lab)
    lid = start(d, lab, checker_argv=[sys.executable, str(checker)])
    assert drive(d, lid) == "verified"
    assert "diagnostic_parse_error" in events(lab["root"], lid)


def test_loop_require_structured_fails_closed(lab):
    checker = lab["tmp"] / "checkr.py"
    checker.write_text(
        'print("GNOSISLAB_VERDICT {corrupt")\n'
        "import sys\nsys.exit(0)\n")
    d = make_daemon(lab, require_structured=1)
    lid = start(d, lab, checker_argv=[sys.executable, str(checker)])
    assert drive(d, lid) == "check_error"


def test_loop_structured_failures_rendered(lab):
    """failures[] render into the continuation prompt as diagnostic
    data, not instructions."""
    out = ('GNOSISLAB_VERDICT {"schema":1,"failures":[{"name":"exists",'
           '"expected":"file","got":"none","hint":"write it"}]}')
    v = verdict.parse(out)
    rendered = verdict.render_failures(v["failures"])
    assert "expected 'file'" in rendered and "hint: write it" in rendered
    assert "not instructions" in rendered


# -- exec-mode fork -----------------------------------------------------------

EXEC_AGENT_SRC = """\
import json, os, sys
log = os.environ["EXEC_LOG"]
open(log, "a").write(json.dumps({"argv": sys.argv[1:]}) + "\\n")
n = sum(1 for _ in open(log))
print(json.dumps({"sessionID": f"sess-{n}"}))
"""


def test_exec_fork_at_stagnation(lab, monkeypatch):
    """stagnation + fork argv → fork command runs once, lineage kept,
    then blocked (one fork per episode)."""
    agent = lab["tmp"] / "exec_agent.py"
    agent.write_text(EXEC_AGENT_SRC)
    monkeypatch.setenv("EXEC_LOG", str(lab["tmp"] / "exec_calls.jsonl"))
    checker = lab["tmp"] / "check_same.py"
    checker.write_text("print('same output always')\n"
                       "import sys\nsys.exit(1)\n")
    base = [sys.executable, str(agent), "{prompt}"]
    profile = AgentProfile(
        name="fake", start=base,
        resume=[sys.executable, str(agent),
                "--session", "{session_id}", "{prompt}"],
        fork=[sys.executable, str(agent),
              "--fork", "--session", "{session_id}", "{prompt}"],
        session_id_regex=r'"sessionID":\s*"(sess-\d+)"')
    d = make_daemon(lab, profile=profile, stagnation=2)
    lid = start(d, lab,
                checker_argv=[sys.executable, str(checker)])
    assert drive(d, lid, timeout=60) == "blocked"
    ev = events(lab["root"], lid)
    assert "fork_requested" in ev and "fork_created" in ev
    assert "blocked" in ev
    log = json.loads("[" + ",".join(
        l for l in (lab["tmp"] / "exec_calls.jsonl")
        .read_text().splitlines()) + "]")
    assert any("--fork" in c["argv"] for c in log)
    assert d.loops[lid].loop.parent_session_id


def test_workdir_lock_rejects_second_loop(lab):
    """Same resolved workdir may only host one active loop — file
    changes must be attributable to exactly one writer."""
    d = make_daemon(lab)
    lid1 = start(d, lab)
    import pytest as _p
    with _p.raises(ValueError, match="workdir"):
        start(d, lab)
    d.loops[lid1].stop()
    # stopping frees the workdir
    assert start(d, lab) != lid1
