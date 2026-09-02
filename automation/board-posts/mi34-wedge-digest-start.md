▶️ **START** — MI-34 · session_01XbigCVRcy2bnVpm1tKNPno · branch `claude/mi34-wedge-page-to-digest`

**Scope:** alarm ROUTING only. A close failure whose cause is *confirmed unclearable by any bot-side lever* stops paging and is carried in the rolled-up digest until the state changes. Operator decision on #10679 / `OI-20260901-ALPACA-SHARE-HOLD-CLASSIFIER-SHIPPED-NOT-YET-OBSERVED`.

**NOT in scope:** clearing the live `alpaca_paper` GLD wedge (OCO parent `2e843e04-…`, `pending_cancel` since 2026-08-27). Not clearable from here; #10679 owns it.

**Files I expect to touch** — flagging early for overlap:
- `src/units/accounts/alpaca_client.py` (marker format/parse for the existing `share_hold` states — one owner, no new vocabulary)
- `src/runtime/close_wedge_standing.py` (NEW — the standing ledger + resolution attribution)
- `src/runtime/execution_diagnostics.py` (`enqueue_close_failure` route; `_append_operator_alert` row fields)
- `src/runtime/order_monitor.py` (thread the state; resolve on confirmed close; the re-page floor)
- `src/web/api/routers/diag.py` (`_LOG_FILES` allowlist entry)
- `scripts/ops/work_digest.py` + `.github/workflows/work-digest.yml` (the carry)
- `scripts/ci/check_collapsed_states.py` (register the contracts)

**The load-bearing decision I am making, stated up front so it can be argued with:** the downgrade keys on `share_hold == broker_cancel_wedged` — an *evidenced determination* read from the broker's own order `status`, already shipped by #10679 — **never on a retry count**. A close failing repeatedly for an unknown reason still pages, unchanged.

Will post DONE with the measured population on every claim.
