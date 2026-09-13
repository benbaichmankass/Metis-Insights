# MI-278 U21 — a fan-out package's exit, declared once: the DiD is a band of +3.8 to +29.6pp, and 10 of the 12 self-contradicting packages are in the control arm

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> **Unit:** MI-278 U21 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Advances, and deliberately does NOT close,** `BL-20260912-WHICH-FANOUT-ROW-IS-ROWS-0-MOVES-THE-STOP-OUT-ADJUDICATION-AND-SWINGS-THE-HEADLINE-DID-BY-10-POINTS`.
> **Tier-1.** One new `scripts/research/` module, read-only over a journal pull. No `src/`, no config, no order path, nothing enacted on the fleet.

---

## 0. The answer, in six sentences

An order package's sibling `trades` rows are one price path across several accounts and they do not close together, so `adjudicate_exit` — which grades ONE row — lets input order decide whether a package counts as a stop-out. **The rule this unit declares is that you do not select a row at all:** the unit is the package, its verdict is `unanimous` / `disagreement` / `ungradeable`, and a disagreement is reported rather than arbitrated, which removes input order from the answer by construction instead of pinning a sort in one caller. Re-stating MI-278 U15's difference-in-differences under that rule — importing **U15's own arm splitter unmodified**, so the population and era split are its choices and only the unit rule varies — gives a band of **+3.8pp to +29.6pp**, whose sign survives every way of resolving the disagreements. **That band contains all three of U15's published point estimates (17.9 / 21.2 / 28.2pp) and is wider than their spread**, because it also absorbs the contradictions each point estimate silently resolved. **Twelve packages contradict themselves and ten of them are in the control arm** — which is the mechanism behind U15's observation that the treated arm looked stable while the whole swing came through the control. And an unplanned finding: running **U15's own code, unmodified, against a journal pull taken today** reproduces every cell except one, `control_pre` stop-outs reading **30** where the memo published **31**.

---

## 1. What was undefined

`BL-20260912-WHICH-FANOUT-ROW-IS-ROWS-0-MOVES-THE-STOP-OUT-ADJUDICATION-AND-SWINGS-THE-HEADLINE-DID-BY-10-POINTS` measured a published DiD moving across three defensible row-selection rules on identical data:

| | population order | earliest close | latest close |
|---|---|---|---|
| all stop-outs | 21.2pp | **28.2pp** | 17.9pp |
| declared-only | 7.6pp | 13.2pp | **4.3pp** |

⚠️ **Population order is not a choice anybody made.** It is whatever `/api/diag/journal` returned — id-DESC today. A paginated read, a different pull order or a re-sorted input moves a published causal statistic with no data change and nothing to notice.

The row is explicit that **sorting in one caller closes nothing** (two callers already sort, differently) and that **picking whichever rule gives the nicest number closes nothing** either.

---

## 2. The rule, and the argument for it

**The unit is the PACKAGE. No row is selected.**

`package_exit_verdict` grades every sibling and returns one of three states, never collapsed:

| verdict | meaning |
|---|---|
| `unanimous` | every gradeable sibling agrees — the package has that exit |
| `disagreement` | they were graded and they contradict each other — **reported, never arbitrated** |
| `ungradeable` | no sibling could be graded at all — *we could not look* |

`disagreement` is deliberately not a kind of `ungradeable`. Ungradeable is a gap; disagreement is a finding, and the two want different responses.

**Why not simply pick a rule.** Earliest, latest and "the row the strategy manages" are all defensible, and choosing between them needs an argument about what a package's exit *is* — when the first leg closes, when the last does, or when the managed leg does. This unit does not pretend that argument is settled. What it refuses is the status quo, in which the answer is decided by the order rows arrived in. Not selecting a row is available *now*, is order-invariant by construction, and does not foreclose that argument later.

**A rate then comes back as a band.** `did_band` counts every disagreement both ways — `rate_low` treats them all as non-stop-outs, `rate_high` as stop-outs — and pairs the four cells against each other at the extremes. This is the same idiom the e35 sign-survival test already uses.

⚠️ **The band covers disagreements ONLY.** Packages excluded as `ungradeable` leave the denominator, and that exclusion can carry its own selection effect. The counts ride in the output (`ungradeable_outside_band`) so the exclusion is never invisible; bounding it needs a different instrument and this is not one.

⚠️ **`did_band` is a real FOUR-cell difference-in-differences.** An earlier draft of it took two arms, computed `treated − control`, and called that a DiD — the label naming a quantity the code did not compute, which is this repo's unprovenanced-diagnostic-output sub-class A, committed inside the module written to refuse exactly that. It was caught before shipping by re-reading the function against its own docstring rather than against its tests, which passed. The parameters are now named for their cells so a call site cannot repeat it.

---

## 3. The restatement

**Population, stated:** one `/api/diag/journal?table=trades` pull of **1000 rows** joined to **1000** indexed order packages, `tol = 0.001`. Arms, eras and leg membership come from `stop_integrity_both_arms.build`, **imported unmodified** — so a difference against U15 is attributable to the unit rule and cannot be a different population. Reproduce with:

```
python3 scripts/research/fanout_exit_unit.py --restate-u15 \
    --trades <trades.json> --packages <order_packages.json> --tol 0.001
```

### 3.1 The four cells under the declared rule

| cell | unanimous stop | unanimous other | **disagreement** | ungradeable | denominator | rate band |
|---|---|---|---|---|---|---|
| treated_pre | 2 | 14 | **2** | 0 | 18 | 0.111 – 0.222 |
| treated_post | 8 | 9 | **0** | 0 | 17 | 0.471 – 0.471 |
| control_pre | 28 | 31 | **3** | 0 | 62 | 0.452 – 0.500 |
| control_post | 40 | 24 | **7** | 0 | 71 | 0.563 – 0.662 |

**DiD band = [+3.8pp, +29.6pp] · `sign_survives = True` · `ungradeable_outside_band = 0`.**

Zero ungradeable packages means the band's one stated blind spot is **empty on this population** — which is a property of this week's data, not a guarantee, and is why the field is reported rather than assumed away.

### 3.2 What the band says that a point estimate cannot

It **contains all three** of U15's published points and is wider than their spread. That is the honest reading: each point estimate resolved twelve self-contradicting packages one way and reported a single number, and the spread between the three understates the uncertainty because all three resolve them by the *same* accident of ordering.

⚠️ **The sign surviving is not a significance test.** `sign_survives` invents no alpha and is insensitive to n — it can pass a comparison with no power, which is a finding filed against my own U18 whose backlog id is deliberately NOT quoted here because it rides the unmerged PR #12092 and a reference that does not resolve reads as tracked while being tracked by nobody — link it once that lands. The `treated_pre` cell holds **2** stop-outs in 18 packages. A direction, not an estimate.

### 3.3 The disagreements are concentrated, and that is the mechanism

**12 self-contradicting packages: 2 treated_pre, 0 treated_post, 3 control_pre, 7 control_post — 10 of 12 in the control arm.**

U15 observed that the treated arm was insensitive to row selection (35.9pp under all three rules) while the entire swing came through the control (7.7 / 14.8 / 18.0pp), and could say only that divergent-close packages were "concentrated there". This names the concentration exactly: the control arm holds five times as many self-contradicting packages as the treated arm. A single-arm study reads a stable number and has no way to discover the choice matters.

Measured on the same pull, `spread_verdict` grades **both treated cells `selections_agree_on_this_population_only__closes_nothing`** and **both control cells `selections_disagree__row_selection_is_load_bearing_here`**.

---

## 4. An unplanned finding: U15's own code does not reproduce its own table

Running `stop_integrity_both_arms.build` + `census` **unmodified** against today's pull:

| cell | packages | stop-outs (today) | stop-outs (U15's published table) |
|---|---|---|---|
| treated_pre | 18 | 2 | 2 |
| treated_post | 17 | 8 | 8 |
| control_pre | 62 | **30** | **31** |
| control_post | 71 | 41 | 41 |

Every package count matches exactly; every declared/amended split matches; **one control_pre package changed its verdict.** On a fixed denominator that alone moves the DiD from **+28.2pp to +26.6pp**.

⚠️ **I cannot attribute that flip to row order rather than to a data change, and will not pretend otherwise.** Two causes are consistent with it: (a) a self-contradicting package adjudicated differently because the two pulls ordered its rows differently — and `control_pre` demonstrably holds **3** such packages, so a one-package difference is exactly what that predicts; or (b) a trade whose `exit_price` or bracket was re-stamped between the pulls. Distinguishing them needs U15's original pull, which was not retained. What the observation does establish is weaker and still worth having: **the published table is not reproducible from the current journal, and nothing in either run would have told a reader that.**

---

## 5. What this unit does NOT do

- **It does not close the backlog row.** Its criteria require the analyses to *share* the declaration, and the two known callers are unwired: `stop_integrity_both_arms.py` is imported by this unit rather than converted by it, and `stop_width_counterfactual_2026_09_11.py` is untouched. ⚠️ **The reason for leaving that second one alone changed while this unit was in flight, and the record should show that rather than be tidied.** It was open under PR #12092, so editing it would have guaranteed a conflict; #12092 **merged before this PR did**, taking that reason with it — and the conflict arrived anyway, through the shared `DOCUMENT-INDEX.md` / `RESEARCH-CAPABILITY-INDEX.md` / backlog anchors both changes append to. Wiring both callers is now unblocked and is the follow-up. I would rather say so than let a green PR imply a closed row.
- **It does not settle what a package's exit IS.** It removes input order from the answer; the argument between earliest, latest and the managed leg is still unmade.
- **It does not re-grade MI-271's or MI-275's headlines** — only the DiD that `BL-20260912-WHICH-FANOUT-ROW-IS-ROWS-0-MOVES-THE-STOP-OUT-ADJUDICATION-AND-SWINGS-THE-HEADLINE-DID-BY-10-POINTS` names.
- **It does not touch the three live dashboard routes** MI-278 U12 named. Those are `src/web/`, Tier-2, and changing a number the operator reads is a decision rather than a side effect of a re-derivation.
- **It bounds disagreements, not exclusions.** See §2.

---

## 6. Verification

`python3 scripts/research/fanout_exit_unit.py --self-test` → **54 controls, 0 failures**, no network.

The harness was shown able to fail before its green was believed. Five defects were planted and each was caught by the controls that name it:

| planted defect | controls that fired |
|---|---|
| arbitrate a disagreement by taking the first gradeable row (the status quo) | **3, 4, 5, 9, 27** — including **#9, order-invariance** |
| `earliest_close` silently falls back to population order when `closed_at` is missing | **45, 47** |
| report selection agreement as settling the question | **43** |
| return `treated_post − control_post` while calling it a difference-in-differences | **37, 38** |
| fold `ungradeable` packages back into the denominator | **35** |

⚠️ **Two of those rows exist because planting found the harness was not good enough, and that is worth recording rather than smoothing over.**

- The **mislabelled-DiD** plant initially tripped **nothing**. Every band fixture had both `pre` cells at zero, and at zero the four-cell formula and the two-arm one coincide — so a green harness was consistent with the module committing the exact defect it was written to refuse. Controls **37** and **38** were added with all four cells at distinct non-zero rates; #38 asserts the answer is specifically *not* the two-arm difference.
- The **agreement** plant initially tripped nothing either, but for a different and less interesting reason: the plant itself was ineffective, replacing only the second half of a two-line string and leaving the prefix the control tests. Re-planted properly, control **43** fires. Recorded because "the plant did not work" and "the control is weak" look identical from the failure count alone, and only reading both told them apart.

Two controls exist specifically to prove the underlying defect is real rather than asserted: **#48** shows population order flips when the input list is reversed, and **#49** shows the declared rule does not flip on the same reversal.
