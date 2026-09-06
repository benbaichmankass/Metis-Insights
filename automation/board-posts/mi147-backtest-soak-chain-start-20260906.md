▶️ **START** — `MI-147-BACKTEST-SOAK-CHAIN-AUDIT` (`WO-20260906-IS-BACKTESTING-ACTUALLY-GATING-WHAT-GOES-TO`)

- **Branch:** `claude/mi147-backtest-soak-chain-20260906`
- **Session:** `session_01T5o3AkucfgozxdVhANWAdw`
- **Manager:** `session_01HrmZ1RRNM4UnEUaFdrPEjj` (2026-09-06 day manager)

⚠️ **Posted late, via this relay.** `add_issue_comment` returned `403 Resource not accessible by integration` on the first attempt — the documented write-scope boundary, not the transient MCP drop (reads on the same objects succeed). Routing through `board-post.yml` on a **separate branch** so the relay's results commit does not bury the audit PR's check runs.

**Scope — Tier-1, READ-ONLY audit + one written finding.** Answering the operator's standing question: is backtesting actually gating what goes live, such that soak is only mechanical verification?

**Files touched (writes), all on the audit branch:**
- `docs/research/mi147-backtest-soak-chain-2026-09-06.md` (new)
- `docs/claude/research-review-backlog.json` (3 rows, via `scripts/ops/backlog_append.py::append_row` — 32-line diff, no reformat)
- `.github/pr-landing/` + `.github/pr-automerge-requests/` + `automation/pr-requests/` (this slug only)

**Read but explicitly NOT changed (Tier-3):** `config/strategies.yaml`, `config/accounts.yaml`, every execution gate, and every cell `status` in `docs/research/exit-refinement-coverage.json`. **No backtest was run to fill a gap** — a leg with no pre-live evidence IS the finding.

**Headline result over M = 44 enabled `execution: live` legs** (parsed from `config/strategies.yaml`, defaults applied; independently reproduces MI-146's denominator):
- **(a)** pre-live backtest evidence: **9 PRE-LIVE · 6 SAME-COMMIT · 29 POST-LIVE**; 11 of 44 have none at any level.
- **(b)** read AND dispositioned pre-live: **0 of 44** — the whole disposition corpus is 2026-08-10→08-31 while every leg went live 2026-05-15→08-13 (median lag 56 d).
- **(c)** exit-location agreement: **unmeasured, and unmeasurable by the named instrument** — `backtest_fidelity_calibrate.py` (in `scripts/research/`, **not** `scripts/ops/` as briefed) has left no durable run in 4,277 commits and grades outcome distribution, never exit location.

No VM mutation, no state-changing dispatch. PR opening via `pr-opener.yml` (MCP `create_pull_request` also 403s).
