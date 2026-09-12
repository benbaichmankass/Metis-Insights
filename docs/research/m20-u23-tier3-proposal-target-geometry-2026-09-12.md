# MI-278 U23 — the Tier-3 proposal: do not change a target parameter yet, and here are the two arms that would license one

> **Doc status:** `unknown` · category `research` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U23 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **This is the object's `done_condition` deliverable** — *"NOT done when a memo exists — done when a proposal the operator can accept or reject exists."*
> **Tier-1 document proposing a Tier-3 decision.** No `config/`, no `src/`, no parameter changed. **Nothing here is enacted.**

---

## 0. The proposal, in one box

> **PROPOSE: change no take-profit parameter now.** The one lever with enough attributed mass is the take-profit, and the M20 walk-forward evidence base **structurally cannot** grade it — `tp_geometry` is perfectly confounded with `family` and with `block_unit`, and there is no dose.
>
> **PROPOSE INSTEAD: run two named arms** (§4). They are cheap, they are the *only* thing that converts this from an uninterpretable 75%-vs-30% into a decision, and neither touches live money.
>
> **ACCEPT** = authorise those two arms. **REJECT** = say so, and this object closes as *not gradeable on the current evidence*, which is a legitimate outcome and better than the alternative below.
>
> ⚠️ **The alternative is the failure mode this cycle exists to stop.** There is a number sitting right here that *looks* like a mandate — uncapped arms are `candidate` 75% of the time (21 of 28 arms) against capped arms' 30% (66 of 218) — and acting on it would be acting on a family comparison wearing a geometry label.

---

## 1. The brief's premise did not survive its own measurement

M20 was framed as *hold winners longer*: winner hold fell 5.17h → 1.90h while loser hold rose, and the R term is 97.4% of the winner-size fall. The natural suspicion is that an **active-management lever** — a trailing stop, a break-even ratchet, a give-back stop — is cutting winners off.

**MI-278 U2 measured that and it is not what is happening.** Over 183 closes / 49 winners with `created_at >= 2026-08-27`:

- the entire lever family accounts for **3 of 49 winners**;
- the two `giveback_stop` fires **banked +$2,471.70** — the lever that by construction only fires on a trade that *was* winning made money;
- `giveback_stop` is armed on **1 of 44** enabled legs anyway (U1).

So there is no lever to loosen. Any proposal that tightens or relaxes a trailing parameter would be tuning something measured idle.

**What U2 found instead is a hole:** of the 31 winners that ended short of their declared `tp_r`, **17 (55%) carry an unattributable or contaminated label**, and that share is *invariant* to the label-recovery tolerance across 0.000R–0.250R — a measurement, not a threshold artifact.

**And the mass that IS attributable sits on the target:** 18 of 49 winners ended exactly at the take-profit.

---

## 2. Why the take-profit is the only candidate lever

| lever | attributed share of the 49 winners | can it be proposed on? |
|---|---|---|
| take-profit | **18** ended exactly there | the only one with mass |
| stop family (incl. `giveback_stop`) | 6, at 12–13% of declared target; give-back **+$2,471.70** | measured idle or positive — nothing to fix |
| everything unattributable | **17 of the 31 short-of-target winners (55%)** | not a lever, a blind spot |

⚠️ **Every live-population firing rate in this table is a LOWER BOUND**, because 43% of the window is unattributable or contaminated (U2, U3). A proposal that treated these as complete counts would be overstating its own denominator.

---

## 3. The evidence base cannot grade the target — and the reason is structural

`docs/research/m20-fold-dispersion-arms-consolidated.jsonl` is the M20 walk-forward arm set and it does carry `tp_geometry`. **Population: 246 arms, 33 legs, 3 families.** Reproduce every number below with:

```
python3 scripts/research/m20_target_geometry_evidence_audit.py \
    --arms docs/research/m20-fold-dispersion-arms-consolidated.jsonl
```

### 3.1 Perfect confounding

```
live_parity_uncapped  <=>  family == scalp                 <=>  block_unit == per_leg        (28 arms)
live_parity_capped    <=>  family in {donchian, pullback}  <=>  block_unit == family_pooled  (218 arms)

geometry vs family     : confounded   strata holding both: []
geometry vs block_unit : confounded   strata holding both: []
```

**No family carries both geometries.** The within-family comparison does not exist in this dataset — not "is weak", *does not exist*.

### 3.2 The number that invites the mistake

| split | n | candidate | rate |
|---|---|---|---|
| `live_parity_uncapped` | 28 | 21 | **0.750** |
| `live_parity_capped` | 218 | 66 | 0.303 |
| — | | | |
| family `scalp` | 28 | 21 | **0.750** |
| family `donchian` | 67 | 29 | 0.433 |
| family `pullback` | 151 | 37 | 0.245 |

The top row and the fourth row are **the same 28 arms**. "Uncapped wins" and "scalp wins" are one observation with two names, and nothing in the dataset can tell them apart.

### 3.3 No dose to fall back on

Every one of the **246** arms carries `tp_cap_pct == 0.099` — the venue clamp that `bracket-target-reachability-2026-08-24.md` calls *"a hard target nobody chose"*. A single value is not a dose-response, so the usual fallback (argue the geometry from its magnitude) is also unavailable.

⚠️ **Noted and deliberately not over-read:** the *uncapped* arms carry `0.099` too. That may mean the field records declared config rather than what was applied. I did not establish which, and it does not change the verdict.

### 3.4 A third confound, reported rather than buried

| verdict | n | median `usable_folds` | median `n_oos` |
|---|---|---|---|
| `candidate` | 87 | **23** | **288** |
| `honest_negative` | 159 | 16 | 222 |

`verdict` tracks **how much data the arm got to see**. So even a separable geometry contrast would need folds and OOS balanced across the arms.

---

## 4. What I am asking for — the two arms

Both are research runs. Neither touches live money, `config/`, or the order path.

**ARM A — the within-family geometry contrast.** Run `live_parity_capped` **and** `live_parity_uncapped` on the *same* family with the *same* `block_unit`. One family is enough to break the confound; `pullback` is the obvious choice at 151 arms. **This is the arm whose absence is the entire finding.**

**ARM B — a cap dose.** Run at least three `tp_cap_pct` values rather than only the 0.099 venue clamp, so the question *"is 9.9% the right target?"* has a shape instead of a single point. §3.3 is why this is separate from Arm A: without it, even a clean geometry verdict says nothing about the level.

**Acceptance criterion, stated in advance so it cannot be chosen afterwards:** the geometry effect is real if it survives *within* a family with folds and OOS balanced across arms (§3.4). A cross-family difference does not count, however large — that is precisely the 75% (21/28) versus 30% (66/218) being rejected here.

---

## 5. What this proposal does NOT claim

- ⚠️ **It does not satisfy the object's `done_condition`, and says so rather than declaring victory.** That condition requires *"at least one lever reaches a Tier-3 proposal with IS/OOS + walk-forward evidence behind it."* This proposal reaches the operator **with the finding that the walk-forward evidence cannot support the lever** — which is a proposal they can accept or reject, but it is *not* the lever-with-evidence the condition asks for. The object stays open. Closing it on this would be marking my own homework.
- **It does not say target geometry is irrelevant.** It says this dataset cannot tell you, which is different and more useful.
- **It proposes no parameter value.** Not `tp_r`, not the cap, not a trailing setting.
- ⚠️ **It does not rest on the fan-out DiD.** MI-278 U21 put that at a band of **+3.8pp to +29.6pp** with a treated-pre cell of **2 stop-outs in 18 packages**; U15 already called that *"a direction, not an estimate… not a number to make a Tier-3 decision on"*, and nothing here leans on it.
- **The 43% unattributable share is untouched by any of this.** Closing that hole would change every count in §2 and is the higher-value work.

---

## 6. A correction to my own reporting this session

I stated in several board posts and PR bodies that **"three decision requests on the work object are unanswered."** **That is unsupported and I am withdrawing it.** The object file contains the string `decision` **zero** times — checked directly, not inferred — and has no `decision_requests` key. I do not know where the claim came from; it may have been carried across a context compaction from a different surface. It matters because it told the operator something was waiting on them that I cannot show was waiting on them.

The genuine asks now in front of the operator are the two arms in §4, and the several PRs listed on the coordination board awaiting a click.

---

## 7. Verification

`python3 scripts/research/m20_target_geometry_evidence_audit.py --self-test` → **19 controls, 0 failures**, no network.

The harness was shown able to fail before its green was believed — four planted defects, each caught by the controls that name it:

| planted defect | controls that fired |
|---|---|
| report a confounded factor as `separable` | **1, 12, 14** |
| count a single cap value as a dose | **9** |
| collapse an absent factor into `single_level` | **6, 7** |
| grant the licence without checking separability | **12, 14, 19** |

Control **3** is the positive control that matters: adding one arm inside an existing family flips the verdict to `separable` and **names the rescuing stratum** — so the refusal in §3.1 is a property of the data, not of a probe that can only say no.
