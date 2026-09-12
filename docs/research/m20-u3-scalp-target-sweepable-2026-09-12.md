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

## 4d. FIRST RESULTS — the SCREENING arm, 4 legs, and it answers nothing on its own

Run [`34681993688`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/34681993688)
(`cells=bracket_geometry`, `walkforward=false`, `timeout_bars` unset ⇒ **the
harness default of 24**) plus the local `sol_5m` smoke run on the same
config-exact base. Population per leg is the `m27_data` file the workflow
pulls, split at **2025-07-01**; every cell is the 7-value grid of §4 with the
leg's own `tp_at_r: 1.5` excluded as the baseline.

| leg | BASE IS R | BASE OOS R | Σ IS ΔR | Σ OOS ΔR | IS/OOS sign | cells passing |
|---|--:|--:|--:|--:|:--:|--:|
| `ict_scalp_eth_15m` | 44.36 | 15.73 | −42.12 | +4.71 | **flips** | 0/7 |
| `ict_scalp_sol_15m` | 23.65 | 20.46 | +75.10 | −20.15 | **flips** | 0/7 |
| `ict_scalp_xrp_15m` | 20.87 | 15.71 | −44.49 | +21.75 | **flips** | 0/7 |
| `ict_scalp_sol_5m` | 81.33 | 51.27 | +19.20 | −27.19 | **flips** | 0/7 |
| `ict_scalp_xrp_5m` | 106.01 | 24.10 | −39.91 | +19.97 | **flips** | 0/7 |
| `ict_scalp_avax_5m` | 147.04 | 43.14 | +9.77 | +9.48 | same (both +) | 0/7 |
| `ict_scalp_5m` (BTC) | 88.70 | 40.18 | −10.98 | −39.47 | same (both −) | 0/7 |

⚠️ **UPDATED 2026-09-12T09:0xZ from 4 legs to the COMPLETE 7, and the headline
claim moved with it.** This section first read *"on 4 of 4 legs the dominant
direction REVERSES"*. Over all seven it is **5 of 7**, and the two exceptions
matter: `avax_5m` agrees POSITIVE in both windows and `btc_5m` agrees NEGATIVE
in both. `avax_5m` is exactly the leg that goes on to produce the only widening
candidates at parity (§4h), so the exception is not noise — do not re-quote the
4-of-4 figure.

**0 of 28 cells cleared the IS+OOS gate. Every one is `honest_negative`.**

### The sign structure is the actual finding

On **5 of 7 legs the dominant direction of the target effect REVERSES between
IS and OOS** (4 of 4 when this was first written; see the note above). `sol_15m` says wider is emphatically better in-sample — ΔR rising
monotonically to **+21.45R at 4R against a 23.65R book, a near-doubling** — and
uniformly worse out-of-sample, on all seven cells. `xrp_15m`, `eth_15m` and
`sol_5m` say the opposite, also on 6–7 of 7.

⚠️ **A cell-level agreement rate must NOT be quoted as a statistic.** The seven
cells within a leg are nested variations over the *same* trades, so they are
nowhere near independent and the effective n is the LEG count (**7**), not 49. What survives is the leg-level statement above: four legs,
four reversals, no consistent direction.

That is the signature of period character rather than of a lever. A target that
was genuinely cutting winners short would improve **both** windows wherever it
binds; instead the cells that help in one window hurt in the other.

### The bias is now MEASURED, not inferred — and it is monotone across the grid

§2b established the truncation in aggregate. This measures it **per cell, on the
exact grid being swept**, from the sweep's own `by_outcome` map. Population:
`/tmp/sol5m.csv`, 26,496 SOLUSDT 5m bars, split `2025-08-15`, `--sim-breakeven`,
24-bar timeout. **One leg and a small corpus** — this establishes the SHAPE, not
a fleet magnitude.

| target | IS n | IS timeout | share | OOS n | OOS timeout | share |
|---|--:|--:|--:|--:|--:|--:|
| 0.75R | 42 | 9 | **21.4%** | 34 | 4 | **11.8%** |
| 1.0R | 42 | 14 | 33.3% | 34 | 9 | 26.5% |
| 1.25R | 41 | 14 | 34.1% | 33 | 10 | 30.3% |
| **1.5R (live)** | 41 | 15 | **36.6%** | 32 | 11 | **34.4%** |
| 2.0R | 41 | 17 | 41.5% | 32 | 13 | 40.6% |
| 2.5R | 41 | 21 | 51.2% | 32 | 14 | 43.8% |
| 3.0R | 41 | 23 | 56.1% | 32 | 14 | 43.8% |
| 4.0R | 41 | 24 | **58.5%** | 32 | 15 | **46.9%** |

**The share rises monotonically with the target in both windows** — IS 21.4% →
58.5%, OOS 11.8% → 46.9%. At 4R **the majority of trades never resolved at a
level**; they were killed by a clock production does not have. The wide cells
are therefore not merely noisy, they are graded on a substantially different
population from the narrow ones, and the difference runs in the direction that
makes them look worse.

**Every cell verdict now prints this** (`timeout_share` /
`_fidelity_suffix`), so a future reader cannot mistake a screening verdict for
a parity one. It returns **`None`, never `0.0`**, when a window has no trades
or no outcome map — `0.0` would read as *perfect fidelity*, which is the most
flattering wrong answer available.

⚠️ **A candidate finding was RAISED AND REFUTED here, recorded so it is not
re-raised.** On this smoke corpus five widening cells tied IS max-drawdown to
four decimals (`ΔDD = +0.000000`), which looks like the gate rejecting cells on
a criterion they cannot move — `tp2R` improves net R in **both** windows
(+1.09 IS, +0.80 OOS) and improves OOS drawdown, and is discarded on an exact
tie. **It does not reproduce**: across the four 124k-bar corpora the count of
exact IS ties is **0/7 on every leg**, against 5/7 here. It is a small-sample
artifact of a 26k-bar run, **not** a gate defect, and `tp2R` is not a
candidate — its grading is simply uninformative at this timeout.

### ⚠️ AND THIS ARM CANNOT SETTLE THE QUESTION — ⛔ BUT NOT FOR THE REASON GIVEN BELOW; SEE §4e

Every figure above was produced with the **24-bar force-close of §2b**, which
production does not have on any `ict_scalp` leg. That defect is **not neutral
across this grid** — §4d's own table shows it catching 21.4% of trades at
0.75R and 58.5% at 4R.

⛔ **THE PARAGRAPH THAT STOOD HERE INFERRED THE WRONG DIRECTION FROM THAT, AND
IT IS REPLACED RATHER THAN SOFTENED.** It read: *"a wider target needs more
bars to reach, so a 24-bar guillotine truncates preferentially the trades a
wider target would have won … its own defect penalises the wider cells. A
negative here is expected whether or not the lever is real."* **The premise is
true and the conclusion is backwards.** The clock does bind harder at wider
targets — that is what the rising share means — but binding harder *helps*
those cells, because at 4R most of the trades it catches are ones that would
otherwise have run on to the stop. **I conflated "the mechanism binds harder"
with "the mechanism hurts more."** §4e measures it, §4f reproduces it on a
third leg.

⚠️ **AND §2b IS NOT AT FAULT — do not read this as a correction to it.** §2b
says in terms that *"the error is FLATTERING"*, and its own table already
carries the answer: at `tp 3.0` the harness reports **+5.274 R** at a 24-bar
timeout and **−0.962 R** at parity. The misreading was introduced here, in
§4d, by attributing to §2b a cross-target claim it never made.

So the honest reading is:

- **at 24-bar timeout, no target change clears the gate on any of 4 legs**; and
- **that arm was tilted IN FAVOUR of widening**, so the negative is stronger
  than it looks, not weaker. The parity arm is still the one a proposal must
  cite — not to rescue the negative, but because parity is what live does.

Reporting "the target sweep came back negative" from this run alone would be
the unprovenanced-diagnostic failure this repo has a guard for: a true number
under a label that does not describe what was computed. **The PARITY arm
(`timeout_bars=100000`) is the one that can answer it**, and it is dispatched;
only that arm may be cited by any proposal about hold length. Both arms are run
so the gap is quantified rather than swapped in silently.

⚠️ **Nor does a negative on the TARGET rescue the ratchet hypothesis of §4c** —
these are different levers, and this run tested only one of them. §4c's
break-even cell family (`be_off`, now in the sweep) is untested at parity too.

## 4e. ⛔ CORRECTION — THE TIMEOUT BIAS RUNS THE OTHER WAY, AND §4d GOT IT BACKWARDS

**§4d and §2b assert that the 24-bar force-close penalises wider targets. The
parity arm refutes that. It FLATTERS them.** The error ran in the direction of
my own forming hypothesis — that the target might yet be a lever once parity
was applied — which is exactly the direction a self-check is for. It is
corrected here rather than quietly amended, because a reader acting on §4d
would discount a negative that is in fact stronger than it looks.

Population: run
[`34683160559`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/34683160559),
`timeout_bars=100000`, **2 of 7 legs so far** — same corpora, same split, same
config-exact base as the screening arm. **Only the timeout differs**, so the
two arms are directly comparable in absolute `total_R`.

**The baseline is the control**, and it is a good one: at the live 1.5R target
removing the timeout moves IS by **−0.31 R** (eth) and **+0.45 R** (sol). So
any large shift on a *cell* is about that cell, not about parity in general.

| IS window | baseline (1.5R) shift | **4R cell** shift |
|---|--:|--:|
| `ict_scalp_eth_15m` | −0.31 | **−13.86** |
| `ict_scalp_sol_15m` | +0.45 | **−23.17** |

And on `eth_15m` the whole grid orders itself by target: narrow cells **gain**
at parity (+1.34, +2.33, +2.03 at 0.75/1/1.25R), wide cells **lose** (−3.81,
−2.69, −5.42, −13.86 at 2/2.5/3/4R), crossing over right around the live value.

**The mechanism, and why my §4d reasoning was incomplete.** I argued a wider
target needs more bars, so a clock truncates the trades it would have won.
True — and it ignores the trades it would have *lost*. At 4R, **58.5% of trades
never reach the target** (§4d's own table). With a 24-bar clock every one of
those exits at bar 24 at whatever intermediate P/L it holds; without it, most
run on to the stop and pay a full 1R. The clock is a **free partial exit**, and
the wide cells collect it most because they are the cells with the most
unresolved trades. §4d counted the truncated winners and not the rescued
losers.

**What this does to the conclusion: it strengthens it.** The screening arm was
biased **in favour** of widening and still returned **0 of 35**. At parity the
wide cells get materially worse, and the parity arm returns **0 of 14** on the
two legs read so far — with `eth_15m` no longer even flipping sign (Σ IS
**−60.03**, Σ OOS **−23.04**, both negative). On the evidence so far,
**widening the `ict_scalp` take-profit does not help, and the harness's own
defect was making it look better than it is.**

⚠️ **A SEPARATE FINDING, and it is the one §2b was reaching for.** The
timeout's effect on the *baseline* varies in SIGN by leg and window, and on one
of them it is enormous: `sol_15m` **OOS** goes **+20.46 R → +0.20 R** at parity
— the harness's clock accounts for **99%** of that leg-window's entire reported
profit at the live target. `eth_15m` OOS goes the other way (+15.73 → +19.13).
So §2b's *"~62% of reported net R"* must not be read as a fleet constant; it is
leg-and-window specific and can reach ~100%. **A backtest number for an
`ict_scalp` leg is not comparable to live until it is produced at parity.**

⚠️ **n = 2 legs.** Five parity legs are still running, and both readings above
could move. What is already settled is the DIRECTION of the bias, because the
baseline control pins it.

## 4f. A THIRD, INDEPENDENT LEG CONFIRMS THE CORRECTION — AND ADDS A CAPITAL TERM

Same harness, a **different corpus and no IS/OOS split** (so this is
supporting evidence, **not** a gated verdict): `/tmp/sol5m.csv`, SOLUSDT 5m,
`--sim-breakeven`, each target run at the 24-bar default and again at
`--timeout-bars 100000`.

| target | timeout share (24-bar) | mean bars held, 24-bar → parity | net_R 24-bar → parity | **ΔR at parity** |
|---|--:|--:|--:|--:|
| 0.75R | 17.1% | 12.0 → 18.1 | 18.68 → 19.00 | **+0.32** |
| 1.0R | 30.3% | 14.8 → 34.0 | 17.01 → 18.00 | **+0.99** |
| 1.25R | 32.4% | 15.6 → 40.5 | 15.41 → 13.53 | −1.88 |
| **1.5R (live)** | 35.6% | 16.7 → 46.5 | 13.69 → 9.74 | −3.95 |
| 2.0R | 41.1% | 18.2 → 54.2 | 15.57 → 11.80 | −3.77 |
| 2.5R | 47.9% | 19.1 → 74.4 | 10.46 → 4.02 | −6.43 |
| 3.0R | 50.7% | 19.3 → 77.3 | 11.06 → 0.80 | −10.26 |
| 4.0R | 53.4% | 19.5 → 95.2 | 11.28 → **−2.84** | **−14.12** |

**Narrow cells gain at parity, wide cells lose, monotonically** — +0.32 at
0.75R to −14.12 at 4R. That is the §4e correction reproduced on a third leg
and a different timeframe, so it is not an artifact of the two 15m corpora.

**And at parity the target grid is monotone DECREASING in net_R** — 19.00,
18.00, 13.53, 9.74, 11.80, 4.02, 0.80, −2.84. On this leg the tightest target
tested is the best one, and every widening is worse than the live value.

⚠️ **THE CAPITAL TERM, which the R-only gate does not see.** At parity a 4R
target holds **95.2 bars against 18.1 at 0.75R — 5.3× longer — while taking
FEWER trades (59 vs 72)**. So a wider target buys longer occupancy and less
turnover for *less* R. The `exit-refinement` skill's Path B metric
(`net_r_per_capital_day`) is the one that prices this, and it would penalise
the wide cells far harder than the Path A (net_R + maxDD) gate used above.
**Any U4 proposal must be graded on Path B as well**, or it will understate
the case against widening.

⚠️ **AND A MISLABEL IN MY OWN THROWAWAY PROBE, recorded rather than quietly
fixed.** `/tmp/timeout_bias.py` prints its summary under *"net_R the timeout
DESTROYS"*, and the numbers it reports there are **negative for the wide
cells**, i.e. the timeout *creates* net_R for them. The label names the
opposite of what the column computes — UNPROVENANCED DIAGNOSTIC OUTPUT
sub-class A, in a script written by the session that had just finished writing
about that failure class. It is a scratch probe and is not committed, so no
guard would have caught it; the numbers above are read off the per-cell table,
not off that summary line.

## 4g. A PRE-REGISTERED PREDICTION, recorded BEFORE the result

Written at **2026-09-12T08:56Z**, while `ict_scalp_avax_5m`'s parity job is
still running. Recorded first so the correction of §4e cannot be
rationalised after the fact either way.

**The screening arm has exactly one leg that looks like a win**, and it is the
one the correction says should not survive parity. Over the 6 screening legs
read so far (42 cells): **0 pass both halves of the gate**, and **6 improve
net_R in BOTH windows while failing on max-drawdown** — of which **4 are
`avax_5m` at WIDE targets**, rising monotonically to `tp4R` at **IS ΔR +28.59 /
OOS ΔR +13.52**. That is by far the strongest-looking cell anywhere in the
screening arm.

| leg | cell | IS ΔR | OOS ΔR | rejected on |
|---|---|--:|--:|---|
| `avax_5m` | tp4R | **+28.59** | **+13.52** | IS ΔDD +2.79 |
| `avax_5m` | tp3R | +16.52 | +7.01 | IS ΔDD +6.56 |
| `avax_5m` | tp2.5R | +12.20 | +9.30 | IS ΔDD +4.27 |
| `avax_5m` | tp2R | +1.43 | +1.61 | IS/OOS ΔDD +6.55/+3.06 |
| `sol_5m` | tp2R | +5.21 | +1.94 | IS ΔDD +4.03 |
| `xrp_5m` | tp2R | +3.43 | +2.00 | IS ΔDD +5.43 |

**THE PREDICTION.** §4e says the 24-bar clock *flatters* wide targets, and
§4d measured the timeout share rising to ~58% at 4R. If that is right, then at
parity `avax_5m`'s wide cells must **collapse** — the +28.59 R at 4R should
fall sharply, because most of those trades never reached 4R and were being
banked at bar 24 instead of running on to the stop.

**What each outcome means, stated now:**

- **They collapse** → the correction holds on the hardest case, and the
  target lever is dead across the family. This is what I expect.
- **They survive** → **the correction is wrong or incomplete, and `avax_5m` is a
  real candidate leg** that must go to a walk-forward. I would then owe an
  explanation of why this leg differs, and §4e would need its own correction.
- **They collapse but stay net-positive in both windows** → the cell is real
  but smaller than screening implied, and the maxDD half still governs.

⚠️ **Either way `avax_5m` alone would not carry a Tier-3 proposal**: one leg out
of eight, on a Path A gate that §4f shows omits the capital term, with no
walk-forward yet run on the target grid at parity. The prediction is about the
INSTRUMENT, not about shipping anything.

## 4h. THE RESULT — my prediction was HALF RIGHT, and the half that failed matters more

`ict_scalp_avax_5m` at parity landed at **08:58Z**, after the §4g prediction was
committed (`b8eda76f6`, **08:56:13Z**). Grading it against what I wrote:

| cell | IS ΔR @24-bar | IS ΔR @parity | collapse | IS maxDD @24-bar | IS maxDD @parity |
|---|--:|--:|--:|--:|--:|
| tp2R | +1.43 | −2.77 | −294% | 87.14 | **28.27** |
| tp2.5R | +12.20 | −2.05 | −117% | 84.86 | **29.38** |
| tp3R | +16.52 | **+4.51** | −73% | 87.15 | **36.99** |
| tp4R | +28.59 | **+9.80** | −66% | 83.38 | **24.90** |

*(baseline: IS R 147.04 → 133.76, IS maxDD 80.59 → **87.99** at parity)*

**CONFIRMED — the R gains collapsed, as predicted.** `tp4R` fell from +28.59 to
+9.80 (−66%), `tp3R` from +16.52 to +4.51 (−73%), and `tp2R`/`tp2.5R` went
negative outright. The §4e mechanism holds on the hardest case.

**REFUTED — the cells nevertheless PASSED.** `tp3R` and `tp4R` are the
**first `bracket_geometry` CANDIDATES at parity on a widening**, and they
passed on the half I was not watching: **in-sample max-drawdown falls from
~83–87 R to ~25–37 R.** I predicted the gate outcome from the R column alone
and did not consider that removing the clock would change the drawdown path.
That is a real miss, and the prediction is recorded as half-refuted rather than
re-described as a success.

### ⚠️ Why I am NOT treating this as a finding yet — the drawdown is n=1 in EPISODES

**maxDD is a path statistic set by a single worst episode.** A −59 to −63 R
improvement appearing on all four wide cells is **four views of the same
episode**, not four independent observations — the same nesting caveat §4d
applied to net R, applied to drawdown, where it bites harder because the
statistic is an extremum rather than a sum. The whole PASS rests on the
baseline's one deep in-sample drawdown being one the wider targets happen to
sit out.

**The walk-forward is exactly the test for that**, and it is the M20 gate
anyway: a one-episode artifact will not reproduce across yearly folds, a real
property will. It was **not** run here (`walkforward=false` on this arm), so
**`tp3R`/`tp4R` are IS/OOS candidates and nothing more.** A parity run with
`walkforward=true` is dispatched.

### What this does and does not change

- **It does NOT reverse §4e or §4f.** The clock still flatters wide targets;
  that is why these cells collapsed by two-thirds.
- **It DOES retire the sentence "widening does not help" as a family-wide
  claim.** On 6 parity legs read, 5 return nothing and 1 returns two widening
  candidates. The honest statement is now **leg-specific**, not fleet-wide.
- **It does NOT approach a Tier-3 proposal.** One leg of eight, no
  walk-forward, on a Path A gate that §4f shows omits the capital term — and
  §4f measured wide targets holding ~5× longer for fewer trades, which is
  precisely what Path B would charge them for and Path A does not.

### Parity scoreboard so far (6 of 7 legs)

| leg | candidates |
|---|---|
| `ict_scalp_eth_15m` | none (Σ IS −60.03, Σ OOS −23.04) |
| `ict_scalp_sol_15m` | none |
| `ict_scalp_xrp_15m` | **tp1.25R** — a *narrowing* |
| `ict_scalp_sol_5m` | none |
| `ict_scalp_xrp_5m` | none |
| `ict_scalp_avax_5m` | **tp3R, tp4R** — *widenings*, on drawdown |
| `ict_scalp_5m` (BTC) | still running |

**3 candidates in 42 parity cells, on 2 of 6 legs, pointing in OPPOSITE
directions** — one leg wants a tighter target, one wants a wider one. That is
not a family-level lever; it is at most two per-leg ones, and neither is
walk-forwarded.

## 4i. ROBUSTNESS NOTE — the fan-out denominator, applied to this queue's OWN inputs

`BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`
says a rate about PRICE behaviour computed per `trades` row double-counts,
because one signal fans out to several accounts sharing one price path. **U3's
own numbers are unaffected** — they are backtest output from one engine, where
no fan-out exists. **U2's are not**, and U2 is merged, so this is checked here.

**Measured** on the live journal (`/api/diag/journal`, 1000 rows, ids
4720–5719, pulled 2026-09-12T08:5xZ), restating U2's population exactly
(closed · non-backtest · `pnl NOT NULL` · pairs excluded · `created_at >=
2026-08-27`):

| U2 figure | rows | distinct order_packages | inflation |
|---|--:|--:|--:|
| the decision population | 184 | 143 | 1.287× |
| ...of which winners | 49 | **43** | 1.140× |

**U2's conclusions survive.** Its headline is *the whole exit-lever family is
3 of 49 winners* — three fires is three packages either way, 6.1% per row
against ≤7.0% per package, and *"no lever is cutting winners short"* is
unchanged. What is owed under the row's own resolution criterion is that the
package count be **stated**, which it now is.

**But the inflation is NOT uniform, and that direction matters**: 1.287× over
all rows against 1.140× on winners, so a per-row *win rate* is biased **down**.
Measured: **26.6% per row (49/184) against 28.7% per package (41/143)** — 2.0 pp
absolute, +7.7% relative.

### Two packages book a win and a loss on the SAME signal

Only 2 of 143, and both land in already-known defective populations rather than
being new:

- `pkg-261c8a3bf39a45c7` — **two rows on the SAME account** (`bybit_1`,
  `ict_scalp_sol_5m`): one `stuck_strategy_watchdog` at **−888.91**, one
  `netting_attributed` at **+30.23**. Not account fan-out at all; this is the
  `OI-20260908` netting population, where a live position is closed off a
  side-blind read.
- `pkg-293021e2e84a48db` — three accounts, all `reconciler_filled`:
  `bybit_portfolio` **−594.00**, real-money `bybit_2` **−3.92**, `bybit_1`
  **+200.62**. Sizes differ legitimately; the **sign** does not follow from one
  price path. All three carry the label U1 established *cannot name a mechanism
  by construction*.

### The surfaces, named — and the scariest one is CLEAN

The row asks that the surfaces computing such rates be named. A lexical screen
over `scripts/{research,ops,ml}`, `src/runtime` and `src/web/api/routers` for
files that (a) read the LIVE journal, (b) compute a rate, and (c) mention a
price-path concept returns **27 files, 17 of which reference no package unit
anywhere**. Controls: the package probe fires on the other 10, and the pure
backtest harnesses (`scripts/backtest_ict_scalp.py`,
`scripts/research/m27/ict_scalp_exit_sweep.py`) are correctly **absent**.

⚠️ **That 17 is a LEXICAL SCREEN, not a finding — each hit needs a real check,
and the first one I ran came back clean.** The only production surface in the
list is `src/web/api/routers/strategies.py`, which serves the SPA's Strategies
tab. **It is NOT affected**: the route filters to real money only, and that
population has **inflation exactly 1.000×** — 42 closed non-backtest
real-money rows, 42 distinct packages, every multiplicity 1, all on `bybit_2`
(the only real-money account in the window). A per-row win rate there equals
the per-package one. **An earlier draft of this section named it as at-risk on
the strength of the screen alone; that would have been an accusation against a
correct surface, and it is recorded rather than silently dropped.**

## 4j. ⛔ THE BREAK-EVEN RATCHET IS REFUTED TOO — 0 of 7 at parity

§4c called the ratchet *"probably the lever, not the target"*. **It is not the
lever either.** Run
[`34684327271`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/34684327271),
`cells=breakeven_ratchet`, **`timeout_bars=100000` (live parity)**,
`walkforward=true`, all **7** legs, one `be_off` cell each — the cell that
REMOVES `--sim-breakeven` from the config-exact base.

| leg | IS ΔR | IS ΔDD | OOS ΔR | OOS ΔDD |
|---|--:|--:|--:|--:|
| `ict_scalp_eth_15m` | −1.06 | +0.23 | +3.23 | −0.90 |
| `ict_scalp_sol_15m` | +5.89 | +1.05 | +7.96 | +1.13 |
| `ict_scalp_xrp_15m` | −2.78 | +1.94 | +14.02 | −2.22 |
| `ict_scalp_sol_5m` | −11.46 | +7.44 | −6.34 | +3.72 |
| `ict_scalp_xrp_5m` | −10.29 | +2.33 | +14.22 | −11.38 |
| `ict_scalp_avax_5m` | **−24.76** | +16.64 | −0.81 | +2.73 |
| `ict_scalp_5m` (BTC) | +3.67 | −6.27 | −0.71 | +0.64 |

**0 of 7 cells clear the gate.** No walk-forward ran, correctly — nothing
reached it.

Three things the numbers say beyond the zero:

1. **Disarming it COSTS R in-sample and GAINS out-of-sample** — Σ IS **−40.79**,
   Σ OOS **+31.57**, helping on 2 of 7 legs IS and 4 of 7 OOS. The same
   IS/OOS sign disagreement the target grid showed. Two levers, same shape:
   period character, not structure.
2. **Disarming RAISES in-sample max-drawdown on 6 of 7 legs.** The ratchet is
   doing what a break-even stop is supposed to do — buying drawdown protection.
   §4c's 2×2 read it as costing R; at parity and at fleet scale it is *paying*
   for that R.
3. **The biggest single effect is on `avax_5m` (IS ΔR −24.76)** — the same leg
   that produced the only widening candidates. Its exits are the most
   ratchet-sensitive in the family, in the direction of *keep the ratchet*.

⚠️ **AND THIS OVERTURNS MY OWN SMOKE RESULT, which pointed the other way.** The
local 26k-bar probe (§4c) had `be_off` gaining in **both** windows (+1.62 IS,
+1.14 OOS) on n=35/30. At fleet scale on 124k-row corpora it loses in-sample on
5 of 7 legs, once by −24.76 R. **A 65-trade corpus said "disarm it" and a
3,000-trade one says the opposite.** Recorded because that smoke number is in
this document and would otherwise be read as supporting evidence.

### Where that leaves MI-278's lever search

| lever | population | candidates |
|---|---|--:|
| `bracket_geometry` @ 24-bar | 7 legs × 7 cells = **49** | **0** |
| `bracket_geometry` @ parity | 7 legs × 7 cells (6 read) | **3**, on 2 legs, opposite directions |
| `breakeven_ratchet` @ parity | 7 legs × 1 cell = **7** | **0** |

**U1 enumerated the mechanisms, U2 found no lever ends winners, and U3 now
finds no lever changes the outcome either — except three per-leg cells that
disagree with each other and have not been walk-forwarded.** That is a
negative result about the whole M20 "hold winners longer" framing for this
family, and it is the deliverable, not a failure to find one.

## 4k. WHAT IS STILL UNTESTED — bounding the negative honestly

U1 measured that **7 of the 8 `ict_scalp_*` legs arm exactly
`{tp_cross, sl_cross, breakeven_ratchet}`** and that the unit reads no
`trail_mult` at all. So for this family the negative above is complete only if
all three are accounted for. They are not — **`sl_cross` is untested**, and
this section says exactly why rather than letting the omission pass.

**The stop on this family is STRUCTURAL, not a distance parameter**
(`src/units/strategies/ict_scalp.py:509–516`, read this session):

```
sl_buffer = params["atr_sl_buffer_mult"] * atr_now      # default 0.20
sl = sweep["extreme"] − sl_buffer      (long)
risk = entry − sl
```

The stop is anchored to the **sweep extreme** — a market-structure level — with
a small ATR buffer placed outside it. Three consequences, each checkable:

1. **`atr_stop_mult` is genuinely not a parameter of this family**, and that
   makes `ict_scalp_*` a **STRUCTURAL** non-e35 control rather than an
   incidental one. Established three independent ways, not asserted:
   - **the e35 commit itself.** `892c9a2c8` (2026-08-30) is the only
     `config/strategies.yaml` commit in the window that ships e35. Attributing
     each changed `atr_stop_mult`/`tp_r` line to its owning leg key gives
     **8 legs — `ada_pullback_2h`, `avax_pullback_2h`,
     `trend_donchian_{ada,avax,eth,sol,xrp}_4h`, `trend_donchian_eth_prop` —
     and NO `ict_scalp_*`.** *(Positive control: the parser recovered 10 leg
     keys and 22 changed-line attributions from that diff, so a null here is a
     real null. An earlier run of this probe returned zero legs on a broken
     indent regex and was NOT reported as evidence.)*
   - **the config today.** All 8 `ict_scalp_*` legs declare
     `atr_stop_mult: None`, so nothing added it afterwards either.
   - **the unit, which is the strongest of the three.** The stop is
     `sweep_extreme ± 0.20×ATR`; the family **has no `atr_stop_mult` to
     change**. e35 could not have reached it *even in principle*.

   ⚠️ **This matters to `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30`,
   whose `clears_when` says in terms that *"'THE MARKET CHOPPED' IS NOT A
   VERDICT WITHOUT THE NON-e35 CONTROL"*.** That row already states e35 never
   touched `ict_scalp`; what is added here is that the claim is now
   **verified, and structurally rather than incidentally** — and the control
   moved nearly as far as the treatment (`ict_scalp_*` 54.7% → 31.8% against
   `trend_donchian_*` 74.1% → 12.9%). ⚠️ **It does NOT clear that row**: its
   `clears_when` demands per-leg stop-out rate and MFE-at-stop, e35 vs non-e35,
   before vs after, and none of that is done here. This strengthens one
   premise; the discriminating measurement is still owed.
2. **`R` is defined per trade by market structure**, so `tp_at_r: 1.5` is a
   multiple of a quantity that varies trade to trade. The target grid of §4
   is therefore a grid over *structural* risk, which is the right shape — but
   it means a target change and a stop change are not independent knobs.
3. **The one stop tunable is `atr_sl_buffer_mult` (0.20), and the harness does
   not expose it.** `scripts/backtest_ict_scalp.py --help` offers
   `--stale-exit-bars`, `--giveback-*`, `--bank-*`, `--tp-at-r`,
   `--sim-breakeven`, `--timeout-bars` — and **no stop flag**. Same shape as
   §1: **unsweepable, not merely unswept.**

⚠️ **SO THE NEGATIVE IS BOUNDED, NOT TOTAL.** The honest statement is: *of the
three exit mechanisms armed on these legs, two are now swept at live parity
and return nothing that survives the gate, and the third's only tunable cannot
currently be swept at all.* Anyone reading §4j's scoreboard as "every
`ict_scalp` exit lever has been tested" would be over-reading it.

### ✅ BUILT — the buffer is sweepable now (updated 2026-09-36Z)

This section first said the flag was *"not built here"* because three sweeps
were in flight. That reasoning applied to **dispatching** a fourth arm, not to
**building** one, and the distinction was worth acting on: `--atr-sl-buffer-mult`
now exists on the same `cfg_overrides` path `--tp-at-r` uses, with a
`stop_geometry` cell family behind it. **No fourth sweep is dispatched here** —
the original caution stands for that.

- **Default `None` is byte-for-byte the old behaviour**, and the value lands on
  `cfg_overrides` so the LIVE `order_package()` still computes the bracket;
  the harness derives no stop of its own.
- **`0.0` is REFUSED**, and that is the deliberate difference from `--tp-at-r`:
  a zero buffer puts the stop exactly ON the swept extreme — the level the
  setup is defined by having swept — and it is not the unset state, so
  accepting it would make *no buffer* and *no override* indistinguishable.
  Its own error type, so a stop failure cannot read as a target failure.
- **Six cells bracketing the live 0.20 on both sides** (0.05/0.10/0.15,
  0.30/0.40/0.60), the leg's own value excluded as a provable no-op and `0.0`
  excluded because the harness refuses it.
- **It moves the book**, verified rather than asserted: on the smoke corpus
  0.20 → 13.686 R / 9.208 DD, 0.10 → 14.274 / 8.970, 0.40 → 14.692 / 8.160.
  ⚠️ **No verdict is claimed from that** — n=73, one small corpus, no IS/OOS
  split, and *both* directions improving at that n is what noise looks like.
- **Mutation-checked 7/7 on the flag and 5/5 on the cells.** Two initially
  survived, both on the CLI hop — a typo'd `cfg_overrides` key and a flag
  parsed then never written — each of which would report the **default** book
  under an override label, the worst failure available to a sweep. That is the
  third time this session that testing the helper and not the wiring let a
  mutant through.

So the enumeration is now **complete and sweepable**: all three mechanisms
armed on these legs have a runner. Two are measured and empty; the stop buffer
is built and **not yet run**, and no claim is made about it.

## 4l. THE PARITY ARM, COMPLETE — and it corrects §4d a THIRD time

All 7 legs, `timeout_bars=100000`, same corpora and split as the screening arm.

| leg | base IS R | base OOS R | Σ IS ΔR | Σ OOS ΔR | IS/OOS sign | candidates |
|---|--:|--:|--:|--:|:--:|---|
| `ict_scalp_eth_15m` | 44.05 | 19.13 | −60.03 | −23.04 | same (both −) | — |
| `ict_scalp_sol_15m` | 24.11 | 0.20 | +27.99 | −35.78 | flips | — |
| `ict_scalp_xrp_15m` | 32.28 | 6.48 | +1.21 | +2.15 | same (both +) | **tp1.25R** |
| `ict_scalp_sol_5m` | 89.46 | 59.34 | −49.47 | −44.55 | same (both −) | — |
| `ict_scalp_xrp_5m` | 118.79 | 18.78 | −80.77 | −8.83 | same (both −) | — |
| `ict_scalp_avax_5m` | 133.76 | 38.31 | +3.95 | −3.08 | flips | **tp3R, tp4R** |
| `ict_scalp_5m` (BTC) | 54.83 | 35.71 | −40.59 | −49.38 | same (both −) | — |

**3 candidates in 49 cells, on 2 of 7 legs, pointing in OPPOSITE directions** —
`xrp_15m` wants the target **narrowed**, `avax_5m` wants it **widened**.

### ⛔ The IS/OOS sign flipping was largely the HARNESS, not "period character"

§4d read the flipping as *"the signature of period character rather than of a
lever."* Measured across both arms on the same legs and corpora, with only the
timeout differing:

| | legs flipping IS/OOS sign |
|---|--:|
| screening (24-bar force-close) | **5 of 7** |
| **live parity** | **2 of 7** |

**Removing the harness's clock removed three of the five flips.** So the
instability I attributed to the market was substantially manufactured by the
instrument. That is the third correction §4d has needed — after the direction
of the bias (§4e) and the 4-of-4 count (§4d note) — and all three run the same
way: **I read an artifact of the 24-bar force-close as a property of the
data.**

⚠️ **AND IT MAKES THE NEGATIVE STRONGER, NOT WEAKER.** At parity **5 of 7 legs
are negative in BOTH windows**, several decisively (`xrp_5m` Σ IS −80.77,
`eth_15m` Σ IS −60.03 / Σ OOS −23.04, `btc_5m` −40.59 / −49.38). That is not a
noisy null; it is a consistent finding that **moving the `ict_scalp` target in
either direction loses R on most legs**, now measured on the population that
matches production.

`btc_5m` is the cleanest illustration of why the two arms disagree so much: at
parity its IS grid runs **+31.42 at 0.75R monotonically down to −33.39 at 4R**,
a 65 R spread across the grid, and its OOS runs the other way. Under the 24-bar
clock that structure was invisible (Σ IS −10.98).

## 4m. ⚠️ EVERY VERDICT ABOVE IS PATH-A ONLY — and the fix is to RECORD, not to grade

Found by auditing my own instrument against the skill that governs it, after
the results were in. **`exit-refinement/SKILL.md` states the gate as two
alternative qualifying paths** (operator directive 2026-08-10, *"capital
efficiency is a SHIPPING criterion, no longer a tiebreak"*):

- **Path A — return.** Beats baseline on **net_R AND maxDD** in both windows.
- **Path B — capital efficiency.** Improves **`net_r_per_capital_day`** in both
  windows, maxDD does not worsen (as a *derived tolerance*,
  `allowed = D_b × (dN / N_b)` capped at `D_b`), and net_R falls by no more
  than a declared floor.

**`scripts/research/m27/ict_scalp_exit_sweep.py::beats` implements Path A and
only Path A** — `cell.total_r > base.total_r and cell.max_dd_r < base.max_dd_r`
— and the cell record it stores carries **only `total_r` and `max_dd_r`**. So
`net_r_per_capital_day` is not merely ungraded, it is **not recorded**, and
Path B cannot be evaluated retroactively on any run in this document.

⚠️ **CONSEQUENCE, STATED PLAINLY: every "0 candidates" in §4d/§4h/§4j/§4l is a
PATH-A verdict, not a full-gate verdict.** A cell that loses R while freeing
capital faster is exactly what Path B exists to qualify, and this sweep
discards it silently. The direction matters here: **§4f measured wide targets
holding 5.3× longer for fewer trades**, which is a large capital-efficiency
*penalty* Path A cannot see — so for the WIDE cells Path B is very unlikely to
rescue anything, and the negative stands. For the **NARROW** cells the reverse
is true: they hold less capital for less time, so Path B is where they would
qualify if anywhere, and **`btc_5m` at parity runs +31.42 R at 0.75R with maxDD
−18.81** — a cell rejected only because its OOS R fell. **That one is a real
candidate for Path B and has not been graded.**

⚠️ **THIS IS A PRE-EXISTING DEFECT IN THE M27 SWEEP, NOT ONE THIS PR
INTRODUCED** — the Path-A-only docstring predates MI-278 (the file's own §GATE
paragraph). Recording that because it means every prior M27 `bracket_geometry`
and ladder verdict measured with this script carries the same limitation, not
just today's. It is fixed here rather than filed onward, per Generation
Discipline Rule 2: an audited non-compliant precedent that touches what I ship
gets fixed in the same PR.

### ⛔ AND MY FIRST READING OF THE REMEDY WAS WRONG — recorded because I nearly shipped it

This section first said the sweep *"implements HALF the binding gate"* and
implied a Path B **pass/fail** was owed. I was one step from writing that
grader. **Reading the owners stopped it, and they are emphatic:**

- `m20_fleet_exit_sweep.capital_delta` is documented **"REPORTED, never
  graded"**, because *"Path B's two thresholds … are deliberately unset: the
  operator sets them from a measured distribution, not from a number a session
  invented."*
- Even that sweep's `path_b_wf_pass` verdict carries *"IS NOT A PROMOTION.
  Both Path B thresholds remain unset"* — and it records that **6 of 18**
  `path_b_wf_pass` rows failed the rate gate at fleet scale.
- The derived drawdown tolerance is **"Reported, not enforced — a criterion
  that promoted on its own would be the same Tier-3 short-circuit the sweep
  exists to avoid."**

**So Path B is ungraded EVERYWHERE, on purpose, and a grader written here would
have invented an operator-reserved threshold** — the exact Tier-3
short-circuit the repo's own machinery refuses. The real defect is narrower and
is now fixed: the M27 sweep did not even **record** the evidence the fleet
sweep reports.

**What shipped instead.** Every cell record now carries a `capital_delta`
block for both windows and an explicit **`gate_paths_graded: ["A"]`**, so a
reader cannot mistake a Path-A verdict for a full-gate one. The comparison is
**imported from its single owner** (`m20_fleet_exit_sweep.capital_delta`, built
on `scripts/capital_efficiency.py`), never re-derived — the skill's own reason:
*"or a cross-harness comparison means nothing."* Unmeasurable rates stay
`None`, never `0.0`. Mutation-checked 5/5, including a mutant that claims
`["A","B"]` and one that re-derives the metric locally.

**Verified on live data**, and it is informative rather than decorative: on the
smoke leg, disarming the ratchet raises `capital_days` **6.34 → 7.92** and
lowers `net_r_per_capital_day` **1.225 → 1.182**, so the capital evidence
points the same way as Path A rather than rescuing the cell.

⚠️ **The recorded block still requires a RE-RUN to exist for the verdicts in
this document** — the field was absent when they ran, and it cannot be
recovered retroactively. Until then §4j's scoreboard is **Path A only**, and
the one cell most likely to be re-read under Path B is **`btc_5m` tp0.75R**
(+31.42 R IS, maxDD −18.81, rejected on OOS R alone).

## 4n. ✅ ONE WALK-FORWARD SURVIVOR — `ict_scalp_xrp_15m` `tp1.25R`, and it NARROWS the target

Run [`34684742080`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/34684742080),
`cells=bracket_geometry`, `timeout_bars=100000`, `walkforward=true`.

```
tp1.25R  [bracket_geometry]  IS ΔR=+5.82 ΔDD=-2.53 (n198) | OOS ΔR=+5.93 ΔDD=-3.72 (n117)
         -> CANDIDATE  [timeout IS 0% OOS 0%]
yearly walk-forward: 3/4 usable folds (need 3) -> PASS
         [2021:skip 2022:skip 2023:PASS 2024:- 2025:PASS 2026:PASS]
WALK-FORWARD SURVIVORS (M20-gated, -> Tier-3 proposal): ['tp1.25R']
```

It **improves both metrics in both windows** — the only cell in this whole
workstream to do so — and then **clears the yearly walk-forward**. `n_OOS=117`
clears the skill's `MIN_OOS_TRADES = 25` floor with room. The
`[timeout IS 0% OOS 0%]` stamp is §4d's fidelity readout confirming this ran at
parity, so it is not a screening artifact.

**And the direction is the opposite of the queue's premise.** MI-277's finding
is that winners are being cut short and MI-278 is titled *hold winners longer*.
The one lever that survives the full gate says **take profit SOONER** on this
leg — 1.5R → 1.25R.

### ⚠️ FIVE THINGS THAT MUST TRAVEL WITH IT, or it is over-read

1. **It passes the walk-forward by exactly the minimum.** 3 of 4 usable folds
   against a `need` of 3, with **2021 and 2022 SKIPPED** — those years are
   empty on `m27_data`, which the workflow's own header records. So the
   confirmation rests on **four** folds, one of which (**2024**) failed. One
   fold moving flips this to a fail.
2. **PATH A ONLY** (§4m). Its capital-efficiency evidence was not recorded when
   this ran, and the full gate has two paths. A Path-A survivor is not a
   full-gate survivor.
3. **One leg of eight**, and the family's other six parity legs return nothing
   in this direction — `btc_5m` at parity is **+31.42 R IS at 0.75R** but
   **−10.71 OOS**, i.e. the same narrowing fails elsewhere. There is no
   family-level claim here.
4. **`tp_at_r` is a multiple of a STRUCTURAL risk** (§4k), so this is not "move
   the target 0.25 R closer" in any fixed-distance sense — it rescales against
   a per-trade sweep-anchored denominator.
5. **A survivor is an INPUT to a Tier-3 proposal, not the proposal.** Under the
   scope this unit works to, every parameter change is proposed and the
   operator decides. Nothing here is enacted, and `config/strategies.yaml` is
   untouched.

**`ict_scalp_avax_5m`'s two candidates (`tp3R`, `tp4R`) are still walking
forward.** If they also survive, the family has two confirmed levers pointing
in **opposite** directions on different legs — which would be the strongest
possible statement that there is no family-wide target fix, only per-leg ones.

## 4o. A SECOND UNENFORCED CLAUSE — the OOS-trade floor, harmless here

While verifying U4's numbers against the raw logs I checked one more claim I
had made by eye: that `n_OOS = 117` clears the skill's `MIN_OOS_TRADES = 25`.

**It does, and the sweep did not check it.** `MIN_OOS_TRADES = 25` (operator
decision, 2026-08-11) lives in `m20_fleet_exit_sweep.py`;
`m27/ict_scalp_exit_sweep.py` has no such constant and applies no floor. So a
thin leg would be graded and reported like any other.

**Measured before calling it a problem**: over the **77 cell-OOS observations**
across every parity and walk-forward log in this workstream, the minimum OOS
trade count is **95** and the maximum 356 — **zero cells below 25**. The
missing floor therefore **binds on nothing in this population**, and no verdict
in this document is affected.

It is recorded because *unenforced-and-currently-harmless* is a different state
from *enforced*, and the difference is invisible from the output. Together with
§4m this makes two clauses of the binding gate that this sweep does not
implement — the capital-efficiency path, which changes what qualifies, and this
floor, which currently changes nothing.

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
