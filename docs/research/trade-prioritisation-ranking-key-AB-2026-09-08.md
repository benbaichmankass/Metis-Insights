# Trade prioritisation — does the ranking KEY drive outcome?

> **Doc status:** `live` · category `research` · measured `2026-09-08` · answers
> `WO-20260908-BUILD-THE-N-BOOK-RANKING-KEY-HARNESS` and
> `OI-20260831-TRADE-PRIORITISATION-IS-LIVE-BUT-UNPROVEN-AND-ITS-AB-HARNESS-DOES-NOT-EXIST`.
> Design: [`trade-prioritisation-research-DESIGN.yaml`](trade-prioritisation-research-DESIGN.yaml).

## The answer, in one line

**NO — and that is a real result.** At power, the shipped `confidence_first`
key is **NOT distinguishable** from the `priority_first` key it replaced on
2026-08-31, nor from `recent_pnl_first`. The only ranking effect that survives
measurement is **STABILITY** — any deterministic order beats a per-tick random
one — and confidence is not shown to be the *right* deterministic order.

The follow-up is therefore the confidence **SCORE**, not more ranking work,
which is exactly what the design predicted
(`BL-20260831-CONFIDENCE-SATURATES-AT-ONE-SO-HALF-OF-ARBITRATIONS-CANNOT-BE-DECIDED-ON-IT`).

⚠️ **This does not say the live key is wrong, and it must not be quoted as
saying so.** It says the correctness argument it shipped on has not been joined
by an outcome result, and that no cheaper alternative was shown to be better
either. Flipping the live key is Tier-3 and nothing here proposes it.

---

## 1. What was built, and what it unblocked

Two questions were blocked on ONE missing capability —
`scripts/backtest_system.py` models **one shared book** and always uses whatever
election key is compiled in:

| | question | status after this build |
|---|---|---|
| 1 | does the ranking KEY drive outcome? | **answered here** |
| 2 | global vs per-account arbitration? | **now runnable**, not answered — its own population, its own write-up |

Shipped:

* [`scripts/research/ranking_keys.py`](../../scripts/research/ranking_keys.py) —
  the four declared arms and the in-process installer.
* [`scripts/research/nbook_portfolio.py`](../../scripts/research/nbook_portfolio.py) —
  the N-book engine (`--book` repeatable, `--arbitration per_account|global`).
* [`scripts/research/rank_arm_report.py`](../../scripts/research/rank_arm_report.py) —
  the gate.
* [`.github/workflows/ranking-key-ab.yml`](../../.github/workflows/ranking-key-ab.yml) —
  fires it on a **free GitHub runner**, not the 1-core trainer
  (`docs/claude/vm-resource-management.md`).

**Nothing in `src/` changed.** The arm is installed by patching
`src.runtime.intents._election_sort_key` for the duration of a replay and
restoring it unconditionally — the technique `backtest_system.py` already uses
for `_decision_vol_regime` and `_REGIME_POLICY_PATH`. `confidence_first` **IS**
the live function, imported by identity, never a copy: a hand-written baseline
would be free to drift from the key that runs, and the A/B would then measure
the copy (`tests/research/test_ranking_keys.py` asserts the identity).

Drift between the two engines is closed by an **executable parity assertion**
rather than by co-location: at N=1 with the full roster, `nbook_portfolio`
reproduces `run_system_backtest` **trade-for-trade**, and the test FAILS if its
fixture stops producing trades so the assertion cannot go vacuous.

---

## 2. The population — state it before any number

**MEASURED** — the primary run. Source: committed at
[`docs/research/ranking-key-ab/2026-09-08-primary/`](ranking-key-ab/2026-09-08-primary/)
(four per-arm payloads + `REPORT.json`/`REPORT.txt`).

| | |
|---|---|
| symbol / feed | BTCUSDT 5m, Binance-vision archive, **2025-08-04 → 2026-09-08** (400 calendar days, 115,200 bars) |
| clock | 15m — **38,401 clock bars** |
| roster | 8 harness members: `trend_donchian`, `fade_breakout_4h`, `squeeze_breakout_4h`, `fvg_range_15m`, `turtle_soup`, `ict_scalp_5m`, `hf_displacement_cont`, `hf_vwap_revert` |
| books / arbitration | 1 book, `per_account` (identical to `global` at N=1 by construction) |
| `signal_ttl_bars` | **16** — live-faithful (see §3) |
| costs | fee 7.5bps roundtrip + venue-aware slippage + perp funding, the ONE shared model |
| seed | `20260908` (the control is reproducible) |
| fold panel | 6 fixed equal-width time slices |
| **contested elections ACHIEVED** | **1,270** (the design *declared* 1,780 — grade against the achieved number, never the declared one) |
| **contested TRADES** | **138–206 per arm** |

⚠️ **Contested trades, not contested elections, are the outcome denominator.**
A contested election only *produces* a trade when the book is flat, so 1,270
elections yield ~138–206 trades. Against the design's own two-sample floor of
**393 per group** at d=0.2, the primary run is **UNDERPOWERED** — which is why
§5 reports a second, deliberately powered arm.

---

## 3. `signal_ttl_bars` is the knob that sets contest density — and it is a HARNESS parameter

**MEASURED**, same roster/feed/clock as above, `confidence_first`, current code:

| `signal_ttl_bars` | contested elections | contested trades |
|---:|---:|---:|
| 1 | 82 | 49 |
| 4 | 289 | 81 |
| 8 | 579 | 133 |
| **16 (primary)** | **1,270** | **138** |
| 32 | 2,941 | 263 |
| 96 | 10,830 | 1,130 |

⚠️ **Choosing the TTL to hit the design's declared `expected_n=1780` would be
manufacturing the population**, and this document does not do it. The primary
TTL is chosen on a live-faithfulness argument, stated so it can be argued with:
the signal stream carries one row per closed bar of a strategy's OWN timeframe,
so consecutive emissions from a 4h member are 16 clock bars apart, and TTL=16 is
*"a signal stays live until its strategy could next speak."* Beyond 16 a signal
outlives the condition that produced it.

**The sample was raised the sanctioned way — by adding real coverage.** The v1
four-member BTCUSDT roster yields **87** contested elections / **3** contested
trades at TTL=16, which cannot power anything. Widening to the eight harness
members that can trade BTCUSDT took it to 1,270 / 138 *at the same TTL*. That is
`docs/CLAUDE-RULES-CANONICAL.md` § "Green is not evidence" obligation 5 applied:
raise the sample, never lower the bar.

---

## 4. Primary result (live-faithful TTL, UNDERPOWERED)

**MEASURED** — `docs/research/ranking-key-ab/2026-09-08-primary/REPORT.txt`.

Control `random_tiebreak`: 206 contested trades, net R **−301.00**, net
**−$2,231.80**, expectancy **−1.4612 R**, contested-path maxDD **$2,247.68**.

| arm | n | d_netR vs control | d_net $ | expectancy R | folds won/lost/inert | p (Bonf α=0.0167) | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `confidence_first` | 138 | **+214.63** | +$938.83 | −0.6259 | 4 / 0 / 2 | 0.260 ✗ | preferred |
| `priority_first` | 206 | +145.20 | +$624.24 | −0.7563 | 3 / 1 / 2 | 0.313 ✗ | preferred |
| `recent_pnl_first` | 189 | +180.05 | +$733.98 | −0.6400 | 3 / 1 / 2 | 0.236 ✗ | preferred |

**Head-to-head — the operator's actual question** (`confidence_first` graded
against each alternative as the reference):

| vs | d_netR | folds won/lost/inert | p | verdict |
|---|---:|---:|---:|---|
| `priority_first` | +69.43 | 4 / 0 / 2 | **0.776** | preferred |
| `recent_pnl_first` | +34.58 | 2 / 1 / 2 | **0.975** | preferred |

A `preferred` verdict on the gate with p ≈ 0.78 and 0.97 is **not a
demonstration of anything**. The gate is a decision rule, not a significance
test, and this is exactly the case where both must be read together.

---

## 5. Powered secondary (TTL=96, deliberately NOT live-faithful) — and it agrees

**MEASURED** —
[`docs/research/ranking-key-ab/2026-09-08-secondary-powered-ttl96/`](ranking-key-ab/2026-09-08-secondary-powered-ttl96/).
Run **only** to establish whether the primary's null is an artefact of n. Its
TTL is not live-faithful and **no live claim rests on it**.

n = 1,130–1,543 per arm, comfortably above the design's 393/group floor.

| arm | n | d_netR vs control | p | verdict |
|---|---:|---:|---:|---|
| `confidence_first` | 1,130 | +701.74 | 0.097 ✗ | preferred |
| `priority_first` | 1,467 | +345.17 | 0.446 ✗ | preferred |
| `recent_pnl_first` | 1,543 | +548.54 | 0.204 ✗ | preferred |

**Head-to-head, AT POWER:**

| vs | d_netR | d_net $ | folds won/lost/inert | p | verdict |
|---|---:|---:|---:|---:|---|
| `priority_first` | +356.57 | +$2,846.78 | **2 / 2** / 2 | 0.583 | **not_preferred** |
| `recent_pnl_first` | +153.20 | +$3,177.69 | **2 / 2** / 2 | 0.937 | **not_preferred** |

**At power the shipped key loses its fold majority against BOTH alternatives and
comes nowhere near the corrected alpha.** The primary's null is not an artefact
of sample size.

⚠️ **R and dollars disagree in this table and the disagreement is real, not a
typo.** `priority_first` and `recent_pnl_first` post a *worse* net R and a
*better* net $ than the shipped arm. R normalises by each trade's own entry
risk while dollars do not, so the two answer different questions and an arm can
win one and lose the other. Quote both or quote neither.

---

## 6. The confound that explains most of the control's deficit: INSTABILITY

**MEASURED** — flip counts from the same runs (`books[].by_exit_reason`):

| arm | primary trades / flips | powered trades / flips |
|---|---:|---:|
| `confidence_first` | 660 / **31** | 3,262 / **20** |
| `priority_first` | 715 / **28** | 3,377 / **18** |
| `recent_pnl_first` | 719 / **34** | 3,446 / **18** |
| `random_tiebreak` | 708 / **92** | 3,493 / **331** |

The control flips **3×** (primary) to **17×** (powered) as often as any
deterministic arm. That is by construction: the control re-rolls its draw per
tick — deliberately, because a frozen draw would be a NAME ranking wearing a
random label — so it changes the winning *owner* between ticks and pays
close-and-reopen churn for it.

**So "every deterministic arm beats the control" is substantially a measurement
of STABILITY, not of ranking quality.** The live `_election_sort_key` docstring
already says a flapping winner is *"strictly worse than an arbitrary-but-stable
one"*; this run puts a number on it. Anyone quoting the arm-vs-control deltas
without the flip counts is attributing to the ranking signal something the churn
may explain.

---

## 7. Two things that would have produced a wrong answer, and were caught

1. **A fold-majority-only gate would have passed a losing arm.** Every
   comparison here shows **2 of 6 folds INERT (33.3%)** — the same 30–39% band
   in which `be_floor_r=1.5` won a strict majority of exercised folds while
   losing 35R. The gate is therefore **pooled net R AND a majority of EFFECTIVE
   folds**, never either alone, and inertness is **imported** from
   `scripts/research/m20_wf_effective.py::is_inert` rather than restated. The
   reporter's self-test plants exactly that trap and requires a refusal.

2. **`decided_by` mislabelled the control's own deciding term.** `deciding_term`
   names terms by zipping `(target_qty,) + ELECTION_TERMS` against the key
   tuple; the control originally omitted the `target_qty` slot, so its random
   draw was reported as `decided_by=target_qty` on **100 of 208** contested
   trades in the first primary run — unprovenanced diagnostic output
   (sub-class A) in the exact field the whole analysis stratifies by. Fixed at
   the source with a constant placeholder in that slot (constant, so it can
   never itself decide), and pinned by
   `test_the_control_can_never_report_target_qty_as_the_deciding_term`.

---

## 8. Stratification by `decided_by`, and a finding that is not about ranking

**MEASURED**, primary run, contested subset:

| arm | `decided_by` | n | net $ | expectancy R |
|---|---|---:|---:|---:|
| `confidence_first` | confidence | 65 | −$1,079.02 | −0.690 |
| | declared_priority | 79 | −$393.83 | −0.939 |
| `recent_pnl_first` | recent_pnl | 175 | −$1,408.61 | −0.639 |
| | confidence | 3 | −$25.27 | −0.239 |
| | declared_priority | 11 | −$63.94 | −0.758 |
| `random_tiebreak` | random | 204 | −$2,305.98 | −1.517 |

⚠️ **Every expectancy in this table is NEGATIVE, under every arm and every
stratum.** On this population the CONTESTED subset loses money regardless of who
wins the contest. That is a finding about the contested population, not about
the ranking, and it bounds how much any ranking key can be worth here: choosing
better among losing candidates cannot make the subset profitable. It is not
graded further in this document and is not a demotion signal for any leg — it
is flagged so the next session does not read a positive `d_netR` as a profitable
arm.

⚠️ Note also that under `confidence_first` only **65 of 144** contested trades
were decided BY confidence; the rest fell through to the tier below. That is the
saturation the design named as a known confound, visible in the field built to
make it visible.

---

## 9. What this does NOT establish

* **Nothing about per-account fan-out.** The primary is one book. The N-book and
  `global` modes are built and tested (a book that does not carry the global
  winner **stands aside**, and it is COUNTED — the
  `BL-20260827-...-STARVES-ITS-PAPER-SIBLING` shape), but that question needs
  its own population and its own write-up.
* **Nothing about symbols other than BTCUSDT**, nor about the live 52-leg
  roster. The live `conviction_arbitration` soak measured 5.01 contests/day
  across the whole fleet; this replay's density is a property of eight BTCUSDT
  members, and the two are not comparable.
* **No p-value here clears its corrected alpha**, and every p is a **lower
  bound** — the test is an iid Welch normal approximation while backtest trade
  sequences are autocorrelated. Treat all of them as "not established".
* **No live change is proposed.** Flipping the ranking key is Tier-3.

## 10. Reproducing this

```bash
python3 scripts/ops/fetch_backtest_candles.py --symbol BTCUSDT --interval 5 \
    --days 400 --source binance_vision --out data/ab_candles.csv

for arm in confidence_first priority_first recent_pnl_first random_tiebreak; do
  python3 scripts/research/nbook_portfolio.py --data data/ab_candles.csv \
    --clock-tf 15m --signal-ttl-bars 16 --folds 6 --seed 20260908 \
    --roster trend_donchian,fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m,turtle_soup,ict_scalp_5m,hf_displacement_cont,hf_vwap_revert \
    --ranking-key "$arm" --json "runtime_logs/ranking_ab/$arm.json"
done

python3 scripts/research/rank_arm_report.py runtime_logs/ranking_ab/*.json
```

or dispatch `.github/workflows/ranking-key-ab.yml`, which does the same on a
free runner and lands the payloads under `docs/research/ranking-key-ab/`.

Runs are **bit-reproducible** given the same feed and seed (verified: two
warm-cache invocations produced byte-identical payloads), and the signal cache
is faithful (a `--refresh` regeneration equals the cached frame exactly).
