# D1 cost-stack verification — is the promotion block's substance satisfied?

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Written 2026-09-25 by lane `lane/d1-verify-cost-stack` (session dispatched
from session_01Ljhs6sFAdWHdMDhJpL5aBP).

## Why this exists

`config/mandates.yaml::MD-PROMOTE-S1-S2` (merged #12920, 2026-09-24) carries:

```
blocked_until: >-
  D1 lands -- the backtest harnesses stop defaulting slippage and funding to
  0.0 and the promotion corpus is re-run net of the full cost stack. ...
```

Checklist row **D1** reads `dropped` — killed 2026-09-22 as a duplicate of
**R1**, which reads `done`. A dropped row can never land, so the block as
*written* can never clear, even if its *substance* is already satisfied.
Operator decision 2026-09-25: keep the mandate blocked, verify the substance
first, then propose replacement text. This doc is that verification. Nothing
here edits `config/mandates.yaml` — only the operator grants a mandate change.

Population for every count below: `git rev-parse HEAD` at the time this
session read the tree, `origin/main` at `05aeeb284` fast-forwarded to the
merge of #12920. Re-run the snippets in place to check any of this again.

---

## 1. Do the harnesses still default slippage and/or funding to 0.0?

**Answer: the CLI paths that back the current promotion corpus do not.
The underlying primitives still do, deliberately, and three harnesses
outside that corpus are not wired to the canonical cost model at all.**

`src/runtime/execution_costs.py` is the single owner of the cost model
(`DEFAULT_FEE_BPS_ROUNDTRIP=7.5`, `DEFAULT_SLIPPAGE_BPS_ROUNDTRIP=5.0`,
`PERP_SLIPPAGE_BPS_ROUNDTRIP=3.0` since E60, `DEFAULT_FUNDING_BPS_PER_WINDOW=1.0`
for perps / `0.0` for non-perps via `is_perp()`). Every path a zero can still
reach:

**(a) `roundtrip_cost_r()`'s own keyword defaults are `slippage_bps_roundtrip=0.0`,
`funding_bps_per_window=0.0`** (`src/runtime/execution_costs.py:246-248`). This
is documented as intentional — "a caller that passes only the fee is
byte-identical to the legacy fee-only term" — but it means any *new* caller
that does not explicitly request `resolve_cost_policy()`'s venue-aware
numbers silently gets zero. The safety property depends entirely on every
caller opting in; nothing stops a future one from not.

**(b) Explicit `--slippage-bps-roundtrip 0` / `--funding-bps-per-window 0`
on any harness CLI** is the documented "fee-only comparison arm" — by design,
not a bug, but a zero that a caller can still produce on purpose (and a
caller who does not notice they did).

**(c) In-process module-level globals default to `0.0`** in every harness
that has one (`scripts/backtest_trend.py:80-81`,
`backtest_pullback.py:69-70`, `backtest_squeeze.py:56-57`,
`backtest_ict_scalp.py:70-71`, `backtest_fvg_range.py:83-84`,
`backtest_chop_scalp.py:68-69`, `backtest_pairs.py:61-63`,
`backtest_fade.py:73-74`, `backtest_funding_carry.py:64`,
`src/backtest/run_backtest_vwap.py:105-106`). `execution_costs.py`'s own
docstring says this is "deliberate and load-bearing" — PR #8468 keeps the
confidence sweep, the ML recorder and the M30 panel bridge byte-identical by
never touching the globals from `run_backtest()` directly. **The CLI's
`main()` overwrites these globals before any run**, resolving an unset flag
through `execution_costs.resolve_cost_policy()` /
`slippage_bps_roundtrip_for()` / `funding_bps_per_window_for()` to the
venue-aware non-zero default (verified by reading each harness's `main()`:
`backtest_trend.py:1206-1209`, `backtest_pullback.py:1128-1131`,
`backtest_squeeze.py:615-618`, `backtest_ict_scalp.py:1170-1173`,
`backtest_fvg_range.py:750-753`, `backtest_chop_scalp.py:708-711`,
`backtest_pairs.py:409-413`, `backtest_fade.py:606-609`,
`backtest_funding_carry.py:407-409`, `run_backtest_vwap.py:1231-1234`). So
**a zero only reaches a `--json` summary here if a caller imports the module
and calls `run_backtest()`/`main()`'s inner function directly, bypassing
`main()`** — no such caller was found feeding `comms/strategy_evidence/`.
`scripts/research/regime_debt_matrix.py::build_harness_cmd()` (the function
that constructs the command line `build_strategy_evidence.py` and the
evidence corpus run through) passes **no** `--slippage`/`--funding` flags at
all, so every corpus run resolves through the CLI's venue-aware default.

**(d) Three harnesses have no wiring to `execution_costs` at all** — a leg
routed through one of these is undercosted with no warning, and this is
*wider than D1's original note*, which named two of the three:

- `scripts/backtest_orb.py` — fee only, in **points** not bps
  (`FEE_POINTS_ROUNDTRIP`), no slippage term, no funding term anywhere in
  the file.
- `src/backtest/backtester.py` — its own separate, hardcoded cost dict
  (`taker_fee_pct=0.055`, `slippage_pct=0.02`) — not zero, but disconnected
  from the canonical venue decisions (E60's 3.0bps figure cannot reach it)
  and has no funding term.
- `scripts/backtest_xsec_momentum.py` — slippage IS wired (`args.slippage_bps_roundtrip`
  resolves through `execution_costs.slippage_bps_roundtrip_for(None)` at
  `backtest_xsec_momentum.py:817-818`), but there is **no funding term
  anywhere in the file** — a held perp leg in the cross-sectional book pays
  no funding in this harness.
- **New finding, not in D1's or R1's note:** `scripts/backtest_vol_target.py`
  has only a turnover-bps drag (`_turnover_drag`, `turnover_bps` CLI flag) —
  no fee/slippage/funding split via `execution_costs` at all. Checked
  against the current 52-leg evidence corpus below: no record's
  `cost_stack.source` points at a vol_target run, so this harness does not
  currently back any scored leg — filed as a coverage gap, not a live
  defect (see § 4).

None of (a)–(d) are new discoveries beyond what R1's note already scoped for
the 12 risk-bearing legs; (d)'s `backtest_vol_target.py` item is new.

---

## 2. `comms/strategy_evidence/*.json` — cost-stack coverage and staleness, by venue

Read all 52 files this session (`git rev-parse HEAD` above). Venue
classified from `symbol`: `*USDT` → Bybit perp, `{MES,MGC,MHG}` → IBKR
futures, everything else → Alpaca equity/ETF.

| venue | records | carry a full cost_stack | at the venue-DECIDED slippage | at the flat/provisional 5.0bps |
|---|---|---|---|---|
| Bybit perp (USDT) | 28 | 27 (1 `no_harness`: `turtle_soup`) | **3** (3.0bps, generated 2026-09-24T17:22–17:23Z, run-dir `2026-09-24-e69`, *after* E60 merged 15:07:47Z) | **24** (still 5.0bps, generated 2026-09-22 or 2026-09-24T12:41–12:50Z — *before* E60 merged) |
| Alpaca equity/ETF | 19 | 18 (1 `harness_failed`: `splg_trend_long_1d`) | n/a — no venue-specific number has been decided yet | 18 (5.0bps — the *provisional* basis, operator 2026-09-24: "a working artifact until we get evidence backed numbers") |
| IBKR futures (MES/MGC/MHG) | 5 | 4 (1 `harness_failed`: `ict_scalp_mgc_15m`) | n/a — same as above | 4 (5.0bps, same provisional basis) |
| **total** | **52** | **49** | **3** | **46** |

49/52 matches D3's note exactly (52 − 2 `harness_failed` − 1 `no_harness` =
49). **Not previously stated this precisely: of those 49, only 3 carry the
operator's actual decided number for their venue; the other 46 are either
stale against a decision that has since landed (24 perp records) or
carrying a number nobody has measured yet (22 equity/futures records).**

The 24-stale-perp-record figure is **not a new finding** — it is the live
`state: queued` pipeline item `PI-20260924-JCVFRWEA-0001`, filed the day E60
merged, which named 27 re-priceable perp records at the time (`turtle_soup`
excluded as `no_harness`). Three of those 27 have since been regenerated
(`trend_donchian_ada_4h`, `trend_donchian_avax_4h`, `trend_donchian_sol_4h`,
all in run-dir `2026-09-24-e69`, ~2h after E60 merged) — this session
re-measured the count at **24 remaining**, not 27; the pipeline item's own
`what` field is being extended (not replaced) to record that, per its
append-only-elaboration contract (`scripts/ops/pipeline.py::_identity_mismatches`).

**Directionally important**: the 24 stale perp records carry the *higher*
pre-decision default (5.0bps) against the *lower* decided figure (3.0bps).
A record priced at 5.0bps is charging a perp leg **more** round-trip cost
than the operator's own measurement supports — the staleness is in the
conservative direction, not the "optimistic by an unknown amount" direction
the original D1/blocked_until language was written to guard against (that
language was written when the number in question was **0.0**, not **5.0
vs. 3.0**). This distinguishes "stale" from "dangerous" for this specific
population — see § 3.

---

## 3. Is D1's substance satisfied?

**Partially. The danger D1 was written to catch — a silently zeroed cost
term producing a fee-only, optimistically-biased verdict — is fixed for
every harness path that currently backs the 52-record evidence corpus** (§ 1
(a)-(c)). That is real, verified work: `git show 7373971ed` (E60) and R1's
PR #12705 are both on `main`, and every `cost_stack` in the corpus (49/52
records) carries a nonzero fee + slippage (+ funding where perp) triple, not
a fee-only number.

**What remains, stated as exactly what it is rather than resolved to a row id:**

1. **24 of 27 re-priceable Bybit-perp records are stale against E60's own
   decided figure** (3.0bps) — mechanical, no new measurement needed, and
   the pipeline item that tracks it (`PI-20260924-JCVFRWEA-0001`) is still
   `queued`. Not dangerous (§ 2, directional note), but inconsistent: a
   record's `decision_rule.verdict` was computed against a slippage number
   the operator has since superseded.
2. **All 22 Alpaca-equity/IBKR-futures records carry a provisional, never
   -measured 5.0bps** — E62 (the lane tasked with replacing it) is
   `landed_unproven`: its structural fix (#12878, resolving `sl_cross` exit
   price from the fills store) is merged and deployed, but its own
   done-when ("the first post-deploy Alpaca sl-family close carries
   `exit_price_source=exchange_fill`") has not yet been observed. So there
   is still no real-fill number to compare non-perp legs against — the
   mandate's own `never` clause already refuses to promote an
   equities/futures leg whose record modelled *less* than 5.0bps, which
   covers the immediate risk, but the *cost-tolerance* clause
   (`bar.cost_tolerance_bps: 0.0`, "realized may not exceed modelled") has
   no realized number to check on these venues at all yet.
3. **R1 explicitly scoped itself to the 12 legs already on a risk-bearing
   account roster** (its own note: "the remaining ~43 of the full 55-leg
   live roster ... were NOT re-run this session"). `MD-PROMOTE-S1-S2` is not
   restricted to those 12 — it fires for *any* leg already on a Stage-1 soak
   roster. A Stage-1 leg outside the 12 that has never been re-run under a
   cost stack at all is not covered by R1's population, only by whatever
   `build_strategy_evidence.py`'s broader run already produced (49/52
   records do have *a* cost_stack, so this is narrower than it sounds — but
   the *freshness* guarantee in point 1 only applies to the subset that has
   actually been checked, i.e. none of them have been checked against
   "does this match the currently decided number" except by this session).
4. **Three harnesses remain unwired to the canonical cost policy** (§ 1(d)).
   None currently back a scored leg in the corpus, so this is a latent gap,
   not a live one — but nothing stops a future leg from routing through
   `backtest_orb.py` or `backtester.py` and landing in the corpus
   undercosted with no guard catching it: `scripts/ci/check_roster_promotion_evidence.py`'s
   clause C3 checks that a `cost_stack` is *present and resolved*, not that
   its `slippage` matches `execution_costs.slippage_bps_roundtrip_for(symbol)`
   at read time — so a stale OR a hand-authored record with a plausible but
   wrong number would still pass C3 today. This guard gap is filed
   separately (§ 5) rather than fixed in this PR — it is a new CI check, out
   of this lane's verify-and-propose scope.

None of 1–4 reproduce the *specific* failure `blocked_until` names — nothing
found here defaults to 0.0 in the live corpus. But "D1 lands" as literally
written required *the corpus re-run net of the full cost stack*, and that
full re-run, at the currently-decided numbers, has not happened: R1 covered
12/55 legs, and even within R1's own 12, one venue's numbers (the 24 perp
records outside R1's later E60/E69 touch) are stale.

---

## 4. Proposed replacement `blocked_until` text

Not applied — `config/mandates.yaml` is untouched by this PR. Proposed for
the operator to grant or amend:

```yaml
blocked_until: >-
  BOTH of the following are checkable, not this row id. (a) Zero
  comms/strategy_evidence/*.json records for a Bybit-perp leg (symbol
  matching execution_costs.is_perp) carry a cost_stack.slippage that
  differs from execution_costs.slippage_bps_roundtrip_for(symbol) at read
  time -- equivalently, pipeline item PI-20260924-JCVFRWEA-0001 (or
  whatever id supersedes it) reads state done or killed, not queued. TODAY:
  24 of 27 re-priceable perp records still carry the pre-E60 flat 5.0bps
  default against the decided 3.0bps (conservative, not optimistic, but
  unchecked). (b) A committed realized-slippage record exists for Alpaca
  equities and/or IBKR futures -- i.e. checklist row E62's own done-when
  has fired at least once (an Alpaca sl-family close carries
  exit_price_source=exchange_fill) -- so the 5.0bps basis those legs'
  records carry is either confirmed by a measurement or explicitly
  reaffirmed as the working assumption by the operator, not merely
  unexamined. TODAY: E62 is landed_unproven; no such close has been
  observed yet. Until both (a) and (b) hold, MD-PROMOTE-S1-S2's
  cost-tolerance clause (bar.cost_tolerance_bps: 0.0, "realized may not
  exceed modelled") is being evaluated against a modelled number nobody
  has re-checked is still current, on at least one venue in play.
```

This names two checkable artifacts (a pipeline item's `state`, and a
checklist row's `done-when` observation) instead of a checklist row id that
can be `dropped`, and it distinguishes the residual gap (stale-but-safe /
provisional-but-labeled) from the original danger (silently zero) so a
future reader does not re-read "blocked" as "still zero-cost."

---

## 5. Not done in this PR, stated rather than left implied

- **Not regenerating the 24 stale perp records.** `PI-20260924-JCVFRWEA-0001`
  already owns that as a `dispatch_lane` item; re-running it is mechanical
  (no new measurement) but is build-lane work, not this lane's verify-and-
  propose scope, and this lane's ceiling does not cover a 24-leg re-run plus
  verdict-flip review.
- **Not adding a staleness check to `check_roster_promotion_evidence.py`'s
  clause C3.** Real gap (§ 3 point 4), but a new CI guard is code change
  beyond "measure and propose."
- **Not wiring `backtest_orb.py` / `backtester.py` / xsec_momentum's funding
  term / `backtest_vol_target.py` to `execution_costs`.** None currently
  back a scored leg; filed as follow-ups, not fixed here.
- **Not editing `config/mandates.yaml`.** Only the operator grants a mandate
  change — § 4 is a proposal, not an application.
