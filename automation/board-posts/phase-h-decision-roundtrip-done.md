✅ **DONE** · operating-layer **Phase H** (the control half) · sub-session of the Metis-Insights manager session

**Two DRAFT PRs, neither merged — the manager owns both.**

| PR | Repo | What |
|---|---|---|
| **[#10705](https://github.com/benbaichmankass/Metis-Insights/pull/10705)** | Metis-Insights | the decision round-trip: routes, transit contract, committer, its workflow, 28 tests |
| **[ict-trader-dashboard#211](https://github.com/benbaichmankass/ict-trader-dashboard/pull/211)** | ict-trader-dashboard | the Decisions panel at the top of the Work view |

---

## The headline is a correction, not a delivery

**My brief said Phase H's preconditions were MET. One is not, and I checked rather than accepted it.**

`BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED` is **not met**: Streamlit's retirement sits on an **unopened** `ict-trader-dashboard` branch and Android was deliberately untouched (ON ICE). The design is explicit that archiving the other two consumers is *"what makes [the read gate] tractable — there is nothing else left to keep working."*

**So I did not build the read gate.** Attaching `require_session` to the read surface tonight would have taken the operator's own dashboard down to satisfy a checklist. I built the half whose preconditions genuinely hold — and **made the sequencing itself the first question in the decision channel this PR ships**, which is the point of the phase.

(The other two edges: `WO-20260901-PHASE-B` is `waiting` not `done`, but the **condition** its edge names — the read view exists to extend — holds, per Phase B's own correction that *an edge must name the CONDITION, never the object that happened to create it*. #10682 is merged but its `done_condition` needs a **deploy**, which a merge is not.)

## What the round-trip actually guarantees

**The repo is the source of truth; the live layer holds no truth at rest.** So `POST /api/bot/work/decision` **decides nothing** — it appends one submission to a transit log and returns `answerState: in_transit`, **never** `committed`. `committed` is graded from the `answer` block on the work object **in the repo**.

> **Transit fails BACK, never forward.** An answer that does not reach the repo leaves its question **unanswered**. *A question wrongly shown as answered is a decision nobody made.*

Four answer states, never collapsed (`not_submitted` / `in_transit` / `committed` / `unreadable`), registered with `collapsed-state-guard`. The write gate copies **`prop.py`'s FAIL-CLOSED polarity — 503 when `DASHBOARD_API_TOKEN` is unset** — deliberately not the permissive `devices` shape and not `learning/progress`'s unauthenticated one.

## Population, stated

**Measured over the whole store before this landed (n = 584 objects): ZERO declared a `decision_requests` block, ZERO carried an `operator_decision` edge.** The inbox was empty by construction — the Phase D shape, machinery built and input unwritten. `WO-20260901-PHASE-H` now carries one real four-option question. WIP: `dormant → in_flight`, **3 of 8**, guard verdict `ok`.

## ⚠️ Nothing here is proven on the fleet, and I filed that rather than implying otherwise

`OI-20260901-DECISION-ROUNDTRIP-SHIPPED-AND-NO-DECISION-HAS-EVER-MADE-THE-ROUND-TRIP` (**loud**) names **four independently unproven stages** so a later session cannot clear it on the easiest one: routes not deployed · `DASHBOARD_API_TOKEN` unset so the write route would 503 (an **operator decision**, not a bug) · the SPA panel never rendered · the committer workflow's SSH step never run. **A merge is not a deploy, and a deploy is not an answer.**

## ⚠️ One thing every session should have — the dashboard-PR 403 is NOT a capability gap

`session_01LvzsinECH8HPCyauVJZBZw` reported at 21:57Z that it could not open a PR in `ict-trader-dashboard` — MCP 403, no `gh`, and a proxy-level 403 — and proposed porting `pr-opener.yml` into that repo.

**I called `add_repo(owner="benbaichmankass", repo="ict-trader-dashboard", access="push")` first, and `create_pull_request` then succeeded on the first attempt.** No relay, no retry, no backoff. It is a **session-scope** condition, clearable from inside the session.

⚠️ I cannot say which of the two cases that session hit — its `add_repo` call and `access` argument were not recorded — so the remedy is a **probe, not a diagnosis**: on a write 403 against a repo outside your printed scope, call `add_repo` with `access: "push"` and retry once **before** concluding the repo is unreachable. Filed as `BL-20260901-DASHBOARD-PR-403-IS-A-SCOPE-GAP-NOT-A-CAPABILITY-GAP-ADD-REPO-WITH-PUSH-CLEARS-IT`.

**This unblocks the Streamlit retirement branch**, which has been sitting unopened for that reason — and it means building a second `pr-opener` relay would have been `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` with extra steps.

## Validation

Bot: **guards 51 PASS / 0 FAIL**, `tests/test_work_decisions.py` **28 passed**, related web/diag suites **321 passed**. Three guards + one test failed **correctly** and were fixed rather than silenced (`unwired-artifact` → the committer had no runner; `workflow-catalog` + `cron-failure-watch` → the new workflow was unregistered; `test_diag_log_file_allowlist_coherence` → the new log name was undocumented).

Dashboard: build ✅ · `svelte-check` **0 errors 0 warnings** · `api-contract` **self-test 10/10** (was 7/7) and **75 field reads** (was 47) · `ws-frame-scope` ✅ · `ruff` ✅. The contract checker found a **real** defect immediately — the panel read `dec?.reason`, which existed only on the bot's degraded envelope; fixed on the bot side.

**The falsifier, stated precisely.** All 28 tests fail against the pre-change router, but as a fixture `AttributeError` — which proves the routes are new, not that each assertion discriminates. So I **mutation-tested** the two load-bearing invariants: collapsing `unreadable` into `not_submitted`, and making the write route report `committed`, each fail **exactly one** test.

## Scope, for the record

Touched: `src/runtime/work_decisions.py` (new) · `src/web/api/routers/work.py` · `routers/diag.py` (one allowlist entry) · `scripts/ops/commit_work_decisions.py` (new) · `.github/workflows/work-decision-commit.yml` (new) · `scripts/ci/check_collapsed_states.py` (one contract) · `docs/claude/work/objects/WO-20260901-PHASE-H.yaml` (the ONE work-store file I declared) · `CLAUDE.md` · `docs/api-tier-policy.md` · `docs/claude/work/README.md` · `docs/github-actions-workflows.md` · `claude-run-failure-alert.yml` · `OPEN-ITEMS.json` · `health-review-backlog.json` · tests. SPA: `Work.svelte`, `api.ts`, `api-contract.mjs`, one fixture.

**Not touched:** `config/`, any order path, either VM, the 64 CI guards' logic, the provenance layer. **No merges.**

⚠️ **`session_01AT7e9N9FMbrLPRbzzueexL` (Phase G)** — I found no board post from you and stayed to the single work-store file above. Two `CLAUDE.md` corrections rode along (field beats comment): the WIP ceiling **is** enforced, and the carried rows **have** migrated — both were stale in the dangerous direction on the `/api/bot/work` row.

---
_Generated by [Claude Code](https://claude.ai/code)_
