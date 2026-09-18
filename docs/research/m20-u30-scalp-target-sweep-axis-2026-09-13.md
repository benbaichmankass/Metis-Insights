# M20 U30 — the ict_scalp target now has a sweep axis, and the matrix ref that hid the gap is corrected

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Row worked:** `BL-20260912-THE-SCALP-FAMILY-S-TARGET-HAS-NO-SWEEP-E35-EXCLUDES-IT-BY-DESIGN-AND-THE-FLEET-SWEEP-DOES-NOT-SWEEP-A-TARGET`
**Object:** `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · MI-278 U30

---

## 1. What this is for

MI-278 U2 attributed every winner since 2026-08-27 and found **no lever cutting them
short** — the lever family is 3 of 49. The only mechanism with attributed mass is the
**take-profit**: 18 of 49 winners ended exactly at their declared target, and U2b measured
ict_scalp winners landing just *under* `tp_at_r: 1.5` (median R **1.216** pre / **1.353**
post). U23 reached the same place from the other direction and could propose no parameter
change, because the walk-forward corpus is confounded.

So the counterfactual the evidence points at is: **would a further target have held winners
longer?** Until today no harness could run it.

**This unit builds the instrument. It changes no parameter and proposes none.**

## 2. The gap was three deep, and only the first layer was known

| # | layer | state before |
|---|---|---|
| 1 | `e35_bracket_geometry_sweep.py` sweeps `tp_r` | **excludes the scalp family by design** — its grid walks down from the `50.0` sentinel, which means something different against a real bracket. **Correct, and not the bug.** |
| 2 | `m20_fleet_exit_sweep.py` covers scalp | its lever columns are all *post-entry* overrides; it **passed no target at all** |
| 3 | `scripts/backtest_ict_scalp.py` | **had no `--tp-at-r` flag** — measured by listing every `add_argument`; `tp_at_r` appeared only in comments |

And beneath those, the reason layer 3 was never noticed: the target reaches the harness from
`_load_yaml_params()`, which loads the **`ict_scalp_5m` block, hardcoded, for every scalp
leg**. That premise holds only because all 8 legs are config-exact copies — and it is
precisely what makes a per-leg target sweep structurally impossible, because varying the
target per leg is the thing that breaks it.

**So the only way to move the scalp target was to edit `config/strategies.yaml` — a Tier-3
live file no research run may touch.** That, not anyone's oversight, is why this was never
swept.

## 3. Two things checked before building, each with a positive control

**(a) `ict_scalp` applies no venue TP clamp.** `src/runtime/tp_venue_cap.py::CLAMPING_FAMILIES`
is `{donchian, pullback, fade, squeeze}`, and `_TP_SENTINEL_CAP_PCT` appears nowhere in
`ict_scalp.py` — against the control `trend_donchian.py:393`, which does
`tp = min(entry * (1 + cap), entry + tp_r * risk)`.

⚠️ **That makes a wider scalp target riskier than a wider donchian one, not safer.** Where the
clamping families turn a too-far target into a nearer one, ict_scalp **sends it** and the venue
refuses the order. A cell whose targets sit past the cap measures a book of orders that could
not have been placed. A placeability census therefore ships with the axis (§5).

**(b) The override lever is `cfg["tp_at_r"]`, consumed by the live unit.**
`ict_scalp._resolve_params` is `{key: cfg.get(key, default) …}` and `order_package` does
`tp = entry ± tp_at_r * risk`. Injecting into `cfg_overrides` means **the live unit computes
the target** — the harness never re-derives one, so a swept cell measures geometry production
would really place.

## 4. What was built

**`scripts/backtest_ict_scalp.py`** — `--tp-at-r`, injected into `cfg`. Default `None`, so every
caller written before it is byte-identical. It applies **even under `--ignore-yaml`**: an
explicit request is not a YAML read, and stacking them must not silently drop the request.

**`scripts/research/m20_fleet_exit_sweep.py`** — a pre-registered grid, emitted into the matrix's
`bracket_geometry` column, plus the scalp base passing each leg its **own** declared target.

```
SCALP_TP_AT_R_GRID = (1.0, 1.25, 2.0, 2.5, 3.0, 4.0, 6.0)
```

It is e35's grid with two deliberate differences. **`1.5` is dropped** — it is what all 8 live
legs declare, so the cell would *be* the base. **`1.25` is added** — the measured winner mass
sits at 1.216/1.353R, i.e. just *below* the live target, and **a grid that only widens cannot
refute "the target is too near".** Keeping the rest of e35's points is what makes a
cross-family read possible.

⚠️ **`4.0` and `6.0` are expected to time out on a 5m bracket and are kept anyway.** A grid
trimmed to the values thought likely to win cannot produce an honest negative, and the row's
own criteria say an honest negative **closes** it.

⚠️ **The column is `bracket_geometry`, not `tp_geometry`.** That string is already taken and
means something else entirely — whether a cell was measured at live TP parity
(`live_parity_capped` / `no_take_profit` / …). The matrix's own legend calls `bracket_geometry`
*"a new DIMENSION, not a ninth lever"*, so a target cell is a slice of that column, never a
ninth lever beside the eight post-entry ones. A test pins the two apart.

## 5. Three things the summary now states that it could not before

| field | states | why |
|---|---|---|
| `tp_at_r_source` | `cli_flag` · `config` · `unit_default` · **`unknown`** | "1.5 because the leg declares it" and "1.5 because nobody said otherwise" are different facts and only the first is evidence about the leg. `unknown` reports `None`, never the default — substituting it would state a geometry nobody chose. The default is **read from the unit**, not written as `1.5`. |
| `placeability_state` | `all_within_venue_cap` · `some_beyond_venue_cap` · **`not_measured`** | see §3(a). `not_measured` means no trades, so **no distance exists** — that is not the clean negative. ⚠️ It is a **flag, not a proof of rejection**: that the other units clamp at `TP_VENUE_CAP_PCT` is measured; that a venue refuses at exactly that distance is this repo's standing claim and is not re-verified here. |
| `target_binding` | `binds` · **`never_reached`** · `not_measured` | past some width no trade reaches the target and every wider cell returns the *same* book — see §6. Three identical rows in a verdict table read as three data points; they are **one** observation. |

## 6. It runs, and the axis moves the book

Every emitted cell, run end to end through the real harness on the repo's 5,000-bar BTC
sample (`data/backtest_candles.csv`, 2022-07-23 → 2022-07-27, **n = 4 trades**):

| cell | `tp_at_r` | trades | `tp_hit` | timeout | `sl_hit` | gross_r | binding |
|---|---|---|---|---|---|---|---|
| base | 1.5 | 4 | 1 | 2 | 1 | 1.0057 | `binds` |
| `tp1R` | 1.0 | 4 | **2** | 1 | 1 | 1.0795 | `binds` |
| `tp1.25R` | 1.25 | 4 | 1 | 2 | 1 | 0.7557 | `binds` |
| `tp2R` | 2.0 | 4 | 1 | 2 | 1 | 1.5057 | `binds` |
| `tp2.5R` | 2.5 | 4 | 1 | 2 | 1 | 2.0057 | `binds` |
| `tp3R` | 3.0 | 4 | 0 | 3 | 1 | 1.2672 | **`never_reached`** |
| `tp4R` | 4.0 | 4 | 0 | 3 | 1 | 1.2672 | **`never_reached`** |
| `tp6R` | 6.0 | 4 | 0 | 3 | 1 | 1.2672 | **`never_reached`** |

⚠️ **STATE THE POPULATION: this is a 4-trade smoke sample over four days of 1m BTC bars, and it
is EVIDENCE THAT THE INSTRUMENT WORKS AND NOTHING ELSE.** It is not a result about the target,
it does not run on a scalp leg's real 5m feed, and none of these numbers went through the
IS/OOS split or the walk-forward. Do not quote a single figure from this table as a finding
about `tp_at_r`.

What it *does* establish: the entry set is constant at 4 across every cell (the target does not
gate entries, as it must not), `tp_hit` falls monotonically as the target widens, and beyond
3.0R the target stops binding — which is exactly the degeneracy `target_binding` was added to
make un-mistakable.

## 7. A defect found on the way, and fixed

`base_args` now passes `--tp-at-r`, so a bare `base + extra` would emit the flag **twice**.
This file already states the rule, at its own `--min-confidence` override: *"the recorded
command IS the evidence for what a row measured, and a command carrying two contradictory
floors cannot be read back as a claim about either."* **That reasoning had been applied to one
flag and to nothing else.**

⚠️ **It was already happening, and not hypothetically.** `declared_levers()` emits
`--stale-exit-bars <declared>` into the base and the `stale8_lt0R` cell appends its own.
Measured against the real config: **`ict_scalp_eth_15m` declares `stale_exit_bars: 12`**, and
its argv has carried both all along.

`compose_cell_argv` strips any flag the cell sets from the base first. **It changes no number**
— verified empirically against the real parser (`--tp-at-r 1.5 --tp-at-r 3.0` → the summary
reports `3.0`), so argparse already took the cell's value and every run to date measured what
the cell asked for. What was wrong was the *record*.

## 8. The matrix ref that hid all of this, corrected

Eight `bracket_geometry` rows carried one identical ref:

> *"Not swept in the 2026-08-20 run (scope was the trend/pullback/squeeze fleet). Crypto — the
> free `data.binance.vision` lane DOES cover these, so this is genuinely pending, not blocked."*

**Wrong in the dangerous direction, and it cost a dispatched run.** It attributed the gap to the
*scope of one run* and asserted the data lane covers it, so a session reading `pending` would
schedule a sweep. One did — `e35-bracket-sweep.yml` run **34677990760** returned
`shard-plan: 0 job(s); 7 not scheduled (out_of_scope_family=7)`. **Data was never the blocker.**
The honest grade until today was `blocked:no_harness_lever`.

⚠️ **The eight rows did not share a cause, and I nearly wrote one correction for all of them.**
An assertion caught it: **7** crypto ict_scalp legs plus **`fvg_range_15m`**, whose cause is
different and **still open** — its harness *has* `--tp-r` (positive control: `--data` also True)
and the sweep already threads it, but no cell sweeps it and the leg **declares no target at
all**, so a grid there must first establish what the base *is*. Filed as
`BL-20260913-FVG-RANGE-15M-HAS-A-TARGET-FLAG-AND-NO-SWEEP-CELL-AND-DECLARES-NO-TARGET-TO-SWEEP-AROUND`.

⚠️ **And the axis reaches 7 of the 8 scalp legs, not 8.** `ict_scalp_mgc_15m` never carried that
ref: it is correctly `blocked:no_free_lane_candle_feed` — an IBKR COMEX future the free lane
cannot serve — and **U30 does not unblock it**. That 7 is the same 7 the e35 run reported.

## 9. Verification

* **29 controls** in `tests/test_scalp_target_sweep_axis.py`, and the harness is shown able to
  **fail**: seven defects planted, **all seven caught** — base stops passing the leg's target
  (2 tests) · compose becomes a plain concatenation (3) · the no-op value hardcoded to 1.5 (2) ·
  the lever renamed to the colliding `tp_geometry` (1) · an unparseable target silently becoming
  the unit default (4) · an empty book reading as the clean negative (2) · `never_reached`
  collapsed into `not_measured` (2).
* **Regression: 1,246 passed** across every `scalp` / `fleet_sweep` / `m20` / `bracket` / `e35` /
  `exit_` test file.
* **One real failure, from a guard doing its job:**
  `test_the_declared_producer_set_matches_what_cells_for_ACTUALLY_emits` caught that
  `COLUMNS_WITH_A_SWEEP_PRODUCER` no longer described reality. `bracket_geometry` is now
  declared there — **deliberately in both producer sets**, because e35 produces it for
  donchian/pullback/squeeze and the fleet sweep now produces it for scalp. Membership changes
  **no grading** (the consumer's `elif` was already satisfied by the own-driver entry); it makes
  the declaration true.
* **Three other failures are pre-existing and environmental, measured by stashing this diff and
  re-running on a clean tree:** `ModuleNotFoundError: fastapi` / `_cffi_backend` in the sandbox.
  Already covered by four open rows including
  `BL-20260912-A-BARE-PYTEST-RUN-ABORTS-AT-COLLECTION-HERE-SO-A-FAILED-GREP-RETURNS-AN-EMPTY-SET-THAT-READS-AS-A-CLEAN-DIFFERENTIAL`
  — no duplicate filed.

## 10. What this does NOT do

⚠️ **The row does not clear.** Its criteria need *"a `tp_at_r` cell for at least one ict_scalp leg
carrying an IS/OOS verdict WITH a yearly walk-forward at the exit-refinement gate"*. **A harness
existing is not a verdict**, and a smoke run on 4 trades is not a sweep. The 7 matrix cells move
from a *mis-graded* `pending` to a *true* `pending`, which is a smaller claim than it looks and
is the honest one.

⚠️ **No parameter change is proposed and none may be.** Raising `tp_at_r` on a live leg is
**Tier-3**; doing it on this evidence would be the cosmetic-cell anti-pattern the row itself
names.

⚠️ **A new refusal ships with this.** `--bank-at-r` at or above the *effective* target is now
refused at parse time rather than run — the rung coincides with the fixed TP and the ladder
lever is a provable no-op. Verified safe against the callers: `m27/ict_scalp_exit_sweep.py`
already refuses `rung >= tp_at_r`, and `m20_exit_sweep.py`'s bank cells target the trend and
pullback harnesses, not this one. It fires only when `--bank-frac > 0`, and the boundary **moves
with the target** — the same rung that is refused at 1.5R runs at 3.0R.

---

## Standing

`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED` is
`loud: true` and unchanged: **17 consecutive losing days, −$38,851.81**, onset **2026-08-27**,
bounded below by a +$4,172.64 winning day on 08-26. Nothing in U30 addresses it, and the
instrument built here cannot: it grades a bracket over historical candles and says nothing
about what changed on the fleet.
