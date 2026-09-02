#!/usr/bin/env python3
"""Run the per-(trend-regime, direction) net-R matrix for the regime-coverage
debt roster — the rec #5 follow-up for the strategies the sandbox can't reach.

For each `coverage_debt` strategy (config/regime_coverage_exemptions.yaml) it:
  1. classifies the harness (Donchian trend -> backtest_trend.py; pullback ->
     backtest_pullback.py; TTM-style BB-inside-KC squeeze -> backtest_squeeze.py)
     and extracts the EXACT live params from config/strategies.yaml,
  2. resolves the candle feed for the symbol — Binance-vision for `*USDT` crypto,
     Yahoo (yfinance) for equities/ETFs, and Yahoo continuous futures for
     MES/MGC/MHG (ES=F/GC=F/HG=F, mirroring the dashboard `_yf_ticker`),
  3. runs the harness `--emit-trades` then `regime_tag_emitted.py` and collects
     the matrix JSON, tagged with a **fidelity** flag.

**Fidelity.** A strategy is `faithful` when the base harness models every lever
its config declares. The pullback harness exposes the vol-skip / stale-exit /
trail-vol lever flags, so those variants run faithfully.

**Trend harness, updated 2026-08-08 (convergence step (a) of
`BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE`).** There used to
be TWO `backtest_trend.py` — this matrix ran the one WITHOUT the M20/M21 levers,
so a Donchian variant declaring trail-decay / giveback / bank / confirm-bars /
skip-hours / vol-skip / trail-vol measured `approximate` purely because the
harness the pipeline invokes had no flag for it. That was a WIRING fact, not a
capability gap. All 15 levers are now ported into `scripts/backtest_trend.py`
(the live-faithful engine — it freezes the entry bar's ATR for the trail, which
is what `trend_donchian.monitor()` does; the sibling `scripts/research` copy
trails off a rolling ATR and produces a different trade set), so those variants
run **faithfully**.
Design-doc §5f has the measurement.

**`exit_head_*` is location-dependent, not permanently unmodellable** — but that
is a fact about MEASURABILITY, not about this harness run's fidelity, and the two
are recorded separately. The head is a self-contained artifact published to
`runtime_logs/trainer_mirror/exit_head/`, so `exit_head_replayable()` asks whether
a servable head is actually loadable HERE: on the trainer / live VM it is, so the
replay (`scripts/ml/exit_head_replay.py`) can re-resolve each trade's exit; on a
GitHub-hosted runner there is no mirror, so the gap cannot be measured at all.
Fail-closed: any error verifying ⇒ not replayable, never a silent certification.

**`fidelity` stays `approximate` either way, deliberately.** It grades the HARNESS
RUN, and the harness has no `--exit-head-*` flag — the replay is a SEPARATE pass
over the emitted trades. Upgrading the leg to `faithful` because a head is merely
loadable would claim the row's numbers account for an exit head that never touched
them, on the field the research→results promotion gate reads. So
`annotate_exit_head_replayability()` records `exit_head_replayable` +
`exit_head_deferred_to_replay` as their own fields instead. (An earlier revision
folded this into `fidelity` via a conditional that turned out to be INERT — the
union could only add keys `build_harness_cmd` had already listed;
`BL-20260808-INERT-CONDITIONAL-SHIPPED-AS-A-BEHAVIOUR-CHANGE`.)

Yahoo needs network the sandbox firewalls, so this is built to run on a free
GitHub-hosted runner (see .github/workflows/regime-debt-matrix.yml). The crypto
path is exercisable in-sandbox for testing.

Usage:
  python scripts/research/regime_debt_matrix.py --only trend_donchian_eth_4h --json
  python scripts/research/regime_debt_matrix.py --crypto-only --workdir /tmp/rdm --json
  python scripts/research/regime_debt_matrix.py --json > matrix.json   # full roster
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from typing import Optional

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- feed resolution -------------------------------------------------------
# Yahoo tickers for the symbols that need translating (mirror dashboard _yf_ticker).
_YF_TICKER = {"MES": "ES=F", "MGC": "GC=F", "MHG": "HG=F", "XAUUSD": "GC=F"}
# Bybit interval code fetch_backtest_candles speaks, per timeframe.
_TF_TO_BYBIT_INT = {"1h": "60", "2h": "120", "4h": "240", "1d": "D"}
# Yahoo base interval to fetch per timeframe (Yahoo has no 2h/4h -> fetch 60m + resample).
_TF_TO_YF_INT = {"1h": "60m", "2h": "60m", "4h": "60m", "1d": "1d"}

# Plain trend param keys the base harness fully models; anything else on a
# Donchian strategy is an unmodelled lever -> approximate.
_TREND_PLAIN = {"model", "signal_prefixes", "enabled", "execution", "timeframe",
                "symbols", "donchian", "atr_period", "atr_stop_mult", "trail_mult",
                "tp_r", "min_confidence", "long_only", "adx_min", "adx_max",
                "adx_period", "shadow_model_ids", "description"}
# Trend lever config-key -> harness flag (levers the trend harness DOES model,
# so a trend strategy carrying ONLY these is faithful, not approximate). The
# stale-exit lever was ported into scripts/backtest_trend.py as the rec #5
# follow-up so trend_donchian_sol's chop-long cell can be re-measured with its
# declared exit lever ON (BL-20260717-REGIME-COVERAGE-DEBT). exit_head_* stay in
# _UNREPLAYABLE — levers THIS harness cannot model. NOT a statement about the system.
# (Corrected 2026-07-30, operator: the previous comment here read "an ML exit head can
# never be replayed offline", which is FALSE and was propagated into a research
# conclusion. The M20 toolchain replays exactly this: build_intrabar_exit_panel.py
# builds the per-bar in-trade panel and analyze_exit_head.py SIMULATES the head's exit
# decisions per trade -- first bar the head says exit, the trade realizes its
# mark-to-market R there -- under grouped/purged/embargoed walk-forward, scored against
# the baseline fixed SL/TP exit. So exit_head_* is out of scope for backtest_trend.py,
# and that is all this set means. See docs/research/regime-debt-matrix-corrected-cost-
# 2026-07-30.md A6 for the correction and the actual re-audit path.)
#
# `side_filter` (added live 2026-07-30 by #7966, with matching --side-filter flags in
# backtest_trend.py + backtest_pullback.py) MUST be forwarded. Two LIVE, enabled
# strategies carry `side_filter: short` — sol_pullback_2h and trend_donchian_xrp_4h.
# Without forwarding, the matrix measures BOTH legs for a strategy that only ever
# trades short live, so any cell read off that row rests partly on long trades the
# strategy will never take. Caught while wiring the squeeze harness, hours after
# #7966 landed; the capability was added correctly, it just did not know these lever
# maps existed. BL-20260730-SIDE-FILTER-NOT-FORWARDED.
_TREND_LEVER_FLAG = {
    "stale_exit_bars": "--stale-exit-bars", "stale_exit_below_r": "--stale-exit-below-r",
    "side_filter": "--side-filter",
    # Ported into scripts/backtest_trend.py on 2026-08-08 (convergence step (a) of
    # BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE). These 15
    # levers previously lived ONLY in the scripts/research copy, so a
    # variant declaring one measured `approximate` purely because the harness the
    # pipeline runs had no flag for it — a WIRING fact, not a capability gap
    # (design-doc §5f). The harness now models them, so they are faithful.
    "bank_frac": "--bank-frac", "bank_at_r": "--bank-at-r",
    "giveback_min_mfe_r": "--giveback-min-mfe-r", "giveback_r": "--giveback-r",
    "trail_decay_arm_r": "--trail-decay-arm-r",
    "trail_decay_stall_bars": "--trail-decay-stall-bars",
    "trail_decay_tight_mult": "--trail-decay-tight-mult",
    "confirm_bars": "--confirm-bars",
    "skip_hours": "--skip-hours",
    "vol_skip_above_pctl": "--vol-skip-above-pctl",
    "vol_skip_below_pctl": "--vol-skip-below-pctl",
    "vol_pctl_window": "--vol-pctl-window",
    "trail_vol_above_pctl": "--trail-vol-above-pctl",
    "trail_vol_below_pctl": "--trail-vol-below-pctl",
    "trail_vol_tight_mult": "--trail-vol-tight-mult",
}
# Pullback lever config-key -> harness flag (these the pullback harness DOES model).
_PB_LEVER_FLAG = {
    "stale_exit_bars": "--stale-exit-bars", "stale_exit_below_r": "--stale-exit-below-r",
    "vol_skip_below_pctl": "--vol-skip-below-pctl", "vol_skip_above_pctl": "--vol-skip-above-pctl",
    "trail_vol_below_pctl": "--trail-vol-below-pctl", "trail_vol_above_pctl": "--trail-vol-above-pctl",
    "trail_vol_tight_mult": "--trail-vol-tight-mult", "vol_pctl_window": "--vol-pctl-window",
    "side_filter": "--side-filter",  # see the note on _TREND_LEVER_FLAG above
}
_PB_PLAIN = {"model", "signal_prefixes", "enabled", "execution", "timeframe", "symbols",
             "trend_lookback", "pullback_lookback", "pullback_frac", "atr_period",
             "atr_stop_mult", "trail_mult", "tp_r", "min_confidence", "adx_min",
             "adx_max", "adx_period", "shadow_model_ids", "description"}
# Squeeze (TTM-style BB-inside-KC) lever config-key -> harness flag. The squeeze
# harness (scripts/backtest_squeeze.py) is the SAME harness that validated the
# strategy in docs/audits/squeeze-breakout-complement-2026-05-24.md — it was simply
# never wired into classify(), so squeeze_breakout_4h fell through to
# "unclassifiable" and its FOUR live regime cells could not be re-audited at all
# (BL-20260730-SQUEEZE-NO-HARNESS, found by the 2026-07-30 authored-cell re-audit).
_SQZ_LEVER_FLAG = {
    "stale_exit_bars": "--stale-exit-bars", "stale_exit_below_r": "--stale-exit-below-r",
    "giveback_min_mfe_r": "--giveback-min-mfe-r", "giveback_r": "--giveback-r",
    "timeout_bars": "--timeout-bars", "cooldown_bars": "--cooldown-bars",
}
_SQZ_PLAIN = {"model", "signal_prefixes", "enabled", "execution", "timeframe", "symbols",
              "bb_period", "bb_std", "kc_mult", "atr_period", "atr_stop_mult",
              "trail_mult", "min_confidence", "shadow_model_ids", "description"}
# NOTE `side_filter` is absent from BOTH _SQZ_PLAIN and _SQZ_LEVER_FLAG on purpose:
# backtest_squeeze.py has no --side-filter flag (only the trend + pullback harnesses
# gained one in #7966). So a squeeze strategy declaring side_filter degrades to
# `approximate` and names it as an omitted lever — the honest outcome. Do NOT "fix"
# that by adding it to _SQZ_PLAIN; that would silently claim the harness applies a
# side filter it does not implement. Porting --side-filter into backtest_squeeze.py is
# the real fix if a squeeze variant ever needs it.
# `tp_r` is DELIBERATELY not in _SQZ_PLAIN. scripts/backtest_squeeze.py has no
# --tp-r flag: it models the Chandelier trail as the sole profit-exit, which is
# what src/units/strategies/squeeze_breakout_4h.py itself documents ("No fixed
# profit target — the trail is the sole profit-exit; ``tp`` is [a formality]").
# So the omission is harmless ONLY while tp_r is far enough out that it cannot
# bind before the trail fires. That is a CHECKED bound, not an assumption: below
# the threshold `tp_r` is reported as an omitted lever and the row degrades to
# `approximate`, which correctly blocks cell authoring.
#
# 20R is chosen as comfortably beyond any plausible 3.5-ATR-trail exit while
# still failing loudly if someone sets a real target (e.g. tp_r: 3). The live
# config is tp_r: 50.0. The STRONGER form of this check is empirical — verify no
# emitted trade's MFE ever reached tp_r on the sample — which needs a post-run
# fidelity adjustment in both callers: BL-20260730-SQZ-TPR-EMPIRICAL-CHECK.
_SQZ_TP_R_NONBINDING = 20.0
# Levers no offline harness can replay.
_UNREPLAYABLE = {"exit_head_model", "exit_head_threshold", "exit_head_action"}


def exit_head_replayable(cfg: dict) -> bool:
    """Can this leg's declared exit head actually be REPLAYED here?

    `_UNREPLAYABLE` used to be unconditional, which made a location fact read as
    a capability fact: the exit head is a SELF-CONTAINED artifact (booster inline)
    published to ``runtime_logs/trainer_mirror/exit_head/``, so it is replayable
    wherever that mirror exists — the trainer and the live VM — and not on a
    GitHub-hosted runner, which has no copy. (Design-doc §5e recorded this as
    "needs the model registry at inference"; that premise was wrong, and an
    overstated impossibility closes off work.)

    So this asks the environment, not the config: a leg declaring
    ``exit_head_model`` is faithful **only where a servable head is loadable**,
    and honestly `approximate` everywhere else. Fail-CLOSED on any error — an
    unreadable artifact dir means we could not verify, which is not the same as
    replayable, and must never silently certify a leg as faithful.
    """
    if not cfg.get("exit_head_model"):
        return True                       # nothing declared → nothing to replay
    tf = str(cfg.get("timeframe") or "")
    symbols = cfg.get("symbols") or []
    if not tf or not symbols:
        return False
    try:
        sys.path.insert(0, REPO)
        from scripts.ml.exit_head_replay import default_artifact_dir, load_heads
        load_heads(default_artifact_dir(), tf, str(symbols[0]))
        return True
    except Exception:  # noqa: BLE001  # allow-silent: FAIL-CLOSED probe — the swallowed outcome is "NOT faithful", the conservative answer. This is the inverse of the silent-empty class: it cannot hide a failure as a clean result, it can only refuse to certify. Every caller labels the leg `approximate` and names the omitted levers, so the degradation is reported, never hidden.
        return False


def annotate_exit_head_replayability(cfg: dict, row: dict, omitted: list[str]) -> None:
    """Record WHERE this leg's exit-head gap can be measured. Both callers use
    this so the two cannot drift.

    Sets two fields, deliberately kept separate from ``fidelity``:

    * ``exit_head_replayable`` — does a servable head load HERE?
    * ``exit_head_deferred_to_replay`` — the declared exit-head levers that
      ``scripts/ml/exit_head_replay.py`` can resolve here. Empty where no head
      loads, and empty when the leg declares no head at all.

    **`fidelity` is deliberately NOT upgraded when a head is present**, and that
    is the whole point of the field split. `fidelity` grades the HARNESS RUN,
    and the harness genuinely does not apply the exit head — there is no
    `--exit-head-*` flag; the replay is a SEPARATE pass over the emitted trades.
    Flipping a leg to `faithful` merely because a head is loadable would claim
    the row's numbers account for an exit head that never touched them, on the
    field the research->results promotion gate reads. A location fact earns a
    location field, not a quality upgrade.

    This replaces an earlier attempt that made the `_UNREPLAYABLE` fold
    conditional. That was **inert**: `build_harness_cmd` already lists every
    cfg key with no harness flag in `omitted` (exit_head_* among them), so the
    fold could only ever UNION keys that were present regardless — identical
    output whether or not a head loaded. Caught by exercising both branches;
    the passing test only ever ran the no-head one.
    """
    row["exit_head_replayable"] = exit_head_replayable(cfg)
    declared = sorted(k for k in cfg if k in _UNREPLAYABLE)
    row["exit_head_deferred_to_replay"] = (
        [k for k in declared if k in set(omitted)] if row["exit_head_replayable"]
        else [])


def classify(cfg: dict) -> str | None:
    if "donchian" in cfg:
        return "trend"
    if "trend_lookback" in cfg or "pullback_frac" in cfg:
        return "pullback"
    # TTM-style squeeze: Bollinger Bands contracting inside the Keltner Channels.
    # Checked AFTER trend/pullback so a strategy carrying both shapes keeps its
    # existing harness (no silent re-routing of an already-measured strategy).
    if "kc_mult" in cfg and "bb_period" in cfg:
        return "squeeze"
    return None


def resolve_feed(symbol: str, timeframe: str) -> dict:
    if symbol.upper().endswith("USDT"):
        return {"source": "binance", "ticker": symbol,
                "interval": _TF_TO_BYBIT_INT.get(timeframe, "D"), "resample": timeframe}
    return {"source": "yahoo", "ticker": _YF_TICKER.get(symbol.upper(), symbol),
            "interval": _TF_TO_YF_INT.get(timeframe, "1d"), "resample": timeframe}


def _fetch_csv(feed: dict, days: int, out: str) -> None:
    """Populate `out` with a timestamp,open,high,low,close,volume CSV."""
    if feed["source"] == "binance":
        # Force Binance-vision: fetch_backtest_candles defaults to source=auto,
        # which tries Bybit first — Bybit geoblocks US GH-runners (403), wasting
        # ~14s of retries per symbol before it falls back. Skip straight to the
        # feed that actually works off-VM.
        subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts/ops/fetch_backtest_candles.py"),
             "--symbol", feed["ticker"], "--interval", feed["interval"],
             "--days", str(days), "--output", out, "--source", "binance_vision"],
            check=True, cwd=REPO)
        return
    # Yahoo via yfinance -> write the same CSV shape the harnesses read.
    import pandas as pd
    import yfinance as yf
    period = f"{min(days, 720)}d" if feed["interval"].endswith("m") else f"{days}d"
    df = yf.download(feed["ticker"], period=period, interval=feed["interval"],
                     auto_adjust=False, progress=False, threads=False)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance returned no rows for {feed['ticker']}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower).reset_index()
    # first column is the datetime index (Datetime/Date) -> canonical `timestamp`
    df = df.rename(columns={df.columns[0]: "timestamp"})
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    df[cols].to_csv(out, index=False)


def roundtrip_fee_bps(symbol: str) -> float:
    """Venue-appropriate round-trip fee in bps for *symbol* — the research-harness
    counterpart of the live close path's resolver.

    Delegates to ``core.profile_loader.roundtrip_fee_bps_for``, which returns
    ``0.0`` for a commission-free venue (US equity/ETF on Alpaca) and ``None``
    meaning "no venue-specific rate — use the estimator default" for
    crypto/futures/fx. ``None`` (and any resolver failure) falls back to
    ``trade_costs.DEFAULT_FEE_BPS_ROUNDTRIP`` so the default lives in exactly one
    place and is never duplicated here.

    Why this exists (BL-20260730-RESEARCH-VENUE-FEE): this function used to be the
    literal constant ``7.5`` for EVERY symbol, so all 14 commission-free
    ``(alpaca, spot)`` instruments (SPY QQQ TQQQ QLD GLD IWM TLT IEF SLV USO GDX
    SPLG IAUM SCHA) were charged a crypto-perp fee — a ~25x over-charge worth
    ~0.04-0.12 R/trade. Over-charging can only make a strategy look WORSE, so the
    bug's signature is **false OFF cells** (gating a leg that is actually fine),
    never a fabricated edge. #7930 fixed the identical bug in the live close-path
    writer (``database._record_trade_cost_estimate``); the research harness that
    sources Tier-3 regime cells was missed, which is why the equity/ETF matrix
    (#7918) and its walk-forward verdicts (#7920-#7924) need re-running.
    """
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    try:
        from src.core.profile_loader import roundtrip_fee_bps_for
        from src.runtime.trade_costs import DEFAULT_FEE_BPS_ROUNDTRIP
    except Exception as exc:  # noqa: BLE001
        # Loud, not silent: a resolver miss would quietly restore the 25x
        # over-charge this function exists to remove. Fall back to the documented
        # default and SAY SO, so a degraded run is visible in the log.
        print(f"[fee] resolver unavailable ({exc}); falling back to 7.5 bps for {symbol}",
              file=sys.stderr)
        return 7.5
    resolved = roundtrip_fee_bps_for(symbol)
    return float(DEFAULT_FEE_BPS_ROUNDTRIP if resolved is None else resolved)


def build_harness_cmd(name: str, cfg: dict, harness: str, csv: str, resample: str,  # inert: name — the argv is built from cfg/harness/csv; the cell name is used by the CALLER for reporting, never by the command
                      emit: str, jout: str,
                      fee_override: Optional[float] = None
                      ) -> tuple[list[str], bool, list[str]]:
    """Return (argv, faithful, omitted_levers).

    ``fee_override`` (bps) forces the round-trip fee for the fixed-window fee
    A/B (BL-20260730-FEE-AB-FIXED-WINDOW) — pass e.g. 0.0 and 7.5 across two arms
    over the SAME fetched candle CSV so the per-cell delta isolates the fee
    effect from the window slide that confounds two-run comparisons. ``None``
    (the default) keeps the venue-appropriate resolved fee — every existing
    caller is byte-for-byte unchanged.
    """
    py = sys.executable
    symbol = cfg["symbols"][0]
    fee = roundtrip_fee_bps(symbol) if fee_override is None else float(fee_override)
    common = ["--data", csv, "--symbol", symbol, "--resample", resample,
              "--atr-period", str(cfg.get("atr_period", 14)),
              "--atr-stop-mult", str(cfg.get("atr_stop_mult", 2.5)),
              "--trail-mult", str(cfg.get("trail_mult", 5.0)),
              "--min-confidence", str(cfg.get("min_confidence", 0.0)),
              "--fee-bps-roundtrip", str(fee),
              "--emit-trades", emit, "--json", jout]
    # ADX gating is supported by the trend + pullback harnesses but NOT by
    # scripts/backtest_squeeze.py (it has no --adx-* flags). Kept OUT of `common`
    # so an adx-carrying squeeze strategy degrades honestly to `approximate`
    # instead of crashing the subprocess with "unrecognized arguments" — a
    # harness failure reads as a fetch/harness error, which would misattribute a
    # missing capability as a broken run.
    adx_flags: list[str] = []
    if cfg.get("adx_min") is not None:
        adx_flags += ["--adx-min", str(cfg["adx_min"])]
    if cfg.get("adx_max") is not None:
        adx_flags += ["--adx-max", str(cfg["adx_max"])]
    if harness != "squeeze":
        common += adx_flags
    omitted: list[str] = []
    if harness == "trend":
        argv = [py, os.path.join(REPO, "scripts/backtest_trend.py"),
                "--donchian", str(cfg.get("donchian", 20))] + common
        if cfg.get("long_only"):
            argv.append("--long-only")
        # pass every trend lever the harness can now model (stale-exit)
        for k, flag in _TREND_LEVER_FLAG.items():
            if cfg.get(k) is not None:
                argv += [flag, str(cfg[k])]
        omitted = sorted(k for k in cfg
                         if k not in _TREND_PLAIN and k not in _TREND_LEVER_FLAG)
        faithful = not omitted
    elif harness == "squeeze":
        argv = [py, os.path.join(REPO, "scripts/backtest_squeeze.py"),
                "--bb-period", str(cfg.get("bb_period", 20)),
                "--bb-std", str(cfg.get("bb_std", 2.0)),
                "--kc-mult", str(cfg.get("kc_mult", 1.0))] + common
        for k, flag in _SQZ_LEVER_FLAG.items():
            if cfg.get(k) is not None:
                argv += [flag, str(cfg[k])]
        omitted = sorted(k for k in cfg
                         if k not in _SQZ_PLAIN and k not in _SQZ_LEVER_FLAG
                         and k != "tp_r")
        # tp_r counts as omitted only when it is near enough to actually bind —
        # see _SQZ_TP_R_NONBINDING. A missing tp_r is the harness's own default
        # (trail-only), so it is not an omission either.
        tp_r = cfg.get("tp_r")
        if tp_r is not None and float(tp_r) < _SQZ_TP_R_NONBINDING:
            omitted = sorted(set(omitted) | {"tp_r"})
        faithful = not omitted
    else:
        argv = [py, os.path.join(REPO, "scripts/backtest_pullback.py"),
                "--trend-lookback", str(cfg.get("trend_lookback", 40)),
                "--pullback-lookback", str(cfg.get("pullback_lookback", 10)),
                "--pullback-frac", str(cfg.get("pullback_frac", 0.5))] + common
        # pass every lever the pullback harness can model
        for k, flag in _PB_LEVER_FLAG.items():
            if cfg.get(k) is not None:
                argv += [flag, str(cfg[k])]
        omitted = sorted(k for k in cfg
                         if k not in _PB_PLAIN and k not in _PB_LEVER_FLAG)
        faithful = not omitted
    return argv, faithful, omitted


def _regime_tag(csv: str, emit: str, resample: str, label: str) -> dict:
    """Run regime_tag_emitted over an emitted-trades file → its matrix dict."""
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/research/regime_tag_emitted.py"),
         "--trades", emit, "--data", csv, "--resample", resample,
         "--label", label, "--json"],
        check=True, cwd=REPO, capture_output=True)
    return json.loads(out.stdout.decode())


def _fee_ab_diff(arms: dict) -> dict:
    """Per-(regime, side) net-R delta of each higher-fee arm vs the lowest-fee arm.

    The trade SET is identical across arms (fees do not feed back into signal
    generation), so `diff = high_fee − low_fee` is a CLEAN per-cell fee
    attribution — negative = the phantom drag the fee imposes on that cell. This
    is what BL-20260730-FEE-AB-FIXED-WINDOW asked for: a measured number, not the
    window-slide-confounded inference the two-run comparison could only offer.
    """
    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    keys = sorted(arms, key=lambda k: float(k))
    if len(keys) < 2:
        return {"note": "need >=2 arms to diff"}
    base = keys[0]
    base_m = (arms.get(base) or {}).get("by_regime", {}) or {}
    out: dict = {"base_fee_bps": base, "by_regime": {}}
    for hi in keys[1:]:
        hi_m = (arms.get(hi) or {}).get("by_regime", {}) or {}
        for reg in sorted(set(base_m) | set(hi_m)):
            b, h = base_m.get(reg, {}), hi_m.get(reg, {})
            cell = out["by_regime"].setdefault(reg, {})
            for side in ("net_r", "long_r", "short_r"):
                bv, hv = _f(b.get(side)), _f(h.get(side))
                cell[f"d_{side}__{base}_to_{hi}"] = (
                    round(hv - bv, 4) if (bv is not None and hv is not None) else None)
            # carry the n so a reader can weight the delta (small-n = drift-prone)
            cell["long_n"] = h.get("long_n", b.get("long_n"))
            cell["short_n"] = h.get("short_n", b.get("short_n"))
    return out


def emit_trades_for(name: str, cfg: dict, workdir: str, days: int, *,
                    symbol_override: Optional[str] = None,
                    fee_override: Optional[float] = None) -> dict:
    """Fetch the feed + run the config-exact harness ``--emit-trades`` for ONE
    ``(strategy, symbol)`` → a per-trade JSONL, and RETURN where it landed.

    The shared emit primitive behind the A1 backtest-augment runner
    (``scripts/ml/backtest_augment_runner.py``) and the GLD Track-B compat gate
    (``gld-compat-matrix.yml``). It is the fetch → ``build_harness_cmd`` →
    subprocess half of :func:`run_one`, WITHOUT the ``_regime_tag`` matrix step:
    both consumers want the RAW emitted trades (``{strategy, entry_time,
    direction, gross_r, net_r, confidence}``), not the per-regime bucketing.
    ``run_one`` is left byte-for-byte unchanged (it fetches independently), so no
    existing matrix caller is touched — the small duplicate fetch is deliberate.

    ``symbol_override`` runs the SAME strategy config on a DIFFERENT symbol — the
    A1 per-symbol replay: ``trend_donchian``@1h on BTC/ETH/SOL from one config.
    The timeframe stays the config's own (``trend_donchian`` stays 1h on every
    symbol), which is exactly the pooled manifest's roster.

    ``fee_override`` (bps) forces the harness round-trip fee (GLD Track B passes
    ``0.0`` for the commission-free ETF); ``None`` keeps the venue-resolved fee
    (crypto → its real Binance/Bybit fee, per the corrected-cost contract).

    Returns a dict: ``{strategy, symbol, timeframe, harness, feed, fidelity,
    omitted_levers, fee_bps_roundtrip, emit_path, n_emitted}`` and, on any
    failure, ``error`` (with ``emit_path=None``). Failures are NAMED in the row,
    never swallowed — the caller decides whether a partial roster is fatal.
    """
    eff = {**cfg, "symbols": [symbol_override]} if symbol_override else dict(cfg)
    harness = classify(eff)
    sym = (eff.get("symbols") or [None])[0]
    tf = eff.get("timeframe")
    row: dict = {"strategy": name, "symbol": sym, "timeframe": tf,
                 "harness": harness, "emit_path": None, "n_emitted": 0}
    if harness is None or not sym or not tf:
        row["error"] = ("unclassifiable (no donchian/pullback/squeeze params "
                        "or no symbol/timeframe)")
        return row
    feed = resolve_feed(sym, tf)
    row["feed"] = feed
    # Namespace the workfiles by (strategy, symbol) so a per-symbol replay of the
    # SAME strategy never clobbers a sibling symbol's fetch/emit.
    label = f"{name}__{sym}"
    csv = os.path.join(workdir, f"{label}__data.csv")
    emit = os.path.join(workdir, f"{label}__trades.jsonl")
    jout = os.path.join(workdir, f"{label}__bt.json")
    try:
        _fetch_csv(feed, days, csv)
    except Exception as e:  # noqa: BLE001  # allow-silent: fetch NAMED in row["error"] + returned, never swallowed (as run_one)
        row["error"] = f"fetch failed: {type(e).__name__}: {e}"
        return row
    argv, faithful, omitted = build_harness_cmd(
        name, eff, harness, csv, feed["resample"], emit, jout,
        fee_override=fee_override)
    row["fidelity"] = "faithful" if faithful else "approximate"
    row["omitted_levers"] = omitted
    # exit_head_* is replayable WHERE THE PUBLISHED ARTIFACT LIVES (trainer /
    # live VM), not everywhere. Recorded as its own field rather than folded
    # into fidelity — see annotate_exit_head_replayability().
    annotate_exit_head_replayability(eff, row, omitted)
    row["fee_bps_roundtrip"] = (roundtrip_fee_bps(sym) if fee_override is None
                                else float(fee_override))
    try:
        subprocess.run(argv, check=True, cwd=REPO,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        row["error"] = f"harness failed: {(e.stderr or b'').decode()[-300:]}"
        return row
    try:
        with open(emit) as fh:
            row["n_emitted"] = sum(1 for ln in fh if ln.strip())
    except OSError as e:  # noqa: BLE001 — emit-read failure is NAMED, not swallowed
        row["error"] = f"emit unreadable: {type(e).__name__}: {e}"
        return row
    row["emit_path"] = emit
    return row


def run_one(name: str, cfg: dict, workdir: str, days: int,
            fee_arms: Optional[list] = None) -> dict:
    harness = classify(cfg)
    sym = (cfg.get("symbols") or [None])[0]
    tf = cfg.get("timeframe")
    row: dict = {"strategy": name, "symbol": sym, "timeframe": tf, "harness": harness}
    if harness is None or not sym or not tf:
        row["error"] = "unclassifiable (no donchian/pullback/squeeze params or no symbol/timeframe)"
        return row
    feed = resolve_feed(sym, tf)
    row["feed"] = feed
    csv = os.path.join(workdir, f"{name}__data.csv")
    emit = os.path.join(workdir, f"{name}__trades.jsonl")
    jout = os.path.join(workdir, f"{name}__bt.json")
    try:
        _fetch_csv(feed, days, csv)
    except Exception as e:  # noqa: BLE001
        row["error"] = f"fetch failed: {type(e).__name__}: {e}"
        return row
    argv, faithful, omitted = build_harness_cmd(name, cfg, harness, csv,
                                                feed["resample"], emit, jout)
    row["fidelity"] = "faithful" if faithful else "approximate"
    row["omitted_levers"] = omitted
    annotate_exit_head_replayability(cfg, row, omitted)
    # Record WHICH fee graded this row, so a reader can never again have to guess
    # whether a verdict was produced under the venue-blind 7.5-bps default
    # (BL-20260730-RESEARCH-VENUE-FEE). 0.0 = commission-free venue.
    row["fee_bps_roundtrip"] = roundtrip_fee_bps(cfg["symbols"][0])

    # FIXED-WINDOW FEE A/B (BL-20260730-FEE-AB-FIXED-WINDOW): grade the SAME
    # fetched candle window at each fee arm and diff per cell. `emit`/`jout` above
    # are the single-arm paths; each arm gets its own so they never clobber.
    if fee_arms:
        arms: dict = {}
        for fee in fee_arms:
            emit_a = os.path.join(workdir, f"{name}__fee{fee}__trades.jsonl")
            jout_a = os.path.join(workdir, f"{name}__fee{fee}__bt.json")
            argv_a, _, _ = build_harness_cmd(
                name, cfg, harness, csv, feed["resample"], emit_a, jout_a,
                fee_override=fee)
            try:
                subprocess.run(argv_a, check=True, cwd=REPO,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                arms[str(fee)] = _regime_tag(csv, emit_a, feed["resample"], name)
            except subprocess.CalledProcessError as e:
                row.setdefault("arm_errors", {})[str(fee)] = (
                    f"harness failed: {(e.stderr or b'').decode()[-200:]}")
            except Exception as e:  # noqa: BLE001
                row.setdefault("arm_errors", {})[str(fee)] = (
                    f"{type(e).__name__}: {e}")
        row["fee_ab"] = {"fee_arms": [str(f) for f in fee_arms],
                         "arms": arms,
                         "diff": _fee_ab_diff(arms)}
        return row

    try:
        subprocess.run(argv, check=True, cwd=REPO,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        row["error"] = f"harness failed: {(e.stderr or b'').decode()[-300:]}"
        return row
    try:
        row["matrix"] = _regime_tag(csv, emit, feed["resample"], name)
    except Exception as e:  # noqa: BLE001
        row["error"] = f"regime-tag failed: {type(e).__name__}: {e}"
    return row


def load_roster() -> dict:
    ex = yaml.safe_load(open(os.path.join(REPO, "config/regime_coverage_exemptions.yaml")))
    strat = yaml.safe_load(open(os.path.join(REPO, "config/strategies.yaml"))).get("strategies", {})
    return {n: strat.get(n, {}) for n in (ex.get("coverage_debt") or {})}


def resolve_strategy(name: str) -> Optional[dict]:
    """Config for ANY declared strategy, whether or not it is in `coverage_debt`.

    Why this exists (BL-20260730-REGIME-CELL-UNAUDITABLE): the roster above is the
    **debt list**, and authoring a cell PAYS THE STRATEGY DOWN OUT of `coverage_debt`
    — so the moment a Tier-3 cell is authored, both re-grade tools stop being able to
    measure that strategy at all (`regime_cell_walkforward.run_cell` returned the
    literal error "not in coverage_debt roster"). The tooling could grade candidates
    but never RE-AUDIT a decision it had already made.

    That bit immediately: the 2026-07-30 corrected-cost re-run could not re-measure
    `gld_pullback_1h` — the one live Tier-3 cell whose evidence the fee fix most
    called into question — because authoring that cell had removed it from the
    roster. A blind spot exactly where the live gate is.

    So an explicitly-named strategy (`--only`, or a walk-forward request) resolves
    against `config/strategies.yaml` directly. The DEFAULT roster is unchanged: still
    the debt list, so a bare full-roster run means the same thing it always did.
    """
    strat = yaml.safe_load(
        open(os.path.join(REPO, "config/strategies.yaml"))).get("strategies", {}) or {}
    cfg = strat.get(name)
    return cfg if isinstance(cfg, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", help="run only these strategies (may be outside coverage_debt — an already-celled strategy stays auditable)")
    ap.add_argument("--crypto-only", action="store_true", help="skip Yahoo feeds (sandbox-testable)")
    ap.add_argument("--workdir", default="/tmp/regime_debt_matrix")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--fee-ab", default=None,
                    help="CSV of round-trip fee bps arms for a fixed-window fee "
                         "A/B (e.g. '0,7.5'); grades the SAME candle window at "
                         "each fee so per-cell deltas isolate the fee effect from "
                         "window drift (BL-20260730-FEE-AB-FIXED-WINDOW). Default: "
                         "single venue-resolved-fee run.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    fee_arms: Optional[list] = None
    if args.fee_ab:
        try:
            fee_arms = [float(x) for x in str(args.fee_ab).split(",") if x.strip() != ""]
        except ValueError:
            print(f"[fee-ab] bad --fee-ab value {args.fee_ab!r}; expected CSV of "
                  "numbers e.g. '0,7.5'", file=sys.stderr)
            return 2
        if len(fee_arms) < 2:
            print("[fee-ab] need >=2 fee arms to diff (e.g. '0,7.5')", file=sys.stderr)
            return 2
    os.makedirs(args.workdir, exist_ok=True)
    roster = load_roster()
    names = args.only or sorted(roster)
    results = []
    for n in names:
        # An explicitly-named strategy may live outside `coverage_debt` (an
        # already-celled one, e.g. gld_pullback_1h) — resolve it so an authored
        # cell stays auditable. BL-20260730-REGIME-CELL-UNAUDITABLE.
        cfg = roster.get(n) or resolve_strategy(n)
        if cfg is None:
            results.append({"strategy": n, "error": "not declared in strategies.yaml"})
            continue
        sym = (cfg.get("symbols") or [None])[0]
        if args.crypto_only and not (sym or "").upper().endswith("USDT"):
            results.append({"strategy": n, "symbol": sym, "skipped": "non-crypto (crypto-only)"})
            continue
        results.append(run_one(n, cfg, args.workdir, args.days, fee_arms=fee_arms))

    # COVERAGE DECLARATION (BL-20260730-REGIME-CELL-UNAUDITABLE, and the binding
    # "Green is not evidence" rule §3). This run's roster is a WORK QUEUE — the
    # coverage_debt list — not the population of live strategies. Authoring a cell
    # PAYS THE STRATEGY DOWN OUT of that queue, so the queue systematically excludes
    # exactly the decisions most in need of re-checking: the 2026-07-30 corrected-cost
    # re-grade reported "34 rows, 0 errored, 0 skipped" while silently omitting
    # gld_pullback_1h, the one live Tier-3 cell it existed to re-check. Emitting the
    # population + the excluded set means "34 rows" can never again be read as "the
    # whole audit".
    try:
        strat_all = yaml.safe_load(
            open(os.path.join(REPO, "config/strategies.yaml"))).get("strategies", {}) or {}
        live = {k for k, v in strat_all.items()
                if isinstance(v, dict) and v.get("execution", "live") != "shadow"}
        covered = {r.get("strategy") for r in results}
        coverage = {
            "roster_kind": "coverage_debt (a WORK QUEUE, not the live population)",
            "declared_live_strategies": len(live),
            "covered": len(covered),
            "not_covered": sorted(live - covered),
            "warning": ("Strategies absent here are NOT cleared — they were never "
                        "measured by this run. An ALREADY-CELLED strategy is absent "
                        "precisely because a cell was authored for it; re-audit those "
                        "explicitly with --only <name>."),
        }
    except Exception as exc:  # noqa: BLE001
        coverage = {"error": f"could not compute coverage: {type(exc).__name__}: {exc}"}

    payload = {"count": len(results), "coverage": coverage, "results": results}
    if fee_arms is not None:
        payload["fee_ab_arms"] = [str(f) for f in fee_arms]
    if args.json:
        print(json.dumps(payload))
    elif fee_arms is not None:
        # A/B print: the per-cell fee delta (isolated), not the absolute matrix.
        for r in results:
            print(f"{r['strategy']:26s} {r.get('fidelity','-'):11s} "
                  f"{r.get('error') or r.get('skipped') or ''}")
            diff = (r.get("fee_ab") or {}).get("diff", {})
            for reg, cell in (diff.get("by_regime") or {}).items():
                deltas = " ".join(f"{k}={v}" for k, v in cell.items()
                                  if k.startswith("d_") and v is not None)
                print(f"    {reg:13s} {deltas}  (long_n{cell.get('long_n')} short_n{cell.get('short_n')})")
            if r.get("arm_errors"):
                print(f"    arm_errors: {r['arm_errors']}")
    else:
        for r in results:
            m = (r.get("matrix") or {}).get("by_regime", {})
            tot = (r.get("matrix") or {}).get("totals", {})
            print(f"{r['strategy']:26s} {r.get('fidelity','-'):11s} "
                  f"{r.get('error') or r.get('skipped') or ''}")
            for reg, s in m.items():
                print(f"    {reg:13s} net_r={s['net_r']:>8} long={s['long_r']:>8}(n{s['long_n']}) "
                      f"short={s['short_r']:>8}(n{s['short_n']})")
            if tot:
                print(f"    TOTAL net_r={tot.get('net_r')} long={tot.get('long_r')} short={tot.get('short_r')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
