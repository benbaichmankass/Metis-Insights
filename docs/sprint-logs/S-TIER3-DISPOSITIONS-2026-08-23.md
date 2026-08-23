# S-TIER3-DISPOSITIONS-2026-08-23

## Date Range
2026-08-23 (continuation of the `s4-pairs-control` session; `main` at `72b254f9` at start).

## Objective
Unblock the three CRITICAL rows that were waiting on an operator **decision** rather than on
work, then act on the one decision that produced a shippable change.

## Tier
Tier-1 throughout **except one Tier-3 change shipped under explicit operator approval**:
`eth_pullback_prop_2h` `execution: live → shadow` in `config/strategies.yaml`. No account mode
flipped, no model promoted, no other strategy touched.

## Work Completed

**1. The three Tier-3 dispositions collected and recorded.** Each put to the operator with four
options and the measured evidence; recorded in **both** the backlog rows and the workplan, which
is what those rows resolve on (*"RESOLVED when the operator records a disposition"*).

| # | decision | binding constraint carried with it |
|--:|---|---|
| Crypto pullback family (5 of 6 legs OOS-negative) | **Retune first, then re-decide** | Cells **RE-RUN, not re-read**; each Path-A delta added to its **own leg's base** |
| 22 of 34 open trades price-only | **Declare the 9 first** | Each declared **VALUE** stays Tier-3; **measure** the drop with `exit_path_coverage.py` |
| Scalps held 10–100× horizon | **`ict_scalp` take-profit path first** | Step 1 is **Tier-1, startable now**; max-hold exit deferred, not dropped |

**2. Measured what decision 1 actually risks, rather than leaving it an impression.** The
−14.61R headline **overstates** it: the worst leg (`htf_pullback_trend_2h`) routes only to
`bybit_1`, which is **paper**, as does `sol_pullback_2h`. Two of five touch real money, at
−6.80R and −0.59R.

**3. Found the one leg the headline UNDER-states, and carved it out** (Tier-3, operator-approved).
`eth_pullback_prop_2h` (−11.78R) routes to **`breakout_1`, the PROP account**, where a breach is
**terminal** — $150 daily-loss, $4,700 static-DD floor — not a drawdown a retune recovers.
Demoted to `shadow`; the other five stay live under the retune-first disposition.
⚠️ **This reverses an operator-approved promotion** (shadow → live, 2026-06-25, corrected-swap
evidence). The reversal rests on NEW evidence plus the prop asymmetry, **not** on that promotion
having been wrong.

**4. Two rows filed** — `BL-20260823-STALE-COMMENT-CLAIMS-PROP-VARIANTS-ARE-SHADOW` and
`BL-20260823-ZOMBIE-ROUTINES-ENABLED-WITH-NULL-NEXT-RUN` (operator: backlog, do not action).

## Validation Performed
- **Verified the mechanism at the emission site, not from the docs.** `execute.py`'s prop branch
  returns early on a shadow/dry package with `status='shadow'`, `close_reason='prop_shadow_no_emit'`
  and **never calls `emit_prop_ticket`** — so the package still logs, the soak keeps accruing, and
  only the ticket stops.
- **Asserted the OUTCOME:** `execution_mode('eth_pullback_prop_2h') → shadow` through the real
  registry `coordinator.py:1279` calls; **seven other legs asserted still `live`**.
- `run_guards.py` all relevant guards pass. Two fired first and both were real:
  `dry-run-guard` (resolved via its **sanctioned** inline `# shadow-guard: allow — <reason>`
  marker, which records approval on the line rather than routing around the guard) and
  `strategy-coverage-guard` (matrix regenerated).

## Contradictions or Drift Found
- ⚠️ **`execute.py`'s comment names `trend_donchian_{sol,eth}_prop` as `execution: shadow`; both
  are `live`.** So were all three prop strategies — **nothing exercised the prop shadow branch in
  production**, and this demotion is its **first live exerciser**. I was about to cite that comment
  as precedent for a money-path change. Field beats comment; filed.

## My Own Errors, On The Record
- ⚠️ **An edit silently did nothing while every assert passed.** The first attempt matched
  `execution: live` **inside a comment** in the same block, mangled the comment, and left the real
  gate `live`. Preconditions all asserted true; the file was written. Caught only by re-parsing the
  YAML and checking the outcome. **Verify what changed, not what you intended to change.**
- ⚠️ **A fabricated CI overrun**, retracted: I reported `pytest-run` at "~40+ min against a
  10.1–11.6 min baseline" when it ran **11.73 min**. I inferred elapsed time by summing background
  sleeps I had *issued* (`run_in_background` returns instantly) and never read a clock. Filed as
  `BL-20260822-ELAPSED-TIME-INFERRED-FROM-ISSUED-SLEEPS-NOT-A-CLOCK`. ⚠️ **I then dated these very
  dispositions seven hours wrong, one commit after filing that row** — filing a lesson does not
  apply it.
- ⚠️ **An over-correction:** I told the operator I had said "nine PRs" when the count was eight.
  Checked: "Nine" was **accurate**; the miscount was an earlier "Seven" over a list of eight SHAs.
  I invented an error while confessing to one.

## Risks and Follow-Ups
- **The demotion is the prop shadow branch's first production exerciser** — worth a look on the
  next review that the leg logs `status='shadow'` packages and emits **zero** tickets.
- The prop venue is a **manual-ticket bridge** and the operator gates each ticket, so nothing was
  auto-executing; the "terminal breach" framing was sharper than the live risk warranted.
- The gate is per-**STRATEGY**, so this leg's paper routing on `bybit_1` goes shadow too.

## Next Recommended Sprint
`BL-20260818-IB-BROKER-PNL-READER-HAS-NO-CALLER` (Tier-2 — read workplan item 1.8 first, same
rows), then the three newly-unblocked lanes, then the yfinance feed (**prove it on a runner**).
