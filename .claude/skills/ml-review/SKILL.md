---
name: ml-review
description: Autonomous review of the ICT bot's ML LIFECYCLE — trainer service health, training cycles since the last review, dataset builds, per-model status (latest training metrics + shadow/live track record), promotion/demotion recommendations against the 3-stage ladder (candidate→shadow→advisory), per-model fit within the unified-confidence framework, and AI-experiment proposals to continue expanding ML coverage. Owns docs/claude/ml-review-backlog.json (AI experiment follow-ups, new manifests to try, new features/feeds to engineer). Use when the operator says "run the ml review", "/ml-review", "how are the models doing", or "what should we train next". NOT for live trading promotion past shadow (Tier-3, operator-gated) — this skill proposes, the operator promotes. NOT for system health (use /health-review) and NOT for strategy trade scoring (use /performance-review).
---

# /ml-review — model/training lifecycle review

> **⚠️ READ FIRST — WHAT THIS SESSION IS.** This is **full end-to-end QA of the
> ML lifecycle**, NOT a scan-and-sweep-under-the-rug exercise. Your job is to
> actively **HUNT** for issues (broken/degenerate models, stalled cycles, GIGO
> datasets, drift, a live head quietly failing), **ROOT-CAUSE** them, **PROPOSE**
> the exact fix or experiment, decide the Tier-3 promotion/demotion calls **WITH
> the operator**, and then **drive** them. **Finding a fixable issue and logging
> it to a backlog as a post-it note instead of driving it is a REVIEW FAILURE** —
> that is how problems become operational catastrophes. You can ALWAYS weigh in
> with the operator — but raising the flags is YOUR job; never passively wait for
> the operator to point at the problem. This framing governs every review session.

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
6. **Identify promotion / demotion candidates** against the 3-stage
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
  echo "=== STAGE-GUARD (promote/demote/hold per model) ==="
  .venv/bin/python -m ml stage-guard --db data/trade_journal.db
  echo "=== TRAINER RESOURCES ==="
  df -h /home | tail -1; free -m | head -2
```

> Use `python -m ml list-models` — there is **no** `python -m ml.registry list`.

If the trainer relay errors, set the relevant findings to `skip` and
note the failure mode in `anomalies[]`. The trainer is **not** a
live-trading blocker — escalate trainer issues with lower urgency
unless an `advisory` model is involved.

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

**Relay reachability note:** only the `shadow/predictions`, `shadow/stats`,
`shadow/drift`, and `trades/scores` rows above are in the
`vm-diag-snapshot` relay's `/api/bot/*` allowlist — the `ml/*` rows
(`ml/status`, `ml/cycle`, `ml/sessions`, `ml/registry`, `ml/builds`,
`ml/db_pulls`, `ml/runs/*`) are **direct-HTTPS-only** (or the trainer-VM
relay for the underlying trainer-side data). **Batch the relay-eligible
rows into ONE `vm-diag-request` issue** (JSON array or one-per-line body,
e.g. `["shadow/stats?model_id=X", "shadow/drift?model_id=X",
"trades/scores?limit=200"]`) rather than a separate issue per path — per
the `diag-data` skill's default pattern (MB-20260706-CI-MINUTES: this
repo hit its Actions minutes cap opening 427 issues in 5.5 days). The
trainer-VM pull below is already correctly batched into a single
`cmd:` block — keep doing that.

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
  - **Single-manifest OOM quarantine — MANDATORY check
    (BL-20260717-TRAINER-SINGLE-MANIFEST-OOM).** Scan the cycle log
    (`training_cycle.jsonl` / `/api/bot/ml/cycle`) for
    `manifest_quarantine_tripped` and `manifest_quarantined` events. These are
    the trainer's durable escalation that a manifest OOMs *alone* on the 6 GB
    box (it can't commit a backlog item itself). A quarantine trip is a
    **required flag** — it means that manifest hasn't trained for ≥3 cycles.
    You OWN the disposition (Rule 3, `docs/claude/trainer-resource-protocol.md`):
    (a) shrink its peak RSS (dataset chunking / shorter 5m window — a manifest
    change, Tier-3 propose), (b) route it to the GPU burst (note LightGBM is
    CPU-bound, so the burst just gets it off the box — no speedup), or (c)
    drop/split it. Log an `ml-review-backlog` item naming the manifest and the
    chosen disposition; the quarantine self-clears once it trains fit or after
    the recheck window. Known first case: `btc-regime-5m-lgbm-flow-v1`.
- **`trainer_datasets`** — `ok` if `datasets-out/` has the expected
  families built within 72h. `concern` if no datasets dir or all
  builds error.
- **`trainer_registry`** — `ok` if ≥1 model at `shadow`+. `concern` if
  the registry is empty or all models stuck at `candidate` (training
  runs, nothing passes eval).

Roll up to `trainer_models` (`ok | watch | concern | skip`): `ok` when
every model retrained in the last cycle with sane metrics; `watch`
when a model's headline metric degraded run-over-run, or a `shadow`
model still has zero predictions long after promotion to shadow;
`concern` (⇒ `operator_attention_required`) only when an `advisory`
model — one that influences orders — is degrading on live/shadow data.
A registry of `candidate`/`shadow` models with healthy metrics and zero
predictions is `ok` (expected pre-activation).

## Per-model status — REQUIRED every run

Emit one entry in `model_status[]` for every model in `python -m ml
list-models`. Each entry:

```
{
  "model_id": "...",
  "stage": "candidate | shadow | advisory  (canonical 3-stage; normalize legacy names via ml.manifest.canonical_stage)",
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

## Soak integrity audit — REQUIRED every run (operator directive 2026-07-19)

**Why this section exists:** the ETH xa dead-feature bug (BL-20260628-XA-TRAINING-ZERO
— a head soaking for WEEKS on features that were all-zeros at train time) was only
caught at the promotion-decision phase, after the soak completed. That class of
error must be caught at review time, every review. The operating principle is:

> **The soak validates MECHANICS, not edge.** Edge is proven OFFLINE — powered
> walk-forward + historical signal replay over years of data. A live soak exists
> to verify serving mechanics (feature parity, labeling, wiring), which is
> checkable deterministically with a few dozen live decisions. A soak that has
> been running for days without its mechanics verified is not accruing evidence
> — it is accruing risk of exactly this bug class.

For EVERY head at `shadow` or `advisory`, check and report three things:

1. **Train/serve feature parity.** Diff the head's live-logged `feature_row`s
   (shadow_predictions.jsonl) against the same features in its training dataset:
   any feature that is zero-variance/dead on ONE side but live on the other is a
   parity bug — flag it LOUDLY and file it, do not wait for promotion. (This is
   the check that catches the xa class in one review instead of one month.)
2. **Label-accrual coverage.** labeled/total live rows for the head. A large
   unlabeled fraction (the MES 1,213-of-1,861 class — stale candle base blocking
   labeling) means the soak is accruing NOTHING gate-relevant; flag it as a
   data-pipeline bug, not "needs more soak time".
3. **Evidence progress.** What decision will this soak's evidence feed, and is
   that evidence actually accruing (power counters moving)? A soak with no
   destination decision or no accrual is wasted — recommend closing or fixing it.

Report these in a compact `soak_audit[]` block (head, parity ok|BUG, label
coverage %, evidence destination + accrual verdict). A parity or labeling bug
found here is a mandatory flag in the review output and a backlog item.

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

The **3-stage ladder** (canonical since the 2026-06-16 collapse, low →
high influence): `candidate → shadow → advisory`. The legacy 7-stage
names still resolve — `ml.manifest.canonical_stage` maps
`research_only`/`backtest_approved → candidate` and
`limited_live`/`live_approved → advisory`, so old registry rows / pasted
output normalize cleanly; report the **canonical** stage.

Promotion gates (the lifecycle, `docs/claude/trainer-vm-mode.md` § 5):
- `candidate → shadow` is the autonomous trainer track. /ml-review notes
  when a model is *ready* for shadow but does not flip it (the
  `model-training` skill + `promote-stage` action do the flip).
- `shadow → advisory` is **the** live-trading gate — operator
  approval required. This is where /ml-review earns its keep:
  recommend the promotion with evidence, or hold. **Cite the computed
  gate packet** — run `python -m ml gate-check <model_id> --db <journal>
  --datasets-root <datasets-out>` on the trainer VM (S-MLOPT-S4) and
  quote its `ready` verdict + any `blocking[]` gate names in the
  `evidence` field. The gate is the mechanical, pre-registered
  go/no-go (min shadow volume, min days in shadow, **OOS edge vs
  baseline under purged WF-CV**, drift within KS/PSI bounds, live
  agreement); /ml-review should not recommend `promote` while the
  packet reports `ready: false`, and should quote the cleared gates
  when it recommends one. Computing the packet is Tier-1; the flip
  stays Tier-3. `advisory` is now the single influence stage (the old
  `limited_live`/`live_approved` tiers were collapsed into it).

Demotion: any model influencing orders that degrades on live data is
a candidate for demotion to `shadow`. Demotion to a lower influence
stage is *less* risky than promotion, but still Tier-3 — propose, do
not enact.

If a proposal isn't yet supportable, file it as a backlog item with
the criteria it would need to meet next time.

## Underperformer refinement lifecycle (2026-06-23) — REQUIRED each run

Underperforming models are **refined, not abandoned.** Full spec:
[`docs/claude/model-refinement-lifecycle.md`](../../docs/claude/model-refinement-lifecycle.md).
Every `/ml-review`:

1. **Detect** — run `python -m ml stage-guard --db data/trade_journal.db`
   (in the trainer pull above). It proposes `promote | demote | hold` per model
   from the canonical triggers (drift `significant`, live score collapse,
   `brier_lift < 0`, `AUC < 0.5` for `advisory`; all gates pass for `shadow`).
2. **On a `demote` (an `advisory` model degrading)** — recommend the soft-off
   (`advisory → shadow`, Tier-3) in `promotion_recommendations[]` AND open a
   `[refinement]` item in `docs/claude/ml-review-backlog.json` with the trigger
   evidence, a concrete refinement hypothesis, and a `resolution_criteria`
   (re-gate clears → restore; else N=3 attempts → **retire** to `candidate` +
   deprecate the manifest).
3. **Drive each open `[refinement]` item one step** — log a refinement attempt
   (append to `updates[]`), or re-gate, or resolve (`restored` / `retired`).
4. A `shadow` model stuck failing the gate with no path (e.g. a degenerate
   `f1=0` baseline) is the same refine-or-retire question — file it `[refinement]`.

"Turn off" = soft-off (demote to `shadow`, still observes) or **retire**
(demote to `candidate` → shadow factory emits nothing + drop from the training
rotation). Both flips are Tier-3 (`promote-stage`); this skill proposes, the
operator approves. The strategy analogue is `/performance-review`'s
`strategy-refinement-queue.json` + the M7 gate — same detect→refine→restore-or-retire shape.

## Reviewing within the unified-confidence framework (2026-06-16)

The target architecture is `docs/unified-confidence-risk-DESIGN.md`:
model outputs no longer each carry a bespoke gate — they feed composite
**confidence lenses** (conviction + sizing/feasibility + exposure), and
a model's **stage** decides which conviction it feeds (`shadow` → the
observed/logged conviction; `advisory` → the influencing conviction).
So every per-model review now also asks **"how good an input is this to
its lens?"** Concretely, /ml-review must additionally:

1. **Review the conviction meta-model** — `conviction-meta-v1` (family
   `conviction_meta`, a LightGBM stacker over the calibrated lens
   inputs) is the **v2 learned conviction**. Treat it like any model in
   `model_status[]` + the promotion gate, but call out that its
   `shadow → advisory` promotion is **the** switch that turns the
   *learned* conviction live (replacing the formulaic blend). It trains
   on the order-package `(lens inputs → realized win)` rows produced by
   the live observe-only `meta.conviction` soak — so flag if that soak
   isn't accruing.
2. **Check the calibration artifacts** — per-strategy confidence
   calibrators fit by `scripts/ml/fit_confidence_calibrators.py`
   (raw→P(win), isotonic/Platt/decile). Report coverage (which
   strategies are fit; e.g. `ict_scalp_5m` may be pending), staleness,
   and quality (Brier/ECE raw→calibrated). A missing/stale calibrator
   means the live conviction stamp falls back to raw normalization for
   that strategy — note it.
3. **Tag each model's lens role** — regime heads → `c_reg`,
   setup-quality → `c_setup`, trade-outcome → `c_wr` (conviction lens);
   execution-quality / prop-mission → the **sizing** lens. A degenerate
   head (f1=0 — e.g. the trade-outcome / prop baselines) feeding the
   conviction is a *weak input*; surface it as a conviction-quality
   concern, not just a training-metric note.
4. **Stage = influence** under the framework: an `advisory` model's
   output is in the influencing conviction; recommend `shadow→advisory`
   only when the gate packet clears AND the model is a genuinely useful
   lens input (per the calibration / track-record evidence).

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

## Draining the backlog — a HARD COMPLETION GATE (not a sample)

**An ml-review is NOT complete until every open item in
`docs/claude/ml-review-backlog.json` has been triaged THIS run.**
Triaging a sample / "the recent few" is a review failure — the backlog
IS the standing open-task list. (Health and performance backlogs are
not touched here; each of the three reviews enforces this same gate on
its own list.)

**Enumerate the FULL open set, then walk it 100%:**

1. **Count first.** Filter to every item whose `status` is not a
   terminal-resolved value (`resolved`/`closed`/`done`/`invalid`/
   `wont_fix`/`superseded`). Record `open_at_start` — you must touch
   every one.
2. **For EACH open item:** re-validate against this cycle's registry /
   training / drift data; then disposition into exactly one bucket —
   **resolved** (the new cycle closes it / the experiment landed),
   **fixed_now** (an in-scope write — a proposal filed, the backlog
   itself), **invalid/superseded** (stale), or **kept_open** (still
   needs a training run / more soak / a Tier-3 promotion decision — add
   an update with this run's re-validation + the blocker, so it never
   sits stale-and-unlooked).
3. **Write it back** + record EVERY item's disposition in
   `backlog_drain[]` (array length == `open_at_start`).

**Coverage assertion (the gate).** Emit `backlog_coverage:
{open_at_start, triaged, resolved, fixed_now, closed_stale, kept_open,
count_untriaged}`. **`count_untriaged` MUST be 0.** A review with
`count_untriaged > 0` is INCOMPLETE — do not post the ping or report it
done. The ping cites `X/Y backlog items triaged`.

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
priority: normal      # 'high' only if an advisory (order-influencing) model is degrading
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
