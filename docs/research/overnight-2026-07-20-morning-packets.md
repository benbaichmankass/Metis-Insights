# Overnight results + morning Tier-3 decision packets (2026-07-20 → 07-21)

Prepared by the overnight ML-continuation session (operator directive: work
autonomously, hold NEW Tier-3 decisions for the morning). Everything below is
evidence + a recommendation; **nothing here has been executed** except where
explicitly marked DONE.

## Executed overnight (no decision needed)

- **Tier-2 registry-fingerprint cache invalidation: SHIPPED + LIVE** (operator
  approved in chat). PR #7179 merged as `ee814548`, `pull-and-deploy` completed
  22:00Z, `/api/diag/version` verified `ee814548`. Promotions now activate
  restart-free (mirror publish → git-sync → fingerprint flip → next tick).
- **ETH xa dataset defect FIXED at the data layer** (relay #7186): cross_asset
  side-stream rebuilt under current code, `market_features v901` rebuilt with
  it, verify gate read `ALL_POPULATED` (xa_peer2_* now live — the
  BL-20260628-XA-TRAINING-ZERO / MB-20260719 defect is closed at the root).
  `eth-regime-15m-lgbm-xasset-v1` **retrained + registered** on the fixed
  dataset (eval: recall_volatile 0.935, precision_volatile 0.324, weighted_f1
  0.482, n_eval 35054). Its shadow-soak clock restarts 2026-07-20 → M25 gates
  re-checkable ~2026-07-27.
- **MES re-cert** — pre-approved; executes at 23:50Z tonight (result appended
  to this doc's sprint follow-up when done). First live validation of the
  restart-free activation path.

## Packet A — SOL `trend_vol` OFF-cells (Tier-3 decision: author or not)

**Pipeline** (relays #7188/#7194, done 21:58Z): full-history cell attribution
over the SOL roster (`trend_donchian_sol`, `trend_donchian_sol_4h`,
`sol_pullback_2h`; 875 trades 2021-10→2026-06) with the vol axis driven by the
NEW advisory head `sol-regime-15m-lgbm-fc-pcv-v1` (2,004 scored bars, zero
frozen-fallbacks) → auto-authored OFF-cells (net ≤ −$50, ≥ 10 trades) →
confirmation A/B → 4-fold walk-forward.

**Auto-authored cells (3, all on the CALM side):**

| Cell | Net | Trades | Note |
|---|---|---|---|
| `trend_donchian_sol` transitional/calm **long** | −$116 | 46 | Mirrors the AUTHORED BTC cell (`trend_donchian` transitional/calm long −$356/43t) — same "Donchian long without a real trend" pathology, strong cross-symbol consistency |
| `sol_pullback_2h` trending/calm **long** | −$110 | 76 | |
| `sol_pullback_2h` chop/calm **long** | −$92 | 45 | |

**Results vs the acceptance bar** (BTC's bar was: gated ≥ ungated net AND
gated ≤ ungated maxDD in EVERY fold — BTC passed 4/4):

| Window | Ungated | Evidence+ML | Verdict |
|---|---|---|---|
| Full history | $2084 / DD $583 | $2154 / DD $672 | net ↑, **DD ↑ (worse)** |
| 2022-07→2023-07 | $526 / $263 | $616 / $238 | PASS both |
| 2023-07→2024-07 | $478 / $346 | $484 / $317 | PASS both |
| 2024-07→2025-07 | $456 / $407 | $586 / $361 | PASS both |
| 2025-07→2026-06 | $8 / $459 | **−$65** / $343 | **FAIL net** (DD better) |

**Honest caveats:**
1. **3/4 folds, not 4/4** — and the failing fold is the most recent year.
2. **Label-fidelity gap:** the harness serves the fc-pcv head WITHOUT its 6
   `fc_*` forecast features (no offline forecast join), and the measured probe
   says the fc-less label agrees with the full-feature label only **80.85%**
   of the time (mean |ΔP(volatile)| 0.19). Live gating would use the
   full-feature label, so the backtest is an ~81%-faithful proxy of what the
   live gate would do. (BTC's evidence didn't have this gap — its head was
   base-features-only, served exactly.)
3. All three cells are `calm` cells: the harvest here mostly refines the
   TREND axis, and only bites when `REGIME_ML_VERDICT_MODE=use` resolves SOL
   labels (which it now can — the SOL advisory head went live tonight).

**Options for the morning:**
- **(a) Do not author SOL cells yet** (bar not met). Revisit after building an
  offline fc-feature join for the harness (removes caveat 2) — the clean way.
- **(b) Author only the BTC-consistent cell** (`trend_donchian_sol`
  transitional/calm long) — the one with independent cross-symbol replication —
  and let the other two wait for a fc-faithful re-run.
- **(c) Author all three** accepting 3/4 + all-folds-DD-improvement.

**Recommendation: (a) or (b).** (b) is defensible on the replication argument;
(c) over-trusts a proxy-labeled backtest that fails its most recent fold.

## Packet B — frozen-dataset per-head decisions

See `docs/research/M25-frozen-dataset-remediation-2026-07-20.md` (WS-4 memo).
One-line recap: accept-frozen for both live fc-pcv heads + build refresh
siblings in parallel; retire `eth-regime-15m-lgbm-selfonly-v901ctrl` to
candidate; mark the two constant baselines intentionally-frozen. (The ETH
xasset head is already refreshed — done overnight, above.)

## Packet C — M26 P1 conflict-taxonomy soak (info)

Verified accruing: two real `flip_suppressed_hold_policy` events captured on
2026-07-20 (SPY shorts vs a held long, 16:44Z + 17:00Z) — the classified-soak
writer is upstream-triggered by exactly these. P2 (transition score) waits on
~a week of rows. A small Tier-1 follow-up: add `conflict_taxonomy_soak` to the
diag `log_file` allowlist for direct reads.
