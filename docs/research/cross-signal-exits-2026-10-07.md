# Cross-signal exits — entry from one signal family, stop/exit from another (2026-10-07)

> **Doc status:** `unknown` · category `evidence` · last verified `2026-10-07` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Lane:** CROSS-SIGNAL-RESEARCH (checklist row `CROSS-SIGNAL-RESEARCH`, manager `session_01MM8o5js6TcDFeNAPBY4Ntv`, lane `session_01FRPsG9ZsFVMoYE9ARzoHV6`). Research only: no config, strategy or harness edit. Tier-1.

**Operator's idea (2026-10-07, paraphrased from voice):** *"build a research unit around mixed strategies: a strategy based on one type of market structure with, say, a stop loss correlated to momentum instead of price movement; see if different ways of crossing how we look at the signals provide a sustainable edge."* The hypothesis as registered here: **the exit/stop should listen to a different dimension than the entry did.**

## 0. The answer in six lines

1. **Every live stop today is price-path.** The Chandelier trail ratchets off the since-entry price extreme scaled by ATR; the stale stop reads open R; the giveback stop reads R given back from the peak. None of them reads a momentum series. The harnesses *compute* Wilder ADX, +DI/−DI and the Donchian midline slope, but consume them only at **entry**.
2. **The harness-ready crossings are volatility-regime, time, price-stall, regime-label and ML exits on structure entries** — and all five have already been swept across the fleet and recorded in `docs/research/exit-refinement-coverage.json`. They are overwhelmingly honest negatives. The monthly `m20-exit-lever` template re-sweeps the first three on every live-roster leg (prop legs included), so new hand units for those cells would duplicate standing work.
3. **One cross cell has passed on a real-money leg and sits unshipped:** `ada_pullback_2h` × volatility-conditioned trail (`vt_hot90_t2.5`), PASS on 2026-07-15 and again on 2026-09-29 (base OOS 49 trades, +19.66R base OOS). `RQ-20261007-040` is its confirmation; `RQ-20261007-041` is the generalisation control on its three real-money siblings, where the same lever is an honest negative.
4. **The operator's literal idea — a momentum-conditioned stop — needs a lever.** Two specs are written below (§ 4.1 ADX-conditioned trail, § 4.2 momentum-keyed time stop) and the questions are pre-registered in `research/queue/blocked/RQ-20261007-042` and `-043` with rules, grids and the exact dispatch that unblocks them.
5. **One harness-ready cell has never been measured at live geometry and has no dispatch route:** pullback entry × trend-midline flip exit (`--flip-exit-bars`). `blocked/RQ-20261007-044`; a one-line cell addition makes it dispatchable (§ 4.3). A session can run it today.
6. **Prior, stated before any run:** the closest proxies (price-stall trail decay, ADX-label regime-flip exit) failed on nearly every leg, and the flip replay's finding — *"wherever the flip fires it guts the trend tail"* — is the specific risk a momentum stop must beat. § 5 says what would make the hypothesis FAIL.

Matrix counts (§ 3): **HARNESS-READY 12 · NEEDS-A-LEVER 9 · INFEASIBLE / NOT-WORTH-IT 7** over 28 cells.

## 1. Inventory — what the harnesses and live units compute today

### 1.1 Entry signal families (the "market structure" side)

| family | live leg(s) | harness | signal, with where it is computed |
|---|---|---|---|
| Donchian breakout | `trend_donchian_eth_4h`, `trend_donchian_xrp_4h` (bybit_2 real money), `trend_donchian_eth/_sol` (1h), `trend_donchian_eth_prop/_sol_prop` (breakout_2 / tradeify_1 / velotrade_1) | `scripts/backtest_trend.py` | N-bar channel breakout (`--donchian`, :1237); confidence = breakout depth / ATR (:1255) |
| Pullback-in-trend | `xrp_pullback_2h`, `ada_pullback_2h` (real money), `eth/sol_pullback_2h`, `eth_pullback_prop_2h` | `scripts/backtest_pullback.py` | trend lookback + pullback fraction (:1091-1096); HTF trend midline `df["mid"]` |
| Failed-breakout fade | `fade_breakout_4h` (shadow) | `scripts/backtest_fade.py` | pierce-and-close-back-inside (docstring :7-14); ADX<20 chop gate at :196-198 |
| Squeeze (volatility structure) | `squeeze_breakout_4h` | `scripts/backtest_squeeze.py` | BB inside KC then breakout (:563-565) |
| FVG / range, ICT sweep+displacement | `fvg_range_15m` (shadow), `ict_scalp_*` | `scripts/backtest_fvg_range.py`, `scripts/backtest_ict_scalp.py` | range touches + FVG (:697-716; exit styles :75); sweep + displacement + FVG + HTF EMA (:1166-1168) |

### 1.2 Momentum / strength signals computed but consumed only at entry

| signal | where computed | consumed by |
|---|---|---|
| Wilder ADX | `backtest_trend.py:196 _adx`, `backtest_pullback.py:163`, `backtest_fade.py:140`; live `src/runtime/regime/detector.py:65 wilder_adx` | entry gates `--adx-min/--adx-max` (trend :1264-1266), soft regime weight `--adx-soft-weight-*` (:1270-1283), fade chop gate (:591) |
| +DI / −DI | `backtest_trend.py:172 _directional_indicators` | entry `--direction-filter di` (:1291) |
| Donchian midline slope | `backtest_trend.py:373` (`dir_slope`), pullback `df["mid"].diff()` (:370) | entry `--direction-filter slope`; **exit** only in pullback via `--flip-exit-bars` (:1152, fired :678) |
| RSI, volume | not in any harness; VWAP only in research-only `src/units/strategies/hf_vwap_revert.py:114` | — |

### 1.3 Volatility, regime, ML and target-revision signals

| signal | where | exit lever today |
|---|---|---|
| ATR (stop/trail scale) | every harness (`--atr-period`) | `--atr-stop-mult`, `--trail-mult` (Chandelier) |
| ATR trailing percentile | trend `--vol-pctl-window` :1372; pullback :1205 | entry skip `--vol-skip-*`; **exit** `--trail-vol-above/below-pctl --trail-vol-tight-mult` (trend :1374-1383, pullback :1207-1217, squeeze :608-618); rule single-homed in `src/research/trail_levers.py:40 effective_trail_mult` |
| e38 / regime label (ADX chop<20≤transitional<25≤trending) | `src/runtime/regime/detector.py:95 regime_label`; `config/regime_policy.yaml` OFF cells | **exit** only offline: `scripts/research/m20_regime_flip_replay.py` (P4.5, trainer-local, no workflow) |
| ML exit head | `exit-head-donchian-1h-v1` live on `trend_donchian_eth/_sol` (`exit_head_model`, strategies.yaml); `scripts/research/analyze_exit_head.py` | `research-exit-head-replay-trainer.yml` (dispatchable, trainer) |
| Target expectation / extension | `src/runtime/target_expectation.py:233 evaluate_extension` | **no harness lever** (blocked/RQ-20261007-001..004, GEOM-B1-HARNESS) |
| Vol-target overlay | `scripts/backtest_vol_target.py` | sizing, not an exit; no full cost stack (CLAUDE.md D1 list) |

### 1.4 Exit / stop levers the harnesses expose, by the dimension each one reads

| lever | reads | harnesses | dispatch route |
|---|---|---|---|
| `--atr-stop-mult`, `--trail-mult` (Chandelier) | price path (vol-scaled) | trend, pullback, squeeze, fade (`--exit-style trail`) | m20 `trail_geometry` cells; e35 bracket sweep (`STOP_MULT_GRID`, `e35_bracket_geometry_sweep.py:135`) |
| `--tp-r`, `--tp-cap-pct` | R geometry | all | e35 `TP_R_GRID` (:134) |
| `--giveback-min-mfe-r/--giveback-r` | R given back from peak | trend :1320-1324, pullback :1170-1173, squeeze, ict_scalp, fvg | m20 `giveback_stop` |
| `--be-floor-r`, `--bank-frac/--bank-at-r`, `--rr-floor` | R geometry | trend, pullback (rr_floor needs tp_cap) | m20 `rr_floor` cells |
| `--stale-exit-bars/--stale-exit-below-r` | time × open R | trend :1284-1290 (fires :784-791), pullback :1146-1149 (fires :680-682), squeeze, ict_scalp, fvg | m20 `stale_stop` |
| `--timeout-bars`, `--flat-at-utc` | time | all / trend | e35 `TIMEOUT_GRID`; RQ-20260930-503 |
| `--trail-decay-arm-r / -stall-bars / -tight-mult` | R reached, or **price stall** (bars since new extreme) — the P4.1 momentum-exhaustion *proxy* | trend :1325-1333, pullback :1184-1192 | m20 `trail_decay` (cells `decay_arm2R`, `decay_stall6`, `decay_stall10`, `decay_arm1.5R_stall6`) |
| `--trail-vol-above/below-pctl / -tight-mult` | **volatility regime** | trend, pullback, squeeze | m20 `vol_trail` (`vt_hot90`, `vt_hot80`, `vt_cold10`) |
| `--flip-exit-bars` | **trend structure** (close vs HTF midline for N bars) | pullback only (:1152) | none — swept once in July (`m20_exit_sweep.py:180-182`), no `cells_for` cell |
| `--exit-style tp1r / mid / far` | **channel structure** (midpoint / far band) | fade :582, fvg_range :75 | `research-harness-dispatch.yml` runs each harness at its *default* (`far` for fade, fixed flags :148-152) |
| regime-flip exit | **regime label** | `m20_regime_flip_replay.py` over `--emit-trades` | trainer-local, no workflow |
| exit head τ-policy | **ML** | `analyze_exit_head.py` replay | `research-exit-head-replay-trainer.yml` |

Two naming corrections to the dispatch brief, so nobody searches for them: `scripts/research/exit_lever_ablation.py` **does not exist**; the lever-OFF ablation is `m20_fleet_exit_sweep.cells_for(without_declared_levers=...)` (:916-946) plus `exit_sweep_positive_control.py`. `e3_joint_lever_sweep.py` replays levers over an existing in-trade **panel** at decision time (cost-neutral by construction, its own docstring :11-25) and is a screen, not a gate — a positive there must be re-run through the fleet sweep.

## 2. Cost stack and gate the units inherit

Every dispatched arm runs the harness CLI, whose `main()` resolves slippage and funding through `src/runtime/execution_costs.py:138 resolve_cost_policy` (`None` ⇒ venue default, explicit wins; `backtest_trend.py:1444`, same shape in pullback/squeeze/fade), on top of the 7.5 bps round-trip fee. The m20 workflow grades with `m20_fleet_exit_sweep.beats` (Path A), `is_path_b_candidate` / `drawdown_exchange_rate` (Path B, grant cap `dN/N_b ≤ 1.0`), `MIN_OOS_TRADES = 25` (:599), and `walkforward` over `FOLDS` 2021–2026 (:217, :2076). The base arm is the leg's **config-exact** book with every declared lever present (`base_args` :414), so the positive control the dispatch asked for — the current leg reproducing its committed record — is the base row of every m20 result record (`base_net_r_OOS`, `base_trades_OOS`).

## 3. Feasibility matrix — entry family × exit dimension

Legend: **R** HARNESS-READY (an existing lever makes the exit follow that dimension; dispatch route named) · **N** NEEDS-A-LEVER (one named flag or cell would do it) · **I** INFEASIBLE / NOT-WORTH-IT (why). *Coverage* = verdict already in `exit-refinement-coverage.json` for the real-money / prop legs.

| entry ↓ / exit dimension → | price path (baseline) | time | volatility regime | **momentum: ADX-conditioned trail** | **momentum: DI-cross / slope-flip exit** | **momentum: time-stop keyed to momentum decay** | **momentum divergence** | trend / channel structure | regime label flip | ML exit head |
|---|---|---|---|---|---|---|---|---|---|---|
| **Donchian breakout** (trend) | R — base; e35/m20 | R — `stale_stop` (m20). *Coverage:* shipped xrp_4h, eth_prop; honest_negative else | R — `vol_trail` (m20). *Coverage:* honest_negative on eth_4h, xrp_4h, eth/sol 1h, eth_prop, sol_prop | **N — spec § 4.1** (`--trail-adx-*`); question `blocked/RQ-042` | N — `--exit-di-cross-bars N` (+DI/−DI already at :172); not specced, lower priority | **N — spec § 4.2** (`--momentum-stale-*`); `blocked/RQ-043` | N/I — needs a swing detector no harness has; lowest priority | N — port `--flip-exit-bars` from pullback to trend (midline slope exists :373); Turtle-style `--exit-channel-bars N` also absent | R (session) — `m20_regime_flip_replay.py`. *Coverage:* honest_negative fleet-wide (P4.5) — not worth re-running | R — replay workflow. *Coverage:* shipped eth/sol 1h; honest_negative 4h; RQ-20260927-003 prop feasibility |
| **Pullback-in-trend** | R — base | R — `stale_stop`. *Coverage:* honest_negative | R — `vol_trail`. **PASSED unshipped on ada_pullback_2h** (2026-07-15, 2026-09-29) → **RQ-040 confirm, RQ-041 control** | **N — § 4.1**; `blocked/RQ-042` | R (lever) / N (dispatch cell) — `--flip-exit-bars` is a slope-side structure flip; see structure column | **N — § 4.2**; `blocked/RQ-043` | N/I — as above | **R (session-local) / N (cell)** — `--flip-exit-bars` :1152; July BTC FAIL at old geometry; never run config-exact at 0.099 → `blocked/RQ-044`, § 4.3 | R (session) — honest_negative (eth_pullback +67.7R → −11.8R) | R — round 3 E1 FAIL (AUC ~0.54), `blocked:no_lever_consumer_in_unit` |
| **Failed-breakout fade** | R — `--exit-style trail` (live shape) | R — `--timeout-bars` only (no stale lever in fade) | I — no `--trail-vol-*` in fade; leg is shadow since 2026-06-01 (−86R live) — not worth a lever | I — same | I — same | I — same | I | R — `--exit-style mid/far`: **same family as the entry** (both read the Donchian channel), so not a cross. Audit 2026-05-24: 4h far −16.5R vs trail +40.1R — the structure exit *loses* to the price-path trail on this entry | I | I |
| **Squeeze** (volatility structure) | R — base | R — `stale_stop` (m20, squeeze_breakout_4h in template) | R — `vol_trail` (since 2026-08-17). *Coverage:* in template; same-dimension as the entry (vol → vol), weak cross | N — § 4.1 would need a port to squeeze (ADX not computed there) | I — no DI in squeeze | N — as left | I | I — no channel/midline in squeeze | R (session) — not run; low prior | I — no exit panel for squeeze |
| **FVG / range, ICT scalp** (fixed bracket) | R — fixed SL/TP | R — `stale_stop` / `--sim-breakeven` (m20 scalp cells) | I — fixed-bracket legs have no trail to condition (`m20_fleet_exit_sweep.py:71-73`) | I — same | I | N — a momentum-keyed stale would apply; but 5m/15m cells died on the cost stack in batch 1 (RQ-20261006-005..031) — not worth it | I | R — fvg `--exit-style mid/far/tp1r` (:75), same family as entry; shadow leg | I | R — scalp exit heads measured (coverage) |

**Momentum-conditioned stops, explicitly:** every momentum column on the two live-money entry families is **N**. The one thing that *looks* like a momentum stop and exists — stall-armed trail decay — reads price (bars since a new extreme), and its fleet sweep (P4.1, 2026-07-12; re-swept at live parity 2026-08-13/15) is honest_negative on every real-money leg except the two where a decay cell shipped (`trend_donchian_xrp_4h` arm2R→2.5, `trend_donchian_eth_prop` stall10→1.8). The regime-flip EXIT (ADX label drops into an OFF cell) is the only lever that has ever read ADX after entry, and it is an honest negative fleet-wide.

## 4. Lever proposals (specs only — this lane builds nothing)

### 4.1 Spec A — ADX-conditioned trail (`momentum_trail`)

**Flags** (trend + pullback; squeeze later only if ADX is added there):

```
--trail-adx-below A            float, default 0.0  = off, byte-identical
--trail-adx-falling-bars k     int,   default 0    = no falling test (level only)
--trail-adx-tight-mult T       float, default 0.0  = off; T < trail_mult required when armed
```

**Semantics.** On each managed bar `j` (after the intrabar stop test, before the ratchet, exactly where `_effective_trail_mult` is called at `backtest_trend.py:729`), the lever is *fired* when `ADX_j < A` and, if `k > 0`, `ADX_j < ADX_{j-k}`. When fired the candidate trail mult is `min(base_mult, T)`; the price-ratcheted stop never loosens (same `max()`/`min()` ratchet as today), so "widen while momentum confirms" is expressed as *the base mult is the wide one*; the lever can only tighten. Composes with trail_decay and vol_trail **by minimum**, like vol_trail does. ADX is Wilder-14 on the leg's own bars from `_adx` (already imported; compute it whenever the lever is armed, extending `adx_active` at :360). NaN (warm-up) ⇒ inert (fail-permissive), as vol_trail.

**Single home.** Add `adx_j: float | None`, `adx_ref: float | None`, `trail_adx_below`, `trail_adx_tight_mult` to `src/research/trail_levers.effective_trail_mult` (the ONE rule; its docstring forbids a second copy) and thread the values from both harnesses. `backtest_pullback.py:585-620` still carries an inline `_eff_tm/_vol_tm`; this lever is the moment to route pullback through the shared function, not to add a third copy.

**Default = today's behaviour.** `A = 0` ⇒ `ADX_j < 0` is never true ⇒ the mult is untouched ⇒ byte-identical net_R, trades, exit reasons on every committed baseline.

**Attribution.** Emitted trade rows gain `trail_adx_fired_bars` (count) and the exit reason stays `trail_stop` with `trail_adx_tightened: true` when the final stop was set on a fired bar — the same per-trade tag P4.1 added for decay.

**Golden tests** (`tests/test_adx_conditional_trail_lever.py`, mirroring `tests/test_vol_conditional_trail_lever.py`):
1. `A=0` ⇒ identical summary and trade JSONL to the lever-less run on `data/backtest_candles.csv` (md5-equal).
2. `A=100, T=trail_mult` ⇒ identical to (1) — firing with no tightening changes nothing.
3. `A=100, T<trail_mult, k=0` ⇒ identical to a constant `--trail-mult T` run (every bar fires).
4. `k=3` with a monotonically rising synthetic ADX ⇒ never fires; with a falling one ⇒ fires on every bar past warm-up.
5. Composition: with vol_trail also armed, the mult on a bar where both fire is `min(T_adx, T_vol)`.

**Cells** (`m20_fleet_exit_sweep.cells_for`, fam in donchian/pullback): `adx20_k0`, `adx20_k3`, `adx25_k0`, `adx25_k3`, each with `T = max(1.5, trail_mult/2)`; matrix lever `momentum_trail`; `m20-exit-lever-sweep.yml` `levers` input accepts it; coverage JSON gains the column. Then `blocked/RQ-20261007-042` moves to the queue with the dispatch written in its `clears_when`.

### 4.2 Spec B — time-stop keyed to momentum decay (`momentum_stale`)

**Flags** (trend + pullback):

```
--momentum-stale-bars N          int,   default 0   = off, byte-identical
--momentum-stale-adx-below A     float, default 0.0 = off
```

**Semantics.** At bar CLOSE, after the intrabar stop test and the existing stale/giveback checks (precedence: giveback → flip → stale → momentum_stale → rr_floor, i.e. inserted before `rr_floor` so composing it cannot re-grade a shipped lever's recorded verdict — the same rule the rr_floor comment states at pullback :683-690), close the trade when `(j - entry) >= N` **and** `ADX_j < ADX_entry` **and** `ADX_j < A`. Open R is **not** consulted — that is the whole difference from `stale_stop`. Exit reason `momentum_stale`; rows carry `adx_at_entry`, `adx_at_exit`. Default `N=0` ⇒ never fires ⇒ byte-identical.

**Golden tests:** (1) `N=0` md5-identical to baseline; (2) `A=100, N=1` with a synthetic strictly-falling ADX ⇒ every trade exits at the first bar after entry unless the stop hits first (stop-first preserved); (3) with a strictly-rising ADX ⇒ never fires; (4) on a leg declaring `stale_exit_bars`, the stale-swap base (shipped stale removed) is produced by `shipped_lever_cells`-style omission and is **not** the config-exact base — asserted by name in the cell's `params`.

**Cells:** `ms8_a20`, `ms8_a25`, `ms12_a20`, `ms12_a25`; matrix lever `momentum_stale`; on legs that ship a stale stop the sweep emits BOTH the combo (config-exact base) and the swap base, and the unit's rule grades the swap. Unblocks `blocked/RQ-20261007-043`.

### 4.3 Cell-only: pullback trend-flip exit (`flip_exit`)

No harness change. `cells_for` gains `("flip1", "flip_exit", ["--flip-exit-bars", "1"])` and `("flip2", "flip_exit", ["--flip-exit-bars", "2"])` for `fam == "pullback"`; the workflow's `levers` input accepts `flip_exit`; coverage JSON gains the column. Unblocks `blocked/RQ-20261007-044`. Secondary, not specced: porting `--flip-exit-bars` to `backtest_trend.py` (the midline series already exists at :373) would open the Donchian × structure-flip cell, and a Turtle-style `--exit-channel-bars N` (close beyond the N-bar opposite extreme) would open the classic breakout-in / channel-out construction.

### 4.4 Dispatch gap (not a harness lever)

`research-harness-dispatch.yml` fixes every harness's argv to `--symbol --timeframe --json --emit-trades` (:148-152), so it can only ever run a harness at defaults and cannot carry a crossed arm; `research-script-run.yml` admits `scripts/backtest*.py` but the runner holds no candles and a bare harness writes no `verdict.json` (`script_run.py:102`). The m20 workflow is therefore the only dispatchable route for a lever A/B, and a cross cell is dispatchable exactly when `cells_for` knows it. That is why §§ 4.1–4.3 all end in a `cells_for` entry.

## 5. Units registered (band RQ-20261007-040..059; 040–044 used, 045–059 unused)

| id | where | route | question | rule, in one line |
|---|---|---|---|---|
| RQ-20261007-040 | queue | `m20-exit-lever-sweep.yml`, legs `ada_pullback_2h`, levers `vol_trail`, auto-graded | confirm the twice-passed `vt_hot90_t2.5` on a fresh window | PASS (verdict pass, base OOS ≥ 25, cell = vt_hot90) → Tier-3 declare proposal, bybit trio in one PR; pass on another cell → needs_review; no_action_warranted → coverage row → honest_negative; < 25 → insufficient_base |
| RQ-20261007-041 | queue | same workflow, legs `xrp_pullback_2h,trend_donchian_eth_4h,trend_donchian_xrp_4h`, levers `vol_trail` | does the ADA cross generalise to its real-money siblings? | per leg pass / honest_negative / insufficient_base; with 040 PASS: ≥1 sibling pass = *principle supported*, 0 = *leg-specific*; with 040 FAIL = *no surviving harness-ready cell* |
| RQ-20261007-042 | blocked | m20 once `momentum_trail` exists (§ 4.1) | ADX-conditioned trail on 6 real-money/prop legs | per leg P2 gate + wf ≥ 4/6 + attribution (the lever's own exits must carry the gain); ≥2 legs = supported, 1 = leg-specific, 0 with ≥4 gradeable = refuted in this form |
| RQ-20261007-043 | blocked | m20 once `momentum_stale` exists (§ 4.2) | momentum-keyed time stop vs config-exact and vs the shipped R-keyed stale (swap) | as 042; a pass vs combo but not vs swap is "adds to, does not replace" — no proposal |
| RQ-20261007-044 | blocked (session-local route stated) | m20 once `flip_exit` cell exists (§ 4.3) | pullback × trend-midline flip exit at live geometry on 5 pullback legs | per leg P2 gate + wf; combo rule on legs with shipped decay |

All five carry `decision_rule.registered_before_run: true`, a power block with the derivation (d = 0.45 → 39; gate floor 25; expected_n from measured base OOS counts, never pooled across legs), full cost stack via the harness `main()` resolver, and `what_it_does_not_do`. Guards run locally on this branch: `check_research_queue_decision_rule.py --base origin/main` (graded, 0 failures), `tests/test_research_queue.py`, `check_research_queue_health.py`, dispatcher dry run (`040`/`041` `would_dispatch route=runner`).

**Why not more queued units.** Structure × {vol_trail, stale, trail_decay} on every live-roster leg, prop legs included, is already generated monthly by `research/templates/m20-exit-lever.yaml` (`grid.legs: live_roster_legs`, rosters read from `config/accounts.yaml`), so a hand unit per cell would re-ask a standing question. Regime-flip and ML-head crossings have committed fleet verdicts. The fade `mid/far` exits are the same family as the fade entry. Everything that remains is a lever.

## 6. What would make the hypothesis FAIL

Written before any of the five units runs.

1. **RQ-040 lands `no_action_warranted` with base OOS ≥ 25.** The only passing cross cell was a two-window artifact; the coverage row moves to honest_negative and no harness-ready cell supports the idea.
2. **RQ-040 passes and RQ-041 lands 0 passes on ≥ 2 gradeable siblings.** The ADA result is instrument-specific volatility clustering, not a property of crossing signal families; the operator decides on a one-leg declare, and the memo's principle claim is withdrawn.
3. **RQ-042 and RQ-043 both land 0 passes with ≥ 4 gradeable legs each.** The literal idea — stop reads momentum — is refuted *in this form* on real-money and prop legs; what survives is at most the P4.1 price-stall proxy already shipped on two legs.
4. **A cell passes the gate while its own fired exits are net negative** (the attribution read in 042/043). That is "passes for the wrong reason" and is pre-declared not to pass.
5. **Any pass that rests on a base OOS under 25 or a walk-forward under 4 of 6** is insufficient_base / not generalising and is reported, never proposed — the same floors the exit-refinement skill binds every lever to.

What would **support** it: 040 confirms **and** ≥ 1 sibling in 041 passes, **or** ≥ 2 legs pass in 042 or 043 once the lever exists. Either outcome routes to a Tier-3 declare proposal the manager carries; nothing here changes config.

## 7. What this lane did not do, and spend

- Built no lever, no cell, no workflow change; edited no config or strategy.
- Did not run any backtest: every number above is read from committed records (coverage JSON, result records, the 2026-05-24 fade audit, the M20 memo) and cited.
- Did not re-register the m20 template's standing questions as hand units (§ 5).
- The 15 unused ids 045–059 return to the manager's band.
- Spend: estimated **≈ $9** of the $25 ceiling (token-based estimate; the lane has no meter).

**Readout owner and date:** pipeline item `PI-20261007-9ARZOHV6-0001` (due 2026-10-21) re-reads `research/results/RQ-20261007-040/` and `-041/`, applies the rules above verbatim, updates the coverage rows, and checks whether a build row for § 4.1 has been opened.
