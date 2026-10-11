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
