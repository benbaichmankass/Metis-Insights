"""S-051 — Read-only diagnostic endpoints for off-VM Claude / operator scripts.

Token-gated by ``DIAG_READ_TOKEN``. GET-only. Never returns secret material.
The PM-side / web-sandbox session has no mutation authority on the VM by
design — see ``docs/claude/vm-operator-mode.md`` § 9.

Allowlists for tables, systemd units, and log files are hard-coded at module
load. There is no path-traversal or arbitrary-SQL surface: callers pass an
alias which the server resolves via a static mapping. The sqlite connection
is opened with ``mode=ro`` so a downstream bug introducing UPDATE/DELETE
would still fail at the driver level.

Failure modes:
- 503 ``diag_disabled`` if ``DIAG_READ_TOKEN`` is unset (feature off).
- 401 ``missing_token`` / ``invalid_token`` on bad bearer.
- 400 ``unknown_<thing>`` on requests outside the allowlists.
- 503 ``journal_unavailable`` on a structural sqlite3.Error inside
  ``_journal_select`` (S-067 — was previously a silent ``[]``).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from src.utils.paths import runtime_logs_dir, trade_journal_db_path
from src.web.runtime_status import _resolve_git_sha

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diag", tags=["diag"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(trade_journal_db_path())
# Every runtime-log reader resolves through runtime_logs_dir() so DATA_DIR /
# RUNTIME_LOGS_DIR overrides match the writers (heartbeat.py,
# signal_audit_logger.py, runtime_status.py). The 2026-05-11 silent
# freeze (trader wrote /data/bot-data/runtime_logs/heartbeat.txt while
# diag read /home/ubuntu/ict-trading-bot/runtime_logs/heartbeat.txt) is
# the canonical incident this PR (T2) closes.
_AUDIT_LOG = runtime_logs_dir() / "signal_audit.jsonl"
_BOT_LOG = _REPO_ROOT / "bot.log"
_HEARTBEAT = runtime_logs_dir() / "heartbeat.txt"
_STATUS_JSON = runtime_logs_dir() / "runtime_status.json"

_JOURNAL_TABLES: dict[str, str] = {
    "order_packages": "datetime(updated_at)",
    "trades": "id",
    # 2026-05-29 — M13 AI-analyst tables. Before this, the insights cache
    # had NO read path on the /api/diag/* relay: the cache files
    # (runtime_logs/insights/*.json) aren't in _LOG_FILES, the generator
    # units weren't in _CANONICAL_UNITS, and /api/bot/insights/* lives
    # outside /api/diag/* so the relay can't reach it. A relay-only review
    # session (e.g. /performance-review's M13 cross-check) was therefore
    # blind to the analyst's output AND to whether the generator was even
    # alive. Exposing these two tables (both keyed by autoincrement id) lets
    # a session read the analyst's history + spend via journal?table=...
    # and confirm the generator is writing. Read-only, no secrets.
    "insights_history": "id",
    "insights_usage": "id",
}

_CANONICAL_UNITS: tuple[str, ...] = (
    # NB: the retired pre-rename trader unit "ict-bot.service" was removed here
    # (2026-06-28 full-system audit) — the live trader is ict-trader-live.service
    # (below). ict-bot.service has no deploy/ file and is not installed, so its
    # presence only made /api/diag/services perpetually report a not-found unit.
    # Do not re-add it.
    "ict-trader-live.service",
    "ict-web-api.service",
    "ict-telegram-bot.service",
    "ict-heartbeat.service",
    "ict-git-sync.service",
    "ict-git-sync.timer",
    # 2026-05-11 — external liveness watchdog (PR #950). Both the
    # oneshot service and its driving timer need to be queryable so
    # the operator (and Claude sessions) can verify the dead-man
    # switch is firing on cadence and inspect its decisions.
    "ict-liveness-watchdog.service",
    "ict-liveness-watchdog.timer",
    # 2026-05-28 — IB Gateway auto-heal watchdog (BL-20260527-003). The
    # oneshot + its driving timer, queryable so a session can verify the
    # MES dead-man switch is enabled and firing on cadence (and read its
    # probe/restart decisions) — same rationale as the liveness-watchdog
    # pair above.
    "ict-ib-gateway-watchdog.service",
    "ict-ib-gateway-watchdog.timer",
    # Web-API self-heal watchdog. The oneshot + its driving timer, queryable
    # so a session can verify the ict-web-api.service dead-man switch is
    # enabled and firing on cadence (and read its probe/restart decisions) —
    # same rationale as the watchdog pairs above.
    "ict-web-api-watchdog.service",
    "ict-web-api-watchdog.timer",
    # 2026-06-17 — DB-integrity checker (dashboard-truth Phase 4). The hourly
    # oneshot + its driving timer, queryable so a session can verify the
    # "alert us when intake breaks" check is enabled and firing on cadence
    # (and tail its WARN/CRITICAL decisions) — same rationale as the watchdog
    # pairs above.
    "ict-db-integrity.service",
    "ict-db-integrity.timer",
    # 2026-05-29 — the Claude update-channel drainer (@claude_ict_comms_bot).
    # It is the SOLE consumer of runtime_logs/pending_claude_pings, but was
    # never queryable from the diag surface, so when the channel went silent
    # (operator received no pings) there was no read path to see whether the
    # bridge was active or what its send errors were. Adding it here makes
    # `/api/diag/services` report its state and `/api/diag/journalctl?unit=
    # ict-claude-bridge.service` tail its journal (the unit now logs to
    # journald — see deploy/ict-claude-bridge.service).
    "ict-claude-bridge.service",
    # 2026-05-29 — M13 AI-analyst generator (fast tier every 15 min) + its
    # per-strategy slow tier (every 60 min) and their driving timers. These
    # are the SOLE writers of the insights cache + insights_history/usage
    # tables, but were never queryable from the diag surface — so when
    # /performance-review's M13 cross-check found the cache unreadable, there
    # was no read path to tell whether the generator was alive, stale, or
    # erroring. Adding them makes `/api/diag/services` report state and
    # `/api/diag/journalctl?unit=ict-insights-generator.service` tail the
    # generator log (cadence, budget skips, API errors).
    "ict-insights-generator.service",
    "ict-insights-generator.timer",
    "ict-insights-generator-strategies.service",
    "ict-insights-generator-strategies.timer",
    # 2026-06-13 — the hourly + daily reporter oneshots and their timers.
    # ict-hourly-snapshot is the SOLE writer of runtime_logs/balance_snapshots.json
    # (the dashboard + risk-gate account-balance view); ict-health-snapshot is the
    # SOLE writer of the artifacts/health/* cron snapshots. Both were invisible on
    # the diag surface, so when ict-hourly-snapshot's balance write silently diverged
    # to the repo path at the data-dir migration, the stall hid for ~3 weeks with no
    # read path to catch it (BL-20260611-M15-2). Making them queryable lets a session
    # verify the writer is firing on cadence and tail its journal for errors — same
    # rationale as the watchdog / bridge / insights pairs above.
    "ict-hourly-snapshot.service",
    "ict-hourly-snapshot.timer",
    "ict-health-snapshot.service",
    "ict-health-snapshot.timer",
)

_ADVISORY_LOG = runtime_logs_dir() / "advisory_decisions.jsonl"
_SHADOW_PRED_LOG = runtime_logs_dir() / "shadow_predictions.jsonl"
_SHADOW_PRED_BACKFILL_LOG = runtime_logs_dir() / "shadow_predictions_backfill.jsonl"
_IBKR_MES_PULL_LOG = runtime_logs_dir() / "ibkr_mes_pull.jsonl"
_NEWS_DECISIONS_LOG = runtime_logs_dir() / "news_decisions.jsonl"
_CONVICTION_SIZING_LOG = runtime_logs_dir() / "conviction_sizing.jsonl"
_CONVICTION_ARBITRATION_LOG = runtime_logs_dir() / "conviction_arbitration.jsonl"
_EXIT_LADDER_SOAK_LOG = runtime_logs_dir() / "exit_ladder_soak.jsonl"
_ORPHAN_EVENTS_LOG = runtime_logs_dir() / "orphan_events.jsonl"

_LOG_FILES: dict[str, Path] = {
    "audit": _AUDIT_LOG,
    "status": _STATUS_JSON,
    "heartbeat": _HEARTBEAT,
    "bot_log": _BOT_LOG,
    # M11 S10: ML advisory-score audit log. Written by
    # Coordinator.log_advisory_scores() when advisory-stage models are active.
    # Empty/absent when no advisory models are wired (expected for most installs).
    "advisory_decisions": _ADVISORY_LOG,
    # WS7 shadow-prediction audit log. Written by with_shadow_preds() on every
    # actionable signal once a shadow-stage model is auto-wired (the default).
    # Exposing the tail here lets a layer-2 health review confirm models are
    # actually logging in real time — the operator's "shadow-or-live, and a
    # non-logging model is a critical error" directive (2026-05-21). Absent
    # only if no shadow predictions have ever been written.
    "shadow_predictions": _SHADOW_PRED_LOG,
    "shadow_predictions_backfill": _SHADOW_PRED_BACKFILL_LOG,
    # Progress log for the operator-gated MES IBKR historical pull
    # (scripts/ops/pull_mes_ibkr_history.sh, run via the pull-mes-ibkr-history
    # system-action). Detached + paced, so this tail is how a session monitors
    # it. Absent until the pull has been run at least once.
    "ibkr_mes_pull": _IBKR_MES_PULL_LOG,
    # M9 news layer soak log. One JSON line per actionable signal the news
    # layer evaluated (decision/adjustment/veto/query/symbol), written by
    # src.news.news_audit only while the layer is active. The LOG is observe-only,
    # but it is NOT the case that the veto can't yet gate live money: when the
    # source is active the veto (pipeline.py) gates a live trade by default
    # (NEWS_VETO_ENABLED default-on; CLAUDE.md "selecting rss is the deliberate
    # activation"). The observe-until-opt-in half is the influence SIZING
    # (NEWS_INFLUENCE_MODE, default off), not the veto.
    # Absent until the news layer is active (NEWS_SOURCE=rss, or newsapi + NEWS_API_KEY).
    "news_decisions": _NEWS_DECISIONS_LOG,
    # Unified-confidence soak logs (observe-only, no order influence). Exposing
    # the tail here is how a session VERIFIES the conviction soak is actually
    # accruing evidence on the live VM before P4/P5 graduate it to driving
    # money. ``conviction_sizing`` (P2, #3796): one line per order — the would-be
    # conviction size vs the RiskManager qty. ``conviction_arbitration`` (P3,
    # #3810): one line per multi-intent aggregation — the would-be conviction
    # winner/target vs the actual priority/max-qty pick. Both written by the
    # observe-only annotators; neither ever changes an order. Absent until the
    # respective code path first runs (sizing: every order; arbitration: only
    # when ≥2 conviction-bearing intents compete on a symbol).
    "conviction_sizing": _CONVICTION_SIZING_LOG,
    "conviction_arbitration": _CONVICTION_ARBITRATION_LOG,
    # Exit-ladder soak (P3, dynamic-take-profit consistency): one line per
    # executed order (venue=api live broker order / venue=prop manual ticket) —
    # the materialized laddered exit that WOULD be used vs the single SL/TP
    # bracket actually placed. Observe-only; never changes an exit. Tail it to
    # verify the soak is accruing before P4 graduates the ladder to the real
    # exit. Absent until the first live opening order runs.
    "exit_ladder_soak": _EXIT_LADDER_SOAK_LOG,
    # NEW orphan trade rows (operator directive 2026-06-24: orphan is a problem
    # to reconcile, never a resting status). One JSON line per orphan-created
    # event (account/symbol/side/trade_id/origin/ts), written by
    # execution_diagnostics.enqueue_orphan_created_flag at every orphan-row
    # creation. The /health-review (and /system-review) drain this tail into the
    # health-review backlog so every orphan is tracked for reconciliation. Absent
    # until the first orphan row is created.
    "orphan_events": _ORPHAN_EVENTS_LOG,
}

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000
_DEFAULT_JOURNAL_LINES = 200
_MAX_JOURNAL_LINES = 2000
# 2026-05-18: bumped 10 → 30 after repeated `journalctl?lines=30..300`
# calls timed out on the live VM mid-health-review. Root cause is a
# large persistent journal whose backwards-scan exceeds the prior 10 s
# cap even for small `-n` values. The companion curl --max-time in
# .github/workflows/vm-diag-snapshot.yml was bumped from 20 → 40 so
# the HTTP layer doesn't preempt the new server-side limit. A longer
# tail-scan is bounded by the FastAPI worker thread budget; the read
# is the only path to `order_monitor:` lines (the trader writes to
# journal only — bot.log went stale 2026-05-03) so the diag surface
# stops working entirely if this is too tight.
_JOURNALCTL_TIMEOUT_S = 30
_SYSTEMCTL_TIMEOUT_S = 5

# Strict ISO-8601 form accepted by /api/diag/journalctl?since=… / ?until=…
# before forwarding to journalctl --since/--until. Matches:
#   2026-05-10T21:13:00            (naive UTC, journalctl assumes local)
#   2026-05-10T21:13:00Z           (explicit UTC)
#   2026-05-10T21:13:00+00:00      (explicit offset)
#   2026-05-10 21:13:00            (space-separated, journalctl-native)
# Rejects everything else — defence in depth even though the subprocess
# is invoked via argv list (no shell). FU-20260511-001.
_ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})?$"
)


def _diag_token() -> str | None:
    tok = os.environ.get("DIAG_READ_TOKEN", "").strip()
    return tok or None


def _require_diag_token(request: Request) -> None:
    expected = _diag_token()
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "diag_disabled"},
        )
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = auth[len("Bearer "):].strip()
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )


def _clamp(value: int | None, default: int, max_: int) -> int:
    if value is None or value < 1:
        return default
    return min(value, max_)


def _normalize_unit(unit: str) -> str:
    canonical = unit if "." in unit else f"{unit}.service"
    if canonical not in _CANONICAL_UNITS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_unit", "allowed": list(_CANONICAL_UNITS)},
        )
    return canonical


def _heartbeat_snapshot() -> dict[str, Any]:
    from src.runtime.heartbeat import heartbeat_label  # local import to keep router cheap
    if not _HEARTBEAT.exists():
        return {"present": False, "mtime": None, "age_seconds": None, "label": "stopped"}
    mtime = _HEARTBEAT.stat().st_mtime
    age = time.time() - mtime
    return {
        "present": True,
        "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
        "age_seconds": round(age, 2),
        "label": heartbeat_label(age),
    }


def _status_json_payload() -> dict[str, Any] | None:
    if not _STATUS_JSON.exists():
        return None
    try:
        with _STATUS_JSON.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # S-067 borderline: was silently `return None`. Keep the
        # `None` sentinel (callers branch on it) but log so a
        # corrupt status.json is visible in bot.log next time.
        logger.warning(
            "diag: status_json read failed: %s: %s",
            type(exc).__name__, exc,
        )
        return None


def _audit_tail(limit: int) -> list[dict[str, Any]]:
    if not _AUDIT_LOG.exists():
        return []
    try:
        with _AUDIT_LOG.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        # S-067 borderline: was silently `return []`. Log so a
        # signal_audit.jsonl read failure surfaces.
        logger.warning(
            "diag: audit_tail read failed: %s: %s",
            type(exc).__name__, exc,
        )
        return []
    out: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _journal_select(table: str, limit: int) -> list[dict[str, Any]]:
    if table not in _JOURNAL_TABLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_table", "allowed": sorted(_JOURNAL_TABLES.keys())},
        )
    if not _DB_PATH.exists():
        # Genuine "DB hasn't been created yet" — distinct from "DB
        # reachable but broken". Keep the empty-list shape here so a
        # fresh install doesn't 503 out of the gate.
        return []
    order_col = _JOURNAL_TABLES[table]
    try:
        # mode=ro guarantees no mutation can happen here even if a future
        # change accidentally introduces an UPDATE/DELETE statement.
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # S-067: "no such table" / schema mismatch / locked DB / corrupt
        # file used to be silently swallowed and surfaced as ``[]`` —
        # indistinguishable from "table empty". The /db_info endpoint
        # was added in #624 specifically to work around this; this is
        # the actual fix. Operator scripts and off-VM Claude sessions
        # now see a real 503 instead of a misleading empty result.
        logger.exception("diag: _journal_select(table=%s) sqlite read failed", table)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "journal_unavailable",
                "table": table,
                "reason": f"sqlite error: {type(exc).__name__}",
            },
        )


# ---------------------------------------------------------------------------
# Historical audit query (2026-06-01) — the time/event-filtered reader the
# line-capped /audit + /log_file tails cannot provide.
#
# /audit and /log_file?name=audit return only the last _MAX_LIMIT (1000) lines
# of signal_audit.jsonl — on a busy day that is ~15 min of history — so an
# off-VM session cannot retrieve an arbitrary historical window or grep for a
# specific event type (e.g. all `regime_shadow_gate` rows on a given day; the
# PERF-20260601-008/011 regime-router verification needs exactly that). The
# full audit stream is dual-written to trade_journal.db::signals
# (signal_audit_logger._dual_write_to_db, on by default; SIGNAL_DUAL_WRITE_
# DISABLED opts out) with the typed columns PLUS the entire original payload
# as JSON in `meta`. This reader SELECTs that table with since/until (on the
# indexed logged_at_utc) + optional event / strategy / symbol / side filters
# and offset paging, so a historical-window verification is one bounded query
# instead of an unreachable tail.
# ---------------------------------------------------------------------------

# `event` is matched inside the `meta` JSON blob via LIKE; restrict it to a
# safe identifier charset so a caller cannot smuggle LIKE wildcards (% / _) or
# otherwise alter the match. strategy / symbol / side are bound params
# (injection-safe) so they need no charset guard.
_EVENT_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _normalize_utc_bound(iso: str) -> str:
    """Convert a validated ISO-8601 bound to the canonical column format
    (UTC, ``+00:00`` offset) so a plain TEXT comparison against
    ``logged_at_utc`` is correct regardless of whether the caller sent
    ``Z`` / ``+00:00`` / a naive timestamp.

    ``logged_at_utc`` is always written as
    ``datetime.now(timezone.utc).isoformat()`` (fixed ``+00:00`` offset), so
    once the bound is in that same representation a lexicographic ``>=`` /
    ``<=`` is also chronological — and, unlike wrapping the column in SQLite
    ``datetime()``, this does NOT depend on the live SQLite version's support
    for the ``T`` separator / ``Z`` suffix (added only in SQLite 3.42).
    """
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Already regex-validated upstream; fall back to the raw string.
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _signals_query(
    *,
    since: str | None,
    until: str | None,
    event: str | None,
    strategy: str | None,
    symbol: str | None,
    side: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Time/event-filtered read of ``trade_journal.db::signals`` (the audit
    dual-write). Newest-first by ``logged_at_utc``. Read-only (``mode=ro``).

    Each returned row carries the typed columns merged with the parsed
    ``meta`` payload, so callers see the same ``event`` / ``regime`` /
    ``adx_14`` / ``enforced`` / ``cell`` fields the JSONL row had. Typed
    columns win on key collision (they are the canonical projection).

    Missing DB or absent ``signals`` table → empty result with a flag
    (not a 503), matching ``_journal_select``'s fresh-install tolerance and
    surfacing the "dual-write never ran / disabled" case explicitly.
    """
    result: dict[str, Any] = {
        "table": "signals",
        "filters": {
            "since": since, "until": until, "event": event,
            "strategy": strategy, "symbol": symbol, "side": side,
        },
        "limit": limit,
        "offset": offset,
        "rows": [],
        "count": 0,
        "dual_write_present": False,
    }
    if not _DB_PATH.exists():
        return result
    where: list[str] = []
    params: list[Any] = []
    # logged_at_utc is stored as an ISO-8601 string with a stable +00:00
    # offset (log_signal stamps datetime.now(timezone.utc).isoformat()), so
    # a lexicographic >=/<= compare is also chronological. Bound params.
    if since:
        where.append("logged_at_utc >= ?")
        params.append(_normalize_utc_bound(since))
    if until:
        where.append("logged_at_utc <= ?")
        params.append(_normalize_utc_bound(until))
    if strategy:
        where.append("strategy = ?")
        params.append(strategy)
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)
    if side:
        where.append("side = ?")
        params.append(side)
    if event:
        # event lives in the meta JSON blob, not a typed column. json.dumps
        # renders it as `"event": "<name>"`; match that substring. event is
        # charset-validated at the route layer so no LIKE-wildcard smuggling.
        where.append("meta LIKE ?")
        params.append(f'%"event": "{event}"%')
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT id, logged_at_utc, strategy, symbol, side, qty, status, "
        "reason, meta FROM signals" + clause +
        # logged_at_utc is a fixed +00:00 isoformat string, so a TEXT sort is
        # chronological — and avoids depending on SQLite datetime() T/Z parsing.
        " ORDER BY logged_at_utc DESC, id DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:  # allow-silent: not silent — surfaces no-such-table as an explicit error flag and logs + 503s every other sqlite error (mirrors _journal_select)
        # "no such table: signals" => the dual-write has never run (or was
        # disabled before any write). Surface that explicitly as a non-fatal
        # signal rather than a misleading 503, so the caller learns the
        # table is absent (and can check SIGNAL_DUAL_WRITE_DISABLED).
        if "no such table" in str(exc).lower():
            result["error"] = "signals_table_absent"
            return result
        logger.exception("diag: _signals_query sqlite read failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "signals_query_unavailable",
                "reason": f"sqlite error: {type(exc).__name__}",
            },
        )
    result["dual_write_present"] = True
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        meta_raw = row.pop("meta", None)
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
            except (json.JSONDecodeError, TypeError):
                row["meta_raw"] = meta_raw
            else:
                if isinstance(meta, dict):
                    # Full original payload (event/regime/adx_14/enforced/…)
                    # under the canonical typed columns.
                    row = {**meta, **row}
        out.append(row)
    result["rows"] = out
    result["count"] = len(out)
    return result


def _db_info_payload() -> dict[str, Any]:
    """Return DB metadata for diagnostic cross-referencing of trader vs
    web-api. Resolves the same ``_DB_PATH`` the journal endpoint reads,
    plus inode + size + table list + per-table row count.

    The 2026-05-09 ``order_packages returns []`` mystery surfaced
    because the existing ``journal`` endpoint silently swallowed
    ``sqlite3.Error`` (returns ``[]``) — so a "no such table" or schema
    mismatch was indistinguishable from "table empty". S-067 fixed the
    journal endpoint itself; this endpoint stays as the
    failure-surfacing companion (it returns the per-table error string
    even when the journal endpoint already 503s on the same condition).

    Best-effort: every step is wrapped so a single failure never
    aborts the whole payload. ``error_per_table`` is only populated
    when a SELECT raised; missing keys mean the count succeeded.
    """
    payload: dict[str, Any] = {
        "db_path": str(_DB_PATH),
        "db_path_resolved": None,
        "exists": False,
        "size_bytes": None,
        "inode": None,
        "tables": [],
        "row_counts": {},
        "error_per_table": {},
        "load_error": None,
    }
    try:
        payload["db_path_resolved"] = str(_DB_PATH.resolve())
    except Exception as exc:  # noqa: BLE001
        payload["load_error"] = f"resolve: {type(exc).__name__}: {exc}"
        return payload

    if not _DB_PATH.exists():
        return payload
    payload["exists"] = True
    try:
        st = os.stat(_DB_PATH)
        payload["size_bytes"] = st.st_size
        payload["inode"] = st.st_ino
    except OSError as exc:
        payload["load_error"] = f"stat: {type(exc).__name__}: {exc}"

    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "ORDER BY name"
                ).fetchall()
            ]
            payload["tables"] = tables
            for tbl in tables:
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}")
                    payload["row_counts"][tbl] = int(cur.fetchone()[0])
                except sqlite3.Error as exc:
                    payload["error_per_table"][tbl] = (
                        f"{type(exc).__name__}: {exc}"
                    )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        payload["load_error"] = f"connect: {type(exc).__name__}: {exc}"

    return payload


# S-067 follow-up #9: vm_health implementation moved to
# src/web/api/_vm_health.py to remove the diag.py / dashboard.py
# fork. Re-exported under the legacy ``_vm_health`` name so
# tests (e.g. tests/test_web_api_diag.py + the monkeypatching in
# the S-067 silent-empty regression tests) keep working without
# modification.
from src.web.api._vm_health import vm_health as _vm_health  # noqa: E402


def _is_active_batch(units: list[str]) -> dict[str, str]:
    if not units:
        return {}
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", *units],
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {u: "unknown" for u in units}
    states = (proc.stdout or "").splitlines()
    return {
        u: (states[i].strip() if i < len(states) else "unknown")
        for i, u in enumerate(units)
    }


def _normalize_journalctl_timestamp(ts: str) -> str:
    """Convert a validated ISO-8601 string into journalctl's universal form.

    journalctl 245 (Ubuntu 20.04) rejects ISO-8601 with the ``T`` separator
    or trailing ``Z`` — it expects ``YYYY-MM-DD HH:MM:SS`` and optionally a
    timezone word like ``UTC``. journalctl 252+ accepts both forms, but the
    live VM still runs the older binary, so passing ``2026-05-11T15:40:00Z``
    verbatim returns rc=1 with no log lines (issue #930).

    Normalize:
      ``2026-05-11T15:40:00Z``        → ``2026-05-11 15:40:00 UTC``
      ``2026-05-11T15:40:00+00:00``   → ``2026-05-11 15:40:00 UTC``
      ``2026-05-11T15:40:00-05:00``   → ``2026-05-11 15:40:00 -05:00``
      ``2026-05-11T15:40:00``         → ``2026-05-11 15:40:00`` (naive, local)
      ``2026-05-11 15:40:00``         → ``2026-05-11 15:40:00`` (passthrough)

    The input is already validated by _ISO_TIMESTAMP_RE at the route layer,
    so we know it's well-formed.
    """
    # T → space
    out = ts.replace("T", " ", 1)
    # Z (or +00:00 / +0000) → UTC suffix (journalctl-native)
    if out.endswith("Z"):
        out = out[:-1] + " UTC"
    elif out.endswith("+00:00") or out.endswith("+0000"):
        # Strip the offset, replace with the UTC word
        out = out.rsplit("+", 1)[0].rstrip() + " UTC"
    return out


def _journalctl_tail(
    unit: str,
    lines: int,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    canonical = _normalize_unit(unit)
    cmd = [
        "journalctl",
        "-u",
        canonical,
        "-n",
        str(lines),
        "--no-pager",
        "--output=short-iso",
    ]
    # ?since / ?until support — passes through to journalctl's native
    # --since/--until flags. The endpoint route validates the format
    # with _ISO_TIMESTAMP_RE before reaching this helper. Normalize
    # to journalctl's universal "YYYY-MM-DD HH:MM:SS [UTC]" form so
    # older journalctl versions (Ubuntu 20.04 ships 245) also accept it.
    if since:
        cmd.extend(["--since", _normalize_journalctl_timestamp(since)])
    if until:
        cmd.extend(["--until", _normalize_journalctl_timestamp(until)])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_JOURNALCTL_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return {"unit": canonical, "available": False, "reason": "journalctl_not_found", "lines": []}
    except subprocess.TimeoutExpired:
        return {"unit": canonical, "available": False, "reason": "timeout", "lines": []}
    output = proc.stdout or ""
    stderr = (proc.stderr or "").strip()
    out_lines = output.splitlines()[-lines:] if output else []
    # journalctl rc=1 has overloaded semantics:
    #   * rc=0, stdout=N lines      → matches found
    #   * rc=1, stdout="", stderr="" → query valid, just no matching entries
    #   * rc=1, stdout="", stderr=X → real failure (bad args, perm, etc.)
    #   * rc=0, stdout=""           → unit has no entries at all
    # Treat empty-stderr rc=1 as "available, just empty" so a legitimate
    # zero-match window doesn't get misreported as a unit-unavailable
    # failure (issue #930).
    available = proc.returncode == 0 or (proc.returncode == 1 and not stderr)
    result: dict[str, Any] = {
        "unit": canonical,
        "available": available,
        "returncode": proc.returncode,
        "lines": out_lines,
    }
    if not available and stderr:
        result["stderr"] = stderr[:500]  # truncate; defensive
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/snapshot")
async def get_snapshot(request: Request, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    _require_diag_token(request)
    n = _clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT)
    states = _is_active_batch(list(_CANONICAL_UNITS))
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "heartbeat": _heartbeat_snapshot(),
        "status": _status_json_payload(),
        "audit_tail": _audit_tail(n),
        "order_packages": _journal_select("order_packages", n),
        "trades": _journal_select("trades", n),
        "vm_health": _vm_health(),
        "services": [{"unit": u, "state": states.get(u, "unknown")} for u in _CANONICAL_UNITS],
    }


@router.get("/audit")
async def get_audit(request: Request, limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    _require_diag_token(request)
    return _audit_tail(_clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT))


@router.get("/journal")
async def get_journal(
    request: Request,
    table: str,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    _require_diag_token(request)
    return _journal_select(table, _clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT))


@router.get("/audit_query")
async def get_audit_query(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    event: str | None = None,
    strategy: str | None = None,
    symbol: str | None = None,
    side: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Historical, time/event-filtered audit read backed by the
    ``trade_journal.db::signals`` dual-write.

    Unlike ``/audit`` and ``/log_file?name=audit`` — which tail only the last
    ``_MAX_LIMIT`` (1000) lines of ``signal_audit.jsonl`` (~15 min on a busy
    day) — this reaches arbitrary history because the full audit stream is
    mirrored to the indexed ``signals`` table. Use it to pull a specific
    window (``since`` / ``until``) or every row of one event type
    (``event=regime_shadow_gate``) without the tail cap.

    Params:
      * ``since`` / ``until`` — ISO-8601 (``2026-06-01T15:00:00Z``); filter on
        ``logged_at_utc``. Validated against ``_ISO_TIMESTAMP_RE``.
      * ``event`` — match the audit ``event`` field (stored in the ``meta``
        JSON), e.g. ``regime_shadow_gate``, ``vwap_eval``. Charset
        ``[A-Za-z0-9_]+``.
      * ``strategy`` / ``symbol`` / ``side`` — exact-match typed columns.
      * ``limit`` (≤ ``_MAX_LIMIT``) + ``offset`` — page back through the
        full table.

    Rows are newest-first and carry the typed columns merged with the parsed
    ``meta`` payload (``regime`` / ``adx_14`` / ``enforced`` / ``cell`` …).
    Empty ``rows`` with ``dual_write_present: false`` / ``error:
    signals_table_absent`` means the dual-write hasn't populated the table
    (check ``SIGNAL_DUAL_WRITE_DISABLED``).
    """
    _require_diag_token(request)
    for label, value in (("since", since), ("until", until)):
        if value is not None and not _ISO_TIMESTAMP_RE.match(value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_timestamp",
                    "param": label,
                    "expected": "ISO-8601 like 2026-06-01T15:00:00Z",
                    "got": value,
                },
            )
    if event is not None and not _EVENT_NAME_RE.match(event):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_event",
                "expected": "identifier matching [A-Za-z0-9_]+",
                "got": event,
            },
        )
    return _signals_query(
        since=since,
        until=until,
        event=event,
        strategy=strategy,
        symbol=symbol,
        side=side,
        limit=_clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT),
        offset=max(0, offset),
    )


@router.get("/db_info")
async def get_db_info(request: Request) -> dict[str, Any]:
    """Diagnostic — resolved DB path, inode, table list, row counts.

    Companion to ``/journal``. Surfaces the per-table error string when
    a SELECT raises (``journal`` swallows it as ``[]``). Trader vs
    web-api inode mismatch on the same logical path is the canonical
    signature for the 2026-05-09 ``order_packages returns []`` mystery.
    """
    _require_diag_token(request)
    return _db_info_payload()


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    _require_diag_token(request)
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "heartbeat": _heartbeat_snapshot(),
        "status": _status_json_payload(),
        "vm_health": _vm_health(),
    }


@router.get("/services")
async def get_services(request: Request) -> list[dict[str, str]]:
    _require_diag_token(request)
    states = _is_active_batch(list(_CANONICAL_UNITS))
    return [{"unit": u, "state": states.get(u, "unknown")} for u in _CANONICAL_UNITS]


@router.get("/journalctl")
async def get_journalctl(
    request: Request,
    unit: str,
    lines: int = _DEFAULT_JOURNAL_LINES,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Tail systemd-journal lines for an allowlisted unit.

    ``since`` / ``until`` accept ISO-8601 timestamps (``2026-05-10T21:13:00Z``
    or ``2026-05-10 21:13:00``) and forward to journalctl's native
    ``--since`` / ``--until`` flags. Format is strictly validated against
    ``_ISO_TIMESTAMP_RE`` before being passed to the subprocess argv —
    arbitrary strings are rejected with HTTP 400. Without these params
    the endpoint preserves the pre-FU-20260511-001 tail-only behaviour
    (max 2000 lines, recent end of the journal). FU-005 / FU-008 style
    historical-window evidence needs ``?since=`` to reach back hours.
    """
    _require_diag_token(request)
    for label, value in (("since", since), ("until", until)):
        if value is not None and not _ISO_TIMESTAMP_RE.match(value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_timestamp",
                    "param": label,
                    "expected": "ISO-8601 like 2026-05-10T21:13:00Z",
                    "got": value,
                },
            )
    return _journalctl_tail(
        unit,
        _clamp(lines, _DEFAULT_JOURNAL_LINES, _MAX_JOURNAL_LINES),
        since=since,
        until=until,
    )


@router.get("/version")
async def get_version(request: Request) -> dict[str, Any]:
    """Diagnostic — git SHA + captured timestamp of the running web-api
    process. Used by ``scripts/deploy_pull_restart.sh`` to assert that
    a post-deploy restart actually rolled the running code forward
    (the 2026-05-09 24h-stale-code incident shipped because nothing
    in the deploy chain confirmed the running web-api had rebooted).

    Returns ``git_sha`` resolved by the same helper that powers
    ``runtime_logs/runtime_status.json::git_sha`` so the value is consistent
    between read sources. ``"unknown"`` is a legitimate value on
    sandbox / dev hosts without git available; the deploy script
    treats ``unknown`` as a soft failure.
    """
    _require_diag_token(request)
    return {
        "git_sha": _resolve_git_sha(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/log_file")
async def get_log_file(
    request: Request,
    name: str,
    lines: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    _require_diag_token(request)
    if name not in _LOG_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_log_file", "allowed": sorted(_LOG_FILES.keys())},
        )
    n = _clamp(lines, _DEFAULT_LIMIT, _MAX_LIMIT)
    path = _LOG_FILES[name]
    if not path.exists():
        return {"name": name, "path": str(path), "present": False, "lines": []}
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            content = fh.readlines()
    except OSError as exc:
        return {
            "name": name,
            "path": str(path),
            "present": True,
            "error": str(exc),
            "lines": [],
        }
    return {
        "name": name,
        "path": str(path),
        "present": True,
        "size_bytes": path.stat().st_size,
        "lines": [ln.rstrip("\n") for ln in content[-n:]],
    }


@router.get("/shadow_stats")
async def get_shadow_stats(
    request: Request,
    model_id: str | None = None,
    stage: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """Token-gated mirror of GET /api/bot/shadow/stats for diag-relay access.

    FU-20260516-001: /api/bot/shadow/stats is not under /api/diag/ so the
    vm-diag-snapshot relay cannot reach it. This endpoint exposes the same
    aggregate shadow-prediction stats through the authenticated diag surface
    so Layer-2 health reviews can cross-tab audit actionable signals against
    shadow prediction counts without requiring SSH.
    """
    _require_diag_token(request)
    try:
        from ml.shadow.inspector import aggregate, filter_records, iter_records
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "shadow_inspector_unavailable", "detail": str(exc)},
        ) from exc

    override = __import__("os").environ.get("SHADOW_PREDICTIONS_LOG")
    log = (
        __import__("pathlib").Path(override)
        if override
        else runtime_logs_dir() / "shadow_predictions.jsonl"
    )

    since_dt = None
    if since is not None:
        try:
            from datetime import timezone
            ts = __import__("datetime").datetime.fromisoformat(since)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            since_dt = ts
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "invalid_since", "detail": str(exc)}) from exc

    records = filter_records(iter_records(log), model_id=model_id, stage=stage, since=since_dt)
    stats = aggregate(records)
    rows = [
        {
            "model_id": s.model_id,
            "stage": s.stage,
            "count": s.count,
            "score_mean": s.score_mean,
            "score_min": s.score_min if s.count else None,
            "score_max": s.score_max if s.count else None,
            "first_seen": s.first_seen.isoformat() if s.first_seen else None,
            "last_seen": s.last_seen.isoformat() if s.last_seen else None,
        }
        for s in stats
    ]
    return {
        "log_present": log.is_file(),
        "log_path": str(log),
        "records": rows,
        "count": len(rows),
    }


@router.get("/exchange_positions")
async def get_exchange_positions(
    request: Request,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Read-only **exchange-side** open positions per account — the BROKER's
    truth, not the journal.

    Added 2026-06-19 (BL-20260618-RECONCILE-DUP residual / BL-20260619): a
    web/PM session has no other way to confirm whether a journal orphan
    actually exists on the broker before any cleanup. This mirrors
    ``get_account_balances`` exactly — it opens a brief read-only client per
    account via ``account_open_positions`` (the same primitive the live
    reconciler calls each tick), so it adds no new connection class and places
    NO order.

    ``account_id`` filters to one account. Per-account ``positions`` is:
      * ``null``  — could-not-read (logged-out IB gateway / missing creds /
        SDK error). NOT the same as flat.
      * ``[]``    — genuinely flat on the exchange.
      * ``[{symbol, side, size, entry_price, unrealised_pnl}, ...]`` — live.

    Tier 1 — read-only, token-gated, best-effort per account.
    """
    _require_diag_token(request)
    try:
        from src.units.ui.data_loaders import account_open_positions, list_accounts
    except Exception as exc:  # noqa: BLE001  # allow-silent: logged + re-raised as 503 (not swallowed)
        logger.warning("get_exchange_positions: import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "data_loaders_unavailable", "detail": str(exc)},
        ) from exc

    try:
        accounts = list_accounts() or []
    except Exception as exc:  # noqa: BLE001  # allow-silent: read-only diag; logged, returns empty accounts so the call still answers
        logger.warning("get_exchange_positions: list_accounts failed: %s", exc)
        accounts = []

    out: list[dict[str, Any]] = []
    for acc in accounts:
        aid = (acc or {}).get("account_id")
        if account_id and aid != account_id:
            continue
        positions: Any = None
        err: str | None = None
        try:
            positions = account_open_positions(acc)
        except Exception as exc:  # noqa: BLE001  # allow-silent: per-account error surfaced in the row (error + positions=null), logged; one account must not fail the call
            err = f"{type(exc).__name__}: {exc}"
            logger.warning("get_exchange_positions: %s raised %s", aid, exc)
        out.append({
            "account_id": aid,
            "exchange": (acc or {}).get("exchange"),
            # null = could-not-read; [] = flat; list = live positions.
            "positions": positions,
            "count": (len(positions) if isinstance(positions, list) else None),
            "error": err,
        })
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "requested_account_id": account_id,
        "accounts": out,
    }
