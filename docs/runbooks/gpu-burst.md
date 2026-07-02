# GPU-burst training tier — runbook (M19 Tier-1)

Ledger-gated, teardown-guaranteed spot-GPU bursts. Train on a rented spot GPU in
short bursts, **export inference to CPU/ONNX**, serve on the free VMs — no torch/CUDA
ever on the money-box. Spec: [`docs/research/T1-gpu-burst-spend-SPEC.md`](../research/T1-gpu-burst-spend-SPEC.md).

## Status

- **Spend approved** (operator, 2026-07-02); **cost-tracking shipped first**
  (`comms/gpu_spend_ledger.json` + `GET /api/bot/gpu/spend` + dashboard GPU-spend panel).
- **Workflow spine shipped** (`.github/workflows/gpu-burst-train.yml`) — runs the
  budget preflight + comments back **with zero spend** until armed.
- **Pending:** pick a provider, add its key, verify the launch/teardown adapter, arm.

## Provider: RunPod community spot (chosen 2026-07-02)

**~$0.20–0.40/GPU-hr**, so $10/mo buys ~25–33 GPU-hr. Adapter:
`scripts/ml/gpu_burst/runpod_burst.py` (official `runpod` Python SDK), dispatched by
`run_burst.sh` when `GPU_PROVIDER=runpod`. It launches ONE community-cloud pod and
**terminates it in a `finally`** — a crash/timeout can't leak a billing pod.

## The arm steps (operator + Claude)

1. **Operator — create the account + key.** Sign up (runpod.io) → create an API key →
   add it to the bot repo's Actions secrets as **`RUNPOD_API_KEY`**; set repo
   **variables** `GPU_PROVIDER=runpod` (leave `GPU_BURST_ARMED` unset). Fund a **small
   prepaid balance** — the belt-and-suspenders second ceiling (the account can't
   overspend it). *(The only human step — a secret value.)*
2. **Claude — verify launch+teardown.** Open a `gpu-burst-train` issue with
   `verify: true` while `GPU_BURST_ARMED=1` is set *temporarily* — the adapter's
   `--verify` path launches the cheapest pod, confirms it reaches RUNNING, and tears
   it down (a few cents), proving launch→bill→teardown end-to-end. Confirm on the
   RunPod console + the dashboard GPU-spend panel that no pod lingers and the cost
   posted. This is where the on-pod train/export exec is finalized against a live pod.
3. **Arm.** Once the verify run is clean, the operator sets **`GPU_BURST_ARMED=1`**
   for real. Until armed, every trigger is a **dry preflight only** (no spend).

## Running a burst

Open an issue labelled **`gpu-burst-train`** with a body like:

```
experiment: T1.1 deep-head bake-off
est_cost: 0.40
train_cmd: python -m ml train btc-regime-15m-tcn-v1 --datasets-root datasets-out
max_minutes: 90
```

The workflow then:
1. **Preflight** — aborts if `month-to-date + est_cost` would exceed the $10 cap.
2. **Arm gate** — if not armed, comments the preflight result and stops (no spend).
3. **Burst** (armed only) — `run_burst.sh` launches the one spot pod, trains, exports
   the CPU/ONNX artifact (with a numeric parity gate) to the model mirror, and **tears
   the pod down in a bash EXIT trap** (crash/timeout-safe).
4. **Record** — appends the actual billed cost to `comms/gpu_spend_ledger.json`
   (committed) → visible on the dashboard GPU-spend panel + `GET /api/bot/gpu/spend`.

## Safety properties

- **Ledger-gated:** the committed ledger is the source of truth; the workflow refuses
  to launch past the monthly cap (`scripts/ml/gpu_burst/preflight.py`).
- **Teardown-guaranteed:** pod termination is in a `trap ... EXIT`, so a failed run
  can't leak a billing pod. **Follow-up:** a scheduled "reaper" workflow that lists +
  kills any pod older than `max_minutes` as a provider-side backstop (added with the
  provider adapter).
- **Data one-way:** only the read-only training corpus goes *to* the pod; only the
  exported CPU artifact comes back. No secrets / money-DB / live creds touch the pod.
- **First experiment:** the ~1-GPU-hr **T1.1 deep-head bake-off** vs
  `btc-regime-15m-lgbm-base-pcv` — the cheapest falsifiable "does a deep model beat
  the incumbent tree at all?" (~$0.30–0.40). Its cost posts as the first ledger row.
