"""Observe-only soak log for per-account GROSS EXPOSURE.

Mirrors the canonical soak family (``pairs_soak.py`` / ``allocator_soak.py`` /
``exit_ladder_soak.py``): a pure builder, a best-effort JSONL writer under
``runtime_logs_dir()``, and a pure reader envelope.

**Why this exists.** `docs/design/gross-exposure-governance-DESIGN.md` § 7 lists
*"any value shipped without § 4's observation soak behind it"* as explicitly NOT
proposed, and § 6 requires the ceiling to sit **below the venue limit and above
normal operation**. Both of those need a DISTRIBUTION of normal operation, per
account, over time. PR #8665 made the measurement emittable and #8678 made it
readable; neither accumulates it. A single read cannot answer "what is normal",
and the first one taken (2026-08-09) landed on a **Sunday** — a held book with
no intraday variation in it, which is exactly the sample you must not generalise
from.

**The statistic that matters is the MAX, not the mean.** A ceiling that clears
the average but not the peak silently clamps correctly-risk-sized trades at the
peak — the failure § 6 names as *worse than no ceiling at all*.

**Observe-only.** Nothing reads this back to make a trading decision. It cannot
refuse a trade: it calls ``RiskManager.report()``, which reads policy only to
REPORT what was declared, and ``observe_exposure()`` is connection-free (equity
from the balance snapshot, notional from the journal). No socket, no order path.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SOAK_LOG_NAME = "exposure_soak.jsonl"

# Cadence knob, NOT an enable gate — the `ACCOUNT_REACHABILITY_CHECK_SECONDS` /
# `PROP_MONITOR_PULSE_SECONDS` shape. A required observability capability must
# not sit behind a default-off `*_ENABLED` flag (Prime Directive), so this is on
# by default and `<= 0` pauses it without a redeploy.
_CADENCE_ENV = "EXPOSURE_SOAK_SECONDS"
_DEFAULT_CADENCE_S = 900.0  # 15 min — ~26 samples per US trading day

# In-process cadence state. A restart re-samples immediately, which is harmless
# for an append-only observation log (and mildly desirable: a restart is exactly
# when you want a fresh reading).
_last_emit_ts: Optional[float] = None


def cadence_seconds() -> float:
    """Resolve the sampling cadence. Read at call time (next-tick effect)."""
    try:
        return float(os.environ.get(_CADENCE_ENV, _DEFAULT_CADENCE_S))
    except (TypeError, ValueError):
        return _DEFAULT_CADENCE_S


def build_exposure_soak_record(
    *,
    account_id: str,
    exchange: Optional[str] = None,
    account_class: Optional[str] = None,
    exposure: Optional[Dict[str, Any]] = None,
    venue_session: Optional[str] = None,
    **fields: Any,
) -> Optional[Dict[str, Any]]:
    """Pure builder — a JSON-able dict, or None on bad input. Never raises.

    The three exposure states are carried through **verbatim and uncollapsed**
    (`measured` / `policy_declared` / `exposure_multiple`). In particular an
    unmeasured account keeps `exposure_multiple: None` and never becomes `0.0`:
    *"we could not look"* and *"the account is flat"* are opposite statements to
    whoever later computes a max, and a fabricated zero would drag a per-account
    minimum toward a value that was never observed.
    """
    try:
        if not account_id:
            return None
        exp = exposure if isinstance(exposure, dict) else {}
        rec: Dict[str, Any] = {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            "account_id": str(account_id),
            "measured": bool(exp.get("measured")),
            "policy_declared": bool(exp.get("policy_declared")),
            "exposure_multiple": exp.get("exposure_multiple"),
            "open_gross_notional": exp.get("open_gross_notional"),
            "equity": exp.get("equity"),
            "max_gross_exposure_pct": exp.get("max_gross_exposure_pct"),
            "unmeasured_reason": exp.get("unmeasured_reason"),
        }
        if exchange:
            rec["exchange"] = str(exchange)
        if account_class:
            rec["account_class"] = str(account_class)
        # The 2026-08-09 lesson, recorded per row rather than left to be
        # reconstructed: an account can be quiet because the VENUE IS SHUT or
        # because it is REFUSING, and those are indistinguishable from the
        # trades table alone — only the calendar separates them. Stamping the
        # session phase here means a later reader never has to infer it.
        # CAVEAT, propagated deliberately from `market_hours.us_equity_session`:
        # US holidays are NOT modeled, so `rth` is "scheduled open", never proof
        # the venue actually traded. Do not read it as confirmation.
        if venue_session:
            rec["venue_session_us_equity"] = str(venue_session)
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        return rec
    except Exception:  # noqa: BLE001
        return None


def soak_log_path():
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / SOAK_LOG_NAME


def record_exposure_soak(record: Optional[Dict[str, Any]]) -> bool:
    """Best-effort append of one JSON line. Swallows all I/O errors."""
    if not record:
        return False
    try:
        path = soak_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return True
    except OSError:
        return False


def read_soak_records(
    *,
    limit: int = 200,
    account_id: Optional[str] = None,
    measured_only: bool = False,
) -> Dict[str, Any]:
    """Newest-first envelope ``{present, log_path, count, records, summary}``.

    ``summary.by_account`` is the point of the whole file: per account, the
    **max** measured multiple (what a ceiling must clear), the latest reading,
    and — stated beside them, never omitted — ``measured_n`` and ``rows``, so
    the max is never read over an unstated denominator. A max computed from two
    samples and a max computed from two hundred are different claims.
    """
    path = soak_log_path()
    empty_summary = {"total_scanned": 0, "by_account": {}, "venue_sessions": {}}
    if not path.exists():
        return {"present": False, "log_path": str(path), "count": 0,
                "records": [], "summary": empty_summary}
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"present": True, "log_path": str(path), "count": 0, "records": [],
                "error": str(exc), "summary": empty_summary}

    recs: List[Dict[str, Any]] = []
    by_account: Dict[str, Dict[str, Any]] = {}
    venue_sessions: Dict[str, int] = {}
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        recs.append(r)

        aid = r.get("account_id", "?")
        slot = by_account.setdefault(aid, {
            "rows": 0, "measured_n": 0, "unmeasured_n": 0,
            "max_multiple": None, "min_multiple": None,
            "last_multiple": None, "last_logged_at_utc": None,
            "policy_declared": False,
        })
        slot["rows"] += 1
        if r.get("measured"):
            slot["measured_n"] += 1
            m = r.get("exposure_multiple")
            if isinstance(m, (int, float)):
                m = float(m)
                slot["max_multiple"] = m if slot["max_multiple"] is None else max(slot["max_multiple"], m)
                slot["min_multiple"] = m if slot["min_multiple"] is None else min(slot["min_multiple"], m)
                slot["last_multiple"] = m
        else:
            slot["unmeasured_n"] += 1
        if r.get("policy_declared"):
            slot["policy_declared"] = True
        slot["last_logged_at_utc"] = r.get("logged_at_utc")

        vs = r.get("venue_session_us_equity")
        if vs:
            venue_sessions[vs] = venue_sessions.get(vs, 0) + 1

    total = len(recs)
    recs.reverse()  # newest-first
    if account_id:
        recs = [r for r in recs if r.get("account_id") == account_id]
    if measured_only:
        recs = [r for r in recs if r.get("measured")]
    recs = recs[: max(1, min(int(limit), 2000))]
    return {
        "present": True,
        "log_path": str(path),
        "count": len(recs),
        "records": recs,
        "summary": {
            "total_scanned": total,
            "by_account": by_account,
            "venue_sessions": venue_sessions,
        },
    }


def emit_exposure_soak(*, force: bool = False) -> int:
    """Sample every declared account's exposure once. Returns rows written.

    Called once per trader tick from ``src/main.py``; internally cadence-gated
    so the tick cost is one cheap DB/snapshot read per account per cadence
    window, not per tick. Best-effort throughout — **never raises into the tick**
    (observability must not perturb the trader loop).
    """
    global _last_emit_ts
    try:
        import time as _time
        cadence = cadence_seconds()
        if cadence <= 0 and not force:
            return 0  # paused by the operator
        now = _time.time()
        if not force and _last_emit_ts is not None and (now - _last_emit_ts) < cadence:
            return 0
        _last_emit_ts = now

        try:
            from src.runtime.market_hours import us_equity_session
            session = us_equity_session()
        except Exception:  # noqa: BLE001 — a missing session label must not skip the sample
            session = None

        from src.units.accounts import load_accounts
        accounts = load_accounts() or []
        written = 0
        for acct in accounts:
            try:
                rm = getattr(acct, "risk_manager", None)
                if rm is None:
                    continue
                # The SAME call the enforcing side reports through — never a
                # reconstruction, which would be a second definition of
                # "exposure" free to drift from the one that governs.
                exp = rm.report().get("exposure")
                rec = build_exposure_soak_record(
                    account_id=getattr(acct, "name", None) or "?",
                    exchange=getattr(acct, "exchange", None),
                    account_class=getattr(acct, "account_class", None),
                    exposure=exp,
                    venue_session=session,
                )
                if record_exposure_soak(rec):
                    written += 1
            except Exception:  # noqa: BLE001 — one bad account must not stop the sweep
                continue
        return written
    except Exception:  # noqa: BLE001 — best-effort; never break the tick
        return 0
