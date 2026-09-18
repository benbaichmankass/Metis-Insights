# The scalp family's missing arm was the target-setting one — and the control that proves it barely proves anything

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> MI-312 · `2026-09-18` · successor to MI-307 ([`offline-mfe-distribution-2026-09-18.md`](offline-mfe-distribution-2026-09-18.md), #12503)
> · row `OI-20260906-THE-BRACKET-CALIBRATION-INSTRUMENT-EXISTS-AND-ITS-VERDICT-HAS-NOT-BEEN-ACTED-ON`

**⚠️ PROPOSE ONLY. Nothing in `config/` is touched. Every `tp_at_r` is Tier-3 and the operator's.**

---

## 0. The answer in one paragraph

MI-307 graded 8 legs `not_capped_capable` — the 7 crypto `ict_scalp_*` legs plus `fvg_range_15m` —
because their harnesses implement no `--tp-cap-pct`, so *"no capped arm, and therefore no positive
control, is possible"*. **That diagnosis is half wrong, and acting on it as written would have
manufactured a passing control.** These legs' live units apply **no venue clamp at all**, so the
harness *default* arm is already the live-comparable book; what did not exist was the **opposite**
arm — no way to switch the target off, so `mfe_r` stayed truncated at the live target and could say
nothing about what lay above. Same conclusion as MI-307 (no control was possible), inverse cause,
different flag. The arm now exists, the distribution is produced at **n = 45–1,664 per leg**, and
**the control passes on 8 of 8 legs within 0.71 pp** — but that number is not the good news it
looks like: measured per trade, the predicted and observed quantities **disagree on 21 of 7,338
joined trades (0.29%)**, so on this family the control is **near-tautological** and confirms the
plumbing rather than falsifying the instrument. What the arm is actually worth is the
**uncensoring**: the live 1.5R target sits almost exactly at the **p80** of the reachable
excursion and truncates the **p90 by 14.6%–50.9%**.

---

## 1. The premise correction, and why it is not a quibble

**MEASURED at `25bad2e31`.** Three independent places in this repo already carried the answer, and
MI-307 imports from one of them:

| probe | reading |
|---|---|
| `grep -rn "TP_VENUE_CAP_PCT\|0\.099" src/` | applied in **exactly four** unit modules — `trend_donchian`, `htf_pullback_trend_2h`, `fade_breakout_4h`, `squeeze_breakout_4h` — and **nowhere downstream**. `position_telemetry` / `bracket_calibration` / `target_expectation` import it to *report* `cap_r`, never to apply it. |
| `src/units/strategies/ict_scalp.py:525` | `tp = entry ± tp_at_r * risk` — **unclamped** |
| `src/units/strategies/fvg_range_15m.py:376` | `tp = R` (opposite boundary) — **unclamped** |
| `src/runtime/tp_venue_cap.py::CLAMPING_FAMILIES` | `{donchian, pullback, fade, squeeze}` — **not `scalp`, not `fvg`** |
| `m20_fleet_exit_sweep.tp_geometry_for` | names this exact condition **`live_parity_uncapped`**: *"no cap applied AND the live unit does not clamp, so this IS parity for that unit"* |
| `m20_fleet_exit_sweep.base_args` | already **refuses** to pass `--tp-cap-pct` outside `LIVE_TP_CAPPED_FAMILIES` |

So adding `--tp-cap-pct` to those two harnesses would have flipped
`harness_implements_flag` to `True` while `base_args` still declined to pass it: **a membership
boolean would have changed and no arm would have appeared.**

### 1.1 And the dispatched arm is a measured no-op here

⚠️ **Two populations, and the first one alone would have been the wrong number to publish.**

| population | clamp would bind on |
|---|---|
| BTCUSDT 5m, `2025-01-01..2025-04-01`, **n=45** (the validation slice) | **0 of 45** |
| all 8 legs, `2021-01-01..2026-09-16`, **n=45–1,664 per leg** | **0.00% – 6.49%** |

The slice said *never*; the full history says *rarely*. **Both are true of their own populations,
which is why the population is stated** — this memo's own earlier working figure was the slice's
zero, and it is corrected here rather than quietly carried. The per-leg figures are in § 3.

Either way the conclusion holds and is sharper than "inert": a `--tp-cap-pct 0.099` arm differs
from the live arm on **under 1 trade in 15 at worst and under 1 in 500 on four of the eight legs**,
so a positive control run against it would have been overwhelmingly a comparison of a book with
itself. On the validation slice the two runs were **byte-identical** — `unit_tp_cap_inert` on 45 of
45, identical outcomes, identical MFE. That is the contaminated instrument
`CY-20260906-TRADING-TRUTH` names: worse than no instrument, because it gets acted on.

**The reason is arithmetic, not subtlety.** A 1.5R target at these legs' risk sits at a **p50 of
1.17%–3.75% of entry** against a **9.9%** clamp — the clamp is 3× to 8× further away than the
target it would have to bind.

### 1.2 What the state should have asked

`not_capped_capable` asks *does the harness implement `--tp-cap-pct`*. The question that decides
whether a leg is gradeable is *can the harness reproduce this leg's live target, **and** can it also
run the uncapped basis*. For a clamping family those coincide; for a non-clamping one they come
apart, and these 8 legs are the population where they do. MI-307's `run_leg` is corrected in the
same PR to gate the capped arm on the **family** and return a new `capped_arm_not_applicable`
state, so it cannot label a scalp counterfactual live-comparable.

---

## 2. Method — two arms, and the one that is new

| arm | flag | what it is |
|---|---|---|
| **`live`** | *(none)* | the strategy's own target. **This IS live parity on a non-clamping family**, and it always was. |
| **`no_tp`** | `--no-tp` | target disabled; the trade runs to its stop or its timeout. The **target-setting** basis — MI-307's "uncapped" arm, and the thing that did not exist. |

**Instrument:** [`scripts/research/mi312_scalp_target_arms.py`](../../scripts/research/mi312_scalp_target_arms.py)
(16 selftests, 4 of them negative controls). It **imports** rather than re-derives:
`exit_capture.mfe_r_of`, `target_basis.resolve_target`, `tp_venue_cap.TP_VENUE_CAP_PCT`, and
`m20_fleet_exit_sweep.{classify, resolve_data, base_args, harness_implements_flag,
tp_geometry_for}`. Leg membership is **derived** from source — a family outside
`CLAMPING_FAMILIES` whose harness can disable its target — never a hardcoded list of eight, so it
cannot go stale.

**Per-trade provenance.** [`scripts/target_basis.py`](../../scripts/target_basis.py) is the one
definition of *what target did this arm place*, with five never-collapsed states (`unit_tp` ·
`unit_tp_clamped` · `unit_tp_cap_inert` · `no_target` · `no_unit_tp`) stamped on **every emitted
trade**. `no_target` and `no_unit_tp` yield the same `tp` (`None`) and mean opposite things — an arm
we asked for, versus a strategy that produced no target — so pooling them would let a
degenerate-signal bug read as a deliberate experimental arm. A reader never infers the arm from what
the caller believed it passed.

**Candles:** Binance Vision USDⓈ-M futures archive, `2021-01-01 → 2026-09-16`, fetched with
`scripts/ops/fetch_backtest_candles.py --source binance_vision` (`api.bybit.com` is geoblocked from
these containers — MI-307's finding, reconfirmed).

**The default arm is byte-identical to the unpatched harness**, and this was verified rather than
argued, because both live units are documented **verbatim ports** of these harnesses
(`fvg_range_15m.py:228`, `ict_scalp.py:736`): the harness from `HEAD` and the patched one were run
on the same slice and produced an identical summary, **45 of 45 identical rows, zero changed
top-level fields, zero changed meta values**, only two new provenance keys.

### 2.1 The control — and the measurement of how little it tests

Same form as MI-307 § 2.1, with MI-307's reach expression **unchanged**:
`P(mfe_r >= min(cap_r, x))`. Two substitutions are forced by this family rather than chosen:

* **`x` is per trade**, not a grid value — `ict_scalp` declares `tp_at_r` and `fvg_range` targets a
  range boundary whose distance in R differs every trade, so the would-be target is read from each
  row's own emitted `unit_tp` as `|unit_tp − entry| / risk`.
* **`cap_r` never binds**, because the family does not clamp. It is **evaluated, not special-cased**,
  and how often it *would* have bound is reported (§ 3) rather than assumed away.

⚠️ **THE CONTROL PASSES AND THAT IS NOT THE FINDING.** MI-307's arms were different books with
different trade counts (`sol_pullback_2h`: 200 vs 277), which is what made its 3.2 pp agreement
meaningful. Here:

| | value |
|---|---|
| legs agreeing | **8 of 8** |
| mean / median / max abs delta | **−0.37 pp** / **−0.45 pp** / **0.71 pp** |
| `entry_overlap` | **0.990 – 1.000** |
| **per-trade disagreement, entries joined across arms** | **21 of 7,338 — 0.29%** |

**So 99.71% of the control is a quantity compared with itself.** Given one entry and one set of
bars, *"uncensored MFE reached 1.5R"* and *"the live arm exited at the 1.5R target"* are nearly the
same event by construction; they come apart only on intrabar SL-first ordering and on a lever firing
first, which is what the 21 trades are. **The control therefore establishes that the MFE reader, the
target derivation and the exit classification agree — real, and much less than MI-307's.** Reporting
0.71 pp beside MI-307's 3.2 pp as if the tighter number were the better instrument would be exactly
backwards.

**Why the two families differ, mechanically** (POPULATION: the 4 `ict_scalp` 5m legs, full history):
`sl_hit` is **essentially identical across arms** — 263→264, 383→385, 294→294, 432→432 — because
removing a target never changes a loser. The `tp_hit` population redistributes into `timeout` and
`be_stop` (avax: `tp_hit` 383→0, `timeout` 786→1082, `be_stop` 69→150), and mean hold lengthens only
**~2.5 bars** (18.0→20.7). The break-even ratchet, armed at 1R, catches the reversal almost as fast
as the target did — so `next_eligible_idx` barely moves and the two arms keep the same entries.
MI-307's legs are **trailing** strategies, where removing the cap lets a winner run and genuinely
re-partitions the book.

---

## 3. The result, at stated n

**POPULATION: target-setting (`no_tp`) arm, ALL trades — not winners-only — `2021-01-01 → 2026-09-16`,
config-exact per leg.** All-trades for MI-307 § 3.1's reason: a take-profit is hit by any trade whose
excursion reaches it, including every trade that went +2R and reversed into a stop, and those are
exactly the trades a target would have changed.

| leg | n | MFE p50 | p80 | p90 | max | declared target (R) | reach @ target | clamp *would* bind |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `ict_scalp_avax_5m` | 1664 | 0.78 | 1.66 | **2.59** | 57.65 | 1.50 | 23.4% | 0.66% |
| `ict_scalp_sol_5m` | 1471 | 0.81 | 1.73 | **2.46** | 16.99 | 1.50 | 25.3% | 1.22% |
| `ict_scalp_xrp_5m` | 1210 | 0.79 | 1.68 | **2.59** | 13.43 | 1.50 | 24.9% | 0.58% |
| `ict_scalp_5m` | 1135 | 0.68 | 1.54 | **2.25** | 41.50 | 1.50 | 21.1% | 0.18% |
| `ict_scalp_sol_15m` | 663 | 0.68 | 1.47 | **2.12** | 8.47 | 1.50 | 18.4% | 6.49% |
| `ict_scalp_eth_15m` | 597 | 0.60 | 1.53 | **2.29** | 10.27 | 1.50 | 21.1% | 1.34% |
| `ict_scalp_xrp_15m` | 557 | 0.71 | 1.38 | **1.99** | 25.10 | 1.50 | 17.6% | 3.23% |
| `fvg_range_15m` | **45** | 0.63 | 2.21 | 4.53 | 10.61 | 2.10 *(variable)* | 31.1% | 0.00% |

⚠️ **`fvg_range_15m` is n=45 and does not belong in the same reading as the rows above it.** It
clears the 30-reading floor `winner_mfe_p80` uses and nothing more; it is `execution: shadow`; its
target is a **range boundary**, so `2.10` is a median of a varying quantity rather than a declared
constant; and its two arms have `entry_overlap` **1.000** with a **0.00 pp** delta, i.e. its control
is the emptiest of the eight. Quote it as a per-leg curiosity, never pooled with the scalp legs.

⚠️ **`ict_scalp_mgc_15m` is absent and that is correct** — MGC is non-crypto, no candle file
resolves, and it grades `no_data`. It is in MI-307's `no_data` bucket, not among the 8. So this unit
covers **7 live `ict_scalp` legs + 1 shadow `fvg` leg**.

### 3.1 The uncensoring — what the arm was actually for

**POPULATION: as above; `live` is the same leg's own live-parity arm over the same span.**

| leg | p80 live → no_tp | p90 live → no_tp | **p90 lift** |
|---|---|---|---:|
| `ict_scalp_avax_5m` | 1.53 → 1.66 | 1.72 → 2.59 | **+50.9%** |
| `ict_scalp_xrp_5m` | 1.58 → 1.68 | 1.80 → 2.59 | **+44.2%** |
| `ict_scalp_sol_5m` | 1.57 → 1.73 | 1.74 → 2.46 | **+41.1%** |
| `ict_scalp_eth_15m` | 1.51 → 1.53 | 1.71 → 2.29 | **+33.9%** |
| `ict_scalp_5m` | 1.52 → 1.54 | 1.72 → 2.25 | **+31.1%** |
| `ict_scalp_sol_15m` | 1.46 → 1.47 | 1.68 → 2.12 | **+25.9%** |
| `ict_scalp_xrp_15m` | 1.39 → 1.38 | 1.74 → 1.99 | **+14.6%** |
| `fvg_range_15m` | 1.77 → 2.21 | 2.53 → 4.53 | +78.8% *(n=45)* |

**The shape is the same on all seven scalp legs and it is the finding of this unit.** The declared
1.5R target sits **almost exactly at the p80** of the reachable excursion — p80 moves by at most
0.13R when the target is removed, and on three legs by ≤0.02R — while the **p90 is truncated by
14.6% to 50.9%**. A target at p80 is not obviously wrong; what was not measurable before is *where
it sits*, and it sits at the eightieth percentile of what the trade could have reached.

⚠️ **This is a REACH statement, not a P&L claim, and the distinction is the whole of § 4.**

---

## 4. What is proposed: nothing, and why that is not evasion

**No `tp_at_r` value is proposed.** Three reasons, and the first two are decisive on their own:

1. **It is Tier-3 and the operator's.** Per-leg target geometry is not a session's to set.
2. **A reach-rate is not a P&L claim.** § 3.1 says a target at 2.5R would have been *touched* on the
   legs' top decile. It says nothing about whether moving the target earns more — because a target
   further out also converts trades that currently take +1.5R into trades that give it back. Net-R is
   a sweep's question.
3. **The sweep has already answered that question for the family that has one.** MI-307 § 6 measured
   the e35 corpus at ~180 cells per leg over 3,413 rows carrying a real `tp_r`, and **12 of 19 legs
   have zero cells passing both IS and OOS**. This unit does not re-open it and commissions no new
   sweep.

**What is new and is the operator's to weigh** is that the scalp family's target was never chosen
against a measured excursion distribution at all — `tp_at_r: 1.5` is identical on all 8 legs,
including `ict_scalp_mgc_15m`, which trades a different asset class — and the distribution now
exists at n = 557–1,664 per leg where before there was nothing. **That is an input to a decision, not
the decision.**

---

## 5. What this does not establish

- **Not a P&L claim.** § 4.
- **Not a strong validation.** § 2.1 — the control passes and is 99.71% tautological on this family.
  Its value is that it is now *possible* and *measured*, where MI-307 could not run it at all.
- **Not a fidelity clearance.** Gate condition 1 (backtest↔live exit-location fidelity) is untouched
  and still unmet; MI-307 § 5.1 measured 44 of 44 legs `insufficient_n` and this unit changes none of
  that. Every figure here is simulated and `src/runtime/provenance.py` classifies every harness row
  `unverified`, correctly — a harness row carries no `exit_price_source` because no broker filled it.
- **Not applicable to the other 47 legs.** § 3.
- **Nothing was armed, merged into config, or proposed as a value.**

---

## 6. Filed, not fixed

- **`BL-20260918-BASE-ARGS-CANNOT-PRODUCE-A-LIVE-PARITY-FVG-RUN-…`** — `base_args` emits no
  `--exit-style` for fvg (the YAML declares none), so the harness falls through to its own `mid`
  default while the live unit targets the **far** boundary. Measured by running `base_args` and
  reading its 16 emitted flags. It **conditions three `honest_negative` cells** on the matrix's
  `fvg_range_15m` row (`stale_stop`, `giveback_stop`, `regime_flip_exit`) and is adjudicated into
  `known_caveats.conditions_verdicts` accordingly. This unit's driver injects `--exit-style far` for
  its own runs, which leaves every other `base_args` caller exposed. The remedy is a Tier-3 config
  declaration or a Tier-1 harness-default change that re-bases historical verdicts — not this unit's
  call.

---

## 7. Reproduce

```bash
python3 scripts/research/mi312_scalp_target_arms.py --selftest     # 16 checks, 4 negative controls
python3 scripts/ops/fetch_backtest_candles.py --symbol SOLUSDT --interval 5 \
    --source binance_vision --start-date 2021-01-01 --end-date 2026-09-16 \
    --output data/SOLUSDT_5m.csv                                   # repeat per leg symbol/tf
python3 scripts/research/mi312_scalp_target_arms.py --run
```

⚠️ `--reuse` exists only to fan the 16 runs across cores; **clear `runtime_logs/mi312/` after any
harness edit**, because it cannot know which harness source produced an emit already on disk.

Artifacts: [`mi312-scalp-target-arms-2026-09-18.json`](mi312-scalp-target-arms-2026-09-18.json)
(per-leg, both arms, full reach curves, `entry_overlap`, `clamp_would_bind_rate`). `data/*.csv` and
`runtime_logs/` are gitignored, so the candles and emits are not committed — the fetch above
rebuilds them.
