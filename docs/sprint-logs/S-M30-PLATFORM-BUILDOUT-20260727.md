# Sprint Log: S-M30-PLATFORM-BUILDOUT-20260727

## Date Range
2026-07-27 (single extended session, driven under `research-driver` — the
overnight de-soak + M24–M29 close-out workplan N-queue, then the N7 build phase,
then operator-directed platform validation + live-IB triage).

## Objective
Build out and **validate** the M30 technical quant-research platform (a
data-first feature→outcome discovery workflow), break the ML eval-book label
wall by admitting paper trades as a tagged eval population (L3), fix a latent
real/paper eval-contamination seam that L3 would build on, and resolve an
operator-reported live alert-spam issue on the IBKR-fed strategies.

## Tier
Mixed. Research tooling + docs + backlog + ledger = **Tier-1** (merged on green).
The eval-pipeline changes (eval account_class filter #7697, L3 #7700) and the
IB alert-severity reclassification (#7701) touch `ml/`/`src/` runtime = **Tier-2**,
opened as DRAFTs and merged only after explicit operator approval.

## Starting Context
M30 was at P0 scoping (scoping doc + inventory written). The overnight driver
had already merged Phase 0 / WS-1 / WS-2 / WS-3 / WS-3b and the N-queue scoping.
The research platform's build increments (N7) and its validation were the open
work; the operator additionally asked mid-session for a full audit + pipeline
validation "so the next research session uses tools that actually work," then
raised a recurring MES/MGC/MHG "no candle data" alert.

## Repo State Checked
`origin/main` at each unit start; canonical set (CLAUDE.md, CLAUDE-RULES-CANONICAL,
ARCHITECTURE-CANONICAL, ROADMAP.md); the coordination board (#6927).

## Files and Systems Inspected
`scripts/research/{build_research_panel,analyze_research_panel}.py` +
`src/research/component_vector.py`; `ml/datasets/families/setup_candidates.py`,
`ml/experiments/splitters.py`, `scripts/ml/{m23_phase2_labelvol.sh,m23_ev_gate.py}`;
`src/runtime/intent_multiplexer.py` + `src/runtime/strategy_signal_builders.py` +
`src/runtime/outcomes.py`; the live IB state via the diag relay
(`/api/diag/ib_state`, `services`, trader `journalctl`, relay #7699); the trainer's
synced `trade_journal.db` via the `trainer-vm-diag` relay (#7689–#7695, #7699).

## Work Completed
- **M30 platform (merged):** C1 panel builder (#7686), C2 toolkit (#7687),
  ledger (#7688), Study 1 pooled real-book (#7692), Study 2 vwap per-strategy
  (#7694). Studies 1/2 are honest results: 2 FDR-survivor *leads*
  (`vwap_deviation_std`, `model_score_mean`), neither a confirmed finding; the
  real yield is that discovery is starved by **feature-capture breadth**, not
  row count (Study 2), and the real book is 84% one strategy (Study 1).
- **Validation (merged #7698):** executable proof through the C2 CLI on
  synthetic panels — signal recovered OOS AUC 0.89, null rejected (AUC 0.42,
  zero survivors), leakage hard-refused — + 106 unit tests + two read-only
  correctness audits (C1/C2 core sound; eval-pipeline seam found). Go/no-go = GO.
- **Eval contamination fix (merged #7697):** `setup_candidates::_load_live_trades`
  made account_class-authoritative (was `is_demo=0` alone). Live-quantified:
  **0 contamination today** (all 401 real rows `is_demo=0`, all paper `is_demo=1`)
  — a latent robustness fix + the L3 prerequisite, flagged honestly as such.
- **C2 hardening (merged #7696):** cohort discipline (a mixed real+paper panel
  is refused unless explicitly isolated/pooled — the never-blended contract in
  the toolkit) + NaN-safe JSON output. +6 tests.
- **L3 paper-book eval population (merged #7700):** `_load_live_trades(include_paper=…)`;
  paper emitted as a distinct `event_source='live_paper'` + `is_live_trade=False`
  (structurally never on the real-money eval side); paper evaluator manifest;
  `INCLUDE_PAPER` harness flag (off by default); `m23_ev_gate --population`
  selector (default `live`, unchanged). Breaks the ~376-row eval wall.
- **IB alert-spam fix (#7701, operator-approved, merged):** the transient
  "no candle data returned" builder `RuntimeError` is reported at `Level.WARN`
  (persisted + banner-visible, but NOT in `_TELEGRAM_LEVELS` → stops paging)
  instead of `Level.ERROR`; genuine builder failures still page. One chokepoint
  in `intent_multiplexer.py`, zero trading-behavior change.
- **Records (#7702):** IB investigation + failed `ict-mes-ibkr-pull.service`
  logged to the health-review backlog; M30 deep-research kickoff prompt written;
  ROADMAP M30 status cell updated.

## Validation Performed
- Full research-infra test suite: 106 tests green locally; per-PR CI green on
  each merge. Adversarial C2 validation (signal/null/leakage) ALL PASS via the
  real CLI. ruff verified against the CI-pinned `ruff<0.16` (local 0.16 gives
  false positives).
- L3: 62 tests + an end-to-end `include_paper=True` build (`event_source`
  cusum/live/live_paper split, no `is_paper` leak); default path confirmed
  byte-for-byte unchanged.
- IB diagnosis: `ib_state` relay #7699 confirmed a transient breaker flap
  (`likely_wedged:false`, `liveness_probe_timeout`, recovers ~90s), NOT a wedge
  — so no gateway restart was warranted (the sanctioned action was deliberately
  NOT taken).

## Documentation Updated
- `docs/research/technical-quant-research-platform-validation-2026-07-27.md` (new — go/no-go).
- `docs/research/technical-quant-research-ledger.md` (Studies 1 + 2 + queued next).
- `docs/research/M30-deep-research-SESSION-PROMPT.md` (new — paste-ready kickoff).
- `ROADMAP.md` — M30 status cell → "PLATFORM v1 BUILT + VALIDATED" + SESSION PROMPT pointer.
- `docs/claude/health-review-backlog.json` — +2 IB items.

## Contradictions or Drift Found
None. `scripts/ci/check_canonical_doc_coherence.py` PASSES all four invariants
(dead-VM-IP single-source, removed-gates-not-live, no 7-stage ladder, hierarchy
mirror) after the ROADMAP edit. The never-blended real/paper contract is
preserved and consistently described (L3 is additive — paper is a *distinct*
tagged eval cohort, never blended into real-money aggregates).

## Risks and Follow-Ups
- **`BL-20260727-IB-LIVENESS-PROBE-CACHED-HANDLE-FALSETRIP`** (health backlog) —
  the underlying probe-flap root cause (cached-handle false-trip over the socat
  relay, BL-20260709 class). #7701 fixes the alert-spam symptom; the flap itself
  is a Tier-2 live-connection fix for a focused session.
- **`BL-20260727-ICT-MES-IBKR-PULL-SERVICE-FAILED`** (health backlog) — the MES
  data-pull sidecar (audit-residue F1) still failing 2026-07-27.
- Platform self-serve loose-ends P1–P6 (queued in the validation doc + ledger):
  P1 C2 `--features` selector (unblocks pooled discovery — do first), P2 sweep
  driver, P3 backtest bridge, P4 wider feature capture, P5 per-bar panel, P6 SHAP.

## Deferred Items
The N2/N4 drafts from earlier in the driver (#7683, #7685) merged; N5 was a
foregone null; N6 audit-residue F1/F3 are partially captured in the health
backlog. The deep-research studies themselves are the next session's work
(prompt ready).

## Next Recommended Sprint
The **M30 deep-research session** (`docs/research/M30-deep-research-SESSION-PROMPT.md`)
— build P1 (C2 `--features` selector) first, then run the queued studies
(Study 3 common-core, per-strategy sweep, exit-timing/regime). Precede it with
the operator's `/system-review` (which will drain the two new IB backlog items).

## Wrap-Up Check
- [x] Coordination board #6927 START/DONE posted per unit.
- [x] `doc-freshness` run — coherence PASS; this sprint log written to close the
      decision-landing gap; ROADMAP + backlog updated.
- [x] Material decisions landed in ROADMAP (M30 cell) + this sprint log + the
      health backlog (IB items) + the research ledger (studies).
