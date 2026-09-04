▶️ **START / ✅ DONE — MI-107** · session_018zzzSLT8UdJ1e1RbFeyvsz · branch `claude/mi107-downgrade-and-tune` · PR **#10980**

**Scope touched** (docs + decision records only, no runtime): `docs/design/strategy-demote-and-tune-DESIGN.md` (new), `docs/claude/SUNSET-DISPOSITIONS.json`, `docs/claude/work/objects/WO-20260903-SUNSET-DISPOSITIONS-OWED.yaml`, `docs/claude/work/MANAGER-CHECKLIST.json` (MI-107 row only). **No `config/`, no `src/`, no workflow.** Posting late — this session was a respawn and went straight at proving the push path the predecessor lost its work to.

⚠️ **#10980's TITLE IS STALE.** `claude-pr-automerge.yml` opened it off the first commit, so the title is that commit's subject ("declare landing for..."). Both `pulls.update` and `add_issue_comment` **403** from this session (the documented write-scope boundary) and there is no relay that edits a PR body, so it cannot be corrected from here. **The PR is the MI-107 deliverable, not a landing declaration.** I did not close-and-reopen to fix a title — that would churn the PR number the merge queue tracks.

## What landed

The operator rejected the retire / do-not-retire **axis** on 2026-09-03 and asked for a third path. The flow is written. **(c) the exit condition was built FIRST**, and it is bounded on the **tuning budget, not the outcome** — `strategy_gate.py::min_live_trades` is 30 and a 1d leg trades ~4x/year, so an outcome-keyed exit is **~7.5 years, never met by construction**. Reader is `sunset_pass.py` on its existing weekly cron; at budget expiry the row **cannot stay demoted**. No new register, no new guard, **no third execution gate**.

## The finding other sessions should know

**None of the ten sunset candidates qualifies for the flow.** Measured (`comms/sunset/2026-09-01/INDEX.json` + `config/strategies.yaml`, 2026-09-04): all ten have `lifetime_closed_trades: 0` and **not one is routed to real money**. And `execution: shadow` folds into `effective_dry` on **every account including paper**, so demoting them **stops their paper fills** and makes any exit condition strictly harder to meet. They are not underperforming — they are **not running**. Hence a third disposition: **`REPAIR`**, diagnosis as the work.

**The shadow-backlog already exists**: 8 of 52 enabled legs are `execution: shadow`, and `turtle_soup` has been shadow since **2026-04-29** — four months, zero closes, routed to nothing.

⚠️ **Relevant to whoever owns `OI-20260831-PER-ACCOUNT-ARBITRATION`:** `trend_donchian_sol` emitted **144 actionable signals and wrote ZERO journal rows** on `bybit_1`. Retiring it would delete the evidence that row is waiting on. It is recorded `keep`, not retired.

## State

`MI-107` is **`waiting`**, blocked on `DEC-20260904-DEMOTE-AND-TUNE-FLOW` — a `decision_requests[]` block on the work object, so it is genuinely operator-owed **once #10980 lands on `main`**. Guards: `run_guards.py --base main` → **PASS 49 · FAIL 0**. Nothing retired, nothing demoted, every move still Tier-3.

**Not claimed:** that the flow works. It is agreed by nobody, no leg has run through it, and the budget-expiry forced disposition is **designed, not built**.
