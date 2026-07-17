# Trainer-VM resource protocol — the heavy-job queue + GPU-burst routing

> **Binding for any session that runs training/ML work on the trainer VM.**
> Adopted 2026-07-17 (BL-20260715-TRAINER-CYCLE-MEM-SATURATION +
> BL-20260717-TRAINER-CYCLE-TERM-AT-START). The problem this solves: multiple
> Claude sessions + scheduled timers all wanted the trainer VM's RAM at once,
> collided, and thrashed the box — so sessions were `sudo systemctl stop`-ing
> jobs by hand to recover it.

## The resource reality (don't fight it — fit it)

The trainer VM is **1 OCPU / 6 GB** (Ampere Always-Free; the pool is **full**,
so we can't grow it). Its memory-heavy jobs each need a big slice of that 6 GB:

| Job | Trigger | Peak RSS | Notes |
|---|---|---|---|
| Training cycle (`python -m ml train` per manifest) | `ict-trainer.timer` (daily ~00:44) + catch-up | **~5 GB** per manifest | `MemoryMax=5G` cgroup cap → cgroup-OOM if it exceeds |
| Promotion-readiness sweep | `ict-promotion-readiness.timer` (**04:00**, moved off the cycle window 2026-07-17) | **~3.2 GB** / ~99 min | |
| Drift-retrain scan | `ict-drift-retrain.timer` (hourly) | light when plan-only; **~5 GB** if it dispatches `ml train` | |
| **Manual session training** | a human/session runs `python -m ml train …` | **~5 GB** | the uncoordinated one that caused the thrash |

**Any two heavy jobs at once > 6 GB → cgroup-OOM or swap-death.** The
`MemorySwapMax=512M` cap deliberately prevents unbounded swap-thrash (which
wedges SSH), so the failure mode is a clean OOM-kill or a manual stop — not a
recoverable slow run.

## Rule 1 — everything memory-heavy goes through the shared QUEUE

There is **one** shared lock (`runtime_logs/trainer/.heavy.lock`,
`scripts/ops/_trainer_heavy_lock.sh`). Every heavy job acquires it **blocking**
before starting real work and holds it until done, so heavy jobs **serialize**
(a FIFO-ish queue) instead of colliding. Work still gets done — just one at a
time — which is the correct trade on a fixed 6 GB box.

- The three timer wrappers (`run_training_cycle.sh`,
  `run_promotion_readiness.sh`, `run_drift_retrain.sh`) already take the lock;
  if the queue stays busy past `TRAINER_HEAVY_LOCK_WAIT_S` (default 1 h) they
  **skip that run and retry on the next timer** (not a failure).
- **Manual sessions MUST use the queue too.** Never run a bare
  `python -m ml train …` / `build-dataset` on the trainer. Run it through:

  ```bash
  scripts/ops/trainer_run.sh python -m ml train ml/configs/<manifest>.yaml
  ```

  It waits its turn, then runs. If the queue is busy > the wait it exits 75 and
  tells you to try later or use the GPU burst (Rule 2). This is the fix for the
  Jul 14–17 manual-stop thrash: you no longer have to babysit/kill jobs — the
  queue does it.

Do **not** `sudo systemctl stop ict-trainer.service` to "make room" — that just
kills an in-flight cycle (the thing we were doing wrong). Start your work
through `trainer_run.sh` and it will queue safely.

**What counts as "memory-heavy" (queue) vs light (run direct).** The queue is
for the ~5 GB jobs — `python -m ml train`, `build-dataset`, a big sweep. A
**per-strategy research backtest** (`scripts/backtest_*.py` over a candle CSV)
is a **light** job (vectorized pandas over a resampled ~15 k-bar frame, well
under 1 GB) — run it **directly**, NOT through `trainer_run.sh`. Wrapping a light
backtest in the queue makes it block up to `TRAINER_HEAVY_LOCK_WAIT_S` (1 h)
behind a running training cycle for no memory benefit (observed 2026-07-17: a
direction-filter backtest sat stuck an hour behind another session's cycle). A
light job running concurrently with a cycle uses the box's spare headroom
safely; only the 5 GB jobs need to serialize.

## Rule 2 — route heavy training to the GPU burst when it's the better resource

The trainer VM is not the only training resource. The **GPU-burst platform**
(`.github/workflows/gpu-burst-train.yml` + `scripts/ml/gpu_burst/`, RunPod spot)
trains off-box on a rented GPU, on a **$10 / month** budget tracked in
`comms/gpu_spend_ledger.json` (surfaced at `/api/bot/gpu/spend`; the workflow
spend-gates against the cap). It does **not** touch the 6 GB VM's RAM at all.

**Prefer the GPU burst over the trainer VM when** — and it's within the
remaining monthly budget:

- the run is **large / experimental / one-off** (a deep-sequence model, a big
  sweep, a from-scratch retrain) rather than the routine daily cycle;
- it would otherwise **block the queue** for a long time (a multi-hour train
  starving the daily cycle + readiness sweep);
- GPU acceleration materially shortens it (deep/sequence models — TCN, TSFM,
  larger LGBM sweeps).

**Keep it on the trainer VM when** the run is small, routine, part of the daily
cycle, or the GPU budget for the month is spent. Check
`/api/bot/gpu/spend` (or `comms/gpu_spend_ledger.json`) first; staying within
the cap is mandatory (the workflow enforces it, but plan for it).

Rule of thumb: **routine + cheap → trainer queue; heavy + within budget →
GPU burst.** Either way, never run heavy training on the trainer VM *outside*
the queue.

## Rule 3 — if the workload genuinely won't fit, that's a flag, not a hack

If a *single* manifest can't train within the 5 GB cgroup cap (a real OOM even
alone, e.g. the Jul 15 cycle), the fix is one of: (a) shrink that manifest's
peak (batch size / dataset chunking) — a model-specific code change; (b) move
it to the GPU burst; or (c) drop/split it. Do **not** raise `MemorySwapMax`
above 512 M to "fix" it — that re-opens the swap-death that wedges the box.
Raise it as a health-review backlog item with the offending manifest named.

## Tuning knobs

| Env | Default | Meaning |
|---|---|---|
| `TRAINER_HEAVY_LOCK_WAIT_S` | `3600` (1 h) | Max queue wait before a job skips (timer) / gives up (manual). |
| `TRAINER_HEAVY_LOCK_FILE` | `runtime_logs/trainer/.heavy.lock` | The shared lock file. |

Related: `docs/claude/trainer-vm-mode.md` (trainer-VM autonomy contract),
`docs/sprint-logs/S-M19-GPU-BURST-2026-07-02.md` (the burst platform).
