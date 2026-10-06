"""Evidence-policy regimes (plan-20261006-2058Z, review-20261006-2134Z).

Preregistered evidence admission: the default repeated_measurement
regime still requires measured variance, and a trial may declare
single_measurement (with a sealed rationale) at design_experiment.
The declaration rides the trial, is snapshotted onto the sealed bundle
at capture, and is snapshotted again onto each admitted observation.

Coverage map:
  - model layer: policy parse/canonicalize, NULL → default
  - validators: check_evidence_policy / check_uncertainty_requirements
  - store: column roundtrip, additive migration on old-schema DBs
  - integrity: trial↔bundle↔observation divergence, missing rationale,
    unmeasured variance under the default regime, all-zero advisory
  - HTTP lifecycle: design → capture → run → record_observation under
    both regimes; refusal messages; sealed immutability; the absent
    observation-level bypass
  - resources: programme://{id}/trials exposes the declared policy
  - views: null variance renders "not measured" without crashing
  - archive: evidence_policy_json survives the archive round-trip
"""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from conftest import (
    call_tool_http,
    list_tools_http,
    read_resource_http,
    make_programme,
    make_hypothesis,
    make_trial,
)

from ml_episteme_mcp.archive import ArchiveConfig, Archiver
from ml_episteme_mcp.enforcement.commitments import (
    check_evidence_policy,
    check_uncertainty_requirements,
)
from ml_episteme_mcp.integrity.checks import _check_evidence_policy_consistency
from ml_episteme_mcp.state.models import (
    Bundle,
    CodeSnippet,
    EvidenceRegime,
    EVIDENCE_POLICY_VERSION,
    Hypothesis,
    Observation,
    Programme,
    ProgrammeStatus,
    Trial,
    TrialStatus,
    canonical_evidence_policy,
    default_evidence_policy,
    parse_evidence_policy,
)
from ml_episteme_mcp.state.store import StateStore

FIXTURES = Path(__file__).parent / "fixtures"
TRAIN_STUB = str(FIXTURES / "train_stub.py")

SINGLE_MEASUREMENT = {
    "version": 1,
    "regime": "single_measurement",
    "rationale": "The comparison is deterministic for the sealed substrate.",
}


# ---------- fixtures ----------


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "test.db")
    s.connect()
    yield s
    s.close()


def _mk_programme(store, pid="prog-ep"):
    store.create_programme(Programme(
        id=pid, goal="evidence-policy test", constraints={},
        allowed_variables=["lr"], budget_max_trials=10,
        budget_max_wall_time_hours=1.0,
    ))
    store.create_hypothesis(Hypothesis(
        id=f"hyp-{pid}", programme_id=pid, statement="lr affects acc",
        failure_criterion="acc < 0.5", variables_involved=["lr"],
    ))


def _mk_trial(store, tid="trial-ep", pid="prog-ep", policy=None):
    store.create_trial(Trial(
        id=tid, programme_id=pid, hypothesis_id=f"hyp-{pid}",
        config_json='{"lr": 0.01}',
        evidence_policy_json=json.dumps(policy) if policy else None,
    ))


def _mk_bundle(store, tid="trial-ep", bid="bundle-ep", policy=None):
    store.create_bundle(Bundle(
        id=bid, trial_id=tid, code_ref="/tmp/train.py",
        code_hash="sha256:ep1", env_ref="python:3.12",
        seeds_json="[42]", splits_json='{"train": 0.8}',
        evidence_policy_json=json.dumps(policy) if policy else None,
    ))
    store.link_bundle(tid, bid)


# ---------- model layer ----------


class TestPolicyModel:
    def test_default_policy_shape(self):
        p = default_evidence_policy()
        assert p["version"] == EVIDENCE_POLICY_VERSION
        assert p["regime"] == EvidenceRegime.repeated_measurement.value

    def test_null_reads_as_default(self):
        """Legacy rows (NULL column) are never reinterpreted."""
        assert parse_evidence_policy(None) == default_evidence_policy()

    def test_parse_malformed_raises(self):
        with pytest.raises(ValueError):
            parse_evidence_policy("{not json")

    def test_canonical_fills_version_and_regime(self):
        p = canonical_evidence_policy({"regime": "single_measurement",
                                       "rationale": "r"})
        assert p["version"] == EVIDENCE_POLICY_VERSION
        assert p["rationale"] == "r"


class TestValidators:
    def test_undeclared_policy_is_valid(self):
        assert check_evidence_policy(None) is None

    def test_default_regime_explicit_is_valid(self):
        assert check_evidence_policy(
            {"regime": "repeated_measurement"}) is None

    def test_single_measurement_with_rationale_is_valid(self):
        assert check_evidence_policy(dict(SINGLE_MEASUREMENT)) is None

    @pytest.mark.parametrize("bad", [
        {"regime": "nonsense"},
        {"version": 99, "regime": "repeated_measurement"},
        {"regime": "single_measurement"},
        {"regime": "single_measurement", "rationale": ""},
        {"regime": "single_measurement", "rationale": "   "},
        "not-a-dict",
        [{"regime": "single_measurement"}],
    ])
    def test_bad_policies_rejected(self, bad):
        err = check_evidence_policy(bad)
        assert err is not None
        assert "admission" in err

    def test_uncertainty_positive_variance_accepted(self):
        assert check_uncertainty_requirements({"m": 0.01}) is None

    def test_uncertainty_measured_zero_accepted(self):
        """The honest trichotomy: measured zero is not 'not measured'."""
        assert check_uncertainty_requirements({"m": 0.0}) is None

    def test_uncertainty_unmeasured_rejected(self):
        err = check_uncertainty_requirements(None)
        assert err is not None
        # The refusal names the preregistered escape hatch.
        assert "single_measurement" in err

    def test_uncertainty_empty_mapping_rejected(self):
        assert check_uncertainty_requirements({}) is not None

    def test_uncertainty_non_mapping_rejected(self):
        assert check_uncertainty_requirements([0.1]) is not None


# ---------- store ----------


class TestStoreRoundtrip:
    def test_policy_columns_roundtrip(self, store):
        _mk_programme(store)
        _mk_trial(store, policy=SINGLE_MEASUREMENT)
        _mk_bundle(store, policy=SINGLE_MEASUREMENT)
        store.create_observation(Observation(
            id="obs-ep", trial_id="trial-ep",
            metrics_json='{"m": 1.0}', variance_json="null",
            spatiotemporal_region="test",
            evidence_policy_json=json.dumps(SINGLE_MEASUREMENT),
        ))
        assert json.loads(
            store.get_trial("trial-ep").evidence_policy_json
        ) == SINGLE_MEASUREMENT
        assert json.loads(
            store.get_bundle("bundle-ep").evidence_policy_json
        ) == SINGLE_MEASUREMENT
        obs = store.get_observation("obs-ep")
        assert json.loads(obs.variance_json) is None  # not measured
        assert json.loads(obs.evidence_policy_json) == SINGLE_MEASUREMENT

    def test_legacy_rows_read_default(self, store):
        _mk_programme(store)
        _mk_trial(store)  # no policy → NULL
        _mk_bundle(store)
        assert parse_evidence_policy(
            store.get_trial("trial-ep").evidence_policy_json
        )["regime"] == "repeated_measurement"
        assert parse_evidence_policy(
            store.get_bundle("bundle-ep").evidence_policy_json
        )["regime"] == "repeated_measurement"

    def test_migration_adds_columns_to_old_schema(self, tmp_path):
        """A pre-regimes DB gets evidence_policy_json via ALTER TABLE."""
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE trials (id TEXT PRIMARY KEY, programme_id TEXT,
                hypothesis_id TEXT, config_json TEXT, bundle_id TEXT,
                status TEXT);
            CREATE TABLE bundles (id TEXT PRIMARY KEY, trial_id TEXT,
                code_ref TEXT, env_ref TEXT, seeds_json TEXT,
                splits_json TEXT);
            CREATE TABLE observations (id TEXT PRIMARY KEY,
                trial_id TEXT, metrics_json TEXT, variance_json TEXT,
                spatiotemporal_region TEXT, created_at TEXT);
        """)
        conn.commit()
        conn.close()
        s = StateStore(db)
        s.connect()
        try:
            for table in ("trials", "bundles", "observations"):
                cols = {r["name"] for r in s._fetchall(
                    f"PRAGMA table_info({table})")}
                assert "evidence_policy_json" in cols, table
        finally:
            s.close()


# ---------- integrity ----------


class TestIntegrityCheck:
    def _run(self, store):
        return _check_evidence_policy_consistency(store)

    def test_clean_state_passes(self, store):
        _mk_programme(store)
        _mk_trial(store, policy=SINGLE_MEASUREMENT)
        _mk_bundle(store, policy=SINGLE_MEASUREMENT)
        res = self._run(store)
        assert res["violations"] == []

    def test_trial_bundle_divergence_detected(self, store):
        _mk_programme(store)
        _mk_trial(store, policy=SINGLE_MEASUREMENT)
        _mk_bundle(store)  # sealed default — diverges from declaration
        res = self._run(store)
        assert any("diverges" in v for v in res["violations"])

    def test_missing_rationale_detected(self, store):
        _mk_programme(store)
        _mk_trial(store, policy={"version": 1,
                                 "regime": "single_measurement"})
        res = self._run(store)
        assert any("rationale" in v for v in res["violations"])

    def test_unmeasured_variance_under_default_detected(self, store):
        """A hand-constructed observation that enforcement would never
        admit must be caught at audit time."""
        _mk_programme(store)
        _mk_trial(store)
        _mk_bundle(store)
        store.create_observation(Observation(
            id="obs-bad", trial_id="trial-ep",
            metrics_json='{"m": 1.0}', variance_json="null",
            spatiotemporal_region="test",
            evidence_policy_json=json.dumps(default_evidence_policy()),
        ))
        res = self._run(store)
        assert any("unmeasured variance" in v for v in res["violations"])

    def test_measured_zero_is_advisory_not_violation(self, store):
        _mk_programme(store)
        _mk_trial(store)
        _mk_bundle(store)
        store.create_observation(Observation(
            id="obs-zero", trial_id="trial-ep",
            metrics_json='{"m": 1.0}', variance_json='{"m": 0.0}',
            spatiotemporal_region="test",
            evidence_policy_json=json.dumps(default_evidence_policy()),
        ))
        res = self._run(store)
        assert res["violations"] == []
        assert any("all-zero" in a for a in res.get("advisories", []))

    def test_malformed_policy_json_detected(self, store):
        _mk_programme(store)
        store.create_trial(Trial(
            id="trial-ep", programme_id="prog-ep",
            hypothesis_id="hyp-prog-ep", config_json="{}",
            evidence_policy_json="{malformed",
        ))
        res = self._run(store)
        assert any("malformed" in v for v in res["violations"])

    def test_unknown_regime_detected(self, store):
        _mk_programme(store)
        _mk_trial(store, policy={"version": 1, "regime": "bogus"})
        res = self._run(store)
        assert any("unknown regime" in v for v in res["violations"])


# ---------- views ----------


class TestViews:
    def test_trial_view_renders_unmeasured_variance(self, store):
        """variance_json='null' must render 'not measured', not crash."""
        from ml_episteme_mcp.observability.views.trial import (
            render_trial_detail,
        )
        _mk_programme(store)
        _mk_trial(store, policy=SINGLE_MEASUREMENT)
        _mk_bundle(store, policy=SINGLE_MEASUREMENT)
        store.create_observation(Observation(
            id="obs-null", trial_id="trial-ep",
            metrics_json='{"m": 1.0}', variance_json="null",
            spatiotemporal_region="test",
            evidence_policy_json=json.dumps(SINGLE_MEASUREMENT),
        ))
        resp = render_trial_detail(store, "prog-ep", "trial-ep")
        html = resp.body.decode()
        assert "not measured" in html
        assert "single_measurement" in html

    def test_trial_view_renders_measured_variance(self, store):
        from ml_episteme_mcp.observability.views.trial import (
            render_trial_detail,
        )
        _mk_programme(store)
        _mk_trial(store)
        _mk_bundle(store)
        store.create_observation(Observation(
            id="obs-var", trial_id="trial-ep",
            metrics_json='{"m": 1.0}', variance_json='{"m": 0.01}',
            spatiotemporal_region="test",
        ))
        resp = render_trial_detail(store, "prog-ep", "trial-ep")
        html = resp.body.decode()
        assert "0.0100" in html
        assert "not measured" not in html


# ---------- archive ----------


class TestArchive:
    def test_archive_roundtrip_preserves_policy(self, store, tmp_path):
        """Policy on trial/bundle/observation survives the archive."""
        arch_dir = tmp_path / "archives"
        arch_dir.mkdir()
        archiver = Archiver(store, ArchiveConfig(
            enabled=True, batch_size=10, archive_dir=arch_dir))
        _mk_programme(store)
        store.create_code_snippet(CodeSnippet(
            code_hash="sha256:ep1", code_text="x",
            captured_at="2026-10-06T00:00:00Z", size_bytes=1))
        _mk_trial(store, policy=SINGLE_MEASUREMENT)
        _mk_bundle(store, policy=SINGLE_MEASUREMENT)
        store.create_observation(Observation(
            id="obs-arc", trial_id="trial-ep",
            metrics_json='{"m": 1.0}', variance_json="null",
            spatiotemporal_region="test",
            evidence_policy_json=json.dumps(SINGLE_MEASUREMENT),
        ))
        store.update_programme_status("prog-ep", "abandoned")
        result = archiver.archive_programme("prog-ep")
        assert result["archived"] is True

        # Read the archive DB directly — the columns must be present
        # and populated, not silently dropped.
        archives = list(arch_dir.glob("*.db"))
        assert len(archives) == 1
        conn = sqlite3.connect(archives[0])
        conn.row_factory = sqlite3.Row
        t = conn.execute(
            "SELECT evidence_policy_json FROM trials").fetchone()
        b = conn.execute(
            "SELECT evidence_policy_json FROM bundles").fetchone()
        o = conn.execute(
            "SELECT evidence_policy_json, variance_json "
            "FROM observations").fetchone()
        conn.close()
        assert json.loads(t["evidence_policy_json"]) == SINGLE_MEASUREMENT
        assert json.loads(b["evidence_policy_json"]) == SINGLE_MEASUREMENT
        assert json.loads(o["evidence_policy_json"]) == SINGLE_MEASUREMENT
        assert json.loads(o["variance_json"]) is None


# ---------- export ----------


class TestExport:
    def test_rocrate_retains_policy_and_null_variance(
            self, store, tmp_path):
        """The sealed regime + 'not measured' variance survive the
        RO-Crate export — provenance cannot silently reinterpret the
        observation."""
        _mk_programme(store)
        store.create_code_snippet(CodeSnippet(
            code_hash="sha256:ep1", code_text="x",
            captured_at="2026-10-06T00:00:00Z", size_bytes=1))
        _mk_trial(store, policy=SINGLE_MEASUREMENT)
        _mk_bundle(store, policy=SINGLE_MEASUREMENT)
        store.create_observation(Observation(
            id="obs-x", trial_id="trial-ep",
            metrics_json='{"m": 1.0}', variance_json="null",
            spatiotemporal_region="test",
            evidence_policy_json=json.dumps(SINGLE_MEASUREMENT),
        ))

        # WAL data must checkpoint into the main file before the
        # exporter's immutable=1 read-only open can see it.
        store.close()

        from gnosislab_export.common.snapshot import load_snapshot
        from gnosislab_export.ro_crate.exporter import export_rocrate

        snap = load_snapshot(episteme_db=store.path,
                             anamnesis_db=None, programme_id="prog-ep")
        obs = snap.observations[0]
        assert obs.variance is None  # not measured — never fabricated
        assert obs.evidence_policy["regime"] == "single_measurement"
        assert snap.trials[0].evidence_policy == SINGLE_MEASUREMENT
        assert snap.bundles[0].evidence_policy == SINGLE_MEASUREMENT

        crate = export_rocrate(snap, tmp_path / "crate")
        results = json.loads(
            (crate / "payload" / "results"
             / f"{obs.trial_id}.json").read_text())
        rec = results["observations"][0]
        assert rec["variance"] is None
        assert rec["evidence_policy"]["rationale"] == (
            SINGLE_MEASUREMENT["rationale"])
        graph = json.loads(
            (crate / "ro-crate-metadata.json").read_text())["@graph"]
        trial_ent = next(e for e in graph
                         if e.get("@id") == "#trial/trial-ep")
        prop = next(p for p in trial_ent["additionalProperty"]
                    if p["name"] == "evidence_policy")
        assert json.loads(prop["value"])["regime"] == (
            "single_measurement")

    def test_export_legacy_store_defaults_policy(
            self, store, tmp_path):
        """A trial without a declared policy exports as
        repeated_measurement — old rows are never reinterpreted."""
        _mk_programme(store)
        _mk_trial(store)  # NULL policy
        _mk_bundle(store)
        store.close()  # checkpoint WAL for the immutable=1 open

        from gnosislab_export.common.snapshot import load_snapshot
        from gnosislab_export.ro_crate.exporter import export_rocrate

        snap = load_snapshot(episteme_db=store.path,
                             anamnesis_db=None, programme_id="prog-ep")
        assert snap.trials[0].evidence_policy is None
        crate = export_rocrate(snap, tmp_path / "crate2")
        graph = json.loads(
            (crate / "ro-crate-metadata.json").read_text())["@graph"]
        trial_ent = next(e for e in graph
                         if e.get("@id") == "#trial/trial-ep")
        prop = next(p for p in trial_ent["additionalProperty"]
                    if p["name"] == "evidence_policy")
        assert json.loads(prop["value"])["regime"] == (
            "repeated_measurement")


# ---------- HTTP lifecycle ----------

pytestmark_http = pytest.mark.asyncio


async def _design_and_capture(server_url, policy=None, seeds=(42,)):
    """programme → hypothesis → design(+policy) → capture; returns ids
    plus the design/capture payloads."""
    pid = await make_programme(server_url)
    hid = await make_hypothesis(server_url, pid)
    args = {}
    if policy is not None:
        args["evidence_policy"] = policy
    tid = await make_trial(server_url, pid, hid, **args)
    cap = await call_tool_http(server_url, "capture_bundle", {
        "trial_id": tid, "code_ref": TRAIN_STUB, "env_ref": "python3.12",
        "seeds": list(seeds), "splits": {"train": 0.8},
    })
    return pid, hid, tid, cap


async def _run_to_completion(server_url, pid, tid):
    r = await call_tool_http(server_url, "run_trial", {
        "programme_id": pid, "trial_id": tid})
    assert "error" not in r, f"run_trial failed: {r}"
    for _ in range(30):
        st = await call_tool_http(server_url, "get_trial_status", {
            "programme_id": pid, "trial_id": tid})
        if st.get("status") in ("completed", "failed"):
            return st
        await asyncio.sleep(1)
    raise AssertionError(f"trial did not reach a terminal state: {st}")


@pytest.mark.asyncio
class TestHTTPLifecycle:
    async def test_default_regime_full_lifecycle(self, server_url):
        pid, hid, tid, cap = await _design_and_capture(server_url)
        assert "error" not in cap
        assert cap["evidence_policy"]["regime"] == "repeated_measurement"
        await _run_to_completion(server_url, pid, tid)
        r = await call_tool_http(server_url, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "variance": {"acc": 0.01}, "spatiotemporal_region": "test"})
        assert "error" not in r
        assert r["evidence_regime"] == "repeated_measurement"

    async def test_default_regime_missing_variance_rejected(
            self, server_url):
        pid, hid, tid, _ = await _design_and_capture(server_url)
        await _run_to_completion(server_url, pid, tid)
        r = await call_tool_http(server_url, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "spatiotemporal_region": "test"})
        assert "error" in r
        # The refusal names the preregistered escape hatch — it cannot
        # be applied retrospectively, only at the next design.
        assert "single_measurement" in r["error"]

    async def test_default_regime_empty_variance_rejected(
            self, server_url):
        pid, hid, tid, _ = await _design_and_capture(server_url)
        await _run_to_completion(server_url, pid, tid)
        r = await call_tool_http(server_url, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "variance": {}, "spatiotemporal_region": "test"})
        assert "error" in r

    async def test_default_regime_measured_zero_accepted(
            self, server_url):
        pid, hid, tid, _ = await _design_and_capture(server_url)
        await _run_to_completion(server_url, pid, tid)
        r = await call_tool_http(server_url, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "variance": {"acc": 0.0}, "spatiotemporal_region": "test"})
        assert "error" not in r
        assert any("zero" in w for w in r.get("warnings", []))

    async def test_single_measurement_no_variance_accepted(
            self, server_url):
        pid, hid, tid, cap = await _design_and_capture(
            server_url, policy=SINGLE_MEASUREMENT)
        assert cap["evidence_policy"]["regime"] == "single_measurement"
        assert cap["evidence_policy"]["rationale"] == (
            SINGLE_MEASUREMENT["rationale"])
        await _run_to_completion(server_url, pid, tid)
        r = await call_tool_http(server_url, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "spatiotemporal_region": "test"})
        assert "error" not in r
        assert r["evidence_regime"] == "single_measurement"

    async def test_single_measurement_supplied_variance_stored(
            self, server_url):
        """Instrument uncertainty under single_measurement is stored
        verbatim — the regime names the evidence basis, not a count."""
        pid, hid, tid, _ = await _design_and_capture(
            server_url, policy=SINGLE_MEASUREMENT)
        await _run_to_completion(server_url, pid, tid)
        r = await call_tool_http(server_url, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "variance": {"acc": 0.02}, "spatiotemporal_region": "test"})
        assert "error" not in r

    @pytest.mark.parametrize("policy", [
        {"regime": "single_measurement"},
        {"regime": "single_measurement", "rationale": ""},
        {"regime": "single_measurement", "rationale": "  "},
        {"regime": "bogus_regime", "rationale": "x"},
        {"version": 2, "regime": "repeated_measurement"},
    ])
    async def test_bad_policy_rejected_at_design(
            self, server_url, policy):
        """Malformed/unearned policies fail at design_experiment —
        declaration time, never at admission."""
        pid = await make_programme(server_url)
        hid = await make_hypothesis(server_url, pid)
        r = await call_tool_http(server_url, "design_experiment", {
            "programme_id": pid, "hypothesis_id": hid,
            "config": {"lr": 0.001}, "evidence_policy": policy})
        assert "error" in r

    async def test_single_seed_default_regime_warns(self, server_url):
        """Advisory, not refusal — code may replicate internally."""
        pid, hid, tid, cap = await _design_and_capture(
            server_url, seeds=(42,))
        assert "error" not in cap
        msgs = [w.get("message", "") for w in cap.get("warnings", [])
                if w.get("kind") == "evidence_policy"]
        assert any("single-seed" in m or "replication" in m
                   for m in msgs), cap["warnings"]

    async def test_multi_seed_default_regime_no_policy_warning(
            self, server_url):
        _, _, _, cap = await _design_and_capture(
            server_url, seeds=(1, 2, 3))
        msgs = [w for w in cap.get("warnings", [])
                if w.get("kind") == "evidence_policy"]
        assert msgs == []

    async def test_capture_rescals_trial_policy(self, server_url):
        """The bundle's sealed policy equals the design-time
        declaration — capture takes no policy parameter."""
        pid, hid, tid, cap = await _design_and_capture(
            server_url, policy=SINGLE_MEASUREMENT)
        assert "error" not in cap
        assert cap["evidence_policy"] == canonical_evidence_policy(
            SINGLE_MEASUREMENT)
        # A second capture is refused — the seal cannot be reopened
        # to change the regime after execution planning.
        r = await call_tool_http(server_url, "capture_bundle", {
            "trial_id": tid, "code_ref": TRAIN_STUB,
            "env_ref": "python3.12", "seeds": [1],
            "splits": {"train": 0.8}})
        assert "error" in r

    async def test_no_observation_level_bypass(self, server_url):
        """record_observation's schema has no policy/override field —
        the caller supplies evidence, never the rule."""
        tools = await list_tools_http(server_url)
        schema = next(t for t in tools
                      if t.name == "record_observation").input_schema
        props = schema.get("properties", {})
        assert "variance" in props
        assert "spatiotemporal_region" in props
        for prop in props:
            assert "policy" not in prop and "override" not in prop \
                and "bypass" not in prop
        # variance is regime-conditional, not schema-required
        assert "variance" not in schema.get("required", [])

    async def test_positional_signature_order(self, server_url):
        """Regression guard for the (trial_id, metrics,
        spatiotemporal_region, variance=None) ordering — a required
        param must not follow a defaulted one."""
        tools = await list_tools_http(server_url)
        schema = next(t for t in tools
                      if t.name == "record_observation").input_schema
        required = set(schema.get("required", []))
        assert {"trial_id", "metrics", "spatiotemporal_region"} <= required
        assert "variance" not in required

    async def test_resource_exposes_declared_policy(self, server_url):
        pid, hid, tid, _ = await _design_and_capture(
            server_url, policy=SINGLE_MEASUREMENT)
        trials = await read_resource_http(
            server_url, f"programme://{pid}/trials")
        entry = next(t for t in trials if t["id"] == tid)
        assert entry["evidence_policy"]["regime"] == (
            "single_measurement")
        assert entry["evidence_policy"]["rationale"] == (
            SINGLE_MEASUREMENT["rationale"])

    async def test_observations_resource_exposes_policy_snapshot(
            self, server_url):
        """trial://{id}/observations answers which regime governed the
        observation — the admission-time snapshot, not a join."""
        pid, hid, tid, _ = await _design_and_capture(
            server_url, policy=SINGLE_MEASUREMENT)
        await _run_to_completion(server_url, pid, tid)
        r = await call_tool_http(server_url, "record_observation", {
            "trial_id": tid, "metrics": {"acc": 0.9},
            "spatiotemporal_region": "test"})
        assert "error" not in r
        obs = await read_resource_http(
            server_url, f"trial://{tid}/observations")
        entry = next(o for o in obs
                     if o["id"] == r["observation_id"])
        assert entry["variance"] is None  # not measured
        assert entry["evidence_policy"]["regime"] == (
            "single_measurement")
        assert entry["evidence_policy"]["rationale"] == (
            SINGLE_MEASUREMENT["rationale"])

    async def test_legacy_caller_default_policy_in_resource(
            self, server_url):
        """Callers that never heard of evidence_policy keep the old
        behaviour: repeated-measurement required."""
        pid, hid, tid, cap = await _design_and_capture(server_url)
        trials = await read_resource_http(
            server_url, f"programme://{pid}/trials")
        entry = next(t for t in trials if t["id"] == tid)
        assert entry["evidence_policy"]["regime"] == (
            "repeated_measurement")
