▶️ **START** — session `01Hd8opmCBcNQRchwdasWpvL` · branch `claude/alpaca-ext-hours-close-qty`

**Scope I am touching**
- `src/units/accounts/alpaca_client.py` — Alpaca close paths. **ORDER PATH, Tier-2.** PR opens as **DRAFT**; I will **not** merge it.
- `src/web/api/routers/diag.py` — one read-only `log_file` allowlist entry.
- `tests/test_alpaca_wiring.py`, `docs/claude/health-review-backlog.json`.

No VM mutation, no workflow dispatch beyond these relays, no merge.

---

⚠️ **Heads-up for any session working `alpaca_paper` / GLD — the briefed diagnosis is REFUTED.**

I was dispatched on the live *"GLD won't flatten"* pages with the theory that `_close_extended_hours` is a sibling call site that never got `BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE`'s fix. Verified against the code and the live fleet, that is **not** what is happening:

1. **PR #10666 already did that port** — merged 2026-09-01T16:23Z, ~4h *before* the 20:12Z measurement in my brief. It is **deployed and running**: its new self-diagnosing log line (`alpaca extended-hours close GLD still insufficient-qty after ~6.0s`) is firing on the trader right now.
2. **The regular-hours path fails identically.** At 13:30–13:34Z today (inside RTH) the close failed with the same error on the same order — and that path already has *both* `_await_qty_available` **and** the `DELETE /v2/positions/GLD?cancel_orders=true` escalation. So there is no working sibling to copy from; porting would have fixed nothing.
3. **Actual blocker — broker-side.** GLD OCO parent `2e843e04-5487-470c-a702-70e796fbd05e` is stuck in Alpaca **`status: "pending_cancel"` with `canceled_at: null`**, resting since 2026-08-27. Its 39 shares stay `held_for_orders`, so `qty_available: 0` on every path. Alpaca's own `cancel_orders=true` liquidation returns the same `insufficient qty available for order (requested: 39, available: 0)`.

That is `BL-20260716-ALPACA-QQQ-WEDGED-PENDING-CANCEL` **recurring**, and the existing code comment already concedes this is past the last programmatic lever.

**I am not claiming a fix for the wedged position.** No bot-side change clears an Alpaca `pending_cancel`; that needs operator/venue action. What my PR does is make the condition **nameable** — the residual-order dict already carries `status: pending_cancel` and nothing ever reads it, so a permanently-wedged broker order is reported identically to a transient cancel race, and the alarm retries forever on something no retry can clear.

**Real-money exposure:** `alpaca_live` (`mode: live`, `account_class: real_money`) shares this client code and trades GLD, but is **currently flat with zero resting orders** — verified via `/api/diag/exchange_positions` + `/api/diag/alpaca_open_orders`. Exposure is **structural, not realised**.

**Alarm question:** #10666's exponential backoff is deployed. I could **not** measure the actual page rate — `runtime_logs/operator_alerts.jsonl` (the ring backing the `close_failure` banner) is **not on the diag `log_file` allowlist**, so it is unreadable from a relay-bound session. My PR adds that entry. Note the close-failure alert does **not** ride `outcomes.jsonl`, so `/api/bot/logs?level=error` shows zero of them — do not read that as "not paging".

✅ DONE to follow.
