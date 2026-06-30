# Sprint Log: S-EDGE-WALL-TRILOGY-2026-06-30

## Date Range
2026-06-30 (single session, autonomous research + strategy-quality review)

## Objective
Operator direction: (1) build a framework to test/score signal generators and
research new ones; (2) evaluate an end-to-end learned entry/exit policy; then
(3) test the selection (allocator) frontier; finally (4) a strategy-quality
kill/promote pass. The through-question: **where, if anywhere, does a learnable
per-trade edge live in this system?**

## Tier
Tier-1 (research tooling, docs, backlog, observability) throughout. One Tier-3
artifact produced as a **gated draft only** (PR #5244, real-money promotion) —
not merged.

## Starting Context
Carried the M18 prior (entry-feature outcome ≈ coin-flip OOS) and the 2026-06-29
allocator findings (EV scorer doesn't beat dumb priority). The signal-research
framework P0 + entry-wall finding were already merged earlier in the session
(#5199/#5207/#5220).

## Repo State Checked
`main` advanced several times mid-session; the working branch
`claude/mls-promotion-review-1s8oke` was rebased/restarted from fresh `origin/main`
after each merge. Designated-branch discipline maintained.

## Files and Systems Inspected
`docs/research/{signal-research-framework,capital-allocation-ai,M18-allocator-backtest-findings,exit-management-ml-experiment,where-edge-lives-entry-wall}*`,
`scripts/research/allocator_{candidate_dataset,ranker_eval,multisymbol_backtest}.py`,
`ml/datasets/families/{setup_candidates,exit_candidates}.py`, `config/{accounts,strategies}.yaml`,
`docs/claude/strategy-refinement-queue.json`, the perf/health review backlogs,
`scripts/ml/strategy_review_packet.py` (M7 gate), trainer + live diag relays.

## Work Completed
- **Exit-management P0 (PR #5225, merged):** `exit_candidates` in-trade dataset
  family + `exit-policy-v1` manifest + `classification_auc` evaluator + 23 tests.
  Trainer verdict: powered synthetic-OOS **AUC 0.5209 (n=138,391)** — exit-timing
  is at the wall.
- **M18 c_ml selection probe (PR #5234, merged):** added the only untested `P_win`
  input (regime label + leakage-safe per-(owner,regime) historical expectancy) to
  the allocator scorer harness + ranker variants + 7 tests. Verdict: every variant
  **OOS AUC 0.508–0.514 (n=1,236)**, none beating `confidence` (0.522) — selection
  is at the wall. The M18 learned cross-candidate ranker is closed.
- **Strategy-quality M7 pass (#5243 + per-packet #5245/#5246):** generated review
  packets for 16 cells. 2 `DEMOTE_SHADOW` badges (squeeze_breakout_4h n_closed=1,
  htf_pullback_trend_2h n_closed=5) judged **low-n floor artifacts — not proposed**;
  4h alts = HOLD (not promote); all else HOLD. Backlog follow-ups merged (#5247).
- **Gated draft (PR #5244, NOT merged):** promote `trend_donchian_eth_4h` +
  `_xrp_4h` to real-money bybit_2 — staged for operator approval + the
  regime-gated re-sweep + account-compat gate.

## Validation Performed
All merged PRs: local pytest + ruff green, full CI green pre-merge. Trainer runs
confirmed end-to-end (build rc=0, eval rc=0) before reading verdicts. M7 packet
verdicts cross-checked against backlog caveats (artifact-contaminated records) and
per-packet `n_closed` before any Tier-3 proposal.

## Documentation Updated
- `docs/research/exit-management-ml-experiment-DESIGN.md` §8 (exit verdict).
- `docs/research/M18-allocator-backtest-findings-2026-06-29.md` (c_ml verdict section).
- `docs/claude/performance-review-backlog.json` (+PB-20260630-002/003).
- `docs/claude/health-review-backlog.json` (+BL-20260630-PRINTPACKETS).

## Contradictions or Drift Found
- M7 gate over-fires `demote` at low n (logged PB-20260630-002).
- `generate-strategy-review-packets` `print_packets` cats wrong dir on the live VM
  (logged BL-20260630-PRINTPACKETS). No canonical-doc contradiction introduced.

## Risks and Follow-Ups
- **PR #5244** (Tier-3, gated draft) awaits operator approval + the SRQ-001
  regime-gated re-sweep + `account_compat_matrix` for bybit_2 before any merge.
- Fill-rate puzzle on squeeze (1.2%) / htf (9%) — investigate (PB-20260630-003).
- Add the M7 min-n guard (PB-20260630-002) + fix print_packets path
  (BL-20260630-PRINTPACKETS).

## Deferred Items
The strategy book is in "hold and let evidence accrue" — no live kill/demote/promote
warranted on clean evidence this pass. The regime gate already neutralizes the known
money-losers.

## Next Recommended Sprint
- Required verification before starting: re-confirm bybit_2 real-money record is
  clean (the 26 unreconciled orphans, PB-20260625-002) before any live strategy flip.
- Candidates: the M7 min-n guard fix; the squeeze/htf fill-rate investigation;
  whether to graduate any 4h alt to real money once its demo soak + re-sweep clear.

## Wrap-Up Check
Through-line established across **three independent reads**: entry (~0.51), exit
(0.52), and selection (0.51) per-trade prediction are all at the M18 wall — timing
prediction is unlearnable OOS from decision-time features here; ML's value is
**regime/context as a gate** (the live A/B-validated vol-gate), not per-trade ranking.
This closes the "is there a learnable entry/exit/selection edge?" question. Real
levers ahead are strategy-level quality + sizing/risk (both Tier-3). doc-freshness:
all session changes are additive research docs + backlog + gated draft — no canonical
contradiction.
