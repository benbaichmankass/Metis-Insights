# Local PnL fallback for non-Bybit (IBKR / paper) trades — 2026-06-16

**Ref:** BL-20260616-IBKRPNL · operator report (MGC / MHG paper trades on
`ib_paper` showing `$0.00`).

## Symptom

Operator screenshots: two `ib_paper` (IBKR paper futures) positions —
**MGC LONG #2597** and **MHG LONG #2578** — rendering **`+$0.00`**, plus
related dead rows with no PnL (MGC **#2596** `orphaned`/`stuck_strategy_watchdog`,
MGC #2612/#2613 `rejected`). Reported as "orphaned / flattened trades with no
PnL that should be computable."

Ground truth (live `trade_journal.db` via the diag relay):

| id | sym | dir | status | pnl | exit_price | order_package_id |
|----|-----|-----|--------|-----|-----------|------------------|
| 2597 | MGC | long | open | NULL | NULL | pkg-81602a3b… |
| 2578 | MHG | long | open | NULL | NULL | **NULL** |
| 2596 | MGC | long | orphaned | NULL | NULL | pkg-6dc62bc2… |
| 2612/2613 | MGC | short | rejected | NULL | NULL | … |

So the screenshot rows are actually **open** (the `$0.00` is *unrealised*
PnL), and there are genuinely-closed/orphaned rows with NULL *realised* PnL.

## Root cause

The bot's PnL machinery is **Bybit-only**:

- `src.units.accounts.clients.account_closed_pnl_for_trade` queries Bybit V5
  `/v5/position/closed-pnl` and returns `None` for every non-Bybit account
  ("Currently only `bybit` is wired").
- `order_monitor._sweep_pending_pnl_from_bybit` (the SSOT PnL filler, adopted
  2026-05-18 when the local `_compute_close_pnl` was deleted under *"Bybit is
  the only source; the system doesn't need its own calculator"*) only calls
  that lookup.
- The dashboard `/api/bot/positions` unrealised PnL comes from the broker
  (`account_open_positions`), falling back to `None`; the Streamlit client-side
  candle fallback is **multiplier-blind** so a futures move rounds to ~$0.

Net: **every IBKR / Alpaca / OANDA trade gets no PnL at all** — not unrealised
while open, not realised when it closes/orphans. The data to compute it is
always present: entry/exit/qty/direction on the trade + order package, and the
per-contract multiplier `contract_value_usd` in `config/instruments.yaml`
(MGC=10, MHG=2500, MES=5, crypto perps=1).

Separately, **#2578's `order_package_id` is NULL** — `coordinator.py:910`
stamped the id onto `pkg.meta` only when `pkg.meta` was *already* a dict, so a
package with `meta=None` journaled its trade with a broken trade↔package link.

## Decision (operator, 2026-06-16)

1. Compute PnL locally **when the broker can't** (reverses the 2026-05-18
   directive only for the non-Bybit case; Bybit still uses broker truth first).
2. For an exit with no broker fill (orphaned / watchdog-flattened / disappeared):
   use **mark-to-market** (last market close), stamped as an estimate.
3. **Backfill + fix forward.**

## The structural contract (not a Bybit band-aid)

Realised PnL resolves the **same way for every account**: *prefer broker
truth, fall back to local compute.* Whether an integration *can* provide
broker truth is a **declared integration capability**, not a per-account flag
or a hardcoded "is it Bybit" check:

- **`src.units.accounts.clients.BROKER_PNL_READER_EXCHANGES`** is the single
  source of truth — the set of exchanges with a wired authoritative closed-PnL
  reader in `account_closed_pnl_for_trade` (today: `{"bybit"}`).
  `account_has_broker_pnl_reader(account)` is the resolver every PnL path uses.
- **Default is local** (the universal fallback): an integration with no reader
  automatically gets correct local PnL — so a new broker never strands a trade
  at `$0.00`, and there's no default-off capability gate (Prime Directive).
  Adding broker truth is the explicit opt-in (extend the reader + add the
  exchange to the set). This is now a **wiring step in the `new-broker` skill**
  (step 2b) + an end-to-end PnL verification step, so every future integration
  declares its source.

## Fix

- **`src/runtime/local_pnl.py`** (new) — pure helpers: `compute_realized_pnl`,
  `compute_unrealized_pnl`, `compute_pnl_percent`, `contract_value_usd_for`
  (re-export), and `last_mark_price` (cached last close from the canonical
  candle feed). Exchange-agnostic — no broker-specific logic.
- **`order_monitor._sweep_local_pnl_for_unpriced`** — runs every monitor tick
  after the Bybit sweep. Scans `status IN ('closed','orphaned') AND pnl IS NULL
  AND position_size>0` (14-day window, ≤100/tick) and fills PnL from
  `exit_price` or mark-to-market, writing `pnl`/`pnl_percent`/`exit_price` +
  `notes.{pnl_source,exit_price_source}` and re-linking a NULL
  `order_package_id`. Source selection is **declarative**: integrations with no
  broker reader are filled here; broker-reader integrations are **deferred to
  the Bybit sweep within the broker retention window** (`_BROKER_PNL_RECOVERY_MS`,
  7d) and only filled locally once older — so fee-accurate broker truth is never
  pre-empted, while a genuinely-abandoned broker row is still rescued from a
  permanent `$0.00`. Runs unconditionally — baseline correctness, no gate
  (the initial `LOCAL_PNL_COMPUTE_DISABLED` kill-switch was removed 2026-06-16
  on operator review; rollback for a bug is revert + redeploy).
- **`dashboard.py::_local_unrealised_for_trade`** — same contract for **open**
  positions: broker first (`_broker_unrealised_for_trade`), then a server-side
  mark-to-market fallback (`unrealizedPnlSource="markprice_local"`),
  multiplier-aware.
- **`coordinator.py`** — initialise `pkg.meta = {}` before stamping
  `order_package_id` so every trade row carries the link (closes the #2578 gap).
- **`.claude/skills/new-broker/SKILL.md`** — PnL-source declaration is now a
  required wiring touch point (step 2b) + post-ping verification.

## Backfill

No separate script is needed: once deployed, `_sweep_local_pnl_for_unpriced`
scans the existing 14-day `pnl IS NULL` closed/orphaned rows and fills them on
the next monitor tick (e.g. #2596). `rejected`/zero-size rows correctly stay
NULL (no result). Open rows (#2597/#2578) get unrealised PnL immediately via
the dashboard fallback, and realised PnL + re-link when they later close.

## Follow-up

- The Streamlit `_position_upnl` client-side fallback is multiplier-blind — now
  moot (the API returns the correct value) but worth a dashboard-repo cleanup.
