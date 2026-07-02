#!/usr/bin/env python3
"""RunPod provider adapter for the M19 GPU-burst tier — launch / verify / teardown.

The RunPod-specific half of `run_burst.sh`. Uses the official `runpod` Python SDK
(`pip install runpod`). Its ONE job is to launch exactly one **community-cloud
(spot)** pod, hand back its id + billed rate, and **guarantee teardown** — pod
termination is in a `finally`, so a crash/timeout can never leak a billing pod.

Three entry points:

- ``--verify`` — the money-safety smoke test: launch the cheapest pod, confirm it
  reaches RUNNING, then terminate it immediately. A few cents; proves
  launch→bill→teardown end-to-end BEFORE any real (armed) training run. Run this
  once after the key lands; only then set ``GPU_BURST_ARMED=1``.
- ``--ssh-probe`` — launch, connect over direct public-IP SSH with the ephemeral
  key, run an env check (python/nvidia-smi/git), terminate. Proves the SSH exec
  path the training run rides on.
- default — launch a pod, clone the pinned SHA, build the PUBLIC market dataset,
  ``python -m ml train <manifest>``, and stream the trained model bundle
  (model_state + metrics + manifest, gzip|base64) back over SSH; the bundle lands
  under ``runtime_logs/trainer_mirror/gpu_burst/``, then the pod is terminated.
  Scoped to ``market_features`` crypto heads (public data only — no money DB).

Cost facts (gpu_type, rate, gpu_hours, cost) are written to ``--emit-github-output``
(``$GITHUB_OUTPUT``) for the workflow's ledger record-run step.

Safety: reads ``RUNPOD_API_KEY`` from the env (never logged). No secrets, money-DB,
or live creds ever go to the pod. Only public code + public market data go up; only
the trained model bundle comes back.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from typing import Any

from scripts.ml.gpu_burst import _remote

# First on-pod training target: a cheap, robust BTC regime head. Its `dataset:`
# block (market_features / BTCUSDT / 15m / v002) needs only PUBLIC Bybit klines —
# no money DB — so it satisfies the pod data contract. Overridable via --manifest.
_DEFAULT_MANIFEST = "ml/configs/btc-regime-15m-lgbm-v2.yaml"

# SSH readiness + probe timings. A freshly-RUNNING pod takes a few seconds more
# to accept the injected key on its sshd, so we retry the readiness echo.
_SSH_READY_TIMEOUT_S = 180
_SSH_READY_INTERVAL_S = 10
_SSH_CMD_TIMEOUT_S = 60

# The connectivity/environment probe: prove proxy SSH + the account key work AND
# the pod is a usable train box (GPU present, python, git) — before we ever build
# the full train path against it.
_PROBE_CMD = (
    "echo '=== ict burst ssh probe ==='; "
    "python --version 2>&1 || python3 --version 2>&1; "
    "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1 | head -2 "
    "|| echo 'nvidia-smi unavailable'; "
    "git --version 2>&1; "
    "echo '=== probe end ==='"
)

# Default to a cheap 24GB-class community card; overridable.
_DEFAULT_GPU = "NVIDIA GeForce RTX 4090"
_DEFAULT_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
_POLL_TIMEOUT_S = 420   # 7 min to reach RUNNING before we give up + tear down
_POLL_INTERVAL_S = 10

# Community-spot capacity is a lottery: the requested card is often momentarily
# out of stock ("This machine does not have the resources to deploy your pod").
# Rather than crash, walk this fallback list of cheap, common community cards
# until one has capacity. The requested --gpu-type is always tried FIRST.
_GPU_FALLBACKS = [
    "NVIDIA GeForce RTX 4090",
    "NVIDIA GeForce RTX 3090",
    "NVIDIA RTX A5000",
    "NVIDIA RTX A4000",
    "NVIDIA GeForce RTX 4080",
]


def _is_capacity_error(exc: Exception) -> bool:
    """True when create_pod failed because the pool is momentarily out of stock
    (a transient availability miss, not an auth/quota/code fault)."""
    msg = str(exc).lower()
    return any(
        s in msg
        for s in ("resource", "not have", "capacity", "no longer any instances", "out of stock")
    )


# Zero-config pod SSH — NO custom docker_args.
#
# The RunPod OFFICIAL images (we use `runpod/pytorch:*`) ship full SSH already
# configured: on start they inject the `PUBLIC_KEY` env var into
# ~/.ssh/authorized_keys and run sshd on port 22 (per RunPod's "Connect via SSH"
# docs — official templates need NO setup). So for direct public-IP SSH we only
# hand the pod our EPHEMERAL public key via `env={"PUBLIC_KEY": pub}`, a public IP,
# and an exposed port 22 — the image does the rest. An ephemeral per-run key means
# zero operator config (no account key, no GitHub secret).
#
# We deliberately DON'T pass a custom `docker_args` bootstrap: (1) it's redundant
# with the image's own SSH start-script, and (2) RunPod interpolates docker_args
# RAW into its GraphQL mutation, so any '%' (Unexpected character) or '$' (Expected
# Name, found "$") in a shell snippet aborts the launch before a pod is even
# created — a fragility class we sidestep entirely by not sending shell there.
# `PUBLIC_KEY` is expanded by the IMAGE on the pod; our value carries only
# ssh-ed25519 base64 (no '$'/'%'), so the structured env input is GraphQL-safe.


def _public_ssh_endpoint(pod: dict) -> tuple[str | None, int | None]:
    """Extract the public (ip, host-port) mapped to the pod's private port 22.

    RunPod get_pod returns runtime.ports = [{ip, isIpPublic, privatePort,
    publicPort, type}]. Returns (None, None) until a public 22 mapping appears.
    """
    ports = ((pod or {}).get("runtime") or {}).get("ports") or []
    for p in ports:
        try:
            if int(p.get("privatePort")) == 22 and p.get("isIpPublic") and p.get("ip") and p.get("publicPort"):
                return str(p["ip"]), int(p["publicPort"])
        except (TypeError, ValueError):
            continue
    return None, None


def _wait_public_ssh_endpoint(
    runpod: Any, pod_id: str, timeout_s: int = _SSH_READY_TIMEOUT_S,
) -> tuple[str | None, int | None]:
    """Poll get_pod until RunPod publishes the public IP + mapped SSH port."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ip, port = _public_ssh_endpoint(runpod.get_pod(pod_id) or {})
        if ip and port:
            return ip, port
        time.sleep(_SSH_READY_INTERVAL_S)
    return None, None


def _sdk() -> Any:
    """Import + auth the runpod SDK lazily (so lint/tests don't need it installed)."""
    try:
        import runpod  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover - exercised only on a host w/o the SDK
        raise RuntimeError("runpod SDK not installed — `pip install runpod` on the runner.") from e
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not key:
        raise RuntimeError("RUNPOD_API_KEY unset — cannot reach RunPod (no pod, no spend).")
    runpod.api_key = key
    return runpod


def _pod_rate(runpod: Any, pod: dict, gpu_type: str) -> float:
    """Best-effort $/hr for the launched pod (pod field, else the GPU's spot price)."""
    for k in ("costPerHr", "lowestBidPrice"):
        v = pod.get(k)
        try:
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            pass
    try:
        info = runpod.get_gpu(gpu_type)  # {'lowestPrice': {'minimumBidPrice': ...}}
        lp = (info or {}).get("lowestPrice") or {}
        for k in ("minimumBidPrice", "uninterruptablePrice"):
            if lp.get(k) is not None:
                return float(lp[k])
    except Exception:  # noqa: BLE001 - pricing is advisory; never block on it
        pass
    return 0.0


def _ssh_capture(
    endpoint: tuple[str, int], key_path: str, command: str, timeout_s: int,
) -> tuple[int, str, str]:
    """Run one direct-SSH command against (ip, port); return (rc, stdout, stderr).
    A timeout → rc 124."""
    host, port = endpoint
    argv = _remote.ssh_argv_direct(host, port, key_path, command)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        # On timeout, TimeoutExpired carries the partial capture as BYTES even
        # under text=True (the decode step is bypassed) — decode before use, or
        # `bytes + str` raises TypeError and we lose the partial-output diagnostic.
        return 124, _as_text(e.stdout), _as_text(e.stderr) + "\n(timeout)"
    return proc.returncode, proc.stdout, proc.stderr


def _as_text(v: Any) -> str:
    """Coerce a subprocess capture (str | bytes | None) to str."""
    if v is None:
        return ""
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else v


def _wait_ssh_ready(
    endpoint: tuple[str, int], key_path: str, timeout_s: int = _SSH_READY_TIMEOUT_S,
) -> bool:
    """Poll until the pod's sshd accepts our ephemeral key (or give up)."""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        rc, out, err = _ssh_capture(endpoint, key_path, "echo __ssh_ok__", timeout_s=30)
        if rc == 0 and "__ssh_ok__" in out:
            return True
        last = (err or out).strip()
        time.sleep(_SSH_READY_INTERVAL_S)
    print(f"::warning::ssh not ready after {timeout_s}s — last: {last[-200:]}")
    return False


def _wait_running(runpod: Any, pod_id: str, timeout_s: int = _POLL_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pod = runpod.get_pod(pod_id) or {}
        status = pod.get("desiredStatus") or (pod.get("runtime") or {}).get("uptimeInSeconds")
        if pod.get("runtime") and (pod.get("runtime") or {}).get("uptimeInSeconds", 0) > 0:
            return True
        if status == "RUNNING":
            return True
        time.sleep(_POLL_INTERVAL_S)
    return False


def _resolve_repo_sha() -> str:
    """The pinned SHA the pod clones — GITHUB_SHA on the runner, else local HEAD."""
    sha = os.environ.get("GITHUB_SHA", "").strip()
    if sha:
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 - a resolve failure is reported by the caller
        pass
    return ""


def _manifest_dataset_scope(manifest_path: str) -> dict:
    """Read a manifest's `dataset:` block → {family, symbol, timeframe, version}.

    The manifest is the single source of truth for what the pod builds. pyyaml is
    lazy-imported so the module stays importable (and unit-testable) without it.
    """
    import yaml  # lazy: pyyaml need not be present just to import this module

    with open(manifest_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    ds = doc.get("dataset") or {}
    return {
        "family": ds.get("family"),
        "symbol": ds.get("symbol_scope"),
        "timeframe": ds.get("timeframe"),
        "version": ds.get("version") or "v002",
    }


def _write_bundle(manifest: str, data: bytes) -> str:
    """Write the returned model bundle under the trainer mirror, keyed by manifest."""
    stem = os.path.splitext(os.path.basename(manifest))[0]
    os.makedirs(_remote.MIRROR_SUBDIR, exist_ok=True)
    dest = os.path.join(_remote.MIRROR_SUBDIR, f"{stem}.bundle.json")
    with open(dest, "wb") as fh:
        fh.write(data)
    return dest


def run(
    *,
    experiment: str,
    gpu_type: str,
    image: str,
    verify: bool,
    emit_path: str | None,
    ssh_probe: bool = False,
    manifest: str = _DEFAULT_MANIFEST,
    max_minutes: int = 60,
) -> int:
    runpod = _sdk()
    pod_id = ""
    gpu_used = gpu_type
    started = time.monotonic()
    rate = 0.0
    # Any SSH path (probe today, full train next) uses a THROWAWAY per-run key: the
    # pod is launched with a public IP + our ephemeral key handed to the image via
    # PUBLIC_KEY (the official image installs it + runs sshd) — so it works with zero
    # operator config (no account key).
    # Pure --verify stays key-free + never opens a public port.
    key_path = pub = None
    need_ssh = ssh_probe or not verify
    if need_ssh:
        key_path, pub = _remote.gen_ephemeral_keypair()
    try:
        # Try the requested card first, then walk the fallback list — but only
        # skip forward on a genuine capacity miss; any other error re-raises.
        candidates = [gpu_type] + [g for g in _GPU_FALLBACKS if g != gpu_type]
        pod: dict = {}
        last_capacity_err: Exception | None = None
        for cand in candidates:
            try:
                pod = runpod.create_pod(
                    name=f"ict-burst-{os.environ.get('GITHUB_RUN_ID', 'local')}",
                    image_name=image,
                    gpu_type_id=cand,
                    cloud_type="COMMUNITY",           # the cheap spot pool
                    gpu_count=1,
                    container_disk_in_gb=20,
                    volume_in_gb=0,
                    # SSH paths need a public IP + exposed port 22; the official
                    # image's own start-script installs PUBLIC_KEY + runs sshd, so
                    # NO docker_args (see _public_ssh_endpoint note). Verify skips it.
                    support_public_ip=need_ssh,
                    ports="22/tcp" if need_ssh else None,
                    env={"PUBLIC_KEY": pub} if pub else None,
                ) or {}
            except Exception as e:  # noqa: BLE001 - re-raised below unless it's a capacity miss
                if _is_capacity_error(e):
                    print(f"::notice::no community capacity for '{cand}' — trying next card.")
                    last_capacity_err = e
                    pod = {}
                    continue
                raise
            pid = str(pod.get("id") or "")
            if pid:
                pod_id, gpu_used = pid, cand
                break
            print(f"::notice::'{cand}': create_pod returned no id — trying next card.")
            pod = {}
        if not pod_id:
            print(f"::error::no community capacity across {candidates} — nothing launched, no spend. "
                  f"last: {last_capacity_err}")
            return 4
        rate = _pod_rate(runpod, pod, gpu_used)
        print(f"launched pod {pod_id} ({gpu_used}) @ ${rate:.4f}/hr")

        if not _wait_running(runpod, pod_id):
            print(f"::error::pod {pod_id} did not reach RUNNING in {_POLL_TIMEOUT_S}s — tearing down.")
            return 6

        if verify:
            print(f"VERIFY OK — pod {pod_id} reached RUNNING; tearing down (smoke test, no training).")
            return 0

        # SSH paths: wait for RunPod to publish the public IP + mapped SSH port,
        # then wait for our own sshd to accept the ephemeral key.
        ip, port = _wait_public_ssh_endpoint(runpod, pod_id)
        if not (ip and port):
            print(f"::error::pod {pod_id} never exposed a public SSH port in {_SSH_READY_TIMEOUT_S}s "
                  "(community machine may not offer a public IP) — tearing down.")
            return 7
        endpoint = (ip, port)
        print(f"pod {pod_id} public SSH endpoint: {ip}:{port}")
        if not _wait_ssh_ready(endpoint, key_path):
            print(f"::error::pod {pod_id} sshd never accepted the ephemeral key within "
                  f"{_SSH_READY_TIMEOUT_S}s — tearing down.")
            return 7

        if ssh_probe:
            rc, out, err = _ssh_capture(endpoint, key_path, _PROBE_CMD, timeout_s=_SSH_CMD_TIMEOUT_S)
            print("---- pod probe stdout ----")
            print(out)
            if err.strip():
                print("---- pod probe stderr ----")
                print(err)
            if rc != 0 or "=== probe end ===" not in out:
                print(f"::error::probe command failed (rc={rc}) — SSH reached but pod env check incomplete.")
                return 8
            print(f"SSH PROBE OK — public-IP SSH + ephemeral key work on pod {pod_id}; tearing down.")
            return 0

        # --- Full on-pod training run -------------------------------------
        # Scope guard: ONLY a market_features (crypto regime/direction) manifest
        # may train on a pod — it needs public market data only, never the money
        # DB. Anything else is refused before a byte leaves the VM (data contract).
        try:
            scope = _manifest_dataset_scope(manifest)
        except Exception as e:  # noqa: BLE001 - a bad manifest path/parse is a clean abort
            print(f"::error::could not read manifest '{manifest}': {e} — tearing down.")
            return 9
        family, symbol, timeframe = scope["family"], scope["symbol"], scope["timeframe"]
        if family != "market_features" or not str(symbol or "").upper().endswith("USDT"):
            print(f"::error::manifest '{manifest}' is out of pod scope "
                  f"(family={family}, symbol={symbol}). Only market_features crypto heads "
                  "train on a pod (public data only, no money DB). Tearing down.")
            return 9
        repo_sha = _resolve_repo_sha()
        if not repo_sha:
            print("::error::could not resolve a pinned repo SHA (GITHUB_SHA / git rev-parse) — tearing down.")
            return 9

        script = _remote.build_remote_train_script(
            repo_sha=repo_sha, manifest_path=manifest,
            symbol=symbol, timeframe=timeframe, version=scope["version"],
        )
        train_timeout = max(60, int(max_minutes) * 60)
        print(f"training {manifest} on pod {pod_id} @ {repo_sha[:12]} "
              f"(symbol={symbol} tf={timeframe}); ssh timeout {train_timeout}s")
        rc, out, err = _ssh_capture(endpoint, key_path, script, timeout_s=train_timeout)
        # stdout carries the artifact between markers — print only the pre-marker
        # build/train chatter (bounded) so the log stays readable.
        head = out.split(_remote._ARTIFACT_BEGIN, 1)[0]
        print("---- pod train stdout (head) ----")
        print(head[-4000:])
        if err.strip():
            print("---- pod train stderr (tail) ----")
            print(err[-2000:])
        if rc != 0:
            print(f"::error::on-pod training failed (rc={rc}) — tearing down.")
            return 5
        try:
            data = _remote.decode_artifact_stream(_remote.extract_artifact_b64(out))
        except Exception as e:  # noqa: BLE001 - train ran but the return framing broke
            print(f"::error::training finished but the artifact wasn't returned cleanly: {e} — tearing down.")
            return 5
        dest = _write_bundle(manifest, data)
        print(f"TRAIN OK — {len(data)} bytes returned from pod {pod_id}; wrote {dest}. Tearing down.")
        return 0
    finally:
        # ALWAYS runs — the money-safety guarantee.
        elapsed_hr = (time.monotonic() - started) / 3600.0
        if pod_id:
            try:
                runpod.terminate_pod(pod_id)
                print(f"terminated pod {pod_id}")
            except Exception as e:  # noqa: BLE001 - the scheduled reaper is the backstop
                print(f"::warning::terminate_pod({pod_id}) failed: {e} — the reaper will catch it")
        cost = round(elapsed_hr * rate, 4)
        if emit_path:
            with open(emit_path, "a", encoding="utf-8") as fh:
                fh.write(f"gpu_type={gpu_used}\n")
                fh.write(f"rate={rate}\n")
                fh.write(f"gpu_hours={round(elapsed_hr, 4)}\n")
                fh.write(f"cost={cost}\n")
        print(f"cost facts — gpu_hours={elapsed_hr:.4f} rate=${rate:.4f} cost=${cost:.4f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="Smoke test: launch cheapest pod, confirm RUNNING, terminate.")
    ap.add_argument("--ssh-probe", action="store_true",
                    help="Launch, prove public-IP SSH + the ephemeral key work + the pod env (GPU/python/git), terminate.")
    ap.add_argument("--experiment", default="(unnamed)")
    ap.add_argument("--manifest", default=os.environ.get("MANIFEST") or _DEFAULT_MANIFEST,
                    help="Manifest to train on the pod (market_features crypto head only). Default: a BTC regime head.")
    ap.add_argument("--max-minutes", type=int, default=int(os.environ.get("MAX_MINUTES") or 60),
                    help="Wall-clock cap for the on-pod train SSH command (minutes).")
    ap.add_argument("--gpu-type", default=os.environ.get("RUNPOD_GPU_TYPE", _DEFAULT_GPU))
    ap.add_argument("--image", default=os.environ.get("RUNPOD_IMAGE", _DEFAULT_IMAGE))
    ap.add_argument("--emit-github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = ap.parse_args(argv)
    try:
        return run(
            experiment=args.experiment,
            gpu_type=args.gpu_type,
            image=args.image,
            verify=args.verify,
            ssh_probe=args.ssh_probe,
            emit_path=args.emit_github_output,
            manifest=args.manifest,
            max_minutes=args.max_minutes,
        )
    except RuntimeError as e:
        print(f"::error::{e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
