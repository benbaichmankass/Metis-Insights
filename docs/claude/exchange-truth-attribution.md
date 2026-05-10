# Exchange-truth attribution

S-067 follow-up #6. Insulates performance reads from local
schema/state bugs in `trade_journal.db` by mirroring Bybit's fill
log into a separate read-only sqlite store and exposing aggregates
via `/api/bot/pnl/exchange`.

## Why this exists

`trade_journal.db::trades` is the bot's view of what *should* have
happened. The exchange's fill log is what *did* happen. The two can
disagree:

* PR #627 — `/api/bot/positions` returned `[]` for the entire
  lifetime of the endpoint because of a column-rename `OperationalError`.
  The dashboard rendered "no open positions" while live exposure
  was real.
* The 2026-05-10 review surfaced a row with `status='closed'` in the
  DB while the position was still open on the exchange.

The S-067 sprint hardened `trade_journal.db` reads against silent
empties (loud failure paths now). This follow-up adds a redundant
truth source so a future regression in *either* the writer (runtime)
or the reader (web API) doesn't compromise the operator's
performance view: when local + exchange disagree, exchange wins.

## Architecture

```
Bybit V5 API
    │  GET /v5/execution/list
    ▼
scripts/pull_exchange_fills.py  ─► runtime_state/exchange_fills.sqlite
    (CLI; opt-in cron)               (separate from trade_journal.db)
                                          │
                                          ▼
                        src/runtime/exchange_fills_store.py
                                          │
                                          ▼
                  GET /api/bot/pnl/exchange?days=N
                  (Tier-1 read, no session)
```

Three modules:

* **`src/runtime/exchange_fills_store.py`** — sqlite schema +
  idempotent upsert + read-aggregate helpers. Primary key is
  Bybit's `exec_id`, so re-running the puller on overlapping windows
  is safe.
* **`src/runtime/exchange_fills_puller.py`** — pure logic; takes a
  `fetch_my_trades` callable and returns rows ready for `upsert_fills`.
  Mocked in tests; the real callable is ccxt's `exchange.fetch_my_trades`.
* **`scripts/pull_exchange_fills.py`** — CLI entry-point that wires
  the above together with the live ccxt Bybit connector.

The store lives at `runtime_state/exchange_fills.sqlite` (gitignored
directory, alongside `prop_state.json`). Override via
`EXCHANGE_FILLS_DB` env var.

## Operator: enabling the daily puller

The puller does not run automatically — operator opt-in. Two paths:

### systemd timer (recommended)

```bash
# Drop a unit + timer under deploy/ and let scripts/install_systemd_units.sh
# pick them up on the next deploy. Skeleton:
#
# deploy/ict-pull-exchange-fills.service:
#   [Unit]
#   Description=Pull recent Bybit fills into local store
#   After=network-online.target
#   [Service]
#   Type=oneshot
#   WorkingDirectory=/opt/ict-trading-bot
#   EnvironmentFile=/etc/ict-trading-bot/exchange.env
#   ExecStart=/usr/bin/python3 scripts/pull_exchange_fills.py --days 2
#
# deploy/ict-pull-exchange-fills.timer:
#   [Timer]
#   OnCalendar=daily
#   RandomizedDelaySec=900
#   Persistent=true
#   [Install]
#   WantedBy=timers.target
```

Add `ict-pull-exchange-fills.service` to `DEPLOY_RESTART_SKIP` (see
`docs/claude/deployment-ops.md` § Services restarted) so the deploy
script doesn't restart the oneshot.

### Ad-hoc run

```bash
BYBIT_API_KEY=... BYBIT_API_SECRET=... \
  python3 scripts/pull_exchange_fills.py --days 7 --account live
```

## Phase-1 vs phase-2

**Phase-1 (this PR):**

* Schema + idempotent upsert.
* Bybit fills puller (read-only).
* `/api/bot/pnl/exchange?days=N` returns fee + flow aggregates.

The phase-1 endpoint is enough to:

* Detect missing fills — symbols where `trade_journal.db` has
  executed orders but the fills store is empty.
* Reconcile fee expectations against
  `trade_journal.db::trades.pnl` aggregates.
* Compare gross flow volume between the two sources as a sanity
  check before promoting the local DB's P&L number.

**Phase-2 (deferred):** true P&L attribution requires lot-matching
(FIFO buy/sell pairing) over the fill stream, which is non-trivial
for partial fills, hedged positions, and cross-symbol netting on
unified accounts. Phase-2 will add `realized_pnl` / `unrealized_pnl`
fields to the wire shape — additive, so existing readers won't break.

**Phase-3 (further deferred):** Telegram-alerted reconciliation
report comparing every closed trade in `trade_journal.db` against
exchange truth, with mismatch escalation to
`runtime_logs/recon_mismatches.jsonl`. Requires the lot-matching
from phase-2 first.

## Trust contract

* The exchange wins on disagreement. `trade_journal.db` is the
  bot's intent log; the fills store is what actually happened.
* The puller never writes to `trade_journal.db`. The two stores
  share no connection or transaction.
* The puller is read-only on the exchange side. It uses the same
  API key/secret env vars as the live trader (the existing key has
  read-execution permissions; no new permissions needed).
