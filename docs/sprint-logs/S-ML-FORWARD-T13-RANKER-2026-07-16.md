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

### 4. Ops
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
- `docs/claude/ml-review-backlog.json` — `MB-20260629-ALLOC-RANKER` → resolved.
- `ROADMAP.md` — T1.3 row rewritten to the ran/negative outcome; allocator-regret
  soak-clock note updated.
- This sprint log.

## Tier-3 Proposals
None. T1.3 closed negative (no routing change proposed); peak-head E3 kept shadow;
fc→advisory awaits a powered read. The rules EV scorer remains the allocator
selector, unchanged.

## Follow-ups / Next
- fc→advisory powered re-read ~2026-07-20 (`MB-20260705-FC-ADVISORY-READINESS`) —
  the lead promotion candidate.
- T1.3 reopen condition (not now): a within-tick contrastive target + clean
  per-trade cost labels (`MB-20260629-ALLOC-COSTCAP`) + a net-positive
  opportunity set.
