# Dataset Versioning Policy

> **Status:** Canonical (data scope). Adopted in **S-AI-WS3**.
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).

## Naming convention

Every dataset artifact lives at a path of the form:

```
<output_dir>/<family>/<symbol_scope>/<timeframe>/<version>/
  metadata.json
  data.jsonl
```

- `family` matches a row in [`dataset-taxonomy.md`](dataset-taxonomy.md).
- `symbol_scope` is `all` or a single symbol token (e.g. `BTCUSDT`)
  or a comma-joined list (e.g. `BTCUSDT,ETHUSDT`). Lists are sorted
  alphabetically before being joined.
- `timeframe` is `all` or a canonical token (`1m`, `5m`, `15m`,
  `1h`, `4h`, `1d`).
- `version` is `vNNN` with monotonic integer increments per
  `(family, symbol_scope, timeframe)` triple. Versions are immutable
  once published.

## When to bump version

- **Always** when the data inside `data.jsonl` would differ from a
  prior build, even if the schema is unchanged (rebuilding off
  fresh upstream rows counts).
- **Always** when the family schema changes (column added, removed,
  renamed, type-changed). Schema changes also bump
  `builder_version` in metadata.
- **Always** when the leakage-test result changes from passed to
  failed or vice versa. A `failed` artifact is for offline
  diagnosis only and must not be promoted to a model registry as a
  candidate dataset.

## When NOT to bump version

- Cosmetic builder edits that are bit-for-bit identical to the
  previous build (rare; treat any uncertainty as a bump).
- Metadata-only edits (e.g. fixing a `notes` typo). In this case
  the artifact is rebuilt with the same `version` only when running
  with `overwrite=True` and the operator has accepted the rebuild.

## Retention

The in-repo policy is **append-only**: never delete an existing
version directory. Old versions are the audit trail for past model
training runs.

For remote storage (Hugging Face, see
[`huggingface-datasets.md`](../integrations/huggingface-datasets.md)),
the published dataset repo holds the same hierarchy and the same
append-only rule applies.

If disk pressure forces a cleanup on the Oracle VM, the local
`output_dir` is the one that gets pruned, NOT the remote published
copy. Pruning rules:

1. Never prune the latest version per
   `(family, symbol_scope, timeframe)`.
2. Never prune any version whose `generation_commit_sha` matches a
   currently-deployed model in the registry.
3. Prefer pruning intermediate versions older than 90 days over
   trimming recent ones.

Pruning must be a separate operator-acknowledged Tier 2 action; it
is NOT automated by any builder.

## Lineage requirements

The metadata block alone must let a future operator reproduce a
training run from this dataset. That means:

- `generation_commit_sha` points at a commit that contains the
  builder source code that produced the artifact.
- `source` names the upstream system (e.g. `trade_journal.db`).
- `notes` records any non-default builder kwargs (e.g.
  `strategy_version=vwap-v2`).

If any of these are stale, the dataset is presumed unreproducible
and must NOT be used for training a candidate destined for any
live-influence tier.

## Update rule

Changes to this policy require an update in the same PR to
[`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
§ Architecture Change Log.
