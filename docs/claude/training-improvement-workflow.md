# Training / improvement session workflow

How Claude runs an autonomous "improve the strategy or models" cycle.

The session is split into **four stages**, three of which can run hands-off
for the operator. Pings (via PR / commit titles → existing Telegram wiring)
fire at each stage boundary so the operator always knows where they are.

```
Stage 1: Research + hypotheses              →  ping: TRAINING-START
Stage 2: PLAN.md + hypotheses.py committed  →  ping: TRAINING-PLAN PR opened
        (push triggers GitHub Action; operator does nothing)
Stage 3: GitHub Action finishes, commits    →  ping: TRAINING-RESULTS PR opened
Stage 4: Review + recommendations           →  ping: RECOMMENDATIONS (PM REVIEW) PR opened
        (operator approves; Claude opens a follow-up IMPLEMENT: PR with
         actual code changes against the live strategy / model)
```

All four pings ride on existing telegram wiring — see
[`telegram-pings.md`](telegram-pings.md). No new infra, just title
conventions the VM-side script already greps for.

---

## Stage 1 — Research + hypotheses (Claude, local session)

When the operator says "let's run a training/improvement session on
\<strategy/model\>", Claude:

1. Append a checkpoint with `[TRAINING-START] <strategy>` in the title
   to `docs/claude/checkpoints/CHECKPOINT_LOG.md`. The commit fires the
   session-start ping.
2. Review **current state**:
   - Read the strategy/model code under `src/units/strategies/` (or the
     ML training entry point under `ml/`).
   - Read the most recent backtest / performance numbers (latest
     entries in `experiments/` if present, dashboards alerts, hourly
     reports). If nothing recent exists, propose a baseline backtest
     as the first hypothesis.
   - Skim `docs/claude/bug-log.md` for known weaknesses in the area.
3. Open-source / external research (**wide scope, free sources only**):
   - HuggingFace MCP: `paper_search`, `hub_repo_search` for relevant
     models / techniques / papers.
   - Web search for recent (≤ 12 months) blog posts, repos, and
     papers on the technique.
   - Free market-data sources for any context lookups (Bybit public,
     Coinbase public, Kraken public, CryptoCompare, yfinance, our HF
     datasets) — same rules as `testing-policy.md`.
   - **Do not use paid data sources** (e.g. Bigdata.com MCP). If a
     hypothesis genuinely requires paid data to evaluate, drop it
     and note the reason in PLAN.md.
4. Produce a **hypotheses table** in the Stage-2 plan doc (next stage):
   | # | Hypothesis | Why we think it helps | How we test it | Success metric |
   Aim for 3–5 hypotheses, ranked by expected impact / cost.

**Stop conditions for Stage 1:**
- Hypotheses table is empty or all entries are speculative → ask the
  operator (`[BLOCKED-PM]`) which direction to take. Do not invent.
- Research surfaces a known dead-end (paper retracted, repo abandoned,
  technique already tried per `bug-log.md`) → drop the hypothesis,
  note it in the plan.

---

## Stage 2 — PLAN.md + hypotheses.py, committed for autonomous run

Claude writes:

1. `experiments/<run-id>/PLAN.md` — hypothesis table, datasets used,
   compute budget, expected runtime, what "success" looks like per
   hypothesis. `<run-id>` = `YYYY-MM-DD-<slug>` (e.g.
   `2026-05-01-vwap-htf-filter`).
2. `experiments/<run-id>/hypotheses.py` — Python module that defines:
   - `setup(ctx)` (optional) — pre-fetches data into `ctx`, populated
     fields available to all hypotheses.
   - `def H1(ctx) -> dict`, `H2(ctx)`, ... — each returns
     `{'metrics': {...}, 'baseline_metrics': {...}, 'summary_md': str}`.
   - `HYPOTHESES = [('H1', H1), ('H2', H2), ...]` — ordered registry.
   The module imports `scripts.training.{data_loader, backtest_helpers}`
   for shared helpers and the strategies under test from
   `src.units.strategies.*`.

The push to `claude/training-plan-<run-id>` triggers
`.github/workflows/training-run.yml` automatically (it filters on
changes to `experiments/*/hypotheses.py`). The Action:

- installs `requirements.txt` + `yfinance`,
- runs `python scripts/training/run_experiment.py --run-id <run-id>`,
- commits results to `claude/training-results-<run-id>`,
- opens a draft PR titled `TRAINING-RESULTS: <run-id>`.

3. Open a draft PR titled `TRAINING-PLAN: <run-id>` with PLAN.md +
   hypotheses.py. Body: hypothesis summary, expected runtime, link to
   the Action run. This PR fires the "notebook ready" ping (kept name
   for backwards compat, even though there's no notebook anymore).

**The operator's manual action in the entire workflow: zero.** Pings #1-#3
arrive automatically; only ping #4 (recommendations writeup) needs
operator decision.

**Data sources** — free only: yfinance, Coinbase public, Bybit public,
our HF datasets. Loader prints fallback diagnostics on each failure.
See [`testing-policy.md`](testing-policy.md#test-data-sources-read-first).

**Why GitHub Actions, not Colab?** Free Colab disconnects after ~90 min
of tab inactivity, so "close the tab and walk away" doesn't actually
work without paying for Colab Pro. GitHub Actions runs to completion
(6 hr cap, plenty for our backtests), free, and naturally tied to the
git push that ends Stage 2. No new infra to maintain.

---

## Stage 3 — GitHub Action runs autonomously, commits results

Triggered automatically by Stage 2's push of `hypotheses.py`. The
Action job (`training-run.yml` → `run`):

1. Checks out the plan branch.
2. Installs deps and runs `scripts/training/run_experiment.py`.
3. Per hypothesis: writes `experiments/<run-id>/results/<hid>/{metrics.json, summary.md}`.
4. Failures get `FAILURE.md` instead of `summary.md`; the run continues.
5. Aggregates `experiments/<run-id>/results/SUMMARY.md`.
6. Commits to `claude/training-results-<run-id>` and opens
   `TRAINING-RESULTS: <run-id>` (or `TRAINING-RESULTS [FAILED]:` if any
   hypothesis errored) via `gh pr create`.

The PR opening fires the "training done" ping.

**No artifact bigger than ~10 MB goes into the PR.** Large model
weights / datasets get pushed to Hugging Face under the org and the
PR carries the HF URL. See
[`huggingface-workflows.md`](huggingface-workflows.md).

---

## Stage 4 — Claude reviews, recommends, and (after approval) ships

Trigger: a `TRAINING-RESULTS:` PR is opened.

> **Auto-trigger note:** ideally the VM detects the PR opening and
> spawns a Claude session automatically. That wiring is a follow-up
> sprint flagged for PM review (touches `deploy/`). Until it lands,
> the operator manually starts a session via Telegram `/vm` or the
> web UI when the "training done" ping arrives. See
> § "VM auto-trigger (follow-up sprint)" below.

The reviewing Claude session:

1. Pulls the `claude/training-results-<run-id>` branch and reads
   `experiments/<run-id>/results/SUMMARY.md` + the per-hypothesis
   files.
2. For each hypothesis, decides: **adopt / reject / needs more data**.
3. Writes a **strategy-level writeup** (no code) to
   `experiments/<run-id>/RECOMMENDATIONS.md` on a new branch
   `claude/recommendations-<run-id>`.
4. Opens a draft PR titled
   `RECOMMENDATIONS (PM REVIEW): <run-id>`. The PR diff is the
   writeup only — no source-code changes. Body MUST contain:
   - Per-hypothesis result summary (1 line each).
   - **Proposed strategy-level change** (what changes about how the
     strategy behaves — entry/exit logic, sizing, regime filter, etc.
     — described at the level of a trader explaining a rule change,
     not a diff).
   - Why we should make the change (mechanism + supporting metric).
   - Expected impact on live (sharpe lift, drawdown change, trade
     frequency change, anything operator cares about).
   - Risks / what could go wrong post-deploy.
   - The chat link, so the operator can reply in the session that
     opened the PR.
5. **Stop and wait.** Per the autonomous-live-trading rule in
   `CLAUDE.md`, the *system* is pre-approved but a *strategy change*
   is not — that's the moment the operator gets to steer. The PR
   opening fires the "recommendations ready" ping.

After the operator approves the strategy-level writeup (in chat or
by approving the PR):

6. The reviewing session merges the writeup-only PR into `main`
   (docs-only, no code risk).
7. Opens a **separate** implementation PR `IMPLEMENT: <run-id>`
   on branch `claude/implement-<run-id>` with the actual code
   changes against `src/units/strategies/` (or `ml/` etc.). This
   PR is reviewed and merged under the existing PM-review gate
   for live trading code (`src/units/strategies/`,
   `src/runtime/orders.py`, etc., per `CLAUDE.md` § Merging Rules).
8. Final checkpoint with `CP-…-COMPLETE` (training session id) →
   sprint-end ping.

**Why split writeup and implementation?** The operator reviews
behaviour, not diffs. The strategy-level writeup is the actual
decision; the implementation PR is the mechanical translation
(and gets its own narrower PM review for the code itself).

---

## Mid-session input (any stage)

If Claude needs operator input at any point:

1. Commit `[BLOCKED-PM] <one-line question>` (fires urgent ping).
2. Open a draft PR `BLOCKED: <one-line question>` (fires GitHub
   notification).
3. **Body must include the chat link** so the operator can click,
   answer in the same session, and let Claude continue.
4. Stop. Don't start unrelated work.

This is the existing escalation contract from
[`telegram-pings.md`](telegram-pings.md#blocker-pings--escalation-contract);
training sessions just reuse it.

---

## Ping summary (for cross-reference)

| Stage boundary | Trigger | Existing wiring it rides on |
|---|---|---|
| Session start | Checkpoint commit with `[TRAINING-START]` in title | "checkpoint appended" ping |
| Plan + run started | PR opened with `TRAINING-PLAN:` title; the GitHub Action then runs autonomously | "PR opened (DRAFT for PM review)" ping (treats `TRAINING-PLAN:` as PM-relevant, see `telegram-pings.md`) |
| Training done | PR opened with `TRAINING-RESULTS:` title | same as above |
| Recommendations ready | PR opened with `RECOMMENDATIONS (PM REVIEW):` title (writeup only) | matches existing `(PM REVIEW)` convention |
| Implementation ready (post-approval) | PR opened with `IMPLEMENT:` title | generic PR-opened ping; PM-review gate applies because it touches live trading code |
| Mid-session block | `[BLOCKED-PM]` commit + `BLOCKED:` PR | existing blocker ping (urgent) |

No new ping infra. Only new title conventions, and the VM-side
script's title-grep list needs the four new prefixes added.

---

## File / branch conventions (single source of truth)

| Thing | Location |
|---|---|
| Run-id format | `YYYY-MM-DD-<slug>`, slug = lower-kebab description |
| Plan branch | `claude/training-plan-<run-id>` |
| Plan doc | `experiments/<run-id>/PLAN.md` |
| Hypotheses module | `experiments/<run-id>/hypotheses.py` |
| Shared helpers | `scripts/training/{run_experiment,data_loader,backtest_helpers}.py` |
| GitHub Action | `.github/workflows/training-run.yml` |
| Results branch (Action pushes here) | `claude/training-results-<run-id>` |
| Results dir | `experiments/<run-id>/results/` |
| Recommendations branch (writeup only) | `claude/recommendations-<run-id>` |
| Recommendations doc | `experiments/<run-id>/RECOMMENDATIONS.md` |
| Implementation branch (after approval) | `claude/implement-<run-id>` |
| Large artifacts | Hugging Face under our org, URL referenced from PR |

`experiments/` is gitignored except for `PLAN.md`, `RECOMMENDATIONS.md`,
`hypotheses.py`, and the text/metrics under `results/`.

---

## VM auto-trigger (follow-up sprint, PM review required)

To make Stage 4 truly hands-off the operator should not have to start
a Claude session when the "training done" ping arrives. The wiring:

1. The VM's existing `ict-git-sync.timer` already pulls `main` every
   5 minutes and runs `scripts/deploy_pull_restart.sh`.
2. Extend that script (or add a sibling) to detect new
   `TRAINING-RESULTS:` PRs via `gh pr list --search 'TRAINING-RESULTS
   in:title is:open'`, dedupe against a state file
   `/var/lib/ict-trader/seen-training-prs.txt`, and POST a
   "start review session" message to the Telegram bot which
   already has a `/vm_write` path that can spawn Claude Code on
   the web.
3. Idempotent: re-running the script when no new PR exists sends
   nothing.
4. State file location and Telegram payload format need PM sign-off
   before deploy. Per `CLAUDE.md` merging rules, anything in `deploy/`
   needs PM review — so this lands in its own sprint, not as part of
   the workflow rollout.

Until that ships, the operator does the one manual action: tap the
Telegram link in the "training done" ping, which opens the PR; from
there, "open a Claude Code session on this PR" via the web UI. The
session reads the workflow doc you're reading now and continues with
Stage 4.

---

## Cross-references

- [`ml-training-policy.md`](ml-training-policy.md) — what Claude does
  vs. delegates for ML work.
- [`huggingface-workflows.md`](huggingface-workflows.md) — large
  model / dataset storage.
- [`telegram-pings.md`](telegram-pings.md) — ping contract this
  workflow rides on.
- [`external-delegation.md`](external-delegation.md) — Claude
  orchestrates, GitHub Actions / HF / VM execute.
- `.github/workflows/training-run.yml` — the GitHub Action that runs
  Stage 3.
- `scripts/training/run_experiment.py` — the orchestrator the Action
  invokes; loads `experiments/<run-id>/hypotheses.py` and writes results.
- Removed: `notebooks/templates/training_improvement_template.ipynb`
  and `notebooks/training/<run-id>.ipynb` — the prior Colab-based path.
  Replaced by GitHub Actions because free Colab disconnects after ~90 min
  idle.
