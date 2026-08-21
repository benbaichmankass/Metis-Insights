# Sprint Log: S-WAVE0-EXIT-FETCH-20260821

> ⚠️ **PROVENANCE: COMPILED FROM COMMITTED ARTIFACTS, NOT A FIRST-HAND ACCOUNT.**
> The session that did this work (`wave0-8g7443`) closed at 2026-08-21T18:12:05Z
> without writing a log, and its Wave-0.1 result — a measurement that refutes a
> remedy the backlog itself proposed — would otherwise survive only inside another
> session's ledger row. Written by `spsxq6` on operator direction, from sources
> that can be opened and checked: the `0649418` merge diff,
> [`docs/research/exit-eval-fetch-attribution-2026-08-21.md`](../research/exit-eval-fetch-attribution-2026-08-21.md)
> (538 lines, 10 sections), the three backlog rows filed, and four coordination-board
> comments ([START](https://github.com/benbaichmankass/Metis-Insights/issues/6927#issuecomment-5373059500) ·
> [QUEUED](https://github.com/benbaichmankass/Metis-Insights/issues/6927#issuecomment-5373453965) ·
> [CLAIM](https://github.com/benbaichmankass/Metis-Insights/issues/6927#issuecomment-5373550248) ·
> [RELEASE](https://github.com/benbaichmankass/Metis-Insights/issues/6927#issuecomment-5373565199)).
>
> **What a compiler cannot supply, and what is therefore absent below:** what was
> tried and abandoned, how long anything took, and what was nearly got wrong. Those
> are usually a log's most valuable lines. `wave0-8g7443` is live again and should
> **overwrite anything here that misreads what it actually did** — a wrong execution
> record is worse than a missing one.

## Date Range
2026-08-21 17:21Z (board `▶️ START`) → 18:12Z (`✅ DONE`), single session
`wave0-8g7443`, concurrent with `spsxq6` and the tail of `dcf5220b`.
Re-opened 19:11:55Z for T.1 — **that work is not covered here.**

## Objective
Wave 0.1 of `docs/claude/WORKPLAN-2026-08-21.md`:
`BL-20260821-EXIT-EVAL-BREACHES-60S-ON-A-THIRD-OF-CYCLES`. Operator decision on
record was **investigate and propose — do NOT flip**, so the deliverable was
evidence plus an exact diff, stopping at the Tier-3 gate.

## Tier
Tier-1 throughout. **`src/` unchanged** — verified in the `0649418` diff: the five
files are `CLAUDE.md`, `docs/claude/WORKPLAN-2026-08-21.md`,
`docs/claude/health-review-backlog.json`, `docs/claude/session-board.json`, and
the new research doc. The Tier-3 change lives *inside* the doc, where it cannot
merge by accident.

## Starting Context
The item's own `next_action` proposed raising `CANDLE_CACHE_TTL_MAX_S`. The
review had recorded the breach at 32.4% of cycles.

## Repo State Checked
Measurements taken against the repo at `a252119`; `main` moved several times
during the session.

## Files and Systems Inspected
`src/runtime/market_data.py` (`_client_cache_key`, `connector_for_symbol`,
`_candle_cache_key`) · `src/exchange/ib_connector.py` · `src/units/accounts/ib_client.py`
(`get_ib_client`) · `src/runtime/tick_cost.py` · `/api/diag/tick_cost` ·
`exit_interval_soak` · `config/accounts.yaml`.

## Work Completed
**Root-caused Wave 0.1 and refuted the remedy the backlog row proposed.**

- **The pass is 96.7% candle fetch** — 40.81 s of a 42.19 s mean pass (n=281).
- **The cadence knob is inert:** `interval == max(30 s, pass)` on **938/993
  (94.5%)** intervals; **277/993 (27.9%)** of passes exceed 60 s *before any sleep*.
- **Root cause:** `_client_cache_key` excludes `interactive_brokers` from the
  connector memo, so a fresh `IBMarketData` is built per request and the candle
  cache — keyed on a per-**object** token — **cannot hit at any TTL**.
- **The decisive line:** the one open IB 15m package was fetched from the venue
  **281 times in 281 consecutive passes** (1.000/pass, zero hits) against a 90 s
  TTL and a 42.19 s revisit interval predicting ~132.
- **The controls behaved**, which is what makes it a finding rather than a
  coincidence: 4h (Bybit) 0.563 predicted vs 0.498 measured · 1h (Alpaca) 0.422 vs
  0.406 · 2h (Bybit) 0.141 vs 0.132 — all within **12%**. Only IB is 2.13× over.
- **⚠️ The row's own `next_action` is REFUTED:** at an **86 400 s** cap an IB frame
  still goes to the venue **5/5** times. It would help the tick's Alpaca 1d legs
  and move the exit loop **not at all**.
- **The exclusion's stated safety premise was already false:** `IBMarketData` holds
  no socket — it takes its client from `get_ib_client()`, already a process-wide
  registry — so every wrapper for one endpoint already shares one `IBClient`.
  `CLAUDE.md` was corrected where it asserted otherwise, with the exclusion left
  **unchanged in code**.
- **Wave 0.2 STEP 0 re-classified:** *"does `bybit_portfolio` share a demo UID with
  `bybit_1`?"* was carried as operator-blocked. It is blocked on a **read surface
  that does not exist**, and building one is **Tier-1**. Distinct key envs are
  consistent with both same-UID and different-UID, so config narrows and cannot
  close it; `GET /v5/user/query-api` returns `userID`+`parentUid` and greps over
  `src/` and `scripts/` return **zero hits**.

## Validation Performed
- Instrument self-check, both dimensions: `fetchby.*` totals **11 467.9 s** vs
  `Σ fetch.*` **11 467.9 s** (delta **0.0 s**); 1 326 misses + 3 170 `cache_hit`
  = 4 496 = `fetchby` n exactly.
- Controlled reproduction at `a252119`: 5 identical requests → bybit 1 venue call ·
  alpaca 1 · **IB 15m 5** · **IB 1d 5**; with the proposed patch, **1 · 1 · 1 · 1**.
- Durable interval record, stated as a tail rather than a whole: 993 intervals,
  8 processes, 13.0 h from a 1 000-line tail of a 3.2 MB file — **28.9%** over 60 s,
  max **95.9 s**, p90 73.8 s.
- Negative control on time-of-day: breach rate **20.5–37.1% flat across all 14 UTC
  hours**, including hours when US equity venues are shut — rules out a
  market-hours artifact.
- Pre-existing test failures **verified pre-existing** by stashing the patch and
  re-running on clean `a252119` (identical 2 failed / 3 passed, `ModuleNotFoundError:
  ccxt`), not assumed.
- CI 4/4 on head `76a9625`, read individually via `get_check_runs` — **not** from
  the `check_suite.completed` events, of which twelve arrived across three heads.

## Documentation Updated
`docs/research/exit-eval-fetch-attribution-2026-08-21.md` (new, 538 lines) ·
`CLAUDE.md` (the false socket premise) · `docs/claude/WORKPLAN-2026-08-21.md`
(Wave 0.1 status + the STEP 0 re-classification) ·
`docs/claude/health-review-backlog.json` · `docs/claude/session-board.json`.

## Contradictions or Drift Found
- **A canonical doc asserting something the code does not do.** `CLAUDE.md`
  justified the IB exclusion as *"an `IBMarketData` holds a live socket on a
  specific clientId"*. It holds no socket. Worse, the row never stated what the
  exclusion **costs**, so a reader would conclude IB had been considered and
  handled. Corrected in place.
- **A backlog row whose `next_action` was measurably wrong** — see the refutation
  above. Corrected rather than silently dropped.
- **Three separate items gated on a fact with no read surface**, filed as a class:
  `…NO-READ-SURFACE-FOR-TIMER-SCHEDULE` (which manufactured a phantom
  high-severity defect), `…FETCH-COST-HAS-NO-VENUE-AXIS` (forced a bound where a
  measurement was wanted), `…NO-BYBIT-ACCOUNT-IDENTITY-READ-SURFACE` (parked a
  decided Tier-3 item). This became **Phase 0 item 0.6** of the work plan.

## Risks and Follow-Ups
- `BL-20260821-FETCH-COST-HAS-NO-VENUE-AXIS` (medium/T1) — `tick_cost` has no
  venue axis, which is why the saving is a **bound** (15.1–30.3 s/pass) rather than
  a number.
- `BL-20260821-CANDLE-CACHE-SIZE-AND-FLUSH-UNOBSERVABLE` (low/T1) — the cache
  clears **wholesale** at >512 entries and neither size nor flush is reportable,
  while IB fetches measurably poison it.
- `BL-20260821-NO-BYBIT-ACCOUNT-IDENTITY-READ-SURFACE` (medium/T1) — Wave 0.2's gate.

## Deferred Items
- **The Tier-3 diff itself.** Approved 2026-08-21T18:05Z and **not implemented**
  in this session; `src/` untouched, the approval recorded in the backlog row so a
  later session ships § 6 without re-asking. *(It was reordered ahead of Phase 0 by
  operator direction at ~19:11Z and taken up by this session's re-open — not
  covered by this log.)*
- **The max interval after the change was deliberately NOT projected** — the mean
  is projectable from the fetch budget; the max is a queue-stall property. Saying
  so was treated as the point rather than a gap.
- **The breach is NOT fixed.** 28.9% over 60 s stands. Wave 0.1's *done-condition*
  (deliver a proposal) is met; the row's *resolution_criteria* (< 1% over n ≥ 500
  across ≥ 3 processes) is untouched, and the row says so rather than reading closed.

## Next Recommended Sprint
Ship § 6 with its named falsifier — off-loop `fetch.15m` must fall from
**1.000/pass** toward `interval/90`; **if it stays at 1.000 the change did not
take.** Then Phase 0 of the work plan.

## Wrap-Up Check
`0649418` merged; board `🔓 RELEASE` + `✅ DONE` at 18:12:05Z; post-merge state
verified on `main` by that session (backlog 777 rows, ids unique; `src/`,
`config/`, `deploy/` **zero changes** vs `a252119`).

**Recorded because that session recorded it against itself:** three CI guards
caught defects in its own text before it pushed — a percentage with no
denominator (`claim-basis-guard`), two unannotated impossibility claims
(`impossibility-claim-guard`), and a vacuous-green attempt on uncommitted paths
(`run_guards.py`). It also corrected itself twice: the staleness cost is
**paper-only** (`ib_paper`; `ib_live` is `mode: dry_run`), asserted twice before
`config/accounts.yaml` was read; and its own PR body's backlog count was stale
**twice over**.
