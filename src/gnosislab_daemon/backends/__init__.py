"""Agent backends — the transport seam between LoopRunner and a
backend agent (plan-20261011-0152Z, Phase B1).

Two transports fill the same contract:

- ``ExecBackend``   — one CLI subprocess per turn; process exit is the
                      turn boundary. Strong isolation; cold-start cost.
- ``SessionBackend`` — a persistent driver process controlled over
                      stdio JSONL; a ``turn_end`` event is the turn
                      boundary. The driver owns every vendor detail.

Neither boundary is a verdict. The checker — and only the checker —
decides task completion; a backend reports *that the turn ended* and
*how it claimed to end*, never *whether the task is done*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BackendCapabilities:
    """What a backend can provably do. Capabilities are discovered
    (session: driver's ready handshake) or structural (exec: profile
    argv templates) — never assumed."""
    resume: bool = False        # continue the same conversation/context
    fork_context: bool = False  # branch conversation state
    cancel: bool = False        # in-flight turns can be interrupted
    persistent: bool = False    # backend survives the turn boundary


@runtime_checkable
class AgentBackend(Protocol):
    """The event-stream contract a session-mode backend fulfils.

    ``dispatch`` starts or continues one turn of work.
    ``events`` drains everything observed since last call.
    ``request_cancel`` asks the backend to stop current work;
    completion of a cancel is only proven by a subsequent terminal
    event (``turn_end``/``exit``) — the request itself is a receipt.
    ``close`` releases the backend entirely (cooperative first).

    ``ExecBackend`` is deliberately NOT an AgentBackend: the exec
    transport is polled (spawn/drain/kill verbs), not streamed — a
    fake ``events()`` on it would pretend a stream that doesn't
    exist. SessionBackend implements the contract in full.
    """

    def capabilities(self) -> BackendCapabilities: ...
    def dispatch(self, prompt: str, turn_id: str,
                 kind: str) -> str | None: ...
    def events(self) -> list[dict]: ...
    def request_cancel(self, turn_id: str) -> None: ...
    def close(self, grace: float) -> None: ...
    def alive(self) -> bool: ...


from .exec_backend import ExecBackend       # noqa: E402
from .session_backend import SessionBackend  # noqa: E402

__all__ = ["AgentBackend", "BackendCapabilities",
           "ExecBackend", "SessionBackend"]
