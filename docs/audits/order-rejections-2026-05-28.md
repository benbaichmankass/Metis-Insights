# Order-rejection investigation — 2026-05-28

**Branch:** `claude/ict-order-rejections-BmqU1`
**Tier:** Investigation = Tier-1 (read). The one code change shipped on the
branch is a logging/observability fix in the order-path file
(`src/core/coordinator.py`) — opened as a **draft PR for operator review**,
not merged. The risk-cap and rejection-noise items below are **Tier-3
recommendations only** (no code/config change applied).

## TL;DR

The "~88% of recent orders are rejected" signal is almost entirely a
**`vwap`-on-the-demo-account artifact**, not a real-money execution failure.

- Every recent rejection is **`bybit_1` / `vwap` / `BTCUSDT`**, reason
  `risk_refused: sized_qty=0 … check daily-loss budget`.
- `bybit_1` is the **demo** account (paper money) and `vwap` is
  **`execution: shadow`** (data-only — never sends a live order). So these
  refusals touch **zero real money** on two independent axes.
- The rows are written **`is_demo=0`** because of a logging bug (below), which
  is what made them *look* like live-account rejections in the diag pull. That
  was the false premise the investigation opened on.
- The real-money account (`bybit_2`) and the five live strategies
  (`trend_donchian`, `turtle_soup`, `ict_scalp_5m`, `fade_breakout_4h`,
  `squeeze_breakout_4h`) show **no order-path failure** in the data pulled —
  they were simply **"no actionable signal"** during the window.
- The trader is **alive and healthy** (ticking ~60–90 s, market data OK,
  heartbeat fine).

## Evidence (live diag relay, 2026-05-28 ~22:1x UTC)

Pulled via the `vm-diag-request` issue relay (single-param paths; the relay
truncates each comment to ~55 KB and the journal endpoint has no offset/filter,
so windows are "newest-N that fit in 55 KB"):

### `journal?table=trades` — 43 most-recent rows (15:59→22:10 UTC)

| dimension | breakdown |
|---|---|
| status | **rejected: 43 / 43** |
| account_id | **bybit_1: 43** |
| strategy_name | **vwap: 43** |
| symbol | BTCUSDT: 43 |
| is_demo | 0: 43  ← *mislabelled; see bug below* |
| `notes.reason` | all `risk_refused: sized_qty=0 with balance≈274 500 available_usd=n/a total_account_usd=n/a min_balance_usd=50.00 … check daily-loss budget` |

`balance` drifts 274 564 → 273 585 across the window (demo equity declining).
`available_usd`/`total_account_usd` are `n/a` because `vwap` is shadow →
`effective_dry=True` → the coordinator builds no exchange client, so the live
linear-balance fetch is skipped.

### `journal?table=order_packages` — 19 most-recent decisions (19:55→22:17 UTC)

| status | count | notes |
|---|---|---|
| orphaned | 15 | all `vwap`; `sized_qty_by_account: {}`, `aggregated_target_qty: 0.0` |
| closed | 3 | all `vwap`, `close_reason=sl_cross` |
| open | 1 | `vwap` (`pkg-a026e731937e4d11`) |

At the **decision** level (one row per package) the same picture holds: 100 %
`vwap`. The few that opened (3 closed + 1 open) are `vwap` shadow trades that
sized non-zero on the account whose daily budget wasn't exhausted, ran, and
closed on `sl_cross`.

### `journalctl?unit=ict-trader-live.service` (live tail)

- Bot ticking normally; `Bybit market data environment: mainnet`.
- The five non-vwap strategies all log **"no actionable signal"** (Turtle Soup
  "no setup", trend_donchian "no breakout / confidence below min", fade
  "regime not chop", squeeze "no squeeze release", ict_scalp "no liquidity
  sweep"). They are **quiet, not rejecting**.
- `vwap` fires on essentially every tick and currently shows
  `strategy_monocle: skipping dispatch — strategy=vwap already has open
  package pkg-99b8f1318664454b` and `intent_multiplexer: 'vwap' emitted intent
  … target_qty=0.000000`.
- **Separate real defect surfaced:** a flood of
  `FCM publish non-2xx: status=404 … NOT_FOUND` from
  `src.runtime.mobile_push.notifier` — the Android push token is stale/not
  found. Unrelated to order rejections, but it is the kind of thing that makes
  the **Android app** look broken (it was the app's Order Packages / Performance
  tabs that surfaced this). Logged for follow-up.

## Why `sized_qty=0` (the dominant cause)

`_explain_zero_sized_qty` only emits `risk_refused` (not `below_min_balance`)
when `gate_balance ≥ min_balance_usd`. With balance ≈ 274 500 ≫ 50, the zero
comes from inside `RiskManager.position_size`. Working the arithmetic with the
logged inputs (entry≈73 729, sl≈73 822 → `risk_distance ≈ 93.6`,
`risk_pct=0.01`, vwap `strategy_risk_pct=1.0`, `qty_precision=3`):

- Unbounded risk-sized qty ≈ `274 500·0.01 / 93.6 ≈ 29 BTC`.
- Margin pre-flight ceiling ≈ `274 500·3·0.9 / 73 729 ≈ 10 BTC` — does **not**
  bind to zero.
- The only path that yields **exactly 0** is the **daily-loss-budget gate**:
  `loss_budget_remaining = daily_usd(100) + daily_pnl`. The scaled qty is
  `loss_budget_remaining / 93.6`; that floors below `min_qty (0.001)` only when
  `loss_budget_remaining ⪅ 0.09`, i.e. **`daily_pnl ≈ −100`** (cap exhausted).

So `bybit_1`'s **daily-loss cap is exhausted**, and every subsequent signal on
that account sizes to 0. With ~$274k of Bybit demo paper balance against a
`daily_usd: 100` cap (≈0.04 % of equity, copied from `bybit_2`'s real-money
profile), the cap trips almost immediately each UTC day and then refuses
everything for the rest of the day. The `STRATEGY_REFUSAL_COOLDOWN_SECONDS`
(5 min) gate throttles the re-fire, so the refusals land roughly every ~5 min →
dozens of `rejected` rows per day, all `vwap` (the only frequently-firing
strategy). This is the same shape logged before as FU-20260510-002
(`src/runtime/strategy_monocle.py` header).

**Not directly verified:** I could not read `daily_risk_state` or `bybit_1`'s
closed-today trades to confirm `daily_pnl ≈ −100` numerically — the diag
`journal` allowlist is `{order_packages, trades}` only, the relay truncates to
55 KB, and the endpoint has no offset/filter. The `daily_pnl ≈ −100` conclusion
is **inferred from the sizing arithmetic + the reason string**, which is
unambiguous given balance ≫ min_balance. Confirming it needs a filtered
Data-Explorer query (`/api/bot/db/table/daily_risk_state` /
`…/trades?filter_col=account_id`) or the BL-20260528-001 multi-param relay fix.

## The `is_demo` mislabel (the fix on this branch)

`Coordinator.multi_account_execute` journals the two **early** refusal paths —
the `position_size` exception (`sizing_failed`) and the `sized_qty<=0` gate —
with `_early_account_cfg`, a minimal dict that **omitted `demo`**.
`_log_trade_to_journal` derives `is_demo` from `account_cfg.get("demo")`, so
those rows were written `is_demo=0` even for the demo account. The RiskBreach
and `exchange_rejected` paths already use the richer `account_cfg` and were
stamped correctly.

**Fix (shipped on branch, draft PR):** add
`"demo": getattr(account, "demo", False)` to `_early_account_cfg` so the early
paths match. Regression test: `tests/test_rejection_demo_flag.py`. This is the
change that lets the operator (and any future diag pull) *filter the demo noise
out* of the rejection statistic — it directly removes the false premise.

## Operator-approved fixes (2026-05-28, implemented on this branch)

After review, the operator approved the following (Tier-3 — pending merge +
deploy):

1. **Percentage-based daily-loss cap.** New `daily_loss_pct` risk field; when
   set, the daily-loss budget is `daily_loss_pct × equity` instead of the fixed
   `daily_usd` (which becomes the absolute fallback used only when no equity
   snapshot is available). Set to **0.05 (= `max_dd_pct`)** on the Bybit + IB
   accounts (`bybit_1`, `bybit_2`, `ib_paper`, `ib_live`); the **prop account is
   left unchanged** (keeps its deliberately strict absolute `$50`). This is the
   fix that stops `bybit_1` re-tripping a $100 cap against a ~$274k paper
   balance. Code: `src/units/accounts/risk.py` (`effective_daily_loss_usd`,
   `is_daily_cap_exhausted`, gates in `evaluate` + `position_size`).
2. **Latching cap-hit / resume notification.** New
   `src/runtime/daily_cap_alert.py` + `enqueue_daily_cap_alert`: exactly one
   Telegram when an account first exhausts its daily-loss cap, and one when it
   clears (incl. the 00:00 UTC auto-reset). Closes the silent-cap gap — the
   `sized_qty<=0` path previously emitted no ping. Hooked into
   `multi_account_execute` once per account per dispatch (self-deduping latch in
   a `runtime_logs` JSON file; money-DB schema untouched).
3. **`is_demo` mislabel** — fixed earlier on this branch (`_early_account_cfg`
   carries the `demo` flag).

**Verified, no fix needed:**

- **Daily reset / auto-resume** works by design — `RiskManager` has no persistent
  "halted" latch; `daily_pnl` is recomputed each tick from *today's* closed
  trades, so it resumes automatically at the UTC roll. It only *looked* stuck
  because the demo's tiny cap re-tripped within minutes of resuming (the % cap
  fixes that).
- **`bybit_1` strategy wiring** — all six strategies are configured
  (`trend_donchian, turtle_soup, ict_scalp_5m, fade_breakout_4h,
  squeeze_breakout_4h, vwap`) and the live journalctl showed all six
  signal-builders running each tick. `vwap` being `shadow` (data-only) is
  correct; the other five are live-on-demo. No change required.

## Remaining follow-up

- **Re-pull the original window's 8 `exchange_rejected` rows** — they predate
  this window and are unreachable via the single-param relay (no offset). Pull
  via a filtered Data-Explorer query to characterise the exchange-side refusals.
- **Rejection-row noise** — a daily-loss-capped account still writes a
  `rejected` *trades* row each cooldown interval. With the % cap this is now rare
  (caps trip far less often), so left as-is; revisit if it recurs.

## What was NOT found

- No evidence of a real-money (`bybit_2`) order-path failure in the pulled data.
- No broken/changed exchange filter, no margin failure, no `exchange_rejected`
  cluster in the 43-row / 19-package window (all refusals were RiskManager-side
  `sized_qty=0`).
- No mode-flip, no auto-disable — consistent with the Prime Directive (account
  stays live; refusals are per-trade).
