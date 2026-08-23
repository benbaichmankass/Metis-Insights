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

⚠️ **CORRECTED 2026-08-23 — the population was five open trades, and it is not
representative.** Re-measured over the leg's **296-entry backtest population**
(2021-08-19 → 2026-08-23): median ATR/entry is **1.87%**, not the ~5.8% those
live rows showed, and median `cap_r` is **2.11**, not 0.687. The soak figure sits
around the **p95** of the distribution. So `tp_r 3.0` is genuinely placeable on
**22.3%** of entries and clamped on 77.7% — the finding stands in direction and
was **overstated in magnitude**, and my accompanying claim that the leg is
*"structurally impossible"* was wrong. Full re-measurement:
[`xrp-pullback-joint-geometry-2026-08-23.md`](xrp-pullback-joint-geometry-2026-08-23.md).

⚠️ **And the obvious remedy is measured WRONG.** Making 3.0R reachable by
tightening the stop — what the placeability arithmetic invites — was run as a
joint grid: **37 (stop, target) cells, zero positive, and the interior optimum is
the LIVE geometry** (stop 2.5 / `tp_r` 3.0, −13.02R). Stop 1.0 costs a further
**66R**. The second axis is the same story: 20 trail cells, zero positive, live
cell optimal. So `#10171` moved the leg the right way and there is **no Tier-3
change to propose here** — two of three axes are exhausted, and the entry axis
is untested.

⚠️ **Scope and limits of the original claim, stated plainly.** `cap_r` is fixed at
entry and varies with entry ATR, so the soak read was **n = 5 trades**; a calmer
entry clamps less, which is exactly what the 296-entry population shows.
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
because `adx_min` is not declared on the 1d/1h legs. ⚠️ **An earlier draft of
this paragraph said step one was to DECLARE `adx_min` on those legs. § 7.5
measured that and the answer is NO** — the set is 12 not 10, it is an
entry-behaviour change (no ADX filter exists on them today), 25/28/30 would
refuse 53–86% of entries, and the only non-destructive value is fitted to the
sample minimum. The predicate is wrong, not the value: **these legs' entry
thesis is trend-and-pullback STRUCTURE, never ADX**, so the remedy is a
trend-structure thesis predicate in `_pullback_thesis_intact` — code, with its
own falsifier — not a value in YAML. Read § 7.5 before acting on this
paragraph. Only then does the target question arise, and the answer is
volatility-split: low-vol legs have room for a genuine multi-R target; the 2 h
crypto legs have 0.36–1.6 R and two of them have nothing.

**`ict_scalp` — 8 legs, already compliant.** `tp_at_r: 1.5`, a real expectation
that fits well inside `cap_r`. **Not part of the problem population**, and worth
saying because it is the existence proof that the fleet *can* carry one.

---

## 6.5 The measured starting point per leg — the venue already chose one

*(Added 2026-08-23, after the § 6 families were written. It changes the shape of
the answer, so it is a section rather than an edit.)*

§ 0 opened on the operator's premise: *a sentinel target is not an expectation;
it is the absence of one.* That is right about the **config** and, on half the
sentinel legs, **wrong about the venue**.

`_TP_SENTINEL_CAP_PCT`'s comment justifies the 9.9% clamp as *"still far enough
that the monitor's Chandelier trail remains the real profit-exit."* Measured
across **10 sentinel legs** (e35 base runs, full history, cap ON — i.e. live
conditions), clamped-TP exits vs trail exits:

| leg | sym/tf | TP : trail | **clamp-imposed target (median R)** |
|---|---|---|---|
| `trend_donchian` | BTC/1h | 0.10 | 5.98 |
| `trend_donchian_1h` | BTC/1h | 0.18 | 5.38 |
| `trend_donchian_eth` | ETH/1h | 0.33 | 4.08 |
| `trend_donchian_sol` | SOL/1h | 0.68 | 3.22 |
| `trend_donchian_avax_4h` | AVAX/4h | 0.97 | 1.48 |
| `trend_donchian_eth_4h` | ETH/4h | **1.71** | 2.04 |
| `xrp_pullback_2h` | XRP/2h | **2.78** | — |
| `trend_donchian_xrp_4h` | XRP/4h | **3.11** | 2.11 |
| `trend_donchian_ada_4h` | ADA/4h | **3.86** | 1.57 |
| `trend_donchian_sol_4h` | SOL/4h | **3.88** | 1.44 |

**The claim holds on every 1h leg and fails on every 4h leg plus the 2h
pullback.** Arithmetic, not luck: a longer bar carries a bigger ATR, so the same
9.9% price distance is fewer R — and the sweep's own `tp_r_effective_median` is
monotone in exactly that.

### What this changes about the question

**On 5 of 10 sentinel legs a hard target already exists, is hit 1.7–3.9× more
often than the trail, and nobody chose its value.** It is 9.9%, picked in
May 2026 to satisfy Bybit ErrCode 10001.

So the per-family construction task is **not** *"invent an expectation for a leg
that has none"* on those legs. It is *"replace a venue constant with a thesis"* —
and the constant is a **measured starting point**, not a guess: the right-hand
column is what the leg has actually been trading to, for its whole history, at
the R where it actually exits.

`sentinel_no_expectation` cannot express this. *"No target, the trail is the
exit"* and *"no DECLARED target, and the clamp is the dominant exit"* are
opposite conditions sharing one grade — the collapsed-state shape, in the
vocabulary built to grade exactly this. Filed as a `high` row in **PR #10198** (`…VENUE-CLAMP-IS-THE-UNDECLARED-TARGET…`;
the id resolves once that PR lands, so it is cited by PR here rather than as a
tracking reference this branch cannot resolve).

### The coherence check, and it is a strong one

The best risk-adjusted cell in the entire 2,189-cell donchian corpus is
`trend_donchian_sol_4h tp1.5_sm2_to96` — IS MAR 4.25 → **7.41**, OOS
0.73 → **1.44**, at **+0.015R of drawdown per +1R of return** in-sample and an
*improvement* in drawdown out-of-sample.

Its declared **1.5R** sits beside sol_4h's clamp-imposed **1.44R**. Its tighter
stop (2.0 vs 2.5) lifts `cap_r` by 1.25×, so 1.5R is genuinely *reachable*
rather than truncated. The winning geometry is, almost exactly:

> **declare what the venue was already placing, and tighten the stop until it is
> reachable.**

That is § 6's donchian construction rule — *"one change, two effects, and they
are the same change"* — arrived at independently, from the opposite direction,
and it is the first measured instance of it.

### The clamp also disarms the levers

The clamp does not only replace the target. It silently disarms **every lever
whose trigger was calibrated above the truncation point**. Measured on
`xrp_pullback_2h`: `trail_decay_arm_r: 4.49` fired **0 of 296 times** in five
years (max MFE **2.92**, so the arm sits at 1.54× the largest peak the leg has
ever printed), and `arm 4.49` returns **byte-identical** net R to `arm 0.0` at
every `trail_mult` tested. Uncapped, the same leg reaches 4.49R on 7.1% of
trades — so the value was fitted in a harness without `--tp-cap-pct` and is
inert in production.

⚠️ **State the population: 4 legs measured, 2 inert** — the other being
`trend_donchian_sol_4h` (arm **5.57** vs max MFE **3.85**), one of the fleet's
*better* performers, whose best cell is precisely the one that stops relying on
the trail. Four more enabled legs declare an arm and were not measured; that is
*we did not look*, not *reachable*. Filed as a `medium` row in **PR #10198** (`…DECLARED-TRAIL-DECAY-ARMS-ARE-INERT…`).

### Active management: the entry bracket is the precondition, not the alternative

The expectation a bracket carries at entry is a **starting** prediction that the
strategy is meant to revise as the trade develops — a momentum leg's momentum
read should drive its own revision. The point is not to pick the value that
works most often and freeze it; it is that **whatever the current read is, it
must be expressed as a bracket**, rather than left implicit in a wide sentinel
with a trailing measurement doing the work.

The infrastructure for the revision half already exists and is inert on exactly
this population. `_base.monitor` has declared a `{"tp": float}` verdict — *move
the take-profit* — since it was written, and **no strategy has ever produced
one** (AST-verified); `target_extension_soak` is now the annotate-only producer,
and its `sentinel_no_expectation` / `no_expectation_declared` states are these
legs.

A leg running a sentinel behind a trail has **no expectation to revise**, so the
revision machinery cannot engage. And per this section, what it is *actually*
running is a venue constant — which no thesis can revise either, because no
thesis chose it, and (per the levers above) the constant may also have disarmed
the trail decay that was supposed to be managing the trade instead.

**So the entry bracket and active management are one task, not two.** Declaring
the entry expectation is what turns the trail from *the whole exit policy* into
*one input to a revisable one*.

---

## 7. What I am proposing, and what I am not

**Tier-1, done here:** the census script, this document, and the three backlog
rows below.

**Tier-3 — proposed with evidence, explicitly NOT applied, needs an operator OK:**

1. ⚠️ **~~Declare `adx_min` on the 10 inheriting pullback legs~~ — I RETRACT the
   framing of this item. It is not the cheap observability fix I called it.**
   Corrected 2026-08-23 after the operator approved it *on my characterization*,
   which was wrong in three ways. Recorded here rather than quietly fixed,
   because the approval was given on the strength of the wrong description.

   **(a) The set is 12, not 10.** "Legs that inherit the target" and "legs that
   lack `adx_min`" are DIFFERENT SETS. The `adx_min`-lacking set adds
   `mgc_pullback_1d` and `mhg_pullback_1d`, which declare `tp_r: 50.0`
   explicitly but no floor. Declaring on only the 10 would leave two legs
   `thesis_unknown` while looking complete — and `mhg_pullback_1d` is one of the
   five legs actually OBSERVED in the soak with `no_adx_min_declared`.

   **(b) It is an ENTRY-BEHAVIOUR change, not an observability one.**
   `htf_pullback_trend_2h.py:76` declares `"adx_min": None` as the class
   default, and the gate at line 297 runs only `if adx_min_p is not None or
   adx_max_p is not None`. So these 12 legs have **no ADX filter at entry
   today**; declaring one **starts refusing setups that are currently
   admitted**. My "changes no geometry" was true of SL/TP and false of what
   matters — admission. `thesis_unknown` on these legs is therefore **correct
   reporting, not a defect**: a leg with no declared entry regime condition
   genuinely has no thesis to re-evaluate.

   **(c) There is no value to declare, and inventing one contradicts the
   directive.** No class default exists to fall back on. The only values in the
   family are **25 / 28 / 30, all on 2h CRYPTO legs**; these 12 are 1d/1h
   equity, bond and metal. Porting a number across instrument class and
   timeframe is chosen-not-measured — and it is literally *"reaching for a
   refusal"*, which the operator's own standing directive forbids.

   **What the item should be instead:** these legs have no declared entry regime
   condition at all. Giving them one is a real strategy change needing its own
   evidence. **The prerequisite is measurable and is the actual next step:
   compute the ADX-at-entry distribution for each of the 12 over history, so a
   floor is DERIVED from what it would refuse rather than chosen.** A floor that
   refuses ~0% is nearly inert and cheap; one that refuses 40% is a different
   strategy. Nobody knows which today.

   **Blast radius, measured:** all 12 route to `alpaca_live` (**real_money but
   `mode: dry_run`**, so nothing executes today), plus paper accounts, plus
   `ib_paper` for the mgc/mhg pair. Zero live real-money exposure right now —
   but `alpaca_live` is the latent account root `CLAUDE.md` flags as *"16
   strategies all `execution: live`; flipping that one account takes 16 legs
   live at once."* Whatever is declared now is what goes live that day, which is
   an argument for deriving the value rather than shipping one quickly.
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

## 7.5 MEASURED: what an `adx_min` floor would actually refuse

Ran 2026-08-23 to settle § 7 item 1 with evidence instead of a chosen number.
Tool: `scripts/research/adx_entry_distribution.py` (10/10 selftest). Candles from
the bot's own `/api/bot/candles` (the connector the strategies trade on), entries
from `scripts/backtest_pullback.py` run with `--adx-min 0` — which **computes ADX
and rejects nothing**, so the trade set is the unfiltered one the fleet runs
today. ADX comes from the harness's own `_adx`, **imported not re-derived**; it
is the verbatim copy of the live strategy's, which is what stops the live
predicate and the harness disagreeing about the number. Measured at the **entry
bars**, not over all bars — a different distribution that would not answer this.

**Coverage, stated because it is partial: 6 of 12 legs measured, 4
`insufficient_n`, 2 `no_data`.**

| leg | state | n | ADX min | p50 | max |
|---|---|---|---|---|---|
| `gdx_pullback_1d` | measured | 24 | 11.08 | 19.49 | 42.30 |
| `slv_pullback_1d` | measured | 22 | 15.34 | 19.10 | 55.56 |
| `iaum_pullback_1d` | measured | 21 | 10.14 | 23.02 | 54.03 |
| `tlt_pullback_1d` | measured | 20 | 15.92 | 19.48 | 38.95 |
| `gld_pullback_1d` | measured | 19 | 10.29 | 22.85 | 52.40 |
| `ief_pullback_1d` | measured | 17 | 12.01 | 21.24 | 43.47 |
| `tlt_pullback_1h` | `insufficient_n` | 8 | — | — | — |
| `gld_pullback_1h` | `insufficient_n` | 3 | — | — | — |
| `qqq_pullback_1h` / `spy_pullback_1h` | `insufficient_n` | 2 each | — | — | — |
| `mgc_pullback_1d` / `mhg_pullback_1d` | **`no_data`** | — | — | — | — |

⚠️ **`no_data` is not `no_entries`.** MGC/MHG are IBKR futures and the candles
route returns `no_data` for them — *we could not look*, the opposite claim from
*we looked and found none*. The 1h legs are thin because the venue serves only
~1 month of hourly bars (221–257), not because those legs rarely trade.
⚠️ **n is 17–24 per leg, 123 entries total across six legs.** Small. The
direction below is large enough to survive it; no single leg's rate is.

### The result: every value the family declares would delete most of the strategy

| floor | GDX | GLD | IAUM | IEF | SLV | TLT |
|---|---|---|---|---|---|---|
| 10 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| 15 | 20.8% | 10.5% | 23.8% | 17.6% | 0.0% | 0.0% |
| 18 | 41.7% | 26.3% | 38.1% | 23.5% | 31.8% | 25.0% |
| 20 | 54.2% | 36.8% | 42.9% | 47.1% | 50.0% | 50.0% |
| **25** *(eth/xrp)* | **75.0%** | **52.6%** | **57.1%** | **64.7%** | **72.7%** | **70.0%** |
| **28** *(ada)* | **75.0%** | **57.9%** | **81.0%** | **76.5%** | **81.8%** | **70.0%** |
| **30** *(sol)* | **79.2%** | **73.7%** | **81.0%** | **82.3%** | **86.4%** | **80.0%** |

**Porting the family's own crypto values would have refused a mean of 65% (at
25), 74% (at 28) or 80% (at 30) of every historical entry on these legs** —
range 53–86%. That is not unblocking `thesis_unknown`; it is deleting two-thirds
to four-fifths of the strategy. The instinct to reuse 25/28/30 was exactly the
cross-instrument port this measurement existed to test, and it fails.

### And there is no inert value either

The only floor refusing 0% across all six is **10** — but the lowest ADX-at-entry
observed anywhere is **10.14**, so a floor at 10 sits 0.14 below the sample
minimum. It is **fitted to the sample**: inert on the observed history by
construction, and refusing a future entry the moment one prints below it. It
would also test almost nothing at exit, since ADX on these instruments is rarely
near 10. A declaration that is inert by fitting is not evidence of safety.

### So the answer is: DO NOT declare `adx_min` on these legs

There is no value that is both meaningful and non-destructive, and that is not a
tuning failure — it is the measurement telling us the predicate is wrong.

**These legs' entry thesis is not ADX.** Their entry condition is the
trend-and-pullback STRUCTURE (`trend_lookback`, `pullback_lookback`,
`pullback_frac`); the ADX band is an *optional additional* regime filter that
only the four 2h crypto legs ever adopted. `_pullback_thesis_intact` tests the
ADX floor because that is what the crypto legs declare — so on a leg that never
declared one it correctly returns `thesis_unknown`.

**The real remedy is a trend-structure thesis predicate** — "is the trend that
defined this entry still intact?" — for legs with no declared ADX band. That is a
change to `_pullback_thesis_intact` (code, with its own design and falsifier),
**not** a value in `config/strategies.yaml`. It is also the § 4-faithful answer:
the thesis is the family's OWN entry condition re-evaluated, and for these legs
that condition was never ADX.

---

## 8. Filed

| id | sev | note |
|---|---|---|
| `BL-20260823-NO-TARGET-KEY-STATE-UNREACHABLE-IN-PRODUCTION` | medium | The 5th state can never fire; sentinel provenance (chosen vs inherited) is collapsed for 10 live legs |
| `BL-20260823-XRP-DECLARED-TARGET-EXCEEDS-VENUE-CAP` | high | `tp_r 3.0` vs `cap_r 0.687`, real money; sentinel→**clamped**, not →declared |
| `BL-20260823-TARGET-EXPECTATION-DOCSTRING-COUNTS-STALE` | low | 29/26 vs measured 28/24 |
**Filed in PR #10198** (the sibling branch carrying the sweep corpus + tooling;
cited by PR because the ids do not resolve on *this* branch until it lands):

| row | sev | note |
|---|---|---|
| venue clamp is the undeclared target | high | 5 of 10 sentinel legs run a venue-chosen ~1.5R target that out-exits the trail 1.7–3.9× (§ 6.5) |
| declared trail-decay arms are inert | medium | 2 of 4 measured `trail_decay_arm_r` values can never fire; one is on a top performer |
| e35 Path B unreachable (raw run_cell dict) | high | The bracket sweep's Path B answered False for every cell ever gated — wrong argument shape |
| xrp_pullback geometry tuning exhausted | medium | 37 geometry + 20 trail cells, zero positive, live config optimal on both axes |
| e35 sweep evidence has no durable path | medium | 3,781 measured cells were artifact-only; 2,189 now committed |
| lever-reachability audit blind without journal rows | low | Grades all 8 levers `unmeasured`; the backtest already carries the answer |

**Evidence contamination caveat — and my first version of it was too narrow.**
None of the above touches IB live-parity, and that caveat now matters *more* than
when I wrote it. I originally described the `place_protective()` parentless-OCA
defect as dropping take-profits on "any position whose protection was re-armed
(naked-autoprotect sweep, reconciler adopt/re-attach)". That is the narrow framing
`BL-20260823-IB-TRAILING-A-STOP-SILENTLY-DROPPED-THE-TARGET` (severity
**critical**, merged in #10174 while this work was in flight) explicitly corrects:
`IBClient.modify_protective` — the **routine SL/TP adjust, i.e. the monitor's
stop-trail wire** — is itself implemented as a re-arm *through* `place_protective`.

So the affected population is not "positions that happened to be re-armed"; it is
**every IB position whose stop ever trailed**, which on a trailing strategy is the
normal lifecycle of any trade that was *working*. The corollary that row draws is
the one to carry: **no IB winner could ever reach its target.** `IBClient.place()`
— the ENTRY bracket — was fine throughout (children linked by `parentId`).

**The size of that population is still unmeasured** (that row says so itself), so
nothing here is calibrated against it. This document is unaffected on its own
terms: `cap_r` is config-and-ATR geometry, the e35 sweep is a backtest over
historical candles, and **no claim above reads an IB exit outcome**. But two
things follow. (1) Any future calibration of these targets against *live IB*
fills must measure that population first. (2) The fleet has been unable to reach
its targets for **two independent reasons at once** — the venue clamp on Bybit
(§ 4–5) and an un-transmitted take-profit on IB — which are separate defects with
separate remedies and should not be conflated when reading exit statistics.
