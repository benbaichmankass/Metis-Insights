# QLD and TQQQ, examined — the two never-swept live legs now have a MEASURED verdict

> **Doc status:** `live` · category `evidence` · last verified `2026-09-08` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md)

**MI-195** · branch `claude/mi195-tp-decisions-and-examine` · supersedes the *absence* recorded by MI-156
([`reachable-take-profit-proposal-2026-09-07.md`](reachable-take-profit-proposal-2026-09-07.md) § 5, DEC-20260907-TP-UNEXAMINED)

⚠️ **TIER-1 THROUGHOUT. `config/strategies.yaml` WAS NOT MODIFIED BY THIS SESSION.** The change that
turns these findings into config is Tier-3 and is put to the operator in § 6, not applied.

---

## 0. The question, and why it was the right one

The operator was offered three ways to *publish* the fact that `qld_trend_long_1d` and
`tqqq_trend_long_1d` had never been swept, and rejected all three:

> **"Why don't we just examine them?"**

That was correct, and the three options were the wrong shape — every one of them asked how to record
an absence and none asked what it would cost to remove it. **It cost one routing line.**

---

## 1. The blocker was a routing gap, not a missing dataset — MEASURED

The e3.5 planner (`scripts/research/e35_shard_plan.py::resolve_feed_source`) reached yfinance by
exactly one route: `if sym in fleet.PROXY_DATA`. `QLD`/`TQQQ` are not `PROXY_DATA` — correctly, since
they need no proxy, they *are* the instrument — so they fell through to Dukascopy, which refuses them.

**The Dukascopy refusal is a refusal to PROXY, and it was being read as a property of the leg.** Its
own text says so, and so does the table it lives in:

| | |
|---|---|
| `dukascopy_instruments.REFUSED` docstring | *"Symbols the adjudication REFUSED **to proxy**"* |
| the `QLD` entry | *"…so its path is not 2x the QQQ path — **a QQQ series is not a substitute at any horizon**"* |

That argument is **correct and is untouched by this change** — no QQQ series is ever served for QLD.
It simply never reached the question of whether QLD's *own* series exists. It does, and it had been
mapped the whole time: `ml/datasets/adapters/yf_symbols.py` carries `QLD -> QLD` and `TQQQ -> TQQQ`
as **pass-through** entries, and `YF_MAX_HISTORY_DAYS["1d"] is None` (uncapped).

**This is the same shape `MHG` was already rescued from**, and `resolve_feed_source`'s own docstring
had conceded it one paragraph above the refusal: *"that refusal was about Dukascopy, never about the
leg being unservable."* MHG had a `PROXY_DATA` entry to ride out on; QLD/TQQQ needed no proxy, so
they fell through the crack **between** the two branches.

### 1.1 The substrate — MEASURED 2026-09-08, Yahoo chart API v8

| symbol | daily bars | first | last | bars in the sweep's own 1830 d window |
|---|---:|---|---|---:|
| `QLD` | **5084** | 2006-06-21 | 2026-09-04 | 1255 |
| `TQQQ` | **4167** | 2010-02-11 | 2026-09-04 | 1255 |
| `QQQ` *(positive control)* | 6709 | 2000-01-03 | 2026-09-04 | 1255 |

Identical in-window bar counts, as one exchange calendar must give. **The substrate was never
missing.**

### 1.2 The fix, and what it CANNOT do

A **last rung** on `resolve_feed_source`, reached only when Dukascopy cannot serve the leg, gated on
the yfinance ticker being *the symbol itself* (`yf_serves_directly`) so a proxy row can never ride it.

**MEASURED, and this is the property that matters:**

```
planner matrix:  41 jobs -> 43 jobs
ADDED   : qld_trend_long_1d, tqqq_trend_long_1d
REMOVED : (none)
feed_source CHANGED on a pre-existing leg: NONE
```

Every currently-mapped symbol keeps the feed it had, so **the committed corpus stays comparable and
no recorded verdict is re-based onto a new source.** `NVDA` — unknown to Dukascopy *and* absent from
the yfinance map — still raises, so `mapped` / `refused` / `unknown` stay three states.

---

## 2. THE HEADLINE — the answer is `none`, and it is now MEASURED

**POPULATION:** `e35_bracket_geometry_sweep.py` at `origin/main` 417bd6b6 + this branch's routing
rung, full available history per leg, fee-charged (`DEFAULT_FEE_BPS_ROUNDTRIP` 7.5),
`split_mode=oos-trades`, `split_target_oos=50`. **237 cells** on QLD (175 TP-bearing), **219** on
TQQQ (175 TP-bearing).

| leg | lifetime trades | IS / OOS split | TP cells | TP cells passing IS+OOS | **shippable take-profit** |
|---|---:|---|---:|---:|---|
| `qld_trend_long_1d` | 113 | 63 / 48 @ 2017-12-15 | 175 | **0** | **NONE** |
| `tqqq_trend_long_1d` | 117 | 67 / 48 @ 2020-02-04 | 175 | **0** | **NONE** |

**So `unexamined` becomes a measured `none` on both legs.** That is the answer, and it is the answer
the sibling population predicted: across the six other `donchian`/`1d` legs already in the corpus,
**4 of 1080 TP cells passed (0.37%)** and **4 of 6 legs produced zero**.

### 2.1 But the *reason* is far sharper than "nothing passed"

Every non-passing TP cell on these legs failed with `tie_no_improvement` at **`d_net_r` exactly 0.0** —
QLD `tp4`, `tp6`; TQQQ `tp2.5`, `tp3`. A cell scoring *identically to base* is a cell whose
take-profit **never fired**. And QLD's one cell that reached the walk-forward, `tp2`, scored
**`wf_wins_effective` 0/6 — all five "wins" INERT**, i.e. in every fold the cell was the base arm
wearing a different label.

That is not a weak edge. It is a target that never reaches the wire.

---

## 3. WHY — the reachability ceiling, measured for the first time on these two legs

`cap_r = TP_VENUE_CAP_PCT × entry / risk`, `risk = atr_stop_mult × ATR`, `TP_VENUE_CAP_PCT = 0.099`.
Both legs declare `atr_stop_mult: 2.5`.

**MEASURED from the fetched candles** (ATR14/close over each leg's full history; n = 5071 / 4154 /
6696 observations):

| leg | `atr_stop_mult` | ATR14/close median | **`cap_r` median** | `cap_r` p10 (high vol) | `cap_r` p90 (low vol) |
|---|---:|---:|---:|---:|---:|
| `qld_trend_long_1d` | 2.5 | 0.0284 | **1.40** | 0.73 | 2.20 |
| `tqqq_trend_long_1d` | 2.5 | 0.0401 | **0.99** | 0.51 | 1.53 |
| `qqq_trend_long_1d` *(control)* | 2.0 | 0.0157 | 3.14 | 1.33 | 5.09 |

**These are the two worst-placed legs in the fleet for a reachable take-profit, and the cause is
structural rather than incidental.** They are 2× and 3× leveraged, so ATR/entry runs 1.8× and 2.6×
QQQ's, and `cap_r` is inversely proportional to it. **On TQQQ the median `cap_r` is 0.99 — the venue
clamp binds BELOW 1R, i.e. the take-profit rests nearer than the stop.**

Set against MI-156 § 3's measured live `cap_r` table, whose lowest entries were `gdx_pullback_1d`
1.28 and `trend_donchian_ada_4h` 2.11, **QLD 1.40 and TQQQ 0.99 sit at and below the bottom of the
entire fleet range.** This explains the `tie_no_improvement` results exactly: a declared `tp_r` of
2, 2.5, 3, 4 or 6 is inert on these legs **by arithmetic**, before any question of edge arises.

⚠️ **WHAT § 3 DOES NOT ESTABLISH.** This `cap_r` is derived from the **candle** series (ATR14/close),
not from live trades — both legs return **zero** `position_telemetry` rows, which MI-156 § 8 already
recorded for `tqqq_trend_long_1d`. It is the distribution of the ratio the units themselves compute,
which is the right quantity; it is **not** a per-trade live measurement, and no live `cap_r` for
these two legs exists to check it against.

---

## 4. A cell DID pass on QLD — and it must not be shipped

`qld_trend_long_1d` cell **`sm2`** graded `path_b_wf_pass`, walk-forward **5/6 effective** (IS
`d_net_r` +4.20, OOS positive; both arms failed the primary gate on `maxdd_worse`, which is what
routes a cell to Path B).

**It carries no take-profit.** It is a **stop** cell — the same shape as the `sm1.5` cell MI-156
declined for `gld_pullback_1h`, and the dispatch for this session named it explicitly: *approving a
reachable target is not approval of `sm1.5`, and you must not ship it.* The same reasoning binds
here, so **`sm2` is REPORTED and NOT PROPOSED.** It is recorded because a later session looking for
QLD exit evidence should find it rather than re-derive it — not because it is ready.

`tqqq_trend_long_1d` produced **no passing cell on any axis**.

---

## 5. ⚠️ The load-bearing caveat — my window is NOT the corpus's window

**This run used each leg's FULL available history; the committed corpus used a trailing 1830-day
window.** Measured on the QQQ control, that difference is a factor of ~4–5 in trade count (median
**140** trades full-history vs an implied **~30** in the corpus row for the same leg).

Two consequences, and neither should be softened:

1. **These verdicts are better-powered than any 1d row in the corpus** — 113 and 117 lifetime trades
   against 23–32 for the six sibling legs, and IS/OOS splits of 63/48 and 67/48 that actually
   approach the `split_target_oos` of 50 the workflow asks for and the six siblings cannot reach.
2. **They are therefore NOT directly comparable to the committed corpus rows**, and must not be
   pasted into `e35-bracket-corpus.jsonl` beside them as if they were. A corpus-comparable run is a
   separate dispatch of `e35-bracket-sweep.yml` at `--days 1830`, which the routing rung now permits
   and which this session did not run.

**I could not establish** what these legs' verdicts would be at 1830 days. Given § 3, the reachability
ceiling is a property of the instrument's volatility rather than of the window, so the take-profit
answer is very unlikely to change — but that is an INFERENCE, not a measurement, and the `sm2` result
in particular is a claim about a 20-year window.

Also not established: I did not re-derive the sweep's gate. `wf_pass` / `path_b_wf_pass` verdicts and
effective-win counts are read as the harness reported them.

---

## 6. What this puts back to the operator — Tier-3, NOT APPLIED

**DEC-20260907-TP-UNEXAMINED returns in a legitimate form.** The question is no longer *"is
`unexamined` an acceptable published state for two live legs?"* — it is now:

> Both legs were examined. Neither has a shippable take-profit, and § 3 shows why: their venue
> reachability ceiling is `cap_r` **1.40** and **0.99**, the lowest measured anywhere on the fleet.
> Should their `tp_intent` move from `mode: unexamined` to `mode: none`?

The honest `reason` is **not** the `trail_is_the_profit_exit` the other 20 legs carry — that names a
deliberate design choice. These two would need a distinct reason, because what was measured is that
*no target is expressible at this leverage*, e.g. `reason: no_reachable_target_venue_cap_binds_below_1_5r`
with the measured `cap_r` recorded beside it. Collapsing them into the existing `trail_is_the_profit_exit`
bucket would lose exactly the fact this session bought.

⚠️ **A SECOND, SHARPER QUESTION THIS RAISES, and it is outside this memo's scope.** A leg whose
take-profit cannot be placed above ~1R is a leg on which the bracket's profit side is *structurally*
decorative. That is an argument about whether `TP_VENUE_CAP_PCT` fits leveraged instruments at all —
**and `TP_VENUE_CAP_PCT` is explicitly off the table** (MI-148, and this session's standing
prohibitions). It is recorded as a question, not proposed as a change.

**Not proposed here, deliberately:** any `tp_r` value for either leg (none is reachable); the `sm2`
stop cell (§ 4); any change to `TP_VENUE_CAP_PCT`; any `execution: shadow` demotion (no measurement
says these legs are bad — § 2 says their *target* is unreachable, which is a different claim).

---

## 7. Provenance

Candles came from Yahoo's `v8/finance/chart` endpoint — **the same endpoint `yfinance` wraps**, taken
directly because the repo fetcher's `yfinance` client was rate-limited (HTTP 429) from this sandbox's
IP. Written to `data/{QLD,TQQQ,QQQ}_1d.csv` in the fetcher's own schema, under the stem
`e35_shard_plan.data_basename` derives (neither symbol is in `fleet.PROXY_DATA`, so the stem is the
symbol — the file's name asserts exactly what its content is).

**The positive control is what makes this admissible:** `qqq_trend_long_1d` was swept on a CSV built
the same way and reproduced the committed corpus's qualitative verdict — **zero passing TP cells, and
its single walk-forward candidate failing** (corpus: 0 `tp_gate_pass`, 0 `wf_pass`; this run:
`tp6_sm1.5 -> wf_fail 2/6`). A run on a CI runner through `scripts/ops/fetch_backtest_candles.py`
(now reachable for these legs) is the stronger provenance and is the recommended next run.

*Measured 2026-09-08. `config/strategies.yaml` unmodified. Reports:
`qld_tqqq.json/2026-09-08/{report.json,results.jsonl,SUMMARY.md}` and the QQQ control alongside.*
