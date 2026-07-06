"""Tests for the GPU-burst preflight gate + cost recorder (M19 Tier-1)."""
from __future__ import annotations

import base64
import gzip
import json

import pytest

from scripts.ml.gpu_burst import _remote, ingest_bundle, preflight, record_run, runpod_burst
from src.runtime import gpu_spend


def _ledger(tmp_path, runs=None, budget=10.0):
    p = tmp_path / "gpu_spend_ledger.json"
    p.write_text(json.dumps({"budget_usd_per_month": budget, "runs": runs or []}), encoding="utf-8")
    return str(p)


def test_preflight_passes_under_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("GPU_SPEND_LEDGER", _ledger(tmp_path))
    assert preflight.main(["--est-cost", "0.40", "--experiment", "T1.1"]) == 0


def test_preflight_aborts_over_budget(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "GPU_SPEND_LEDGER",
        _ledger(tmp_path, runs=[{"run_id": "big", "ended_at": "2999-01-02T00:00:00Z", "cost_usd": 9.8}]),
    )
    # projected 9.8 + 0.5 = 10.3 > 10 → non-zero (abort). Uses the real current
    # month; the run above is dated far in the future so it lands in no real month —
    # so pin the gate directly too:
    assert gpu_spend.would_exceed_budget(0.5, "2999-01") is True


def test_record_run_appends_and_prices(tmp_path, monkeypatch):
    path = _ledger(tmp_path)
    monkeypatch.setenv("GPU_SPEND_LEDGER", path)
    rc = record_run.main([
        "--run-id", "gpu-test-1", "--experiment", "T1.1 bake-off",
        "--gpu-type", "RTX 4090", "--gpu-hours", "0.9", "--rate", "0.34",
        "--started", "2026-07-02T00:00:00Z", "--ended", "2026-07-02T00:54:00Z",
        "--status", "completed",
    ])
    assert rc == 0
    ledger = json.loads(open(path).read())
    assert len(ledger["runs"]) == 1
    entry = ledger["runs"][0]
    assert entry["run_id"] == "gpu-test-1"
    assert abs(entry["cost_usd"] - 0.306) < 1e-6  # 0.9 * 0.34, filled on append


def test_record_run_authoritative_cost_wins(tmp_path, monkeypatch):
    path = _ledger(tmp_path)
    monkeypatch.setenv("GPU_SPEND_LEDGER", path)
    record_run.main([
        "--run-id", "gpu-test-2", "--experiment", "T1.2",
        "--gpu-hours", "3.0", "--rate", "0.34", "--cost", "0.95",  # billed != hours×rate
        "--ended", "2026-07-05T00:00:00Z",
    ])
    entry = json.loads(open(path).read())["runs"][0]
    assert entry["cost_usd"] == 0.95  # the recorded billed figure, not 1.02


def test_runpod_adapter_fails_safe_without_key(monkeypatch):
    """No RUNPOD_API_KEY → the adapter aborts (rc 3) before touching the API."""
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    assert runpod_burst.main(["--verify", "--experiment", "smoke"]) == 3


def test_runpod_adapter_key_but_no_sdk_fails_safe(monkeypatch):
    """Key set but SDK absent (sandbox) → still a clean abort, never a partial launch."""
    monkeypatch.setenv("RUNPOD_API_KEY", "dummy")
    # _sdk() raises RuntimeError if `runpod` isn't importable; main() maps it to rc 3.
    rc = runpod_burst.main(["--verify", "--experiment", "smoke"])
    assert rc == 3


def test_runpod_ssh_probe_flag_fails_safe_without_sdk(monkeypatch):
    """--ssh-probe is a recognized mode and still fails safe (rc 3) with no SDK/key path."""
    monkeypatch.setenv("RUNPOD_API_KEY", "dummy")
    assert runpod_burst.main(["--ssh-probe", "--experiment", "smoke"]) == 3


def test_public_ssh_endpoint_parses_mapped_22():
    """runtime.ports with a public 22 mapping → (ip, publicPort)."""
    pod = {"runtime": {"ports": [
        {"privatePort": 8888, "isIpPublic": True, "ip": "1.2.3.4", "publicPort": 40000, "type": "http"},
        {"privatePort": 22, "isIpPublic": True, "ip": "1.2.3.4", "publicPort": 41234, "type": "tcp"},
    ]}}
    assert runpod_burst._public_ssh_endpoint(pod) == ("1.2.3.4", 41234)


def test_public_ssh_endpoint_none_until_public_22():
    """No public 22 mapping yet → (None, None) (a private-only or empty ports list)."""
    assert runpod_burst._public_ssh_endpoint({}) == (None, None)
    private = {"runtime": {"ports": [{"privatePort": 22, "isIpPublic": False, "ip": "10.0.0.1", "publicPort": 22}]}}
    assert runpod_burst._public_ssh_endpoint(private) == (None, None)


def test_ssh_argv_direct_uses_ip_port_root():
    argv = _remote.ssh_argv_direct("1.2.3.4", 41234, "/tmp/k", "echo hi")
    assert argv[0] == "ssh"
    assert "root@1.2.3.4" in argv
    assert "-p" in argv and "41234" in argv
    assert "BatchMode=yes" in argv and "StrictHostKeyChecking=no" in argv
    # keepalive so a long, quiet training run isn't idle-dropped (#5455 broken-pipe)
    assert "ServerAliveInterval=30" in argv and "ServerAliveCountMax=20" in argv
    assert argv[-1] == "echo hi"


def test_no_custom_docker_bootstrap_sent_to_runpod():
    """We rely on the official image's own SSH start-script — NOT a custom
    docker_args bootstrap. RunPod interpolates docker_args raw into its GraphQL
    mutation, so any '%' (#5447) or '$' (#5449) in a shell snippet aborts the
    launch before a pod is created. Guard the whole regression class: the module
    must not carry a shell bootstrap constant, and run() must never pass a
    non-empty docker_args to create_pod."""
    import inspect

    assert not hasattr(runpod_burst, "_DOCKER_SSH_BOOTSTRAP")
    src = inspect.getsource(runpod_burst.run)
    assert "docker_args=" not in src  # no docker_args kwarg passed to create_pod


class _FakeRunpod:
    """Minimal stand-in for the runpod SDK for the capacity-fallback tests."""

    class _CapacityError(Exception):
        pass

    def __init__(self, launch_on=None):
        # launch_on: the gpu_type_id that "has capacity"; None → every card is out.
        self.launch_on = launch_on
        self.created = []
        self.terminated = []

    def create_pod(self, *, gpu_type_id, **_):
        self.created.append(gpu_type_id)
        if self.launch_on is not None and gpu_type_id == self.launch_on:
            return {"id": "pod-xyz", "costPerHr": 0.34}
        raise self._CapacityError("This machine does not have the resources to deploy your pod.")

    def get_pod(self, _pod_id):
        return {"desiredStatus": "RUNNING", "runtime": {"uptimeInSeconds": 5}}

    def get_gpu(self, _gpu):
        return {"lowestPrice": {"minimumBidPrice": 0.34}}

    def terminate_pod(self, pod_id):
        self.terminated.append(pod_id)


def test_runpod_capacity_all_exhausted_no_spend(monkeypatch):
    """Every card out of stock → clean rc 4, and terminate is never called (no pod)."""
    fake = _FakeRunpod(launch_on=None)
    monkeypatch.setattr(runpod_burst, "_sdk", lambda: fake)
    rc = runpod_burst.run(experiment="smoke", gpu_type="NVIDIA GeForce RTX 4090",
                          image="img", verify=True, emit_path=None)
    assert rc == 4
    assert len(fake.created) == len(runpod_burst._GPU_FALLBACKS)  # walked the whole list
    assert fake.terminated == []  # nothing launched → nothing to tear down


def test_runpod_capacity_fallback_then_launch(monkeypatch, tmp_path):
    """First card out of stock, a later card has capacity → verify OK (rc 0), pod torn down."""
    second = runpod_burst._GPU_FALLBACKS[1]
    fake = _FakeRunpod(launch_on=second)
    monkeypatch.setattr(runpod_burst, "_sdk", lambda: fake)
    emit = tmp_path / "gh_output"
    rc = runpod_burst.run(experiment="smoke", gpu_type=runpod_burst._GPU_FALLBACKS[0],
                          image="img", verify=True, emit_path=str(emit))
    assert rc == 0
    assert fake.terminated == ["pod-xyz"]  # teardown guarantee held
    out = emit.read_text()
    assert f"gpu_type={second}" in out  # emits the card actually launched, not the requested one


# ---- on-pod exec building blocks (_remote.py, no live pod) ----

def test_ssh_argv_is_noninteractive_proxy():
    argv = _remote.ssh_argv("pod-123", "/tmp/k", "echo hi")
    assert argv[0] == "ssh"
    assert "pod-123@ssh.runpod.io" in argv
    assert "echo hi" == argv[-1]
    # ephemeral-key hygiene: no host-key prompt, batch mode, key passed via -i
    assert "-i" in argv and "/tmp/k" in argv
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=no" in argv


def test_ssh_argv_requires_pod_id():
    with pytest.raises(ValueError):
        _remote.ssh_argv("", "/tmp/k", "echo hi")


def test_remote_script_is_safe_and_pinned():
    script = _remote.build_remote_train_script(
        repo_sha="abc123", manifest_path="ml/configs/btc-regime-15m-lgbm-v2.yaml",
        symbol="BTCUSDT", timeframe="15m",
    )
    assert "set -euo pipefail" in script            # abort-on-error
    assert "git checkout --quiet abc123" in script  # pinned SHA, not a floating branch
    # Real ml CLI: build the PUBLIC market dataset, then train the manifest.
    assert "build-dataset market_raw" in script
    assert "build-dataset market_features" in script
    assert "python -m ml train ml/configs/btc-regime-15m-lgbm-v2.yaml" in script
    assert "ICT_OFFVM_BUILD_HOST=1" in script       # off-VM adapter guard
    assert _remote._ARTIFACT_BEGIN in script and _remote._ARTIFACT_END in script
    # ONNX export + parity gate are FICTIONAL — they don't exist in the codebase,
    # so the script must not reference them (the v1-DRAFT regression).
    assert "--parity-gate" not in script
    assert "--export-onnx" not in script
    # Safety: no secret / money-DB / cred reference reaches the pod.
    for forbidden in ("RUNPOD_API_KEY", "trade_journal", "DASHBOARD_API_TOKEN",
                      "SECRET", "TELEGRAM", ".env"):
        assert forbidden not in script


def test_remote_script_rejects_floating_sha():
    with pytest.raises(ValueError):
        _remote.build_remote_train_script(
            repo_sha="", manifest_path="m", symbol="BTCUSDT", timeframe="15m",
        )


def test_remote_script_requires_symbol_and_timeframe():
    with pytest.raises(ValueError):
        _remote.build_remote_train_script(
            repo_sha="abc123", manifest_path="m", symbol="", timeframe="15m",
        )


def test_artifact_roundtrip_through_stream():
    payload = b"onnx-model-bytes-\x00\x01\x02" * 100
    b64 = base64.b64encode(gzip.compress(payload)).decode()
    # the pod frames the base64 between markers amid ordinary build chatter
    stdout = f"cloning...\ntraining...\n{_remote._ARTIFACT_BEGIN}\n{b64}\n{_remote._ARTIFACT_END}\ndone\n"
    extracted = _remote.extract_artifact_b64(stdout)
    assert _remote.decode_artifact_stream(extracted) == payload


def test_artifact_extract_missing_markers_raises():
    with pytest.raises(ValueError):
        _remote.extract_artifact_b64("no markers here")


def test_remote_script_embeds_symbol_timeframe_version():
    script = _remote.build_remote_train_script(
        repo_sha="deadbeef", manifest_path="ml/configs/eth-regime-15m-lgbm-v1.yaml",
        symbol="ETHUSDT", timeframe="15m", version="v002",
    )
    # the built dataset path is symbol/timeframe/version-scoped
    assert "datasets-out/market_raw/ETHUSDT/15m/v002" in script
    assert "--symbol-scope ETHUSDT --timeframe 15m" in script
    # the fixed crypto market_features params ride along (byte-identical to the cycle)
    assert "vol_threshold=0.005 trend_threshold=0.005 n_vol_buckets=3" in script


# ---- runpod_burst train-path helpers (no live pod) ----

def test_manifest_dataset_scope_reads_dataset_block(tmp_path):
    m = tmp_path / "head.yaml"
    m.write_text(
        "model_id: x\n"
        "dataset:\n"
        "  family: market_features\n"
        "  symbol_scope: BTCUSDT\n"
        "  timeframe: 15m\n"
        "  version: v002\n",
        encoding="utf-8",
    )
    scope = runpod_burst._manifest_dataset_scope(str(m))
    assert scope == {"family": "market_features", "symbol": "BTCUSDT",
                     "timeframe": "15m", "version": "v002"}


def test_manifest_dataset_scope_defaults_version(tmp_path):
    m = tmp_path / "head.yaml"
    m.write_text("dataset:\n  family: market_features\n  symbol_scope: BTCUSDT\n  timeframe: 1h\n",
                 encoding="utf-8")
    assert runpod_burst._manifest_dataset_scope(str(m))["version"] == "v002"


def test_as_text_coerces_bytes_and_none():
    # subprocess.TimeoutExpired carries partial capture as BYTES even under
    # text=True — coerce so the timeout handler doesn't `bytes + str` TypeError (#5457).
    assert runpod_burst._as_text(b"partial train log") == "partial train log"
    assert runpod_burst._as_text("already str") == "already str"
    assert runpod_burst._as_text(None) == ""


def test_resolve_repo_sha_prefers_github_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "cafebabe1234")
    assert runpod_burst._resolve_repo_sha() == "cafebabe1234"


def test_write_bundle_lands_under_mirror(tmp_path, monkeypatch):
    monkeypatch.setattr(_remote, "MIRROR_SUBDIR", str(tmp_path / "gpu_burst"))
    dest = runpod_burst._write_bundle("ml/configs/btc-regime-15m-lgbm-v2.yaml", b"{\"ok\":1}")
    assert dest.endswith("btc-regime-15m-lgbm-v2.bundle.json")
    assert open(dest, "rb").read() == b"{\"ok\":1}"


# ---- deep-sequence (market_sequences) burst wiring (M19 T1.1) ----

def _write_seq_manifest(tmp_path):
    m = tmp_path / "tcn.yaml"
    m.write_text(
        "model_id: btc-regime-15m-tcn-v1\n"
        "trainer_config:\n"
        "  seq_len: 64\n"
        "  feature_columns: [log_return, rolling_log_return_vol, hour_of_day, dayofweek]\n"
        "dataset:\n"
        "  family: market_sequences\n"
        "  symbol_scope: BTCUSDT\n"
        "  timeframe: 15m\n"
        "  version: v001\n",
        encoding="utf-8",
    )
    return m


def test_manifest_scope_surfaces_sequence_params(tmp_path):
    scope = runpod_burst._manifest_dataset_scope(str(_write_seq_manifest(tmp_path)))
    assert scope["family"] == "market_sequences"
    assert scope["sequence"] == {
        "version": "v001", "seq_len": 64,
        "feature_columns": ["log_return", "rolling_log_return_vol", "hour_of_day", "dayofweek"],
    }


def test_pod_scope_accepts_both_public_families_only():
    assert runpod_burst._in_pod_scope("market_features", "BTCUSDT")
    assert runpod_burst._in_pod_scope("market_sequences", "ETHUSDT")
    # Non-public families / non-crypto symbols are refused (data contract).
    assert not runpod_burst._in_pod_scope("trade_outcomes", "BTCUSDT")
    assert not runpod_burst._in_pod_scope("market_features", "MES")
    assert not runpod_burst._in_pod_scope(None, "BTCUSDT")


def test_remote_script_sequence_path_builds_window_and_installs_onnx():
    seq = {"version": "v001", "seq_len": 64,
           "feature_columns": ["log_return", "rolling_log_return_vol", "hour_of_day", "dayofweek"]}
    s = _remote.build_remote_train_script(
        repo_sha="abc123def456", manifest_path="ml/configs/btc-regime-15m-tcn-v1.yaml",
        symbol="BTCUSDT", timeframe="15m", version="v001", sequence=seq,
    )
    # torch from the image (fail-fast, not a 10-min install) + onnx/onnxruntime for parity.
    assert 'import torch' in s and "pip install --quiet onnx onnxruntime" in s
    # windows the SAME public market_features → market_sequences, in order, before train.
    assert "build-dataset market_sequences --output-dir datasets-out --version v001" in s
    assert "seq_len=64" in s
    assert "feature_columns=log_return,rolling_log_return_vol,hour_of_day,dayofweek" in s
    assert "market_features_path=datasets-out/market_features/BTCUSDT/15m/v001" in s
    assert s.index("build-dataset market_features") < s.index("build-dataset market_sequences") < s.index("ml train")
    # data contract: still public-only — no money DB / secret / cred references.
    assert "trade_journal" not in s and "DASHBOARD_API_TOKEN" not in s and "VM_SSH_KEY" not in s


def test_remote_script_non_sequence_path_unchanged():
    s = _remote.build_remote_train_script(
        repo_sha="abc123def456", manifest_path="ml/configs/btc-regime-15m-lgbm-v2.yaml",
        symbol="BTCUSDT", timeframe="15m", version="v002",
    )
    assert "market_sequences" not in s
    assert "onnxruntime" not in s
    assert "import torch" not in s


# ---- bundle → registry ingest (M19 T1) ----

def _write_bundle_json(tmp_path, *, model_id="btc-regime-15m-lgbm-v2", stage="shadow", run_id="20260702T183000Z"):
    bundle = {
        "run_dir": f"ml/experiments-runs/{model_id}/{run_id}/",
        "model_state": {"booster_str": "tree...", "feature_names": ["a", "b"]},
        "metrics": {"macro_f1": 0.61, "f1_volatile": 0.40, "nested": {"x": 1}},
        "manifest": {"model_id": model_id, "target_deployment_stage": stage,
                     "dataset": {"family": "market_features"}},
    }
    p = tmp_path / f"{model_id}.bundle.json"
    p.write_text(json.dumps(bundle), encoding="utf-8")
    return p


def test_ingest_forces_candidate_and_namespaces(tmp_path):
    bundle = _write_bundle_json(tmp_path, stage="shadow")  # even if manifest says shadow
    reg_root = tmp_path / "registry-store"
    exp_root = tmp_path / "experiments-runs"
    dest = ingest_bundle.ingest(
        bundle_path=str(bundle), registry_root=str(reg_root),
        experiments_root=str(exp_root), code_revision="deadbeef",
    )
    entry = json.loads(open(dest).read())
    # forced candidate — never auto-land at shadow (which would auto-wire onto strategies)
    assert entry["target_deployment_stage"] == "candidate"
    assert entry["status"] == "candidate"
    # namespaced to a burst-only id — never the bare production id
    assert entry["model_id"] == "btc-regime-15m-lgbm-v2-gpuburst"
    assert not (reg_root / "btc-regime-15m-lgbm-v2.json").exists()  # production id untouched
    assert entry["code_revision"] == "deadbeef"
    # registry metrics keep only scalar numerics (nested dropped); full metrics.json materialized
    assert entry["metrics"] == {"macro_f1": 0.61, "f1_volatile": 0.40}
    run = exp_root / "btc-regime-15m-lgbm-v2-gpuburst" / "20260702T183000Z"
    assert (run / "model_state.json").exists()
    assert json.loads((run / "metrics.json").read_text())["nested"] == {"x": 1}
    assert entry["model_state_path"].endswith("model_state.json")


def test_ingest_refuses_to_overwrite_promoted_burst_id(tmp_path):
    # Pre-seed a burst id an operator has promoted past candidate → re-burst must abort
    # rather than refresh its served weights.
    from ml.registry.model_registry import ModelRegistry
    reg_root = tmp_path / "registry-store"
    exp_root = tmp_path / "experiments-runs"
    ms = tmp_path / "ms.json"
    ms.write_text("{}", encoding="utf-8")
    reg = ModelRegistry(reg_root)
    # register() honors the manifest stage for a new id → lands directly at advisory
    reg.register(model_id="btc-regime-15m-lgbm-v2-gpuburst",
                 manifest={"model_id": "btc-regime-15m-lgbm-v2-gpuburst",
                           "target_deployment_stage": "advisory"},
                 model_state_path=str(ms), metrics={}, code_revision="x", run_id="r0", by="operator")
    bundle = _write_bundle_json(tmp_path)
    with pytest.raises(ValueError):
        ingest_bundle.ingest(bundle_path=str(bundle), registry_root=str(reg_root),
                             experiments_root=str(exp_root), code_revision="y")


def test_ingest_run_id_from_bundle_dir():
    assert ingest_bundle._run_id_from_bundle({"run_dir": "a/b/RUNID/"}, "fb") == "RUNID"
    assert ingest_bundle._run_id_from_bundle({}, "fb") == "fb"


def test_ingest_rejects_bundle_without_model_id(tmp_path):
    p = tmp_path / "bad.bundle.json"
    p.write_text(json.dumps({"model_state": {}, "manifest": {"dataset": {}}}), encoding="utf-8")
    assert ingest_bundle.main(["--bundle", str(p), "--registry-root", str(tmp_path / "r"),
                               "--experiments-root", str(tmp_path / "e")]) == 1


def test_remote_script_build_params_override_vol_threshold():
    script = _remote.build_remote_train_script(
        repo_sha="deadbeef", manifest_path="ml/configs/btc-regime-15m-tcn-vt003-v1.yaml",
        symbol="BTCUSDT", timeframe="15m", version="v001",
        build_params={"vol_threshold": 0.003},
    )
    # override lands; the other cycle params stay at their defaults
    assert "vol_threshold=0.003" in script
    assert "vol_threshold=0.005" not in script
    assert "vol_window_n=20 forward_window_m=5" in script
    assert "trend_threshold=0.005 n_vol_buckets=3" in script


def test_remote_script_build_params_unknown_key_refused():
    with pytest.raises(ValueError, match="unknown market_features build_params"):
        _remote.build_remote_train_script(
            repo_sha="deadbeef", manifest_path="m.yaml",
            symbol="BTCUSDT", timeframe="15m",
            build_params={"db_path": "/data/trade_journal.db"},
        )


def test_manifest_dataset_scope_surfaces_build_params(tmp_path):
    m = tmp_path / "head.yaml"
    m.write_text(
        "model_id: x\n"
        "dataset:\n"
        "  family: market_sequences\n"
        "  symbol_scope: BTCUSDT\n"
        "  timeframe: 15m\n"
        "  version: v001\n"
        "  build_params:\n"
        "    vol_threshold: 0.003\n"
        "trainer_config:\n"
        "  seq_len: 64\n"
        "  feature_columns: [log_return]\n",
        encoding="utf-8",
    )
    scope = runpod_burst._manifest_dataset_scope(str(m))
    assert scope["build_params"] == {"vol_threshold": 0.003}
    assert scope["sequence"]["seq_len"] == 64
