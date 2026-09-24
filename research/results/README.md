# `research/results/` — one result record per research run

> **Doc status:** `live` · category `lookup` · last verified `2026-09-22`.
> **Not** in `docs/DOCUMENT-INDEX.md`, and that is correct rather than an
> omission: `check_document_index.py`'s population is `docs/**`, so no file
> under `research/` is in scope — `research/queue/README.md` carries no such
> header for the same reason. Claiming registration here would have been a
> statement the index contradicts.

One file per run: **`research/results/<research_unit>/<run_id>.jsonl`**, written
by [`.github/actions/research-result`](../../.github/actions/research-result/action.yml)
through [`scripts/research/research_result.py`](../../scripts/research/research_result.py),
and validated on every PR by `research-results-guard`
([`scripts/ci/check_research_results.py`](../../scripts/ci/check_research_results.py)).

## Why this exists

MEASURED 2026-09-22 by reading `.github/workflows/`: the research-producing
workflows overwhelmingly end a run with `actions/upload-artifact` plus a
`gh issue comment`. **An artifact EXPIRES** (14 to 90 days) **and an issue
comment is not queryable**, so a result that cost real runner minutes exists
only until it doesn't, and no later session can `grep` for it.

[`docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md`](../../docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md)
§ R1 already asked for this and called it *"the foundation"*. R2
(`assert_rows_landed.py`) shipped — landing is part of the run — but R2 can only
assert that *some* rows reached `main`. It cannot assert that what landed is a
**result**: that it names the question it answers, the rule it was graded
against, what it was measured over, and whether anyone actually looked.

## ⚠️ THIS IS NOT A SECOND CORPUS, AND MUST NOT BECOME ONE

The per-leg measurement rows stay where they are. `docs/research/*-corpus.jsonl`
is the canonical store for what a sweep **measured**; a record here **points at**
those rows (`artifact.store` + `artifact.locator`) and never copies them. Two
stores holding one number is how the two drift — the same reasoning
[`scripts/research/research_disposition.py`](../../scripts/research/research_disposition.py)
gives for keeping the disposition ledger separate from the corpora it reads.

Three stores, three different facts, three different lifetimes:

| store | the fact it holds |
|---|---|
| `docs/research/*-corpus.jsonl` | what a run **measured**, per cell / per leg |
| `research/results/**` | what one run **concluded**, against a rule registered before it ran |
| `docs/research/research-disposition-ledger.jsonl` | whether a human or a session **read** it, and what they decided |

## The first field to read is `read_state`, and it is NOT `verdict`

`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states": *"we did not look"* and
*"we looked and found nothing"* must be distinguishable. So these are two fields
and the validator refuses every combination that folds them:

| `read_state` | means | legal `verdict` | legal `population.n` |
|---|---|---|---|
| `measured` | the producer ran and the numbers are real | `pass` · `fail` · `no_action_warranted` · `indeterminate` | an integer — **`0` is legal and means we LOOKED AND FOUND NOTHING** |
| `no_data` | it ran and the population was empty | `not_applicable` | `null` |
| `producer_failed` | it ran and broke — **we looked, and it broke** | `not_applicable` | `null` |
| `not_attempted` | it did not run — **we did not look** | `not_applicable` | `null` |

⚠️ **Never write `n: 0` for an unmeasured quantity.** A fabricated zero asserts a
measured empty sample and drags any aggregate to a value nobody observed. The
validator refuses it, with a planted negative control in its `--self-test`.

## `research_unit: null` means NOT-QUEUE-DISPATCHED

It does **not** mean "this was only a test", and it waives nothing: a manually
fired run still measured something over some population. Such a result lands
under `_unattributed/` — a named state, rather than loose at the store root
where a path bug would look identical.

## Why the unit id is in the PATH

The key a later session holds is the queue unit. It opens
`research/queue/RQ-20260922-007.yaml` and finds that unit's answers one directory
over, **by name**, with no index and no query engine —
`comms/strategy_evidence/` is the precedent that works for exactly this reason.

`.jsonl` rather than `.json`, even though a file usually holds one line, is what
lets `scripts/ci/assert_rows_landed.py` — the existing owner of *"did my rows
reach `main`"* — read the store verbatim. It also makes `--min-rows` mean
something for a per-leg fan-out: `RQ-20260922-002` wants 8 and `RQ-20260922-006`
wants 12, because there is no partial success when all the legs are read in one
process from one checkout.

## Adding a consumer

One step, at the end of the producing workflow, in a job that checks out with the
PAT (`commit-to-main` requires it — see that action's own header):

```yaml
- uses: ./.github/actions/research-result
  with:
    token: ${{ secrets.BRANCH_PROTECTION_TOKEN }}
    research-unit: ${{ inputs.research_unit }}
    power-state: ${{ inputs.power_state }}
    decision-rule-id: RULE-…
    decision-rule-registered-at: '2026-09-22'
    verdict: ${{ steps.derive.outputs.verdict }}
    read-state: ${{ steps.derive.outputs.read_state }}
    population: ${{ steps.derive.outputs.population }}
    n: ${{ steps.derive.outputs.n }}
    measurement-file: rr/measurement.json
    artifact-store: docs/research/<the store the producer ACTUALLY git adds>
    artifact-locator: <enough to find the rows, or why they are unreachable>
    tool: scripts/research/<what ran>
```

⚠️ **Read `artifact-store` off the producer's own `git add`** — never off a doc,
a backlog row, or a sibling workflow. `assert_rows_landed.py`'s docstring records
what naming the wrong store cost the first time.

Also declare `research_unit` and `power_state` as `workflow_dispatch` inputs:
`scripts/research/dispatch_queue.py` refuses to fire a unit graded `accruing`
unless the unit declares `run.inputs.research_unit`, and `gh workflow run -f
<input-the-workflow-never-declared>` **errors** — so a workflow without the input
is undispatchable for exactly the units that most need the safety label.

## Status — two consumers, and the other seventeen

Wired: `research-exit-head-build.yml` · `m20-exit-lever-sweep.yml`.
The remaining artifact-and-comment-only research workflows are tracked as
follow-up on `docs/claude/work/MANAGER-CHECKLIST.json` row **E5** and in
`docs/claude/work/PIPELINE.jsonl`.

⚠️ **The landing half is proven on first dispatch, not by the PR that added it.**
Both consumers are `workflow_dispatch`-only, so no CI run exercises
`commit-to-main` through this action. The emit + validate + guard half **is**
proven — three self-tests, run in CI. Do not quote this store as end-to-end
verified until a run id can be named.
