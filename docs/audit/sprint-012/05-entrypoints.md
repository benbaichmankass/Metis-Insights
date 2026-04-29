# § 5 — Entrypoint reconciliation

Every script, unit, or `__main__` that claims to start the bot, with
canonical-vs-stale verdict.

## 5.1 Canonical entrypoint

**`ict-trader-live.service`** → `python3 -u -B -m src.main`

- `src/main.py` reads `.env`, validates env (`DRY_RUN`,
  `ALLOW_LIVE_TRADING`, `MODE`) at lines 127-140, builds settings, and
  calls `run_pipeline(...)` at line 162.
- `src/runtime/pipeline.py` is the actual loop.

This is the only path the live VM uses to trade.

## 5.2 Top-level shell scripts

| Script | Command it issues | Verdict |
|---|---|---|
| `run_trader.sh` | `python3 -u -B -m src.core.automated_trading_loop` | **STALE.** Wrong entrypoint module — calls an orphan loop instead of `src.main`. Edit to call `python -m src.main` or delete (PR C6). |
| `run_telegram_bot.sh` | `python3 -u -B -m src.bot.telegram_query_bot` | Redundant with `ict-telegram-bot.service`. Keep as a manual debug launcher, document, or delete (PR C6 — recommend keep, document use case). |
| `check_bots.sh` | `ps`/`tmux`/`grep` for `automated_trading_loop.py` | **STALE.** Greps for the wrong process name. Edit to grep for `src.main` and `src.bot.telegram_query_bot`, or delete (PR C6). |
| `bot_logger.sh` | (logging utility) | Out of strict scope; will be assessed and either kept or deleted in C6. Default keep — operator tool. |

## 5.3 Orphan Python entrypoint

**`src/core/automated_trading_loop.py`** (≈112 LOC).

- Last modified by commit `4fe893f DEPLOY CANDIDATE: Turtle Soup
  Iteration #5 — Replaced src/core/automated_trading_loop.py`.
- Contains pure-pandas helpers (e.g. ATR, a turtle_soup-shaped signal
  function) that are **never imported** by `src/main.py`,
  `src/runtime/pipeline.py`, or `src/core/coordinator.py`.
- Only consumer is `run_trader.sh`.

**Action (PR C6):** delete the file together with `run_trader.sh`'s
reference. If any usable logic exists in here that is not already in
`strategies/turtle_soup_mtf_v1.py` it must be folded into the new
`src/units/strategies/turtle_soup.py` in PR C1; otherwise let it go.

## 5.4 Other systemd units (`deploy/`)

These are out of trader-loop scope but listed for completeness. None of
them are stale:

| Unit | ExecStart | Purpose |
|---|---|---|
| `ict-env-check.service` | `scripts/startup_env_check.py` | preflight env validation |
| `ict-git-sync.service` (+ `.timer`) | `scripts/deploy_pull_restart.sh` | periodic `git pull` + service restart |
| `ict-heartbeat.service` (+ `.timer`) | `scripts/daily_heartbeat.py` | daily Telegram status |
| `ict-telegram-bot.service` | `python3 -u -B -m src.bot.telegram_query_bot` | Telegram UI |

## 5.5 Definition-of-canonical-entrypoint paragraph

After S-012, the canonical entrypoint section in
`docs/claude/deployment-ops.md` (added in PR C6) should say:

> The live trader is launched by systemd unit `ict-trader-live.service`,
> which runs `python3 -u -B -m src.main`. There are no other live trader
> entrypoints. Manual launches go through the same module:
> `PYTHONPATH=. python3 -m src.main`. The Telegram bot is launched by
> `ict-telegram-bot.service` running `python3 -u -B -m
> src.bot.telegram_query_bot`. Anything else (`run_trader.sh`,
> `automated_trading_loop.py`) was removed in S-012 (PR C6).
