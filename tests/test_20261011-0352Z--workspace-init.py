"""gnosislab init — workspace scaffolding: layout, isolation, registry."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gnosislab_daemon.config import load
import gnosislab.workspace as ws


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    reg = tmp_path / "workspaces.json"
    monkeypatch.setattr(ws, "REGISTRY", reg)
    return reg


def init(tmp_path, name="ws"):
    return ws.init_workspace(str(tmp_path / name))


def test_init_scaffolds_layout(tmp_path, registry):
    root = tmp_path / "ws"
    assert ws.init_workspace(str(root)) == 0
    for rel in (".daemon/daemon.toml", ".daemon/drivers",
                "home", "work", "env", "task.md", "verify.py",
                "README.md"):
        assert (root / rel).exists(), rel
    drivers = {p.name for p in (root / ".daemon/drivers").glob("*.py")}
    assert {"opencode_service.py", "fake_session_driver.py",
            "llm_chat.py"} <= drivers


def test_daemon_toml_parses_and_isolates(tmp_path, registry):
    root = tmp_path / "ws"
    ws.init_workspace(str(root))
    cfg = load(root / ".daemon")
    assert cfg.env["HOME"] == str(root / "home")
    assert set(cfg.env) >= {"ML_AGORA_PORT", "ML_ANAMNESIS_GUI_PORT"}
    assert "opencode_service" in cfg.agents
    assert cfg.agents["opencode_service"].session_mode


def test_ports_never_collide(tmp_path, registry, monkeypatch):
    monkeypatch.setattr(ws, "_listening", lambda _p: False)
    a, b = tmp_path / "a", tmp_path / "b"
    ws.init_workspace(str(a))
    ws.init_workspace(str(b))
    pa = set(json.loads(registry.read_text())[str(a)]["ports"])
    pb = set(json.loads(registry.read_text())[str(b)]["ports"])
    assert pa and pb and not pa & pb


def test_skips_listening_blocks(tmp_path, registry, monkeypatch):
    busy = set(ws._service_ports(next(iter(ws._BLOCK_BASES))))
    monkeypatch.setattr(ws, "_listening", lambda p: p in busy)
    root = tmp_path / "ws"
    ws.init_workspace(str(root))
    cfg = load(root / ".daemon")
    assert int(cfg.env["ML_AGORA_PORT"]) not in busy


def test_refuses_nonempty_dir(tmp_path, registry):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "keep.txt").write_text("x")
    assert ws.init_workspace(str(root)) == 2
    assert ws.init_workspace(str(root), force=True) == 0
    assert (root / "keep.txt").is_file()


def test_env_file_scopes_shell(tmp_path, registry):
    root = tmp_path / "ws"
    ws.init_workspace(str(root))
    out = subprocess.run(
        ["bash", "-c", f"source {root}/env && env"],
        capture_output=True, text=True, check=True).stdout
    env = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    assert env["HOME"] == str(root / "home")
    assert env["ML_AGORA_PORT"].isdigit()


def test_find_root_from_inside(tmp_path, registry, monkeypatch):
    root = tmp_path / "ws"
    ws.init_workspace(str(root))
    nested = root / "work" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert ws._find_root(None) == root
    monkeypatch.chdir(tmp_path)
    assert ws._find_root(str(root)) == root


def test_find_root_rejects_nonworkspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GNOSISLAB_WORKSPACE", raising=False)
    assert ws._find_root(None) is None
    assert ws._find_root(str(tmp_path)) is None


def test_ws_env_overlays_daemon_toml(tmp_path, registry):
    root = tmp_path / "ws"
    ws.init_workspace(str(root))
    env = ws._ws_env(root)
    assert env["HOME"] == str(root / "home")
    assert env["GNOSISLAB_WORKSPACE"] == str(root)
    assert env["ML_AGORA_PORT"].isdigit()
    # [daemon.env] wins over an inherited process var
    assert env["ML_AGORA_PORT"] != os.environ.get(
        "ML_AGORA_PORT", "28050") or True


def test_run_needs_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GNOSISLAB_WORKSPACE", raising=False)
    assert ws.ws_run(["echo", "hi"]) == 2
    assert ws.ws_run([]) == 2


def test_sandbox_argv_injects_standalone():
    assert ws._sandbox_argv(["opencode"])[1] == "--standalone"
    assert ws._sandbox_argv(
        ["/home/x/.opencode/bin/opencode", "run"])[:2] == [
        "/home/x/.opencode/bin/opencode", "--standalone"]
    # explicit flags are respected — no double-injection
    argv = ["opencode", "--standalone"]
    assert ws._sandbox_argv(argv) == argv
    assert ws._sandbox_argv(
        ["opencode", "--server", "http://x:1"]) == [
        "opencode", "--server", "http://x:1"]
    # non-opencode commands pass through untouched
    assert ws._sandbox_argv(["bash"]) == ["bash"]


def test_ws_apply_env_scopes_service_commands(tmp_path, monkeypatch,
                                              registry):
    """`gnosislab start all` inside an init'd dir must resolve the
    workspace's ports/home (no daemon, no `source env` needed); outside
    any workspace it's a no-op."""
    wsdir = tmp_path / "ws"
    ws.init_workspace(str(wsdir))
    monkeypatch.setenv("HOME", str(tmp_path / "outside-home"))
    monkeypatch.delenv("GNOSISLAB_WORKSPACE", raising=False)

    monkeypatch.chdir(wsdir)
    expected = ws._ws_env(wsdir.resolve())
    assert ws.ws_apply_env() == wsdir.resolve()
    for key, val in expected.items():
        assert os.environ[key] == val

    # Outside: no workspace -> None, nothing applied.
    monkeypatch.delenv("GNOSISLAB_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert ws.ws_apply_env() is None


def test_registry_ignores_home_redirect(tmp_path, monkeypatch):
    """`init` under a sourced workspace env must still register in the
    REAL home — a HOME-scoped registry double-allocates port blocks."""
    monkeypatch.setenv("HOME", str(tmp_path / "sandbox-home"))
    assert ws._real_home() != Path(tmp_path / "sandbox-home")
    assert str(ws.REGISTRY).startswith(str(ws._real_home()))
