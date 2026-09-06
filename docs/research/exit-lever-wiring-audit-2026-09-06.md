# Exit-lever wiring audit — is the bracket / active-management thesis in the roadmap, built, and applied?

**MI-146** · work object [`WO-20260906-ARE-THE-BRACKET-AND-ACTIVE-MANAGEMENT-MECHANISMS`](../claude/work/objects/WO-20260906-ARE-THE-BRACKET-AND-ACTIVE-MANAGEMENT-MECHANISMS.yaml) · registry key `pending-20260906T105301Z-2`.

⚠️ **PROPOSE-ONLY.** Strategy logic and exit geometry are Tier-3. Nothing in this memo is applied. No `src/`, `config/` or matrix file was edited.

## The thesis being audited (operator, 2026-09-06)

> "the brackets need to be the prediction of where we think the price is gonna go. That's what it's based on, and trades need to be ending at the brackets. Either we were wrong and it hit the stop loss or it was right and it hit the take profit. But none of those are, like, formalities. … the stop loss is our risk manager to make sure that things get stopped out at the right level, but we also need to keep on moving it up to be banking our R as it's coming in."

> "we've had lots of trades that end up giving back most of their R before they close out."

---

## What was measured, and against what

Every number below is measured this session against a **named population**. Three sources, in the order of authority this repo declares (*field beats comment*):

| # | source | what it is | when read |
|---|---|---|---|
| **F1** | `GET /api/bot/config` on `ict-bot.duckdns.org` | the **running trader's** resolved strategy config | `as_of 2026-09-06T10:57:38Z`, trader `git_sha 603d5c11` (`git_sha_on_disk a558aaed`, `restart_pending true`) |
| **F2** | `src/units/strategies/*.py`, `src/runtime/*.py` at `origin/main` `90575851` | the **unit source** — what code can actually run | 2026-09-06 |
| **F3** | `/api/diag/journal?table=trades&limit=400` | the **realised close reasons** | 2026-09-06T10:57Z |
| C1 | `docs/research/exit-refinement-coverage.json` (`updated_at 2026-08-30`) | the coverage matrix — **a claim**, graded against F1/F2 | — |

Leg→unit resolution is not asserted: it is computed by importing and calling the production resolver `src.runtime.pipeline.monitor_unit_for` on every matrix leg.

---

# Q1 — IS IT IN THE ROADMAP PROPERLY?

**Yes for the thesis. No for the done-condition.** Both halves matter and they point in opposite directions.

### The thesis IS carried, and better than the question assumed

`ROADMAP.md` line 121 is not an implicit M20 exit row. The milestone is **literally titled "Active Trade Management"**, renamed from *Exit Refinement* on **2026-08-20 by operator directive**, and its own text states the thesis:

> "…the bracket must carry a **predictive expectation at entry**, and active monitoring may revise it in EITHER direction (extending a target on an outperforming trade is in scope; every lever screened before this reframe could only cut a trade short)."

It is developed further in [`docs/design/exit-mechanism-construction-PROCESS.md` § E3.6](../design/exit-mechanism-construction-PROCESS.md), which quotes the operator's 2026-08-20 statement of the same thesis, derives four consequences, names an ML track (**ML-1** conditional barrier race · **ML-2** predictive bracket · **ML-3** revision policy), and — importantly — states a **falsifier**:

> "a predictive bracket is a **claim about where the trade will exit**, so it is graded against realised exits — calibration first …, P&L second. A bracket that improves net R while being systematically wrong about *where* trades exit has not met this bar."

§ E3.6(2) already contains the same measurement this audit re-derived independently, at larger n: **16 of 19 legs declare `tp_r: 50.0`**, so the placed take-profit is `entry × 1.099` — the exchange's rejection threshold — and **76.2% of the fleet's net R comes from the 23.1% of trades whose target is more than 5R away**, i.e. from trades the bracket cannot close (`e35-bracket-is-not-a-decision-2026-08-20.md`, 6,428 trades / 19 legs / 2021-08-16→2026-08-19, net of fees).

**So the thesis is not missing. It is documented, dated, attributed, and falsifiable.** That is a genuinely good state and the question's premise ("or is it only implicit in M20's exit-refinement rows") is too pessimistic. Say so plainly.

### The DONE-CONDITION does not carry it — and that is the real gap

M20's stated done-condition is the coverage matrix:

> "The per-leg done-condition tracker is `docs/research/exit-refinement-coverage.json` + the binding `exit-refinement` skill."

and the `exit-refinement` skill defines it as: *"the milestone/health view of 'are we done' = no `pending`/`blocked` rows on live legs."*

**That grades whether every cell was SCREENED, not whether trades end at their brackets.** The consequence is concrete and checkable, not rhetorical:

1. **A fleet where every cell is `honest_negative` satisfies M20's done-condition perfectly.** 320 of 468 cells (68.4%) already are. M20 can be declared DONE with the thesis 100% unmet.
2. **None of E3.6's three new capabilities has a column in the matrix.** The 9 lever columns are `trail_geometry`, `stale_stop`, `giveback_stop`, `exit_ladder`, `exit_head_ml`, `trail_decay`, `regime_flip_exit`, `vol_trail`, `bracket_geometry`. The first eight are all **post-entry overrides that can only tighten or close**; `bracket_geometry` is **entry-time only**. There is no column for *target extension*, none for *a stated exit expectation at entry*, and none for *revision conditioned on the strategy's own thesis*. E3.6(1) says this in words — *"extend the target has no implementation anywhere in the harness or the live monitor"* — and F2 confirms it (see Q2).
3. **The falsifier E3.6 states is not measured anywhere.** Calibration of a stated expectation against realised exits has no instrument, no artifact and no cell.

### Proposed milestone text (PROPOSAL — Tier-1 doc change, operator to approve wording)

Not a new milestone: **M20's done-condition should be replaced**, because the milestone is already correctly named and scoped. Suggested replacement for the "done-condition tracker" sentence in the M20 row:

> **M20 done-condition (two parts, both required).**
> **(a) COVERAGE — unchanged:** every live leg carries a verdict per lever column in `docs/research/exit-refinement-coverage.json`, with no `pending`/`blocked` row on a live leg.
> **(b) OUTCOME — new, and (a) alone does NOT satisfy the milestone:** on the directional book (pairs sleeve excluded — separate executor), over a stated ≥90-day window and net of fees:
>   1. **every live leg declares a reachable take-profit** — no leg's resting target may be set by `TP_VENUE_CAP_PCT` rather than by a stated expectation; a leg that deliberately has no target must say so as an explicit declaration, not by carrying a 50R sentinel;
>   2. **≥ 60% of closes end at a declared bracket or a declared active-management lever** (`tp` / `sl` / `sl_cross` / `stale_stop` / `giveback_stop` / `exit_head` / a target-revision exit), against a **baseline measured at 36.7%** on 2026-09-06 (n=120);
>   3. **reconciliation and plumbing account for < 20% of closes**, against a **baseline of 63.3%** on the same population; and
>   4. **the stated entry expectation is graded for calibration** against realised exit location, per E3.6's own falsifier — a published calibration read, not a P&L read.
>
> ⚠️ **(b)(2) and (b)(3) are BASELINES, not fitted thresholds.** They are stated so the direction of travel is checkable; the operator sets the target values. What is not negotiable is that a purely coverage-based done-condition lets M20 complete while the thesis fails, which is the state measured on 2026-09-06.

---

# Q2 — IS IT BUILT?

**Population: the 51 of 52 matrix rows that resolve to a live monitor unit.** (The 52nd is the deliberately-bundled `shadow fleet` row, which enumerates no legs — documented in the matrix `_doc` and correctly excluded.) 51 rows × 9 levers = **459 cells**.

### The whole post-entry toolkit lives in TWO units

Measured by grepping each unit's source (F2) for the lever's runtime entry point, and resolving each leg to its unit with `monitor_unit_for` (F1+F2):

| monitor unit | legs | `trail_geometry` | `stale_stop` | `giveback_stop` | `trail_decay` | `vol_trail` | `exit_head_ml` | `exit_ladder` | `regime_flip_exit` |
|---|---:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| `trend_donchian` | 22 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| `htf_pullback_trend_2h` | 19 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| `ict_scalp` | 8 | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `squeeze_breakout_4h` | 1 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `fvg_range_15m` | 1 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

Positive control for the ❌ column: the same grep finds `giveback_verdict` in `trend_donchian.py:623` and `htf_pullback_trend_2h.py:683`, so the probe demonstrably fires on a positive before being trusted on a negative.

### Consumer present vs actually ARMED on the running trader

| lever | legs with a runtime consumer | legs ARMED in the running config | armed / consumer | which legs |
|---|---:|---:|---:|---|
| `trail_geometry` | 42 | **42** | 100.0% | all 42 |
| `trail_decay` | 41 | **17** | 41.5% | 17 legs |
| `exit_head_ml` | 22 | **3** | 13.6% | `trend_donchian`, `_eth`, `_sol` |
| `stale_stop` | 49 | **3** | 6.1% | `ict_scalp_eth_15m`, `trend_donchian_eth_prop`, `trend_donchian_xrp_4h` |
| `giveback_stop` | 41 | **1** | 2.4% | `uso_trend_1h` |
| `vol_trail` | 41 | **0** | 0.0% | — |
| `exit_ladder` | **0** | 0 | — | no runtime consumer in any live monitor unit |
| `regime_flip_exit` | **0** | 0 | — | module exists, **zero call sites** |

### THE CENTRAL DISTINCTION: `n/a` vs `honest_negative` vs *never wired*

The question asked whether the matrix collapses "tested and rejected" into "never wired". **It partly does, and it partly does something more subtle that matters more.** Three genuinely different things are in play and only two of them are the matrix's fault.

**1. `n/a` on the `ict_scalp` family is CORRECT as a matrix status — and is the operator's gap, not a matrix bug.**
All 36 `n/a` cells are the four trailing levers (`trail_geometry`, `trail_decay`, `vol_trail`, `regime_flip_exit`) on `ict_scalp` (8 legs) and `fvg_range_15m`. The refs say: *"Fixed SL/TP/timeout bracket; `ict_scalp.monitor()` only trails to break-even after 1R — no primary trailing stop to reshape."* **F2 confirms it**: `ict_scalp.monitor()` calls `_base.monitor_breakeven_sl` and nothing else on the trail axis. So the status is honest. **But read it in the operator's terms and it inverts**: the matrix is saying *"this family has no active management to refine"*, and the thesis says *"then that is the thing to build"*. `n/a` is the deepest form of *never wired* — it is not "no code to run the lever", it is "no mechanism for the lever to modify". It is the correct label and it is the finding.

**2. `blocked:no_lever_consumer_in_unit` is the right vocabulary, it exists, and it is used on 5 cells while 19 identically-wired siblings read `honest_negative`.** This is the concrete, checkable mislabel:

- 17 **live** `htf_pullback_*` legs carry `exit_head_ml: honest_negative`. Their unit, `htf_pullback_trend_2h`, has **no exit-head consumer at all** (F2). Whatever the sweep concluded, there is no code path to ship it into.
- 2 `ict_scalp` legs (`_avax_5m`, `_xrp_15m`) carry `exit_head_ml: honest_negative` while 5 sibling `ict_scalp` legs on the same lever correctly carry `blocked:no_lever_consumer_in_unit`.

**3. `exit_ladder` (45 cells) and `regime_flip_exit` (42 cells) are `honest_negative` for levers with no runtime at all — and here the label is DEFENSIBLE.** This is research-before-build done correctly, and it should not be filed as a defect:

- `src/runtime/regime_flip_exit.py`'s own docstring states it plainly: *"it has **no runtime implementation at all** — verified 2026-08-14: zero references under `src/`, zero in `config/`. … this module is its first half, and it is deliberately NOT yet called by the order path."* The predicate was replayed offline, found negative fleet-wide (*"wherever the flip actually fires … it guts the trend tail — eth_pullback_2h +67.7R→−11.8R"*), and the build correctly stopped. **That is the process working.**
- `exit_ladder` is built through **three observe-only tiers** — `exit_plan.py` (P1) → `exit_plan_materializer.py` (P2) → `exit_ladder_soak.py` (P3) — each of which says in its own docstring that nothing reads it back. The banking verdict (`memo § 6.2`: *"partial-TP banking reduced net_R in every one of the 20 banking cells"*) is a real measured negative.

**But even a defensible `honest_negative` is not the same object as one measured on a live lever, and the matrix renders them identically.** `trend_donchian trail_decay: honest_negative` was measured on a lever that runs in production on 17 sibling legs; `trend_donchian exit_ladder: honest_negative` was measured on a lever that has never executed a single live exit. Both are one word in one column.

### The deliverable table — NEVER WIRED vs WIRED AND HONESTLY FAILED

| lever | runtime module | called from a live monitor? | armed live | verdict | **classification** |
|---|---|---|---:|---|---|
| **`trail_geometry`** | in-unit (Chandelier ratchet) | ✅ 42 legs | 42 | 35 `honest_negative` on real sweeps; 5 `shipped` | **WIRED · honestly failed** where negative. The one lever that is fully deployed. |
| **`trail_decay`** | `src/runtime/trail_decay.py` | ✅ 41 legs | 17 | 12 `shipped` + 5 `shipped_gate_failed` + 24 `honest_negative` | **WIRED · genuinely mixed.** Real negatives on real code. |
| **`stale_stop`** | `src/runtime/exit_levers.py` | ✅ 49 legs | 3 | 45 `honest_negative`, 3 `shipped` | **WIRED · honestly failed** on 45 legs. Fired 3× in 10 days. |
| **`giveback_stop`** | `src/runtime/exit_levers.py` | ✅ 41 legs (**❌ 10**) | **1** | 39 `honest_negative` on wired legs; **10 `honest_negative` on legs with NO consumer** (8 `ict_scalp` + squeeze + fvg) | **MIXED.** Wired-and-failed on 41; **10 cells are NEVER WIRED and mislabelled as tested.** |
| **`vol_trail`** | `src/runtime/trail_vol.py` | ✅ 41 legs | **0** | 35 `honest_negative` + 9 correct `n/a` | **WIRED · honestly failed everywhere.** The clean case: code exists, was swept on the code, lost. Nothing to build. |
| **`exit_head_ml`** | `src/runtime/exit_head_shadow.py` | ✅ **22 legs only** | 3 | 5 `blocked:no_lever_consumer_in_unit` (**all passed their gate**); **19 `honest_negative` with no consumer** | **MIXED, and the most valuable row.** 22 wired legs = genuine results. **29 legs have no consumer; 19 of them are labelled as tested.** |
| **`exit_ladder`** | `exit_plan`→`materializer`→`soak` | ❌ **zero acting call sites** (soak only) | 0 | 45 `honest_negative` + 1 `passed_unshipped` | **NEVER WIRED.** The negative is real (banking lost net_R in 20/20 cells) and was found *before* building — correct process — but it is a **harness** negative, not a live-lever one. |
| **`regime_flip_exit`** | `src/runtime/regime_flip_exit.py` | ❌ **zero call sites** (its own docstring says so) | 0 | 42 `honest_negative` + 8 correct `n/a` | **NEVER WIRED, DELIBERATELY.** Predicate built, replay negative fleet-wide, build correctly stopped. **Not a gap — do not "fix" it.** |
| **`bracket_geometry`** | entry-time, every unit | ✅ (entry only) | all | 17 `shipped`, 14 `honest_negative`, 8 `pending` | **WIRED but ENTRY-ONLY.** Cannot be revised after entry — see below. |
| **target EXTENSION** | `src/runtime/target_extension_soak.py` | **annotate-only**, from `trend_donchian` + `htf_pullback_trend_2h` (41 legs) | 0 | **no matrix column exists** | **BUILT TO ANNOTATE, NEVER FLIPPED — and UNEVALUABLE:** 100/100 soak rows read `sentinel_no_expectation`. Not tracked by any matrix cell. |

**Summary of the mislabel set: 120 of 459 cells (26.1%) carry a "tested" verdict (`honest_negative`/`shipped`/`passed_unshipped`) for a lever with no runtime consumer in that leg's own unit.** Of those, ~87 (`exit_ladder` 45 + `regime_flip_exit` 42) are defensible research-before-build, and **33 are the genuine mislabel** — 19 `exit_head_ml` + 10 `giveback_stop` + 2 `stale_stop` + 1 `vol_trail` + 1 `exit_ladder passed_unshipped`.

### The finding underneath all of it: no lever can move a target, and the targets are not predictions

**(a) No strategy has ever extended a take-profit — but the producer's annotate phase EXISTS and is running.**

⚠️ **Correcting my own first reading, which was wrong in the direction that would have wasted the operator's time.** My initial pass concluded *"there is no code that could move a target"*. That is false, and the backlog caught it: `src/runtime/target_extension_soak.py` exists and is **called from both `trend_donchian.monitor()` (line 754) and `htf_pullback_trend_2h.monitor()`** — 41 legs. It is the **observe-only first phase of the extension producer**, mirroring the M20 stale-stop rollout exactly. What is missing is the Tier-3 flip from annotate to acting, not the code.

What IS confirmed, by the soak module's own AST-verified docstring (2026-08-23) and by F2 this session:

- `_base.monitor` has declared `{"tp": float}` since it was written and **no strategy has ever produced one**. Fourteen `return {"sl": …}` sites exist across the units; **`return {"tp": …}` exists nowhere.**
- The downstream channel is **fully built**: `interpret_verdict` parses a `tp` delta independently of `sl`, `order_monitor._apply_update` routes it, `_send_modify_to_exchange` forwards it, `execute.modify_open_order` amends the resting leg on Bybit / IB / Alpaca. The only *acting* producer in the repo is `order_monitor.py:590`, rolling a package's tp forward when `turtle_soup` emits `next_tp = meta.tp2` — and `turtle_soup` is `execution: shadow` with no declared `tp_r`.

**So on the live fleet a resting take-profit has never been moved**, and the lever inventory remains entirely reductive: eight columns that cut a trade short, one entry-time column.

**And the soak cannot yet evaluate the lever — measured, not assumed.** `GET /api/diag/log_file?name=target_extension_soak`, read 2026-09-06T10:31Z, **100 of 100 rows** (the tail the endpoint returns) carry `expectation_state: sentinel_no_expectation` and `extension_state: no_expectation_declared`, across 8 distinct live legs. This re-confirms the open row `BL-20260826-TARGET-EXTENSION-SOAK-IS-100PCT-SENTINEL-AND-CANNOT-YET-OBSERVE-THE-LEVER` eleven days after it was filed: the soak cannot distinguish *"the lever never fires"* from *"no trade ever had a real target to approach"*.

**One live row makes the whole thesis gap concrete** (`eth_pullback_2h`, ETHUSDT long, `pkg-c90e8cf40c904e9e`):

| field | value | |
|---|---|---|
| `target_r` / `target_source_key` | `50.0` / `tp_r` | what the strategy "declares" |
| `expectation_price` | **6264.99** | where the declared target would sit |
| `cap_r` | **3.19** | where the venue clamp actually binds |
| `placed_price` / `current_tp` | **2698.81** | **what is actually resting at the venue** |

The declared expectation and the resting order differ by **2.3×**, and the resting one is set by `TP_VENUE_CAP_PCT`. This is not a reconstruction — it is one row of live production telemetry.

**(b) The resting take-profit on most of the fleet is a venue artefact, not a prediction.** `trend_donchian.py:25` says it outright: *"There is no fixed profit target — the trail is the sole profit-exit, so `tp` is placed `tp_r × risk` away (a **deliberately far sentinel**)."* The sentinel is then clamped by `src/runtime/tp_venue_cap.py::TP_VENUE_CAP_PCT = 0.099`, whose own docstring explains it is **Bybit's ~10% rejection boundary** (ErrCode 10001) — and warns that it *"is applied to every symbol, including legs that touch no Bybit account"*, so GLD/QQQ/SCHA/IWM/MES/MGC are clamped by a limit imported from a venue they do not trade on. The effective target is `min(cap_r, tp_r)` where `cap_r = 0.099 × entry / risk` — **a function of price and entry ATR, containing no view about where the trade should end.**

Measured on the running config (F1), **population: the 44 enabled `execution: live` legs**:

| | legs | share |
|---|---:|---:|
| `tp_r ≥ 20` (sentinel — unreachable) | 15 | 34.1% |
| **no take-profit declared at all** | 10 | 22.7% |
| reachable declared take-profit (`< 20R`) | 19 | 43.2% |
| **⇒ NO REACHABLE TAKE-PROFIT** | **25** | **56.8%** |

`timeout_bars` is declared on **2 of 52** enabled legs.

**On 56.8% of the live fleet, "it was right and it hit the take profit" is structurally impossible.** The only exit available to a winning trade is the ATR trail, and a trail *by construction* gives back the distance from the high-water mark to the trail line. **The giveback the operator observed is not a defect in the trail — it is the trail working exactly as designed, on legs whose design contains no profit-taking prediction at all.**

⚠️ **And note the inversion:** the family that best implements the thesis is `ict_scalp` — 8 legs, fixed `tp_at_r: 1.5` + fixed stop + break-even ratchet, so its trades genuinely do end at a declared bracket. It is precisely the family the matrix marks `n/a` on all four trailing levers.

---

# Q3 — IS IT APPLIED WHERE WE CLAIM?

**Yes — this is the half of the system that is in good shape, and it should be said plainly.**

**Population: every cell whose matrix status is `shipped` (39) or `shipped_gate_failed` (7) = 46 cells across 52 rows**, each checked against the leg's entry in the **running** `/api/bot/config` (F1), not against the matrix.

**Result: 46 of 46 shipped cells are armed on the running trader. Zero drift.**

**Independently cross-checked.** `scripts/ci/check_matrix_config_agreement.py`, run this session, reports: *"52 matrix rows, 51 resolved to a leg in `strategies.yaml`, 1 aggregate/unresolvable … OK — every matrix cell agrees with config on shipped-ness."* That guard grades the matrix against the **repo** config; this audit graded it against the **running** config. They agree, which is the stronger statement — the repo and the VM are not drifting on the shipped set.

⚠️ **Correcting my own first pass.** My initial predicate flagged one disagreement — `tlt_pullback_1h` `bracket_geometry: shipped` with no `tp_r` in the running config. **My predicate was wrong, not the matrix.** The shipped cell is named `sm2` and the matrix ref identifies it as a **stop-mult** cell; `atr_stop_mult: 2.0` is live and correct. The corrected count is 46/46.

But the corrected reading is itself a finding in the operator's terms: **the "bracket geometry" cell that was swept, gated, walk-forwarded and Tier-3-shipped on `tlt_pullback_1h` contains no take-profit at all.** The leg's `tp` is `None`. We optimised and shipped a stop and called it bracket geometry.

### What the fleet actually does (F3 — realised closes)

**Population: `/api/diag/journal?table=trades&limit=400` → 216 rows with `status='closed'` and `is_backtest=0`, window 2026-08-27 → 2026-09-06.** The pairs sleeve (77 rows, 35.6%) is broken out because it is an isolated 2-leg executor with its own gate and its own exit semantics — blending it would be the "never blend populations" error.

**Directional book only, n = 120:**

| exit reason | n | share | |
|---|---:|---:|---|
| `reconciler_filled` | 29 | 24.2% | plumbing |
| `sl` | 20 | 16.7% | **BRACKET** |
| `netting_attributed` | 16 | 13.3% | plumbing |
| `intent_reduce_executed` | 13 | 10.8% | plumbing |
| `exchange_flat_reconciled` | 10 | 8.3% | plumbing |
| `stuck_strategy_watchdog` | 8 | 6.7% | plumbing |
| `sl_cross` | 8 | 6.7% | **BRACKET** |
| `tp` | **7** | **5.8%** | **BRACKET** |
| `exit_head` | 4 | 3.3% | active mgmt |
| `stale_stop` | 3 | 2.5% | active mgmt |
| `giveback_stop` | 2 | 1.7% | active mgmt |

- **Ended at a declared bracket: 35 / 120 = 29.2%** — of which **take-profit is 7 / 120 = 5.8%** and stop is 23.3%.
- **Ended by an active-management lever: 9 / 120 = 7.5%**, produced by exactly **three legs**.
- **Ended by reconciliation or plumbing: 76 / 120 = 63.3%.**
- Across the whole closed book incl. pairs (n=216): bracket = 35/216 = **16.2%** — consistent with the 13.0% in the dispatch brief; the difference is window and population, and mine is stated.

**The single largest fact in this table is not the bracket gap — it is that 63.3% of closes are not decisions at all.** Before "trades should end at their brackets" can be true, trades have to end at *something the strategy chose*. Today, roughly two in three do not.

### The one lever that implements the operator's sentence, in its only live instance

`giveback_stop` — *"keep on moving it up to be banking our R as it's coming in"* — is armed on **1 of 41 legs that could run it**. In the 10-day window it fired **twice, on `uso_trend_1h`, both profitable: +1793.32 and +678.38.**

⚠️ **State the population: n = 2.** Two fires is not evidence the lever is good. It is evidence that the lever *exists, runs, and reaches profitable exits* — which is exactly the thing 40 other wired legs have never been given the chance to demonstrate, because they are swept-and-negative under a **50R sentinel target** that guarantees the trade never has a bankable R to protect in the first place.

---

# Recommendation — what to build first

Ordered by (value × confidence) ÷ effort. **All four are proposals. 2–4 are Tier-3.**

### 1. Split the matrix vocabulary — Tier-1, do this first, it costs nothing and unblocks the rest

A decision cannot be made off a matrix that renders "we tested it on the real code and it lost" identically to "there is nothing to test it on". The vocabulary **already exists** (`blocked:no_lever_consumer_in_unit`, used correctly on 5 cells). Proposal:

- **Re-status the 33 genuine mislabels** to `blocked:no_lever_consumer_in_unit`: 19 `exit_head_ml` (17 live `htf_pullback_*` + 2 `ict_scalp`), 10 `giveback_stop` (8 `ict_scalp`, squeeze, fvg), 2 `stale_stop` (squeeze, fvg), 1 `vol_trail` (squeeze), 1 `exit_ladder passed_unshipped` (`ict_scalp_sol_15m`).
- **Add a `basis` field to every cell**: `live_lever` (measured on code that runs in production) vs `harness_only` (measured on a lever with no runtime). That distinguishes the ~87 defensible `exit_ladder`/`regime_flip_exit` research-before-build negatives from the 320-cell mass without demoting them — they stay `honest_negative`, correctly, and a reader can see what they were measured on.
- **Add a CI check** that fails a cell graded `honest_negative`/`shipped` whose lever has no consumer in the leg's `monitor_unit_for` unit. The mislabel recurred 33 times; a reminder is not a mechanism.

### 2. Ship the exit head into `ict_scalp` — Tier-3, and it is the ONLY passed-gate work blocked purely on missing code

The 5 `blocked:no_lever_consumer_in_unit` cells are the **only cells in the entire matrix that passed their gate and are blocked on wiring alone.** All five are `ict_scalp`; the matrix ref records the operator already unblocked the gate on 2026-08-13 (*"the E1→E2 gate's LIVE arm is a wiring check, not the evidence — the backtest is. Awaiting E3 ship (Tier-3)"*), and notes *"all 5 surviving candidates are ict_scalp; every non-scalp family in the re-run is honest_negative."*

| leg | AUC (live-parity re-measure) | beats_actual | disposition |
|---|---|---|---|
| `ict_scalp_sol_5m` | 0.6184 (n_oos 1150) | 22/23 | reproduces — **ship candidate** |
| `ict_scalp_xrp_5m` | 0.5987 (n_oos 1100) | 21/22 | reproduces — **ship candidate** |
| `ict_scalp_sol_15m` | 0.5808 (n_oos 450) | 6/9 | reproduces — **ship candidate** |
| `ict_scalp_5m` (BTC) | — | — | **UNMEASURED** (`data_missing:BTCUSDT`) — exclude |
| `ict_scalp_eth_15m` | 0.6083 | beats_hard **4/11** | **DID NOT REPRODUCE** → `honest_negative` — **exclude** |

**Propose shipping 3 legs, explicitly excluding 2.** The work is an exit-head consumer in `ict_scalp.monitor()`, mirroring the one already live in `trend_donchian.monitor()` — with the same in-distribution guard (the #6201 IWM incident) and the same annotate-first→Tier-3 sequence. This is the highest-confidence unbuilt item in the system.

### 3. Give the 25 targetless live legs a reachable, stated take-profit — Tier-3, and it is the root cause

Everything else is downstream of this. On 25 of 44 live legs the take-profit is either a 50R sentinel clamped to Bybit's rejection threshold or absent. **Until a leg has a target that can be reached, no giveback lever can be evaluated on it and no trade on it can end at a bracket.** The `honest_negative` on `giveback_stop` across 39 wired legs was measured under exactly that condition, so it does not answer the operator's question — it answers a different one.

⚠️ **Do NOT "fix" this by lowering `tp_r` to an equivalent number.** `tp_venue_cap.py` warns explicitly: *"NO `tp_r` REPRODUCES THIS CLAMP … `cap_r = TP_VENUE_CAP_PCT × entry / risk` — a **percent-of-entry** against a **multiple-of-risk**. They are different functions of different variables, so lowering a leg's `tp_r` to some 'equivalent' figure is not equivalent and tightens the real target on trades the clamp was never binding for."*

The right shape is **E3.6's ML-2 (predictive bracket)**: regress the exit location an entry should expect, graded on **calibration first, P&L second** per E3.6's own falsifier. That is already named in the roadmap and has never been started. The cheapest honest first step is not a model at all: **publish the calibration instrument** — for every live leg, the distribution of realised exit R against its declared target — so the claim "our brackets are predictions" becomes falsifiable before anything is changed.

### 4. Flip the target-extension producer from annotate to acting — Tier-3, cheap, **and strictly blocked on (3)**

This is **not** a build-from-scratch item, and I initially mis-scoped it as one. `target_extension_soak.py` already runs the extension decision every tick on 41 legs; `order_monitor` already applies a `tp` update end-to-end. The remaining work is the annotate→act flip, the same shape as the M20 stale-stop rollout.

E3.6(3) records the cost asymmetry that makes this the safest lever in the inventory: **"amending a resting level is not a fill and costs nothing"**, against a 0.082–0.163R round-trip for an early exit that eats most of a +0.12–0.14R mean edge. Every one of the eight existing levers pays that round-trip; a target revision pays nothing.

⚠️ **But it cannot be gated today, and this is the ordering argument for the whole memo.** The soak reads 100/100 `sentinel_no_expectation` — there has never been a real target for a trade to approach, so the lever has no evidence and cannot acquire any. **Recommendation 3 is a hard prerequisite for recommendation 4.** Do (3) on a small set of legs, let the soak accrue rows that are not sentinels, *then* gate this.

⚠️ **Do not read the `exit_ladder` banking negative as evidence against this.** Banking a fixed partial and revising a predictive target are different objects; the memo's "What NOT to build" section says so explicitly.

### What NOT to build

- **`regime_flip_exit`.** Zero call sites is the *correct* state. The replay found it guts the trend tail (`eth_pullback_2h +67.7R → −11.8R`). Building it would be building a measured loser.
- **`vol_trail` anywhere.** 41 legs have the consumer, it was swept on the real code, and it lost on all of them. This is the model of a closed question.
- **`exit_ladder` banking as scoped.** Lost net_R in 20/20 cells. ⚠️ But note the distinction: that verdict is about **banking a fixed partial**, which is not the same object as **a predictive target that can be revised**. Do not let the banking negative be quoted as evidence against recommendation 3 or 4.

---

## Filed observations (not fixed here — this is a propose-only audit)

**Checked against the existing backlogs before filing** (`scripts/ops/backlog_search.py`, five probes). Three of my four candidate findings already have rows — those are **re-confirmations at a later date**, not new filings, and saying so is the point:

| # | finding | disposition |
|---|---|---|
| 1 | **33 matrix cells grade a lever as tested on legs whose unit cannot run it**, while the correct status (`blocked:no_lever_consumer_in_unit`) exists and is used on 5 sibling cells. No detector. | **NEW — file it.** Nearest existing rows (`BL-20260818-UNGRADED-CELLS-DO-NOT-SURFACE-AT-THE-FLEET-LEVEL`, `BL-20260814-NINE-SHIPPED-LEVERS-NEVER-GRADED-AGAINST-THEIR-OWN-ABSENCE`) are about *ungraded* and *un-A/B'd* cells, not about a cell graded against absent code. |
| 2 | `tp_venue_cap`'s 0.099 binds the real target and is applied to legs touching no Bybit account | **ALREADY FILED** — `BL-20260901-E35-TP-R-CAN-BE-INERT-BEHIND-THE-VENUE-CLAMP-AND-THE-BINDING-RATE-IS-UNMEASURED` (open). **Re-confirmed here with the binding rate no longer unmeasured on one leg**: the live soak row shows `cap_r 3.19` against `target_r 50.0`, i.e. the clamp binds by 15.7×. |
| 3 | 63.3% of directional closes are reconciliation/plumbing, not decisions (n=120) | **RECURRENCE of a known class** — `BL-20260820-EXIT-REASON-UNCLASSIFIED-IS-THE-MODAL-VALUE` measured 781/1226 = **63.7%** unclassifiable on 2026-08-20. My independent measure on a different population and a different definition lands at **63.3%**. Two months, two methods, the same number: this is stable, structural, and is upstream of the entire M20 programme. **Update that row rather than filing a fourth.** |
| 4 | The exit-lever inventory is structurally one-directional — 8 reductive columns, 1 entry-time column, **no matrix column for target revision** — while the milestone that owns it was renamed on 2026-08-20 to require bidirectional revision | **PARTLY FILED** — `BL-20260826-TARGET-EXTENSION-SOAK-IS-100PCT-SENTINEL-AND-CANNOT-YET-OBSERVE-THE-LEVER...` (open) covers the soak's blindness and is **re-confirmed here at 100/100 eleven days later**. What is NOT filed is that the *coverage matrix has no column for the capability at all*, so it is invisible to M20's done-condition. **File that half.** |

⚠️ Also worth the operator's attention, found while searching: `BL-20260814-THREE-SIBLING-SWEEPS-STILL-BUILD-NO-TAKE-PROFIT-BOOKS-AND-STAMP-NOTHING` records that **42 of the 43 `regime_flip_exit` negatives were graded on no-take-profit books.** That is independent confirmation of this memo's central claim from a different direction — and it means those negatives, like the `giveback_stop` ones, were measured under the very condition recommendation 3 exists to remove.

---

*Measured 2026-09-06 against running trader `git_sha 603d5c11`, repo `origin/main 90575851`, matrix `updated_at 2026-08-30`.*
