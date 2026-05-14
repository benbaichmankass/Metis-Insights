from __future__ import annotations
from src.runtime.signal_writer import write_signal
from src.utils.signal_audit_logger import log_signal
from src.runtime.risk_counters import inject_runtime_counters, inject_per_strategy_counters
from src.news.news_pipeline import get_news_score
# PR-6: signal builder functions extracted to strategy_signal_builders.py.
# Re-exported here for back-compat (existing callers + tests import from pipeline).
from src.runtime.strategy_signal_builders import (  # noqa: E402
    turtle_soup_signal_builder,
    vwap_signal_builder,
)

import os

HALT_FLAG_PATH = "/tmp/trader_halt.flag"

# S-026 G2: legacy single-client path placeholder. Sizing is decided
# per-account inside Coordinator.multi_account_execute via
# RiskManager.position_size(); the legacy path has no per-account
# context. The placeholder exists so safe_place_order's halt/news/risk-
# cap rails can still run through when MULTI_ACCOUNT_DISPATCH=false.
_DRY_MODE_PLACEHOLDER_QTY = 1.0
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

import logging  # noqa: E402
from typing import Any, Callable, Dict, Optional  # noqa: E402

import pandas as pd  # noqa: E402

from src.runtime.notify import send_to_operator  # noqa: E402
from src.runtime.orders import safe_place_order  # noqa: E402
from src.runtime.outcomes import Level, report  # noqa: E402
from src.units.ui.telegram_format import Section, kv_block, render_html, render_plain  # noqa: E402
from src.web.runtime_status import write_status  # noqa: E402

_OUTCOME_LEVEL_BY_STATUS: Dict[str, Level] = {
    # Happy / expected
    "submitted": Level.INFO,
    "multi_account_dispatched": Level.INFO,
    "dry_run": Level.INFO,
    "skipped": Level.INFO,
    "halted": Level.INFO,
    "news_veto": Level.INFO,
    "refused": Level.INFO,
    # Validation: bounded but a sign of upstream drift
    "failed_validation": Level.WARN,
    # Hard failures: page the operator
    "failed_exchange": Level.ERROR,
    "failed_dispatch": Level.ERROR,
    "error": Level.ERROR,
}


def _report_pipeline_outcome(result: Dict[str, Any], signal: Dict[str, Any]) -> None:
    """Translate the run_pipeline result dict into an outcomes.report() call.

    Never raises. Centralizes the status → level mapping so individual
    sites in the pipeline don't have to care about alerting.
    """
    try:
        status = str((result or {}).get("status") or "unknown")
        level = _OUTCOME_LEVEL_BY_STATUS.get(status, Level.ERROR)
        meta = (signal or {}).get("meta") or {}
        report(
            "pipeline_order",
            status,
            level=level,
            reason=(result or {}).get("reason"),
            symbol=(signal or {}).get("symbol"),
            side=(signal or {}).get("side"),
            qty=(signal or {}).get("qty"),
            strategy=meta.get("strategy_name"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("outcomes reporting failed")

logger = logging.getLogger(__name__)



def default_signal_builder(settings: dict) -> Dict[str, Any]:
    return {
        "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
        "side": "buy",
    }


def _build_killzone_exchange(settings: dict):
    """Back-compat shim — the canonical home is now
    ``src.runtime.market_data._build_exchange_client``.

    S-033 (architecture-audit-2026-05-02 § P1-8): connector
    construction moved out of the pipeline so signal builders aren't
    coupled to exchange reachability. Existing call sites + tests that
    monkeypatch ``pipeline._build_killzone_exchange`` keep working
    through this thin re-export.
    """
    from src.runtime.market_data import _build_exchange_client
    return _build_exchange_client(settings)


def _killzone_symbol(settings: dict) -> str:
    configured = settings.get("SYMBOL")
    if configured:
        return configured

    exchange_name = str(settings.get("EXCHANGE", settings.get("exchange", "bybit"))).strip().lower()
    if exchange_name == "binance":
        return "BTC/USDT"

    return "BTC/USDT:USDT"


def _signal_to_order_package(signal: Dict[str, Any], settings: dict):
    """Build an ``OrderPackage`` from a pipeline signal dict.

    The signal shape is what every builder in this module produces:
    ``{symbol, side, price/entry_price, stop_loss, take_profit,
    meta: {strategy_name, ...}}`` — S-026 G1: no qty (sizing is the
    per-account RiskManager's job in G2). The Coordinator's
    per-account dispatch path consumes ``OrderPackage``, which has a
    slightly different shape (``direction`` instead of ``side``,
    ``entry`` / ``sl`` / ``tp``). This helper bridges the two so we
    can fan a pipeline-generated signal out to every account in
    ``config/accounts.yaml`` without changing the strategy builders.
    """
    from src.core.coordinator import OrderPackage

    meta = dict(signal.get("meta") or {})
    side = str(signal.get("side", "")).strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError(
            f"_signal_to_order_package: side must be buy/sell, got {side!r}"
        )
    direction = "long" if side == "buy" else "short"

    entry = signal.get("entry_price") or signal.get("price") or meta.get("price")
    sl = signal.get("stop_loss") or meta.get("stop_loss") or meta.get("sl")
    tp = signal.get("take_profit") or meta.get("take_profit") or meta.get("tp")
    if entry is None or sl is None or tp is None:
        raise ValueError(
            "_signal_to_order_package: signal missing entry/sl/tp "
            f"(entry={entry!r}, sl={sl!r}, tp={tp!r}); strategy must "
            "populate price+stop_loss+take_profit before fan-out."
        )

    strategy = (
        meta.get("strategy_name")
        or signal.get("strategy")
        or settings.get("STRATEGY")
        or "unknown"
    )
    return OrderPackage(
        strategy=str(strategy),
        symbol=str(signal.get("symbol") or settings.get("SYMBOL") or "BTCUSDT"),
        direction=direction,
        entry=float(entry),
        sl=float(sl),
        tp=float(tp),
        confidence=float(meta.get("confidence") or 0.0),
        meta=meta,
    )


def _multi_account_dispatch_enabled(settings: dict) -> bool:
    """Return True when pipeline signals should fan out to every account.

    Default flipped to **true** (post-CP-2026-05-02): the architecture is
    "strategy produces signal; each account decides whether to live-trade
    that signal." Per-account ``mode: live | dry_run`` in
    ``config/accounts.yaml`` (operator directive 2026-05-03) is the only
    dry/live toggle in the codebase — checked inside
    ``RiskManager.evaluate()``.

    Operator can still pin to the legacy single-client path by exporting
    ``MULTI_ACCOUNT_DISPATCH=false`` — used for single-account smoke
    deployments that don't want to load Coordinator.
    """
    raw = settings.get("MULTI_ACCOUNT_DISPATCH") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("MULTI_ACCOUNT_DISPATCH", "true")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def _signal_meta(signal: Dict[str, Any]) -> Dict[str, Any]:
    meta = signal.get("meta") if isinstance(signal, dict) else None
    return meta if isinstance(meta, dict) else {}


def _extract_order_package_fields(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Pull entry / sl / tp / direction off *signal* with the same
    precedence as ``_signal_to_order_package``.

    Returns ``None`` for any field that isn't present so the renderer
    can show ``—`` rather than fabricating a value. Used only for the
    operator-facing Telegram envelope; never as a sizing input.
    """
    meta = _signal_meta(signal)
    entry = signal.get("entry_price") or signal.get("price") or meta.get("price")
    sl = signal.get("stop_loss") or meta.get("stop_loss") or meta.get("sl")
    tp = signal.get("take_profit") or meta.get("take_profit") or meta.get("tp")
    side = (signal.get("side") or "").lower()
    direction = "long" if side == "buy" else ("short" if side == "sell" else None)
    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "direction": direction,
        "confidence": signal.get("confidence") or meta.get("confidence"),
    }


def _pipeline_result_sections(
    *, signal: Dict[str, Any], result: Dict[str, Any], strategy: str,
) -> list:
    """Build the collapsable detail sections for the per-tick Telegram
    "Pipeline result" message.

    Sections are stable in shape so the operator can predict where to
    look:

    1. **Strategy** — name + signal confidence + meta keys.
    2. **Order package** — entry / sl / tp / direction / qty when the
       signal carried them; explicit "(not generated)" otherwise.
    3. **Multi-account dispatch** — per-account result list when the
       multi_account path ran.
    4. **Why & next step** — only when status indicates a failure;
       echoes the reason string and the operator-actionable hint
       (e.g. "/accounts live <account_name> to flip out of dry mode").
    """
    sections: list = []
    status = result.get("status", "unknown")
    reason = result.get("reason")
    meta = _signal_meta(signal)

    # 1. Strategy detail
    strat_rows = [
        ("Strategy", strategy),
        ("Symbol", signal.get("symbol")),
        ("Side", signal.get("side")),
        ("Qty (signal)", signal.get("qty")),
        ("Confidence", signal.get("confidence") or meta.get("confidence")),
    ]
    sections.append(Section(
        summary=f"Strategy — {strategy}",
        body=kv_block(strat_rows),
        priority=10,
    ))

    # 2. Order package detail (entry / sl / tp / direction). The
    # "not generated" body is only meaningful when the strategy
    # actually fired (side ∈ {'buy', 'sell'}) — on no-signal ticks
    # there's no package to show and the section adds noise. CP-18 P3.
    pkg = _extract_order_package_fields(signal)
    side_actionable = str(signal.get("side", "")).strip().lower() in ("buy", "sell")
    if any(v is not None for v in (pkg["entry"], pkg["sl"], pkg["tp"])):
        pkg_rows = [
            ("Direction", pkg["direction"]),
            ("Entry",     pkg["entry"]),
            ("Stop loss", pkg["sl"]),
            ("Take profit", pkg["tp"]),
            ("Confidence", pkg["confidence"]),
        ]
        sections.append(Section(
            summary="Order package — generated",
            body=kv_block(pkg_rows),
            priority=20,
        ))
    elif side_actionable:
        sections.append(Section(
            summary="Order package — not generated",
            body=(
                "Signal did not carry entry/sl/tp at the top level; the "
                "legacy single-client validation path ran instead of the "
                "multi-account dispatch fast-path."
            ),
            priority=20,
        ))

    # 3. Multi-account dispatch (only when that path ran)
    multi = result.get("multi_account_results")
    if isinstance(multi, list) and multi:
        lines = []
        for r in multi:
            if not isinstance(r, dict):
                continue
            acc = r.get("name") or r.get("account") or r.get("account_id") or "?"
            err = r.get("error")
            st = "ok" if err is None else (str(err) or "error")
            qty = r.get("sized_qty") if r.get("sized_qty") is not None else r.get("qty")
            line = f"{acc}: {st}"
            if qty is not None and err is None:
                line += f" qty={qty}"
            lines.append(line)
        sections.append(Section(
            summary=f"Accounts dispatched — {len(multi)}",
            body="\n".join(lines) or "(empty)",
            priority=30,
        ))

    # 4. Failure remediation hint
    if status in {"failed_validation", "failed_exchange",
                  "failed_dispatch", "error"}:
        hint_lines = []
        if reason:
            hint_lines.append(f"Reason: {reason}")
        if reason and "account_mode_dry_run" in str(reason):
            hint_lines.append(
                "Action: this account is in dry_run mode "
                "(config/accounts.yaml `mode: dry_run` or runtime "
                "/accounts dry/live override). Flip it via Telegram "
                "/accounts live <account_name> to start live execution."
            )
        sections.append(Section(
            summary=f"Why & next step — {status}",
            body="\n".join(hint_lines) or "(no detail)",
            priority=5,
        ))

    return sections


def _signal_carries_full_sltp(signal: Dict[str, Any]) -> bool:
    """True only when the signal carries entry, sl, and tp at the top
    level (or under ``meta``). Same shape as the local
    ``_signal_packageable`` inside ``run_pipeline``; lifted to module
    scope so the G5 missing-sltp warning can re-use it without copying
    the predicate."""
    if not isinstance(signal, dict):
        return False
    _meta = signal.get("meta") or {}
    return (
        (signal.get("entry_price") or signal.get("price") or _meta.get("price")) is not None
        and (signal.get("stop_loss") or _meta.get("stop_loss") or _meta.get("sl")) is not None
        and (signal.get("take_profit") or _meta.get("take_profit") or _meta.get("tp")) is not None
    )


def _has_open_package_for_strategy(strategy_name: Optional[str]) -> Optional[str]:
    """Strategy-monocle gate: return the order_package_id of an existing
    open package for *strategy_name*, or ``None`` when no open package
    exists.

    Operator directive 2026-05-03: a strategy may have **one** open
    package globally — across all accounts that follow it. Once a
    package is logged, the strategy's job is to monitor + update
    that package via ``order_monitor`` until SL/TP hits or the
    strategy decides to close (PRs 2 + 3 of this sprint wire the
    close path).

    Best-effort — a DB-read failure returns ``None`` (i.e. "no open
    package known"), which means the dispatcher proceeds. The risk
    is creating one extra duplicate package in the DB-read failure
    window; the alternative (refusing the dispatch on every
    DB-read failure) trades a real bug for a hypothetical one.

    The strategy_name is read from ``signal.meta.strategy_name``
    (the canonical attribution source post-BUG-033). When unset
    (multiplexer / unknown), the gate is bypassed — there's no
    canonical name to scope the open-package query to.
    """
    if not strategy_name:
        return None
    try:
        from src.units.db.database import Database
        import os as _os
        db_path = (
            _os.environ.get("TRADE_JOURNAL_DB")
            or _os.path.join(
                _os.path.abspath(
                    _os.path.join(_os.path.dirname(__file__), "..", "..")
                ),
                "trade_journal.db",
            )
        )
        db = Database(db_path=db_path)
        # 2026-05-09 — dropped ``linked_only=True``. With the filter on,
        # a multi-account dispatch where every account refused on
        # ``zero_exchange_capacity`` left the package row at
        # status='open', linked_trade_id=NULL — and the next tick's gate
        # query filtered it out, letting the dispatch retry every
        # minute. The result was 50+ rejection rows per cluster in
        # ``trades`` until ``_sweep_unlinked_packages`` orphaned the
        # row at +5 min. Treating any open row (linked or not) as
        # gate-blocking turns the rejection cadence from 1/min into
        # 1 per 5-min sweep window.
        rows = db.get_order_packages_by_strategy(
            strategy_name, status="open", limit=1,
        )
        if rows:
            return str(rows[0].get("order_package_id") or "")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_has_open_package_for_strategy(%s): DB read failed — %s",
            strategy_name, exc,
        )
        return None


# Default cooldown (seconds) after a strategy was internally refused
# (sized_qty=0 from RiskManager) before the dispatcher will re-attempt
# the same strategy. Tuned to one full 5 m candle so VWAP / turtle_soup
# get a fresh bar of market data before retrying — the most common
# transient cause of a sized_qty=0 refusal is Bybit V5 returning
# ``availableToBorrow=0`` for the borrow side of a spot-margin order
# (S-056 / S-058) and that field repopulates on the exchange's own
# cadence, not ours. Pre-fix the strategy_monocle gate only blocked
# on *open* packages, so a refused signal re-fired every minute and
# accumulated 20 ``status='rejected'`` rows over 1 h on 2026-05-10
# (per the trade-journal evidence FU-20260510-002 originally
# mislabelled as a 170131 cluster). Operator override via
# ``STRATEGY_REFUSAL_COOLDOWN_SECONDS`` in the systemd unit.
_DEFAULT_REFUSAL_COOLDOWN_SECONDS = 300


def _refusal_cooldown_seconds() -> int:
    raw = os.environ.get("STRATEGY_REFUSAL_COOLDOWN_SECONDS")
    if raw is None:
        return _DEFAULT_REFUSAL_COOLDOWN_SECONDS
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_REFUSAL_COOLDOWN_SECONDS
    return v if v >= 0 else _DEFAULT_REFUSAL_COOLDOWN_SECONDS


def _recent_refusal_for_strategy(
    strategy_name: Optional[str],
    cooldown_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Return ``{"order_package_id", "age_seconds", "cooldown_seconds"}``
    when *strategy_name* has a ``status='rejected'`` order_packages row
    updated within the cooldown window, else ``None``.

    Belt-and-braces companion to ``_has_open_package_for_strategy``.
    The open-package gate already blocks dispatch while a strategy has
    an outstanding live position; this gate blocks dispatch while a
    strategy's most-recent attempt was *internally refused*
    (``sized_qty=0`` → ``log_rejection_to_journal(status='rejected')``
    in coordinator.multi_account_execute). The two together prevent
    both kinds of duplicate dispatch — including the
    sized_qty=0 cascade FU-20260510-002 captured.

    Best-effort — DB-read failure returns ``None`` (i.e. "no
    cooldown known") rather than refusing every dispatch on a
    transient SQLite hiccup. Tradeoff matches the open-package
    helper's contract.
    """
    if not strategy_name:
        return None
    cooldown = cooldown_seconds if cooldown_seconds is not None else _refusal_cooldown_seconds()
    if cooldown <= 0:
        return None
    try:
        from datetime import datetime, timezone
        from src.units.db.database import Database
        import os as _os
        db_path = (
            _os.environ.get("TRADE_JOURNAL_DB")
            or _os.path.join(
                _os.path.abspath(
                    _os.path.join(_os.path.dirname(__file__), "..", "..")
                ),
                "trade_journal.db",
            )
        )
        db = Database(db_path=db_path)
        rows = db.get_order_packages_by_strategy(
            strategy_name, status="rejected", limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        updated = row.get("updated_at") or row.get("created_at")
        if not updated:
            return None
        try:
            ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
        age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
        if age_seconds < 0 or age_seconds > cooldown:
            return None
        return {
            "order_package_id": str(row.get("order_package_id") or ""),
            "age_seconds": float(age_seconds),
            "cooldown_seconds": int(cooldown),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_recent_refusal_for_strategy(%s): DB read failed — %s",
            strategy_name, exc,
        )
        return None


# turtle_soup_signal_builder and vwap_signal_builder are re-exported from
# src.runtime.strategy_signal_builders (PR-6 extraction). They are imported
# at the top of this file for back-compat; the bodies below are removed.
#
# The following is a tombstone comment so grep / git blame traces back here:
#   turtle_soup_signal_builder → src/runtime/strategy_signal_builders.py
#   vwap_signal_builder        → src/runtime/strategy_signal_builders.py


def _coerce_ohlcv_with_dt_index(raw: Any) -> pd.DataFrame:
    """
    Normalise raw exchange OHLCV into a DataFrame with a UTC
    ``DatetimeIndex``.

    The ICT analyzer requires a DatetimeIndex (kill-zones are derived
    from ``df.index.hour``). We accept either:

    - a list of ``[ts_ms, open, high, low, close, volume]`` rows
      (the ccxt / Bybit / Binance native shape), or
    - a DataFrame already containing a ``timestamp`` column in ms or a
      DatetimeIndex.
    """
    if isinstance(raw, pd.DataFrame):
        df = raw.copy()
    else:
        df = pd.DataFrame(
            raw,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )

    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" not in df.columns:
            raise RuntimeError(
                "ICT strategy: candle frame is missing a 'timestamp' "
                "column and has no DatetimeIndex."
            )
        df["timestamp"] = pd.to_datetime(
            df["timestamp"], unit="ms", utc=True
        )
        df = df.set_index("timestamp")

    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _write_ict_signals_from_meta(signal: dict, settings: dict) -> None:
    """Write individual ICT detections even when no trade is taken."""
    if not isinstance(signal, dict):
        return

    meta = signal.get("meta") or {}
    symbol = signal.get("symbol", settings.get("SYMBOL", "BTCUSDT"))
    timeframe = settings.get("TIMEFRAME", "15m")

    fvgs = meta.get("fvgs") or []
    for fvg in fvgs:
        if not isinstance(fvg, dict):
            continue
        fvg_type = fvg.get("type", "unknown")
        gap_low = fvg.get("gap_low")
        gap_high = fvg.get("gap_high")
        price = None
        if gap_low is not None and gap_high is not None:
            try:
                price = (float(gap_low) + float(gap_high)) / 2.0
            except Exception:
                price = None
        write_signal(
            symbol=symbol,
            signal_type=f"fvg_{fvg_type}",
            direction=fvg_type,
            price=price,
            timeframe=timeframe,
            reason="ICT FVG detected",
            metadata=str(fvg),
        )

    order_blocks = meta.get("order_blocks") or meta.get("obs") or []
    for ob in order_blocks:
        if not isinstance(ob, dict):
            continue
        ob_type = ob.get("type", "unknown")
        low = ob.get("low")
        high = ob.get("high")
        price = None
        if low is not None and high is not None:
            try:
                price = (float(low) + float(high)) / 2.0
            except Exception:
                price = None
        write_signal(
            symbol=symbol,
            signal_type=f"ob_{ob_type}",
            direction=ob_type,
            price=price,
            timeframe=timeframe,
            reason="ICT order block detected",
            metadata=str(ob),
        )

# Ordered list of strategies tried in multiplexed mode; first actionable signal wins.
# Source of truth is config/strategies.yaml (S-007). Order in the YAML determines
# multiplexer priority. Falls back to the original hardcoded list if the registry
# cannot be loaded (e.g. missing pyyaml in a minimal deploy environment).
def _strategies_from_registry() -> list:
    try:
        from src.strategy_registry import load_strategies
        return [s["name"] for s in load_strategies()]
    except Exception as exc:
        logger.warning("pipeline: registry unavailable, using hardcoded STRATEGIES list: %s", exc)
        # S-012 PR C3: hardcoded fallback matches the production roster
        # in config/strategies.yaml after PR B1.
        return ["turtle_soup", "vwap"]


STRATEGIES = _strategies_from_registry()

# Per-strategy risk allocation fractions applied inside the multiplexer.
# S-012 PR C5: roster reduced to turtle_soup + vwap (50 / 50 split). The
# legacy breakout / killzone / ict builders and entries are deleted.
STRATEGY_RISK_PCT: Dict[str, float] = {
    "turtle_soup": 0.5,
    "vwap": 0.5,
}

_STRATEGY_BUILDERS: Dict[str, Callable[[dict], Dict[str, Any]]] = {
    "turtle_soup": turtle_soup_signal_builder,
    "vwap": vwap_signal_builder,
}


def multiplexed_signal_builder(settings: dict) -> Dict[str, Any]:
    """
    Loop STRATEGIES in order; return the first actionable signal.

    If a strategy raises an exception it is logged and skipped.
    Returns a side=none signal when no strategy fires.

    S-026 G1: signals carry no qty — sizing is the per-account
    RiskManager's job. The per-strategy risk allocation
    (``STRATEGY_RISK_PCT``) is recorded under
    ``meta["strategy_risk_pct"]`` so the downstream sizer (G2) can
    apply it when computing the per-account quantity.
    """
    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    for strategy_name in STRATEGIES:
        builder = _STRATEGY_BUILDERS.get(strategy_name)
        if builder is None:
            logger.warning("Multiplexer: unknown strategy '%s' — skipping", strategy_name)
            continue
        try:
            signal = builder(settings)
        except Exception as exc:
            logger.warning("Multiplexer: strategy '%s' raised %s — skipping", strategy_name, exc)
            report(
                "strategy_builder",
                "exception",
                level=Level.ERROR,
                reason=f"{type(exc).__name__}: {exc}",
                strategy=strategy_name,
            )
            continue

        if signal.get("side") in ("buy", "sell"):
            risk_scale = STRATEGY_RISK_PCT.get(strategy_name, 1.0)
            signal = dict(signal)
            meta = dict(signal.get("meta") or {})
            meta["strategy_risk_pct"] = float(risk_scale)
            signal["meta"] = meta
            logger.info(
                "Multiplexer: '%s' produced actionable signal (risk_scale=%.2f)",
                strategy_name, risk_scale,
            )
            return signal

        logger.info("Multiplexer: '%s' returned no actionable signal", strategy_name)

    logger.info("Multiplexer: no strategy fired — staying flat")
    return {"symbol": symbol, "side": "none",
            "meta": {"strategy_name": "multiplexed", "reason": "no_strategy_triggered"}}



def run_pipeline(
    settings: dict,
    exchange_client: Any = None,
    telegram_client: Any = None,
    signal_builder: Optional[Callable[[dict], Dict[str, Any]]] = None,
) -> dict:
    """Pipeline adapter. Chooses strategy from STRATEGY env var.

    S-012 PR C5: roster is turtle_soup + vwap. Default is the multiplexer
    so unset / unknown values still iterate the active strategies.
    """
    logger.info("Pipeline start")

    strategy_name = str(os.environ.get("STRATEGY", "multiplexed")).strip().lower()

    if signal_builder is not None:
        builder = signal_builder
    elif strategy_name in ("turtle_soup", "turtlesoup"):
        builder = turtle_soup_signal_builder
    elif strategy_name == "vwap":
        builder = vwap_signal_builder
    else:
        # "multiplexed" or anything unknown → multiplexer.
        builder = multiplexed_signal_builder

    logger.info("Using strategy builder: %s", strategy_name)
    signal = builder(settings)
    _write_ict_signals_from_meta(signal, settings)

    if signal.get("side") in ("buy", "sell"):
        meta = signal.get("meta", {}) or {}
        price = meta.get("price", meta.get("entry_price", signal.get("price")))

        _strat_key = (meta.get("strategy_name") or strategy_name or "").lower()
        try:
            from src.strategy_registry import signal_prefixes as _sp
            _prefixes = _sp(_strat_key)
            _sig_type = _prefixes[0] if _prefixes else "trade_signal"
        except Exception:
            # Pre-S-007 fallback: preserves exact historical behaviour.
            _sig_type = (
                "ml_breakout" if _strat_key == "breakout_confirmation"
                else ("fvg" if meta.get("fvg") else "trade_signal")
            )

        write_signal(
            symbol=signal.get("symbol", "UNKNOWN"),
            signal_type=_sig_type,
            direction="bullish" if signal.get("side") == "buy" else "bearish",
            price=float(price) if price is not None else None,
            timeframe=settings.get("TIMEFRAME", settings.get("timeframe", "unknown")),
            reason="Actionable pipeline signal",
            metadata=str(signal),
        )

    logger.info("Generated signal: %s", signal)

    if signal.get("side") not in ("buy", "sell"):
        logger.info("No actionable signal; skipping order placement.")
        result = {"status": "skipped", "reason": "no_signal", "signal": signal}
    elif os.path.exists(HALT_FLAG_PATH):
        logger.warning("Trader is HALTED — flag file present. Skipping order placement.")
        result = {"status": "halted", "reason": "halt_flag_active"}
    else:
        settings = inject_runtime_counters(settings, exchange_client)
        _strat_name = (signal.get("meta") or {}).get("strategy_name")
        if _strat_name:
            settings = inject_per_strategy_counters(settings, _strat_name)
        _sym = signal.get("symbol", settings.get("SYMBOL", "BTCUSDT"))
        _base = _sym.upper().split("/")[0]
        if _base.endswith("USDT"):
            _base = _base[:-4]
        _tags = list(dict.fromkeys(t for t in [_base, _sym] if t))
        news_result = get_news_score(settings, symbol_tags=_tags)
        if news_result.veto:
            logger.warning("news veto: %s", news_result.reason)
            result = {"status": "news_veto", "reason": news_result.reason, "signal": signal}
            _veto_msg = (
                f"\U0001f6ab News veto: {news_result.reason}\n"
                f"Symbol: {signal.get('symbol', '?')} | Side: {signal.get('side', '?')}"
                f" | Qty: {signal.get('qty', '?')}\n"
                f"Adj: {news_result.adjustment:.4f} | Items: {news_result.item_count}"
            )[:200]
            try:
                send_to_operator(_veto_msg, telegram_client=telegram_client)
            except Exception:
                logger.exception("news veto notify failed")
        else:
            logger.info(
                "news: decision=%s adj=%.4f items=%d reason=%s",
                news_result.decision,
                news_result.adjustment,
                news_result.item_count,
                news_result.reason[:80],
            )

            multi = _multi_account_dispatch_enabled(settings)

            # Operator directive 2026-05-03 — the per-account
            # ``RiskManager.dry_run`` flag is the only dry/live toggle
            # in the codebase. The legacy ``global_dry`` env-var check
            # was deleted; the multi-account fast-path now runs
            # whenever the signal carries full SL/TP, and each account
            # decides whether to live-trade it via its own RiskManager.
            #
            # G5 — the predicate moved to module scope as
            # ``_signal_carries_full_sltp`` so the missing-sltp warning
            # in the audit-log block uses the same definition.
            if multi and _signal_carries_full_sltp(signal):
                # S-026 G2: the multi-account dispatch fast-path skips
                # the legacy ``safe_place_order`` validation entirely.
                # Sizing is now decided per-account inside
                # ``Coordinator.multi_account_execute`` via
                # ``RiskManager.position_size(pkg, balance)`` — the
                # single qty-deciding site post-G2. Halt-flag + news
                # veto are already checked above.

                # Strategy-monocle gate (one open package per strategy
                # globally, regardless of how many accounts follow it).
                # Per the operator directive 2026-05-03: a strategy
                # that already has an open package focuses on
                # *monitoring + updating* that package until SL/TP
                # hits or the strategy decides to close. Pre-fix every
                # actionable tick stacked a new package, so VWAP
                # accumulated 10+ open packages with the operator's
                # accounts unable to keep up.
                _gate_strategy = (
                    (signal.get("meta") or {}).get("strategy_name")
                    or signal.get("strategy")
                )
                _existing_open = _has_open_package_for_strategy(
                    _gate_strategy
                )
                if _existing_open is not None:
                    logger.info(
                        "strategy_monocle: skipping dispatch — strategy=%s "
                        "already has open package %s",
                        _gate_strategy, _existing_open,
                    )
                    result = {
                        "status": "skipped",
                        "reason": "open_package_exists",
                        "strategy": _gate_strategy,
                        "open_package_id": _existing_open,
                        "signal": signal,
                    }
                    _report_pipeline_outcome(result, signal)
                    return result
                # Refusal cooldown — second strategy_monocle gate. Prevents
                # the dispatcher from re-firing the same signal every tick
                # when the most recent attempt was internally refused
                # (``sized_qty=0`` from RiskManager → log_rejection_to_journal
                # ``status='rejected'``). 2026-05-10 produced 20 such rows
                # in 1 h on bybit_2/vwap because the open-package gate above
                # only catches outstanding live positions, not refused
                # ones. Cooldown defaults to 300 s (~one 5 m candle); the
                # most common transient cause of refusal is Bybit V5
                # returning ``availableToBorrow=0`` (S-056 / S-058) and
                # repopulating on the exchange's cadence rather than ours.
                # Operator override: ``STRATEGY_REFUSAL_COOLDOWN_SECONDS``.
                _recent_refusal = _recent_refusal_for_strategy(_gate_strategy)
                if _recent_refusal is not None:
                    logger.info(
                        "strategy_monocle: skipping dispatch — strategy=%s "
                        "refused %.0fs ago (cooldown=%ds, last_pkg=%s)",
                        _gate_strategy,
                        _recent_refusal["age_seconds"],
                        _recent_refusal["cooldown_seconds"],
                        _recent_refusal["order_package_id"],
                    )
                    # Land a dedicated audit row so the operator can
                    # reconstruct cooldown cadence without grepping the
                    # info-level pipeline.log. Best-effort — never let
                    # an audit failure bypass the gate.
                    try:
                        log_signal({
                            "event": "cooldown_blocked",
                            "strategy": _gate_strategy,
                            "symbol": signal.get("symbol"),
                            "side": signal.get("side"),
                            "age_seconds": _recent_refusal["age_seconds"],
                            "cooldown_seconds": _recent_refusal["cooldown_seconds"],
                            "last_refused_package_id": _recent_refusal["order_package_id"],
                        })
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "strategy_monocle: cooldown audit emit failed",
                        )
                    result = {
                        "status": "skipped",
                        "reason": "recent_refusal_cooldown",
                        "strategy": _gate_strategy,
                        "last_refused_package_id": _recent_refusal["order_package_id"],
                        "cooldown_age_seconds": _recent_refusal["age_seconds"],
                        "signal": signal,
                    }
                    _report_pipeline_outcome(result, signal)
                    return result
                try:
                    from src.core.coordinator import Coordinator
                    pkg = _signal_to_order_package(signal, settings)
                    coord = Coordinator()
                    multi_results = coord.multi_account_execute(pkg)
                    result = {
                        "status": "multi_account_dispatched",
                        "multi_account_results": multi_results,
                        "order": signal,
                        "sized_qty_by_account": (pkg.meta or {}).get(
                            "sized_qty_by_account", {}
                        ),
                    }
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "multi-account dispatch failed: %s", exc,
                    )
                    result = {
                        "status": "failed_dispatch",
                        "reason": f"multi_account_execute: {exc}",
                        "order": signal,
                    }
            else:
                # Legacy single-client path. Reached when:
                #   * MULTI_ACCOUNT_DISPATCH is pinned off by the operator
                #     (single-account smoke deployments), or
                #   * signal is missing entry/sl/tp (smoke/synthetic).
                # S-026 G2: sizing has fully moved into the per-account
                # RiskManager. This path still runs ``safe_place_order``
                # for halt-flag / news / validation rails, but it has no
                # per-account context — there is no balance to size
                # against. Use ``DRY_MODE_PLACEHOLDER_QTY`` (1.0) so
                # validation can run. Per the operator directive of
                # 2026-05-03, ``safe_place_order`` no longer carries a
                # mode gate either — the per-account RiskManager is the
                # only dry/live toggle, so this fallback path now hits
                # the exchange via ``client.place_order`` directly when
                # an exchange_client is injected. Tests can stub
                # ``exchange_client`` to assert the dispatch shape.
                _signal_for_orders = {**signal, "qty": _DRY_MODE_PLACEHOLDER_QTY}
                result = safe_place_order(_signal_for_orders, settings, exchange_client)

    _report_pipeline_outcome(result, signal)

    # S-012 PR E4 + S-026 G4: include strategy attribution so the audit
    # log answers "which strategy fired this tick" for every line.
    # Source priority:
    #   1. signal.meta.strategy_name (set by every builder in this module)
    #   2. top-level signal["strategy"]
    #   3. settings["STRATEGY"]
    #   4. env STRATEGY
    #   5. final default — "multiplexed" (matches the actual production
    #      builder when STRATEGY is unset/multiplexed) — *not* "unknown",
    #      because the operator's hourly report counts "unknown" as a
    #      separate bucket and a missing label is uninformative noise.
    #      "unknown" was the silent default before BUG-033; the audit
    #      log now lands a meaningful name for every actionable tick.
    #
    # G5 (CP-2026-05-02-09): the same value also feeds the Telegram
    # "Pipeline result" message.
    _meta = signal.get("meta") or {}
    _strategy = (
        _meta.get("strategy_name")
        or signal.get("strategy")
        or (settings.get("STRATEGY") if isinstance(settings, dict) else None)
        or os.environ.get("STRATEGY")
        or "multiplexed"
    )

    # BUG-033 (CP-2026-05-02-22): the operator reported the hourly
    # summary showing actionable signals attributed to "unknown" even
    # though "Strategies (today)" correctly listed them under their
    # real names. Post-G1 the multiplexer preserves meta correctly, so
    # the remaining leak path is unclear. Land a one-shot diagnostic
    # warning that fires when an actionable signal still resolves to
    # the safety-default attribution — captures the signal keys, meta
    # keys, and which fallback level matched. The next hourly cycle
    # tells the operator (via journalctl) exactly which path produces
    # an under-attributed signal so a follow-up PR can fix it at the
    # source. Delete this block once a real cause is identified.
    if (
        signal.get("side") in ("buy", "sell")
        and (
            _meta.get("strategy_name") in (None, "")
            and signal.get("strategy") in (None, "")
        )
    ):
        try:
            _settings_keys = (
                list(settings.keys()) if isinstance(settings, dict) else []
            )
            logger.warning(
                "audit: actionable signal lacks meta.strategy_name + "
                "top-level strategy; resolved=%r via fallback. "
                "signal_keys=%s meta_keys=%s settings_has_STRATEGY=%s "
                "env_has_STRATEGY=%s",
                _strategy,
                list(signal.keys()),
                list(_meta.keys()),
                "STRATEGY" in _settings_keys,
                "STRATEGY" in os.environ,
            )
        except Exception:  # noqa: BLE001
            logger.exception("audit: BUG-033 diagnostic warning failed")

    # G5 (CP-2026-05-02-09): when an actionable signal reaches the
    # validator without entry/sl/tp populated at the top level, the
    # multi-account dispatch fast-path skips it and the legacy
    # single-client path raises ``failed_validation``. Log the
    # smoking-gun so journalctl identifies the offending strategy
    # without us having to interpret per-tick "failed_validation"
    # noise. S-026 G1: the qty>0 gate dropped — strategies no longer
    # emit qty (sizing is the per-account RiskManager's job in G2).
    if (
        signal.get("side") in ("buy", "sell")
        and not _signal_carries_full_sltp(signal)
    ):
        logger.warning(
            "pipeline: actionable %s signal lacks entry/sl/tp at top level "
            "→ falls into legacy single-client path. signal=%s",
            _strategy, signal,
        )
        try:
            report(
                "pipeline",
                "signal_missing_sltp",
                level=Level.WARN,
                strategy=_strategy,
                symbol=signal.get("symbol"),
                side=signal.get("side"),
            )
        except Exception:  # noqa: BLE001
            logger.exception("pipeline: report() for signal_missing_sltp failed")

    try:
        log_signal(
            {
                "event": "pipeline_result",
                "strategy": _strategy,
                "symbol": signal.get("symbol"),
                "side": signal.get("side"),
                "qty": signal.get("qty"),
                # Carry the structural pattern + confidence the strategy
                # emitter set on the signal. Without these, the dashboard
                # /api/bot/signals consumer can't tell which ICT pattern
                # fired or how confident the model was — see
                # ict-trading-bot#556 + ict-trader-dashboard#5.
                "pattern": signal.get("signal_type") or signal.get("pattern"),
                "confidence": signal.get("confidence"),
                "price": signal.get("entry") or signal.get("entry_price") or signal.get("price"),
                "status": result.get("status"),
                "reason": result.get("reason"),
            }
        )
    except Exception as _audit_exc:  # noqa: BLE001
        logger.exception("pipeline audit log_signal failed")
        # Audit data loss is operationally relevant — surface it.
        # WARN, not ERROR, because this is a write to a JSONL we own;
        # if it fails repeatedly the disk-free health check or hourly
        # report will escalate.
        report(
            "audit_log",
            "write_failed",
            level=Level.WARN,
            reason=f"{type(_audit_exc).__name__}: {_audit_exc}",
        )

    status = result.get("status", "unknown")
    reason = result.get("reason")
    symbol = signal.get("symbol", "?")
    side = signal.get("side", "?")
    qty = signal.get("qty", "?")

    # G5 — the leading line keeps the canonical
    # ``Pipeline result: status=... | strategy=... | symbol=... |
    # side=... | qty=...`` format so journalctl greps and the existing
    # audit consumers stay stable. Section bodies (collapsable in HTML
    # clients) carry the deeper detail the operator asked for —
    # strategy attribution, order package, why-it-failed remediation.
    header = (
        f"Pipeline result: status={status} | strategy={_strategy} "
        f"| symbol={symbol} | side={side} | qty={qty}"
    )
    if reason:
        header += f" | reason={reason}"

    sections = _pipeline_result_sections(
        signal=signal, result=result, strategy=_strategy,
    )
    html_body = render_html(header=header, sections=sections)
    plain_body = render_plain(header=header, sections=sections)

    try:
        send_to_operator(plain_body, html_body, telegram_client=telegram_client)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline: all notify paths failed")

    logger.info("Pipeline complete: %s", result)

    write_status()

    return {
        "signal": signal,
        "order_result": result,
    }
