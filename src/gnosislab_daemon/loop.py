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

from . import checktool, prompts, verdict
from .backends import ExecBackend, SessionBackend
from .config import AgentProfile
from .session_driver import ProtocolError
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
        self._forked = False           # one session-fork per episode
        # last turn_end asked for a human — restored from persisted
        # turn_reason so a crash between turn_end and check keeps it
        self._needs_input = self.loop.turn_reason == "needs_input"
        # turn outcome unknown (crash) — restored from persisted flag
        self._uncertain = bool(self.loop.uncertain_turn)
        # backend transports — exec owns CLI subprocess plumbing,
        # session owns the persistent driver lifecycle
        self.exec = ExecBackend(Path(loop.workdir))
        self.driver: SessionBackend | None = None
        self._await_ready = False
        self._pending_send: str | None = None
        self._status_req: tuple[str, float] | None = None
        self._status_last = 0.0

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
        if status in PARKED:
            # invariant: a parked loop holds no driver — one driver
            # per ACTIVE loop, released on every park, so leaks are
            # impossible by construction rather than by audit
            self._close_driver()
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
        if self.exec.drain(stream, buf, self.limits["output_bytes"]):
            self._last_out_at = time.time()

    # -- spawning -------------------------------------------------------
    def _prompt(self, kind: str, check_tail: str = "") -> str:
        task = self._read_task()
        maxb = self.limits["feedback_bytes"]
        if self.loop.attempts == 0:
            p = prompts.initial_prompt(task)
            if self.limits.get("expose_check_tool"):
                p += "\n\n" + checktool.INSTRUCTION
            return p
        if kind == "fork":
            return prompts.fork_prompt(task, check_tail, maxb)
        if kind == "resume":
            return prompts.continue_prompt(task, check_tail, maxb)
        return prompts.restart_prompt(
            task, check_tail, self.loop.context_note, maxb)

    def _launch(self, kind: str, check_tail: str = "") -> bool:
        """kind: 'start' | 'resume' | 'fork' (exec) — or a session send."""
        if self.profile.session_mode:
            return self._session_launch(check_tail, kind)
        if self.limits.get("expose_check_tool"):
            checktool.write_tool(self.workdir, self.loop.checker_argv)
        argv = self.profile.argv(kind, self._prompt(kind, check_tail),
                                 self.loop.session_id)
        if argv is None:
            return False
        try:
            self.proc = self.exec.spawn(argv)
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
            self.check = self.exec.spawn(self.loop.checker_argv)
        except OSError as exc:
            self._set("check_error",
                      blocked_reason=f"checker spawn failed: {exc}")
            self._emit("check_error", error=str(exc))
            return
        self.check_started = time.time()
        self._check_out.clear()
        self._set("verifying")

    # -- session-mode driver ------------------------------------------------
    def _session_launch(self, check_tail: str = "",
                        kind: str = "resume") -> bool:
        """Send a turn into the persistent driver session. Spawns the
        driver on first use; the turn begins once it reports ready."""
        if kind == "fork":
            # a fork op may only be issued on a live, ready driver
            d = self.driver
            if d is not None and d.alive() and d.ready \
                    and d.capabilities().fork_context:
                try:
                    d.dispatch("", "", kind="fork")
                except ProtocolError as exc:
                    self._driver_protocol_error(str(exc))
                    return True
            else:
                self._emit("fork_unsupported")
                return False
        if self.driver is None or not self.driver.alive():
            mapping = {
                "workdir": str(self.workdir),
                "session_id": self.loop.session_id or "",
                "state_dir": str(self.root.root),
                "loop_id": self.loop.id,
                "config_dir": self.profile.config_dir,
            }
            argv = self.profile.driver_argv(mapping)
            if argv is None:
                self._set("blocked",
                          blocked_reason="profile has no driver argv")
                return True
            if self.limits.get("expose_check_tool"):
                checktool.write_tool(self.workdir, self.loop.checker_argv)
            self.driver = SessionBackend(self.workdir)
            try:
                self.driver.spawn(
                    argv, self.limits["driver_ready_seconds"])
            except OSError as exc:
                self.driver = None
                self._set("blocked",
                          blocked_reason=f"driver spawn failed: {exc}")
                self._emit("spawn_error", error=str(exc))
                return True
            self.loop.active_pid = self.driver.pid
            self.loop.active_pid_start = proc_start_time(self.driver.pid)
            self._await_ready = True
            self._emit("driver_spawn", pid=self.driver.pid)
        self._pending_send = self._prompt(kind, check_tail)
        self.loop.attempts += 1
        self.agent_started = time.time()
        self._last_out_at = self.agent_started
        self._set("running")
        if self.driver.ready:     # live driver: send now — the
            self._send_turn()     # ready-path only fires on fresh spawn
        return True

    def _send_turn(self) -> None:
        """DISPATCH_INTENT is persisted BEFORE the write so a crash
        mid-send reconciles instead of replaying blind."""
        prompt, self._pending_send = self._pending_send, None
        self.loop.turn_seq += 1
        self.loop.turn_id = f"{self.loop.id}-t{self.loop.turn_seq}"
        self.loop.dispatch = "intent"
        self.loop.turn_reason = ""       # stale reason must not
        self._save()                     # survive into the new turn
        try:
            req = self.driver.dispatch(prompt, self.loop.turn_id)
        except ProtocolError as exc:
            self._driver_protocol_error(str(exc))
            return
        self.loop.dispatch = "dispatched"
        self._save()
        self._emit("dispatch", turn_id=self.loop.turn_id, req=req)

    def _driver_protocol_error(self, detail: str) -> None:
        self._emit("protocol_error", detail=detail)
        if self.loop.dispatch in ("intent", "dispatched"):
            # the turn may or may not have reached the backend — the
            # protocol broke, so its outcome is unknowable
            self.loop.uncertain_turn = self.loop.turn_id
            self.loop.dispatch = ""
        self._close_driver()
        self._set("blocked", blocked_reason=f"driver protocol: {detail}")

    def _close_driver(self) -> None:
        if self.driver is not None:
            self.driver.close(self.limits["shutdown_grace"])
            self.driver = None
        self._status_req = None
        self._await_ready = False
        self.loop.active_pid = None
        self.loop.active_pid_start = None

    def _handle_driver_event(self, ev: dict, now: float) -> None:
        kind = ev.get("event")
        if kind == "ready":
            self.loop.session_id = ev.get("session_id")
            self._await_ready = False          # handshake satisfied —
            # the bound must not fire on an already-ready driver
            self.driver.note_ready(ev.get("capabilities") or {})
            self._emit("driver_ready",
                       capabilities=ev.get("capabilities"))
            if self._pending_send is not None:
                self._send_turn()
        elif kind == "ack":
            pass                                    # receipt only
        elif kind == "status":
            if self._status_req and \
                    ev.get("id") == self._status_req[0]:
                self._status_req = None
        elif kind == "session":
            self.loop.parent_session_id, self.loop.session_id = (
                self.loop.session_id, ev.get("session_id"))
            self._emit("session", session_id=self.loop.session_id,
                       cause=ev.get("cause"))
            self._save()
        elif kind == "turn_end":
            reason = ev.get("reason", "complete")
            self._needs_input = (reason == "needs_input")
            self.loop.dispatch = "turn_ended"
            self.loop.turn_reason = reason   # persist at observation
            self._save()
            self._emit("agent_exit", code=0, reason=reason)
            self._run_check()
        elif kind == "progress":
            self._emit("progress", summary=str(
                ev.get("summary", ""))[:200])
        elif kind == "heartbeat":
            pass
        elif kind == "error":
            self._emit("driver_error", detail=ev.get("detail"))
            if ev.get("fatal"):
                self._close_driver()
                self._set("blocked",
                          blocked_reason="driver fatal error")
        elif kind == "protocol_error":
            self._driver_protocol_error(str(ev.get("detail")))

    def _session_tick(self, now: float) -> None:
        d = self.driver
        if d is None:
            self._set("blocked", blocked_reason="driver lost")
            return
        ceiling = self.limits.get("max_seconds") or 0
        if ceiling and now - _ts(self.loop.cycle_started) > ceiling:
            # operator-declared budget — park, never a verdict
            self._emit("cycle_budget", max_seconds=ceiling)
            self._close_driver()
            self._set("blocked", blocked_reason="max_seconds")
            return
        log = d.drain_stderr()
        if log:
            self._emit("driver_log", tail=log)
        for ev in d.events():
            self._handle_driver_event(ev, now)
            if self.loop.status != "running":
                return
        if not d.alive():
            self._emit("driver_exit", code=d.dead_exit())
            self._close_driver()
            if self.loop.dispatch in ("intent", "dispatched"):
                # crashed mid-turn: the backend session may still be
                # working — do NOT blindly resend; evaluate + park
                self._emit("uncertain_turn",
                           turn_id=self.loop.turn_id)
                self.loop.dispatch = ""
                self.loop.uncertain_turn = self.loop.turn_id
                self._needs_input = False
                self._uncertain = True
                self._save()
                self._run_check()
            else:
                self._set("blocked", blocked_reason="driver exited")
            return
        if self._await_ready:
            if now - d.spawned_at > self.limits["driver_ready_seconds"]:
                self._driver_protocol_error("no ready handshake")
            return
        # status probe — bounds protocol RESPONSIVENESS only, never
        # turn length; probes ride at the bound's own cadence
        bound = self.limits["driver_status_seconds"]
        if self._status_req is None \
                and now - self._status_last >= bound:
            try:
                req = d.op("status")
            except ProtocolError as exc:
                self._driver_protocol_error(str(exc))
                return
            self._status_req = (req, now + bound)
            self._status_last = now
        elif self._status_req is not None and now > self._status_req[1]:
            self._driver_protocol_error(
                "status probe unanswered (driver unresponsive)")

    # -- continue paths ---------------------------------------------------
    def _continue(self, check_tail: str) -> None:
        turns = self.loop.attempts
        if turns >= self.limits["max_turns"]:
            self._set("blocked",
                      blocked_reason=f"max_turns={self.limits['max_turns']}")
            self._emit("blocked", reason="max_turns")
            return
        if self._stagnant >= self.limits["stagnation"]:
            # one session fork per stagnation episode — a branch of the
            # CONVERSATION, never a filesystem rollback. Exec mode
            # needs a fork argv template; session mode needs the
            # driver's declared capability. Neither is assumed.
            can_fork = (
                self.profile.fork is not None
                if not self.profile.session_mode
                else (self.driver is not None and self.driver.ready
                      and self.driver.capabilities().fork_context))
            if not self._forked and can_fork and self.loop.session_id:
                self._emit("fork_requested",
                           parent_session_id=self.loop.session_id)
                self.loop.parent_session_id = self.loop.session_id
                self._forked = True
                if self._launch("fork", check_tail):
                    self._emit("fork_created", reason="stagnation")
                    return
                self._emit("fork_failed")
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
        if digest == self._check_digest_last:
            self._stagnant += 1
        else:
            self._stagnant, self._forked = 0, False
        self._check_digest_last = digest
        self.check = None

        # GNOSISLAB_VERDICT — advisory diagnostics, exit code decides
        v = verdict.parse(tail)
        self.loop.last_check = {"exit": code, "ts": utcnow(),
                                "tail": tail[-2000:]}
        if v["present"]:
            self.loop.last_check["verdict"] = v
        if v["present"] and not v["ok"]:
            self._emit("diagnostic_parse_error", error=v["error"])
            if self.limits.get("require_structured"):
                self._emit("check", exit=code, tail=tail[-500:])
                self._set("check_error",
                          blocked_reason="structured verdict malformed: "
                                         f"{v['error']}")
                return
        elif v["present"] and v.get("pass") is not None \
                and v["pass"] != (code == 0):
            # a "pass" field contradicting the exit code is a contract
            # violation — fail closed, never a false success
            self._emit("verdict_mismatch", exit=code,
                       claimed=v["pass"])
            self._emit("check", exit=code, tail=tail[-500:])
            self._set("check_error",
                      blocked_reason="verdict pass field contradicts "
                                     f"exit code {code}")
            return
        self._emit("check", exit=code, tail=tail[-500:])

        if code == 0:
            self._set("verified")
            self._emit("verified", attempts=self.loop.attempts)
        elif code == 1:
            if self._uncertain:
                # prior turn's outcome never proven — park rather than
                # risk a duplicate dispatch (at-least-once ≠ exactly-once)
                self._uncertain = False
                self._set("blocked",
                          blocked_reason="uncertain_turn: prior dispatch "
                                         "outcome unknown")
                self._emit("blocked", reason="uncertain_turn")
                return
            if self._needs_input:
                # agent asked for a human — park for the operator;
                # a parked loop holds no driver
                self._needs_input = False
                self.loop.needs_input = True
                self._set("paused",
                          paused_from="running",
                          blocked_reason="agent needs operator input")
                self._emit("agent_needs_input")
                return
            self.loop.status = "running"
            self._save()
            feedback = (verdict.render_failures(v["failures"])
                        if v["present"] and v["ok"] and v["failures"]
                        else tail)
            self._continue(feedback)
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
            if self.profile.session_mode:
                self._session_tick(now)
                return
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
            # new task content = a new episode: prior-turn uncertainty
            # and stale turn reasons were adjudicated already
            self.loop.uncertain_turn = ""
            self.loop.turn_reason = ""
            self._stagnant = 0
            self._uncertain = False
            self._needs_input = False
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
        self.exec.kill(proc)

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
        self._close_driver()
        if self.check is not None:
            self._kill(self.check)
            self.check = None
        self._set("paused")
        self._emit("pause")

    def resume(self) -> None:
        if self.loop.status != "paused":
            return
        self.loop.needs_input = False   # operator answered
        if self.loop.paused_from == "verifying":
            self.loop.paused_from = ""
            self._set("verifying")   # check child is gone — tick re-runs it
        else:
            self.loop.paused_from = ""
            self._set("created")
        self._emit("resume")

    def stop(self) -> None:
        self._kill_agent()
        self._close_driver()
        if self.check is not None:
            self._kill(self.check)
            self.check = None
        checktool.remove_tool(self.workdir)
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
        if self.profile.session_mode:
            # an orphaned driver's stdio is unreachable — kill it and
            # treat an in-flight turn as uncertain, never replayed
            if pid_alive(self.loop.active_pid, self.loop.active_pid_start):
                try:
                    os.killpg(self.loop.active_pid, signal.SIGKILL)
                except OSError:
                    pass
            self.loop.active_pid = None
            self.loop.active_pid_start = None
            if self.loop.dispatch in ("intent", "dispatched"):
                self._emit("uncertain_turn", turn_id=self.loop.turn_id)
                self.loop.dispatch = ""
                self.loop.uncertain_turn = self.loop.turn_id
                self._uncertain = True
                self._save()
            self._run_check()   # evaluate whatever the turn left behind
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
