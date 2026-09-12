# M20 · U3a — the scalp target was UNSWEEPABLE, not unswept

> **Doc status:** `live` · category `research` · 2026-09-12 · MI-278 unit U3a ·
> object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` ·
> cycle `CY-20260906-TRADING-TRUTH`
>
> **Tier-1 research tooling.** Touches `scripts/backtest_ict_scalp.py` and its
> test. **No unit file, no `config/`, no `src/`, no order path.** The live
> `tp_at_r: 1.5` is **unchanged on every leg** — this builds the runner that
> makes a proposal *possible*, and the proposal itself is Tier-3 and is not
> made here.

## 1. What this unit found before it built anything

U3's assignment was the counterfactual for the mechanism U2 implicates. It could
not start, and **the reason is the finding**.

The coverage matrix (`docs/research/exit-refinement-coverage.json`) carries 8
`ict_scalp_*` rows whose `bracket_geometry` cell reads **`pending`** (7) or
`blocked:no_free_lane_candle_feed` (1), with the reason:

> *"Not swept in the 2026-08-20 run (scope was the trend/pullback/squeeze fleet).
> Crypto — the free `data.binance.vision` lane DOES cover these, so this is
> genuinely pending, not blocked."*

**The feed half of that is correct and is not disturbed here.** What is
understated is the word `pending`, which a reader takes to mean *schedulable,
nobody scheduled it*. MEASURED 2026-09-12, **no runner in the fleet could
produce those cells**, and the obstacle was three deep:

| # | producer | why it cannot sweep the scalp target | basis |
|---|---|---|---|
| 1 | `scripts/research/e35_bracket_geometry_sweep.py` | refuses the family **by design** — gates on `fam in (donchian, pullback, squeeze)`, emits `out_of_scope_family` | **dispatched run `34677990760`** planned `0 job(s); 7 not scheduled (out_of_scope_family=7)` — observed, not read off the source |
| 2 | `scripts/research/m20_fleet_exit_sweep.py` | passes **no target flag** on its `scalp` branch (its own comment: *"tp_at_r / timeout come from YAML / harness defaults"*). `--tp-r` appears only on the `fvg` branch and in the live-parity cap block, and in **both** it is a config-exact passthrough of a single `cfg` value — **never a grid** | read at `scripts/research/m20_fleet_exit_sweep.py:511,585` |
| 3 | **the root** — `scripts/backtest_ict_scalp.py` | exposed **no target CLI at all**: **30** `add_argument` calls, **none** a target; `tp_at_r` came from YAML only | grep with the count stated so the negative carries a denominator |

So even a caller willing to pass a grid had nothing to pass it to. **The scalp
target was not unswept. It was unsweepable.**

⚠️ **This re-grades no cell.** `pending` remains the honest status. What changes
is what a reader should expect it to cost to clear — which was the whole point
of adjudicating the row into the matrix's `conditions_verdicts`.

## 2. A second finding that SIMPLIFIES the work: the scalp target is unclamped

Before designing a grid I had to know whether a swept target is reachable in
production, because a grid above a venue clamp measures a book production cannot
run — the *"six arms shipping inert"* class `m20_fleet_exit_sweep` documents at
its own `--tp-cap-pct` default.

**MEASURED, with a positive control:**

| probe | `ict_scalp.py` | `trend_donchian.py` (control) |
|---|--:|--:|
| clamp references (`tp_venue_cap` / `TP_VENUE_CAP` / `_TP_SENTINEL_CAP_PCT` / `0.099`) | **0** | **6** |

`CLAMPING_FAMILIES` = `{donchian, pullback, fade, squeeze}` and
`CLAMPING_UNIT_MODULES` = `{trend_donchian, htf_pullback_trend_2h,
fade_breakout_4h, squeeze_breakout_4h}` — **`scalp` is in neither**, and a
whole-tree grep found **no other hardcoded `0.099`** and **nothing on the order
path** (`src/units/accounts/`, `src/core/`, `src/runtime/orders.py`).

**So a scalp target grid is reachable in production at any value**, and the
`--tp-cap-pct` plumbing every other family needs is **not** needed here. That is
a simplification, and it is stated with its control because a *negative* result
about a clamp is exactly the kind that must not be taken on silence.

## 2b. THE BIGGER FINDING — the harness force-closes at 24 bars and production never does

Building the runner surfaced something larger than the runner, and it changes §4.

**`scripts/backtest_ict_scalp.py` force-closes every trade at `timeout_bars`, default 24.
No `ict_scalp` leg has ANY time-based exit in production.**

Established from six directions, with a **positive control** — the same probe finds
`time_decay` in `vwap.py`, `fvg_range_15m.py` and `fade_breakout_4h.py`, so the silence
for scalp is a real absence and not a broken grep:

1. none of the 8 legs declares a temporal **exit** key (23 config keys read per leg);
2. the unit implements no timeout — 0 occurrences outside one comment, against a control
   of **19** params it does read;
3. `_base.py` — no time-based exit;
4. `order_monitor.py` — no global max-hold, and it never reads `time_decay_minutes`
   (which nothing in `src/` sets to a non-`None` value anyway);
5. `session_filter_enabled` is an **ENTRY** gate — it raises inside `order_package()` to
   refuse a *signal* — and it is `False` on all 8 legs regardless;
6. `m20_fleet_exit_sweep`'s `scalp` branch passes **no** `--timeout-bars`, so every
   fleet-sweep scalp cell inherits the 24.

### What it costs

⚠️ **POPULATION: ONE leg (SOLUSDT 5m), ONE quarter (2025-07-01 → 2025-09-30, 26,496
Binance-vision bars), n = 56–73, NO IS/OOS, NO walk-forward. A SMOKE, not a swept
verdict — do not quote it as one.**

| `timeout_bars` | `tp_at_r` | n | win% | net R | mean bars held | outcomes |
|---:|---:|---:|---:|---:|---:|---|
| 24 (harness default) | **1.5 (LIVE)** | 73 | 56.2 | **+5.356** | 17.2 | sl 22 · **timeout 28** · tp 23 |
| 100000 (parity) | **1.5 (LIVE)** | 65 | 47.7 | **+2.033** | 53.9 | sl 34 · tp 31 · timeout 0 |
| 24 | 3.0 | 73 | 53.4 | +5.274 | 20.5 | sl 23 · **timeout 45** · tp 5 |
| 100000 | 3.0 | 56 | 28.6 | **−0.962** | 125.4 | sl 40 · tp 16 |

At the **live** parameter the artificial timer ends **28 of 73 trades (38.4%)** and
supplies roughly **62% of the reported net R**. **The error is FLATTERING**, which is the
direction that gets acted on — the same shape as
`BL-20260820-HARNESS-DOES-NOT-MODEL-QUANTIZATION-REFUSAL`.

⚠️ **AND IT IS A DIFFERENT CORPUS FROM THE MATRIX'S OWN CELLS — split the two claims.**
The table above is `data.binance.vision` SOLUSDT 5m. Every M27 scalp verdict in
`exit-refinement-coverage.json` was measured on the trainer's `/home/ubuntu/m27_data`,
which `ict-scalp-exit-sweep.yml`'s own header records as a **different population**
(different start date, row count and md5, and a different yearly walk-forward fold set).
So: the **divergence** — harness force-closes at 24, production never closes on time — is
established from **code** and is population-independent. The **magnitude** (38.4% of
trades, ~62% of net R) is **corpus-specific and is NOT the matrix's number**, which nobody
has measured. Re-deriving it on `m27_data` is part of what closes the row.

### Why this is filed separately rather than as a duplicate

It is the `ict_scalp` instance of
`BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES`,
and **that row's own `resolution_criteria` clause (b) asks for exactly this
enumeration**. It also *strengthens* the parent: that row could only call the divergence
*"close to inert"* at its default of **200** and honestly marked that **inherited, not
established**. Scalp's default is **24**, and at 24 it binds hard and is now measured.

Filed as
`BL-20260912-THE-ICT-SCALP-HARNESS-FORCE-CLOSES-AT-24-BARS-AND-LIVE-HAS-NO-TIME-EXIT-AT-ALL`.
**Nothing is enacted** — the default was deliberately NOT changed here, because changing
it silently re-grades every existing scalp verdict.

## 3. What was built

### `--tp-at-r` (default `None` = byte-for-byte the old behaviour)

The override is written into **`cfg_overrides`**, so the **LIVE**
`order_package()` still computes the bracket — the harness never derives a
target of its own and the run stays config-exact in every other respect. That is
the whole design: `run_backtest` already does
`cfg = {"symbol": ..., "timeframe": ..., **cfg_overrides}` and reads
`pkg["tp"]` back from the live unit.

Two decisions were given **one owner** each, because the first draft inlined
both and my own mutation testing showed the tests were asserting a *copy*:

- **`resolve_tp_at_r_override(raw)`** — raises `TpOverrideError` at or below
  zero. At or below zero the target sits at or behind entry, which is not a book
  production can run: **refusing beats measuring it**.
- **`bank_rung_state(bank_frac, bank_at_r, effective_tp_at_r)`** — **four
  states, never collapsed**: `no_ladder` · `provable_no_op` ·
  `rung_below_target` · **`unknown` (*we could not establish the ceiling* —
  never a pass)**.

### Why `bank_rung_state` is part of *this* change and not a separate tidy-up

`ict_scalp` is a fixed-bracket strategy, so a bank rung at or above the leg's
`tp_at_r` is a **provable** no-op — the TP check returns on the same bar and
`bank_frac*tp + (1-bank_frac)*tp == tp`. Such an arm reports a clean *"no
change"* that is **indistinguishable from "tested and lost"**.

The harness already documented that trap in a comment. **`--tp-at-r` is what
makes a comment insufficient:** before it, the ceiling was the constant `1.5`
and an author could check the grid once by hand; now the ceiling is a *swept*
quantity, so the **same** rung is inert in one cell of a grid and live in the
next. The run therefore stamps the condition. `tp_at_r_effective` and
`tp_at_r_source` ship beside it so a reader can tell which ceiling applied.

### Verification

- **27 tests**, `tests/test_backtest_ict_scalp_tp_override.py`.
- **Mutation-tested — 4 of 4 mutants caught.** This is recorded because the
  *first* version of the suite caught only **1 of 2**: it kept a local copy of
  the rung ladder and asserted the copy, so collapsing `unknown` into a pass in
  production left every test green. **A test that restates the logic it tests is
  not a test.** The fix was to give the logic one owner and import it — the same
  discipline this repo applies to `monitor_unit_for` and `provenance.py`.
- The end-to-end case drives **`main()`** with a spy on `order_package` and
  asserts the value arrives, so it covers the whole `argparse → resolve →
  cfg_overrides → unit` hop. An earlier version asserted a *literal line of
  source*, which broke the moment the refusal was extracted — brittle, and not
  behaviour.

### Wired into the sweep, and PROVEN end to end

A flag with no caller is a dead feature, so `--tp-at-r` is wired into
`scripts/research/m27/ict_scalp_exit_sweep.py` — the script
`ict-scalp-exit-sweep.yml` runs, and the producer of every M27 scalp cell in the
matrix. It gains `tp_cells()` (the `bracket_geometry` grid) and a `--timeout-bars`
**live-parity axis** whose default passes nothing, i.e. byte-for-byte what every
existing cell was measured at.

Exercised on real data rather than asserted — one run, `SOLUSDT 5m`, 26,496
Binance-vision bars, split `2025-08-15`:

```
tp_at_r=1.5  timeout_bars=24 (harness default — NOT live parity; production has no time exit)
cells (7): ['tp0.75R','tp1R','tp1.25R','tp2R','tp2.5R','tp3R','tp4R']
IS 12960 / OOS 13536 bars · BASE IS 41 trades +15.841R · BASE OOS 32 trades −2.155R
→ all 7 graded honest_negative; CANDIDATES: NONE
```

Three things this establishes and one it does not. It establishes that the cells
**generate**, that the leg's own `1.5` is **excluded** (7 cells from an 8-point
grid), and that the banner **names the timeout as non-parity** rather than
letting it pass silently. It establishes **nothing about the target**: n = 41/32,
one leg, one quarter, and on the wrong corpus (`m27_data` is what the matrix
uses). Every cell reads IS-negative / OOS-positive, which at n ≈ 35 is the
signature of an unstable split — and the gate correctly refused all seven rather
than reporting the OOS half as a win.

## 4. PRE-REGISTERED grid — recorded BEFORE any run

Stated here so the grid cannot be chosen after seeing results.

- **Parameter:** `tp_at_r`, the only target knob the unit reads.
- **Grid:** `0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0` — **brackets the live
  `1.5` on BOTH sides.** A one-sided grid cannot distinguish *"further is
  better"* from *"we only looked further"*, and MI-277's question (winners
  cut short) biases toward looking only upward. `1.5` is included as the
  in-grid baseline.
- **Legs:** the 8 `ict_scalp_*` legs, per leg — never pooled. `tp_at_r` is
  declared per leg and a fleet-pooled verdict would hide a leg that disagrees.
- **Gate:** the `exit-refinement` standard — IS/OOS with a **yearly
  walk-forward**, `MIN_OOS_TRADES = 25`, Path A (`net_R` + `maxDD`) reported
  beside Path B (`net_r_per_capital_day`).
- **Costs:** the harness's own venue-aware model, on. `--tp-cap-pct` is **not**
  applied, per §2 — and that is a *derived* omission, not a default accepted
  silently.
- **⚠️ TIMEOUT — the axis §2b forces, and the grid is INVALID without it.**
  Every arm runs at BOTH `--timeout-bars 24` (the harness default, i.e. what
  every existing scalp cell was measured at) **and** a parity value large enough
  never to bind. Reporting only the default would reproduce the confound; reporting
  only parity would make the new numbers incomparable with every cell already in
  the matrix. The **parity arm is the one a Tier-3 proposal may cite**; the default
  arm exists solely to quantify the gap. ⚠️ The timer's grip TIGHTENS with the
  target (28 of 73 at 1.5, 45 of 73 at 3.0), so at the default the harness is
  **biased against exactly the direction this unit is testing** — a one-armed
  target sweep would have produced a confidently wrong answer.
- **Refusals declared in advance:** a cell whose `bank_rung_state` is
  `provable_no_op` or `unknown` is **not** counted as a tested arm. A leg under
  the OOS floor reports `underpowered`, which is **not** a negative result.

⚠️ **No sweep has been run and no verdict is claimed.** This unit built the
runner. U3b runs the grid; only then can U4 make a Tier-3 proposal, and only for
a lever that clears the gate.

## 4b. CONVERGENT EVIDENCE FROM THE LIVE BOOK — and it points AGAINST the queue's hypothesis

The sweep is a backtest. The U5 excursion instrument answers the same question
from a **methodologically independent** direction: ATR-unit favourable/adverse
excursion measured off the candle path, needing **no PnL, no exit price, no R
denominator and no provenance** — so it is immune to both the fabricated-PnL
class and to U2b's ratcheted-risk contamination.

Run on the live book: `/api/diag/journal` trades + order_packages (`limit=1000`),
restricted to MI-271's decision population, **705 (package, window) rows**,
windows 4/12/24/48h.

| family | 4h | 12h | 24h | 48h |
|---|--:|--:|--:|--:|
| **scalp** (fixed 1.5R target — the M20 subject), n=113–124 | **1.12** | **1.13** | **1.30** | **1.16** |
| donchian (trailing), n=36–43 | 0.53 | 0.65 | 0.96 | 1.69 |
| pullback, n=16–17 | 0.81 | 0.79 | 0.59 | 1.54 |

*(mean MFE ÷ mean MAE, both in ATR units. Medians tell the same story: scalp
0.96 / 1.10 / 1.28 / 1.10.)*

**For the scalp family the excursion profile beyond entry is essentially
SYMMETRIC at every horizon.** Favourable and adverse movement are the same
magnitude, so a wider fixed target has no free lunch available to it: the extra
favourable excursion it reaches for must be paid for with the extra adverse
excursion sitting alongside it. That is precisely the trade the parity backtest
measured — at `tp_at_r` 3.0, `tp_hit` fell 31 → 16 while `sl_hit` rose 34 → 40
and net R went **+2.03 → −0.96**.

For donchian and pullback the ratio is **below 1** at 4–24h — adverse excursion
*dominates* — so holding longer is worse still there.

### ⚠️ What this instrument does NOT say, and why the caveat cuts one way

The excursion is measured **from entry over a fixed window, regardless of when
the trade actually closed**, so it answers *was more movement available?* and
not *could any rule have captured it?* In particular **it ignores path order**:
an 8-ATR favourable excursion arriving *after* a 6-ATR adverse one is
unreachable by any stop-respecting rule, and this measurement counts it anyway.

**That bias runs TOWARD the hold-longer hypothesis, not against it** — the
instrument is generous to the case it is being used to test, and the case still
does not clear. So the reading here is conservative, which is the direction a
negative result needs to be.

⚠️ Two instruments agreeing is not a verdict either. Both are pre-gate: the
backtest is a **screen** with no walk-forward, and this is an observational
profile with per-leg weekly n of 3–9. **U3b's gated run decides**; this is why
the pre-registered grid brackets `1.5` on BOTH sides rather than only widening.

## 4c. ⚠️ THE LEVER IS PROBABLY NOT THE TARGET — IT IS THE BREAK-EVEN RATCHET

**This section reverses §4b's reading and is the most important result in the
unit.** It exists because I caught a fidelity defect in my own measurement: the
first parity grid omitted `--sim-breakeven`, so it ran **without the break-even
ratchet that U1 established is armed on all 8 scalp legs** (`be_offset_bps: 15`,
baseline-on at 1R). Re-run config-exact:

| `tp_at_r` | n | win% | net R | maxDD | outcomes |
|---:|--:|--:|--:|--:|---|
| 0.75 | 72 | 72.2 | **+7.911** | 6.15 | tp 52 · sl 20 |
| 1.0 | 72 | 62.5 | +6.839 | 8.55 | tp 45 · sl 27 |
| **1.5 (LIVE)** | 65 | 55.4 | **−0.695** | 8.64 | sl 29 · tp 24 · **be_stop 12** |
| 2.0 | 64 | 56.3 | +1.402 | 9.08 | sl 28 · tp 18 · **be_stop 18** |
| 3.0 | 60 | 53.3 | **−8.781** | 9.84 | sl 28 · **be_stop 24** · tp 8 |

### The 2×2, with an internal control that cannot be argued with

| | BE armed (live) | BE disarmed |
|---|--:|--:|
| `tp_at_r` 0.75 | **+7.911** | **+7.911** |
| `tp_at_r` 1.5 (live) | **−0.695** | **+2.033** |

**The 0.75 row is IDENTICAL to three decimal places, and that is the control**:
the ratchet arms at 1R, so at a 0.75R target it is unreachable by construction
and must have exactly zero effect. It does. That the mechanism's own prediction
holds to the digit is what makes the 1.5 row credible.

Decomposed on this leg-quarter:

- disarm the ratchet at the **live** target — **+2.729 R**
- narrow 1.5 → 0.75, ratchet-free on both sides — **+5.878 R**
- both — **+8.606 R**

So §4b's *"narrower is better"* is **half an artifact of the ratchet**: part of
what narrowing buys is simply putting the target below the arming threshold.
The two levers are entangled in the live config and are separated only by the
2×2 above.

### The mechanism, and why it matches MI-277's signature exactly

`be_stop` share rises monotonically with the target — **0.0% / 0.0% / 18.5% /
28.1% / 40.0%** — because a wider target keeps the trade above 1R for longer,
giving the ratchet more chances to arm and stop it at entry.

⚠️ **That is precisely the fingerprint MI-277 measured on the live book**:
winner hold collapsing **5.17h → 1.90h** while **losses stayed at exactly 1R**.
A break-even ratchet produces both halves by construction — it truncates trades
that reached 1R (shortening winner hold) and cannot touch a loser (which never
reaches 1R, so losses stay at exactly −1R). No other lever in the U1 inventory
has that shape.

### ⚠️ The tension with U1, stated rather than resolved by preference

U1 tested *"the break-even ratchet is the collapse mechanism"* against the LIVE
JOURNAL and **refuted it — as dominant, on n=5**. This is a BACKTEST on a
different population, a different instrument and a different quarter. **Both can
be true**: the ratchet can be costly here and still not be the dominant driver
of the live collapse. Do not read this section as overturning U1, and do not
read U1 as closing this. What it does is make the ratchet the **highest-value
thing left to measure properly**, which it was not before.

### What this is NOT

⚠️ **ONE leg, ONE quarter, n=60–72, NO IS/OOS, NO walk-forward, and on
`data.binance.vision` rather than the `m27_data` corpus every matrix cell uses.
A SCREEN.** The surface is also **non-monotonic** at 2.0 (+1.402 against 1.5's
−0.695), which at this n is a noise warning and not a shape to fit to.
`be_offset_bps` is **Tier-3**. Nothing here is a proposal; it is the evidence
that says which proposal is worth building, and U3b's gated run decides.

## 5. Landing

**This PR declares `landing: hold`.** `check_pr_landing.py::TIER1_SURFACE`
allowlists `scripts/{ci,ops,research,reports}/**` and **not** top-level
`scripts/`, so `scripts/backtest_ict_scalp.py` cannot self-land whatever its
tier. That allowlist polarity is deliberate (*"a path not matched here cannot
self-land"*) and is **not** routed around here — the PR asks for a merge click
instead.

## 6. Filed

The U3 row
`BL-20260912-THE-SCALP-FAMILY-S-TARGET-HAS-NO-SWEEP-E35-EXCLUDES-IT-BY-DESIGN-AND-THE-FLEET-SWEEP-DOES-NOT-SWEEP-A-TARGET`
stays **open**: this PR closes only its **root** clause (a target CLI now
exists). The fleet-sweep wiring — a `tp_at_r` cell that actually schedules the
grid — is **not** built, so the family still has no *sweep*, only a *runner*.
Closing the row on the runner would be the merged-not-observed collapse this
repo keeps paying for.
