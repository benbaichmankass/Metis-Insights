#!/usr/bin/env python3
"""RunPod provider adapter for the M19 GPU-burst tier — launch / verify / teardown.

The RunPod-specific half of `run_burst.sh`. Uses the official `runpod` Python SDK
(`pip install runpod`). Its ONE job is to launch exactly one **community-cloud
(spot)** pod, hand back its id + billed rate, and **guarantee teardown** — pod
termination is in a `finally`, so a crash/timeout can never leak a billing pod.

Two entry points:

- ``--verify`` — the money-safety smoke test: launch the cheapest pod, confirm it
  reaches RUNNING, then terminate it immediately. A few cents; proves
  launch→bill→teardown end-to-end BEFORE any real (armed) training run. Run this
  once after the key lands; only then set ``GPU_BURST_ARMED=1``.
- default — launch a pod for a real burst, run the training command on it, retrieve
  the exported CPU artifact, terminate. (The on-pod train/export exec is finalized +
  validated during the first ``--verify`` pass; until then the real path stops after
  a successful launch+teardown so it can't half-run untested logic.)

Cost facts (gpu_type, rate, gpu_hours, cost) are written to ``--emit-github-output``
(``$GITHUB_OUTPUT``) for the workflow's ledger record-run step.

Safety: reads ``RUNPOD_API_KEY`` from the env (never logged). No secrets, money-DB,
or live creds ever go to the pod. Only the read-only training corpus goes up; only
the exported CPU artifact comes back.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
from typing import Any

from scripts.ml.gpu_burst import _remote

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


def _resolve_ssh_key() -> tuple[str | None, str | None]:
    """Materialize the account SSH private key from the RUNPOD_SSH_KEY secret.

    RunPod's proxy SSH (ssh.runpod.io) authenticates against a key registered on
    the ACCOUNT (auto-injected by the platform), NOT a per-pod env key — so the
    runner holds the matching PRIVATE key as the RUNPOD_SSH_KEY Actions secret.
    Writes it to a run-scoped 0600 temp file and derives its public half (passed
    back as PUBLIC_KEY too, belt-and-suspenders). Returns (None, None) when the
    secret is unset.
    """
    raw = os.environ.get("RUNPOD_SSH_KEY", "").strip()
    if not raw:
        return None, None
    d = tempfile.mkdtemp(prefix="ict-burst-key-")
    key_path = os.path.join(d, "id_runpod")
    with open(key_path, "w", encoding="utf-8") as fh:
        fh.write(raw + "\n")   # a trailing newline is required for a valid key file
    os.chmod(key_path, 0o600)
    try:
        pub = subprocess.run(
            ["ssh-keygen", "-y", "-f", key_path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - pub is a bonus; account-injection is the primary path
        pub = None
    return key_path, pub


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


def _ssh_capture(pod_id: str, key_path: str, command: str, timeout_s: int) -> tuple[int, str, str]:
    """Run one proxy-SSH command; return (rc, stdout, stderr). A timeout → rc 124."""
    argv = _remote.ssh_argv(pod_id, key_path, command)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", (e.stderr or "") + "\n(timeout)"
    return proc.returncode, proc.stdout, proc.stderr


def _wait_ssh_ready(pod_id: str, key_path: str, timeout_s: int = _SSH_READY_TIMEOUT_S) -> bool:
    """Poll until the pod's sshd accepts the injected ephemeral key (or give up)."""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        rc, out, err = _ssh_capture(pod_id, key_path, "echo __ssh_ok__", timeout_s=30)
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


def run(
    *,
    experiment: str,
    gpu_type: str,
    image: str,
    verify: bool,
    emit_path: str | None,
    ssh_probe: bool = False,
) -> int:
    runpod = _sdk()
    pod_id = ""
    gpu_used = gpu_type
    started = time.monotonic()
    rate = 0.0
    # Any SSH path (probe today, full train next) authenticates with the account
    # SSH key (RUNPOD_SSH_KEY secret) over RunPod's proxy — the platform injects
    # the matching account public key into the pod. Pure --verify stays key-free.
    key_path = pub = None
    if ssh_probe or not verify:
        key_path, pub = _resolve_ssh_key()
        if not key_path:
            print("::error::RUNPOD_SSH_KEY secret unset — add the account SSH private key "
                  "(matching a public key registered in RunPod › Settings › SSH Keys). "
                  "No pod launched, no spend.")
            return 3
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
                    support_public_ip=False,
                    # RunPod's official templates read PUBLIC_KEY at start to write
                    # authorized_keys (SSH_PUBLIC_KEY is only an "override" the base
                    # template doesn't honour — using it left the key uninstalled →
                    # Permission denied). Pass both; PUBLIC_KEY is the one that works.
                    env={"PUBLIC_KEY": pub, "SSH_PUBLIC_KEY": pub} if pub else None,
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

        # SSH paths need the pod's sshd to accept the injected key first.
        if not _wait_ssh_ready(pod_id, key_path):
            print(f"::error::pod {pod_id} never accepted SSH within {_SSH_READY_TIMEOUT_S}s — tearing down.")
            return 7

        if ssh_probe:
            rc, out, err = _ssh_capture(pod_id, key_path, _PROBE_CMD, timeout_s=_SSH_CMD_TIMEOUT_S)
            print("---- pod probe stdout ----")
            print(out)
            if err.strip():
                print("---- pod probe stderr ----")
                print(err)
            if rc != 0 or "=== probe end ===" not in out:
                print(f"::error::probe command failed (rc={rc}) — SSH reached but pod env check incomplete.")
                return 8
            print(f"SSH PROBE OK — proxy SSH + ephemeral key work on pod {pod_id}; tearing down.")
            return 0

        # Full on-pod train → export → parity → artifact-return is wired on top of
        # the _remote helpers once this probe confirms SSH end-to-end (next increment).
        print("::error::full train path not yet wired — run a probe:true issue first to validate SSH, "
              "then the train driver lands. Stopping after launch (teardown follows).")
        return 5
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
                    help="Launch, prove proxy SSH + the ephemeral key work + the pod env (GPU/python/git), terminate.")
    ap.add_argument("--experiment", default="(unnamed)")
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
        )
    except RuntimeError as e:
        print(f"::error::{e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
