#!/usr/bin/env python3
"""M20 fleet-wide exit-lever sweep — every donchian/pullback-family leg,
CONFIG-EXACT, driven straight from config/strategies.yaml.

The exit-refinement skill's P2 stage industrialized: for each strategy leg it
resolves the leg's harness (donchian family -> scripts/backtest_trend.py,
pullback family -> scripts/backtest_pullback.py), its data file, and its OWN
YAML params (donchian/atr/trail/min_conf/long_only/adx_min/pullback_frac...),
then A/Bs the exit-lever cells (stale-stop, giveback-stop, trail +/-1) against
the config-exact base:

  1. IS/OOS split — PER-LEG and DERIVED by default (`--split-mode oos-trades`,
     targeting `--split-target-oos` trades in OOS). `--split` is the fixed
     calendar date used when `--split-mode=date`, and the FALLBACK when the
     derivation cannot be satisfied; it is NOT the boundary by default. (This
     line read "IS/OOS split (--split, default 2025-07-01)" until 2026-08-13,
     which was accurate before `resolve_split` and then described an input that
     had stopped governing — the same drift that left the workflow's own `split`
     description naming a quantity it no longer controlled.) A cell is a
     CANDIDATE only if it beats base on net_R AND maxDD in BOTH windows.
  2. Candidates go to a yearly walk-forward (2021..2026); PASS needs
     beats-or-ties base on net_R AND maxDD in >= 2/3 of usable folds.

Anything else is an honest negative. Output (one dir per run):
  runtime_logs/m20_fleet/<UTC-date>/results.jsonl   one row per leg x cell x window
  runtime_logs/m20_fleet/<UTC-date>/verdicts.json   per-leg matrix-aligned verdicts
  runtime_logs/m20_fleet/<UTC-date>/SUMMARY.md      human table

Data conventions (trainer): data/{SYMBOL}_{5m,15m,1h,1d}.csv — the finest
available file is used with --resample to the leg's timeframe. PROXY map for
futures without their own file (MGC/XAUUSD -> GC_F); a proxied leg's verdict is
tagged proxy:true. A leg with no data resolves to data_missing (the coverage
matrix's `blocked` reason) rather than being skipped silently.

Tier-1 research tooling — never writes config; Tier-3 ships remain
operator-gated. Run on the trainer (long: hours) detached:
  nohup .venv/bin/python3 scripts/research/m20_fleet_exit_sweep.py \
      --out runtime_logs/m20_fleet >/tmp/fleet_sweep.log 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import exit_capture  # noqa: E402  (the ONE exit-capture definition)

# families with harness exit-lever support; everything else is reported
# no_harness_levers (vwap/turtle_soup/fade — pending harness levers).
# ict_scalp gained stale/giveback lever support 2026-07-28 (M27 follow-up) — the
# trailing levers (trail_geometry/trail_decay/vol_trail) stay n/a for a
# fixed-bracket scalp, so cells_for only emits its stale/giveback cells here.
DONCHIAN_HARNESS = "scripts/backtest_trend.py"
PULLBACK_HARNESS = "scripts/backtest_pullback.py"
SQUEEZE_HARNESS = "scripts/backtest_squeeze.py"
FVG_HARNESS = "scripts/backtest_fvg_range.py"
SCALP_HARNESS = "scripts/backtest_ict_scalp.py"
FAMILY_HARNESS = {"donchian": DONCHIAN_HARNESS, "pullback": PULLBACK_HARNESS,
                  "squeeze": SQUEEZE_HARNESS, "fvg": FVG_HARNESS,
                  "scalp": SCALP_HARNESS}

# Families whose LIVE unit clamps the TP to _TP_SENTINEL_CAP_PCT (0.099).
# MEASURED 2026-08-10 by grepping src/units/strategies/ for the constant:
# trend_donchian.py, htf_pullback_trend_2h.py, fade_breakout_4h.py and
# squeeze_breakout_4h.py carry it; fvg_range does NOT, and ict_scalp uses a
# real tp_at_r bracket rather than a sentinel. Applying the cap to a family
# whose live unit has none would MANUFACTURE a parity break instead of
# reproducing one -- so this list is a measurement, not a convenience.
# THE CLAMP IS APPLIED VENUE-BLIND — measured 2026-08-10, and it corrects the
# opposite assumption this comment used to carry. The constant's own docstring
# says "Bybit (and most exchanges) reject TP further than ~10%", which reads as
# a Bybit-specific constraint, so the natural inference is that an Alpaca/IBKR
# leg never binds it and the parity break is crypto-only. The CODE says
# otherwise: all four units compute
#     tp = min(entry * (1 + _TP_SENTINEL_CAP_PCT), entry + tp_r * risk)
# with NO exchange/account branch anywhere (trend_donchian.py:388,
# htf_pullback_trend_2h.py:322, squeeze_breakout_4h.py:176,
# fade_breakout_4h.py:264). Field beats comment: every leg in these families
# places the capped TP whatever the broker, so --tp-cap-pct is live parity for
# the equities and futures legs too, not an upper bound. The break is WIDER
# than the Bybit-only reading, not narrower.
#
# `fade` IS UNREACHABLE FROM classify() AND THAT IS CORRECT, not coverage.
# Measured 2026-08-10 against config/strategies.yaml: 52 of 55 declared
# strategies classify (donchian 23 · pullback 19 · scalp 8 · fvg 1 ·
# squeeze 1); the three that do not — fade_breakout_4h, turtle_soup, vwap —
# are ALL `execution: shadow`, so the census covers every live-executing leg.
# fade_breakout_4h carries _TP_SENTINEL_CAP_PCT in its unit file but places no
# live order, so the parity break reaches money through THREE families, not
# four. The entry stays because backtest_fade.py implements the lever and the
# day fade is promoted this becomes live rather than something to remember;
# it must not be read as "fade is being measured today".
LIVE_TP_CAPPED_FAMILIES = {"donchian", "pullback", "fade", "squeeze"}

PROXY_DATA = {"MGC": "GC_F", "XAUUSD": "GC_F", "MES": "ES_F", "MHG": "HG_F"}
DATA_GRAIN = ["5m", "15m", "1h", "1d"]
TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}

# Per-harness-run subprocess cap. 1800s, matching run_census, NOT the 900s this
# used to carry: the 2026-08-10 census measured a single full-history
# ict_scalp_5m run at 955s, so an IS-window scalp run sat right on the old cap.
# A cell that times out reads `verdict: error` — honest, but it is a
# measurement we failed to take, and taking it is cheap on a free runner.
# Overridable with --cell-timeout.
CELL_TIMEOUT_S: float = 1800.0

# Memo for run_cell, keyed on the full invocation. See run_cell's docstring for
# why this is load-bearing rather than cosmetic.
_CELL_CACHE: dict[tuple, dict] = {}

FOLDS = [("2021", "2021-01-01", "2022-01-01"), ("2022", "2022-01-01", "2023-01-01"),
         ("2023", "2023-01-01", "2024-01-01"), ("2024", "2024-01-01", "2025-01-01"),
         ("2025", "2025-01-01", "2026-01-01"), ("2026", "2026-01-01", None)]


def classify(name: str) -> str | None:
    if "ict_scalp" in name or "scalp" in name:
        return "scalp"
    if "pullback" in name and "htf_pullback" not in name:
        return "pullback"
    if "htf_pullback" in name:
        return "pullback"
    if "squeeze" in name:
        return "squeeze"
    if "fvg" in name:
        return "fvg"
    if "donchian" in name or "_trend" in name:
        return "donchian"
    return None


def _resolve_one(sym: str, tf: str, data_dir: Path) -> tuple[str | None, str | None]:
    """(path, resample) for ONE symbol spelling — no proxy substitution.

    Extracted from `resolve_data` so the native and proxy spellings can be
    tried in order; the body is unchanged.
    """
    leg_min = TF_MINUTES.get(tf, 60)
    # native grain first (a 1d archive usually has YEARS more history than
    # the 1h file it would otherwise be resampled from), then finest
    native = data_dir / f"{sym}_{tf}.csv"
    if native.exists():
        return str(native), None
    # EXACT-TIMEFRAME ONLY, and deliberately NOT inside the grain loop below
    # (BL-20260814-BTCUSDT-HAS-NO-CANONICAL-5M-CSV). Some series live under a
    # `backtest_` prefix rather than the canonical spelling — BTC 5m is the
    # live case: `backtest_BTCUSDT_5m.csv` is 647,585 rows (2020-03-25..
    # 2026-05-21), deeper than any canonical alt 5m file, and is already the
    # DEFAULT feed for all six walkforward_vol_* scripts, i.e. the series
    # behind the live regime-router OFF cells. The prefix glob further down
    # cannot reach it: its prefixes are {sym.lower()} plus the USDT-stripped
    # base, and `backtest_btcusdt_5m.csv` starts with neither.
    #
    # WHY EXACT-TF AND NOT A GRAIN CANDIDATE. Putting this in the grain loop
    # would be the harmful version. DATA_GRAIN is FINEST-FIRST, so a BTC
    # 1h/2h/4h/1d leg — which today falls through to BTCUSDT_15m.csv — would
    # start taking the 5m file instead, and that file ends 2026-05-21 against
    # the 15m file's 2026-07-10. Every BTC leg coarser than 15m would quietly
    # lose ~7 weeks of the most recent history, behind verdicts already
    # recorded in the coverage matrix. That is this module's own docstring
    # warning one level less visible than the MGC_1d.csv incident: there the
    # NAME lied, here the name would be honest and only the RANGE dishonest.
    # Restricted to the leg's own timeframe, the probe can only fire where
    # nothing resolves at all, so it cannot move any recorded basis.
    #
    # MEASURED, not argued from the shape (trainer-diag #9325): enumerating
    # every leg x symbol x both prefer_native modes = 110 resolutions, this
    # changes 4 — ict_scalp_5m (live) and vwap (shadow), each in both modes,
    # all four `None` -> resolved. Nothing that resolves today moves.
    # data/backtest_ESF_1h.csv also exists and is inert: no leg sits at
    # (ESF, 1h). data/backtest_candles.csv does not parse as (sym, tf) at all.
    prefixed = data_dir / f"backtest_{sym}_{tf}.csv"
    if prefixed.exists():
        return str(prefixed), None
    for g in DATA_GRAIN:
        if TF_MINUTES[g] > leg_min:
            break
        p = data_dir / f"{sym}_{g}.csv"
        if p.exists():
            resample = tf if TF_MINUTES[g] < leg_min else None
            return str(p), resample
    prefixes = {sym.lower()}
    if sym.upper().endswith("USDT"):
        prefixes.add(sym.lower()[:-4])
    best: tuple[int, Path] | None = None
    for p in data_dir.glob("*.csv"):
        low = p.name.lower()
        if not any(low.startswith(pre + "_") or low == pre + ".csv"
                   for pre in prefixes):
            continue
        grain = next((g for g in DATA_GRAIN if f"_{g}" in low), None)
        if grain is None or TF_MINUTES[grain] > leg_min:
            continue
        if best is None or TF_MINUTES[grain] < best[0]:
            best = (TF_MINUTES[grain], p)
    if best is not None:
        resample = tf if best[0] < leg_min else None
        return str(best[1]), resample
    return None, None


def resolve_data(symbol: str, tf: str, data_dir: Path,
                 prefer_native: bool = False) -> tuple[str | None, bool, str | None]:
    """(path, proxy?, resample) — finest grain <= leg tf; None if nothing.

    Primary convention data/{SYMBOL}_{grain}.csv; fallback is a
    case-insensitive prefix glob (covers legacy names like
    btc_1h_multiyear.csv), matching on the symbol and its USDT-stripped
    base, picking the finest grain token found in the filename.

    `prefer_native` (BL-20260814-PROXY-MAP-SHADOWS-NATIVE-DATA) decides which
    spelling is tried FIRST, and the default is deliberately the historical
    proxy-first order:

    - **False (default)** — `PROXY_DATA` is applied unconditionally, so MGC
      resolves `GC_F_*.csv` even when `MGC_*.csv` exists. Two reasons, and the
      first is the load-bearing one: the PROXY IS THE DEEPER SERIES. Measured
      2026-08-14 on the trainer, genuine native IBKR *contract* history is
      `market_raw/MGC/1d/v003` at **940 rows** (2022-09-30..) vs the proxy
      `GC_F_1d.csv` at **2,512** (2016-07-12..) — ~2.7x. MHG 1,043 and MES 677
      are the same shape. Preferring native by default would collapse the
      2021..2026 fold structure. Second, independently: flipping which series a
      RECORDED verdict was measured against must not ride along silently inside
      a reachability fix.

      ⚠️ **`datasets-out/market_raw/{MGC,MHG,MES}/1d` IS NOT NATIVE** — it is
      built by `build_trainer_datasets.sh::build_equity_daily MGC "GC=F"`, i.e.
      yfinance on the FULL-SIZE contract, and it is byte-for-byte the proxy:
      2,511 of 2,512 overlapping closes identical to `GC_F_1d.csv` (the one
      difference is the proxy's stale last bar), MES 2,514 of 2,514. Converting
      it to `data/MGC_1d.csv` produces a file whose NAME asserts a provenance
      its CONTENT does not have, and `prefer_native` would then report
      `proxy=False` and let the head round train on exactly the series it
      refuses. A session did that on 2026-08-14 and removed it the same hour;
      do not recreate it. Native means the IBKR contract shards under
      `data/ibkr_datasets/market_raw/`, nothing else.
    - **True** — try the native spelling first and fall back to the proxy.
      For a consumer that REFUSES proxied data this is the only way native
      data is reachable at all: `m20_exit_head_round` skips any leg whose
      `proxy` is set ("native history required for head training"), and
      because the proxy was applied unconditionally that skip fired for
      MES/MGC/MHG no matter what was on disk — so the three `exit_head_ml`
      cells blocked on native IBKR history could never close, and the Tier-2
      pull action added 2026-07-07 for exactly those cells was inert against
      this resolver.

    Depth vs fidelity is a real trade-off, so it is a caller decision rather
    than one default pretending to serve both.
    """
    alt = PROXY_DATA.get(symbol)
    proxied = alt is not None and alt != symbol
    # Default order is EXACTLY the historical one (proxy alone when a proxy is
    # declared) — and deliberately no native fallback either, so a missing
    # proxy file keeps reading `data_missing` rather than silently switching
    # that leg onto a different series. Both halves are about NOT changing a
    # recorded verdict's basis as a side effect. On depth: the PROXY is deeper
    # at 1d too (940 native rows vs 2,512) — see the docstring, and note that
    # `datasets-out/market_raw/MGC/1d` is NOT native, it is yfinance GC=F.
    order = [alt if proxied else symbol]
    if prefer_native and proxied:
        order.insert(0, symbol)
    for cand in order:
        path, resample = _resolve_one(cand, tf, data_dir)
        if path is not None:
            return path, cand != symbol, resample
    # Nothing found either way. Report whether a proxy WOULD have been used,
    # preserving the historical not-found contract (every caller checks
    # `data is None` before reading this flag, but the shape stays stable).
    return None, proxied, None


# WHICH CONFIG KEYS CONSTITUTE EACH DECLARED EXIT LEVER.
#
# The map exists so the lever-OFF arm can REMOVE a shipped lever from the
# config-exact base. `trail_geometry` is deliberately ABSENT: `trail_mult` is a
# continuous parameter of the family block with no OFF state (a trail-less
# donchian leg is a different strategy, not the same leg with a lever off), and
# it is emitted by the family branch rather than by `declared_levers()`. Listing
# it here would let `--without-declared-lever trail_geometry` silently produce a
# base whose stop geometry is undefined.
#
# `exit_ladder`, `exit_head_ml` and `regime_flip_exit` are absent for a simpler
# reason: no leg DECLARES them in YAML, so there is nothing in the base to drop.
LEVER_DECLARED_KEYS: dict[str, tuple[str, ...]] = {
    "stale_stop": ("stale_exit_bars", "stale_exit_below_r"),
    "giveback_stop": ("giveback_min_mfe_r", "giveback_r"),
    "trail_decay": ("trail_decay_arm_r", "trail_decay_stall_bars",
                    "trail_decay_tight_mult"),
    "vol_trail": ("trail_vol_above_pctl", "trail_vol_below_pctl",
                  "trail_vol_tight_mult"),
}


def declared_levers_present(cfg: dict) -> list[str]:
    """Which of `LEVER_DECLARED_KEYS` this leg's config actually arms.

    THE DENOMINATOR FOR THE LEVER-OFF ARM. A run asked to drop `stale_stop`
    against a leg that never declared one produces a base byte-identical to the
    config-exact base — a row that MUST NOT read as "we measured the lever off",
    because nothing was off. Comparing this against the requested set is what
    keeps "we removed it" and "there was nothing to remove" tellable apart.
    """
    return [lev for lev, keys in sorted(LEVER_DECLARED_KEYS.items())
            if any(cfg.get(k) is not None for k in keys)]


def base_args(name: str, cfg: dict, fam: str, data: str, resample: str | None,  # inert: `name` — the leg id, kept because FIVE external callers pass it positionally (m20_flip_replay_sweep, m21_entry_head_round, m20_exit_head_round, m21_entry_sweep, and this module); every arg is built from `cfg`, so dropping it would be a cross-script signature break for no behavioural gain. It affects NOTHING here — do not add a doc claiming otherwise.
              tp_cap_pct: float = 0.0,
              fee_bps_roundtrip: float | None = None,
              min_confidence_override: float | None = None,
              *,
              without_declared_levers: frozenset[str] | None = None) -> list[str]:
    tf = str(cfg.get("timeframe") or "1h")
    sym = (cfg.get("symbols") or ["?"])[0]
    a = ["--data", data, "--symbol", sym, "--timeframe", tf]
    if resample:
        a += ["--resample", resample]
    _drop_keys = frozenset(
        k for lev in (without_declared_levers or ())
        for k in LEVER_DECLARED_KEYS.get(lev, ()))

    def opt(flag, key):
        # The drop is enforced HERE rather than inside `declared_levers()` so a
        # family branch that also emits a lever key cannot route around it. A
        # dropped key is OMITTED, never passed as 0/None: the harness treats an
        # absent flag as "lever not armed", and passing a falsy value would be a
        # different book (an armed lever at a degenerate threshold).
        if key in _drop_keys:
            return
        v = cfg.get(key)
        if v is not None:
            a.extend([flag, str(v)])

    def declared_levers():
        # Config-exact means DECLARED EXIT LEVERS too — a shipped stale/giveback
        # cell is part of the leg's baseline, so a new lever cell is measured
        # ON TOP of it (the structural combo A/B the one-lever-per-leg rule
        # wants). Donchian + pullback harnesses carry these flags.
        #
        # UNLESS the lever-OFF arm asked for one to be removed. That arm exists
        # because the sweep is STRUCTURALLY UNABLE to grade a SHIPPED lever
        # otherwise: a shipped lever is inside this base, so every cell measured
        # against it answers "does this alternative beat the shipped one?" and
        # none answers "is the shipped one worth anything?". 21 live decisions on
        # the coverage matrix rest on pre-TP-parity evidence and are unanswerable
        # without it (BL-20260813-SEVENTEEN-SHIPPED-LEVERS-REST-ON-PRE-TP-PARITY-EVIDENCE).
        opt("--stale-exit-bars", "stale_exit_bars")
        opt("--stale-exit-below-r", "stale_exit_below_r")
        opt("--giveback-min-mfe-r", "giveback_min_mfe_r")
        opt("--giveback-r", "giveback_r")
        opt("--trail-decay-arm-r", "trail_decay_arm_r")
        opt("--trail-decay-stall-bars", "trail_decay_stall_bars")
        opt("--trail-decay-tight-mult", "trail_decay_tight_mult")
        # The VOL-trail lever is a declared exit lever too, and omitting it made
        # this base NOT config-exact on the two census legs that declare one —
        # `trend_donchian_eth` (cold tail, below 0.1 / tight 2.5) and
        # `qqq_pullback_1h` (hot tail, above 0.8 / tight 2.5). Both harnesses
        # have carried the flags all along; only this list was short. Found
        # 2026-08-10 while reading the YAML to write the Tier-3 promotion packet,
        # i.e. AFTER a run whose deltas for those legs were measured against a
        # baseline missing a lever that is armed in live.
        # `vol_pctl_window` above is the ENTRY vol-skip gate's window and is
        # already threaded — which is precisely why the gap was easy to miss:
        # a `vol_*` key was present, just not the trail one.
        opt("--trail-vol-above-pctl", "trail_vol_above_pctl")
        opt("--trail-vol-below-pctl", "trail_vol_below_pctl")
        opt("--trail-vol-tight-mult", "trail_vol_tight_mult")
    if fam == "donchian":
        opt("--donchian", "donchian")
        opt("--atr-period", "atr_period")
        opt("--atr-stop-mult", "atr_stop_mult")
        opt("--trail-mult", "trail_mult")
        opt("--min-confidence", "min_confidence")
        # M21 E-2: a declared confirmation gate is part of the leg's
        # config-exact base, same as the declared exit levers.
        opt("--confirm-bars", "confirm_bars")
        opt("--skip-hours", "skip_hours")
        opt("--vol-skip-above-pctl", "vol_skip_above_pctl")
        opt("--vol-skip-below-pctl", "vol_skip_below_pctl")
        opt("--vol-pctl-window", "vol_pctl_window")
        declared_levers()
        if cfg.get("long_only"):
            a.append("--long-only")
    elif fam == "squeeze":
        for flag, key in (("--bb-period", "bb_period"), ("--bb-std", "bb_std"),
                          ("--kc-mult", "kc_mult"), ("--atr-period", "atr_period"),
                          ("--atr-stop-mult", "atr_stop_mult"),
                          ("--trail-mult", "trail_mult"),
                          ("--timeout-bars", "timeout_bars"),
                          ("--min-confidence", "min_confidence")):
            opt(flag, key)
    elif fam == "fvg":
        for flag, key in (("--range-lookback", "range_lookback"),
                          ("--atr-period", "atr_period"),
                          ("--adx-period", "adx_period"), ("--adx-max", "adx_max"),
                          ("--min-width-pct", "min_width_pct"),
                          ("--max-width-pct", "max_width_pct"),
                          ("--touch-tol-pct", "touch_tol_pct"),
                          ("--min-touches", "min_touches"),
                          ("--third-frac", "third_frac"),
                          ("--fvg-search", "fvg_search"),
                          ("--min-fvg-size-bps", "min_fvg_size_bps"),
                          ("--atr-stop-buffer", "atr_stop_buffer"),
                          ("--exit-style", "exit_style"), ("--tp-r", "tp_r"),
                          ("--timeout-bars", "timeout_bars"),
                          ("--min-confidence", "min_confidence")):
            opt(flag, key)
    elif fam == "scalp":
        # ict_scalp self-loads its detection params from the ict_scalp_5m YAML
        # block (backtest_ict_scalp._load_yaml_params); every ict_scalp leg is a
        # config-exact copy, so only the leg-level knobs need passing. The live
        # monitor trails SL to break-even after 1R, so --sim-breakeven is part of
        # the config-exact base (matches M27 run_symbol_p0.py). tp_at_r / timeout
        # come from YAML / harness defaults.
        a.append("--sim-breakeven")
        opt("--htf-rule", "htf_filter_timeframe")
        opt("--htf-ema-period", "htf_filter_ema_period")
        opt("--min-confidence", "min_confidence")
        declared_levers()
    else:
        # `trend_len` / `pullback_len` were keys NO strategy has ever declared
        # (the YAML says `trend_lookback` / `pullback_lookback`), so `opt` read
        # None, passed no flag, and backtest_pullback fell back to its OWN
        # defaults — 40 / 10 / 0.5. That is silently correct only for a leg that
        # happens to declare those exact values, and 11 of 19 pullback legs do
        # not: spy/qqq/tlt/gld `_1h` are 60/12, the `_1d` metals 15, several use
        # `pullback_frac: 0.618`, `ief_pullback_1d` a 30 trend window.
        # Unlike the trail-vol gap this is ENTRY geometry — it changes which
        # trades exist at all, not just how they exit. Found 2026-08-10 while
        # reading the YAML blocks for the promotion diff.
        opt("--trend-lookback", "trend_lookback")
        opt("--pullback-lookback", "pullback_lookback")
        opt("--pullback-frac", "pullback_frac")
        opt("--atr-period", "atr_period")
        opt("--atr-stop-mult", "atr_stop_mult")
        opt("--trail-mult", "trail_mult")
        opt("--min-confidence", "min_confidence")
        opt("--adx-min", "adx_min")
        # M21 E-2: a declared confirmation gate is config-exact base here too.
        opt("--confirm-bars", "confirm_bars")
        opt("--skip-hours", "skip_hours")
        opt("--vol-skip-above-pctl", "vol_skip_above_pctl")
        opt("--vol-skip-below-pctl", "vol_skip_below_pctl")
        opt("--vol-pctl-window", "vol_pctl_window")
        declared_levers()
    # FEE BAND. Passed through verbatim when set, so a fee-survival A/B measures
    # the SAME base at two cost levels rather than two different books. None means
    # "the harness's own default" and is recorded as such -- never silently stamped
    # as 7.5, because a row that did not declare its fee is not a row measured at
    # the default, it is a row whose fee we did not record.
    if fee_bps_roundtrip is not None:
        a += ["--fee-bps-roundtrip", str(fee_bps_roundtrip)]
    # ENTRY-SELECTIVITY BAND. The surviving thread of SRQ-20260618-003 after the
    # 15bps arm refuted the "fewer, larger-R trades escape the fee band"
    # hypothesis: if halving the trade COUNT does not clear the band, does
    # raising the per-trade EDGE? ict_scalp's confidence is a genuine continuous
    # blend (0.4*body_to_range + 0.3*sweep_depth_atr + 0.3*fvg_size_norm, capped
    # at 1.0), so a floor is a real selectivity axis and not a two-valued switch.
    #
    # REPLACES the cfg-derived floor rather than stacking on it. argparse would
    # take the last of two `--min-confidence` flags and get the same NUMBER, but
    # the recorded command IS the evidence for what a row measured, and a command
    # carrying two contradictory floors cannot be read back as a claim about
    # either. Strip-then-append so the emitted args say exactly one thing.
    if min_confidence_override is not None:
        stripped: list[str] = []
        i = 0
        while i < len(a):
            if a[i] == "--min-confidence":
                i += 2          # drop the flag AND its value
                continue
            stripped.append(a[i])
            i += 1
        a = stripped + ["--min-confidence", str(min_confidence_override)]
    # Live-parity TP: only for families whose live unit actually clamps.
    if tp_cap_pct > 0.0 and fam in LIVE_TP_CAPPED_FAMILIES:
        a += ["--tp-cap-pct", str(tp_cap_pct)]
        tpr = cfg.get("tp_r")
        if tpr is not None:
            a += ["--tp-r", str(tpr)]
    return a


# MIN-OOS-TRADES FLOOR — operator decision 2026-08-11, value 25.
#
# A DENOMINATOR REQUIREMENT, not a fitted threshold (contrast the Path B rate
# floor, measured the same day and REFUSED: `no_separation` on both candidate
# predictors over 604 rows). Chosen from the coverage cost curve, not a fit --
# floor 10 keeps 34 of 51 legs / 27 passes, floor 25 keeps 32 legs / 27 passes,
# floor 50 keeps 20 legs / 7 passes. 10->25 is free (two legs, zero passes) and
# 25->50 is the cliff. Full rationale + the honest limit at the enforcement site.
MIN_OOS_TRADES = 25

REGIME_POLICY_PATH = REPO / "config" / "regime_policy.yaml"

# The three states of "does the LIVE regime gate narrow this leg's book?".
# Deliberately three, not a boolean: "we could not read the policy" and "the
# policy does not name this leg" are opposite statements about our knowledge,
# and collapsing them would let an unreadable file read as "no gating anywhere".
GATE_DELTA_NONE = "none"                    # not named in the policy: base == live
GATE_DELTA_NARROWER = "narrower_live"       # named with an OFF side: live trades LESS
GATE_DELTA_UNKNOWN = "unknown"              # policy unreadable — we did not look


def _policy_off_legs() -> set[str] | None:
    """Strategy names the live regime policy REFUSES in at least one cell.

    None (not an empty set) when the policy cannot be read — an unreadable file
    must not be reported as "no leg is gated".

    The exception list is NARROW on purpose, and `silent-empty-guard` was right to
    reject the broad `except Exception` this started as. Returning `None` made the
    STATE honest ("we did not look") while leaving the CAUSE silent, so a run whose
    policy read failed would stamp every leg `unknown` with nothing on stdout
    saying why — the reader sees a legible state and cannot act on it. Now the
    three realistic failures of "read a file, parse YAML" are caught by type and
    **announced**, and anything else propagates: an unexpected exception here is a
    bug in this function, not a condition to absorb.
    """
    try:
        import yaml
        doc = yaml.safe_load(REGIME_POLICY_PATH.read_text()) or {}
    except (OSError, ImportError, yaml.YAMLError) as exc:
        # LOUD, not silent — the whole point of the guard's objection.
        print(f"  !! regime policy unreadable ({type(exc).__name__}: {exc}) — "
              f"every leg's gate delta will be reported `unknown`, NOT `none`",
              file=sys.stderr)
        return None
    if not isinstance(doc, dict):
        print(f"  !! regime policy is {type(doc).__name__}, expected a mapping — "
              f"gate deltas will be reported `unknown`", file=sys.stderr)
        return None
    off: set[str] = set()

    def scan(strats: object) -> None:
        if not isinstance(strats, dict):
            return
        for name, sides in strats.items():
            if isinstance(sides, dict) and any(v is False for v in sides.values()):
                off.add(str(name))

    for section in ("trending", "transitional", "chop"):
        scan(doc.get(section))
    # trend_vol is nested one level deeper: {trend: {vol: {strategy: sides}}}.
    # isinstance-guarded at BOTH levels so a malformed file degrades to a smaller
    # `off` set rather than raising past the narrow except above.
    trend_vol = doc.get("trend_vol")
    if isinstance(trend_vol, dict):
        for vols in trend_vol.values():
            if isinstance(vols, dict):
                for strats in vols.values():
                    scan(strats)
    return off


def regime_gate_delta(leg: str, off_legs: set[str] | None) -> str:
    """Whether the LIVE hard gate would narrow *leg*'s book vs this sweep's base.

    The sweep runs the harness at its `--regime-router off` default, which sets
    `REGIME_ROUTER_DISABLED=1` — so every base book here is the UNGATED book,
    while the live router is BASELINE-ON. For a leg the policy never names the
    two coincide and the base IS the live book; for a leg with an authored OFF
    cell the base includes trades production refuses, and a base-book LEVEL read
    (`base_net_r`, `base_rate`, and therefore Path B's derived tolerance) is a
    statement about a book the live leg does not trade.
    """
    if off_legs is None:
        return GATE_DELTA_UNKNOWN
    return GATE_DELTA_NARROWER if leg in off_legs else GATE_DELTA_NONE


def inert_giveback_reason(cfg: dict, min_mfe_r: float) -> str | None:
    """Why a giveback rung CANNOT fire on this leg, or None if it can.

    A fixed-bracket leg exits at ``tp_at_r``. A giveback rung that requires
    peak open profit >= tp_at_r therefore needs the trade to be alive at an R
    the bracket already closed — and the harness's exit order settles it: the
    TP check RETURNS before the giveback block is reached
    (``backtest_ict_scalp._simulate_exit``), so the rung is a PROVABLE no-op,
    not merely a rare one. Same reasoning the harness already documents for
    ``--bank-at-r`` ("a rung at bank_at_r >= tp_at_r is a provable no-op").

    MEASURED 2026-08-10: every live ict_scalp leg is ``tp_at_r: 1.5`` and the
    grid emitted ``gb1R_afterMFE2R`` for all of them. Three legs reported it at
    EXACTLY 0.0 on net_R, maxDD and capital/day across both windows, under the
    gate reason ``tie_no_improvement`` — which reads as "we measured it and it
    made no difference". It was never measurable. That is the cosmetic-cell
    anti-pattern (`BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS`): a row that
    occupies a line in the verdict table, consumes two harness runs per window,
    and answers a question nobody can act on.

    Returning the REASON rather than a bool is the point — the cell is recorded
    as skipped-and-why, never silently dropped, so "not run" stays
    distinguishable from "run and flat".
    """
    tp = cfg.get("tp_at_r")
    try:
        tp = float(tp) if tp is not None else None
    except (TypeError, ValueError):
        return None
    if tp is None or tp <= 0:
        return None  # no fixed bracket declared — the rung is reachable
    if min_mfe_r >= tp:
        return (f"provable_noop: giveback arms at MFE>={min_mfe_r:g}R but the leg "
                f"takes profit at tp_at_r={tp:g}R, and the harness's TP check "
                f"returns before the giveback block")
    return None


CLI_FLAG_FOR_CFG_KEY = {k: "--" + k.replace("_", "-")
                        for keys in LEVER_DECLARED_KEYS.values() for k in keys}


def shipped_lever_cells(cfg: dict,
                        dropped: list[str]) -> list[tuple[str, str, list[str]]]:
    """The lever-OFF arm's cells: re-apply each DROPPED lever at the leg's OWN
    declared values, on top of a base that no longer carries it.

    This inverts what a normal cell asks. Normally the base HAS the shipped
    lever and a cell asks "does this alternative beat it?" — a question that
    cannot grade the shipped lever itself. Here the base has it OFF and the cell
    puts it back at exactly the live values, so the delta the sweep already
    computes (`d_net_r`, `d_max_dd`, Path A `beats()`, the walk-forward) becomes
    a direct verdict on the SHIPPED cell.

    The tag is prefixed `shipped_` so a corpus reader can never mistake one of
    these for an alternative-lever cell; the values are appended to the tag so
    two legs' rows are not conflated by a bare lever name.
    """
    out: list[tuple[str, str, list[str]]] = []
    for lever in dropped:
        extra: list[str] = []
        parts: list[str] = []
        for key in LEVER_DECLARED_KEYS[lever]:
            v = cfg.get(key)
            if v is None:
                continue
            extra += [CLI_FLAG_FOR_CFG_KEY[key], str(v)]
            parts.append(f"{v:g}" if isinstance(v, (int, float)) else str(v))
        if extra:
            out.append((f"shipped_{lever}_" + "_".join(parts), lever, extra))
    return out


def cells_for(cfg: dict, fam: str | None = None,
              skipped: list | None = None,
              *,
              without_declared_levers: frozenset[str] | None = None,
              ) -> list[tuple[str, str, list[str]]]:
    """(cell_tag, matrix_lever, extra_args). Config-exact base is implied.

    ``skipped``, when given, collects ``{cell, lever, reason}`` for every cell
    withheld as structurally inert, so the run reports what it did NOT ask as
    well as what it did.

    ``without_declared_levers`` switches the function into the LEVER-OFF ARM and
    emits ONLY the `shipped_*` revalidation cells. The alternative cells are
    withheld deliberately: measured against a base whose shipped lever has been
    removed, they answer a different question than the same tag does in a normal
    run, and two rows carrying one tag while measuring two books is the exact
    provenance failure the run-level identity fields exist to prevent. Sweep the
    alternatives in a normal run; use this arm to grade what is already live.
    """
    if without_declared_levers:
        dropped = [lev for lev in declared_levers_present(cfg)
                   if lev in without_declared_levers]
        cells = shipped_lever_cells(cfg, dropped)
        if skipped is not None and not cells:
            skipped.append({
                "cell": None, "lever": None,
                "reason": "no_declared_lever_to_drop:"
                          + ",".join(sorted(without_declared_levers))})
        return cells
    out = [
        ("stale8_lt0R", "stale_stop", ["--stale-exit-bars", "8"]),
        ("stale12_lt0R", "stale_stop", ["--stale-exit-bars", "12"]),
    ]
    for tag, min_mfe in (("gb1R_afterMFE1R", 1.0), ("gb1R_afterMFE2R", 2.0)):
        reason = inert_giveback_reason(cfg, min_mfe)
        if reason:
            if skipped is not None:
                skipped.append({"cell": tag, "lever": "giveback_stop",
                                "reason": reason})
            continue
        out.append((tag, "giveback_stop",
                    ["--giveback-min-mfe-r", f"{min_mfe:g}",
                     "--giveback-r", "1.0"]))
    # INTRABAR BREAK-EVEN ARMING — scalp only, because it is the only family
    # whose live monitor ratchets to break-even at all (monitor_breakeven_sl;
    # the trail families have no such ratchet to re-base).
    #
    # A CELL, not a base variant, deliberately: routing it through the normal
    # grid gives it the full gate — config-exact base A/B, IS/OOS, and the
    # yearly walk-forward — instead of a bespoke two-arm comparison that would
    # skip generalisation. The 2026-08-10 sweep is a standing reminder of why
    # that matters: five cells passed BOTH windows and still failed the
    # walk-forward.
    #
    # The prize is bounded and small (24 near-miss trades across all 7 scalp
    # legs, ceiling 60R = 3.70% of gross from clean target hits), and the COST
    # is the open question — arming on a touch also scratches trades that dip
    # and recover. On a 4-trade smoke sample it converted one timeout to a
    # be_stop for -0.022R, which proves the lever is live and settles nothing.
    if fam == "scalp":
        out.append(("be_touch_arm", "stale_stop", ["--be-arm-on-touch"]))
    tm = cfg.get("trail_mult")
    if tm is not None:
        t = float(tm)
        for d in (-1.0, 1.0):
            nt = t + d
            if nt >= 1.5:
                out.append((f"trail{nt:g}", "trail_geometry",
                            ["--trail-mult", str(nt)]))
    # M20 P4.1 trail-decay cells (momentum-exhaustion design § 2): tighten the
    # trail once the move is R-armed and/or stalls. Only for families whose
    # harness carries the lever (trend/pullback); tight mult scales off the
    # leg's own base trail (half, floored at 1.5) so cells stay config-relative.
    if tm is not None and fam in ("donchian", "pullback"):
        tight = max(1.5, round(float(tm) / 2.0, 1))
        decay = [
            (f"decay_arm2R_t{tight:g}",
             ["--trail-decay-arm-r", "2.0"]),
            (f"decay_stall6_t{tight:g}",
             ["--trail-decay-stall-bars", "6"]),
            (f"decay_stall10_t{tight:g}",
             ["--trail-decay-stall-bars", "10"]),
            (f"decay_arm1.5R_stall6_t{tight:g}",
             ["--trail-decay-arm-r", "1.5", "--trail-decay-stall-bars", "6"]),
        ]
        for tag, extra in decay:
            out.append((tag, "trail_decay",
                        extra + ["--trail-decay-tight-mult", str(tight)]))
        # M20-X vol-conditional trail cells (regime-conditional exits § 1):
        # tighten the trail on bars whose trailing ATR percentile is in the
        # gated tail. Same config-relative tight mult as the decay cells.
        # Design: docs/research/M20X-vol-conditional-trail-DESIGN.md.
        vt = [
            (f"vt_hot90_t{tight:g}", ["--trail-vol-above-pctl", "0.9"]),
            (f"vt_hot80_t{tight:g}", ["--trail-vol-above-pctl", "0.8"]),
            (f"vt_cold10_t{tight:g}", ["--trail-vol-below-pctl", "0.1"]),
        ]
        for tag, extra in vt:
            out.append((tag, "vol_trail",
                        extra + ["--trail-vol-tight-mult", str(tight)]))
    return out


def winner_mfe_p80(harness: str, base: list[str], split: str) -> float | None:
    """P80 of the WINNER-trade MFE distribution over the IS window only
    (M20 P4.4 — the percentile arm is baked from train-window trades so the
    OOS verdict never sees test data; the by_year folds inside IS carry the
    one-scalar caveat, recorded in the cell tag). None when < 30 winners.

    MFE IS READ VIA `exit_capture.mfe_r_of`, NOT `row["mfe_r"]`. This function
    used to do the top-level read, so for every `ict_scalp` leg — whose harness
    nests `mfe_r` under `meta` — it collected zero MFEs and returned `None`,
    which its own contract above declares to mean "fewer than 30 winners".
    Measured 2026-08-10: `ict_scalp_avax_5m` has 1,102 trades and the arm
    reported the not-enough-winners answer. An inert percentile arm that says
    "insufficient data" is worse than one that errors, because the caller
    records a legitimate-looking abstention. `winners_seen` is now counted
    SEPARATELY from `mfes` so the two causes stay distinguishable in the log.
    """
    tmp = "/tmp/m20_p80_emit.jsonl"
    Path(tmp).unlink(missing_ok=True)
    cmd = [sys.executable, str(REPO / harness), *base,
           "--emit-trades", tmp, "--json", "/tmp/m20_p80_metrics.json",
           "--end", split]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if p.returncode != 0:
            print(f"    p80: harness rc={p.returncode} — no percentile arm")
            return None
        mfes, winners_seen = [], 0
        for line in Path(tmp).read_text().splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            if float(t.get("net_r") or 0) <= 0:
                continue
            winners_seen += 1
            m = exit_capture.mfe_r_of(t)
            if m is not None:
                mfes.append(m)
        if winners_seen and not mfes:
            # The distinguishing branch: winners EXIST and none carried a
            # readable MFE. That is a harness/reader shape mismatch, not a
            # thin sample, and it must never masquerade as one.
            print(f"    p80: {winners_seen} winners, 0 readable mfe_r — "
                  "arm UNAVAILABLE (shape mismatch), not 'thin sample'")
            return None
        if len(mfes) < 30:
            print(f"    p80: {len(mfes)} winner MFEs (< 30) — thin sample")
            return None
        mfes.sort()
        return round(mfes[int(0.8 * (len(mfes) - 1))], 2)
    except (OSError, json.JSONDecodeError, ValueError, TypeError,
            subprocess.TimeoutExpired) as exc:
        # NARROW deliberately (silent-empty-guard, 2026-08-10). The broad
        # `except Exception` this replaces would have reported a CODE defect —
        # say an AttributeError from a refactor of `exit_capture.mfe_r_of` —
        # as "p80 unavailable", i.e. as a data condition. That is the same
        # mislabelling this function's own docstring was written to fix: an
        # inert arm that returns a legitimate-looking abstention is worse than
        # one that errors, because the caller records the abstention as a
        # measurement. These five are the failures the I/O and parsing can
        # actually produce; anything else propagates and stops the sweep.
        print(f"    p80: unavailable ({type(exc).__name__}: {exc})")
        return None


def leg_target_r(cfg: dict) -> float | None:
    """The leg's FIXED R target, or None for a trail-exit leg.

    `ict_scalp` declares `tp_at_r`; `fade`/`fvg_range` declare `tp_r`. A
    donchian/pullback/squeeze leg exits on its trail and has **no** target — it
    cannot "nearly reach" one, so this returns None and every near-miss figure
    downstream is None rather than a reassuring 0%.
    """
    for key in ("tp_at_r", "tp_r"):
        try:
            v = float(cfg.get(key))
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return None


def run_census(harness: str, args: list[str], target_r: float | None,
               start=None, end=None) -> dict:
    """Config-exact base run with --emit-trades, summarised by exit_capture.

    Measures the CURRENT live exit geometry — no lever applied. This is the
    "how bad is it, and where" pass that has to precede designing a lever, so
    the design is driven by a distribution instead of by one remembered trade.
    """
    tmp_json, tmp_trades = "/tmp/m20_census.json", "/tmp/m20_census_trades.jsonl"
    Path(tmp_trades).unlink(missing_ok=True)
    cmd = [sys.executable, str(REPO / harness), *args,
           "--json", tmp_json, "--emit-trades", tmp_trades]
    if start:
        cmd += ["--start", start]
    if end:
        cmd += ["--end", end]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    if p.returncode != 0:
        return {"error": (p.stderr or p.stdout)[-250:]}
    rows = []
    try:
        for line in Path(tmp_trades).read_text().splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as exc:
        # Distinguish "the harness produced no trades" from "we could not read
        # what it produced" — collapsing them would report a silent empty as a
        # measured zero.
        return {"error": f"emit_read: {exc}"}
    return exit_capture.summarize(rows, target_r=target_r)


def resolve_split(harness: str, base: list[str], mode: str,
                  fixed_split: str, target_oos: int) -> tuple[str, dict]:
    """The IS/OOS boundary as a DATE, optionally derived from the leg's own
    trade distribution.

    WHY. A FIXED calendar split silently makes a strategy's TRADE FREQUENCY
    decide whether it can be graded. Measured 2026-08-13: at the corpus-standard
    2025-07-01 the 1d equity legs land **OOS n=3-6** against a floor of 25 — not
    because the leg is bad but because it trades ~20x/year, so a fixed date buys
    it a handful of trades. Their lifetimes are 33-79 trades, so 6 of the 7 can
    support a 25-trade OOS window; the date just was not placed to give them one.
    Same defect the E1 fold cut had (fold_blocks in train_exit_head.py).

    `oos-trades` runs the base over FULL history, reads the entry timestamps,
    and returns the date that leaves ~`target_oos` trades after it.

    ⚠️ THE TARGET IS NOT THE ACHIEVED COUNT, AND THIS FUNCTION DOES NOT CLAIM IT
    IS. The harness windows CANDLES, not trades, so an OOS run starting at the
    derived date needs warmup and may produce slightly different trades near the
    boundary. The authority on what OOS actually contained stays the measured
    `_base_oos_n` the caller already checks against MIN_OOS_TRADES — this only
    places the boundary better. Returned meta records target AND mode so a
    verdict states its own derivation.

    When the leg cannot support the requested target, the target is CLAMPED to
    the largest it can support (`lifetime // 2`) and the boundary is still
    derived from this leg's own trades — recorded as
    `split_target_clamped_from`/`_to`, never silent, because a verdict must not
    report a target it did not use.

    Falls back to the fixed date, WITH A STATED REASON, only when the leg
    cannot seat `MIN_OOS_TRADES` on BOTH sides (a 33-trade leg giving 25 to OOS
    leaves 8 for IS, which fits nothing) — that leg is ungradeable at any
    boundary, so there is nothing better to return. Never silently returns the
    fixed date.

    THE THREE OUTCOMES STAY DISTINGUISHABLE, which is the whole point:
    `split_fallback` unset + no clamp = derived at the asked-for target ·
    clamp keys present = derived at a REDUCED target (we looked and the leg is
    thin) · `split_fallback="leg_too_thin"` = ungradeable at any target ·
    `split_fallback` in {`harness_rc`, `emit_unreadable`} = **we could not
    look**, which is a different statement from either and must never be read
    as thinness.
    """
    meta = {"split_mode": mode, "split_target_oos": target_oos}
    if mode == "date":
        meta["split"] = fixed_split
        return fixed_split, meta

    tmp = "/tmp/m20_split_emit.jsonl"
    Path(tmp).unlink(missing_ok=True)
    cmd = [sys.executable, str(REPO / harness), *base,
           "--emit-trades", tmp, "--json", "/tmp/m20_split_metrics.json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if p.returncode != 0:
            meta.update(split=fixed_split, split_fallback="harness_rc",
                        split_detail=(p.stderr or p.stdout)[-200:])
            return fixed_split, meta
        stamps = []
        for line in Path(tmp).read_text().splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            et = t.get("entry_time")
            if et:
                stamps.append(str(et))
        stamps.sort()
    except (OSError, json.JSONDecodeError, ValueError, TypeError,
            subprocess.TimeoutExpired) as exc:
        meta.update(split=fixed_split, split_fallback="emit_unreadable",
                    split_detail=str(exc)[:200])
        return fixed_split, meta

    meta["split_lifetime_trades"] = len(stamps)
    # Require IS to be at least as large as OOS. A leg that cannot give both
    # sides `target_oos` trades is genuinely too thin, and moving the date
    # would only trade one unusable window for another.
    #
    # BUT "cannot support THIS target" is not "cannot be graded", and until
    # 2026-08-14 those were the same branch: the guard returned the fixed
    # CALENDAR date, which for exactly the low-frequency legs that trip it is
    # the worst available boundary -- the very defect the derivation exists to
    # remove. Measured on one dispatch pair, same legs, same geometry, target
    # 25 -> 35:
    #
    #     iwm_trend_long_1d   OOS 24 -> 4      scha  OOS 23 -> 5
    #     splg_trend_long_1d  OOS 24 -> 4      (eth_prop, 900+ trades: 24 -> 33)
    #
    # Asking for MORE out-of-sample trades returned six times FEWER. That is a
    # cliff, not a degradation, and it fires precisely when the caller asks for
    # more rigour. Tracked as:
    # BL-20260814-SPLIT-DERIVATION-FALLBACK-IS-A-CLIFF-SO-ASKING-FOR-MORE-OOS-RETURNS-FAR-FEWER
    # (kept on ONE line even though it overruns: artifact-validity-guard
    # resolves ids by regex, so a wrapped id silently resolves to NOTHING and
    # the comment claims tracking that does not exist.)
    #
    # So CLAMP to the largest target the leg can actually support and keep
    # deriving from the leg's own trades. The clamp is RECORDED, never silent:
    # a verdict must not report a target it did not use. The fixed-date
    # fallback survives for the one case it is right for -- a leg that cannot
    # seat MIN_OOS_TRADES on both sides is ungradeable at any boundary, and
    # there is nothing better to return.
    if len(stamps) < 2 * target_oos:
        supportable = len(stamps) // 2
        if supportable >= MIN_OOS_TRADES:
            meta["split_target_clamped_from"] = target_oos
            meta["split_target_clamped_to"] = supportable
            target_oos = supportable
        else:
            meta.update(split=fixed_split, split_fallback="leg_too_thin")
            return fixed_split, meta

    boundary = stamps[-target_oos][:10]          # YYYY-MM-DD
    meta["split"] = boundary
    return boundary, meta


def insufficient_base_reason(base_oos_n, floor: int, split: str,
                             split_meta: dict) -> str:
    """Why a cell was refused for a thin OOS window -- INCLUDING which window.

    The old message was `f"OOS base {n} trades < floor {floor}"`, which names a
    COUNT over a window it does not name. That reads as a statement about the
    LEG ("this strategy has 24 trades") when it can equally be a statement about
    the BOUNDARY ("the derivation handed this 407-trade leg a 24-trade window").
    Those are opposite conditions with opposite remedies -- wait for trades, vs
    move the split -- and both were printing the same sentence.

    Measured 2026-08-14 on the two legs that motivated this
    (BL-20260814-SPLIT-TARGETS-EXACTLY-THE-FLOOR-SO-BOUNDARY-LOSS-ALWAYS-FAILS):
    `htf_pullback_trend_2h` refused at n=24 under the derived split and graded
    at n=95 under the corpus-standard one, same config, same day. Nothing in the
    refusal said which split produced the 24, so establishing that took a fresh
    trainer relay run rather than a read.

    Pure and side-effect-free so it can be tested directly -- the verdict block
    that calls it lives inside `main()` and is not otherwise reachable from a
    test. It composes a STRING and nothing more; no caller branches on it.
    """
    mode = split_meta.get("split_mode")
    lifetime = split_meta.get("split_lifetime_trades")
    fallback = split_meta.get("split_fallback")
    parts = [f"window from {split}", f"split_mode={mode}"]
    # The target is meaningless under `date` (nothing was targeted), so it is
    # omitted rather than printed as None -- a None target would read as a
    # derivation that failed rather than one that never ran.
    if mode != "date":
        parts.append(f"targeting {split_meta.get('split_target_oos')}")
    # `lifetime` is the discriminator the whole message exists for: it is what
    # separates a trade-starved leg from a badly-placed boundary. It is absent
    # under `date` (no emit run happened), and absence is reported by OMISSION
    # rather than a fabricated 0 -- "we did not count the leg's lifetime" and
    # "the leg has no trades" are opposite claims.
    if lifetime is not None:
        parts.append(f"leg lifetime {lifetime}")
    # A clamp means the caller's target was NOT the one used. Printing only the
    # requested target beside a refusal invites the reader to compute a band the
    # run never operated in.
    if split_meta.get("split_target_clamped_to") is not None:
        parts.append(f"target CLAMPED {split_meta.get('split_target_clamped_from')}"
                     f"->{split_meta['split_target_clamped_to']}")
    if fallback:
        parts.append(f"FELL BACK: {fallback}")

    # THE DIAGNOSIS, not just the inputs (criterion (4) of
    # BL-20260814-SPLIT-DERIVATION-FALLBACK-IS-A-CLIFF-SO-ASKING-FOR-MORE-OOS-RETURNS-FAR-FEWER).
    # Carrying `lifetime` made the discriminator AVAILABLE; it still left every
    # reader to do the arithmetic, and the two conclusions have opposite
    # remedies. A leg that cannot seat `floor` on BOTH sides is trade-starved at
    # ANY boundary -- re-running is wasted. A leg whose lifetime could seat one
    # and did not got a badly-placed split -- waiting for trades is wasted.
    # Third state kept distinct on purpose: under `split_mode=date` no emit run
    # happened, so the lifetime was never counted and the honest answer is that
    # we cannot tell -- NOT a default to either diagnosis.
    if lifetime is None:
        verdict = ("UNDIAGNOSED: leg lifetime was never counted under "
                   "split_mode=date, so 'thin leg' and 'thin window' are "
                   "indistinguishable here -- re-run with split_mode=oos-trades "
                   "to find out")
    elif lifetime < 2 * floor:
        verdict = (f"THE LEG IS TRADE-STARVED: {lifetime} lifetime trades cannot "
                   f"seat {floor} on BOTH sides at any boundary -- re-running "
                   f"the sweep returns this again; wait for trades")
    else:
        verdict = (f"THE BOUNDARY IS MISPLACED, NOT THE LEG: {lifetime} lifetime "
                   f"trades could seat a floor-clearing window and this split "
                   f"gave it {base_oos_n} -- re-run with a larger "
                   f"--split-target-oos")
    return (f"OOS base {base_oos_n} trades < floor {floor} "
            f"({', '.join(parts)}) -- {verdict}")


def run_cell(harness: str, args: list[str], start=None, end=None) -> dict:
    """One harness run, memoized on its full invocation.

    The memo is not a micro-optimization; it removes an O(candidates) blow-up
    that the scalp family is the first leg to actually hit. The walk-forward
    re-runs the leg's BASE for every fold of every candidate, and those base
    fold runs are byte-identical across candidates — so a leg with five
    candidates paid for the same six base folds five times. Measured cost of
    that redundancy on an ict_scalp 5m leg (2026-08-10 census timings: ~16 min
    per full-history run): up to eight extra full-history-equivalents, i.e.
    more than two hours per leg of recomputing a known answer.

    Correctness rests on the runs being deterministic in their inputs, which
    they are — same harness, same argv, same window, same frame. A shallow copy
    is handed out so a caller that annotates a result cannot poison the entry
    for the next reader.
    """
    key = (harness, tuple(args), start, end)
    hit = _CELL_CACHE.get(key)
    if hit is not None:
        return dict(hit)
    tmp = "/tmp/m20_fleet_cell.json"
    cmd = [sys.executable, str(REPO / harness), *args, "--json", tmp]
    if start:
        cmd += ["--start", start]
    if end:
        cmd += ["--end", end]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=CELL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        # Deliberately NOT cached: a timeout is "we did not finish looking",
        # not a measured result, and caching it would make one slow run
        # permanent for the rest of the process.
        return {"error": f"timeout after {CELL_TIMEOUT_S:.0f}s"}
    if p.returncode != 0:
        return {"error": (p.stderr or p.stdout)[-250:]}
    try:
        out = json.loads(Path(tmp).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"json: {exc}"}
    _CELL_CACHE[key] = out
    return dict(out)


# What counts as an out-of-sample window too thin for its verdict to be
# comparable to a full one. REPORTING ONLY — nothing gates on it. Set at 20
# because the 2026-08-10 fleet sweep produced Path A PASSes on OOS windows of
# 3, 4 and 5 trades, and the daily legs cluster there while the hourly ones sit
# in the hundreds; 20 separates those two populations without pretending to be
# a statistical threshold. It is a LABEL, not a gate.
_THIN_OOS_TRADES = 20


def beats(cell: dict, base: dict) -> bool:
    """net_R AND maxDD both no worse (strict net_R improvement OR dd improvement).

    This is **Path A** of the exit-refinement gate, and it is deliberately
    unchanged here. Note what it implies for a capital-releasing lever: a cell
    that frees capital while giving up a little net_R fails `cn >= bn` and
    short-circuits to `is_oos_fail` BEFORE any walk-forward runs. That is the
    documented reason every pullback `stale_stop` cell in the coverage matrix
    reads `honest_negative` off the 2026-07-12 fleet sweep — the gate could not
    see the axis. Path B (§ P2 of the exit-refinement skill) exists for exactly
    that population; its two thresholds are UNSET pending the distribution this
    sweep now reports, so nothing here grades against them.
    """
    try:
        cn, bn = float(cell["net_total_r"]), float(base["net_total_r"])
        cd, bd = float(cell["max_drawdown_r"]), float(base["max_drawdown_r"])
    except (KeyError, TypeError, ValueError):
        return False
    return cn >= bn and cd <= bd and (cn > bn or cd < bd)


def drawdown_exchange_rate(cell: dict, base: dict) -> dict:
    """Does the cell buy drawdown at a better rate than the book already pays?

    THE PATH B THRESHOLD, DERIVED RATHER THAN CHOSEN (operator directive
    2026-08-10: "let's get an evidence based number for the drawdown tolerance
    instead of guessing").

    The design note left both Path B thresholds unset, which was right — but the
    thing to avoid is not just an ARBITRARY scalar, it is a scalar at all. A
    single fleet-wide "accept up to +X R of drawdown" cannot be evidence-based,
    because the legs are not commensurable: a leg whose base book earns 40R
    against a 12R drawdown is being asked a different question from one earning
    4R against 9R, and one number answers both wrongly. It also collides with
    the per-leg direction the sweep's own heterogeneity forces (be_touch_arm:
    +10.72R on ict_scalp_sol_5m, -11.18R on ict_scalp_avax_5m).

    So the criterion is a RATE, not an allowance, and it carries no tunable:

        the cell may deepen drawdown only if net_R per unit of drawdown
        does not get worse ==>  N_c / D_c  >=  N_b / D_b

    The book's own realised ratio is the evidence. A cell clearing it buys
    drawdown at least as cheaply as the strategy already does, in the currency
    the strategy already trades in; a cell failing it is asking the operator to
    accept a worse exchange rate than the status quo, which is a decision no
    threshold should smuggle through.

    ``allowed_d_max_dd`` is that rate expressed back as an allowance for THIS
    leg — ``D_b * (d_net_r / N_b)`` — so the operator can read the implied
    tolerance per leg beside the drawdown the cell actually asks for. That is
    the "evidence-based number": measured per leg from its own base, not chosen
    once for the fleet.

    EQUIVALENT MARGINAL FORM (algebraically identical, and the more intuitive
    reading): ``N_c/D_c >= N_b/D_b`` rearranges to ``ΔN/ΔD >= N_b/D_b`` — the
    net_R bought at the MARGIN must beat the rate the book earns on AVERAGE.

    STATED PROPERTY, not a hidden one: the criterion is strict on an efficient
    book and permissive on an inefficient one, because a poor average rate is a
    low bar to clear. Worked, on the same +1.0R-for-+2.0R ask (marginal 0.50):

        base 40R / 12R dd (rate 3.33) -> REJECT (allowed +0.30, headroom -1.70)
        base  4R /  9R dd (rate 0.44) -> PASS   (allowed +2.25, headroom +0.25)

    A fleet-wide "+2R of drawdown is acceptable" scalar passes BOTH. Whether the
    permissive half is desirable is an operator judgement — one could add a floor
    on the base rate, but that reintroduces exactly the free parameter this
    avoids, so it is left unset and surfaced rather than decided here.

    Compared by cross-multiplication rather than division (both drawdowns are
    positive magnitudes — ``mdd = max(mdd, peak - cum)``), so no ratio has to be
    formed to make the decision.

    UNGRADEABLE IS ITS OWN ANSWER, never a pass:
      * ``base_unprofitable`` (N_b <= 0) — a book that loses money has no
        exchange rate worth preserving, and "improved a negative ratio" is not a
        statement about drawdown at all.
      * ``base_no_drawdown`` (D_b <= 0) — nothing to scale the allowance from.
      * ``unreadable`` — a missing field is not a failed comparison.
    """
    out: dict = {
        "passes": None, "reason": None,
        "base_net_r": None, "base_max_dd": None,
        "cell_net_r": None, "cell_max_dd": None,
        "d_net_r": None, "d_max_dd": None,
        "allowed_d_max_dd": None, "headroom": None,
    }
    try:
        n_c, n_b = float(cell["net_total_r"]), float(base["net_total_r"])
        d_c, d_b = float(cell["max_drawdown_r"]), float(base["max_drawdown_r"])
    except (KeyError, TypeError, ValueError):
        out["reason"] = "unreadable"
        return out
    out.update({"base_net_r": round(n_b, 4), "base_max_dd": round(d_b, 4),
                "cell_net_r": round(n_c, 4), "cell_max_dd": round(d_c, 4),
                "d_net_r": round(n_c - n_b, 4), "d_max_dd": round(d_c - d_b, 4)})
    if n_b <= 0:
        out["reason"] = "base_unprofitable"
        return out
    if d_b <= 0:
        out["reason"] = "base_no_drawdown"
        return out
    # ---------------------------------------------------------- THE GRANT CAP
    #
    # Operator-approved 2026-08-11 (Tier-3). `allowed = D_b x (dN / N_b)` is a
    # FRACTION of the base book's entire drawdown, and the fraction is unbounded
    # above: measured over the 604-row corpus, 31 rows are entitled to MORE than
    # the whole base drawdown, the largest at 1.70x (`tlt_pullback_1h trail4`).
    # Past 1.0 the allowance has stopped being a share of the book's risk budget
    # and become an expansion of it, so the cap is `dN/N_b <= 1.0` — structural,
    # the point where a share becomes an expansion, NOT a fitted parameter.
    #
    # ⚠️ HOW TO READ THIS — the cap is easy to misread in three ways:
    #
    #  1. IT CAPS THE ENTITLEMENT, NEVER THE ASK. A cell asking for less than
    #     the cap is untouched. `grant_capped: true` does NOT mean "this cell was
    #     too risky" — it means "its entitlement was absurd; its actual ask may
    #     well have been fine." Most capped rows IMPROVE drawdown.
    #  2. IT CHANGES ZERO VERDICTS ON THE MEASURED POPULATION, and that is not a
    #     defect. Of the 31 over-entitled rows, ZERO actually ask for more
    #     drawdown than D_b (largest real ask among them: +0.78R against a
    #     15.35R base). It is PROPHYLACTIC — a bound on a future cell, not a
    #     correction of a present one. An earlier version of this
    #     recommendation claimed it "binds 1 of 18 rows"; that was WRONG, and
    #     shipping it on that claim would have been a risk control that controls
    #     nothing, sold as one that does.
    #  3. A `grant_ratio > 1.0` ROW IS NOT A FAILING ROW. Read `passes`.
    #
    # The cap enters `passes`, not just the reported allowance — clamping only
    # the printed number while the decision used the uncapped one would be a
    # diagnostic that describes a policy the code does not apply.
    grant_ratio = (n_c - n_b) / n_b
    allowed_uncapped = d_b * grant_ratio
    allowed = min(allowed_uncapped, d_b)
    asked = d_c - d_b
    out["grant_ratio"] = round(grant_ratio, 4)
    out["allowed_d_max_dd_uncapped"] = round(allowed_uncapped, 4)
    # Strict `<`: a row exactly at the cap is not "capped", it is at the bound.
    out["grant_capped"] = bool(allowed < allowed_uncapped)
    out["allowed_d_max_dd"] = round(allowed, 4)
    # Positive headroom = the cell asks for LESS drawdown than its net_R gain
    # entitles it to at the book's own rate, AFTER the cap.
    out["headroom"] = round(allowed - asked, 4)
    # The rate test by cross-multiplication (no ratio formed), AND the cap. The
    # rate half is unchanged; `asked <= d_b` is the new conjunct.
    out["passes"] = ((n_c * d_b) >= (n_b * d_c)) and (asked <= d_b)
    if not out["passes"] and (n_c * d_b) >= (n_b * d_c):
        # Name WHICH half refused, so a cap refusal is never read as a rate
        # refusal — they call for opposite follow-ups.
        out["reason"] = "grant_exceeds_base_drawdown"
    return out


def is_path_b_candidate(g_is: dict, g_oos: dict, cap_oos: dict) -> bool:
    """net_R up on BOTH windows and capital/day up, but Path A said no.

    The population Path B exists for: a cell that makes more R per unit of
    capital-time and pays for it in drawdown. Deliberately does NOT look at
    drawdown — that is the axis the operator's (still unset) tolerance governs,
    and gating on it here would make Path B unreachable by construction.

    Requires BOTH windows positive on net_R. A cell positive only
    out-of-sample is the small-window artifact, not a trade-off: measured
    2026-08-10 on ict_scalp_xrp_5m, `stale8_lt0R` read IS -13.25R / OOS +9.06R
    — the best OOS cell in the sweep and a 22R swing between adjacent periods.
    """
    def _up(v):
        return v is not None and v > 0
    return (_up(g_is.get("d_net_r")) and _up(g_oos.get("d_net_r"))
            and _up(cap_oos.get("d_net_r_per_capital_day")))


def walkforward(harness: str, base_args_: list, cell_args: list,
                log, leg: str, tag: str, *, require_dd: bool) -> dict:
    """Yearly folds. Returns {wins, usable, folds:[...], summary}.

    ``require_dd`` is the whole reason this is a function rather than the
    inline loop it replaces. Path A's walk-forward demands net_R no worse AND
    maxDD no worse per fold. Applying that to a Path B candidate would reject
    it in every fold BY CONSTRUCTION — trading drawdown for net_R is what makes
    it Path B — so the walk-forward would answer a question the cell never
    claimed to pass, and the ~2/3 tally would be a fabricated negative.

    For Path B the fold test is the cell's OWN claim (net_R no worse), and the
    drawdown delta is RECORDED per fold rather than gated. That way the
    operator sets a tolerance against a measured distribution of the cost,
    which is the same discipline `capital_efficiency` and the exposure ceiling
    already follow: measure the axis first, threshold it second.
    """
    wins = usable = 0
    folds = []
    for fname, fs, fe in FOLDS:
        fb = run_cell(harness, base_args_, start=fs, end=fe)
        fc = run_cell(harness, cell_args, start=fs, end=fe)
        log({"leg": leg, "cell": f"{tag}@wf{fname}", "window": "fold",
             "base": fb, "lever": fc})
        if "error" in fb or "error" in fc:
            folds.append({"fold": fname, "usable": False,
                          "why": fb.get("error") or fc.get("error")})
            continue
        usable += 1
        try:
            d_net = float(fc["net_total_r"]) - float(fb["net_total_r"])
            d_dd = float(fc["max_drawdown_r"]) - float(fb["max_drawdown_r"])
        except (KeyError, TypeError, ValueError):
            folds.append({"fold": fname, "usable": True, "ok": False,
                          "why": "unreadable"})
            continue
        ok = d_net >= 0 and (d_dd <= 0 or not require_dd)
        wins += 1 if ok else 0
        folds.append({"fold": fname, "usable": True, "ok": ok,
                      "d_net_r": round(d_net, 4), "d_max_dd": round(d_dd, 4)})
    return {"wins": wins, "usable": usable, "folds": folds,
            "summary": f"{wins}/{usable}"}


def beats_detail(cell: dict, base: dict) -> dict:
    """WHY a cell passed or failed `beats`, per window — the half the report was
    missing (2026-08-10).

    `beats` is a bool over TWO windows and TWO axes, and the sweep recorded only
    the collapsed label `is_oos_fail` plus an OOS-ONLY capital table. Measured
    that day, that combination was unreadable: 18 cells showed a POSITIVE OOS
    capital delta AND a positive OOS net_R while carrying `is_oos_fail`, and
    nothing in the artifact could say whether they died on IS net_R, IS maxDD, or
    OOS maxDD. "Improved out-of-sample, failed in-sample" and "improved both,
    worsened drawdown" are opposite findings — the first is the classic
    small-window artifact, the second is a real trade-off — and a Path B
    threshold set without separating them would be set on half the evidence.

    Returns the per-window deltas plus a `reason` naming the binding constraint
    (`None` when the window passes). Reports; grades nothing.
    """
    try:
        cn, bn = float(cell["net_total_r"]), float(base["net_total_r"])
        cd, bd = float(cell["max_drawdown_r"]), float(base["max_drawdown_r"])
    except (KeyError, TypeError, ValueError):
        # Unreadable is NOT "failed on net_R" — say which.
        return {"passed": False, "reason": "unreadable",
                "d_net_r": None, "d_max_dd": None}
    reasons = []
    if cn < bn:
        reasons.append("net_r_worse")
    if cd > bd:
        reasons.append("maxdd_worse")
    if not reasons and cn == bn and cd == bd:
        reasons.append("tie_no_improvement")
    return {"passed": not reasons, "reason": "+".join(reasons) or None,
            "d_net_r": round(cn - bn, 4), "d_max_dd": round(cd - bd, 4)}


def capital_delta(cell: dict, base: dict) -> dict:
    """Cell-vs-base capital-efficiency comparison — **REPORTED, never graded.**

    Path B's two thresholds (how much `net_r_per_capital_day` must improve, and
    how much net_R may fall) are deliberately unset: the operator sets them from
    a measured distribution, not from a number a session invented. So this
    returns the raw pair plus their deltas and says nothing about pass/fail.

    Every value is `None`, never `0.0`, when the underlying rate was
    unmeasurable — `capital_efficiency.days_from_bars` already refuses to
    fabricate a hold, and collapsing "we could not measure the rate" into "the
    rate was zero" here would re-introduce exactly what that refusal prevents
    (docs/CLAUDE-RULES-CANONICAL.md § "Collapsed states"). A consumer must be
    able to tell an unmeasured cell from a flat one before ranking on it.
    """
    def _f(d: dict, k: str):
        v = d.get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    out = {}
    for key in ("net_r_per_capital_day", "net_r_per_position_day",
                "mean_bars_held", "capital_days"):
        c, b = _f(cell, key), _f(base, key)
        out[f"cell_{key}"] = c
        out[f"base_{key}"] = b
        out[f"d_{key}"] = (round(c - b, 4) if c is not None and b is not None
                           else None)
    cn, bn = _f(cell, "net_total_r"), _f(base, "net_total_r")
    out["d_net_total_r"] = (round(cn - bn, 4)
                            if cn is not None and bn is not None else None)
    # Path B's net_R floor is expressed as a FRACTION of base net_R, so carry
    # the ratio too — but only when base net_R is non-zero AND positive. A
    # negative or zero base makes "fell no more than X%" meaningless rather
    # than merely large, so it is None, not a misleading number.
    out["net_r_retained_frac"] = (round(cn / bn, 4)
                                  if cn is not None and bn is not None and bn > 0
                                  else None)
    return out


def main(argv: list[str]) -> int:
    global CELL_TIMEOUT_S
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--split", default="2025-07-01",
                    help="IS/OOS boundary date. Used directly when "
                         "--split-mode=date, and as the fallback when "
                         "oos-trades cannot be satisfied.")
    ap.add_argument("--split-mode", choices=["oos-trades", "date"],
                    default="oos-trades",
                    help="How the IS/OOS boundary is placed. `oos-trades` "
                         "(default) derives a per-leg date targeting "
                         "--split-target-oos trades in OOS, so a "
                         "low-frequency leg is gradeable; `date` is the legacy "
                         "fixed calendar split. See resolve_split().")
    ap.add_argument("--split-target-oos", type=int, default=MIN_OOS_TRADES,
                    help="Trades to TARGET in the OOS window under "
                         "--split-mode=oos-trades. Defaults to the floor a "
                         "cell is judged against, so the boundary aims at "
                         "exactly what the verdict requires.")
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "m20_fleet"))
    ap.add_argument("--only", default=None,
                    help="CSV of leg names to restrict to (debug)")
    ap.add_argument("--family", default=None,
                    help="CSV of families to restrict to (scalp,pullback,donchian,"
                         "squeeze,fvg). Lets a runner shard by family without "
                         "hardcoding a leg list that would drift from "
                         "config/strategies.yaml.")
    ap.add_argument("--levers", default=None,
                    help="CSV of matrix levers to restrict cells to (e.g. "
                         "trail_decay) — skips already-verdicted cells on a re-run")
    ap.add_argument("--list", action="store_true",
                    help="print the run plan (leg -> harness/data/cells) and exit")
    ap.add_argument("--fee-bps-roundtrip", type=float, default=None,
                    help="Override the harness roundtrip fee (bps). Default None "
                         "= the harness's own execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP "
                         "(7.5). Exists because a base measured at ONE fee level "
                         "cannot answer a fee-SURVIVAL question: SRQ-20260618-003 "
                         "rejected the 5m scalp alts precisely because they were "
                         "+50R at 7.5bps and -38R at 15bps, and the whole 15m "
                         "hypothesis is that fewer, larger-R trades escape that "
                         "band. The corpus is entirely 7.5bps, so the 15bps arm "
                         "was unrunnable without this flag.")
    ap.add_argument("--min-confidence-override", type=float, default=None,
                    help="Override every leg's config-declared min_confidence "
                         "(entry-selectivity floor). Default None = use each "
                         "leg's own declared value, i.e. the config-exact base. "
                         "Exists for the surviving arm of SRQ-20260618-003: the "
                         "15bps run refuted 'fewer trades escape the fee band', "
                         "leaving 'higher edge per trade' untested. A row swept "
                         "with an override is NOT comparable to a config-exact "
                         "row, so the value joins the corpus measurement key.")
    ap.add_argument("--without-declared-lever", action="append", default=[],
                    choices=sorted(LEVER_DECLARED_KEYS),
                    metavar="LEVER",
                    help="LEVER-OFF ARM. Remove this declared exit lever from "
                         "every leg's config-exact base, then measure ONE cell "
                         "that puts it back at the leg's own live values. "
                         "Repeatable. Exists because the normal sweep is "
                         "STRUCTURALLY unable to grade a SHIPPED lever -- the "
                         "shipped lever IS the base, so every cell asks 'does "
                         "this alternative beat it?' and none asks 'is it worth "
                         "anything?'. 21 live decisions on the coverage matrix "
                         "rest on pre-TP-parity evidence and are unanswerable "
                         "without this arm. Choices are the levers a leg can "
                         "DECLARE in YAML; trail_geometry is absent on purpose "
                         "(trail_mult is a continuous parameter with no OFF "
                         "state). A row swept with this set is NOT comparable "
                         "to a config-exact row, so the value joins the corpus "
                         "measurement key.\n"
                         "DROP ONE LEVER PER RUN when a leg declares several. "
                         "Dropping two removes BOTH from the base, so the cell "
                         "restoring one measures its contribution in a book that "
                         "still lacks the other -- a clean one-lever A/B, but "
                         "against a counterfactual base, not the live "
                         "configuration. 2 legs are affected "
                         "(trend_donchian_eth, trend_donchian_eth_prop); the run "
                         "warns and every row records which other levers were "
                         "absent, so this is never silent.")
    ap.add_argument("--tp-cap-pct", type=float, default=0.0,
                    help="Run with the LIVE-PARITY take-profit "
                         "(production: 0.099 -- the Bybit ~10%% TP-distance "
                         "clamp on the 50R sentinel). Applied ONLY to families "
                         "whose live unit carries the clamp "
                         "(LIVE_TP_CAPPED_FAMILIES); applying it elsewhere "
                         "would manufacture a parity break rather than "
                         "reproduce one. 0 = off, the geometry every verdict "
                         "before 2026-08-10 was measured on.")
    ap.add_argument("--census", action="store_true",
                    help="MEASURE-FIRST pass (operator-directed 2026-08-10): run "
                         "each leg's config-exact base ONLY and report the exit-capture "
                         "census (MFE capture distribution + near-miss-to-target rate "
                         "for fixed-target legs). Applies no lever and grades nothing "
                         "-- it sizes the prize and orders the legs before any lever "
                         "is designed.")
    ap.add_argument("--cell-timeout", type=float, default=CELL_TIMEOUT_S,
                    help="Per-harness-run subprocess cap in seconds (default "
                         "1800). A run that exceeds it is recorded as "
                         "verdict:error -- a measurement not taken, never a "
                         "measured negative. Raise it for the 5m scalp legs, "
                         "whose full-history run was measured at ~955s.")
    ap.add_argument("--p80-only", action="store_true",
                    help="P4.4 re-run: evaluate ONLY the dynamic p80 decay cell "
                         "per leg (fixed cells already verdicted)")
    a = ap.parse_args(argv[1:])
    if a.cell_timeout and a.cell_timeout > 0:
        CELL_TIMEOUT_S = float(a.cell_timeout)

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml")
                                 .read_text()) or {}).get("strategies") or {}
    only = set(a.only.split(",")) if a.only else None
    fams = set(a.family.split(",")) if a.family else None
    levers = set(a.levers.split(",")) if a.levers else None
    without_levers = frozenset(a.without_declared_lever or ())
    if without_levers and a.census:
        # The census measures ONE base per leg and grades nothing. Running it
        # against a mutated base would print a capture distribution for a book
        # that is not the live book, under the same column headings — a labelled
        # number computed from a substituted input.
        print("ERROR: --census measures the config-exact base and cannot be "
              "combined with --without-declared-lever.")
        return 2
    data_dir = Path(a.data_dir)
    run_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plan, skipped = [], []
    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or (only and name not in only):
            continue
        fam = classify(name)
        # Shard filter FIRST: a leg belonging to another family is out of
        # scope, not "skipped". Recording it as a skip would make every
        # shard report the same unrelated legs and inflate the skip count
        # N-fold when the shards are read together.
        if fams and fam not in fams:
            continue
        if fam is None:
            skipped.append({"leg": name, "reason": "no_harness_levers"})
            continue
        sym = (cfg.get("symbols") or [None])[0]
        tf = str(cfg.get("timeframe") or "1h")
        data, proxy, resample = resolve_data(str(sym), tf, data_dir)
        if data is None:
            skipped.append({"leg": name, "reason": f"data_missing:{sym}"})
            continue
        harness = FAMILY_HARNESS[fam]
        inert: list = []
        cells = cells_for(cfg, fam, skipped=inert,
                          without_declared_levers=without_levers)
        # WHAT THIS LEG ACTUALLY HAD REMOVED, against what the run asked for.
        # A leg that never declared the requested lever produces a base
        # byte-identical to the config-exact base; recording only the run-level
        # request would let that row read as a lever-OFF measurement when
        # nothing was off. Empty list = "we looked, this leg declares none" --
        # not the same statement as the run not asking.
        present = declared_levers_present(cfg)
        dropped = sorted(set(present) & without_levers)
        plan.append({"leg": name, "family": fam, "symbol": sym, "tf": tf,
                     "harness": harness, "data": data, "proxy": proxy,
                     "resample": resample,
                     "declared_levers_present": present,
                     "declared_levers_dropped": dropped,
                     # Cells withheld as structurally inert ride WITH the leg
                     # rather than vanishing: a cell that is absent from the
                     # table and a cell that ran flat must stay tellable apart.
                     "inert_cells": inert,
                     "base": base_args(name, cfg, fam, data, resample, a.tp_cap_pct,
                                       a.fee_bps_roundtrip,
                                       a.min_confidence_override,
                                       without_declared_levers=without_levers),
                     "cells": [c for c in cells
                               if not levers or c[1] in levers]})

    print(f"plan: {len(plan)} legs runnable, {len(skipped)} skipped")
    # A LEG WITH TWO LEVERS DROPPED IS MEASURING AGAINST A COUNTERFACTUAL BASE.
    # Each shipped cell restores exactly one, so the A/B is still clean for that
    # lever — but the book it is clean IN lacks the other one, which is not the
    # live configuration. That distinction is invisible in a results table, so it
    # gets its own line rather than being left for a reader to derive from
    # `declared_levers_dropped`. Every affected row also carries
    # `base_missing_other_levers`.
    for p in plan:
        _d = p.get("declared_levers_dropped") or []
        if len(_d) > 1:
            print(f"  !! MULTI-LEVER BASE {p['leg']}: dropped {_d} together. Each "
                  f"shipped cell restores ONE, so its delta is that lever's "
                  f"contribution in a book still missing the rest — NOT its "
                  f"contribution to the live config. Re-run one lever at a time "
                  f"for a live-configuration answer.", flush=True)
    for s in skipped:
        print(f"  SKIP {s['leg']}: {s['reason']}")
    for p in plan:
        for c in p["inert_cells"]:
            # A leg-level note carries no cell name. Printing a bare `None`
            # where a cell tag goes invites reading it as a cell called None —
            # so the two entry shapes are rendered differently rather than
            # formatted by one template that fits only the common case.
            print(f"  INERT {p['leg']}: {c['cell']} — {c['reason']}" if c["cell"]
                  else f"  NO-OP {p['leg']}: {c['reason']}")
    if a.list:
        for p in plan:
            print(f"  RUN  {p['leg']:28s} {p['harness'].split('/')[-1]:22s} "
                  f"{p['data']}{' [PROXY]' if p['proxy'] else ''} "
                  f"cells={[c[0] for c in p['cells']]}")
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)

    if a.census:
        census: dict = {}
        for p in plan:
            cfg = strategies.get(p["leg"]) or {}
            tr = leg_target_r(cfg)
            row = run_census(p["harness"], p["base"], tr, end=None)
            # exit_kind describes what the census ACTUALLY treated the leg as,
            # not what the YAML declares. A leg carrying the `tp_r: 50.0`
            # disabled-TP sentinel declares a target and has none in practice;
            # labelling it `fixed_target` while every near-miss column reads
            # None invites the reader to think the None is a bug. Third instance
            # this session of a label not describing the computation behind it.
            declared = tr is not None
            na = row.get("near_miss_not_applicable")
            row.update({"family": p["family"], "symbol": p["symbol"],
                        "tf": p["tf"], "proxy": p["proxy"],
                        "target_r_declared": tr,
                        "exit_kind": ("trail" if not declared
                                      else "target_inert" if na else "fixed_target"),
                        "exit_kind_reason": na})
            census[p["leg"]] = row
            # Print the ROBUST statistics. The first run printed capture_mean
            # alone, which is the one figure a small MFE denominator blows up
            # (fvg_range_15m read -14.13), so the stdout line — the only part
            # visible without downloading the artifact — was the least
            # trustworthy number in the block.
            _gb = next((r for r in (row.get("giveback_ladder") or [])
                        if r["mfe_ge_r"] == 1.0), None)
            print(f"  {p['leg']:28s} med={row.get('capture_median')} "
                  f"wmed={row.get('capture_winners_median')} "
                  f"Rwt={row.get('capture_r_weighted')} "
                  f"<30%={row.get('capture_lt_30_pct')} "
                  f"gb1R={(str(_gb['lost_n']) + '/' + str(_gb['mfe_ge_n'])) if _gb else '-'} "
                  f"nm90={row.get('near_miss_90_pct')} "
                  f"meas={row.get('capture_measured_n')}/{row.get('n_trades')}"
                  f"{' ERR ' + str(row['error'])[:60] if 'error' in row else ''}",
                  flush=True)
            # A LEG THAT TRADED AND MEASURED NOTHING IS A DEFECT, NOT A RESULT.
            # Run 3 printed `meas=0/1102` for ict_scalp_avax_5m in a row of
            # Nones and it read as ordinary output; the cause was that the
            # scalp harness nests mfe_r under `meta` while the reader looked
            # top-level. Zero capture over a thousand trades is never a
            # property of the book — it is always a reader defect — so it gets
            # its own line rather than one column among five.
            if "error" not in row and row.get("n_trades") and not row.get("capture_measured_n"):
                print(f"    !! ZERO CAPTURE COVERAGE over {row['n_trades']} trades "
                      f"— mfe_r unreadable for this harness, NOT a finding about "
                      f"the leg. Do not read any None above as a measurement.",
                      flush=True)
        ok = {k: v for k, v in census.items() if "error" not in v}
        # The same fact, hoisted so it cannot be missed in a long log.
        zero_cov = sorted(k for k, v in ok.items()
                          if v.get("n_trades") and not v.get("capture_measured_n"))
        if zero_cov:
            print(f"\n!! {len(zero_cov)} leg(s) traded with ZERO capture coverage: "
                  f"{', '.join(zero_cov)}", flush=True)
        # ALWAYS STATE THE POPULATION. Two denominators, and they differ: every
        # leg can be capture-measured, only fixed-target legs can be near-missed.
        cap_legs = [v for v in ok.values() if v.get("capture_mean") is not None]
        nm_legs = [v for v in ok.values() if v.get("near_miss_90_pct") is not None]
        (run_dir / "capture_census.json").write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "legs_planned": len(plan), "legs_measured": len(ok),
            "legs_errored": len(census) - len(ok),
            "legs_capture_measured": len(cap_legs),
            "legs_near_miss_applicable": len(nm_legs),
            # Named in the artifact too, so a consumer reading the JSON without
            # the log still sees which legs' Nones are unreadable-vs-measured.
            "legs_zero_capture_coverage": zero_cov,
            "note": ("Measure-first pass; no lever applied, nothing graded. "
                     "near_miss_* is null for trail-exit legs because they have "
                     "no target to nearly reach -- null means N/A, not 0%."),
            "total_r_left_on_table_90pct_band": round(sum(
                v["near_miss_r_left_on_table"] for v in nm_legs
                if v.get("near_miss_r_left_on_table") is not None), 2) or None,
            "legs": census,
        }, indent=1))
        lines = ["# M20 exit-capture census (measure-first, nothing graded)", "",
                 f"Legs planned **{len(plan)}**, measured **{len(ok)}**, "
                 f"errored **{len(census) - len(ok)}**. "
                 f"Capture measurable on **{len(cap_legs)}**; near-miss applicable "
                 f"to **{len(nm_legs)}** (fixed-target legs only).", ""]
        if zero_cov:
            lines += [f"> ⚠️ **{len(zero_cov)} leg(s) traded with ZERO capture "
                      f"coverage** — `{', '.join(zero_cov)}`. Their blank cells "
                      "below mean *mfe_r was unreadable*, **not** that the leg "
                      "keeps none of its move. Do not read them as measurements.",
                      ""]
        lines += [
                 "",
                 "`gb>=1R` is the GIVEBACK ladder at the 1R rung: "
                 "`lost/reached` — of the trades that ran at least +1R in open "
                 "profit, how many still closed RED, and the R that cost. This "
                 "is the operator's complaint stated for a leg with no target, "
                 "and it is the column to read: a breakout book is structurally "
                 "full of small pokes that fail, so a bad `cap <30%` can be that "
                 "structure rather than leakage. The full ladder "
                 "(0.5/1/1.5/2R) is in capture_census.json.", "",
                 "",
                 "`R->tgt` is the BOUNDED near-miss prize (`target_r - net_r`): what "
                 "the near-miss losers would have banked by reaching their OWN "
                 "declared target. Read it INSTEAD of `R left`, which sums an "
                 "unbounded intrabar peak and is skewed by the harnesses' "
                 "stop-before-target intrabar convention — one bar spanning from "
                 "below the stop to far above the target books a -1R loss AND "
                 "records a huge MFE (measured: `ict_scalp_avax_5m` 182.34R over "
                 "FOUR trades). `gb R med` is the same guard on the ladder.", "",
                 "| leg | kind | n | cap med | cap w-med | cap Rwt | cap <30% "
                 "| gb>=1R | gb R left | gb R med | nm@90% | nm pop | tgt hit "
                 "| R left | R->tgt |",
                 "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for leg, v in sorted(census.items(),
                             key=lambda kv: -(kv[1].get("near_miss_90_pct") or -1)):
            if "error" in v:
                lines.append(f"| {leg} | — | — | ERROR | {str(v['error'])[:40]} | — | — |")
                continue
            kind = v['exit_kind']
            if v.get('exit_kind_reason'):
                kind += f" ({v['exit_kind_reason']}, declared {v.get('target_r_declared')}R)"
            # nm@90% ALWAYS ships beside its denominator. "0.0" over three
            # losers is not the claim "0.0" over three hundred is, and the
            # first table printed the rate alone.
            # The 1R rung, ALWAYS as lost/reached — a bare percentage over an
            # unstated denominator is the class this census keeps tripping over.
            gb = next((r for r in (v.get("giveback_ladder") or [])
                       if r["mfe_ge_r"] == 1.0), None)
            gb_cell = (f"{gb['lost_n']}/{gb['mfe_ge_n']}"
                       + (f" ({gb['lost_pct']}%)" if gb["lost_pct"] is not None else "")
                       ) if gb else "—"
            lines.append(f"| {leg} | {kind} | {v['n_trades']} | "
                         f"{v['capture_median']} | "
                         f"{v.get('capture_winners_median')} | "
                         f"{v.get('capture_r_weighted')} | "
                         f"{v['capture_lt_30_pct']} | "
                         f"{gb_cell} | {gb['r_left'] if gb else '—'} | "
                         f"{v['near_miss_90_pct']} | {v['near_miss_measured_n']} | "
                         f"{v.get('target_r_reached_n')} | "
                         f"{v['near_miss_r_left_on_table']} |")
        (run_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
        print("census ->", run_dir)
        return 0

    results = (run_dir / "results.jsonl").open("a", encoding="utf-8")

    def log_result(row: dict) -> None:
        results.write(json.dumps(row) + "\n")
        results.flush()

    verdicts: dict = {}
    for p in plan:
        leg = p["leg"]
        print(f"== {leg} ({p['symbol']} {p['tf']}) ==", flush=True)
        # PER-LEG boundary. A fixed date makes trade FREQUENCY decide whether a
        # leg can be graded at all; see resolve_split(). The split is resolved
        # once per leg and reused for every cell, so IS/OOS means the same thing
        # across a leg's candidates.
        leg_split, split_meta = resolve_split(
            p["harness"], p["base"], a.split_mode, a.split, a.split_target_oos)
        if split_meta.get("split_fallback"):
            print(f"    split: FELL BACK to {leg_split} "
                  f"({split_meta['split_fallback']}"
                  + (f", lifetime={split_meta['split_lifetime_trades']}"
                     if "split_lifetime_trades" in split_meta else "") + ")")
        elif a.split_mode != "date":
            print(f"    split: {leg_split} (targeting {a.split_target_oos} OOS "
                  f"trades of {split_meta.get('split_lifetime_trades')} lifetime "
                  f"— ACHIEVED count is base_oos below, not this target)")
        base_is = run_cell(p["harness"], p["base"], end=leg_split)
        base_oos = run_cell(p["harness"], p["base"], start=leg_split)
        log_result({"leg": leg, "cell": "base", "window": "IS", **base_is})
        log_result({"leg": leg, "cell": "base", "window": "OOS", **base_oos})
        if "error" in base_is or "error" in base_oos:
            verdicts[leg] = {"status": "harness_error",
                             "error": base_is.get("error") or base_oos.get("error")}
            continue
        leg_v = {"proxy": p["proxy"], "family": p["family"], "levers": {},
                 **split_meta,
                 # Cells the grid deliberately did not ask, and why. Without
                 # this the verdict file cannot distinguish a cell that was
                 # never run from one that ran and moved nothing.
                 "inert_cells": p.get("inert_cells") or [],
                 # PER-LEG lever-OFF state. The run-level `without_declared_levers`
                 # says what was ASKED; these say what this leg actually HAD.
                 # A leg with `dropped: []` under a non-empty run-level request
                 # measured the ordinary config-exact base — a fact the run-level
                 # field alone would misreport.
                 "declared_levers_present": p.get("declared_levers_present") or [],
                 "declared_levers_dropped": p.get("declared_levers_dropped") or []}
        # HOW FAR AWAY IS THE LIVE TP, IN R? Measured from THIS leg's own frame,
        # not assumed. `BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`
        # was reported to the operator with an ILLUSTRATIVE "1.3-2.0R" derived
        # from atr_stop_mult 2.5 at 2-3% ATR — a range that had never been
        # measured and would harden into fact if left unstated. The harnesses
        # emit tp_r_effective_{n,median,min,max} whenever the cap is active, and
        # run_cell already returns the whole summary, so the number is free; it
        # was simply never surfaced anywhere a reader could see it. `None` here
        # means the cap was OFF for this run (legacy geometry), which is a
        # different statement from "the TP is far away".
        leg_v["live_tp_reach_r"] = {
            w: {k: d.get(f"tp_r_effective_{k}") for k in ("n", "median", "min", "max")}
            for w, d in (("IS", base_is), ("OOS", base_oos))
        }
        # THE BASE BOOK, PER WINDOW — recorded for EVERY leg, unconditionally.
        #
        # `drawdown_exchange_rate` already computes `base_net_r`/`base_max_dd`,
        # but only inside a Path B candidate's entry — so the one axis a Path B
        # FLOOR would be defined on (`net_R per unit of drawdown`, the rate the
        # allowance is scaled from) was recorded ONLY for cells that had already
        # passed the Path B predicate. Deriving a floor from that corpus would
        # condition on the outcome: the legs whose rate is low enough to be the
        # problem are exactly the ones most likely to admit a candidate, so they
        # are over-represented, and the legs that produced no candidate at all
        # contribute nothing — the denominator is missing by construction.
        #
        # The operator's ask (2026-08-10) is a floor derived from capital-
        # utilisation + PnL data rather than picked. That requires the rate for
        # the WHOLE population, which is what this block records. It is free:
        # `run_cell` already returned the whole base summary.
        #
        # `rate` is None -- never 0.0 -- when the base book cannot express one.
        # A book that lost money and a book with no drawdown are not "rate zero";
        # they are two distinct ungradeable states, and `why` says which, so a
        # consumer ranking on the rate can drop them rather than sort them to the
        # bottom as if measured (docs/CLAUDE-RULES-CANONICAL.md, "Collapsed
        # states").
        def _base_book(d: dict) -> dict:
            def _f(k):
                try:
                    v = d.get(k)
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None
            n, dd = _f("net_total_r"), _f("max_drawdown_r")
            if n is None or dd is None:
                rate, why = None, "unreadable"
            elif n <= 0:
                rate, why = None, "base_unprofitable"
            elif dd <= 0:
                rate, why = None, "base_no_drawdown"
            else:
                rate, why = round(n / dd, 4), None
            return {"net_total_r": n, "max_drawdown_r": dd,
                    "net_r_per_drawdown_r": rate, "rate_ungradeable_why": why,
                    "total_trades": d.get("total_trades"),
                    "net_r_per_capital_day": d.get("net_r_per_capital_day"),
                    "capital_days": d.get("capital_days"),
                    "mean_bars_held": d.get("mean_bars_held")}

        leg_v["base_book"] = {"IS": _base_book(base_is),
                              "OOS": _base_book(base_oos)}
        # M20 P4.4 — dynamic MFE-percentile decay cell: arm at the leg's own
        # P80 winner-MFE (IS window only) instead of a fixed R. Only where the
        # family has the decay lever and the fixed decay cells are in scope.
        decay_in_scope = any(lv == "trail_decay" for _, lv, _ in p["cells"])
        if a.p80_only:
            p["cells"] = []  # fixed cells already verdicted; p80 cell only
        # THE LEVER-OFF ARM SUPPRESSES IT. `cells_for` returns only the
        # `shipped_*` cells under the arm, but this injection happens AFTER that
        # early return and so bypassed it — the first live run emitted a
        # `decay_p80arm*` cell on 5 of 7 legs, measured against a base whose
        # shipped lever had been removed. Those rows are labelled correctly (the
        # identity fields ride on every row) but they answer a different question
        # than the same tag does in a normal run, which is exactly what the arm
        # was documented NOT to do. Found by reading the arm's own first results,
        # not by the tests — which covered `cells_for` and never this hop.
        if (p["family"] in ("donchian", "pullback") and decay_in_scope
                and not without_levers):
            tm_val = next((float(x[1]) for x in
                           zip(p["base"], p["base"][1:])
                           if x[0] == "--trail-mult"), None)
            p80 = winner_mfe_p80(p["harness"], p["base"], leg_split)
            if p80 is not None and p80 > 0.5 and tm_val:
                tight = max(1.5, round(tm_val / 2.0, 1))
                p["cells"].append(
                    (f"decay_p80arm{p80:g}R_t{tight:g}", "trail_decay",
                     ["--trail-decay-arm-r", str(p80),
                      "--trail-decay-tight-mult", str(tight)]))
                print(f"   p80 winner-MFE arm = {p80}R", flush=True)
            else:
                print(f"   p80 cell skipped (p80={p80}, tm={tm_val})",
                      flush=True)
        for tag, lever, extra in p["cells"]:
            args = p["base"] + extra
            c_is = run_cell(p["harness"], args, end=leg_split)
            c_oos = run_cell(p["harness"], args, start=leg_split)
            log_result({"leg": leg, "cell": tag, "window": "IS", **c_is})
            log_result({"leg": leg, "cell": tag, "window": "OOS", **c_oos})
            if "error" in c_is or "error" in c_oos:
                leg_v["levers"].setdefault(lever, []).append(
                    {"cell": tag, "verdict": "error"})
                continue
            candidate = beats(c_is, base_is) and beats(c_oos, base_oos)
            entry = {"cell": tag, "is_oos_pass": candidate}
            # Record WHICH window bound, and on WHICH axis. `is_oos_fail` alone
            # cannot distinguish "helps only in the recent regime" from "helps
            # both but costs drawdown"; see beats_detail.
            entry["gate"] = {"IS": beats_detail(c_is, base_is),
                             "OOS": beats_detail(c_oos, base_oos)}
            # Capital efficiency is recorded for EVERY cell, including the ones
            # Path A rejects — those are precisely the Path B population, and a
            # verdict file that carried the axis only for Path-A survivors would
            # be unable to answer the question it was added for.
            entry["capital"] = {"IS": capital_delta(c_is, base_is),
                                "OOS": capital_delta(c_oos, base_oos)}
            # THE MIN-OOS-TRADES FLOOR (operator decision 2026-08-11: 25).
            #
            # Path A's `beats()` had NO minimum trade count, so a cell cleared it
            # over a book that might hold THREE trades and then posted a "6/6
            # walk-forward" over folds that were nearly empty. Measured over the
            # 603-cell corpus: 35.8% of cells sat on an OOS base under 10 trades,
            # 65.2% under 50, and 33 of the 40 PASSING cells (82%) were under 50
            # with 13 under 10. `spy_trend_long_1d vt_hot90_t2` passed on 3 OOS
            # trades at +0.80R with a maxDD delta of EXACTLY 0.0 -- the lever
            # never touched the drawdown path.
            #
            # Unlike the Path B RATE floor (measured the same day and REFUSED --
            # both candidate predictors returned `no_separation` over 604 rows),
            # this is NOT a fitted threshold. It is a DENOMINATOR REQUIREMENT,
            # the shape `research_results_gate.min_trades` already ships, and it
            # needs no separation test. The VALUE came from the cost curve, not
            # from a fit: floor 10 -> 34 of 51 legs / 27 passes; floor 25 -> 32
            # legs / 27 passes; floor 50 -> 20 legs / 7 passes. So 10->25 costs
            # two legs and ZERO passes (free), and the cliff is 25->50. 25 is the
            # last point before coverage is paid for. A floor of 50+ would also
            # structurally exclude every DAILY-timeframe leg, which cannot reach
            # 50 trades in a ~1y OOS window -- rejecting them for bar size, not
            # for a bad lever.
            #
            # ITS OWN STATE, never folded into `is_oos_fail`: "we did not look at
            # enough trades" and "we looked and the lever failed" are opposite
            # findings, and collapsing them would make a thin book indistinguish-
            # able from a refuted lever. The cell's numbers are still recorded --
            # they are evidence -- and the walk-forward is skipped (it would be
            # measuring the same too-thin book, and it is the expensive step).
            #
            # HONEST LIMIT: this floor is a PROXY for the statistic that actually
            # matters -- how many trades the LEVER fired on, and whether the
            # effect exceeds its own noise. A ΔmaxDD of exactly 0.0 is the lever
            # reporting that it barely fired, and this floor would NOT catch a
            # cell on a 200-trade base that modified two exits. The corpus does
            # not record per-cell fire counts; that gap stays open.
            _base_oos_n = base_oos.get("total_trades")
            _thin = (isinstance(_base_oos_n, (int, float))
                     and _base_oos_n < MIN_OOS_TRADES)
            entry["base_trades_oos"] = _base_oos_n
            entry["min_oos_trades_floor"] = MIN_OOS_TRADES
            # WHERE THE BOUNDARY CAME FROM. `_base_oos_n` is a count over a
            # window that was CHOSEN, and until 2026-08-14 nothing downstream
            # recorded the choice -- so a refusal read as "this leg has 24
            # trades" when the leg has 407 and the DERIVATION handed it 24
            # (BL-20260814-SPLIT-TARGETS-EXACTLY-THE-FLOOR-SO-BOUNDARY-LOSS-ALWAYS-FAILS,
            # measured on htf_pullback_trend_2h: 95 OOS at the corpus-standard
            # split vs 24 at the derived one -- same leg, same day, same
            # config). That is diagnostic-provenance sub-class B: an implicit
            # input selection substituted for the declared one, with nothing in
            # the output revealing it. `resolve_split`'s own docstring already
            # promised the cure -- "Returned meta records target AND mode so a
            # verdict states its own derivation" -- and no verdict read the
            # meta, so the promise was prose about a property that did not
            # exist. Recorded on EVERY cell, not only refused ones: a boundary
            # that decides a PASS deserves the same audit trail as one that
            # decides a refusal.
            #
            # PURELY ADDITIVE -- no verdict branch reads these keys, so this
            # cannot move a grade. `split_target_oos` is the TARGET; the
            # ACHIEVED count is `base_trades_oos` above, and the two are not
            # interchangeable (resolve_split's docstring is explicit that the
            # harness windows CANDLES, not trades).
            entry["split"] = leg_split
            entry["split_mode"] = split_meta.get("split_mode")
            entry["split_target_oos"] = split_meta.get("split_target_oos")
            entry["split_lifetime_trades"] = split_meta.get(
                "split_lifetime_trades")
            entry["split_fallback"] = split_meta.get("split_fallback")
            # THE CLAMP MUST RIDE WITH THE TARGET OR THE ROW LIES BY OMISSION.
            # `split_target_oos` above is what the CALLER ASKED FOR; when the
            # leg could not support it, `resolve_split` clamps and derives at a
            # smaller one. Without these two keys the corpus row reads
            # `split_target_oos: 35` for a run that used 32 -- a row reporting a
            # target it did not use, which is exactly what resolve_split's own
            # docstring forbids.
            #
            # Caught 2026-08-14 by reading back the twelve OFF-arm rows this
            # session had just produced: the three clamped rows landed with
            # `split_target_clamped_to: null` while their OOS window had
            # visibly moved 4 -> 31. The meta carried the clamp, the writer
            # dropped it, and nothing downstream could tell. Verifying my own
            # output is what found it; the sweep itself was correct.
            entry["split_target_clamped_from"] = split_meta.get(
                "split_target_clamped_from")
            entry["split_target_clamped_to"] = split_meta.get(
                "split_target_clamped_to")
            if _thin:
                # Record what it WOULD have been, so the floor's effect on this
                # cell is auditable rather than invisible.
                entry["would_have_been"] = (
                    "is_oos_pass" if candidate else "is_oos_fail")
                entry["verdict"] = "insufficient_base"
                entry["insufficient_base_why"] = insufficient_base_reason(
                    _base_oos_n, MIN_OOS_TRADES, leg_split, split_meta)
            elif candidate:
                wf = walkforward(p["harness"], p["base"], args, log_result,
                                 leg, tag, require_dd=True)
                entry["walkforward"] = wf["summary"]
                entry["walkforward_folds"] = wf["folds"]
                entry["verdict"] = ("PASS" if wf["usable"] >= 4
                                    and wf["wins"] * 3 >= wf["usable"] * 2
                                    else "wf_fail")
            elif is_path_b_candidate(entry["gate"]["IS"], entry["gate"]["OOS"],
                                     entry["capital"]["OOS"]):
                # THE GATE GAP (found 2026-08-10): a Path B candidate
                # short-circuited to `is_oos_fail` BEFORE any walk-forward ran,
                # so every Path B candidate on record — five donchian cells plus
                # ict_scalp_sol_5m's be_touch_arm — had ZERO generalisation
                # evidence. The walk-forward is the only guard against per-leg
                # selection noise (44 legs x ~10 cells is ~440 comparisons), and
                # it never ran on precisely the population a Path B threshold
                # would promote from.
                #
                # The fold test is the cell's OWN claim (net_R no worse), NOT
                # Path A's net_R-and-drawdown pair, which a drawdown-trading
                # cell fails by construction. Per-fold drawdown deltas are
                # RECORDED so the operator sets a tolerance against a measured
                # cost distribution instead of a remembered one.
                #
                # `path_b_wf_pass` IS NOT A PROMOTION. Both Path B thresholds
                # remain unset; this says only "the net_R gain generalises
                # across folds", which is the prerequisite for the question, not
                # the answer to it.
                wf = walkforward(p["harness"], p["base"], args, log_result,
                                 leg, tag, require_dd=False)
                entry["walkforward"] = wf["summary"]
                entry["walkforward_folds"] = wf["folds"]
                entry["path_b_candidate"] = True
                # THE DERIVED TOLERANCE, per window and per leg. Reported, not
                # enforced — this is the evidence the operator's Path B decision
                # rests on, and a criterion that promoted on its own would be
                # the same Tier-3 short-circuit the sweep exists to avoid.
                entry["dd_exchange_rate"] = {
                    "IS": drawdown_exchange_rate(c_is, base_is),
                    "OOS": drawdown_exchange_rate(c_oos, base_oos),
                }
                entry["verdict"] = ("path_b_wf_pass" if wf["usable"] >= 4
                                    and wf["wins"] * 3 >= wf["usable"] * 2
                                    else "path_b_wf_fail")
                # THE VERDICT NAME DOES NOT MEAN THE RATE GATE PASSED, and at
                # fleet scale 6 of 18 `path_b_wf_pass` rows failed it (2026-08-10,
                # 43-leg corpus) — including `tlt_pullback_1h trail4`, whose OOS
                # base is UNGRADEABLE and whose grant is the largest in the fleet
                # at 170% of the base book's whole drawdown. The docstring above
                # disclaims it, the per-row `rate ok` column reports it, and a
                # reader scanning a table of `path_b_wf_pass` rows still would not
                # guess that a third of them fail the gate the prose describes.
                # That is a label not describing what was computed.
                #
                # THREE-STATE, never collapsed to a boolean: `False` (a gradeable
                # window said no) and `None` (no window could be graded at all)
                # are opposite findings, and folding "we could not look" into
                # either "ok" or "failed" is the exact defect
                # `collapsed-state-guard` exists for. Carried on the entry so the
                # corpus and every downstream table can key on it.
                _r = [entry["dd_exchange_rate"][w]["passes"] for w in ("IS", "OOS")]
                _graded = [v for v in _r if v is not None]
                entry["path_b_rate_ok"] = (
                    None if not _graded else all(_graded))
                entry["path_b_rate_windows_graded"] = len(_graded)
            else:
                entry["verdict"] = "is_oos_fail"
            leg_v["levers"].setdefault(lever, []).append(entry)
            print(f"   {tag:20s} -> {entry['verdict']}"
                  f"{' wf=' + entry.get('walkforward', '') if 'walkforward' in entry else ''}",
                  flush=True)
        # THE SELECTION DENOMINATOR. Per-leg promotion means picking the best
        # cell for THIS leg, and 44 legs x ~10 cells is ~440 comparisons — some
        # will look like winners by chance. A winner reported without the number
        # of cells it beat is a winner over an unstated denominator, the same
        # defect `rCoverage`/`pnlCoverage` exist to prevent one level down.
        # Recorded per leg so a later promotion packet cannot omit it.
        _all_entries = [e for es in leg_v["levers"].values() for e in es]
        leg_v["selection"] = {
            "cells_tried": len(_all_entries),
            "cells_withheld_inert": len(leg_v["inert_cells"]),
            "path_a_pass": sum(1 for e in _all_entries
                               if e.get("verdict") == "PASS"),
            "path_b_candidates": sum(1 for e in _all_entries
                                     if e.get("path_b_candidate")),
            "path_b_wf_pass": sum(1 for e in _all_entries
                                  if e.get("verdict") == "path_b_wf_pass"),
            # The same count split by the gate the name does NOT test, so the
            # roll-up cannot present 18 Path B passes when 12 of them are what a
            # reader means by that. `rate_ungradeable` is its own bucket.
            "path_b_wf_pass_rate_ok": sum(
                1 for e in _all_entries
                if e.get("verdict") == "path_b_wf_pass"
                and e.get("path_b_rate_ok") is True),
            "path_b_wf_pass_rate_failed": sum(
                1 for e in _all_entries
                if e.get("verdict") == "path_b_wf_pass"
                and e.get("path_b_rate_ok") is False),
            "path_b_wf_pass_rate_ungradeable": sum(
                1 for e in _all_entries
                if e.get("verdict") == "path_b_wf_pass"
                and e.get("path_b_rate_ok") is None),
        }
        verdicts[leg] = leg_v

    # `tp_cap_pct` is part of the MEASUREMENT IDENTITY, not a run detail.
    #
    # The same (leg, cell, split) measured at the legacy no-TP geometry and at
    # live parity are two different numbers about two different books
    # (BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP: the harnesses
    # modelled no take-profit at all while production places a 9.9%-clamped one,
    # and `tqqq_trend_long_1d` went 32 -> 75 trades once the real geometry was
    # used). A verdicts file that records `split` but not the geometry lets a
    # downstream corpus mix the two vintages under one label — the same defect
    # one level up from where it was originally found.
    # THE REGIME BOOK IS PART OF THE MEASUREMENT IDENTITY TOO — same argument as
    # `tp_cap_pct` above, one axis over.
    #
    # This sweep never passes `--regime-router`, so `backtest_system` takes its
    # own default (`"off"`) and sets `REGIME_ROUTER_DISABLED=1`. The LIVE router
    # is BASELINE-ON. So every base book measured here is the UNGATED book, and
    # until 2026-08-11 nothing in `verdicts.json` or the corpus said so — 604
    # rows with zero `regime` keys. A function default standing in for the live
    # input, with nothing in the output revealing the substitution, is
    # `diagnostic-provenance-guard` sub-class B + C.
    #
    # What this costs, stated precisely, because it is NOT "the corpus is void":
    #   * DELTA comparisons survive intact. Both arms of a cell share the same
    #     ungated base, so `d_net_r`, `d_max_dd`, Path A's `beats()` and the
    #     walk-forward are all comparisons over ONE consistent population.
    #   * Base-book LEVEL reads do NOT survive for a policy-named leg —
    #     `base_net_r`, `base_rate`, and therefore Path B's derived tolerance
    #     `D_b x (dN/N_b)`, describe a book production refuses to trade.
    # Measured 2026-08-11: 6 of 51 legs / 56 of 604 rows are policy-named.
    off_legs = _policy_off_legs()
    for leg, leg_v in verdicts.items():
        if isinstance(leg_v, dict):
            leg_v["regime_gate_delta"] = regime_gate_delta(leg, off_legs)
    (run_dir / "verdicts.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "split_fallback_date": a.split, "split_mode": a.split_mode,
         "split_target_oos": a.split_target_oos, "tp_cap_pct": a.tp_cap_pct,
         # The router value ACTUALLY used by the harness runs above — recorded
         # rather than asserted, so a future run that does pass the flag records
         # the difference instead of inheriting this comment.
         "regime_router": "off",
         # The FLOOR THAT GRADED THIS RUN. Part of the measurement identity for
         # the same reason `tp_cap_pct` and `regime_router` are: a corpus mixing
         # floor-0 and floor-25 vintages under one label would let an ungraded
         # thin cell and a refused one share a row. A run predating the field
         # records nothing, and the extractor keys None distinctly.
         "min_oos_trades_floor": MIN_OOS_TRADES,
         # THE FEE BAND THIS RUN MEASURED. Identity, not metadata: the same cell at
         # 7.5bps and at 15bps are two different books, and SRQ-20260618-003 is the
         # worked example -- the 5m alts flipped from +50R to -38R across exactly
         # that gap. None = the harness default was used and this run did not
         # declare one.
         "fee_bps_roundtrip": a.fee_bps_roundtrip,
         # THE ENTRY-SELECTIVITY BAND THIS RUN MEASURED. Identity for the same
         # reason as the fee: a leg swept at its declared floor and the same leg
         # swept at an imposed one are two different populations, and the whole
         # point of the arm is that they score differently. None = no override,
         # i.e. every leg ran at its own config-exact declared value -- which is
         # NOT the same statement as "floor 0", since a leg may declare one.
         "min_confidence_override": a.min_confidence_override,
         # THE LEVER-OFF ARM THIS RUN MEASURED. Identity, not metadata: a leg
         # swept with its shipped stale-stop removed and the same leg swept
         # config-exact are two different books, and the arm exists precisely
         # because the difference between them IS the answer. `[]` = no lever
         # removed, i.e. the ordinary config-exact base. Recorded as a sorted
         # list so the corpus key is stable across argument order.
         "without_declared_levers": sorted(without_levers),
         "regime_policy_readable": off_legs is not None,
         "regime_policy_off_legs": sorted(off_legs) if off_legs is not None else None,
         "skipped": skipped, "verdicts": verdicts}, indent=1))
    # THE GEOMETRY THIS LEG ACTUALLY RAN, not the one the run requested.
    #
    # `--tp-cap-pct` is applied by `base_args` ONLY when the leg's family is in
    # LIVE_TP_CAPPED_FAMILIES, because only those units carry
    # `_TP_SENTINEL_CAP_PCT`. The PR-comment banner, however, reads the RUN-LEVEL
    # flag and printed "LIVE-PARITY (capped TP 0.099)" on every leg — including
    # the 8 `ict_scalp` legs and `fvg_range_15m`, whose units carry no cap at all
    # (verified 2026-08-10: `grep -c _TP_SENTINEL_CAP_PCT
    # src/units/strategies/ict_scalp.py` -> 0). A banner asserting a geometry the
    # code did not apply is `diagnostic-provenance-guard` sub-class A, and it is
    # worse here than most: the banner's ONLY job is to tell the reader which
    # geometry produced the numbers underneath it.
    #
    # Emitted from the sweep rather than the workflow because THIS is where the
    # family and the allowlist live; duplicating the allowlist into YAML would be
    # a second source of truth free to drift from the one that decides.
    lines = ["# M20 fleet exit-lever sweep", ""]
    for _leg, _v in verdicts.items():
        _fam = _v.get("family")
        if _fam is None:
            # A leg that never reached the sweep (skipped / harness error)
            # records no family, so the geometry is UNKNOWN -- which is not the
            # same as "applied", and must not be printed as either.
            lines.append(f"- geometry (`{_leg}`): unknown — this leg did not run "
                         f"({_v.get('status') or 'no status recorded'}), so no "
                         f"geometry was applied to report")
            continue
        if a.tp_cap_pct <= 0.0:
            _geo = ("legacy (no TP cap) — the geometry every pre-2026-08-10 "
                    "verdict used, NOT what production runs")
        elif _fam in LIVE_TP_CAPPED_FAMILIES:
            _geo = (f"**capped TP {a.tp_cap_pct} APPLIED** — live parity; this "
                    f"family's unit places the capped TP")
        else:
            _geo = (f"**capped TP NOT APPLIED** (requested {a.tp_cap_pct}) — the "
                    f"`{_fam}` family's unit carries no `_TP_SENTINEL_CAP_PCT`, so "
                    f"there is no cap to model. This is live parity FOR THIS LEG, "
                    f"and it is why the `Live TP reach` table is absent below: the "
                    f"quantity does not exist here, which is a different statement "
                    f"from 'the TP is far away' or 'the cap was off'.")
        lines.append(f"- geometry (`{_leg}`): {_geo}")
    lines.append("")
    for leg, v in verdicts.items():
        if "levers" not in v:
            lines.append(f"- **{leg}**: {v.get('status')} ({v.get('error', '')[:80]})")
            continue
        passes = [e["cell"] for es in v["levers"].values() for e in es
                  if e.get("verdict") == "PASS"]
        # THE VERDICT'S OWN DENOMINATOR, on the same line as the verdict.
        #
        # `beats()` compares net_R and maxDD and requires NO minimum trade
        # count, so a PASS over a 3-trade out-of-sample window prints
        # identically to a PASS over 400. Measured 2026-08-10 on the fleet
        # sweep: `spy_trend_long_1d` returned FOUR PASSes on an OOS window of
        # THREE trades (one cell on +0.08R in-sample), `qqq_trend_long_1d` two
        # on four, `scha_trend_long_1d` three on five — while `mgc_trend_1h`
        # returned one on 97, which is a different kind of claim entirely.
        # A reader scanning this list for promotion candidates cannot tell them
        # apart, which is the unasserted-denominator failure applied to the
        # gate's own output.
        #
        # This REPORTS the counts; it does not change the gate. Adding a
        # minimum-n to `beats()` would change what gets promoted and is the
        # operator's call, not a reporting fix's.
        bb = v.get("base_book") or {}
        n_is = (bb.get("IS") or {}).get("total_trades")
        n_oos = (bb.get("OOS") or {}).get("total_trades")
        n_note = f" · base n IS={n_is} OOS={n_oos}"
        thin = (isinstance(n_oos, (int, float)) and n_oos < _THIN_OOS_TRADES)
        lines.append(f"- **{leg}**{' [PROXY]' if v['proxy'] else ''}: "
                     + (f"PASS {passes}" if passes else "all honest negatives")
                     + n_note
                     + (f" ⚠️ **THIN OOS** (<{_THIN_OOS_TRADES} trades — a verdict "
                        "here is not comparable to one on a full window)"
                        if thin else ""))
    for s in skipped:
        lines.append(f"- **{s['leg']}**: SKIPPED — {s['reason']}")

    # ---- How far away IS the live TP, per leg, measured ----------------------
    reach = [(leg, v["live_tp_reach_r"]) for leg, v in verdicts.items()
             if (v.get("live_tp_reach_r") or {}).get("IS", {}).get("n")]
    if reach:
        lines += ["", "## Live TP reach (measured per leg, not assumed)", "",
                  "`tp = min(entry*(1+cap), entry + tp_r*risk)` in units of R — how "
                  "ordinary a target the 9.9% clamp actually is on THIS leg's frame. "
                  "The 1.3-2.0R figure quoted when "
                  "`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP` was filed "
                  "was an illustrative ATR-derived range, never a measurement; these "
                  "are measurements. Empty when the cap is off (legacy geometry) — "
                  "which is not the same statement as 'the TP is far away'.", "",
                  "| leg | IS n | IS median | IS min–max | OOS n | OOS median | OOS min–max |",
                  "|---|--:|--:|--:|--:|--:|--:|"]
        for leg, r in reach:
            i, o = r["IS"], r["OOS"]
            def _rng(d):
                return (f"{d['min']}–{d['max']}"
                        if d.get("min") is not None else "—")
            lines.append(f"| {leg} | {i['n']} | {i['median']} | {_rng(i)} | "
                         f"{o['n']} | {o['median']} | {_rng(o)} |")

    # ---- Capital-efficiency distribution (Path B input, REPORTED not graded) --
    # One row per leg x cell on the OOS window. The operator sets Path B's two
    # thresholds off this; the sweep asserts nothing about them.
    dist = []
    for leg, v in verdicts.items():
        for lever, entries in (v.get("levers") or {}).items():
            for e in entries:
                cap = (e.get("capital") or {}).get("OOS") or {}
                gate = e.get("gate") or {}
                g_is, g_oos = gate.get("IS") or {}, gate.get("OOS") or {}
                dist.append({
                    "leg": leg, "lever": lever, "cell": e["cell"],
                    "path_a": e.get("verdict"),
                    # BOTH windows, and the binding constraint. Without these a
                    # positive OOS row carrying `is_oos_fail` is unreadable.
                    "is_d_net_r": g_is.get("d_net_r"),
                    "is_d_max_dd": g_is.get("d_max_dd"),
                    "is_fail_reason": g_is.get("reason"),
                    # The GATE's OOS net_R delta. It was missing entirely, and
                    # the table's "Δ netR OOS" column printed the CAPITAL
                    # block's `d_net_total_r` instead — a gate-named header over
                    # a differently-sourced number. They are probably the same
                    # quantity; "probably" is not a provenance, and if they ever
                    # diverge the column lies silently. Both are kept in the
                    # JSON; only this one is printed under a gate label.
                    "oos_d_net_r": g_oos.get("d_net_r"),
                    "oos_d_max_dd": g_oos.get("d_max_dd"),
                    "oos_fail_reason": g_oos.get("reason"),
                    # The split Path B turns on: a cell positive on BOTH windows'
                    # net_R is a different animal from one positive only on OOS.
                    "net_r_up_both_windows": (
                        g_is.get("d_net_r") is not None
                        and g_is["d_net_r"] > 0
                        and g_oos.get("d_net_r") is not None
                        and g_oos["d_net_r"] > 0),
                    "d_net_r_per_capital_day": cap.get("d_net_r_per_capital_day"),
                    "cell_net_r_per_capital_day": cap.get("cell_net_r_per_capital_day"),
                    "base_net_r_per_capital_day": cap.get("base_net_r_per_capital_day"),
                    "d_net_total_r": cap.get("d_net_total_r"),
                    "net_r_retained_frac": cap.get("net_r_retained_frac"),
                    "d_mean_bars_held": cap.get("d_mean_bars_held"),
                })
    measured = [d for d in dist if d["d_net_r_per_capital_day"] is not None]
    (run_dir / "capital_distribution.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": "OOS", "split_fallback_date": a.split, "split_mode": a.split_mode,
         "split_target_oos": a.split_target_oos,
        # ALWAYS STATE THE POPULATION — a distribution quoted without its
        # denominator is the failure this repo has a standing rule against.
        "cells_total": len(dist), "cells_measured": len(measured),
        "cells_unmeasured": len(dist) - len(measured),
        "note": ("Path B thresholds are UNSET by design; these rows are the "
                 "evidence for setting them. `null` means the rate was not "
                 "measurable, NOT zero."),
        "rows": sorted(measured,
                       key=lambda d: -(d["d_net_r_per_capital_day"] or 0))
                + [d for d in dist if d["d_net_r_per_capital_day"] is None],
    }, indent=1))
    # BOTH-WINDOW split. A cell positive on OOS alone is the classic
    # small-window artifact; one positive on IS AND OOS is a real candidate.
    # Collapsing them (which the OOS-only table did) makes a Path B threshold
    # unsettable — 2026-08-10, 18 cells read positive-but-failing with no way to
    # tell which kind they were.
    # FOUR populations, not two. The previous version computed `oos_only` as the
    # plain COMPLEMENT of `both` and printed it under the label "only
    # out-of-sample" — a real count under a wrong name (unprovenanced
    # diagnostic output, sub-class A). It inverted the diagnosis on the first
    # scalp leg it ran against: ict_scalp_sol_15m's `be_touch_arm` is IS +1.138
    # / OOS -1.8913, the OVERFIT signature, and the header called it an
    # out-of-sample-only improver. The IS-only and OOS-only shapes mean opposite
    # things and must never share a bucket.
    def _up(v):
        return v is not None and v > 0

    def _known(d):
        return d.get("is_d_net_r") is not None and d.get("oos_d_net_r") is not None

    graded = [d for d in measured if _known(d)]
    # A cell missing either window's delta is NOT sorted into a bucket — "we
    # could not compare" is its own state, never folded into "did not improve".
    ungraded = [d for d in measured if not _known(d)]
    both = [d for d in graded if _up(d.get("is_d_net_r")) and _up(d.get("oos_d_net_r"))]
    is_only = [d for d in graded
               if _up(d.get("is_d_net_r")) and not _up(d.get("oos_d_net_r"))]
    oos_only = [d for d in graded
                if not _up(d.get("is_d_net_r")) and _up(d.get("oos_d_net_r"))]
    neither = [d for d in graded
               if not _up(d.get("is_d_net_r")) and not _up(d.get("oos_d_net_r"))]
    lines += ["", "## Capital efficiency (Path B input — reported, not graded)",
              "", f"Measured on **{len(measured)} of {len(dist)}** cells "
              f"({len(dist) - len(measured)} unmeasurable → `null`, not 0"
              + (f"; {len(ungraded)} measured but missing a window delta, "
                 "left ungraded" if ungraded else "") + "). "
              f"net_R direction: **{len(both)}** up on BOTH windows (the Path B "
              f"population) · **{len(is_only)}** up on IS only (the OVERFIT shape "
              f"— helps in-sample, hurts out) · **{len(oos_only)}** up on OOS only "
              f"(the small-window artifact the walk-forward exists to catch) · "
              f"**{len(neither)}** up on neither. "
              "`why` names the binding constraint that failed Path A.", "",
              "| leg | cell | PathA | why (IS / OOS) | Δ cap/day | Δ netR IS | Δ netR OOS "
              "| Δ maxDD IS | Δ maxDD OOS | shape |",
              "|---|---|---|---|--:|--:|--:|--:|--:|:-:|"]
    def _shape(d):
        """IS/OOS direction as a word, because the two one-sided shapes mean
        OPPOSITE things and a shared bucket hides that."""
        if not _known(d):
            return "?"
        i, o = _up(d.get("is_d_net_r")), _up(d.get("oos_d_net_r"))
        return "both" if i and o else ("IS-only" if i else
                                       ("OOS-only" if o else "neither"))

    for d in sorted(measured,
                    key=lambda d: (not d.get("net_r_up_both_windows"),
                                   -(d["d_net_r_per_capital_day"] or 0)))[:30]:
        why = f"{d.get('is_fail_reason') or 'ok'} / {d.get('oos_fail_reason') or 'ok'}"
        lines.append(
            f"| {d['leg']} | {d['cell']} | {d['path_a']} | {why} | "
            f"{d['d_net_r_per_capital_day']} | "
            f"{d.get('is_d_net_r')} | {d.get('oos_d_net_r')} | "
            # Δ maxDD IS is the number a Path B decision turns on: measured
            # 2026-08-10, EVERY true Path B cell (both windows' net_R up, capital
            # up) was blocked by `maxdd_worse` and NONE by `net_r_worse`. The
            # threshold the operator has to set is therefore a DRAWDOWN
            # tolerance, and printing only the OOS side left it unsizeable.
            f"{d.get('is_d_max_dd')} | {d.get('oos_d_max_dd')} | "
            f"{_shape(d)} |")
    # ---- The DERIVED drawdown tolerance, per Path B candidate ---------------
    # Operator directive 2026-08-10: an evidence-based number, not a guess. The
    # evidence is each leg's OWN net_R-per-drawdown rate; `allowed` is that rate
    # expressed as an allowance for this leg's net_R gain, and `asked` is the
    # drawdown the cell actually wants. headroom = allowed - asked.
    pb = []
    for _leg, _v in verdicts.items():
        for _entries in (_v.get("levers") or {}).values():
            for _e in _entries:
                if _e.get("dd_exchange_rate"):
                    pb.append((_leg, _e))
    if pb:
        lines += ["", "## Path B — the drawdown tolerance each leg's own book implies",
                  "",
                  "No fleet-wide scalar. A cell may deepen drawdown only if net_R per unit "
                  "of drawdown does not get worse (`N_c/D_c >= N_b/D_b`), so the allowance "
                  "is **derived per leg** from that leg's measured base: "
                  "`allowed = base_maxDD x (d_netR / base_netR)`. **Positive headroom on "
                  "BOTH windows** means the cell buys drawdown at least as cheaply as the "
                  "strategy already does. `ungradeable` is NOT a pass — a base book that "
                  "loses money has no exchange rate to preserve.", "",
                  # THE VERDICT NAME IS NOT THE GATE. Measured over the 43-leg
                  # corpus, 6 of 18 `path_b_wf_pass` rows FAIL this table's `rate
                  # ok` column -- the verdict says only that the net_R gain held
                  # up across folds. Stated here because this is the table a
                  # reader scans when deciding, and the two were previously only
                  # reconcilable by reading both columns and knowing they meant
                  # different things.
                  "**`path_b_wf_pass` does NOT mean `rate ok`** -- that verdict "
                  "says only that the net_R gain held across folds. Read the "
                  "`rate ok` column, and read `grant%` beside it.", "",
                  # `grant%` = dN/N_b as a PERCENTAGE OF THE BASE BOOK'S WHOLE
                  # DRAWDOWN, which is what `allowed` literally is. It was
                  # derivable from the existing columns and nobody derived it,
                  # so a cell being granted 170% of the base book's entire
                  # drawdown (tlt_pullback_1h trail4) read as an ordinary row.
                  "`grant%` is `allowed` as a share of the base book's ENTIRE "
                  "drawdown. Above 100% the allowance has stopped being a share "
                  "of the risk budget and become an expansion of it.", "",
                  "| leg | cell | win | base netR | base maxDD | d netR | asked d maxDD "
                  "| allowed d maxDD | grant% | headroom | rate ok |",
                  "|---|---|---|--:|--:|--:|--:|--:|--:|--:|:-:|"]
        for _leg, _e in pb:
            for _w in ("IS", "OOS"):
                _r = _e["dd_exchange_rate"][_w]
                if _r.get("reason"):
                    lines.append(
                        f"| {_leg} | {_e['cell']} | {_w} | {_r.get('base_net_r')} "
                        f"| {_r.get('base_max_dd')} | {_r.get('d_net_r')} "
                        f"| {_r.get('d_max_dd')} | - | - | - | "
                        f"ungradeable: {_r['reason']} |")
                    continue
                # `base_net_r > 0` is guaranteed here -- a non-positive base
                # returned `base_unprofitable` above and took the branch that
                # continues, so this division cannot be by zero or invert sign.
                _grant = round(100.0 * _r['d_net_r'] / _r['base_net_r'])
                lines.append(
                    f"| {_leg} | {_e['cell']} | {_w} | {_r['base_net_r']} "
                    f"| {_r['base_max_dd']} | {_r['d_net_r']} | {_r['d_max_dd']} "
                    f"| {_r['allowed_d_max_dd']} | {_grant}% | {_r['headroom']} "
                    f"| {'Y' if _r['passes'] else 'N'} |")
    (run_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print(f"capital: {len(measured)}/{len(dist)} cells measured")
    print("done ->", run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
