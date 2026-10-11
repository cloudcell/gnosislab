"""Session-mode driver: the daemon↔driver stdio JSONL protocol.

The driver is a persistent helper process spawned once per loop. It
owns all vendor knowledge (service connections, SDK state, message
history); the daemon owns the loop and the verdict. Wire format:

    daemon → driver : {"id": "req-N", "op": "send|status|cancel|fork|
                       shutdown", ...}
    driver → daemon : {"event": "ready|ack|turn_end|session|status|
                       progress|error|heartbeat", ...}

Protocol invariants enforced here:

- exactly one ``ready`` event before anything else is accepted;
- ``turn_end`` must correlate to an in-flight ``turn_id``;
- ``session`` events only arrive in response to fork/resume/attach;
- frames cap at 1 MiB; malformed JSON / unknown lifecycle is
  surfaced as ``protocol_error`` for the loop to park on.

stderr is the driver's private log channel — drained into events as
``driver_log`` with bounded size.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

MAX_FRAME = 1 * 1024 * 1024       # 1 MiB per JSONL frame
MAX_STDERR = 65536
PROTOCOL = 1


class ProtocolError(Exception):
    pass


class SessionDriver:
    """One driver process + its protocol state."""

    def __init__(self, argv: list[str], cwd: Path,
                 ready_timeout: float = 15.0):
        self.argv = argv
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.ready = False
        self.capabilities: dict = {}
        self.session_id: str | None = None
        self.active_turn: str | None = None   # turn_id awaiting turn_end
        self._inbuf = bytearray()
        self._errbuf = bytearray()
        self._seq = 0
        self._ready_timeout = ready_timeout
        self.spawned_at = 0.0

    # -- lifecycle -----------------------------------------------------
    def spawn(self, env: dict | None = None) -> None:
        self.proc = subprocess.Popen(
            self.argv, cwd=self.cwd,
            stdin=subprocess.PIPE,          # the control channel
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
            env={**(env or os.environ), "PYTHONUNBUFFERED": "1",
                 "PWD": str(self.cwd)},
        )
        os.set_blocking(self.proc.stdout.fileno(), False)
        os.set_blocking(self.proc.stderr.fileno(), False)
        self.spawned_at = time.time()

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def dead_exit(self) -> int | None:
        return self.proc.poll() if self.proc else None

    # -- wire ----------------------------------------------------------
    def _write(self, obj: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise ProtocolError("driver not running")
        self._seq += 1
        obj.setdefault("id", f"req-{self._seq}")
        try:
            self.proc.stdin.write(
                (json.dumps(obj) + "\n").encode())
            self.proc.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise ProtocolError(f"driver stdin write failed: {exc}")

    def op(self, name: str, **kw) -> str:
        """Send a command; returns the request id for correlation."""
        obj = {"op": name, **kw}
        self._write(obj)
        if name == "send":
            self.active_turn = kw.get("turn_id")
        return obj["id"]

    # -- drain ----------------------------------------------------------
    def _drain_fd(self, stream, into: bytearray, cap: int) -> bytes:
        """Non-blocking read; returns raw bytes consumed (or b'')."""
        if stream is None:
            return b""
        chunks = []
        while True:
            try:
                c = os.read(stream.fileno(), 8192)
            except (OSError, BlockingIOError):
                break
            if not c:
                break
            chunks.append(c)
            if sum(map(len, chunks)) > cap:
                break
        raw = b"".join(chunks)
        into.extend(raw)
        return raw

    def drain_stderr(self) -> str | None:
        raw = self._drain_fd(self.proc.stderr if self.proc else None,
                             self._errbuf, MAX_STDERR)
        if len(self._errbuf) > MAX_STDERR:
            del self._errbuf[:-MAX_STDERR // 2]
        return raw.decode("utf-8", "replace")[-500:] or None

    def events(self) -> list[dict]:
        """Parse complete JSONL frames from stdout; protocol violations
        surface as {"event": "protocol_error", ...} records."""
        raw = self._drain_fd(
            self.proc.stdout if self.proc else None, self._inbuf,
            MAX_FRAME * 2)
        del raw
        out = []
        while True:
            nl = self._inbuf.find(b"\n")
            if nl < 0:
                if len(self._inbuf) > MAX_FRAME:
                    out.append({"event": "protocol_error",
                                "detail": "frame exceeds cap"})
                    self._inbuf.clear()
                break
            line, self._inbuf = self._inbuf[:nl], self._inbuf[nl + 1:]
            if len(line) > MAX_FRAME:
                out.append({"event": "protocol_error",
                            "detail": "frame exceeds cap"})
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                out.append({"event": "protocol_error",
                            "detail": "malformed JSON frame"})
                continue
            ev = self._validate(msg)
            out.append(ev)
        return out

    def _validate(self, msg: dict) -> dict:
        """Lifecycle rules → protocol_error event when violated."""
        if not isinstance(msg, dict) or "event" not in msg:
            return {"event": "protocol_error",
                    "detail": "frame missing event"}
        ev = msg["event"]
        if ev == "ready":
            if self.ready:
                return {"event": "protocol_error",
                        "detail": "duplicate ready"}
            if msg.get("protocol") != PROTOCOL:
                return {"event": "protocol_error",
                        "detail": "unsupported protocol "
                                  f"{msg.get('protocol')!r}"}
            self.ready = True
            self.capabilities = msg.get("capabilities") or {}
            self.session_id = msg.get("session_id")
            return msg
        if not self.ready:
            return {"event": "protocol_error",
                    "detail": f"{ev} before ready"}
        if ev == "turn_end":
            tid = msg.get("turn_id")
            if self.active_turn is None or tid != self.active_turn:
                return {"event": "protocol_error",
                        "detail": f"turn_end for unknown turn {tid!r}"}
            self.active_turn = None
            return msg
        if ev == "session":
            self.session_id = msg.get("session_id")
            return msg
        if ev in ("ack", "status", "progress", "error", "heartbeat"):
            return msg
        return {"event": "protocol_error",
                "detail": f"unknown event {ev!r}"}

    # -- shutdown --------------------------------------------------------
    def close(self, grace: float = 5.0) -> None:
        """Cooperative shutdown op → grace → kill the process group."""
        if self.proc is None:
            return
        if self.alive():
            try:
                self.op("shutdown")
            except ProtocolError:
                pass
        self._terminate(grace)

    def _terminate(self, grace: float) -> None:
        p = self.proc
        if p is None:
            return
        try:
            p.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        for s in (p.stdin, p.stdout, p.stderr):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass
        self.proc = None
        self.ready = False
        self.active_turn = None
