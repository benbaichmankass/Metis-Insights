# "Declare the 9" — the approach is right, and the values cannot be chosen yet

**Date:** 2026-08-24 · **Tier:** research only, nothing applied · **Scope:** the
`all_undeclared` `trend_donchian` subset from
`BL-20260818-MOST-OPEN-TRADES-HAVE-NO-DECISION-DRIVEN-EXIT`.

Operator disposition, 2026-08-23: **DECLARE THE 9 FIRST** — chosen because the
`trend_donchian` module "already implements ALL FOUR M20 mechanisms and declares
ZERO of them, so this is a config declare against proven, instrumented code."
The disposition explicitly approved **the approach, not a set of numbers**, and
required *"MEASURE THE DROP, DO NOT ASSERT IT."*

This measures first. **The approach holds. The numbers cannot yet be
responsibly chosen, and declaring them anyway would produce the cosmetic-declare
defect — the arm-side twin of the cosmetic *target* measured in #10215.**

## 0. The headline

1. **Only 3 of the disposition's 9 trades are still open** (4711, 4350, 4347).
   Six closed between 2026-08-23 and 2026-08-24. The unit of action was always
   the **leg**, not the trade, so this does not void the disposition — but any
   plan written against those 9 trade ids is two-thirds stale.
2. **9 enabled `trend_donchian`-module legs declare none of the four mechanisms.**
   That 9 is a *coincidence of number* with the 9 trades and must not be
   conflated with it.
3. **A declared arm only helps if it is REACHABLE for that fill**, and the fleet's
   own record says declaring blind mostly produces unreachable arms:
   **`no_arm_declared` 35 · `unreachable` 19 · `reachable` 6** over 60 telemetry
   rows. **Six.**
4. **The available per-leg sample is n = 1–4, and 2 of the 9 legs have zero
   rows.** `trend_donchian_eth_4h`'s `cap_r` spans **1.396 → 8.632 (6.2×)** over
   n=4. No single arm value can be characterised against that.

## 1. Why a blind declare does not move the number

`scripts/ops/exit_path_coverage.py::assess_trade` grades each mechanism, and the
verdict precedence is:

```
if LIVE    in decision_paths -> decision_exit_live
elif UNKNOWN in decision_paths -> unknown
elif LIVE  in price_paths     -> price_only
```

A declared mechanism reaches `LIVE` **only** when `arm_reach == "reachable"` for
that trade. The other two landings are:

| condition | state | trade verdict |
|---|---|---|
| `arm_reach == "unreachable"` or leg verdict `inert` | **ABSENT** | stays **`price_only`**, cause flips to `declared_but_unreachable` |
| no per-trade cap to check against | **UNKNOWN** | becomes **`unknown`** |
| `arm_reach == "reachable"` | LIVE | **`decision_exit_live`** ✅ |

So a blind declare has three outcomes and **two of them are not progress** —
one leaves the finding exactly where it was under a different label, and the
other converts a known finding into an unknown, which is worse for a reader.

**This is the same defect as the cosmetic target.** #10215 measured that a
declared `tp_r` above a leg's `cap_r` is byte-identically the same run as
declaring nothing. A declared `trail_decay_arm_r` above `cap_r` is the identical
error on the other side of the bracket: it reads as a decision in the YAML and
is inert in the book. `cap_r` governs both.

## 2. The population, stated

Enabled legs resolving to the `trend_donchian` module (via
`exit_mechanism_coverage.unit_of`, registration-table basis) that declare **none**
of `trail_decay` / `stale_stop` / `giveback_stop` / `exit_head`:

`mes_trend_long_1d` · `mgc_trend_1h` (shadow) · `qld_trend_long_1d` ·
`slv_trend_1h` · `spy_trend_long_1d` · `tqqq_trend_long_1d` ·
`trend_donchian_ada_4h` · `trend_donchian_eth_4h` · `trend_donchian_sol_prop`

All nine run `atr_stop_mult: 2.5`, so `cap_r = 0.099 / (2.5 × ATR/entry)` —
**a function of each fill's own ATR**, which is why it varies within a leg.

## 3. The sample is too thin to value an arm

`cap_r` per leg, from `/api/diag/position_telemetry` (60 rows, live, 2026-08-24):

| leg | n | min | median | max | spread |
|---|--:|--:|--:|--:|--:|
| `trend_donchian_eth_4h` | 4 | 1.396 | 2.376 | 8.632 | **6.2×** |
| `trend_donchian_ada_4h` | 4 | 1.062 | 1.674 | 2.154 | 2.0× |
| `slv_trend_1h` | 3 | 4.965 | 5.052 | 5.936 | 1.2× |
| `mgc_trend_1h` | 2 | 8.463 | 8.495 | 8.526 | 1.0× |
| `spy_trend_long_1d` | 1 | 3.447 | 3.447 | 3.447 | — |
| `mes_trend_long_1d` | 1 | 2.993 | 2.993 | 2.993 | — |
| `qld_trend_long_1d` | 1 | 1.312 | 1.312 | 1.312 | — |
| `tqqq_trend_long_1d` | **0** | — | — | — | *we did not look* |
| `trend_donchian_sol_prop` | **0** | — | — | — | *we did not look* |

⚠️ **n=1 is not a distribution.** Proposing `mes_trend_long_1d`'s arm from one
observation is the argmax-of-one-draw error, and two legs have no observation at
all — which is *unmeasured*, not *safe*.

**The corroboration is the part that settles it.** This is not a hypothetical
risk: fleet-wide, **19 of the 25 declared arms are `unreachable`** and only 6
are `reachable`. Two of those unreachable declares are on this very module —
`qqq_trend_long_1d` arm **3.56** against a measured cap **2.13**, and
`trend_donchian_sol_4h` arm **5.57** against cap **1.44**. Both were declared by
someone reasoning exactly as a blind declare would.

## 4. The working example, and what it shows

**`scha_trend_long_1d` is the one `trend_donchian`-module leg with a reachable
declared arm**: `trail_decay_arm_r: 2.0` against a measured `cap_r` of **2.778**
→ `arm_reach: reachable`, and its open trade 4710 grades `decision_exit_live`.

That is the template, and it is a template about **arithmetic, not taste**: the
arm sits comfortably under the leg's cap. Every reachable declare on this module
has that property; every unreachable one violates it.

## 5. What I am proposing, and what I am not

**NOT proposing any value.** Nine numbers chosen against n≤4 would be nine
Tier-3 values with no distribution behind them, and on the fleet's own record
most would land unreachable — producing a config that *reads* as nine new
decision exits and delivers, at best, a few.

**Proposing the measurement that makes the declare valuable** — and it needs no
new data source, unlike the M20 candle-feed blocker:

1. **Derive `cap_r` per HISTORICAL fill from the journal**, not from live
   telemetry. `cap_r = 0.099 × entry / (atr_stop_mult × ATR)`; entry is on the
   trade row and ATR is reconstructible on the leg's own bars. Telemetry only
   holds rows whose monitor wrote one (60 total, live-biased); the journal holds
   every fill the leg has ever taken.
2. **Propose an arm at a stated percentile of that distribution**, and quote the
   **reachable fraction** it implies — the number that says how often the declare
   will actually fire rather than sit inert.
3. **Then declare, then re-run `exit_path_coverage.py`** and quote `by_verdict`
   + the cause split, per the disposition's own "measure the drop" requirement.

⚠️ **The criterion goes first**, consistent with the three decisions recorded
2026-08-24: what reachable fraction makes a declare worth shipping is written
down **before** step 1 runs, not chosen from its output.

## 6. Noted, not filed — already open

12 of 60 telemetry rows carry `peak_r = -1e+18` and a `peak_pct_of_cap` derived
**from that sentinel** (e.g. `mgc_trend_1h` → `-1.182e+19`), across 5 legs. This
is already filed twice —
`BL-20260818-TELEMETRY-PEAK-R-STORES-COALESCE-SENTINEL` and
`BL-20260820-TELEMETRY-THIN-WINDOW-SENTINEL-LEAKS-INTO-PEAK-PCT-OF-CAP` — so
the fresh measurement is added to the existing row rather than filed as a
third. `exit_path_coverage.py::_sane` already refuses these values; the
**diag surface does not**, which is why they are visible above.
