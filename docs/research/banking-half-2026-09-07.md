# The banking half of the exit thesis — what actually moves the stop to bank R, per leg

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-163** · task **T-8** of [`TASK-PRIORITY-2026-09-07.md`](../claude/TASK-PRIORITY-2026-09-07.md) · branch `claude/mi163-banking-half-20260907`

⚠️ **PROPOSE-ONLY on anything that changes a live exit.** Strategy params and risk levers are Tier-3. This document measures and proposes; it edits no `config/`, no `src/`, and arms nothing. The instrument (`scripts/research/banking_mechanism_audit.py`) and this memo are Tier-1.

The operator's thesis: brackets should predict where price goes, trades should end at their brackets, and **the stop is the risk manager — it must trail up to bank R as it accrues.** The *target* half closed on 2026-09-07 (MI-146→158). This is the banking half.

---

## The answer in five lines

1. **`src/runtime/exit_plan.py` is SHADOW, not live.** Established by tracing readers to a real order, not by reading a doc. So the manager's headline — *"`be_at_r` is 0 of 44 and `exit_plan.py` reads it with no default"* — **is a fact about a shadow artifact and is not the finding.**
2. **Over the 44, `be_at_r` is not merely undeclared — it is UNREACHABLE, and the live threshold is a code default of `1.0`.** No leg in the population routes to a monitor that reads it (§2.5). The dispatch's inference — *absent means absent* — is right about `exit_plan.py` and wrong about the fleet, in the same code-default-supplies-what-YAML-omits shape MI-156 found for `tp_r`.
3. **Two live banking mechanisms exist, and one of them cannot accrue R at all.** 36 of 44 legs run a monotone Chandelier ratchet; **8 run a one-shot move to break-even that fires once and never again.**
4. **THE FINDING: on the ratchet legs the stop almost never even reaches break-even.** Reaching break-even costs `trail_mult / atr_stop_mult` R of excursion — **median 2.00R across the 36 legs** — while the median live peak is **0.84R**. Measured: **14 of 89 (15.7%) reached break-even; 4 of 89 (4.5%) banked +1R.** There is a risk-manager stop on the fleet; it is calibrated so far out that it is almost never armed.
5. **A fleet-wide rule IS gradeable and a per-leg one is not.** Pooled n = **89** in-population gradeable rows against the repo's own floor of 30 — **sufficient**, where MI-155's per-leg max n of 8 is not.

---

## 1. Is `exit_plan.py` live or shadow? — SHADOW, and here is how it was established

The dispatch could not settle this and said everything depends on it. It does.

**Both callers write; nothing reads.**

| caller | what it does with the plan |
|---|---|
| `src/core/coordinator.py:3483-3526` | derives `exit_plan`, materialises `exit_plan_state`, and puts both in the dict written to the `order_packages` row |
| `src/runtime/exit_ladder_soak.py:66-85` | rebuilds a plan to write a row into `runtime_logs/exit_ladder_soak.jsonl` |
| `src/units/strategies/turtle_soup.py:399-426` | a module-level `exit_plan()` hook |

**The reader search is the part that settles it.** Over all of `src/`, the only references to the `exit_plan` / `exit_plan_state` *columns* outside the write path are `src/units/db/database.py:1425-1428`, which JSON-serialise the values **on the way in**. There is no `SELECT`, no row access, and no branch anywhere that reads either column back. And `turtle_soup.exit_plan()` is **never called**: no `getattr(mod, "exit_plan")`, no direct call site, nothing in the monitor dispatch (`order_monitor._call_strategy_monitor` looks up `getattr(mod, "monitor")` and nothing else).

⚠️ **The positive control that makes the silence meaningful:** the same search shape *does* find live readers for the levers that matter — `trail_mult` resolves through `trend_donchian.py:785`, `trail_decay.resolve_trail_mult`, and `trail_vol.resolve_vol_trail_mult` into a returned `{"sl": ...}`. So the probe can find a positive; `exit_plan`'s silence is a real absence.

**Consequence, and it is the reason this had to be settled first:** grading the fleet's banking behaviour from `exit_plan.py`'s lever reads would have produced a confident, wrong finding about a live trading system. The live banking behaviour is whatever the **strategy unit's `monitor()`** returns as `{"sl": ...}`, applied by `order_monitor._apply_update`.

---

## 2. What actually banks R on the live fleet

**POPULATION: the 44 legs with `enabled: true` AND `execution: live` in `config/strategies.yaml` at `main` a55fae75.** This reproduces MI-146 / MI-148 / MI-155's denominator exactly. ⚠️ `main` advanced to e81f470 during this session; re-checked with `git log a55fae7..origin/main` — **none of those commits touch `config/strategies.yaml`, `src/units/strategies/`, `src/runtime/exit_plan.py` or `src/runtime/position_telemetry.py`**, so every figure below stands on the newer base.

### 2.1 The lever table reproduces — verified, not re-derived

The manager's coverage table was re-measured against the same population and **every cell matches**: `trail_mult` 36 · `tp_r` 36 · `trail_decay_tight_mult` 16 · `trail_decay_stall_bars` 10 · `be_offset_bps` 8 · `trail_decay_arm_r` 7 · `stale_exit_below_r` 3 · `giveback_r` 1 · `be_at_r` 0, all of 44.

### 2.2 But YAML coverage is not behaviour — there are two mechanisms, not one

Each leg's `monitor()` owner resolves through `pipeline.monitor_unit_for`. Reading each owner:

| mechanism | legs | what it does | monotone? | can it accrue R? |
|---|---:|---|---|---|
| **Chandelier ratchet** — `trend_donchian`, `htf_pullback_trend_2h`, `squeeze_breakout_4h` | **36** | each tick proposes `extreme ∓ trail_mult × ATR` from the since-entry extreme, returned **only** when it tightens | **YES** — `if candidate > sl` (long) / `< sl` (short); it can never loosen | **yes** |
| **One-shot break-even** — `ict_scalp` via `_base.monitor_breakeven_sl` | **8** | fires when `price >= entry + be_at_r × 1R` **AND `sl < entry`**, moving the stop to `entry ± offset` | yes, trivially | **NO** |

⚠️ **The one-shot mechanism is structurally incapable of banking R.** Its guard is `sl < entry`. The moment it fires, `sl >= entry`, the guard is False, and it returns `None` for the rest of the trade's life. It banks break-even plus `be_offset_bps`, once, and that is its ceiling. **8 of 44 legs (18.2%) have no mechanism that can bank R at all.**

### 2.5 `be_at_r` over this population is UNREACHABLE, not just undeclared

⚠️ **This corrects a weaker claim I made earlier in this same session, and the correction matters.** My first reading was *"`be_at_r` has a live code default of 1.0 at `turtle_soup.py:550` / `vwap.py:1127`."* Both of those reads are real — and **neither unit is in the population**: `turtle_soup` is `execution: shadow` and `vwap` is `enabled: false`. Checking which units the 44 actually route to:

- All **8** one-shot legs are `ict_scalp`.
- **`ict_scalp` never reads `be_at_r` at all** — `grep` over the unit returns nothing for either `be_at_r` or `one_r_threshold`. Its call is `monitor_breakeven_sl(open_pkg, candles_df, be_offset_bps=be_offset_bps)`.
- So the effective threshold is `_base.monitor_breakeven_sl`'s **signature default, `one_r_threshold: float = 1.0`**.

**Consequence: declaring `be_at_r` in `config/strategies.yaml` on any of the 44 enabled+live legs would change nothing.** The lever is inert over the entire live population. That is a stronger and more actionable statement than "0 of 44 declare it", and it is the one a session should carry forward.

### 2.3 The arithmetic — how much excursion the ratchet costs before it banks anything

All three ratchet units share **identical** geometry, verified in each: `sl = entry ∓ atr_stop_mult × atr` and `risk = |entry − sl|` (`trend_donchian.py:389-398`, `htf_pullback_trend_2h.py:349-354`, `squeeze_breakout_4h.py:179-184`). The trail uses the **entry-time ATR frozen in `meta["atr"]`** (written at `:449`, read at `:770`), so the ATR **cancels**:

> `1R = atr_stop_mult × ATR`, and the stop reaches entry when `extreme − trail_mult × ATR = entry`
> ⇒ **`R_TO_BREAKEVEN = trail_mult / atr_stop_mult`** — exact, no estimation
> ⇒ **excursion to bank +kR = `R_TO_BREAKEVEN + k`**

And `peak_r` is directly comparable to it: `position_telemetry.record_position_telemetry` computes `since_entry_peak(window, entry, risk_per_unit, ...)` on the **same** `window` the trail ratchets on, with `risk_per_unit` being that same `atr_stop_mult × ATR` (`trend_donchian.py:454`). One definition of the extreme, one definition of R.

**Across the 36 ratchet legs, `R_TO_BREAKEVEN` ranges 1.20 – 3.33, median 2.00.** So a typical leg must run **2R in its favour before its stop merely stops losing money**, and **3R before it banks its first R**.

### 2.4 What that costs, measured

**POPULATION: `/api/diag/position_telemetry?limit=1000`, read 2026-09-07 ~15:55Z, 171 rows returned; 103 pass `peak_gradeable`; 89 of those belong to one of the 44 legs and all 89 sit on ratchet legs.** Accounts represented: `bybit_1` 36 · `alpaca_paper` 21 · **`bybit_2` 18 (REAL MONEY)** · `alpaca_portfolio` 10 · `ib_paper` 4.

⚠️ **`peak_r_is_lower_bound` is `true` on 171 of 171.** Every peak is under-measured, so every "reached" count below is a **LOWER BOUND** and every "did not reach" count is an **UPPER BOUND**.

| | n | share |
|---|---:|---:|
| trailing stop reached **break-even** | **14** / 89 | **15.7%** (lower bound) |
| trailing stop banked **+1R or more** | **4** / 89 | **4.5%** (lower bound) |
| stop **still below entry** | 75 / 89 | 84.3% (upper bound) |

Against the fleet's median live peak of **0.84R**, and a median `R_TO_BREAKEVEN` of **2.00R**, this is not a surprise — it is the arithmetic. **The mechanism the operator is owed exists on 36 of 44 legs and is calibrated roughly 2.4× beyond where the trades actually go.**

---

## 3. ⚠️ An instrument defect found on the way — 9 legs are structurally invisible

`record_position_telemetry` is called from **inside** a strategy unit's `monitor()`. Measured: only **`trend_donchian` and `htf_pullback_trend_2h`** carry the hook. `squeeze_breakout_4h`, `ict_scalp`, `turtle_soup` and `vwap` do not.

**So a leg on an unhooked unit emits no peak-R telemetry however much it trades.**

MI-155's verdict table grades **13** legs at `n_live = 0` and calls all 13 `insufficient_n`. Measured here, those 13 **split**:

| | n | meaning |
|---|---:|---|
| on an unhooked unit — **we did not look** | **9** | the 8 `ict_scalp_*` legs + `squeeze_breakout_4h` |
| on a hooked unit — **a real absence of positions** | **4** | `splg_trend_long_1d`, `tqqq_trend_long_1d`, `trend_donchian_eth_prop`, `trend_donchian_sol_prop` |

⚠️ **This is the collapse CLAUDE.md names**: *"never collapse 'we did not look' into 'we looked and found nothing'"*. It is not a criticism of MI-155's verdict — `insufficient_n` is correct for all 13 — but the **reason** differs, and it changes what fixes them. Four need trading volume (T-9's wall, which effort cannot move). **Nine need one line of code**, and no amount of soak will ever produce a row for them.

⚠️ **And it bounds this document too.** The one-shot mechanism (§2.2) is **entirely unmeasured on the live fleet** — all 8 legs that use it are among the 9 invisible. Everything in §2.4 describes the ratchet legs only. I did not look at the one-shot legs, and I am not reporting that I did.

---

## 4. Is the pooled corpus sufficient for a fleet-wide rule? — YES

This is the split that makes T-8 shippable, and it holds.

| | per-leg (MI-155) | pooled (here) |
|---|---|---|
| n | max **8** | **89** (in-population, gradeable) · 103 (all gradeable) |
| floor | 30 (`backtest_fidelity_calibrate.MIN_LIVE_N`) | same floor |
| verdict | `insufficient_n`, **44 of 44**, invariant on any floor in [10,30] | **sufficient** |

**⚠️ The confound MI-155 identified had to be checked, and it binds much less here than it does there.** MI-155 found live p90 moving **4.5×** across timeframes (1h 2.16% → 4h 9.77%), which would make a pooled *tail* statistic a composition artifact. Measured on this corpus in R:

| tf | n | median peak_r | p90 | max |
|---|---:|---:|---:|---:|
| 1h | 30 | 0.86R | 2.23R | 4.35R |
| 4h | 26 | 1.11R | 2.72R | 6.58R |
| 2h | 17 | 0.70R | 3.42R | 6.71R |
| 1d | 16 | 0.73R | 1.09R | 2.51R |

The **medians span 0.70–1.11R, a factor of 1.6**, while the p90s span 1.09–3.42R, a factor of 3.1. **The horizon confound is a tail phenomenon.** A banking threshold in the 0.5–1.5R region sits near the pooled median, where composition moves the answer far less — which is precisely why a fleet-wide threshold is gradeable on this corpus while a per-leg tail quantile is not. The corpus is also reasonably spread across horizons rather than dominated by one.

### What a single fleet-wide threshold would have done

**POPULATION: closed + `peak_gradeable` + in the 44 — n = 71.** Restricted to closed rows because only there is `open_r` the terminal R. A row with `peak_r >= X` and terminal `< X` **must** have crossed X on the way down, so a stop parked at +X would have exited at `>= +X`; `X − open_r` is a rigorous lower bound on the improvement.

Actual realised over the 71: **+28.80R, mean +0.406R/trade.** Median peak **0.88R**; median terminal **−0.19R** — the fleet's typical trade goes green and gives it all back.

| bank at | would arm | rescued | gross ΔR (≥) | mean R/trade (≥) |
|---:|---:|---:|---:|---:|
| **+0.50R** | 42 | 20 | **+14.29** | +0.607 |
| **+0.75R** | 40 | 20 | **+16.87** | **+0.643** |
| +1.00R | 30 | 10 | +10.55 | +0.554 |
| +1.50R | 22 | 9 | +7.69 | +0.514 |
| +2.00R | 16 | 9 | +8.09 | +0.520 |
| +3.00R | 6 | 2 | +2.50 | +0.441 |

⚠️ **THIS IS NOT A NET RESULT AND MUST NOT BE QUOTED AS ONE.** The telemetry carries the peak and the terminal, **never the path**. A stop parked at +X exits on the **first** retrace to X — so on a trade that later resumed it forgoes the remainder, and that cost is unmeasurable from this corpus (checked: scripts/research/banking_mechanism_audit.py — `counterfactual()` reads the served telemetry schema, which carries `peak_r`, `open_r` and `giveback_r` and no intra-trade path, and reports `is_net: False`).

⚠️ **That sentence previously read "is **not** measurable", which the impossibility-claim guard's regex does not match** — the markdown bold between *is* and *not* split the pattern. The claim was no less an impossibility claim for having escaped the check, so it now carries the same `checked:` evidence the guard would have demanded. What the table establishes is that the benefit side is real, large, and **concentrated at low X (0.5–1.0R) — exactly the region where the current mechanism does nothing**, since median `R_TO_BREAKEVEN` is 2.00R. It does **not** establish that any X is profitable. Net requires the path-aware harness (`src/research/trail_levers.py::effective_trail_mult`), which is a separate run.

---

## 5. What I propose — two changes, deliberately sequenced

⚠️ **Both are Tier-3** (they touch strategy unit files the live VM consumes). Neither is applied. The operator approves.

### Proposal A — repair the instrument FIRST (observe-only)

Add the existing `record_position_telemetry` hook to the **two** unhooked monitor units **that the population actually routes to**, exactly as `trend_donchian.py:823-833` already does it. It is observe-only by construction — its own comment says it *"reads nothing back and cannot alter the trail"* — and it is wrapped in the same `except Exception: pass` so it can never break an exit.

⚠️ **This narrows what an earlier revision of this section said, and the narrowing is the useful part.** It read *"the four unhooked monitor units"*, which overstates the work and misdescribes it. There are indeed four unhooked units, but only two carry any of the 44:

| unit | legs covered | shape of the change |
|---|---:|---|
| `squeeze_breakout_4h` | **1** | **pure copy-paste** — it already has `_since_entry` (`:249`) and binds `window` inside `monitor()` (`:336`); the 14-line block drops in directly after |
| `ict_scalp` | **8** | the same block **plus one line** binding a window: it has `_bars_since_entry` (a bar COUNT) but not `_since_entry` (the DataFrame), so it needs `since_entry(candles_df, open_pkg)` imported from `src/runtime/exit_levers.py` — the ONE canonical definition that `trend_donchian._since_entry` itself delegates to, never a third copy |
| `turtle_soup` · `vwap` | **0** | **not needed.** `turtle_soup` is `execution: shadow` and `vwap` is `enabled: false`, so neither is in the population — and both would cost more (neither has `_since_entry`, neither writes `entry_time`, and `vwap` writes no `risk_per_unit` at all, which `peak_r` requires) |

So Proposal A is two files, and those two cover **all 9** structurally-invisible legs.

**Why first:** this is `CY-20260906-TRADING-TRUTH`'s own sequencing rule — *repair the measurement before acting on what it says.* It converts 9 of 44 legs (20.5%) from *we did not look* into observable, and it is the only way the one-shot mechanism ever becomes measurable. **Proposal B's calibration is measured on a corpus that structurally excludes those 9 legs**, so A materially improves the evidence B is chosen from.

### Proposal B — a fleet-wide break-even floor, and NOT a number yet

The defensible change is a floor, not a threshold-and-target: **once `peak_r >= be_floor_r`, the trailing stop may never again sit below entry.** Expressed as one new fleet-wide lever consumed inside the existing ratchet, `max()`-ed against the Chandelier candidate so it can only ever tighten — preserving the monotone-ratchet invariant the units already guarantee.

**I am NOT proposing a value for `be_floor_r`, and that is deliberate.** The cost side is unmeasured (§4), and this repo has already paid for arming an exit-adjacent lever without a walk-forward: `FLIP_CONFIDENCE_THRESHOLD` ran live on real money from ~2026-08-10 with no walk-forward behind it, was finally measured on 2026-08-11, **lost against plain `hold`, and was disarmed the same day**. Proposing +0.75R off a gross-benefit table with no cost term would be that mistake with better arithmetic.

**The gate B should clear before it gets a number:** a path-aware harness sweep over `be_floor_r ∈ {0.5, 0.75, 1.0, 1.5}` that beats the ungated arm **net of the forgone continuation**, on the pooled corpus. The corpus is sufficient for that (§4); what is missing is the run, not the data.

---

## 6. What this document does not establish

- **It does not measure the one-shot legs.** All 8 are structurally invisible (§3). *We did not look.*
- **It does not establish that any banking threshold is profitable.** The benefit side is a lower bound; the cost side is unmeasured.
- **It does not re-grade any exit-matrix cell,** does not touch `TP_VENUE_CAP_PCT`, and does not arm `ICT_SCALP_EXIT_HEAD_MODE`.
- **It does not refute MI-155.** Every leg's `insufficient_n` verdict stands; §3 refines the *reason* for 9 of them, which MI-155 had no instrument to distinguish.
