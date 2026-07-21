# Sprint Log: S-XRP-TPSL-LEGCAP-2026-07-21

## Date Range
- Start: 2026-07-21T08:35:00Z (operator-flagged live screenshot: 23 open TP/SL entries for XRPUSDT on `bybit_2`)
- End: 2026-07-21T10:32:00Z (structural fix merged + deployed, `ict-trader-live.service` verified active on new code)

## Objective
- Primary goal: diagnose and fix the root cause of unbounded Bybit TP/SL leg accumulation on `bybit_2` XRPUSDT under `BYBIT_TPSL_MODE=partial`, without reopening the netted-bracket-sharing bug (`BL-20260720-ICTSCALP-PASTSTOP-EXITS`) that `partial` mode was built to fix.
- Secondary goals: relieve the immediate leg-cap saturation on the live account; land three PRs' worth of content stranded on a stalled/conflicted earlier session's branch (PR #7296) into `docs/claude/{health,performance,ml}-review-backlog.json`; resolve a stale/duplicate PR (#7308) whose content had already landed outside the normal merge flow.

## Tier
- Tier 2 (stopgap leg cancellation, `cancel-stale-tpsl-legs` system-action) + Tier 3 (structural fix touching the live order path — `src/units/accounts/execute.py::modify_open_order`/`close_open_position`, `src/runtime/order_monitor.py`, `src/units/db/database.py` schema).
- Justification: per CLAUDE-RULES-CANONICAL.md, order-path code changes are Tier 3 and require explicit operator approval before merge. The operator approved both the stopgap `--apply` run and the structural-fix merge explicitly in chat this session ("ok, you can merge").

## Starting Context
- Active roadmap items: none of the named M-milestones directly cover this — it surfaced as a live incident during routine PR/backlog follow-up work, not a planned roadmap item.
- Prior sprint reference: `BL-20260721-BYBIT2-XRP-TPSL-LEGCAP` was opened earlier the same day (05:27 UTC) by an earlier session, from a live journalctl `ErrCode 110061` observation — this sprint is the direct continuation of that backlog item to root-cause, stopgap, and structurally fix it.
- Known risks at start: `BYBIT_TPSL_MODE=partial` (PR #7115, 2026-07-20) was itself a Tier-3 fix for a DIFFERENT bug (netted-position bracket sharing) — any fix here had to preserve that fix's qty-scoping, not revert to `full` mode.

## Repo State Checked
- Branch or commit reviewed: `origin/main` at each step (`5b4e018` → `f11c147` → `9fc7439` → `63cb230` → `e3203af` across the session's merges).
- Deployment state reviewed: live VM (`ict-bot-arm`) HEAD confirmed via `pull-and-deploy` system-action output before and after each deploy; `ict-trader-live.service` confirmed `active` post-deploy both times (issues #7318, #7329).
- Canonical docs reviewed: `CLAUDE.md` (BYBIT_TPSL_MODE env-var entry, system-actions table), `docs/claude/system-actions.md`.

## Files and Systems Inspected
- Code files inspected: `src/units/accounts/execute.py` (`_submit_order`, `modify_open_order`, `close_open_position`, `_log_trade_to_journal`, `execute_pkg`), `src/runtime/order_monitor.py` (`_send_modify_to_exchange`, `_send_close_to_exchange`, `_cancel_resting_protection_after_flat`), `src/units/db/database.py` (trades schema + migrations).
- Config files inspected: none changed (`BYBIT_TPSL_MODE` itself was not flipped this sprint — it was already `partial` on `bybit_2`/`bybit_1` per the earlier PR #7115 rollout).
- Deployment files inspected: `.github/workflows/system-actions.yml`, `scripts/ops/notify_run.sh`, `scripts/ops/_lib.sh`.
- Docs inspected: `CLAUDE.md`, `docs/claude/system-actions.md`, `docs/claude/health-review-backlog.json`, `docs/claude/performance-review-backlog.json`, `docs/claude/ml-review-backlog.json`.
- Services or timers inspected: `ict-trader-live.service` (restart-verified twice, post-#7315 deploy and post-#7321 deploy).
- GitHub Actions workflows inspected: `system-actions.yml` (dispatched `pull-and-deploy` ×2, `cancel-stale-tpsl-legs` ×3 — 2 dry-run + 1 apply), `vm-devnull-source-diagnose.yml` (dispatched `udev-watch` mode, unrelated follow-up, inconclusive — SSH connection dropped mid-capture).

## Work Completed
- **Root-caused** the leg-accumulation bug against Bybit's own V5 API docs (fetched live, not assumed): under `tpslMode=Partial`, `set_trading_stop` is documented as ADD-only, never an in-place amend (unlike Full mode). `modify_open_order`'s Partial branch called `set_trading_stop` on every trailing-stop tick with no leg-id tracking and no cancellation, so legs piled up unboundedly until Bybit's 20-combined-leg-per-symbol cap silently blocked further amends.
- **Live-confirmed** the diagnosis: `exchange_positions` showed exactly one real XRPUSDT position on `bybit_2` (165.5 qty) against 20-23 duplicate SL legs all sharing that qty, cross-corroborating an independently-opened backlog item (`BL-20260721-BYBIT2-XRP-TPSL-LEGCAP`, opened 05:27Z by an earlier session from a live `ErrCode 110061` journalctl error).
- **Shipped the Tier-2 stopgap** (PR #7315, merged): `scripts/ops/cancel_stale_tpsl_legs.py` + `_action.sh`, wired into `system-actions.yml` as `cancel-stale-tpsl-legs` (dry-run default, `apply: true` gated). Lists a symbol's live conditional orders, keeps the newest SL/TP leg per group, cancels the rest; refuses on a flat position or zero SL legs found (naked-position guard).
- **Executed the stopgap** (operator-approved): dry-run confirmed the plan twice (state drifted between runs — 20→5 SL legs, likely operator-side manual UI cleanup between checks, later confirmed by the operator); `--apply` cancelled 4 stale legs, verified post-cancel state = 1 SL + 1 TP leg live on `bybit_2` XRPUSDT (system-action issue #7323).
- **Shipped the Tier-3 structural fix** (PR #7321, merged + deployed, operator-approved): `trades.sl_order_id`/`.tp_order_id` columns; `execute_pkg` captures the entry-time Bybit leg id via a before/after conditional-order snapshot diff (Bybit's inline-SL/TP place response never returns the leg's own orderId — this is the only way to learn it); `modify_open_order` amends the tracked leg in place via `amend_order` instead of `set_trading_stop` when an id is tracked (falls back to the legacy add-a-leg path, unchanged, when it isn't — pre-migration trades, Full mode, or an ambiguous capture); `close_open_position` best-effort cancels the closing trade's own tracked leg(s) so a leg never outlives its trade.
- **Deployed the structural fix**: `pull-and-deploy` (issue #7329) moved the live VM `63cb230a → e3203aff`; `ict-trader-live.service` restarted cleanly, boot_audit confirmed `xrp_pullback_2h` (the strategy behind the incident) back up with its one open package recognized.
- **Landed three stranded PRs' worth of backlog content** (unrelated cleanup surfaced while working this incident): PR #7296 (an earlier session's partial backlog-drain) had a real but mostly diff-alignment merge conflict against current `main` in `docs/claude/{health,performance,ml}-review-backlog.json`. Resolved via a JSON-parse-and-diff-by-`id` methodology (not raw text diff) across three follow-up PRs — #7314 (already merged pre-session), #7316 (`ml-review-backlog.json`), #7317 (`health-review-backlog.json` + `performance-review-backlog.json`) — each landing only the genuinely new content and correctly leaving alone items where `main` had already moved further ahead. #7296 closed as fully superseded once verified.
- **Closed PR #7308** as redundant: its content (the `udev-watch` diagnostic mode) was already on `main` via commit `950bb58`, merged outside the normal PR-merge button, leaving the PR object stuck `open`/`merged:false` despite the code being live — verified via a zero-diff check on the one file it touched.

## Validation Performed
- Tests run: 344 targeted tests locally (every test file touching `modify_open_order`/`close_open_position`/`_log_trade_to_journal`/the Bybit partial-tpsl path/the monitor's exchange-modify/close wiring/intent-reduce partial-close/linked-trade-id wiring) — all pass. Full CI `pytest-run` on PR #7321 (real dependency set, unlike this session's sandbox which lacks fastapi/numpy/pandas) — 7961 passed, 8 skipped, 0 failed (one fixture-schema test needed updating for the two new columns, fixed same session). `writer-conformance`/`canonical-db-resolver`/`silent-empty-in-diff`/`dry-run-in-diff` CI guard scripts all run clean against the diff.
- Dry-runs or staging checks: `cancel-stale-tpsl-legs` dry-run dispatched twice against the real `bybit_2`/XRPUSDT account before any `--apply`, per the operator's explicit process request ("merge + dry-run now, then show me the plan before applying").
- Manual code verification: read `execute_pkg`, `modify_open_order`, `close_open_position`, and their callers end-to-end; confirmed the new leg-capture guard (`_bybit_tpsl_mode() == "partial"`) means the structural fix's new API calls never fire for any test or live path not already in partial mode.
- Gaps not yet verified: **the structural fix's live entry-time leg-capture has not yet been observed against a real fresh entry** — `BYBIT_TPSL_MODE=partial` trades opened before the 10:32Z deploy have no tracked leg id and still ride the legacy fallback path until they close and a fresh entry opens under the new code. The PR's own test plan flagged (and did not complete) a live `validate-partial-tpsl`-style entry+modify+close cycle on the demo account (`bybit_1`) to confirm the leg-id capture resolves correctly against the real `get_open_orders(orderFilter="StopOrder")` response shape for a freshly-placed Partial leg — this is the most important open verification, not yet done. The `udev-watch` diagnostic follow-up (unrelated, pre-existing `/dev/null`-clobbering investigation) was dispatched but its SSH stream broke mid-capture — inconclusive, not a verified negative.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none (schema change is additive/nullable, no existing contract changed).
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): not touched — checked, this sprint's change is a broker-adapter-level fix (how a leg amend is dispatched), not a pipeline-stage change.
- Roadmap updates: this log's row added to the Historical Sprint Ledger (see below).
- GitHub Actions doc updates: `docs/claude/system-actions.md` — added the `cancel-stale-tpsl-legs` row in PR #7315; corrected the same row this session (doc-freshness pass) to reflect the structural fix having since shipped, rather than describing it as a still-pending "tracked follow-up."
- Subsystem doc updates: `CLAUDE.md`'s `BYBIT_TPSL_MODE` environment-variable entry corrected this session (doc-freshness pass) — it still described the pre-#7321 always-add-a-leg amend behavior; now documents the leg-id tracking + amend-in-place path and the legacy fallback.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Contradiction 1 (fixed this session): `CLAUDE.md`'s `BYBIT_TPSL_MODE` entry was doc-vs-reality stale after PR #7321 shipped — described `modify_open_order`'s SL amend as always qty-scoped-add, with no mention of the new leg-id-tracked in-place amend path. Fixed.
- Contradiction 2 (fixed this session): `docs/claude/system-actions.md`'s `cancel-stale-tpsl-legs` row described the structural fix as a "tracked follow-up" not yet built — stale after PR #7321 merged the same session. Fixed.
- Code/doc mismatch: `src/runtime/order_monitor.py::_cancel_resting_protection_after_flat`'s comment states "Bybit/Alpaca/OANDA closes are atomic/position-attached, so there are no stranded resting legs to cancel" — true under `BYBIT_TPSL_MODE=full`, **not** true under `partial` (this incident is direct evidence). Not fixed in this sprint (out of scope — that function's IB-only sweep path wasn't touched, and PR #7321's `close_open_position` fix already covers the actual gap for Bybit's own close path); noted here so a future session doesn't take that comment as universally accurate. Not logged as a separate backlog item — recorded here is sufficient since the comment doesn't block or mislead any current work, and the real gap it might have implied (Bybit closes leaving stranded legs) is now closed by PR #7321.

## Risks and Follow-Ups
- Remaining technical risks: the live entry-time leg-capture path (before/after snapshot diff) is probabilistic under a genuine race (two same-symbol entries landing within the same poll window) — deliberately fails safe (leaves the id untracked, falls back to legacy behavior) rather than risking a mis-attributed leg id on a live stop, per the PR's own design, but this means a busy multi-strategy symbol could still occasionally miss capture and take longer to fully stop growing legs.
- Remaining product decisions (Tier 3): none pending — both Tier-2 (stopgap) and Tier-3 (structural fix) actions this sprint were explicitly operator-approved before execution.
- Blockers: none currently open. The one meaningful gap is the live-validation item under *Gaps not yet verified* above — recommend the next session (or this session's continuation) verify a real fresh Bybit Partial-tpsl entry+modify+close cycle captures and amends correctly before broader confidence in the fix.

## Deferred Items
- Deferred item 1: `scripts/ops/cancel_stale_tpsl_legs.py`'s leg-listing helpers (`_stop_orders`/`_leg_group`) duplicate the shape of PR #7321's `_partial_tpsl_leg_ids`/`_classify_new_partial_tpsl_legs` — not factored into one shared module (kept PR #7321 minimal/reviewable for a Tier-3 change); worth a follow-up cleanup PR.
- Deferred item 2: the `udev-watch` `/dev/null`-clobbering diagnostic (unrelated pre-existing investigation, `BL-20260629-DEVNULL-OCI-SOURCE-KILL`) — the fresh capture dispatched this session (issue #7320) had its SSH stream break early; worth a retry by a future session, not treated as a ruled-out negative.

## Next Recommended Sprint
- Suggested next sprint: live-validate PR #7321's leg-id capture against a real fresh `BYBIT_TPSL_MODE=partial` entry on the demo account (`bybit_1`) — open, modify (trailing-stop tick), close — and confirm via `GET /api/diag/exchange_positions` / a direct leg listing that (a) the leg id was captured at entry, (b) the modify used `amend_order` (not `set_trading_stop`) and did not add a new leg, and (c) the close cancelled the tracked leg.
- Why next: this is the single most important unverified claim from this sprint — the fix's local/CI tests are thorough but none of them exercise the real Bybit API response shape for a freshly-placed Partial leg, which is exactly the shape `_classify_new_partial_tpsl_legs` depends on to correctly attribute a leg id.
- Required verification before starting: confirm `bybit_1` (the demo account) is currently flat on whatever symbol is used for the test, per the `validate-partial-tpsl` action's own flat-at-start guard pattern (avoid contaminating a live demo strategy's position, the exact mistake `validate-partial-tpsl`'s first run made on BTCUSDT, #7145).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries — read `execute.py`/`order_monitor.py`/`database.py` end-to-end before writing the fix, and re-read the actual on-disk migration/schema pattern (`_migrate_add_broker_order_id`) to mirror it exactly.
- [x] Documentation was reviewed and updated as part of the sprint — `CLAUDE.md` + `docs/claude/system-actions.md` corrected this session (doc-freshness pass, see above).
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified — N/A, confirmed this sprint's change is a broker-adapter fix, not a pipeline-stage change; `docs/TRADE-PIPELINE.md` not touched.
- [x] Roadmap status was checked — `ROADMAP.md` Historical Sprint Ledger reviewed; this sprint has no existing row (added one, see roadmap diff this sprint).
- [x] Contradictions were recorded — see § Contradictions or Drift Found above.
- [x] Remaining unknowns were stated clearly — see § Validation Performed § Gaps not yet verified.
