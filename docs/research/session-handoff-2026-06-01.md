# Session handoff — 2026-06-01 (shadow trio → MES native validation → demotions → execution bugfixes → regime study)

> **For the next session.** This captures exactly where things stand so you can
> pick up without re-deriving. Read this, then `docs/CLAUDE-RULES-CANONICAL.md`
> + the latest roadmap entry as usual.

## 1. What shipped this session (all merged + deployed to the live VM unless noted)

| PR | What | Tier | State |
|---|---|---|---|
| #2520 | wire `htf_pullback_trend_2h` execution:shadow (bybit_1 demo) | 3 | merged+deployed |
| #2521 | wire `trend_donchian_1h` execution:shadow A/B (bybit_1) | 3 | merged (later RETIRED, see #2554) |
| #2522 | wire `mes_trend_long_1d` execution:shadow LONG-ONLY (ib_paper) | 3 | merged+deployed |
| #2514 | overnight research report + `scripts/research/` harnesses + the `pull-mes-ibkr-history-daily` system-action | 1 | merged |
| #2527 | fix `ibkr_offvm` adapter for DAILY bars (`_to_dt` date→datetime + per-request timeout resilience) | 1 | merged+deployed |
| #2532 | valid `DATASET_VERSION=v003` + `MES_MAX_CONTRACTS=28` for the daily pull | 1 | merged |
| #2540 | DEMOTE `squeeze_breakout_4h` + `fade_breakout_4h` live→shadow | 3 | merged+deployed |
| #2548 | **bar-close debounce** (one entry attempt per closed bar) — fixes the re-entry storm | 2 | merged+deployed |
| #2554 | RE-TUNE live `trend_donchian` 2h/trail3.5 → **1h/trail5.0** + retire `trend_donchian_1h` | 3 | merged+deployed |
| (this doc's branch `claude/regime-matrix`) | regime-study tooling (`regime_matrix.py`, gated `backtest_trend.py`) + this handoff | 1 | **PR open — merge it** |

## 2. Current LIVE roster state (live VM HEAD = #2554 merge commit)

- **Live (sends real orders, bybit_2 real + bybit_1 demo):** `turtle_soup`, `trend_donchian` (**now 1h / donchian 20 / trail 5.0 / min_confidence 0.30, two-sided**), `ict_scalp_5m`.
- **Shadow (logs only, no live order):** `vwap`, `fvg_range_15m`, `htf_pullback_trend_2h`, `mes_trend_long_1d` (ib_paper), plus the **demoted** `fade_breakout_4h` + `squeeze_breakout_4h`. `trend_donchian_1h` is **retired** (`enabled: false`).
- **Bar-close debounce is live** (`STRATEGY_BAR_DEBOUNCE_DISABLED` kill-switch in `strategy_monocle.py`).

## 3. Native-MES validation (done, positive)
677 native MES daily bars (2023-09→2026-05) pulled off the live IB gateway. `mes_trend_long_1d` (long-only Donchian-30/trail4.0) on real MES: **+13.81 R full / +2.93 R OOS, short side −4.1 R (gate justified), tiny DD**. Confirms the long-only edge; shadow accrues more live data before any shadow→live.

## 4. Holistic execution-health audit (done — the demotions/research are NOT corrupted)
- `reconciler_filled` is the normal linear-perp exchange-exit journaling; **fade + ict_scalp win through it** (exits work).
- The Chandelier trail **does** ratchet the live stop.
- **Orphans are 100% phantom** (no broker trade, no PnL lost) — created by the re-entry storm; the bar-close debounce (#2548) addresses the root cause. → `BL-20260601-001` (health-review backlog).
- squeeze/trend 0%-live-win was **regime (ranging month) + the storm**, not a dead edge or broken plumbing.

## 5. ⚠️ Regime study — THE BIG OPEN THREAD (operator chose "broaden the regime study first")

### 5a. CRITICAL reconciliation finding
The overnight research's **BTC `trend_donchian` +43.8 R OOS was over-optimistic and does NOT reproduce on fresh data**:
- My independent engine first showed **−53 R** (it omitted the live unit's `min_confidence=0.30` gate that drops the shallow breaks "where the strategy bleeds").
- Gated, it's **+10.9 R full / −6.1 R OOS** on fresh Bybit `BTC/USDT:USDT` 1h (2021-2026) — marginal + regime-dependent (2023 +20, 2024 +22, **2025 −30**, 2026 +6).
- Residual gap vs +43 R = **data** (research used a now-deleted `/tmp/btc5m.csv` 5m source; I used perp 1h direct).
- **LESSON:** always run new-strategy research through an engine that mirrors the LIVE unit's filters AND on the live data source before trusting it.

### 5b. Flagship regime × direction matrix (trend_donchian, gated, live config)
| Entry regime | net R | long R (n) | short R (n) |
|---|---|---|---|
| trending | −5.7 | **+22.3** (178) | **−28.0** (178) |
| transitional | −2.3 | +21.7 (140) | −24.1 (155) |
| chop | +18.9 | +3.3 (237) | **+15.6** (216) |
| **Total** | +10.9 | **+47.3** | **−36.5** |

**Structural finding (empirically validates the complementarity-across-regimes thesis):** the LONG side is the trend edge (wins in trending/transitional); the SHORT side is effectively a *different* strategy that **only works in chop** (+16 R chop, −52 R trending/transitional — BTC's uptrend punishes trend-shorts).

### 5c. Decisions PENDING the broadened matrix (do NOT act until the roster matrix is in)
1. **`trend_donchian`:** long-only (simple, +47 R, drops the −37 R short drag) **vs** keep short but **gate it to detected chop** (the literal regime-router rule). Operator wants to decide this *together with* the rest of the roster.
2. Re-promotion criteria for the demoted breakout strategies (squeeze/fade) once their regime profile is known.

## 6. EXACT next steps for the next session
1. **Merge the `claude/regime-matrix` PR** (regime tooling onto main).
2. **Build the roster regime×direction matrix** for `fade_breakout_4h`, `squeeze_breakout_4h`, `fvg_range_15m`, `vwap` (and `htf_pullback_trend_2h`, `mes_trend_long_1d`):
   - Each harness (`scripts/backtest_fade.py` / `backtest_squeeze.py` / `backtest_fvg_range.py` / `src/backtest/run_backtest_vwap.py`) exposes `run_backtest(df, *, ...)` + a `--emit-trades PATH` that writes per-trade JSONL `{strategy, entry_time, direction, gross_r, net_r, confidence}`.
   - **Drive each with its EXACT live params from `config/strategies.yaml`** (the `min_confidence` lesson — wrong params = misleading matrix), emit trades, then tag by ADX regime with the same logic as `scripts/research/regime_matrix.py::tag_trades_by_regime` (it's engine-agnostic; consider a small `regime_tag_emitted.py` that reads any harness's JSONL + the OHLCV + resample → regime×direction net-R).
   - **Data:** `data/btc_1h_multiyear.csv` on the trainer (47,459 1h bars, 2021-2026). If gone, re-fetch with the committed ccxt loop (Bybit `BTC/USDT:USDT` 1h since 2021 — the script is in relay issue #2557's body, or rewrite the ~30-line loop).
3. With the full matrix, decide the long-vs-short / regime configs for the whole roster together, and design the **regime router** (step 2 of the initiative): a regime-aware weighting/gate in the intent layer that leans the roster toward strategies that fit the detected regime (the existing per-strategy ADX gates + the `regime-classifier-baseline-v0` shadow model are the building blocks).
4. **Verify the debounce worked:** after a data window, re-measure the orphan / `intent_noop` / re-entry rate vs the pre-#2548 baseline (`PERF-20260601-001` / `BL-20260601-001`).

## 7. How to operate (reminders that mattered this session)
- **VM access is relay-only** from a web session (Trusted network): trainer = `trainer-vm-diag-request` labelled issue with a `cmd:` block (base64 any python to avoid heredoc/indent breakage; one relay at a time — concurrency cancels in-progress); live VM reads = `vm-diag-request`; live VM mutations/deploys = `system-action` (`pull-and-deploy`, etc.).
- The trainer's venv python is `.venv/bin/python` (has pandas/pyarrow/ccxt); bare `python3` lacks them.
- New env vars in `src/runtime|units|web` matching `*_ENABLED`/`*_DISABLED`/etc. trip the `env-gate-guard` — add an inline `# allow-silent: <reason>` if it's genuinely not a live/dry gate.
- Tier-3 (strategies.yaml/accounts.yaml/risk) merges need explicit operator approval; deploys (`pull-and-deploy`) restart `ict-trader-live` (positions persist, watchdog boot-grace covers it).

## 8. Relay issue trail (data lives here)
Pull/deploy/diag relays: #2523–#2559. Regime data specifically: #2557 (BTC fetch + first matrix), #2558 (gated reconciliation), #2559 (flagship regime×direction). Orphan/exec audit: #2542–#2549.
