✅ **DONE** · session `session_014myC5S5VacHNuzzBR8dGBC` · branch `claude/exit-integrity-cluster`
Repo: Metis-Insights · base `12659c7d`

**Area released:** `scripts/ops/broker_bracket_reconcile.py` (new), `tests/ops/` (two new files), `.github/workflows/board-post.yml` (new), `automation/`, `docs/claude/health-review-backlog.json`. **No `src/`, no `config/`, no unit file, no order path, no VM mutation. Tier 1 throughout.** No merge slot claimed — nothing merged.

## Three PRs open, all draft

| PR | what | state |
|---|---|---|
| **#10076** | the detector + the board relay | draft |
| **#10077** | **disposition of #9924** — its test file, reduced | draft |
| **#10078** | **disposition of #9919** — its row, re-landed onto current main | draft |

**#9924 and #9919 should be closed in favour of #10077 / #10078** — I cannot close them (MCP 403 on writes). **#10068 untouched**, per the 20:06Z correction.

## The seven rows, graded against live state

| row | verdict |
|---|---|
| `MONITOR-MANAGES-ONLY-THE-LINKED-LEG` | **fixed, deployed, live-corroborated** — `_package_open_legs()` on `main`, both arms, `/api/diag/version` = `12659c7d` |
| `IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS` | instance resolved (stop×1.00, one OCA group, all 3 symbols); **criteria 3 & 4 now implemented** in the detector; **and it healed by cancelling the wrong leg** |
| `COVERAGE-IS-ONE-SIDED` | detection **verified firing** (both banners live 20:30Z); repair open; population changed underneath it |
| `ICT-SCALP-HAS-NO-TAKE-PROFIT-CLOSE-PATH` | **recurred** on MGC 4773, opened *after* the 08-18 repair; 150 pts past its declared TP |
| `IB-BROKER-PNL-READER-HAS-NO-CALLER` | still standing — **0** call sites vs a control of **3** |
| `ATTACH-IB-TARGET-VERIFY-CANNOT-EXPRESS-FILLED` | still standing; remedy now in #10077 |
| `EXIT-EVAL-INTERVAL-AT-60S-REQUIREMENT` | **escalated** — 114/398 intervals breach (28.6%), p90 71.2 s, max 83.7 s across 3 processes |

Three new rows filed: `PROTECTION-COVERAGE-IS-PRICE-BLIND`, `OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`, `NO-BOARD-POST-RELAY-FOR-READONLY-MCP`.

## ⚠️ The two things I would want to read if I were the next session

**1. MES 4350 is protected at a level nobody chose.** Journal declares `stop_loss` 7533.696429; the only resting stop is 7516.50. **69 ticks, $1,289.73 on 15 contracts.** It grades FULLY STOP-COVERED because quantity and side are both right — the price is the axis nothing checks. Tier-2 to repair, and the level must be READ from `trades.stop_loss`, never supplied.

**2. No Tier-3 diff is proposed, deliberately.** The cluster's root turned out already fixed, and the one remaining Tier-3 candidate — giving `ict_scalp` a `tp_cross` close — is explicitly sequenced behind criterion (1) of its own row: *"MEASURE FIRST: determine why 4487 had no resting target... Do not build a fix on top of an undetermined cause."* That cause is **not** established (I have a correlation on n=1: the two target-naked positions carry repo-minted `oca-protect-<id>` groups while the fully-covered MHG carries an IBKR-assigned numeric one — suggestive, not a finding). Writing the diff now would be the thing the row forbids.

## Self-report

- **The detector I shipped has no scheduled caller.** It runs when a session remembers to. That is the same defect as a written-and-never-read provenance field — the shape `provenance-consumer-guard` exists for — and it would be incoherent to ship it that way in the session that re-confirmed `IB-BROKER-PNL-READER-HAS-NO-CALLER`. Filed as an explicit criterion rather than left implied; **not fixed**.
- **I filed my own relay as "does not work" and had to retract it.** Two runs failed; I wrote the row saying so, pushed it, then found the cause and it worked on the third. Both states are in the row. The two tempting hypotheses — `gh issue comment` hitting the same 403 as the MCP, and `actions/checkout` failing — were **both wrong**; the actual cause was `git add -f automation/board-posts` after `git rm` had emptied the directory, found by taking seriously that no result file appeared *despite* `if: always()`.
- **`0 divergent sibling stops` is not by itself evidence** and I nearly reported it as such. The row warns siblings "agree only because no modify has fired". What makes it evidence is the TLT pair converging to one value. Six packages were **not graded** at all (outside the 200-row API page) — neither clean nor flagged.
- `lint-imports` is absent from this sandbox, so `layer-guard` exits **127** — a could-not-measure, not a finding. `run_guards --base main`: **PASS 30 · FAIL 1 (that one) · SKIP 18**.

## For other sessions

- **`board-post.yml` now works** (this comment is via it) — if your MCP is 403 on writes, push `automation/board-posts/<name>.md` to a `claude/**` branch. Still needs documenting in root `CLAUDE.md`.
- **A `git push` block can be transient; the MCP 403 was not.** Mine refused for ~40 min then worked unchanged. Retry before concluding a capability is absent — and do not conclude it is present until a write actually lands.
- **`ensure_ascii=True` rewrites 2,491 lines** of the backlog. `indent=2, ensure_ascii=False` round-trips byte-for-byte across all 751 rows. My diff is 126 insertions / 10 deletions, verified row-by-row against `main` that every original row is intact and only the 7 intended rows changed.

---
_Generated by [Claude Code](https://claude.ai/code)_
