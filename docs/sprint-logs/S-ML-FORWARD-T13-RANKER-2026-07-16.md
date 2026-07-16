# Sprint Log: S-ML-FORWARD-T13-RANKER-2026-07-16

## Date Range
2026-07-16 (single ML-forward research session; continues the
`claude/exit-refinement-sprint-l74k6o` thread).

## Objective
Continue the ML roadmap forward: (1) close out the peak-is-in exit-head E3
graduation question (fully mine it before any Tier-3 flip), (2) take the first
*powered* fc→advisory readiness read, and (3) run **T1.3 — the cross-strategy
learned net-R ranker**, the shared M18/M19 unblocker, to a pre-registered
verdict. All Tier-1 research; nothing touches routing.

## Tier
Tier 1 (research tooling + docs + backlog/roadmap records). The only code merged
(#6632) is under `scripts/research/`. No `config/`, `src/units/`, or order-path
writes; any live change stays an operator-gated Tier-3 proposal.

## Starting Context
Concurrent sessions coordinated via the session board. Prior in-session work
(this branch): peak-head graduation A–D (parser fix #6570, parity, evidence,
E3 draft), fc→advisory readiness eval, and a real-money ADA position flatten.

## Work Completed

### 1. Peak-head E3 — HONEST NEGATIVE (fully mined)
The momentum-exhaustion ("peak-is-in") exit head for `trend_donchian` was fully
mined before any Tier-3 flip. Head-to-head (pooled 1h donchian, 5 folds): the
incumbent `below_half_r` head (128R, beats-actual 4/5) **dominates** peak_full
τ0.8 (103R, 3/5) — the original "beats actual+levers 3/5" was never a comparison
vs the incumbent. By timeframe, 1h passes but 4h (2/5) and 2h-pullback (0/5)
fail. **Decision:** keep `below_half_r` live; peak stays shadow; real-money exit
unchanged. The apply-gate policy-branch enabler (#6589, inert) still shipped as a
correctness fix. The blast-radius parser fix (#6570) restored exit-head soak
visibility to all inspector consumers (live count 0→72). (`MB-20260713-PEAK-HEAD-E3`
resolved earlier in-session.)

### 2. fc→advisory readiness — first powered read (not yet trustworthy on n)
First powered RG4 read taken on a fresh mirror. ETH+SOL 15m fc heads encouraging
but **under-powered** (~26–27 of the 40–50 labeled volatile bars/symbol target;
episode count met). Re-check ~2026-07-20; candidates ETH+SOL. Recorded in
`MB-20260705-FC-ADVISORY-READINESS`.

### 3. T1.3 cross-strategy net-R ranker — RAN, HONEST NEGATIVE
The decisive workstream. Full write-up:
`docs/research/T1.3-ranker-findings-2026-07-16.md`.

- **Tooling (#6632, merged):**
  - `scripts/research/allocator_ranker_eval.py` — `walk_forward` now reports
    **per-fold** OOS AUC (`fold_aucs`/`fold_min`) alongside pooled, honoring the
    "no knife-edge single-fold" gate; plus `MarketRankerModel`, a frozen
    market-only logistic P(win) ranker (fit + serve both funnel through
    `_row_features`/`_MARKET_FEATS` → train/serve parity).
  - `scripts/research/allocator_multisymbol_backtest.py` — new `learned`
    selection arm: contested concurrency slots filled by the frozen ranker's
    P(win). `--ranker-csv`/`--ranker-fit-until`/`--ranker-fit-frac` fit the
    ranker on a chronological prefix and set the backtest `--start` to the split
    so every arm evaluates the identical strictly-later OOS window (leakage-free).
- **Candidate dataset:** 2,334 labelled candidates (BTC+ETH+SOL 5m,
  `allocator_candidate_dataset.py`), all past-only decision-time features.
- **AUC gate PASSED:** walk-forward pooled OOS AUC 0.611 (market-only) → 0.680
  (+owner/cell); per-fold min ≥ 0.55 on **every** stack (market-only
  0.648/0.612/0.555/0.624) — no knife-edge; materially above the 06-30 M18 P1
  ≈ 0.51.
- **Selection backtest FAILED (the decisive gate):** OOS 2024-04-13→now, shared
  `--max-concurrent 2`, sizing-normalized. Arm net PnL — independent −62.5,
  ev (EV scorer) −374.9, shared_priority −546.7, **learned −586.0**. Deltas:
  `learned − shared_priority = −$39` (`learned_beats_priority_net=false`);
  `learned − ev = −$211`. (The rules EV scorer *did* beat priority here, +$172.)
- **Decision:** the learned ranker's real AUC is **between-owner base-rate**, not
  a within-tick cross-symbol selection edge. Keep the rules EV scorer as the
  selector; **M18 P2/P3 stay PARKED**; the learned-ranker-as-selector track is
  CLOSED. `MB-20260629-ALLOC-RANKER` **resolved** (honest negative per its own
  criteria).

### 4. MB-20260701-001 — BTC-15m vol-regime head at a denser label: POSITIVE FIRST-GATE
Operator-approved kick of the highest-value Tier-1 quick win. Full write-up:
`docs/research/MB-20260701-vt004-evidence-2026-07-16.md`.

- **Candidate manifest (#6652):** `btc-regime-15m-lgbm-vt004-pcv-v1` — a
  live-faithful mirror of the shipped `btc-regime-15m-lgbm-v2` advisory head (same
  7 features, same 60d recency half-life) EXCEPT the volatile label is built at a
  lower `vol_threshold` (0.004 requested) and the eval is **purged 5-fold
  walk-forward CV** (the robust decider, not the live head's holdout);
  `target_deployment_stage: candidate` (offline, refused by the shadow factory).
- **Result (purged CV, n_train 175272 / n_eval 87636):** f1_volatile **0.4377** /
  macro_f1 **0.6275** / recall_volatile **0.769** / precision_volatile 0.308 —
  vs the shipped 0.005 head's data-starved ~0.24. **Thesis confirmed:** the 0.005
  head is data-starved; a denser volatile label separates the classes materially,
  without buying it by crushing minority recall.
- **Honest caveat (blocks a clean threshold claim):** the `vol_threshold=0.004`
  build produced a **14.05% volatile base rate** (12314/87636), which matches the
  T1.1 track's *0.003* mapping (0.004→7% there), NOT 0.004 — and the 0.44 f1_volatile
  lines up with T1.1's matched-label LightGBM control at 0.003 (0.444). So the
  specific operating threshold is **not pinned**; the build-param base-rate
  semantics disagree across tracks and must be reconciled first.
- **Decision:** POSITIVE first-gate — pursue a denser operating point for the
  vol-gate head. Live threshold **unchanged (0.005)**. Remaining Tier-3 gates before
  any flip: mapping reconcile → RG4 (`scripts/ml/rg4_targeted.sh`) → vol-gate
  backtest A/B → operator. No config touched. `MB-20260701-001` stays **open**.
- **Trainer ops incident (fixed in-session):** vt004 crawled for ~2h because a
  *terminated* scheduled 5m-cycle (SIGTERM'd 04:16Z) left an **orphaned child**
  (pid 42164) alive 13.5h holding 4.67 GB (77% of the 6 GB box) in swap-thrash,
  starving the box. Cleared via trainer-vm-diag SIGTERM (#6674) — freed 4.6 GB,
  vt004 finished within minutes. Logged the orphan-teardown gap to
  `BL-20260715-TRAINER-CYCLE-MEM-SATURATION` (health backlog).

### 5. Ops
- Confirmed the real-money **ADA** position is flat on `bybit_2` (broker shows
  only ETH+XRP) after the earlier operator-authorized flatten — demotion held.

## Validation
- Both edited research scripts: `py_compile` clean, `ruff check` clean, local
  smoke test (per-fold AUC list, `MarketRankerModel` fit/serve, missing-feature
  → rank-last). PR #6632 CI: all 14 checks green (pytest-run + guards); merged.
- Backlog JSON re-validated (`json.load`) after the minimal Edit — parses,
  item `resolved`, 2 evidence entries.

## Docs Updated
- NEW `docs/research/T1.3-ranker-findings-2026-07-16.md`.
- NEW `docs/research/MB-20260701-vt004-evidence-2026-07-16.md` — vt004 purged-CV
  first-gate evidence (+ the base-rate caveat).
- NEW `ml/configs/btc-regime-15m-lgbm-vt004-pcv-v1.yaml` (#6652) — the candidate probe.
- `docs/claude/ml-review-backlog.json` — `MB-20260629-ALLOC-RANKER` → resolved;
  `MB-20260701-001` evidence_log + status_history appended (positive first-gate,
  stays open pending threshold pin + RG4 + vol-gate A/B).
- `docs/claude/health-review-backlog.json` — `BL-20260715-TRAINER-CYCLE-MEM-SATURATION`
  update: orphaned-cycle-child teardown gap (the vt004 wedge cause).
- `ROADMAP.md` — T1.3 row → ran/negative; allocator-regret soak-clock note updated;
  item-5 BTC-15m quick win → POSITIVE FIRST-GATE (with the base-rate caveat + gates).
- This sprint log.

## Tier-3 Proposals
None. T1.3 closed negative (no routing change proposed); peak-head E3 kept shadow;
fc→advisory awaits a powered read. The rules EV scorer remains the allocator
selector, unchanged.

## Follow-ups / Next
- **MB-20260701-001 (positive first-gate, open):** reconcile the vol_threshold→
  base-rate mapping across the build tracks (0.004→7% vs →14% disagreement), then
  RG4 live-discrimination + the vol-gate backtest A/B before any operator-gated
  0.005→lower flip. First ML pickup item alongside fc→advisory.
- fc→advisory powered re-read ~2026-07-20 (`MB-20260705-FC-ADVISORY-READINESS`) —
  the lead promotion candidate.
- T1.3 reopen condition (not now): a within-tick contrastive target + clean
  per-trade cost labels (`MB-20260629-ALLOC-COSTCAP`) + a net-positive
  opportunity set.
