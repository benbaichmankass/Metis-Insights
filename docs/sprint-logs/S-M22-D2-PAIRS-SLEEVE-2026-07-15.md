# Sprint Log: S-M22-D2-PAIRS-SLEEVE-2026-07-15

## Date Range

2026-07-15 (single session, continued from the M22 wave-2 research session).

## Objective

Build the full plumbing for the M22 D2 market-neutral cointegration-pairs
sleeve — the winner of the small-TF-directions research — and take all 4
validated pairs (SOL/BTC, BNB/BTC, ETH/BTC, SOL/ETH) live on paper (`bybit_1`)
so they start soaking, per operator direction ("build out the full plumbing…
to go live on paper now so that they can start soaking" → "yes, they should be
live on bybit 1").

## Tier

**Tier-3** (new live order path + `config/pairs.yaml` execution gate). Built
tested-cores-first, shipped inert (shadow) then operator-approved to `live`.
The doc-hygiene follow-up (this log + ARCHITECTURE-CANONICAL row + API table)
is Tier-1.

## Starting Context

The M22 wave-2 research (merged #6494/#6502/#6508/#6511) concluded that
market-neutral crypto cointegration **pairs at 1h** are the reliable small-TF
tool: all 4 pairs robustly net-positive, **fee-insensitive** (R normalized by
the wide spread-stop dwarfs fees), OOS-validated on held-out 2025-26 with no
expectancy decay, cointegration-stable (half-life ~12h, 100% valid windows),
funding-negligible. The Tier-3 proposal
(`docs/research/pairs-sleeve-PROPOSAL-2026-07-15.md`) flagged the architectural
lift: a pair is TWO simultaneous opposite legs → does not fit the single-symbol
intent model → needs a new isolated pairs-executor primitive.

## Repo State Checked

Branch `claude/scalping-chop-strategies-b9n05u` off `main`. Verified the merge
protocol (one other session's PR #6517 open on a different branch). Confirmed
`bybit_1` is `market_type: linear`, `account_class: paper`, `mode: live`
(Bybit demo venue — SL/TP attaches atomically).

## Files and Systems Inspected

Executor placement seams — `execute.py::execute_pkg` / `_submit_order` /
`_log_trade_to_journal` / `close_open_position`; `coordinator.py::OrderPackage`
+ `_log_new_order_package`; `positions.py::has_open_trade_for_strategy`;
`market_data.py::fetch_candles`; `clients.py::bybit_client_for` /
`account_open_positions`; `pipeline.py::monitor_unit_for`; the `src/main.py`
tick loop; `config/accounts.yaml` (bybit_1) + `config/instruments.yaml`; the
soak-router precedents (`allocator`/`exit_ladder`). Mapped via a recon Explore
agent + direct reads.

## Work Completed

- **Signal engine + sizing** (#6518, pre-merged this session): `pairs_engine.py`
  (spread/z, rolling hedge-β, entry/exit — parity-verified vs the backtest
  harness) + `pairs_sizing.py` (β-hedged notionals, per-leg catastrophe
  backstop, correlation haircut). Pure, exhaustively unit-tested.
- **Soak** (#6519): `src/runtime/pairs_soak.py` builder/writer/reader trio →
  `runtime_logs/pairs_soak.jsonl`; `src/web/api/routers/pairs.py` →
  `GET /api/bot/pairs/soak` (Tier-1 read-only, mounted in `main.py`).
- **Executor** (#6519): `src/units/strategies/pairs_executor.py` — pure
  `decide_pair(...)` decision core (9 branch tests) + the live I/O layer
  `run_pairs_tick(settings)`: per closed 1h bar per pair, fetch both legs'
  candles → dedup to one decision per bar → reconstruct open-state from
  journal-durable `order_packages.meta` → decide → for a `live` pair place
  BOTH legs via `_log_new_order_package` + `execute_pkg` (reused placement +
  atomic SL/TP + journal-write), linked by `meta.pairs_group_id`, with a
  leg-imbalance unwind if the 2nd leg fails; close flattens both legs.
  `monitor()` returns None (the executor owns the joint spread-exit).
- **Wiring** (#6519): `src/main.py` once-per-tick hook after `run_monitor_tick`;
  `pipeline.monitor_unit_for` resolves the `pairs_` prefix → `pairs_executor`
  (no-op monitor); `config/pairs.yaml` (4 pairs) + `config/instruments.yaml`
  BNBUSDT entry.
- **Go-live** (#6521): flipped all 4 pairs `execution: shadow → live` on
  `bybit_1`, operator-approved. Foundation shipped at `shadow` first (safe
  default, dry-run-guard allow-markered) then flipped.
- **Docs** (this PR): ARCHITECTURE-CANONICAL change-log row, the
  `/api/bot/pairs/soak` API-table entry + the `pairs_soak` diag `log_file`
  wiring (relay-reachable), ROADMAP M22 update, this sprint log.

## Validation Performed

- Full pairs suite green: `test_pairs_{engine,sizing,soak,executor}.py` —
  **32 passed, 1 skipped** (skip = router import when fastapi absent).
- Executor live-layer tests: config plumbing, open-state reconstruction from
  `order_packages.meta`, per-bar decision dedup, **shadow-places-nothing
  end-to-end** (asserts no exchange client is ever built in shadow mode).
- CI guards green on both PRs: `ruff check .`, `dry-run-guard` (allow-marker on
  the shadow foundation; clean on the live flip since `live` is not a
  demotion), `env-gate-guard`, `canonical-config-loaders`,
  `monitor_unit_for` resolution drift guard, `pytest-run`.
- **Live placement verification (bybit_1) is post-deploy** — pull
  `/api/bot/pairs/soak` (or the `pairs_soak` diag `log_file`) once
  `ict-git-sync` auto-deploys to confirm `open` events + paired leg trades
  (linked by `pairs_group_id`, `account_class: paper`). Tracked as the
  session-close follow-up.

## Documentation Updated

`docs/ARCHITECTURE-CANONICAL.md` (change-log row), `CLAUDE.md` (API table +
diag `log_file` enum), `ROADMAP.md` (M22), this sprint log. The design +
proposal docs (`docs/research/small-tf-directions-2026-07-15.md`,
`pairs-sleeve-PROPOSAL-2026-07-15.md`) were merged in the research session.

## Contradictions or Drift Found

The ROADMAP M22 status still read "executors/sleeve wiring stay operator-gated
Tier-3" (proposed-only) — updated to reflect the sleeve is now BUILT + LIVE on
paper (field-beats-comment).

## Risks and Follow-Ups

- **First live run against the real Bybit demo venue** — the executor's
  placement path was unit-tested with mocks but never exercised against live
  Bybit. Watch the first fills; a leg-placement error triggers the
  leg-imbalance unwind (best-effort) and the per-leg backstop SL/TP is the net.
  One-line rollback: set a pair back to `execution: shadow`.
- **Real-money (`bybit_2`) promotion** is a separate future Tier-3 step after
  the paper soak confirms live==backtest; mandatory `account_compat_matrix`
  first.
- Per-tick cost: 8 Bybit REST candle fetches/tick (2 legs × 4 pairs); cheap on
  Bybit (no IB pacing), acceptable for the paper soak.

## Deferred Items

Real-money promotion; D3 (passive LP, gated on P2 order-flow accrual); D4 (new
signal inputs); P4 (P(win) entry filter, gated on a genuinely new input).

## Next Recommended Sprint

Verify the live paper soak (first placements match a manual recompute), then —
once the soak shows the sleeve trades cleanly — draft the real-money (`bybit_2`)
promotion Tier-3 with the `account_compat_matrix` evidence.

## Wrap-Up Check

Ran `doc-freshness` at session end (no canonical doc contradicts the change).
Both build PRs (#6519 foundation, #6521 live flip) merged operator-approved;
this doc PR is Tier-1 self-merge.
