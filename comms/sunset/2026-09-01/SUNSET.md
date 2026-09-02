# Sunset pass — 2026-09-01

Generated `2026-09-01T23:13:46.788226+00:00` by `scripts/ops/sunset_pass.py`.

**E3 proposes. It never enacts.** Retiring a strategy leg is Tier-3; nothing here writes config or deletes a file.

## Population

- packet dates read: **1** (2026-09-01)
- strategy legs graded: **52**
- lifetime read: **`read`** (45 strategies in the capture)
- account routing: **`read`**
- machinery probe: **`measured`** (115 findings consumed)

## Strategy verdicts — {'watch': 39, 'not_assessed': 3, 'retire_candidate': 10}

## Machinery verdicts — {'unwired': 2}

- `doc_only` — **104** (names carried in `INDEX.json`; the denominator, not an action list)
- `skill_invoked` — **9** (names carried in `INDEX.json`; the denominator, not an action list)

## Retirement candidates — 10

- **`gdx_pullback_1d`** (Tier-3, basis `never_closed_lifetime`) — has never closed a single trade in its life, and the gate files it identically to a healthy young leg every day.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper', 'alpaca_portfolio', 'alpaca_options_paper']`
- **`gld_pullback_1d`** (Tier-3, basis `never_closed_lifetime`) — has never closed a single trade in its life, and the gate files it identically to a healthy young leg every day.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper', 'alpaca_portfolio']`
- **`iaum_pullback_1d`** (Tier-3, basis `never_closed_lifetime`) — has never closed a single trade in its life, and the gate files it identically to a healthy young leg every day.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper']`
- **`mes_trend_long_1d`** (Tier-3, basis `never_closed_lifetime`) — has never closed a single trade in its life, and the gate files it identically to a healthy young leg every day.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['ib_paper']`
- **`scha_trend_long_1d`** (Tier-3, basis `never_closed_lifetime`) — has never closed a single trade in its life, and the gate files it identically to a healthy young leg every day.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper']`
- **`splg_trend_long_1d`** (Tier-3, basis `never_closed_lifetime`) — has never closed a single trade in its life, and the gate files it identically to a healthy young leg every day.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper']`
- **`spy_trend_long_1d`** (Tier-3, basis `never_closed_lifetime`) — has never closed a single trade in its life, and the gate files it identically to a healthy young leg every day.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['ib_paper', 'alpaca_paper', 'alpaca_portfolio']`
- **`tqqq_trend_long_1d`** (Tier-3, basis `never_closed_lifetime`) — has never closed a single trade in its life, and the gate files it identically to a healthy young leg every day.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['alpaca_paper']`
- **`trend_donchian_sol`** (Tier-3, basis `never_closed_lifetime`) — has never closed a single trade in its life, and the gate files it identically to a healthy young leg every day.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `['bybit_1']`
- **`turtle_soup`** (Tier-3, basis `unrouted`) — declared in strategies.yaml and routed to NO account — it cannot reach the order path at all, so it can never become gradeable.
  - lifetime closes `0` (state `read`) · latest-window closes `0` · best ever seen `0` against gate floor `None` · routed to `NOTHING`
