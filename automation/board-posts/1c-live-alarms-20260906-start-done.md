▶️ **START / ✅ DONE (same post — the session is ending at its capability wall)**

**Phase 1C — three unowned live alarms.** Session `session_01RHuSYKu1r1ZErc65KLKV8t`
(sub-session; manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`). Registry key
`pending-20260906T084730Z`. Work object `WO-20260906-THREE-LIVE-ALARMS-ON-THE-TRADING-FLEET`.
Branch `claude/live-alarms-1c-20260906`.

Posting START and DONE together because the session did all it could reach in one pass and is
stopping rather than pretending at coverage. **No VM was touched. No live read was performed.**

**Scope actually touched:** `docs/claude/dispositions/` (one new file) and
`.github/pr-landing/`. No `src/`, no `config/`, no `deploy/`, no order path.

---

### Disposition: 1 of 3 settled

**(c) `restart_pending` — SETTLED, recommend HOLD.** The gap `4ec87e38..c2e47af5` is 7 commits,
all `chore(ops)`, 23 files — 9 `docs/claude/`, 7 `.github/pr-landing/`, 7
`.github/pr-automerge-requests/`, and **0** under `src|config|deploy|scripts`. Probe validated by
positive control (6 / 45 / 311 hits at ~50 / 200 / 600-commit windows), so the zero is real.
`restart_pending` is purely `_RUNNING_GIT_SHA != on_disk` (`diag.py:1761`). **So the restart
delivers no behavioural change — and its real exposure is env drift, which `restart_pending`
cannot see and nobody has read.** ⚠️ A restart is the mechanism by which the operator's
**2026-09-02 hold on arming `BYBIT_GRADED_COVERAGE_MODE`** would be silently lifted if either key
has since been written to `/etc/ict-trader/*.env`. Precondition to lift the hold is a *read*
(`get-env` on `/proc/<MainPID>/environ` vs the `.env`), not an approval.

**(a) SOL orphan — NOT SETTLED.** Genuine-vs-artifact needs a fresh
`/api/diag/bybit_open_orders?account_id=bybit_1`, which this session could not reach. The
`604.7 / 105.41 / trade 5516` figures are **inherited, not verified here.**

⚠️ **But a concrete defect was found in the committed error-feed digest, and it is worth someone's
attention on its own:** reduce-only closes on `bybit_1`/SOLUSDT are being **rejected by the venue** —
`InvalidRequestError: Qty invalid (ErrCode 10001)`, `qty: "33.299999999999955"`, `reduceOnly: true`,
**3 consecutive close failures at 2026-09-06T03:37:22Z**. That qty is the float artifact of `33.3`,
i.e. **not quantised to the symbol's `qtyStep` before send. The position cannot be flattened.**
Order-path, **Tier-3 — escalated, NOT fixed here, no diff proposed.**

Also measured on that symbol: the hedge-mode side-blind divergence is **live and real here** —
`bybit_1`/SOLUSDT position 8.5 graded **100% covered by its own legs** while the side-blind sum
reads **18144%** (1 leg of 1533.7 on the opposite book). Anyone grading 5516 must use
`bybit_leg_sides.graded_book_coverage`, never `covered_qty`.

**(b) MES — NOT ROOT-CAUSED, and please re-scope it.** The dispatch describes it as blind *since
06:33Z today*, pointing at the pre-gateway-wedge class. The digest shows `⚠️ MONITOR BLIND /
candles_unavailable` on `mes_trend_long_1d` **16 times, first_seen 2026-09-02T02:03:23Z,
last_seen 2026-09-05T23:20:12Z**. It is **chronic, not an acute onset this morning.** (The digest
cuts off at 06:28:41Z so it cannot speak to 06:33Z — out of window, not contradicting.) An
inference worth checking, not a root cause: four days of recurrence fits a standing market-data /
contract-definition problem better than a wedged gateway. **There is an open MES position whose
monitor-driven exits are not running.**

> **Population for every digest number above:** `docs/claude/ERROR-FEED-DIGEST.json` at commit
> `c2e47af5`, `generated_at` 2026-09-06T06:28:41Z, `verdict: all_feeds_read`; `operator_alerts`
> 376 rows (oldest 2026-09-01T18:29:17Z), `bot_logs` 1000 rows **truncated**; 117 groups.
> ⚠️ Generated **before** both alarm timestamps, so it contains neither alarm.

---

### ⚠️ Capability note — please do not route further live-read work to a session like this one

**MEASURED this session, n = 3 attempts across two endpoints:** `issue_write method=create` → **403**
`Resource not accessible by integration` (×3, including once *after* the repo was granted git-push
access), `add_issue_comment` → **403**, while `issue_read` and `list_issues` **succeed**. Reads
work where writes refuse and no backoff cleared it ⇒ **write-scope boundary, not the transient MCP
drop.** This is a measurement on this session only — MI-77 measured the opposite on 2026-09-02.

The `vm-driver` fallback was also unavailable: pushing `automation/jobs/*.job` causes **arbitrary
bash to run over SSH on the live trading VM**, and the sandbox's safety classifier refused to stage
it. The job was read-only by construction (six `GET /api/diag/*`, an allowlisted 4-key
`/proc/<MainPID>/environ` read, `systemctl is-active`; token never echoed since results land in a
**public** repo). **That refusal was not worked around and should not be** — a pushed file that
executes on a live money-handling host is a reasonable thing to keep a human in front of.

**Net: this session had no path to the VM at all**, which is why (a) and (b) are open. Full
reasoning, with populations and the explicit NOT-VERIFIED marks, is in
`docs/claude/dispositions/2026-09-06-1C-three-live-alarms.md` on the branch above.

**For the manager to relay to the operator:** (1) confirm the **HOLD** on the restart, or authorise
the env-diff read first; (2) get a session that can reach the diag lane onto the SOL protection
question — it is genuinely unknown and the misleading basis is measured to be misleading *on this
exact symbol*; (3) the `10001 Qty invalid` close failure is a standing risk independent of the
orphan; (4) re-scope MES as chronic.
