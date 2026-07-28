# Sprint Log: S-M30-TRACK1-EXIT-HEAD-20260728

## Date Range
2026-07-28 (Track-1 overnight-research continuation; fresh session handoff from `track1-micro-jji3ee`).

## Objective
Continue Track 1 (exits + regime) per the operator's ordered plan: (1) drive the inherited microstructure PR #7784 to merged; (2) read Study 10's `research-panel-build` verdict → ledger; (3) build the **centerpiece** — the per-bar in-trade EXIT panel (M30×M20 fusion) with a de-Prado meta-label head, through the $0 GH-runner gate; (4) fold in the taker buy/sell imbalance OFI feature; (5) wire the dead `c_reg` lens offline; plus the Tier-3 operator proposals (M27 winners, fvg_range demote, Schwab app) and the flagged `branch-protection-sync` failure.

## Tier
Tier-1 throughout (research tooling + docs + CI fix; `src/research/*` is not imported by the live pipeline). Tier-3 items are **proposed, not executed**.

## Starting Context
Fresh context resume = new session: read `docs/CLAUDE-RULES-CANONICAL.md`, root `CLAUDE.md`, the `session-coordination` skill; posted board ▶️ START on #6927 before the first change. Prior session (`track1-micro-jji3ee`) handoff: #7786 merged; #7784 armed for auto-merge but stuck `blocked` 13h; Study 10 dispatched to `research-panel-build`; centerpiece build handed to this session; `branch-protection-sync.yml` flagged failing on main.

## Work Completed

### #7784 driven to merged (microstructure Study 9)
Root-caused the 13h `blocked`: the re-sync merge commit `9e0dda4` never fired CI (required checks green on the two prior SHAs, absent on the synced head), so armed auto-merge waited on checks that never came. Pushed an empty commit (`c8b74cd`) to re-trigger CI → green → auto-merged (`04:58:52Z`). Study 9 (microstructure = a volatility/regime signal, not entry direction) is on main.

### Study 10 → ledger (#7787, MERGED)
Read run **#30303698142** (`backtest_system`, 1574 trades) from the job log. **NULL / inconclusive-for-generalization:** `not computed` on every outcome — the whole-roster pooled panel reproduces the block-sparsity wall (0 complete-vector rows across the 6 graded feats). Study 8's `cat_regime` entry edge does **not** replicate at roster scale (not even a whole-roster univariate FDR survivor; only `feat_confidence`), and the per-regime partition was inoperative (portfolio adapter doesn't stamp regime per-row → single `∅missing` cell). Two tooling follow-ups queued; the decisive takeaway = the block-sparse decision-time wall is escaped only by a **dense-by-construction** panel → the centerpiece.

### Centerpiece — per-bar in-trade EXIT head (#7788, DRAFT)
The M30×M20 fusion, built + validated. New pure modules (`src/research/intrabar_features.py`, `triple_barrier.py`, `meta_label.py`), the builder (`build_intrabar_exit_panel.py` — dense per-bar rows: running MFE/MAE-R, dMAE/dt, bars-in-trade, dist-to-stop, giveback, in-trade vol, **taker-imbalance OFI**; triple-barrier + **time-stop** hold-vs-exit meta-label), the analyzer (`analyze_exit_head.py` — uniqueness-weighted take/skip+size head, **grouped purged WF-CV**, net-of-fee exit-policy sim vs the fixed SL/TP, deflated Sharpe / PBO, a **pre-registered dual-criterion verdict** AUC>0.55-stable AND net-R>0), the `research-exit-head-build` workflow, the design doc, and 17 unit tests. Grounded in the M20 exit-management lifecycle (§8 Framing-A null on `market_features` vs the shipped E3 PATH-feature head at AUC 0.70). **Tier-1 build; Tier-3 to ship — draft for operator review before the powered run.**

### Taker imbalance OFI (folded into #7788)
`fetch_backtest_candles.py` now preserves `taker_buy_base` (Binance-vision field 9); `intrabar_features` computes the signed imbalance `2·taker_buy/vol − 1` (last bar + in-trade mean), dense on the vision feed, honestly dropped on a Bybit feed.

### c_reg lens — offline draft (#7789, DRAFT)
Audited the full conviction path: the `c_reg` (regime-alignment) lens is **wired end-to-end in code** and dead for exactly one reason — the regime-alignment calibrator has never been fit/shipped (`fit_regime_alignment_calibrators.py` never run). No code gap. Documented the complete chain + the one-command enablement (trainer-autonomous fit → operator-gated mirror-publish → observe-only soak).

### branch-protection-sync failure — root-caused + fixed (#7790)
The flagged main failure was **not** admin-token noise: `GET protection returned HTTP 301 Moved Permanently` — the 2026-07-23 rename made the hardcoded `repos/benbaichmankass/ict-trading-bot` API path redirect, and the curl calls don't follow redirects. Fixed by building the URL from `${{ github.repository }}` (the live canonical name). Tier-1 CI fix. `BL-20260728-BRANCHPROT-RENAME-301`.

## Validation Performed
- 17 new unit tests (`tests/test_m30_exit_head.py`) + 44 related research tests pass; ruff clean on all new files.
- Exit-head toolchain end-to-end on the committed sample (64 dense per-bar rows from 4 ict_scalp trades; taker cols correctly dropped on the taker-less feed) → analyzer honest-`underpowered` at N=64.
- Analyzer computed-path on a powered synthetic panel (1222 rows): OOS AUC 0.89 recovered across 5/5 folds; the net-of-fee policy sim correctly flagged a high-AUC-but-negative-net-R case as `clears_bar=False` (the AUC≠profit dual gate works).
- Study 10 verdict read directly from the GH-runner job log (harness=backtest_system confirmed in the step env).
- fvg_range_15m routing field-checked in `config/strategies.yaml:768` — confirmed on real money (`bybit_2`).

## Documentation Updated
- `docs/research/technical-quant-research-ledger.md` — Study 10 row + detail + queued follow-ups (#7787, merged).
- `docs/research/M30-intrabar-exit-head-DESIGN.md` — the centerpiece design of record (#7788).
- `docs/research/c-reg-lens-enablement-2026-07-28.md` — the c_reg wiring-status draft (#7789).
- This sprint log + the perf-review backlog Tier-3 items (this PR).

## Risks and Follow-Ups
- **The exit-head powered run is the next turn** (post-review): dispatch `research-exit-head-build` on the full 5m feed, sweep `time_stop_bars`/`tp_r`, land the honest verdict in the ledger as the next Study. The M20 §8 null is the prior to beat; a repeat null points the rigor at sizing/selection.
- Study 10 tooling follow-ups: per-strategy common-core `--features` for pooled discovery + the per-row regime stamp on the `backtest_system` adapter.

## Tier-3 proposals (operator-gated — logged, NOT executed)
Posted to board #6927 + the perf-review backlog: (1) ship the M27 winners (XAUUSD 15m, GLD 1h/Alpaca, SOL/XRP 5m); (2) demote `fvg_range_15m` live→shadow (−41R live real money vs strong backtest — confirm from the live journal first); (3) register the Schwab app (macro hand-off); (4) enable `c_reg` (fit + operator-gated mirror-publish).

## PRs
- #7787 — Study 10 ledger (MERGED).
- #7788 — centerpiece exit head (DRAFT, Tier-3 to ship).
- #7789 — c_reg lens enablement doc (DRAFT).
- #7790 — branch-protection-sync rename fix (Tier-1).
- This — session wrap (sprint log + backlog).

## Wrap-Up Check
- Canonical docs: no contradiction introduced (ledger + design docs are additive; the branch-protection fix restores intended behaviour). doc-freshness: the reframing/ledger/design are coherent.
- Every branch has a PR; coordination-board `▶️ START` posted, `✅ DONE` at close; merge protocol followed on every merge.
