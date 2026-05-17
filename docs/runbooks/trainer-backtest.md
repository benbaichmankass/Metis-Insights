# Trainer-VM backtest sweep — runbook

Adopted 2026-05-17 in S-TRAINER-BT-1, as part of the PR #1358
post-incident remediation (full record:
`docs/sprint-logs/S-AUDIT-PIPELINE-2026-05-17.md` § Addendum). The
operator approved building proper testing infrastructure rather than
patching around the trainer's missing venv.

## What this is

A reproducible, one-issue backtest dispatch for the trainer VM. The
trainer has a 3-year BTCUSDT 5m parquet cache, a fresh `.venv`, and a
3-step orchestrator that produces a comparable Sharpe / cadence / max-
DD table across vwap, turtle_soup, and ict_scalp variants.

## How to invoke (Claude session)

Open a GitHub issue with label `trainer-vm-diag-request` and body:

```yaml
cmd: |
  cd /home/ubuntu/ict-trading-bot && git pull --ff-only && \
    bash scripts/ops/run_backtest_sweep.sh
```

The `trainer-vm-diag.yml` workflow runs the bash on the trainer over
SSH, posts the orchestrator's stdout (including the final SUMMARY.md
table) back as an issue comment, and auto-closes the issue. The diag
relay's comment limit is ~62 KB; the harness's stdout fits well under
that (typical: 5–10 KB).

Full output also persists on the trainer at:
```
/home/ubuntu/ict-trader-data/backtests/<UTC-date>/
  all_metrics.json       — per-variant Metrics dataclass dump
  SUMMARY.md             — markdown table (same as the comment tail)
  harness_stdout.log     — verbose run log
  ict_scalp_metrics.json — separate harness's output
  ict_scalp_stdout.log
```

## How to invoke (operator, manual)

SSH into the trainer (`ssh ubuntu@<trainer-ip>`) and run the same
command. The runbook lives in the repo so the workflow and the manual
path read the same source of truth.

## Pieces

| File | Role |
|---|---|
| `scripts/ops/run_backtest_sweep.sh` | Entry-point orchestrator. Composes the four steps below. |
| `scripts/ops/trainer_bootstrap.sh` | Step 1. Creates `$REPO_ROOT/.venv` (python3.11) if missing; installs `requirements.txt` + `requirements-backtest.txt`. Idempotent. |
| `scripts/ops/fetch_qashdev_btc_archive.py` | Step 2. Refreshes the 3-year monthly CSV cache in `/home/ubuntu/ict-trader-data/raw/` and consolidates to a parquet at `/home/ubuntu/ict-trader-data/btc_5m.parquet`. Incremental — only fetches months not already cached, only rebuilds the parquet when raw is newer. |
| `experiments/2026-05-17-post-incident-validation/scripts/run.py` | Step 3. The variant harness. Runs vwap V_BASELINE + V_PROD, turtle_soup TS_PROD + extended T3 sweep (`min_sweep_buffer_bps ∈ {3, 5, 7, 10, 12}`), turtle_soup 5m naive port. Imports the backtest engine from the 2026-05-08 harness via `importlib` (dir name starts with a digit so cannot be a regular Python package). |
| `scripts/backtest_ict_scalp.py` | Step 4. Existing ict_scalp_5m backtest CLI; the orchestrator invokes it for the v2 re-validation. |
| `requirements-backtest.txt` | Pyarrow + requests + python-dateutil. Pandas/numpy are already in `requirements.txt`. |

## Gate criteria

Same as the canonical pre-live gate (#1143 / PR #1156):

| Criterion | Threshold |
|---|---|
| Win rate | ≥ 40 % |
| Expectancy R | ≥ +0.20 |
| Total R | > 0 |
| Max DD R | ≤ 8 |
| Per-trade Sharpe | ≥ 0.5 (or annualized ≥ 1.5) |

A variant clearing all five is recommended for production. A variant
clearing most but losing cadence vs production is flagged for
operator decision — never auto-applied to `config/strategies.yaml`
(that path remains Tier-3 per `docs/CLAUDE-RULES-CANONICAL.md` §
Permission Tiers).

## Expected timing

| Step | Wall-clock | Notes |
|---|---|---|
| 1. bootstrap (cold) | ~90 s | Fresh `.venv`, downloads + builds pandas/numpy/pyarrow. |
| 1. bootstrap (warm) | ~5 s | Re-uses existing venv. |
| 2. fetch (cold) | ~3 min | 38 monthly CSVs × ~150 KB each from raw.githubusercontent.com. |
| 2. fetch (warm) | ~10 s | Skips cached months, re-validates parquet mtime. |
| 3. harness | ~60 s | vwap + turtle_soup 9 variants on 3 years of data. |
| 4. ict_scalp | ~10 s | Small backtest fixture (`data/backtest_candles.csv`). |
| **End-to-end (warm)** | **~90 s** | Typical re-run. |
| **End-to-end (cold)** | **~6 min** | First run on a fresh trainer. |

## Failure modes

**`python3.11` not found.** Cloud-init bootstrap is incomplete. Re-
provision via `provision-training-vm` workflow, or install
python3.11 manually. trainer_bootstrap.sh exits with code 2 and a
clear message.

**parquet missing after fetch.** Network failure or qashdev/btc gone.
The fetcher retries 4× with exponential backoff (2 / 4 / 8 / 16 s);
if every month fails, the script exits 1. Re-run after the upstream
recovers, or point `QASHDEV_BASE_URL` at a mirror.

**Pandas / numpy import errors during the harness.** Means the venv
isn't being activated. Check `which python` inside the diag relay —
it should be `$REPO_ROOT/.venv/bin/python`, not `/usr/bin/python3`.
The orchestrator pins the venv path explicitly so this should only
happen if `$VENV_DIR` is overridden incorrectly.

**Gate fails on a previously-passing variant.** A regression to
investigate before any production change ships. Compare
`all_metrics.json` against the 2026-05-08 baseline at
`experiments/2026-05-08-all-models-training/results/all_metrics.json`.
The two runs use the same engine on overlapping data, so common
variants should match within sampling error. A large delta means a
strategy code change (signal logic, fee model, fills simulation) has
moved the numbers; bisect against `git log -- src/units/strategies/`.

## Related infrastructure

- ML training cycles run via `scripts/ops/run_training_cycle.sh`
  (different orchestrator, manifest-driven, writes to the model
  registry). This runbook is **backtest sweeps only** — no model
  training, no registry writes.
- The Bybit 365-day window fetcher at
  `scripts/ops/fetch_backtest_candles.py` covers the GHA workflows
  (`vwap-backtest.yml`, `ict-scalp-backtest.yml`) which run inside
  CI runners and don't have access to the trainer VM's persistent
  cache. Use the GHA workflows for quick single-config checks on a
  smaller window; use this sweep for multi-year, multi-variant runs.
- Trainer VM authority and detection: `docs/claude/trainer-vm-mode.md`.
- Live-trader runbooks: `docs/runbooks/`.

## Adding a new variant

Edit `experiments/2026-05-17-post-incident-validation/scripts/run.py`.
The variant grid is enumerated in `main()` — add a new entry to the
appropriate group dict and it lands in the SUMMARY table on next
run. Engine changes (e.g. new exit logic, fee model) belong in the
2026-05-08 harness, not here — both runs share the engine.

## Adding a new strategy

If a new strategy unit ships in `src/units/strategies/`, the
backtest engine needs a new signal-function factory analogous to
`_make_vwap_signal_fn` / `_make_turtle_signal_fn`. Add it to the
2026-05-08 harness so both this sweep and any future sweep can use
it. Then add variants here against the new factory.
