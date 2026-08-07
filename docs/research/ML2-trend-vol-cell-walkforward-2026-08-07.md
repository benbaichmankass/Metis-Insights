# ML2 — walk-forward of the six authored `trend_vol` OFF-cells (2026-08-07)

**PROPOSE ONLY.** Every cell change is Tier-3. Nothing here was applied.

## Population (state it before reading any number)

- Cells: the six `trend_vol` entries in `config/regime_policy.yaml`.
- Tool: `scripts/research/regime_cell_walkforward.py --strategy X --regime Y --vol Z
  --vol-labels /tmp/btc_vol_labels.jsonl`, run on the trainer (relay #8564).
- Trades: BTCUSDT reconstructed from Binance-vision candles, 2024-08 → 2026-08.
  `trend_donchian` 1h (`fidelity=approximate`, 5 omitted levers);
  `squeeze_breakout_4h` 4h (`fidelity=faithful`).
- Vol labels: `ml_vol_label_replay` over `market_features` BTCUSDT 15m v520,
  175,272 rows, head `btc-regime-15m-lgbm-fc-pcv-v2`, volatile share 9.36%.
- Verdict basis: fixed `FOLD_PANEL=(3,4,5)`; `*_stable_drag` requires pooled<0
  AND strict majority-negative in EVERY panel member.

**Live fidelity of the labels is UNVERIFIED** — see "Parity" below. These are
backtest verdicts, not live-parity-confirmed ones.

## Results — graded on the direction each cell actually gates

| cell | authored | n | pooled R (gated dir) | maj-neg k3/k4/k5 | verdict |
|---|---|--:|--:|---|---|
| trending/volatile `trend_donchian` | `long: off` | 9 | **−4.9495** | T/T/T | **stable_drag — JUSTIFIED** |
| trending/calm `squeeze_breakout_4h` | `short: off` | 9 (6 short) | **+0.8713** | T/F/T | fold_sensitive — RETIREMENT CANDIDATE |
| transitional/calm `trend_donchian` | `long: off` | 30 | **+1.5624** | F/F/T | fold_sensitive — RETIREMENT CANDIDATE |
| chop/calm `trend_donchian` | `long: off` | 43 | −1.9697 | F/F/T | fold_sensitive — RETIREMENT CANDIDATE |
| trending/volatile `ict_scalp_5m` | `long+short: off` | — | — | — | UNGRADABLE |
| chop/volatile `ict_scalp_5m` | `long+short: off` | — | — | — | UNGRADABLE |

**1 of 6 affirmatively justified. 3 fail. 2 have no evidence path.**

Per Rule 1 of the `regime-selectivity` skill, `*_fold_sensitive` is a
retirement candidate, NOT a pass.

### The one that passes

`trending/volatile trend_donchian long`: pooled −4.9495 over 9 trades,
majority-negative under all three panel fold counts, not fold-sensitive.
Caveat: **n=9** — the volatile regime is only 9.36% of bars, so thin-n here is
structural, not incidental. The verdict is stable across the panel, which is
what the panel exists to test, but 9 trades is 9 trades.

### Two cells look inverted, not merely unjustified

**`squeeze_breakout_4h` trending/calm** gates **short** (pooled **+0.8713**)
while **long** is pooled **−2.4341 with `long_stable_drag=True`** and is left
**on** (`{ long: on, short: off }`). The authored cell gates the direction that
made money and permits the direction that passes the drag test. That is a
stronger claim than "unjustified" and is the one worth looking at first.

**`transitional/calm trend_donchian`** gates a **pooled-POSITIVE** long
(+1.5624 over 30 trades).

### `ict_scalp_5m` cannot be graded by this harness

Both its cells return `ERROR: unclassifiable (no donchian/pullback/squeeze
params or no symbol/timeframe)`. Two of six authored cells therefore have **no
walk-forward evidence path at all** — they are neither justified nor refuted.
That gap is itself a finding: they were authored under the 2026-07-20 Phase-4
packet and cannot currently be re-checked by the tool the gate doc points at.

## Do NOT compare these to the policy file's inline comments as-is

`config/regime_policy.yaml` annotates these cells with full-sample dollars from
the debt matrix (`−$224 / 136t`, `−$55 / 30t`, `−$356 / 43t`, `−$218 / 11t`).
Those are a DIFFERENT population from the R figures above and the trade counts
do not line up (the chop/calm comment says 11t where the walk-forward finds 43;
transitional/calm says 43t where it finds 30). This is not asserted as a
contradiction — the two were computed over different feeds and windows. It is
flagged because someone will otherwise read the two side by side and treat the
mismatch as either a confirmation or a refutation. It is neither until the
populations are reconciled.

## Parity: unverified, and honestly unmeasurable today

The labels file ends **2026-06-30T22:30:00Z** (dataset v520). The ML vol axis
went live **2026-06-29**. Overlap with the live audit corpus is **4 rows**.

`verify` previously reported `comparable: 208 / agreement_pct: 95.67` over that
window because it clamped 204 out-of-range rows onto the final bar; fixed in
`ecc414e` (PR #8553). Establishing real parity needs a dataset rebuilt through
August, re-replayed, then re-verified. Until then these cells are graded on
backtest evidence whose live fidelity is unconfirmed.

## Recommended next steps (all Tier-3 to enact)

1. Rebuild `market_features` BTCUSDT 15m through the current date; re-replay;
   re-run `verify` (now with `--min-overlap`) to establish parity properly.
2. Re-examine `squeeze_breakout_4h` trending/calm — the direction inversion is
   the highest-value item here and does not depend on parity.
3. Give `ict_scalp_5m` an evidence path, or record explicitly that its two
   cells rest on the Phase-4 packet alone.
4. Do not retire any cell on this evidence alone; walk-forward + parity first.
