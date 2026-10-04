# Decision brief — Tier-3 calls sitting on `main`, 2026-10-04

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · the register's machinery has no rule that can verify a decision pack, so its row reads the honest machine state; the verification this lane did is stated in § F
>
> Lane DECISION-BRIEF (session_01XGZZ1RZsfiUwKap24xMV4v), dispatched by Manager Session 2026-10-04 (session_01MM8o5js6TcDFeNAPBY4Ntv). Tier-1, docs only. **No config was changed.** Every number below was re-read from the record named beside it on `main` at `786352e8`, not from the prose about it; where a review's figure was NOT re-measured this session it is marked *(review's figure, not re-pulled)*. Classification follows `.claude/skills/manager/SKILL.md` § "Before any operator popup: classify the decision" — a popup is earned only by a genuine preference.

## The one-page answer

Of everything the dispatch listed, **five items are DECIDE-NOW** (all ML-lifecycle, all answerable in one sitting, all low-stakes — nothing live reads the heads involved). **Zero strategy-config changes are decision-ready.** Every strategy item is either already shipped (`ief_pullback_1d` trail_decay, #15593), covered by a granted mandate, or missing data — and for each missing-data case the data task is now a pipeline row.

| # | item | verdict | one-line recommendation |
|---|---|---|---|
| D1 | `setup-quality-audit-baseline-v0` shadow → candidate (soft-off) | **DECIDE-NOW** | Yes. Structurally degenerate predictor; nothing live reads it. |
| D2 | One MES regime head or two (MB-20260721) | **DECIDE-NOW** | (b): keep the 5m advisory head; demote the 15m shadow twin to candidate. |
| D3 | `prop-mission-policy-baseline-v0` (label degenerate, 0 of 267) | **DECIDE-NOW** | `awaiting_source` waiver (not retire) while Breakout execution is re-routed. |
| D4 | Four demoted heads: BUILD the signal-time producer or KILL | **DECIDE-NOW** (per head) | BUILD `conviction-meta-v1` (values already computed); setup-quality-lgbm-v2 is a Tier-1 builder fix, not a decision; KILL the two btc-regime heads. |
| D5 | MES baselines silent since 09-01 | **NEEDS-DATA → ML-HYGIENE** | Not a preference: find why MES scoring stopped. Row already exists. |
| M1 | RQ-20260929-106 SPY/QQQ pairs 1h — FAIL, reproduced | **MANDATED (MD-KILL-QUESTION)** | Manager closes with `terminal_reason`; no popup. |
| M2 | `ief_pullback_1d` `decay_stall10_t2.5` | **ALREADY DECIDED** (#15593 merged 2026-10-03) | Nothing to ask. Closes on first live trail_decay exit observed. |
| N1 | `trend_donchian_xrp_4h` trail4 (+ decay_stall6) | **NEEDS-DATA** | 1 of 3 partitions; monthly re-sweep rows exist (one has a broken rerun — filed). |
| N2 | `trend_donchian_xrp_4h` Stage-0 p10 sign flip (n=16) | **NEEDS-DATA** | 730d Stage-0 rebuild with a pre-registered rule — filed. Not a popup. |
| N3 | `ief_pullback_1d` `vt_hot80_t2.5` (passed twice, unshipped) | **NEEDS-DATA** | Observe decay_stall10 live + vol_trail P5 parity first — filed. |
| N4 | `ada_pullback_2h` (1/11 lifetime wins, real money) | **NEEDS-DATA** | Tune-before-demote: run the M8 sweep — filed. |
| N5 | RQ-20260929-105 GLD/GDX pairs 1d — Stage-0 PASS | **NEEDS-DATA** | Confirmatory IS/OOS walk-forward unit before any executor-build question — filed. |
| K1 | `ief_pullback_1d` `rrfloor0.75` | **KILL (recommend)** | Inert on every fold-examined year; the gain is pre-2021 and never fold-tested. |
| K2 | Breakout prop shortlist (P2 round 2) | **KILL as a decision item** | 0 of 49 cells PASS. Nothing transfers to Tradeify/Velotrade; only the screen method does. |
| K3 | Regime readout (RQ-20260930-701/702/703/704) | **NO DECISION** | Four nulls/fails; REGIME-SCOPE closes on 703, which has landed. E76 continues per the 09-27 decision. |

---

## A. DECIDE-NOW — answer these in one sitting

All five come from `comms/reviews/ml-review-20261004-125000.json` (PR #16337), `decisions_for_operator`. Stages below are the review's `model_status` block *(review's figure, not re-pulled from the registry this session)*. None of these heads influences a live order today; the only order-influencing MES head (`mes-regime-5m-lgbm-v2`, advisory) is untouched by every recommendation.

### D1 — `setup-quality-audit-baseline-v0`: shadow → candidate (soft-off)
- **Exact change:** trainer registry stage `shadow` → `candidate` for `setup-quality-audit-baseline-v0` (`python -m ml promote-stage ... candidate` on the trainer, via `trainer-vm-diag`). No config file in this repo changes.
- **Evidence:** review `promotion_recommendations[0]`: 1,508 live rows 09-26..10-04 all equal −0.05719596 (`/api/bot/shadow/stats`); code: `PerGroupPredictor` keys on `trainer_config.feature_column = audit_pattern`, which is absent from every served row → `unknown_bucket` → global rate. Structural, not variance. Drift KS 0.0 / PSI 0.0 is the degenerate-predictor pass BL-20260911 names as a gate defect.
- **Pre-registered rule:** none applies — this is a mechanics finding (served feature absent), not an edge claim.
- **Classification:** bucket 1 (data-settled, de-risk). It is listed here only because the review phrased it as an operator decision; the manager may also just do it under the standing authorization and notify.
- **Recommendation:** **Yes, soft-off.** Still trains daily; promote back if a signal-time `audit_pattern` proxy is ever served. If the operator instead wants it kept as a fixed reference denominator, say so and tag it so reviews stop re-finding it.

### D2 — One MES regime head or two (MB-20260721)
- **Exact change:** `mes-regime-15m-lgbm-v2` registry stage `shadow` → `candidate`. The advisory `mes-regime-5m-lgbm-v2` stays.
- **Evidence:** review `decisions_for_operator[2]`: 5m advisory macro_f1 0.7023 vs 15m shadow 0.7015, same label. The review's own caveat: all MES 5m heads moved on the 10-04 rebuild (0.7132 → 0.7023 for the advisory head) — PI-20261004-AHCVAAF5-0001 re-checks the margin in 3 days.
- **Classification:** bucket 1 leaning 3 (the twin adds nothing on the data; whether to keep a spare is a preference).
- **Recommendation:** **(b) — one head.** Demote the 15m twin to candidate; it keeps retraining daily at no cost and can be re-staged if the 5m margin does not recover on AHCVAAF5-0001.

### D3 — `prop-mission-policy-baseline-v0`: waiver or retire
- **Exact change:** either mark the manifest `awaiting_source` (same waiver as `mes-execution-quality`) or retire it.
- **Evidence:** review `decisions_for_operator[3]`: label degenerate, 0 of 267 missions taken; stage `candidate`, last trained 2026-07-26. Row PI-20260929-UAOAYGBB-0001 (routed).
- **Classification:** bucket 3 — both courses are supportable.
- **Recommendation:** **Waiver, not retire.** The label source is Breakout mission intake; the manager says the Breakout account is retired for now and automation is being re-routed (PROP-TERM closed the VM route 2026-10-04; PHONE-EXEC-DESIGN is next). A waiver costs nothing and keeps the manifest; retire only if the prop route is itself killed.

### D4 — Four demoted heads: BUILD the signal-time producer, or KILL
Rows PI-20260929-K85Q64SF-0001..0004, all `routed / ask_operator`. Each head was demoted shadow → candidate on 2026-09-29 because declared features are never served at signal time. Default if silent: they stay candidate and retrain daily at no cost. Per head:

| head | served / declared | missing | recommendation |
|---|---|---|---|
| `conviction-meta-v1` | 3 / 10 | `c_strat c_setup c_wr c_reg adx_14 regime vol_regime` | **BUILD.** The c_* lens values are already computed by the observe-only conviction stamp; the gap is writing them into the shadow row (review `experiments_proposed[2]`, Tier-2 runtime PR). Then a fresh 7-day shadow soak with `drift_clean` and `live_parity` before any promotion talk. |
| `setup-quality-lgbm-v2` | 5 / 9 | `account_id bias dayofweek hour_of_day`; cannot retrain for 70 days (killzone/bias 100% NaN at build) | **Not an operator decision.** It is a Tier-1 dataset-builder fix (`ml/datasets/families/setup_labels.py`, review `experiments_proposed[0]`), routed to ML-HYGIENE (queued checklist row). Re-stage after it retrains and gates. |
| `btc-regime-1h-lgbm-funding-svble-v1` | 7 / 10 | `funding_rate` family (n=411) | **KILL** unless a signal-time funding feed is wanted for another consumer. Two other BTC regime heads exist; nothing live reads this one. |
| `btc-regime-5m-lgbm-flow-v1-offload` | 7 / 13 | `ofi vpin order_imbalance rel_spread_mean microprice_dev` (n=844) | **KILL.** A microstructure producer at signal time is the largest build of the four for a head with no consumer. |

- **Classification:** bucket 3 (spend on a producer build vs not) for conviction-meta and the two btc heads; bucket 2 for setup-quality-lgbm-v2.
- **What a KILL means:** the manifest is retired with the reason recorded on its row; nothing live changes.

### D5 — MES baselines (`mes-setup-quality-baseline-v0`, `mes-trade-outcome-winrate-baseline-v0`)
- The review asks "retire to candidate, or confirm MES setup scoring is expected to resume". Row PI-20260929-K85Q64SF-0005 (routed, ask_operator): 341 predictions each, all archived, 0 in the active log since the 2026-09-01 rotation; the registry declares no feature columns for either, so the served-vs-declared test does not apply.
- **Classification:** bucket 2, not 3. "Why did MES scoring stop" is a diagnostic with a definite answer, and the row's own `clears_when` already says so. **Not a popup.** Route to ML-HYGIENE with the other MES items; the operator decides nothing until the cause is known.

---

## B. Covered by a granted mandate — the manager acts, no popup

### M1 — RQ-20260929-106 SPY/QQQ pairs 1h: FAIL, reproduced
- **Record:** `research/results/RQ-20260929-106/37188717729.jsonl` and `.../37205970522.jsonl`: net_total_r **−121.96R**, n **178**, win 31.5%, maxDD 126.0R, 2024-10-14..2026-10-02, venue-aware costs (`scripts/backtest_pairs.py` resolves slippage/funding per leg through `execution_costs.resolve_cost_policy`; funding 0 for equity legs).
- **Pre-registered rule:** `RULE-RQ0929-106-SPY-QQQ-PAIRS-STAGE0` (registered 2026-09-29): FAIL if net_total_r ≤ 0 with n ≥ 39; first FAIL re-queues for a confirmatory run; second FAIL closes.
- **Caveat the manager should write into the terminal reason:** the second run is the same data window as the first (identical numbers to four decimals). It proves the harness reproduces, not that an independent sample fails. The rule as registered does not require a new window, so it is satisfied as written.
- **Mandate:** `MD-KILL-QUESTION` (granted): a unit that fails its pre-registered rule is closed with a stated `terminal_reason`. **The queue file still reads `status: queued`** — the records carry the generic `RULE-E7-HARNESS-DISPATCHABLE / no_action_warranted` stamp instead of the unit's own rule (defect PI-20261004-FOJGFIZF-0004, owned by RESEARCH-PIPELINE-REPAIR), so the mechanical grader never applied the rule. Close it by hand under the mandate.

### M2 — `ief_pullback_1d` `decay_stall10_t2.5`: already shipped
- #15593 **merged 2026-10-03T18:23Z** (GitHub, re-read this session). `config/strategies.yaml::ief_pullback_1d` on `main` carries `trail_decay_stall_bars: 10` and `trail_decay_tight_mult: 2.5` (field, read this session). `alpaca_live` and `alpaca_portfolio` both roster the leg, so the mirror invariant holds.
- Evidence it shipped on: corpus run 2026-10-02 (split 2015-12-10) base OOS n=39, dNetR IS +7.24 / OOS +0.705, OOS maxDD −5.45 vs base; WF 4/6 (2024, 2025 negative); replicated 2026-08-15 (split 2017-01-20, n=36, WF 4/6). Approved by the manager under the standing authorization.
- **Merged ≠ deployed ≠ observed.** The M20-EXITS checklist row closes on the first ief exit driven by trail_decay being read from the live journal. Nothing to decide; one thing to watch.

---

## C. NEEDS-DATA — the data task is filed; no popup

### N1 — `trend_donchian_xrp_4h` trail4 and decay_stall6_t2.5
- **Proposed change (not applied):** `config/strategies.yaml::trend_donchian_xrp_4h.trail_mult` 5.0 → 4.0 (#15594, closed unmerged by the manager on the lane's own recommendation).
- **Record (`docs/research/m20-sweep-corpus.jsonl`, re-read):** on the 2026-10-02 partition (split 2025-01-01, base OOS n=49, IS n=95) trail4 reads PASS: dNetR IS +2.11 / OOS +0.358, WF 6/6; decay_stall6_t2.5 PASS: IS +0.86 / OOS +0.318, **WF 6/6** (the pipeline row says 4/6 — the field says 6/6). On the two earlier partitions (2026-08-10 split 2025-07-01 n=32; 2026-08-15 split 2025-06-16 n=34) **both cells are `is_oos_fail` with negative IS deltas.** 1 of 3 partitions. Base OOS n=49 sits at the exit-refinement floor's upper neighbourhood (MIN_OOS_TRADES = 25 passes; the row's "power floor 49.06" is a per-unit power figure, not the skill's floor).
- **Why not decision-ready:** a pass that appears only when the split moves is the fragile case the skill warns about, and the P5 live-parity check has not run.
- **Filed:** PI-20261003-84ZIQNJD-0001 (monthly re-sweep) and PI-20261004-FOJGFIZF-0001 already exist. **FOJGFIZF-0001's `origin.rerun` is broken** — it reads `wf_wins_effective`, a key the corpus does not have; run this session it raises `KeyError`. Filed as **PI-20261004-P24XMV4V-0005** (kill-and-refile, or merge into 84ZIQNJD-0001).

### N2 — `trend_donchian_xrp_4h` Stage-0 p10 sign flip
- PI-20261002-84ZIQNJD-0001 (queued, `ask_operator`) asks whether to refresh the committed Stage-0 record for this **real-money bybit_2 leg**, whose p10 flips negative on a 3-day-newer 365d window at **n=16**; a control rebuild at the committed `trail_mult 5.0` gives the identical net_r_oos 5.94, so it is the window, not trail4.
- **Classification:** bucket 2. n=16 is below every floor in use (`MD-PROMOTE-S0-S1` bar `min_n_closed 30`; exit-refinement MIN_OOS_TRADES 25). The record cannot decide either way, so the question is not a preference.
- **Filed: PI-20261004-P24XMV4V-0004** — a 730-day Stage-0 rebuild on the committed config with the rule registered before the run (net of the full cost stack, n ≥ 30, fold majority). If it fails the four-clause bar, `MD-DERISK-ONLY-ROSTER-CUT` covers the cut without a popup; if it passes, the pin is re-stated from the new record and the SIGNAL-0005 pricing is re-stated to the operator as a report.

### N3 — `ief_pullback_1d` `vt_hot80_t2.5` (vol_trail)
- **Record:** PASS on two partitions — 2026-08-15 (split 2017-01-20, n=36): dOOS +3.41, WF 5/6; 2026-10-02 (split 2015-12-10, n=39): dOOS +2.24, WF 5/6. But the 2024, 2025 and 2026 folds read exactly 0.0: the lever did not fire in the last three years of the fold-examined window.
- **Why not now:** one lever per leg at a time (combos only after singles pass, M20 finding); the shipped decay_stall10 is not yet observed live; and `vol_trail` has no P5 live-parity record for any leg (ada_pullback_2h's log-only shadow log, PI-20260930-KZWVN6MG-0001, is the same instrument and still unobserved).
- **Filed: PI-20261004-P24XMV4V-0002.**

### N4 — `ada_pullback_2h` (real money, bybit_2 + bybit_portfolio)
- `comms/reviews/performance-review-20261004-103845.json` (#16284) proposes **no Tier-3 change** (`proposed_tweaks: []`). It reads the leg at 1/11 lifetime wins, expectancyR −0.70, full pnl coverage *(review's figure from `/api/bot/performance`, not re-pulled)*, mirror −$4,335 over 32 on bybit_1.
- **Rule that binds:** `docs/CLAUDE-RULES-CANONICAL.md` § "Tune before demote" — no M8 sweep artifact exists on `main` for this leg (checked: zero `runtime_logs/strategy_tunes/*/ada_pullback_2h__*.json`), so the only admissible proposal is **run the sweep**.
- **Filed: PI-20261004-P24XMV4V-0001.** Separately, the leg's vol_trail cell (#14053) stays HELD on PI-20260929-AQRK6CL1-0012 (P5 parity + fresh-holdout re-grade) — also NEEDS-DATA, rows exist.

### N5 — RQ-20260929-105 GLD/GDX pairs 1d: Stage-0 PASS, and then what
- **Record:** `research/results/RQ-20260929-105/37188716030.jsonl`: net_total_r **+18.72R**, n **144**, win 62.5%, maxDD 6.38R, expectancy +0.13R/trade, 2016-10-06..2026-10-02, venue-aware costs. Against `RULE-RQ0929-105-GLD-GDX-PAIRS-STAGE0` (PASS if net_total_r > 0 and n ≥ 20) that is a PASS — read by hand; the record's own verdict field says `no_action_warranted` (FOJGFIZF-0004 again) and the unit still reads `status: queued`.
- **What a PASS could lead to:** the unit proposes no live leg; the only downstream action is an alpaca pairs executor (`pairs_executor.py` is bybit-only by construction) — a separate, larger Tier-3 build. The sibling SPY/QQQ read failed hard, so the mechanism does not generalise across both ETF pairs.
- **Why not a popup yet:** a single pooled 10-year run on harness defaults with no IS/OOS split and no walk-forward is not the evidence standard a build decision should rest on, and the harness can produce that evidence for free.
- **Filed: PI-20261004-P24XMV4V-0003** — a confirmatory GLD/GDX unit with a pre-registered IS/OOS + walk-forward rule. If it passes, the executor-build question goes to the operator as a bucket-3 preference with both pairs reads attached.

---

## D. KILL — recommended dispositions, nothing to ask

### K1 — `ief_pullback_1d` `rrfloor0.75`
- Corpus 2026-10-02: dNetR IS +0.91 / OOS +2.09, WF "6/6" — but **every fold 2021–2026 reads d_net_r exactly 0.0 and d_max_dd 0.0**, counted as `ok`. The lever never fired in any fold-examined year; the OOS gain sits entirely in 2015–2020, which no fold examined (`tests/test_corpus_fold_coverage.py` pins that gap). This is the "ΔmaxDD 0.0 is that cell announcing itself" case in the exit-refinement skill. Not shipped by the lane; recommend it stays unshipped and is not re-swept.

### K2 — Breakout prop strategy shortlist (P2 round 2)
- `docs/research/p2-breakout-matrix-2026-10-01.md`, results 2026-10-02: **49 of 49 cells graded, 0 PASS** (31 FAIL at Gate A, 1 underpowered, 17 NULL at Gate B). Nearest miss ZECUSD trend 1h under `room033`, P(net>0) 0.68–0.70 on 5 seeds, half-split +8.1 / +32.7 — one of 49 screened, no multiplicity correction. The lane proposed no roster PR.
- **Does it still matter for Tradeify or Velotrade?** No, as a shortlist: there is nothing on it. `tradeify_1` is rostered on `main` as a DXtrade account carrying the same two prop legs as `breakout_1`, and Velotrade is DXtrade too (prop-firm scan, 2026-10-04), so the *method* transfers — `p2_breakout_symbol_screen.py` + `prop_ev_grid.py --costs <firm>` — but every cell would need re-pricing under that firm's commission/swap/spread and re-screening against that firm's symbol list. The Velotrade deep-dive (PI-20261004-BNWMXSJF-0001, due 2026-10-11) should capture both inputs so the screen can be re-run; no new row is needed.
- **Field note:** `config/accounts.yaml` still rosters `breakout_1` (`mode: live`, 2 legs). The dispatch says the account is retired for now; this brief changes no config. If "retired" is meant to hold, the roster should say so — that is a Tier-3 roster edit for the manager, not a research decision.

### K3 — Regime readout: no decision to make
- No readout memo exists; the records do. `research/results/RQ-20260930-701` (trend-axis headroom, 22 crypto legs, 1,764 test-block trades, 300 seeds): **fail**, `no_headroom_trend_axis`. 702 (pooled Donchian, E38 method, n=500 after dedup): **fail**, `no_regime_signal`. 703 (conservative selection arm, same 1,764): **fail** — so the null is the axis, not the selection rule. 704 (vol axis): FAIL, power-bounded (per the REGIME-SCOPE row; 673 < 785, not re-read here). E38 (2026-09-25) found 0 of 22 crypto legs regime-explained.
- REGIME-SCOPE was open on exactly 703's verdict, which has landed (PI-20261001-1MX1RCZS-0002, routed). The operator's 2026-09-27 decision on E76 (soft regime weight / recombination lines) stands and is in flight. **No Tier-3 regime flip is supportable on these records.**

---

## E. Also on `main`, outside the five inputs

- **#16269 — SOL prop bracket `tp_r 2.0 / atr_stop 3.5` (Tier-3, HELD).** PROP-EVIDENCE-2 is running the pre-registered confirming test (out-of-sample walk-forward). Reaches the operator only if it passes — correctly classified already.
- **RESEARCH-PIPELINE-REPAIR owns** the grader defect that makes both pairs units read `no_action_warranted` (FOJGFIZF-0004). Until it lands, every research-harness-dispatch verdict must be read against the unit's own rule by hand, as this brief did.

## F. Method and population

- Corpus: `docs/research/m20-sweep-corpus.jsonl`, 1,416 rows; the 2026-10-02 re-sweep is 48 rows over 3 legs (`iaum_pullback_1d`, `ief_pullback_1d`, `trend_donchian_xrp_4h`): 27 `is_oos_fail`, 16 `insufficient_base` (all iaum, OOS n=4), **5 PASS** — 3 on ief, 2 on xrp. The dispatch said "4 PASSes"; the field says 5.
- Walk-forward "ok" counts a zero-delta fold as a win (`m20_fleet_exit_sweep.py`: PASS at `wf.usable >= 4` and the ≥ 2/3 tally), which is why K1 and N3 are read fold-by-fold here rather than from `wf_wins`.
- Not re-pulled this session: the trainer registry (ML stages), `/api/bot/performance` (ada_pullback_2h lifetime), and `/api/bot/shadow/stats`. Those figures are the two reviews' own, dated 2026-10-04, and are marked as such above.
- Pipeline rows filed by this lane: PI-20261004-P24XMV4V-0001 … -0005 (`docs/claude/work/pipeline/`, `python3 scripts/ops/pipeline.py --check` clean apart from the four pre-existing grandfathered collisions).
