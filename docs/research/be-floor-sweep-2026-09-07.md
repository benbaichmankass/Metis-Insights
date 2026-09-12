# The path-aware `be_floor_r` sweep — the cost term MI-163 could not measure

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-165** · commissioned by the operator 2026-09-07 (*"Commission the path-aware sweep"*) · branch `claude/mi165-be-floor-sweep-20260907`

Landed via [#11266](https://github.com/benbaichmankass/Metis-Insights/pull/11266) · `landing: "hold"` (see § *Why this is held* at the end).

⚠️ **PROPOSE-ONLY.** This document and its instruments are Tier-1. `be_floor_r`
is declared in no config, is armed on no leg, and arming it is Tier-3. Nothing
here edits `config/`, `src/`, or any live exit.

---

## The answer in five lines

1. **No value of `be_floor_r` clears the gate. Not one.** Every arm tested —
   0.5R, 0.75R, 1.0R, 1.5R — **loses** net R against the ungated fleet on the
   pooled backtest corpus.
2. **The loss is monotone in the floor**: −145.16R at 0.5 · **−128.13R at 0.75**
   · −99.08R at 1.0 · −35.04R at 1.5, against a control of **+671.58R over 4090
   trades on 28 graded legs**. The tighter the floor, the more it costs — the
   exact signature of **forgone continuation**.
3. **+0.75R — the peak of MI-163's gross benefit table — is the second-worst arm
   tested.** The cost term does not merely exist; over this population it
   **exceeds the benefit at every value tested**.
4. **1.5R would have PASSED a naive fold-count gate while losing 35R.** It wins a
   strict majority of folds under *all three* panel members and still loses
   money, because 30–39% of its folds are **inert**. This is
   `BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS` caught in the act.
5. **Proposal B should not be armed at any value, and the exact Tier-3 diff is
   therefore: none.** That is a result, not a failure to find one.

---

## 1. What was missing, and what supplies it

MI-163 ([`banking-half-2026-09-07.md`](./banking-half-2026-09-07.md)) established
that 36 of 44 enabled+live legs run a monotone Chandelier ratchet calibrated far
beyond where trades go — `R_TO_BREAKEVEN = trail_mult / atr_stop_mult`, **median
2.00R**, against a median live peak of **0.84R**. **Re-verified here rather than
carried over:** resolving all 44 legs through `pipeline.monitor_unit_for` (the
same resolver the live order-monitor uses) reproduces 19 `trend_donchian` + 16
`htf_pullback_trend_2h` + 1 `squeeze_breakout_4h` = **36 ratchet legs**, 8
`ict_scalp`, and `trail_mult / atr_stop_mult` over the 36 spans **1.20 – 3.33,
median 2.00**. Every figure matches.

MI-163 then measured a gross benefit table peaking at **+16.87R at +0.75R** and
**refused to propose a value**, because that table has no cost term:

> the telemetry carries the peak and the terminal, **never the path**. A stop
> parked at +X exits on the **first** retrace to X — so on a trade that later
> resumed it forgoes the remainder, and that cost is unmeasurable from this
> corpus.

⚠️ **That is a quotation, and this document does not repeat it as a standing
claim — it retires it.** The cost is unmeasurable *from the live telemetry
corpus* and is perfectly measurable from a bar-replay harness, which is what
this run used (checked: scripts/research/be_floor_sweep.py — it drives
scripts/backtest_trend.py / backtest_pullback.py / backtest_squeeze.py, whose
nested SL-first bar loop replays the path MI-163's corpus lacks, and it reports
a cost-net `net_total_r` per arm; also checked
docs/research/RESEARCH-CAPABILITY-INDEX.md, which routes trend-leg replay to
scripts/backtest_trend.py as the one live-faithful engine).

**A backtest harness replays the bar path.** When the break-even floor stops a
trade out at entry, the harness records *that* exit and whatever the trade would
have gone on to pay is simply never earned. The cost is therefore **measured by
construction, not estimated**. That is the whole reason the run had to be
path-aware, and it is what this document supplies.

---

## 2. ⚠️ POPULATION — and it is NOT MI-163's 89 rows

**This is a BACKTEST population. MI-163's is a LIVE telemetry population. They
answer different questions and are never pooled, added, or netted against each
other.** MI-163 measured *what the live fleet did*; this measures *what the lever
would have done on history*. The 89 live rows are precisely the thing that
**cannot** be replayed — they carry no intra-trade path — which is why a harness
was needed at all.

**POPULATION: the 36 ratchet legs, each replayed on its OWN declared parameters
from `config/strategies.yaml` at `main` 59bfa099, on its own instrument history.**

| | n | |
|---|---:|---|
| ratchet legs in scope | **36** | 19 donchian · 16 pullback · 1 squeeze |
| **graded** (control n ≥ 30) | **28** | 4090 control trades |
| `insufficient_n` | **7** | all `*_trend_long_1d` equity legs, n = 21–29 |
| `degenerate_series` | **1** | `splg_trend_long_1d` — **we could not look** |

⚠️ **`degenerate_series` is kept apart from `insufficient_n` deliberately.** Yahoo
served SPLG **36 bars with 1 usable close** on 2026-09-07; graded by the repo's
own `candle_variance.grade_close_variance` (imported, not re-derived) that is
`not_gradeable` — *we could not look* — and folding it into "a leg that traded
too little" would be the collapse `CLAUDE.md` § "Collapsed states" names. It ran
to zero trades; that is the venue's silence, not the lever's.

⚠️ **The 8 `ict_scalp` legs are out of scope BY CONSTRUCTION, not by omission.**
MI-163 §2.2 established their one-shot `monitor_breakeven_sl` mechanism is
guarded by `sl < entry`, so it fires once and never again and **cannot accrue R
at all**. A break-even *floor* is meaningless on a mechanism whose ceiling is
already break-even. MI-164 is concurrently adding the telemetry hook that makes
those 9 legs observable; that changes what can be *measured* about them and
changes nothing about whether this lever applies to them.

**Data provenance, stated because it bounds the result:**

| legs | source | span |
|---|---|---|
| crypto (BTC/ETH/SOL/XRP/ADA/AVAX), 1h base → 2h/4h resample | `data.binance.vision` monthly klines, via `scripts/ops/fetch_backtest_candles.py --source binance_vision` | 2022-01-01 → 2025-08-31 (32,136 bars/symbol) |
| ETFs 1d | Yahoo chart endpoint | 2018-01-02 → 2025-08-29 (1,926 bars) |
| ETFs 1h | Yahoo chart endpoint | ~730 d (Yahoo's own intraday cap), ~5,080 bars |
| MES / MGC / MHG 1d | Yahoo, via **`ES=F` / `GC=F` / `HG=F`** | 2018 → 2025 |

⚠️ **The micro-futures legs are replayed on CONTINUOUS FRONT-MONTH PROXIES**, not
on MES/MGC/MHG themselves. That mapping is the repo's own
(`ml/datasets/adapters/yf_symbols.py::_DEFAULT_TICKER_MAP`, which states the
reason: far deeper history at the same price level), not a choice made here — but
it is an approximation and those three legs' individual numbers carry it. Two of
the three are `insufficient_n` anyway.

⚠️ **`scripts/ops/fetch_backtest_candles.py --source yfinance` could not be used
and the reason is not a repo defect:** the `yfinance` library's cookie/crumb
handshake against `fc.yahoo.com` is reset by this session's egress proxy
(`curl 35`), so every call fails at the COOKIE stage and reports *"no candles
returned"* — a message naming a fetch that never happened. Yahoo's chart endpoint
itself answers normally with an ordinary User-Agent, verified before it was used.
Same venue, same bars, no auth dance. Filed as
`BL-20260907-YFINANCE-LANE-BLOCKED-BY-PROXY-COOKIE-HANDSHAKE`.

**Fidelity note (measured, not assumed):** across all 36 legs, the number of
declared config levers that the matching harness could **not** model is **zero**
— `be_floor_sweep.py` filters config keys against each harness's real signature
and reports what it drops, and it dropped nothing. Every graded leg was replayed
with its own `trail_mult`, `atr_stop_mult`, `tp_r`, trail-decay, stale-exit,
giveback, ADX, session and vol-skip settings.

---

## 3. Method

Each leg is run on the harness that models **its own entry** — donchian →
`backtest_trend`, pullback → `backtest_pullback`, squeeze → `backtest_squeeze` —
so every arm differs from the control in exactly one lever and the delta is
attributable. The lever itself is new and was added to all three, because the
three ratchet units share one trail geometry and a lever meaning different things
in different harnesses would make the pooled number incomparable across legs.

```
be_floor_r:  once peak_r >= be_floor_r, the trailing stop may never sit below entry
             trail = max(trail, entry)      # long   — applied AFTER the ratchet
             trail = min(trail, entry)      # short
```

It is `max()`-ed against the Chandelier candidate, so it can **only ever
tighten** — the monotone-ratchet invariant the live units already guarantee is
preserved, not replaced. It is armed off `mfe`, the same since-entry peak in R
that `position_telemetry.since_entry_peak` records live. `0.0` is off and
**byte-identical**, verified against `HEAD` for all three harnesses before any
arm was run.

- **arms** `be_floor_r ∈ {0.0 (control), 0.5, 0.75, 1.0, 1.5}`
- **folds** contiguous equal-**calendar** slices; **fold panel `{3, 4, 5}`, fixed**
- **inert** `d_net_r == 0.0 AND d_max_dd == 0.0`, imported from
  `m20_wf_effective.is_inert`
- **net** the harness's own cost-net per-trade R (fee + slippage + funding)

⚠️ **Folds are by CALENDAR, not by trade order** — a deliberate departure from
`direction_walkforward.analyze`. That convention folds one arm's own trades; here
five arms are compared to each other and they produce different trade counts, so
equal-count folds would put different wall-clock windows in "fold 2" for
different arms and the delta would not be a comparison. A common partition is a
precondition for a delta.

⚠️ **The fold panel is fixed** because a verdict that flips on fold count is not
a verdict (`BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP`) — the same reasoning
`regime_cell_walkforward.FOLD_PANEL` states. Odd and even members, so a result
must hold under both parities.

⚠️ **There is no parameter FITTING step here, and the walk-forward is not
pretending otherwise.** `be_floor_r` is a single fleet-wide constant, not a
per-fold optimum, so the folds test **stability** — does the arm beat the control
consistently across disjoint windows — rather than out-of-sample generalisation
of a fitted value. That is the honest description of what the panel establishes.

---

## 4. The result

**POPULATION: 28 graded ratchet legs, 4090 control trades, backtest.**

| `be_floor_r` | net total R | Δ vs control | legs better / worse / inert |
|---:|---:|---:|---|
| **0.0 (control)** | **+671.58** | — | — |
| 0.50 | +526.42 | **−145.16** | 9 / 19 / 0 |
| **0.75** | +543.45 | **−128.13** | 8 / 20 / 0 |
| 1.00 | +572.50 | **−99.08** | 10 / 18 / 0 |
| 1.50 | +636.54 | **−35.04** | 11 / 12 / 5 |

**Every arm loses, and the loss shrinks monotonically as the floor rises.** That
shape is the finding: a floor at 1.5R arms rarely and costs little; a floor at
0.5R arms constantly and costs a great deal. The benefit MI-163 measured is real
— 8–11 legs improve at every arm — but it is swamped by the trades that retraced
through entry and then **resumed**, which is exactly the population the live
telemetry cannot see.

The worst single leg is the flagship: `trend_donchian` (BTCUSDT 1h, n=257)
loses **−46.61R** at 0.75R on its own. The best is `trend_donchian_eth` at
**+21.54R** at 0.5R. The dispersion is wide and does not point at a value.

### 4.1 ⚠️ The walk-forward, and the trap inside it

| arm | k=3 | k=4 | k=5 | majority-win under all k? |
|---:|---|---|---|---|
| 0.50 | 34W/50L/0 | 47W/65L/0 | 63W/77L/0 | no |
| 0.75 | 37W/47L/0 | 51W/60L/1 | 67W/71L/2 | no |
| 1.00 | 38W/45L/1 | 49W/57L/6 | **67W/66L/7** | no (k=3, k=4 fail) |
| **1.50** | **34W/25L/25** | **42W/29L/41** | **56W/30L/54** | **YES** |

**`be_floor_r = 1.5` wins a strict majority of exercised folds under every panel
member and still loses 35.04R.** A gate written as "majority of folds positive"
would have passed it. The reason is visible in the same row: **25 / 41 / 54 of
its folds are INERT** — 29.8% / 36.6% / 38.6% — the lever never fired in them at
all. Its wins are many small fold-level improvements; its losses are a few very
large truncated winners, which fold-counting cannot see.

This is not a hypothetical about `m20_wf_effective`'s reasoning; it is that
reasoning reproducing on live-fleet parameters. **A raw `N/M` on this lever would
have been actively misleading**, and the gate is written as pooled-net **AND**
fold-majority precisely so that either one alone cannot carry a verdict.

*(`1.0` at k=5 shows the milder version: 67W/66L is a majority win on that panel
member alone, against −99.08R pooled. The fixed panel catches it; a single
caller-chosen `--folds 5` would not have.)*

### 4.2 What the gate says

```
clears_gate := pooled net R > control  AND  majority of EXERCISED folds win under EVERY panel member
```

| arm | pooled net | fold majority | **GATE** |
|---:|---|---|---|
| 0.50 | ✗ | ✗ | **FAIL** |
| 0.75 | ✗ | ✗ | **FAIL** |
| 1.00 | ✗ | ✗ | **FAIL** |
| 1.50 | ✗ | ✓ | **FAIL** |

**No arm clears it.**

---

## 5. What I propose

### The Tier-3 diff: **none.**

**`be_floor_r` should not be armed at any value, and no line of
`config/strategies.yaml` should change on this evidence.** MI-163's Proposal B
was correct to exist, correct to be framed as a floor rather than a
threshold-and-target, and correct not to carry a number. Now that the number has
been measured, the answer is that no value in the commissioned range pays for
itself.

**This is a successful outcome of the commission, and it must not be softened.**
The repo has already paid once for arming an exit-adjacent lever without a
walk-forward: `FLIP_CONFIDENCE_THRESHOLD` ran live on real money from ~2026-08-10
with nothing behind it, was measured 2026-08-11, **lost against plain `hold`, and
was disarmed the same day**. Proposing +0.75R off a gross table would have been
that mistake with better arithmetic — and the measurement now says it would have
been that mistake with a **−128.13R** cost attached.

### What the evidence does support

The operator's underlying thesis is **not** refuted. MI-163's measurement stands:
the ratchet reaches break-even at a median 2.00R against a median live peak of
0.84R, so on most trades it does nothing. What this sweep refutes is one specific
remedy — pinning the stop at entry — and it refutes it for a specific, legible
reason: **on these legs the wide chandelier is load-bearing.** It exists to ride
retraces on trending instruments, and a break-even floor converts "rode the dip
and continued" into "exited flat" often enough to cost more than it saves.

That points somewhere different, and these are **directions, not proposals** —
none is measured and none should be armed:

1. **The problem may be `trail_mult`, not the absence of a floor.** `R_TO_BREAKEVEN`
   is a *derived* quantity of two levers that already exist and are already
   swept-able. A leg at 3.33 and a leg at 1.20 are not the same leg, and the
   fleet-wide framing may be what is wrong rather than the value.
2. **A floor that scales with the leg** (e.g. a fraction of its own
   `R_TO_BREAKEVEN`) would arm at a comparable point on each leg's own geometry
   rather than at a constant R across legs whose geometry differs 2.8×.
3. **The 8 one-shot `ict_scalp` legs remain the untouched half.** They cannot
   accrue R at all, which is a structural fact no calibration of this lever
   addresses, and MI-164's hook is what will make them measurable.

---

## 6. What this document does NOT establish

- **It does not measure the live fleet.** It is a backtest. MI-163's 89 live rows
  and these 4090 backtest trades are different populations and are never pooled.
- **It does not clear `be_floor_r` outside {0.5, 0.75, 1.0, 1.5}.** The
  commissioned range was those four. The monotone trend suggests the loss keeps
  shrinking above 1.5R, but a floor that arms almost never converges on *doing
  nothing* rather than on a benefit — at 1.5R, 54 of the 140 folds (38.6%; 28 graded legs x k=5) are already inert.
  **A value above 1.5R was not tested and is not endorsed by extrapolation.**
- **It does not grade the 7 `insufficient_n` legs or `splg_trend_long_1d`.** They
  ran; their samples do not clear the repo's own floor of 30, and SPLG's series
  could not be read at all. *We did not look* — not *we looked and found nothing*.
- **It does not measure the one-shot legs**, which by construction this lever
  cannot address.
- **It does not re-grade any exit-matrix cell**, does not touch
  `TP_VENUE_CAP_PCT`, and does not arm `ICT_SCALP_EXIT_HEAD_MODE`.
- **It does not refute MI-163.** Every measurement in that document is reproduced
  here where it was re-checked. This supplies the cost term it explicitly said
  was missing, and the cost term is what changes the answer.

---

## Reproducing

```bash
# crypto
for S in BTCUSDT ETHUSDT SOLUSDT XRPUSDT ADAUSDT AVAXUSDT; do
  python3 scripts/ops/fetch_backtest_candles.py --symbol $S --interval 60 \
    --source binance_vision --start-date 2022-01-01 --end-date 2025-08-31 \
    --output data/ohlcv/${S}_1h.csv
done
# equities: --source yfinance (see the proxy caveat in §2)

python3 scripts/research/be_floor_sweep.py --data-dir data/ohlcv \
  --json docs/research/data/be-floor-sweep-2026-09-07.json
```

Full per-leg and per-fold record, including every fold's `d_net_r` / `d_max_dd`:
[`data/be-floor-sweep-2026-09-07.json`](./data/be-floor-sweep-2026-09-07.json).

---

## Why this is held, and one thing a human must do

`.github/pr-landing/mi165-be-floor-sweep-20260907.json` declares
`landing: "hold"`. The cause is **mechanical, not a danger claim**: R5 of
`check_pr_landing.py` refuses `landing: "self"` because three changed paths
(`scripts/backtest_{trend,pullback,squeeze}.py`) sit outside `TIER1_SURFACE`,
which covers `scripts/ci|ops|research|reports` but not the backtest harnesses at
`scripts/` root. The guard's own words: *"a path outside TIER1_SURFACE is not
thereby dangerous; it is one the guard cannot certify."*

⚠️ **None of the five `HOLD_REASONS` names that case**, so the declaration
carries the closest available term with the true cause spelled out in
`hold_text`. Filed as
`BL-20260907-PR-LANDING-HAS-NO-HOLD-REASON-FOR-TIER1-WORK-OUTSIDE-THE-CERTIFIABLE-SURFACE`
and deliberately **not fixed here**: both candidate remedies edit
`scripts/ci/check_pr_landing.py`, which is `LANDING_MACHINERY`, and R12 exists to
stop a PR landing its own change to the rules by which PRs land.

⚠️ **PR #11266 is a DRAFT and this session could not clear that.** It was opened
through the `pr-opener` relay with `"draft": true`, which **contradicts the
operator's 2026-09-03 ruling** that a PR is held by its landing declaration and
never by the draft flag — *"Going into github to mark drafts ready is not
something we can include in the workflow."* The mistake is recorded rather than
quietly left: `update_pull_request` returns `403 Resource not accessible by
integration` from this session, which is the exact permissions asymmetry that
ruling was written about, so **clearing the draft needs the manager**. The hold
itself is correct and stays; only the redundant draft flag is the error.
