# Which pre-live harnesses RUN the live entry decision, which REIMPLEMENT it, and which only CLAIM to

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> MI-320 · `2026-09-18` · successor to MI-319 ([`fvg-parity-2026-09-18.md`](fvg-parity-2026-09-18.md), #12556)
>
> **Measurement only.** `config/` and `src/` are read and never written. Every
> finding here is FILED; nothing is proposed as a parameter change, and no
> sweep was commissioned.

## 0 — The answer

**Two of thirteen backtest harnesses actually run the live entry decision.**
The rest either reimplement it or have nothing to reimplement. Joined to the
leg roster, that puts the fleet's 45 `execution: live` legs into three groups,
and the group sizes are the finding:

| how the pre-live evidence was produced | live legs | share |
|---|---:|---:|
| the harness **ran the live `order_package`** | **8** | 17.8% |
| a reimplementation **claiming** to be a port, **never adjudicated** | **20** | 44.4% |
| a reimplementation **claiming nothing at all** | **17** | 37.8% |

**The one pair that has ever been adjudicated gates ZERO live legs.**
MI-319 measured `fvg_range_15m` against `scripts/backtest_fvg_range.py` — that
leg is `execution: shadow`.

⚠️ **This is not a claim that any leg's evidence is wrong.** *Unadjudicated* is
a third state, and the whole point of keeping it apart from *divergent* is that
nobody has looked. What the numbers say is where looking would be worth most.

## 1 — Populations

Every figure below is over one of these three, stated at the point of use.

| population | n | how obtained |
|---|---:|---|
| backtest harnesses | **13** | `scripts/backtest_*.py` |
| live strategy units | **16** | `src/units/strategies/*.py`, excluding `_`-prefixed |
| strategy legs | **55**, of which **45** `execution: live`, **0 unresolved** | `config/strategies.yaml` resolved through `pipeline.monitor_unit_for` — the repo's own resolver, so this cannot drift from what the order-monitor believes |

## 2 — Part A: provenance

`classify_provenance` reads the **AST**, never a grep, and takes three
independent signals.

| state | n | harnesses |
|---|---:|---|
| `imports_live_unit` | **2** | `backtest_ict_scalp`, `backtest_system` |
| `reimplements` (a unit CLAIMS to port it) | **3** | `backtest_trend`, `backtest_fade`, `backtest_fvg_range` |
| `corresponds_but_unclaimed` | **3** | `backtest_pullback`, `backtest_squeeze`, `backtest_pairs` |
| `no_corresponding_unit` | **5** | `backtest_chop_scalp`, `backtest_funding_carry`, `backtest_orb`, `backtest_vol_target`, `backtest_xsec_momentum` |

**Why the AST and not a grep.** `backtest_ict_scalp.py` reaches the live
decision with a plain `from src.units.strategies.ict_scalp import
order_package` (line 60), called at line 485. `backtest_system.py` reaches it
through `importlib.import_module` + `getattr(…, "order_package")` (line 329),
bound to a local name. **A grep for the static import form reports
`backtest_system` as a reimplementation** — the exact inversion this part
exists to prevent, and the reason the classifier is asserted against it in a
selftest.

The third signal is a refusal: a harness that defines its own `def
order_package` is a reimplementation **wearing the name**, and crediting the
call site would invert the verdict. No harness does this today; the negative
control asserts the classifier would catch one.

⚠️ **`corresponds_but_unclaimed` is a separate state on purpose.** Folding it
into `no_corresponding_unit` would say *there is nothing to compare*, which is
false and reassuring in the dangerous direction: `backtest_pullback.py` is
referenced by `htf_pullback_trend_2h`, **the second-largest unit in the fleet
(19 legs, 16 `execution: live`)**. What is absent is the **claim**, not the
correspondence.

## 3 — Part B: the claim, and the direction it runs in

⚠️ **The load-bearing port claim lives in the LIVE UNIT and runs the opposite
way from what a reader expects: the unit claims to port the harness.** The
"Live-parity …" comments *inside the harnesses* are a different and much
narrower claim — a confidence formula, one gate — and are never pooled with it.

The formulaic sentence is stable across units:

> *"This adapter ports the validated entry/exit logic from
> ``scripts/backtest_X.py`` into the live ``order_package(cfg, candles_df) ->
> dict`` … contract"*

Over the 16 units, **exactly 3 carry it**:

| unit | scope | harness | says VERBATIM | legs | live |
|---|---|---|---:|---:|---:|
| **`trend_donchian`** | entry/exit | `backtest_trend.py` | no | **23** | **20** |
| `fade_breakout_4h` | entry/exit | `backtest_fade.py` | no | 1 | 0 |
| `fvg_range_15m` | entry | `backtest_fvg_range.py` | **yes** | 1 | 0 |

**The extractor ships with a positive control and needed it.** An earlier draft
required at least one backtick around the path — `fvg_range_15m` writes it bare
— so it found `trend_donchian` and `fade_breakout_4h`, returned a confident
2-of-16, and **missed the one pair already adjudicated**. A claim census that
silently finds nothing is indistinguishable from a repo that makes no claims;
the control (`fvg_range_15m` must be found) is asserted in the selftest rather
than eyeballed.

## 4 — Part D: pointing MI-319's probe at the other claimed pairs

The probe is **imported**, not reimplemented — a second copy of *what counts as
a rejection predicate* is how the two would drift, which is the defect class
this whole unit is about.

| pair | MI-319 state | gate ratio | shared disjuncts | unclassified residuals |
|---|---|---:|---:|---:|
| `fvg_range_15m` ← `backtest_fvg_range` *(control)* | `equivalent_modulo_order` | 1.00 | 18 | **0** |
| `fade_breakout_4h` ← `backtest_fade` | `divergent` | 0.625 | 5 | 5 |
| `trend_donchian` ← `backtest_trend` | **`idiom_mismatch`** | 0.4375 | 5 | 11 |

### 4.1 `trend_donchian` is UNADJUDICABLE by this probe, and that is the honest state

`idiom_mismatch` is a refusal, not a divergence: 16 harness gates against 7
live ones is not a comparison. **Do not read it as evidence of either parity or
divergence.** It exists because pointing MI-319's probe at `ict_scalp` returned
a confident `divergent` for a pair it could not see, at ratio 0.27.

What the refusal is made of, once the residuals are classified:

- **2 `implemented_upstream`** — the side filter. The harness writes it as a
  local `_sf`; live it is `src/runtime/strategy_signal_builders::_resolve_side_filter`
  + `src/runtime/account_side_filter.py`, and **`side_filter` appears in ZERO
  unit files**. A unit-scoped comparison therefore reports a gate the live
  system does run, one layer up. *(The alias half is load-bearing and was found
  by running this: `_sf` exists nowhere in `src/`, so searching for the alias
  returns `not_found` and the gate reads as a divergence.)*
- **2 `harness_only_lever`** — `--adx-min` / `--adx-max`, argparse default
  `None`, and **0 of the 23 `trend_donchian` legs declare either**, while the
  live unit implements no ADX band at all (`adx_min`/`adx_max` appear **0
  times** in it). Harness gate inert, live gate absent: **consistent**.
- **11 unclassified**, of which 5 carry a name-overlap hint pointing at a
  plainly-corresponding predicate on the other side that differs in *shape*
  rather than in *identifier* (`pd.Timestamp(df['timestamp'].iloc[i]).hour in
  skip_hour_set` against `trigger_hour is not None and trigger_hour in
  skip_hour_set`; `float(vp) > vol_skip_above_pctl` against `vol_pctl >
  vol_above`).

⚠️ **A hint is not a classification, and the difference matters.** Promoting a
name overlap to `renamed_counterpart` would launder exactly the differences
this instrument exists to surface — the second predicate in that pair adds a
`None` guard the first does not have. What settles them is a behavioural probe,
which this instrument does not run.

The genuinely unhinted residuals are `not confirmed` (the `--confirm-bars`
lever, which 2 of 23 legs declare and the unit does reference 14 times) and the
`--direction-filter` gates (argparse default `off`, declared by 0 of 23 legs,
and `down_regime` appears **0 times anywhere in `src/`**).

### 4.2 `fade_breakout_4h`: four of the five residuals dissolve on reading, and the fifth is the target contract

The instrument leaves 5 unclassified. **Four of them I then adjudicated by
reading the source, and that read is reported as a read — not as something the
instrument established.**

- `float(adx_i) >= adx_max` / `pd.isna(adx_i)` against live `adx_val >=
  adx_max`: `fade_breakout_4h.py:237-238` does
  `adx_val = float(adx_raw) if pd.notna(adx_raw) else None`, then
  `if adx_val is None or adx_val >= adx_max: raise`. **NaN → None → rejected**,
  exactly as the harness's `pd.isna` skip. The live unit does the NaN
  conversion one line earlier, so the probe sees a shape difference where the
  behaviour is the same. *(This is the leg's own declared lever — `adx_max` is
  declared by 1 of its 1 legs — so it is live on both sides, not inert.)*
- **`direction == 'long' and target <= price` / `direction == 'short' and
  target >= price` — the degenerate-target guard — is genuinely harness-only.**
  Verified with a positive control: `fvg_range_15m.py:387` has one;
  `fade_breakout_4h.py` and `trend_donchian.py` have **none**.

**And it is not a bug — it is the TARGET CONTRACT differing, which is MI-319's
finding recurring.** `fade_breakout_4h.py:265-269` sets
`tp = max(entry*(1−cap), entry − tp_r*risk)` / the long mirror: a **far
sentinel**, structurally never through the price, so the guard is unreachable
live. The harness targets the opposite Donchian boundary, which can be. **Two
of two adjudicated pairs now show the entry porting and the TARGET not**, which
is the same class MI-312 filed as `base_args`.

## 5 — A stale declaration found on the way, and it is the dangerous direction

`src/units/strategies/htf_pullback_trend_2h.py`'s module docstring opens:

> *"HTF trend-pullback continuation — units-layer adapter (**SCAFFOLD, not
> wired**)."* … *"**Not yet wired** into `strategy_signal_builders.py`,
> `intents.py`, or `config/strategies.yaml` (registration is explicit; no
> auto-discovery) — **inert until the Tier-3 activation PR**."*

**Measured, with a control:** that unit resolves **19 legs, 16 of them
`execution: live`** — the second-largest in the fleet — and the three files it
names contain **75 / 2 / 43** references to it, against **0 / 0 / 0** for three
genuinely-unwired units (`hf_vwap_revert`, `hf_displacement_cont`,
`smoke_test`). Nothing later in the 60-line docstring corrects it.

A session reading that header concludes the unit is inert. Filed, not fixed:
`src/**` is outside this lane's Tier-1 surface.

## 6 — What this does NOT establish

- **It is not a behavioural comparison.** Part D compares *rejection predicates*
  structurally. A classified residual says *we know what this difference is*,
  never *the two would place the same trade*.
- **It does not clear any shipped parameter**, and it re-runs no sweep.
- **`corresponds_but_unclaimed` is not an accusation.** `htf_pullback_trend_2h`
  and `squeeze_breakout_4h` may well be faithful; nobody has said so in the
  source and nobody has measured it.
- **`ict_scalp`'s entry is exact by construction, and that moves the question
  rather than answering it.** The harness calls the live `order_package`, so
  there is no port to diverge — but the **arguments** it builds (`per_bar_cfg`)
  and the **window** it passes are a seam, and those are precisely the two
  classes MI-312 (`base_args`) and MI-319 (the declared candle minimum) already
  found. Unmeasured here.
- One symbol, one repo state, read at `fbeeeec53`.

## 7 — Filed, not fixed

- `htf_pullback_trend_2h`'s docstring declares itself unwired while gating 16
  live legs.
- `trend_donchian` — 20 live legs, the largest unit in the fleet — carries the
  same formulaic port claim MI-319 adjudicated on `fvg_range_15m`, and no probe
  in the repo can currently read its idiom.
- A harness-only research lever (`--direction-filter`) has no live
  implementation at all; inert today because no leg declares it and its default
  is `off`, i.e. the evidence a leg would produce with it ON describes a
  strategy that does not exist live.

## 8 — Reproduce

```bash
python3 scripts/research/mi320_harness_provenance.py --selftest   # 41 checks
python3 scripts/research/mi320_harness_provenance.py --run
```

Artifact: `docs/research/mi320-harness-provenance-2026-09-18.json`.
Carrier: `docs/claude/work/objects/WO-20260918-WHICH-HARNESSES-RUN-THE-LIVE-ENTRY-DECISION.yaml`.
