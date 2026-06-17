# S9 yz shadow heads — pre-promotion review (2026-06-09)

Investigation of four loose ends carried from the 2026-06-08 S9 yz monitoring
session, ahead of the operator's ~2026-06-12 promotion review of the three
range-vol (Yang-Zhang) BTC regime heads. Resolves/updates backlog items
`MB-20260609-001`, `MB-20260609-002`, `BL-20260609-004`, `BL-20260609-003`.

Live/eval state pulled via the diag relays (trainer-vm-diag issue #3046; CI
state via the GitHub Actions API). All three heads are at stage `shadow`.

---

## 1. PR #3007 — CI never triggered after the 2nd push (BL-20260609-004; was BL-20260609-001, renumbered 2026-06-17 to clear an id collision with the live-VM CPU-saturation incident)

**Not a CI bug. Root cause: a merge conflict left the PR `dirty`, which blocks
GitHub from building the merge ref, so the second push never triggered the
`pull_request` workflows.** Fixed this session.

Evidence:

- All **11** checks ran and passed on the **first** commit `e286c4ea`
  (`pull_request` event, 2026-06-08T15:05:11Z) — `pytest-run`, `pytest-collect`,
  `ruff-lint`, the canonical guards, etc.
- The PR head had advanced to a **second** commit `61c64816` (the doc-freshness
  backlog appends). That commit had **zero** workflow runs. `get_status` /
  `get_check_runs` report against the *head* SHA only, so they showed
  `total_count: 0` — which the previous session read as "CI never fired."
- `mergeable_state` was `dirty`: the 2nd commit's appends to
  `health-review-backlog.json` conflicted with main's own diverged backlog
  appends. GitHub cannot compute `refs/pull/3007/merge` for a conflicted PR, so
  the `synchronize` event produced no runs.
- The previous session's hypotheses (path/branch filters, idle runners) are
  ruled out: **no CI workflow has a path filter** — `pytest-run`, `ruff-lint`,
  `canonical-*`, `dry-run-guard`, etc. all fire on any `pull_request:
  branches:[main]`, and draft status does not block them.

Fix applied: merged `origin/main` into `claude/vibrant-babbage-9Wbls` (the only
conflict was the trailing `updated_at` scalar; both sides' backlog entries
preserved, JSON re-validated) and pushed `61c6481..2be6549`. Result:
`mergeable_state` → `blocked` (conflict cleared; now only gated by branch
protection), and **all 11 checks re-fired and are green** on the new head.
`GET .../update-branch` had failed first with `422 merge conflict`, confirming
the conflict was real.

---

## 2. 15m head "reduced feature set" vs 1h/5m (MB-20260609-001)

**Intended — not a wiring gap. Nothing to fix before promotion.** The five
fields the 15m head's `row_keys_seen` is "missing" (`confidence`, `direction`,
`killzone`, `setup_type`, `strategy_name`) are **not model features** — they are
signal-time *correlation metadata*, and their presence/absence reflects which
*scoring path* fired, not the model's input vector.

Verified against the code and the three manifests:

- **The three manifests are byte-for-byte identical in `feature_columns`** — all
  11 inputs are bar/OHLC-derived (`vol_bucket`, `rolling_log_return_vol`, the
  four range-vol estimators, `log_return` + 2 lags, `hour_of_day`, `dayofweek`).
  None reference the five "missing" fields.
- `ml/predictors/shadow.py` docstring (lines 43–47) states the `feature_row` /
  `row_keys` carry signal-time metadata for the trade↔score join and **"does
  NOT"** serve as model input — the projection helpers feed the model only the
  bar features.
- Two scoring paths write to `shadow_predictions.jsonl`:
  - **Per-bar** (`src/runtime/regime_bar_scoring.py`): `base_row =
    {symbol, timeframe, event_source:"per_bar"}` → **no** signal-context fields.
  - **Signal-time** (`strategy_signal_builders._emit_shadow_preds`): attaches
    `strategy_name/direction/confidence/setup_type/killzone` **and passes the
    firing strategy's own timeframe**. `feature_row_for_predictor`
    (`src/runtime/regime_shadow.py`) then **skips any regime head whose
    `(symbol, timeframe)` does not match** (`return None`).
- Therefore a 15m head gets signal-context records **only when a 15m BTCUSDT
  strategy (turtle_soup) actually fires** — which it rarely does. The 1h and 5m
  heads pick up signal-context records because strategies on those timeframes
  (vwap / ict_scalp_5m on 5m; etc.) fire.

The shadow record **counts** confirm this exactly (window ≈ 3.9 d):

| head | count | bar-cadence expectation | signal records (excess) | signal-context fields |
|---|---|---|---|---|
| 1h  | 127  | ~94 (1 bar/h)    | ~33  | present |
| 15m | 390  | ~376 (4 bars/h)  | ~0   | **absent** |
| 5m  | 1537 | ~1128 (12 bars/h)| ~409 | present |

The 15m head is ≈ pure bar-cadence (390 ≈ 376) with zero signal records — i.e.
turtle_soup fired no actionable signals in the window. The per-bar path
(S-MLOPT-S13) was built precisely so heads whose strategies rarely fire still
accrue a track record, so the 15m head **is** accruing evidence as designed.

Optional polish (non-blocking): add one line to the 15m manifest `description`
noting its shadow records are currently bar-cadence-only because turtle_soup
fires rarely, so a future reader doesn't re-flag the `row_keys` delta.

---

## 3. 5m head class-distribution / "collapse" check (MB-20260609-002)

**Not class-collapsed in the feared sense; the high `score_mean` is benign. But
the 5m head is the *weakest* volatile detector of the three and is a weak
promotion candidate on its own eval.**

Key correction: the logged `score` is **max-class probability**
(`MulticlassPredictor.predict` returns `max(proba.values())`), range [0.5, 1.0]
for these 2-class (range / volatile) heads. It records the model's *confidence
in whichever class it picked* — **not** P(volatile) and **not** the predicted
label. So a high `score_mean` alone cannot indicate collapse.

Predicted-class distribution, reconstructed from the latest trainer eval
(`metrics.json`, run 20260609; `predicted_c = recall_c·support_c / precision_c`,
which sums to `n_eval` exactly for all three):

| head | volatile base rate | **pred volatile %** | pred range % | precision_vol | recall_vol | **f1_vol** | score_mean |
|---|---|---|---|---|---|---|---|
| 1h  | 17.29% | **30.2%** | 69.8% | 0.374 | 0.655 | **0.476** | 0.77 |
| 15m | 2.67%  | **8.4%**  | 91.6% | 0.163 | 0.511 | **0.247** | 0.84 |
| 5m  | 0.35%  | **1.1%**  | 98.9% | 0.082 | 0.264 | **0.125** | 0.93 |

Findings:

- The 5m head predicts **range 98.9%** of the time, volatile only **1.1%**. It
  is **not** collapsing toward an over-predicted volatile class. The high
  `score_mean` (0.93) is *confident, and overwhelmingly correct, RANGE
  prediction* — range is 99.65% of 5m bars (`support_volatile`=367 of 105,173).
- **`score_mean` ordering (5m 0.93 > 15m 0.84 > 1h 0.77) is the exact inverse of
  detector quality (1h f1_vol 0.476 > 15m 0.247 > 5m 0.125).** `score_mean` is
  purely a function of how dominant `range` is at each timeframe — it is **not**
  a quality or collapse signal. This retires the `score_mean`-based collapse
  hypothesis.
- The count anomaly flagged for the 5m head ("~36% above bar cadence") is the
  ~409 legitimate **signal-time** records layered on top of ~1128 bar records
  (§2) — two designed paths both writing, not double-scoring a bug.
- Genuine concern for the 5m head: at a 0.35% volatile base rate, the detector
  adds little — **precision_volatile 0.082** means 92% of its volatile calls are
  false alarms, and it catches only 26% of true volatile bars (recall 0.264).
  The `class_weight volatile:50.0` (vs 28.0 @15m, 4.0 @1h) did not overcome the
  extreme imbalance. The manifest's promotion rationale (yz beats v2 on
  `f1_volatile` by +0.017 @5m) is a *real but tiny relative* lift on an
  *absolutely poor* detector.

**Promotion read (operator-gated, 2026-06-12):**

- **1h yz** — strongest candidate. Genuine minority-class signal (f1_vol 0.476,
  recall 0.655); predicts volatile 30% vs 17% true.
- **15m yz** — marginal (f1_vol 0.247, recall 0.511); defensible to keep in
  shadow longer.
- **5m yz** — **hold.** Not collapsed, but volatile detection too weak
  (f1_vol 0.125, precision 0.082) to justify influencing orders; promoting it
  would inject mostly-wrong "volatile" flags ~1.1% of the time.

Caveat: this is the held-out training eval (train/serve parity per S-MLOPT-S17).
The live shadow log records only confidence, not the predicted label, so the
predicted-class share cannot be reconstructed from `shadow_predictions.jsonl`
alone — the trainer eval is the authoritative source for class distribution.

---

## 4. Trainer mirror 21.8 d stale (BL-20260609-003)

**The publisher is healthy; the previous session's "publisher hasn't run for
~21.8 days" framing is incorrect.** Two corrections:

- `ict-trainer-publish.timer` is **enabled + active (waiting)**, firing
  `ict-trainer-publish.service` every ~2 min. It ran successfully at
  2026-06-09T06:09:55Z (and every 2 min before that); `trainer_status.json`,
  `training_cycle.jsonl`, and the registry mirror are **fresh** on the live VM.
  The last training cycle completed today (00:13–00:25 UTC, rc=0) followed by
  `publish_post_ok`.
- Only the **backtest sweeps** are stale. Their generator,
  `scripts/ops/run_backtest_sweep.sh` (S-TRAINER-BT-1, written 2026-05-17),
  is **on-demand only — there is no timer or cron for it** (its own header
  documents it as relay-invoked: `cmd: ... bash
  scripts/ops/run_backtest_sweep.sh`). It writes to
  `$ICT_TRADER_DATA_ROOT/backtests/<UTC-date>/`, which the publisher rsyncs.
  It was last run 2026-05-17 (the day it was created) and never since — hence
  `/api/bot/backtests/sweeps` reads 2026-05-17 as newest. **Nothing "stopped";
  there was never a schedule.**

Action taken: launched one fresh detached sweep on the trainer
(trainer-vm-diag #3047) to confirm the generator works end-to-end and refresh
the mirror; the 2-min publish timer mirrors the result on completion. Verify via
`/api/bot/backtests/sweeps` (newest date should advance to 2026-06-09).

Decision / recommendation:

- There is no broken timer to "re-arm." If recurring sweep freshness is wanted,
  add an `ict-backtest-sweep.timer` on the trainer (e.g. weekly) — a small
  Tier-2 trainer-side change (trainer is autonomous territory).
- Otherwise accept the gap as by-design: sweeps are an expensive on-demand
  validation harness, refreshed when triggered. The dashboard's freshness banner
  should be read as "no sweep triggered since X," not "publisher down."

---

## Net actions this session

- **#3007 CI:** conflict resolved, pushed, all checks green (item 1 done).
- **Item 2:** intended-by-design; no fix needed (manifest doc-polish optional).
- **Item 3:** class-distribution produced; 5m head is weak, not collapsed;
  promotion read recorded for the 2026-06-12 review.
- **Item 4:** publisher healthy; sweep generator is on-demand; fresh sweep
  launched + timer recommendation recorded.

When PR #3007 merges (it owns `MB-20260609-001/002` and `BL-20260609-003`), a
follow-up can mark those entries resolved citing this audit.
