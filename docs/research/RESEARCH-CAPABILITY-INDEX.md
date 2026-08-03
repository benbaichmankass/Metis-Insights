# Research capability index — "can we measure X?"

**Purpose.** Answer, in one lookup, *what this repo can already measure* — so a research
session never concludes something is unmeasurable when a tool for it exists.

**Why it exists.** On 2026-07-30 a session concluded that `trend_donchian`'s six live
regime cells could **never** be re-measured, because an ML exit head "cannot be replayed
offline." It built a three-option disposition on that, filed a high-severity backlog row,
and escalated it to the operator as a decision. All of it was wrong: the M20 toolchain
(`build_intrabar_exit_panel.py` → `analyze_exit_head.py`) replays exactly that, under
grouped/purged/embargoed walk-forward. The session had looked at one code comment in one
harness — `regime_debt_matrix.py::_UNREPLAYABLE`, whose real scope is *"`backtest_trend.py`
does not model this lever"* — and generalized it to the whole system.

The measured gap behind that: **47 of 51 scripts in `scripts/research/` were mentioned in
no skill**, while `backtesting/SKILL.md` claimed to map *"every real backtest entry point in
the repo."* A session therefore reconstructs the toolbox from `grep` plus code comments —
and code comments are what misled it. Full audit:
[`RESEARCH-INFRA-AUDIT-2026-07-30.md`](./RESEARCH-INFRA-AUDIT-2026-07-30.md).

> **Binding:** before writing "X cannot be measured / is not replayable / needs new
> tooling" in any research output, check this index and grep `scripts/research/`. A tool
> asserting **impossibility** is not authoritative — see the audit § "impossibility claims".

Paths are repo-relative. Each script's own docstring is the detailed contract; this is the
routing layer, not a replacement for reading it.

---

## 1. Strategy P&L over history (net-of-fee)

| Question | Tool |
|---|---|
| How does a Donchian/trend leg perform? | `scripts/backtest_trend.py` |
| A pullback leg? | `scripts/backtest_pullback.py` |
| A TTM squeeze (BB-inside-KC) leg? | `scripts/backtest_squeeze.py` |
| A fade/failed-breakout leg? | `scripts/backtest_fade.py` |
| ict_scalp? | `scripts/backtest_ict_scalp.py` — ⚠️ **gross only, no fee model** (`BL-20260610-M15-1`) |
| vwap? | `src/backtest/run_backtest_vwap.py` |
| The whole multi-strategy system on one clock? | `scripts/backtest_system.py` |
| Time-series momentum / MA-cross (new-idea harness)? | `scripts/research/research_momentum.py` |
| A roll-adjusted continuous futures series to test on? | `scripts/research/build_continuous_contract.py` |
| Convert raw `market_raw` jsonl candles to harness CSV? | `scripts/research/market_raw_to_csv.py` |

## 2. Regime conditioning — when is a leg strong vs weak?

| Question | Tool |
|---|---|
| Net-R per (trend regime × direction) from **any** harness's emitted trades? | `scripts/research/regime_tag_emitted.py` |
| The same, split by the **2-D `(trend, vol)` cell the live router gates on**? | `scripts/research/regime_tag_emitted.py --vol-labels` |
| **Would this grade survive a different candle feed?** (the #8144 defect) | `scripts/research/regime_tag_emitted.py` — `boundary_exposure`, emitted on every run |
| The direct two-feed test of that | `scripts/research/regime_tag_emitted.py --sensitivity-data <second-feed>` |
| **Per-bar `calm`/`volatile` labels** — replay the live ML vol axis offline | `scripts/research/ml_vol_label_replay.py` |
| Does that replay actually reproduce the LIVE label? | `scripts/research/ml_vol_label_replay.py verify` |
| The same, driven per-strategy off live config, with a fidelity label? | `scripts/research/regime_debt_matrix.py` |
| Is ONE (regime, direction) cell **temporally stable** (the #7915 gate)? | `scripts/research/regime_cell_walkforward.py` |
| Is a cell's verdict **robust to the ADX attribution cut-points** (live 20/25) or does it flip? (R2 — the two un-swept global constants every cell keys on) | `scripts/research/regime_adx_cutpoint_sweep.py` |
| Regime × strategy matrix (routing groundwork)? | `scripts/research/regime_matrix.py` |
| Conditional discovery **within** a regime cell over a panel? | `scripts/research/analyze_panel_by_cell.py` |

⚠️ **Fidelity is the load-bearing field** on §2 rows. `faithful` = the harness models every
lever the live config declares; `approximate` = it does not, and the omitted levers are
named. **Never author or retire a live cell off an `approximate` row.** A lever the harness
cannot model is not necessarily unmeasurable — check §3 before concluding that.

⚠️ **The vol axis is a SECOND fidelity question, and it is about POPULATION, not levers**
(`BL-20260730-2D-VOL-CELLS-UNAUDITABLE`). Six live cells are 2-D `trend_vol` cells. Grading
such a strategy on the 1-D trend axis alone POOLS vol states the live gate already refuses,
so the verdict measures a population live does not trade — it can be complete, green, and
still wrong-signed (a near-miss Tier-3 proposal on `squeeze_breakout_4h` was withheld for
exactly this on 2026-07-30). Pass `--vol-labels` for any strategy carrying a `trend_vol`
cell; `regime_tag_emitted` declares `vol_axis: absent` when you don't, so the gap is always
visible in the artifact rather than assumed away.

⚠️ **A THIRD fidelity question, and it is about the INSTRUMENT itself**
(`BL-20260731-REGIME-ATTRIBUTION-FEED-SENSITIVE`). Per-regime net-R is bucketed by HARD
cutoffs (chop <20 · transitional 20-25 · trending ≥25) applied to a noisy rolling indicator,
against a heavy-tailed per-trade R distribution — so a few large winners sitting near a
cutoff carry tens of R across it on sub-1% input differences. **Measured (#8137): the SAME
357 trades re-tagged against a second, equally-valid BTCUSDT 1h feed moved one bucket by
24.92R (~31% of lifetime net-R)** while the two feeds agreed on trade outcomes to 1.05R and
on regime base rates to 0.5pp. The instrument moved; the market did not. `regime_tag_emitted`
now reports `boundary_exposure` on every run (no second feed needed) and
`feed_sensitivity_checked` so an unchecked grade can never read as a cross-checked one.
**A bucket whose sign flips across feeds cannot source a Tier-3 proposal in either
direction, at any n.** Read each bucket's exposure against its `structural_floor_pct`:
`transitional` is only 5 ADX wide, so its ~100% is a tautology, not an alarm.

⚠️ **The vol label comes from the ADVISORY ML HEAD, never from `vol_detector`.** Under
`REGIME_ML_VERDICT_MODE=use` (live for BTC since 2026-06-28) `intents._decision_vol_regime`
resolves the vol axis from the advisory head's `P(volatile)` keyed **per symbol**. The
frozen-edge `vol_detector` is a *different* label whose own docstring records that the
authored cells "LOSE money under the frozen label" — splitting a harness on it is not a
cheaper approximation, it is a second, opposite mismatch that looks like a fix.

## 3. Exits — including ML exit heads (the 2026-07-30 blind spot)

| Question | Tool |
|---|---|
| Where do exits give back R? (MFE/MAE excursions) | `scripts/research/build_exit_panel.py` |
| **Per-bar in-trade** panel — one row per bar while a position is open | `scripts/research/build_intrabar_exit_panel.py` |
| **Replay an ML exit head offline** and score it vs the fixed SL/TP exit | `scripts/research/analyze_exit_head.py` |
| Is an ML-supplemented exit even feasible for a family? | `scripts/research/m20_ml_exit_probe.py` |
| A/B the declared exit levers over full history | `scripts/research/m20_exit_sweep.py` · fleet-wide: `m20_fleet_exit_sweep.py` |
| Trainer-side exit analysis | `scripts/research/m20_exit_analysis.py` |
| Would a **regime-flip** exit have helped? | `scripts/research/m20_regime_flip_replay.py` · fleet: `m20_flip_replay_sweep.py` |
| Drive one (family, tf) exit-head round end-to-end | `scripts/research/m20_exit_head_round.py` |
| **Can a closed trade's exit price be RECONSTRUCTED when the broker fill was never recovered — and how wrong is it?** Hides the known fill on measured rows, rebuilds the exit from 1m klines with the harness's SL-first rule, and reports error in bps against broker truth + per-venue candle coverage | `scripts/research/exit_reconstruction_validator.py` · v2 (decision-time bracket, BE replay, time-consistency stratification): `scripts/research/exit_reconstruction_validator_v2.py` |
| Runner (off-VM) | `.github/workflows/research-exit-head-build.yml` — label `research-exit-head-request` |

**`analyze_exit_head.py` is the answer to "can an ML exit head be backtested?" — yes.** It
trains the take/skip head *and simulates its decisions per trade* (first bar the head says
exit, the trade realizes its mark-to-market R there, net of an exit fee) against the
baseline fixed exit, under CV **grouped by `trade_id`, purged, embargoed, and
uniqueness-weighted**. That is stricter than the plain harnesses in §1.

## 4. Entries

| Question | Tool |
|---|---|
| Where do a leg's entries lose? | `scripts/research/m21_entry_baseline.py` |
| Fleet entry-filter sweep | `scripts/research/m21_entry_sweep.py` |
| Fleet P_win entry-head round | `scripts/research/m21_entry_head_round.py` |
| Component-level edge attribution | `scripts/research/component_edge_report.py` |

## 5. Panels + standing discovery (the large-N substrate)

| Question | Tool |
|---|---|
| Analysis-ready research panel | `scripts/research/build_research_panel.py` |
| Large-N panel from **backtests** | `scripts/research/build_backtest_panel.py` |
| Analyze a panel (standing discovery toolkit) | `scripts/research/analyze_research_panel.py` |
| Sweep panels per strategy / asset class | `scripts/research/sweep_research_panels.py` |
| Runner (off-VM) | `.github/workflows/research-panel-build.yml` |
| **Augment the pooled decision models** — config-exact harness backtests → `is_backtest=1` rows in one `backtest_trades.db` (A1 W1.2; roster verified vs `setup-candidates-metalabel-p2pool-v1.yaml`) | `scripts/ml/backtest_augment_runner.py` (runner: `.github/workflows/research-backtest-augment.yml`) |
| Emit ONE config-exact harness `(strategy, symbol)` as a per-trade JSONL (the shared fetch→`--emit-trades` primitive behind the augment runner + the GLD compat gate) | `scripts/research/regime_debt_matrix.py::emit_trades_for` |

## 6. Portfolio / allocation / conflict

| Question | Tool |
|---|---|
| Cross-symbol allocator backtest | `scripts/research/allocator_multisymbol_backtest.py` |
| Per-candidate (features → forward net-R) dataset | `scripts/research/allocator_candidate_dataset.py` |
| Walk-forward ranker quality | `scripts/research/allocator_ranker_eval.py` |
| Cost of directional conflict between legs | `scripts/research/m26_p0_conflict_bleed.py` |
| Net-R re-grade scorecard | `scripts/research/net_r_regrade.py` |

## 7. Pairs / market-neutral (M22)

| Question | Tool |
|---|---|
| Is a pair still cointegrated? | `scripts/research/cointegration_stability.py` |
| Scan the universe for pairs | `scripts/research/pairs_universe_scan.py` |
| $-and-lots realism | `scripts/research/pairs_dollar_lots.py` |
| Perp-funding drag | `scripts/research/pair_funding_drag.py` |
| Maker-fee economics | `scripts/research/maker_economics.py` |

## 8. Macro / value / events

See [`macro-research`](../../.claude/skills/macro-research/SKILL.md) — it owns this half
(`scripts/macro/`, `src/units/strategies/macro_thesis/`, `comms/macro/`, the macro workflow
cluster). Binding invariants there: off-VM compute, point-in-time / no lookahead,
verify-the-source-before-you-build.

## 9. Robustness / significance gates (run these BEFORE proposing a live change)

| Question | Tool |
|---|---|
| Temporal stability of a directional edge (walk-forward) | `scripts/research/direction_walkforward.py` |
| Rank stability walk-forward | `scripts/research/rank_walkforward.py` |
| Fee/slippage sensitivity + 3-fold robustness | `scripts/research/validate_robustness.py` |
| Monthly net-R correlation / diversification vs the live fleet | `scripts/research/validate_corr.py` |
| Significance + robustness of sweep survivors | `scripts/research/ws_a_s3_significance.py` |
| Fee/commission headroom | `scripts/research/ws_a_s3b_fee_breakeven.py` |
| **Per-account** routing compatibility (mandatory before live routing) | `scripts/prop/account_compat_matrix.py` |
| Would the R4 research→results gate PASS/BLOCK/ABSTAIN a live leg on **measured** (not fabricated) PnL? (observe-only, P0) | `scripts/research/research_results_gate_report.py` (logic: `src/runtime/research_results_gate.py`) |

## 10. Sweep orchestrators

`sweep_wave1_families.py` · `sweep_wave2_momentum.py` · `ws_a_futures_sweep.py` ·
`ws_a_s2_retune.py` · `chop_scalp_study.py` · `session_gating.py` ·
`hf_vectorized.py` + `hf_solo_sim.py` (HF candidates, research-only).

---

## Maintaining this index

It is only useful while it is complete, and a stale index that *looks* complete is worse
than none — that is the failure it was written to fix. So:

- **Adding a script to `scripts/research/`** → add a row here in the same PR.
- **Enforced** by the `research-capability-index` check in
  `scripts/ops/check_research_index.py` (CI), which fails when a script in
  `scripts/research/` appears in neither this file nor its declared-exempt list. The
  exemption list requires a reason, so "not indexed" is always a visible choice.
- **Do not** claim completeness in prose anywhere else. Point here instead. The
  `backtesting` skill's old "every real backtest entry point in the repo" line is exactly
  the sentence that stopped a session from looking further.
