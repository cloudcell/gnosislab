"""SessionBackend — persistent-driver transport (plan-20261011-0152Z
Phase B2). Owns the driver subprocess lifecycle; the wire protocol
itself lives in ``session_driver.py`` (framing, handshake, ordering).

Capabilities are discovered from the driver's ``ready`` handshake —
never assumed. ``persistent=True`` is the structural fact of the
transport: the backend outlives turns and must be closed explicitly.
"""

from __future__ import annotations

from pathlib import Path

from ..session_driver import ProtocolError, SessionDriver
from . import BackendCapabilities

_CAP_KEYS = ("resume", "cancel", "fork_context")


class SessionBackend:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.driver: SessionDriver | None = None
        self._caps = BackendCapabilities(persistent=True)

    # -- lifecycle ----------------------------------------------------
    def spawn(self, argv: list[str], ready_timeout: float) -> None:
        self.driver = SessionDriver(argv, self.workdir,
                                    ready_timeout=ready_timeout)
        self.driver.spawn()

    def alive(self) -> bool:
        return self.driver is not None and self.driver.alive()

    @property
    def ready(self) -> bool:
        return self.driver is not None and self.driver.ready

    @property
    def pid(self) -> int | None:
        return self.driver.pid if self.driver else None

    @property
    def spawned_at(self) -> float:
        return self.driver.spawned_at if self.driver else 0.0

    def capabilities(self) -> BackendCapabilities:
        return self._caps

    def note_ready(self, caps: dict) -> None:
        """Fold the driver's declared capabilities into ours."""
        self._caps = BackendCapabilities(
            persistent=True,
            **{k: bool(caps.get(k)) for k in _CAP_KEYS})

    # -- protocol surface ----------------------------------------------
    def op(self, name: str, **fields) -> str:
        if self.driver is None:
            raise ProtocolError("no live driver")
        return self.driver.op(name, **fields)

    def dispatch(self, prompt: str, turn_id: str,
                 kind: str = "send") -> str | None:
        """One turn of work. ``kind='fork'`` branches the conversation
        instead of sending; the caller has already persisted dispatch
        intent and gated on capabilities."""
        if kind == "fork":
            return self.op("fork")
        return self.op("send", turn_id=turn_id, text=prompt)

    def events(self) -> list[dict]:
        return self.driver.events() if self.driver else []

    def drain_stderr(self) -> str:
        return self.driver.drain_stderr() if self.driver else ""

    def dead_exit(self) -> int | None:
        return self.driver.dead_exit() if self.driver else None

    def request_cancel(self, turn_id: str) -> None:
        """Cooperative cancel only — completion is proven by a later
        ``turn_end``/exit event, never by this call."""
        if self.driver is not None and self.driver.ready:
            self.op("cancel", turn_id=turn_id)

    def close(self, grace: float) -> None:
        if self.driver is not None:
            self.driver.close(grace)
            self.driver = None
        self._caps = BackendCapabilities(persistent=True)
