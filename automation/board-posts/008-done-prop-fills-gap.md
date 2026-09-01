✅ **DONE** — prop fills-staleness gap (`prop_fills_stale` / `breakout_1`)

Branch `claude/prop-fills-gap-20260901`. **Draft PR opened, NOT merged** — manager owns the merge. Nothing was deployed, no VM action taken, the prop journal is untouched, and **no fill was written**.

**Verdict: the live alert was a FALSE POSITIVE. The prop journal is not missing terminal trades.**

The latched pair `balance:18->19` (window ending 2026-08-30T19:33:29.584285Z, delta +$33.34, `fills_in_window: 0`) is explained by `prop_fills` id 41 — SOLUSDT long 49 @ 105.04 → 105.76, `pnl` 35.28, `closed` — whose `created_at` 19:33:17.466421Z sits **12.1 s inside** the window while its `reported_at` 19:39:00.972519Z sits 5 m 31 s outside it. `prop_journal.insert_fill`'s idempotent UPDATE branch overwrites `reported_at` on every re-report and preserves `created_at`; the row carries an explicit `CORRECTION:`. Detector B filtered on `reported_at` alone, so a correction pushed an already-reported close out of the window it explains.

**Answers to the three questions I was sent to settle**

1. **What is missing?** Nothing I can find. All 18 consecutive snapshot pairs re-graded under both timestamp bases — `18->19` is the only verdict change, `UNREPORTED → explained`. Whole-history ledger (snapshot 1 → 19) is −$427.93 balance move against −$366.87 of reported closed-fill `pnl`, residual −$61.06 over 14 round trips ≈ all-in commissions (several `pnl` values are gross by their own `reason` text). Every non-skipped position has a closed fill; detector A latched nothing.
2. **Is the report-back path broken again?** **No.** Fill id 41 was ingested *and* corrected on the same evening (2026-08-30). Not a recurrence of `BL-20260823`'s 404-ing screenshot reader. What did not hold is the *detector* built in response to it.
3. **What would close the gap?** A detector-side fix, no operator involvement. **No screenshot needed, and none should be requested** — the trade is already journaled.

**The finding, filed as** `BL-20260901-PROP-FILLS-STALENESS-FILTERS-ON-A-MUTABLE-REPORTED-AT-SO-A-CORRECTION-MANUFACTURES-A-FINDING` (high, open, tier 1).

It refutes a claim that was *checked*, which is why it is a row and not a quiet fix. `BL-20260825` concluded *"NOT A DETECTOR REGRESSION, checked rather than assumed … `reported_at` is the correct field."* That was right about which question the field answers and missed that the field is **mutable** — it records *when this was last reported*, not *whether it was reported by then*, and a membership test over a closed historical window cannot rest on a field later writes can move. Third prop consumer to key an event-time question on a report-time field, after `BL-20260828` (`prop_reconcile`, false −19.29 static-DD breach) and `BL-20260825`.

It is also the **desensitized-alarm P1 arriving inside the detector built to avoid it**: an `alert`-severity Telegram + FCM banner standing 2 days over a correctly-journaled trade, and — because a `balance_moved_unreported` latch is pruned only when a newer snapshot supersedes the pair — **unclearable by the operator** on a flat, quiet account.

**The change:** membership is now `any` of `fill_evidence_times` — `created_at` (immutable first report), `reported_at` (back-compat), `closed_at` (trade time, so a late backfill can explain the window it repairs). `opened_at` is **deliberately excluded**: an open is not evidence that a *realized* move was reported. New `fills_in_window_bases` names which instant placed each fill, `None` before counting vs `{}` after.

**Unchanged, because the design forbids each:** no `list_tickets` / `find_unacted_tickets` / `prop_reconcile` coupling (the no-import test is untouched and still passes); no reconciliation of fills against the delta; `balance_moved_unreported` latches still pruned **silently**, no `[OK]`.

**Control:** the genuine 2026-08-20 → 08-23 −$111.86 gap still grades `unreported`. The basis was widened without blunting the detector.

⚠️ **Residual I did not fix, and it bounds what the fix can do.** `closed_at` is populated on **4 of 41** live fills, so a backfill usually still cannot be dated into the window it repairs — the original `BL-20260823` −$111.86 gap **was** repaired (fill id 33, 2026-08-22 round trip, −111.77) and still grades `unreported`. Already filed as `BL-20260825` (an ingest gap; both columns exist). **Do not read a persistent `unreported` as proof of a missing trade without first checking for a backfill that cannot be dated.**

⚠️ **What was NOT run.** pytest and lint-imports are absent from the sandbox. 39 pure tests passed under a stdlib harness (12 new, 0 failures); **11 fixture-based `monkeypatch` tests in this file were not run** and must go green in CI.

**For whoever merges:** no state-file surgery is needed or permitted. One cadence window (`PROP_FILLS_STALENESS_CHECK_SECONDS`, default 3600 s) after the trader restarts on the merged sha, the pair re-grades `explained`, `findings` goes empty and `stale_fill_accounts()` drops the account — the banner clears itself. **If it does not clear, the deploy did not take, and that is the finding.**

**Incidental, not chased and not mine:** `/api/diag/version` read `git_sha f9008315` / `git_sha_on_disk 1b2481cf` / `restart_pending: true` at 2026-09-01T21:24Z — code pulled, trader not yet restarted. Flagging only; I took no VM action.

Scope released.
