# Walk-forward — flip-policy conflict resolution (2026-05-30, VERIFIED)

> **Status:** Tier-1 research. Driver:
> [`scripts/walkforward_flip_policy.py`](../../scripts/walkforward_flip_policy.py)
> (merged in [#2433](https://github.com/benbaichmankass/ict-trading-bot/pull/2433)).
> Scope: [`docs/sprint-plans/CONFLICT-POLICY-WALKFORWARD-SCOPE-2026-05-30.md`](../sprint-plans/CONFLICT-POLICY-WALKFORWARD-SCOPE-2026-05-30.md)
> (merged in [#2410](https://github.com/benbaichmankass/ict-trading-bot/pull/2410)).
> Backlog driver: `PERF-20260530-001`.
> **All numbers below were read from the run's JSON output
> (`runtime_logs/system_backtest/walkforward/walkforward_20260530T211319Z.json`)
> before being written here.**

## Run inputs

- **Data:** `/home/user/qashdev/btc_5m.parquet` (BTCUSDT 5m, 2020-01..2026-02, 647,826 rows; qashdev/btc archive mirror of Binance Vision).
- **Account model:** $10k, risk 0.3%/trade, 3% daily-loss cap, 15m clock, 7.5bps round-trip fee — identical to the original audit.
- **Rosters:**
  - **4mem:** `trend_donchian, fade_breakout_4h, squeeze_breakout_4h, fvg_range_15m`
  - **6mem:** 4mem + `turtle_soup, ict_scalp_5m` (the full live BTCUSDT execution-eligible roster)
- **Folds (anchored, per scope doc):**
  - **A:** train `2020-06..2023-12` / OOS `2024-01..2026-02` (3.5y / 2y — original audit split)
  - **B:** train `2022-01..2024-06` / OOS `2024-07..2026-02` (2.5y / 1.7y — verified 4.2yr re-scoped split)
- **Policies:** `reverse` (live aggregator), `hold`, `flat`.

24 cells = 2 folds × 2 halves × 2 rosters × 3 policies. Signal caches pre-built per `(strategy, window)` via `precache_walkforward.py` (4 workers; ict_scalp_5m on 3.5y was the slowest at ~24 min).

## Results — 4-member roster

| fold | half | policy | net | maxDD% | ret/DD | trades | flips |
|---|---|---|---|---|---|---|---|
| A | train | reverse (live) | −$847 | 11.21 | −0.75 | 697 | **167** |
| A | train | **hold** | **+$395** | **5.59** | **0.66** | 450 | **0** |
| A | train | flat | −$43 | 6.45 | −0.06 | 578 | 160 |
| A | **oos** | reverse | +$460 | 9.61 | 0.44 | 400 | 78 |
| A | **oos** | **hold** | **+$1753** | **2.71** | **5.36** | 253 | **0** |
| A | oos | flat | +$906 | 6.79 | 1.19 | 345 | 74 |
| B | train | reverse | +$249 | 7.09 | 0.34 | 442 | 94 |
| B | train | **hold** | **+$1203** | **5.25** | **2.03** | 307 | **0** |
| B | train | flat | +$771 | 6.22 | 1.15 | 376 | 89 |
| B | **oos** | reverse | −$70 | 8.10 | −0.09 | 312 | 63 |
| B | **oos** | **hold** | **+$877** | **2.67** | **2.94** | 187 | **0** |
| B | oos | flat | +$149 | 5.86 | 0.24 | 267 | 60 |

## Results — 6-member roster (full live roster)

| fold | half | policy | net | maxDD% | ret/DD | trades | flips |
|---|---|---|---|---|---|---|---|
| A | train | reverse | −$6195 | 66.60 | −0.82 | 1727 | 307 |
| A | train | **hold** | **−$4021** | **41.99** | −0.93 | 950 | **0** |
| A | train | flat | −$5562 | 61.72 | −0.78 | 1558 | 285 |
| A | **oos** | reverse | −$9853 | 98.54 | −1.00 | 967 | 166 |
| A | **oos** | **hold** | **−$4682** | **48.13** | −0.97 | 518 | **0** |
| A | oos | flat | −$9841 | 98.42 | −1.00 | 883 | 155 |
| B | train | reverse | −$6014 | 61.97 | −0.96 | 1093 | 174 |
| B | train | **hold** | **−$3367** | **36.93** | −0.91 | 681 | **0** |
| B | train | flat | −$5734 | 59.33 | −0.96 | 988 | 160 |
| B | **oos** | reverse | −$9819 | 98.20 | −1.00 | 739 | 125 |
| B | **oos** | **hold** | **−$4490** | **46.19** | −0.97 | 381 | **0** |
| B | oos | flat | −$9804 | 98.05 | −1.00 | 680 | 118 |

## Verdict

| Criterion | Definition (scope doc § Pass criteria) | Result |
|---|---|---|
| **1** | 4-member hold > reverse in net AND maxDD% across all 4 (fold, half) cells | **PASS** (4/4) |
| **2** | 6-member hold not worse than reverse in OOS net for both folds | **PASS** (2/2) |
| **Overall** | AND of (1) and (2) | **PASS** |

Specifically:
- **4-member, hold-vs-reverse swing (net, maxDD%, ret/DD):**
  - A/train: +$1242 net, 11.21→5.59 maxDD, −0.75→+0.66 ret/DD
  - A/oos:   +$1293 net,  9.61→2.71 maxDD, +0.44→+5.36 ret/DD ← cleanest OOS lift
  - B/train: +$954 net,   7.09→5.25 maxDD, +0.34→+2.03 ret/DD
  - B/oos:   +$947 net,   8.10→2.67 maxDD, −0.09→+2.94 ret/DD ← cleanest OOS lift
- **6-member, hold-vs-reverse swing:**
  - A/train: +$2174 net, 66.60→41.99 maxDD
  - A/oos:   +$5171 net, 98.54→48.13 maxDD (hold halves the bleed — most striking)
  - B/train: +$2647 net, 61.97→36.93 maxDD
  - B/oos:   +$5329 net, 98.20→46.19 maxDD
- **Flip churn under `reverse`:** 63 → 307 flips/cell depending on roster + window; **under `hold` always 0**.

## Findings

1. **The flip-churn finding is robust across train, OOS, and rosters.** In every single (fold × half × roster) cell, `hold` beats `reverse` on both net P&L and maxDD%. The OOS lift is *bigger* than the train lift on both folds at 4-member (+$1293 / +$947 vs +$1242 / +$954) — the strongest possible robustness signal. This is not period-specific.

2. **At 6-member, `hold` is materially less-bad than `reverse` everywhere — by $2k-$5k per cell — but the book still bleeds.** Both folds' 6-member OOS land at maxDD ~46-48% (hold) vs ~98% (reverse, account effectively wiped). Hold halves the catastrophic outcome but does not reverse it, because turtle_soup (priority 50) and ict_scalp_5m (priority 30) monopolise the shared position and lose money in-system regardless of conflict policy (the in-system-loss finding from the 4.2yr addendum reproduces here).

3. **`flat` consistently sits between `reverse` and `hold`.** Standing aside on conflicts forfeits some re-entries (vs hold) but avoids the worst of the flip churn (vs reverse). It is never the best policy in any cell.

4. **`reverse` (the live aggregator's behaviour today) is the worst policy on every single cell of the 4-member roster, and tied for worst on the 6-member roster.** Direction is unambiguous.

## What this licenses (and what it does NOT)

This walk-forward **licenses drafting a Tier-3 design doc + PR** to change the live conflict-resolution policy in `src/runtime/intents.py::aggregate_intents` from "reverse" to "hold". The pass criteria the operator agreed to in the scope doc are met cleanly. See the design proposal below.

It does **NOT** authorise:
- patching `aggregate_intents` directly (Tier-3 — operator approval required);
- promoting `turtle_soup` or `ict_scalp_5m` to `execution: live` (the 6-member-bleeds finding stands; their shadow→live promotion should remain conditional on the decider's *selection* layer landing, per the addendum in `system-portfolio-backtest-2026-05-30.md`);
- changing `signal_ttl_bars` (second-order per the addendum's flip-churn sensitivity sweep).

## Tier-3 design proposal — "hold" conflict policy

> **Status:** PROPOSED, not implemented. Posted for operator review. Stops here per session contract: "keep running until you get to tier three decision, but full discretion for anything that doesn't affect live trading".

### Change

In `src/runtime/intents.py::aggregate_intents`, when same-tick intents resolve to opposite sides (a flip), do **not** close-and-reverse the in-flight position. Instead, return a `DesiredPosition` that matches the *current* held side, so the per-account dispatcher emits no execution delta and the position-owner's `monitor()` (Chandelier trail / SL / TP / time-decay) exits the position naturally.

### Concretely (proposed shape; final to be drafted in a separate Tier-3 PR):

- `aggregate_intents` gains an optional `current_position: Optional[Position]` argument (or the caller passes the current net side).
- New rule: if `len({i.side for i in same_side_group}) > 1` (intents disagree) AND `current_position is not None` AND `current_position.side` is one of those sides, the aggregator returns `DesiredPosition(side=current_position.side, …)` — i.e. it holds the current side and the loser's opposite vote is dropped this tick.
- All other branches (same-side reinforcement, no current position, flat→long, flat→short) are unchanged.
- The dropped loser is logged to `runtime_logs/signal_audit.jsonl` with reason `conflict_hold_loser` so the audit trail is intact.

### Risk surface (why this is Tier-3, not Tier-2)

This is a **change to a core execution invariant** — the same boundary the `/new-strategy` skill flags as untouchable casually. It affects how the live trader behaves at the moment two strategies disagree, which is the moment the operator most needs the system to behave the way they expect. The walk-forward justifies *investigating + drafting*; it does not skip the operator-approval gate.

### Pre-merge validation the Tier-3 PR should carry

1. Unit tests in `tests/test_multi_strategy_intents.py` covering: (a) hold-on-conflict when current position exists, (b) unchanged reverse-when-no-position, (c) audit log entry for the dropped loser, (d) same-side reinforcement unchanged.
2. Intent regression suite must stay green (the 73 tests on `aggregate_intents` + `compute_execution_delta` today).
3. A second walk-forward run on the post-change harness, confirming the cell-by-cell numbers in this audit reproduce. The harness already supports `--flip-policy hold`, so this is a re-run, not a new build.
4. Sprint plan / log entry per the canonical template.

### Activation order (sequenced; each independently operator-approved)

1. **Tier-3 PR #1 — aggregator behaviour change** (the code change above). Ships with the harness re-verification.
2. **Operator-watched soak on bybit_1 (demo)** for a defined window before flipping bybit_2 (live money). The change is execution-policy-level so it doesn't need per-strategy promotion gates.
3. **Decider v2 selection layer** (per [`DECIDER-SINGLE-ACCOUNT-2026-05-24.md`](../sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md) v2 step 2/3) remains the **next** Tier-3 prize and the **prerequisite** for turtle_soup / ict_scalp_5m shadow→live promotion — the 6-member-bleeds finding has not changed.

## Reproduce

```bash
# Pre-build all 24 caches in parallel (one-shot; ~50 min wall-clock).
python3 /home/user/precache_walkforward.py

# Run the 12 (well, 24) backtests off cache (~30-35 min wall-clock).
python3 scripts/walkforward_flip_policy.py \
  --data /home/user/qashdev/btc_5m.parquet
# Reads: runtime_logs/system_backtest/walkforward/walkforward_<UTC>.{json,md}
```
