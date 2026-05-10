# Architecture Change Checklist (S-AI-WS10)

> **Authority:** [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md)
> is the canonical architecture doc. This checklist defines **what
> counts as an architecture change** and **what to update** when one
> ships.
>
> **Why:** The architecture doc drifts away from reality if it's
> updated reactively. This checklist is the forcing function: every
> PR author asks "did I trip a rule below?" before opening the PR.
> Reviewers ask the same question before approving.

## What counts as an architecture change

A change is **architectural** if any of the following is true:

1. **Data schema.** New table, new column, renamed column, changed
   primary key, changed nullability, changed foreign key, on a
   table read by more than one subsystem
   (e.g. `trade_journal.db::trades`, `order_packages`,
   `backtest_results`, `trade_outcomes`).
2. **Model boundary.** New `Predictor` subclass, new `Trainer`,
   new `Evaluator`, new dataset family, new feature column on a
   family read by a trainer, new manifest stanza recognised by
   `ml.experiments.runner`.
3. **Pipeline stage.** New named stage in
   [`docs/pipeline/stage-contracts.md`](../pipeline/stage-contracts.md),
   renamed stage, changed stage input/output type, new
   typed dataclass in [`src/pipeline/types.py`](../../src/pipeline/types.py).
4. **Deployment stage / promotion ladder.** Change to
   `ModelRegistry.target_deployment_stage` values; change to the
   stage-gate allowlists in `ml.shadow.factory.LIVE_INFLUENCE_STAGES`;
   change to `ml/promotion` gate logic.
5. **Runtime responsibility.** A subsystem now owns a new piece of
   state or event flow — e.g. Coordinator gains a new cache;
   strategies gain a new tick-time concern; a new daemon thread
   is introduced; a new on-disk artifact is written.
6. **Public API surface.** New `/api/bot/*` or `/api/diag/*`
   endpoint; new auth tier; new env var consumed by the live
   trader (`src/main.py`) or the web API (`src/web/api/`).
7. **Inter-process / inter-service contract.** New file consumed
   by another systemd unit; new shape of a comms-pipeline request
   or response; new label or workflow trigger pattern.
8. **Configuration shape.** New top-level key in
   `config/strategies.yaml`, `config/accounts.yaml`, or
   `config/units.yaml`; a new operator-facing knob that survives
   reload.

A change is **NOT architectural** if it's purely:

- Strategy-parameter tuning within an existing knob (changing
  `atr_stop_mult: 0.35` → `0.30` is a tuning change, not an
  architecture change — the knob already existed).
- Test-only changes.
- Bug fixes that don't change the contract — only the behaviour
  inside it.
- Performance optimisations that don't change observable surface.
- Doc-only edits (you're already updating docs).

## What to update when an architectural change ships

If your PR trips any of the rules in the previous section,
update **at least one** of:

| If you changed... | Update... |
|---|---|
| System-wide design, pipeline stages, deployment ladder, public APIs | [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md) (always) |
| AI-specific design (data → feature → model → orchestration) | [`docs/architecture/ai-model-platform.md`](ai-model-platform.md) (in addition to the canonical doc when system-wide impact also exists) |
| Pipeline stage contracts | [`docs/pipeline/stage-contracts.md`](../pipeline/stage-contracts.md) |
| Claude operating rules / permission tiers / workflow routing | [`docs/CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md) |
| Dashboard-API surface (`/api/bot/*`) | The Dashboard REST API table in [`CLAUDE.md`](../../CLAUDE.md) |
| Diagnostic-API surface (`/api/diag/*`) | The Diagnostic API table in [`CLAUDE.md`](../../CLAUDE.md) |

Also always:

- Add a row to the **Change log** section at the bottom of
  [`docs/ARCHITECTURE-CANONICAL.md`](../ARCHITECTURE-CANONICAL.md).
- If the change leaves a deliberate gap (e.g. "Coordinator-side
  caching for shadow predictors will land in WS7-PART-6"), add or
  update an entry under the **Known gaps** section.

## How the guard fires

[`.github/workflows/arch-doc-guard.yml`](../../.github/workflows/arch-doc-guard.yml)
runs on every PR. It looks at the changed files and:

- **If** any file matches the high-impact path patterns in
  [`scripts/arch_doc_guard.py::HIGH_IMPACT_PATTERNS`](../../scripts/arch_doc_guard.py),
- **AND** no file matches the arch-doc path patterns
  ([`ARCH_DOC_PATTERNS`](../../scripts/arch_doc_guard.py)),
- **THEN** it emits a `::warning` annotation visible on the PR's
  check summary. **The job still exits 0** — it never blocks merge.

The warning is intentionally soft. Two reasons:
- Some architectural changes do not need a doc update (e.g. a
  fix that restores the documented contract). The PR template's
  "Architecture not affected because ___" checkbox lets the
  author record that explicitly.
- Hard-failing this check would teach the team to bypass it
  ("ignore the docs job, will update later") faster than it
  would teach them to update docs. Advisory beats adversarial.

A future workstream can upgrade the guard to a hard-fail once the
team is fluent with the workflow.
