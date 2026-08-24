# Sprint Log: S-BRACKET-EXPECTATIONS-AND-CANDLE-FEED-2026-08-24

## Date Range
2026-08-24 (single session)

## Objective
Answer the operator's standing question — *what expectation should a bracket
carry at entry, per family, and how is it CONSTRUCTED rather than clamped* —
and unblock the data the question depends on. Mid-session the operator
approved four follow-up decisions; those were executed too.

## Tier
Tier-1 throughout. `config/strategies.yaml` was **not touched**; every Tier-3
implication is proposed, never applied. No model promoted. No live-VM action.

## Starting Context
Continuation of the bracket-expectations workstream. Prior sessions had
established the venue clamp (`cap_r = TP_VENUE_CAP_PCT × entry / risk`), the
sentinel population (`tp_r >= 50` = no real target), and that 25
`no_free_lane_candle_feed` cells had no data lane.

## Repo State Checked
`main` `f13f777` → `bb325096` (this session's merges). Six PRs merged:
#10198, #10183, #10213, #10215, #10219, #10220, #10221, #10222.

## Files and Systems Inspected
- `scripts/ops/fetch_backtest_candles.py`, `ml/datasets/adapters/yfinance_offvm.py`
- `scripts/research/{bracket_reachability_audit,e35_bracket_geometry_sweep}.py`
- `docs/research/e35-bracket-corpus.jsonl` (2204 rows)
- `src/units/strategies/trend_donchian.py`, `src/runtime/target_expectation.py`
- `config/strategies.yaml` (READ only), `scripts/ops/fetch_dukascopy_ohlcv.py`

## Work Completed
1. **Non-crypto candle lane PROVEN on a runner** (was implemented-but-unverified).
   Daily: 251 rows / 364 d of 365. Intraday: 3457 rows / 727 d of 1000.
2. **Two of my own diagnostic defects, caught by the proof and fixed.**
   - `yfinance fetch failed: No module named 'yaml'` blamed a fetch that never
     happened → three stage types (`YfDependencyMissing`/`YfRefused`/
     `YfFetchFailed`), only the last permitted to say "fetch failed".
   - `"the span WILL be truncated"` predicted an outcome never verified. yfinance
     **REFUSES** an over-long intraday request (zero rows), it does not clip →
     the lane now CLAMPS and says so.
3. **Ticker map decoupled** from the `ml.datasets` package (its `__init__` pulls
   14 dataset builders and `yaml` just to read ticker strings) into the
   import-free `yf_symbols.py`, loaded by file path.
4. **`tp_r > cap_r` warn shipped** (operator: *warn, don't refuse*). Found 2 of
   the 4 real targets already cosmetic.
5. **A false claim of mine, corrected** — see Contradictions below.
6. **Dukascopy coverage probe** built, run, and then FIXED after it got 2 of 18
   wrong in opposite directions.
7. **`pullback_frac` cross-leg population established** before any sweep.

## Validation Performed
- Runner proofs asserting a real BOOK (rows>0, schema, high>=low, no NaN closes),
  not a green exit: runs `32734190004`, `32735954272`.
- Positive controls on every new guard: each new assertion was shown to FAIL
  against the pre-fix code and pass against the fix.
- Decoupling verified by blocking `yaml` at the import hook and asserting the
  ABSENCE of `ml.datasets` from `sys.modules` — not merely that the load worked.
- Probe adjudication verified offline against run 1's real output.
- `run_guards.py` clean; `check_canonical_doc_coherence.py` all checks passed.

## Documentation Updated
- `docs/research/bracket-target-reachability-2026-08-24.md` §7 + §8 (struck,
  not deleted — the record of what was believed is kept)
- `docs/research/RESEARCH-CAPABILITY-INDEX.md` (2 new tools + the correction)
- `docs/github-actions-workflows.md` (2 new workflows)

## Contradictions or Drift Found
**A claim I landed earlier the same day was FALSE, in the dangerous direction.**
#10219 recorded *"the live `atr_stop_mult: 2.5` is absent from the joint grid
entirely"*. It is not. `e35_bracket_geometry_sweep` builds its stop axis as
`(None,) + STOP_MULT_GRID`, where `None` = run at the harness base (2.5), and
`cell_tag` omits the `sm` token for those cells. `bracket_reachability_audit`
then did `if stop_mult is None: continue`, dropping **390 of 2204** rows — every
cell at the live stop — and reported its own filter's absence as a property of
the grid. Sub-class **C**. Fixed; population 1540 → 1928.
⚠️ **The two axes moved differently:** truncation gained 153 truncated cells at
the live stop; the cosmetic **49/308 headline is UNCHANGED**. Stated rather than
left to be inferred from the population jump.

## Risks and Follow-Ups
- **Bybit's cap binds non-Bybit venues.** `_TP_SENTINEL_CAP_PCT` is applied in
  the strategy signal builder (`trend_donchian.py:388`) BEFORE the account is
  known, so a cap named for Bybit ErrCode 10001 also clamps prop tickets.
  **Tier-3, order geometry — reported, not touched.**
- The 7 still-unmatched Dukascopy symbols should be re-probed with the FIXED
  matcher before that list is read as absence.
- MES/MGC have no same-ticker Dukascopy instrument; any use would be a PROXY —
  a different instrument that must stay labelled as one.

## Deferred Items
- **The `pullback_frac` sweep harness does not exist** (`m21_entry_sweep` is
  donchian entry-filters). Building it was not authorised and was not done.
- **The no-target book at stop 2.5** (~1 run per leg) — the single missing input
  that would unlock a cosmetic verdict for all 388 base-stop cells. Replaces the
  withdrawn "add 2.5 to STOP_MULT_GRID", which would have produced duplicate
  tags (`axis_of` marks such cells `"none"` → `inert_equals_base`, never run).

## Next Recommended Sprint
Run the no-target baseline (~9-11 runs), re-probe Dukascopy with the fixed
matcher, then build the `pullback_frac` sweep against the 15-leg full-history
stratum.

## Wrap-Up Check
- ⚠️ **`ROADMAP.md` deliberately NOT updated.** A `/system-review` session was
  running concurrently and owns that file per the coordination yield posted to
  board #6927. The roadmap-landing request was posted to the board instead of
  racing a concurrent edit. **This is a known, deliberate gap, not an oversight.**
- ⚠️ **No review-backlog rows filed**, for the same reason — the two backlog
  items this session produced were handed to the `/system-review` session on the
  board rather than written into files it is actively draining.
