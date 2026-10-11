"""Configuration: agent profiles + per-instance daemon settings.

- Agent profiles: user-global ``~/.config/gnosislab/daemon.toml``
  (``[agents.*]`` tables) — shared by every daemon the user runs.
- Instance settings: ``<state-root>/daemon.toml`` (``[daemon]``) —
  listen/port/limits for that daemon only.

Command templates are argv lists; ``{prompt}``/``{session_id}``
substitute as single arguments — never shell strings.
"""

from __future__ import annotations

import re
try:
    import tomllib
except ModuleNotFoundError:     # Python < 3.11
    import tomli as tomllib
from dataclasses import dataclass, field
from pathlib import Path

GLOBAL_CONFIG = Path.home() / ".config" / "gnosislab" / "daemon.toml"
DEFAULT_LISTEN = "127.0.0.1"
DEFAULT_PORT = 38100
DEFAULT_MAX_CONCURRENT = 1
DEFAULT_LIMITS = {
    "max_turns": 20,
    "max_seconds": 0,            # 0 = off: wall-clock is not a verdict —
    # a 6-hour training run is working, not hung
    "stagnation": 4,
    "agent_timeout": 0,          # 0 = off: never kill the agent on time
    "agent_idle_seconds": 0,     # 0 = off: silence alone never kills —
    # long quiet tool runs are legitimate; enable for agents that hang
    # on permission prompts or stalled streams
    "check_timeout": 300,
    "feedback_bytes": 8192,
    "output_bytes": 65536,
}


@dataclass
class AgentProfile:
    name: str
    start: list[str]
    resume: list[str] | None = None
    session_id_regex: str | None = None

    def argv(self, kind: str, prompt: str,
             session_id: str | None = None) -> list[str] | None:
        """Render a command template. Substitutions are whole-argument."""
        tmpl = self.start if kind == "start" else self.resume
        if tmpl is None:
            return None
        out = []
        for arg in tmpl:
            arg = arg.replace("{prompt}", prompt)
            if "{session_id}" in arg:
                if session_id is None:
                    return None
                arg = arg.replace("{session_id}", session_id)
            out.append(arg)
        return out

    def capture_session_id(self, text: str) -> str | None:
        if not self.session_id_regex:
            return None
        m = re.search(self.session_id_regex, text)
        return m.group(1) if m else None


@dataclass
class DaemonConfig:
    listen: str = DEFAULT_LISTEN
    port: int = DEFAULT_PORT
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    env: dict = field(default_factory=dict)   # [daemon.env] — applied
    # to os.environ at serve() before gnosislab.cli is imported: scopes
    # HOME, ML_*_PORT and friends so this daemon's service/export ops
    # cannot touch another stack's ports or ~/.ml-* state
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    agents: dict[str, AgentProfile] = field(default_factory=dict)


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ValueError(f"{path}: invalid TOML — {exc}") from exc


def _parse_agents(raw: dict, path: Path) -> dict[str, AgentProfile]:
    agents: dict[str, AgentProfile] = {}
    for name, spec in raw.items():
        start = spec.get("start")
        if not isinstance(start, list) or not all(
                isinstance(a, str) for a in start):
            raise ValueError(
                f"{path}: agents.{name}.start must be an argv list")
        resume = spec.get("resume")
        if resume is not None and (
                not isinstance(resume, list)
                or not all(isinstance(a, str) for a in resume)):
            raise ValueError(
                f"{path}: agents.{name}.resume must be an argv list")
        agents[name] = AgentProfile(
            name=name, start=start, resume=resume,
            session_id_regex=spec.get("session_id_regex"))
    return agents


def load_agents(path: Path = GLOBAL_CONFIG) -> dict[str, AgentProfile]:
    return _parse_agents(_load_toml(path).get("agents", {}), path)


def load(state_root: Path) -> DaemonConfig:
    """Per-instance config from <state_root>/daemon.toml + global agents.

    ``[agents.*]`` in the state root's daemon.toml overrides/extends the
    user-global profiles — an experiment can carry its own agent
    definitions without touching shared config."""
    root_toml = _load_toml(state_root / "daemon.toml")
    raw = root_toml.get("daemon", {})
    limits = dict(DEFAULT_LIMITS)
    limits.update(raw.get("limits", {}))
    agents = load_agents()
    agents.update(_parse_agents(root_toml.get("agents", {}),
                                state_root / "daemon.toml"))
    return DaemonConfig(
        listen=str(raw.get("listen", DEFAULT_LISTEN)),
        port=int(raw.get("port", DEFAULT_PORT)),
        max_concurrent=int(raw.get("max_concurrent",
                                   DEFAULT_MAX_CONCURRENT)),
        env={str(k): str(v)
             for k, v in raw.get("env", {}).items()},
        limits=limits,
        agents=agents,
    )
