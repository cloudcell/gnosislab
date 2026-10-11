"""State root: the daemon's identity, single-writer boundary, and record.

Layout::

    <state_root>/
        daemon.lock        one writer per registry (flock held for life)
        daemon.json        daemon metadata: port, pid, generation
        daemon.toml        per-instance settings
        management-token   owner-only (0600)
        exports/           artifacts produced by export jobs
        loops/<id>/state.json    atomic snapshot
        loops/<id>/events.jsonl  append-only transitions
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LOOP_STATES = ("created", "running", "verifying", "verified",
               "blocked", "check_error", "paused", "stopped")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


@dataclass
class LoopState:
    id: str
    task_file: str
    task_hash: str
    workdir: str
    agent: str
    checker_argv: list[str]
    status: str = "created"
    watch: bool = False
    mode: str = "exec"             # exec | session — recorded at create
    session_id: str | None = None
    parent_session_id: str | None = None  # lineage after a session fork
    turn_id: str = ""              # session-mode dispatch correlation
    turn_seq: int = 0              # monotonics — persisted so turn_ids
    # stay unique across daemon restarts
    dispatch: str = ""             # ""|intent|dispatched|turn_ended —
    # persisted BEFORE the write so a crash mid-turn is reconcilable
    turn_reason: str = ""          # last turn_end reason, persisted at
    # observation — survives a crash between turn_end and check
    uncertain_turn: str = ""       # turn_id whose outcome is unknown —
    # parks the loop BLOCKED rather than risk a duplicate dispatch
    needs_input: bool = False      # agent asked for a human; parked
    context_note: str = ""
    attempts: int = 0
    active_pid: int | None = None
    active_pid_start: int | None = None
    last_check: dict = field(default_factory=dict)
    blocked_reason: str = ""
    paused_from: str = ""
    limits: dict = field(default_factory=dict)
    cycle_started: str = ""
    created: str = ""
    updated: str = ""

    @classmethod
    def load(cls, path: Path) -> "LoopState":
        d = json.loads(path.read_text())
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def as_dict(self) -> dict:
        return asdict(self)


class StateRoot:
    """Owns one registry. Exactly one live daemon may hold the lock."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.loops_dir = self.root / "loops"
        self.exports_dir = self.root / "exports"
        self._lock_fd = None

    # -- lifecycle ------------------------------------------------------
    def ensure(self) -> None:
        for d in (self.root, self.loops_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def acquire_lock(self) -> bool:
        """flock daemon.lock non-blocking; held for process lifetime.
        A lock left by a dead daemon is released by the kernel, so
        stale files reclaim safely."""
        self.ensure()
        fd = open(self.root / "daemon.lock", "a+")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fd.close()
            return False
        self._lock_fd = fd
        return True

    def release(self) -> None:
        """Drop the lock fd (tests/shutdown); the kernel unlocks on close."""
        if self._lock_fd is not None:
            self._lock_fd.close()
            self._lock_fd = None

    # -- daemon metadata ------------------------------------------------
    @property
    def daemon_json_path(self) -> Path:
        return self.root / "daemon.json"

    def write_daemon_json(self, data: dict) -> None:
        atomic_write(self.daemon_json_path, json.dumps(data, indent=2))

    def read_daemon_json(self) -> dict:
        try:
            return json.loads(self.daemon_json_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    # -- management token -----------------------------------------------
    @property
    def token_path(self) -> Path:
        return self.root / "management-token"

    def ensure_token(self) -> str:
        if self.token_path.is_file():
            return self.token_path.read_text().strip()
        token = secrets.token_urlsafe(32)
        self.token_path.write_text(token + "\n")
        os.chmod(self.token_path, 0o600)
        return token

    def read_token(self) -> str | None:
        try:
            return self.token_path.read_text().strip()
        except OSError:
            return None

    # -- loops ------------------------------------------------------------
    def loop_dir(self, loop_id: str) -> Path:
        return self.loops_dir / loop_id

    def loop_state_path(self, loop_id: str) -> Path:
        return self.loop_dir(loop_id) / "state.json"

    def list_loops(self) -> list[LoopState]:
        out = []
        if self.loops_dir.is_dir():
            for d in sorted(self.loops_dir.iterdir()):
                p = d / "state.json"
                if p.is_file():
                    try:
                        out.append(LoopState.load(p))
                    except (OSError, json.JSONDecodeError):
                        continue
        return out

    def save_loop(self, loop: LoopState) -> None:
        loop.updated = utcnow()
        self.loop_dir(loop.id).mkdir(parents=True, exist_ok=True)
        atomic_write(self.loop_state_path(loop.id),
                     json.dumps(loop.as_dict(), indent=2))

    def new_loop_id(self) -> str:
        existing = {l.id for l in self.list_loops()}
        n = len(existing) + 1
        while f"loop-{n:03d}" in existing:
            n += 1
        return f"loop-{n:03d}"

    # -- events -----------------------------------------------------------
    def emit(self, loop_id: str, kind: str, **detail) -> None:
        self.loop_dir(loop_id).mkdir(parents=True, exist_ok=True)
        rec = {"ts": utcnow(), "event": kind, **detail}
        with open(self.loop_dir(loop_id) / "events.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def read_events(self, loop_id: str, tail: int = 50) -> list[dict]:
        p = self.loop_dir(loop_id) / "events.jsonl"
        try:
            lines = p.read_text().splitlines()[-tail:]
            return [json.loads(l) for l in lines]
        except (OSError, json.JSONDecodeError):
            return []
