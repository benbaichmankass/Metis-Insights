#!/usr/bin/env python3
"""M30 · C1-for-backtests — the BACKTEST research-panel builder (large-N substrate).

**Why this exists.** The live-journal C1 panel (``build_research_panel.py``) is
structurally too thin for discovery: the ~376-row real book + the M18 coin-flip
prior guarantee nothing clears purged-WF-CV × BH-FDR at that N (M30 Studies 1–5,
all null for this reason). But the **backtest engine already produces large-N
samples with the full candle path in memory** — that is the real discovery
substrate. This builder bridges the existing backtest harnesses into the SAME C2
machinery (``analyze_research_panel.py``), unchanged: it runs a harness, and for
every simulated trade emits the **same decision-time feature shape** C1 produces
(``feat_*`` / ``cat_*`` / ``gate_*`` via ``src.research.component_vector.extract``)
plus outcomes — ``win`` / ``r`` AND **native MFE/MAE/giveback** computed from the
backtest's own candle path (``src.research.excursions``). The live/paper journal
becomes validation; the backtest becomes discovery.

**Why the backtest panel is feature-rich where the journal is sparse.** The two
harnesses this bridge targets — ict_scalp (here) and, as follow-ups, backtest_system
/ vwap — call the **live signal builder** (``order_package`` / ``build_*_signal``)
per bar, so each simulated trade carries the same rich decision-time ``meta`` a
live order-package would (sweep geometry, FVG, displacement, mitigation, regime,
adx, vol_regime, killzone/bias). That is exactly the vector ``component_vector``
extracts. The harnesses that only re-implement a thin entry rule inline
(trend/fade/squeeze/ICTBacktester) are NOT wired here — they'd feed C2 an empty
feature vector (the very sparsity we are escaping). The candle path is in memory
either way, so those remain available for an excursion-only (feature-free) study.

**Leakage discipline (the platform's binding invariant).** The excursion columns
(``mfe_r`` / ``mae_r`` / ``giveback_r`` / ``capture_ratio`` / …) summarise the
strictly-FUTURE post-entry price path — they are stamped as OUTCOME columns, never
features. The decision-time ``meta`` the harness stamps ALSO carries some
post-entry facts (``mfe_r`` / ``mae_r`` / ``exit_price`` / ``bars_held``), but
``component_vector.extract`` reads ONLY the registered decision-time specs (sweep
/ fvg / displacement / regime / confidence), so those outcome keys can never leak
into a feature column — the feature set is exactly ``extract``'s output. The
manifest stamps the split so C2 can assert it. The C2 purged/embargoed WF-CV orders
rows by ``closed_at`` (the trade's exit timestamp), so the split respects backtest
time ordering, not row order.

**Observe-only, Tier-1.** Reads a candle CSV + runs a backtest harness in-process,
writes a panel file. No DB write, no broker socket, no order path, no live behavior.

Usage::

    # ict_scalp on the full 5m feed → panel → feed C2 unchanged
    python scripts/research/build_backtest_panel.py \\
        --harness ict_scalp --data data/backtest_candles.csv --stamp-regime \\
        --out runtime_logs/research/bt_ict_scalp_panel.jsonl
    python scripts/research/analyze_research_panel.py \\
        --panel runtime_logs/research/bt_ict_scalp_panel.jsonl --outcome win
    # exit-timing on the SAME panel (excursions come free on this substrate):
    python scripts/research/analyze_research_panel.py \\
        --panel runtime_logs/research/bt_ict_scalp_panel.jsonl --outcome giveback_r
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.research.component_vector import (  # noqa: E402
    KIND_CATEGORICAL,
    KIND_GATE,
    KIND_GRADED,
    extract,
)
from src.research.excursions import (  # noqa: E402
    compute_excursions,
    excursion_feature_names,
)

_EXCURSION_COLS = excursion_feature_names()
# The backtest has no dollar PnL — ``r`` (realized R) is the canonical outcome and
# ``pnl`` is emitted as an R-proxy (== r_multiple) so the C2 edge tables read
# expectancy in R. ``win`` = r > 0. The excursion columns are the exit-timing
# outcomes (strictly future to the decision-time features).
_OUTCOME_COLS = tuple(["pnl", "win", "r", *_EXCURSION_COLS])
_KEY_COLS = ("strategy", "symbol", "cohort", "closed_at")
_COHORT = "backtest"


# ---------------------------------------------------------------------------
# Normalized simulated-trade record (harness-agnostic) — the adapter contract.
# ---------------------------------------------------------------------------


@dataclass
class SimTrade:
    """One simulated trade, normalized across harnesses.

    ``entry_index`` / ``exit_index`` index into the adapter-returned candle frame
    so the bridge can slice the held-window bars for the excursion outcomes.
    ``meta`` is the LIVE decision-time signal meta (the dict ``order_package`` /
    ``build_*_signal`` wrote) — the same shape ``component_vector.extract`` reads.
    """

    strategy: str
    symbol: str
    side: str  # 'long' | 'short' (compute_excursions tolerates buy/sell too)
    entry_price: Optional[float]
    stop_loss: Optional[float]
    exit_price: Optional[float]
    r_multiple: Optional[float]
    entry_index: Optional[int]
    exit_index: Optional[int]
    exit_time: Any  # sortable → the panel's closed_at / the WF-CV time column
    meta: Dict[str, Any] = field(default_factory=dict)
    confidence: Optional[float] = None


# An adapter runs one harness and returns (candle_frame, sim_trades, run_info).
# candle_frame is anything with an ``.iloc[a:b]`` slice yielding rows with
# high/low (a pandas DataFrame in practice); sim_trades index into it.
Adapter = Callable[..., Tuple[Any, List[SimTrade], Dict[str, Any]]]


# ---------------------------------------------------------------------------
# ict_scalp adapter (the flagship — richest live meta of any harness)
# ---------------------------------------------------------------------------


def _adapter_ict_scalp(
    *,
    data_path: str,
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
    stamp_regime: bool = True,
    min_confidence: float = 0.0,
    ignore_yaml: bool = False,
    warmup_bars: int = 50,
    timeout_bars: int = 24,
    cooldown_bars: int = 3,
    htf_rule: str = "1h",
    htf_ema_period: int = 20,
    vol_spec: Optional[Dict[str, Any]] = None,
    sim_breakeven: bool = False,
    **_ignored: Any,
) -> Tuple[Any, List[SimTrade], Dict[str, Any]]:
    """Run scripts/backtest_ict_scalp.py in-process and normalize its trades.

    Uses the harness's own ``_load_candles`` + ``run_backtest(return_trades=True)``
    (the additive, default-off hook), so the candle frame the bridge slices is the
    exact one the harness indexed — entry/exit indices align with no resample
    ambiguity.
    """
    import scripts.backtest_ict_scalp as H  # noqa: PLC0415 — local import by design

    df = H._load_candles(data_path)
    cfg_overrides: Dict[str, Any] = {}
    if not ignore_yaml:
        try:
            cfg_overrides = H._load_yaml_params() or {}
        except Exception:  # noqa: BLE001 — fall back to unit defaults on any read miss
            cfg_overrides = {}

    summary = H.run_backtest(
        df,
        cfg_overrides=cfg_overrides,
        timeframe=timeframe,
        symbol=symbol,
        warmup_bars=warmup_bars,
        timeout_bars=timeout_bars,
        cooldown_bars=cooldown_bars,
        htf_rule=htf_rule,
        htf_ema_period=htf_ema_period,
        min_confidence=min_confidence,
        return_trades=True,
        vol_spec=vol_spec,
        stamp_regime=stamp_regime,
        sim_breakeven=sim_breakeven,
    )
    trades = summary.get("_trades_full", []) or []
    sim: List[SimTrade] = [
        SimTrade(
            strategy="ict_scalp_5m",
            symbol=symbol,
            side=t.direction,
            entry_price=t.entry,
            stop_loss=t.sl,
            exit_price=t.exit_price,
            r_multiple=t.r_multiple,
            entry_index=t.entry_index,
            exit_index=t.exit_index,
            exit_time=t.exit_time,
            meta=dict(t.meta or {}),
            confidence=t.confidence,
        )
        for t in trades
    ]
    info = {
        "harness": "ict_scalp",
        "data_path": data_path,
        "n_bars": int(len(df)),
        "stamp_regime": stamp_regime,
        "min_confidence": min_confidence,
        "ignored_yaml": ignore_yaml,
        "harness_total_trades": summary.get("total_trades"),
        "harness_win_rate_pct": summary.get("win_rate_pct"),
        "harness_expectancy_r": summary.get("expectancy_r"),
    }
    return df, sim, info


def _r_multiple_from_prices(
    side: str, entry: Optional[float], stop: Optional[float], exit_px: Optional[float]
) -> Optional[float]:
    """Signed realized R from entry/stop/exit — 1R = |entry − stop|.

    ``None`` when any price is missing or the stop distance is degenerate (so the
    row emits an honest null ``r`` rather than a fabricated 0). Long: (exit−entry)/risk;
    short: (entry−exit)/risk.
    """
    try:
        entry_f = float(entry)
        stop_f = float(stop)
        exit_f = float(exit_px)
    except (TypeError, ValueError):
        return None
    risk = abs(entry_f - stop_f)
    if risk <= 0:
        return None
    move = (exit_f - entry_f) if str(side).lower() in ("long", "buy") else (entry_f - exit_f)
    return move / risk


def _adapter_backtest_system(
    *,
    data_path: str,
    symbol: str = "BTCUSDT",
    roster: Optional[str] = None,
    clock_tf: str = "15m",
    start: Optional[str] = None,
    end: Optional[str] = None,
    initial_balance: float = 10_000.0,
    risk_pct: float = 0.3,
    daily_loss_pct: float = 3.0,
    signal_ttl_bars: int = 1,
    flip_policy: str = "reverse",
    reentry_policy: str = "suppress",
    **_ignored: Any,
) -> Tuple[Any, List[SimTrade], Dict[str, Any]]:
    """Run scripts/backtest_system.py (the PORTFOLIO harness) in-process and
    normalize its per-strategy closed trades into SimTrades.

    Unlike the single-strategy ict_scalp adapter, this drives the WHOLE roster
    through the real ``aggregate_intents`` on one shared account, so the panel
    spans every roster strategy (each closed trade carries its winning strategy
    as ``owner`` + that strategy's decision-time ``meta``) — broadening the
    feature→outcome discovery beyond one strategy. Uses the harness's additive
    ``attach_full=True`` hook to expose the full closed ledger + the clock-tf
    candle frame the excursion window slices. The panel's ``strategy`` column is
    per-row (the winning strategy), so ``component_vector.extract`` reads each
    row against its own strategy's decision-time specs.
    """
    import scripts.backtest_system as BS  # noqa: PLC0415 — local import by design

    base5m = BS._load_candles(data_path)
    roster_list = (
        [r.strip() for r in roster.split(",") if r.strip() in BS.ROSTER]
        if roster
        else list(BS.ROSTER.keys())
    )
    summary = BS.run_system_backtest(
        base5m,
        roster=roster_list,
        start=start,
        end=end,
        initial_balance=initial_balance,
        risk_pct=risk_pct,
        daily_loss_pct=daily_loss_pct,
        signal_ttl_bars=signal_ttl_bars,
        overrides={},
        refresh=False,
        clock_tf=clock_tf,
        flip_policy=flip_policy,
        reentry_policy=reentry_policy,
        attach_full=True,
        symbol=symbol,
    )
    closed = summary.get("closed_trades", []) or []
    clock = summary.get("clock_frame")
    sim: List[SimTrade] = [
        SimTrade(
            strategy=t.owner,
            symbol=symbol,
            side=t.side,
            entry_price=t.entry,
            stop_loss=t.sl,
            exit_price=t.exit,
            r_multiple=_r_multiple_from_prices(t.side, t.entry, t.sl, t.exit),
            entry_index=t.entry_idx,
            exit_index=t.exit_idx,
            exit_time=t.exit_ts,
            meta=dict(t.meta or {}),
            confidence=t.confidence,
        )
        for t in closed
    ]
    info = {
        "harness": "backtest_system",
        "data_path": data_path,
        "roster": roster_list,
        "clock_tf": clock_tf,
        "flip_policy": flip_policy,
        "reentry_policy": reentry_policy,
        "n_bars": int(len(clock)) if clock is not None else None,
        "harness_total_trades": len(closed),
        "harness_net_pnl": summary.get("net_pnl") or summary.get("total_pnl"),
        "per_strategy_trade_counts": {
            name: sum(1 for t in closed if t.owner == name) for name in roster_list
        },
    }
    return clock, sim, info


ADAPTERS: Dict[str, Adapter] = {
    "ict_scalp": _adapter_ict_scalp,
    "backtest_system": _adapter_backtest_system,
}


# ---------------------------------------------------------------------------
# Excursion window slice (held-window bars → candle dicts)
# ---------------------------------------------------------------------------


def _window_candles(df: Any, entry_index: Optional[int], exit_index: Optional[int]) -> List[Dict[str, Any]]:
    """The held-window bars as candle dicts for ``compute_excursions``.

    Uses the POST-ENTRY path [entry_index+1 .. exit_index] (the fill starts on the
    bar after the signal, matching the harness's own MFE/MAE window), so the
    excursions summarise price AFTER the entry decision. Returns ``[]`` when the
    indices are unusable (→ an honest excursion_present:false row).
    """
    if entry_index is None or exit_index is None:
        return []
    try:
        start = int(entry_index) + 1
        end = int(exit_index) + 1  # inclusive of the exit bar
    except (TypeError, ValueError):
        return []
    if end <= start:
        end = start + 1
    try:
        window = df.iloc[start:end]
        recs = window.to_dict("records")
    except Exception:  # noqa: BLE001 — any slicing/serialization miss → no excursions
        return []
    return [r for r in recs if isinstance(r, dict)]


def _iso_closed_at(value: Any) -> Optional[str]:
    """A sortable string for the WF-CV ``closed_at`` time column.

    A pandas Timestamp / datetime → ISO-8601 (lexically sortable); anything else →
    its ``str``; ``None`` stays ``None`` (the CV drops null-time rows).
    """
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return str(iso())
        except Exception:  # noqa: BLE001
            pass
    return str(value)


# ---------------------------------------------------------------------------
# Build + manifest
# ---------------------------------------------------------------------------


def build_backtest_panel(
    *,
    harness: str,
    adapter_opts: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return ``(rows, manifest)`` — the backtest panel in the C1 schema.

    Tolerant end-to-end: an unknown harness, a missing candle file, or a run that
    produces no trades yields an empty panel + an honest manifest, never a crash.
    """
    adapter = ADAPTERS.get(harness)
    rows: List[Dict[str, Any]] = []
    covered = 0
    run_info: Dict[str, Any] = {}
    error: Optional[str] = None

    if adapter is None:
        error = f"unknown harness '{harness}' (known: {sorted(ADAPTERS)})"
    else:
        try:
            df, sim_trades, run_info = adapter(**adapter_opts)
        except Exception as exc:  # noqa: BLE001 — surface the failure in the manifest
            error = f"{type(exc).__name__}: {exc}"
            df, sim_trades = None, []

        for st in sim_trades:
            comp = extract(st.strategy, st.meta, extra={"confidence": st.confidence})
            feat: Dict[str, float] = {}
            cat: Dict[str, str] = {}
            gate: Dict[str, bool] = {}
            for name, c in comp.items():
                if c.kind == KIND_GRADED:
                    feat[name] = c.value
                elif c.kind == KIND_CATEGORICAL:
                    cat[name] = c.value
                elif c.kind == KIND_GATE:
                    gate[name] = bool(c.value)

            candles = _window_candles(df, st.entry_index, st.exit_index)
            exc: Dict[str, Any] = {k: None for k in _EXCURSION_COLS}
            present = False
            if candles:
                res = compute_excursions(
                    candles,
                    entry_price=st.entry_price,
                    stop_loss=st.stop_loss,
                    side=st.side,
                    exit_price=st.exit_price,
                )
                if res.get("bars_held"):
                    exc = {k: res.get(k) for k in _EXCURSION_COLS}
                    present = True
            if present:
                covered += 1

            r = st.r_multiple
            try:
                r = float(r) if r is not None else None
            except (TypeError, ValueError):
                r = None
            win = 1 if (r is not None and r > 0) else 0

            side = str(st.side or "").strip().lower()
            direction = "long" if side in ("long", "buy") else ("short" if side in ("short", "sell") else side)
            rec: Dict[str, Any] = {
                "strategy": st.strategy,
                "symbol": st.symbol,
                "direction": direction,
                "cohort": _COHORT,
                "closed_at": _iso_closed_at(st.exit_time),
                "excursion_present": present,
                # outcomes — pnl is an R-proxy (no dollar PnL in a backtest)
                "pnl": r,
                "win": win,
                "r": r,
            }
            for k in _EXCURSION_COLS:
                rec[k] = exc.get(k)
            for k, v in feat.items():
                rec[f"feat_{k}"] = v
            for k, v in cat.items():
                rec[f"cat_{k}"] = v
            for k, v in gate.items():
                rec[f"gate_{k}"] = v
            rows.append(rec)

    feature_cols = sorted(
        {k for rec in rows for k in rec if k.startswith(("feat_", "cat_", "gate_"))}
    )
    manifest = {
        "source": "backtest",
        "harness": harness,
        "adapter_opts": {k: v for k, v in adapter_opts.items() if k != "vol_spec"},
        "run_info": run_info,
        "error": error,
        "cohort": _COHORT,
        "row_count": len(rows),
        "excursion_covered": covered,
        "excursion_coverage_pct": round(100.0 * covered / len(rows), 1) if rows else 0.0,
        "key_cols": list(_KEY_COLS),
        "outcome_cols": list(_OUTCOME_COLS),
        "feature_cols": feature_cols,
        "pnl_note": (
            "pnl is an R-proxy (== r_multiple): a backtest has no dollar PnL, so the "
            "C2 expectancy/edge tables read in R units. win = r > 0."
        ),
        "leakage_contract": (
            "feature_cols + key_cols are decision-time (the live signal meta the "
            "harness recorded at bar-of-signal, before the outcome exists); the "
            "outcome_cols (pnl/win/r + the mfe_r/mae_r/giveback_r/capture_ratio/… "
            "excursions) summarise the strictly-future post-entry price path. The "
            "feature set is exactly component_vector.extract's output, which reads "
            "only decision-time specs — the outcome keys the harness also stamps on "
            "meta (mfe_r/exit_price/bars_held) can never enter a feature column. "
            "Regress outcome-on-feature only. The WF-CV orders rows by closed_at "
            "(the exit timestamp), respecting backtest time order."
        ),
    }
    return rows, manifest


def write_panel(
    rows: List[Dict[str, Any]], manifest: Dict[str, Any], out_path: Path
) -> Tuple[Path, Path]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    manifest_path = out_path.with_suffix(out_path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return out_path, manifest_path


def _build_adapter_opts(args: argparse.Namespace) -> Dict[str, Any]:
    vol_spec = None
    if args.vol_spec_json:
        try:
            vol_spec = json.loads(Path(args.vol_spec_json).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a missing spec just leaves vol_regime unknown
            vol_spec = None
    return {
        "data_path": args.data,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "stamp_regime": bool(args.stamp_regime),
        "min_confidence": float(args.min_confidence),
        "ignore_yaml": bool(args.ignore_yaml),
        "warmup_bars": int(args.warmup_bars),
        "timeout_bars": int(args.timeout_bars),
        "cooldown_bars": int(args.cooldown_bars),
        "htf_rule": str(args.htf_rule),
        "htf_ema_period": int(args.htf_ema_period),
        "vol_spec": vol_spec,
        "sim_breakeven": bool(args.sim_breakeven),
        # backtest_system adapter opts (ignored by the ict_scalp adapter via **_ignored)
        "roster": args.roster,
        "clock_tf": str(args.clock_tf),
        "start": args.start,
        "end": args.end,
        "initial_balance": float(args.initial_balance),
        "risk_pct": float(args.risk_pct),
        "daily_loss_pct": float(args.daily_loss_pct),
        "signal_ttl_bars": int(args.signal_ttl_bars),
        "flip_policy": str(args.flip_policy),
        "reentry_policy": str(args.reentry_policy),
    }


def main(argv: Optional[List[str]] = None) -> int:
    import os

    parser = argparse.ArgumentParser(
        description=(
            "Build the M30 backtest research panel (C1-for-backtests): run a "
            "backtest harness and emit per-simulated-trade decision-time features "
            "+ outcomes (win/r + native MFE/MAE excursions) in the C1 schema for "
            "the C2 analyzer. Read-only, observe-only, Tier-1."
        )
    )
    parser.add_argument("--harness", choices=sorted(ADAPTERS), default="ict_scalp",
                        help="Which backtest harness to bridge (default: ict_scalp).")
    parser.add_argument("--data",
                        default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"),
                        help="OHLCV candle CSV the harness runs on ($BACKTEST_DATA_PATH default).")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m", help="Strategy timeframe label (default: 5m).")
    parser.add_argument("--stamp-regime", action=argparse.BooleanOptionalAction, default=True,
                        help="Stamp decision-time regime/adx/vol onto each trade's meta "
                             "(mirrors the live builder). Default on — the regime features "
                             "are wanted. --no-stamp-regime to drop them.")
    parser.add_argument("--min-confidence", type=float, default=0.0,
                        help="Skip entries below this live order_package() confidence.")
    parser.add_argument("--ignore-yaml", action="store_true",
                        help="Use unit defaults instead of config/strategies.yaml params.")
    parser.add_argument("--warmup-bars", type=int, default=50)
    parser.add_argument("--timeout-bars", type=int, default=24)
    parser.add_argument("--cooldown-bars", type=int, default=3)
    parser.add_argument("--htf-rule", default="1h")
    parser.add_argument("--htf-ema-period", type=int, default=20)
    parser.add_argument("--vol-spec-json", default=None,
                        help="Frozen vol spec JSON for vol_regime parity (else vol_regime=unknown).")
    parser.add_argument("--sim-breakeven", action="store_true",
                        help="Simulate the live break-even SL trail (matches the live monitor).")
    # --- backtest_system (portfolio) adapter options (ignored by ict_scalp) ---
    parser.add_argument("--roster", default=None,
                        help="[backtest_system] comma list of roster strategies "
                             "(default: all ROSTER members).")
    parser.add_argument("--clock-tf", default="15m",
                        help="[backtest_system] clock timeframe the shared account "
                             "ticks on (default 15m).")
    parser.add_argument("--start", default=None, help="[backtest_system] start date filter.")
    parser.add_argument("--end", default=None, help="[backtest_system] end date filter.")
    parser.add_argument("--initial-balance", type=float, default=10_000.0,
                        help="[backtest_system] starting account balance.")
    parser.add_argument("--risk-pct", type=float, default=0.3,
                        help="[backtest_system] per-trade risk %% of balance.")
    parser.add_argument("--daily-loss-pct", type=float, default=3.0,
                        help="[backtest_system] daily-loss halt cap %%.")
    parser.add_argument("--signal-ttl-bars", type=int, default=1,
                        help="[backtest_system] clock bars a signal stays live.")
    parser.add_argument("--flip-policy", default="reverse",
                        choices=["reverse", "hold", "flat"],
                        help="[backtest_system] opposite-vote conflict policy.")
    parser.add_argument("--reentry-policy", default="suppress",
                        choices=["suppress", "net"],
                        help="[backtest_system] same-direction re-entry policy.")
    parser.add_argument("--out", default="runtime_logs/research/backtest_panel.jsonl",
                        help="Output JSONL path (a sibling .manifest.json is written too).")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    rows, manifest = build_backtest_panel(
        harness=args.harness, adapter_opts=_build_adapter_opts(args)
    )
    out_path, manifest_path = write_panel(rows, manifest, Path(args.out))
    if not args.quiet:
        print(
            f"backtest panel [{args.harness}]: {manifest['row_count']} rows "
            f"({manifest['excursion_covered']} with excursions, "
            f"{manifest['excursion_coverage_pct']}%), "
            f"{len(manifest['feature_cols'])} feature cols → {out_path} "
            f"(+ {manifest_path.name})"
        )
        if manifest["error"]:
            print(f"  note: {manifest['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
