# CA-A03 — Code audit Wave A: execution core II (exits, positions, fills, invariants, protection, broker truth)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Lane question:** does the code in scope do what it declares, and where does it not?
**Findings file:** [`CA-A03.findings.jsonl`](CA-A03.findings.jsonl), 22 rows in the §5 schema.
**Method:** the lane read 6 protection modules itself. Three read-only subagents each fully read one group of 13–14 modules and reproduced defects with crafted inputs. The lane then re-checked every HIGH itself, by re-running the proof or by crafting its own input and reading the cited lines. Where a claim could be settled against live state, the lane read the diag relay and the public API itself.

## Findings by severity

| sev | id (AUD-20260927-CA-A03-…) | blast | tier | disposition |
|---|---|---|---|---|
| **high** | over-cover-keeper-qty-unchecked | money | 1 | **PI-20260927-ENGC5EUD-0001** |
| **high** | since-entry-window-includes-signal-bar | money | 3 | **PI-20260927-ENGC5EUD-0002** |
| **high** | closed-flat-partial-read-grades-flat | money (missed alert) | 2 | **PI-20260927-ENGC5EUD-0003** |
| **high** | naked-sweep-blind-on-dual-book-hedge-symbols | money | 2 | **PI-20260927-ENGC5EUD-0004** |
| medium | over-cover-unreadable-qty-as-zero | money | 1 | same fix as 0001 |
| medium | stray-oca-cancels-native-bracket-of-any-trade | money (paper today) | 2 | filed |
| medium | reassert-attempt-budget-never-resets | money | 2 | filed (call site in CA-A01 scope) |
| medium | stale-stop-fires-intrabar | money | 3 | filed |
| medium | closed-flat-stale-package-updated-at | money (missed alert) | 2 | filed |
| medium | package-leg-latch-never-pruned | observability — **live: 21 of 25 latches stale** | 1 | filed |
| medium | fifo-pnl-no-contract-multiplier | accounting — **live: MES/MGC/MHG on /api/bot/pnl/exchange** | 1 | filed |
| medium | fifo-pnl-pools-accounts | accounting | 1 | filed |
| medium | fifo-window-truncation-phantom-lots | accounting | 1 | filed |
| medium | telemetry-peak-state-overwrites-running-max | observability | 2 | filed |
| medium | exit-anchor-negative-cache-poisons-symbol | accounting | 2 | filed |
| medium | closed-flat-netting-false-positive | observability | 2 | filed |
| medium | broker-truth-wrong-shape-reads-as-no-record | accounting | 1 | filed |
| medium | exit-head-annotate-collapses-undeclared | observability | 1 | filed |
| low | minor-latent-and-docs (≈20 batched items) | docs/latent | 1 | filed |
| low | funding-sign-unsettled | accounting | 1 | open question |
| — | tp-venue-cap-single-owner; protection-price-states | — | — | verified-non-issue |

## Coverage — behavioural (primary)
- **Tests:** all 109 in-scope test files ran: **1908 passed, 2 skipped**. No existing test catches any finding above; every finding came from a crafted input or live state.
- **Checked against live state:**
  - `stray_oca_soak`: 131 rows, 6 acted.
  - `package_leg_coverage` latch: 25 packages, cross-checked against journal trades 3826–6225.
  - `/api/bot/pnl/exchange?days=30`: IB futures symbols present.
- **Run against crafted inputs:** over_cover_decision, stray_oca_groups, package_leg_coverage (temp DB), exchange_fills_store FIFO, exit_levers (giveback, stale), exit_head_apply, exit_head_shadow.shape_params, exit_anchor, exit_plan/materializer/realism, stale_leg_decision, dead_leg, closed_flat_invariant (DB and resolver), _closed_flat_wiring, clients.account_open_positions, broker_truth, bybit_wallet_truth, bybit_position_book, bybit_ccxt construction, position_telemetry, outcomes, positions, bracket_calibration, target_expectation.
- **Read only, not exercised:** trail_decay, trail_vol, exit_restart_gap, thesis_decay, bybit_leg_sides, bybit_position_mode, close_wedge_standing, orphan_attribution, exchange_fills_alpaca/ib mappers, net_r_label, protection_reassert (pure; the call site was read).

## Coverage — reading (secondary)
All 44 in-scope modules (13,465 lines) were read fully, by the lane or by a subagent. Call sites were read in order_monitor.py, clients.py, coordinator.py, ib_client.py, trend_donchian.py, backtest_trend.py and pnl_exchange.py, limited to the cited line ranges. Test files ran but were not all read line by line.

## Could not settle, and what would settle it
1. **Did the stray sweep ever cancel a *sibling's* native entry bracket?** 5 of its 6 live cancels hit native-bracket groups (IB auto-OCA named after the parent permId). To settle: map each cancelled group's parent permId to its trade id, e.g. via `pull_ib_executions` / the orderId on the trade row, and compare it with the keyed re-arming trade.
2. **Live frequency of dual-book hedge symbols.** `/api/diag/exchange_positions` returned non-JSON, and `/api/diag/ib_open_orders` read `could_not_look` for both IB accounts at 13:16Z. To settle: a clean `exchange_positions` read, counting symbols with idx1 and idx2 both non-zero.
3. **Bybit funding sign.** Compare stored `exchange_funding.amount` against `bybit_transaction_log` funding rows at the same timestamps.
4. **Whether the monitor's candle frame ends in a forming bar.** This is the premise of stale-stop-fires-intrabar. To settle: a live frame dump from the monitor.
5. **Whether `bar_close_at` gets the next minute's close.** Its kline `end` might be inclusive. To settle: one Bybit kline call from the VM; the API is geo-blocked from the sandbox.
