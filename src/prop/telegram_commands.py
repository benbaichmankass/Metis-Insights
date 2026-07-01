"""Structured-command parser for the inbound prop Telegram bridge.

The prop account is a manual bridge: the bot emits a paste-ready ticket, a human
places it on the Breakout terminal, then reports back. Until now that report-back
went through Claude (or the dashboard form) as a middle-man. This module is the
parser half of a **direct** path: the operator types a short structured command
in the prop bot's Telegram channel and the listener
(the prop comms bot, via ``src.prop.telegram_report_handler``) turns it into a
``src.prop.prop_report.ingest_report`` call — no middle-man.

The grammar is deliberately small, positional, and forgiving (a fixed verb +
the symbol + numbers in a fixed order + a free-text reason tail), so it's
deterministic and costs nothing to parse (no LLM). Symbols may be typed in the
venue form (``ETHUSD``) or the canonical form (``ETHUSDT``) — both resolve, via
``src.prop.symbol_map`` downstream in ``ingest_report``.

    close  <symbol> <exit> [pnl] [reason]     e.g.  close ETHUSD 2950 +80 tp
    open   <symbol> <entry> [qty]             e.g.  open ETHUSD 3000 0.5
    skip   <symbol> [reason]                  e.g.  skip ETHUSD stale
    bal    <balance> [equity] [realized]      e.g.  bal 5040 5010

Two split responsibilities:

- :func:`parse_prop_command` — **pure**, no I/O. Returns a normalised *intent*
  (or ``None`` for a non-command line); raises :class:`ValueError` with a usage
  hint for a recognised verb with bad arguments.
- :func:`build_report` — **pure**, turns an intent + the resolved
  ``account_id`` / ``direction`` / ``ticket_id`` (the listener looks those up in
  the prop journal) into the exact dict ``ingest_report`` accepts.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Verb → canonical action. Aliases keep the operator from memorising one exact
# word under time pressure at the terminal.
_CLOSE = {"close", "closed", "c", "exit"}
# `open`/`filled` = the position is actually LIVE (market fill, or a limit that
# tripped). `placed` (below) is the distinct working-order state — deliberately
# NOT an alias of open, so a placed-but-unfilled limit order isn't logged as a
# live position (the conflation the `placed` state fixes).
_OPEN = {"open", "opened", "filled", "fill", "o"}
_PLACED = {"placed", "place", "working", "pending", "p"}
_SKIP = {"skip", "skipped", "cancel", "x"}
_STATUS = {"bal", "balance", "status", "equity", "acct", "account"}

USAGE = (
    "Prop commands (symbol may be venue ETHUSD or bot ETHUSDT):\n"
    "  placed <symbol> <entry> [qty]          e.g. placed ETHUSD 3000 0.5  "
    "(limit order placed, NOT filled yet)\n"
    "  open  <symbol> <entry> [qty]           e.g. open ETHUSD 3000 0.5    "
    "(position now LIVE — filled)\n"
    "  close <symbol> <exit> [pnl] [reason]   e.g. close ETHUSD 2950 +80 tp\n"
    "  skip  <symbol> [reason]                e.g. skip ETHUSD stale\n"
    "  bal   <balance> [equity] [realized]    e.g. bal 5040 5010\n"
    "  add 'acct=<id>' anywhere to target a specific account."
)

# A copy-paste prompt the operator hands to the supervised executor assistant
# (browser-Claude / Comet placing the Breakout trade). It pins the assistant's
# output to the EXACT one-line grammar above, so the operator can paste the
# reply straight back into the prop channel and the bot logs the fill — no
# reformatting, no middle-man. Kept in lock-step with the verbs/grammar here
# (guarded by tests/test_prop_telegram_commands.py).
REPORT_PROMPT = (
    "📋 PROP REPORT PROMPT — copy everything below and give it to your "
    "executor assistant. Paste its reply back here verbatim to log the trade.\n"
    "────────────────────\n"
    "After you act on the Breakout trade, reply with EXACTLY ONE line and "
    "nothing else — no extra words, no code fences — in one of these formats:\n"
    "\n"
    "  placed <SYMBOL> <entry_price> <qty>\n"
    "  open   <SYMBOL> <entry_price> <qty>\n"
    "  close  <SYMBOL> <exit_price> <pnl> <tp|sl|manual>\n"
    "  skip   <SYMBOL> <reason>\n"
    "  bal    <balance> [equity] [realized_today]\n"
    "\n"
    "Rules:\n"
    "• <SYMBOL> = the venue symbol you actually traded (e.g. ETHUSD, SOLUSD) — "
    "drop the perp 'T' suffix.\n"
    "• Prices, qty and balances are plain numbers; prefix P&L with + or - "
    "(e.g. +80, -30).\n"
    "• Use 'placed' when you place a LIMIT / pending order that has NOT filled "
    "yet (no position, no P&L). Use 'open' once it's actually FILLED and the "
    "position is live (a market order fills immediately → use 'open'). Use "
    "'close' when it exits, 'skip' if you did NOT place it (stale / out of "
    "band).\n"
    "• When a 'placed' order later fills, send the matching 'open' line so it "
    "becomes a live trade.\n"
    "• ONE line only — I will paste it back exactly as you write it.\n"
    "\n"
    "Examples:\n"
    "  placed ETHUSD 3000 0.5\n"
    "  open ETHUSD 3000 0.5\n"
    "  close ETHUSD 2950 +80 tp\n"
    "  skip ETHUSD stale/out-of-range\n"
    "  bal 5040 5010"
)


def _num(tok: str) -> Optional[float]:
    """Parse a numeric token, tolerating ``+``/``$``/``,``/trailing ``%``.

    Returns ``None`` when the token is not a number (so it falls through to the
    free-text reason tail).
    """
    t = tok.strip().lstrip("$").replace(",", "").rstrip("%")
    if t in ("", "+", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _account_override(tokens: list) -> Optional[str]:
    """Pull an ``acct=<id>`` / ``@<id>`` override out of the token list (mutates)."""
    for tok in list(tokens):
        low = tok.lower()
        if low.startswith("acct=") or low.startswith("account="):
            tokens.remove(tok)
            return tok.split("=", 1)[1] or None
        if tok.startswith("@") and len(tok) > 1:
            tokens.remove(tok)
            return tok[1:]
    return None


def parse_prop_command(text: str) -> Optional[Dict[str, Any]]:
    """Parse one Telegram line into a normalised intent, or ``None``.

    ``None`` ⇒ the line is not a recognised prop command (the listener ignores
    it). A recognised verb with missing required arguments raises
    :class:`ValueError` carrying a usage hint.
    """
    if not text or not text.strip():
        return None
    tokens = text.strip().split()
    verb = tokens[0].lstrip("/").lower()
    rest = tokens[1:]

    if verb in _CLOSE:
        action, status = "close", "closed"
    elif verb in _OPEN:
        action, status = "open", "open"
    elif verb in _PLACED:
        action, status = "placed", "placed"
    elif verb in _SKIP:
        action, status = "skip", "skipped"
    elif verb in _STATUS:
        action, status = "status", None
    else:
        return None  # not a command — ignore silently

    account_id = _account_override(rest)

    if action == "status":
        nums = [n for n in (_num(t) for t in rest) if n is not None]
        if not nums:
            raise ValueError("balance is required, e.g. `bal 5040 5010`")
        intent: Dict[str, Any] = {
            "_action": "status",
            "kind": "account_status",
            "balance": nums[0],
            "equity": nums[1] if len(nums) > 1 else None,
            "realized_today": nums[2] if len(nums) > 2 else None,
        }
        if account_id:
            intent["account_id"] = account_id
        return intent

    # close / open / skip — first non-numeric token is the symbol; numeric
    # tokens fill the action's positional slots; the rest is the reason tail.
    symbol: Optional[str] = None
    nums: list = []
    reason_parts: list = []
    for tok in rest:
        if symbol is None and _num(tok) is None:
            symbol = tok
            continue
        n = _num(tok)
        if n is not None:
            nums.append(n)
        else:
            reason_parts.append(tok)
    if not symbol:
        raise ValueError(f"{action} needs a symbol, e.g. `{action} ETHUSD ...`")
    reason = " ".join(reason_parts).strip() or None

    intent = {"_action": action, "status": status, "symbol": symbol}
    if account_id:
        intent["account_id"] = account_id

    if action == "close":
        if not nums:
            raise ValueError("close needs an exit price, e.g. `close ETHUSD 2950 +80 tp`")
        intent["exit_price"] = nums[0]
        if len(nums) > 1:
            intent["pnl"] = nums[1]
        intent["reason"] = reason or "manual"
    elif action in ("open", "placed"):
        # `placed` = a limit/pending order placed but not yet filled; `open` =
        # the position is actually live. Same positional grammar (entry + qty);
        # only the reported status differs (handled above).
        if not nums:
            raise ValueError(
                f"{action} needs an entry price, e.g. `{action} ETHUSD 3000 0.5`")
        intent["entry_price"] = nums[0]
        if len(nums) > 1:
            intent["qty"] = nums[1]
    else:  # skip
        intent["reason"] = reason or "stale/out-of-range"

    return intent


def build_report(
    intent: Dict[str, Any],
    *,
    account_id: str,
    direction: Optional[str] = None,
    ticket_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Turn a parsed intent + resolved context into an ``ingest_report`` dict.

    ``account_id`` is the resolved account (the intent's ``acct=`` override wins
    over the caller's default — the listener passes the already-resolved value).
    ``direction`` / ``ticket_id`` come from the open ticket the listener looked
    up (best-effort — ``ingest_report`` re-matches by symbol when they're absent).
    The symbol is passed through verbatim; ``ingest_report`` canonicalises a
    venue symbol (ETHUSD→ETHUSDT) on the way in.
    """
    report: Dict[str, Any] = {"account_id": account_id}
    if intent.get("_action") == "status":
        report.update({
            "kind": "account_status",
            "balance": intent.get("balance"),
            "equity": intent.get("equity"),
            "realized_today": intent.get("realized_today"),
        })
        return report

    report["symbol"] = intent.get("symbol")
    report["status"] = intent.get("status")
    if direction:
        report["direction"] = direction
    if ticket_id:
        report["ticket_id"] = ticket_id
    for key in ("entry_price", "exit_price", "qty", "pnl", "reason"):
        if key in intent and intent[key] is not None:
            report[key] = intent[key]
    return report


__all__ = ["parse_prop_command", "build_report", "USAGE", "REPORT_PROMPT"]
