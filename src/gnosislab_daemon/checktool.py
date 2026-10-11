"""``.gnosislab-check`` — an OPTIONAL, explicitly non-authoritative
self-check helper dropped into the agent's workdir.

The daemon never executes this file (it runs ``checker_argv``
directly), and the agent may edit or delete it — harmless by
construction, since only the daemon's own check run decides. The
wrapper embeds the argv as JSON and execs it without a shell, so
checker arguments containing spaces/quotes survive intact.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

TOOL_NAME = ".gnosislab-check"

_WRAPPER = """#!/usr/bin/env python3
# Self-check helper written by gnosislab daemon. NON-AUTHORITATIVE:
# the supervisor re-runs the real checker independently after your
# turn ends; only that run decides completion. You may edit this file
# — it cannot affect the verdict.
import json, os
argv = json.loads({argv_json!r})
os.execvpe(argv[0], argv, os.environ)
"""


def write_tool(workdir: Path, checker_argv: list[str]) -> Path:
    """Write (or refresh) the self-check wrapper; returns its path."""
    p = Path(workdir) / TOOL_NAME
    p.write_text(_WRAPPER.format(argv_json=json.dumps(checker_argv)))
    os.chmod(p, 0o755)
    return p


def remove_tool(workdir: Path) -> None:
    try:
        (Path(workdir) / TOOL_NAME).unlink()
    except OSError:
        pass


INSTRUCTION = (
    "You may run ./.gnosislab-check to diagnose your work. This is an "
    "untrusted self-check: the supervisor independently runs the real "
    "acceptance check after each turn, and only that check decides "
    "completion."
)
