# Sprint S-017 — Activate live trading + smoke test

**Mode:** Operator-collaborative session. Hard stops at the live smoke.
**Created:** 2026-04-30. **Predecessors:** S-016 housekeeping closed (CP-2026-04-30-13).

## 1. Goal

All outstanding operator-action items from `docs/operator-actions.md` resolved
on the live VM, AND the live trading path verified end-to-end via a smoke
test that places real exchange orders through both strategy plumbings
(turtle_soup on `bybit_1`, vwap on `bybit_2`) and confirms the full
observability chain works (signal → exchange call → trade journal → Telegram
ping → `/trades` / `/balance` / `signal_audit.jsonl` / `runtime_status.json`).

The smoke trades are tagged `meta.strategy_name="smoke_test"` so they don't
pollute strategy P&L attribution.

## 2. Dependencies

- **External:** Operator online for the live smoke (T7). Bybit account funded
  with USDT on both `bybit_1` and `bybit_2`. Anthropic console access for the
  OAuth rotation (T3).
- **Sprint:** S-016 H3 ping wiring must be live on the VM (PR #213). Already
  on main. The first ping fired off CP-2026-04-30-13.
- **Infra:** the unchanged live-order path
  (`src/runtime/orders.py::safe_place_order`) is the ONLY entry point used
  by the smoke script — no strategy code changes anywhere.

## 3. Deliverables

- `scripts/smoke_test_trade.py` — one-off CLI to inject a tagged signal
  through `safe_place_order`, capture the response, close the position if
  filled.
- `tests/test_smoke_test_trade.py` — safety-cap and signal-shape tests.
- `src/bot/telegram_query_bot.py` — `httpx` log filter (operator-action C).
- `.env.bybit_1` + `.env.bybit_2` populated on the VM with live keys.
- Optional: `deploy/ict-web-api.service` `WorkingDirectory=` corrected.
- `docs/runbooks/live-smoke-test.md` — checklist for future smoke verifications.
- `docs/operator-actions.md` — items A/B/C/D marked resolved.
- 2 to 4 real trades in `trade_journal.db`, all attributed to
  `smoke_test` strategy.
- `bug-log.md` updated with anything found.

## 4. Checkpoints

| # | Checkpoint | What completes by then | Risk class | Wall-clock | Gates |
|---|---|---|---|---|---|
| T0 | Plan + alignment | This file committed; operator confirmed A/B/C all-of-the-above and two-key plan | docs | done | T1+ |
| T1 | C: httpx log filter | `logging.getLogger("httpx").setLevel(WARNING)` in bot module + bug-log entry | infra | 15 m | T5 |
| T2 | D: `/opt/ict-trading-bot` verify | Operator runs `ls -la /opt/ict-trading-bot` + `systemctl status ict-web-api` and posts back; symlink fix or unit edit per result | operator-action | 5 m | T5 (only if `ict-web-api` was failed) |
| T3 | A: OAuth revocation | Operator revokes leaked token at console.anthropic.com; verifies the active VM token still works via `/vm what time is it` | operator-action | 5 m | none |
| T4 | B: Bybit API keys (BOTH accounts) | Operator generates one key per account (Read+Trade, no Withdrawals), populates `.env.bybit_1` and `.env.bybit_2`, restarts trader. Verifies `/balance` returns non-zero on both | operator-action | 15 m | T5 |
| T5 | Pre-smoke green | I verify via Telegram: `/health` all units active, `/balance` non-zero on both accounts, `/trades` empty, `/status` shows live trader running | infra | 10 m | T6 |
| T6 | DRY_RUN smoke (autonomous) | With `ALLOW_LIVE_TRADING=false`: invoke `smoke_test_trade.py --account bybit_1 --qty 0.0001 --dry-run` then same for bybit_2. Verify journal logs `dry_run` entries with strategy=`smoke_test` | infra | 20 m | T7 |
| T7 | LIVE smoke (operator confirms) | Operator flips `ALLOW_LIVE_TRADING=true`. **Step 1 of 4:** sub-min order on bybit_1 (qty=0.0001 → expect rejection for size). **Step 2:** real order on bybit_1 (qty=0.001, ~$70 → fill + immediate close). **Step 3:** sub-min on bybit_2. **Step 4:** real on bybit_2. Each step is operator-greenlit before firing | **live trade** | 30 m | T8 |
| T8 | Verify the full chain | For each filled smoke: `/trades` shows it (then doesn't, after close), `/balance` reflects fees, `signal_audit.jsonl` has 2 entries (open + close) tagged `smoke_test`, `runtime_status.json` advanced, Telegram pings fired, `/health` still green | infra | 20 m | T9 |
| T9 | Runbook + bug-log + operator-actions update | `docs/runbooks/live-smoke-test.md` future-checklist; mark A/B/C (and D if done) resolved in `operator-actions.md`; new bug-log rows for anything surfaced | docs | 15 m | T10 |
| T10 | Final checkpoint | `CP-…-S017-COMPLETE` (auto-pings high-priority via S-016 H3 wiring) | docs | 5 m | none |

**Total wall-clock:** ~2.5 hours. T2/T3/T4 are operator-driven and can run in
parallel with T1.

## 5. Risk class & merge model

| Class | Items | Self-merge? |
|---|---|---|
| **infra** (bot UX, smoke script + tests, runbook, operator-actions update, bug-log update) | T1, T6, T8, T9, T10 | ✅ self-merge |
| **operator-action** (must run on the live VM with operator hands) | T2, T3, T4, T7 | ❌ operator does these; I write up the verification |
| **deploy / live** (touching the order path) | none — the smoke goes through the existing `safe_place_order` unchanged | n/a |

The smoke script itself is infra (it constructs and dispatches a signal but
doesn't modify the order path), so it self-merges. The act of *running* it
in LIVE mode (T7) is operator-driven.

## 6. Success criteria

- `python scripts/secret_scan.py` clean throughout.
- `pytest tests/test_smoke_test_trade.py` passes.
- `/balance` on bybit_1 AND bybit_2 returns non-zero numbers in Telegram.
- `/health` shows all four units (`ict-trader-live`, `ict-telegram-bot`,
  `ict-web-api`, `ict-git-sync.timer`) `active`.
- At least one rejected-for-size order entry in `signal_audit.jsonl`
  tagged `smoke_test` (proves the rejection-path plumbing).
- At least one filled+closed round-trip in `trade_journal.db` tagged
  `smoke_test` per account (proves the happy-path plumbing).
- Telegram pings fired automatically for the trades (not just the
  checkpoint pings).
- After all smokes: `/trades` shows no open positions on either account.
- `docs/operator-actions.md` items A, B, C marked resolved (D resolved
  if T2 surfaced anything).

## 7. Hard guardrails

1. **Do not edit `src/runtime/orders.py`, `src/runtime/risk_counters.py`,
   `src/runtime/notify.py`, or any strategy file** — the smoke script
   constructs a signal and calls the existing entry point, full stop.
2. **`smoke_test_trade.py` MUST refuse** if `qty > 0.001` (hard cap).
3. **`smoke_test_trade.py` MUST refuse** if `--confirm` flag is missing
   in LIVE mode.
4. **Operator confirms** before each LIVE order in T7 (4 separate
   greenlights for 4 orders).
5. **`/halt` is the abort key** — if anything looks wrong mid-T7, the
   operator types `/halt` and we stop. The kill-switch flag file
   propagates to `safe_place_order` within one tick.
6. **No new secrets handling** beyond populating the two `.env.bybit_*`
   files which already exist for env-loading. No rotation logic added.
7. **Smoke trades only** — the strategy multiplexer is unchanged. Real
   strategy signals continue to flow through; the smoke is *additive*.

## 8. Hand-off

After T10, the next session is the planning sprint that S-016 was
preparing for. It picks up `docs/claude/bug-log.md`'s standing patterns
(`config`, `git`, `deploy`, `tests`) as architectural topics, and the
cleaned-up bot surface as the operator's working environment.

## 9. Operator pre-conditions (read first if you're the operator)

Before T1 starts:

1. Confirm Bybit account funding: `bybit_1` USDT balance ≥ $200 and
   `bybit_2` USDT balance ≥ $200 (covers two $70 round-trips per
   account + slippage + fees).
2. Confirm you can SSH (or Oracle Console-connect) to the VM —
   T2/T4 need `sudo systemctl restart ict-trader-live` and
   `sudo nano /home/ubuntu/ict-trading-bot/.env.bybit_*`.
3. Have console.anthropic.com open in a tab for T3.
