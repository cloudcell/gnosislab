"""Loop: one task's persisted control state machine.

Tick-driven — the daemon's supervisor thread calls ``tick()`` on each
loop every interval; all blocking work happens in spawned subprocesses
tracked by the loop. States:

    created → running ⇄ verifying → verified|blocked|check_error
    paused / stopped are operator states; a stopped loop never revives.
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import prompts
from .config import AgentProfile
from .state import LoopState, StateRoot, utcnow

PARKED = {"verified", "blocked", "check_error", "paused", "stopped"}
TERMINAL = {"verified", "blocked", "check_error", "stopped"}


def task_hash(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "sha256:unreadable"


def pid_alive(pid: int | None, start: int | None = None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as fh:
            # after ")", fields are state(0)…starttime(19)
            stat = fh.read().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return False            # zombie counts as dead
        if start is not None:
            return int(stat[19]) == start
    except (OSError, IndexError, ValueError):
        return start is None        # can't verify identity → only "exists"
    return True


def proc_start_time(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return int(fh.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


class LoopRunner:
    def __init__(self, loop: LoopState, root: StateRoot,
                 profile: AgentProfile, limits: dict):
        self.loop = loop
        self.root = root
        self.profile = profile
        self.limits = dict(limits)
        self.proc: subprocess.Popen | None = None   # live agent child
        self.check: subprocess.Popen | None = None  # live checker child
        self.check_started = 0.0
        self.agent_started = 0.0
        self._last_out_at = 0.0
        self._agent_out = bytearray()
        self._check_out = bytearray()
        self._check_digest_last = ""
        self._stagnant = 0

    # -- helpers --------------------------------------------------------
    @property
    def workdir(self) -> Path:
        return Path(self.loop.workdir)

    def _read_task(self) -> str:
        try:
            return Path(self.loop.task_file).read_text()
        except OSError:
            return ""

    def _emit(self, kind: str, **kw) -> None:
        self.root.emit(self.loop.id, kind, **kw)

    def _save(self) -> None:
        self.root.save_loop(self.loop)

    def _set(self, status: str, **fields) -> None:
        self.loop.status = status
        for k, v in fields.items():
            setattr(self.loop, k, v)
        self._save()
        self._emit("transition", status=status, **{
            k: v for k, v in fields.items()
            if isinstance(v, (str, int, bool, type(None)))})

    def _bound(self, buf: bytearray) -> str:
        return bytes(buf[-self.limits["output_bytes"]:]) \
            .decode("utf-8", "replace")

    def _drain(self, stream, buf: bytearray) -> None:
        if stream is None:
            return
        while True:
            try:
                chunk = os.read(stream.fileno(), 8192)
            except (OSError, BlockingIOError):
                return
            if not chunk:
                return
            self._last_out_at = time.time()
            buf.extend(chunk)
            if len(buf) > self.limits["output_bytes"] * 4:
                del buf[:-self.limits["output_bytes"] * 2]

    # -- spawning -------------------------------------------------------
    def _spawn(self, argv: list[str]) -> subprocess.Popen:
        proc = subprocess.Popen(
            argv, cwd=self.workdir,
            stdin=subprocess.DEVNULL,   # interactive prompts get EOF —
            # never a silent block; EOF never grants a permission
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
            # PWD is inherited stale (the daemon's own cwd); agents that
            # resolve their project dir from $PWD — opencode does — would
            # write into the repo instead of the loop's workdir.
            env={**os.environ, "PYTHONUNBUFFERED": "1",
                 "PWD": str(self.workdir)},
        )
        os.set_blocking(proc.stdout.fileno(), False)
        return proc

    def _prompt(self, kind: str, check_tail: str = "") -> str:
        task = self._read_task()
        maxb = self.limits["feedback_bytes"]
        if self.loop.attempts == 0:
            return prompts.initial_prompt(task)
        if kind == "resume":
            return prompts.continue_prompt(task, check_tail, maxb)
        return prompts.restart_prompt(
            task, check_tail, self.loop.context_note, maxb)

    def _launch(self, kind: str, check_tail: str = "") -> bool:
        """kind: 'start' (first/new invocation) or 'resume' (session)."""
        argv = self.profile.argv(kind, self._prompt(kind, check_tail),
                                 self.loop.session_id)
        if argv is None:
            return False
        try:
            self.proc = self._spawn(argv)
        except OSError as exc:
            self._set("blocked", blocked_reason=f"agent spawn failed: {exc}")
            self._emit("spawn_error", error=str(exc))
            return True
        self.agent_started = time.time()
        self._last_out_at = self.agent_started
        self._agent_out.clear()
        self.loop.attempts += 1
        self.loop.active_pid = self.proc.pid
        self.loop.active_pid_start = proc_start_time(self.proc.pid)
        self._set("running")
        self._emit("spawn", argv=[a if "{prompt}" not in a else "<prompt>"
                                  for a in argv], attempt=self.loop.attempts,
                   pid=self.proc.pid)
        return True

    def _run_check(self) -> None:
        try:
            self.check = self._spawn(self.loop.checker_argv)
        except OSError as exc:
            self._set("check_error",
                      blocked_reason=f"checker spawn failed: {exc}")
            self._emit("check_error", error=str(exc))
            return
        self.check_started = time.time()
        self._check_out.clear()
        self._set("verifying")

    # -- continue paths ---------------------------------------------------
    def _continue(self, check_tail: str) -> None:
        turns = self.loop.attempts
        if turns >= self.limits["max_turns"]:
            self._set("blocked",
                      blocked_reason=f"max_turns={self.limits['max_turns']}")
            self._emit("blocked", reason="max_turns")
            return
        if self._stagnant >= self.limits["stagnation"]:
            self._set("blocked",
                      blocked_reason="stagnation: identical check output")
            self._emit("blocked", reason="stagnation")
            return
        if self.loop.session_id and self.profile.resume:
            if self._launch("resume", check_tail):
                return
        if self._launch("start", check_tail):
            return
        self._set("blocked",
                  blocked_reason="no resume path and start failed")

    # -- check classification ---------------------------------------------
    def _finish_check(self, code: int) -> None:
        tail = self._bound(self._check_out)
        digest = hashlib.sha256(tail.encode()).hexdigest()[:16]
        self._stagnant = (self._stagnant + 1
                          if digest == self._check_digest_last else 0)
        self._check_digest_last = digest
        self.loop.last_check = {"exit": code, "ts": utcnow(),
                                "tail": tail[-2000:]}
        self._emit("check", exit=code, tail=tail[-500:])
        self.check = None
        if code == 0:
            self._set("verified")
            self._emit("verified", attempts=self.loop.attempts)
        elif code == 1:
            self.loop.status = "running"
            self._save()
            self._continue(tail)
        else:
            self._set("check_error",
                      blocked_reason=f"checker exit {code}")
            self._emit("check_error", exit=code)

    # -- tick --------------------------------------------------------------
    def tick(self, now: float) -> None:
        s = self.loop.status
        if s in PARKED:
            if self.loop.watch and s in TERMINAL - {"stopped"}:
                self._watch_tick()
            return
        if s == "created":
            if self.loop.attempts == 0:
                self._launch("start")
            else:
                self._continue("")
            return
        if s == "running":
            if self.proc is not None:
                self._drain(self.proc.stdout, self._agent_out)
                rc = self.proc.poll()
            else:  # adopted orphan child — exit code unknowable, use -1
                rc = (-1 if not pid_alive(self.loop.active_pid,
                                          self.loop.active_pid_start)
                      else None)
            if rc is None:
                idle = self.limits.get("agent_idle_seconds") or 0
                timeout = self.limits.get("agent_timeout") or 0
                ceiling = self.limits.get("max_seconds") or 0
                if self.proc is not None and idle \
                        and now - self._last_out_at > idle:
                    self._kill_agent()
                    self._emit("agent_idle", idle_s=int(idle))
                    self._run_check()
                elif timeout \
                        and now - self.agent_started > timeout:
                    self._kill_agent()
                    self._emit("agent_timeout")
                    self._run_check()
                elif ceiling \
                        and now - _ts(self.loop.cycle_started) > ceiling:
                    self._kill_agent()
                    self._set("blocked", blocked_reason="max_seconds")
                return
            self._emit("agent_exit", code=rc,
                       out_tail=self._bound(self._agent_out)[-500:])
            self.loop.active_pid = None
            self.loop.active_pid_start = None
            if self.proc is not None and self.proc.stdout is not None:
                self._drain(self.proc.stdout, self._agent_out)
                self.proc.stdout.close()
            self.proc = None
            self._capture_session()
            self._run_check()
            return
        if s == "verifying":
            if self.check is None:
                self._run_check()
                return
            self._drain(self.check.stdout, self._check_out)
            rc = self.check.poll()
            if rc is None:
                if now - self.check_started > self.limits["check_timeout"]:
                    self._kill(self.check)
                    self._finish_check(124)
                return
            if self.check.stdout is not None:
                self.check.stdout.close()
            self._finish_check(rc)

    def _watch_tick(self) -> None:
        """Parked terminal + watch → re-arm when the task content changes."""
        h = task_hash(Path(self.loop.task_file))
        if h != self.loop.task_hash:
            self.loop.task_hash = h
            self.loop.cycle_started = utcnow()
            self._stagnant = 0
            self._set("created")
            self._emit("re_arm", reason="task changed")

    def _capture_session(self) -> None:
        sid = self.profile.capture_session_id(
            self._bound(self._agent_out))
        if sid:
            self.loop.session_id = sid
            self._emit("session", session_id=sid)

    # -- operator actions ---------------------------------------------------
    def _kill(self, proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass

    def _kill_agent(self) -> None:
        if self.proc is not None:
            self._kill(self.proc)
            self.proc = None
        elif pid_alive(self.loop.active_pid, self.loop.active_pid_start):
            try:
                os.killpg(self.loop.active_pid, signal.SIGKILL)
            except OSError:
                pass
        self.loop.active_pid = None
        self.loop.active_pid_start = None

    def pause(self) -> None:
        """Halt the current turn. Resume continues the loop; the agent's
        session id is kept, so a session-capable agent picks up where it
        stopped rather than starting blind."""
        if self.loop.status in PARKED:
            return
        self.loop.paused_from = self.loop.status
        self._kill_agent()
        if self.check is not None:
            self._kill(self.check)
            self.check = None
        self._set("paused")
        self._emit("pause")

    def resume(self) -> None:
        if self.loop.status != "paused":
            return
        if self.loop.paused_from == "verifying":
            self.loop.paused_from = ""
            self._set("verifying")   # check child is gone — tick re-runs it
        else:
            self.loop.paused_from = ""
            self._set("created")
        self._emit("resume")

    def stop(self) -> None:
        self._kill_agent()
        if self.check is not None:
            self._kill(self.check)
            self.check = None
        self._set("stopped")
        self._emit("stop")

    # -- recovery -----------------------------------------------------------
    def adopt(self) -> None:
        """Daemon restarted mid-attempt: adopt the surviving child by
        pid+start identity, or treat a dead child as a finished turn."""
        if self.loop.status == "verifying":
            # check child was daemon-side; just re-run it
            self._run_check()
            return
        if self.loop.status != "running":
            return
        if pid_alive(self.loop.active_pid, self.loop.active_pid_start):
            self.proc = None  # poll by pid identity, output lost
            self.agent_started = time.time()
            self._emit("adopt", pid=self.loop.active_pid)
        else:
            self._emit("child_lost", pid=self.loop.active_pid)
            self.loop.active_pid = None
            self._run_check()  # evaluate whatever the turn left behind


def _ts(s: str) -> float:
    if not s:
        return 0.0
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ") \
            .replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0
