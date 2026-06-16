# Dashboard Truth & Persistence Audit — 2026-06-16

> **Status:** governing document for the `streamlab-dashboard-audit` effort.
> Operator-approved 2026-06-16 ("Full DB-first big fix"). This is the contract
> every subsequent PR in this effort is written and reviewed against.

## Operator directive (the principle that governs everything here)

> The database is the **single source of truth**. The interfaces (Streamlit
> dashboard, Android app, the REST API itself) should **present** it to the
> fullest extent possible and **avoid doing calculations in the UI**. If
> something isn't logged correctly in the database and we patch a consumer to
> present a value the DB never stored correctly, that is the **deeper bug** and
> it is in scope. The UI is wrong because the **system** is wrong — find and fix
> the system-level (DB write-path / schema) defects, not the read-side symptom.

Two consumers, **one** spec. The Streamlit dashboard and the Android app must
show identical, correct, live data. Any divergence that is not an unavoidable
wiring difference is a defect.

## Why patches never stuck (the root pattern)

Every prior "fix" for these symptoms landed at the **read/display layer** — a
filter default, a null-guard, a fallback chain, a client-side recompute. None
of them changed what the bot **persists** at the moment a thing happens. So
every consumer keeps re-deriving the same missing facts, and the two UIs
re-derive them *differently*. That is the "no matter what we do, it's not
fixed" loop. The fix is to make the database carry the truth canonically, then
delete the derivations.

---

## The systemic defects (system-side, evidence-backed)

Evidence is from the code on disk (`file:line`) and a live journal snapshot
pulled via the diag relay on 2026-06-16T19:06Z.

### S1 — The trade↔order-package link is double-stored and the two halves disagree

There are **two** link columns:

- `order_packages.linked_trade_id` (single slot) — written **only for the
  real-money primary OPEN entry**
  (`src/units/accounts/execute.py:942-958`: `is_primary_entry = status=='open'
  AND not intent_reduce AND not is_paper_account`). Demo mirrors,
  `intent_reduce` legs, multi-account fanout legs, and orphan-adopted trades
  **never** set it.
- `trades.order_package_id` (many-to-one back-ref) — written for **every** leg
  (`execute.py:889,921`), added precisely because the single slot lost fanout
  legs (`src/units/db/database.py:95-120`).

The read endpoints `/api/bot/order-packages`
(`src/web/api/routers/order_packages.py:165`) and `/api/bot/trades/closed`
(`src/web/api/routers/trades_closed.py:181`) JOIN on the **single-slot**
`linked_trade_id` side. So whenever the live leg no-ops but a demo/secondary leg
fills, the package shows unlinked.

**Live proof:** every recent `order_packages` row carries `linked_trade_id:
null` and is stamped `"BUG-049 — no linked_trade_id after 5 min; package was
never executed"`, while the corresponding `trades` rows reference those packages
via `order_package_id`. The package says it never traded; the trade says it has
a package.

**Symptom produced:** "open trade with strategy 'trend' but not associated with
any order package" (the orphan-adopt recovered branch,
`src/runtime/order_monitor.py:2610-2634`, inserts the trade with a recovered
`strategy_name` but omits `order_package_id` from the insert dict); and the
Order Packages tab showing packages with no PnL.

### S2 — There is no canonical close-time column

`trades` has **no `closed_at` column** (`src/units/db/database.py:178-208`).
`closedAt` is derived at read time three different ways
(`trades_closed.py:111-112`): `order_packages.updated_at` → parse
`trades.notes` JSON `closed_at` → fall back to the **open** time. And
`order_packages.updated_at` is a generic "row touched" timestamp
(`database.py` bumps it on any update), not a close marker. When S1 breaks the
link, the close time silently collapses to the open time.

### S3 — "24h PnL" is keyed on OPEN time and means "today, UTC"

`/api/bot/stats` `pnl24h` (`src/web/api/routers/dashboard.py:276-290`) sums
rows whose `substr(COALESCE(created_at, timestamp),1,10)` equals the current UTC
date — i.e. it is **"today (UTC)" keyed on the entry timestamp**, not a rolling
24-hour window keyed on close time. A trade opened yesterday and closed today
lands in yesterday's bucket; the figure resets at 00:00 UTC. With real-money
closes currently sparse, real-money `pnl24h ≈ 0` → the KPI blanks. Same
open-time bucketing flaw in `/api/pnl/history`
(`src/web/api/routers/pnl_history.py:61-88`).

### S4 — Realized `pnl` is frequently left NULL at close

The reconciler fallback close branch (`order_monitor.py:3973-4002`) writes
`status='closed'` + `exit_reason='reconciler_filled'` but **no `pnl`**, taken
whenever the broker closed-pnl lookup returns None (Bybit demo, non-Bybit
brokers, reduce-leg mismatches). The disappear/orphan closes
(`order_monitor.py:2040-2051,2126-2140,4047-4051`) also leave `pnl` NULL by
construction. A later sweep `_sweep_local_pnl_for_unpriced`
(`order_monitor.py:4642+`) tries to backfill — a recovery patch over a
write-path that doesn't persist PnL at close. NULL `pnl` sums as 0 (understating
totals) and forces `/performance` to exclude those rows (undercounting trade
counts).

**Live proof:** most non-open journal rows carry `pnl: null`.

### S5 — `trades` conflates decisions/rejections/reduces/adoptions with real positions

A `trades` row no longer means "a position that existed." Status values seen:
`open`, `closed`, `orphaned`, `rejected`, `exchange_rejected`. The newest rows
in the live snapshot are `status:'rejected'`, `position_size:0`
(netting-guard suppression, `intent_noop:at_target`). Each endpoint filters
differently — `/trades/closed` uses `status='closed'`; `/stats` and
`/pnl/history` use `status!='open'` — so the same journal yields different
"closed" sets and different win-rate denominators depending on which surface
you ask.

### S6 — Consumers compute instead of display, and compute differently

`pnl24h`, win-rate, expectancy, equity curves are recomputed per-request in SQL
(`dashboard.py:276-302`, `performance.py:167-216`) **and** re-merged inside each
UI: Android `PerformanceScreen.mergedWithPaper()` /
`PerformanceScreen.kt:441-489`; dashboard `_summary_window` + client equity
merge; the Overview chart sums paper+real into one "Live PnL" number
(`streamlit_app.py:1563`). `account_class` falls back to `is_demo` everywhere
because the backfill (`scripts/ops/backfill_account_class.py`) was never run.
Two UIs → two slightly different numbers, none authoritative.

### Cross-cutting reality: most live activity is PAPER

`ib_paper` futures (MES/MGC/MHG) and `bybit_1` demo are paper; only `bybit_2`
is real money and it is trading thinly. The headline KPIs are real-money-only
and many surfaces default to real-money — so the dashboards look empty while
the bot is busy on paper.

### Corrected assumption (honesty)

The backlog item `BL-20260614-001` ("`/api/bot/order-packages` +
`/api/bot/trades/closed` appear to 500 consumer-facing") is a **false alarm**.
Both routers catch all exceptions and return `[]` — they never 500. The relay
"failure" was an HTTP **422**: the request used `limit=500`, which exceeds the
hard `le=200` cap (`trades_closed.py:210`, `order_packages.py:186`). Consumers
request within-cap and get HTTP 200. The 200-row cap is, however, genuinely too
low for reviewers and the Android Performance window — tracked under the cap fix
below.

---

## The contract

### A. Persistence contract — what the database MUST store canonically

The `trades` table is the canonical record of **a position's lifecycle**. At the
moment each fact becomes true, it is **written to a column** (not only to a JSON
`notes` blob, not only inferable via a JOIN):

1. **Close time** → a real `closed_at` column on `trades`, written on **every**
   close path. `order_packages.updated_at` and `notes.closed_at` stop being the
   source of close-time truth.
2. **Realized PnL** → `trades.pnl` is **non-NULL on every `status='closed'`
   row**. PnL is resolved inside the close transaction (broker truth if
   available, else multiplier-aware local mark-to-market). A closed row with
   NULL pnl is an invariant violation, not a normal state.
3. **Trade↔package link** → exactly **one** canonical direction is authoritative
   and is always written for the leg that actually fills. (Decision: standardize
   on `trades.order_package_id` as the canonical many-to-one link; read
   endpoints JOIN on it; `order_packages.linked_trade_id` is kept in sync as a
   convenience but is no longer the join key.) An orphan-adopted trade always
   carries its resolved `order_package_id` (or an explicit sentinel) — never a
   silently-unlinked row.
4. **Account class** → `trades.account_class` is **non-NULL on every row**
   (backfilled for history, stamped at write). `is_demo` becomes a pure mirror,
   not a fallback the readers depend on.
5. **Status semantics** → one documented vocabulary distinguishing "a real
   position" (`open`/`closed`) from "a non-position decision record"
   (`rejected`/`exchange_rejected`/`orphaned`). All read surfaces use the **same**
   definition of "a closed real trade."
6. **Aggregates** → headline numbers (rolling-24h PnL on a close-time basis,
   totals, win-rate, expectancy, equity, real vs paper) are computed **once,
   server-side, from the canonical columns** and exposed as a single
   authoritative surface. Rolling-24h means "closed within the last 24h by
   `closed_at`," not "closed today UTC."

### B. Consumer contract — what the UIs may and may not do

- Consumers **display** server-provided values. They do **not** recompute
  win-rate, expectancy, equity curves, 24h windows, or paper/real splits
  client-side.
- Real and paper are **never blended** (P4 directive). Every surface that shows a
  PnL number shows it labelled real **or** paper (or as two separate values),
  never summed. This includes the Overview per-chart "Live PnL."
- Null is "not provided" (em-dash), never `0` / `"unknown"`.
- The two consumers render from the **same** canonical fields with the **same**
  semantics. A shared consumer-contract doc (Phase 3) pins field names,
  nullability, and labels so the dashboard and Android cannot drift.

### C. Integrity invariants (Phase 4 guardrail — wired into `/health-review`)

A periodic check fails (and surfaces) if any holds:

- INV-1: a `trades` row with `status='closed'` and `closed_at IS NULL`.
- INV-2: a `trades` row with `status='closed'` and `pnl IS NULL`.
- INV-3: a `trades` row with `status` in (`open`,`closed`) and **no** resolvable
  order-package link by the canonical direction.
- INV-4: a `trades` row with `account_class IS NULL`.
- INV-5: an `order_packages` row in a terminal state whose `linked_trade_id`
  disagrees with the `trades.order_package_id` back-ref for the same fill.

---

## Remediation plan (PR roadmap)

Phase 1 touches the live money system (Tier-2/Tier-3): every PR opens as a
**draft** and merges to `main` only on explicit operator approval.

**Phase 0 — Contract (this document).** Tier-1 docs. ✅ this PR.

**Phase 1 — Repair write-path & schema (bot repo):**
- **P1-A** — Schema: add `trades.closed_at` column + idempotent migration +
  index. (foundational, low-risk)
- **P1-B** — Close path: write `closed_at` on **every** close site; resolve
  `pnl` inside the close transaction so no closed row is ever NULL. (money path)
- **P1-C** — Linkage: make `trades.order_package_id` the canonical join key;
  always stamp it (incl. orphan-adopt); keep `linked_trade_id` in sync; switch
  the read endpoints' JOIN. (data-model)
- **P1-D** — `account_class` backfill + fold into migration; demote `is_demo`
  to mirror.
- **P1-E** — Status semantics doc + one repair/back-fill pass over existing rows
  (`closed_at`, `pnl`, `order_package_id`, `account_class`).

**Phase 2 — Canonical server aggregate.** One authoritative endpoint returning
rolling-24h (close-time), totals, win-rate, expectancy, equity, split real vs
paper, computed from the now-correct columns. Both UIs consume it verbatim.

**Phase 3 — Thin the consumers (dashboard Tier-1 + Android):** split Overview
per-chart PnL real/paper; add expandable open-trade cards on Overview; surface
the paper sub-block + `paperOpenTrades`; add an all-history window; delete
client-side rollups/merges/blends; align Android field-by-field; ship the shared
consumer-contract doc.

**Phase 4 — Guardrails.** The INV-1..5 integrity check wired into
`/health-review` so regressions surface immediately.

---

## Closed-loop verification

Each Phase-1 PR is verified against live data via the diag relay (journal
snapshot before/after) and the INV checks, not against intent. A change is
"done" only when the canonical column carries the truth and the corresponding
read-time derivation has been deleted.
