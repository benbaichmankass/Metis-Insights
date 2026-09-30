"""Strategy-monocle gate helpers — extracted from pipeline.py (PR-8 / D1).

One open package per strategy (``_has_open_package_for_strategy``; applied
per ACCOUNT by ``_open_package_scope``) and a short refusal cooldown after a
``sized_qty=0`` rejection (``_recent_refusal_for_strategy``). The open-package
helpers FAIL CLOSED on a DB-read failure (2026-09-30); the cooldown/debounce
helpers stay best-effort (a failure there only loses a throttle, never
stacks a package on a live one).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

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


#: What ``_has_open_package_for_strategy`` returns in place of a package id
#: when the journal could not be read (fail closed).
UNREADABLE_PACKAGE_PREFIX = "journal_unreadable:"


def _has_open_package_for_strategy(
    strategy_name: Optional[str], symbol: Optional[str] = None
) -> Optional[str]:
    """Strategy-monocle gate: return the order_package_id of an existing
    open package for *strategy_name* (scoped to *symbol* when given), or
    ``None`` when no open package exists.

    Operator directive 2026-05-03: a strategy may have **one** open
    package globally — across all accounts that follow it. Once a
    package is logged, the strategy's job is to monitor + update
    that package via ``order_monitor`` until SL/TP hits or the
    strategy decides to close (PRs 2 + 3 of this sprint wire the
    close path).

    Multi-symbol (2026-05-22): "one open package per strategy" is
    **per instrument**. Pass ``symbol`` so an open BTCUSDT package
    does not suppress an MES entry for the same strategy (and vice
    versa). When ``symbol`` is None the query keeps its legacy
    strategy-global scope (single-symbol callers / tests).

    FAIL CLOSED (2026-09-30): a DB-read failure returns
    ``UNREADABLE_PACKAGE_PREFIX + <exception type>`` so the caller skips the
    dispatch with that cause. Until then it returned ``None`` and the
    dispatcher proceeded, which could stack a second package on a live one
    during the failure window.

    The strategy_name is read from ``signal.meta.strategy_name``
    (the canonical attribution source post-BUG-033). When unset
    (multiplexer / unknown), the gate is bypassed — there's no
    canonical name to scope the open-package query to.
    """
    if not strategy_name:
        return None
    try:
        from src.units.db.database import Database
        from src.utils.paths import trade_journal_db_path
        db_path = trade_journal_db_path()
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
            strategy_name, status="open", limit=1, symbol=symbol,
        )
        if rows:
            return str(rows[0].get("order_package_id") or "")
        return None
    except Exception as exc:  # noqa: BLE001
        # FAIL CLOSED (2026-09-30, PI-20260930-QZSE4AMA-0001 review): an
        # unreadable journal may hide an open package, and passing would
        # stack a second one on it. The account stays live; this dispatch is
        # refused with the cause logged. (Was: return None — dispatch proceeds.)
        logger.warning(
            "_has_open_package_for_strategy(%s, symbol=%s): DB read failed — "
            "blocking dispatch (fail closed): %s",
            strategy_name, symbol, exc,
        )
        return f"{UNREADABLE_PACKAGE_PREFIX}{type(exc).__name__}"


# ---------------------------------------------------------------------------
# Per-ACCOUNT scope for the open-package gate (PI-20260930-QZSE4AMA-0001)
# ---------------------------------------------------------------------------
#
# ``_has_open_package_for_strategy`` answers "does this strategy+symbol have an
# open package ANYWHERE". The 2026-05-03 directive behind it (#387) was an
# anti-STACKING rule — VWAP had 10+ open packages because every tick minted a
# new one — and "globally, across all accounts" was the cheap way to say it on
# a system that then had one real-money book. It predates the Stage-1/Stage-2
# split, so on 2026-09-30 a PAPER leg held a REAL-MONEY leg hostage:
# pkg-5e636e3adddf4a83 (IEF short, 07-29) is open because alpaca_paper and
# alpaca_portfolio still hold it, alpaca_live's leg was rejected and it is
# flat, and every next ief_pullback_1d entry would have been skipped for
# alpaca_live too.
#
# The intent is kept at the grain it actually protects: an ACCOUNT never gets a
# second package for a strategy+symbol while it still holds a leg of the first.
# An account that holds nothing is not blocked by another account's leg — EXCEPT
# its Stage-2 pair partner's (STAGE2_PAIRS below): live and mirror are coupled
# so they only ever take the same trades.

#: Trade statuses that mean "this leg is no longer live". Mirrors
#: ``order_monitor._TERMINAL_TRADE_STATUSES`` plus ``shadow_expired``;
#: ``tests/test_monocle_gate_per_account.py`` pins the superset relation.
_TERMINAL_LEG_STATUSES = (
    "orphaned",
    "exchange_rejected",
    "closed",
    "rejected",
    "rejected_too_small",
    "shadow_expired",
)

#: Stage-2 PAIRS: the real-money book and its paper mirror (CLAUDE.md § "The
#: promotion ladder" — "the mirror and the live account carry the same
#: strategies and take the same trades at all times", operator 2026-09-21).
#: No config key declares the pairing; this constant is tied to the pairs
#: ``tests/test_paper_portfolio_accounts.py::test_mirror_trade_shaping_fields_equal_live``
#: parametrizes (and to ``scripts/ops/mandate_resolver.py::MIRROR_OF``) by
#: ``tests/test_monocle_gate_per_account.py``.
#:
#: PAIR-COUPLED: a leg held by EITHER member keeps BOTH out, so the pair only
#: ever opens a trade together. Stage-1 soak accounts (bybit_1, alpaca_paper,
#: ib_paper, …) are in no pair: their legs block only themselves and never a
#: Stage-2 account — which is what removes the reported bug (a PAPER soak
#: package blocking a REAL-MONEY entry).
STAGE2_PAIRS = (
    ("bybit_2", "bybit_portfolio"),
    ("alpaca_live", "alpaca_portfolio"),
)


def _open_package_holders(
    strategy_name: Optional[str], symbol: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Who holds *strategy_name*'s open package(s) on *symbol*, per account.

    Returns ``None`` ONLY when the journal was read and no open package
    exists. Otherwise a dict:

    * ``read_error`` — the order_packages OR the per-package trades read
      failed. The caller BLOCKS (fail closed): a package it could not read
      may have a live leg on any account, and passing the round would stack
      a second package on it. The account stays live; this round is refused
      with the cause logged (Prime Directive posture).
    * ``by_account`` — every account with a NON-terminal leg on an open
      package. Those accounts are blocked (plus their Stage-2 pair partner).
    * ``unscoped`` — an open package with NO live leg on any account (just
      minted, every leg refused, or not yet linked). It cannot be attributed
      to an account, so it keeps the strategy-wide block: the 2026-05-09
      retry-storm case (``linked_only`` dropped).
    """
    if not strategy_name:
        return None
    try:
        from src.units.db.database import Database
        from src.utils.paths import trade_journal_db_path
        db = Database(db_path=trade_journal_db_path())
        pkgs = db.get_order_packages_by_strategy(
            strategy_name, status="open", symbol=symbol,
        )
        if not pkgs:
            return None
        by_account: Dict[str, str] = {}
        unscoped: Optional[str] = None
        placeholders = ",".join("?" * len(_TERMINAL_LEG_STATUSES))
        conn = db.connect()
        try:
            for pkg in pkgs:
                pkg_id = str(pkg.get("order_package_id") or "")
                rows = conn.execute(
                    "SELECT DISTINCT account_id FROM trades "
                    " WHERE order_package_id = ? "
                    "   AND COALESCE(is_backtest, 0) = 0 "
                    f"  AND COALESCE(status, 'open') NOT IN ({placeholders})",
                    (pkg_id, *_TERMINAL_LEG_STATUSES),
                ).fetchall()
                accounts = [str(r[0]) for r in rows if r[0]]
                if not accounts:
                    unscoped = unscoped or pkg_id
                for acct in accounts:
                    by_account.setdefault(acct, pkg_id)
        finally:
            conn.close()
        return {"unscoped": unscoped, "by_account": by_account, "read_error": None}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_open_package_holders(%s, symbol=%s): journal read failed — "
            "blocking the round (fail closed): %s",
            strategy_name, symbol, exc,
        )
        return {"unscoped": None, "by_account": {},
                "read_error": f"{type(exc).__name__}: {exc}"}


def _accounts_declaring(strategy_name: str) -> Optional[frozenset]:
    """Names of the accounts ``Coordinator.multi_account_execute`` could send
    *strategy_name* to: every ``config/accounts.yaml`` entry not
    ``enabled: false`` whose ``strategies`` names it (or has no ``strategies``
    key — the coordinator's legacy accept-all). A SUPERSET of the eligible set
    by construction, which is safe because ``account_scope`` only narrows.
    ``None`` on a read failure (caller falls back to the strategy-wide block).
    """
    try:
        from src.config.accounts_loader import load_accounts_dict
        errors: list = []
        accounts = load_accounts_dict(errors=errors)
        if errors or not accounts:
            # The loader returns {} on a read failure; "could not read" must
            # not look like "no account declares it".
            logger.warning("_accounts_declaring(%s): accounts.yaml unreadable — %s",
                           strategy_name, errors or "empty")
            return None
        out = set()
        for name, cfg in accounts.items():
            cfg = cfg or {}
            if cfg.get("enabled") is False:
                continue
            assigned = cfg.get("strategies")
            if assigned is None or strategy_name in assigned:
                out.add(str(name))
        return frozenset(out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_accounts_declaring(%s): read failed — %s", strategy_name, exc)
        return None


def _open_package_scope(
    strategy_name: Optional[str],
    symbol: Optional[str],
    scope: Optional[frozenset],
) -> Dict[str, Any]:
    """The open-package gate for ONE dispatch round, per account.

    Returns one of:

    * ``{"action": "pass", "scope": scope}`` — nothing held; round unchanged
      (byte-for-byte the pre-change path, ``scope`` may stay ``None``).
    * ``{"action": "narrow", "scope": frozenset, "excluded": {acct: pkg},
      "pair_coupled": {acct: {"partner", "order_package_id"}}}`` — some
      accounts in the round hold a leg (or their Stage-2 partner does);
      dispatch to the rest only.
    * ``{"action": "block", "order_package_id": pkg, "excluded": {...},
      "pair_coupled": {...}, "read_error": str|None}`` — every account in the
      round is held, an unattributable open package exists, or the journal /
      the round's accounts could not be read (FAIL CLOSED).
    """
    holders = _open_package_holders(strategy_name, symbol)
    if holders is None:
        return {"action": "pass", "scope": scope}
    if holders.get("read_error"):
        return {"action": "block", "order_package_id": None, "excluded": {},
                "pair_coupled": {}, "read_error": holders["read_error"]}
    if holders["unscoped"]:
        return {"action": "block", "order_package_id": holders["unscoped"],
                "excluded": dict(holders["by_account"]), "pair_coupled": {},
                "read_error": None}
    held: Dict[str, str] = dict(holders["by_account"])
    excluded: Dict[str, str] = dict(held)
    pair_coupled: Dict[str, Dict[str, str]] = {}
    for a, b in STAGE2_PAIRS:
        for member, partner in ((a, b), (b, a)):
            if partner in held and member not in held:
                excluded[member] = held[partner]
                pair_coupled[member] = {"partner": partner,
                                        "order_package_id": held[partner]}
    candidates = scope if scope is not None else _accounts_declaring(str(strategy_name))
    if candidates is None:
        return {"action": "block",
                "order_package_id": next(iter(held.values()), None),
                "excluded": excluded, "pair_coupled": pair_coupled,
                "read_error": "accounts.yaml unreadable"}
    hit = {a: p for a, p in excluded.items() if a in candidates}
    if not hit:
        return {"action": "pass", "scope": scope}
    coupled_hit = {a: v for a, v in pair_coupled.items() if a in hit}
    remaining = frozenset(candidates) - frozenset(hit)
    if not remaining:
        return {"action": "block", "order_package_id": next(iter(hit.values())),
                "excluded": hit, "pair_coupled": coupled_hit, "read_error": None}
    return {"action": "narrow", "scope": remaining, "excluded": hit,
            "pair_coupled": coupled_hit}


def _recent_refusal_for_strategy(
    strategy_name: Optional[str],
    cooldown_seconds: Optional[int] = None,
    symbol: Optional[str] = None,
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
        from src.utils.paths import trade_journal_db_path
        db_path = trade_journal_db_path()
        db = Database(db_path=db_path)
        rows = db.get_order_packages_by_strategy(
            strategy_name, status="rejected", limit=1, symbol=symbol,
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


# ---------------------------------------------------------------------------
# Bar-close debounce — one entry attempt per CLOSED bar (PERF-20260601-001)
# ---------------------------------------------------------------------------
#
# The open-package gate blocks re-entry only while a package is *open*. When a
# package closes mid-bar (e.g. the reconciler records an exchange-side SL/TP
# fire), the gate frees and the strategy re-fires its still-valid breakout on
# the very next tick — within the SAME bar. On a 2 h strategy this produced a
# re-entry storm (9 packages in ~1 h on 2026-06-01) and a flood of
# ``intent_noop`` rejection rows (the intent layer no-ops the duplicate while
# a net position is already held), polluting the journal and skewing
# per-strategy stats. A bar-close strategy should act AT MOST ONCE per closed
# bar; this gate enforces that by suppressing a second actionable dispatch for
# the same strategy+symbol within the same timeframe bucket as the most recent
# package it already created. Env kill-switch: ``STRATEGY_BAR_DEBOUNCE_DISABLED``.

_TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400, "1w": 604800,
}


def _timeframe_seconds(timeframe: Optional[str]) -> Optional[int]:
    """Parse a timeframe token (``2h`` / ``15m`` / ``1d``) to seconds.

    Returns ``None`` for unknown / unparseable tokens so the caller can
    fall back to "no debounce" rather than guess a bucket size.
    """
    if not timeframe:
        return None
    tf = str(timeframe).strip().lower()
    if tf in _TIMEFRAME_SECONDS:
        return _TIMEFRAME_SECONDS[tf]
    # generic <int><unit> parse (m/h/d/w) for anything not in the table
    try:
        unit = tf[-1]
        n = int(tf[:-1])
        mult = {"m": 60, "h": 3600, "d": 86400, "w": 604800}.get(unit)
        return n * mult if (mult and n > 0) else None
    except (ValueError, IndexError):
        return None


def _strategy_timeframe_seconds(strategy_name: str) -> Optional[int]:
    """Look up *strategy_name*'s configured timeframe (seconds). Best-effort."""
    try:
        from src.units.strategies import load_strategy_config
        cfg = (load_strategy_config() or {}).get(strategy_name) or {}
        return _timeframe_seconds(cfg.get("timeframe"))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_strategy_timeframe_seconds(%s): config read failed — %s",
            strategy_name, exc,
        )
        return None


def _strategy_timeframe_label(strategy_name: str) -> Optional[str]:
    """Look up *strategy_name*'s configured timeframe TOKEN (e.g. ``"1h"``).

    The string companion of ``_strategy_timeframe_seconds`` — the regime
    advisory spec map keys on the ``(symbol, timeframe)`` token, not seconds, so
    a string is what the ML-vol verdict needs. Returns ``None`` when the
    strategy has no configured ``timeframe`` or the config can't be read
    (best-effort; the verdict then degrades to ``unknown`` — permissive).
    """
    try:
        from src.units.strategies import load_strategy_config
        cfg = (load_strategy_config() or {}).get(strategy_name) or {}
        tf = cfg.get("timeframe")
        return str(tf).strip() if tf else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_strategy_timeframe_label(%s): config read failed — %s",
            strategy_name, exc,
        )
        return None


def _bar_debounce_disabled() -> bool:
    # The flag below is a kill-switch for an over-trading DEBOUNCE, not a
    # live/dry gate: it only throttles re-entry frequency (one entry per
    # closed bar) and never decides whether a strategy trades live vs dry
    # (that stays accounts.yaml mode + strategies.yaml execution). Mirrors
    # the STRATEGY_REFUSAL_COOLDOWN_SECONDS rollback knob.
    raw = os.environ.get("STRATEGY_BAR_DEBOUNCE_DISABLED", "")  # allow-silent: debounce kill-switch, not a live/dry gate
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _same_bar_entry_for_strategy(
    strategy_name: Optional[str], symbol: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return ``{"order_package_id", "bar_seconds", "last_created_at"}`` when
    *strategy_name* already created a package for *symbol* inside the CURRENT
    timeframe bucket (i.e. it already acted this bar), else ``None``.

    "Current bucket" is ``floor(epoch / bar_seconds)`` — so the first entry of
    a 2 h bar dispatches and every subsequent actionable tick in that same 2 h
    window is suppressed, regardless of whether the first package has since
    closed. Resets cleanly when a new bar opens.

    Best-effort: missing timeframe, kill-switch, or a DB-read failure all
    return ``None`` (no debounce) rather than block dispatch on a hiccup.
    """
    if not strategy_name or _bar_debounce_disabled():
        return None
    bar_seconds = _strategy_timeframe_seconds(strategy_name)
    if not bar_seconds or bar_seconds <= 0:
        return None
    try:
        from datetime import datetime, timezone
        from src.units.db.database import Database
        from src.utils.paths import trade_journal_db_path
        db = Database(db_path=trade_journal_db_path())
        # Any-status, newest-first; scan a few in case the latest-updated row
        # (e.g. an old package re-touched by a trail ratchet) is not the
        # latest-created one.
        rows = db.get_order_packages_by_strategy(
            strategy_name, status=None, limit=5, symbol=symbol,
        )
        if not rows:
            return None
        now_bucket = int(datetime.now(timezone.utc).timestamp() // bar_seconds)
        for row in rows:
            created = row.get("created_at")
            if not created:
                continue
            try:
                ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if int(ts.timestamp() // bar_seconds) == now_bucket:
                return {
                    "order_package_id": str(row.get("order_package_id") or ""),
                    "bar_seconds": int(bar_seconds),
                    "last_created_at": str(created),
                }
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_same_bar_entry_for_strategy(%s, symbol=%s): DB read failed — %s",
            strategy_name, symbol, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Empty-sizing brake — refuse a signal ONCE when nothing could be sized
# BL-20260905-MES-TREND-LONG-1D-RE-EMITTED-ONE-DAILY-SIGNAL-SEVEN-TIMES-IN-49-MINUTES-WHEN-SIZING-RETURNED-EMPTY  # noqa: E501
# ---------------------------------------------------------------------------
#
# THE GAP THIS FILLS. The three gates above are each bounded by something that
# is not "did this attempt size anything":
#   * ``_has_open_package_for_strategy`` frees the moment the package leaves
#     'open' — and a package that sized nothing is terminalised (or, pre-
#     BUG-049-backstop, orphaned by the reconciler at +5 min), so it frees on
#     its own and the next tick re-emits. That +5 min is visible in the
#     evidence: seven packages ~8 minutes apart.
#   * ``_recent_refusal_for_strategy`` requires ``status='rejected'`` AND a
#     300 s window. The observed cadence was ~8 min — WIDER than the cooldown.
#   * ``_same_bar_entry_for_strategy`` needs the strategy's configured
#     ``timeframe`` to resolve; when it does not, it returns None and the
#     debounce silently is not there.
# So an empty sizing map had no ceiling of its own. This gate is that ceiling,
# and it keys on the thing that actually went wrong.
#
# ⚠️ IT DOES NOT SUPPRESS SILENTLY. It fires only on a package that recorded a
# NAMED cause (``meta.empty_sizing_refusal.cause``, written by
# ``coordinator.multi_account_execute``) and it logs that cause every time it
# blocks. A brake that quietly swallowed the re-emission would convert a noisy
# failure into a silent one — strictly worse, and the exact class
# ``silent_refusal_alert`` exists to catch.
#
# SCOPE: this is the brake, not the cause. Why sizing returned empty on a leg
# that traded normally 63 days later is a separate, filed question.

def _empty_sizing_brake_disabled() -> bool:
    # Kill-switch mirroring STRATEGY_BAR_DEBOUNCE_DISABLED: this throttles
    # RE-EMISSION of a signal that already sized to nothing. It never decides
    # whether a strategy trades live vs dry (that stays accounts.yaml mode +
    # strategies.yaml execution), and it is default-ON, not a default-off
    # *_ENABLED gate.
    raw = os.environ.get("STRATEGY_EMPTY_SIZING_BRAKE_DISABLED", "")  # allow-silent: re-emission brake, not a live/dry gate
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _empty_sizing_refusal_for_signal(
    strategy_name: Optional[str],
    signal_key: Optional[str],
    symbol: Optional[str] = None,
    *,
    scan_limit: int = 5,
) -> Optional[Dict[str, Any]]:
    """Return the recorded refusal when *this same signal* already sized to
    nothing, else ``None``.

    Matches on ``meta.empty_sizing_refusal.signal_key`` — the SIGNAL's
    identity, not a time window — so the refusal holds for as long as the
    signal keeps re-presenting and lifts the instant the strategy produces a
    different one. A blank ``signal_key`` on either side means "no identity",
    and an unidentifiable signal is never braked.

    Returns ``{"order_package_id", "cause", "signal_key", "refused_at"}``.

    Best-effort, matching the contract of every sibling helper in this module:
    a DB-read failure returns ``None`` (dispatch proceeds) rather than blocking
    every signal on a transient SQLite hiccup. The cost of that direction is
    one extra package in the read-failure window; the cost of the other is a
    trader that stops trading when SQLite hiccups.
    """
    if not strategy_name or not signal_key or _empty_sizing_brake_disabled():
        return None
    try:
        import json as _json
        from src.units.db.database import Database
        from src.utils.paths import trade_journal_db_path

        db = Database(db_path=trade_journal_db_path())
        # Any status: the refusal is terminal ('rejected'), and pre-backstop
        # rows could also be 'orphaned' — the gate must see both, so it filters
        # on the stamp rather than on a status whitelist.
        rows = db.get_order_packages_by_strategy(
            strategy_name, status=None, limit=scan_limit, symbol=symbol,
        )
        for row in rows or []:
            meta = row.get("meta")
            if isinstance(meta, str):
                try:
                    meta = _json.loads(meta)
                except (TypeError, ValueError):
                    continue
            if not isinstance(meta, dict):
                continue
            refusal = meta.get("empty_sizing_refusal")
            if not isinstance(refusal, dict):
                continue
            if str(refusal.get("signal_key") or "") != str(signal_key):
                continue
            return {
                "order_package_id": str(row.get("order_package_id") or ""),
                "cause": str(refusal.get("cause") or "unrecorded"),
                "signal_key": str(signal_key),
                "refused_at": str(refusal.get("refused_at") or ""),
            }
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_empty_sizing_refusal_for_signal(%s, symbol=%s): DB read failed — %s",
            strategy_name, symbol, exc,
        )
        return None
