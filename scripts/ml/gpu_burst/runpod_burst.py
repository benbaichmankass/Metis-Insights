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
import time
from typing import Any

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
) -> int:
    runpod = _sdk()
    pod_id = ""
    gpu_used = gpu_type
    started = time.monotonic()
    rate = 0.0
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

        # Real burst: the on-pod train → CPU/ONNX export → artifact-retrieval exec is
        # finalized + validated during the first --verify pass over a live pod (the
        # SSH/exec + rsync surface can't be exercised from CI without a key). Until
        # then, stop cleanly after a confirmed launch+teardown rather than run
        # unvalidated on-pod logic.
        print("::error::on-pod train/export not yet validated for RunPod — stopping after launch (teardown follows). "
              "Complete + verify the exec path during the first --verify run, then re-run.")
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
            emit_path=args.emit_github_output,
        )
    except RuntimeError as e:
        print(f"::error::{e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
