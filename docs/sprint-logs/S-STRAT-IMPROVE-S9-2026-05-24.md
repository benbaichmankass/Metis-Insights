# S-STRAT-IMPROVE-S9 — sprint log (2026-05-24)

Strategy-improvement program: trend timeframe migration, two new
complementary members (shadow), the single-account decider correction,
models-in-the-loop test, and the MES/cross-asset data + research.

## Shipped (merged to main)
- **Trend timeframe migration 1h → 2h** (donchian 20 / trail 3.5),
  walk-forward validated, deployed + verified live on bybit_2 (real
  money). PR #1875. Live config: +52.5R/6yr backtest, net-positive every
  year incl. 2025 chop.
- **fade_breakout_4h** — new strategy wired `execution: shadow`, routed to
  bybit_1 (demo), deployed + verified evaluating live. PRs #1884 (wiring)
  + #1885 (routing). The failed-breakout fade; uncorrelated complement
  (monthly_corr 0.035 vs trend).

## Built, awaiting operator merge (draft PRs)
- **squeeze_breakout_4h** — the best member-#3 candidate (volatility-
  squeeze breakout; corr 0.30 vs trend, robust plateau). Wired
  `execution: shadow`. PRs **#1907** (wiring) + **#1908** (routing →
  bybit_1). CI green-tracking; mirror of the fade flow.

## Research (on the program branch; tooling + findings)
- **Member-#3 hunt — complete.** Tested: slow-trend (correlated cousin),
  6h-fade (knife-edge), funding-sentiment (no edge — thesis falsified),
  ES/MES via gappy yfinance (didn't transfer), ML entry-filter
  (anti-predictive OOS — trend edge is exit-driven). **Winner: squeeze.**
  Harnesses: `backtest_fade.py`, `backtest_squeeze.py`,
  `backtest_funding.py` + `fetch_funding_history.py`,
  `research_trend_mlfilter.py`.
- **Decider — corrected to single-account** (operator direction). The
  multi-account-blend design (PR #1902) was wrong and is **closed**.
  Correct design: `docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md`.
  `research_decider.py` simulates single-account selection: one fund is
  viable (ret/DD 3.75) but naive selection lets trend hog the book —
  decider-v2 must select smartly (recover toward the 5.81 blend ceiling).
- **MES / cross-asset — data sourced + validated.** Clean 1m S&P 500 via
  Dukascopy (`fetch_dukascopy_index.py`), cached
  `data/SPX500_1m.parquet` (2020-2026, 2.15M rows, on the trainer so we
  never re-fetch; history available back to ~2013). SPX-trend net-positive
  (+29.6R) and **near-uncorrelated with BTC (corr 0.009)** → strong
  portfolio diversification once IBKR is live.

## Current live state
- **bybit_2 (real money):** trend_donchian (2h) only — live.
- **bybit_1 (demo):** turtle_soup, vwap, ict_scalp_5m, fade_breakout_4h
  (shadow), + squeeze_breakout_4h pending #1907/#1908.
- **ib_paper / MES:** offline pending IBKR new-user approval.

## Go-live checklist (what remains)
1. **Merge** squeeze PRs #1907 (wiring) + #1908 (routing) → `pull-and-deploy`
   → squeeze begins shadow data collection. *(operator merge)*
2. **Let fade + squeeze shadow data mature**, confirm live signals match
   backtest (days–weeks).
3. **Mirror bybit_2 to the full roster** with execution gates keeping
   turtle/vwap/ict_scalp in `shadow` (they have no edge) — so the decider
   sees all candidates but only proven members trade real money. *(Tier-3
   PR, operator-approved.)*
4. **Promote fade, then squeeze** `shadow → live` as their data confirms.
5. **Decider-v2** selection logic (regime/model) once ≥2 members live.
6. **MES live** on IBKR once the new user is approved (1–2 days):
   deploy SPX/MES-trend (re-tuned) to ib_paper/ib_live. Data + edge +
   diversification already validated; only execution waits.

## Reconciliation status (skills / training / docs — to verify before go-live)
**Update: all four items below were APPLIED in PR #1915 (this close-out).**
The notes are preserved as the record of what was flagged; the fixes
shipped in the same PR.
- **new-strategy skill** (`.claude/skills/new-strategy`): broadly current
  (used it for fade + squeeze) but predates the `execution: shadow` gate
  and the single-account decider framing — its activation steps say
  `enabled: false` default, whereas we now ship `enabled: true` +
  `execution: shadow`. **Needs a refresh** to document the shadow path.
- **health-review skill** (`.claude/skills/health-review`): pulls live
  runtime via diag relays; should still function, but the strategy roster
  grew to 6 — **verify it surfaces the new strategies/accounts** in its
  grade.
- **Training architecture / automated training**
  (`scripts/ops/run_training_cycle.sh`, `run_mes_training.sh`, `ml/configs/*`):
  the new strategies (trend/fade/squeeze) now produce trades that feed the
  `trade_outcomes` datasets, but there are **no ML manifests scoped to the
  new strategies yet**, and the cross-strategy selection model (decider-v2)
  is a new training target to add. **Needs review** to ensure the cycle
  ingests the new strategies + (later) trains the decider model.
- **Canonical docs** (`docs/ARCHITECTURE-CANONICAL.md`, `README`,
  `CLAUDE.md`): the `execution: shadow` gate is already documented
  (Prime Directive S9). **Still to add:** the 6-strategy roster, the
  single-account decider concept, the MES/cross-asset data source +
  `data/SPX500_1m.parquet` cache, and the new research harnesses. The
  Dashboard `/api/bot/strategies` will reflect the roster automatically.

## Open follow-ups (next session)
- Decider-v2: implement + simulate regime-rule and selection-model
  selection in `research_decider.py`; pick the winner (note: the order
  tie-break test in #1914 was degenerate — the order labels didn't match
  the emit `strategy` field; re-run with matching names).
- ~~Apply the reconciliation items above (skill refresh, training review,
  canonical-doc updates).~~ **Done in PR #1915.**
- Walk-forward the SPX configs before any MES live deployment.
