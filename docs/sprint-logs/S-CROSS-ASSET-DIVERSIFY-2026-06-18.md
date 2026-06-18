# Sprint Log: S-CROSS-ASSET-DIVERSIFY-2026-06-18

## Date Range
2026-06-18 (single session; continues S-REGIME-DIVERSIFY / S-DIVERSIFY-BANK).

## Objective
Direction 1 of the strategy-expansion initiative: put the already-wired
**non-crypto paper books** (ib_paper futures MES/MGC/MHG, alpaca_paper equities
SPY/QQQ/GLD) through the *same* robustness gate that banked the crypto book
(`scripts/ops/portfolio_robustness.py`), then build the combined cross-asset
book and measure whether cross-asset diversification lifts portfolio Sharpe /
lowers drawdown vs crypto-only.

## Tier
Tier-1 (research tooling + docs; trainer-VM backtests). No `src/`,
`config/strategies.yaml`, or `config/accounts.yaml` change — the futures and
equity books are already wired enabled+live on PAPER, so Direction-1's paper
expansion needs no wiring. Real-money promotion stays Tier-3.

## Starting Context
A parallel session banked the 10-cell crypto alt book (+409.8R / Sharpe 4.03)
and the robustness tooling on branch `claude/regime-diversification-research`
(PR #3958), merged to `main` (`ede15bf`) this session. S-DIVERSIFY-BANK named
"gold/futures/equity cross-asset validation" as the next bankable expansion.
This branch (`claude/ict-strategy-expansion-awpv8x`) was rebased onto that main.

## Repo State Checked
`config/accounts.yaml` (ib_paper / alpaca_paper / oanda_practice modes +
routing), `config/strategies.yaml` (futures/equity cell params), the merged
`scripts/ops/portfolio_robustness.py`, `config/research/diversified_paper_book.yaml`,
`docs/research/regime-map-step1-results-2026-06-18.md`.

## Files and Systems Inspected
- Trainer VM via `vm-driver` (push `automation/jobs/*.job`): mapped candle paths
  (`~/ws_a_sweep_out/2026-06-02/data/{ES_F,GC_F,HG_F}.csv` daily 2000–2026;
  `~/m15-phase0/data/{SPY,QQQ,GLD}_1d.csv` 2017–2026), the cached crypto emits
  (`results/m15_regime_map/`), and the harness CLIs.
- `scripts/backtest_trend.py` / `scripts/backtest_pullback.py` (emit interface,
  CSV loader — needs only OHLC, no volume).

## Work Completed
- **Per-cell emits (live params)** for the 6 daily non-crypto cells via the
  trend/pullback harnesses (`--emit-trades`), using each cell's *live*
  `config/strategies.yaml` params (not harness defaults).
- **5 `portfolio_robustness.py` runs:** futures, equity/gold, non-crypto,
  crypto (baseline re-run), combined (16 cells). Raw log
  `automation/results/cross-asset-robust.txt`.
- **Write-up:** `docs/research/cross-asset-diversification-2026-06-18.md`.

## Validation Performed
- Both non-crypto books robust on the crypto gate: futures +172.0R / Sharpe 3.67
  / boot P(+)=1.000 / 5-of-5 holdouts +; equity/gold +74.4R / Sharpe 3.35 /
  P(+)=0.999 / 5-of-5 +; leave-one-cell-out all positive on both.
- **Diversification lift (honest, concurrent-cutoff comparison):** at every
  holdout ≥2023-07 (where crypto + non-crypto both trade), the combined book
  beats crypto-only on BOTH Sharpe and drawdown — e.g. ≥2025-01-01: Sharpe
  2.33→3.17, maxDD 96.2R→89.8R, net +140→+198R. (Full-history combined Sharpe
  5.74 is span-confounded by 21 pre-crypto non-crypto years — not the headline.)
- Emit fidelity: harness echoes each cell's live params; per-cell net_r all
  positive; trade counts sane (daily cadence → 26–189/cell).

## Documentation Updated
- `docs/research/cross-asset-diversification-2026-06-18.md` (new).
- ROADMAP.md: S-CROSS-ASSET-DIVERSIFY row + header note.
- This sprint log.

## Contradictions or Drift Found
- None in the canonical set (`check_canonical_doc_coherence.py` PASS). The
  research doc states VM-free / accurate account modes; no gate or VM-topology
  drift introduced.

## Risks and Follow-Ups
- **Fee model caveat:** emits used 7.5 bps; futures/equities cost is
  per-contract commission + spread. R-space + the large added-cost headroom
  (futures +0.43R/trade, equity +0.71R/trade) cover it directionally, but a
  per-contract `account_compat_matrix` validation is mandatory before any
  real-money step.
- **Thin equity samples** (SPY/QQQ ≈ 3 trades/yr) and **internal equity-beta
  correlation** (MES/SPY/QQQ are one cluster; MGC/MHG/GLD another) — the genuine
  diversification axes are crypto / equity-index / metals, not 6 independent cells.
- **`all_years_positive` flag** trips on thin early-2000s years + crypto's flat
  2026-YTD — same blemish class as the banked crypto book, not a losing signal.

## Deferred Items
- **`mgc_trend_1h`** unvalidated — no ≤1h gold history on the trainer (only daily
  GC_F + spot XAUUSD_15m). Logged to performance-review backlog.
- **Direction 2 (recombination sweep)** — `scripts/ops/recombination_sweep.py`
  over the existing pool + a non-crypto symbol-axis extension.
- **Real-money promotion** of any non-crypto cell — Tier-3, operator + account_compat gated.

## Next Recommended Sprint
Direction 2 (recombination sweep) on the trainer; and, if the operator wants,
a per-contract `account_compat_matrix` pass on the strongest non-crypto cells
(mhg_pullback_1d, mgc_pullback_1d) as the basis for a Tier-3 real-money proposal.

## Wrap-Up Check
- [x] Robustness runs validated on real trainer data (vm-driver log committed).
- [x] Material decision recorded in ROADMAP + this sprint log + backlog.
- [x] No Tier-3 config/live change made without approval (paper books already wired).
- [x] `/doc-freshness` run at session close.
- [x] Draft PR #3960 opened.
