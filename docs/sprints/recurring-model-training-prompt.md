# Recurring Model Training & Evaluation Session Prompt

**Type**: Recurring (weekly, aligned to HF cron)
**Cap**: 6 hours (most offloaded to Colab/HF)
**Spec**: `docs/claude/recurring-sessions.md`
**Format**: Phase 1 (E2E) → Phase 2 (Train + eval) → Phase 3 (Summary ping)

This file is loaded at the start of every recurring model training session. Read CLAUDE.md and `docs/claude/ml-training-policy.md` first.

---

## Critical Rule

**This session NEVER promotes a model to live.** It only **trains** and **evaluates** and **proposes**. Promotion is a Tier 3 sprint with operator review.

---

## Phase 1 — E2E Health Check

Run the standard health check from `recurring-hardening-prompt.md` Phase 1, plus ML-specific checks:

### 1A. Latest model artifact loadable
- Pull the current production model from HF registry.
- Confirm it loads without error (`huggingface_hub` + the relevant loader).
- Confirm signature matches expected (input shape, output shape, version metadata).

### 1B. Training pipeline freshness
- `.github/workflows/training-run.yml` last run successful (no red).
- `.github/workflows/hf-cron.yml` last run successful.
- Last successful training artifact ≤ 7 days old.

### 1C. Data freshness
- Latest training dataset on HF ≤ 7 days old.
- Holdout split timestamp consistent (no leakage from new data into eval).

### 1D. Live signal-to-decision parity
- Spot-check: for the most recent 5 live signals, the model's decision in production matches what re-running it locally on the same input would give. (Catches model-version drift.)

If anything fails, follow the standard outcome routing (pivot/defer/proceed with operator approval).

---

## Phase 2 — Training & Evaluation

### 2A. Pull fresh data
Per `docs/claude/ml-training-policy.md`:
- Trigger HF dataset refresh if needed.
- Confirm data quality: no NaNs, expected feature distributions, no duplicate rows.

### 2B. Trigger training in Colab
Per `docs/claude/colab-workflows.md`:
- Prepare or update `notebooks/training/<strategy>_train.ipynb`.
- Operator triggers Run All in Colab — Claude does NOT run training locally.
- Training output: model artifact + training metrics, pushed to HF model registry as a candidate (not promoted).

### 2C. Evaluate against incumbent
Once training completes, run evaluation:
- Holdout set: same as incumbent's holdout for fair comparison.
- Fixed metrics:
  - Win rate
  - Sharpe ratio
  - Max drawdown
  - R-multiple distribution (mean, median, P95, P5)
  - Trade frequency
  - Calibration error (predicted prob vs actual)
- Run on the same dates as incumbent for direct comparison.

### 2D. Decision matrix
For each candidate vs incumbent:

| Metric | Incumbent | Candidate | Δ | Promote? |
|--------|-----------|-----------|---|----------|
| Win rate | ... | ... | ... | ... |
| Sharpe | ... | ... | ... | ... |
| Max DD | ... | ... | ... | ... |
| ... | ... | ... | ... | ... |

**Promote criteria** (all must be true):
- Sharpe improvement ≥ 0.2
- Max DD no worse than incumbent + 1pp
- Win rate within ±3pp of incumbent (we don't want a model that wins less but bets bigger)
- Calibration error not worse
- Live signal count within ±20% of incumbent (sanity)

### 2E. Write evaluation report
At `docs/model-evals/model-<strategy>-YYYYMMDD.md`:

```markdown
# Model Evaluation — <strategy>_<version> — YYYY-MM-DD

## Candidate
- HF artifact: <link>
- Training data: <range>
- Training time: <minutes>

## Incumbent
- HF artifact: <link>
- Live since: YYYY-MM-DD

## Holdout evaluation

| Metric | Incumbent | Candidate | Δ |
|--------|-----------|-----------|---|
| ... | ... | ... | ... |

## Recommendation
PROMOTE | REJECT | DEFER

## Rationale
...

## Next steps
- [ ] Operator approves recommendation
- [ ] If PROMOTE: file Tier 3 sprint for live promotion (model swap + live monitoring plan)
- [ ] If REJECT: archive candidate, document learnings
- [ ] If DEFER: extend evaluation window, retrain with adjusted hyperparams
```

---

## Phase 3 — Summary Ping

```
🤖 Model Training — YYYY-MM-DD

Strategy: <name>
Candidate trained: ✅
Holdout eval: <pass/fail/marginal>
Recommendation: PROMOTE | REJECT | DEFER
Report: <link to docs/model-evals/...>
Next training: YYYY-MM-DD
Time: <total>
```

Append checkpoint per CLAUDE.md.

---

## What this session is NOT for

- Promoting a model to live (Tier 3 sprint).
- Strategy parameter tuning (that's strategy improvement session).
- Architectural changes to the training pipeline (that's a feature sprint).
- Adding new strategies (feature sprint).
- Hyperparameter sweeps (offload to scheduled sweeps in Colab/HF, review results here).

---

## Reference

- Master spec: `docs/claude/recurring-sessions.md`
- ML policy: `docs/claude/ml-training-policy.md`
- Training workflow: `docs/claude/training-improvement-workflow.md`
- Colab workflows: `docs/claude/colab-workflows.md`
- HF workflows: `docs/claude/huggingface-workflows.md`
- Hardening prompt: `docs/sprints/recurring-hardening-prompt.md`
- Strategy review prompt: `docs/sprints/recurring-strategy-improvement-prompt.md`
