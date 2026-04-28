# Checkpoint log

Append-only log of Claude Code sessions on this repo.
Newest entry on top. Every session **must** add one entry before exiting.

Format: copy `HANDOFF_TEMPLATE.md` and fill it in.
ID convention: `CP-YYYY-MM-DD-NN` (sprint date + 2-digit sequence).

See `../checkpoint-workflow.md` for the full rules.

---

## CP-2026-04-28-12 — M7 Phase 2.4: ICT signal-builder factory

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port)
- **Last completed checkpoint:** CP-2026-04-28-11 (PR #51 merged — HTF
  trend helper).
- **Next checkpoint:** **CP-2026-04-28-13 — register `"ict"` in
  `src/runtime/pipeline.py`'s `_STRATEGY_BUILDERS` and the multiplexer
  `STRATEGIES` order.** Owner: Claude. Scope: thin wiring PR — adds an
  `ict_signal_builder(settings)` adapter in `pipeline.py` that fetches
  candles via the configured exchange and delegates to
  `src.runtime.strategies.ict.build_ict_signal`, then registers it.
  Includes runtime-side tests using a fake exchange. Keep PR-sized.

### Completed
- Created `src/runtime/strategies/` package (`__init__.py`).
- Implemented pure `build_ict_signal(candles_df, settings, htf_df=None)`
  in `src/runtime/strategies/ict.py`. Returns the standard
  `{symbol, side, qty, meta}` signal dict.
- Gates wired (in order): `htf_trend_bias` ≠ neutral → kill-zone gate
  (toggleable via `ICT_REQUIRE_KILLZONE`, default on) → aligned entry
  trigger (unfilled FVG preferred, OB fallback). All gate failures emit
  `side="none"` with `meta.reason` plus full diagnostic payload
  (`fvgs`, `order_blocks`, `kill_zone`, `trend_bias`) so the existing
  `_write_ict_signals_from_meta` writer keeps working.
- Added 12 unit tests in `tests/test_ict_signal_builder.py` covering
  empty input, missing trend source, neutral trend, kill-zone
  active/disabled, bullish FVG → buy, bearish FVG → sell, OB fallback
  (monkeypatched analyzer), no-aligned-zone branch, string-truthy
  settings parsing, invalid `MAX_QTY` fallback, and default-symbol path.
- Confirmed builder is **pure** — no exchange/DB/IO at module load or
  call time. Pipeline `_STRATEGY_BUILDERS` intentionally **not** touched
  this session per the operating rules.

### Files changed
- `src/runtime/strategies/__init__.py` (new)
- `src/runtime/strategies/ict.py` (new)
- `tests/test_ict_signal_builder.py` (new)

### Tests run
- `python scripts/repo_inventory.py` — clean (no junk candidates).
- `python scripts/secret_scan.py` — clean.
- `PYTHONPATH=. python -m pytest -q --ignore=tests/test_main_loop.py tests`
  → **302 passed**, 23 failed (pre-existing in `test_runtime_*`,
  unchanged from CP-11), 2 skipped. Test count delta vs CP-11: **+12**
  (matches new test file). Verified no regressions: this PR adds only
  new, untracked files that cannot affect the runtime-validation/
  pipeline test modules.
- Targeted suite: `pytest tests/test_ict_signal_builder.py -q` → 12/12.

### Remaining
- **CP-13:** runtime wiring PR — `ict_signal_builder(settings)` adapter
  in `pipeline.py` that pulls OHLCV from the configured exchange,
  passes it (plus optional HTF frame) to `build_ict_signal`, and
  registers `"ict"` in `_STRATEGY_BUILDERS`. Add
  `tests/test_runtime_ict.py` with a fake exchange.
- **CP-14:** decide on multiplexer ordering for `"ict"` and update
  `STRATEGIES` list (cheap PR after #13 merges).
- Backlog items 8/9 (VWAP) remain Colab/Ben-owned.
- Pre-existing 23 `test_runtime_*` failures still need their own
  cleanup checkpoint at some point (out of M7 scope).

### Next checkpoint
CP-2026-04-28-13 — `ict_signal_builder` adapter in `pipeline.py` +
registration in `_STRATEGY_BUILDERS`. Branch:
`feat/m7-ict-pipeline-wire`. Read `pipeline.py` only as needed; mirror
the `vwap_signal_builder` shape (lines 108–156) for the OHLCV fetch.

**PR:** [#52](https://github.com/the-lizardking/ict-trading-bot/pull/52) — `feat/m7-ict-signal-builder` (open, awaiting review/merge).

**Telegram sent:** yes

---

## CP-2026-04-28-11 — M7 Phase 2.3: HTF trend confluence helper

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port)
- **Last completed checkpoint:** CP-2026-04-28-10 (PR #50 merged — OB body
  filter).
- **Next checkpoint:** **CP-2026-04-28-12 — M7 Phase 2.4: wire ICT signals
  into a non-runtime entry point (`ict_signal_builder` factory) plus tests.**
  Owner: Claude. Scope: introduce a strategy builder that combines the
  existing FVG/OB detectors with the new HTF trend filter and the
  killzone gate, returning the standard `{symbol, side, qty, meta}`
  signal dict. **Do NOT register it in `pipeline.STRATEGIES` yet** — the
  registration step is its own checkpoint after a smoke-style test exists.
- **Blockers:** none. Branch `feat/m7-htf-trend-helper` is open and does
  not block CP-12.

### 1. Completed
- Added `src/ict_detection/trend.py` with two pure helpers:
  - `ema(series, length)` — standard `ewm(span=length, adjust=False)`
    EMA, exposed so callers and tests share a single numerical source of
    truth.
  - `htf_trend_bias(df, fast=20, slow=50, source="close", eps=1e-9)` —
    returns `"bullish"`, `"bearish"`, or `"neutral"` from the
    relationship between the two EMAs on the most recent bar. Empty
    frames, NaN-tail series, and prices inside the `eps` band all
    return `"neutral"` (no-information posture).
- Added `tests/test_htf_trend.py` (16 tests) covering EMA numerics
  against the pandas reference, monotone up / down / flat / V-shape
  bias outcomes, NaN-tail handling, eps-band classification, full
  argument validation (bad spans, missing source column, fast >= slow),
  and an alternate-source-column case.

### 2. Files changed
- `src/ict_detection/trend.py` (new, 149 lines)
- `tests/test_htf_trend.py` (new, 187 lines)
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (this entry)

### 3. Tests run
- `PYTHONPATH=. pytest tests/test_htf_trend.py -q` — 16 passed in 0.31s.
- `python scripts/repo_inventory.py` — pass.
- `python scripts/secret_scan.py` — pass.
- `PYTHONPATH=. pytest -q --ignore=tests/test_main_loop.py tests` —
  290 passed / 23 failed / 2 skipped. The 23 failures are the same
  pre-existing main failures. **+16 new passes vs CP-10 baseline; no new
  regressions.**

### 4. Remaining
- ICT signal-builder factory that combines FVG/OB + HTF trend + killzone
  gate (next checkpoint, CP-12).
- Register the factory under `STRATEGIES` (later checkpoint).
- Wire `ob_body_min_pct` into the live pipeline (M7 Phase 4 — still
  gated on multi-symbol Colab validation).
- Multi-symbol manifest fixtures for CI use of the backtest CLI.

### 5. Next checkpoint
**CP-2026-04-28-12** — Build a pure ICT signal-builder factory in
`src/runtime/strategies/ict.py` (new module) that takes a settings dict
and returns a `{symbol, side, qty, meta}` dict. Use the existing
`ICTSignalsAnalyzer` for FVG/OB and the new `htf_trend_bias()` to gate
direction. Add unit tests. Do **not** edit `src/runtime/pipeline.py` in
CP-12; registration in `_STRATEGY_BUILDERS` is its own checkpoint.

Read in order: this entry, `docs/claude/checkpoint-workflow.md`,
`docs/sprint-plans/sprint-plan-2026-04-28.md` § M7 Phase 2,
`src/runtime/pipeline.py` (read-only — to mirror the signal-dict shape),
`src/core/signals.py`, `src/ict_detection/trend.py`.

**Telegram sent:** yes (session-complete dispatched via Pipedream
Telegram connector from the agent runtime).

---

## CP-2026-04-28-10 — M7 Phase 2.2: OB body-size filter

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port)
- **Last completed checkpoint:** CP-2026-04-28-09 (PR #49 merged — backtest
  CLI scaffold).
- **Next checkpoint:** **CP-2026-04-28-11 — M7 Phase 2.3: HTF trend
  confluence filter.** Owner: Claude. Scope: add a higher-timeframe trend
  gate (e.g. 50-EMA on a coarser TF) to the ICT signal path so signals
  only fire in the direction of the dominant trend. Smallest safe subtask:
  introduce a pure helper `htf_trend_bias(df, fast=20, slow=50)` plus
  unit tests — no pipeline wiring in this first sub-checkpoint.
- **Blockers:** none. Branch `feat/m7-ob-body-threshold` is open and does
  not block CP-11.

### 1. Completed
- Added a `body_min_pct` parameter to `OrderBlockDetector.__init__`
  (`src/ict_detection/order_blocks.py`). Default `0.0` preserves the
  original any-body behaviour; positive values reject candles whose body
  is below that percentage of close. Both bullish and bearish OB paths
  honour the filter via a single `_passes_body_filter()` helper.
- Updated the `detect_order_blocks()` convenience function to forward the
  new parameter.
- Threaded the new threshold through `ICTSignalsAnalyzer.__init__` in
  `src/core/signals.py` as `ob_body_min_pct` (default `0.0`).
- Added `tests/test_ob_body_threshold.py` (9 tests) covering: default
  back-compat, monotonic filtering, non-zero OB detection on a synthetic
  trending fixture at 0.5% (the regime the research notebook flagged at
  the old 1.5% threshold), zero-close edge case, helper forwarding, and
  `ICTSignalsAnalyzer` wiring.

### 2. Files changed
- `src/ict_detection/order_blocks.py` (+37 / -7)
- `src/core/signals.py` (+9 / -2)
- `tests/test_ob_body_threshold.py` (new, 178 lines)
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (this entry)

### 3. Tests run
- `PYTHONPATH=. pytest tests/test_ob_body_threshold.py -q` — 9 passed.
- `PYTHONPATH=. pytest tests/test_fvg_ob.py tests/test_signals_analyzer.py
  tests/test_swing_detection.py tests/test_ob_body_threshold.py -q` —
  40 passed, 1 skipped (no regressions in adjacent ICT tests).
- `python scripts/repo_inventory.py` — pass.
- `python scripts/secret_scan.py` — pass.
- `PYTHONPATH=. pytest -q --ignore=tests/test_main_loop.py tests` —
  274 passed / 23 failed / 2 skipped. The 23 failures are the same
  pre-existing main failures (test_runtime_validation,
  test_runtime_pipeline, test_runtime_smoke). **+9 new passes vs CP-09
  baseline; no new regressions.**

### 4. Remaining
- HTF trend confluence filter (next checkpoint).
- Multi-symbol manifest fixture(s) for CI use of the backtest CLI.
- Wire `ob_body_min_pct` into the runtime pipeline once research nails
  the exact value (out of scope for the port — belongs in M7 Phase 4).

### 5. Next checkpoint
**CP-2026-04-28-11** — Add a pure HTF trend bias helper and unit tests.
Read in order: this entry, `docs/claude/checkpoint-workflow.md`,
`docs/sprint-plans/sprint-plan-2026-04-28.md` § M7 Phase 2,
`src/core/signals.py`, `src/ict_detection/`. Do not touch
`src/runtime/pipeline.py` in CP-11 — the wiring is a later sub-checkpoint.

**Telegram sent:** yes (session-complete dispatched via Pipedream Telegram
connector from the agent runtime).

---

## CP-2026-04-28-09 — M7 Phase 2.1: backtest CLI scaffold

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28
- **Current sprint phase:** Phase 3 — M7 Phase 2 (ICT runtime port)
- **Last completed checkpoint:** CP-2026-04-28-00 (workflow scaffolding) — note:
  M3a/M3b/M3c (PRs #35/#36/#37/#47), M4a–M4e (PRs #38–#42), and the M6
  multiplexer risk-cap test (PR #43) all merged earlier today directly into
  `main` ahead of the formal checkpoint log being introduced. Backlog items
  1–7 in the user's Apr-28 sprint prompt are therefore already on `main`.
- **Next checkpoint:** **CP-2026-04-28-10 — M7 Phase 2.2: lower OB body
  threshold and add OB-non-empty test on a synthetic trending CSV.** Owner:
  Claude. Scope: introduce a `body_min_pct` filter on `OrderBlockDetector`
  (default keeps current behaviour; lowered value re-enables OB events the
  research notebook flagged as missing at threshold 1.5).
- **Blockers:** none. Branch `feat/m7-backtest-cli-scaffold` is open and does
  not block the next checkpoint.

### 1. Completed
- Added `bin/backtest_ict.py` — multi-symbol/multi-timeframe ICT backtest
  CLI wrapping `src.backtest.backtester.ICTBacktester`. Pure scaffolding, no
  live-trader or pipeline edits. Reads either a manifest CSV
  (`symbol,timeframe,path`) or repeated `--pair SYMBOL:TF:PATH` flags;
  writes a JSON report. Dataclasses `Pair` / `PairResult`, helpers
  `parse_pair_arg`, `load_manifest`, `run_pair`, `run_all`, `aggregate`,
  `render_results`, `main`.
- Added `tests/test_backtest_ict_cli.py` — 14 offline tests covering pair
  parsing, manifest column validation, aggregate math, missing-file and
  malformed-CSV failure paths, and an end-to-end synthetic flat-market run
  that exercises the real `ICTBacktester` and proves the CLI plumbing
  works.

### 2. Files changed
- `bin/backtest_ict.py` (new, 267 lines)
- `tests/test_backtest_ict_cli.py` (new, 189 lines)
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (this entry)

### 3. Tests run
- `python -m py_compile bin/backtest_ict.py tests/test_backtest_ict_cli.py` — pass.
- `PYTHONPATH=. pytest tests/test_backtest_ict_cli.py -q` — 14 passed in 0.73s.
- `python scripts/repo_inventory.py` — pass (no junk candidates).
- `python scripts/secret_scan.py` — pass (no obvious secrets).
- `PYTHONPATH=. pytest -q --ignore=tests/test_main_loop.py tests` —
  265 passed / 23 failed / 2 skipped. The 23 failures pre-exist on `main`
  (verified by stashing this patch and re-running: same 23 failures, same
  files: `test_runtime_validation.py`, `test_runtime_pipeline.py`,
  `test_runtime_smoke.py`). They are environment / fixture issues unrelated
  to this change. `tests/test_main_loop.py` requires the optional `ccxt`
  dependency which is not installed in this sandbox; not introduced by this
  patch. **No new regressions.**

### 4. Remaining
- Lower OB body-size threshold and verify OB detection produces non-zero
  events on a known-trending fixture (next checkpoint).
- Confluence filters (session gate already exists in backtester; HTF trend
  filter still to add).
- Multi-symbol validation runs themselves (Gemini-in-Colab, not Claude).

### 5. Next checkpoint
**CP-2026-04-28-10** — Add `body_min_pct` parameter to
`OrderBlockDetector.__init__` (default `0.0` to preserve current behaviour)
and thread it through `src/core/signals.py:ICTSignalsAnalyzer`. Add a test
proving non-zero OB events on a synthetic strong-trend fixture. Read in
order: this entry, `docs/claude/checkpoint-workflow.md`,
`src/ict_detection/order_blocks.py`, `tests/test_fvg_ob.py`.

**Telegram sent:** yes (session-complete dispatched via Pipedream Telegram
connector from the agent runtime; no token handled in-repo).

---

## CP-2026-04-28-00 — Workflow scaffolding

- **Session date:** 2026-04-28
- **Sprint:** sprint-plan-2026-04-28 (Live Trading Hardening + Repo Cleanup)
- **Current sprint phase:** Phase 0 — workflow setup (pre-backlog)
- **Last completed checkpoint:** _none, this is the first._
- **Next checkpoint:** **CP-2026-04-28-01 — M1 Auto-deploy timer verification**
  (owner: Colab/Ben; depends on Claude's pending timer PR being merged).
  See `docs/sprint-plans/sprint-plan-2026-04-28.md` § M1.
- **Blockers:** none.

### 1. Completed
- Added repository-level checkpoint workflow (this file, `checkpoint-workflow.md`,
  `HANDOFF_TEMPLATE.md`).
- Updated `CLAUDE.md` and `docs/claude/INDEX.md` to route to the new workflow.
- Added `scripts/notify_session.py` thin wrapper around the existing
  `src.runtime.notify.send_via_alert_manager` for session/sprint Telegram pings.

### 2. Files changed
- `CLAUDE.md`
- `docs/claude/INDEX.md`
- `docs/claude/session-workflow.md`
- `docs/claude/checkpoint-workflow.md` (new)
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (new)
- `docs/claude/checkpoints/HANDOFF_TEMPLATE.md` (new)
- `scripts/notify_session.py` (new)

### 3. Tests run
- `python -m py_compile scripts/notify_session.py` — pass.
- No production code touched, so no pytest run required for this patch.

### 4. Remaining
- None for this checkpoint. Sprint backlog is intentionally **not** started
  in this session per the workflow-implementation task.

### 5. Next checkpoint
**CP-2026-04-28-01** — Begin M1 auto-deploy timer verification work as
defined in `docs/sprint-plans/sprint-plan-2026-04-28.md` § M1.
The next Claude session should:
1. Read this log entry first.
2. Read `docs/claude/checkpoint-workflow.md`.
3. Read sprint plan § M1.
4. Confirm whether the timer PR has merged on `main`. If yes, hand the
   verification steps to Colab/Ben as a copy-ready block. If not, the
   smallest safe subtask is to draft/finish the timer PR.

**Telegram sent:** no (workflow scaffolding session, run from agent-side;
no live Telegram creds intended in this environment).
