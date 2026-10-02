"""env_ref mount regression — plan-20261002-2152Z W3.

env_ref accepts a venv dir or a Python executable. Before the fix the
executable form resolved fine but bwrap died at mount: a file
--ro-bind onto a symlink destination fails (venv interpreters are
always symlinks), so trial-71b6004f recorded sandbox_setup_failed.
The fix binds the env *directory* — the venv root for an executable
ref, resolve()d for a dir ref — never the file.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

HAS_BWRAP = shutil.which("bwrap") is not None

ROOT = Path(__file__).resolve().parent.parent
TRAIN_STUB = str(ROOT / "tests" / "fixtures" / "train_stub.py")


async def _call(mcp, name, args):
    result = await mcp.call_tool(name, args)
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


def _stub_venv(tmp_path) -> Path:
    """A minimal venv-shaped dir: bin/python + bin/python3 symlinks to
    the current interpreter (no pyvenv.cfg needed — resolution only
    checks the file exists and is executable)."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    (venv / "bin" / "python3").symlink_to(sys.executable)
    return venv


def _server(tmp_path):
    from ml_episteme_mcp.clients.adaptor import create_stub_adaptor
    from ml_episteme_mcp.clients.local_executor import LocalExecutor
    from ml_episteme_mcp.server import create_server
    from ml_episteme_mcp.state.store import StateStore

    store = StateStore(str(tmp_path / "s.db"))
    store.connect()
    adaptor = create_stub_adaptor()
    adaptor.set_executor(LocalExecutor(
        timeout=60,
        sandbox="full" if HAS_BWRAP else "none",
        trace_reads="off",
        sealed_enforcement="deny" if HAS_BWRAP else "audit",
    ))
    mcp = create_server(store, adaptor, enforcement_config={
        "status_freshness_seconds": 0})
    return mcp, store


async def _run_trial_with_env(mcp, env_ref: str) -> dict:
    """Minimal programme → hypothesis → trial → bundle(env_ref) → run."""
    prog = await _call(mcp, "create_programme", {
        "goal": "env_ref mount probe",
        "constraints": {},
        "allowed_variables": ["lr"],
        "budget": {"max_trials": 5, "max_wall_time_hours": 1.0},
    })
    pid = prog["programme_id"]
    hyp = await _call(mcp, "formulate_hypothesis", {
        "programme_id": pid,
        "statement": "env_ref selects the interpreter",
        "failure_criterion": "trial fails to complete",
        "variables_involved": ["lr"],
    })
    trial = await _call(mcp, "design_experiment", {
        "programme_id": pid,
        "hypothesis_id": hyp["hypothesis_id"],
        "config": {"lr": 0.001},
    })
    tid = trial["trial_id"]
    bundle = await _call(mcp, "capture_bundle", {
        "trial_id": tid,
        "code_ref": TRAIN_STUB,
        "env_ref": env_ref,
        "seeds": [0],
        "splits": {"train": "inline"},
    })
    assert "error" not in bundle, bundle
    return await _call(mcp, "run_trial", {
        "programme_id": pid, "trial_id": tid})


async def test_env_ref_dir_selects_interpreter(tmp_path):
    """Directory form — the form that already worked — still pins the
    interpreter to <dir>/bin/python."""
    mcp, store = _server(tmp_path)
    try:
        venv = _stub_venv(tmp_path)
        r = await _run_trial_with_env(mcp, str(venv))
        assert r.get("status") == "completed", r
        out = json.loads(r["executor_output"])
        assert out["python_exe"] == str(venv / "bin" / "python")
    finally:
        store.close()


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap not installed")
async def test_env_ref_executable_resolves_to_venv_root(tmp_path):
    """The F1 repro: env_ref=<venv>/bin/python3 (a symlink) must not die
    at mount — the fix binds the venv root dir, never the file."""
    mcp, store = _server(tmp_path)
    try:
        venv = _stub_venv(tmp_path)
        r = await _run_trial_with_env(mcp, str(venv / "bin" / "python3"))
        assert r.get("status") == "completed", r
        out = json.loads(r["executor_output"])
        assert "sandbox_setup_failed" not in out.get("stderr", ""), out
        assert out["python_exe"] == str(venv / "bin" / "python3")
    finally:
        store.close()


async def test_env_ref_non_path_tag_falls_back(tmp_path):
    """A non-path tag ('python:3.12') is provenance only — the executor
    default interpreter runs. Pins the documented fallback."""
    mcp, store = _server(tmp_path)
    try:
        r = await _run_trial_with_env(mcp, "python:3.12")
        assert r.get("status") == "completed", r
        out = json.loads(r["executor_output"])
        assert out["python_exe"] == sys.executable
    finally:
        store.close()
