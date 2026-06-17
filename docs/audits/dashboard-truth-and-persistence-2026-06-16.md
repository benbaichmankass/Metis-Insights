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

## Scope expansion (2026-06-16) — writer-side conformance & data precedence

Operator directive (second expansion): audit **everything that writes into the
database**, confirm each writer conforms to the canonical schema/parameters,
find **conflicting data points**, and where they exist declare the
**authority/precedence hierarchy** for resolving them. Ensure every function
reports what it is supposed to. Make **structural** fixes (schema constraints,
one canonical writer per fact, CI guards that keep future builds compliant) —
not read-side patches. This is a precondition for the Phase-1 fix being
holistic: closing trades correctly (P1-B) is pointless if other writers keep
injecting conflicting rows.

New deliverables (feed Phase 1 + add a new Phase 1.5):

- **W1 — Writer inventory & conflict map (canonical DB).** Every caller of
  `insert_trade` / `update_trade` / `insert_order_package` /
  `update_order_package` (and any raw SQL writer), field-by-field: what it
  writes, the trigger, the value semantics. Flag every fact written by more than
  one writer with divergent semantics, and every value that violates the
  intended vocabulary. Known seeds: `trades.direction` stored as both
  `long/short` and `buy/sell`; `account_class` vs `is_demo`; `setup_type` vs
  `strategy_name`; `order_package_id` vs `linked_trade_id`; multi-source `pnl`.
- **W2 — Parallel/duplicate truth-store audit.** Every JSONL/JSON file an API
  endpoint or consumer reads **as a source of truth** instead of the canonical
  DB (`signal_audit.jsonl`, `outcomes.jsonl`, `shadow_predictions*.jsonl`,
  `balance_snapshots.json`, `liquidity_state.json`, `runtime_status.json`,
  `news_decisions.jsonl`, `insights/*.json`, `validation.jsonl`,
  `trainer_mirror/*`). For each: what facts it holds, whether those facts are
  also in the DB, and where the two can diverge. Decide what must be
  canonicalized into the DB vs. legitimately stay file-based (and why).
- **W3 — Derived/aggregate-state writers.** `daily_risk_state`,
  `strategy_versions`, `account_context_snapshots`, `backtest_results`,
  `insights_history` / `insights_usage`, and the `trainer_store.db` ingest —
  who writes them, whether they can drift from the `trades`-derived truth, and
  the precedence rule.

### Data-authority hierarchy (to be finalized by W1–W3)

For every canonical fact there is **one** authoritative writer and a documented
precedence when a secondary source disagrees. Draft top-level rule:

1. **Broker/exchange truth** (filled PnL, fills, balances) wins for the facts it
   owns, when available.
2. **The canonical DB column** is the source of truth for consumers — never a
   JSONL file and never a read-time re-derivation, once W1–W3 land the writers.
3. **Local compute** (mark-to-market PnL, derived aggregates) is the explicit,
   labelled fallback, never blended silently with broker truth.

### Phase 1.5 — Writer conformance & structural constraints

After W1–W3: one canonical writer per fact; normalize divergent vocabularies at
the **write** boundary (e.g. `direction` stored canonically once); add schema
constraints (NOT NULL / CHECK / the close-path invariants); and add **CI guards**
so a future writer that bypasses the canonical path or emits a non-conforming
value fails the build (siblings of the existing `account-class-guard` /
`canonical-db-resolver` guards).

## Writer-audit consolidation (W1–W3 complete, 2026-06-16)

### Concrete defects found (the structural bugs to fix, not patch)

Ranked by impact on truth:

1. **Operator `/closeall` writes a malformed, package-orphaning row.**
   `src/units/ui/processor.py:1659` (`close_open_positions`) closes via **raw
   SQL**, writes `notes` as the bare string `"closed_at=<iso>"` (not JSON, so
   the read-side `_decode_notes_closed_at` can't parse it and any prior notes
   JSON is clobbered), sets **no `pnl`**, and **never cascade-closes the linked
   `order_packages` row** → leaves an open package (monocle leak). Highest-value
   single fix: route it through the canonical close path.
2. **Reconciler orphan-adopt inserts omit `account_class`/`is_demo`.**
   `src/runtime/order_monitor.py:2626` and `:2687` (`_adopt_orphan_position`)
   and the smoke insert `coordinator.py:2657` write neither column → they fall
   to defaults (`is_demo=0`, `account_class=NULL`) → **a paper-account adopted
   orphan is mis-classified as real_money** and leaks into real-money PnL/stats
   until a backfill re-stamps it. Same inserts also omit `order_package_id`
   (the trade→package link), relying solely on the package-side `linked_trade_id`.
3. **`trades.direction` carries two vocabularies.** Live + reconciler writers
   store `long`/`short`; `ml/datasets/backtest_recorder.py:59` is the sole writer
   storing `buy`/`sell` (on `is_backtest=1` rows). The read-side `_SIDE_MAP`
   normalizer exists only because of this. No CHECK constraint enforces a
   vocabulary.
4. **`account_context_snapshots` is permanently NULL on 2–3 columns** —
   `src/units/accounts/context_snapshot.py:235` queries
   `daily_risk_state.utc_date`, but the production column is `date`
   (`risk.py:79-86`) → the query errors, is swallowed, and writes NULL
   `daily_pnl`/`daily_equity_high`/`drawdown_pct` on every row. **Masked by a
   unit-test fixture that builds the table with the wrong (`utc_date`) schema**
   (`tests/test_account_context_snapshot.py:28`) — the "tests pass against a
   schema production doesn't have" anti-pattern. Field beats test: fix the query
   to `date`, fix the fixture, add a real-schema test.
5. **`rebuild_pnl_from_bybit.py:327`** is the only unguarded `pnl` overwriter
   (can clobber a fresher live-sweep value); **`cleanup_ghost_trades.ipynb`**
   targets the stale CWD-relative DB path. Operator tools — fix or quarantine.
6. **Signals + balances + insights are dual-homed / un-homed** (W2): signals read
   from JSONL on `/api/bot/signals` but from the DB on `/api/diag/audit_query`
   (stalled S-034 cutover, silent dual-write); balances have **no DB table**;
   insights split file vs DB reads.

### What is already correct (do NOT "fix")

- **`pnl` precedence is real and enforced**: broker closed-pnl (Bybit) is
  authoritative; close paths deliberately leave `pnl` NULL so the broker sweep
  fills the fee-accurate number; the local mark-to-market sweep only fires past
  the broker-retention window so it never pre-empts broker truth. **This is the
  hierarchy — preserve it.**
- **`order_package_id` (per-leg) ↔ `linked_trade_id` (primary-entry-only) is the
  documented many-to-one design.** A package with `linked_trade_id=NULL` while
  trades reference it via `order_package_id` is *expected*, not corruption. The
  fix is read-side (join on the universal `order_package_id`) + always stamping
  `order_package_id` (incl. reconciler inserts), not "repair the links."
- All derived/aggregate tables (`daily_risk_state`, `strategy_versions`,
  `backtest_results`, `insights_*`, `trainer_store.db`) are **rebuildable caches
  or immutable history** — none can silently corrupt the `trades` truth.
- The DB helpers raise on an unknown column (no silent-drop risk).

### Finalized data-authority hierarchy (per fact)

| Fact | Authoritative source | Precedence rule | Structural fix |
|---|---|---|---|
| realized `pnl` | broker closed-pnl → local compute → (bounded) NULL | broker > local > null; local never pre-empts broker within retention window | guard the rebuild tool; resolve local-at-close only for non-broker-reader accounts (P1-B refinement) |
| `closed_at` | the close path that fires | the `closed_at` **column** (P1-B), never notes-JSON / `op.updated_at` derivation | P1-A column + P1-B writes |
| `direction` | live/reconciler (`long`/`short`) | `long/short` canonical | normalize at write boundary (backtest_recorder) + CHECK |
| `account_class`/`is_demo` | entry writer T1 (from `account_id`) | `account_class` authoritative, `is_demo` mirrors | default both inside `insert_trade`; stamp on reconciler/smoke inserts |
| `status` | the lifecycle writer | documented enum state machine | document + CHECK; keep smoke strings out of `status` |
| trade↔package link | `trades.order_package_id` (per-leg, universal) | order_package_id wins; read endpoints JOIN it; linked_trade_id is a convenience | stamp it on every insert (incl. adopt); switch read JOINs |
| signals / balances / insights | the **DB table** | DB authoritative for reads; JSONL/JSON = append-only audit | finish signals cutover (fail-loud); add balances table; declare insights precedence |
| daily/aggregate state | `trades` (recomputed) | trades is truth; caches are rebuildable | add `is_backtest=0` filter; fix `utc_date`→`date` |

**One-line rule:** the canonical DB **column** is the source of truth for every
consumer-rendered fact; broker truth wins for the facts it owns; local compute is
the explicit, labelled fallback; files are append-only audit, never a read source
for trade/decision/balance facts.

### P1-B refinement forced by the audit

The naive "resolve `pnl` at close so no closed row is ever NULL" would **violate**
the broker>local precedence (it would pre-empt the fee-accurate Bybit number).
Corrected invariant:
- **`closed_at` column** is written unconditionally on every close (close time is
  always known) — INV-1 stands as-is.
- **`pnl`**: for accounts with **no broker pnl reader** (declared capability —
  IBKR/Alpaca/OANDA), resolve local mark-to-market `pnl` **at close** (no broker
  value is ever coming, so the NULL window serves no purpose). For broker-reader
  accounts (Bybit), keep the deferred broker sweep but make INV-2 **time-bounded**
  ("no closed row with NULL pnl older than the broker-retention window") and make
  the local sweep the guaranteed backstop. INV-2 becomes a *convergence*
  guarantee, not "never NULL at the instant of close."

### Phase 1.5 — Writer conformance & structural constraints (expanded task list)

- WC-1: route `/closeall` (`processor.py`) through the canonical close path
  (JSON notes + `closed_at` + pnl resolution + package cascade).
- WC-2: make `insert_trade` default `account_class` from `account_id` and derive
  `is_demo`; stamp `order_package_id` — so reconciler/smoke inserts can't create
  un-stamped/un-linked rows.
- WC-3: normalize `direction` to `long/short` at the write boundary
  (`backtest_recorder`) + add a CHECK (or `canonical_direction()` guard).
- WC-4: fix `account_context_snapshots` `utc_date`→`date`, fix the fixture, add a
  real-schema test; add `is_backtest=0` to `daily_risk_state` recompute.
- WC-5: signals — finish the S-034 cutover (read DB, JSONL append-only audit,
  fail-loud dual-write); add a `balance_snapshots` DB table; declare insights
  precedence.
- WC-6: new **writer-conformance CI guard** (fork `canonical-db-resolver` /
  `account-class-guard`): a new `trades`/`order_packages` writer must use the
  canonical helper, stamp `account_class`+`order_package_id`, and not introduce a
  second `direction`/`status` vocabulary. Plus `status`/`direction` CHECK
  constraints in the schema so the DB itself rejects non-conforming values.
- WC-7: housekeeping — guard `rebuild_pnl_from_bybit.py`; quarantine/repath
  `cleanup_ghost_trades.ipynb`; fix the stale VM IP in `oci-storage-verify.yml`.

## Closed-loop verification

Each Phase-1 change is verified against live data via the diag relay (journal
snapshot before/after) and the INV checks, not against intent. A change is
"done" only when the canonical column carries the truth and the corresponding
read-time derivation has been deleted.

## Progress log — 2026-06-16 (overnight autonomous session)

**Landed on `main` (deployed via `ict-git-sync`):**
- PR #3817 (foundation): the governing contract; **P1-A** (`trades.closed_at`
  column + migration); **WC-4** (`account_context_snapshots` `utc_date`→`date`
  bug + both masking test fixtures rebuilt from the canonical DDL).

**Committed on `claude/streamlab-dashboard-audit-4popmq`** (draft PR #3818 —
the money-path phase; merge gated on operator review):
- **WC-1** — `/closeall` now closes canonically (JSON notes + `closed_at`
  column + cascades the linked order package).
- **P1-B.1** (write) — every `order_monitor` close site stamps the `closed_at`
  column (6 functions; agent-drafted, human-reviewed diff).
- **P1-B.2** (read) — `/trades/closed` resolves
  `COALESCE(t.closed_at, op.updated_at, t.timestamp)` (column authoritative,
  legacy chain as fallback); ordering + `since` use the same key.
- **Phase 2** — `/performance` windows now key on the same canonical close-time
  basis, so `window=24h` is a true rolling-24h-by-close-time window (the
  server-side fix for the dashboards' 24h-PnL KPI). Verified: a trade closed 2h
  ago but opened 30h ago now counts in the 24h window.

All verified locally (SQL + compile + targeted tests); `closed_at` is now
canonical end-to-end (written + read), and the 24h aggregate is close-time-correct.

**NOT yet done — and the deliberately-deferred risky items (need verification I
couldn't do unattended):**
- **WC-3** (normalize `trades.direction` to `long/short` in
  `ml/datasets/backtest_recorder.py`): deferred — could break ML training
  dataset parity if the training pipeline expects `buy/sell`. Verify the
  dataset consumers first.
- **WC-2 write side** (stamp `account_class`/`is_demo`/`order_package_id` on the
  reconciler orphan-adopt + smoke inserts): deferred — the orphan-adopt path is
  rarely exercised and hard to test without a live trader; needs careful review.
- **Read-side order-package link** (`/order-packages` join on the canonical
  `trades.order_package_id` instead of `linked_trade_id`): needs a correlated
  subquery to preserve one-row-per-package + paper-filter semantics — design +
  test before shipping. This is the read-side half of the "trend trade with no
  order package" fix.
- **P1-B pnl-at-close** (non-broker accounts), **P1-E** (historical backfill),
  **WC-5** (signals cutover / balances DB table / insights precedence),
  **WC-6** (writer-conformance CI guard + CHECK constraints), **Phase 3**
  (thin both dashboards — needs the preview app to verify rendering), **Phase 4**
  (INV integrity check in `/health-review`).

**Morning decisions for the operator:**
1. Review + merge draft **#3818** (WC-1 + P1-B + Phase 2) — order-path code, so
   it's gated on your OK. All CI-green, verified locally.
2. WC-3 direction normalization: confirm the ML training datasets tolerate
   `long/short` (vs `buy/sell`) before I flip the writer.
3. Phase 3 dashboard UI changes will go to the **`claude/web-app-preview`**
   branch for you to eyeball before prod (can't verify rendering from here).

## Progress log — 2026-06-17 (widest-scope continuation)

**Phase 4 — DB-integrity alerting (the DB tells us when intake breaks).** PR
**#3829**: `scripts/check_db_integrity.py` (orphan trades, NULL-pnl past the
bounded window, account_class / closed_at gaps, direction-vocabulary drift) +
`scripts/db_integrity_alert.py` + `ict-db-integrity.{service,timer}` (hourly
oneshot, Telegrams `[WARN]`/`[CRITICAL]`) + the `/health-review` SKILL hook.
Registered the new unit in the S-012 canonical service set + deployment-ops doc.

**P1-E — historical backfill, wired for live-DB execution.** The dry-run pivot
(trainer-VM journal copies are empty stubs) is resolved by the new
**`backfill-closed-at` system-action** (Tier-2): runs `backfill_closed_at.py`
DRY-RUN → `--apply` on the live VM, with `--also-account-class` (operator
widest-scope directive) so the same audited pass also closes any residual
`account_class` gap. Allowlisted through all four workflow surfaces + tests +
doc. **Operator action:** dispatch `backfill-closed-at` (issue label
`system-action`) to preview, review the dry-run counts, approve the apply.

**WC-5 (part 1) — balances now have a DB home.** New canonical
`trade_journal.db::balance_snapshots` table (append-only history) + canonical
writer `Database.insert_balance_snapshot` / reader `get_latest_balance_snapshots`.
The hourly-report `account_snapshots()` best-effort dual-writes each reading;
`/api/bot/accounts/balances` is now **DB-authoritative** (latest row per
account, with `delta_1h`/`open_positions`/`api_ok`), JSON snapshot = degraded
fallback (`source` field records which served). Closes the audit's "balances
have no DB table" gap. 40 tests green.

**WC-5 (part 2) — insights are DB-canonical.** `history.latest_payload` +
generator writes `insights_history` **first** then the derived cache; the
`/api/bot/insights/*` router falls back to the newest DB row on cache miss
(`source: history_db`) before the placeholder. A wiped cache dir can no longer
blank the dashboard. 41 insights tests green (PR #3837).

**WC-5 (part 3) — signals cutover DONE (the last WC-5 piece).**
`/api/bot/signals` now reads `trade_journal.db::signals` (the dual-write
target; `meta` expanded to top-level so the shape matches a JSONL record),
with `signal_audit.jsonl` as the **coupled fallback**. The single rollback
`SIGNAL_DUAL_WRITE_DISABLED` flips BOTH sides — writer off AND reader back to
JSONL — so the reader is never left serving a frozen DB. The dual-write is now
**fail-loud**: a DB-write failure escalates once per episode to an ERROR
outcome (it can no longer diverge silently now that reads come from the DB) but
still never raises into the pipeline hot path. Shared `_map_signals` keeps the
rendered shape identical across both sources. 32 cutover/zones/storage tests
green. **RISKIEST piece — live hot path; ships in an operator-gated draft and
wants a dual-write-clean soak before merge.**

**WC-5 COMPLETE** (balances + insights + signals). Doc-freshness: the CLAUDE.md
`/accounts/balances` + `/signals` API rows and the `SIGNAL_DUAL_WRITE_DISABLED`
env note were updated to match.

**Cross-repo follow-up (operator directive 2026-06-17) — DONE.** Android
parity audit complete (PR #56): JSON parsing is crash-safe (`ignoreUnknownKeys`);
fixed two model gaps (`BotStats.paper` sub-block via a dedicated `BotStatsPaper`
type; `/accounts/balances` health fields) + surfaced them in the UI (a separate
paper-KPI section on Status, balance-health chips on Accounts). Gradle build
green; on-device visual check pending.
