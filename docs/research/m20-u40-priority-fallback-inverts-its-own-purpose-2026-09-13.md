# M20 U40 — the arbitration fallback inverts its own stated purpose, and two uncovered legs are now on real money

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U40** · RESEARCH lane · Tier-1 · works `BL-20260909-UNKNOWN-STRATEGY-PRIORITY-NOW-BEATS-45-OF-50-DECLARED-LEGS-AND-THE-CONTENTION-IS-LIVE`.

Guard: [`scripts/ci/check_priority_fallback_distribution.py`](../../scripts/ci/check_priority_fallback_distribution.py)
(37 self-test controls; 25 pytest controls in
[`tests/test_priority_fallback_distribution.py`](../../tests/test_priority_fallback_distribution.py));
registered in `run_guards.py` as `priority-fallback-distribution`.
Baseline: [`docs/claude/work/PRIORITY-FALLBACK-BASELINE.json`](../claude/work/PRIORITY-FALLBACK-BASELINE.json).

---

## One sentence

**The row's second half — the distribution assertion it calls *"the transferable half"* — is now a CI guard; and the row's own framing that *"the contended accounts are paper-class today"* is stale, because the Option-A roster change put two of the five uncovered legs on the live real-money `alpaca_live` account on 2026-09-10, the day after the row was filed.**

---

## 1. The direction, verified in code rather than assumed

The row's claim depends entirely on whether a higher or lower priority wins. Read, not inferred:

* `intents.py::_election_sort_key` carries `-intent.effective_priority()`;
* the aggregator does `ordered = sorted(non_flat, key=...)` and `winner = ordered[0]` — **ascending**.

So a **higher** `effective_priority` sorts first and **wins**. `effective_priority()` falls back to `DEFAULT_PRIORITIES.get(self.strategy, _UNKNOWN_STRATEGY_PRIORITY)`, so an **absent** leg takes the constant.

## 2. Re-measured 2026-09-13

```
R1 fallback = 10 · 50 mapped legs · min mapped = 0 · 45 sit STRICTLY BELOW it
   histogram {0:41, 1:1, 2:1, 3:1, 5:1, 10:1, 20:1, 30:1, 40:1, 50:1}
R2 44 enabled execution:live legs · 5 absent from DEFAULT_PRIORITIES
   gdx_pullback_1d · iaum_pullback_1d · scha_trend_long_1d · slv_pullback_1d · splg_trend_long_1d
```

The constant's own comment reads *"Picked deliberately below the in-scope strategies so a misconfigured new strategy never silently overrides Turtle Soup / VWAP."* It **is** below the original in-scope legs (20/30/40/50) and **above the 45 that came later**. Field beats comment: the comment describes a distribution that has moved.

**Omission does not merely fail to be safe — it WINS.**

> ⚠️ **My count of live legs is 44, not the row's 45.** One leg's gate changed between 09-09 and today. I state my own denominator rather than carrying the row's.

## 3. What escalated: the paper-class caveat is stale

The row records *"The contended accounts are paper-class today; the mechanism is account-agnostic."*

| account | mode | class | uncovered legs routed |
|---|---|---|---|
| **`alpaca_live`** | live | **`real_money`** | **`iaum_pullback_1d`, `slv_pullback_1d`** |
| `alpaca_paper` | live | paper | all five |
| `alpaca_portfolio` | live | paper | `gdx_pullback_1d`, `slv_pullback_1d` |
| `alpaca_options_paper` | live | paper | `gdx_pullback_1d`, `slv_pullback_1d` |

`slv_pullback_1d` and `iaum_pullback_1d` were added to `alpaca_live` by the **Option-A** roster change (`e88ef612a`, **2026-09-10**) — one day after the row was filed. `exposure_state` exists so this is a reportable state rather than a leg list a reader has to cross-reference.

> ⚠️ **This is exposure, not an observed misrouting.** The row already says so: *"NOT SUFFICIENT: no observed misrouting, since the arbitration outcome is not journalled per-tick in a form that would show it."* Nothing here changes that. And U39 separately establishes that `alpaca_live` currently books every dispatch dry — so a misrouting on that account could not manifest today even if the arbitration produced one.

## 4. Why the guard REPORTS R1/R2 and enforces only R3

R1 and R2 are **violated on the current tree**. Making either a hard failure would red every PR in the repo on day one — the shape `check_pr_queue_watch.py` writes down about `never_ran` (*"failing would red every PR in the repo on merge day, which is how a guard gets disabled instead of fixed"*), and the residue `diagnostic-provenance-guard` had to drain to 0 before its ungated step was survivable. **And the remedy is Tier-3**: an arbitration priority is an order-routing change and the operator decides, so no guard can clear it.

**R3 is what can fail today**: a WORSENING against a committed seed — a new uncovered leg, **or a seeded one moving onto a real-money account**. That second clause matters precisely because it is what Option A did with no new leg appearing.

Both failure shapes were proved **end to end**, not only through the pure function: shrinking the seed → `exit 1`; blanking the real-money seed → `exit 1`; restored → `exit 0`.

> ⚠️ **Seeding is not a disposition.** The five legs are still uncovered and an uncovered leg still wins. The seed is a floor so a regression is detectable without holding the repo hostage to a decision it does not own — the `checklist_routing_age` idiom.
>
> ⚠️ **`--strict` arms R1/R2 and there is no flag to unset.** Once the operator's change lands, the guard is already able to enforce them.
>
> ⚠️ **`improved` is reported and does not fail — and it is the signal to RE-SEED.** A baseline wider than reality stops catching the next regression.

## 5. A deliberate divergence from the row's letter, stated rather than silent

The row asks that **`scripts/check_strategy_coverage.py` gain a fourth invariant**. That file sits at `scripts/` root, outside `check_pr_landing.TIER1_SURFACE` (`scripts/ci/**`, `scripts/ops/**`, `scripts/research/**`, `scripts/reports/**`), so an edit there cannot self-land and would join a human-merge queue that already has three PRs waiting. **What the row needs is that the invariant exists and runs in CI, not which file hosts it.** Both invariants are in the new guard; `check_strategy_coverage.py` is untouched.

## 6. Verification

* **37 self-test controls, 25 pytest controls**, plus two end-to-end R3 failure proofs.
* **11 defects planted; 9 caught by a named control, 2 caught only as an exception.** Named catches include *relax `<` to `<=`* , *drop the `AnnAssign` branch so `DEFAULT_PRIORITIES` vanishes* (3 fired), *count absent legs without naming them*, *`no_baseline` grades `within_baseline`*, *a seeded leg moving onto real money no longer worsens*, *delete the real-money warning*, *render `unknown` as `none`*, and *stop reporting the beaten count*.
* ⚠️ **Two plants — weakening the `ungradeable` guard in `distribution_state` and in `coverage_state` — exit 1 by `ValueError`/`TypeError` rather than by an assertion.** The defect is caught, and loudly, but by Python rather than by a control that says what is wrong. **Recorded as a weaker form of coverage rather than counted as a clean catch**, because a traceback does not tell a reader which invariant broke. That those guards are load-bearing enough to crash without them is itself the evidence they are not decorative.
* ⚠️ **One control was WRONG and a plant found it**: *"a real-money exposure is called out loudly"* keyed on `"REAL MONEY, LIVE"`, which the per-account line already contains — so deleting the explicit warning passed. Now keyed on the warning's own words; the plant fires 2.
* **3 no-op sanity plants fired zero.** Seven plants re-run against the pytest surface: 7/7 caught, no-op green.

## 7. Scope

**No `src/`, no `config/`, no unit file.** `src/runtime/intents.py`, `config/strategies.yaml` and `config/accounts.yaml` are **read only** — the guard parses them with `ast`/`yaml` and never writes. `scripts/ci/run_guards.py` gains one registration. **No priority value is proposed**; that is Tier-3 and is the operator's.

## 8. Standing

`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED` is `loud: true` and unchanged: **17 consecutive losing days, −$38,851.81**, onset 2026-08-27, bounded below by a **+$4,172.64 winning day on 2026-08-26**. U40 does not address it — the affected legs are alpaca ETF legs, not the bybit directional legs in that streak.
