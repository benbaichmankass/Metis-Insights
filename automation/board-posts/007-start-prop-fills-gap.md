▶️ **START** — prop fills-staleness gap (`prop_fills_stale` / `breakout_1`, latched since 2026-08-30T19:33:29Z)

Branch `claude/prop-fills-gap-20260901`. **Draft PR only — I will not merge.** The manager owns the merge.

**Scope I am touching**
- `src/prop/prop_fills_staleness.py` — detector B's fill-membership predicate ONLY
- `tests/test_prop_fills_staleness.py`
- `docs/claude/health-review-backlog.json` (via `scripts/ops/backlog_append.py`)
- `automation/` relay request files (board post + PR open), since my GitHub MCP is read-only here (`add_issue_comment` → 403 "Resource not accessible by integration", confirmed twice against a live `get_me`, so this is the permission boundary and not the transient drop)

**Not touching:** the live VM, `POST /api/bot/prop/report` (Tier-2), the prop journal itself, any config, any other module.

---

**Headline: the live alert is a FALSE POSITIVE, and the mechanism behind it is a real defect.**

Reads (direct diag against `https://ict-bot.duckdns.org`, live, 2026-09-01T21:2x–21:3xZ): `prop_account_status` (19 rows), `prop_fills` (41 rows), and the latch file `/data/bot-data/runtime_logs/prop_fills_staleness_state.json`.

The latch holds exactly one finding — `balance:18->19`, window `(2026-08-30T13:37:39.446290Z, 2026-08-30T19:33:29.584285Z]`, delta `+$33.34`, `fills_in_window: 0`.

But `prop_fills` id 41 — SOLUSDT long 49 @ 105.04 → 105.76, `pnl` 35.28, status `closed` — carries:

| field | value | vs. window end |
|---|---|---|
| `created_at` | `2026-08-30T19:33:17.466421Z` | **12.1 s BEFORE** — inside the window |
| `reported_at` | `2026-08-30T19:39:00.972519Z` | 5 m 31.4 s AFTER — outside |

`prop_journal.insert_fill` is idempotent, and its UPDATE branch overwrites `reported_at` with `now` while preserving `created_at`. The row's own `reason` text carries an explicit `CORRECTION:` — it is a corrective re-report. So a close that WAS reported 12 seconds before the snapshot got pushed out of the very window it explains. `assess_balance_move` filters fills on `reported_at` alone.

**Two defects in one predicate, both demonstrated on live rows:**

1. **`reported_at` is mutable.** A corrective re-report — routine on this bridge; ids 20 and 41 both show the drift (+4270 s and +344 s) — can manufacture `unreported` on a correctly-reported close.
2. **The window is bounded by REPORT time while the balance delta is caused by TRADE time.** A late backfill, which is the normal repair here, can never land inside the window it repairs. Live instance: the original `BL-20260823` −$111.86 gap (pair `10->11`) **was** backfilled — fill id 33, a 2026-08-22 round trip, `pnl` −111.77 (Δ $0.09 vs the delta), reported 2026-08-23T11:04:22Z, i.e. after the 08-23T08:11 snapshot that closed the window. That pair still grades `unreported` today. **The finding is unretireable by the repair that fixes it.**

This is the desensitized-alarm P1 arriving inside the detector built to avoid it: a latched `alert`-severity banner standing for 2 days on a correctly-journaled trade.

**Is the report-back path broken again (the `BL-20260823` precedent)? No.** Fills are flowing — id 41 was ingested and then corrected the same evening (2026-08-30), so both the ingest chokepoint and the operator path were working 2 days ago. This is not a recurrence of the 404-ing screenshot reader.

**Is anything actually missing? On the evidence I can reach, no.**
- Re-graded all **18** consecutive snapshot pairs under both timestamp bases. `18->19` is the **only** pair whose verdict changes, and it flips `UNREPORTED → explained`.
- Whole-history ledger (snapshot id 1 → id 19): balance move **−$427.93** against **−$366.87** of reported closed-fill `pnl`; residual **−$61.06** across 14 round trips ≈ all-in commissions, several of which the rows' own `reason` text says are excluded from `pnl`.
- Every non-skipped ticket has a closed fill. (One cosmetic quirk: ticket `prop-manual-SOLUSD-498665711`'s close was journaled under a different `ticket_id`, `prop-manual-1014c5abcce8` — same symbol/qty/entry. A linkage artifact from 2026-06-29, not a missing trade, and it predates the first snapshot.)
- Detector A (`crossed_unreported`) is quiet — no crossing findings latched.

**Operator ask: NONE.** No screenshot is needed and none should be requested — the trade is already in the journal. I am not writing a fill and not touching the prop journal.

---

**What I am preparing (detector-side only):** widen detector B's membership test from one mutable instant to the fill's own evidence instants — `created_at` (immutable first report), `reported_at` (kept, back-compat), and `closed_at`/`opened_at` (trade time, when present) — matching if **any** falls in the half-open window, and publishing which basis matched so the verdict states its own derivation.

**Explicitly unchanged, because the design forbids each:**
- no coupling to `list_tickets` / `find_unacted_tickets` / `prop_reconcile` — the existing no-import test stays and still passes;
- **no** reconciliation of fills against the delta — the question stays *"was anything reported"*, never *"does it add up"*;
- `balance_moved_unreported` latches still pruned **silently**, no `[OK]`.

Will post ✅ DONE with the PR link.
