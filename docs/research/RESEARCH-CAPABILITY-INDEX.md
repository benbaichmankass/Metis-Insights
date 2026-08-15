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
| How does a Donchian/trend leg perform? | `scripts/backtest_trend.py` — the **one** trend engine (43 flags; its trail freezes the ENTRY bar's ATR, which is what `trend_donchian.monitor()` does) and the one `regime_debt_matrix` runs. There used to be a second, non-live-faithful copy at `scripts/research/backtest_trend.py`; its 15 lever flags were ported here (PR #8633) and it was **retired to a hard-fail shim 2026-08-09**. History: design-doc §5f + `BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE` |
| **Is `trend_donchian`'s live trail-decay lever still justified?** Config-exact `(arm_r, tight_mult)` grid + a lever-OFF arm on the CONVERGED engine, IS/OOS + per-year folds; reports whether the lever is INERT (identity vs OFF, and whether `max_mfe_r` even reaches the arm) before ranking cells | `scripts/research/m20_trail_resweep.py` (`BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL`) |
| **Why does M20's coverage roll-up over-count `shipped`, and how were the bundled rows split?** One-shot migration that explodes the multi-leg coverage rows into one row per leg, assigning per-leg status ONLY from each ref's explicit wording (silent, or a run that ERRORED/TIMED OUT, becomes `pending` — an un-run cell is not a negative). Asserts row count and leg-uniqueness, and refuses to run against an already-exploded file rather than corrupting it | `scripts/research/m20_explode_coverage_rows.py` (`BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS`) |
| **How many trades does a proposed trail-decay retune actually TOUCH?** The denominator behind the sweep's aggregate: trades that ARM the lever (`mfe_r >= arm_r`) and, of those, which exit differently between two `tight_mult` values — with the per-trade R deltas, so a headline is traceable to the trades that produced it. Three-way verdict (`INERT` / `TOO_THIN` / `MEASURABLE`) so "no evidence" never reads as "no difference". Arms joined on `entry_time`, never zipped positionally | `scripts/research/m20_trail_attribution.py` (`BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL`) |
| **A banking cell says `honest_negative` — WHICH kind of negative is it?** Reads a sweep's existing `verdicts.json` and reports what the net_R+maxDD gate discards: **MAR** (`net_R/maxDD`) and **DD/R** (drawdown removed per unit of net_R surrendered), beside `banked_pct` (the rung-fill denominator — a rung that never filled is INERT, not a negative). `honest_negative` covers at least three different objects: a return-for-smoothness trade, a **both-axes loss** (measured on 3 of 4 ict_scalp 5m legs, 2026-08-10), and an inert rung; the gate reports all three identically. **Changes no gate, ships nothing** | `scripts/research/m20_banking_risk_adjusted.py` (`BL-20260810-BANKING-GATE-CANNOT-PASS`; memo §10.6/§11) |
| **Is a second trend engine creeping back in?** Convergence guard — fails when any other `backtest_trend.py` exposes an engine entry point, and names the flags it declares that the canonical engine does not. Carries the retired fork's measured divergence as the record | `scripts/research/trend_harness_divergence.py` (CI: `trend-engine-convergence-guard`) |
| **Did the capital EARN ITS KEEP while it was committed?** — `net_R per position-day` and `net_R per capital-day`, the exit-refinement gate's declared-but-never-implemented tiebreak. A trade reaching TP after 149 bars scores identically to one reaching it in 10 under a net_R+maxDD gate; only this separates them (operator directive 2026-08-10, off a real-money `eth_pullback_2h` held 149 bars on a 2h strategy). Bar length is **measured from the frame's timestamps**, never the `--timeframe` label; a value it could not compute is **None, never 0.0** (an undefined rate must not rank alongside a flat one). `capital_bars` is SIZE-WEIGHTED so a partial-TP release is credited its shorter hold — measuring banking on unweighted hold would score it identically to doing nothing. **The ONE definition** (used by `backtest_ict_scalp.py` + `backtest_pullback.py`; a second copy would make cross-harness comparison meaningless, the `candle_io.py` lesson) | `scripts/capital_efficiency.py` (`BL-20260810-NO-STALL-EXIT-CAPITAL-SITS-IN-DEAD-TRADES`) |
| **Is a Path B base-rate FLOOR supported by the data, or would it be a guess?** Path A's derived allowance (`allowed = base_maxDD x (dNetR / base_netR)`) gets more permissive the WEAKER the base book — `eth_pullback_2h vt_cold10_t2.5` cleared with +43.59R of headroom on a book earning 6.62R against a 16.41R drawdown (rate 0.40). A floor would fix that and re-introduce the free parameter the derivation removed, so this tests whether the rate actually predicts fold-generalisation before anyone sets one. **Three verdicts, never collapsed:** `insufficient_population` (we did not look) · `no_separation` (we looked, it does not predict) · `separation` (floor + effect + p). Scanning K floors is selection over an unstated denominator, so K is printed and a `separation` must clear a **Bonferroni** bar; alpha is surfaced as the one CHOSEN number. Recommends nothing it cannot support | `scripts/research/m20_path_b_floor.py` (operator directive 2026-08-10: *"database decisions and not arbitrary guesses"*) |
| **Where does the sweep evidence GO between runs?** Flattens `verdicts.json` into an accumulating, versioned per-cell corpus. Before this, every sweep's evidence went to an Actions **artifact no session can download** (`CLAUDE.md` § PM-side capabilities) with a top-30 slice in the PR comment — so each run restarted the population from zero and no threshold could be derived from more than one sweep. Legs that produced NOTHING (harness error, skipped) get rows too: they are part of the fleet denominator. Re-extracting a run **supersedes** its rows rather than appending, so a post-fix re-run does not leave the pre-fix vintage in the population | `scripts/research/m20_corpus_extract.py` → `docs/research/m20-sweep-corpus.jsonl` |
| **A corpus row says `tp_cap_pct: null` — was that run UNCAPPED, or did it just not record the field?** The two are opposite claims and the corpus cannot tell them apart, because `tp_cap_pct` is in `measurement_key`: a mislabelled row keys as a separate geometry and never supersedes its true counterpart. Diagnosed for the 140 affected rows by **dating the code from each run's head SHA, never from when the change reached `main`** — the runs were `event: push`, so `inputs` was empty and `TP_CAP_PCT` fell to the workflow default `0.099`; the two bracketing runs differ only in whether their sweep script wrote the field (`d76643b9` no, `e6e519d1` yes, ~8 minutes apart on the feature branch while `main` saw it a day later). Fills **only** the cap: `min_oos_trades_floor` stays null because null is the TRUTH for a run that predates the floor, and the relabel therefore collapses nothing | `scripts/research/m20_corpus_relabel_tp_cap.py` (dry-run by default; refuses rather than guessing when the corpus does not match its stated preconditions) |
| **Two ETH legs passed the exit-head E1 gate — is that the SYMBOL, or the BOOK SIZE?** Gives the ETH hypothesis a denominator instead of two positives (operator decision (a), 2026-08-14) and inverts it: over the 20 of 36 resolved `exit_head_ml` cells whose ref states an `n_oos`, `is ETH` classifies the verdict at 80.0% while a single `n_oos >= 350` split reaches 90.0%, and holding book size fixed the symbol adds nothing (large-book stratum: ETH 2 of 2, **non-ETH 4 of 5**). What is left of the effect is ONE cell. The point is about the GATE: E1 needs two-thirds of folds on the right side, per-fold noise falls as trades-per-fold rises, so a gate 90% predicted by book size is substantially a **power test** — and a thin-book `honest_negative` is nearer *underpowered* than *the head does not help*, which the status vocabulary cannot say. Prints its population, its mixed geometries, and that the 350 split is fitted on the same sample (so 90.0% is an upper bound), and reports the one internally-clean live-parity stratum separately | `scripts/research/m20_exit_head_denominator.py` (`BL-20260814-EXIT-HEAD-E1-GATE-IS-90-PERCENT-PREDICTED-BY-OOS-BOOK-SIZE-NOT-SYMBOL`) |
| **How long does a live trade actually wait between exit evaluations, and would decoupling the monitor fix it?** The per-hook tick split (`/api/diag/tick_cost`) answers both. Measured 2026-08-10 over 18 ticks: the tick costs **104s mean / 125s max** against the operator's 60s ask, split ~50/50 between signal generation (51.7%) and the order monitor (46.8%), with `attributed_pct` 98.5 so every other hook is ~1.5% COMBINED. The decouple is therefore **necessary AND barely sufficient** — the monitor's own 52.8s peak becomes the cadence floor, 13% under the target on an 18-tick sample — so it must ship with a budget on the monitor's own runtime, whose VALUE needs a soak rather than this reading | `/api/diag/tick_cost` → `docs/research/M20-exit-monitor-decouple-evidence-2026-08-10.md` |
| Load OHLCV (CSV / Parquet / **JSONL**, `ts`→`timestamp`) for a research script | `scripts/candle_io.py` — `load_candles` / `resample_ohlcv`, lifted verbatim from the retired engine and now **the one reader**: `scripts/backtest_trend.py::_load_candles` delegates to it, so JSONL (what `build_continuous_contract.py` writes) works again (`BL-20260809-TWO-CANDLE-READERS-DIVERGE-ON-JSONL`) |
| A pullback leg? | `scripts/backtest_pullback.py` |
| A TTM squeeze (BB-inside-KC) leg? | `scripts/backtest_squeeze.py` |
| A fade/failed-breakout leg? | `scripts/backtest_fade.py` |
| ict_scalp? | `scripts/backtest_ict_scalp.py` — ⚠️ **gross only, no fee model** (`BL-20260610-M15-1`) |
| vwap? | `src/backtest/run_backtest_vwap.py` |
| The whole multi-strategy system on one clock? | `scripts/backtest_system.py` |
| Does our backtest actually reproduce the LIVE trade distribution (is a leg's backtest TRUSTWORTHY)? | `scripts/research/backtest_fidelity_calibrate.py` — the backtest↔live agreement gate (win-rate + KS on realized-R over measured-provenance live trades), verdict `calibrated`/`drifts`/`insufficient-live`; the earned-trust linchpin of the Faithful-Backtest Platform (`docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md`) |
| Does adding funding+slippage move a leg's backtest↔live agreement toward the gate (P1 cost attribution)? | `scripts/research/backtest_fidelity_cost_ab.py` — execution-realism A/B: derives fee-only vs fee+slippage+funding arms from one cost-on emit (identical trade set), calibrates each against the measured-provenance live journal, reports the KS/win-rate delta + a direction-stratified cost-on agreement (`docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md` § 3.B/§ 5a) |
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
| **Where ARE all the fold-dispersion arms, and how do I get them into one file?** Walks the trainer's `runtime_logs/m20_exit_head/` tree and emits one consolidated record. Exists because the study's headline was **22% machine-readable** — 6 legs committed against a screened denominator of 27, the rest prose-only. Stamps the `fold_offset` the rows cannot state for themselves (`BL-20260815-EVIDENCE-ROWS-DO-NOT-RECORD-FOLD-OFFSET`) from each arm's `round_report.json`, keeping `offset_source` and a separate `dir_offset` so the two can be cross-checked instead of one standing in for the other; an unreadable report yields a **null offset, never a defaulted 0**. Refuses to write an empty record (an empty file that exists reads as a measured negative). RUNS ON THE TRAINER; transfer the output **compressed and hash-checked** — a plain-text emit was silently truncated at 137 of 234 rows | `scripts/research/m20_consolidate_dispersion_arms.py` → `docs/research/m20-fold-dispersion-arms-consolidated.jsonl` |
| **How much does an E1 verdict depend on WHERE the fold boundaries fall — and what is the mover rate?** Derives the fold-dispersion headline from the consolidated arms record instead of prose (the doc's `per_leg` denominator had already gone stale at 2 where the data says 3, and a power calculation was built on it). Prints **both** dedup rules because they differ by 7.4 points — any-screen 9/27 = 33.3% vs every-screen 7/27 = 25.9% — and naming only one silently picks a side; states the denominator including the 14 single-arm pairs EXCLUDED (a leg measured at one offset cannot move); and names the legs whose **mover verdict itself** disagrees across screens (2 of 22), so the rate is read as one draw of a statistic that has its own dispersion | `scripts/research/m20_dispersion_rate.py` → reads `docs/research/m20-fold-dispersion-arms-consolidated.jsonl`; pinned against the doc by `tests/test_dispersion_rate_matches_the_doc.py` |
| **A committed corpus is missing a field the producer now stamps — how do I backfill it WITHOUT inventing values?** Adds `fold_offset` + `fold_offset_basis` to the 33 rows of `m20-exit-head-rounds.jsonl` written before the driver stamped it. The offset is **ESTABLISHED, not defaulted**: `--fold-offset` landed in `43820a32` @ 2026-08-14T23:49:18Z and every row predates it, so each was produced by a driver whose argparse would have *rejected* the flag — the unshifted partition is therefore derived. 28 rows dated from the round-dir timestamp; the 5 naming only a relay were dated by **reading the GitHub API**, not inferred from issue numbering. A row that cannot be dated, or one dated *after* the flag, records `null` + `unavailable` — never 0, because "we did not record it" and "it was the control arm" are different facts and collapsing them is the defect that opened the row. Idempotent (a row already carrying the key is untouched, so a measured value can never be overwritten by an inferred one) and refuses to rewrite an empty file. Read it as the worked example for the general shape: **backfill only what the code's own history proves, and give the inference its own provenance field** | `scripts/research/m20_backfill_corpus_fold_offset.py` → rewrites `docs/research/m20-exit-head-rounds.jsonl` (`--write`; dry-run by default) |
| **What IS the M20 coverage number, and how many cells still block the milestone?** The one place the headline is derived — three sessions hand-counted three different figures (319/311/304) for an unchanged file. Reports all three cuts and names each; `--done-condition` lists the blocking cells (**61**, not 57 — the headline counts `blocked` as closed and the done-condition does not); `--check` is the CI guard | `scripts/research/m20_coverage_rollup.py` |
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
