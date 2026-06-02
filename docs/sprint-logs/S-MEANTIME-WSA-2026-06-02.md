# S-MEANTIME-WSA — WS-A futures diversification + IBKR paper deployment

## Date Range
- **Start:** 2026-06-02
- **End:** 2026-06-02

## Objective
- **Primary:** Act on the Meantime Expansion Program (`docs/sprint-plans/MEANTIME-EXPANSION-PROGRAM-2026-06-02.md`) WS-A — find BTC-uncorrelated diversifiers and start paper-trading the validated ones, while the live book accumulates data.
- **Secondary:** Tighten the real-money roster to backtest winners; fix the multi-symbol over-breadth surfaced during the paper deploy; reconcile docs.

## Tier
- **Mixed 1/2/3.** Tier-1 (research scripts + docs), Tier-2 (`pull-and-deploy`), Tier-3 (roster change, new strategies + routing, `SUPPORTED_SYMBOLS`, intent-emission gate). All Tier-3 merges were explicitly operator-approved in-session before merge+deploy.

## Starting Context
- Active program: Meantime Expansion Program (created same session). Maps onto roadmap M7/M8 (strategy) + M9/M10 (AI traders).
- Prior live state: `bybit_2` real money ran the full six-strategy roster incl. net-negative members (turtle_soup, fade, squeeze, vwap-shadow); `ib_paper` ran MES (`mes_trend_long_1d`) + crypto strategies mirrored on MES.
- Known risk: concentration (real PnL on ~1 net-positive strategy on 1 symbol); fade/squeeze passed backtest then bled live (−86R).

## Repo State Checked
- Branch `claude/strategy-ml-review-2CX8n`; main advanced across the session (a6ae60c → 95e610e → 2ac0440 → 3d35e24).
- Live VM deploy state verified each step via the `system-actions` `pull-and-deploy` comment-back + `vm-diag-snapshot` relays.
- Canonical docs reviewed: CLAUDE.md, `docs/ARCHITECTURE-CANONICAL.md`, the `new-strategy` / `vm-ops` / `backtesting` / `doc-freshness` / `sprint-format` skills.

## Files and Systems Inspected
- **Code:** `src/runtime/intent_multiplexer.py`, `src/runtime/intents.py` (`SUPPORTED_SYMBOLS`, `DEFAULT_PRIORITIES`), `src/runtime/strategy_signal_builders.py` (`mes_trend_long_1d` builder as template), `src/runtime/market_data.py` (`connector_for_symbol`/`fetch_candles`), `src/core/coordinator.py` (`_eligible_for_dispatch`), `src/main.py` (`_resolve_tick_symbols`/`_symbols_for_account`), `src/units/accounts/ib_client.py` (`_build_contract`, `_round_to_tick`), `src/units/strategies/htf_pullback_trend_2h.py` (reused unit), `scripts/backtest_pullback.py`.
- **Config:** `config/{accounts,strategies,instruments}.yaml`, `config/strategy_descriptions.json`.
- **Deploy/services:** `scripts/ops/pull_and_deploy.sh`/`deploy_pull_restart.sh`; verified `ict-trader-live.service` active post each deploy.
- **Relays:** `system-actions.yml`, `vm-diag-snapshot.yml`, `trainer-vm-diag.yml`.

## Work Completed
1. **Winners-only real-money roster** (PR #2630, merged 95e610e, deployed). `bybit_2` → `[trend_donchian, ict_scalp_5m, fvg_range_15m, htf_pullback_trend_2h]`; graduated fvg_range + htf_pullback (0.3% risk) from demo; dropped turtle_soup/vwap/fade/squeeze from real money (kept on `bybit_1` demo).
2. **WS-A research arc** (PR #2634, Tier-1; trainer-VM via `trainer-vm-diag` relay; `scripts/research/ws_a_*`, `docs/research/ws-a-s*`):
   - **S1** 18×4 generalization matrix (daily, net-of-fee): trend generalizes; fade detonates on equity indices (SPX −113R) — same failure mode as its live BTC loss; metals/energy lead.
   - **S2** overfitting-aware re-tune: Gold/pullback, Gold/trend, Copper/pullback clear both-window filter; consistent tighter-stop/longer-filter neighborhood.
   - **S3** block-bootstrap (B=10k, 27y) significance: **Copper/pullback (p05 exp +0.25) + Gold/pullback (p05 exp +0.08) PASS**; Gold/trend fails (p05 −0.03).
   - **S3b** fee headroom: both survive >30bps round-trip → commission de-risked.
3. **Metals paper sleeve** (PR #2634, Tier-3, deployed): `mgc_pullback_1d` (MGC) + `mhg_pullback_1d` (MHG) on `ib_paper`, `execution: live`, reusing `htf_pullback_trend_2h.order_package` with the exact S2/S3 params; `SUPPORTED_SYMBOLS += {MGC, MHG}`; `instruments.yaml` profiles + `ib_client._build_contract` COMEX `ContFuture` branches; symbol-aware tick rounding.
4. **Per-strategy symbol-scope gate** (PR #2643, merged 3d35e24, deployed): `intent_multiplexer._collect_intents` skips a strategy on any tick symbol not in its `config/strategies.yaml::symbols:` (permissive on miss). Gated at emission (not dispatch) to avoid the aggregator-starvation interaction. Crypto strategies no longer trade MES; each strategy scoped to its instrument.
5. **doc-freshness reconciliation** (commit 3ab0447): CLAUDE.md candles routing, `new-strategy` skill (multi-symbol + scope), ARCHITECTURE change-log row, `BL-20260602-001` resolved.

## Validation Performed
- **Tests (local):** `tests/test_mgc_mhg_pullback_1d.py` (18), `tests/test_intent_symbol_scope.py` (4), + 324 intent/coordinator/pipeline regression all green; ruff clean. CI green on all merged PRs (#2630, #2634, #2643), all 11 required checks each.
- **Deploy verified:** each `pull-and-deploy` comment-back confirmed HEAD advanced + `ict-trader-live.service` active + `boot_reconcile: 0 ghost_trades / 0 untracked / 0 errors`. Final HEAD `3d35e24`.
- **COMEX entitlement (live):** `audit_query?event=mgc_pullback_1d_eval` + `mhg_pullback_1d_eval` show real evals on MGC/MHG with computed ADX regime — candles fetching from IBKR COMEX, no contract/data errors.
- **Symbol gate (live):** post-deploy `audit_query?event=mgc_pullback_1d_eval&since=12:28:30Z` returns 3 rows, **all symbol=MGC** (pre-deploy the same window had MGC+MES+MHG+BTCUSDT). Gate confirmed working in production.
- **Code review of agent-authored wiring:** audited `ib_client._round_to_tick` (MES byte-identical via `max(ndigits,4)` floor — no regression), `_build_contract` (BTC guard preserved, COMEX map), the metals builders (faithful MES clone, no long-only gate, correct `fetch_candles` routing).

### Gaps not yet verified
- **No metals trade has fired yet** — all evals `side=none` (expected; daily pullback systems trade ~5–7×/yr). Real fills/PnL on MGC/MHG remain to be observed over the coming weeks.
- **Symbol gate covers the production intent-multiplexer path only** (`STRATEGY=multiplexed_intents`). The legacy first-wins pipeline path is not gated (not the production path) — noted in the backlog resolution.
- WS-A edges validated on yfinance daily **continuous-contract (`=F`)** data; roll-adjusted re-validation deferred (the demo-execute forward run on native IBKR contracts now serves that purpose).

## Documentation Updated
- CLAUDE.md (candles routing), `new-strategy` skill (multi-symbol + per-strategy scope), `docs/ARCHITECTURE-CANONICAL.md` (2026-06-02 change-log row), `docs/claude/health-review-backlog.json` (BL-20260602-001 resolved), `docs/research/ws-a-s{1,2,3,3b}-*` + `tradeable-universe-*` (new), `docs/sprint-plans/MEANTIME-EXPANSION-PROGRAM-2026-06-02.md` (new), `config/strategy_descriptions.json` (mgc/mhg).

## Contradictions or Drift Found
- **Created + fixed this session:** the symbol-scope gate retired the 2026-05-22 "crypto strategies mirror on MES" behaviour; reconciled in the `new-strategy` skill + ARCHITECTURE change-log + CLAUDE.md. `SUPPORTED_SYMBOLS={BTCUSDT}` claim in the skill was already stale (MES added 2026-05-22) — corrected.
- **Pre-existing, now resolved:** `BL-20260602-001` (per-strategy `symbols:` did not gate evaluation) — fixed by PR #2643.

## Risks and Follow-Ups
- **Forward-data wait:** Gold/Copper paper trades are infrequent; meaningful live-vs-backtest comparison is weeks out.
- **NinjaTrader integration** (operator: separate session) — the eventual live-futures venue; the two validated strategies graduate there once built.
- **Roll-adjusted re-validation** of the two passers if/when native-contract history is pulled (lower priority — the demo forward run is the real test).

## Deferred Items
- WS-B (backtest-augmented training + sim realism), WS-C (fade diagnosis + drift monitor), WS-D (decider), WS-A Bybit-alts sweep + intraday (ict_scalp/fvg) futures sweep — all per the program plan.

## Next Recommended Sprint
- **S-MEANTIME-WSB** — sim-realism (slippage/funding/partial-fill/latency) + backtest-augmented training (program WS-B/S2), OR the IBKR-intraday futures sweep to cover `ict_scalp`/`fvg_range`. Verification: trainer-VM sweep reproducibility + live-holdout eval.
- Meanwhile: monitor the metals paper sleeve for first fills (a `/performance-review` once trades accrue).

## Wrap-Up Check
- [x] Code inspected directly (file:line; agent diff audited line-by-line).
- [x] Canonical docs reviewed + updated (doc-freshness run).
- [x] TRADE-PIPELINE: intent-emission stage changed → reflected in ARCHITECTURE change-log + new-strategy skill (no separate TRADE-PIPELINE doc stage contract affected beyond the symbol-scope note).
- [x] ROADMAP checked — ledger row to be added at close.
- [x] Contradictions recorded (above).
- [x] Unknowns stated (Gaps not yet verified).
- [x] Production-active: all changes deployed + verified at HEAD `3d35e24`.
