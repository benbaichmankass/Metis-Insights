# Sprint Log: S-LLM-BURST-WORKER-2026-08-18

## Date Range
- Start: 2026-08-18
- End: 2026-08-18

## Objective
**Primary:** design and pilot a bursty, ~free "LLM worker" Claude can drive itself for
bounded coding/research subtasks, and establish whether delegation actually pays.

**Secondary:** answer "does the repo reflect the true state of the cloud infra?" — the
Oracle free-tier question raised by Oracle halving the Always Free Ampere allowance to
2 OCPU / 12 GB on 2026-06-15 while this repo documents 4/24.

## Tier
**Tier 1** throughout for the shipped code — new tooling only: no `src/`, no `config/`,
no unit file, no order path, no live trading data, nothing running on either VM.

One **Tier-2** action was taken with explicit operator approval in chat: a `set-diag-token`
dispatch (run 32117038449), which restarted `ict-web-api.service` only. `ict-trader-live.service`
was never touched.

## Starting Context
- Active roadmap: M20/M31 exit + telemetry work dominated the day (separate sessions).
- Prior sprint: `S-SYSREV-TRADE-MECHANICS-2026-08-18` (concurrent, different scope).
- Known risks entering: the operator's stated constraints were, in order — smoothest
  operator experience, lowest cost, highest Claude autonomy, safety, sufficient
  performance. No prior art in-repo for delegating to a non-Anthropic model.

## Repo State Checked
- Branch `claude/llm-burst-worker-design-87vrhg`, restarted from `origin/main` three times
  (after each merge) per the merged-PR rule.
- Base commits: `cf3f0c1` → `1f82751` → `55151fd` → `eca4b66` → `8e3daab`.
- Canonical docs reviewed: `CLAUDE.md` (§ "PM-side session capabilities", § "Dashboard REST
  API", § "VM authority split"), `docs/CLAUDE-RULES-CANONICAL.md` (tiers, "If you see
  something say something", collapsed states), `.claude/skills/sprint-format/SKILL.md`.

## Files and Systems Inspected
- **Code:** `scripts/ops/terminate_instance.py` (OCI auth convention), `scripts/ci/run_guards.py`,
  `scripts/check_diagnostic_provenance.py`, `docs/claude/health-review-backlog.json` (schema).
- **Workflows:** `.github/workflows/{get-diag-token,set-diag-token,guards,pytest-run,pytest-collect}.yml`,
  plus a narrowed scan of all 66 workflows for deliberate secret-emitters.
- **Live systems (read-only):** `https://ict-bot.duckdns.org/api/{health,bot/stats,bot/config,bot/notifications,diag/status}`;
  OCI compute inventory via `list_instances` (runs 32128150842, 32131324874).
- **Egress probes:** Groq / Cerebras / Gemini API hosts, credentialed and not; the live VM
  by raw IP and by Caddy hostname; `api.github.com`.

## Work Completed
1. **Design memo** — evaluated Oracle VM, Colab, GitHub Actions, Codespaces, and free hosted
   APIs against the operator's stated priority order. Landed on *the runner IS the worker*:
   for bursty delegation the unit is one bounded subtask, so a job's own lifecycle is the
   worker lifecycle — nothing to start, health-check, idle out or stop.
2. **`scripts/llm/scope_guard.py`** — default-deny path filter enforcing the operator-authorised
   scope (public repo code + docs only). One denied path refuses the whole batch.
3. **`scripts/llm/delegate.py`** — runs one subtask against an OpenAI-shaped backend.
   Three-state envelope, bounded retry on transient classes only, truncation detection.
4. **`.github/workflows/llm-delegate.yml`** — `preflight` / `models` / `delegate` modes.
   Results post to issue #9944.
5. **`scripts/ops/oci_inventory.py` + `.github/workflows/oci-inventory.yml`** — read-only OCI
   inventory diffed against a declared topology, plus Ampere free-tier budget arithmetic.
6. **`comms/cloud/expected_topology.json`** — the declared baseline, seeded from a live run.
7. **Backlog** — 4 findings filed; one deduped against a concurrent session's row at wrap.
8. **Coordination board** — `START` (posted late, flagged as such) + `DONE` on issue #6927.

**Merged:** #9936, #9945, #9948, #9953 — all squashed to `main`, all required checks green.

## Validation Performed
- **58 tests** across `tests/scripts/test_llm_delegate_scope.py` (35) and
  `tests/ops/test_oci_inventory.py` (13 + 10 added at wrap), all passing locally and in CI.
- **All 29 relevant guards** pass via `scripts/ci/run_guards.py --base-ref main`; `ruff check .`
  clean. The guard runner's own "2 paths are UNCOMMITTED … this is NOT a clean bill of health"
  warning was honoured — files were committed and guards re-run before trusting the pass.
- **`bash -n`** on every embedded shell block in both new workflows.
- **Live end-to-end:** `guard-review-006` and four `n3-*` tasks returned `status: completed`
  with real token usage against Gemini 3.6 Flash, $0.
- **Live OCI:** run 32131324874 returned **3 × `match`, 0 drift** against the seeded baseline.
- **Security probe (three-way, to avoid a one-sided read):** the leaked diag token returned
  **200**, an obviously-invalid token **401**, no token **401** — establishing that auth
  enforces and the specific leaked value still authorises.

**Gaps not yet verified:**
- Delegation precision is **n=5 completed tasks / 19 claims**. That is an existence proof
  with a stated denominator, **not** a stable hit rate.
- `mode=models` was run against Cerebras and used for Gemini only via the backend's own 404
  message; the Gemini model list was never enumerated directly.
- The `oci-inventory` workflow's **issue-label trigger path was never exercised** — only
  `workflow_dispatch`. The `oci-inventory` label may not exist.
- `--fail-on-drift` has never run against a real drifting topology; only unit-tested.

## Documentation Updated
- **Roadmap:** new **M37** row (this sprint's record + next steps with falsifiers).
- **Subsystem docs:** `docs/design/llm-burst-worker-DESIGN.md` (new; corrected twice during
  the session as measurements invalidated earlier claims).
- **Backlog:** `docs/claude/health-review-backlog.json` — 4 items filed, 1 deduped.
- **Not applicable:** no pipeline stage changed, so `docs/TRADE-PIPELINE.md` is untouched and
  the dashboard Trade Process tab needed no visual check.

## Contradictions or Drift Found
1. **`get-diag-token.yml`'s safety rationale is stale.** It justifies emitting a live secret
   into an issue comment / run artifact with "this repo has exactly two principals". The repo
   is **public** (`private: false`, verified). That reasoning holds only on a private repo,
   and the repo has flipped visibility before (public → private 2026-07-06 → public 2026-07-07).
2. **`set-diag-token.yml` reports "authorized with the new token"** after testing only that
   *a* token authorises — it never compares old to new, so a no-op rotation reports success.
   Unprovenanced diagnostic output, sub-class A, on a workflow rather than a script.
3. **`CLAUDE.md`'s web-session reachability claim is half wrong.** The raw VM IP is firewalled
   (confirmed, rc=28) but the Caddy HTTPS hostname resolves fine from a web session, including
   credentialed `/api/diag/*`. Future sessions should not skip a working path on that line.
4. **Duplicate backlog rows** for the same `DIAG_BASE_URL` finding, filed 1h45m apart by two
   concurrent sessions. Deduped at wrap; evidence merged into the earlier row.
5. **Not caused here, but confirmed:** the documented VM topology is **accurate** — 4 OCPU /
   24 GB across three Ampere instances, exactly as `CLAUDE.md` states. The prose was right; it
   had simply never been checked.

## Risks and Follow-Ups
**Technical risks:**
- Delegation's weak mode is **cross-file claims where the referenced file is out of scope** —
  it produced one confident false positive concluding a doc was wrong about a feature
  implemented in a file it had not been given. Mitigated in the system prompt, not eliminated.
- The scope guard is now root-scoped and narrow. If it becomes *too* narrow the tool quietly
  stops being useful; four tests assert legitimate paths still pass, which is the tripwire.

**Tier-3 product decisions:** none arising.

**Blockers:** none. Both tracks are complete and self-contained.

## Deferred Items
- Scheduling `oci-inventory` with `--fail-on-drift` so topology drift becomes a notification
  rather than something someone has to remember to run.
- Exercising the `oci-inventory` issue-label path (and creating the label if absent).
- Pointing the delegate at real backlog items rather than at its own source.
- A local-GGUF backend (design Phase 3) — deferred indefinitely; no privacy driver exists,
  and the hosted path is both cheaper and better.

## Next Recommended Sprint
**Schedule the inventory, then use the delegate on real work.**

*Why:* the inventory currently only tells the truth when someone asks it, which is the same
failure mode as prose nobody checks — the whole reason it was built. And delegation is proven
at n=5 **on its own source code**, which is the easiest possible case: the reviewer wrote the
file being reviewed and could verify every claim instantly. Whether it holds on unfamiliar
subsystems is untested.

*Required verification:* a scheduled drift run must be shown to FAIL on a real induced drift
before it is trusted (a green check that cannot go red is worse than no check). For delegation,
grade the first 5 tasks on code the session did **not** write, and report precision against
that denominator separately — do not pool it with the self-review numbers.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked; M37 added.
- [x] Contradictions were recorded (5 above, incl. ones not caused here).
- [x] Remaining unknowns were stated clearly (see *Gaps not yet verified*).
