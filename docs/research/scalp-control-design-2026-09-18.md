# The scalp family's weak control is a property of the FAMILY — no arm-vs-arm control can be independent

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> MI-318 · `2026-09-18` · answers the residual MI-312 left ([`scalp-family-target-arms-2026-09-18.md`](scalp-family-target-arms-2026-09-18.md), #12515) · successor to MI-317 ([`per-leg-target-geometry-packet-2026-09-18.md`](per-leg-target-geometry-packet-2026-09-18.md), #12526)

**⚠️ COMMISSIONS NOTHING. No sweep, no harness run, no re-measurement.** This is a BOUND computed over
one already-committed artifact. MI-307's memo records that re-measuring is the failure mode
`OI-20260906` exists to stop, and the cheapest way to get this question wrong would be to answer it
by running something.

---

## 0. The answer in one paragraph

MI-312 asked, and did not answer, whether a **stronger control** exists for the non-clamping
(`ict_scalp` / `fvg_range`) family — its own control passes on 8 of 8 legs within 0.71 pp while being
**99.71% a quantity compared with itself**. The answer is **no, and the reason is not MI-312's
design**: on this family the two arms share **99.00%–100.00% of their entries** (mean 99.55%,
n = 7,342 joined pairs over 8 legs), *at the largest perturbation a target change can possibly make* —
deleting the target outright. Since every candidate target's exit time is provably sandwiched between
the live arm's and the target-removed arm's, **the entry divergence any alternative target could
produce is bounded by a divergence already measured at ≤ 1.00%.** So the obvious "stronger" design —
predict the hit-rate at a target the live arm never ran, then run that arm — would be a **second
near-tautology**, not an improvement. The weakness is a property of the family's exit mechanics, and
a genuinely falsifiable control has to predict something the MFE distribution **underdetermines**.

---

## 1. Why this is a bound and not a measurement

**The argument, stated before the numbers so it can be checked rather than trusted.**

1. **`no_tp` is the largest perturbation available.** You cannot change a target more than by
   deleting it.
2. **Exit time is monotone in target distance.** With `other` the first stop / break-even / timeout
   exit, a trade's exit is `exit(T) = min(first touch of T, other)`. So for any two targets
   `T_near ≤ T_far`: **`exit(T_near) ≤ exit(T_far) ≤ exit(no_tp) = other`**. Every candidate
   target's exit therefore sits *between* the live arm's and the `no_tp` arm's, per trade.
3. **Entries advance through `next_eligible_idx`**, a function of exit time, so the entry sequence
   under any candidate target is likewise sandwiched between the two measured arms.
4. **Hence the entry-set divergence any candidate target can produce is bounded by the divergence
   already measured between `live` and `no_tp`.**

⚠️ **STEP 4 IS A BOUNDING ARGUMENT, NOT A PROOF OF SET-OVERLAP MONOTONICITY, AND SAYING OTHERWISE
WOULD BE OVERCLAIMING.** Exit times are provably sandwiched (step 2). That entry-SET overlap is
therefore monotone in target distance is the natural reading and is **not** formally derived here —
the entry sequence is a recursion, and a recursion whose steps are individually bounded need not have
monotone set-overlap at every index. **What is measured, and needs no such caveat, is the extreme
case:** at the largest perturbation available the two books still share essentially all their
entries. That is the load-bearing half, and it is in § 2.

---

## 2. The measurement

**POPULATION: all 8 legs in MI-312's committed artifact `mi312-scalp-target-arms-2026-09-18.json`
(7 live `ict_scalp` legs + 1 `execution: shadow` `fvg_range_15m`), `2021-01-01 → 2026-09-16`,
config-exact per leg. `entry_overlap` is the live arm against the `no_tp` arm.**

| leg | n live | n no_tp | joined pairs | `entry_overlap` | independent-content ceiling | control Δpp | state |
|---|---:|---:|---:|---:|---:|---:|---|
| `ict_scalp_avax_5m` | 1670 | 1664 | 1664 | 0.9952 | 0.48% | -0.44 | `cannot_be_independent` |
| `ict_scalp_sol_5m` | 1472 | 1471 | 1471 | 0.9993 | 0.07% | -0.36 | `cannot_be_independent` |
| `ict_scalp_xrp_5m` | 1217 | 1210 | 1210 | 0.9942 | 0.58% | -0.47 | `cannot_be_independent` |
| `ict_scalp_5m` | 1137 | 1135 | 1135 | 0.9982 | 0.18% | -0.48 | `cannot_be_independent` |
| `ict_scalp_sol_15m` | 667 | 663 | 663 | 0.9940 | 0.60% | -0.71 | `cannot_be_independent` |
| `ict_scalp_eth_15m` | 601 | 597 | 597 | 0.9900 | 1.00% | +0.03 | `cannot_be_independent` |
| `ict_scalp_xrp_15m` | 557 | 557 | 557 | 0.9928 | 0.72% | -0.54 | `cannot_be_independent` |
| `fvg_range_15m` | 45 | 45 | 45 | 1.0000 | 0.00% | +0.00 | `cannot_be_independent` |

| | value |
|---|---|
| `entry_overlap` across the 8 legs | **min 0.9900 · max 1.0000 · mean 0.9955** |
| **independent-content ceiling** (worst leg, `1 − entry_overlap`) | **1.00%** |
| joined pairs | 7,342 |
| MI-312's **observed** per-trade disagreement (§ 2.1, cited) | **21 of 7,338 = 0.29%** |
| verdict | `arm_vs_arm_cannot_be_independent` — 8 of 8 |

**The two numbers are consistent, and that consistency is the check.** The ceiling (1.00%) must lie
*above* the observed disagreement (0.29%) or one of them would be wrong; it does. The bound is not
tight — 0.29% is roughly a third of it — because not every non-shared entry produces a disagreement.

⚠️ **THE 0.29% IS CITED, NOT RECOMPUTED, AND THE INSTRUMENT SAYS SO IN ITS OWN OUTPUT.** It was
measured from MI-312's harness emits under `runtime_logs/mi312/`, which are **gitignored**. MI-312
discloses this and gives the reproduce command, so it is not hidden — but *disclosed* is not
*re-readable*, and it means the mechanical explanation behind the weak control rests on data no later
session can inspect without a multi-hour re-run. Recorded in § 5.

⚠️ **NO VERDICT HERE TURNS ON THE THRESHOLD.** `INDEPENDENCE_FLOOR = 0.95` is a **chosen** value, and
the *minimum* observed overlap clears it by 0.04 — a selftest asserts that margin precisely so this
cannot quietly become a threshold artifact. At any floor between 0.90 and 0.99 the verdict is
identical on all 8 legs.

⚠️ **`fvg_range_15m` IS n=45 AND `entry_overlap` 1.0000 — the emptiest row in the table.** It is
reported for completeness and must not be pooled with the scalp legs; MI-312 says the same of it.

---

## 3. What follows — and what does NOT

**The obvious "stronger" control is refuted before it is built.** Predicting the hit-rate at a target
the live arm never ran (say 2.5R) and then running that arm would produce two books sharing **at
least** 99.00% of their entries, because that arm is sandwiched between the two already measured.
It would pass, tightly, and mean no more than MI-312's does. **Building it would spend a harness run
to manufacture a second near-tautology** — which is exactly the shape `CY-20260906-TRADING-TRUTH`
calls worse than no instrument, because it gets acted on.

**The mechanism is already established and is not re-derived here.** MI-312 measured why: `sl_hit` is
essentially unchanged across arms (263→264, 383→385, 294→294, 432→432 on the four 5m legs) because
removing a target never changes a loser; the `tp_hit` population redistributes into `timeout` and
`be_stop`; and mean hold lengthens only **~2.5 bars** (18.0→20.7). **The break-even ratchet, armed at
1R, catches the reversal almost as fast as the target did**, so `next_eligible_idx` barely moves.
MI-307's legs are *trailing* strategies where removing the cap genuinely re-partitions the book; this
family is not.

**So a falsifiable control must predict something the MFE distribution UNDERDETERMINES.** A
max-excursion distribution fixes *whether a level was reached*; it does not fix **which mechanism ended
the trade** or **when**. Two candidates, named with what would validate each:

| candidate | why it can fail | what it needs |
|---|---|---|
| **exit-mix** — predict the `be_stop` / `timeout` split at a counterfactual target | the split depends on the PATH after the peak, which `mfe_r` does not encode; a wrong model of the ratchet shows up immediately | per-trade exit reasons from both arms |
| **hold-time distribution** — predict the shift in bars-held | likewise path-dependent; MI-312's ~2.5-bar shift is a single pooled number, not a distribution | per-trade entry/exit indices |

⚠️ **BOTH NEED THE EMITS, WHICH ARE NOT COMMITTED (§ 5). THIS UNIT THEREFORE SPECIFIES THEM AND DOES
NOT RUN THEM** — running one is a decision about spending a harness cycle, and slipping it inside a
unit whose queued question was *"does a stronger control exist"* would answer a question nobody asked
while skipping the one they did.

⚠️ **THIS DOES NOT SAY MI-312 IS WRONG, and reading it that way inverts the finding.** Its control is
real, it is measured, and it was the first one possible on this family at all — MI-307 could not run
one. What this establishes is that **it could not have been made stronger by choosing a different
comparison arm**, which converts an apparent shortcoming into a structural fact and removes the
temptation to "fix" it.

⚠️ **AND IT SAYS NOTHING ABOUT THE DISTRIBUTION'S VALIDITY.** The p80/p90 figures MI-312 published,
and MI-317 § 3.A carried into the operator packet, are untouched. A weak *control* bounds how much
the instrument was *tested*, not how wrong it is.

---

## 4. What this does not establish

- **Not a P&L claim, and not a target proposal.** No `tp_at_r` value is proposed; that stays Tier-3
  and is carried by `OI-20260918-THE-TARGET-GEOMETRY-PACKET-EXISTS-AND-ITS-THREE-DECISIONS-ARE-UNANSWERED`.
- **Not a proof of set-overlap monotonicity** — § 1, step 4, stated as a bound.
- **Not applicable beyond the 8 legs.** MI-307's 19 clamping-family legs are *trailing* strategies
  whose arms genuinely differ (e.g. `sol_pullback_2h`: 200 vs 277 trades), which is precisely why its
  3.2 pp control means more than MI-312's 0.71 pp. **Do not carry this verdict onto them.**
- **Not a measurement of anything new.** Every figure is recomputed from a committed artifact, except
  the one explicitly marked as cited.

---

## 5. Filed, not fixed

- **`BL-20260918-MI312-EXIT-MIX-EVIDENCE-IS-GITIGNORED-SO-ITS-MECHANISM-CLAIM-CANNOT-BE-RE-READ`** —
  MI-312's per-leg artifact carries quantiles, reach curves, `entry_overlap` and the target basis, but
  **no exit-mix**. The `tp_hit 383→0 / timeout 786→1082 / be_stop 69→150` figures that explain *why*
  the control is weak live only in `runtime_logs/mi312/*.jsonl`, which is gitignored. Disclosed by
  that memo, and still not re-readable — and it is the exact data both stronger-control candidates in
  § 3 would need.

---

## 6. Reproduce

```bash
python3 scripts/research/mi318_scalp_control_bound.py --selftest   # 15 checks, 4 negative controls
python3 scripts/research/mi318_scalp_control_bound.py \
    --write docs/research/mi318-scalp-control-bound-2026-09-18.json
```

Reads only `docs/research/mi312-scalp-target-arms-2026-09-18.json`, which is on `main`. No network,
no harness, no candles. Artifact:
[`mi318-scalp-control-bound-2026-09-18.json`](mi318-scalp-control-bound-2026-09-18.json).
