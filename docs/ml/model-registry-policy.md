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
  "notes": ""
}
```

The `history` array is append-only — every promotion adds a
`StatusEvent` and never edits past entries. This is the durable
audit trail.

## Rollback

Demoting (e.g. `live-approved → advisory`, `champion → candidate`)
is a normal `promote(...)` call with a rollback transition target.
No gate is enforced (rollbacks are always allowed) but the
`--reason` is mandatory.

A rollback does NOT delete the model artifact; it only changes the
tier metadata. The artifact triple under
`<experiments_root>/<id>/<runid>/` remains immutable.

## Versioning

The registry stores `model_id` strings as opaque identifiers.
Versioning is the manifest author's responsibility — typical
convention: `<task>-<approach>-<vNNN>` (e.g.
`setup-quality-baseline-v0`). A new training run with the same
`model_id` is rejected (`RegistryError`); use a fresh id when
producing a successor.

## Forbidden

- Editing past `StatusEvent` entries. The history is append-only.
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
- the `RegistryEntry` field set,
- the CLI `promote` semantics.
