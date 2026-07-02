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

## The three arm steps (operator + Claude)

1. **Pick a provider + create the account.** RunPod (or Vast) community spot is the
   spec's pick — **~$0.20–0.40/GPU-hr**, so $10/mo buys ~25–33 GPU-hr. (OCI GPUs work
   but are ~$2/hr on-demand with no cheap spot + need a service-limit-increase ticket
   — ~5× fewer GPU-hr per dollar; only sensible for a one-off first test.)
2. **Add the provider API key to Actions secrets** (e.g. `RUNPOD_API_KEY`) and set the
   repo **variable** `GPU_PROVIDER` (`runpod` | `vast` | `oci`). *(Human step — a
   secret value.)* Funding a **small prepaid balance** on the provider is the
   belt-and-suspenders second ceiling (the account can't overspend it).
3. **Verify + arm.** Claude fills in `provider_launch`/`provider_teardown` in
   `scripts/ml/gpu_burst/run_burst.sh` for the chosen backend, verifies a manual
   launch→terminate leaves no running pod, then the operator sets the repo variable
   **`GPU_BURST_ARMED=1`**. Until then every trigger is a **dry preflight only**.

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
