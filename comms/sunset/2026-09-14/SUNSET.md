# Sunset pass — 2026-09-14

Generated `2026-09-14T10:33:58.764288+00:00` by `scripts/ops/sunset_pass.py`.

**E3 proposes. It never enacts.** Retiring a strategy leg is Tier-3; nothing here writes config or deletes a file.

## Population

- packet dates read: **13** (2026-09-01, 2026-09-02, 2026-09-03, 2026-09-04, 2026-09-05, 2026-09-06, 2026-09-07, 2026-09-08, 2026-09-09, 2026-09-10, 2026-09-11, 2026-09-13, 2026-09-14)
- strategy legs graded: **52**
- lifetime read: **`read`** (46 strategies in the capture)
- legs absent from that capture (**`not_observed`, NOT zero**): **11** — the capture lists only pnl-bearing closes, so these legs have no lifetime measurement and are never proposed on it
- account routing: **`read`**
- machinery probe: **`measured`** (93 findings consumed)

## Strategy verdicts — {'governed_elsewhere': 21, 'retire_candidate': 21, 'watch': 7, 'not_assessed': 3}

## Machinery verdicts — {'unwired': 2}

- `doc_only` — **85** (names carried in `INDEX.json`; the denominator, not an action list)
- `skill_invoked` — **6** (names carried in `INDEX.json`; the denominator, not an action list)

## Retirement candidates — 21

- **`avax_pullback_2h`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `8` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['bybit_1']`
- **`fade_breakout_4h`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `13` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['bybit_1']`
- **`fvg_range_15m`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `1` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['bybit_1', 'bybit_2', 'bybit_portfolio']`
- **`gdx_pullback_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `None` (capture `read`, this leg `not_observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper', 'alpaca_portfolio', 'alpaca_options_paper']`
- **`gld_pullback_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `None` (capture `read`, this leg `not_observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper', 'alpaca_portfolio']`
- **`htf_pullback_trend_2h`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `29` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['bybit_1']`
- **`iaum_pullback_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `None` (capture `read`, this leg `not_observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper', 'alpaca_live']`
- **`ief_pullback_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `2` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper', 'alpaca_portfolio', 'alpaca_live']`
- **`iwm_trend_long_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `3` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['ib_paper', 'alpaca_paper', 'alpaca_portfolio']`
- **`mes_trend_long_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `None` (capture `read`, this leg `not_observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['ib_paper']`
- **`mhg_pullback_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `5` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['ib_paper']`
- **`qld_trend_long_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `1` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper']`
- **`qqq_trend_long_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `2` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['ib_paper', 'alpaca_paper', 'alpaca_portfolio']`
- **`scha_trend_long_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `None` (capture `read`, this leg `not_observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper']`
- **`slv_pullback_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `2` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper', 'alpaca_portfolio', 'alpaca_live', 'alpaca_options_paper']`
- **`slv_trend_1h`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `23` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper', 'alpaca_portfolio', 'alpaca_options_paper']`
- **`splg_trend_long_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `None` (capture `read`, this leg `not_observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper']`
- **`spy_trend_long_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `None` (capture `read`, this leg `not_observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['ib_paper', 'alpaca_paper', 'alpaca_portfolio']`
- **`tlt_pullback_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `4` (capture `read`, this leg `observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['ib_paper', 'alpaca_paper', 'alpaca_portfolio', 'alpaca_live']`
- **`tqqq_trend_long_1d`** (Tier-3, basis `persistently_silent`) — zero closed trades across 13 consecutive packet dates; the gate has never had anything to grade.
  - lifetime closes `None` (capture `read`, this leg `not_observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper']`
- **`turtle_soup`** (Tier-3, basis `unrouted`) — declared in strategies.yaml and routed to NO account — it cannot reach the order path at all, so it can never become gradeable.
  - lifetime closes `None` (capture `read`, this leg `not_observed`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `NOTHING`
