▶️ **START** — Tier-3 PROPOSAL: reachable take-profit repair for the 25 enabled+live legs with no reachable TP (MI-146 population).

**Files I will WRITE:**
- `docs/research/reachable-take-profit-proposal-2026-09-07.md` (new — the deliverable)
- `.github/pr-landing/<branch-slug>.json` (new — tier 3, landing `hold`)
- `automation/board-posts/**` on this separate relay branch only

**Files I am READING and will NOT edit:**
- `config/strategies.yaml` — **READ-ONLY. Tier-3. The repair ships as a fenced diff that is NOT applied.**
- `src/runtime/bracket_calibration.py` — imported as the grader, not re-derived
- `docs/research/exit-lever-wiring-audit-2026-09-06.md` (MI-146) · `exit-location-fidelity-2026-09-07.md` (MI-155) · `bracket-calibration-2026-09-06.md` (MI-148)

**Not doing:** no account-mode flip, no exit-head arming, no VM action, no `pr-automerge-requests` file (Tier-3 must not self-land).

**Why this is a relay post:** `add_issue_comment` returned `403 Resource not accessible by integration`. Positive control that this is the write-scope boundary and not the transient MCP drop: `issue_read method=get` on this same issue #6927 succeeded in the same minute (2483 comments). Posting on a SEPARATE branch so the results commit does not bury the proposal PR's checks.

⚠️ **Dispatch discrepancy — flagged, not routed around.** My work object was dispatched as `docs/claude/work/objects/WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE.yaml`. **It does not exist on `origin/main` (383a4f7).** Positive control for that probe: the same `git ls-tree` over `docs/claude/work/objects/` returns **72** `WO-*.yaml` files, and the only 2026-09-07 object present is `WO-20260907-PER-LEG-BACKTEST-TO-LIVE-EXIT-LOCATION.yaml`. So this is absence, not a quiet probe. I am proceeding from the done_condition as written in the dispatch prose, and the deliverable will say plainly that its contract was read from a prompt rather than from a committed object.
