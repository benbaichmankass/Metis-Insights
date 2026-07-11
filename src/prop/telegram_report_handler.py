"""Inbound prop report handler — turn an operator's Telegram line into an ingest.

The parser half lives in :mod:`src.prop.telegram_commands`; this module is the
**enrich + ingest** half. It's transport-agnostic: it takes the raw text the
operator typed and returns a reply string (or ``None`` for a non-command line),
calling :func:`src.prop.prop_report.ingest_report` on the way.

The transport is the existing **Claude/prop comms bot** (``src/bot/claude_bridge.py``,
already long-polling ``TELEGRAM_CLAUDE_BOT_TOKEN`` — the channel prop tickets are
emitted to). Folding the handler into that bot's message handler means the
report-back loop closes with **no Claude/dashboard middle-man, no new bot token,
and no new service** — the operator just replies to a prop ticket in the same
channel (``close ETHUSD 2950 +80 tp``) and the trade updates.

Authorisation is the bridge's job (it already restricts to the operator's
``TELEGRAM_CHAT_ID``); this module assumes the caller has authorised the chat.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.prop import prop_journal
from src.prop.telegram_commands import USAGE, build_report, parse_prop_command

logger = logging.getLogger(__name__)

# When a trade is reported, remind the operator to send a fresh balance so the
# rule-distance guard isn't blind — but only if the last account-status snapshot
# is missing or older than this (reuse the periodic status-request threshold so
# the two paths agree; `PROP_STATUS_REQUEST_MAX_AGE_HOURS <= 0` disables both).
_STATUS_NUDGE_DEFAULT_MAX_AGE_H = 24.0
# Fill actions that leave/alter a live position — worth a balance nudge. A
# `skip` opens nothing, so it never nudges.
_NUDGE_ACTIONS = {"open", "filled", "placed", "close", "closed"}


def default_prop_account() -> Optional[str]:
    """The account a bare command targets when no ``acct=`` override is given.

    ``PROP_DEFAULT_ACCOUNT`` wins; otherwise resolve the single prop account
    from ``accounts.yaml`` (when exactly one exists — the common case today). If
    several prop accounts exist and none is pinned, returns ``None`` so the
    handler asks the operator to disambiguate rather than guess.
    """
    pinned = os.environ.get("PROP_DEFAULT_ACCOUNT")
    if pinned:
        return pinned
    try:
        from src.config.accounts_loader import load_accounts_dict

        accts = load_accounts_dict() or {}
    except Exception as exc:  # noqa: BLE001 — no config → no default
        logger.warning("telegram_report_handler: accounts load failed: %s", exc)
        return None
    from src.prop.prop_identity import is_prop_account
    prop_ids = [
        aid for aid, a in accts.items()
        if isinstance(a, dict) and is_prop_account(a)
    ]
    return prop_ids[0] if len(prop_ids) == 1 else None


def resolve_open_ticket(account_id: str, canonical_symbol: str) -> Tuple[
        Optional[str], Optional[str]]:
    """Newest still-open ticket for ``(account, symbol)`` → ``(direction, ticket_id)``.

    Lets a bare ``close ETHUSD ...`` inherit the direction + ticket id of the
    ticket it's reporting against, so the journal links exactly. Best-effort —
    ``(None, None)`` when nothing matches (``ingest_report`` then re-matches by
    symbol alone).
    """
    try:
        tickets = prop_journal.list_tickets(account_id=account_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram_report_handler: ticket lookup failed: %s", exc)
        return None, None
    sym = str(canonical_symbol).upper()
    for t in tickets:  # list_tickets is newest-first
        if str(t.get("symbol", "")).upper() != sym:
            continue
        if t.get("status") not in ("emitted", "placed", "filled"):
            continue
        return t.get("direction"), t.get("ticket_id")
    return None, None


def _strip_code_fence(text: str) -> str:
    """Drop a surrounding ```/```json fence if the operator pasted one."""
    s = text.strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _looks_like_json_report(text: str) -> bool:
    s = _strip_code_fence(text)
    return s.startswith("{") or '"account_id"' in s


def handle_json_report(text: str, *, default_account: Optional[str] = None
                       ) -> Optional[str]:
    """Ingest a pasted JSON report-back (the shape the rendered TICKET tells the
    executor to reply with: ``{"account_id":…,"symbol":…,"status":"open",…}``).

    Returns a one-line ack on success, a hint string on malformed/invalid JSON,
    or ``None`` when the text doesn't look like a JSON report at all (caller then
    tries the structured command grammar). This closes the gap where the ticket
    instructs a JSON reply but the bot only understood ``open ETHUSD …`` lines —
    both report-back formats now work in the prop channel.
    """
    if not _looks_like_json_report(text):
        return None
    raw = _strip_code_fence(text)
    try:
        report: Any = json.loads(raw)
    except (ValueError, TypeError):
        return ("⚠ That looked like a JSON report but I couldn't parse it. Paste "
                "the exact block from the ticket, or use a line like "
                "`open ETHUSD 1620 0.73`.")
    if not isinstance(report, dict):
        return "⚠ JSON report must be an object like the ticket's report-back block."

    report = dict(report)  # don't mutate the caller's parse
    if not (report.get("account_id") or report.get("account")) and default_account:
        report["account_id"] = default_account

    try:
        from src.prop.prop_report import ingest_report

        out = ingest_report(report)
    except ValueError as exc:
        return f"⚠ rejected: {exc}"
    except Exception as exc:  # noqa: BLE001 — never crash the caller on one message
        logger.exception("telegram_report_handler: json ingest failed")
        return f"⚠ error: {exc}"
    return _confirm_json(report, out)


def _status_age_hours(account_id: str) -> Optional[float]:
    """Age (hours) of the newest ``prop_account_status`` row, or ``None``."""
    try:
        row = prop_journal.latest_account_status(account_id)
    except Exception as exc:  # noqa: BLE001 — a read failure must not raise
        logger.warning("telegram_report_handler: status age read failed: %s", exc)
        return None
    if not row:
        return None
    ts = row.get("reported_at") or row.get("created_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)


def account_status_nudge(account_id: Optional[str]) -> Optional[str]:
    """A one-shot 'send your balance' reminder, or ``None`` when a fresh snapshot
    already exists (so a report right after a balance update doesn't nag).

    Folds the account-status ask into the trade-report flow (operator ask,
    2026-07-11): logging a trade now prompts for the balance in the same reply
    instead of relying only on the separate periodic ``prop_status_request``
    ping — same freshness threshold, so the two never double up.
    """
    if not account_id:
        return None
    raw = os.environ.get("PROP_STATUS_REQUEST_MAX_AGE_HOURS")
    try:
        max_age = float(raw) if raw not in (None, "") else _STATUS_NUDGE_DEFAULT_MAX_AGE_H
    except (TypeError, ValueError):
        max_age = _STATUS_NUDGE_DEFAULT_MAX_AGE_H
    if max_age <= 0:  # feature paused
        return None
    age = _status_age_hours(account_id)
    if age is not None and age < max_age:
        return None  # snapshot fresh enough — don't nag
    stale = "no balance on file yet" if age is None else f"last balance {age:.0f}h old"
    return (
        f"📋 Also send the account balance so the rule-distance guard "
        f"(daily-loss / DD-floor cushion) is armed — {stale}:\n"
        "• bal <balance> <equity> [realized_today]   e.g. `bal 5040 5010`\n"
        "• or send a screenshot of the account screen"
    )


def _with_status_nudge(ack: str, account_id: Optional[str], action: Optional[str]) -> str:
    """Append the balance nudge to a fill ack when the guard is stale/blind."""
    if not action or str(action).lower() not in _NUDGE_ACTIONS:
        return ack
    nudge = account_status_nudge(account_id)
    return f"{ack}\n\n{nudge}" if nudge else ack


def _confirm_json(report: Dict[str, Any], out: Dict[str, Any],
                  *, nudge: bool = True) -> str:
    """Human one-line ack for a JSON report-back ingest."""
    kind = out.get("kind")
    if kind == "account_status":
        rd = out.get("rule_distance") or {}
        return (f"✅ account status recorded [{report.get('account_id')}] · "
                f"to daily-loss ${rd.get('distance_to_daily_loss_usd')} · "
                f"to DD-floor ${rd.get('distance_to_dd_floor_usd')}")
    sym = report.get("symbol")
    status = str(report.get("status") or out.get("status") or "").upper()
    tid = out.get("ticket_id")
    tail = f" · ticket {tid}" if tid else ""
    if status == "CLOSED":
        ack = (f"✅ recorded CLOSE {sym} @ {report.get('exit_price')} "
               f"pnl {report.get('pnl', '—')} ({report.get('reason', '—')}){tail}")
    elif status == "SKIPPED":
        ack = f"✅ recorded SKIP {sym} ({report.get('reason', '—')}){tail}"
    else:
        ack = (f"✅ recorded {status or 'OPEN'} {sym} @ {report.get('entry_price')} "
               f"qty {report.get('qty', '—')}{tail}")
    if nudge:
        return _with_status_nudge(ack, report.get("account_id"), status)
    return ack


def handle_command(text: str, *, default_account: Optional[str] = None) -> Optional[str]:
    """Parse → enrich → ingest one message. Returns the reply text, or ``None``.

    ``None`` ⇒ the line was not a recognised prop command (caller stays silent /
    falls through to its own handling). A malformed command returns a usage hint
    string; a successful ingest returns a one-line confirmation.

    A pasted JSON report-back (what the rendered ticket instructs) is handled
    first via :func:`handle_json_report`; everything else falls through to the
    structured command grammar (``close ETHUSD 2950 +80 tp``).
    """
    json_reply = handle_json_report(text, default_account=default_account)
    if json_reply is not None:
        return json_reply

    try:
        intent = parse_prop_command(text)
    except ValueError as exc:
        return f"⚠ {exc}\n\n{USAGE}"
    if intent is None:
        return None

    account_id = intent.get("account_id") or default_account
    if not account_id:
        return ("⚠ No prop account resolved — set PROP_DEFAULT_ACCOUNT or add "
                "`acct=<id>` to the command.")

    direction = ticket_id = None
    if intent.get("_action") in ("close", "open", "skip"):
        try:
            from src.prop.symbol_map import to_bot_symbol

            canonical = to_bot_symbol(intent.get("symbol")) or intent.get("symbol")
        except Exception:  # noqa: BLE001 — fall back to the typed symbol
            canonical = intent.get("symbol")
        direction, ticket_id = resolve_open_ticket(account_id, canonical)

    report = build_report(
        intent, account_id=account_id, direction=direction, ticket_id=ticket_id)

    try:
        from src.prop.prop_report import ingest_report

        out = ingest_report(report)
    except ValueError as exc:
        return f"⚠ rejected: {exc}"
    except Exception as exc:  # noqa: BLE001 — never crash the caller on one message
        logger.exception("telegram_report_handler: ingest failed")
        return f"⚠ error: {exc}"

    return _confirm(intent, report, out)


def _confirm(intent: dict, report: dict, out: dict) -> str:
    """Human one-line ack of a successful ingest."""
    if intent.get("_action") == "status":
        rd = out.get("rule_distance") or {}
        dl = rd.get("distance_to_daily_loss_usd")
        dd = rd.get("distance_to_dd_floor_usd")
        return (f"✅ account status recorded [{report['account_id']}] · "
                f"to daily-loss ${dl} · to DD-floor ${dd}")
    sym = report.get("symbol")
    act = intent.get("_action")
    tid = out.get("ticket_id")
    tail = f" · ticket {tid}" if tid else ""
    if act == "close":
        ack = (f"✅ recorded CLOSE {sym} @ {report.get('exit_price')} "
               f"pnl {report.get('pnl', '—')} ({report.get('reason')}){tail}")
    elif act in ("open", "placed"):
        verb = "PLACED" if act == "placed" else "OPEN"
        ack = (f"✅ recorded {verb} {sym} @ {report.get('entry_price')} "
               f"qty {report.get('qty', '—')}{tail}")
    else:
        return f"✅ recorded SKIP {sym} ({report.get('reason')}){tail}"
    return _with_status_nudge(ack, report.get("account_id"), act)


def handle_screenshot(image_bytes: bytes, media_type: str = "image/png",
                      *, default_account: Optional[str] = None) -> str:
    """Vision-parse a terminal screenshot into report(s), ingest each, ack.

    The image path of the manual bridge (operator ask, 2026-07-11): the operator
    sends a photo of the Breakout/DXtrade terminal and the bot extracts the same
    structured report(s) the text grammar would, routing them through the one
    ``prop_report.ingest_report`` chokepoint. A single screen may yield a fill
    AND an account_status (a portfolio screen showing both) — account-status
    reports are ingested FIRST so a balance in the same shot suppresses the
    trade ack's stale-balance nudge. Always returns an operator-readable string.
    """
    try:
        from src.prop.screenshot_parse import ScreenshotParseError, parse_screenshot
    except ImportError:  # pragma: no cover - module always present in-repo
        return "⚠ screenshot reading is unavailable — type the report instead."

    try:
        reports = parse_screenshot(
            image_bytes, media_type, default_account=default_account)
    except ScreenshotParseError as exc:
        return f"⚠ {exc}"
    except Exception as exc:  # noqa: BLE001 — never crash the caller
        logger.exception("telegram_report_handler: screenshot parse failed")
        return f"⚠ couldn't read that screenshot: {exc}"

    if not reports:
        return ("⚠ I couldn't find a trade or balance in that screenshot. Send the "
                "Position or account screen, or type it — e.g. "
                "`close ETHUSD 2950 +80 tp` / `bal 5040 5010`.")

    from src.prop.prop_report import ingest_report

    # Account-status first so a same-shot balance is on file before the fill ack
    # computes its stale-balance nudge (else it would nudge for a balance we just
    # recorded from the same image).
    reports = sorted(
        reports, key=lambda r: 0 if str(r.get("kind") or "") == "account_status" else 1)

    acks: List[str] = []
    saw_fill = False
    nudge_account: Optional[str] = None
    for report in reports:
        if not (report.get("account_id") or report.get("account")) and default_account:
            report["account_id"] = default_account
        try:
            out = ingest_report(report)
        except ValueError as exc:
            acks.append(f"⚠ rejected: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("telegram_report_handler: screenshot ingest failed")
            acks.append(f"⚠ error: {exc}")
            continue
        acks.append(_confirm_json(report, out, nudge=False))
        if out.get("kind") != "account_status":
            saw_fill = True
            nudge_account = nudge_account or report.get("account_id") or report.get("account")

    body = "📸 " + "\n".join(acks)
    if saw_fill:
        nudge = account_status_nudge(nudge_account)
        if nudge:
            body = f"{body}\n\n{nudge}"
    return body


__all__ = ["handle_command", "handle_json_report", "handle_screenshot",
           "account_status_nudge", "default_prop_account", "resolve_open_ticket"]
