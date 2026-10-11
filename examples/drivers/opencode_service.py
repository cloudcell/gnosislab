#!/usr/bin/env python3
"""opencode_service — reference session driver for opencode.

Speaks the gnosislab daemon's stdio JSONL protocol on its own stdio
and drives `opencode run` subprocesses underneath. This is the
adaptor role, demonstrated honestly: the driver owns vendor details
(opencode's argv shape, its JSON event format, session-id capture)
while the daemon owns the loop and the verdict.

What the driver owns:

- session continuity: first send runs `opencode run`; the session id
  is captured from opencode's own JSON output and later sends run
  `opencode run --session <id>`. The id is also reported to the
  daemon via the `ready` handshake (or a `session` event after fork).
- turn lifecycle: a send spawns one `opencode run`; its exit emits
  `turn_end` — complete on rc 0, error otherwise, cancelled if the
  daemon asked to cancel.
- cancel: SIGKILL to the turn's process group, then turn_end.
- fork: marks the next send to run with `--fork --session <id>`;
  the resulting new session id is emitted as a `session` event
  (cause=fork). Fork branches CONVERSATION state only — never files.

Usage (in a daemon profile):

    [agents.opencode_service]
    mode = "session"
    driver = ["python3", "{config_dir}/drivers/opencode_service.py",
              "--workdir", "{workdir}"]

Env: OPENCODE_BIN overrides the binary path (default: `opencode`
looked up on PATH). OPENCODE_MODEL is passed as --model when set.
OPENCODE_RUN_ARGS is shlex-split and appended to every `run` argv —
use it for provider/permission flags; the default already includes
`--auto` (stdin is DEVNULL — a permission prompt would die on EOF).
"""

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading

PROTOCOL = 1
SESSION_RE = re.compile(r'"sessionID"\s*:\s*"([^"]+)"')
_emit_lock = threading.Lock()


def emit(obj):
    with _emit_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def diag(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


class OpencodeDriver:
    def __init__(self, workdir: str, session_id: str | None):
        self.workdir = workdir
        self.session_id = session_id
        self.proc: subprocess.Popen | None = None
        self.active_turn: str | None = None
        self.fork_next = False
        self.cancelled = False
        self.lock = threading.Lock()
        self.bin = os.environ.get("OPENCODE_BIN", "opencode")
        self.model = os.environ.get("OPENCODE_MODEL")
        self.extra = shlex.split(os.environ.get(
            "OPENCODE_RUN_ARGS", "--auto"))

    # -- turn lifecycle -------------------------------------------------
    def _argv(self, text: str) -> list[str]:
        a = [self.bin, "run", "--format", "json", *self.extra]
        if self.model:
            a += ["--model", self.model]
        if self.session_id:
            a += ["--session", self.session_id]
            if self.fork_next:
                a.append("--fork")
        a.append(text)
        return a

    def _reader(self, proc: subprocess.Popen, turn_id: str,
                cancelled: bool):
        """Stream the agent's JSON output; on exit, turn_end."""
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                m = SESSION_RE.search(line)
                if m and m.group(1) != self.session_id:
                    self.session_id = m.group(1)
                    emit({"event": "session",
                          "session_id": self.session_id,
                          "cause": "fork" if self.fork_next
                          else "session"})
                emit({"event": "progress", "turn_id": turn_id,
                      "summary": line[:200]})
            rc = proc.wait()
        except Exception as exc:            # reader itself broke
            diag(f"reader error: {exc}")
            rc = -1
        with self.lock:
            self.proc = None
            self.active_turn = None
            self.fork_next = False
        reason = ("cancelled" if cancelled
                  else "complete" if rc == 0 else "error")
        emit({"event": "turn_end", "turn_id": turn_id,
              "reason": reason, "exit": rc})

    def send(self, req_id: str, turn_id: str, text: str):
        with self.lock:
            if self.proc is not None:
                emit({"id": req_id, "event": "error",
                      "detail": "turn already in flight",
                      "fatal": False})
                return
            env = dict(os.environ, PWD=self.workdir)
            try:
                self.proc = subprocess.Popen(
                    self._argv(text), cwd=self.workdir, env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True)
            except OSError as exc:
                emit({"id": req_id, "event": "error",
                      "detail": f"opencode spawn failed: {exc}",
                      "fatal": True})
                return
            self.active_turn = turn_id
            self.cancelled = False
            threading.Thread(target=self._reader,
                             args=(self.proc, turn_id, False),
                             daemon=True).start()
        emit({"id": req_id, "event": "ack", "accepted": True})

    def cancel(self, req_id: str, turn_id: str):
        with self.lock:
            proc = self.proc
            self.cancelled = True
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        # turn_end(cancelled) is emitted by the reader thread once the
        # process actually exits — ack alone never proves quiescence.
        emit({"id": req_id, "event": "ack", "accepted": True})

    def fork(self, req_id: str):
        if not self.session_id:
            emit({"id": req_id, "event": "error",
                  "detail": "no session to fork", "fatal": False})
            return
        self.fork_next = True
        emit({"id": req_id, "event": "ack", "accepted": True})

    def status(self, req_id: str):
        emit({"id": req_id, "event": "status", "responsive": True,
              "active_turn_id": self.active_turn})

    def shutdown(self, req_id: str):
        emit({"id": req_id, "event": "ack", "accepted": True})
        with self.lock:
            proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        sys.exit(0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    drv = OpencodeDriver(args.workdir, args.session)
    emit({"event": "ready", "protocol": PROTOCOL,
          "session_id": args.session or "",
          "capabilities": {"resume": True, "cancel": True,
                           "fork_context": True}})
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
            drv.send(rid, req.get("turn_id") or "",
                     req.get("text") or "")
        elif op == "status":
            drv.status(rid)
        elif op == "cancel":
            drv.cancel(rid, req.get("turn_id") or "")
        elif op == "fork":
            drv.fork(rid)
        elif op == "shutdown":
            drv.shutdown(rid)
        else:
            emit({"id": rid, "event": "error",
                  "detail": f"unknown op {op!r}", "fatal": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())
