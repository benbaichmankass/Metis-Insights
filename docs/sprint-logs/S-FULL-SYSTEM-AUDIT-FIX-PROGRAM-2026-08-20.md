# Sprint Log: S-FULL-SYSTEM-AUDIT-FIX-PROGRAM-2026-08-20

## Date Range
- Start: 2026-08-20
- End: 2026-08-20 (single session, one context compaction)

## Objective
- **Primary goal:** Run a comprehensive system audit — *starting by auditing the audit
  skill itself*, on the operator's grounds that *"we keep on doing audit sessions and
  you say that everything's okay … and we still find things that we think are built
  out but aren't actually built out."* Binding method: **"Verify everything. Do not
  assume. Do not trust. Verify."**
- **Secondary goals (operator-named, all delivered):**
  1. An updated **audit skill** covering cohesion, philosophy, design-vs-actual
     performance, and live end-to-end verification.
  2. Updates to the **system-review and workflow skills** so work is done correctly —
     *"not building things that then stay unwired for weeks or months"* — verifying
     everything built since the previous review, and resolving recurring bugs
     **structurally** by reviewing the whole backlog for larger classes first.
  3. The **modularity / scalability principle**: build so a system change takes as few
     edits as possible, or concentrates them in one place; and audit implementations
     to verify every aspect works as designed.
  4. The operator's worked example: *"the back test risk and the live config don't
     match … it needs to, in any case, check various different risk percentages."*

## Tier
- **Tier 1** throughout. Docs, tests, CI guards, observability and read paths,
  trainer-side orchestration, research harness reporting.
- **Justification:** no change in this program touches `config/strategies.yaml`,
  `config/accounts.yaml`, `config/risk_caps.yaml`, the order path, or any live unit
  file. The one production mutation — the prop-fill direction repair — was an
  operator-approved data fix dispatched through the sanctioned `system-actions` wire.

## Starting Context
- Active roadmap items: the audit program itself; M20 exit work and the LLM-burst
  scheduling ran concurrently in **other** sessions (their sprint logs are separate).
- Prior sprint reference: none — this program was opened by operator directive.
- Known risks at start: the audit skill was suspected insufficient by the operator,
  which meant the instrument had to be validated before its readings were trusted.

## Repo State Checked
- Branch or commit reviewed: `main`, plus the live VM and trainer VM through the diag
  surfaces and the trainer relay.
- Deployment state reviewed: live trader (`ict-bot-arm`), trainer VM, and the mirror
  at `/api/bot/ml/*`; the live journal via `/api/bot/*`; the Svelte SPA and Streamlit
  consumers.

## Files and Systems Inspected
- **Trainer lifecycle:** `scripts/ops/run_training_cycle.sh`,
  `scripts/ops/publish_trainer_mirror.sh`, `scripts/ops/trainer_dataset_gc.py`,
  `scripts/ops/run_promotion_readiness.sh`, `ml/promotion/`.
- **Research fleet:** `scripts/backtest_*.py` (25 harness files),
  `src/runtime/execution_costs.py`, `scripts/ml/record_harness_trades.py`,
  `ml/datasets/backtest_recorder.py`, `scripts/research/backtest_fidelity_calibrate.py`.
- **API + consumers:** `src/web/api/routers/notifications.py`, `training_center.py`,
  the live `/api/bot/ml/{status,cycle,registry}` + `/api/bot/prop/status` payloads,
  `webapp/src/routes/{Prop,Models}.svelte`.
- **Guard family:** `check_claim_basis`, `check_unwired_artifacts`,
  `check_collapsed_states`, `check_risk_basis_agreement`, `run_guards`.
- **Instruction system:** `.claude/skills/{full-system-audit,system-review}/SKILL.md`.

## Work Completed
- **Both operator-named deliverables** landed in **#9998**: the rewritten
  `full-system-audit` skill (seven axes ordered by blast radius, plus a standing
  design-criticism phase) and the hardened `system-review` / workflow skills.
- **Fix program landed as eight PRs.** #10038 promotion-readiness pre-gate evidence
  sync · #10047 measured `oos_edge` RSS (**refuted** the re-enable) · #10048
  `diag_fetch` failure-stage provenance · #10051 M5 `/test` consumer removed · #10056
  risk-basis single definition + unit + grid + guard · #10059 trainer disk /
  staleness / refusal publishing · #10061 trainer cycle refusal state · #10062 R
  cost-basis provenance · #10063 cost-model single owner · #10064 harness reads live
  risk. Dashboard **#206** fixed F-110 and F-111 and shipped an API-contract checker.
- **Prop phantom-monitor bug root-caused, repaired, and verified.** A `prop_fills`
  row admitted with `direction: NULL` keyed differently from its own closes, so
  `find_open_prop_positions` reported a phantom-open 83-SOL position indefinitely.
  Repair applied and verified by importing the **deployed** `_position_key` over all
  32 live fills (3 keys / 0 OPEN; control restoring NULL → 4 keys / 1 OPEN).
- **Five new CI guards or guard extensions**, each with planted-control self-tests.

## Validation Performed
- Every fix carries **discriminating controls**, not import errors: a surgical break
  must fail a *named subset* of tests while the rest pass. Recorded per PR.
- **Positive controls before any count** — an extractor must be shown to find a
  positive before its silence is trusted.
- Sibling-suite sweeps derived by grep from the changed symbols, after the one CI
  cycle burned by not running the suite named after the edited file.
- `run_guards.py --base main` **after committing** on every branch (an uncommitted
  tree silently deselects guards — a trap hit and recorded).

## Documentation Updated
- `docs/audits/full-system-audit-2026-08-20.md` — Parts 1–28, including Part 27
  (what executing the fixes revealed) and Part 28 (the five times a mechanical check
  caught the auditor).
- `CLAUDE.md` — M5 removal, `BACKTEST_DATA_PATH` correction, backtest-route status.
- Backlog rows filed with severity, tier, and `resolution_criteria` throughout.

## Contradictions or Drift Found
- **The audit corrected itself four times**, each recorded rather than quietly fixed:
  F-103's `already_done` claim (true of one of two daily cycles — but the published
  one, **78.9% of hours**); B4's "five harnesses hardcode their own fee" (five were
  already correct aliases; a name-driven sweep would have rewritten correct files);
  B4's `build_backtest_panel` claim (it has no cost references at all); and B2, whose
  A/B confound was disarmed by the only caller.
- **`risk_pct` carries two different units** — a fraction live, a percent in the
  harness fleet — so every default backtest sizes at **one fifth** of live risk.
- **`src/research/risk_basis.py` had no consumer one day after shipping** — this
  program's own build-and-abandon.

## Risks and Follow-Ups
- `BL-20260820-TRAINER-PUBLISHES-NO-DISK-METRIC` — at
  `fix_landed_awaiting_trainer_redeploy`. **The code landing is not the evidence.**
- `BL-20260820-TRAINER-DISK-THRESHOLDS-UNCALIBRATED` — thresholds are declared
  CHOSEN over n=1; closes by calibrating or by resolving the pressure, never by muting.
- `BL-20260820-UNWIRED-ARTIFACT-GUARD-DEFEATED-BY-STRING-LITERALS` — the class is
  open; only the prose was fixed.
- `BL-20260820-CLAIM-BASIS-GUARD-BLIND-TO-DETAIL-AND-EVIDENCE` — fixed forward; 16
  legacy basis-less rows deliberately not retro-fixed.
- `MB-20260719-PROMOREADY-OOSEDGE-OOM` — open on **two** independent counts now
  (5.19 GB peak vs a 4.5 GiB cap, and ~2.6 h wall clock).

## Deferred Items
- **The structural prop fix** — deferred by explicit operator direction until the
  audit completed. The admission contract (`ingest_report` validates `account_id` and
  `symbol`, passes `direction` through unvalidated, while `_position_key` requires all
  three) is the root cause and is unfixed.
- The remaining 24 harnesses, and B5's account-constraint half (no backtest can
  produce the refusals that kill 37.2% of real order packages).
- B4's second half: `roundtrip_cost_r` defaulting slippage and funding to `0.0` — a
  documented deliberate default, so flipping it re-bases every in-process comparison
  and is a decision, not a cleanup.

## Next Recommended Sprint
- **The structural prop fix**, now unblocked: enforce at admission the same identity
  fields the position key requires, so a fill can never be admitted permanently
  unclosable. Then re-run the phantom check over all fills as the verification.
- **Trainer disk pressure** — a pinning-policy decision on the 41 manifest pins
  holding 111 dataset version dirs (9.9 GB); the GC is measured at 0.09 GB and is
  explicitly *not* the remedy.

## Wrap-Up Check
- Deliverables: **both operator-named skills shipped** (#9998).
- Every finding filed with severity + tier + `resolution_criteria`.
- Every PR states its population before its numbers, and its unverified item as an
  unchecked box.
