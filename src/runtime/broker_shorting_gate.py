"""Per-ACCOUNT short gate from the BROKER'S OWN ``shorting_enabled`` flag.

Why this exists
---------------
``BL-20260823-ALPACA-SHORTING-FLAG-READ-NEVER-CONSUMED``; operator, 2026-09-28:
*"Build the gate now — Refuse short signals, with a logged reason, on any
account whose broker reports shorting disabled."*

``AlpacaClient.account_status()`` has returned ``shorting_enabled`` since
2026-07-01 and nothing on the order path ever read it. The only thing keeping
shorts off ``alpaca_live`` was the CONFIG policy ``accounts.yaml::side_filter:
long`` (:mod:`src.runtime.account_side_filter`). That covers exactly the
accounts someone remembered to declare it on — ``alpaca_live`` today — and is a
policy statement, not a reading of what the venue will accept. With
``MD-PROMOTE-S1-S2`` armed (2026-09-28) a leg can reach an account without a
human in the path, so the venue's own answer has to be consulted too.

How it is enforced
------------------
In ``Coordinator.multi_account_execute``, immediately after the ``side_filter``
fold and only on a not-already-dry account: a refused short writes a journal
rejection row with ``reason=`` :data:`REFUSAL_TOKEN` (so the cause survives in
the DB, not only in a ~30-minute journald line) and the account is skipped for
that package. NO order path is added. A short on an account whose journal shows
a LONG (or is unreadable) is NOT refused: under FLIP_POLICY=flat that short is a
close, and a close must never be blocked by a short gate. An account with an
options ``express_as`` (``alpaca_options_paper``) is skipped: it expresses a
bearish signal as a bear put DEBIT spread, which buys premium and is not a
short sale.

Four states, never collapsed
----------------------------
``enabled`` · ``disabled`` · ``unknown`` (we could not look: no client, no
creds, read failed, field absent) · ``not_applicable`` (exchange has no such
flag). Only ``disabled`` refuses. ``unknown`` places and WARNS: this mirrors
``side_filter``'s fail-permissive discipline, and a refused order on absence of
evidence is a gate acting on nothing. The venue itself still rejects an
unexecutable short; this gate's job is to stop us SENDING one it told us about.

The read is cached per account for :data:`CACHE_TTL_S` and only made for a
SHORT package on a non-dry alpaca account, so a long never costs a call.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SHORTING_STATES = ("enabled", "disabled", "unknown", "not_applicable")

#: Exchanges whose account status carries ``shorting_enabled``.
_FLAGGED_EXCHANGES = ("alpaca",)

#: How long a real reading is trusted. A broker flipping ``shorting_enabled``
#: takes effect here within this window; ``unknown`` is never cached.
CACHE_TTL_S = 600.0

#: The journal ``reason`` / result ``error`` for a refusal.
REFUSAL_TOKEN = "broker_shorting_disabled"

_cache: Dict[str, Tuple[float, str]] = {}


def _clear_cache() -> None:
    _cache.clear()


def shorting_state(
    account_id: str,
    exchange: Optional[str],
    client_factory: Callable[[], Any],
    *,
    now: Optional[float] = None,
) -> str:
    """Resolve *account_id*'s broker-reported shorting permission."""
    if str(exchange or "").strip().lower() not in _FLAGGED_EXCHANGES:
        return "not_applicable"
    t = time.monotonic() if now is None else now
    hit = _cache.get(account_id)
    if hit is not None and t - hit[0] < CACHE_TTL_S:
        return hit[1]
    state = "unknown"
    try:
        client = client_factory()
        status = client.account_status() if client is not None else None
        raw = (status or {}).get("shorting_enabled")
        if raw is True:
            state = "enabled"
        elif raw is False:
            state = "disabled"
    except Exception as exc:  # noqa: BLE001 — a read failure is 'unknown'
        logger.warning("broker_shorting_gate: status read failed for %s: %s",
                       account_id, exc)
    # Only cache a real reading; an 'unknown' is re-tried next time.
    if state != "unknown":
        _cache[account_id] = (t, state)
    return state


def refuses_short(
    account_id: str,
    exchange: Optional[str],
    direction: Optional[str],
    client_factory: Callable[[], Any],
) -> Tuple[bool, str]:
    """``(refused, state)`` for *direction* on *account_id*.

    Only a SHORT on an account whose broker reads ``shorting_enabled: false``
    is refused. A long, or an unread direction, never triggers a broker read.
    """
    norm = direction.strip().lower() if isinstance(direction, str) else None
    if norm != "short":
        return False, "not_applicable"
    state = shorting_state(account_id, exchange, client_factory)
    if state == "unknown":
        logger.warning(
            "broker_shorting_gate: could not read shorting_enabled for %s — "
            "short NOT refused by this gate (fail-permissive)", account_id)
    return state == "disabled", state


def refusal_reason(account_id: str) -> str:
    return (f"{REFUSAL_TOKEN}: {account_id}'s broker reports "
            f"shorting_enabled=false — short refused, journalled, not sent")
