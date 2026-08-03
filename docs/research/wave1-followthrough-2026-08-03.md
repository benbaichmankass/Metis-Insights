# Wave 1 follow-through — A1 runner, GLD Track-B compat, R2 sweeps (2026-08-03)

**Plan:** [`WORK-PLAN-2026-08-02.md`](WORK-PLAN-2026-08-02.md) Wave-1 follow-through
(continues the `wave-1-sweeps-l2kiin` handoff). Tier-1 research tooling + evidence on
free GitHub runners, $0, no VM lane. **No cell is authored, revoked, or changed by this
document.** No `config/accounts.yaml` edit is made.

**Tooling shipped this session:** [PR #8423](https://github.com/benbaichmankass/Metis-Insights/pull/8423)
— the A1 backtest-augment runner (`scripts/ml/backtest_augment_runner.py`), the shared
`emit_trades_for` primitive (`scripts/research/regime_debt_matrix.py`), the two free-runner
workflows (`research-backtest-augment.yml`, `gld-compat-matrix.yml`), a test, two labels,
and capability-index rows.

---

## 1. R2 — ADX cut-point robustness on the other live-celled families

R2 (RESEARCH-PROGRAM) grades whether a live regime cell's verdict is robust to the two
un-swept global ADX attribution constants (live `CHOP_MAX_ADX=20` / `TREND_MIN_ADX=25`) or
flips under an equally-defensible alternative. Engine:
`scripts/research/regime_adx_cutpoint_sweep.py`. Following the gld_pullback_1h precedent
(#8413, robust 16/16), dispatched on the remaining live-celled families.

### 1a — `htf_pullback_trend_2h` trending (#8422) — **faithful**, robust, **over-gate flag**

BTCUSDT 2h, pullback harness, **faithful** fidelity, 159 emitted trades (730d), trending cell.

- **Cut-point robustness (the R2 question):** `short_stable_drag` 0/16 pairs, `long_stable_drag`
  0/16 pairs → **both verdicts robust** across the whole grid. The trending verdict is **not
  fragile** to the un-swept 20/25 constants.
- **Secondary observation — a possible OVER-GATE.** At the live 20/25 the trending bucket
  measures **short-R = +6.8847** (positive) and long-R = −4.2441, but `long_stable_drag=False`
  (long is not strict-majority-negative under the fold panel). So under
  [`regime-selectivity`](../../.claude/skills/regime-selectivity/SKILL.md) Rules 1–2 **neither
  side is a justified stable-drag OFF cell** in trending — yet the live cell gates **both**
  (`config/regime_policy.yaml` trending: `htf_pullback_trend_2h { long: off, short: off }`).
  Gating the short side removes a **+6.88R positive** contribution.

  This is a **flag, not a change.** The live cell's comment justifies only the LONG
  re-measurement (`was long:on … re-measured -6.85R`); the SHORT `off` has no drag justification
  in this faithful full-sample+fold-panel read. Per Rule 2, retiring the short gate requires a
  clean **walk-forward on the actual cell** (`regime_cell_walkforward.py`, `*_stable_drag` under
  `FOLD_PANEL=(3,4,5)`), on the population the router actually enforces (1-D trend vs the 2-D
  `trend_vol` cell) — **not a single full-sample R2 grade.** Filed for that re-audit:
  `BL-20260803-HTF2H-TRENDING-SHORT-OVERGATE` (Tier-3, do **not** enact here).

### 1b — `trend_donchian` trending (#8421) — **approximate → inconclusive**

BTCUSDT 1h, trend harness, **approximate** fidelity (omitted levers: `exit_head_action`,
`exit_head_model`, `exit_head_threshold`, `trail_decay_arm_r`, `trail_decay_tight_mult`),
127 emitted trades.

- Pooled short-R = **0** across every cut-point (no short trades land in the trending bucket),
  so the live `short: off` verdict has **no trades to grade** here.
- The long side reads negative (−2.1 … −4.1) but the row is **approximate**, and the capability
  index + `regime-selectivity` are explicit: **never author or retire a cell off an approximate
  row.** So the R2 cut-point read on `trend_donchian` is **inconclusive** — it inherits the
  known `BL-20260730-DONCHIAN-APPROX-ONLY` limitation (the trend harness can't model the
  exit-head + trail-decay levers this strategy carries). No cell action.

### 1c — `ict_scalp_5m` — **NOT dispatchable (R2-unauditable)**

`ict_scalp_5m` carries no donchian/pullback/squeeze param shape, so
`regime_debt_matrix.classify()` (reused by `regime_adx_cutpoint_sweep.py`) returns `None` and
the sweep errors `unclassifiable` before emitting a trade. It has **live, operator-approved
`trend_vol` OFF cells** (Phase-4 gate 2026-07-20) that therefore **cannot be cut-point- or
walk-forward-audited** by this tooling. Same class as the fixed `BL-20260730-SQUEEZE-NO-HARNESS`.
Filed `BL-20260803-ICTSCALP-NO-HARNESS-R2-UNAUDITABLE`; fix is wiring
`scripts/backtest_ict_scalp.py` into `classify()` / `build_harness_cmd()`.

---

## 2. GLD Track B (M36) — per-account compat gate (#8425) — **alpaca_portfolio SKIP → FLAG**

Operator pre-approved (2026-08-02) routing `gld_pullback_1h` onto `alpaca_portfolio` (the
paper-money portfolio mirror), **gated on the compat-matrix evidence**. The gate ran on a free
runner: emit the config-exact `gld_pullback_1h` harness on GLD (Yahoo candles) at the corrected
commission-free **0 bps**, then score against every account's ruleset
(`scripts/prop/account_compat_matrix.py --ledger`). **Faithful**, 123 emitted trades.

| account | class | verdict | end-return mean | P(breach) | survival |
|---|---|---|--:|--:|--:|
| **`alpaca_portfolio`** ⭐ | paper | **skip** | 32.7% | **0.132** | **0.871** |
| `alpaca_live` | real_money | **skip** | 32.7% | 0.132 | 0.871 |
| `alpaca_paper` · `bybit_1` · `bybit_2` · `ib_paper` · `ib_live` · `oanda_practice` · `bybit_portfolio` · `alpaca_options_paper` | mixed | ROUTE | 24.6% | 0.081 | 0.922 |
| `breakout_1` | prop | ROUTE | ev_net $1,293 | — | P(net>0)=0.960 |

**Verdict: `alpaca_portfolio` does NOT clear its own ruleset gate** (survival **0.871 < 0.90**
floor AND P(breach) **0.132 > 0.10** cap), even though the strategy is +EV (higher end-return
than the ROUTE accounts). The split is driven by `alpaca_portfolio`'s **2% risk_pct** against
its **5% max-DD / 5% daily-loss** caps — at that sizing gld_pullback_1h breaches the drawdown
caps too often; the ROUTE accounts sit at a lower per-trade risk and survive.

**This is a genuine flag, and it compounds a field-beats-comment finding:** `gld_pullback_1h`
is **already in `alpaca_portfolio`'s roster** (`config/accounts.yaml:787`, since the account's
2026-07-16 S-PAPER-PORTFOLIO creation — it mirrors `alpaca_live`). So the operator's
"route it onto alpaca_portfolio" was already in config, **and** the corrected-cost evidence now
says it **fails that account's survival gate at the account's own risk sizing** — a paper book
carrying a strategy its ruleset would reject.

**Disposition (operator's call, Tier-3 — no config edit made):**
1. **Keep** — accept it: paper money, +EV, and the gate failure is a *drawdown/survival* concern
   (survival 87%), not ruin; OR
2. **Reduce the routing's risk** — the ROUTE accounts pass at a lower per-trade risk, so a lower
   `risk_pct` (or a per-strategy risk override) for the gld leg on `alpaca_portfolio` would likely
   clear the survival gate; OR
3. **De-route** — drop `gld_pullback_1h` from `alpaca_portfolio`'s roster.

Filed `BL-20260803-GLD-ALPACA-PORTFOLIO-SURVIVAL-SKIP` to track the decision. The confirmed
`trending.gld_pullback_1h { short: off }` cell is unaffected (it applies globally, independent
of routing).

---

## 3. A1 (W1.2) — pooled crypto augment DB (#8424) — **fetch-dep bug found + fixed; re-dispatch pending**

First A1 run: **0 rows across 0/9 legs** — every `binance_vision` candle fetch exited non-zero.
**Root cause (deterministic, not transient):** `scripts/ops/fetch_backtest_candles.py` imports
`requests`; the A1 workflow installed `pyyaml pandas numpy` only — dropping `yfinance` (correctly,
A1 is crypto-only) **also dropped `requests`, which yfinance had supplied transitively** in the
sibling regime-debt-matrix / adx-cutpoint workflows. So every fetch failed `import requests`.
(Confirmed against the R2 #8421 crypto fetch, which succeeded 35 min earlier on the identical
`binance_vision` path *because* its workflow installed yfinance.)

**Fix:** `research-backtest-augment.yml` now installs `requests` explicitly (this PR). **Re-dispatch
A1 after this merges** (`research-backtest-augment-request` label). Once it produces the augmented
`backtest_trades.db`, **W2.1** (trainer FIFO lane, NOT this session) builds
`include_backtest=True` pooled datasets scoped to the run-tag, retrains `setup_candidates` /
`trade_outcomes`, and evaluates on a **live-only holdout** — that verdict is the A1 answer
(`MB-20260530-001`); a promotion is Tier-3.

---

## Follow-ups

| # | Item | Owner / tracking |
|---|---|---|
| 1 | Re-dispatch A1 after the `requests` fix merges → then W2.1 trainer build + live-holdout | this session (re-dispatch) → trainer FIFO lane (W2.1) |
| 2 | **Operator decision:** keep / reduce-risk / de-route `gld_pullback_1h` on `alpaca_portfolio` (fails survival gate at 2% risk) | `BL-20260803-GLD-ALPACA-PORTFOLIO-SURVIVAL-SKIP` (Tier-3) |
| 3 | Walk-forward re-audit of `htf_pullback_trend_2h` trending short (over-gate: +6.88R yet gated) | `BL-20260803-HTF2H-TRENDING-SHORT-OVERGATE` (Tier-3) |
| 4 | Wire `backtest_ict_scalp.py` into `classify()` so ict_scalp_5m's live cells become auditable | `BL-20260803-ICTSCALP-NO-HARNESS-R2-UNAUDITABLE` |
