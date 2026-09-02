▶️ **START** — repair session: `replay-pregate-nightly` failure-stage misattribution

**Branch:** `claude/replay-pregate-failure-stage` (base `main`, DRAFT PR to follow)

**What I am touching**
- `.github/workflows/replay-pregate-nightly.yml` — the "Run fleet pre-gate on trainer VM" step
- a new small classifier module under `scripts/ml/` + its tests
- `scripts/ml/replay_pregate_fleet.py` (per-model incremental emission, if partial-credit lands)

**Why.** Scheduled run #4365 (`33491178494`, `2026-09-01T09:14:38Z`) FAILED — the only
non-success among 30 scheduled runs in the window, so this is a specific break, not a
scheduling outage. The SSH session dropped at model 10 of 22 (`client_loop: send disconnect:
Broken pipe`) and the workflow reported `no JSON object in driver output` — a PARSING label
for a CONNECTION failure. That is UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A
(`CLAUDE.md` § "Diagnostic provenance"): a failure message naming a cause no code path tested.
Root cause: the step does `ssh ... || true`, discarding the exit code, then blind-scans stdout
for `{`…`}`.

**NOT touching:** any `docs/claude/*-review-backlog.json` (an ml-backlog drain is live on that
file), `docs/claude/OPEN-ITEMS.json`, `config/`, `ROADMAP.md`, or any order-path file.
No merge — the manager owns it.

**Note for whoever holds the merge slot:** I am not claiming it. Draft PR only.
