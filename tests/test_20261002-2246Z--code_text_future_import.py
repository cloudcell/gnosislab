"""Regression: code:// bundles whose sealed code starts with
`from __future__ import annotations` must execute.

Before the fix, _generate_execution_wrapper inlined the sealed
code_text verbatim after the wrapper preamble — so a `__future__`
import (which must occur at the top of a file) was a SyntaxError.
Recorded live as trial-85750c7b. The fix materializes the primary as
a real file (user_training.py) and imports it through
spec_from_file_location + sys.modules — the same loader the file-path
branch uses.

These tests exercise the generated wrapper standalone via
`python3 -c` (no __file__ → mkdtemp fallback) and via a real file
(__file__ → artifact-dir-style base).
"""

import json
import subprocess
import sys

from ml_episteme_mcp.tools.trial import _generate_execution_wrapper

_FUTURE_DATACLASS = (
    "from __future__ import annotations\n"
    "from dataclasses import dataclass\n"
    "\n"
    "@dataclass\n"
    "class Config:\n"
    "    x: int\n"
    "    label: str\n"
    "\n"
    "def run_training(config):\n"
    "    cfg = Config(x=config['x'], label=config['label'])\n"
    "    return {'metrics': {'acc': float(cfg.x) + len(cfg.label)}}\n"
)

_MAIN_GUARD = (
    "def run_training(config):\n"
    "    return {'metrics': {'ok': 1.0}}\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    raise SystemExit('main guard must not fire under import')\n"
)

_FILE_USER = (
    "from pathlib import Path\n"
    "def run_training(config):\n"
    "    # __file__ must exist — inlined snippets had no __file__.\n"
    "    return {'metrics': {'named': float(Path(__file__).name == 'user_training.py')}}\n"
)


def test_future_import_code_text_executes_via_dash_c():
    """The trial-85750c7b repro: __future__ + dataclass as code_text.

    Inlining put the __future__ line after the wrapper preamble →
    SyntaxError. Materialized as a file it is a normal module.
    """
    wrapper = _generate_execution_wrapper(
        "trial-future", {"x": 7, "label": "ab"},
        "code://sha256:deadbeef",
        code_text=_FUTURE_DATACLASS,
    )
    res = subprocess.run(
        ["python3", "-c", wrapper],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["metrics"]["acc"] == 9.0


def test_future_import_code_text_executes_via_file(tmp_path):
    """Same repro but run as a real script — __file__ → dirname, so
    user_training.py materializes next to the wrapper (the artifact
    dir under the real executor)."""
    wrapper = _generate_execution_wrapper(
        "trial-future", {"x": 7, "label": "ab"},
        "code://sha256:deadbeef",
        code_text=_FUTURE_DATACLASS,
    )
    wp = tmp_path / "wrapper.py"
    wp.write_text(wrapper)
    res = subprocess.run(
        [sys.executable, str(wp)],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    # The materialized primary must be a real file next to the wrapper
    materialized = tmp_path / "user_training.py"
    assert materialized.exists()
    assert materialized.read_text(encoding="utf-8") == _FUTURE_DATACLASS
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["metrics"]["acc"] == 9.0


def test_main_guard_does_not_fire():
    """A `__main__` guard inside the snippet must NOT execute —
    under inlining the snippet ran in __main__ and the guard fired."""
    wrapper = _generate_execution_wrapper(
        "trial-guard", {}, "code://sha256:guard",
        code_text=_MAIN_GUARD,
    )
    res = subprocess.run(
        ["python3", "-c", wrapper],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["metrics"]["ok"] == 1.0


def test_dunder_file_available_in_snippet():
    """`__file__` inside the snippet resolves to user_training.py —
    inlined snippets raised NameError."""
    wrapper = _generate_execution_wrapper(
        "trial-file", {}, "code://sha256:file",
        code_text=_FILE_USER,
    )
    res = subprocess.run(
        ["python3", "-c", wrapper],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["metrics"]["named"] == 1.0


def test_deps_still_resolve_for_materialized_primary():
    """A dep import inside the materialized primary still resolves
    through _deps/ — materialization must not regress multi-file."""
    primary = (
        "from __future__ import annotations\n"
        "from src.trainer import compute\n"
        "def run_training(config):\n"
        "    return {'metrics': {'acc': compute()}}\n"
    )
    dep = "def compute():\n    return 0.9\n"
    wrapper = _generate_execution_wrapper(
        "trial-deps", {}, "code://sha256:prim",
        code_text=primary,
        dep_snippets=[("/w/src/trainer.py", dep)],
    )
    res = subprocess.run(
        ["python3", "-c", wrapper],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["metrics"]["acc"] == 0.9
