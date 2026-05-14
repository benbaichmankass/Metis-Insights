# Model Registry Policy

> **Status:** Canonical (registry-policy scope). Adopted in **S-AI-WS4**
> (2026-05-10).
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).
> Implementation: [`ml/registry/model_registry.py`](../../ml/registry/model_registry.py).
> Promotion gates: [`ml/promotion/__init__.py`](../../ml/promotion/__init__.py).
>
> **Companion:** [`training-center.md`](training-center.md) describes
> how registry entries are produced.

## Purpose

Document the model registry's status state machine, the legal
transitions, and the gates that must be satisfied before each
transition. The registry is the durable system of record for which
models exist, what version they are, and what tier of influence they
currently have.

## Status set

| Status | Meaning |
|---|---|
| `candidate` | Freshly registered. Ran train + evaluate. No further validation yet. |
| `paper` | Has passed leakage + walk-forward checks. Suitable for paper-trading evaluation. |
| `advisory` | Has passed transaction-cost evaluation and beats the heuristic baseline. May annotate or veto live trades only if the operator opts the strategy into advisory mode. |
| `live-approved` | Has passed the shadow-mode soak (≥ 7 days clean) and is approved for limited live influence. Operator approval required. |
| `champion` | The current incumbent for its `model_family`. At most one per family at a time. |

## Allowed transitions

```
candidate → paper, champion
paper     → advisory, candidate
advisory  → live-approved, paper
live-approved → champion, advisory
champion  → candidate
```

Any edge not listed above is rejected by
`ModelRegistry.promote(...)` with `RegistryError`. The rollback
edges (paper ← candidate, advisory ← paper, live-approved → advisory)
exist so a regression at any tier can be demoted without rewriting
history.

## Transition gates

Each transition has a documented set of gates the operator must
satisfy before promoting. Gates are enforced as a CLI guard:
`python -m ml promote ...` requires `--gates-acknowledged` for any
transition with documented gates and prints the gate list otherwise.

The `--reason` string captures which gates were satisfied for the
durable record.

| Transition | Gates |
|---|---|
| `candidate → paper` | `leakage_test_passed`, `walk_forward_evaluation_complete` |
| `candidate → champion` | `comparison_against_incumbent_complete`, `operator_explicit_approval` |
| `paper → advisory` | `transaction_cost_evaluation_complete`, `metrics_beat_heuristic_baseline` |
| `advisory → live-approved` | `shadow_mode_clean_soak_at_least_7d`, `operator_explicit_approval`, `rollback_plan_documented` |
| `live-approved → champion` | `comparison_against_incumbent_complete`, `operator_explicit_approval` |

Rollback transitions (`paper ← candidate`, `advisory ← paper`,
`live-approved → advisory`, `champion → candidate`) carry no
documented gates because demoting is always permitted; the
`--reason` string still captures the trigger.

## Registry entry shape

One JSON file per `model_id` under the registry root. The shape
matches `RegistryEntry.to_dict()`:

```json
{
  "model_id": "backtest-pnl-mean-baseline-v0",
  "status": "candidate",
  "manifest": { "...": "snapshot of the training manifest" },
  "model_state_path": "/abs/path/to/experiments/<id>/<runid>/model_state.json",
  "metrics": { "mse": 0.001, "mae": 0.025, "n_eval": 2.0 },
  "code_revision": "abc123def",
  "created_at": "2026-05-10T12:00:00+00:00",
  "history": [
    {
      "from_status": null,
      "to_status": "candidate",
      "by": "experiments-runner",
      "reason": "initial registration",
      "at": "2026-05-10T12:00:00+00:00"
    }
  ],
  "notes": "",
  "runs": [
    {
      "run_id": "20260514T162241Z",
      "model_state_path": "/abs/path/.../<runid>/model_state.json",
      "metrics": { "mse": 0.001, "mae": 0.025, "n_eval": 2.0 },
      "code_revision": "abc123def",
      "at": "2026-05-14T16:22:41+00:00",
      "by": "experiments-runner"
    }
  ]
}
```

The `history` array is append-only — every promotion adds a
`StatusEvent` and never edits past entries. This is the durable
audit trail of status changes.

The `runs` array is append-only too — every training cycle adds a
`RunRecord` and never edits past entries. See "Per-run training
history" below.

## Per-run training history (added 2026-05-14, #1133/#1139)

Every training cycle on the trainer VM re-runs every manifest. The
registry preserves the full per-run history under a stable `model_id`:

- The top-level fields (`metrics`, `model_state_path`,
  `code_revision`) mirror the **newest** run.
- The `runs` array records **every** run, sorted by `run_id` (a UTC
  timestamp `YYYYMMDDTHHMMSSZ` produced by `run_experiment`).
- Each `RunRecord` carries the run's `run_id`, `model_state_path`,
  `metrics`, `code_revision`, `at`, and `by`.

### Append-on-duplicate `register()`

`ModelRegistry.register(...)` is idempotent on `(model_id, run_id)`:

1. **First call for `model_id`** — creates the entry with
   `runs=(first_run,)`.
2. **Subsequent call, new `run_id`** — appends a new `RunRecord`,
   refreshes the entry's top-level metrics / state path / code
   revision to the latest run, and appends a
   `StatusEvent("re-trained at run_id=X")` to `history`. Status,
   stage, `created_at`, and `stage_history` are preserved.
3. **Same `(model_id, run_id)` twice** — no-op (guards retries
   without duplicating a run).

This is the contract behind daily-cadence training: re-runs of the
same manifest never raise — they accumulate per-run history.

### Backfill: `python -m ml.registry.reconcile`

Strictly-additive reconciler that walks
`<experiments_root>/<model_id>/` and rebuilds `runs` from every run
dir on disk. Existing `RunRecord`s are preserved verbatim; missing
ones are synthesized with `code_revision="unknown"` and
`by="registry-reconcile (backfilled from disk)"` so the gap is
self-documenting. A `RunRecord` whose experiment dir is missing
from disk is preserved, not dropped — reconcile never causes data
loss.

```
python -m ml.registry.reconcile \
    [--registry-root  ml/registry-store] \
    [--experiments-root ml/experiments-runs] \
    [--model-id MODEL_ID]   (repeatable, default = all entries)
    [--dry-run]
```

Use cases:
- One-shot migration for entries written before `runs` existed.
- Restore from backup that pre-dates today's runs.
- Sanity-recovery after manual edits drop a row.

Idempotent — re-running on an in-sync registry is a no-op.

## Rollback

Demoting (e.g. `live-approved → advisory`, `champion → candidate`)
is a normal `promote(...)` call with a rollback transition target.
No gate is enforced (rollbacks are always allowed) but the
`--reason` is mandatory.

A rollback does NOT delete the model artifact; it only changes the
tier metadata. The artifact triple under
`<experiments_root>/<id>/<runid>/` remains immutable.

## Versioning

The registry stores `model_id` strings as opaque identifiers — the
manifest author owns the family-level convention (typical:
`<task>-<approach>-<vNNN>`, e.g. `setup-quality-baseline-v0`).

Within a `model_id`, **per-run versioning** is the `run_id` —
the UTC timestamp `YYYYMMDDTHHMMSSZ` produced by
`ml.experiments.runner.run_experiment` and recorded on every
`RunRecord` in `runs`. Daily-cadence training accumulates run
records under a stable `model_id` (see "Per-run training history"
above).

For a **family-level successor** (architectural change to the
manifest, breaking schema, etc.), bump the manifest's `model_id`
suffix (`-v0` → `-v1`) so the new family lives in its own
registry entry. The append-on-duplicate path is for repeat training
runs of the same manifest, not for evolving the manifest itself.

## Forbidden

- Editing past `StatusEvent` entries. The history is append-only.
- Editing past `RunRecord` entries. The `runs` list is
  append-only too. Use `python -m ml.registry.reconcile` if a
  reconstruction from disk is needed.
- Promoting through a tier without satisfying its documented gates
  (the CLI enforces this; bypassing requires editing the JSON
  directly, which is a process violation).
- Promoting any model to `live-approved` or `champion` without an
  operator-issued approval recorded in `--by` + `--reason`.
- Deleting an entry. The registry is append-only at the entry level
  too. Old / failed candidates stay around as historical record.

## Update rule

This doc must be reviewed in the same PR as any change to:

- `VALID_STATUSES` or `_ALLOWED_TRANSITIONS` in
  `ml/registry/model_registry.py`,
- `PROMOTION_GATES` in `ml/promotion/__init__.py`,
- the `RegistryEntry` or `RunRecord` field set,
- the `register()` append/idempotency contract,
- the CLI `promote` semantics,
- the `ml/registry/reconcile.py` reconciler behaviour.
