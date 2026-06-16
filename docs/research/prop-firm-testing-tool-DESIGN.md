# Prop-firm strategy evaluation tool — DESIGN (2026-06-16)

> **Tier-1 research/backtest tooling.** Nothing in this design touches the live
> order path, `config/strategies.yaml`, `config/accounts.yaml`, or any unit the
> live VM consumes. The tool reads historical candles + replays strategies
> offline and reports pass/fail against a prop-firm ruleset. It never trades.
>
> Status: **DESIGN — for operator review before build** (per operator decision
> 2026-06-16). Build kicks off as a v1 evaluator on top of the existing
> `scripts/backtest_system.py` portfolio engine once this is approved.
>
> Target account (operator decision 2026-06-16): **Breakout "1-Step Classic"** —
> one-phase eval, 10% target / 3% daily loss / 6% static drawdown (§4).

## 1. Why this exists

The operator is pursuing a **Breakout prop-firm account** (funded crypto
trading). Prop firms don't pay out on raw edge — they pay out on edge that
**survives their rule set**: profit target, max drawdown, daily-loss limit, a
minimum number of trading days, and (the usual silent account-killer) a
**consistency rule** that caps how much of total profit any single day may
contribute. A strategy that is net-profitable standalone can still **fail the
evaluation or blow a funded account** by breaching one of these constraints.

This tool answers a question none of the existing harnesses answer:

> **Which strategy — or combination of strategies — would pass a Breakout
> evaluation and then survive a funded period without tripping a rule that
> closes the account?**

It is worth building **independent of whether we ever wire the live Breakout
integration** (see § 8): it is a portfolio-quality question that needs no
external dependency, no broker credentials, and no monthly bridge fee to run.

## 2. What already exists (reuse, don't rebuild)

The substrate is already in the repo. `scripts/backtest_system.py` is the
**system/portfolio backtester** (operator directive 2026-05-30):

- Replays **all roster strategies together** over one price history, routing
  their signals through the **real** `src/runtime/intents.py::aggregate_intents`
  netting (same-side → max target_qty, opposite-side → higher-priority wins).
- Manages **one shared account**: finite `--initial-balance`, per-trade risk
  sizing (`--risk-pct`), a `--daily-loss-pct` halt, fees, fills at next-bar
  open.
- Uses each strategy's **real** `order_package()` + `monitor()` — the exact
  functions the live trader calls.
- Already emits, per run (`_summarize`): `net_pnl`, `return_pct`,
  `max_drawdown_usd`, `max_drawdown_pct`, `return_dd_ratio`, `total_trades`,
  `win_rate_pct`, `capital_utilization_pct`, `by_exit_reason`,
  `per_strategy_attribution`, and an `equity_curve` (computed in full; only the
  tail is serialized in the summary today).
- Already takes `--roster <csv>` — so **combination selection is free**: any
  subset of strategies is just a roster string.

`PropRiskManager` (`src/units/accounts/prop_risk.py`) + `prop_state_io.py`
already model the *per-trade gate* side of prop rules (profit-target/min-days
mission skip, daily-loss cap, intraday drawdown, position-size cap, overnight +
weekend restrictions). These survived the velotrade purge (PR #3680) and are
reused as the **in-sim gate** so the evaluation reflects what the bot *would
actually have refused* live.

**What is missing** is a layer that takes the portfolio engine's output and
judges it against a **full prop ruleset over time** — specifically trailing
max-drawdown and the consistency rule, neither of which is modeled anywhere
today. That layer is this tool.

## 3. Architecture

```
config/prop_rulesets/breakout.yaml      # the ruleset (configurable, see §4)
            │
            ▼
scripts/prop/evaluate_prop.py           # NEW — the evaluator + combo search
   ├── imports the portfolio engine from scripts/backtest_system.py
   │     (run it in-process → get FULL equity_curve + closed-trade ledger,
   │      not just the 5-row tail; backtest_system.py itself untouched)
   ├── src/prop/ruleset.py              # NEW — load + validate a ruleset YAML
   ├── src/prop/evaluator.py            # NEW — replay equity/trades vs ruleset
   └── src/prop/report.py               # NEW — pass/fail matrix + breach trace
            │
            ▼
runtime_logs/prop_eval/<UTC-date>/      # JSON + Markdown matrix output
```

Design choices:

- **Import the engine in-process, don't shell out.** `_summarize` only
  serializes `equity_curve_tail` (last 5 points); the evaluator needs the
  **full equity curve + the full per-trade ledger** (each trade's pnl, owner
  strategy, open/close timestamps) to compute daily buckets, trailing
  drawdown, and per-day profit share. Cleanest path: call the portfolio engine
  function directly and consume its in-memory `closed` list + `equity_curve`.
  `backtest_system.py` stays a Tier-1 reuse with **no edits** (if a small
  refactor is needed to expose the engine function cleanly, it is additive and
  called out in the build PR).
- **New code lives under `src/prop/` + `scripts/prop/`** — a self-contained
  unit, no imports into live-path modules beyond reading the engine output and
  reusing `PropRiskManager`.
- **Ruleset is data, not code** — adding a second prop firm later (or updating
  Breakout's numbers) is a YAML edit, never a code change.

## 4. The ruleset schema (`config/prop_rulesets/breakout.yaml`)

**Target account (operator decision 2026-06-16): Breakout "1-Step Classic"** —
$45 one-time, one-phase evaluation, 80/20 profit split (upgradeable to 90/10 at
$54). The headline rules are **confirmed from the firm's own plan card**
(screenshot, 2026-06-16); two fields the card doesn't show remain
**UNCONFIRMED** and are flagged inline.

Confirmed from the plan card:

| Rule | Value | Source |
|---|---|---|
| Profit target | **10%** | card |
| Max daily loss | **3%** | card |
| Max drawdown | **6%, STATIC** | card (note: *static*, off the starting balance — not trailing) |
| Phases | **1** (one-phase eval) | card |

Still unconfirmed (not on the card — operator to verify on Breakout's full
rules page): **min trading days** and whether a **consistency rule** exists.

```yaml
# config/prop_rulesets/breakout.yaml
# Breakout "1-Step Classic" — $45, one-phase eval, 80/20 split.
# Headline limits CONFIRMED from the plan card (2026-06-16).
# Fields tagged [UNCONFIRMED] are not on the card — verify before trusting.
ruleset: breakout
plan: 1-step-classic
account_size_usd: 25000           # set to the actual account size purchased
profit_split: 0.80               # 80/20 (90/10 upgrade = 0.90)
unconfirmed: true                 # loud banner stays until the two fields below are verified

phases:
  evaluation:
    profit_target_pct: 0.10       # +10% to clear            [CONFIRMED]
    min_trading_days: 0           # not shown on card        [UNCONFIRMED — verify]
    max_eval_days: null           # 1-Step Classic appears time-unlimited [UNCONFIRMED — verify]
  funded:                         # one-phase: same limits continue post-pass
    profit_target_pct: null
    min_trading_days: 0

# Account-killers — breaching ANY = instant fail (eval and funded).
limits:
  daily_loss_pct: 0.03            # max loss in one day      [CONFIRMED]
  max_drawdown_pct: 0.06          # overall drawdown limit   [CONFIRMED]
  drawdown_type: static           # STATIC off starting balance, NOT trailing [CONFIRMED]
  max_position_pct: null          # per-position cap, if any [UNCONFIRMED — verify]

# Consistency rule — the silent algo-account killer. Presence not on the card.
consistency:
  enabled: false                  # flip to true if Breakout has one [UNCONFIRMED — verify]
  max_single_day_profit_share: 0.40  # placeholder threshold if enabled [UNCONFIRMED]

# Time restrictions (map onto PropRiskManager's existing gates).
restrictions:
  weekend_flat: false             # crypto trades weekends   [UNCONFIRMED — verify]
  overnight_flat: false           # 24/7 crypto              [UNCONFIRMED — verify]

# Funded-phase survival horizon.
funded_soak_days: 30
```

The **static 6% drawdown** is the dominant constraint for our roster: it is an
absolute floor at 94% of starting balance that **never ratchets up with
profit**, so an early loss is far more dangerous than under a trailing rule.
Combined with the **3% daily loss** and a **10%** target, the evaluator's job is
to find the combo that reaches +10% without the equity *ever* touching −6% from
start or −3% in a day. The two open fields (min days, consistency) only make the
pass *harder*, never easier, so a combo that fails with them off is already out.

## 5. The evaluator (`src/prop/evaluator.py`)

Input: a ruleset + a portfolio run's **full equity curve** (timestamped) and
**closed-trade ledger**. Output: a structured verdict.

Checks, evaluated in time order so the **first breach wins** and is reported
with its timestamp:

1. **Daily-loss breach** — bucket the equity curve by UTC day; if any day's
   drawdown from that day's start exceeds `limits.daily_loss_pct` → FAIL
   (`daily_loss`, with the day + amount).
2. **Max-drawdown breach** — running peak (trailing) or account-start (static)
   per `drawdown_type`; if equity falls more than `max_drawdown_pct` below the
   reference → FAIL (`max_drawdown`, with timestamp + depth).
3. **Position-size breach** — if any trade's notional exceeded
   `max_position_pct` of equity at entry → FAIL (`position_size`).
4. **Profit target (eval)** — did cumulative return reach
   `phases.evaluation.profit_target_pct` **before** `max_eval_days` AND with
   `>= min_trading_days` active days? If not → did-not-pass (not a "breach",
   just "eval not cleared in window").
5. **Consistency** — once the target is hit, compute each day's realized profit
   as a share of total profit; if any single day > `max_single_day_profit_share`
   → FAIL (`consistency`, with the offending day + its share). This is the check
   that fails strategies that make their whole month in one lucky candle.
6. **Funded soak** — re-run checks 1–3 + 5 over a `funded_soak_days` horizon
   (continuing the same price history past the eval-pass point) to answer "would
   it then survive funded, not just pass the eval."

Verdict shape (one per roster combo):

```json
{
  "ruleset": "breakout",
  "unconfirmed": true,
  "roster": "trend_donchian,htf_pullback_trend_2h",
  "eval": {
    "passed": true,
    "days_to_target": 11,
    "active_trading_days": 7,
    "first_breach": null
  },
  "funded_soak": {
    "survived": false,
    "first_breach": {"rule": "max_drawdown", "ts": "...", "detail": "trailing DD 8.3% > 8.0%"}
  },
  "metrics": {                       // straight from backtest_system._summarize
    "net_pnl": 2140.0, "return_pct": 8.56,
    "max_drawdown_pct": 8.3, "consistency_worst_day_share": 0.52,
    "total_trades": 41, "win_rate_pct": 58.5
  },
  "headline": "EVAL PASS / FUNDED FAIL (trailing DD)"
}
```

## 6. The combination search (`scripts/prop/evaluate_prop.py`)

The operator specifically wants **combinations**, not just single strategies.

- **Candidate generation.** Start from the cleanly-backtestable BTCUSDT roster
  the portfolio engine already supports (`trend_donchian`, `fade_breakout_4h`,
  `squeeze_breakout_4h`, `fvg_range_15m`; `ict_scalp_5m` + `turtle_soup`
  deferred per the engine's documented coverage). Enumerate all non-empty
  subsets (15 combos for 4 strategies; bounded and cheap with the signal cache).
- **Per-combo run.** For each subset: run the portfolio engine once (signals are
  cached under `runtime_logs/system_backtest/signals/`, so re-running combos is
  fast), feed the full output to the evaluator, collect the verdict.
- **Ranking.** Sort by a prop-appropriate objective, **not** raw R:
  primary = "passes eval AND survives funded soak", then by funded-soak
  drawdown margin, then days-to-target, then consistency margin. A combo that
  makes less money but never breaches outranks a higher-R combo that blows up.
- **Output.** A Markdown matrix + JSON under
  `runtime_logs/prop_eval/<UTC-date>/`: one row per combo →
  `{eval pass?, days-to-target, worst-DD vs limit, consistency margin,
  survives-funded?, net $}`, with the **first breach** annotated for every
  failure so it's obvious *what* would have closed the account.

CLI sketch (mirrors `backtest_system.py` conventions):

```
python scripts/prop/evaluate_prop.py \
  --ruleset config/prop_rulesets/breakout.yaml \
  --data data/backtest_candles.csv --start 2021-01-01 --end 2026-06-01 \
  --combos all            # or an explicit csv of rosters
  --json runtime_logs/prop_eval/2026-06-16/matrix.json
```

## 7. Build plan (v1, after this design is approved)

All Tier-1. No live-path edits.

1. `src/prop/ruleset.py` — load + validate the YAML, defaults, `unconfirmed`
   banner. + tests.
2. `src/prop/evaluator.py` — the six checks over equity curve + ledger. + tests
   with synthetic curves (a curve that breaches each rule exactly once).
3. `src/prop/report.py` — verdict → Markdown/JSON matrix. + tests.
4. `scripts/prop/evaluate_prop.py` — in-process engine call, combo search,
   ranking, output. (If exposing the engine function needs a tiny additive
   refactor of `backtest_system.py`, it's called out explicitly in the PR.)
5. `config/prop_rulesets/breakout.yaml` — the placeholder ruleset (§4).
6. Run the current roster, commit the first matrix under
   `runtime_logs/prop_eval/` as the baseline (loudly flagged UNCONFIRMED).

Estimated: ~one focused sprint. The genuinely new logic is the trailing-DD +
consistency-rule math and the combo ranking; price replay, netting, sizing, and
equity/DD accounting are already done by the portfolio engine.

## 8. Relationship to the live Breakout integration (separate track)

This tool is **decoupled** from wiring the live account. The live integration
(operator-led) is its own question — re-confirming Breakout's platform
(**DXtrade** was the platform discussed; see
`docs/integrations/dxtrade-contract-template.md`, the empty contract drop-zone),
whether API/algo access is direct or bridge-only (the "separate server, billed
monthly" path), and whether algo trading is even permitted under their ToS. The
velotrade/DXtrade *client* was purged in PR #3680 but is recoverable from git
history; the generic `PropRiskManager` was kept. None of that blocks this
evaluation tool, which runs entirely offline.

## 9. Honesty / limits

- **The default ruleset numbers are placeholders, not Breakout's terms.** Every
  report carries an `unconfirmed: true` banner until the operator swaps in
  verified values. A pass against placeholder numbers proves nothing about the
  real eval.
- **Backtest ≠ funded reality.** Slippage, funding, fills, and Breakout's exact
  equity-accounting (mark-to-market vs realized for the DD calc) will differ.
  The tool ranks *relative* combo robustness and flags *obvious* rule breaches;
  it is a filter, not a guarantee.
- **Coverage matches the portfolio engine** — BTCUSDT, the four
  cleanly-resamplable members first; `ict_scalp_5m` / `turtle_soup` come in when
  their signal-stream generators are registered, same as the engine's roadmap.
```
