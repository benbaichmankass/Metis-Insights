# `fvg_range_15m` IS a verbatim entry port — the weak half is the target, and the contract is wrong

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> MI-319 · `2026-09-18` · successor to MI-312 ([`scalp-family-target-arms-2026-09-18.md`](scalp-family-target-arms-2026-09-18.md), #12515) and MI-318 ([`scalp-control-design-2026-09-18.md`](scalp-control-design-2026-09-18.md), #12551)
> · queue item `_lane_queues.research.then[1]`

**⚠️ PROPOSE ONLY. Nothing in `config/` or `src/` is touched, and
`scripts/backtest_fvg_range.py` is imported and read, never edited — so no verdict any other run
produced can have been changed by this one.**

---

## 0. The answer in one paragraph

This unit was dispatched on the premise that `fvg_range_15m`'s harness *"reimplements entry and
computes its own target, so it is NOT a verbatim port like scalp's."* **Half of that is refuted and
half of it is confirmed, and the two halves are claims about different things — which is why they
are graded separately here rather than under one verdict.** The **entry** block *is* a verbatim
port: 18 of 19 harness rejection-disjuncts and 18 of 20 live ones are byte-identical after a
naming-only fold, the three residuals are each classified as something that is not an entry rule,
**zero are unclassified**, and fed the same candles the two implementations accept the same bars —
**14 of 14 accepts matched, with entry and target agreeing exactly, the stop to 4.3e-09, and no bar
accepted by one side alone**, over **622 graded bars**. The **target** claim is correct: the harness
default is `mid` and the live unit targets the opposite boundary (`far`), which is MI-312's already
filed `base_args` defect confirmed from the harness side. And a third thing, which neither claim
anticipated and which is the most useful output here: **the unit's own declared minimum candle count
is not sufficient for its own ADX gate.** At the 51 bars it declares enough, the chop verdict
disagrees with the validated one on **21.9% of bars (136 of 622)**; at the 250 the runtime actually
fetches it has converged (**0 flips of 423**). The deployed path is fine; **the contract is wrong and
nothing enforces the number that makes it true**, and that shape holds on at least one sibling unit.

---

## 1. What was actually in dispute

Two claims, pointing opposite ways, neither measured:

| source | claim |
|---|---|
| `src/units/strategies/fvg_range_15m.py:228` (the `order_package` docstring) | *"The logic is a VERBATIM port of `scripts/backtest_fvg_range.py::run_backtest`'s per-bar entry block … so live signals match the validated simulation."* |
| the MI-319 dispatch | the harness *reimplements* entry and computes its own target; **not** a verbatim port like scalp's |

This repo has already settled how to break that tie. `scripts/research/trend_harness_divergence.py`
says it in terms: *"a `def run_backtest(...)` node is a fact about the code, a docstring saying
'RETIRED' is a claim, and a guard that trusts the claim is cheaper to lie to than to satisfy."* So
the docstring is the **thing graded**, never the evidence — and the dispatch gets the same treatment.

⚠️ **AND THE DOCSTRING'S SCOPE IS LOAD-BEARING.** It claims the *per-bar entry block* and says
nothing about the target, which the harness computes **outside** that block (`exit_style`). Grading
both under one verdict would let a true entry claim launder a false target one. § 2 and § 4 are
therefore separate sections with separate verdicts, and neither carries to the other.

---

## 2. Probe P1 — the code: do they reject on the same predicates?

**POPULATION: every `if <test>:` in `run_backtest` whose body is only `continue` (plus the loop's own
`i += 1`), and every `if <test>:` in `order_package` whose body is only `raise` — read from each
file's AST at `24efd678b`. 13 gates each; flattened at top-level `or` into 19 harness disjuncts and
20 live ones.**

Comparing whole predicates first grades this **`divergent`**, and that verdict is an artifact of
syntax rather than of logic: the harness writes the degenerate-target guard as **two** `if`s and the
live unit writes it as **one** `if A or B`. Those reject exactly the same bars. So the comparison is
made at the level of **disjuncts**, which folds that case structurally rather than by an allowlist of
strings someone noticed — the difference between a check and a presence-only marker.

| | value |
|---|---:|
| shared disjuncts | **18** |
| harness-only | **1** |
| live-only | **2** |
| **unclassified divergences** | **0** |

Each residual is classified by the **names it references in the AST**, not by its text:

| residual | class | why it is not an entry rule |
|---|---|---|
| `i < next_idx` | `harness_loop_bookkeeping` | the harness's entry-overlap suppression. The live unit is called once per tick against a position book and has no such notion, by construction. |
| `len(candles_df) < needed` | `live_input_validation` | the harness validates its frame once before the loop; the live unit validates its argument on every call. |
| `missing_cols` | `live_input_validation` | same — an OHLC-column check the harness performs at load. |

### 2.1 What P1 refuses to grade, found by pointing it at the sibling leg

MI-312 § 2 says *"both live units are documented **verbatim ports** of these harnesses
(`fvg_range_15m.py:228`, `ict_scalp.py:736`)"*. **Half of that citation does not hold, and it is worth
recording because the premise is load-bearing for seven `execution: live` legs.** `fvg_range_15m`
makes the claim twice (lines 180 and 228). **`ict_scalp.order_package` makes no port claim at all** —
read, not grepped-for-absence — and `ict_scalp.py:736` is the `stale_stop` **monitor** docstring
claiming *exit-lever* parity for one lever. An entry-port claim was generalised from an exit-lever one.

**So the obvious next step was to point P1 at the `ict_scalp` pair, and it returned a confident
`divergent` for a pair it cannot see.** `backtest_ict_scalp.py::run_backtest` holds **17 `if` nodes
and four `continue`s** — its entry gating is not written as bare rejection gates — so the probe found
**3 harness gates against 11 live ones**, and 8 of the live side's gates had nowhere to match. That
residual is an artifact of the idiom, not a divergence: **sub-class C, an unasserted denominator,
committed by the module that exists to grade other people's claims.**

P1 now tests an **idiom precondition first** and grades **`idiom_mismatch`** — *we could not look*,
a third state distinct from both `divergent` and `could_not_parse` — when the two sides' gate counts
are too unequal for a set comparison to mean anything. The ratio is reported beside every verdict:

| pair | harness gates | live gates | ratio | verdict |
|---|---:|---:|---:|---|
| `fvg_range_15m` | 13 | 13 | **1.00** | graded |
| `ict_scalp` | 3 | 11 | **0.27** | **`idiom_mismatch`** — refused |

⚠️ **The 0.5 threshold is CHOSEN and its n is TWO**, which is stated in the code rather than implied.
The polarity is what makes it safe: a false `idiom_mismatch` costs a **refusal a reader can act on**,
a false `divergent` costs a **wrong verdict about whether a live unit matches the book it was
validated against**.

⚠️ **This says nothing about whether `ict_scalp` IS a port.** It is now **unmeasured** rather than
wrongly measured, and measuring it needs a probe that can read that harness's idiom. That is the next
unit, not this one's finding.

**Verdict P1: `equivalent_modulo_order`.** The ordering differs — the live unit computes the target
and its degenerate guard *before* confidence, the harness *after* — and that reorders nothing that
matters, because neither gate's input depends on the other's output, so the accepted set is
unchanged. § 3 confirms that behaviourally rather than leaving it as an argument.

---

## 3. Probe P2 — the behaviour: fed the same candles, do they accept the same bars?

**POPULATION: `data/btc_1m_sample.csv` resampled to 15m by the harness's own `_resample` — 673 bars,
`2026-03-15 09:45` → `2026-03-22 09:45`, symbol BTCUSDT (the unit's default; `config/strategies.yaml`
declares none for this leg). 622 bars graded per arm — the warm-up floor to `n-2`, the harness loop's
own bound. Harness run at `--exit-style far`, the live-parity arm per § 4.**

Run in **two window modes**, because they answer different questions: `full_prefix` gives the live
unit every bar before `i`, exactly as the harness has, isolating **code** divergence; `live_250`
gives it the 250-bar tail `strategy_signal_builders.fvg_range_15m_signal_builder` actually fetches
(read from its AST, not assumed), isolating the **input seam**.

| arm | bars graded | both accept | both reject | harness-occupied | **live-only** | **harness-only** | state |
|---|---:|---:|---:|---:|---:|---:|---|
| shipped defaults | 622 | 0 | 622 | 0 | 0 | 0 | **`no_accepts_in_population`** |
| relaxed touches+width | 622 | 3 | 598 | 21 | **0** | **0** | `agree` |
| relaxed all gates | 622 | 11 | 280 | 331 | **0** | **0** | `agree` |

**Both window modes produced identical tables**, so nothing in this population separates the code
question from the seam question — consistent with § 5, where the seam is measured directly and found
converged at 250.

On the 14 matched accepts, field agreement over the whole set:

| field | max absolute difference |
|---|---:|
| direction | identical on 14 of 14 |
| entry | **0.0** |
| target | **0.0** |
| confidence | **0.0** |
| stop | **4.3e-09** (the live unit rounds to 8 dp; the harness does not) |

⚠️ **THE SHIPPED-PARAMETER ROW IS `no_accepts_in_population` AND THAT IS NOT AGREEMENT.** Two
implementations that both accept nothing agree about nothing. On this slice the shipped gates admit
no trade at all, so the shipped-parameter accept side is **untested here** — the honest reading, and
the reason the relaxed arms exist. Relaxing gates is not a weaker test of a **parity** claim: the
code does not know which parameters it was handed, and a larger accept count is more power, not less.

⚠️ **AND REJECT-SIDE AGREEMENT IS AGREEMENT ABOUT THE ANSWER, NOT THE REASON.** The live unit names
its rejecting gate in the `ValueError` it raises; the harness just `continue`s, and reading which of
its gates fired would mean editing it, which this unit refused to do. So the 1,500 both-reject bars
establish that neither side wanted those bars, and not that they declined them for the same reason.
The live-side census is recorded for what it is worth (shipped arm, 622 rejects): `adx_regime` 421,
`touches` 66, `width_bounds` 64, `no_boundary_edge` 58, `price_outside_range` 8, `no_fvg` 5,
`unknown_gate` **0**.

---

## 4. Probe P4 — the target, graded on its own

| | |
|---|---|
| live unit | `tp = R` (long) / `tp = S` (short) — the **opposite range boundary**, unclamped (`fvg` is absent from `tp_venue_cap.CLAMPING_FAMILIES`) |
| harness styles | `mid`, `far`, `tp1r` |
| harness **default** | **`mid`** |
| the live-parity arm | **`far`** |

**The dispatch's target claim is correct.** This is the same defect MI-312 filed as
`BL-20260918-BASE-ARGS-CANNOT-PRODUCE-A-LIVE-PARITY-FVG-RUN-BECAUSE-THE-YAML-DECLARES-NO-EXIT-STYLE-AND-THE-HARNESS-DEFAULT-IS-THE-WRONG-TARGET`,
reached here from the opposite direction — MI-312 measured it by running `base_args` and reading its
16 emitted flags; MI-319 reads the harness's own default and the live unit's own assignment. Two
independent probes, same conclusion, so the row is corroborated rather than restated. **No new row
is filed for it.**

⚠️ **This is why § 2's verdict does not clear the leg.** The entry block being a faithful port and
the shipped harness run being live-comparable are different facts, and only the first is established.

---

## 5. Probe P3 — the finding neither claim anticipated

Exactly one quantity in the entry block is **history-length dependent**, and it was identified by
reading the formulas rather than by guessing: `_atr` is `rolling(period).mean()` (finite window),
`range_hi`/`range_lo` are rolling extrema, the touch count and the FVG scan are finite slices — but
`_adx` is a **three-deep `ewm(alpha=1/period, adjust=False)` recursion seeded at the first
observation**, so its value at bar `i` depends on everything before `i`. Whether that matters is a
measurement, not an argument.

**POPULATION: the same 673 bars. Reference = ADX over the full prefix; comparison = ADX over a tail
window of W bars ending at the same bar. `adx_period` 14, `adx_max` 20.0 (the unit's own defaults).
The graded count falls with W because a W-bar window needs W bars of prefix.**

| W | graded bars | **gate flips** | mean \|ΔADX\| | max \|ΔADX\| | state |
|---:|---:|---:|---:|---:|---|
| **51** — the unit's own declared minimum | 622 | **136 (21.9%)** | 6.25 | 31.25 | `diverges` |
| 100 | 573 | 9 (1.57%) | 0.255 | 1.83 | `diverges` |
| **250** — what the runtime fetches | 423 | **0 (0.00%)** | 7.3e-06 | 5.2e-05 | **`converged`** |

The tolerance for `converged` is **0.01 ADX points, stated rather than tuned**, and it is checked
rather than asserted: the closest any graded bar's reference ADX sat to the 20.0 threshold is
**0.0278**, i.e. **535× the largest residual at W=250**, so that residual could not have moved a
verdict. (An earlier draft used `1e-6` and duly graded the deployed window `diverges` on a
5.2e-05 residual that flipped nothing — a verdict driven by the threshold instead of by the
condition, which is the sub-class A defect this repo has a guard for. It is recorded here rather
than quietly corrected.)

**So the live path is fine today and the contract is wrong.** The unit's guard is
`needed = max(range_lookback, atr_period, adx_period) + 3` = **51**, and it raises below that — i.e.
the unit *declares* 51 candles sufficient. A caller honouring that declaration would get a regime
verdict disagreeing with the validated one on more than a fifth of bars, and nothing anywhere
enforces the 250 that makes the declaration true. The only caller today is
`strategy_signal_builders.fvg_range_15m_signal_builder`, which passes 250 — measured, with a
positive control: a grep across `src/`, `scripts/` and `tests/` finds that builder as the sole
importer of `order_package`. **Latent, not live.**

### 5.1 It is a class of at least two, and the third could not be graded

**POPULATION: every file in `src/units/strategies/` that both defines `_adx` and contains the
recursive `ewm(alpha=alpha, adjust=False)` — found by probing for the recursion, so a unit that grows
one later is discovered rather than remembered. n = 3.**

| unit | declared minimum | builder fetches | gap |
|---|---:|---:|---|
| `fvg_range_15m` | 51 | 250 | `minimum_below_fetched` |
| `fade_breakout_4h` | 50 | 200 | `minimum_below_fetched` |
| `hf_vwap_revert` | 147 | — | **`could_not_grade`** — its builder's `limit=` is not a constant |

⚠️ **The 21.9% is measured for `fvg_range_15m` ALONE and must not be quoted for the other two.**
Their divergence needs their own candles, symbols and periods; what § 5.1 establishes is the
*structural* exposure — the declared minimum sitting far below what is actually passed — and nothing
about its size elsewhere. `could_not_grade` on `hf_vwap_revert` is *we could not look*, not a clean
reading.

---

## 6. What this does **not** establish

- **Not a fidelity clearance.** Gate condition 1 (backtest↔live exit-location fidelity) is untouched
  and still unmet. This is a harness-vs-live-**unit** comparison run entirely offline; no broker
  filled anything, and `src/runtime/provenance.py` would classify every row here `unverified`.
- **Not an exit-side result.** The docstring claims the entry block, and this unit grades the entry
  block and the target. The live `monitor()` against the harness's SL→TP→timeout precedence is **not
  compared here** and remains the open half of fvg's parity story.
- **Not a shipped-parameter result on the accept side.** 0 accepts at shipped gates on this
  population (§ 3). The matched accepts are 14, from two relaxed arms — a small denominator, stated.
- **Not a statement about other symbols, timeframes or spans.** One symbol, seven days, one
  timeframe. MI-312's `fvg_range_15m` row is n=45 over 5.7 years; this is a different and far
  smaller slice and the two must not be pooled.
- **Not a proof of ADX convergence against unbounded history.** The reference is a 673-bar prefix.
  It establishes that 250 agrees with *more than 250* to 5.2e-05 on this data; the geometric decay of
  an `alpha = 1/14` recursion makes the extension plausible and this did not measure it.
- **Nothing was armed, merged into config, or proposed as a value.**

---

## 7. Filed, not fixed

- **`BL-20260918-FVG-RANGE-15M-DECLARES-51-CANDLES-SUFFICIENT-WHILE-ITS-OWN-ADX-GATE-NEEDS-ABOUT-250-AND-DISAGREES-ON-21-9-PERCENT-OF-BARS-AT-THE-DECLARED-MINIMUM`**
  — § 5. Tier-2 (`src/`), so proposed and not applied. The remedy is to derive the warm-up from the
  ADX recursion's own convergence rather than from `max(period…) + 3`; the one-line version is to
  raise `needed` to the window the builder already passes, which makes the declaration true without
  changing any live behaviour.
- The target defect is **already filed by MI-312** and is corroborated here, not re-filed (§ 4).

---

## 8. Reproduce

```bash
python3 scripts/research/mi319_fvg_parity.py --selftest   # 37 checks, 14 negative controls
python3 scripts/research/mi319_fvg_parity.py --run        # writes the JSON artifact
```

The harness is loaded by spec and registered in `sys.modules` before `exec_module` — an unregistered
spec-loaded module makes `dataclasses` resolve annotations through `sys.modules[cls.__module__]`,
get `None`, and die inside `_is_type`. The live unit is imported **before** the harness, because
`backtest_fvg_range.py` puts `<repo>/scripts` on `sys.path[0]` at import time and `scripts/ml/` then
shadows the real top-level `ml` package
(`BL-20260918-SCRIPTS-ON-SYS-PATH-SHADOWS-THE-ML-PACKAGE`).

Artifact: [`mi319-fvg-parity-2026-09-18.json`](mi319-fvg-parity-2026-09-18.json)
