# Sprint Log: S-M20-ICTSCALP-ETH15M-STALE-SHIP-2026-07-29

## Date Range
- Start: 2026-07-29
- End: 2026-07-29

## Objective
- Primary goal: Ship the `ict_scalp_eth_15m` stale-stop (stale12) exit lever — the sole
  M20-gate-clearer of the 8-leg `ict_scalp` family — from `passed_unshipped` to live
  enforcement. Tier-3 but **paper-only** (routes solely to `bybit_1`, Bybit demo).
- Secondary goals: harness-exact live parity; leave the real-money `ict_scalp_5m` BTC leg
  byte-for-byte unchanged; deploy + verify live; hand the P5/P7 first-fire verification to a
  durable backlog.

## Tier
- Tier 3 (strategy exit-logic + config), executed under explicit operator approval
  ("merge"). Zero real-money exposure — the only leg that declares the lever is
  `ict_scalp_eth_15m` on `bybit_1` (`account_class: paper`).
- Justification: modifies `ict_scalp.monitor()` (a live-money-shared module) + declares an
  exit param in `config/strategies.yaml`. Default-OFF design keeps every non-declaring leg
  (incl. real-money `ict_scalp_5m`) unaffected.

## Starting Context
- Active roadmap items: M20 Exit Refinement (near-done); `MB-20260728-ICTSCALP-EXIT-LEVERS`
  filed the ready-to-execute spec (ml-review-backlog).
- Prior sprint reference: [`S-M27-ICTSCALP-EXIT-REFINEMENT-2026-07-28`](S-M27-ICTSCALP-EXIT-REFINEMENT-2026-07-28.md)
  (built the harness levers; swept 7/8 legs honest_negative; eth_15m `passed_unshipped`, operator HELD).
- Known risks at start: the spec flagged that `meta.entry_time` was NOT confirmed present on
  ict_scalp packages (only donchian stamps it) — if absent the lever would ship a silent no-op.

## Repo State Checked
- Branch or commit reviewed: `main` (pre-ship); shipped via PR #7863 (squash `91ff9a9`).
- Deployment state reviewed: live trader on `git_sha 91ff9a96` post-deploy (verified).
- Canonical docs reviewed: CLAUDE.md, exit-refinement skill, ML-review-backlog spec.

## Files and Systems Inspected
- Code files inspected: `src/units/strategies/ict_scalp.py`, `trend_donchian.py`
  (`_stale_stop_verdict`, `_since_entry`), `src/units/strategies/_base.py`
  (`monitor_breakeven_sl`), `src/runtime/order_monitor.py` (cfg threading + `run_monitor_tick`),
  `scripts/backtest_ict_scalp.py` (harness lever semantics), `strategy_signal_builders.py`
  (eth_15m variant builder), `src/runtime/market_data.py` (candle `timestamp` column).
- Config files inspected: `config/strategies.yaml` (`ict_scalp_eth_15m`), `config/accounts.yaml`
  (eth_15m routes to `bybit_1` `mode:live` `account_class:paper`).

## Work Completed
- **`ict_scalp.py` monitor**: added `_stale_stop_verdict` (+ `_coerce_int/_coerce_float`,
  `_bars_since_entry`) run BEFORE the break-even ratchet — `monitor_breakeven_sl` has no close
  path, so this realizes "stop-first, before the break-even modify". Default-OFF: undeclared
  legs return `None`. Matches the harness exactly (`scripts/backtest_ict_scalp.py:170`): fire when
  `bars_since_entry >= stale_exit_bars` AND `open_r < stale_exit_below_r`. The harness enters at
  the signal bar's close with `start_idx = signal_bar + 1`; the live twin reproduces that via a
  strictly-after-`entry_time` bar count minus one. Stop-first guard defers to a current-bar SL/TP
  cross. Fail-safe (missing entry_time / risk / ambiguous age → `None`; never raises).
- **`order_package()`**: stamped `meta.entry_time` (signal bar's timestamp) — additive + inert,
  mirrors `trend_donchian`; propagates through the variant builder's `**pkg_meta`. `risk_per_unit`
  was already present.
- **`config/strategies.yaml`**: declared `stale_exit_bars: 12` + `stale_exit_below_r: 0.0` on the
  `ict_scalp_eth_15m` block only. Rollback = delete the two lines.
- **Tests**: monitor↔harness bar-exact parity test + default-off / winner-hold / stop-first-defers
  in `tests/test_ict_scalp_exit_levers.py`.
- **Coverage matrix**: `docs/research/exit-refinement-coverage.json` `ict_scalp_eth_15m` stale_stop
  `passed_unshipped` → `shipped`.

## Validation Performed
- `tests/test_ict_scalp_exit_levers.py` — 13 pass (incl. the new parity test asserting the live
  monitor fires on the exact bar `_simulate_exit` does and not before).
- Full `tests/*ict_scalp*` — 72 pass; real-money 5m leg verified unaffected (declares nothing → `None`).
- YAML parse + only `ict_scalp_eth_15m` carries the new keys (5m/mgc/sol/xrp confirmed `None`).
- CI: all 9 PR guard workflows green on `dcb50ed`.
- **Live deploy verified**: `/api/diag/version` + `/api/diag/status` both `git_sha 91ff9a96`;
  `bot_uptime_s 967` (trader restarted post-merge); heartbeat running; `ict_scalp_eth_15m` in the
  loaded strategy list; `bybit_1` paper live.
- **Cross-check**: `trend_donchian_xrp_4h` fired a REAL `stale_stop` live (04:40 UTC, meta
  `stale_exit_bars:8` + `entry_time` set) — the same lever family, confirming the mechanism.

## Documentation Updated
- ROADMAP.md — newest-first changelog bullet (2026-07-29) recording the shipment + deploy.
- `docs/research/exit-refinement-coverage.json` — stale_stop → `shipped` (in PR #7863).
- `docs/claude/ml-review-backlog.json` — `MB-20260728-ICTSCALP-EXIT-LEVERS` update (shipped+deployed).
- `docs/claude/health-review-backlog.json` — new `BL-20260729-ICTSCALP-ETH15M-STALE-FIRSTFIRE`
  (carries the P5/P7 first-fire verification).
- This sprint log.

## Contradictions or Drift Found
- ROADMAP listed `ict_scalp_eth_15m stale12` as a queued/HELD Tier-3 item (2026-07-28 entries).
  Resolved by adding the 2026-07-29 changelog bullet recording the shipment (the dated 07-28
  entries are left as historical record).

## Risks and Follow-Ups
- **P5/P7 first-fire mechanics UNVERIFIED** — the paper leg had taken zero post-deploy trades
  (~6h in, chop, `ict_scalp_eth_15m_signal` count 0). Verification is carried by
  `BL-20260729-ICTSCALP-ETH15M-STALE-FIRSTFIRE`; the in-session hourly watch is retired with this
  session.
- Marginal evidence (stale12: IS +8.09R / OOS +2.92R, walk-forward 3/4 usable folds, benefit
  mostly a ~3R lower maxDD) — paper-only by design so the online soak is the arbiter.

## Deferred Items
- Real-money `ict_scalp_5m` BTC stale lever: honest_negative (no change).
- `exit_ladder` (no harness lever) and `exit_head_ml` (pending) for the ict_scalp family — out of scope.

## Next Recommended Sprint
- A `/health-review` (or `/system-review`) drains `BL-20260729-ICTSCALP-ETH15M-STALE-FIRSTFIRE`
  once `ict_scalp_eth_15m` takes a trade that ages into the stale window.

## Wrap-Up Check
- [x] Work committed on the designated branch, PR opened.
- [x] Canonical docs reconciled (doc-freshness run); ROADMAP + sprint log + backlogs updated.
- [x] Coverage matrix reflects reality.
- [x] Monitoring handed to a durable backlog item (no reliance on the ephemeral session watch).
