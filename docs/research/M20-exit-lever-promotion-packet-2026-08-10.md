# M20 exit-lever promotion packet — 2026-08-10 (Tier-3)

**Operator approval on record (2026-08-10):** *"you have approval for the
implementing the winners and the tick instrumentation"*.

Evidence: `m20-exit-lever-sweep` run over the 10 census legs at **LIVE-PARITY
geometry** (`tp_cap_pct 0.099` — what production actually places), IS/OOS split
`2025-07-01`, yearly walk-forward 2021–2026. Verdicts are quoted from that run,
not from the pre-2026-08-10 sweeps, whose cells were measured on a book that
could never take profit (`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`).

---

## 1. Path A survivors — 5 cells across 4 legs (was 7 across 5; rows 1-2 retracted)

Path A = beats base on net_R **and** maxDD on **both** windows, then ≥ 2/3 of
usable yearly folds. Every row below also improves capital efficiency.

**Rows 1–2 are struck through: they did not survive re-measurement** against a
corrected baseline (§ 2 and the box below). Their original numbers are left
visible rather than deleted, so the size of the error is on the record.

| # | leg | exec | cell | Δ netR IS | Δ netR OOS | Δ maxDD IS | Δ maxDD OOS | Δ cap/day |
|--:|---|---|---|--:|--:|--:|--:|--:|
| ~~1~~ | ~~`trend_donchian_eth`~~ | live | ~~`decay_stall10_t2.5`~~ **RETRACTED** | +7.49 | +9.99 | −7.77 | −5.59 | **+0.130** |
| ~~2~~ | ~~`trend_donchian_eth`~~ | live | ~~`stale12_lt0R`~~ **RETRACTED** | +5.60 | +4.02 | −5.92 | −1.15 | +0.013 |
| 3 | `eth_pullback_2h` | **live** | `decay_stall10_t2.5` | **+24.01** | +5.12 | −3.72 | −1.27 | +0.053 |
| 4 | `trend_donchian_1h` | shadow | `vt_hot90_t2.5` | +11.92 | +15.53 | −8.15 | −14.54 | +0.056 |
| 5 | `trend_donchian_eth_prop` | shadow | `decay_stall10_t1.8` | +1.37 | +7.28 | −3.89 | −7.15 | +0.097 |
| 6 | `trend_donchian_eth_prop` | shadow | `stale12_lt0R` | +1.26 | +5.43 | −4.18 | −4.88 | +0.071 |
| 7 | `avax_pullback_2h` | shadow | `decay_stall6_t2.5` | +5.36 | +4.78 | −3.99 | −2.02 | +0.031 |

**Only ONE surviving cell touches a live-executing leg** — row 3
(`config/strategies.yaml::execution`). Rows 4–7 land on `execution: shadow`
legs and change nothing about money until those legs are separately promoted.
Rows 1–2 were the other two live-leg cells and are retracted below. So the
money-affecting content of this packet is a single two-key addition to one leg.

> ### ⛔ ROWS 1–2 ARE DEAD — the re-run settled it (2026-08-10, run `31414856214`)
>
> Rows 1–2 are on `trend_donchian_eth`, one of the two legs whose baseline
> omitted an **armed** vol-trail lever (§ 2). Re-measured against the corrected
> base, **neither survives**:
>
> | cell | on the BROKEN base | on the CORRECTED base |
> |---|---|---|
> | `decay_stall10_t2.5` | **PASS** · ΔnetR **+7.49** IS / +9.99 OOS · ΔmaxDD −7.77 / −5.59 | **`is_oos_fail`** · ΔnetR **−1.00** IS / +16.06 OOS · ΔmaxDD **+5.96** / −4.63 |
> | `stale12_lt0R` | **PASS** · ΔnetR +5.60 / +4.02 · ΔmaxDD −5.92 / −1.15 | **`wf_fail`** — still clears both windows on both axes (+5.96 / +3.40, −0.40 / −3.32) and now **fails the yearly folds** |
>
> `decay_stall10_t2.5` does not merely weaken: its in-sample sign **flips**, and
> its in-sample drawdown goes from 7.77R better than base to 5.96R worse. Had
> this shipped on the pre-correction evidence, we would have deployed a lever
> that is IS-negative on the real book.
>
> **The correction is self-verifying.** Two cells that previously read as
> improvements now correctly read `tie_no_improvement` with 0.0 on every axis:
> `trend_donchian_eth vt_cold10_t2.5` and `qqq_pullback_1h vt_hot80_t2.5` —
> each is the cell that *re-declares the lever the leg already carries*. Under
> the broken base they looked like additions because the base lacked what the
> leg actually has. Two independent legs, same signature.
>
> **That leaves exactly one live-leg row standing:** row 3,
> `eth_pullback_2h decay_stall10_t2.5`, whose leg declares `trail_mult` and
> nothing else, so its base was genuinely config-exact.
>
> The operator's approval was given against the pre-correction packet. It now
> covers one row, not three.

### `decay_stall10` generalises across TWO legs, not three (revised)

Originally reported as three — `trend_donchian_eth` · `trend_donchian_eth_prop`
(as `_t1.8`, the same lever with the leg-scaled tight mult) · `eth_pullback_2h`.
**`trend_donchian_eth` is out** on the corrected base (see the box above), so the
claim is two legs across two families. `trend_donchian_eth_prop` and
`eth_pullback_2h` declare no vol-trail lever, so their measurements stand.

It is still **not fleet-wide**, and the retraction sharpens rather than weakens
that point: on `trend_donchian_1h` and `avax_pullback_2h` it is IS-only (the
overfit shape), and on `trend_donchian` and `trend_donchian_sol` it improves
neither window. Per-leg declaration is the correct vehicle, not a family
default.

---

## 2. The diff is smaller than the cell names imply — READ THIS BEFORE EDITING

Written from the current `config/strategies.yaml`, not from the cell tags.
Several winners are **one-key completions of a half-declared lever**, and one is
a **change** rather than an addition. The sweep's base was config-exact, so these
existing declarations were already in the baseline and the deltas above are real.

| leg | current | change |
|---|---|---|
| `trend_donchian_eth` | `stale_exit_bars: 8`, `stale_exit_below_r: 0.0`, `trail_vol_below_pctl: 0.1`, `trail_vol_tight_mult: 2.5`, `vol_pctl_window: 200`, `trail_mult: 5.0` | **NO CHANGE — both cells retracted** on the corrected base (see § 1). Left in the table because the *reasoning* still holds for a future re-attempt: `stale8_lt0R` reads `tie_no_improvement` because 8 is already the base, so any stale cell here must move the number. |
| `eth_pullback_2h` | `trail_mult: 5.0` (no exit levers declared) | **add** `trail_decay_stall_bars: 10` + `trail_decay_tight_mult: 2.5` |
| `trend_donchian_1h` | `trail_mult: 5.0` | **add** `trail_vol_above_pctl: 0.9` + `trail_vol_tight_mult: 2.5` (+ `vol_pctl_window: 200`) |
| `trend_donchian_eth_prop` | `trail_mult: 3.5` | **add** `stale_exit_bars: 12` + `stale_exit_below_r: 0.0` + `trail_decay_stall_bars: 10` + `trail_decay_tight_mult: 1.8` |
| `avax_pullback_2h` | `trail_decay_tight_mult: 2.5` + `trail_decay_arm_r: 4.86` (**armed**, R-armed) | **add** `trail_decay_stall_bars: 6` — a SECOND arming condition on a live lever. The base threaded `arm_r` + `tight_mult`, so this Δ is validly measured. |

### CORRECTION (2026-08-10, same day) — the "two inert levers" finding was WRONG

An earlier revision of this packet reported that `trend_donchian_eth` and
`avax_pullback_2h` each carried a half-declared, never-firing exit lever. **Both
claims were false — I read the YAML blocks incompletely.** Read from the field:

- `trend_donchian_eth` carries `trail_vol_below_pctl: 0.1` alongside
  `trail_vol_tight_mult: 2.5`. `src/runtime/trail_vol.py` requires
  `tight_mult > 0 AND (above > 0 OR below > 0)` — `below = 0.1` satisfies it, so
  the **cold-tail vol-trail lever is ARMED** on that leg.
- `avax_pullback_2h` carries `trail_decay_arm_r: 4.86` alongside
  `trail_decay_tight_mult: 2.5`. `src/runtime/trail_decay.py` requires
  `tight > 0 AND (arm_r > 0 OR stall > 0)` — so its **decay lever is ARMED**
  (R-armed at 4.86R). Adding `trail_decay_stall_bars: 6` adds a SECOND arming
  condition to a live lever; it does not switch on a dead one.

### The real finding the correction exposed: the base was NOT config-exact

`m20_fleet_exit_sweep.py::base_args::declared_levers()` threaded the stale,
giveback and trail-**decay** levers into the baseline and **omitted the
trail-VOL one** — while both harnesses had carried `--trail-vol-*` all along.
Exactly two census legs declare an armed vol-trail lever, and both were
therefore measured against a baseline missing a lever that is armed in live:

| leg | declared in YAML | in the sweep's base? |
|---|---|---|
| `trend_donchian_eth` | `below_pctl 0.1` / `tight 2.5` | **no** |
| `qqq_pullback_1h` | `above_pctl 0.8` / `tight 2.5` | **no** |

It was easy to miss because a `vol_*` key *was* threaded — `vol_pctl_window`,
which belongs to the **entry** vol-skip gate, not to the trail.

**What this does and does not invalidate.** Both arms of each A/B omitted the
lever, so each Δ is internally consistent; what is unverified is its
**transport to the live book**. Exit levers interact — a cold-vol-tightened
trail changes which trades a decay or stale rule ever sees — so a Δ measured on
a book without that tightening is not a measurement of the book we would deploy
onto. Fixed in `declared_levers()`, with
`tests/test_m20_fleet_capital_report.py::test_every_declared_exit_lever_reaches_the_config_exact_base`
asserting per-leg completeness (and a companion test failing on any new
lever-shaped YAML key that is not threaded).

### Live wiring verified, not assumed

Each promoted key must be read by the live monitor, or the YAML would be a
silent no-op that *reads* as shipped:

- `stale_exit_bars` / `stale_exit_below_r` → `trend_donchian._stale_stop_verdict`
  and the pullback sibling.
- `trail_decay_*` → `src/runtime/trail_decay.py::resolve_trail_mult`, called from
  `trend_donchian.py` and `htf_pullback_trend_2h.py`.
- `trail_vol_*` → `src/runtime/trail_vol.py::resolve_vol_trail_mult`, called from
  `trend_donchian.py:955` and `htf_pullback_trend_2h.py:523`.

All three read from `open_pkg["meta"]` first, and the units thread the declared
keys into meta at package build (`trend_donchian.py:461-467`) because
`run_monitor_tick` passes `cfg={}` in production. So a **newly declared key
reaches new packages only** — positions already open keep the geometry they were
opened with. That is correct behaviour and it means the change is observable only
on trades opened after the deploy.

---

## 3. Path B candidates — NOT part of this approval

Both cleared the **derived** drawdown tolerance (each leg's own
net_R-per-drawdown rate — no fleet scalar) on both windows AND the Path B
walk-forward. They still need the operator's Path B call, because the tolerance
criterion has a stated asymmetry rather than a threshold.

| leg | exec | cell | headroom IS | headroom OOS | note |
|---|---|---|--:|--:|---|
| `trend_donchian_sol` | **live** | `trail6` (`trail_mult 5.0 → 6.0`) | +3.20 | +4.59 | clean — base book 39.85R/11.53R IS, 12.61R/6.85R OOS |
| `eth_pullback_2h` | **live** | `vt_cold10_t2.5` | +43.59 | +0.60 | **read with suspicion** — the IS base book is 6.62R over a 16.41R drawdown (rate 0.40), so almost any drawdown clears it. This is exactly the permissive-on-an-inefficient-book case; a floor on the base rate would bite here, at the cost of reintroducing the free parameter the criterion removed. |

---

## 4. What the walk-forward rejected, and why that matters

Cells that passed **both windows on both axes** and then failed the folds:
`spy_pullback_1h vt_hot80_t2.5` · `htf_pullback_trend_2h gb1R_afterMFE2R` ·
`htf_pullback_trend_2h decay_stall10_t2` · `trend_donchian_eth decay_stall6_t2.5` ·
`avax_pullback_2h decay_arm1.5R_stall6_t2.5`. Plus two Path B candidates on
`qqq_pullback_1h` (`decay_stall6_t2.5`, `vt_hot80_t2.5`) that cleared the derived
tolerance on both windows with the largest capital gain in the sweep (+0.226/day)
and failed the Path B folds.

The walk-forward rejected roughly as much as it admitted. The `qqq` pair is the
sharpest case: before the gate gap was closed on 2026-08-10 a Path B candidate
short-circuited to `is_oos_fail` **before any walk-forward ran**, so those two
would have arrived as clean candidates — positive headroom both windows, biggest
capital gain, nothing visibly wrong.

---

## 5. Deploy + verification

`config/strategies.yaml` is Tier-3 and the live VM auto-deploys from `main`
(`ict-git-sync`), so **merging is deploying**.

Post-deploy checks, in order:

1. `/api/bot/strategies` — the changed legs report the new keys.
2. `/api/diag/log_file?name=exit_lever_soak` — the annotate rows for a promoted
   leg should STOP (a declared lever takes the real close path instead of
   writing an observe-only row). Continuing annotate rows on a promoted leg mean
   the declaration did not reach `meta`.
3. First closed trade on each changed leg: confirm the exit reason is the
   promoted lever (`stale_stop` / a tightened trail) and not the base geometry.
4. Nothing to verify on rows 4–7 of § 1 until those legs leave `execution: shadow`.

**Rollback** is deleting the added keys — every lever is undeclared-by-default
and an absent key restores the prior geometry exactly (the units fall back to
annotate-only).
