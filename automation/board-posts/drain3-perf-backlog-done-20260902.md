✅ **DONE** — Backlog drain #3 · scope `docs/claude/performance-review-backlog.json`

**PR: https://github.com/benbaichmankass/Metis-Insights/pull/10717** (draft)

**Burn-down: unresolved 45 → 44.** `45 − 2 closed + 1 filed = 44`, reconciled against head. Base sha `de61ead9`: 111 rows (66 resolved / 32 kept_open / 13 open). Head: 112 rows (68 / 31 / 13).

**CLOSED 2 · REFUSED 3 · FILED 1.**

A real class existed, and the repo had already named it — the 2026-08-29 triage's *"signals that never become trades, with nothing measuring why"*. One live measurement retires both of its rows in this file:

- **352 unfilled decisions over the 30.9d overlap window, all 352 attributed, ZERO residual**, no package lacking an underlying reason row.
- Root cause: **cross-strategy contention on a netted `(account, symbol)`** — **141 of 163 (86.5%)** `all_accounts_noop` suppressions were raised while a **different** strategy leg already held that account+symbol.
- Closed: `PB-20260816-ETHPULLBACK-FILL-RATE-12PCT` (high), `PB-20260630-003`.
- Refused with measurements: `PB-20260821-R-AND-DOLLARS-DISAGREE-IN-SIGN` (reproduces and **worsened** — 7 of 40 rows disagree in sign, new worst expectancyR **+206.9** from a single trade at **R = +3,672**, and the trailed-stop mechanism is now proven at **82 of 477 rows** rather than the n=1 on file), `PB-20260618-015` (n=4 real-money closes, pnlCoverage 0.0), `PB-20260821-SLV-TREND-1H-ZERO-WINS-IN-13` (headline stale a second time; coverage 0.77 → 0.2222 → **0.125**, which retires the row's own claim to priority).
- Filed 1 via `append_row` (no similarity refusal): `PB-20260902-MGC-TREND-1H-IS-LIVE-AND-STRUCTURALLY-CANNOT-TRADE` — a leg marked `execution: live` with **95 decisions and 0 fills**, because siblings own MGC on ib_paper **73.3%** of the window.

**Sibling sessions:** I touched no other backlog file and did not edit `OPEN-ITEMS.json`. No `OPEN-ITEMS` row is cleared by this evidence — checked, and reported as such rather than left unsaid.

⚠️ **For whoever is awake:** the live trader is serving `git_sha 49f03e37` with `git_sha_on_disk b466e327` and `restart_pending: true`. Every live claim in the PR describes `49f03e37`, not merged `main`.
