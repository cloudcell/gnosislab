"""gnosislab_daemon: Loop state machine + daemon isolation + API auth.

Fake agent: a python script that logs every invocation to $FAKE_LOG,
prints a session id, and behaves per $FAKE_MODE. Independent checker:
a script that exits 0 only when $FAKE_SENTINEL exists — the agent's
own claim of completion is never consulted.
"""

import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

import pytest

from gnosislab_daemon.config import AgentProfile, DaemonConfig
from gnosislab_daemon.daemon import Daemon
from gnosislab_daemon.state import StateRoot, LoopState
from gnosislab_daemon.web import make_server

FAKE_AGENT_SRC = """\
import json, os, signal, sys, time
log = os.environ["FAKE_LOG"]
open(log, "a").write(json.dumps({"argv": sys.argv[1:]}) + "\\n")
n = sum(1 for _ in open(log))
print(json.dumps({"sessionID": "sess-42"}))
if os.environ.get("FAKE_READ_STDIN"):
    sys.stdin.readline()            # DEVNULL → immediate EOF, no hang
mode = os.environ.get("FAKE_MODE", "")
if mode == "crash_once" and n == 1:
    os.kill(os.getpid(), signal.SIGKILL)
if mode in ("sentinel", "crash_once"):
    if n >= int(os.environ.get("FAKE_DONE_AT", "2")):
        open(os.environ["FAKE_SENTINEL"], "w").write("done")
if mode == "sleep":
    time.sleep(3600)
"""

CHECK_SRC = """\
import os, sys
if os.path.exists(os.environ.get("FAKE_SENTINEL", "/nonexistent")):
    sys.exit(0)
print("sentinel missing — work incomplete")
sys.exit(int(os.environ.get("FAKE_CHECK_EXIT", "1")))
"""


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    """Isolated state root + fake agent + checker."""
    agent = tmp_path / "fake_agent.py"
    agent.write_text(FAKE_AGENT_SRC)
    checker = tmp_path / "check.py"
    checker.write_text(CHECK_SRC)
    task = tmp_path / "task.md"
    task.write_text("Write the word done to the sentinel file.")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("FAKE_SENTINEL", str(tmp_path / "sentinel"))
    monkeypatch.setenv("FAKE_MODE", "sentinel")
    # tests set these ad hoc via os.environ — scrub leftovers so a
    # previous test's knob never leaks into this one
    for var in ("FAKE_DONE_AT", "FAKE_CHECK_EXIT", "FAKE_READ_STDIN"):
        monkeypatch.delenv(var, raising=False)
    root = StateRoot(tmp_path / "state")
    root.ensure()
    return {
        "root": root, "tmp": tmp_path, "task": str(task),
        "agent_argv": [sys.executable, str(agent), "{prompt}"],
        "check_argv": [sys.executable, str(checker)],
    }


def make_daemon(lab, resume=None, session_regex=None, **limits):
    cfg = DaemonConfig(
        agents={"fake": AgentProfile(
            name="fake",
            start=lab["agent_argv"],
            resume=resume,
            session_id_regex=session_regex)},
        limits={**{
            "max_turns": 10, "max_seconds": 120, "stagnation": 5,
            "agent_timeout": 30, "check_timeout": 10,
            "feedback_bytes": 4096, "output_bytes": 16384}, **limits})
    return Daemon(lab["root"].root, cfg)


def drive(daemon, loop_id, timeout=20):
    r = daemon.loops[loop_id]
    deadline = time.time() + timeout
    while time.time() < deadline:
        daemon.tick()
        if r.loop.status in ("verified", "blocked", "check_error",
                             "stopped"):
            return r.loop.status
        time.sleep(0.05)
    return f"TIMEOUT({r.loop.status})"


def calls(lab):
    p = Path(os.environ["FAKE_LOG"])
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text().splitlines()]


def start_loop(daemon, lab, **kw):
    kw.setdefault("checker_argv", lab["check_argv"])
    return daemon.create_loop(
        agent="fake", task_file=lab["task"], workdir=str(lab["tmp"]),
        **kw).id


# -- core loop -------------------------------------------------------------

def test_false_completion_continues(lab):
    """Agent exits 0 claiming done; checker disagrees until attempt 2."""
    os.environ["FAKE_DONE_AT"] = "2"
    d = make_daemon(lab)
    lid = start_loop(d, lab)
    assert drive(d, lid) == "verified"
    assert len(calls(lab)) == 2          # agent ran twice despite claims
    loop = d.loops[lid].loop
    assert loop.last_check["exit"] == 0
    events = [e["event"] for e in lab["root"].read_events(lid)]
    assert events.count("spawn") == 2 and "verified" in events


def test_verified_stops_immediately(lab):
    os.environ["FAKE_DONE_AT"] = "1"
    d = make_daemon(lab)
    lid = start_loop(d, lab)
    assert drive(d, lid) == "verified"
    n = len(calls(lab))
    for _ in range(20):                  # parked: no further agent turns
        d.tick()
    assert len(calls(lab)) == n == 1


def test_session_resumed_not_restarted(lab):
    os.environ["FAKE_DONE_AT"] = "2"
    agent = lab["agent_argv"][1]       # sys.executable is [0]
    d = make_daemon(
        lab,
        resume=[sys.executable, agent, "--session", "{session_id}",
                "{prompt}"],
        session_regex='"sessionID":\\s*"([^"]+)"')
    lid = start_loop(d, lab)
    assert drive(d, lid) == "verified"
    argv2 = calls(lab)[1]["argv"]      # sys.argv[1:] — exe+script not incl.
    assert argv2[:2] == ["--session", "sess-42"]


def test_no_resume_restarts_with_context(lab):
    os.environ["FAKE_DONE_AT"] = "2"
    d = make_daemon(lab)                 # no resume template
    lid = start_loop(d, lab)
    assert drive(d, lid) == "verified"
    c = calls(lab)
    assert len(c) == 2
    assert "sentinel missing" in c[1]["argv"][-1]  # checker tail fed back


def test_crash_preserves_state(lab):
    os.environ["FAKE_MODE"] = "crash_once"
    os.environ["FAKE_DONE_AT"] = "2"
    d = make_daemon(lab)
    lid = start_loop(d, lab)
    deadline = time.time() + 10
    while lab["root"].loop_state_path(lid) is not None:
        d.tick()
        if d.loops[lid].loop.attempts >= 1:
            break
        assert time.time() < deadline
        time.sleep(0.05)
    assert lab["root"].loop_state_path(lid).is_file()
    saved = LoopState.load(lab["root"].loop_state_path(lid))
    assert saved.attempts == 1 and saved.task_file == lab["task"]
    # parent "dies": real init would reap the crashed child — do the
    # equivalent here so the next daemon sees a truly dead pid
    p = d.loops[lid].proc
    if p is not None:
        p.wait(timeout=5)
        if p.stdout is not None:
            p.stdout.close()
    c = d.loops[lid].check               # a check may also be in flight
    if c is not None:
        c.wait(timeout=5)
        if c.stdout is not None:
            c.stdout.close()

    # daemon restarts mid-turn: adopts/reconciles, then finishes
    d2 = make_daemon(lab)
    d2.discover()
    assert lid in d2.loops
    assert drive(d2, lid) == "verified"


def test_repeated_failure_blocks(lab):
    os.environ["FAKE_MODE"] = "none"     # agent never writes sentinel
    d = make_daemon(lab, max_turns=3)
    lid = start_loop(d, lab)
    assert drive(d, lid) == "blocked"
    assert "max_turns" in d.loops[lid].loop.blocked_reason
    assert len(calls(lab)) == 3          # bounded: exactly the limit


def test_stagnation_blocks(lab):
    os.environ["FAKE_MODE"] = "none"
    d = make_daemon(lab, max_turns=50, stagnation=2)
    lid = start_loop(d, lab)
    assert drive(d, lid) == "blocked"
    assert "stagnation" in d.loops[lid].loop.blocked_reason
    assert len(calls(lab)) < 50


def test_check_error_classified_separately(lab):
    os.environ["FAKE_MODE"] = "none"
    os.environ["FAKE_CHECK_EXIT"] = "2"  # checker itself errors
    d = make_daemon(lab)
    lid = start_loop(d, lab)
    assert drive(d, lid) == "check_error"
    assert len(calls(lab)) == 1          # broken checker ≠ agent failure


def test_checker_spawn_failure_is_check_error(lab):
    d = make_daemon(lab)
    lid = d.create_loop(
        agent="fake", task_file=lab["task"], workdir=str(lab["tmp"]),
        checker_argv=["/nonexistent/checker-xyz"]).id
    assert drive(d, lid) == "check_error"


def test_inputs_are_data_not_shell(lab, tmp_path):
    """Metacharacters in task/checker argv must not be evaluated."""
    marker = tmp_path / "INJECTION_MARKER"
    Path(lab["task"]).write_text(
        f"do work; $(touch {marker}) `touch {marker}` && touch {marker}")
    bad = tmp_path / "agent';rm' .py"
    agent_argv = [sys.executable,
                  str(tmp_path / "fake_agent.py"), "{prompt}"]
    d = make_daemon(lab)
    d.config.agents["fake"].start = agent_argv
    d.config.agents["fake"].session_id_regex = '"sessionID":"([^"]+)"'
    lid = start_loop(d, lab, checker_argv=[
        sys.executable, str(lab["tmp"] / "check.py"),
        f"'; touch {marker}; '"])
    drive(d, lid)
    d.loops[lid].stop()                # cleanup regardless of verdict
    assert not marker.exists()
    assert calls(lab)  # agent still ran with the literal prompt


def test_agent_stdin_gets_eof_not_hang(lab):
    """An agent that reads stdin (permission prompt) must get EOF
    immediately — never a silent block on the daemon's terminal."""
    os.environ["FAKE_READ_STDIN"] = "1"
    os.environ["FAKE_DONE_AT"] = "1"
    d = make_daemon(lab, agent_timeout=30)
    lid = start_loop(d, lab)
    t0 = time.time()
    assert drive(d, lid) == "verified"
    assert time.time() - t0 < 10     # would sit ~30s if stdin blocked


def test_idle_agent_killed_and_checked(lab):
    """A silent, non-exiting agent is killed after agent_idle_seconds —
    the checker then judges the work actually left behind."""
    os.environ["FAKE_MODE"] = "sleep"
    d = make_daemon(lab, max_turns=1, agent_idle_seconds=0.5,
                    agent_timeout=60)
    lid = start_loop(d, lab)
    assert drive(d, lid) == "blocked"
    events = [e["event"] for e in lab["root"].read_events(lid)]
    assert "agent_idle" in events
    assert d.loops[lid].loop.attempts == 1


def test_operator_pause_resume_stop(lab):
    os.environ["FAKE_MODE"] = "none"
    d = make_daemon(lab, max_turns=50)
    lid = start_loop(d, lab)
    r = d.loops[lid]
    while r.loop.status != "verifying" and r.loop.attempts < 1:
        d.tick(); time.sleep(0.02)
    r.pause()
    assert r.loop.status == "paused"
    r.resume()
    # created → next turn; verifying → check re-runs (paused mid-check)
    assert r.loop.status in ("created", "verifying")
    r.stop()
    assert r.loop.status == "stopped"


def test_watch_rearm_on_task_change(lab):
    os.environ["FAKE_DONE_AT"] = "1"
    d = make_daemon(lab)
    lid = start_loop(d, lab, watch=True)
    assert drive(d, lid) == "verified"
    os.environ["FAKE_MODE"] = "none"     # next cycle can't pass
    Path(lab["task"]).write_text("new task content")
    deadline = time.time() + 5
    while d.loops[lid].loop.status == "verified":
        d.tick(); time.sleep(0.05)
        assert time.time() < deadline, "watch never re-armed"
    d.loops[lid].stop()                # leave no agent running at teardown


def test_create_loop_validates(lab):
    d = make_daemon(lab)
    with pytest.raises(ValueError):
        d.create_loop(agent="nope", task_file=lab["task"],
                      workdir=str(lab["tmp"]), checker_argv=["true"])
    with pytest.raises(ValueError):
        d.create_loop(agent="fake", task_file="/nonexistent.md",
                      workdir=str(lab["tmp"]), checker_argv=["true"])


# -- state-root isolation --------------------------------------------------

def test_lock_same_root_second_daemon_refused(lab):
    a = StateRoot(lab["tmp"] / "r")
    b = StateRoot(lab["tmp"] / "r")
    assert a.acquire_lock()
    assert not b.acquire_lock()
    a.release()


def test_lock_distinct_roots_independent(lab):
    a = StateRoot(lab["tmp"] / "a")
    b = StateRoot(lab["tmp"] / "b")
    assert a.acquire_lock() and b.acquire_lock()
    a.release(); b.release()


def test_stale_lock_reclaimed_after_death(lab):
    """A daemon killed without cleanup leaves daemon.lock behind —
    the next daemon must still acquire it (flock dies with the fd)."""
    root = lab["tmp"] / "r"
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from gnosislab_daemon.state import StateRoot; import time;"
         f"assert StateRoot({str(root)!r}).acquire_lock();"
         "time.sleep(60)"])
    deadline = time.time() + 5
    while not (root / "daemon.lock").exists():
        time.sleep(0.05)
        assert time.time() < deadline
    time.sleep(0.3)                      # let it take the lock
    proc.kill(); proc.wait()
    reclaimed = StateRoot(root)
    assert reclaimed.acquire_lock()
    reclaimed.release()


# -- API -------------------------------------------------------------------

@pytest.fixture()
def api(lab):
    lab["root"].ensure_token()
    d = make_daemon(lab)
    httpd = make_server(d, "127.0.0.1", 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=d.supervise, args=(0.05,),
                     daemon=True).start()
    yield d, port, lab["root"].read_token(), lab
    d.shutdown()
    httpd.shutdown()
    httpd.server_close()


def req(port, method, path, token=None, body=None):
    r = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"}
                    if token else {})})
    try:
        with urllib.request.build_opener(
                urllib.request.ProxyHandler({})).open(
                    r, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        with e:  # HTTPError holds a socket — close or 3.14 ResourceWarning
            return e.code, json.loads(e.read() or b"{}")


def test_api_requires_auth(api):
    d, port, token, _lab = api
    code, _ = req(port, "GET", "/api/loops")
    assert code == 401
    code, out = req(port, "GET", "/api/loops", token=token)
    assert code == 200 and out["loops"] == []
    code, _ = req(port, "GET", "/api/loops", token="wrong")
    assert code == 401


def test_api_create_and_finish_loop(api):
    os.environ["FAKE_DONE_AT"] = "1"
    d, port, token, lab = api
    code, out = req(port, "POST", "/api/loops", token=token, body={
        "agent": "fake", "task": lab["task"], "workdir": str(lab["tmp"]),
        "check": ["true"]})
    assert code == 201, out
    lid = out["id"]
    code, out = req(port, "GET", f"/api/loops/{lid}", token=token)
    assert code == 200 and out["id"] == lid
    deadline = time.time() + 10
    while d.loops[lid].loop.status not in (
            "verified", "blocked", "check_error"):
        assert time.time() < deadline
        time.sleep(0.05)
    assert d.loops[lid].loop.status == "verified"


def test_api_service_name_validation(api):
    d, port, token, _lab = api
    code, _ = req(port, "POST", "/api/services/systemd/start",
                  token=token, body={})
    assert code == 404
    code, out = req(port, "GET", "/api/services", token=token)
    assert code == 200
    names = {s["name"] for s in out["services"]}
    assert names == {"anamnesis", "episteme", "zetesis", "arete",
                     "agora"}


def test_api_artifact_traversal_rejected(api):
    d, port, token, _lab = api
    for path in ("/api/export/artifacts/../daemon.json",
                 "/api/export/artifacts/..%2fdaemon.json",
                 "/api/export/artifacts//etc/passwd"):
        code, _ = req(port, "GET", path, token=token)
        assert code in (404, 400), path


def test_api_health_open_but_loopback(api):
    d, port, token, _lab = api
    code, out = req(port, "GET", "/api/health")
    assert code == 200 and out["ok"]


def test_external_bind_refused(lab):
    d = make_daemon(lab)
    with pytest.raises(ValueError):
        make_server(d, "0.0.0.0", 0)
