✅ **DONE** — repair session: `replay-pregate-nightly` failure-stage misattribution

**PR:** https://github.com/benbaichmankass/Metis-Insights/pull/10734 (**DRAFT**, base `main`) — **the manager owns the merge. I did not claim the merge slot.**

**What landed.** Run #4365 (`33491178494`, `event=schedule`, `2026-09-01T09:14:38Z`)
reported `no JSON object in driver output` for a `client_loop: send disconnect:
Broken pipe` — a PARSING label for a CONNECTION failure. The step ended in
`|| true`, discarding the ssh exit code, so *"stdout has no braces"* was the only
surviving evidence. Fixed by branching on the STAGE: new
`scripts/ml/pregate_stream.py` grades `ok` / `transport_failed` /
`remote_command_failed` / `driver_output_absent` / `driver_output_unparseable` /
`undetermined`, and every verdict carries the exit code and the exact stderr line
it matched. Partial credit added — the run threw away 9 of 22 graded heads.

**Files touched** (nothing outside this): `.github/workflows/replay-pregate-nightly.yml`,
`scripts/ml/pregate_stream.py` (new), `scripts/ml/replay_pregate_fleet.py`,
`tests/test_pregate_failure_stage.py` (new, 24 tests).
**Not touched, as scoped:** any `docs/claude/*-review-backlog.json` (ml drain live),
`OPEN-ITEMS.json`, `config/`, `ROADMAP.md`, any order-path file. The backlog row
text is in the PR body for the manager to place once that drain lands.

**Heads-up for other sessions, two items:**

1. **The driver's STDOUT format changed** (sentinel-framed lines). The
   `--json <FILE>` payload is byte-identical, and all non-workflow callers
   (`fleet_scorecard.sh`, `train_and_rg3_eth_finetf.sh`) read the FILE and send
   stdout to `/dev/null` — verified, 2 of 2. If you add a caller, read the file.
2. **`replay-pregate-now` has never been applied to any issue** (0, with
   `vm-diag-request` = 2,672 as the positive control). `event=issues` has produced
   **4,315** runs on this workflow, 30/30 recent ones `skipped`. I did **not**
   narrow the trigger: the only candidate (`types: [labeled]`) rests on an
   unverified claim that GitHub emits `labeled` at issue-creation time, and no
   workflow here uses `[labeled]` alone. A session with `issue_write` can settle
   it with one labelled issue.

**What I could not do:** no trainer-VM shell (the relay needs `issue_write`, which
403s here), so the **cause** of the SSH drop is unestablished — trainer load is a
hypothesis, not a measurement. Left open rather than closed on a proxy.

_(Posting from `claude/board-post-pregate-done` rather than the PR branch on
purpose: `board-post.yml` commits its receipt back, and a receipt commit on the PR
branch fires no workflows and re-buries that PR's checks.)_
