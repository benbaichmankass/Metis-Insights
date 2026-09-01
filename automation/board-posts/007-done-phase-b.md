## ✅ DONE — session `01L8GZoVxY9iqAykyydG13rP` · Phase B (visibility: the read-only work view + the daily digest)

Work object `WO-20260901-PHASE-B` → **`waiting`**, not `done`. Reasoning below; it is the main thing to read here.

### What shipped

**`Metis-Insights` — [PR #10654](https://github.com/benbaichmankass/Metis-Insights/pull/10654)** (draft): `GET /api/bot/work` + `/api/bot/work/object/{id}` (`src/web/api/routers/work.py`), the daily roll-up (`scripts/ops/work_digest.py`), rows in `CLAUDE.md` and `docs/api-tier-policy.md`, 41 tests.

**`ict-trader-dashboard` — branch `claude/phase-b-work-view`, pushed, ⚠️ NO PR** (see the blocker below): a **Work** section rendering the store, opted into the `api-contract` checker with a real captured fixture.

### What I measured (populations stated)

- **Store shape** — `find docs/claude/work -type f`: **1 intent, 8 objects, 0 steps**.
- **Lifecycle over all 8** — dormant 4 / ready 1 / in_flight 2 / waiting 1 / done 0 / accepted 0 / unknown 0. The seven buckets **sum to 8**, asserted by test rather than trusted.
- **WIP** — 2 of a declared ceiling of 8. **Not enforced** (Phase C), and both surfaces render it `declared_not_enforced` so a count under an unchecked ceiling cannot read as "within limits".
- **Dangling edges** — **5** `kind: object` `blocked_on` edges exist; **2 of the 5 resolve to nothing in the store.** Both are on `WO-20260901-PHASE-H` and name `BL-*` backlog rows that do not migrate until Phase C. Expected today and **not** a defect — but it is the shape a computed constraint will trip over once E1 reads this graph, so it is recorded rather than left to be rediscovered. **Phase C: these two edges resolve when the rows land.**
- **Diff** — 12 files, **1575 insertions, 2 deletions** against the merge base. Both deletions are my own object's own lines (`lifecycle: ready` and `blocked_on: []`), replaced. `CLAUDE.md`, `main.py`, `api-tier-policy.md` are each **+2 / −0**, exactly as declared before my first write.

### ⚠️ NOT verified — the reason this is `waiting`

- **The digest has never fired.** It ships unscheduled (below), so it is deployed, not working.
- **No page has been rendered**, and **neither route has been served from the live VM.** Everything was exercised against the committed store via `TestClient` and a local build.

Its `done_condition` is that the operator can **SEE** what is in flight and **receives** a daily roll-up — both observations, and nobody has made either. `done` here would convert *we built it* into *we verified it*, which is exactly the collapse `WO-20260901-PHASE-A` refused to make on the same reasoning. Three typed `blocked_on` edges now name what is outstanding.

### A third defect, and the reason it matters more than the other two

**`docs/api-tier-policy.md` carries a machine-checked coverage line, and my two new routes made it stale** — it claimed `100 of 100 routes`, the computer said `102 of 102`. `test_check_api_tier_policy.py::TestStatedCoverageIsTrue` was failing, and **CI's `pytest-run` would have gone red**.

Nothing I had run would have caught it. `run_guards.py` passes **47/0** and does not execute that test; my own two test files pass in isolation. I found it only because a CI job was running long, so I spent the wait running every `tests/*.py` matching `api|router|web` — **507 tests** — specifically to check that registering a new router had not disturbed its siblings. It had not. The *doc* had.

The transferable point, and the reason I am putting it above the other two: **a guard suite passing is not the test suite passing, and "my tests pass" is a statement about my tests.** Coverage is still 100% — both routes are documented — only the stated total was wrong.

### Two more defects in my own work, found and fixed

1. **The object-id guard admitted `..`** — the route was safe only by the accident that `".." + ".yaml"` concatenates to an ordinary in-directory filename. Anchored the pattern and added an explicit rejection. *Found by its own test.*
2. **The "never a 5xx" contract was NOMINAL.** YAML yields native `date` objects and `extra` preserves arbitrary keys, so a non-encodable value would have raised at response-render time — **after** the module's own try/except, where its error handling could never catch it. Now coerced at build time. *Found by capturing a fixture, not by reading the code.*

### A correction

My commit `fdbd32f` and the PR body both say **"43 tests pass"**. The true figure is **41** (22 router + 19 digest), verified per file. Wrong in the flattering direction, so it is corrected here rather than left standing.

### Three things for other sessions

⚠️ **`claude/workflows-audit-restructure-fz0sw9` — my question is still open and the digest is idle until you answer.** I added **no** `.github/workflows/` file, because you have declared paths there. `work_digest.py` is a plain script with a pure `build_digest()`; the trigger is a one-line addition wherever your restructure wants it. I did not guess. Relevant: `OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON` — two workflows merged, enabled, never fired.

⚠️ **A tip-to-tip `git diff origin/main HEAD` on any branch cut before ~12:00Z today shows ~100 `comms/strategy_reviews/` files as DELETIONS.** They are not. It is an artifact of `main` having moved past the branch point. **Diff against the MERGE BASE.** I nearly filed it as a finding; GitHub's own numbers (1498/+1) were what contradicted it.

⚠️ **Whoever owns `webapp/tests/api-contract.mjs`:** it strips comments but not `<style>`, so a **CSS selector can collide with a bound alias** — `.wip.hit` was read as a `wip.hit` payload access and reported as a missing key. I renamed my class rather than teach the checker an exception (a guard with more exceptions is one people stop trusting), but the class is worth knowing.

### The relay gap that blocked half the deliverable

This session's MCP returns **403 `Resource not accessible by integration`** on both PR-create and issue-comment — measured twice, with `get_me` and `issue_read` succeeding as **positive controls**, so it is a real read-only boundary, not the documented intermittent drop. `curl` is no fallback: an authenticated **read** to `api.github.com` returns the sandbox's own 403, so that response carries no information about write permission.

- In **this** repo that is covered: `board-post.yml` carried this post, `pr-opener.yml` opened #10654.
- In **`ict-trader-dashboard` neither relay exists**, and its `ci.yml` triggers only on `pull_request`-to-main and `push`-to-main — so a branch push runs **no checks at all** and the change cannot even be driven green. **The SPA branch is pushed and has no PR.** I started porting `pr-opener.yml` there and **deliberately stopped**: standing up CI automation in a second repo is a sanctioning decision, not a Tier-1 one. **One operator click on the branch's PR link clears it.**

### Verification

**PR #10654: all 4 required checks GREEN** — `pytest-run` (15m14s), `guards`, `pytest-collect`, `repo-inventory` — on head **`1d27d9d`**, left as a **draft** for the operator since it touches `src/`.

Locally: guards **PASS 47 · FAIL 0**; **507 web/API tests** pass (the widened run described above), including my 41 + 9 digest self-tests.

⚠️ **A local pass is NOT a CI pass here, and `BL-20260824-SANDBOX-TEST-SUITE-DIVERGES-FROM-CI` is open saying exactly that** — the sandbox run diverges from CI by 95 collection errors and 34 failures. So read my 507 as *evidence that registering the router did not disturb its siblings*, which is what I ran it for, and **not** as a stand-in for the check that actually gates the merge. `api-tier-policy-guard` and `silent-empty-guard` both failed first and were fixed properly rather than suppressed.

⚠️ **A retraction, because I had this wrong and said so more than once.** I reported `pytest-run` as running 40+ then 50+ minutes against a ~15m20s baseline, and was preparing to raise CI slowness as a finding. **It is false.** The job ran **15m14s** (`13:03:16Z` → `13:18:30Z`) — dead on the baseline. What actually happened is that `pull_request_read`'s `get_check_runs` kept returning `status: "in_progress"` for that job for roughly **35 minutes after it had completed**; I polled at ~13:53Z and was still told it was running. I treated a stale read as an observation, which is the exact error this repo has a guard family for — I was reading a derived surface without establishing that it reports current state. **The polling surface lags; do not time a job by it.** No CI-performance row filed, because there is nothing to file.

⚠️ **Ruff, stated carefully:** a repo-wide reading of **13,445** errors was an artifact of my installing ruff **0.16**; `requirements-dev.txt` pins `>=0.15.0,<0.16` *precisely because* 0.16 expanded its default rules. At the pinned version the repo had **8** errors — all mine, all fixed. I nearly reported a repo-wide lint catastrophe that does not exist.

Tier-1 throughout: docs, tests, a read path, and a read-only SPA section. Nothing touched live config, the order path, or risk caps.

---
_Generated by [Claude Code](https://claude.ai/code)_
