# What expectation should a bracket carry at entry — per family, constructed not clamped

**Date:** 2026-08-23 · **Tier-1, observe-only.** Every configuration change this
document points at is **Tier-3** and is written here as a *proposal with its
evidence*, not applied. Nothing in this session touched `config/strategies.yaml`.

**Operator directive this answers:**

> *"Brackets ALWAYS represent our prediction of where the trade should end …
> The only solution here is to properly build out the active management infra,
> not layer on bandaids to a poorly constructed strategy."*

**Tooling:** `scripts/research/bracket_expectation_census.py` (11/11 selftest),
new here. Live values from `/api/diag/log_file?name=target_extension_soak`
(direct diag, served by `https://ict-bot.duckdns.org`). Sweep values are
**re-read from** `e35-bracket-geometry-sweep-2026-08-20.md` — **no sweep was
re-run**, and § 3 below is a new reading of that existing table, not new
measurement.

---

## 0. The headline

**A target is not a free parameter.** The venue clamp makes the reachable
target a function of the stop and of the instrument's volatility:

```
cap_r = TP_VENUE_CAP_PCT * entry / risk      risk = atr_stop_mult * ATR
      = 0.099 / (atr_stop_mult * ATR/entry)
```

So `cap_r` is **inversely proportional to `atr_stop_mult`** — *widening a stop
lowers the reachable target in R* — and falls with volatility. An expectation
is therefore **constructed by choosing (stop, target) jointly**, and is
**clamped whenever the target is chosen alone.** Three independent lines of
evidence below say the same thing, and the third is a live counter-example
created today.

---

## 1. The population is 42% larger than a `grep` of the YAML shows

⚠️ **State the population — this number differs across three of them, and a
fourth was wrong.**

| population | n | sentinel **declared in YAML** | sentinel **effective** | real target |
|---|---|---|---|---|
| all declared | 55 | 30 | 40 | 12 |
| enabled, any execution | 52 | 28 | 38 | 12 |
| **enabled + live** | **45** | **24** | **34** | **11** |

The declared column reproduces the operator's brief exactly. The **effective**
column is the one that describes runtime: **10 `pullback` legs declare no
target key at all** and inherit `tp_r = 50.0` from their strategy class
(`htf_pullback_trend_2h.py:67`; the same default exists in `trend_donchian.py:81`,
`squeeze_breakout_4h.py:65`, `fade_breakout_4h.py:88`).

```
gdx_pullback_1d   gld_pullback_1d   gld_pullback_1h   iaum_pullback_1d   ief_pullback_1d
qqq_pullback_1h   slv_pullback_1d   spy_pullback_1h   tlt_pullback_1d    tlt_pullback_1h
```

**The soak confirms this from the other side**, which is what makes it a
measurement rather than an inference: those legs emit
`target_source_key: "tp_r", target_r: 50.0` on rows whose YAML contains
neither key.

⚠️ **Declared and effective are reported separately and must never be summed or
collapsed.** A leg that writes `tp_r: 50.0` chose the sentinel; a leg that
writes nothing inherited it. Identical runtime behaviour, **different remedy** —
which is exactly why `target_expectation.STATE_NO_TARGET_KEY` exists.

### 1.1 That state is unreachable in production — a real gap in a good module

`resolve_expectation` carefully distinguishes `no_target_key` from
`sentinel_no_expectation`, and its docstring gives the reason: calling one the
other *"would accuse 20 legs of a defect they may not have"*. **In production it
can never fire.** The monitor hands it the *merged* config (class defaults +
YAML), so the default has already been applied and the state resolves to
`sentinel_no_expectation` every time. All 72 soak rows are that state; zero are
`no_target_key`.

The **grade** is behaviourally right — the effective `tp_r` really is 50. What
is lost is **provenance**: whether the sentinel was chosen or inherited, which
is the axis that decides the remedy. Filed below.

### 1.2 The module's own counts are stale by one commit

`target_expectation.py` says *"29 of 52 enabled legs … 26 of them
`execution: live`"*. Measured at `9544ced~1` that was exactly right; `#10171`
(xrp `tp_r` 50→3, plus the `htf` demotion) moved it to **28 / 24**. Not a defect,
but the docstring is quoted as a population and should track.

---

## 2. Reading the soak — and it is THIN, stated with the row count

**72 rows are 16 open trades observed 1–5× each, not 72 observations.** 15
distinct legs = **44% of the 34 effective-sentinel live legs**; 21 have zero
rows. Window **2026-08-23 10:12Z → 14:43Z (4.5 h)**.

- **`expectation_state`: 72/72 `sentinel_no_expectation`.**
- **`extension_state`: 72/72 `no_expectation_declared`.**

Read with the operator's caveat, this is the **expected and correct** shape, not
a null result: these legs have no target to extend *from*. A read on
`extension_state` alone would score it "the lever never fires"; it means "there
was never a target".

**The soak's real payoff so far is a different field.** It publishes `cap_r` and
`thesis` per live trade, and that is what made § 3–4 measurable at all.

### 2.1 31% of open trades could not use the lever even if targets existed

Per **trade** (n=16), not per row:

| thesis predicate | state | trades |
|---|---|---|
| `donchian_rebreak` | readable | 6 |
| `adx_floor` | readable | 5 |
| `adx_floor` | **`no_adx_min_declared`** | **5** |

`thesis_unknown` never extends — correctly, per § 4: an unread thesis is not an
intact one. But it means **5 of 16 open trades (31%) are already excluded from
the lever** before any target work begins. All five are the `pullback_1d`/`1h`
family (`mhg`, `tlt`, `ief`, `spy`, `qqq`) — **the same family that inherits its
sentinel**. These legs declare neither a target nor a thesis parameter.

---

## 3. The existing sweep already answered "which axis", and nobody read it that way

Re-reading `e35-bracket-geometry-sweep-2026-08-20.md` (19 legs × 199 cells =
3,781 measured cells, net of fees, 2021-08-16→2026-08-19) by **which axis the
argmax moved**:

| | count |
|---|---|
| argmax moves the **stop** | **17 / 19** |
| argmax moves the **tp** | 10 / 19 |
| argmax is **joint** (tp *and* stop) | 8 / 19 |
| argmax uses the **tightest** stop in the grid (1.5) | 9 / 19 |

The sweep's own conclusion was *"mostly overfitting; one joint cell survives,
n=1 leg"*. That is right about any single leg. But the **direction** across legs
is a separate and much stronger observation — and it splits by family:

| family | legs w/ stop change | stop multiples chosen | mean |
|---|---|---|---|
| **donchian** | 11 | 1.5 ×8, 2.0 ×3 — **zero at 3.0 / 3.5** | **1.64** |
| squeeze | 1 | 1.5 | 1.50 |
| **pullback** | 5 | 2.0 ×1, 3.0 ×2, 3.5 ×2 — **zero at 1.5** | **3.00** |

`STOP_MULT_GRID = (1.5, 2.0, 3.0, 3.5)`. The two families select from
**disjoint halves**, overlapping only at 2.0.

⚠️ **How much this is worth, honestly.** Each argmax is the maximum of a
199-cell search, so individually noise-prone; the sweep's own gate killed 112
of 133 rows. The legs are also not independent: 11 donchian legs are **8
distinct (symbol, timeframe) pairs** and **6 distinct symbols**. Under a null of
uniform choice across four stop values, all landing in the two tightest is
`(1/2)^6 ≈ 0.016` at the conservative n. **Suggestive and directionally
consistent, not established** — and the pullback arm (`(3/4)^5 ≈ 0.24`) is
weaker still. This is a hypothesis with a mechanism, not a result.

### 3.1 The mechanism is `cap_r`, which is why the joint cell is the one that survives

A tp-only sweep searches a dimension the stop partly determines. Tightening the
stop **raises the ceiling on the target** — so "tighten the stop" and "make a
real target placeable" are *the same move*, and a single-axis sweep cannot see
it. That is precisely why the only cell to survive dispersion testing
(`eth_pullback_2h tp2_sm3.5_to48`, `split_sensitive: false`, `pass_fraction 1.0`)
was joint while the best single-axis cell on the same leg flipped sign.

---

## 4. Is an expectation constructible? A per-leg answer

Backing the **real** `ATR/entry` out of each live trade's measured `cap_r`
(`ATR/entry = 0.099 / (atr_stop_mult × cap_r)`), then re-computing `cap_r` at
each family's sweep-preferred stop. **n = 15 legs = 15 open trades — one trade
each, so each `ATR/entry` is a single-entry reading, not a distribution.**

| leg | stop | live `cap_r` | ATR/entry | `cap_r` at preferred stop | verdict |
|---|---|---|---|---|---|
| `spy_pullback_1h` | 2.5 | 61.22 | 0.06% | 43.7 | ✅ |
| `tlt_pullback_1d` | 2.5 | 14.05 | 0.28% | 10.0 | ✅ |
| `ief_pullback_1d` | 2.5 | 11.41 | 0.35% | 8.2 | ✅ |
| `qqq_pullback_1h` | 2.5 | 8.96 | 0.44% | 6.4 | ✅ |
| `mes_trend_long_1d` | 2.5 | 7.48 | 0.53% | 12.5 | ✅ |
| `slv_trend_1h` | 2.5 | 6.14 | 0.65% | 10.2 | ✅ |
| `iwm_trend_long_1d` | 2.5 | 2.84 | 1.40% | 4.7 | ✅ |
| `scha_trend_long_1d` | 2.5 | 2.78 | 1.43% | 4.6 | ✅ |
| `trend_donchian_eth_4h` | 2.5 | 1.40 | 2.84% | 2.3 | ✅ |
| `qld_trend_long_1d` | 2.5 | 1.31 | 3.02% | 2.2 | ✅ |
| `avax_pullback_2h` | 2.5 | 2.26 | 1.75% | 1.6 | ⚠️ ~1.6R only |
| `mhg_pullback_1d` | 2.0 | 2.33 | 2.13% | 1.3 | ⚠️ ~1.3R only |
| `eth_pullback_2h` | 2.5 | 1.79 | 2.22% | 1.3 | ⚠️ ~1.3R only |
| **`xrp_pullback_2h`** | 2.5 | **0.69** | **5.76%** | **0.49** | ❌ **no real target fits** |
| **`ada_pullback_2h`** | **1.5** | **0.84** | **7.83%** | **0.36** | ❌ **no real target fits** |

**Three groups, three different answers:**

- **✅ Constructible today (10/15).** Every donchian/trend leg and every low-vol
  pullback leg. For donchian the sweep-preferred tightening to 1.5 ATR does
  *both* jobs at once: it is the change the sweep independently selected for
  8 of 11 legs, **and** it lifts `cap_r` from 1.31–7.48 to 2.19–12.47, which is
  where a real multi-R target becomes placeable.
- **⚠️ Tight but real (3/15).** A ~1.3–1.6 R target fits at the family's
  preferred wide stop. A 3 R target does not. A modest honest target beats a
  sentinel.
- **❌ Not constructible at all (2/15).** `xrp_pullback_2h` and
  `ada_pullback_2h` run at **5.76%** and **7.83%** ATR/entry. At *any* sane stop
  the venue ceiling is below 1 R. **`ada_pullback_2h` already runs the tightest
  stop in the grid (1.5) and still only reaches 0.84 R.**

That last group is the finding I most want on the record, because it is the one
the directive cannot be satisfied for by construction: **on Bybit, at 2 h, at
6–8% ATR, a predictive bracket does not fit inside the venue's own clamp.** The
honest options are all Tier-3 and all have costs — a sub-1R target (inverts the
strategy into a scalp), a different venue/timeframe, or accepting that these two
legs are trail-exited by design and saying so explicitly rather than via a
sentinel.

---

## 5. ⚠️ Live: `#10171` moved XRP from *no expectation* to *a refused one*

`#10171` (merged 13:37Z today) set `xrp_pullback_2h` `tp_r: 50.0 → 3.0` and left
**`atr_stop_mult` unchanged at 2.5** (only `trail_mult` moved, 5.0 → 6.0).

Measured on the open trade, all 5 soak rows: **`cap_r = 0.687`.** The declared
3.0 R therefore **binds on 5 of 5 rows**, and the level that actually rests is
**0.687 R — a target nearer than the stop.** Making 3.0 R reachable would need
the stop **4.36× tighter (0.573 ATR)**, which is not a sane stop.

So the leg did not move `sentinel_no_expectation → declared`. It moved
**`sentinel_no_expectation → clamped`** — a state `target_expectation.py`
deliberately keeps separate: *"a clamped leg had an expectation the venue
refused to place; a sentinel leg never had one."* The bracket now **states** a
prediction the venue will not honour.

⚠️ **Scope and limits of this claim, stated plainly.** `cap_r` is fixed at entry
and varies with entry ATR, so **n = 1 trade**; a calmer entry would clamp less.
The R:R-below-1 condition itself is already recorded in root `CLAUDE.md`
(*"`xrp_pullback_2h` at R:R 0.687 (real money)"*) — that is independent
confirmation of the same number, not a new discovery. **What is new is that the
fix did not resolve it**, because the target moved and the stop did not. This is
the live instance of § 0: choosing a target alone gets it clamped.

**Routing (why this matters):** `xrp_pullback_2h` → **`bybit_2`, real money**
(also `bybit_1` paper, `bybit_portfolio`). The census flags the same shape on
`trend_donchian_eth_prop` and `trend_donchian_sol_prop` (`tp_r: 6.0`,
`cap_r ≈ 1.98` at a 2% reference ATR) — both routed to **`breakout_1`**, where a
breach is terminal. **All three non-scalp real targets in the live fleet are
clamped.**

---

## 6. The per-family answer, and how each is constructed

Per § 4, the thesis is the family's own entry condition re-evaluated. The
expectation should be **the level at which that condition is expected to
expire** — not a round number, and not the venue's rejection threshold.

**`donchian` / `squeeze` — 19 of the 34 effective sentinels.**
Thesis: *is the channel still being pushed* (`donchian_rebreak`, readable on
6/6 observed trades). A breakout that fails is wrong quickly, which is what the
sweep's preference for the tightest stop encodes. **Construction:** tighten
`atr_stop_mult` toward 1.5–2.0 — the sweep's own argmax for 11/11 legs — which
lifts `cap_r` into the 2.2–12.5 R band, then set the target from the empirical
excursion of breakouts *of this channel width*, capped at `cap_r`. One change,
two effects, and they are the same change.

**`pullback` — 15 of the 34, and the family that needs the most work.**
Thesis: *does ADX still clear `adx_min`* — **unreadable on 5 of 16 trades**,
because `adx_min` is not declared on the 1d/1h legs. **Step one is not a target
at all: it is declaring `adx_min` on the 10 inheriting legs**, because
`thesis_unknown` never extends and a target without a thesis cannot be managed.
Only then does the target question arise, and the answer is volatility-split:
low-vol legs have room for a genuine multi-R target; the 2 h crypto legs have
0.36–1.6 R and two of them have nothing.

**`ict_scalp` — 8 legs, already compliant.** `tp_at_r: 1.5`, a real expectation
that fits well inside `cap_r`. **Not part of the problem population**, and worth
saying because it is the existence proof that the fleet *can* carry one.

---

## 7. What I am proposing, and what I am not

**Tier-1, done here:** the census script, this document, and the three backlog
rows below.

**Tier-3 — proposed with evidence, explicitly NOT applied, needs an operator OK:**

1. **Declare `adx_min` on the 10 inheriting pullback legs.** Cheapest, highest
   leverage, unblocks 31% of open trades from `thesis_unknown`, and changes no
   geometry.
2. **Tighten the donchian family's stop toward 1.5–2.0 ATR**, one leg at a time
   with a per-leg walk-forward. ⚠️ The sweep is argmax-of-199 and its gate killed
   112/133 rows — this needs its own dispersion test per leg, not the argmax.
3. **Re-open `xrp_pullback_2h`.** Its declared 3.0 R is unplaceable at the
   current stop; either the stop moves with it or the target should be set at
   what actually fits. It is on real money.
4. **Decide explicitly what `xrp_pullback_2h` and `ada_pullback_2h` are.** If a
   predictive bracket cannot fit, that should be a declared property of the leg,
   not an inherited 50.

**Not proposed:** no demotion of any leg (none has had a genuine multi-axis
tuning attempt, and TUNE BEFORE DEMOTE is canonical); no model promotion; no
change to the soak, which should keep accruing — at 44% leg coverage and 4.5 h
it is far too thin to carry a verdict.

---

## 8. Filed

| id | sev | note |
|---|---|---|
| `BL-20260823-NO-TARGET-KEY-STATE-UNREACHABLE-IN-PRODUCTION` | medium | The 5th state can never fire; sentinel provenance (chosen vs inherited) is collapsed for 10 live legs |
| `BL-20260823-XRP-DECLARED-TARGET-EXCEEDS-VENUE-CAP` | high | `tp_r 3.0` vs `cap_r 0.687`, real money; sentinel→**clamped**, not →declared |
| `BL-20260823-TARGET-EXPECTATION-DOCSTRING-COUNTS-STALE` | low | 29/26 vs measured 28/24 |

**Evidence contamination caveat, carried forward as instructed:** none of the
above touches IB live-parity. The `place_protective()` parentless-OCA defect
silently dropped take-profits on any position whose protection was re-armed, and
**the size of that population is unmeasured** (2 observations is not a rate). No
comparison in this document uses IB exit outcomes; the harness reads historical
candles only. Any future calibration of these targets against *live IB* fills
must measure that population first.
