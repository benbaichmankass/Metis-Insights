# Sprint Log: S-PROXY-EQUITIES-ALPACA-LIVE-2026-07-07

## Date Range
- Start: 2026-07-07
- End: 2026-07-07 (real-venue revalidation deferred to the next US RTH — see
  *Gaps not yet verified* / *Risks and Follow-Ups*)

## Objective
- Primary goal (operator directive): the real-money `alpaca_live` account
  (~$150) can't afford ≥1 whole share of the expensive equity ETFs it was
  configured to trade (SPY ~$620 / QQQ ~$560 / GLD ~$310 / IWM ~$230 …), and
  Alpaca bracket orders reject fractional shares (`risk.WHOLE_UNIT_QTY_EXCHANGES
  = {alpaca}`) — so those cells never fire. Find **sub-$100 proxy ETFs** that
  track the same underlyings, backtest them, and wire the strong ones so the
  account can actually trade.
- Secondary (operator-approved mid-session): **promote** the two standouts
  (SPLG + IAUM) onto real-money `alpaca_live` and **normalize its risk caps**
  off the 2026-06-30 10%/10%/10% "test-account" escalation.

## Tier
- Tier 3 (the two shipped PRs both touch `config/strategies.yaml` /
  `config/accounts.yaml` / `config/instruments.yaml` + the signal-builder /
  intent registries; #5920 additionally changes `alpaca_live` **risk caps** —
  the most gated change class). Operator approval obtained in chat for: the
  proxy roster, the SPLG+IAUM promotion, and the exact `2% / 5% / 5%` risk
  numbers ("2/5"), then explicit "merge".
- Research/backtest/compat artifacts within are Tier-1.

## Starting Context
- Active roadmap item: M15 (Alpaca ETF sleeve) / M7-M8 (strategy expansion).
- Prior sprints: S-M15-ALPACA-LIVE-2026-06-25/27 (the real-money ETF go-live),
  S-EXPANSION-ETF-BREADTH-2026-06-20 (the unleveraged ETF book),
  S-LEVERAGED-ETF-2026-06-30 (the TQQQ/QLD paper cells — same wiring pattern,
  and the sprint that recorded `alpaca_live`'s 10% risk blows the compat gate).
- Known facts at start: `alpaca_live` was live real-money at `risk_pct: 0.10`,
  `max_dd_pct: 0.10`, `daily_loss_pct: 0.10` — a deliberate escalation to try to
  force one whole share of the pricey ETFs, which the S-LEVERAGED-ETF compat run
  had already shown blows the survival gate (survival 0.69 vs the 0.90 floor).

## Repo State Checked
- Branch/commit: `claude/alpaca-proxy-equities-g4sc4q` cut from `origin/main`.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`
  (tiers, the two execution gates, Prime Directive), ROADMAP.md; skills
  `backtesting`, `new-strategy`, `sprint-format`, `doc-freshness`.
- Deployment state: no live-VM action taken by hand; the cells + risk caps
  deploy on merge to `main` via `ict-git-sync` auto-pull + restart.

## Files and Systems Inspected
- Wiring touch points (mirrored from the live `spy_trend_long_1d` /
  `gld_pullback_1d` cells): `src/runtime/strategy_signal_builders.py`
  (3 cloned `_signal_builder` fns + 3 `.monitor_unit` tuple entries —
  `splg`/`scha` → `trend_donchian`, `iaum` → `htf_pullback_trend_2h`),
  `src/runtime/intent_multiplexer.py` (3 imports + 3 name→builder dict entries),
  `config/strategies.yaml`, `config/accounts.yaml`, `config/instruments.yaml`
  (SPLG/IAUM/SCHA profiles, each with the `ib: {sec_type: STK, exchange: SMART,
  primary_exchange: ARCA, currency: USD}` block main added mid-session),
  `config/strategy_descriptions.json`, `scripts/ops/etf_account_compat.sh`.
- Sizing / affordability path: `src/units/accounts/risk.py`
  (`RiskManager.position_size`, `WHOLE_UNIT_QTY_EXCHANGES`, `requires_whole_unit_qty`).
- Compat gate: `scripts/prop/account_compat_matrix.py` (Monte-Carlo
  survival / P(breach) / EV via `src/prop/montecarlo.py`).
- Roster-pin tests: `tests/test_strategy_registry.py`,
  `tests/test_s007_pipeline_rewire.py`, `tests/test_s007_validate_script.py`,
  `tests/test_alpaca_wiring.py`.

## Work Completed
- **Proxy identification + backtests (Tier-1, #5891).** Confirmed the viable
  sub-$100 proxies: **SPLG ≡ SPY** (same S&P 500 index, ~$72), **IAUM ≡ GLD**
  (same spot gold, ~$63); **SCHA ≈ IWM** (broad small-cap, ~$52 — a *different*
  index than the Russell 2000, so a looser proxy). **No sub-$100 QQQ proxy**
  exists (Nasdaq-100 ETFs are all pricey). Backtested each proxy's own price
  series through the same daily cell params as its expensive twin.
- **Paper wiring (Tier-3, #5895 — MERGED).** Added `splg_trend_long_1d`,
  `iaum_pullback_1d`, `scha_trend_long_1d` to `alpaca_paper` (`execution: live`
  → paper money): 3 signal builders + `monitor_unit` tags, intent-multiplexer
  registration, `strategies.yaml` cells (SPLG/SCHA reuse `trend_donchian` params;
  IAUM reuses `htf_pullback_trend_2h`), `instruments.yaml` profiles, descriptions,
  `etf_account_compat.sh` rows, and the roster-pin test updates (registry count
  45→48 + the four roster sets). CI caught a real wiring gap first
  (`test_strategy_monitor_unit_resolution` — the cells had no resolvable
  `monitor()` until the `.monitor_unit` tuple entries were added); fixed +
  verified 59 tests pass locally.
- **Risk-cap sweep (Tier-1, #5911 / research #5916).** Swept `alpaca_live`
  `risk_pct` under the restored 5% caps to find the highest per-trade risk where
  BOTH proxies clear the compat survival gate AND are affordable at ~$150:

  | risk% (caps 5%) | SPLG survival / P(breach) | IAUM survival / P(breach) | Both ROUTE? |
  |---|---|---|---|
  | **2.0%** | **1.00 / 0.093** | **1.00 / 0.035** | ✅ **← chosen** |
  | 3.0% | 0.94 / 0.236 | 0.89 / 0.173 | ✗ |
  | 10% (prior hack) | 0.69 / 0.47 | 0.69 / 0.40 | ✗✗ |

  Affordability at ~$150 (live ATR stops): SPLG needs `risk_pct ≥ 1.93%`, IAUM
  `≥ 1.03%`. At **2%** both clear — IAUM comfortably (~2 shares), SPLG at the
  edge (~1 share).
- **Real-money promotion (Tier-3, #5920 — MERGED squash `b8ff87a`, merged by
  operator 2026-07-07 21:33 UTC).** `config/accounts.yaml::alpaca_live` only:
  `strategies +=` `splg_trend_long_1d` + `iaum_pullback_1d` (**NOT** `scha` —
  marginal edge, stays paper); `symbols +=` `SPLG`, `IAUM`; `risk`: `risk_pct
  0.10 → 0.02`, `max_dd_pct 0.10 → 0.05`, `daily_loss_pct 0.10 → 0.05`
  (+ `daily_usd: 200`). ROLLBACK path documented inline (restore 0.10/0.10/0.10).

## Validation Performed
- Local test suites green before each merge: `test_alpaca_wiring` +
  `test_alpaca_live_host_routing` + `test_slv_gdx_pullback_wiring` +
  `test_strategy_registry` + `test_s007_*` (67 pass on the #5920 branch; 59 on
  #5895). `config/accounts.yaml` parses; post-merge `alpaca_live` risk verified
  `{max_dd 0.05, daily_loss 0.05, risk_pct 0.02}` and `splg+iaum routed: True`,
  `symbols tail: ['SPLG','IAUM']` via a `python3` config-load check on the
  rebased head.
- Both PRs passed full CI and merged to `main`.
- **Gaps not yet verified (honest):** **real-venue revalidation is NOT done.**
  It could not be — the US equity session had already closed (merge landed
  21:33 UTC; RTH is 13:30–20:00 UTC). Unconfirmed until the next session:
  that IAUM (and SPLG when affordable) actually sizes ≥1 whole share and places
  a bracket order on `alpaca_live` — **or** cleanly refuses at 0 shares (the
  `RiskManager.position_size` fail-safe: if `risk_budget < stop_distance` it
  logs and refuses, never oversizes, so a too-small account simply doesn't
  trade). A self-check-in is armed for 2026-07-08 15:00 UTC to pull
  `/api/diag/journal?table=trades` + `/api/diag/exchange_positions?account_id=alpaca_live`
  + the account-down latch and confirm.

## Documentation Updated
- This sprint log (new).
- ROADMAP.md — Historical Sprint Ledger row + Last-Updated header (this session).

## Contradictions or Drift Found
- None introduced. The proxy cells are described consistently with the existing
  ETF-cell docs; the `alpaca_live` risk-cap change is the sanctioned
  account-level `risk_pct` basis (the `strategy-risk-guard` CI check only forbids
  `risk_pct` under `strategies.yaml`, which this does not touch). No
  execution-gate / tier / VM-topology / removed-gate drift.

## Risks and Follow-Ups
- **Real-venue revalidation (the one open box, tracked by the 2026-07-08
  check-in).** Confirm live sizing/bracket-placement OR a clean fail-safe
  refusal on `alpaca_live`; watch `/api/bot/positions` + the account-down alert.
- **SPLG edge-of-affordability at ~$150.** At 2% SPLG is ~1 share at the edge —
  a small down-move in equity or an ATR widening pushes it to a 0-share refusal
  (safe, but it won't trade). A modest funding bump (~$250) gives both proxies
  comfortable headroom without changing `risk_pct`. Operator's call.
- **SCHA (paper-only).** Marginal edge + a looser index match than the others;
  stays on `alpaca_paper` for soak, not promoted.
- **No QQQ proxy.** Nasdaq-100 has no sub-$100 ETF; the QQQ cell stays
  unaffordable on `alpaca_live` until the account is funded higher.

## Deferred Items
- Intraday (1h) proxy cells — not built; daily cells only this session.
- Real-money promotion of SCHA — deferred pending a stronger paper track record.

## Next Recommended Sprint
- Close the real-venue revalidation (the check-in drives it), then compare the
  first live proxy fills against the backtest; if SPLG proves too edge-of-
  affordable, propose the small funding bump (no `risk_pct` change) or leave it
  as a fail-safe no-op.

## Wrap-Up Check
- Code inspected directly (signal builders / intent registry / risk sizing /
  compat script + the four roster-pin test files) — yes.
- Canonical docs reviewed + this log + ROADMAP updated — yes.
- No TRADE-PIPELINE stage changed (config routing + risk caps only) — n/a.
- Roadmap checked + ledger row added — yes.
- Contradictions recorded — none.
- Unknowns stated plainly — real-venue revalidation is UNVERIFIED (next US RTH);
  everything claimed "merged" is confirmed via the PR merge state, not inferred.
- doc-freshness coherence check run this session (see PR).
