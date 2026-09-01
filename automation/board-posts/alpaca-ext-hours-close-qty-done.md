✅ **DONE** — session `01Hd8opmCBcNQRchwdasWpvL` · branch `claude/alpaca-ext-hours-close-qty` · **PR #10679 (DRAFT, not merged)**

**Outcome: the briefed diagnosis was refuted, and that is the finding.**

I was dispatched on the live `alpaca_paper` GLD *"won't flatten"* pages with the theory that `_close_extended_hours` was an uncovered sibling of `BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE`. Measured against the code and the fleet:

- **PR #10666 already made that port** (merged 16:23Z, ~4h before the 20:12Z measurement in my brief) and it **is deployed** — its new `still insufficient-qty after ~6.0s` log line was firing on the trader at 21:19Z.
- **The regular-hours path fails identically** at 13:30–13:34Z the same day, and it already has *both* `_await_qty_available` **and** the `cancel_orders=true` escalation. There was no working sibling to port from.
- **Actual cause is broker-side:** GLD OCO parent `2e843e04-…` at `status: pending_cancel`, `canceled_at: null`, resting since 2026-08-27, holding all 39 shares. Our DELETE *was accepted* — that is what moved it to `pending_cancel` — and Alpaca never completed its own cancel. `BL-20260716-ALPACA-QQQ-WEDGED-PENDING-CANCEL` recurring.

So the scope's second branch applies: **it cannot be ported, and here is why.**

**What the PR does change** (two provable defects): `classify_share_hold()` gives four never-collapsed states so a permanent wedge stops reading identically to a transient race (the residual dict already carried `status` and nothing ever branched on it); and `_cancel_open_orders_for_symbol` now reads the broker's envelope instead of discarding it — `_request` "never raises on HTTP", so its `except Exception` was dead code and it counted cancels *issued*, not *accepted*.

**It does NOT clear the wedged position.** No bot-side lever releases an Alpaca `pending_cancel`. ⚠️ **Operator action needed on `2e843e04` (paper-console reset / venue-side cancel).** The close still returns `retCode 1` — a real failure, never a defer.

**Real-money exposure:** `alpaca_live` (real_money, `mode: live`) shares this code and trades GLD, but was verified **flat, zero resting orders** at 21:23Z. Structural, not realised.

**Alarm question — reported as UNMEASURED, not as fine.** #10666's exponential backoff is deployed but I could not grade it: `runtime_logs/operator_alerts.jsonl` had no diag read surface (PR adds it), `/api/bot/notifications` carries no history, and ⚠️ **these alerts do not ride `outcomes.jsonl`** — the full 1000-row `/api/bot/logs?level=error,warn` feed (08-21 → 09-01) holds **zero** close-failure rows while the banner was live. Do not read that silence as "not paging". A live read after this deploys is what settles it.

*Noticed while measuring, already owned by `BL-20260825-TRANSIENT-CLASSIFIER-MISSES-THE-VARIANT-FAMILIES`:* **839 of those 1000 rows (83.9%)** are `strategy_builder` no-candle exceptions. Still live, worse than when filed.

Filed: `BL-20260901-ALPACA-CANCEL-WEDGE-UNNAMED` (high), `BL-20260901-OPERATOR-ALERTS-HAS-NO-READ-SURFACE` (medium) — both via `backlog_append.py` `similar_ok=True`, 18 insertions / 0 deletions.

Releasing the files listed in my START. Not merging — #10679 awaits operator approval.
