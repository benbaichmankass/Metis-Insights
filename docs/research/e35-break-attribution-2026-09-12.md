> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

# The 2026-08-30 break: the family-matched control does NOT degrade, and that points at e35

**Session** `session_013iSqp4LsU1eq8K326eUtgj` (OPS lane, MI-279) · **row**
`BL-20260911-A-DATED-REGIME-BREAK-ON-2026-08-30-COLLAPSED-THE-DIRECTIONAL-LEGS-WIN-RATE-AND-NOBODY-NOTICED-FOR-TWO-WEEKS`

**Verdict: e35 is IMPLICATED and NOT ESTABLISHED. The row does not clear.** What
*is* established is narrower and more useful than either candidate: the "it was
the market / the trend family" explanation **is refuted for the e35 arm** by a
control the previous measurement did not build.

Re-run it: `python3 scripts/research/e35_break_attribution.py --journal <pull>`.

## The measurement

**Population** — the 1000 rows `/api/diag/journal?table=trades` returns (ids
4653..5652, `created_at` 2026-08-17T19:06Z → 2026-09-12T06:02Z), read
2026-09-12T~10:2xZ. **n = 204 graded**, and every exclusion is censused by name
rather than silently applied: `not_closed` 403, `pairs_sleeve_exonerated` 273,
`fabricated_close` 50 (`exit_reason == netting_attributed`), `off_venue` 38
(non-Bybit), `pnl_null` 32.

Split on **`created_at`**, never `closed_at` — bracket geometry is fixed at
**entry**, so a trade opened before the deploy carries the *old* geometry however
late it closes. That is the sharpening `OI-20260830-E35-GEOMETRY-...` had to make
to its own clause (b) after a trade satisfied the letter and proved the wrong
thing.

| arm | n pre | win pre | n post | win post | Δ |
|---|--:|--:|--:|--:|--:|
| **e35** (the 9 legs `892c9a2c` changed) | 25 | **60.0%** | 20 | **5.0%** | **−55.0pp** |
| **control, same family** (trend/pullback, untouched by e35) | 6 | 16.7% | 24 | **20.8%** | +4.2pp |
| control, other (scalp etc.) | 58 | 53.4% | 71 | 32.4% | −21.1pp |

## Why the third arm is the whole point

A two-arm split — e35 against everything else — **cannot separate** *"the geometry
broke these legs"* from *"the trend family broke, and all nine e35 legs are trend
legs."* All nine are trend/pullback; the rest of the Bybit book is dominated by
`ict_scalp_*`. So the obvious comparison is confounded by family.

Venue was ruled out first: restricting both arms to Bybit changes the e35 and
broad-control numbers **not at all** (−55.0pp / −20.5pp either way), so *"crypto
degraded more than equities"* is not the story.

**Then the family-matched control:** trend/pullback legs on the same venue,
untouched by e35 — `trend_donchian_eth` (8 post closes), `trend_donchian_sol` (3),
`xrp_pullback_2h` (6), `eth_pullback_2h` (6), `sol_pullback_2h` (1). **It did not
degrade.** In the same window, same family, same venue, it sits at **20.8%** while
the e35 arm sits at **5.0%**.

**One-sided binomial, stated basis:** P(≤1 win in 20 | p = the control's own post
rate 0.208) = **0.0586**.

## What this does and does not license

- **There IS a broad common cause.** The scalp/other arm fell −21.1pp (53.4% →
  32.4%, n=58→71). The row's *"the market chopped"* candidate is **not refuted**
  and explains most of the book.
- **e35 degraded far beyond it, and beyond its own family.** A 4× gap against a
  family-matched control in the same window is the discriminating fact the row
  asked for.
- ⚠️ **p = 0.059 is not significance, and n = 20 is not a population.** The e35
  post arm is **1 winner in 20**; the family control's *pre* arm is **n = 6**,
  which is why the reported statistic is a **post-period level comparison** and
  not a difference of deltas. Anchoring a Δ on 6 trades would be the low-n
  failure this repo keeps paying for.
- ⚠️ **The collapse is broad across the e35 legs, not one leg**, which argues
  against a single bad cell: `trend_donchian_eth_4h` 6/9 → 0/2,
  `trend_donchian_sol_4h` 4/4 → 0/2, `trend_donchian_ada_4h` 3/3 → 0/1,
  `trend_donchian` 1/5 → 0/6, `trend_donchian_xrp_4h` 0/3 post. Six of the seven
  legs with post trades have **zero** winners. Per-leg n is 1–6, so this is a
  shape, not seven results.
- **PnL is deliberately not reported.** The book mixes a futures multiplier,
  paper and real money, and ~200 closed rows carry manufactured provenance, so a
  PnL sum is dominated by whichever arm holds the `ib_paper` rows. Win rate is
  the robust statistic at this n. A session wanting money must filter through
  `src.runtime.provenance` first.

## What would settle it, and what must not be done meanwhile

**Settle it by re-running this script when the e35 arm reaches n ≈ 60 post
closes** (roughly three more weeks at the observed rate), or by widening the
journal pull beyond 1000 rows so the pre-window is not truncated. The arms, the
exclusions and the statistic are already fixed in code, so the next session
changes nothing but the input — which is the point of shipping it as a script.

⚠️ **Reverting e35 is Tier-3 and is NOT proposed here.** Two reasons, and the
second is the one that matters: p ≈ 0.06 at n = 20 does not carry a real-money
parameter change; and the row itself says reverting *alone* resolves nothing,
because without the control we never learn whether it was the cause and
`ict_scalp_*` stays unexplained — and `ict_scalp_*` is in the arm that **did**
degrade for a reason nobody has established.
