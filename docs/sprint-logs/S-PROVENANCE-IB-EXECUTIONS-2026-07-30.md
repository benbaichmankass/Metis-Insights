# S-PROVENANCE-IB-EXECUTIONS-2026-07-30

**Objective:** Continue S-PROVENANCE-EXITLEAK-ROOTCAUSE — close the three open ends
PR #8039 deliberately left: make IB PnL measurable, make the provenance CI guard
blocking, and stop INV-2 pressuring fabrication. Then widen the audit until the
numbers are trustworthy.

**Dates:** 2026-07-30 · **Branch:** `claude/provenance-ib-executions-s582dx` · **PR:** #8069 (draft)
**Tier:** All Tier-1. Two Tier-2 halves prepared as exact diffs, deliberately unmerged.

---

## Objective

The briefing carried four ordered tasks. All four shipped. But the session's most
important output is not the code — it is that **measuring the live journal
corrected the premise the work was built on**, and exposed a defect in what I had
already shipped.

## Work completed

### 1. IB executions reader (Tier-1)

`ib_paper` was believed to hold +$240,569 of the fabricated PnL, and
`interactive_brokers` is absent from `clients.BROKER_PNL_READER_EXCHANGES` with no
IB fills reader, so every IB close fell through to the mark-substituting sweep.
IBKR *does* serve truth via `reqExecutions` (each fill carries
`CommissionReport.realizedPNL`).

- `src/runtime/exchange_fills_ib.py` — IB `Fill` → `exchange_fills` row. Duck-typed
  with an injected fetcher, so it imports and tests with no `ib_insync` and no
  gateway. Broker-truth `realizedPNL` rides in the row's `raw` JSON: the schema is
  `CREATE TABLE IF NOT EXISTS`, so a typed column would silently not apply to the
  store already on the VM.
- `IBClient.executions(since)` — bounded like `_req_all_open_orders`. Returns
  `None` on read failure, `[]` only on a confirmed-clean empty read. Safe on a
  readonly client (`reqExecutions` is stateless, unlike `reqAccountUpdates`).
- `exchange_accounts.live_ib_fill_accounts()` + `scripts/pull_ib_executions.py`.
- 41 tests, `ib_insync`-free.

**Stated limit:** forward-accruing, NOT a backfill — IBKR's execution history is
short-lived, so it cannot retroactively measure historical rows. The module does
not *assert* a retention window it cannot verify; `coverage_summary` reports what
the venue actually served each run.

### 2. `/performance` `pnlCoverage` + all 4 write-only keys consumed; guard REQUIRED

`rCoverage` correctly refused to let partial R-measurement masquerade as full,
while the `pnl` it is derived from was silently fabricated. That asymmetry *was*
the defect. Consumers added:

| key | consumer |
|---|---|
| `pnl_source` | `/performance` `pnlCoverage` + per-strategy split |
| `close_exec_type` | `monitor_miss_analysis` now refuses to classify a venue force-close (`BustTrade` = liquidation, `AdlTrade` = auto-deleverage) against the strategy's own bracket |
| `exit_reason_source` | same diagnostic reports how many `exit_reason` labels are `unresolved` placeholders |
| `unrealizedPnlSource` | `/positions` emits `unrealizedPnlProvenance` via the canonical module |

Vocabulary gained **key-aware classification**: a live mark on an OPEN position is
`estimated` (the correct valuation — no truer number exists until it closes); the
same string on a CLOSED trade stays `fabricated`. Reporting only — nothing
overridden becomes `MEASURED`.

`provenance-consumer-guard` promoted to a REQUIRED context in the same PR that
makes it green — not waivered green.

### 3. INV-2 was pressuring fabrication

INV-2 demanded a number for every closed row past the sweep grace and never asked
what KIND, so the only way to clear it was to invent one. An invariant whose only
satisfying move is to invent data is a forcing function pointed the wrong way.
Now silence still alerts; an explicit `unmeasured` marker clears it; INV-2b counts
every declaration so the marker cannot become a mute button.

### 4. Measurement — and two corrections

`scripts/ops/provenance_exposure_audit.py` (new, re-runnable) run against the live
journal via the trainer relay (#8072/#8073, 829-row closed population, current to
07:40 UTC).

**Correction A — the headline figure.** The briefed "+$247,683.78, the bulk of it
`ib_paper`" did not reproduce against closed rows. Both numbers are right; they
measure different populations:

| population | rows | fabricated | fabricated PnL |
|---|---|---|---|
| closed, non-backtest, `pnl NOT NULL` (the decision population) | 829 | 206 | **−$36,018.60** |
| any status, incl. backtest | 845 | 222 | **+$247,683.78** |

The all-status figure is dominated by **4 `orphaned` `ib_paper` rows carrying
+$284,084.92**. In the closed population the concentration is **`bybit_1`**
(152/323, 47.1%, −$18,125) and **`bybit_portfolio`** (11/12, 91.7%, −$13,100);
`ib_paper` closed rows are 3 of 24. The **trend** reproduces closely across both:
0.0% (May) → 23.7% (Jun) → **65.3% (Jul)**.

This re-orders the work: the IB reader is correct and worth having, but ib_paper
is not the biggest closed-population exposure — and the two accounts that are
already have a broker reader.

**Correction B — a defect in my own PR.** `pnl_source` is nearly information-free
live (only `(none)` ×576 and `local_compute` ×253), so `pnlCoverage` as first
shipped reported **0.0 for every window**, including the 504 rows whose exit price
is genuine broker truth. Fixed with `provenance.classify_pnl(row)` — worst
recognised bucket across both keys. Live coverage: **504/829 = 60.8% measured**.

I shipped that flaw by reasoning from the field NAME instead of measuring its
VALUES. The lesson generalises: this workstream is about not trusting unmeasured
numbers, and I produced one.

### 5. A bug caught in-session, then guarded

My first INV-2 predicate used a bare `json_extract`. SQLite's `json_extract`
**raises** `malformed JSON` — it does not return NULL — and one bad row aborts the
whole statement, so it would have turned the integrity report into an outage.
`COALESCE(json_extract(...), '')` LOOKS null-safe and is not; review does not
catch this. Fixed, swept the repo (everything else already guarded), and added
`scripts/ci/check_json_extract_guarded.py` with 13 tests proving it **fires** —
including on the exact shape I nearly shipped.

## Validation

- 126 tests green: 52 `provenance.py` (which shipped with **none**, and whose
  semantics I changed), 9 end-to-end `pnlCoverage` against a synthetic journal,
  11 INV-2, 13 json-extract guard, 41 IB.
- `provenance-consumer-guard` → OK (was failing on 4 keys). `json-extract-guard`
  → OK (891 files).
- `ruff check .` clean on the **repo-pinned** ruff (`>=0.15.0,<0.16`). Note: ruff
  0.16 expands the default rule set and reports ~9.3k repo-wide — a local
  unpinned ruff is NOT the CI gate.
- 84 sandbox test-collection errors verified **identical on the clean baseline**
  (missing deps, not this change).

## Flags raised

**P1 — `pull_request` CI does not run on ANY PR in this repo**
(`BL-20260730-PR-CI-NOT-ATTACHING`). PR #8069 shows `total_count: 0` across five
real pushes; #8039 the same; **#8053 merged to `main` today with zero checks**.
`actions_list` filtered to `event=pull_request` returns only alert workflows.
Push-triggered and issue-triggered workflows DO run, so Actions is not disabled —
the fault is specific to the `pull_request` event. Zero checks renders identically
to green. Needs operator/console access to diagnose.

**High — `ml/` is provenance-blind** (`BL-20260730-ML-LABELS-IGNORE-PNL-PROVENANCE`).
`trade_outcomes.py` sets `won = pnl > 0` straight off `trades.pnl` with no filter;
one grep hit in the whole tree, an unrelated comment. Filed rather than fixed —
measure before altering a training population.

**Medium — stray 8 MB `trade_journal.db` at the trainer repo root**
(`BL-20260730-TRAINER-JOURNAL-COPY-STALE`, corrected in-session). I first filed
this as "trainer 10 days stale"; #8071 showed the real 677 MB store under `data/`
is current. The residual finding is the stray file — the artifact
`canonical-db-resolver` exists to prevent.

**Low — promote `json-extract-guard` to required once green on main**
(`BL-20260730-JSON-EXTRACT-GUARD-PROMOTE`).

## Left for the operator (Tier-2/3)

1. `docs/proposals/tier2-sweep-candle-anchoring-2026-07-30.md` — stop pricing a
   confirmed close from a live mark; anchor to the bar at `closed_at`, stamp
   `candle_at_close` (ESTIMATED, never MEASURED), declare rather than invent when
   there is no anchor. Evidence: median 1.33 bps, p90 16.05, 46/48 within 50 bps.
   Scope limit stated: IBKR candle coverage is 0%.
2. Adding `interactive_brokers` to `BROKER_PNL_READER_EXCHANGES` + scheduling the
   IB puller on a timer.

Historical relabel remains **RELABEL ONLY, never re-price** (operator decision).

## Docs updated

- `CLAUDE.md` § "Number provenance" — both populations stated side by side, with
  the rule **quote the population or don't quote the number**, plus the
  `classify_pnl` two-key requirement.
- `docs/claude/health-review-backlog.json` — 4 items filed, 1 corrected.
