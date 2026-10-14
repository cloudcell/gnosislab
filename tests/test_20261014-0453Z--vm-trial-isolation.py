"""VM-only hardening: trusted Episteme state and network are not trial inputs.

These structure/policy tests complement actual bubblewrap integration tests on
a disposable GnosisLab-VM. No live VM or external network is needed here.
"""
import os
import shutil
from pathlib import Path

import pytest

from ml_episteme_mcp.clients.local_executor import LocalExecutor


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/" + tool)
    monkeypatch.setenv("ML_EPISTEME_TRIAL_ISOLATION", "enforced")
    ex = LocalExecutor(sandbox="full", trace_reads="off")
    state = tmp_path / "state"
    artifact = state / ".ml-episteme" / "artifacts" / "prog" / "trial"
    artifact.mkdir(parents=True)
    wrapper = artifact / "wrapper.py"
    wrapper.write_text("print('ok')\n")
    (state / "ingest.env").write_text("secret\n")
    ex._vm_state_root = state
    return ex, state, artifact, wrapper


def test_vm_mode_fail_closed_without_full_bwrap(monkeypatch):
    monkeypatch.setenv("ML_EPISTEME_TRIAL_ISOLATION", "enforced")
    monkeypatch.setattr(shutil, "which", lambda tool: None)
    for mode in ("auto", "minimal", "none", "full"):
        with pytest.raises(RuntimeError, match="requires sandbox=full"):
            LocalExecutor(sandbox=mode)
    monkeypatch.setenv("ML_EPISTEME_TRIAL_ISOLATION", "unexpected")
    with pytest.raises(ValueError, match="ML_EPISTEME_TRIAL_ISOLATION"):
        LocalExecutor(sandbox="full")


def test_vm_trial_mounts_mask_state_and_network(isolated):
    ex, state, artifact, wrapper = isolated
    args = ex._sandbox_argv(["python3", str(wrapper)], artifact, wrapper)
    for opt in ("--unshare-net", "--unshare-pid", "--unshare-ipc", "--cap-drop"):
        assert opt in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    hidden = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--tmpfs"]
    assert str(state) in hidden
    assert args.count(str(artifact)) >= 2
    assert [str(state), str(state)] not in [
        args[i + 1 : i + 3] for i, a in enumerate(args[:-2]) if a == "--bind"
    ]
    assert args.index("--tmpfs") < args.index("--bind")
    assert "--proc" in args


def test_vm_trial_rejects_sensitive_input_mounts(isolated):
    ex, state, artifact, wrapper = isolated
    state_db = state / "state.db"
    state_db.write_text("db")
    for path in (state, state_db, state.parent, state / "ingest.env"):
        with pytest.raises(ValueError, match="protected trusted state"):
            ex._sandbox_argv(
                ["python3", str(wrapper)], artifact, wrapper,
                extra_ro_paths=[str(path)],
            )
        with pytest.raises(ValueError, match="protected trusted state"):
            ex._sandbox_argv(
                ["python3", str(wrapper)], artifact, wrapper,
                extra_rw_paths=[str(path)],
            )
    # Attempting a sealed-code overlay over the trusted DB must fail.
    with pytest.raises(ValueError, match="protected trusted state"):
        ex._sandbox_argv(["python3"], artifact, wrapper,
                         overlay_ro=[(str(wrapper), str(state_db))])


def test_vm_trial_rejects_symlink_alias_to_state(isolated, tmp_path):
    ex, state, artifact, wrapper = isolated
    alias = tmp_path / "alias"
    alias.symlink_to(state, target_is_directory=True)
    with pytest.raises(ValueError, match="protected trusted state"):
        ex._sandbox_argv(
            ["python3", str(wrapper)], artifact, wrapper,
            extra_ro_paths=[str(alias / "ingest.env")],
        )


def test_vm_trial_allows_own_artifact_and_external_data(isolated, tmp_path):
    ex, _, artifact, wrapper = isolated
    extra = tmp_path / "dataset"
    extra.mkdir()
    args = ex._sandbox_argv(
        ["python3", str(wrapper)], artifact, wrapper,
        extra_ro_paths=[str(extra)],
        extra_rw_paths=[str(artifact / "tmp")],
    )
    assert "--ro-bind" in args
    assert str(extra) in args


def test_regular_standalone_executor_unchanged(monkeypatch):
    monkeypatch.delenv("ML_EPISTEME_TRIAL_ISOLATION", raising=False)
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/" + tool)
    ex = LocalExecutor(sandbox="full")
    args = ex._sandbox_argv(["python3"], None, Path("/tmp/wrapper.py"))
    assert "--unshare-net" not in args
    assert ex._vm_isolation is False
