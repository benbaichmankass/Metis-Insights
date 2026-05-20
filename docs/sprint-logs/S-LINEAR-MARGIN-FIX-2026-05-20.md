# Sprint Log: S-LINEAR-MARGIN-FIX

**Sprint:** 10 (S-LINEAR-MARGIN-FIX)  
**Date:** 2026-05-20  
**Type:** auto-claude (Tier-3 — touches live coordinator + risk path)  
**Closes:** PR #1026 (superseded — circuit breaker already fixed; margin sizer rewritten fresh)  
**PR:** TBD — draft, Ben's approval required before merge

---

## Background

PR #1026 (`claude/no-auto-dry-flip-and-margin-cap`, 2026-05-12) had two parts:

1. **Remove auto-dry-flip circuit breaker** — violates Prime Directive ("no auto-flip").
2. **Fix position-sizer margin bug** — replace arbitrary `_MARGIN_SAFETY_BUFFER = 0.9` with live `availableToWithdraw` from Bybit UNIFIED API.

Part 1 is already done on `main`: an intermediate PR renamed `_EXCHANGE_REJECTION_PAUSE_THRESHOLD` → `_EXCHANGE_REJECTION_ALERT_THRESHOLD` and changed the action from `set_account_dry_run` to `push_alert`. Prime Directive no longer violated.

Part 2 was still needed. PR #1026 couldn't be rebased (887-commit conflict-fest — branch cut from pre-squash snapshot). This sprint implements Part 2 fresh against current `main`, with one design improvement.

---

## Design: Hybrid ceiling (improvement over PR #1026)

PR #1026 proposed: use `available_usd` when present, **skip the ceiling entirely** when `available_usd=None` (fetch failure). That's a regression — on any transient Bybit API failure the account has zero margin protection.

This sprint implements a hybrid:

| `available_usd` | Ceiling formula | When |
|---|---|---|
| Not None | `available_usd × leverage / entry` | Live linear-perp accounts, fetch succeeded |
| None | `balance_usd × leverage × 0.9 / entry` | Spot, dry-run, or fetch failure |

The buffer path (0.9×) is preserved as a permanent safety net. The live figure is more accurate because it reflects existing open positions consuming margin — the root cause of the 2026-05-12 110007 incident.

---

## Root Cause of 2026-05-12 Incident

bybit_2: $158 wallet, 3× leverage. Risk-based sizing produced 0.008 BTC ($729 notional). Required initial margin = $729 / 3 = $243. Wallet had $158. Bybit returned ErrCode 110007 ("ab not enough for new order").

The `_MARGIN_SAFETY_BUFFER = 0.9` workaround was added as a patch: `158 × 3 × 0.9 / entry ≈ 0.005 BTC`. This helped in the specific incident but fails silently whenever existing open positions consume a large fraction of available margin — `0.9 × wallet` can still exceed `availableToWithdraw`.

---

## Files Changed

**`src/units/accounts/execute.py`**
- Added `_fetch_linear_available_balance(client)` — calls `GET /v5/account/wallet-balance?accountType=UNIFIED`, returns USDT `availableToWithdraw`, returns `None` on any error.

**`src/core/coordinator.py`**
- In `multi_account_execute` sizing block: when `market_type == "linear"`, `client is not None`, `not effective_dry`, and not a test order, calls `_fetch_linear_available_balance` and passes the result as `available_usd` to `position_size`. Falls back to `available_usd = None` on any exception.

**`src/units/accounts/risk.py`**
- `position_size`: hybrid ceiling — when `available_usd is not None`, uses `available_usd × leverage / entry`; when `None`, falls back to `balance_usd × leverage × _MARGIN_SAFETY_BUFFER / entry`. `_MARGIN_SAFETY_BUFFER = 0.9` kept as the fallback constant.
- Updated docstring to describe both paths.
- Removed `del available_usd` (parameter is now used, not discarded).

**`tests/test_risk_manager_margin_cap.py`**
- 4 existing buffer-path tests preserved (unchanged behavior)
- 4 new live-figure tests added: ceiling scales to `available_usd`, refuses when too small, no-op when fits, `None` falls back to buffer

---

## Test Results

- `tests/test_risk_manager_margin_cap.py`: 8/8 passing
- `tests/test_coordinator_shadow_cache.py`: 22/22 passing (no regression)
- Full suite: 3534 passing (pre-existing failures not caused by this sprint)

---

## Definition of Done Assessment

- [x] `_fetch_linear_available_balance` added to execute.py
- [x] Coordinator calls it for linear live accounts, passes `available_usd` to sizer
- [x] Hybrid ceiling in `position_size`: live figure when available, buffer fallback always present
- [x] 8/8 tests passing — both paths covered
- [x] No regression in coordinator shadow cache or other risk tests
- [x] Sprint log written
- [x] Draft PR open, Ben's approval required (Tier-3: live coordinator + risk path)

---

## Follow-ups Generated

PR #1026 can be closed — circuit breaker already fixed separately; this sprint delivers the margin-sizer fix fresh against current main.
