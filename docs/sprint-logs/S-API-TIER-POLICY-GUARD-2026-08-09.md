# Sprint Log: S-API-TIER-POLICY-GUARD-2026-08-09

## Date Range
- Start: 2026-08-09
- End: 2026-08-09

## Objective
- Primary goal: ship a CI guard that keeps `docs/api-tier-policy.md` complete, **then** backfill the missing rows — in that order, because the guard stops the bleeding and backfilling first just means more drift by the time it lands.
- Secondary goals: re-derive the completeness figure rather than restating it; record the tier of each route from the **runtime gate**, not the route name.

## Tier
- **Tier 1** — docs + CI only.
- Justification: no runtime, config, or order-path file was modified. The guard *reads* `src/web/api/routers/` to enumerate routes; it changes none of them. `git diff --stat` confirms no file under `src/` or `config/` in the diff.

## Starting Context
- Active roadmap items: none directly — this drains `BL-20260809-API-TIER-INVENTORY-77PCT-STALE` from the health-review backlog.
- Prior sprint reference: [`S-COLLAPSED-STATES-2026-08-09.md`](S-COLLAPSED-STATES-2026-08-09.md), the session that found the gap. Its recurring defect — *a claim that is asserted rather than verified* — is the one this file embodied.
- Known risks at start: the backlog item warned that a naive matcher over-counts the gap by crediting none of the Tier-2.5 family-row convention, and that bulk-generating tier rows would be worse than the gap.

## Repo State Checked
- Branch or commit reviewed: `claude/api-tier-policy-guard-npihz7` off `main` @ `55c3455`.
- Deployment state reviewed: none — nothing here deploys.
- Canonical docs reviewed: `CLAUDE.md` § "Dashboard REST API", `docs/api-tier-policy.md`, `docs/claude/health-review-backlog.json`.

## Files and Systems Inspected
- Code files inspected: all 40 modules under `src/web/api/routers/`, `src/web/api/main.py`, `src/web/api/auth.py`; `scripts/check_diagnostic_provenance.py` (the shape to copy), `scripts/ci/run_guards.py`, `scripts/ci/guard_selftests.py`, `scripts/ops/check_backlog_refs.py`.
- Config files inspected: none.
- Deployment files inspected: `.github/workflows/guards.yml` (read-only — the registry lives in `run_guards.py`, so the YAML needed no edit).
- Docs inspected: as above, plus `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.
- Services or timers inspected: none.
- GitHub Actions workflows inspected: `guards.yml`.

## Work Completed
- **Item 1 — the guard.** `scripts/check_api_tier_policy.py`: a route defined under `src/web/api/routers/` must carry a row. Routes are enumerated **by AST, joining each `@router.<verb>` to its `APIRouter(prefix=...)`** — matching the decorator string alone would compare `/snapshot` against `/api/diag/snapshot` and report a gap for every route in the repo. Registered as `api-tier-policy-guard` in `scripts/ci/run_guards.py`, with a failure-path self-test in `scripts/ci/guard_selftests.py`.
- **Item 2 — the family-row convention is credited, not overridden.** The Tier-2.5 section documents diag leaves (`audit`, `journal`, …) after a sibling full path. The parser resolves a bare leaf against the last full path in the same row, so the existing shorthand counts as documentation **as written**. A guard that silently forces a correct document into a reformat picks a fight with the thing it protects; a test (`TestCreditsTheFamilyRowConvention`) pins this.
- **Item 3 — the backfill, row by row.** All missing rows written by reading the actual gate in each router. Coverage went **36/92 → 92/92**.
- **Item 4 — the banner replaced by the guard's name**, and its number computed rather than counted (`--list`), with a test asserting the stated figure equals a freshly computed one.
- **Item 5 — a third bearer token surfaced and given a home** (see Contradictions).

## Validation Performed
- **Tests run:** `tests/test_check_api_tier_policy.py` — 24 passed. Also re-ran `tests/test_check_artifact_validity.py tests/test_check_research_index.py tests/test_check_backlog_refs.py` — 57 passed.
- **The enumerator was verified against the live FastAPI route table**, not just asserted. Built a venv, imported `src.web.api.main:app`, and diffed `app.routes` against the AST walk: it finds **exactly** the 92 router-defined routes with **zero false positives**, and the only live routes it does not cover are the five defined outside `routers/` — `GET /api/health` plus FastAPI's four built-in docs routes. Kept as a test (`test_matches_the_live_fastapi_route_table`, skipped where FastAPI is not importable — the guard itself is stdlib-only by design).
- **The failure path was exercised, in both modes.** `--all` with a row deleted → exit 1. A diff adding a new undocumented route → exit 1 naming that route. A diff touching only the `APIRouter(prefix=…)` line → all routes in that file pulled into scope.
- **The coverage assertion was checked to bite:** editing the doc's stated number to 91/92 fails the test; restored, it passes.
- **Gate inventory measured with a denominator, not sampled:** an AST pass over all 92 route handlers found **22 gated** (16 `_require_diag_token`, 2 `require_session`, 3 `_check_admin_token`, 1 `_require_write_token`) and **70 ungated**. Every tier row below rests on that scan rather than on the route's name.
- **Lint:** `ruff check` clean on all new/changed files under the repo's pin (0.15.22). `ruff format --check` would reformat them — **and would equally reformat `scripts/check_diagnostic_provenance.py` and `scripts/ci/run_guards.py`**, and no workflow runs it, so this is repo convention rather than a miss. Checked, not assumed; the board flagged the unpinned-ruff trap the same day.
- **Gaps not yet verified:** I did not enumerate every sibling guard affected by the `--all` step-skip below — only the mechanism and the one instance I was next to. Said so in the backlog item rather than implying a full sweep.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none.
- Trade pipeline doc updates: none — no pipeline stage touched.
- Roadmap updates: none.
- GitHub Actions doc updates: none — the registry entry is self-describing.
- Subsystem doc updates: `docs/api-tier-policy.md` (backfill + banner replaced by the guard's name); `CLAUDE.md` (one corrected gate description, below).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **Contradiction 1 — a third bearer token with no home in the taxonomy.** Four routes are gated by `DASHBOARD_API_TOKEN`, which is neither the Tier-2 JWT nor the Tier-2.5 diag token. Worse, **the two gates fail in opposite directions when the var is unset**: `POST /api/bot/prop/report` is fail-**closed** (503, deliberately — BL-20260705), while `GET`/`DELETE`/`PATCH` on `/api/bot/devices` are fail-**open** (`_check_admin_token` returns silently). They are now a labelled Tier-2 sub-table stating that per row. I did **not** change any gate: tightening one is a runtime change (Tier 2), not a docs backfill.
- **Contradiction 2 — `CLAUDE.md` labels the `devices` rows Tier 1**, on the strength of the permissive default. Recorded in place as a known divergence rather than resolved by rewriting either doc: that table documents the contract a consumer sees, this one documents the gate mechanism. Whether fail-open is right for the DELETE/PATCH is a live question, not a settled one.
- **Code/doc mismatch — fixed.** `CLAUDE.md`'s `POST /api/bot/prop/report` row read "token-gated via `DASHBOARD_API_TOKEN` **when set**", which describes the permissive `_check_admin_token` shape the prop write path deliberately does *not* use. Field beats comment: the row now states the fail-closed 503 behaviour.
- **Tier-1's own rule was violated by two of its routes.** The section said "Read-only — never mutates state"; `POST /devices/register` and `POST /learning/progress` are unauthenticated writes. Rather than mis-tier them (their runtime gate is *nothing*, so they are not Tier 2), the rule now names the carve-out explicitly and bounds it: a write touching money, an order, config, or a notification does not qualify.
- **The completeness figure moved again.** The backlog recorded 90 routes / 36 documented; this session measured **92 / 36** — same documented count, two apart on the denominator, hours later. Not reconciled, and deliberately so: it is the argument for the guard, and the enumerator was validated against the live app instead.

## Risks and Follow-Ups
- Remaining technical risks: the guard's population is `routers/` only. A route added directly to `main.py` would not be caught. That is stated in the doc rather than silently assumed, and `/api/health` is rowed anyway.
- Remaining product decisions (Tier 3): none.
- Blockers: none.
- **Filed:** `BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH` — under `run_guards.py --all` (push / workflow_dispatch), `changed` is empty, so a per-**step** `when` clause can never match and the step is silently skipped. `guards.yml` comments that push runs everything "stricter than the retired behaviour, never weaker"; for step-gated guards it is the reverse. Verified by observation, and `diagnostic-provenance-guard`'s scan step has the same shape. Not fixed here — the one-line fix flips behaviour for every step-gated guard at once and wants its own PR. `api-tier-policy-guard` sidesteps it by carrying its `--all` step **ungated**, which is also what makes the row-deletion case catchable.

## Addendum — two follow-ons taken in the same session

### A. My own test committed the defect class the guard catches

`pytest-run` went red on `test_matches_the_live_fastapi_route_table`. The repo pins only `fastapi>=0.110.0`, so **CI resolved 0.141 while my venv had 0.115** — and on the newer version `include_router` leaves an `_IncludedRouter` wrapper exposing neither `.path` nor `.routes` (sub-routes hang off `.original_router.routes`). My one-level walk of `app.routes` therefore found **5** routes, and then reported all 92 enumerated routes as "not live".

The bug was not the traversal. It was that **the probe never asserted its own denominator** — it compared a near-empty extraction against a real set and produced a confident, entirely wrong 92-line diff. That is sub-class **C** (unasserted denominator) from `CLAUDE.md` § "Diagnostic provenance", occurring inside the test written to validate a guard against that very class.

Fixed by traversing **by structure, not version sniffing** (a version check would fail silently on the next rename, exactly as the original did), and by making an implausible extraction fail as *a broken probe* naming the real cause. Verified green under **both** 0.115.6 and 0.141.1, and `TestLiveRouteProbe` pins both shapes with stubs so the next regression is caught as a one-line assertion rather than an unreadable route dump.

### B. `BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH` — resolved, but not by the fix I proposed

The item offered option (a): *"under `force_all`, treat a per-step `when` as satisfied — one line, and it makes the comment true."* **Measuring first killed it.**

Both step-gated commands in the registry — the only two, `api-tier-policy-guard` and `diagnostic-provenance-guard`, not the "several" the item guessed at — consume `{pr_diff}`, and on push that file is **empty**. Forcing them to run makes `check_diagnostic_provenance.py` print *"OK — every scanned diagnostic states what it computed"* and exit 0 having scanned nothing. **Option (a) would have made the comment true and the check false.** Substituting the whole-tree equivalent is no better: `--all` exits 1 on **52** pre-existing grandfathered sites (which is *why* it is diff-scoped) and would redden `main` on every merge.

So the skip is correct and stays. The real defect was that it was **indistinguishable from an ordinary not-relevant skip** — a reader could not tell *"we asked about this diff and it did not apply"* from *"there was no diff to ask about"*. Shipped: the skip names its reason, the summary prints a `NOT SCANNED on this event (N)` block with the remedy, `guards.yml`'s "stricter … never weaker" claim (the thing that made the gap invisible) is corrected, and `tests/test_run_guards_step_scoping.py` pins the distinction **and the premise** — it fails if a future step-gated command stops consuming `{pr_diff}`, so the reasoning is re-derived rather than inherited.

**The transferable bit:** both A and B are the same shape as the sprint that opened this file — *a claim asserted rather than verified*. In B the claim was mine, in my own backlog item, written the same day.

## Addendum 2 — the merge itself became the next finding (PRs #8711, #8715)

Recorded because this log's "Work Completed" stopped at the guard, and three further changes shipped after it merged.

### C. The merge-friction livelock, and the operator correction that caused the real fix

Landing #8698 cost **four** CI cycles. `main` moved twice under it — #8709 seconds before the merge-slot claim, #8710 during the re-run — and each re-sync started a fresh ~9-minute `pytest-run` that the next merge invalidated. With sessions merging faster than one CI cycle, a branch can never be simultaneously **green** and **up-to-date**.

I first reported this as *"the merge-slot protocol structurally cannot serialize."* **The operator pushed back and was right.** `session-board.json`'s schema is explicit — *"At most ONE session holds this at a time. Mirror the claim here before merge, clear after."* — and the VM-lane section spells out the wait discipline (*"Lane held → post `🕓 QUEUED · behind <holder>`, wait. … Newest never wins."*). The contract **does** define waiting. I had generalised from step 1's open-PR sanity check to the whole mechanism, which conveniently blamed the design for my own gap: **I held the live slot for 30 minutes and never wrote the `merge_slot` mirror**, so the durable record read `held_by: null` throughout. That is a fresh instance of `BL-20260720-MERGE-PROTOCOL-LAPSE`, whose own text is *"sessions treated this JSON as the claim, it went untouched under load."*

Timestamps split the blame and that matters: **#8709 merged 00:46:45, seconds *before* my claim** (not a violation — I held nothing yet); **#8710 merged after it**. I had presented both as evidence the design was broken.

**Shipped (#8711, operator-directed):** `strict: false` on `main` — unticks *"Require branches to be up to date before merging."* Required checks still gate (`REQUIRED_CONTEXTS` untouched, `enforce_admins` still true); only the up-to-date coupling is gone. Accepted exposure is a **semantic** conflict — clean textually, green alone, broken together — mitigated by `guards` + `pytest-run` also running on every push to `main`.

### D. Two measured claims of mine that were false

**"A 34-minute `pytest-run`" and "the runner pool is contended."** Both wrong, both repeated to the operator. Measured, every run took ~9 minutes: **8m51s / ~9m16s / 8m42s / 8m57s**. The "34 minutes" was **`get_check_runs` serving stale data** — reporting `in_progress` ~30 minutes after the run object was `completed / success`. I read a lagging API as a slow runner and inferred a cause I never checked. Corollary: `get_status` returns `state: pending, total_count: 0` here because everything is a Check Run and the *legacy status* API is empty — that means **no data**, not "pending". **Probe `get_job_logs`: a 404 means genuinely still running.**

**The stacking finding survived** and is filed with real numbers as `BL-20260810-REQUIRED-WORKFLOWS-NO-CONCURRENCY-GROUP`: `pytest-run` / `pytest-collect` / `repo-inventory` declare no `concurrency:` group while `guards.yml` does, so a re-queue stacks a second full suite. #8711 demonstrated it on itself — un-drafting re-queued a second ~9-min `pytest-run` on an unchanged head, while `guards` on that same head was correctly cancelled by its group (the control).

### E. A green sync that never said what it set (#8715)

#8711 flipped `strict` true → false and `branch-protection-sync.yml` went green printing **only** `Required contexts now: [...]`. The change was correct and **unverifiable from CI**: no MCP tool reads branch protection and `api.github.com` is unreachable from a sandbox session, so the only evidence was *"the PUT returned 200"* plus inference. That is the `diagnostic-provenance` class in the workflow that owns branch protection.

#8715 echoes `strict` + `enforce_admins` + `contexts` **and asserts the response matches what the file declares**, failing the run otherwise — because HTTP 200 means *GitHub accepted the request*, not *the field is now what you asked for*. Verified both directions against synthetic responses. Its first real run is also the confirmation owed for #8711.

## Addendum 3 — the doc-vs-config guard, and the same defect recurring inside its own fix (#8722, 2026-08-10 03:44Z)

### F. A guard that passed 4/4 while five docs stated a value its source contradicts

`canonical-doc-coherence` printed **"all checks passed"** over a corpus in which five
canonical surfaces asserted require-up-to-date was the merge safety net, hours after
`STRICT` was set to `false`. Every check it ran was a grep for a known-bad **phrase**.
**None compared a claim in prose against the file that sets the value**, so a doc could
be confidently, verifiably wrong about a live gate and the guard stayed green.

Added `declared values match their source` — a value-extractor per contract over the
authoritative file, plus the stale phrasings that actually shipped, searched only when
the live value contradicts them. Three contracts, all from observed drift:

| contract | source | drift caught |
|---|---|---|
| branch-protection require-up-to-date | `branch-protection-sync.yml::STRICT=` | prose calling it the merge safety net while it is `false` |
| prop write gate | `routers/prop.py::_require_write_token` | **fail-CLOSED** (503 when the token is unset) described as permissive |
| devices admin-token gate | `routers/devices.py::_check_admin_token` | **permissive** when unset — the opposite shape, documented adjacent |

An extractor that stops matching is a **hard failure, not a skip**: rename `STRICT=` and
the check exits 1 saying it is silently disabled. Relevance globs now follow the
**sources** (the workflow + the two routers), because a PR that changes a gate and not
its prose is precisely the PR that needs this check and was the one being skipped.

### G. The sweep in #8715 missed two surfaces — the two that matter most

`.claude/settings.json`'s merge-guard **deny message** and `docs/claude/coordination-board.md`
(which transcribes it) still said *"sync THIS branch to origin/main IMMEDIATELY before
merging."* That is not prose about the rules; it is the text a session is handed **at the
instant it merges**, so it outranks any doc it might not open. Meanwhile `merge-queue.md`
and `session-coordination/SKILL.md` — corrected in #8715 — said the opposite.

Not hypothetical: concurrent session `m20-exit-continue-g1qv46` followed it, hit a
`git rebase origin/main` conflict on `health-review-backlog.json`, and a `;`-chained
script carried on and committed conflict markers locally while printing `PUSHED` on a
push the remote had refused. The hook was actively instructing the behaviour.

### H. And the new check missed the same two files, three times in sequence

Each failure was **a scan that ran and reported clean** — never one that failed to run.

1. **The pattern list did not cover the wording that shipped.** `coordination-board.md`
   was already in the scanned set. The check read it and passed, because the pattern said
   `"LAST, right before merging"` and the file said `"IMMEDIATELY before merging"`. That
   is the *known-stale-phrasings-not-meaning* limit its own docstring declares, hit within
   minutes of declaring it.
2. **`.claude/settings.json` was scanned by nothing.** `_active_files()` reads
   `.claude/**/SKILL.md` and `.claude/commands/*.md`; `settings.json` is neither. The hook
   messages had no guard over them at all.
3. **Adding it to the scan list was not enough — the finding was suppressed anyway.**
   `settings.json` is minified: the merge-guard hook is one ~2 KB line, so the ±2-line
   historical-marker context window was the *entire hook* and was guaranteed to contain
   some "was" or "correct". The stale sentence matched a pattern, sat in a scanned file,
   and passed. **I added the file, observed green, and would have reported that as
   verified.** A guard reporting on a file it was reading and silently ignoring is the
   same shape as the defect it exists to catch. Fixed with `_historical_near` (±300 chars
   around the match), falling back to whole-context when the line cannot be located, so it
   is no stricter than before for every file that already passed.

**Verified by planting, not inspection:** all three sync phrasings fire; the planted stale
deny message in the real `settings.json` produces the finding where the identical plant
produced PASS before the fix; a historically-marked retraction stays quiet; a marker ~900
characters away **on the same line** now fires — pinned as a third self-test assertion so
the suppression window cannot silently widen back.

### I. Two defects found while verifying the above

- **The api-tier live-route probe skipped on `except Exception`.** Importing the app pulls
  in `jwt` → `cryptography`, whose Rust bindings raise pyo3's `PanicException`, a
  **`BaseException`** subclass. So a broken *environment* surfaced as a failing *test* —
  the inversion that probe's denominator floor exists to prevent, one layer up. Widened to
  `BaseException` with interrupts re-raised.
- **`_planted` removed its file but not the directories it created.** Invisible until a
  planter targeted a *new* directory; the new one plants under `.claude/skills/` and left
  an empty `_selftest_*` directory inside the skills tree on **every run**. Git does not
  track empty directories, so `git status` stayed clean while it accumulated. Fixed in
  `_planted`, so every future planter is covered.

### Deferred item 5 is now RESOLVED

*"`strict: false` is inferred, not observed"* — **observed twice.** #8712 merged from
`bd75dfe` onto a `main` already at `f0c909c` with no re-sync, and #8722 merged from
`1bb42b1` onto `300d4d6`. A merge of #8715 was separately blocked by a **genuine textual
conflict**, not by `behind`, which is the distinction worth keeping.

### The through-line

#8715's sweep missed two files. #8722's check then missed the same two files for three
different reasons in sequence. Every one was a scan that ran and printed clean.
**A green from a guard means nothing until you have watched it go red on the real input** —
which is why every claim in this addendum was verified by planting the failure rather than
by reading the code.

### Merge protocol (recorded because it went right)

Slot was held by `m20-exit-continue-g1qv46`, who was legitimately unable to merge #8721
(a running census shard whose branch a merge would delete) and had converted that into
holding the token. Raised as a `❓ QUESTION` on #6927 — merging #8722 touches their branch
in no way — rather than waiting indefinitely or jumping the queue. They released within
minutes with an accurate diagnosis of their own: *"I converted 'I can't merge yet' into
'nobody can merge.'"* The slot is a **merge** token, not a lock on a long-running job.

## Deferred Items
- Deferred item 1: hardening `DELETE`/`PATCH /api/bot/devices` to the prop route's fail-closed shape (Tier 2 — a runtime change).
- Deferred item 2: the 52 pre-existing `diagnostic-provenance --all` findings remain grandfathered. Not touched here; they are why that guard cannot run whole-tree on push.
- Deferred item 3: the **fix** for `BL-20260810-REQUIRED-WORKFLOWS-NO-CONCURRENCY-GROUP` (adding groups to three workflows). The finding shipped; the fix did not. It carries one unverified caution — `cancel-in-progress` on a **required** check leaves that context non-successful until the superseding run lands.
- Deferred item 4: the `merge_slot` mirror is **structurally unwritable for a claim on the PR that carries it** — writing it costs a full CI restart and reaches nobody until merge, by which point the claim is over. Filed as `BL-20260810-MERGE-SLOT-MIRROR-UNWRITABLE-PRE-MERGE`; the remedy is a store not gated on merging, not "remember harder".
- Deferred item 5: **`strict: false` is inferred, not observed**, until #8715's notice prints it or a behind-`main` PR merges without re-sync.

## Next Recommended Sprint
- Suggested next sprint: none required by this work. The guard is self-maintaining.
- Why next: the inventory now fails CI when it drifts, so it no longer needs a periodic manual audit — which was the point.
- Required verification before starting: none.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries — every tier row rests on an AST gate scan over all 92 handlers, plus reading the four token-gate helpers.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked — no milestone claims this work.
- [x] Contradictions were recorded (four, above) rather than quietly reconciled.
- [x] Remaining unknowns were stated clearly — notably that the affected-guard list for the step-skip is unenumerated.
