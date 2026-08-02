# Sprint Log: S-RESEARCH-BACKLOG-C30UJ9-2026-08-02

## Date Range
2026-08-02 (single session, `metis-insights-research-backlog-c30uj9`).

## Objective
Continue the RESEARCH-PROGRAM-2026-07-30 / WORK-PLAN backlog. The handoff pointed
at **A1** (`MB-20260530-001`) and **M25 P2** (powered-RG4 sweep) as "the two open
trainer-relay items." Operator directed a strict **verify-before-redo** pass and a
**thorough work plan** grounded in what actually needs doing, routed to the correct
compute tier.

## Tier
Tier-1 throughout (docs, tests, research tooling). No `src/`, `config/`, order-path,
or live-VM mutation. The operator-approved Tier-2 items (timestamp writer migration,
netting attribution fix) were **prepared/queued, not executed** — handed to a fresh
session.

## Starting Context
Predecessor `0tq6uz` wrapped with #8355/#8358/#8363 merged. Board #6927 checked; VM
lane free; sibling sessions active on BTC fc-pcv-v2 drift (`zajauh`/`backlog-cont`),
ETH class-weight (#8345), and the `/dev/null` diagnostic (`ragtvm`).

## Repo State Checked
`origin/main` re-cut per PR (rebased through the merge-queue-off rebase-race each
time). Board #6927 read + `START`/slot/`DONE` posted. `docs/claude/vm-resource-management.md`
read (compute routing). Canonical-doc-coherence scan: PASS.

## Files and Systems Inspected
Research-program + macro + review-backlog surfaces (5 verification subagents);
`scripts/research/regime_cell_walkforward.py` + `direction_walkforward.py` +
`regime_tag_emitted.py`; `ml/datasets/families/{setup_labels,trade_outcomes}.py` +
`backtest_recorder.py` + `record_harness_trades.py` (A1 pipeline); the S-MLOPT-S7 +
S-WEEKLY-REVIEW-EXEC sprint logs; `src.utils.closed_at` + `order_monitor.py` /
`execute.py` writers (timestamp + netting scoping).

## Work Completed
- **Verified reframe (the core finding):** both handoff items are stale. **M25 P2 is
  DONE** (07-19 RG4 sweep memo; the gate was reframed 07-19 so RG4 is advisory-only;
  its dated re-checks are consumed or sibling-owned). **A1's pipeline is BUILT and was
  RUN — MES-only** (#8318/#8326, into a sidecar db, not the live journal). The macro
  program (M1/M2/M5/M28/M29/M32/M33/M34) is conclusively closed-negative.
- **PR #8373** — committed `docs/research/WORK-PLAN-2026-08-02.md`: the verified,
  routed (free-runner / trainer / GPU-burst / live-read), tier-labeled,
  collision-checked execution plan superseding the stale handoff.
- **PR #8374** — reconciled the A1 backtest-augmentation state across both backlogs
  (`MB-20260530-001` + health `BL-20260731-BACKTEST-AUGMENTATION-NEVER-FED`), closing
  the S-MLOPT-S7 "Closes" vs `kept_open` contradiction.
- **PR #8395 (W1.1)** — regime-cell gate integrity: `regime_cell_walkforward.py`
  gained the 2-D vol-cell axis (`--vol`/`--vol-labels`, hard-error when a 2-D cell is
  requested without labels — never a silent 1-D fallback) and a **fold-count-invariant**
  `cell_verdict` (fixed `FOLD_PANEL=(3,4,5)`, agreement required; `*_fold_sensitive`
  reported). Unblocks the required `*_stable_drag` gate for the six live 2-D cells.
  42 tests pass; workflow summary renderer updated.

## Validation Performed
- `pytest tests/test_regime_cell_walkforward.py tests/test_regime_vol_axis.py` → 42
  passed (incl. the new fold-flip regression test reproducing the exact 2/3·2/4·3/5
  scenario, which now fails-closed + is flagged fold-sensitive).
- `ruff check` clean on the changed script + test.
- Workflow YAML re-parsed; summary renderer verified against real `cell_verdict` output.
- All three PRs green on the full required-check set (pytest + guards).
- `check_canonical_doc_coherence.py` → all checks passed.

## Documentation Updated
- `docs/research/WORK-PLAN-2026-08-02.md` (new).
- `docs/claude/ml-review-backlog.json` — `MB-20260530-001` verified-state update.
- `docs/claude/health-review-backlog.json` — `BL-20260731-BACKTEST-AUGMENTATION-NEVER-FED`
  sharpened; `BL-20260730-WALKFORWARD-NO-VOL-AXIS` + `BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP`
  marked **resolved** (by #8395); new `BL-20260802-DIRWF-FOLDCOUNT-VERDICT-FLIP` filed.
- This sprint log.

## Contradictions or Drift Found
- S-MLOPT-S7 claims "Closes MB-20260530-001" while the item is `kept_open` and its
  criterion is unmet → reconciled in #8374 (item stays `kept_open`, evidence recorded).
- ROADMAP.md is ~4 days stale (self-stamps 07-28, changelog stops 07-29; does not
  reflect the 08-01/02 work). **Pre-existing, not caused by this session; NOT edited**
  (hot shared file; a scoped reconciliation is its own task). Flagged here + in the
  plan doc so the next session picks it up.

## Risks and Follow-Ups
- **`BL-20260802-DIRWF-FOLDCOUNT-VERDICT-FLIP`** — `direction_walkforward.analyze`
  (lines 107-108) has the identical fold-flip bug my #8395 fix did NOT touch (it's the
  standalone 1-D direction gate used by m20/m21/rec5). Filed to the health backlog.
- **2-D runner wiring** — the offline driver accepts a `--vol-labels` path but
  `regime-cell-walkforward.yml` doesn't yet generate/pass one (needs an
  `ml_vol_label_replay` artifact, trainer-side). Noted in the plan doc + the resolved
  backlog item.
- **Stale natgas econ scorecard** (`comms/macro/econ_event_study_scorecard.json` reads
  the pre-#8300 verdict) — low-priority diagnostic-provenance nit; regen needs the
  study re-run on a runner. Recorded here (not fixed — hand-editing a verdict would be
  fabrication).

## Deferred Items
The rest of `WORK-PLAN-2026-08-02.md`: timestamp read-side+CI-guard → writer
canonicalization+migration (operator-approved Tier-2), netting attribution fix
(operator-approved Tier-2, design-packet-first), the free-runner sweeps (ADX / ETF
fee re-grade / GLD-1h), A1 pooled-model rollout (needs a new `research-backtest-augment`
runner workflow), W0.2 CI guards, W0.4 `regime-selectivity` skill. Handed to a fresh
session with a paste-ready prompt.

## Next Recommended Sprint
Execute the plan doc's Wave-0 → Wave-1 in a fresh context (paste-ready prompt
delivered to the operator). Start with the timestamp read-side + CI guard (safest
Tier-1 slice), then the operator-approved Tier-2 items design-packet-first.

## Wrap-Up Check
- [x] `doc-freshness` run — canonical set consistent; decisions landed in
      ROADMAP-relevant surfaces (plan doc) + this sprint log + the review backlogs.
- [x] All shipped fixes marked resolved in their backlog items; the one residual filed.
- [x] Board #6927 updated; merge slot released.
