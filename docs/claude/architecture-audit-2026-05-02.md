# Architecture compliance audit — 2026-05-02

**Trigger:** operator request, post-#308 (BUG-034 VWAP execution fix). Wider audit
to verify compliance with the 6 architectural rules + codify them in
`CLAUDE.md` so future sessions can't drift.

**Method:** 4 parallel `Explore` sub-agents, one rule cluster each, surveyed
the repo without modifying code. Findings synthesised here. No fixes shipped
in this session — every violation gets its own follow-up sprint (most are
Tier 2, touch live routing, and need PM-review).

**Severity:**
- **P0** — affects live-trading correctness *now*. Operator may already be
  losing money or running blind.
- **P1** — architectural debt; not actively breaking, but the next bug
  shaped like BUG-034 will hide here.
- **P2** — cleanup; nice-to-have.

---

## The 6 rules (verbatim from operator, 2026-05-02)

1. **Unit separation.** Always separate unit structure and functionality.
2. **Flow.** Strategy units produce signals and trade packages. Signals are
   logged in the *signals log* in the DB unit. Order packages are logged in
   the *order packages log* in the DB unit. The strategy unit has no trade
   execution functions — it just generates, logs, and monitors. The order
   packages log tracks: order id, originating strategy, entry/sl/tp, signal
   logic + confidence, timestamps for each step/update, status (open|closed).
   While a trade is open, the strategy continues to monitor and update the
   order package.
3. **Account/risk/execute.** Each account is set to follow a certain
   strategy. When the order packages log is updated with that strategy's
   package, the account's risk manager runs the package through risk rules
   to decide whether to place + size, then executes. The account logs (and
   updates) the trade in the *trade log* in the DB unit. While a trade is
   open and the order package gets updates, the account re-runs the package
   through its risk manager to decide whether to close or stay open.
4. **UI mirroring.** The UI functionality unit mirrors the structure: trade
   log commands show trade logs **by account**; order package log commands
   show order package logs **per strategy**.
5. **Telegram = thin shell.** The Telegram bot's menus and commands are
   based on the UI functionality unit. The bot only attaches UI helpers to
   the menu — no trading logic, no DB queries, no aggregation.
6. **Live by default.** Default is for everything to be set to live trading.
   If live trades aren't being placed at the end, the operator must be told.

---

## Findings (severity-ranked)

### P0-1 — Account ↔ strategy filter is loaded but **never enforced**

**Rule:** 3 (Account/risk/execute).

**Symptom:** `config/accounts.yaml` declares `bybit_1.strategies: [turtle_soup]`
and `bybit_2.strategies: [vwap]`, but `Coordinator.multi_account_execute`
fans every package to every account regardless of the assignment. A vwap
signal lands in `bybit_1` (turtle_soup-only wallet) and a turtle_soup signal
lands in `bybit_2` (vwap-only wallet).

**Evidence:**
- `src/units/accounts/__init__.py::load_accounts` line 67 stores the list
  on `TradingAccount.strategies`.
- `src/core/coordinator.py::multi_account_execute` line 527 iterates every
  account and only filters by `account_type` (line 528). Never consults
  `account.strategies`.

**Impact:** Production routing bug. Every signal touches every wallet.
Compounded by the fact that fills eventually land (post-#308) — the
operator won't see this as a fill-rate gap; they'll see it as
*"why does my vwap PnL come from a wallet labelled turtle_soup?"*

**Fix sprint:** **S-029 PR1.** Add a `pkg.strategy in account.strategies`
guard in `multi_account_execute` (skip account when missing, log a
`skipped: not_assigned` result). Add a regression test that pins this
contract per-strategy. Tier 2.

---

### P0-2 — Live trades are **not** written to `trade_journal.db`

**Rule:** 3 (Account/risk/execute) + 4 (DB unit).

**Symptom:** Only smoke-test orders write to `trades` (via
`Coordinator._log_smoke_to_journal`, lines 825–862). `execute_pkg` never
calls `Database.insert_trade`. The hourly report's "Strategies (today)"
counts come from the journal — but the journal is essentially empty for
real trades.

**Evidence:**
- `src/data_layer/database.py::Database.insert_trade` lines 150–179.
- `src/core/coordinator.py::_log_smoke_to_journal` lines 825–862 — only
  caller in production.
- `src/units/accounts/execute.py::execute_pkg` lines 36–147 — no DB write.
- `src/core/coordinator.py::multi_account_execute` lines 412–719 — no DB
  write after `execute_pkg` returns.

**Impact:** Operator's reporting surface is silently incomplete. PnL
attribution, fill-rate diagnostics, and per-strategy performance metrics
all read from the journal. They're all missing real-trade rows.

**Fix sprint:** **S-029 PR2.** Add a `Database.insert_trade` call inside
`execute_pkg` immediately after a successful exchange submission (and a
`Database.update_trade` on close). Status starts `open`; closes via the
new monitor loop (P0-4). Tier 2.

---

### P0-3 — No liveness watchdog ("0 fills despite N signals")

**Rule:** 6 (Live by default + tell-me-if-not).

**Symptom:** Nothing alerts when signals are firing but no orders are
placing. BUG-034 (the VWAP execution gap fixed in #308) hid for an unknown
period because the only "is the trader alive?" surface is the operator
manually reading the hourly report and noticing `vwap: N signals / 0 fills`.

**Evidence:**
- `src/runtime/hourly_report.py` builds the report (lines 559–591) but
  doesn't itself act on a fill-rate threshold.
- `grep "fill_rate\|liveness\|silent.*trader"` in `src/` returns nothing
  actionable.
- The new `src/runtime/execution_diagnostics.py` (#308) covers per-tick
  failures but not multi-tick silence.

**Impact:** The operator is the watchdog. That's the gap the recurring
hardening sessions were meant to close — but they only run bi-daily.

**Fix sprint:** **S-029 PR3.** Add `src/runtime/liveness_watchdog.py` that
runs hourly (piggyback the hourly report job), reads the last 1 h of
`signal_audit.jsonl`, and pings the operator (`enqueue_execution_failure`
or a new `enqueue_liveness_alert`) when:
- ≥ 5 actionable signals fired AND
- 0 trades landed in `trade_journal.db` for the same window.
Includes a "snoozed for N h" override the operator can set via Telegram.
Tier 1 (observability-only, no order-path change).

---

### P1-4 — No open-trade monitor loop (Rules 2 + 3 both require it)

**Rule:** 2 (strategy monitors open packages) + 3 (account re-evaluates
risk on open trades).

**Symptom:** Strategies are stateless one-tick functions
(`order_package(cfg, candles_df) → dict`). Once a trade is placed, nothing
re-enters the strategy or the risk manager to decide whether to update
sl/tp or close. `TradingAccount.positions` is defined but never populated.

**Evidence:**
- `src/units/strategies/vwap.py`, `turtle_soup.py` — no `monitor()` /
  `update_package()` methods.
- `src/units/accounts/account.py:58` — `self.positions: List[Dict] = []`,
  never written to.
- No polling task in `src/runtime/heartbeat.py` or anywhere else iterates
  open trades.

**Impact:** Stops + targets only fire because Bybit holds them server-side
(stopLoss / takeProfit submitted with the order). If the strategy decides
mid-trade *"market shifted, tighten the SL"* — there's no path to do that.

**Fix sprint:** **S-030.** Multi-PR sprint:
- PR1: Build the order-packages log (P1-5 — needed first).
- PR2: Add a `monitor()` hook on each strategy that takes the current
  package + fresh candles and returns an updated package or `None` if
  unchanged.
- PR3: Heartbeat-driven loop reads open packages, calls `monitor()`, and
  on changes calls a new `account.update_open_trade(pkg)` that re-runs
  `risk_manager.approve` and either modifies the exchange order or closes.
Tier 2 across all PRs.

---

### P1-5 — No order-packages log

**Rule:** 2 + 4.

**Symptom:** `OrderPackage` is a dataclass passed by reference. After
`execute_pkg` returns, the package is gone. There is no place that records
"strategy X emitted package Y at time T with confidence C, status open;
later updated to status closed at T+1 with reason 'sl-hit'".

**Evidence:**
- `src/data_layer/database.py` — only `trades`, `backtest_results`,
  `strategy_versions` tables. No `order_packages` table.
- `runtime_logs/signal_audit.jsonl` records the `pipeline_result` event
  but only with `{strategy, symbol, side, qty, status, reason}` —
  not entry/sl/tp, not confidence, not the full lifecycle.

**Impact:** Operator can see "a vwap signal fired" and "a trade exists",
but can't trace the OrderPackage that linked them or replay how the
package evolved while the trade was open. Required for P1-4 (monitor loop)
to have anywhere to write its updates.

**Fix sprint:** **S-030 PR1.** Add `order_packages` table to
`trade_journal.db`. Schema:
```
order_package_id TEXT PRIMARY KEY,
strategy_name TEXT NOT NULL,
symbol TEXT NOT NULL,
direction TEXT NOT NULL,
entry REAL NOT NULL,
sl REAL NOT NULL,
tp REAL NOT NULL,
confidence REAL,
signal_logic TEXT,            -- JSON blob of signal reasoning
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
status TEXT NOT NULL,         -- open | closed | rejected
linked_trade_id INTEGER,      -- FK to trades.id, NULL until placed
close_reason TEXT,
meta TEXT                     -- JSON for extensibility
```
`Database.insert_order_package` + `Database.update_order_package` writers,
called from `multi_account_execute` (insert) and the new monitor loop
(update). Tier 1 (DB schema + writer; behaviour is additive).

---

### P1-6 — Telegram bot has 31+ business-logic handlers

**Rule:** 5 (Telegram = thin shell over UI unit).

**Symptom:** `src/bot/telegram_query_bot.py` directly opens `trade_journal.db`,
reads `signal_audit.jsonl`, parses `CHECKPOINT_LOG.md`, makes raw HTTP calls
to Bybit, etc. Only `/hourly` correctly delegates to `src/ui/processor.py`.

**Evidence (selected — full list in Agent 4 report):**
- `fetch_today_pnl()` / `fetch_open_positions_count()` — direct
  `sqlite3.connect(trade_journal.db)` queries (lines 116–158).
- `_read_audit_tail()` — reads `signal_audit.jsonl` directly (line 1161).
- `_render_signals_block()` — reimplements the filtering already in
  `processor.get_recent_signals()` (lines 1264–1291).
- `/price` — raw HTTP call to Bybit API (line 1058).
- `/closeall` — calls `dl.close_all_bybit_positions_for_strategy` directly,
  which bypasses the canonical `execute_pkg` close path.
- `/sprintlet_status`, `/checkpoint`, `/health`, `/vmstats` — direct file
  reads + parsing.

**Impact:** Every change to the data model breaks the bot in N places.
Two implementations of "filter signals by strategy" already exist (one in
`processor.py`, one inlined in the bot) — they will drift. The
`/closeall` path is a Rule-3 violation that bypasses `execute_pkg`.

**Fix sprint:** **S-031.** Pull each handler's business logic into a UI
helper. Multi-PR (one per command cluster):
- PR1: `processor.get_status()`, `processor.get_balances()`,
  `processor.get_positions_count()` — replace `fetch_today_pnl` etc.
- PR2: `processor.get_signals_block()` — replace `_read_audit_tail` +
  `_render_signals_block` (also delete the duplicate in `processor.py`
  vs `data_loaders.py`).
- PR3: `processor.get_price(symbol)` — replace raw HTTP in `/price`.
- PR4: `processor.close_open_positions(strategy, account)` that internally
  routes through `execute_pkg` (closes the Rule-3 violation).
- PR5: catch-all — sprint/checkpoint/health handlers move to a UI helper.
Each PR is small enough to self-merge as Tier 1 once unit tests pin the
new helper's contract.

---

### P1-7 — `src/bot/data_loaders.py` is in the bot folder but is a UI helper

**Rule:** 1 (unit separation) + 5 (bot is thin).

**Symptom:** `data_loaders.py` lives under `src/bot/` but does no
bot/Telegram work. It loads data from the DB, the YAML, the exchange,
the signal log. Worse, it imports from
`src.units.accounts.clients`, `src.runtime.outcomes`,
`src.runtime.api_reporting`, `src.runtime.pipeline.STRATEGIES` — leaking
unit boundaries through the bot.

**Evidence:**
- File path: `src/bot/data_loaders.py`.
- Imports inventory from Agent 4's report.
- Functions like `_load_yaml_accounts`, `account_balance_with_diagnostic`,
  `recent_trades_for`, `strategy_dashboard_data` all belong to the UI
  unit by content, not the bot unit.

**Impact:** Hard to evolve either side without breaking the other. Future
attempts to "thin the bot" (P1-6) will either pull this file with them or
create a circular dependency.

**Fix sprint:** **S-032.** Rename `src/bot/data_loaders.py` →
`src/ui/data_loaders.py` (or fold contents into `src/ui/processor.py`)
and rewrite the bot's imports. Catch the boundary leaks as part of the
move (don't re-export `src.units.accounts.clients` — instead route
through `processor.py`'s public API). Tier 1.

---

### P1-8 — Pipeline signal builders fetch OHLCV directly

**Rule:** 2 (strategies pure; no exchange calls).

**Symptom:** `turtle_soup_signal_builder` (`pipeline.py:227-228`) and
`vwap_signal_builder` (`pipeline.py:328-329`) call `_build_killzone_exchange()`
which instantiates a `BybitConnector` / `BinanceConnector` and fetches
OHLCV inline. The strategy modules themselves (`src/units/strategies/*`)
are clean — but the runtime adapter layer that wraps them isn't.

**Evidence:** Agent 1 report § 2.

**Impact:** Couples signal generation to exchange reachability. Test
isolation requires the operator to mock both layers. Mild — strategies
themselves are still pure, so the boundary is preserved at the unit-folder
level.

**Fix sprint:** **S-033.** Pull OHLCV fetching out of the signal builders
into a dedicated `src/runtime/market_data.py` (or a method on the
Coordinator) that the pipeline calls before invoking the builder.
Builders accept candles only. Tier 1.

---

### P2-9 — Signals split across two stores

**Rule:** 4 (DB unit owns signals log).

**Symptom:** `runtime_logs/signal_audit.jsonl` (file) and
`data/trades.db::signals` (separate SQLite, NOT the main `trade_journal.db`)
both exist. The UI reads the JSONL; the SQL table is a relic of an
abandoned approach.

**Evidence:** Agent 3 report § 2.

**Fix sprint:** **S-034.** Decision needed — keep the JSONL (and delete
the unused SQL table) or fold the JSONL into a new `signals` table inside
`trade_journal.db` (alongside the new `order_packages` table from
S-030 PR1). Recommend the latter for Rule-4 alignment. Tier 1.

---

### P2-10 — DB unit lives at `src/data_layer/`, not `src/units/db/`

**Rule:** 1 (unit folder layout).

**Symptom:** Per Rule 1, every unit should live under `src/units/`.
`src/data_layer/database.py` is the DB unit by content but isn't filed
under `src/units/`. Same observation for `src/ui/` (UI unit) and
`src/runtime/` (orchestration / "pipeline" unit).

**Impact:** Cosmetic. Doesn't break anything; just makes "what is a unit?"
ambiguous to future sessions.

**Fix sprint:** **S-035** (last). After S-029 → S-034 land, do a single
move-and-rename PR: `src/data_layer/` → `src/units/db/`,
`src/ui/` → `src/units/ui/`, `src/runtime/` → `src/units/runtime/` (or
keep runtime separate and document why in `repo-map.md`). Tier 1, but
huge diff — schedule for a low-traffic window.

---

## Summary table

| Severity | ID | Title | Rule | Fix sprint | Tier |
|---|---|---|---|---|---|
| **P0** | 1 | Account ↔ strategy filter unenforced | 3 | S-029 PR1 | 2 |
| **P0** | 2 | Live trades not logged to `trade_journal.db` | 3 + 4 | S-029 PR2 | 2 |
| **P0** | 3 | No liveness watchdog | 6 | S-029 PR3 | 1 |
| **P1** | 4 | No open-trade monitor loop | 2 + 3 | S-030 (multi-PR) | 2 |
| **P1** | 5 | No order-packages log | 2 + 4 | S-030 PR1 | 1 |
| **P1** | 6 | Bot has 31+ business-logic handlers | 5 | S-031 (multi-PR) | 1 |
| **P1** | 7 | `data_loaders.py` is in the wrong unit | 1 + 5 | S-032 | 1 |
| **P1** | 8 | Pipeline builders fetch OHLCV directly | 2 | S-033 | 1 |
| **P2** | 9 | Signals split across two stores | 4 | S-034 | 1 |
| **P2** | 10 | DB / UI / runtime not under `src/units/` | 1 | S-035 | 1 |

---

## Recommended sprint sequence

1. **S-029 (this session's follow-on).** P0-1, P0-2, P0-3 — three small PRs.
   The first two stop active misrouting + reporting blindness; the third
   means future BUG-034-shaped failures self-report within an hour.
2. **S-030.** P1-4 + P1-5 — order-packages log + monitor loop. Order-packages
   log is the prerequisite for monitor.
3. **S-031.** P1-6 — thin the bot. Multi-PR; each one Tier 1.
4. **S-032.** P1-7 — move `data_loaders.py` to the UI unit.
5. **S-033.** P1-8 — pull OHLCV out of signal builders.
6. **S-034.** P2-9 — consolidate signals into the DB unit.
7. **S-035.** P2-10 — final folder reshuffle.

After S-029 the system is *operationally correct*. After S-031 the unit
boundaries are *enforced*. Everything else is polish.

---

## What landed in this session

- Findings doc (this file).
- `CLAUDE.md` § *Architecture rules* (the 6 rules + enforcement pointers).
- `docs/claude/sprint-planning.md` — added a *Unit boundary declaration*
  section that every sprint prompt must fill in.

No code changes. Every fix sprint above is its own PR cycle.
