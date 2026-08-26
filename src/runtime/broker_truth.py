"""Broker-truth realized-PnL ledger — the committed source of truth for an
account's **authoritative** lifetime realized PnL when the live per-row journal
can't be trusted for that account.

Why this exists (BL-20260713-BYBIT2-PNL-UNDERRECORD): for a **netting** account
that also mixed spot + perp and switched Bybit sub-accounts mid-history
(``bybit_2``), the journal's per-row ``pnl`` under-records vs the broker's own
wallet truth (reconciler exit=entry false-closes + uncaptured fees + a spot /
sub-account-switch conversion cohort that per-fill FIFO can't attribute — see
``docs/audits/bybit2-broker-reconciliation-2026-07-13.md``). The exchange-fills
store's self-FIFO realized *also* mis-attributes such an account.

The only trustworthy figure for those accounts is the **account-level wallet
delta** (Bybit UM ``Change`` − transfers, which nets fees + funding +
conversions). This module records that authoritative number in a **committed**
JSON ledger (``comms/broker_truth_ledger.json``) so the dashboard can surface it
next to the journal's approximate figure — *without* rewriting any money-DB row
(no fabricated per-row precision).

Same shape/behaviour contract as ``src.runtime.gpu_spend`` (the committed
``gpu_spend_ledger.json`` surfaced at ``/api/bot/gpu/spend``): a **file** (not a
DB table) because it's written by a reviewed reconciliation run + committed;
stdlib-only; **best-effort read** — a missing/garbled ledger degrades to an
empty summary, never raises to the API.

Authoritative-by-record: ``realized_usd`` is whatever the reconciliation run
computed from the operator's UM export (wallet-truth); ``fees_usd`` /
``funding_usd`` are carried for display. It is NOT re-derived here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.utils.paths import repo_root

LEDGER_ENV = "BROKER_TRUTH_LEDGER"


def ledger_path() -> Path:
    """Resolve the ledger file: ``$BROKER_TRUTH_LEDGER`` → ``<repo>/comms/broker_truth_ledger.json``."""
    env = os.environ.get(LEDGER_ENV)
    if env:
        return Path(env)
    return Path(repo_root()) / "comms" / "broker_truth_ledger.json"


def _empty() -> dict[str, Any]:
    return {"schema_version": 1, "accounts": [], "updated_at": None}


def load_ledger(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """The raw ledger dict. Missing/garbled → a default empty ledger (never raises)."""
    p = Path(path) if path is not None else ledger_path()
    if not p.is_file():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        data["accounts"] = []
    data.setdefault("schema_version", 1)
    data.setdefault("updated_at", None)
    return data


def _coerce_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _clean_account(rec: Any) -> dict[str, Any] | None:
    """Coerce one ledger record into the wire shape, or None if it's not usable."""
    if not isinstance(rec, dict):
        return None
    account_id = rec.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        return None
    return {
        "account_id": account_id,
        "realized_usd": _coerce_float(rec.get("realized_usd")),
        "fees_usd": _coerce_float(rec.get("fees_usd")),
        "funding_usd": _coerce_float(rec.get("funding_usd")),
        "as_of": rec.get("as_of"),
        "window_start": rec.get("window_start"),
        "window_end": rec.get("window_end"),
        "source": rec.get("source"),
        "sub_accounts": rec.get("sub_accounts") if isinstance(rec.get("sub_accounts"), list) else None,
        "note": rec.get("note"),
    }


def summarize_broker_truth(
    path: str | os.PathLike[str] | None = None,
    *,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Roll the ledger up for the API / dashboard.

    Returns ``{present, count, account_id, accounts:[...], updated_at}``. When
    ``account_id`` is given, ``accounts`` is filtered to that account (still a
    list; empty when unknown). ``present`` is True when the ledger file parsed to
    at least one usable account record. Best-effort — never raises.
    """
    ledger = load_ledger(path)
    accounts = [a for a in (_clean_account(r) for r in ledger.get("accounts", [])) if a is not None]
    present = bool(accounts)
    if account_id is not None:
        accounts = [a for a in accounts if a["account_id"] == account_id]
    return {
        "present": present,
        "count": len(accounts),
        "account_id": account_id,
        "accounts": accounts,
        "updated_at": ledger.get("updated_at"),
    }


#: The three states a journal-trust verdict can take. NEVER collapsed to a
#: boolean: "we have no ledger record for this account" and "this account's
#: journal agrees with the broker" are opposite claims, and only one of them is
#: evidence of anything.
TRUST_KNOWN_DIVERGENT = "known_divergent"
TRUST_NO_RECORD = "no_record"
TRUST_UNREADABLE = "unreadable"


def _ledger_read_state(path: str | os.PathLike[str] | None = None) -> str:
    """Did we actually obtain the ledger's contents? ``read``/``absent``/``unreadable``.

    ⚠️ This asks the FILE directly rather than reading it off
    :func:`summarize_broker_truth`, and that is the whole point.
    :func:`load_ledger` funnels a missing file, an unparseable file and a file
    listing nothing into one identical empty envelope (``present: False``), so
    a verdict derived from that envelope CANNOT distinguish "we could not look"
    from "we looked and the ledger is empty". Measured while writing the tests
    for this module: a deliberately corrupted ledger graded ``read`` with an
    empty account map, i.e. every account came back "unrecorded" — the exact
    collapse the three verdict states exist to prevent, one layer down.

    ``load_ledger``'s own contract is deliberately NOT changed: it is
    best-effort by design and has a live consumer
    (``/api/bot/pnl/broker-truth``) that wants the degraded envelope.
    """
    p = Path(path) if path is not None else ledger_path()
    try:
        if not p.is_file():
            return "absent"
        json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return "unreadable"
    except Exception:  # noqa: BLE001  # allow-silent: never break a trade read over a ledger
        return "unreadable"
    return "read"


def journal_trust_map(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Read the ledger ONCE and return every account known not to reconcile.

    ``{"read_state": "read" | "absent" | "unreadable",
       "accounts": {<id>: <record>}}``.

    Separate from :func:`journal_trust` because the natural per-row call site
    is a loop over a trade page: resolving the verdict per row would re-read
    the ledger file once per trade. Resolve the map once per request, then
    grade rows against it with :func:`journal_trust_for`.

    ``read_state`` is NOT collapsed into an empty ``accounts``. "the ledger
    listed nothing", "the ledger is not there" and "the ledger would not parse"
    are three different facts, and an empty map from a failed read would
    silently grade every account as merely unrecorded — which reads, to anyone
    skimming, as *fine*. ``absent`` on the live VM means the committed ledger
    did not reach the deploy, which is a deploy failure, not a data state.
    """
    state = _ledger_read_state(path)
    if state != "read":
        return {"read_state": state, "accounts": {}}
    try:
        accounts = summarize_broker_truth(path).get("accounts") or []
    except Exception:  # noqa: BLE001  # allow-silent: a ledger read failure must never break a trade read
        return {"read_state": "unreadable", "accounts": {}}
    out: dict[str, Any] = {}
    for rec in accounts:
        aid = rec.get("account_id")
        if aid:
            out[str(aid)] = rec
    return {"read_state": state, "accounts": out}


_NO_RECORD_NOTE = (
    "no broker-truth record for this account. NOT a clean bill of health — the "
    "ledger is populated by hand from an operator's venue export, so this means "
    "nobody has reconciled this account, never that it reconciles"
)
_DIVERGENT_NOTE = (
    "this account has a recorded broker wallet-truth figure BECAUSE its per-row "
    "journal pnl is known not to reconcile. Do not quote a sum over these rows "
    "as the account's result — read /api/bot/pnl/broker-truth beside it"
)
_UNREADABLE_NOTE = (
    "the broker-truth ledger could not be read — WE DID NOT LOOK, which is not "
    "the same as finding no record"
)


def journal_trust_for(
    account_id: str | None,
    trust_map: dict[str, Any],
) -> dict[str, Any]:
    """Grade ONE account against a map from :func:`journal_trust_map`."""
    # BOTH `absent` and `unreadable` mean we never obtained a verdict for this
    # account. `read_state` on the map keeps WHY they differ; the per-account
    # verdict does not need to, and must not report either as `no_record`.
    if trust_map.get("read_state") not in (None, "read"):
        return {"state": TRUST_UNREADABLE, "account_id": account_id,
                "realized_usd": None, "as_of": None, "source": None,
                "note": _UNREADABLE_NOTE}
    rec = (trust_map.get("accounts") or {}).get(str(account_id)) if account_id else None
    if rec is None:
        return {"state": TRUST_NO_RECORD, "account_id": account_id,
                "realized_usd": None, "as_of": None, "source": None,
                "note": _NO_RECORD_NOTE if account_id
                        else "no account id on the row; nothing to look up"}
    return {"state": TRUST_KNOWN_DIVERGENT, "account_id": account_id,
            "realized_usd": rec.get("realized_usd"),
            "as_of": rec.get("as_of"),
            "source": rec.get("source"),
            "note": _DIVERGENT_NOTE}


def journal_trust(
    account_id: str | None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Is this account's per-row journal ``pnl`` known to disagree with the broker?

    WHY THIS EXISTS. ``comms/broker_truth_ledger.json`` has recorded since
    2026-07-13 that ``bybit_2``'s journal UNDER-RECORDS — wallet-truth
    −$262.52 against a per-row journal sum of roughly −$33, an ~8× gap — and
    the ONLY consumer was its own read-only route. Nothing on the journal read
    path consulted it, so a reader querying that account's closed trades got a
    confident number and no warning. On 2026-08-26 a session duly reported that
    account as "+$0.88, flat, no problem" and the operator had to correct it
    from the venue UI. A ledger that records which accounts cannot be trusted,
    and is not consulted where they are read, is a fact nobody receives.

    ⚠️ ``no_record`` IS NOT A CLEAN BILL OF HEALTH. The ledger is populated by
    hand from an operator's venue export, so an absent record means only that
    nobody has reconciled this account — never that it reconciles. Rendering
    ``no_record`` as "trusted" is the exact collapse this three-state return
    exists to prevent.

    Returns ``{state, account_id, realized_usd, as_of, source, note}``.
    ``realized_usd``/``as_of``/``source`` are populated only for
    ``known_divergent``. Best-effort — never raises. For a loop over many rows
    use :func:`journal_trust_map` + :func:`journal_trust_for` instead, which
    read the ledger once.
    """
    return journal_trust_for(account_id, journal_trust_map(path))


def upsert_account_truth(
    record: dict[str, Any],
    path: str | os.PathLike[str] | None = None,
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Insert or replace one account's truth record (keyed by ``account_id``) and
    write the ledger back. Returns the written ledger dict.

    Used by the reconciliation run (``scripts/ops/reconcile_netting_pnl.py
    --emit-ledger``) to record the authoritative figure after an operator has
    reviewed the dry-run. ``updated_at`` is passed in (the module never reads a
    wall clock — keeps it deterministic/testable).
    """
    account_id = record.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        raise ValueError("record must carry a non-empty 'account_id'")
    p = Path(path) if path is not None else ledger_path()
    ledger = load_ledger(p)
    others = [r for r in ledger.get("accounts", []) if not (isinstance(r, dict) and r.get("account_id") == account_id)]
    others.append(record)
    ledger["accounts"] = others
    if updated_at is not None:
        ledger["updated_at"] = updated_at
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return ledger
