# Sprint Log: S-ROADMAP-WORKPLAN-REVIEW-2026-08-14

## Date Range
- Start: 2026-08-14 (~11:30Z)
- End: 2026-08-16 05:50Z (the session sat idle ~29h between 2026-08-15 00:56Z and 2026-08-16 05:42Z; that gap is stated because every "current" reading taken before it was stale by the time the session resumed)

## Objective
- Primary goal: operator-requested full review of the roadmap and all work plans across the three repos — what is actually built vs what remains — then a single prioritized, serialized plan the session could execute **autonomously** while the operator was travelling.
- Secondary goals: (a) run without colliding with a concurrent long-running M20 exit-strategy session; (b) a performance read to aim the next work; (c) operator Telegram pings ~1/hr regardless of whether anything happened.

## Tier
- Mixed: predominantly **Tier 1**, with two **Tier 2** changes in PR #9334.
- Justification: the review, research packets, backlog reconciliation and diag reads are Tier-1 (docs / observability / read paths). PR #9334 changes a live notification cadence and a live read surface, which is Tier-2 — shipped only after the operator answered "Ship both Tier-2 fixes" in conversation on 2026-08-14. No Tier-3 file was touched: `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml`, `src/runtime/orders.py` and `src/runtime/risk_counters.py` are untouched by every PR below.

## Starting Context
- Active roadmap items: M20 exit-coverage matrix (owned by a **concurrent** session), M16/M25 regime, macro M1/M28.
- Prior sprint reference: [`S-M20-TPCAP-PROVENANCE-AND-BLOCK-SIZE-2026-08-13`](S-M20-TPCAP-PROVENANCE-AND-BLOCK-SIZE-2026-08-13.md), [`S-SYSTEM-AUDIT-REVIEW-2026-08-13`](S-SYSTEM-AUDIT-REVIEW-2026-08-13.md).
- Known risks at start: an operator-flagged concurrent M20 session working the same exit/IB surfaces — collision was the primary process risk, and it materialised once (see *Contradictions*).

## Repo State Checked
- Branch/commit reviewed: start `main` @ `84ba67e0`; end `main` @ `5d5bbb67`. Work branch `claude/roadmap-work-plan-review-m2aw6p`, ending synced to `5d5bbb67`, 0 unpushed, tree clean.
- Deployment state reviewed: **verified twice against the live VM, not inferred.** `/api/diag/status` returned `git_sha be7c8cd3` at 2026-08-14T22:51:33Z (= `main` at that moment, i.e. this sprint's #9334 was deployed) and `git_sha 5d5bbb67` at 2026-08-16T05:43:14Z (= current `main`).
- Canonical docs reviewed: `CLAUDE.md` (§ Dashboard REST API, § Environment Variables), `ROADMAP.md`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`, `docs/claude/system-actions.md`, `.importlinter`.

## Files and Systems Inspected
- Code files inspected: `src/prop/prop_status_request.py`, `src/prop/prop_identity.py`, `src/prop/prop_balance.py`, `src/prop/prop_reconcile.py`, `src/prop/breakout_notify.py`, `src/prop/prop_journal.py`, `src/prop/telegram_report_handler.py`, `src/web/api/routers/prop.py`, `src/runtime/account_reachability_alert.py`, `src/core/coordinator.py`, `src/units/db/database.py`, `src/main.py`, `scripts/ops/dead_leg_audit.py`, `scripts/ci/run_guards.py`.
- Config files inspected: `config/prop_rulesets/breakout.yaml` (`account_size_usd: 5000`, `drawdown_type: static`, `max_drawdown_pct: 0.06` ⇒ the $4,700 floor), `config/accounts.yaml`, `config/strategies.yaml` (read-only, for the account/strategy routing table).
- Deployment files inspected: none changed.
- Docs inspected: `CLAUDE.md`, `ROADMAP.md`, `docs/claude/health-review-backlog.json`, `docs/research/WORKPLAN-2026-08-14.md`, `docs/research/CAPITAL-ACTIVATION-PACKET-2026-08-14.md`, `docs/research/M27-GLD-1H-PROMOTION-PACKET-2026-08-14.md`.
- Services/timers inspected (via `/api/diag/services`, twice): `ict-trader-live`, `ict-web-api`, `caddy`, `ict-telegram-bot`, `ict-claude-bridge` all `active`; all timers `active`. `ict-ib-gateway-watchdog.timer` `inactive` on the trader — **correct, not a finding** (that watchdog runs on the gateway VM, gated on `/etc/ict-vm-role == gateway`).
- GitHub Actions workflows inspected: `vm-diag-snapshot` (relay, used 6×), `system-actions` (`send-ping`, used 8×), guards/pytest via PR checks.

## Work Completed
- **#9244** — roadmap review: found the roadmap's own "remaining" lists could not be trusted; 3 seam fixes, 2 decision packets, a standing dead-leg audit (`scripts/ops/dead_leg_audit.py` + 10 tests).
- **#9313** — Lane 0.6: measured that `breakout_1`'s breach guard had been scoring a **$125.61** cushion off a then-25-day-stale reading.
- **#9316** — M30: resolved both loose ends; recorded that neither surviving item is worth starting.
- **#9317** — recorded the RIGHT reachability fix before anyone built the obvious wrong one (a `balance()` probe on the live tick).
- **#9322** — generalised the stale-forward-looking-claim defect beyond the roadmap, after my own check-ins hit it twice.
- **#9334 (Tier-2, operator-approved)** — two live fixes:
  - `run_prop_status_request` no longer early-exits on a flat book. Trigger is now `prop_identity.declared_prop_account_ids(live_only=True)` ∪ position-holders; an open position is message *context*, not the trigger. Rationale: the static DD floor is account-level and binds while flat.
  - `/api/bot/prop/status` + `compute_rule_distance` now carry `status_age_hours` + a four-state `status_freshness` (`ok`/`stale`/`absent`/`unchecked`, plus `error` on the envelope). Undateable ⇒ `stale`, matching `prop_balance.prop_sizing_balance`.
  - New `src/runtime/silent_refusal_alert.py` — latched alert for an account that produces refusal rows and **zero** placed rows. Journal-read only; **no broker round-trip**, preserving `account_reachability_alert`'s documented invariant.
  - New `src/runtime/dead_leg.py` — one home for the placed/refused status vocabulary + verdict rule, shared by the live alert and the offline audit.
- **#9336** — closed the two backlog rows #9334 resolved, each stating what it did **not** fix.
- Coordination: board `▶️ START` + `✅ DONE` on issue [#6927](https://github.com/benbaichmankass/Metis-Insights/issues/6927); ceded `BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY` to the M20 session (they subsequently shipped #9331 for it).
- Operator pings: updates 19–26 via `send-ping` (issues #9335, #9339, #9355, #9356, #9359, #9365, #9369).

## Validation Performed
- Tests run: `pytest -k prop` → **408 passed, 6 skipped**. `tests/test_silent_refusal_alert.py` + `tests/test_dead_leg_audit.py` → **32 passed**. `tests/test_prop_rule_distance_freshness.py` → 8 passed. `tests/test_prop_identity.py` → 25 passed.
- Guards: `scripts/ci/run_guards.py --base main` over a **regenerated, asserted-non-empty** `/tmp/pr.diff` — #9334 (1,756 lines) → **PASS 32 · FAIL 0 · SKIP 5**; #9336 (44 lines) → **PASS 14 · FAIL 0 · SKIP 23**. Includes `collapsed-state-guard`, `prop-identity-guard`, `silent-empty-guard`, `layer-guard`, `canonical-doc-coherence`, `ruff-lint`.
- Manual code verification: read every changed file in full before editing; read `git log`/history context on the Tier-2 prop files.
- **Live production verification** (this is the part that distinguishes shipped from working):
  - `/api/bot/prop/status?account_id=breakout_1` (diag [#9364](https://github.com/benbaichmankass/Metis-Insights/issues/9364), 2026-08-15T00:53Z) returned `status_age_hours: 614.42`, `status_freshness: "stale"`, `status_max_age_hours: 24.0`, `distance_to_dd_floor_usd: 125.61`, with the freshness verdict repeated inside `rule_distance` as designed. Re-read 2026-08-16T05:43Z (diag [#9547](https://github.com/benbaichmankass/Metis-Insights/issues/9547)): `645.24` hours — the field is live and advancing.
  - Trader liveness read twice (diag [#9362](https://github.com/benbaichmankass/Metis-Insights/issues/9362), [#9547](https://github.com/benbaichmankass/Metis-Insights/issues/9547)): heartbeat `running`, `exit_loop_health: fresh`, services active.
- Dry-runs / staging: none required (no VM mutation was performed by this sprint; `send-ping` and the read-only diag relay were the only workflow dispatches).
- **Gaps not yet verified:**
  1. **The Telegram half of the prop ask.** The API half of #9334 is field-verified; the operator-facing `bal <balance> <equity>` prompt rides the same deployed code but **no send was observed**. Not claimed as verified.
  2. **`silent_refusal_alert` has never fired in production.** Its cadence is hourly and no qualifying account existed during the observation windows; it is verified by tests and by deployment, not by a live latch.
  3. **Whether `alpaca_live` was funded.** The operator answered "Fund it, keep dry_run"; no balance read was taken to confirm.
  4. The 29-hour idle gap was not observed — no claim is made about system behaviour between 2026-08-15 00:56Z and 2026-08-16 05:42Z beyond the two end-point reads.

## Documentation Updated
- Rules doc updates: none required.
- Architecture doc updates: none required.
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): **not updated — no pipeline stage changed.** #9334 adds a tick *hook* (observability) and a read-surface field; it neither adds nor alters a signal→order→trade stage.
- Roadmap updates: this sprint's Historical Sprint Ledger row (this PR).
- GitHub Actions doc updates: none.
- Subsystem doc updates: `CLAUDE.md` in **three** places whose prose had become false — the `PROP_STATUS_REQUEST_*` env row (said "while a prop position is open"), the `/api/bot/prop/status` API row (lacked the freshness contract), and a new `SILENT_REFUSAL_*` env row. *Field beats comment.*
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **A test asserted the defect.** `test_flat_account_never_pings_and_prunes_state` pinned the exact broken behaviour (`== []` when flat, state pruned to `{}`) and passed. Rewritten with the reason in its docstring so it is not "restored" later.
- **`CLAUDE.md` prose described behaviour the code no longer had** in two rows (above), and one of those rows had described the *defect* as the design.
- **Not mine, but load-bearing and recorded here:** the M20 session found that `scripts/ci/run_guards.py` consumed `/tmp/pr.diff` without generating it, so local guard runs silently scanned a stale diff while printing "All relevant guards passed"; their fix rides PR #9257. This sprint's guard results are unaffected — the diff was regenerated from `origin/main...HEAD` and asserted non-empty before every run — but that was habit, not protection, and it is luck worth naming. *(Their board comment announced a backlog id for this; as of 2026-08-16 that id resolves to nothing in `docs/claude/health-review-backlog.json`, because the row lands with #9257, which has not merged. Deliberately not citing it here and deliberately not filing a duplicate — `check_backlog_refs.py` caught the dangling citation in an earlier draft of this log, which is the guard doing exactly its job.)*
- **Filed earlier this session:** on the trainer VM the canonical resolver points at an **empty** stray journal (`BL-20260814-TRAINER-CANONICAL-RESOLVER-POINTS-AT-EMPTY-JOURNAL`) — 0 rows at `<repo>/trade_journal.db` while the real 4,649-row journal sits under `data/`. `scripts/ops/dead_leg_audit.py` hard-stops rather than reporting a clean all-clear against it.
- **Collision recorded honestly:** my IB thread-safety fix was **wrong** and the M20 session's review said so ("a plain lock is NOT sufficient… pinning is the fix"). I took theirs and deleted my test, which asserted the lock I no longer shipped.

## Risks and Follow-Ups
- Remaining technical risks: `BL-20260813-ALPACA-BALANCE-NONE-WHILE-ACCOUNT-READS-ACTIVE` stays **open** — #9334 made it *detectable*, not fixed. `balance()` still returns None on `alpaca_paper`/`alpaca_portfolio` while `/v2/account` reads ACTIVE.
- Remaining product decisions (Tier 3): (a) M27 GLD 1h — [packet](../research/M27-GLD-1H-PROMOTION-PACKET-2026-08-14.md) recommends **shadow-first**, 18 trades over ~2y is too thin for real money; (b) Lane 2 capital activation — [packet](../research/CAPITAL-ACTIVATION-PACKET-2026-08-14.md); (c) whether `ib_live` should be retired (zero strategies routed, one symbol).
- Blockers: **`breakout_1`'s breach guard is still blind.** As of 2026-08-16T05:43Z the snapshot is 2026-07-20 — **645h / 26.9 days** stale — and the $125.61 cushion to the $4,700 static-DD floor is derived from it. The bot now asks every 12h; only the operator's `bal <balance> <equity>` closes it. This is a live prop-account-killer distance computed from a month-old reading.

## Deferred Items
- Both open CRITICALs deliberately **not** taken — `BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY` (ceded; the M20 session shipped #9331, now `partially_resolved`) and `BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP` (inside their PR #9257, sprint log `S-M20-TPCAP-PROVENANCE-AND-BLOCK-SIZE`).
- Most of the 37 open HIGH items are that same M20 exit/IB domain and were left alone.
- A one-line backlog annotation recording the *field* verification of the prop fix was skipped rather than opening a third PR against a file that conflicts on nearly every concurrent-session pair; the evidence lives in diag #9364 and in this log instead.

## Next Recommended Sprint
- Suggested next sprint: **operator-decision execution** — the prop `bal`, the `alpaca_live` funding confirmation, and the `ib_live` retire/keep call — followed by whichever of the two Tier-3 packets the operator rules on.
- Why next: every remaining non-M20 item in this session's queue is gated on one of those decisions, not on engineering.
- Required verification before starting: re-read `/api/bot/prop/status` (the age advances continuously, so any figure in this log is a floor); confirm on board #6927 that the M20 session still owns the exit/IB surfaces before touching anything there.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint (3 `CLAUDE.md` rows + this log + the ledger row).
- [x] No pipeline stage changed, so `docs/TRADE-PIPELINE.md` was correctly **not** updated (stated explicitly rather than left ambiguous).
- [x] Roadmap status was checked; ledger row added.
- [x] Contradictions were recorded — including one where my own fix was wrong and another session's was right.
- [x] Remaining unknowns were stated clearly in *Gaps not yet verified* (4 items), rather than rounded up to "done".
