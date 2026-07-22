# M27 P1 — 15m scalp promotion PROPOSAL (Tier-3, operator-gated) — 2026-07-22

> **Status: PROPOSED — awaiting operator approval. NOT self-wired.** This
> documents the exact `config/strategies.yaml` cells I would add for the three
> passing M27 P1 15m legs, the evidence, and the caveats. No config change is
> made until the operator approves. Evidence: `M27-P1-15m-findings-2026-07-22.md`.

## The three candidates

M27 P1 (15m timeframe sweep, config-exact, net of 7.5 bps, anchored 4-fold OOS)
cleared the gate ("net expectancy > 0 with ≥ 3/4 folds positive") for three
crypto legs on the **ungated baseline** — the same evidence bar on which the 5m
`ict_scalp_sol_5m` / `ict_scalp_avax_5m` legs were shipped:

| Leg | baseline folds+ | baseline TotR (ExpR) | notes |
|---|---|---|---|
| **XRPUSDT 15m** | **4/4** | +20.66 (0.068) | also off-cells 4/4 (+15.89) — most robust |
| **ETHUSDT 15m** | 3/4 | +23.28 (0.076) | off-cells 4/4 (+14.31, exp 0.142) |
| **SOLUSDT 15m** | 3/4 | +26.47 (0.075) | baseline the stronger config |

AVAX 15m (baseline 3/4 +9.77R, exp 0.026) is a marginal pass held for more
evidence; BTC/ADA 15m rejected.

## Proposed cells (exact YAML — for `config/strategies.yaml`)

Each is a NEW strategy entry mirroring the existing 5m alt legs (same signal
params; only `timeframe` differs). Shipped **ungated** (baseline clears ≥3/4 on
its own — the `off_cells`/`vol_spec` local-gate transcription is deferred; see
the caveat), and the entries carry `execution: live` gated to the **`bybit_1`
paper/demo** venue for the live==backtest soak alongside the existing 5m legs
(real-money `bybit_2` is a separate, later Tier-3 gate).

```yaml
  ict_scalp_xrp_15m:
    # XRPUSDT 15m — M27 P1 STRONG PASS: baseline 4/4 folds +20.66R AND
    # off-cells 4/4 +15.89R (net 7.5bps). Ungated evidence clears the gate.
    model: null
    signal_prefixes: [ict_scalp]
    enabled: true
    execution: live          # bybit_1 paper soak (account routing unchanged)
    timeframe: "15m"
    symbols: [XRPUSDT]
    sweep_lookback_bars: 12
    swing_lookback_bars: 20
    atr_period: 14
    sweep_buffer_bps: 5.0
    displacement_atr_mult: 1.3
    min_displacement_body_to_range: 0.55
    min_fvg_size_bps: 2.0
    mitigation_mode: "wick_rejection"
    htf_trend_filter_enabled: true
    htf_filter_timeframe: "1h"
    htf_filter_ema_period: 20
    atr_sl_buffer_mult: 0.20
    tp_at_r: 1.5
    be_offset_bps: 15
    session_filter_enabled: false
    session_start_hour: 7
    session_end_hour: 17
    shadow_model_ids: []
  # ict_scalp_eth_15m: same block, symbols: [ETHUSDT]  (baseline 3/4 +23.28R)
  # ict_scalp_sol_15m: same block, symbols: [SOLUSDT]  (baseline 3/4 +26.47R)
```

(ETH/SOL are byte-identical except `symbols:` and the evidence comment.)

## Required before real-money (`bybit_2`) — the operator gates

1. **`scripts/prop/account_compat_matrix.py`** for each leg × the routed
   account (mandatory per the prop-accounts architecture — a strategy is never
   routed to an account it wasn't evaluated against under that account's rules).
2. **A native-15m confirmation pull** (see caveat) if any leg is to go past
   paper.

## Honest caveats (why this is a paper-soak proposal, not a real-money one)

- **Resample vs native 15m.** The P1 sweep resampled the on-disk 5m shards to
  15m rather than pulling native Bybit 15m klines. The 2023-only frozen vol
  spec is identical either way, but on a native-15m input the `off_cells`
  two-axis gate degenerates to single-axis (`vol5 == vol15`), so the off-cells
  numbers above are a single-vol-axis filter, not the full BTC two-axis shape.
  **Recommendation:** a native Bybit-15m pull + re-validation before any
  real-money routing (cheap; Batch-4 XAUUSD used the native path).
- **Additive, not a replacement.** These 15m legs run *alongside* the existing
  5m legs on the same symbols — more concurrent scalp exposure per symbol. The
  netting/tpsl hardening (cascade-close + qty-scoped partial tpsl, 2026-07-20)
  makes concurrent same-symbol legs' live evidence trustworthy, but the
  operator should weigh the added exposure.

## Operator decision

Approve to add the 3 cells (paper soak on `bybit_1`) as written, request the
native-15m confirmation pull first, or hold. I will not touch
`config/strategies.yaml` until you say which.
