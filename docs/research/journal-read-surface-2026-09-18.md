# `/api/diag/journal` accepts `offset` and discards it — measured, and the clamp is not the bug

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-305** · ENGINEERING lane · Tier-2 · `session_01XYu2vvg9Qgxoqf4jJQyd8i` · measured `2026-09-18`
Reproduces and extends **MI-298** (2026-09-17) and **MI-301** (2026-09-18).

## 1. What was measured, and the population

All figures below were taken **this session** against the live VM through
`scripts/ops/diag_fetch.sh` (served by `https://ict-bot.duckdns.org`), with the
trader at `git_sha d258e852c` / `git_sha_on_disk 613025b75`. Prior readings were
**reproduced, not trusted**.

**Population:** all four tables in `diag._JOURNAL_TABLES`. For each, seven
query-string variants were pulled and the responses compared by **SHA-256 of the
canonicalised JSON**, not by byte length — length equality alone would not have
distinguished "same rows" from "same size".

| variant | trades | order_packages | insights_history | insights_usage |
|---|---|---|---|---|
| `limit=1000` | n=1000 | n=1000 | n=1000 | n=1000 |
| `limit=1000&offset=1000` | **identical** | **identical** | **identical** | **identical** |
| `limit=2000` | **identical** | **identical** | **identical** | **identical** |
| `limit=5&offset=5` | identical to `limit=5` | identical | identical | identical |
| `limit=5&since=2026-08-01` | identical to `limit=5` | identical | identical | identical |
| `limit=5&bogus_param=zzz` | identical to `limit=5` | identical | identical | identical |

**The probe discriminates — this is the positive control, and without it the
table above would be worthless.** `limit=5` returns a genuinely different
payload (n=5, different SHA) on every table, so "identical" is a reading about
the route, not about a broken probe.

**The defect is uniform across all four tables.** MI-305's dispatch asked
explicitly whether `offset` might be honoured on *some* tables and not others —
it is honoured on none, which is a simpler and more complete finding than the
one reported.

## 2. What that costs — the denominator

Row counts from `/api/diag/db_info`, taken in the same session:

| table | total rows | reachable | **unreachable** |
|---|---:|---:|---:|
| `trades` | 5,890 | 1,000 | **4,890 (83.0%)** |
| `order_packages` | 4,753 | 1,000 | **3,753 (79.0%)** |
| `insights_history` | 149,913 | 1,000 | **148,913 (99.3%)** |
| `insights_usage` | 150,019 | 1,000 | **149,019 (99.3%)** |

**The window's FLOOR RISES as the table grows.** The route returns the newest
`limit` rows ordered by a monotonic key, so what falls out of reach is always
the OLDEST rows — which is exactly the pre-era half every before/after study
needs. Measured `trades` id floor, from this repo's own record:

| date | floor id | source |
|---|---:|---|
| 2026-09-10 | 4653 | `CLAUDE.md` `OI-20260910-ORPHAN-ADOPT-SIZE-GATE…` (quoted, not re-measured) |
| 2026-09-12 | 4701 | `CLAUDE.md` `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE…` (quoted, not re-measured) |
| **2026-09-18** | **4891** | **measured this session** |

⚠️ **Two of those three points are QUOTED from prior sessions' records and only
the third is mine** — stated so the trend is not read as a single controlled
measurement. On that basis **238 ids of pre-era history left the reachable
window in 8 days**, ~30/day. Nothing was deleted; the window moved.

## 3. Cause

`get_journal` declared `table` + `limit` only. **FastAPI discards an undeclared
query parameter in silence** — no error, no warning, no field in the response.
So `offset=1000` was accepted and ignored and the caller received a confident,
specific, **wrong** population. That is `UNPROVENANCED DIAGNOSTIC OUTPUT`
sub-class **B** (implicit input selection) as `CLAUDE.md` defines it, on the
surface most sessions grade populations from.

It is made worse by `limit` being **partially** honoured: `limit=5` works,
`limit=2000` silently clamps. A caller who watches `limit` take effect
reasonably infers the route respects its parameters.

## 4. The clamp is DELIBERATE and is kept

**This is the central judgement of the unit, and the answer is not to remove the
bound.**

1. `_MAX_LIMIT = 1000` is **surface-wide**, shared by `/audit`, `/journalctl`,
   `/log_file` and `/journal` — a policy, not an accident at one call site.
2. **Measured cost:** one `limit=1000` page of `order_packages` is already
   **4.8 MB**. At `limit=5000` that is ~24 MB in a single response, from a
   web-api sharing a 2-OCPU / 12 GB box with the live trader. This repo has paid
   for unbounded reads twice (the June 2026 wedges).
3. **The repo already decided this question, in this same file.**
   `/api/diag/audit_query` exists *because* "`/audit` and `/log_file` tail only
   the last `_MAX_LIMIT` lines … ~15 min on a busy day", and its own docstring
   states the remedy it chose: *"`limit` (≤ `_MAX_LIMIT`) + `offset` — page back
   through the full table."* It keeps the clamp and adds paging.

**`/journal` simply never got that treatment.** The fix is therefore paging
*within* the clamp — not a larger clamp, and not an unbounded read.

## 5. What shipped

- **`offset` is honoured**, clamped to `>= 0`.
- **An unsupported parameter is a `400` naming it**, with the supported set and
  a pointer to `/audit_query`. Silently accepting and ignoring does not survive.
- **`envelope=true`** returns what was actually read: `returned_range`,
  `total_rows`, `has_more`, `order_by`, `page_stability`, `limit_state`.
- **The default remains a bare array.** ~15 in-repo consumers parse this
  response as a JSON array (`scripts/ops/system_invariants.py`,
  `scripts/research/*`, …); flipping the default would trade one breakage for
  another. The envelope is opt-in and is **named in the 400**, so it is
  discoverable from the surface itself.

### 5.1 The trap inside the fix

`order_packages` is ordered by **`datetime(updated_at)`** — a column that is
both **non-unique** and **mutable**. Adding `OFFSET` over a non-total order lets
a page boundary fall inside a tie, so rows are **skipped or duplicated** across
pages. A naive `offset` addition would have shipped a NEW silent wrongness
inside the fix for a silent wrongness.

- A deterministic `order_package_id` tiebreaker makes every single query a
  **total order**, so no boundary can land inside a tie.
- That does **not** make a multi-page *walk* snapshot-consistent: a row updated
  between two page reads genuinely moves. `page_stability` reports
  `mutable_key` for `order_packages` and `stable_key` for the three id-ordered
  tables, so a caller treating a paged pull as a census is told which it has.

### 5.2 No `since`/`until`, and that is a refusal rather than an omission

These tables carry **more than one** timestamp column — `trades` has
`timestamp`, `created_at` **and** `closed_at`; `order_packages` has `created_at`
**and** `updated_at`. A date filter here would have to pick one **implicitly**,
which is the very sub-class B defect this change removes. A caller who passes
them is told, and pointed at `/audit_query`, which filters a single **declared**
column (`signals.logged_at_utc`). Adding a date filter to `/journal` needs the
column named in the request; that is a separate decision.

## 6. Verification

- **41 tests pass** in `tests/test_web_api_diag.py` (11 new).
- **8 planted mutants, all caught**: dropping `OFFSET`; dropping the
  unsupported-param refusal; dropping the tiebreaker; always appending the
  tiebreaker; `total_rows=0` instead of `None`; every table as `stable_key`;
  `limit_state` always `as_requested`; flooring `offset` to 0.
- ⚠️ **One mutant SURVIVED the first pass and that is recorded rather than
  quietly fixed.** Dropping the tiebreaker left all three behavioural
  assertions green, because SQLite's order among tied rows is *unspecified by
  SQL but stable in practice* for a small single-table scan — so the test was
  passing on a property SQLite gave for free. The contract is now pinned on the
  **public `order_by` field**, which does discriminate.
- Guards: `collapsed-state-guard` PASS, `ruff-lint` PASS, `silent-empty-guard`
  PASS, `async-route-blocking-guard` PASS, `layer-guard` PASS (6 contracts kept).
  `pr-landing` = `declared_needs_approval`.
- ⚠️ `tests/test_s067_silent_empty_fixes.py::test_diag_vm_health_returns_none_per_field_on_psutil_failure`
  **fails on `origin/main` too** (psutil absent in this sandbox) — verified by
  stashing. Pre-existing, not this change.

## 7. What this does NOT fix

- **The default response is still unannotated.** A caller that does not pass
  `envelope=true` still gets a bare array with no truncation signal. That is the
  price of not breaking ~15 consumers. Flipping the default is a follow-up once
  they are migrated.
- **No consumer branches on the new fields yet.** `scripts/ops/system_invariants.py`
  already works around this clamp by hand (its own comment: *"`/api/diag/journal`
  is hard-clamped to `_MAX_LIMIT = 1000` rows … a journal-only reading would
  have graded it CLEAN"*) and is the natural first consumer. Wiring it is out of
  MI-305's scope, and is filed rather than done.
- **No `collapsed-state-guard` contract is registered** for `total_rows_state`
  or `page_stability`. That guard requires a real consumer that *branches*, and
  the only consumer today is the reader of the response; registering now would
  fail the guard or invite exactly the decorative branch it exists to prevent —
  the same disposition `CLAUDE.md` records for `BYBIT_HEDGE_MODE_SYMBOLS`'s
  `unresolved`. Stated here so it is a judgement on the record rather than an
  omission, and it is the operator's to overrule.
- **The route stays READ-ONLY.** `mode=ro`, `SELECT` only, no new route, no
  write path — the premise the closed diag-token decision rests on is untouched.
