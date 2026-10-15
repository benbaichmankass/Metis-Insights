"""The trader-restart Telegram ping, and the boot-time journal ↔ broker reconcile.

ONE message per trader start (checklist row ``TELEGRAM-RESTART-MSG``, operator
2026-10-15), replacing two older pings — the flat per-strategy package list
(S-021 / BUG-048) and the separate Bybit-only ghost alert (Sprint A-3):

* **Line 1 — status headline.** ``✅`` when every live trade is accounted for
  and nothing needs attention; ``❌ N problem(s)`` otherwise.
* **Line 2 — cause.** From :mod:`src.runtime.restart_cause` (deploy marker,
  systemd's record of the previous exit, host uptime). ``cause: unknown`` when
  the evidence does not settle it — never a guess.
* **Sections by venue / account class** (Bybit live, Alpaca live, IB, Prop,
  then the paper books), each a collapsible ``<blockquote expandable>`` built by
  the shared :func:`src.units.ui.telegram_format.render_html_fitted` (no second
  renderer). A section lists, per account, ``N broker positions / N tracked /
  SL+TP at broker k/N``, every ghost and untracked position by name, and only
  the legs that HAVE open packages.

**Selectivity.** Before this, every start with any open package pinged the full
list, and every merge that touches runtime code restarts the trader
(``scripts/deploy_pull_restart.sh`` — the non-runtime safe set already skips
docs/tests/research merges). So a routine deploy restart with a clean
reconcile now sends ONE compact two-line message; the full sectioned message
is reserved for a start that needs attention: any problem, or a cause that was
not planned (crash, OOM, VM reboot, watchdog autoheal, unknown).

**What is MEASURED, not claimed.** The old body said *"Bybit holds SL/TP at the
broker for every open position"* — a claim, and false for non-Bybit venues. The
bracket state is now read from each venue's read-only order surface
(``account_bybit_open_orders`` / ``account_alpaca_open_orders`` /
``account_ib_open_orders``). An account or bracket we could not read renders
``— (could not read)`` and counts as a problem: *we did not look* is never
folded into *nothing wrong*. The trader has no broker read path for prop
(``breakout``) accounts; those render ``— (no broker read from the trader)``
and are named in the headline rather than counted as zero.

Design constraints: best-effort; nothing here may raise into boot or delay the
first tick for more than :data:`REPORT_WAIT_S` (``main.py`` runs it on a daemon
thread and waits that long at most). Reconcile covers
``mode: live`` accounts only — a dry account has no broker positions.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_DRY_MODES = {"dry", "dry_run", "dry-run", "paper"}
#: exchanges the trader can read positions + resting orders for
_READABLE = {"bybit", "alpaca", "interactive_brokers", "ib"}
NO_READ_PATH = "— (no broker read from the trader)"
COULD_NOT_READ = "— (could not read)"


def snapshot_strategy_versions_on_boot() -> Optional[int]:
    """Snapshot the active ``config/strategies.yaml`` into
    ``trade_journal.db::strategy_versions`` on boot.

    Wires the previously-dead ``strategy_versions`` table to an actual
    producer (S-PERSIST-CANON): each distinct strategies.yaml content gets
    one row, keyed by a short content hash so an unchanged config is an
    idempotent no-op. Gives the Data Explorer an in-DB, queryable history
    of strategy-config versions alongside git + strategy_changelog.json.
    Best-effort — never raises; a failure never blocks boot.

    Returns the new row id, or None when the config was unchanged or the
    snapshot could not be written.
    """
    try:
        import hashlib
        from datetime import datetime, timezone

        import yaml

        from src.units.db.database import Database
        from src.utils.paths import repo_root

        cfg_path = os.path.join(repo_root(), "config", "strategies.yaml")
        with open(cfg_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        parsed = yaml.safe_load(raw) or {}
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        version_name = f"strategies-{digest}"

        db = Database()
        if db.get_strategy_version(version_name) is not None:
            return None  # unchanged config — idempotent
        desc = f"auto-snapshot {datetime.now(timezone.utc).isoformat()}"
        row_id = db.save_strategy_version(version_name, parsed, description=desc)
        logger.info(
            "boot_audit: snapshotted strategy config version %s (row %s)",
            version_name, row_id,
        )
        return row_id
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort audit snapshot; never blocks boot
        logger.warning("boot_audit: strategy_version snapshot failed: %s", exc)
        return None




# ---------------------------------------------------------------------------
# Gather
# ---------------------------------------------------------------------------

def _load_live_accounts() -> Dict[str, Dict[str, Any]]:
    """``{account_id: cfg}`` for enabled ``mode: live`` accounts, in the shape
    the venue read helpers take (``list_accounts`` carries the credential and
    IB connection fields), with ``account_class`` from ``accounts.yaml``."""
    try:
        from src.config.accounts_loader import load_accounts_dict
        raw = load_accounts_dict() or {}
    except Exception as exc:  # noqa: BLE001  # allow-silent: an empty roster is rendered as "could not read accounts"
        logger.warning("boot_reconcile: could not load accounts config: %s", exc)
        return {}
    try:
        from src.units.ui.data_loaders import list_accounts
        shaped = {a["account_id"]: a for a in list_accounts() if a.get("account_id")}
    except Exception as exc:  # noqa: BLE001  # allow-silent: falls back to the raw yaml dict
        logger.warning("boot_reconcile: list_accounts failed: %s", exc)
        shaped = {}
    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict) or cfg.get("enabled") is False:
            continue
        if str(cfg.get("mode") or "live").lower() in _DRY_MODES:
            continue
        merged = {**cfg, **shaped.get(str(name), {})}
        merged["account_id"] = str(name)
        merged.setdefault("exchange", "bybit")
        merged["mode"] = str(cfg.get("mode") or "live")
        merged["account_class"] = cfg.get("account_class")
        out[str(name)] = merged
    return out


def _open_journal_trades(account_id: str, db_path: str) -> Optional[List[Dict[str, Any]]]:
    """Open non-backtest trade rows for *account_id*; ``None`` = could not read."""
    if not os.path.exists(db_path):
        return None
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, symbol, direction, strategy_name FROM trades "
                "WHERE account_id = ? AND status = 'open' "
                "AND COALESCE(is_backtest, 0) = 0",
                (account_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001  # allow-silent: None renders "could not read" and counts as a problem
        logger.warning("boot_reconcile: journal query failed for account=%s: %s", account_id, exc)
        return None


def _open_packages_by_account(db_path: str) -> Optional[Dict[str, Dict[str, int]]]:
    """``{account_id: {strategy: n}}`` for open LINKED order packages (a trade
    was placed), via the linked trade's account. ``None`` = could not read."""
    if not os.path.exists(db_path):
        return None
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            rows = conn.execute(
                "SELECT COALESCE(t.account_id, '(unlinked account)'), p.strategy_name, COUNT(*) "
                "FROM order_packages p LEFT JOIN trades t ON t.id = p.linked_trade_id "
                "WHERE p.status = 'open' AND p.linked_trade_id IS NOT NULL "
                "GROUP BY 1, 2",
            ).fetchall()
    except Exception as exc:  # noqa: BLE001  # allow-silent: None renders "could not read"
        logger.warning("boot_audit: open-package query failed: %s", exc)
        return None
    out: Dict[str, Dict[str, int]] = defaultdict(dict)
    for acct, strat, n in rows:
        out[str(acct)][str(strat)] = int(n)
    return dict(out)


def _side(raw: Any) -> Optional[str]:
    return {"buy": "long", "long": "long", "sell": "short", "short": "short"}.get(
        str(raw or "").strip().lower())


def _closing_side(pos_side: Optional[str]) -> Optional[str]:
    return {"long": "short", "short": "long"}.get(pos_side or "")


def _leg_kind(order: Dict[str, Any]) -> Optional[str]:
    """``stop`` | ``target`` | None for one resting order row, venue-agnostic.

    The STOP family is tested first: a stop-limit's type contains "limit", and
    an LMT-first test would file it as a take-profit (the same ordering
    ``AlpacaClient.protection_state`` / ``IBClient._protective_leg_side`` use).
    """
    sot = str(order.get("stop_order_type") or "").lower()          # Bybit
    if "takeprofit" in sot:
        return "target"
    if "stop" in sot or "trailing" in sot:
        return "stop"
    otype = str(order.get("order_type") or "").lower().replace(" ", "")
    if any(k in otype for k in ("stop", "stp", "trail")):
        return "stop"
    if "limit" in otype or "lmt" in otype:
        if str(order.get("venue")) == "bybit" and not order.get("reduce_only"):
            return None       # a non-reduce-only Bybit limit is an entry, not a TP
        return "target"
    return None


def _brackets(cfg: Dict[str, Any]) -> Optional[Tuple[Dict[Tuple[str, Optional[str]], Dict[str, bool]], List[Dict[str, Any]]]]:
    """Measured bracket state for *cfg*'s account.

    Returns ``(position_level, orders)`` — ``position_level`` maps
    ``(symbol, side)`` to ``{"stop": bool, "target": bool}`` for venues that
    carry protection ON the position (Bybit Full mode); ``orders`` is every
    resting order row tagged with ``venue``. ``None`` = could not look (never
    the same as "nothing rests").
    """
    ex = str(cfg.get("exchange") or "").lower()
    from src.units.accounts import clients
    if ex == "bybit":
        payload = clients.account_bybit_open_orders(cfg)
        if payload is None:
            return None
        pos_level: Dict[Any, Any] = {}
        # a symbol whose StopOrder or Order cross-check failed has an UNREAD half
        for item in payload.get("order_symbols_unchecked") or []:
            pos_level[("__unread__", str(item).split("/")[0].upper())] = True
        for p in payload.get("positions") or []:
            pos_level[(str(p.get("symbol") or "").upper(), _side(p.get("side")))] = {
                "stop": p.get("stop_loss") is not None,
                "target": p.get("take_profit") is not None,
            }
        return pos_level, [{**o, "venue": "bybit"} for o in payload.get("orders") or []]
    if ex == "alpaca":
        payload = clients.account_alpaca_open_orders(cfg)
        if payload is None:
            return None
        return {}, [{**o, "venue": "alpaca"} for o in payload.get("orders") or []]
    if ex in ("interactive_brokers", "ib"):
        rows = clients.account_ib_open_orders(cfg)
        if rows is None:
            return None
        return {}, [{**o, "venue": "ib",
                     "side": o.get("side") or o.get("action")} for o in rows]
    return None


def _grade(sym: str, side: Optional[str], pos_level, orders) -> Optional[Dict[str, bool]]:
    """``{"stop", "target"}`` for one broker position; ``None`` = unread."""
    if pos_level.get(("__unread__", sym)):
        return None
    got = dict(pos_level.get((sym, side)) or {"stop": False, "target": False})
    want = _closing_side(side)
    for o in orders:
        if str(o.get("symbol") or "").upper() != sym:
            continue
        oside = _side(o.get("side"))
        if want and oside and oside != want:
            continue          # a leg for the other book of a hedged symbol
        kind = _leg_kind(o)
        if kind:
            got[kind] = True
    return got


@dataclass
class AccountReport:
    account_id: str
    group: str
    readable: bool = True                       # the trader has a broker read path
    broker: Optional[List[Tuple[str, Optional[str]]]] = None   # None = could not read
    tracked: int = 0
    journal_open: Optional[int] = None          # None = could not read
    ghosts: List[str] = field(default_factory=list)
    untracked: List[str] = field(default_factory=list)
    bracket_read: bool = True
    full_bracket: int = 0
    missing_bracket: List[str] = field(default_factory=list)
    legs: Optional[Dict[str, int]] = None        # strategy -> open packages; None = could not read

    @property
    def problems(self) -> int:
        n = len(self.ghosts) + len(self.untracked) + len(self.missing_bracket)
        if self.readable and self.broker is None:
            n += 1
        if self.journal_open is None:
            n += 1
        if self.readable and self.broker and not self.bracket_read:
            n += 1
        return n


_GROUP_ORDER = ["Bybit live", "Alpaca live", "IB live", "Prop",
                "Bybit paper", "Alpaca paper", "IB paper"]


def _group(cfg: Dict[str, Any]) -> str:
    ex = str(cfg.get("exchange") or "").lower()
    cls = str(cfg.get("account_class") or "").lower()
    if ex == "breakout" or cls == "prop":
        return "Prop"
    venue = {"bybit": "Bybit", "alpaca": "Alpaca", "interactive_brokers": "IB",
             "ib": "IB"}.get(ex, ex.title() or "Other")
    return f"{venue} {'live' if cls == 'real_money' else 'paper'}"


def reconcile_account(cfg: Dict[str, Any], db_path: str,
                      legs: Optional[Dict[str, Dict[str, int]]]) -> AccountReport:
    aid = cfg["account_id"]
    ex = str(cfg.get("exchange") or "").lower()
    rep = AccountReport(aid, _group(cfg), readable=ex in _READABLE)
    rep.legs = None if legs is None else dict(legs.get(aid) or {})
    journal = _open_journal_trades(aid, db_path)
    rep.journal_open = None if journal is None else len(journal)
    if not rep.readable:
        return rep
    try:
        from src.units.accounts.clients import account_open_positions
        positions = account_open_positions(cfg)
    except Exception as exc:  # noqa: BLE001  # allow-silent: None renders "could not read" and counts as a problem
        logger.warning("boot_reconcile: positions read failed for %s: %s", aid, exc)
        positions = None
    if positions is None:
        return rep
    rep.broker = [(str(p.get("symbol") or "").upper(), _side(p.get("side")))
                  for p in positions if float(p.get("size") or 0) > 0]
    if journal is None:
        return rep
    j_keys = {(str(t.get("symbol") or "").upper(), _side(t.get("direction"))) for t in journal}
    j_syms = {k[0] for k in j_keys}

    def _held(sym: str, side: Optional[str]) -> bool:
        return (sym, side) in j_keys if side else sym in j_syms

    b_keys = set(rep.broker)
    b_syms = {k[0] for k in b_keys}
    for sym, side in rep.broker:
        if _held(sym, side):
            rep.tracked += 1
        else:
            rep.untracked.append(f"{sym} {side or ''}".strip())
    for t in journal:
        sym, side = str(t.get("symbol") or "").upper(), _side(t.get("direction"))
        at_broker = (sym, side) in b_keys if side and any(s for _, s in b_keys) else sym in b_syms
        if not at_broker:
            rep.ghosts.append(f"trade #{t.get('id')} {sym} {side or ''}".strip())
            logger.warning("boot_reconcile: GHOST TRADE account=%s trade_id=%s symbol=%s",
                           aid, t.get("id"), sym)
    if rep.broker:
        try:
            br = _brackets(cfg)
        except Exception as exc:  # noqa: BLE001  # allow-silent: unread brackets count as a problem
            logger.warning("boot_reconcile: bracket read failed for %s: %s", aid, exc)
            br = None
        if br is None:
            rep.bracket_read = False
        else:
            pos_level, orders = br
            for sym, side in rep.broker:
                g = _grade(sym, side, pos_level, orders)
                label = f"{sym} {side or ''}".strip()
                if g is None:
                    rep.missing_bracket.append(f"{label}: SL/TP at broker {COULD_NOT_READ}")
                elif g["stop"] and g["target"]:
                    rep.full_bracket += 1
                else:
                    miss = "+".join(k for k, lbl in (("SL", "stop"), ("TP", "target")) if not g[lbl])
                    rep.missing_bracket.append(f"{label}: no {miss} at broker")
    return rep


def build_restart_report(*, cause=None) -> Dict[str, Any]:
    """Everything the ping says, as data. Never raises."""
    from src.runtime import restart_cause
    from src.utils.paths import trade_journal_db_path

    if cause is None:
        cause = restart_cause.determine()
    db_path = trade_journal_db_path()
    legs = _open_packages_by_account(db_path)
    accounts: List[AccountReport] = []
    cfgs = _load_live_accounts()
    for cfg in cfgs.values():
        try:
            accounts.append(reconcile_account(cfg, db_path, legs))
        except Exception as exc:  # noqa: BLE001  # allow-silent: the account is appended as unread (counts as a problem)
            logger.warning("boot_reconcile: %s failed: %s", cfg.get("account_id"), exc)
            accounts.append(AccountReport(cfg["account_id"], _group(cfg), broker=None,
                                          journal_open=None))
    return {"cause": cause, "accounts": accounts, "legs": legs,
            "accounts_loaded": bool(cfgs)}


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _problem_count(report: Dict[str, Any]) -> int:
    n = sum(a.problems for a in report["accounts"])
    if report["legs"] is None:
        n += 1
    if not report["accounts_loaded"]:
        n += 1
    if report["cause"].is_problem:
        n += 1
    return n


def _totals(accounts: List[AccountReport]) -> Tuple[int, int, int]:
    pos = sum(len(a.broker) for a in accounts if a.broker)
    tracked = sum(a.tracked for a in accounts if a.broker)
    full = sum(a.full_bracket for a in accounts if a.broker)
    return pos, tracked, full


def _account_line(a: AccountReport) -> str:
    if not a.readable:
        return f"{a.account_id}: broker {NO_READ_PATH}; journal open " + (
            COULD_NOT_READ if a.journal_open is None else str(a.journal_open))
    if a.broker is None:
        return f"{a.account_id}: broker positions {COULD_NOT_READ}"
    n = len(a.broker)
    tracked = COULD_NOT_READ if a.journal_open is None else str(a.tracked)
    if n == 0:
        br = "n/a"
    elif not a.bracket_read:
        br = COULD_NOT_READ
    else:
        br = f"{a.full_bracket}/{n}"
    return f"{a.account_id}: {n} broker positions / {tracked} tracked / SL+TP at broker {br}"


def _section_for(group: str, accts: List[AccountReport]):
    from src.units.ui.telegram_format import Section
    body: List[str] = []
    for a in accts:
        body.append(_account_line(a))
        for g in a.ghosts:
            body.append(f"  ❌ ghost: {g} (journal open, broker flat)")
        for u in a.untracked:
            body.append(f"  ❌ untracked: {u} (broker open, no journal row)")
        for m in a.missing_bracket:
            body.append(f"  ❌ {m}")
        if a.legs is None:
            body.append(f"  legs with open packages: {COULD_NOT_READ}")
        else:
            for strat, n in sorted(a.legs.items()):
                body.append(f"  · {strat}: {n} open package(s)")
    probs = sum(a.problems for a in accts)
    readable = [a for a in accts if a.readable]
    pos, tracked, full = _totals(readable)
    unread = sum(1 for a in readable if a.broker is None)
    br_unread = sum(1 for a in readable if a.broker and not a.bracket_read)
    if readable:
        summary = f"{group} — {pos} position(s), {tracked} tracked, SL+TP {full}/{pos}"
        if br_unread:
            summary += f" (brackets unread on {br_unread} account(s))"
        if unread:
            summary += f", {unread} account(s) unread"
        mark = "❌ " if probs else "✅ "
    else:
        summary = f"{group} — {len(accts)} account(s), broker not readable from the trader"
        mark = "❌ " if probs else "➖ "      # not measured: neither ✅ nor ❌
    summary = mark + summary + (f" — {probs} problem(s)" if probs else "")
    prio = _GROUP_ORDER.index(group) if group in _GROUP_ORDER else len(_GROUP_ORDER)
    return Section(summary=summary, body="\n".join(body), priority=prio)


def render_restart_message(report: Dict[str, Any]) -> Tuple[str, Optional[str], bool]:
    """``(text, parse_mode, full)``.

    *full* is False for the compact form: a PLANNED restart (deploy / manual)
    with zero problems — two lines, plain text. Anything else is the full
    sectioned HTML message.
    """
    from src.units.ui.telegram_format import html_escape, render_html_fitted

    cause = report["cause"]
    accounts: List[AccountReport] = report["accounts"]
    problems = _problem_count(report)
    readable = [a for a in accounts if a.readable]
    pos, tracked, full = _totals(readable)
    n_live = len(accounts)
    no_path = [a.account_id for a in accounts if not a.readable]
    tail = f"; {len(no_path)} prop account(s) not broker-readable" if no_path else ""

    if problems == 0:
        head = (f"✅ Trader restarted — all live trades accounted for: {tracked}/{pos} "
                f"broker positions tracked, SL+TP at broker {full}/{pos}, "
                f"{n_live} live account(s){tail}")
    else:
        head = f"❌ Trader restarted — {problems} problem(s) need attention{tail}"

    if problems == 0 and cause.planned:
        return f"{head}\n{cause.line()}", None, False

    by_group: Dict[str, List[AccountReport]] = defaultdict(list)
    for a in accounts:
        by_group[a.group].append(a)
    sections = [_section_for(g, accts) for g, accts in by_group.items()]
    if not report["accounts_loaded"]:
        from src.units.ui.telegram_format import Section
        sections.append(Section(summary="❌ live accounts — " + COULD_NOT_READ,
                                body="config/accounts.yaml could not be loaded", priority=0))
    if report["legs"] is None:
        from src.units.ui.telegram_format import Section
        sections.append(Section(summary="❌ open order packages — " + COULD_NOT_READ,
                                body="the order_packages query failed; see bot.log", priority=0))
    # render_html_fitted bolds + escapes the header; the cause goes in line 2
    text = render_html_fitted(header=head, sections=sections, footer=None)
    first, _, rest = text.partition("\n")
    text = f"{first}\n{html_escape(cause.line())}\n{rest.lstrip()}" if rest else \
        f"{first}\n{html_escape(cause.line())}"
    return text, "HTML", True


def send_restart_report_on_boot() -> Optional[Dict[str, Any]]:
    """Build, log and send the ONE restart ping. Never raises."""
    try:
        report = build_restart_report()
        text, parse_mode, full = render_restart_message(report)
    except Exception as exc:  # noqa: BLE001  # allow-silent: never-raise contract; logged, boot continues
        logger.warning("boot_audit: restart report failed: %s", exc)
        return None
    logger.info("boot_audit: restart report (%s, %s): %s",
                "full" if full else "compact", report["cause"].evidence, text.replace("\n", " | "))
    try:
        from src.runtime import telegram_channels
        telegram_channels.send("restart", text, parse_mode=parse_mode)
    except Exception as exc:  # noqa: BLE001  # allow-silent: the log line above already carries the report
        logger.warning("boot_audit: Telegram restart ping failed: %s", exc)
    report["text"], report["full"] = text, full
    return report


#: how long boot waits for the report before starting the first tick anyway
REPORT_WAIT_S = 45.0


def start_restart_report_thread(wait_s: float = REPORT_WAIT_S) -> Optional[threading.Thread]:
    """Run :func:`send_restart_report_on_boot` on a daemon thread and wait up
    to *wait_s* for it. Normally it finishes first — so the reconcile reads a
    journal and broker the first tick has not touched yet (the old synchronous
    behaviour) — but a slow broker (an IB Gateway connect can take ~20 s) can
    never hold the first tick back longer than *wait_s*. Never raises."""
    try:
        t = threading.Thread(target=send_restart_report_on_boot,
                             name="boot-restart-report", daemon=True)
        t.start()
        t.join(timeout=max(0.0, wait_s))
        if t.is_alive():
            logger.warning("boot_audit: restart report still running after %.0fs — "
                           "starting the first tick; it will send when done", wait_s)
        return t
    except Exception as exc:  # noqa: BLE001  # allow-silent: never-raise contract; logged, boot continues
        logger.warning("boot_audit: could not start restart-report thread: %s", exc)
        return None
