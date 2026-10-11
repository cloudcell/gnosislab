#!/usr/bin/env python3
"""fake_session_driver — conformance fixture for the daemon's
session-mode stdio JSONL protocol (plan-20261011-0152Z, Phase B).

This is the reference driver every real driver is tested against: it
implements the whole protocol — ready handshake with capabilities,
send/ack, correlated turn_end, status probes, cancel, fork, shutdown —
with NO vendor backend behind it. What "the agent does" is scripted
via environment variables so tests can drive every edge:

    FAKE_DRIVER_SESSION      session_id to report (default ses_fake1)
    FAKE_DRIVER_NO_READY     emit nothing at startup (handshake timeout)
    FAKE_DRIVER_BAD_JSON     emit a malformed frame after ready
    FAKE_DRIVER_DUP_TURNEND  emit a second, uncorrelated turn_end
    FAKE_DRIVER_UNRESPONSIVE ready, then sleep forever (status probe
                             must time out — never answer stdin)
    FAKE_DRIVER_NEEDS_INPUT  turn_end with reason=needs_input
    FAKE_DRIVER_ERROR        turn_end with reason=error (non-fatal)
    FAKE_DRIVER_EARLY_TURNEND turn_end before any send (violation)
    FAKE_DRIVER_STDERR       echo a line on stderr after ready
    FAKE_DRIVER_DIE_ON_SEND  exit(3) immediately on first send
    FAKE_DRIVER_SLOW         seconds before each turn_end — turn work
                             runs on a worker thread so status/cancel
                             ops keep working while it is in flight

Wire: commands on stdin as JSONL, events on stdout as JSONL,
diagnostics on stderr. Protocol major version: 1.
"""

import json
import os
import sys
import threading
import time

PROTOCOL = 1
_emit_lock = threading.Lock()
_turns = {}                     # turn_id -> {"cancel": bool}


def emit(obj):
    with _emit_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def _turn_reason():
    if os.environ.get("FAKE_DRIVER_NEEDS_INPUT"):
        return "needs_input"
    if os.environ.get("FAKE_DRIVER_ERROR"):
        return "error"
    return "complete"


def _turn_worker(turn_id: str, n: int) -> None:
    """Simulated turn work. Cancel flips the reason; quiescence is
    only claimed when the worker actually finishes."""
    state = _turns.setdefault(turn_id, {"cancel": False})
    end = time.time() + float(os.environ.get("FAKE_DRIVER_SLOW", "0"))
    while time.time() < end:
        if state["cancel"]:
            emit({"event": "turn_end", "turn_id": turn_id,
                  "reason": "cancelled"})
            return
        time.sleep(min(0.05, end - time.time()))
    emit({"event": "turn_end", "turn_id": turn_id,
          "reason": "cancelled" if state["cancel"] else _turn_reason(),
          "text": f"fake turn {n} done"})


def main():
    sid = os.environ.get("FAKE_DRIVER_SESSION", "ses_fake1")
    n = 0

    if os.environ.get("FAKE_DRIVER_NO_READY"):
        time.sleep(3600)
        return 0
    if os.environ.get("FAKE_DRIVER_BAD_JSON"):
        sys.stdout.write("{not json\n")
        sys.stdout.flush()
    emit({"event": "ready", "protocol": PROTOCOL, "session_id": sid,
          "capabilities": {"resume": True, "cancel": True,
                           "fork_context":
                           not os.environ.get("FAKE_DRIVER_NO_FORK")}})
    if os.environ.get("FAKE_DRIVER_STDERR"):
        sys.stderr.write("driver diagnostic line\n")
        sys.stderr.flush()
    if os.environ.get("FAKE_DRIVER_EARLY_TURNEND"):
        emit({"event": "turn_end", "turn_id": "ghost", "reason":
              "complete"})
    if os.environ.get("FAKE_DRIVER_UNRESPONSIVE"):
        time.sleep(3600)   # ready, then wedged — stdin never read
        return 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            emit({"event": "error", "detail": "malformed command",
                  "fatal": False})
            continue
        op, rid = req.get("op"), req.get("id")

        if op == "send":
            n += 1
            emit({"id": rid, "event": "ack", "accepted": True})
            if os.environ.get("FAKE_DRIVER_DIE_ON_SEND"):
                sys.stderr.write("simulated crash mid-turn\n")
                sys.stderr.flush()
                os._exit(3)
            if os.environ.get("FAKE_DRIVER_DUP_TURNEND"):
                emit({"event": "turn_end", "turn_id": "ghost-2",
                      "reason": "complete"})
            tid = req.get("turn_id") or ""
            if float(os.environ.get("FAKE_DRIVER_SLOW", "0")):
                _turns[tid] = {"cancel": False}
                threading.Thread(target=_turn_worker,
                                 args=(tid, n), daemon=True).start()
            else:
                emit({"event": "turn_end", "turn_id": tid,
                      "reason": _turn_reason(),
                      "text": f"fake turn {n} done"})
        elif op == "status":
            emit({"id": rid, "event": "status", "responsive": True,
                  "active_turn_id": req.get("turn_id")})
        elif op == "fork":
            if os.environ.get("FAKE_DRIVER_NO_FORK"):
                emit({"id": rid, "event": "error",
                      "detail": "fork unsupported", "fatal": False})
            else:
                sid = sid + ".forked"
                emit({"id": rid, "event": "ack", "accepted": True})
                emit({"event": "session", "session_id": sid,
                      "cause": "fork"})
        elif op == "cancel":
            emit({"id": rid, "event": "ack", "accepted": True})
            tid = req.get("turn_id")
            if tid in _turns:
                _turns[tid]["cancel"] = True
        elif op == "shutdown":
            emit({"id": rid, "event": "ack", "accepted": True})
            return 0
        else:
            emit({"id": rid, "event": "error",
                  "detail": f"unknown op {op!r}", "fatal": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())
