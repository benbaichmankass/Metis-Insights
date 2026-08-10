# M20 exit-lever promotion packet — 2026-08-10 (Tier-3)

**Operator approval on record (2026-08-10):** *"you have approval for the
implementing the winners and the tick instrumentation"*.

Evidence: `m20-exit-lever-sweep` run over the 10 census legs at **LIVE-PARITY
geometry** (`tp_cap_pct 0.099` — what production actually places), IS/OOS split
`2025-07-01`, yearly walk-forward 2021–2026. Verdicts are quoted from that run,
not from the pre-2026-08-10 sweeps, whose cells were measured on a book that
could never take profit (`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`).

---

## 1. Path A survivors — 7 cells across 5 legs

Path A = beats base on net_R **and** maxDD on **both** windows, then ≥ 2/3 of
usable yearly folds. Every row below also improves capital efficiency.

| # | leg | exec | cell | Δ netR IS | Δ netR OOS | Δ maxDD IS | Δ maxDD OOS | Δ cap/day |
|--:|---|---|---|--:|--:|--:|--:|--:|
| 1 | `trend_donchian_eth` | **live** | `decay_stall10_t2.5` | +7.49 | +9.99 | −7.77 | −5.59 | **+0.130** |
| 2 | `trend_donchian_eth` | **live** | `stale12_lt0R` | +5.60 | +4.02 | −5.92 | −1.15 | +0.013 |
| 3 | `eth_pullback_2h` | **live** | `decay_stall10_t2.5` | **+24.01** | +5.12 | −3.72 | −1.27 | +0.053 |
| 4 | `trend_donchian_1h` | shadow | `vt_hot90_t2.5` | +11.92 | +15.53 | −8.15 | −14.54 | +0.056 |
| 5 | `trend_donchian_eth_prop` | shadow | `decay_stall10_t1.8` | +1.37 | +7.28 | −3.89 | −7.15 | +0.097 |
| 6 | `trend_donchian_eth_prop` | shadow | `stale12_lt0R` | +1.26 | +5.43 | −4.18 | −4.88 | +0.071 |
| 7 | `avax_pullback_2h` | shadow | `decay_stall6_t2.5` | +5.36 | +4.78 | −3.99 | −2.02 | +0.031 |

**Only 3 of the 7 touch a live-executing leg** (`config/strategies.yaml::execution`).
Rows 4–7 land on `execution: shadow` legs and change nothing about money until
those legs are separately promoted — worth stating plainly so the packet is not
read as a 7-cell money change.

### `decay_stall10` generalises across three independent legs

`trend_donchian_eth` · `trend_donchian_eth_prop` (as `_t1.8`, the same lever with
the leg-scaled tight mult) · `eth_pullback_2h`. That is the only cell in the
sweep to pass on three legs, across two families.

It is still **not fleet-wide**: on `trend_donchian_1h` and `avax_pullback_2h`
it is IS-only (the overfit shape), and on `trend_donchian` and
`trend_donchian_sol` it improves neither window. Per-leg declaration is the
correct vehicle, not a family default.

---

## 2. The diff is smaller than the cell names imply — READ THIS BEFORE EDITING

Written from the current `config/strategies.yaml`, not from the cell tags.
Several winners are **one-key completions of a half-declared lever**, and one is
a **change** rather than an addition. The sweep's base was config-exact, so these
existing declarations were already in the baseline and the deltas above are real.

| leg | current | change |
|---|---|---|
| `trend_donchian_eth` | `stale_exit_bars: 8`, `stale_exit_below_r: 0.0`, `trail_vol_tight_mult: 2.5`, `vol_pctl_window: 200`, `trail_mult: 5.0` | **`stale_exit_bars: 8 → 12`** (a change, not an add — `stale8_lt0R` measured `tie_no_improvement` here precisely because 8 is already the base) · **add** `trail_decay_stall_bars: 10` + `trail_decay_tight_mult: 2.5` |
| `eth_pullback_2h` | `trail_mult: 5.0` (no exit levers declared) | **add** `trail_decay_stall_bars: 10` + `trail_decay_tight_mult: 2.5` |
| `trend_donchian_1h` | `trail_mult: 5.0` | **add** `trail_vol_above_pctl: 0.9` + `trail_vol_tight_mult: 2.5` (+ `vol_pctl_window: 200`) |
| `trend_donchian_eth_prop` | `trail_mult: 3.5` | **add** `stale_exit_bars: 12` + `stale_exit_below_r: 0.0` + `trail_decay_stall_bars: 10` + `trail_decay_tight_mult: 1.8` |
| `avax_pullback_2h` | `trail_decay_tight_mult: 2.5` (**inert** — no `stall_bars`/`arm_r`) | **add** `trail_decay_stall_bars: 6` (one key completes it) |

### Two half-declared levers found while writing this

Both are currently **inert**, which is a finding in its own right — a leg that
looks configured and behaves as if it were not:

- `trend_donchian_eth` carries `trail_vol_tight_mult: 2.5` + `vol_pctl_window: 200`
  with **no** `trail_vol_above_pctl`/`below_pctl`. `src/runtime/trail_vol.py`
  requires `tight_mult > 0` AND (`above > 0` OR `below > 0`), so the vol-trail
  lever never fires on that leg.
- `avax_pullback_2h` carries `trail_decay_tight_mult: 2.5` with no
  `trail_decay_stall_bars` / `trail_decay_arm_r`, so its decay lever never arms.

Neither is a bug in the sweep — the config-exact base correctly modelled them as
inert. They are logged so a future reader does not mistake the declaration for
behaviour.

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
