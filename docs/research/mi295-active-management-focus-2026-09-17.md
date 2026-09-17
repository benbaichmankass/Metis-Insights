# MI-295 — active management: where it stands, and where to focus next

> **Doc status:** `unknown` · category `plan` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-295** · RESEARCH lane · Tier-1 · `session_017PQcFJne7qyuVHzH5RZtFC` · manager `session_01GHpyXT2qssLJsrvXBQRkQF`
**Object:** `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`
**Ask (operator, 2026-09-17):** *"For research, I want an update on active management so we can decide where to focus next here, and this may also require reviewing some soaking items."*

**This is a decision surface, not a memo.** Nothing here is enacted. No `src/`, no `config/`, no `scripts/`, no order path. Every parameter question below is Tier-3 and the operator's.

---

## 0. The recommendation, in one box

> **DECIDED-ready, not research-ready. The M20 active-management question is ANSWERED to the standard the work object asked for, and the answer has been sitting in a green, unmerged pull request for five days.**
>
> **PROPOSE — stop commissioning research on this subject and spend three human actions instead:**
>
> | # | action | cost | what it buys |
> |---|---|---|---|
> | **1** | **Land PR #11933** (MI-278 U3/U4) — ⚠️ **resolve its register conflict first, see § 2.1** | a short landing pass, **not one click** | lands the **only** IS/OOS + walk-forward evidence the object's `done_condition` asks for, plus the two Tier-3 proposals it produced |
> | **2** | **Answer U4's Proposal 1 vs Proposal 3** — accept one, or reject both | one decision | the object's `done_condition` is then **met**, and M20 closes as answered rather than as abandoned |
> | **3** | **Land PR #12205** (U41) — ⚠️ **also conflicted, see § 2.1** | a short landing pass | lands the `both_contribute` attribution that `OI-20260911`'s clause asks for; `landing: self`, all five checks green, but **green CI is not mergeability** |
>
> **⚠️ THE HONEST HEADLINE IS UNCOMFORTABLE AND MUST NOT BE SOFTENED: the premise the workstream was opened on did not survive its own measurement.** M20 was framed as *hold winners longer*. Across **175 cells over 7 legs in five arms** (MI-278 U3), **nothing tested makes a winner run longer and survives the gate.** The two cells that survive **both narrow the trade** — take profit sooner, or tighten the stop. They are on the **same leg** and they are **mutually exclusive**.
>
> **What I am NOT recommending, and why:** not another sweep, not a re-measurement, not a new instrument. Three separate units have now established that the *next* obvious measurement is unbuildable, and re-running the ones that work changes no recommendation. § 5 says exactly what would change this recommendation if the operator rejects it.

---

## 1. What "active management" is doing right now — the live readout

**MEASURED**, `GET https://ict-bot.duckdns.org/api/bot/performance?window=<w>`, read **2026-09-17T19:06:5xZ**, browser-direct through Caddy. Every figure below is from that read.

⚠️ **`days=` IS SILENTLY IGNORED — verified this session, not inherited.** `?days=7` returns `window: "all"`, `since: null`, `n: 431` — **byte-identical to `?window=all`**. Anything quoted from `days=` is a lifetime figure wearing a window label. Every number here uses `window=`.

### 1.1 Real money

| | 7d | 30d |
|---|--:|--:|
| `since` | 2026-09-10T19:06:55Z | 2026-08-18T19:06:56Z |
| trades | 4 | 40 |
| wins | **0** | 12 (30.0%) |
| totalPnl | **−$10.96** | **−$20.04** |
| expectancyR | −0.8472 | +0.0446 |
| `pnlCoverage` | **1.00** | 0.75 |
| reached SL | 2 | 16 |
| **reached TP** | **0** | **3** |
| **mid-bracket** | 2 | **21** |

### 1.2 The number that answers the operator's question

**Over 30 days on real money, the declared take-profit was reached 3 times in 40 trades (7.5%), the declared stop 16 times (40.0%), and 21 of 40 (52.5%) ended BETWEEN the two levels.**

`mid_bracket` means, in `src/runtime/bracket_outcome.py`'s own words, *"we LOOKED, on a measurable price, against a real bracket, and the price sat between the levels"* — a genuine non-bracket close. **That bucket IS the active-management surface.** It is the majority of real-money closes by count.

<!-- population-ok: not a claim of mine — a verbatim restatement of
     src/runtime/bracket_outcome.py's own docstring caveat about how the
     mid_bracket bucket must be read. The 52.5% it warns against misreading
     carries its population one paragraph above (21 of 40 gradeable real-money
     closes, 30d window, read 2026-09-17T19:06:56Z); restating the denominator
     inside the caveat would not make the caveat a measurement. -->
⚠️ **`midBracket` IS AN OUTCOME, NOT A DEFECT, and the module says so in terms:** several legs (`vwap_cross`, `exit_head`, `time_decay`) exit deliberately before a bracket. **Do not read that 52.5% as 52.5% failure.** What it establishes is where the decisions live.

**And the majority of that surface is unattributed.** MI-278 U2 (on `main`) attributed every winner since 2026-08-27 — **n = 183 closes / 49 winners**, five accounts — and found:

* **23–27 of 49 winners (47–55%)** attributable to a named per-leg mechanism (a *range*, because it is the only quantity that moves with the label-recovery tolerance);
* of those, **18 ended exactly at the declared take-profit**;
* **the entire lever family is 3 of 49** — two `giveback_stop` (which **banked +$2,471.70**, the largest lever contribution in the window) and one `exit_head` (+$34.07, a scratch);
* **21 of 49 (43%) are unattributable or contaminated**, and that share is **invariant across the whole 0.000R–0.250R tolerance range** — a measurement, not a threshold artifact.

**So: no lever is cutting winners short. The take-profit is the only lever with mass. And for 43% of winners we cannot say what ended them.**

### 1.3 The paper blocks, with their coverage stated

| block | window | n | totalPnl | `pnlCoverage` | reached TP | mid-bracket |
|---|---|--:|--:|--:|--:|--:|
| paper (= `demo`, byte-identical) | 7d | 142 | −$48,206.52 | **0.2958** | 5 | 102 |
| paper | 30d | 628 | **+$165,580.96** | **0.2118** | 33 | 477 |
| `paperPortfolio` | 7d | 11 | −$5,348.73 | 0.3636 | 0 | 4 |
| `paperPortfolio` | 30d | 66 | −$9,698.72 | 0.3788 | 6 | 25 |

⚠️ **THE PAPER 30d NUMBER FLIPS SIGN AGAINST THE 7d NUMBER AND IS 79% RECONSTRUCTED — do not quote either as a result.** `pnlCoverage 0.2118` means 21.2% of that PnL is broker-measured and the rest is reconstructed. A +$165,580 headline over a 79%-reconstructed population is exactly the shape `CLAUDE.md` § "Number provenance" exists to stop being quoted. **The real-money block is the only one whose coverage (0.75 / 1.00) supports a conclusion, and it is small (n=40 / n=4).**

⚠️ **`journalTrust` reports `bybit_2` — the real-money account — as `accountsKnownDivergent` on both windows.** The journal and the broker-truth ledger disagree about it. This is not new and is not this lane's to fix; it is stated because every real-money figure above rides on it.

---

## 2. Correction to this lane's own dispatch brief — the prior lane's work is NOT broadly stranded

My dispatch said the MI-278 results *"never reached `main`"*. **MEASURED, and it is wrong in the direction that would have cost this session a day of re-derivation.**

Population: all **51** `origin/claude/mi278-*` remote branches, each diffed against `origin/main`, every `docs/research/**.md` and `docs/claude/work/**.md` in the diff tested with `git cat-file -e origin/main:<path>`.

* **35 distinct `m20-u*` unit memos ARE on `origin/main`** (the lane's own wrap block records the same count independently).
* **Exactly 6 research documents are stranded**, on **7 open PRs**:

| PR | unit | document | landing | checks |
|---|---|---|---|---|
| **#11933** | **U3 + U4** | `m20-u3-scalp-target-sweepable-2026-09-12.md`, **`m20-u4-scalp-exit-proposals-2026-09-12.md`** | `hold` / unvouchable_paths | **5 of 5 green** (2026-09-13T08:22Z) |
| **#12205** | U41 | `m20-u41-e35-break-attribution-verdict-2026-09-13.md` | **`self`** | **5 of 5 green** (2026-09-17T11:10Z) |
| #12158 | U30 | `m20-u30-scalp-target-sweep-axis-2026-09-13.md` | `hold` / unvouchable_paths | green at last push |
| #12181 | U37 | `m20-u37-margin-ceiling-visibility-2026-09-13.md` | — | — |
| #12070 | U17 | `m20-u17-arbitration-fallback-inversion-2026-09-12.md` | — | — |
| #11976 | U8 | `m20-u8-stop-attribution-2026-09-12.md` | — | — |
| #12219 | U45 | *(U45's memo landed separately on `main`)* | — | — |

**The work does not need redoing. It needs landing.** The brief's other claim — that the conflicts are in shared registers and **zero** in `src/`/`tests/`/`scripts/`/`config/` — I did not re-verify per PR and am not asserting.

### 2.1 ⚠️ CORRECTION, 2026-09-17T20:1xZ — "merge it" was wrong; these PRs are CONFLICTED

**I wrote § 0 rows 1 and 3 as one-click merges. That was wrong and it is my error, not a change in the world.** Re-read at **20:1xZ**, after `main` advanced to `89f9d0c3e`:

| PR | `mergeable_state` | CI |
|---|---|---|
| **#11933** | **`dirty`** | 5 of 5 green |
| **#12205** | **`dirty`** | 5 of 5 green |
| #12471 (this one) | `clean` | 5 of 5 green |

**`dirty` is a real merge conflict.** A click will not land either PR; the register conflict has to be resolved first, so row 1 and row 3 are **a short landing pass, not a click**.

⚠️ **How I got it wrong is worth more than the correction.** Both PRs read `mergeable_state: unknown` every time I looked — GitHub had not finished computing — and I reported merge-readiness from **CI being green** instead. That is this repo's own *"read the field, not the prose about it"* failure, committed on a field that was simply not ready yet: **green CI is not mergeability**, and `unknown` is *we could not look*, never *fine*.

**The manager reached the same conclusion independently and by measurement** (`89f9d0c3e`, on the M20 object): *"of the conflicts probed across these branches, every one is in a SHARED REGISTER (`health-review-backlog.json` in 5 of 7, then `CLAUDE.md`, `DOCUMENT-INDEX.md`, `OPEN-ITEMS.json`, one M20 work object) and ZERO are in `src/`, `tests/`, `scripts/` or `config/`."* That is the dispatch-brief claim § 2 above declines to assert on my own authority — **it now has a source, and it is the manager's, not mine.**

**Nothing about the substance changes.** The evidence is complete, it still needs landing, and the Tier-3 answer is still the thing that closes the object. What changes is the *cost and shape* of rows 1 and 3: a serialised landing pass with a union resolve on the registers (`scripts/ops/merge_json_register.py` exists for exactly this), not a click.

⚠️ **And a local `git merge-tree` probe CANNOT be used to check this.** This clone has `merge.jsonregister.driver` armed, so a local test-merge of a register-touching branch reports clean for a branch GitHub grades `dirty` — `BL-20260910-AN-ARMED-CLONES-LOCAL-TEST-MERGE-IS-A-FALSE-NEGATIVE-ON-GITHUB-MERGEABILITY`. I ran one, it said `CLEAN`, and **GitHub says `dirty`**. Take mergeability from GitHub.

---

## 3. ⚠️ The finding that changes the recommendation: #11933 carries the object's own `done_condition` deliverable, and the lane's wrap does not name it

`WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`'s `done_condition`, verbatim:

> The active-management levers that govern when a WINNER is closed are enumerated, measured against the post-2026-08-27 population, and each is graded as it-helps / it-hurts / not-gradeable with a stated population. **At least one lever reaches a Tier-3 proposal with IS/OOS + walk-forward evidence behind it.** NOT done when a memo exists — done when a proposal the operator can accept or reject exists.

**PR #11933 delivers that sentence.** Five arms, **175 cells over 7 legs**, at live parity, with a yearly walk-forward:

| lever | cells / legs | cleared IS/OOS | cleared walk-forward |
|---|--:|--:|--:|
| `bracket_geometry` @ 24-bar timeout | 49 / 7 | **0** | — |
| `bracket_geometry` @ **live parity** | 49 / 7 | 3, on 2 legs, opposite directions | **1** |
| `breakeven_ratchet`, one-axis | 7 / 7 | **0** | — |
| `stop_geometry` (stop buffer) @ parity | 42 / 7 | 4, on 3 legs | **1** |
| `breakeven_ratchet` × target (cross) | 28 / 7 | **0** | — |

**The lane's wrap block on the work object lists `u3` among the units not on `main`, but names #12158 (U30) as *"THE HIGHEST-VALUE UNBLOCKING ACTION AVAILABLE TO A HUMAN"* and does not mention #11933 at all in `unfinished_and_what_it_costs`.** U30 builds an *axis*; U3/U4 carry the *verdict and the proposals*. I think the ranking is inverted, and I am saying so rather than inheriting it.

### 3.1 The two survivors — the actual decision in front of the operator

Both are `ict_scalp_xrp_15m`. **Both fail the same 2024 fold. Each was measured with the other held at its LIVE value. Neither run measured the pair.**

| | **Proposal 1** — `tp_at_r` 1.5 → **1.25** | **Proposal 3** — `atr_sl_buffer_mult` 0.20 → **0.10** |
|---|--:|--:|
| IS ΔR / ΔDD | +5.82 / −2.53 (n=198) | **+7.13** / −1.02 (n=199) |
| OOS ΔR / ΔDD | **+5.93** / **−3.72** (n=117) | +3.75 / −0.15 (n=117) |
| walk-forward | 3 of 4 usable folds (need 3) | 3 of 4 usable folds (need 3) |
| direction | takes profit **sooner** | tightens the **stop** |

⛔ **THEY ARE MUTUALLY EXCLUSIVE AND MUST NOT BOTH BE ACCEPTED.** On this family the stop distance **is** the R unit (`sl = sweep_extreme ± atr_sl_buffer_mult × ATR`), so narrowing the buffer *shrinks R*, and `tp_at_r` is denominated in R. Applying both produces a geometry **neither cell tested**. If both are wanted, that is a **third sweep**, not an addition.

**MEASURED, so the blast radius is not guessed at:**

* `/api/bot/config` read **2026-09-17** from the running trader: `ict_scalp_xrp_15m` → `tp_at_r: 1.5`, `atr_sl_buffer_mult: 0.2`, `execution: live`, `enabled: true`. **Neither proposal is enacted** — correct, they are Tier-3.
* `config/accounts.yaml`: `ict_scalp_xrp_15m` routes to **`bybit_1` ONLY** — `mode: live`, `account_class: **paper**`. **No real-money account carries this leg.**

**So this is a one-field, one-leg, paper-class Tier-3 decision.** That is as cheap as a Tier-3 decision in this repo gets, and it is the gate on closing a workstream the operator opened with *"no sessions should stop until we have resolved that issue."*

### 3.2 What U3 refuted, which is worth as much as what it proposed

* **The break-even ratchet is refuted TWICE**, the second time on its own causal mechanism: `disarm_effect(tp)` over 28 cells / 7 legs → **0 legs rise, 4 fall, 3 non-monotone**. The control rung reproduces the one-axis figures exactly on 7 of 7 legs.
* **Proposal 2 (`ict_scalp_avax_5m` `tp3R`/`tp4R`) was WITHDRAWN** by the walk-forward after clearing IS/OOS. ⚠️ **`tp4R`'s OOS ΔR of +13.75 is the largest single OOS improvement anywhere in MI-278 and it did not survive** — it rested on in-sample drawdown gains of −51.00 R and −63.09 R the folds do not carry. Do not resurrect it from that line.
* **The parity defect is the largest finding in the unit and is not about targets at all:** the harness force-closes at 24 bars and **production has no time exit on any `ict_scalp` leg**. Worth up to **99%** of a leg-window's reported profit (`sol_15m` OOS +20.46 R → **+0.20 R** at parity), and it **changes verdicts**. At a 24-bar timeout **5 of 7** legs flip sign between IS and OOS; at parity, **2 of 7**.

---

## 4. The soaking items the ask points at — reviewed, and three are settled enough to stop re-reading

| row | state | what I did |
|---|---|---|
| `OI-20260906-THE-BRACKET-CALIBRATION-INSTRUMENT-EXISTS-AND-ITS-VERDICT-HAS-NOT-BEEN-ACTED-ON` | **Waiting on an operator decision, not on evidence** | **Deliberately did NOT re-run it.** Its own criteria say *"A RE-RUN OF THE INSTRUMENT CLEARS NOTHING… Re-measuring is precisely the failure mode this row exists to stop."* It clears on a recorded Tier-3 decision — including *"the sentinel idiom stays"*. **That decision is the same class as § 3.1's, and the two should be taken together.** |
| `OI-20260906-ML2-CALIBRATION-PASSED-BUT-THE-SHARPNESS-RESULT-IS-AGAINST-THE-WRONG-BASELINE` | **Scientifically settled; carriage is what is open** | ML-2 was BUILT and REFUTED — calibration passes (MACE 0.0061, n=9,814), sharpness fails at **0 of 5** quantiles against the risk-scaled baseline. **Do not rebuild it on those features.** The row stays open only until the refutation is carried somewhere a session planning a target change will meet it. |
| `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED` | **Attribution clause MET by #12205; row stays open on its separate detector clause** | Verdict `both_contribute` — excursion fell in **both** arms including the control e35 never touched, **and** the e35 arm's stop-out rate rose **+11.6pp** on top. Population **214 package ids of 1000**. ⚠️ **Only the sign of the excursion term survives** — the MFE:MAE mean overstates the control's fall ~16×; quote the **median** (favourable-to-adverse travel roughly halved), never −22.47. **Consequence: reverting e35 alone leaves the control arm and `ict_scalp_*` unexplained.** |
| `OI-20260912-THE-WINNER-SIZE-COLLAPSE-IS-AN-R-COLLAPSE-AND-ITS-MAGNITUDE-IS-NOT-ESTABLISHED` | Genuinely accruing | Not touched. Its magnitude gap is real and needs a wider measured population. |
| `OI-20260911-THE-TWO-DETECTORS-ARE-BUILT-AND-NEITHER-HAS-EVER-FIRED-ON-THE-FLEET` | Already re-affirmed today (`verified_at: 2026-09-17`) | Not re-read. |

### 4.1 One thing from the ask that is NOT settled, and it is a real-money hole

The brief carried a finding I confirmed against the row: real-money `bybit_2` shows `streak_days: 6` and grades **`no_streak`**, because `LOSING_STREAK_MIN_LOSS_USD` is a **$500 fleet-wide absolute floor** and the bleed was **−$19.10**.

**A real-money account on six consecutive losing days is structurally invisible to the detector built to catch exactly that.** The floor's own basis (MI-276) is a fleet-wide false-positive sweep; it was never sized against an account this small. Already filed as `BL-20260911-LOSING-STREAK-SIZE-GATE-IS-A-FLEET-WIDE-ABSOLUTE-SO-IT-UNDER-SERVES-A-SMALL-REAL-MONEY-ACCOUNT` in the performance backlog. **Not this lane's to fix (the fix is `src/`, Tier-2) — flagged loudly rather than left as a footnote.**

---

## 5. Why "more research" is the wrong next move — three independent unbuildability results

This is the part that makes the recommendation a recommendation rather than an opinion. **The next obvious measurement in each direction has been tried and is refused by construction, not by effort.**

1. **U48 (on `main`, 2026-09-17):** U23's **ARM A** — the within-family geometry contrast — is **`unsatisfiable_by_construction`**. `tp_geometry` is *computed from* family membership in `CLAMPING_FAMILIES` by the stamp's declared single owner, and the cap enters only through a `tp_cap_pct > 0.0` **sign test**, so `{0.0, any one positive}` is exhaustive. **No family can ever carry both levels, at any cap.** The backlog row prescribing that arm **can never close as written**, and it is currently named as blocking this object's `done_condition`.
2. **U47 (on `main`, 2026-09-17):** the real-money risk dispersion was enumerated against a **closed** candidate list — 4 unreachable, 4 refuted, 1 contributing. It is **not any clamp a session can read**. The remedy is a one-line **Tier-2** diag stamp, and it now blocks two high-severity rows.
3. **U2 (on `main`):** the **43%** unattributable-or-contaminated share of winners is **invariant to the tolerance across the entire range**. It is not a threshold to tune; it is a hole in the record.

**What IS still runnable** (U48 § 5), if the operator wants one more research arm: **≥3 distinct positive `tp_cap_pct` values on `pullback` (151 arms) or `donchian` (67)**, folds and OOS balanced. That is U23's **ARM B**, and it is the *only* surviving axis. It answers *"is 9.9% the right cap level?"* — **it does not answer the M20 question**, which is about `ict_scalp`, where any `tp_cap_pct` sweep is **inert** (`scalp ∉ CLAMPING_FAMILIES`).

---

## 6. If the operator rejects this recommendation — what would settle it instead

Stated in advance so it cannot be chosen afterwards.

* **If the answer is "I don't trust a one-leg, minimum-passing walk-forward":** that is reasonable and § 3.1's own caveats say so. The settling evidence is a **joint cell** — `tp_at_r 1.25` × `atr_sl_buffer_mult 0.10` measured together, since the two interact by construction. That is one sweep on one leg, and it is the *only* measurement that makes accepting both defensible.
* **If the answer is "close the 43% hole first":** that is the highest-value research left and U23 says so too. It is **not** a sweep — it needs the exit-label recovery applied over a wider window, and it would change every count in § 1.2.
* **If the answer is "none of this, the target geometry stays as it is":** that is a legitimate outcome, it **closes** `OI-20260906-THE-BRACKET-CALIBRATION-INSTRUMENT-…` on its own terms, and it must be **RECORDED as a decision** rather than left looking like work about to start. ⚠️ That last clause is the failure mode this whole cycle exists to kill.

---

## 7. What this document does NOT claim

* **It does not claim the object is done.** Its `done_condition` needs a proposal the operator can accept or reject **to have reached them**; #11933 is unmerged, so it has not. What I claim is that the *work* is finished and the *landing* is not.
* **It proposes no parameter value of its own.** Proposals 1 and 3 are MI-278 U4's, reproduced so the decision is in one place.
* **It re-ran no instrument and re-derived no prior result.** Every MI-278 figure is quoted from the unit that measured it, with that unit's own population.
* **It did not verify the per-PR conflict claim** in the dispatch brief (§ 2), and says so rather than repeating it.
* **The live readout in § 1 is a single dated read** of a moving endpoint. It is a snapshot, not a standing property.
