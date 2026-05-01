# Training / improvement session workflow

How Claude runs an autonomous "improve the strategy or models" cycle.

The session is split into **four stages**, three of which can run hands-off
for the operator. Pings (via PR / commit titles → existing Telegram wiring)
fire at each stage boundary so the operator always knows where they are.

```
Stage 1: Research + hypotheses     →  ping: TRAINING-START
Stage 2: Notebook + plan committed →  ping: TRAINING-PLAN PR opened
        (operator opens Colab, runs all cells, closes the tab)
Stage 3: Colab finishes, commits   →  ping: TRAINING-RESULTS PR opened
Stage 4: Review + recommendations  →  ping: RECOMMENDATIONS (PM REVIEW) PR opened
        (operator approves; Claude opens a follow-up PR with the
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
3. Open-source / external research (**wide scope**):
   - HuggingFace MCP: `paper_search`, `hub_repo_search` for relevant
     models / techniques / papers.
   - Bigdata.com MCP: market regime context for the symbols /
     timeframes being studied (single focus, single time period per
     call — see the MCP's own discipline rules).
   - Web search for recent (≤ 12 months) blog posts, repos, and
     papers on the technique.
4. Produce a **hypotheses table** in the Stage-2 plan doc (next stage):
   | # | Hypothesis | Why we think it helps | How we test it | Success metric |
   Aim for 3–6 hypotheses, ranked by expected impact / cost.

**Stop conditions for Stage 1:**
- Hypotheses table is empty or all entries are speculative → ask the
  operator (`[BLOCKED-PM]`) which direction to take. Do not invent.
- Research surfaces a known dead-end (paper retracted, repo abandoned,
  technique already tried per `bug-log.md`) → drop the hypothesis,
  note it in the plan.

---

## Stage 2 — Notebook + plan, committed for one-click run

Claude writes:

1. `experiments/<run-id>/PLAN.md` — hypothesis table, datasets used,
   compute budget, expected runtime, what "success" looks like per
   hypothesis. `<run-id>` = `YYYY-MM-DD-<slug>` (e.g.
   `2026-05-01-vwap-htf-filter`).
2. `notebooks/training/<run-id>.ipynb` — a copy of
   `notebooks/templates/training_improvement_template.ipynb` with the
   experiment-specific cells filled in. The notebook MUST satisfy:
   - **One-click**: Runtime → Run all; no human input after that.
   - Reads `GITHUB_TOKEN` and `GITHUB_USERNAME` from
     `google.colab.userdata` (already in the operator's Colab).
   - Clones the repo at the current branch SHA, runs experiments,
     writes outputs to `experiments/<run-id>/results/`.
   - Hardens against long runs: every long step is checkpointed to
     Drive so a Colab disconnect doesn't lose hours of work.
   - On success **or** failure, commits results to a fresh branch
     `claude/training-results-<run-id>` and opens a draft PR titled
     `TRAINING-RESULTS: <run-id>` (or `TRAINING-RESULTS [FAILED]:
     <run-id>` on failure) via the GitHub REST API.
3. Open a draft PR titled `TRAINING-PLAN: <run-id>` with the notebook
   + plan. Body: link to the Colab "Open in Colab" URL, expected
   runtime, hypothesis summary. This PR fires the "notebook ready"
   ping.

The operator's only manual action in the entire workflow:
**click the Colab link, click Runtime → Run all, close the tab.**

**Data sources** — same rules as everywhere: no Binance, prefer
HF datasets / Bybit public / repo fixtures. See
[`testing-policy.md`](testing-policy.md#test-data-sources-read-first).

---

## Stage 3 — Colab runs autonomously, commits results

The notebook (running on Colab):

1. Executes the experiments per PLAN.md.
2. Writes per-hypothesis result files under
   `experiments/<run-id>/results/<hypothesis-id>/`:
   - `metrics.json` (sharpe, drawdown, win-rate, etc.)
   - `equity_curve.png` (or whatever plots are relevant)
   - `summary.md` (one paragraph: did it beat baseline, by how much)
3. Writes `experiments/<run-id>/results/SUMMARY.md` aggregating all
   hypotheses into a single ranked table.
4. Commits everything to `claude/training-results-<run-id>` and opens
   a draft PR `TRAINING-RESULTS: <run-id>` via GitHub API.
5. If a step fails, the notebook still commits whatever partial
   results exist plus a `FAILURE.md` traceback, and opens the PR with
   `[FAILED]` in the title so the operator knows to check.

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
3. Drafts the actual code changes against `src/units/strategies/`,
   `src/units/accounts/`, or `ml/` as appropriate — but commits them
   to a **separate** branch `claude/recommendations-<run-id>`.
4. Opens a draft PR titled
   `RECOMMENDATIONS (PM REVIEW): <run-id>`. Body MUST contain:
   - Per-hypothesis result summary (1 line each).
   - Proposed change (file + 1-line description).
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

After the operator approves (in chat or by approving the PR):

6. The reviewing session merges `claude/recommendations-<run-id>`
   into `main`. This is the only stage that touches live strategy
   code, so it fits the existing PM-review gate from `CLAUDE.md`.
7. Final checkpoint with `CP-…-COMPLETE` (training session id) →
   sprint-end ping.

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
| Notebook ready | PR opened with `TRAINING-PLAN:` title | "PR opened (DRAFT for PM review)" ping (treats `TRAINING-PLAN:` as PM-relevant, see `telegram-pings.md`) |
| Training done | PR opened with `TRAINING-RESULTS:` title | same as above |
| Recommendations ready | PR opened with `RECOMMENDATIONS (PM REVIEW):` title | matches existing `(PM REVIEW)` convention |
| Mid-session block | `[BLOCKED-PM]` commit + `BLOCKED:` PR | existing blocker ping (urgent) |

No new ping infra. Only new title conventions, and the VM-side
script's title-grep list needs the four new prefixes added.

---

## File / branch conventions (single source of truth)

| Thing | Location |
|---|---|
| Run-id format | `YYYY-MM-DD-<slug>`, slug = lower-kebab description |
| Plan + notebook branch | `claude/training-plan-<run-id>` |
| Notebook copy | `notebooks/training/<run-id>.ipynb` |
| Plan doc | `experiments/<run-id>/PLAN.md` |
| Results branch (Colab pushes here) | `claude/training-results-<run-id>` |
| Results dir | `experiments/<run-id>/results/` |
| Recommendations branch | `claude/recommendations-<run-id>` |
| Large artifacts | Hugging Face under our org, URL referenced from PR |

`experiments/` is gitignored except for `PLAN.md` and `results/`
text/metrics. The notebook template enforces this.

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
- [`colab-workflows.md`](colab-workflows.md) — notebook conventions,
  data-source rules.
- [`huggingface-workflows.md`](huggingface-workflows.md) — large
  model / dataset storage.
- [`telegram-pings.md`](telegram-pings.md) — ping contract this
  workflow rides on.
- [`external-delegation.md`](external-delegation.md) — Claude
  orchestrates, Colab/HF/VM execute.
- `notebooks/templates/training_improvement_template.ipynb` — the
  notebook template Stage-2 copies from.
