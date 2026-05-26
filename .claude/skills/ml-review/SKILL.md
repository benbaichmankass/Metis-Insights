---
name: ml-review
description: Autonomous review of the ICT bot's ML LIFECYCLE — trainer service health, training cycles since the last review, dataset builds, per-model status (latest training metrics + shadow/live track record), promotion/demotion recommendations against the 7-stage ladder, and AI-experiment proposals to continue expanding ML coverage. Owns docs/claude/ml-review-backlog.json (AI experiment follow-ups, new manifests to try, new features/feeds to engineer). Use when the operator says "run the ml review", "/ml-review", "how are the models doing", or "what should we train next". NOT for live trading promotion past shadow (Tier-3, operator-gated) — this skill proposes, the operator promotes. NOT for system health (use /health-review) and NOT for strategy trade scoring (use /performance-review).
---

# /ml-review — model/training lifecycle review

This is the **ML-lifecycle** session of the three-way review split
(`/health-review` covers system health, `/performance-review` covers
trading + strategy scoring). It reviews the trainer VM, every model in
the registry, and the experiment pipeline — then proposes the next
promotions/demotions and the next experiments to run.

Promoting a model past **`shadow`** (the order-influencing gate) is
**Tier-3** and requires explicit operator approval. This skill emits
the recommendation; the operator decides.

If the user asked about *system/pipeline health* — STOP, use
`/health-review`. If the user asked about *strategy trade
performance / per-decision scoring* — STOP, use
`/performance-review`.

## Scope (what this skill DOES)

1. **Establish the window** — since the last ml-review (§ "The review
   window").
2. **Pull trainer-VM state** through the trainer diag relay
   (§ "Fetching trainer state").
3. **Pull live-VM ML-mirror state** — the file-based mirror surfaced at
   `/api/bot/ml/*` (§ "Fetching live-VM ML state").
4. **Grade trainer-center health** — service, datasets, registry
   (§ "Trainer center rubric").
5. **Emit a per-model status line** for every model in `python -m ml
   list-models` (§ "Per-model status — REQUIRED").
6. **Identify promotion / demotion candidates** against the 7-stage
   ladder (§ "Promotion/demotion recommendations").
7. **Propose AI experiments** — new manifests, new features, new
   datasets, new model families to try (§ "Experiment proposals").
8. **Drain the ml-review backlog** (§ "Draining the backlog").
9. **Emit the response JSON** + **post a one-line update to the Claude
   channel** (§ "Output" + § "Posting to the Claude channel").

## Out of scope (DO NOT do here)

- Pipeline plumbing / DB integrity / service state of the LIVE trader
  → `/health-review`.
- Strategy trade scoring / per-decision A–F grades / strategy tweaks
  → `/performance-review`.
- **Running a training cycle / promoting a model / building a
  dataset** — those actions belong to the `model-training` skill (which
  this skill chains *into* if the operator approves a proposal).
  /ml-review is the analytical step, not the action step.
- Editing `ml/configs/*.yaml` manifests — Tier-3. Propose changes;
  operator decides.

## The review window

Window runs from the last ml-review to now. Determine "last review":

1. The newest `cycle_end` you noted in a prior ml-review JSON
   (recorded in the Claude-channel ping or, if you persisted them,
   the prior review files).
2. Otherwise the cadence of the trainer timer (~24h for the daily
   timer) — review the last cycle.

## Fetching trainer state (use git-actions + diag-data)

The trainer VM is reached via the `trainer-vm-diag.yml` relay
(unrestricted SSH bash; label `trainer-vm-diag-request`). One block
collects everything ml-review needs:

```
cmd: |
  REPO=/home/ubuntu/ict-trading-bot
  echo "=== TRAINER SERVICE ==="
  systemctl is-enabled ict-trainer.service; systemctl is-active ict-trainer.service
  systemctl is-enabled ict-trainer.timer;   systemctl is-active ict-trainer.timer
  systemctl show ict-trainer.service --property=ExecMainStatus,ActiveEnterTimestamp,ActiveExitTimestamp
  echo "=== TRAINER RECENT LOG ==="
  journalctl -u ict-trainer.service -n 200 --no-pager
  echo "=== TRAINER CYCLE LOG ==="
  tail -n 100 "$REPO/runtime_logs/training_cycle.jsonl"
  echo "=== TRAINER DATASET BUILDS ==="
  ls -la "$REPO/datasets-out/" 2>/dev/null; tail -n 40 "$REPO/runtime_logs/trainer/dataset_builds.jsonl"
  echo "=== TRAINER REGISTRY ==="
  cd "$REPO" && .venv/bin/python -m ml list-models
  echo "=== TRAINER RESOURCES ==="
  df -h /home | tail -1; free -m | head -2
```

> Use `python -m ml list-models` — there is **no** `python -m ml.registry list`.

If the trainer relay errors, set the relevant findings to `skip` and
note the failure mode in `anomalies[]`. The trainer is **not** a
live-trading blocker — escalate trainer issues with lower urgency
unless a `live_approved` model is involved.

## Fetching live-VM ML state (use diag-data)

These endpoints surface the trainer mirror on the live VM (so the
dashboard / Streamlit / Android can read it). Pull each through the
diag relay or direct HTTPS:

| Pull | Path | Use |
|---|---|---|
| ML status (mirror health) | `GET /api/bot/ml/status` | mirror age, sync state |
| Latest cycle | `GET /api/bot/ml/cycle` | most-recent trainer cycle the live VM saw |
| Training sessions | `GET /api/bot/ml/sessions` | per-manifest sessions in the window |
| Registry mirror | `GET /api/bot/ml/registry` | what the live VM thinks the registry is |
| Dataset builds | `GET /api/bot/ml/builds` | dataset-build health |
| Live→trainer DB pulls | `GET /api/bot/ml/db_pulls` | sync log |
| Per-run metrics | `GET /api/bot/ml/runs/{manifest}/{run_id}` | drill into a specific run |
| Shadow predictions | `GET /api/bot/shadow/predictions?model_id=X&since=<iso>` | the predictions a shadow model emitted |
| Shadow stats | `GET /api/bot/shadow/stats?model_id=X` | aggregate prediction stats |
| Shadow drift | `GET /api/bot/shadow/drift?model_id=X` | KS + PSI score-distribution drift |
| Trade scores | `GET /api/bot/trades/scores?limit=N` | predictions joined to closed trades — the realized-track-record source |

If the trainer-mirror age (`/api/bot/ml/status`) is far older than the
last trainer cycle, the live VM's view is stale — note that and
prefer the trainer-VM pull as ground truth.

## Trainer center rubric

Grade three roll-ups (each `ok | watch | concern | skip`; `skip` if
the trainer relay errored):

- **`trainer_service`** — `ok` if timer enabled+active and the last
  cycle (`cycle_end` in `training_cycle.jsonl`) is within the cadence
  window (≤24h for a daily timer) with `overall_rc=0`. `concern` on
  non-zero `ExecMainStatus`, persistent `FAILED`/`error` lines, or
  last run >72h.
- **`trainer_datasets`** — `ok` if `datasets-out/` has the expected
  families built within 72h. `concern` if no datasets dir or all
  builds error.
- **`trainer_registry`** — `ok` if ≥1 model at `shadow`+. `concern` if
  the registry is empty or all models stuck at `research_only` (training
  runs, nothing passes eval).

Roll up to `trainer_models` (`ok | watch | concern | skip`): `ok` when
every model retrained in the last cycle with sane metrics; `watch`
when a model's headline metric degraded run-over-run, or a `shadow`
model still has zero predictions long after promotion to shadow;
`concern` (⇒ `operator_attention_required`) only when an
`advisory`+/`live_approved` model — one that influences orders — is
degrading on live/shadow data. A registry of `candidate`/`shadow`
models with healthy metrics and zero predictions is `ok` (expected
pre-activation).

## Per-model status — REQUIRED every run

Emit one entry in `model_status[]` for every model in `python -m ml
list-models`. Each entry:

```
{
  "model_id": "...",
  "stage": "research_only | candidate | backtest_approved | shadow | advisory | limited_live | live_approved",
  "registry_status": "candidate | promoted | ...",
  "last_training": {
    "run_id": "YYYYMMDDThhmmssZ",
    "at": "YYYY-MM-DDTHH:MM:SS+00:00",
    "code_revision": "<sha>",
    "headline_metric": "macro_f1=0.70 | mae=3.26 | winrate=0.55",
    "n_eval": 0,
    "trend_vs_prior_run": "improved | flat | degraded | first_run"
  },
  "live_shadow": {
    "influence": "none(shadow) | advisory | live",
    "predictions": 0,
    "score_summary": "score distribution / mean when predictions > 0, else 'no predictions yet'",
    "drift": "ks=... psi=... verdict=stable|drifting|insufficient",
    "realized": "win-rate / PnL of closed trades this model scored, when joinable; else 'n/a'"
  },
  "note": "<= 160 chars — one-line health verdict"
}
```

For the headline metric pick by family:
- Classification → `macro_f1` + `accuracy`.
- Regression → `mae` / `mse`.
- Winrate → the rate.

For the realized track record, join shadow predictions to closed
trades via `/api/bot/trades/scores`. Distinguish **shadow** (observing,
no order influence) from **advisory+/live** (influencing orders) —
a degrading model that *influences orders* is the urgent case.

**No predictions yet** is a valid, honest status — `predictions: 0`
when a `shadow` model was just promoted and the live trader hasn't
emitted a matching signal yet. Don't paper over it as a gap.

## Promotion/demotion recommendations

For each model, emit a `promotion_recommendations[]` entry **only**
when the data supports one:

```
{
  "model_id": "...",
  "current_stage": "shadow",
  "proposed_stage": "advisory",
  "direction": "promote | demote",
  "tier": 3,
  "evidence": "<= 240 chars — N predictions, win-rate on joined trades, drift verdict, training trend",
  "risk_note": "<= 160 chars — what to watch after applying, kill-switch path"
}
```

The 7-stage ladder (low → high influence):
`research_only → candidate → backtest_approved → shadow → advisory → limited_live → live_approved`.

Promotion gates (the lifecycle, `docs/claude/trainer-vm-mode.md` § 5):
- `research_only → candidate → backtest_approved → shadow` is the
  autonomous trainer track. /ml-review notes when a model is *ready*
  for shadow but does not flip it (the `model-training` skill +
  `promote-stage` action do the flip).
- `shadow → advisory` is **the** live-trading gate — operator
  approval required. This is where /ml-review earns its keep:
  recommend the promotion with evidence, or hold.
- `advisory → limited_live → live_approved` — same Tier-3 gate.
  Recommend only when the prior stage has produced
  statistically-meaningful evidence (typically multi-week shadow with
  hundreds of joined trades).

Demotion: any model influencing orders that degrades on live data is
a candidate for demotion to `shadow`. Demotion to a lower influence
stage is *less* risky than promotion, but still Tier-3 — propose, do
not enact.

If a proposal isn't yet supportable, file it as a backlog item with
the criteria it would need to meet next time.

## Experiment proposals

This is the forward-looking output. For each gap in coverage, emit an
`experiments_proposed[]` entry:

```
{
  "kind": "new_manifest | new_feature | new_dataset_family | new_target | hyperparam_sweep",
  "name": "<short slug>",
  "rationale": "<= 240 chars — what we'd learn",
  "input_changes": "<= 200 chars — datasets / features / horizon involved",
  "expected_metric_signal": "<what would convince us this is worth shadowing>",
  "tier": 3,
  "next_step": "PR a new ml/configs/<name>.yaml | extend ml/features/... | new dataset family in src/ml/datasets/..."
}
```

Examples of valid experiments: a new dataset family for a symbol the
trainer hasn't touched, a feature that prior reviews suspected (cite
the backlog item), an alternative target horizon, a hyperparam sweep
on a stuck model.

## Draining the backlog

Read `docs/claude/ml-review-backlog.json` — the parking lot for
**AI experiments, manifest ideas, feature engineering follow-ups,
promotion-criteria notes** from prior sessions. (Health and
performance backlogs are not touched here.) For each open item:

1. Triage: still valid? does the new cycle's data close it?
2. **Act on what you can** — convert resolved items to a closed
   `proposed_tweak` / `experiment` in this review's output, or close
   as `invalid`; otherwise leave open with any new evidence appended.
3. Edit the backlog file: mark resolved/invalid items, keep deferred
   items. Record each action in `backlog_drain[]`.

New backlog items added here are for **ML/experiment follow-ups
only**. Each item carries `id`, `opened_at`, `opened_by`, `source`,
`title`, `description`, `tier` (typically 3), `trigger_condition`,
`resolution_criteria`, `status`.

## Posting to the Claude channel

Every ml-review ends with a one-line update to
`@claude_ict_comms_bot`. Primary path:

```
action: send-ping
target: claude
priority: normal      # 'high' only if a live_approved/advisory model is degrading
message: /ml-review — <N> models, <K> retrained, <P> proposed promotions, <D> demotions, <E> experiments. trainer=<ok|watch|concern>.
```

≤200 chars. Cite the trainer-center grade + counts. Point at the
response JSON for detail. Fallback: append to
`docs/claude/pending-pings.jsonl`.

## Output

Emit a single JSON object conforming to
`comms/schema/ml_review_response.template.json`:

- `reviewed_at`, `reviewer: "claude"`, `window_start`, `window_end`.
- `overall_assessment` ∈ `healthy | caution | investigate`.
- `trainer_findings`:
  - `trainer_service`, `trainer_datasets`, `trainer_registry`,
    `trainer_models` — each `{status, note}`.
- `model_status[]` — REQUIRED, one entry per model in the registry
  (§ above); `[]` only when the trainer relay errored (then
  `trainer_models: skip`).
- `promotion_recommendations[]` — Tier-3 proposals with evidence;
  empty when none warranted.
- `experiments_proposed[]` — forward-looking experiment ideas.
- `backlog_drain[]` — actions taken on
  `docs/claude/ml-review-backlog.json`.
- `anomalies[]` — free-form notable items (datasets failing, runs
  erroring, predictions silently dropping, etc.).
- `recommended_action`, `operator_attention_required`.

Each `note`/`evidence` cites specific `model_id`s, `run_id`s, and
numbers so the operator can verify fast.

## What you DO write (and what you don't)

**Write:**
- Edit `docs/claude/ml-review-backlog.json` to drain + add new items.
- Post the Claude-channel ping (`send-ping` system-action; fallback
  `docs/claude/pending-pings.jsonl`).
- The read-only diag-trigger issues (`vm-diag-request`,
  `trainer-vm-diag-request`, `vm-web-api-recover`) — they auto-close.

**Do NOT:**
- Touch `src/`, `config/`, `ml/configs/`, or any live-path /
  manifest file. **No exceptions** — manifest/code changes go in
  `experiments_proposed[]` for operator approval.
- Run a training cycle / promote a model / build a dataset from this
  skill — chain into the `model-training` skill if the operator
  approves a proposal.
- Modify `docs/claude/health-review-backlog.json` or
  `docs/claude/performance-review-backlog.json`.
- Append to `comms/claude_strategy_scores.jsonl` — that's
  `/performance-review`.
- Ask the operator to paste/download/SSH — autonomy violation.
- Ask scoping questions — the scope is fixed (this file).

## If the relays are unreachable

If the trainer relay fails, emit a partial review with
`trainer_service: skip` (and the dependent dimensions `skip`),
`model_status: []`, and a note in `anomalies[]`. Still drain the
backlog and still post the Claude-channel ping. Do not synthesize
model data without evidence.

If only the live-VM relay fails, fall back to the trainer-VM view as
ground truth and note the stale-mirror situation.
