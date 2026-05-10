# Hugging Face dataset publishing workflow

> **Status:** Canonical. Adopted in **S-AI-WS3** (2026-05-10).
>
> **Scope:** This doc describes the *workflow*. It does NOT add a
> Python dependency on `huggingface_hub` or wire any auto-push
> behavior into the bot. Publishing is an explicit operator action.

## When to publish

A dataset is a candidate for Hugging Face publication when **all**
of the following are true:

1. Its row in [`docs/data/dataset-taxonomy.md`](../data/dataset-taxonomy.md)
   has a buildable builder.
2. The builder has produced a versioned artifact under the canonical
   layout (see [`versioning-policy.md`](../data/versioning-policy.md)).
3. `python -m ml.datasets validate <path>` returns exit code 0.
4. `leakage_test_status` is `passed` or `n/a`.
5. The operator has explicitly approved publication.

## What lives where (WS9 split)

- **Local artifacts** — `<output_dir>/<family>/<scope>/<tf>/vNNN/`
  on the build host (developer laptop, GitHub Actions runner, or
  Hugging Face Space; **not** the Oracle live VM for anything
  bigger than a smoke build).
- **Hugging Face dataset repo** — same path layout under a single
  repo per family (or per project, depending on org policy). Repo
  visibility starts as **private** until the operator decides
  otherwise.

WS9 in [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md)
codifies this split: heavy training and large dataset builds run on
Hugging Face / dev machines, not on the Oracle live VM.

## Manual publish flow (today)

Until a dedicated CLI subcommand lands (filed for a follow-up
sprint), publication is two steps:

1. Build the dataset locally:

   ```
   python -m ml.datasets build backtest_results \
     --output-dir ./out \
     --version v001 \
     --source trade_journal.db \
     -- db_path=/abs/path/to/trade_journal.db
   ```

2. Validate, then upload via the Hugging Face web UI or `huggingface-cli`
   (installed only on the build host, never on the Oracle VM):

   ```
   python -m ml.datasets validate ./out/backtest_results/all/all/v001
   huggingface-cli upload <hf-repo> ./out/backtest_results/all/all/v001 \
     backtest_results/all/all/v001 --repo-type dataset
   ```

The HF token used MUST have write access only to the dataset repo
in question. Do NOT use a token with org-wide write privileges.

## Hugging Face dataset card (README.md)

Every published dataset family carries a Hugging Face dataset card
at the family root in the HF repo. The card MUST include:

- A pointer back to this repo and the canonical taxonomy /
  schema docs.
- The family's source description (e.g. `trade_journal.db`).
- A statement that the dataset is for research / training only and
  must not be construed as financial advice.
- The data tier (raw vs derived vs labeled) and the leakage-test
  status.
- A reference to the `metadata.json` schema in
  [`dataset-schema.md`](../data/dataset-schema.md).

A template card is filed as a follow-up.

## Forbidden

- Publishing any dataset whose `metadata.json` carries
  `generation_commit_sha=unknown`.
- Publishing any dataset whose `leakage_test_status` is `failed`
  (failed datasets are diagnostic only).
- Auto-publishing on push. Every publication is operator-driven.
- Running heavy dataset builds (full historical pull, multi-symbol,
  multi-timeframe) on the Oracle live VM. WS9.

## Update rule

Changes to this workflow require an update in the same PR to
[`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
§ Architecture Change Log.
