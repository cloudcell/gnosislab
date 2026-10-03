"""Harness-defect remediation tests (plan-20261002-2054Z).

W1: generated wrappers must register the imported module in
sys.modules before exec_module — @dataclass under
'from __future__ import annotations' resolves its lazy annotation
namespace via sys.modules.get(cls.__module__).__dict__.

W2: input_data_undigested exempts by-design skip reasons (not a
regular file / exceeds digest cap / IsADirectoryError legacy) and
keeps flagging genuine gaps (no reason, 'not readable at finalize'
with a real error).
"""

import hashlib
import json
import subprocess
import sys

import pytest

from ml_episteme_mcp.state.models import (
    Hypothesis,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
)
from ml_episteme_mcp.state.store import StateStore
from ml_episteme_mcp.integrity.checks import run_checks


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "t.db")
    s.connect()
    yield s
    s.close()


def _seed_trial(store, trial_id="trial-t1"):
    store.create_programme(Programme(
        id="prog-t", goal="g", constraints={}, allowed_variables=["x"],
        budget_max_trials=10, budget_max_wall_time_hours=1.0,
        status=ProgrammeStatus.active,
    ))
    store.create_hypothesis(Hypothesis(
        id="hyp-t", programme_id="prog-t",
        statement="s", failure_criterion="f", variables_involved=["x"],
    ))
    store.create_trial(Trial(
        id=trial_id, programme_id="prog-t", hypothesis_id="hyp-t",
        config_json="{}", status=TrialStatus.designed,
    ))
    store.update_trial_status(trial_id, "running")
    store.update_trial_executor_output(
        trial_id, json.dumps({"status": "completed",
                              "stdout": "{}", "exit_code": 0}))
    store.update_trial_status(trial_id, "completed")
    return trial_id


def _attach_manifest(store, trial_id, manifest: dict):
    content = json.dumps(manifest).encode()
    digest = _sha(content)
    store.create_artifact_file(
        content_hash=digest, filename="executed_code.json",
        content=content, content_type="application/json",
        captured_at="2026-10-02T00:00:00+00:00", original_path="/x",
    )
    store.create_trial_artifact(
        ta_id=f"ta-{trial_id}", trial_id=trial_id,
        content_hash=digest, filename="executed_code.json",
        artifact_type="other",
        created_at="2026-10-02T00:00:00+00:00",
    )


def _undigested_check(store):
    chk = next(
        c for c in run_checks(store)["checks"]
        if c["name"] == "input_data_undigested"
    )
    return chk


# ---- W1: sys.modules registration ---------------------------------


# The reproducer from inv-21a1eda3: lazy annotations + @dataclass is
# the exact crash shape of trial-b2596eb6.
_DATACLASS_MODULE = (
    "from __future__ import annotations\n"
    "import json\n"
    "from dataclasses import dataclass\n"
    "\n"
    "@dataclass\n"
    "class Config:\n"
    "    x: int\n"
    "    label: str\n"
    "\n"
    "def run_training(config):\n"
    "    cfg = Config(x=config['x'], label=config['label'])\n"
    "    return {'metrics': {'acc': float(cfg.x)}, 'variance': {}}\n"
)


def test_trial_wrapper_registers_module(tmp_path):
    """Generated trial wrapper must exec a __future__+dataclass
    code_ref — the sys.modules registration is the fix."""
    from ml_episteme_mcp.tools.trial import _generate_execution_wrapper

    code = tmp_path / "train.py"
    code.write_text(_DATACLASS_MODULE)
    wrapper = _generate_execution_wrapper(
        "trial-repro", {"x": 7, "label": "a"}, str(code),
    )
    assert "sys.modules[spec.name] = module" in wrapper

    wp = tmp_path / "wrapper.py"
    wp.write_text(wrapper)
    res = subprocess.run(
        [sys.executable, str(wp)],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["metrics"]["acc"] == 7.0


@pytest.mark.asyncio
async def test_generator_wrapper_registers_module(tmp_path):
    """prepare_data's generator wrapper shares the same pattern —
    drive the real end-to-end path with a __future__+dataclass
    generator."""
    from ml_episteme_mcp.clients.local_data_handler import (
        LocalDataHandler,
    )
    from ml_episteme_mcp.clients.local_executor import LocalExecutor

    gen = tmp_path / "gen.py"
    gen.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class GenConfig:\n"
        "    n: int\n"
        "\n"
        "def generate_data(config, output_path):\n"
        "    cfg = GenConfig(n=config.get('n', 3))\n"
        "    with open(output_path, 'w') as f:\n"
        "        for i in range(cfg.n):\n"
        "            f.write(f'{i}\\n')\n"
    )
    handler = LocalDataHandler(
        data_dir=tmp_path / "data", executor=LocalExecutor(),
    )
    data_ref_id = await handler.prepare_data(
        split="train", regime="generated",
        generator_code_ref=str(gen),
        generator_seed=1, generator_params={"n": 5},
    )
    assert data_ref_id.startswith("data-ref-")


# ---- W2: reason taxonomy -------------------------------------------


def test_undigested_by_design_reasons_exempt(store):
    """All three impossibility classes are accounted-for, not
    violations — the inv-21a1eda3 false-positive."""
    tid = _seed_trial(store)
    _attach_manifest(store, tid, {
        "schema_version": 2,
        "files": [
            {"path": "/dev/urandom", "role": "input_data",
             "sha256": None,
             "reason": "not a regular file (crw-rw-rw-) — not digested"},
            {"path": "/data/train.npy", "role": "input_data",
             "sha256": None,
             "reason": "exceeds digest cap (64 MiB) — not digested"},
            {"path": "/work/cwd", "role": "input_data",
             "sha256": None,
             "reason": "not readable at finalize: IsADirectoryError"},
        ],
    })
    chk = _undigested_check(store)
    assert chk["ok"] is True, chk["violations"]
    assert "3 entry(ies) skipped by recorded reason" in chk["detail"]


@pytest.mark.parametrize("reason", [
    "not readable at finalize: FileNotFoundError",
    "not readable",
    None,
])
def test_undigested_genuine_gaps_still_flag(store, reason):
    """Missing or failure reasons remain violations — a recorded
    failure is still a failed capture."""
    tid = _seed_trial(store)
    entry = {"path": "/d.json", "role": "input_data", "sha256": None}
    if reason is not None:
        entry["reason"] = reason
    _attach_manifest(store, tid, {
        "schema_version": 2, "files": [entry],
    })
    chk = _undigested_check(store)
    assert chk["ok"] is False
    assert any(v["trial_id"] == tid for v in chk["violations"])


def test_undigested_detail_denominator_preserved(store):
    """The rc-6 P5 denominator shape ('K of N … M manifest(s)')
    is asserted elsewhere — the accounted-for suffix is additive."""
    tid = _seed_trial(store)
    _attach_manifest(store, tid, {
        "schema_version": 2,
        "files": [
            {"path": "/a.json", "role": "input_data",
             "sha256": "sha256:" + "a" * 64},
            {"path": "/b.json", "role": "input_data",
             "sha256": None, "reason": "vanished"},
            {"path": "/dev/null", "role": "input_data",
             "sha256": None,
             "reason": "not a regular file (crw-rw-rw-) — not digested"},
        ],
    })
    chk = _undigested_check(store)
    assert "1 of 3" in chk["detail"]
    assert "1 manifest" in chk["detail"]
    assert "1 entry(ies) skipped" in chk["detail"]
