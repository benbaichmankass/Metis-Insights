# Recurring Strategy Improvement Session Prompt

**Type**: Recurring (weekly)
**Cap**: 4 hours
**Spec**: `docs/claude/recurring-sessions.md`
**Format**: Phase 1 (E2E) → Phase 2 (Strategy review) → Phase 3 (Summary ping)

This file is loaded at the start of every recurring strategy improvement session. Read CLAUDE.md first, paying special attention to the **autonomous live-trading rule** (no per-trade gates) and the **live-mode invariant** (Tier 3 governs strategy params).

---

## Critical Rule

**This session NEVER changes strategy parameters in `config/strategies.yaml` or `src/units/strategies/*.py`.** It only **proposes** changes. Parameter changes are Tier 3 — they require a focused sprint with operator review per CLAUDE.md.

If at any point you find yourself about to edit a strategy file or `strategies.yaml`, stop. File a proposal in `docs/strategy-reviews/` and ping the operator.

---

## Phase 1 — E2E Health Check

Run the standard health check from `recurring-hardening-prompt.md` Phase 1, plus strategy-specific checks:

### 1A. Per-strategy signal volume
For each strategy in `config/strategies.yaml` with `enabled: true`:
- Last 24h signal count (from `runtime_logs/signal_audit.jsonl` or hourly reports).
- **Red flag**: zero signals in 24h for a strategy that has historically averaged > 5/day.

### 1B. Per-strategy fill rate
- Fill rate = orders placed / signals fired in last 7 days.
- **Red flag**: fill rate < 50% with no documented reason (RiskManager refusal, mode toggle, manual halt).

### 1C. Per-strategy intraday drawdown
- Max DD per strategy in last 7 days.
- **Red flag**: any strategy showing > `risk.max_dd_pct` drawdown that wasn't caught by RiskManager — that means a risk-engine bug, not a strategy-tuning question.

### 1D. Backtest freshness
- Most recent backtest result per strategy in `outputs/` or HF dataset.
- **Red flag**: > 30 days since last backtest run for any enabled strategy.

If any check fails, follow the same outcome routing as the hardening prompt: pivot, defer, or proceed only with operator approval.

---

## Phase 2 — Strategy Review (per active strategy)

### 2A. Pull live performance data
For each enabled strategy, gather:
- Last 7 days of signals (with timestamps, side, symbol, entry, SL, TP, outcome)
- Last 7 days of orders (placed, filled, rejected, with reasons)
- Last 7 days of P&L per strategy

Source: `runtime_logs/`, `trade_journal.db`, hourly report archive.

### 2B. Pull backtest expectations
For each strategy, find the most recent backtest result:
- Win rate (backtest vs live last 7d)
- Avg R-multiple (backtest vs live)
- Max DD (backtest vs live)
- Trade frequency (backtest vs live)

### 2C. Drift analysis
For each strategy, compute:
- `live_vs_backtest_winrate_delta` = live winrate - backtest winrate
- `live_vs_backtest_freq_delta` = live freq - backtest freq
- Document and flag deltas exceeding ±20%.

### 2D. Hypothesize causes for drift
For each significant drift:
- Market regime shift? (check macro indicators if available)
- Parameter no longer optimal? (timeframe, threshold, filter)
- Execution issue? (slippage, fill rate, timing)

### 2E. Propose adjustments

For each strategy, write a proposal at `docs/strategy-reviews/<strategy>-YYYYMMDD.md`:

```markdown
# Strategy Review — <strategy_name> — YYYY-MM-DD

## Live performance (last 7 days)
- Signals: N | Orders: M | Fill rate: X%
- Win rate: X% | Avg R: Y | Max DD: Z%

## Backtest expectations
- (latest backtest date)
- Win rate: X% | Avg R: Y | Max DD: Z%

## Drift analysis
- Win rate delta: X% (significant / not)
- Frequency delta: X% (significant / not)
- Hypothesized cause: ...

## Proposed adjustments
1. <param>: <current> → <proposed> (rationale: ...)
2. ...

## Next steps
- [ ] Operator approves proposal
- [ ] /test <strategy> with proposed params
- [ ] If staging passes, file Tier 3 sprint with full PM review
```

### 2F. Triggering a backtest
If a proposal needs supporting data (e.g., "what would win rate be if we raised the volume filter to 1.5x?"), queue a Colab backtest per `docs/claude/colab-workflows.md`. Do not run training/backtest in this session — offload.

---

## Phase 3 — Summary Ping

```
📈 Strategy Review — YYYY-MM-DD

Strategies reviewed: N
Drift detected: <list>
Proposals queued: <links to docs/strategy-reviews/...>
Backtest jobs queued: <list>
Next review: YYYY-MM-DD
Time: <total>
```

Append checkpoint per CLAUDE.md.

---

## What this session is NOT for

- Implementing new strategies (that's a feature sprint).
- Fixing strategy bugs (that's a hardening session).
- Promoting strategies dry-run → live (that's a Tier 3 sprint with PM review).
- Tuning risk caps (that's Tier 3 too).

---

## Reference

- Master spec: `docs/claude/recurring-sessions.md`
- Live-mode invariant: `CLAUDE.md`
- Backtest workflow: `docs/claude/colab-workflows.md`
- Hardening prompt: `docs/sprints/recurring-hardening-prompt.md`
