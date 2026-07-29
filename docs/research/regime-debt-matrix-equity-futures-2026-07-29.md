# Regime-Debt Matrix — Equity / ETF / Futures Roster (rec #5)

**Date:** 2026-07-29
**Run:** [issue #7917](https://github.com/benbaichmankass/Metis-Insights/issues/7917#issuecomment-5119466817) · workflow `regime-debt-matrix` run `30462697297` (free GitHub runner, `results.json` uploaded)
**Engine:** `scripts/research/regime_debt_matrix.py` (#7916) · `.github/workflows/regime-debt-matrix.yml`
**Window:** 730 days · **Roster:** 35 rows — **16 faithful · 19 approximate · 0 errored · 0 skipped**

This closes the data-blocked half of the rec #5 regime-coverage debt matrix. The
crypto-plain matrix (#7912) covered the Binance-reachable subset; the sandbox
firewalls Yahoo, so the equity/ETF/futures majority (SPY/QQQ/IWM/TLT/GLD/SLV/GDX/
IAUM/IEF/QLD/TQQQ/SPLG/SCHA/USO + MES/MGC/MHG) ran here on a free runner. **The
Yahoo path is now first-run-verified** — every equity/ETF row and all three
continuous-futures proxies (`ES=F`/`GC=F`/`HG=F` for MES/MGC/MHG) served cleanly,
zero errored rows.

## What a cell means (and the bar for acting on one)

Each row is a per-`(trend_regime, direction)` net-R breakdown from the strategy's
**exact live params**, using the same ADX regime tagging (`regime_tag_emitted.py`)
the router uses. Net-R normalizes P&L by each trade's own risk, so cross-instrument
cells compare on one axis.

Per the rec #5 **no-cosmetic-cell rule**, an OFF cell is proposed only when a
`(regime, direction)` is **negative at adequate n AND enforceable**. This run
**authors no cell** — it is evidence only. Two gates stand between a losing cell
here and a live gate:

1. **Fidelity.** Only **faithful** rows (base harness models every declared lever)
   can source a cell. An **approximate** row runs base geometry with declared
   levers omitted, so a losing cell there may be an artifact of the missing lever —
   not enforceable until re-run faithfully.
2. **Regime-of-sample stability.** A full-sample cell — even a powered one — can be
   an artifact of *when* the regime happened to occur. **#7915 just walk-forward-
   refuted the 2h-pullback long-drag on exactly this basis.** So a powered losing
   cell here is a **walk-forward candidate**, not a draft PR. Any Tier-3 OFF-cell
   PR must first pass the same 2-year walk-forward (`direction_walkforward.py`) that
   #7915 established as the standard.

## Dispositions

### 1. Strong powered losing cell → walk-forward candidate (faithful)

**`gld_pullback_1h` — (trending, SHORT) = −15.68R @ n=36.**
The standout. GLD 1h pullback is a strong *long* engine (+59.4R lifetime; +32.98R
long in trending alone) whose entire drag is the short side (−14.4R total short,
concentrated in trending −15.68R@36 and transitional −3.66R@11). Faithful, powered,
large magnitude, and directionally clean (long stays strongly positive). This is
**the one row that clearly warrants a walk-forward confirmation run** before drafting
a `(trending, short)` — or whole-short — OFF cell. Enforceable via the frozen ADX
trend-regime path GLD already resolves (no advisory head required).

### 2. Powered but modest → watch, re-confirm before any cell (faithful)

- **`qqq_pullback_1h` — trending −6.06R @ n=80** (long −3.08@39, short −2.99@41),
  while transitional (+8.86) and chop (+7.26) carry it. A clean *trend-regime*
  (both-direction) skip candidate — powered (n=80) but small per-trade (~−0.076R),
  so it needs walk-forward confirmation to distinguish signal from noise.
- **`slv_trend_1h` — short −5.5R** (trending −5.61@23, chop −5.07@10; transitional
  +5.20@23). Long side is +49R. Direction-cell (short) watch; n is moderate.
- **`trend_donchian_ada_4h` / `trend_donchian_avax_4h`** — trending net slightly
  negative (−2.8 / −4.2) but each cell n=8–14 (marginal); chop carries both (+14R).
  Below the confidence bar — watch, not a candidate.

### 3. Refuted-enforceability — the 2h-pullback long-drag → stays debt

`ada_pullback_2h` (trending long −3.27@36), and the **approximate** `avax_pullback_2h`
(−14.08@44) and `sol_pullback_2h` (−12.39@40) all reproduce the 2h-pullback
long-drag. **#7915 walk-forward-refuted this exact pattern as regime-of-sample-
unstable** — it does not survive out-of-sample. Consistent here; **no cell**, stays
tracked debt. (Do not re-litigate — the walk-forward already ruled.)

### 4. Under-powered daily cells → stay debt

Most 1d equity/futures strategies have n=1–7 per cell over 730 days (the long-only
trend legs `mes/spy/qld/tqqq_trend_long_1d`, `gdx/ief_pullback_1d`): all directionally
fine or too thin to gate. **`tlt_pullback_1d`** is uniformly negative across regimes
(−4.8R total) but max n=5/cell — that reads as **strategy-level** underperformance,
not a regime cell; routed to a strategy-review follow-up, not an OFF cell.

### 5. Approximate rows → re-run faithfully before dispositioning

19 rows ran base-geometry-only with declared levers omitted; **none is dispositioned
as a cell.** The ones worth a faithful re-run (powered losers whose omitted lever
could plausibly be the cause):

- **`trend_donchian_sol` (1h)** — chop long −9.26R@35 (long-only), but `exit_head_*`
  + `stale_exit_*` levers omitted (material exit levers). Re-run through the faithful
  exit path before any read. **RESOLVED (2026-07-29):** the trend harness was
  extended to model the stale-exit lever (#7926) and the row was re-measured with
  it ON (#7928) — the chop-long drag **collapses −9.26R → −2.32R@39** (~−0.06R/trade),
  the unmodeled `exit_head_*` only shrinks it further. The drag was largely the
  omitted lever, **not a cell** → stays tracked debt. Detail:
  `docs/research/regime-cell-walkforward-2026-07-29.md` § "Follow-up dispositions".
- **`spy_pullback_1h`** — chop −8.70R, trending short −9.38@25; `skip_hours` omitted.
- **`avax_pullback_2h`** — see §3 (long-drag, already refuted class).

## Outcome

- **No cell authored.** rec #5 equity/ETF/futures debt is now **measured** (0 errored)
  rather than data-blocked.
- **One walk-forward candidate** carried forward: `gld_pullback_1h` short. Two watches
  (`qqq_pullback_1h` trending, `slv_trend_1h` short). All gated on the #7915-standard
  2-year walk-forward before any Tier-3 draft PR — no full-sample cell ships un-validated.
- **The 2h-pullback long-drag stays refuted** (consistent with #7915); under-powered
  daily cells stay tracked debt; approximate rows await a faithful re-run.
- Full per-strategy matrix: [#7917](https://github.com/benbaichmankass/Metis-Insights/issues/7917#issuecomment-5119466817).

**Follow-up (2026-07-29):** the walk-forward gate ran on the candidate + the two
watches — `docs/research/regime-cell-walkforward-2026-07-29.md`. `gld_pullback_1h`
trending-short **survived** (4/4 folds) → Tier-3 draft OFF cell (#7923);
`qqq_pullback_1h` survived weakly (offered); `slv_trend_1h` was **refuted**
(regime-of-sample) and stays debt.

**Tier-1** — research evidence only; no `config/`, no order path.
