"""Hourly operator summary — S-022 PR2.

Replaces the previous twice-a-day "service is alive" one-liner with a
structured hourly report that answers, at a glance, every question an
operator typically wants to ask:

  * Are ticks running?
  * What signals fired in the last hour?
  * What trades were placed / closed, and what was the realized PnL?
  * What does each account look like (balance + 1h delta)?
  * What did each strategy do today?
  * Is anything broken?

Data sources (all already present — no new infra):
  * ``runtime_logs/signal_audit.jsonl`` — pipeline tick + signal events.
  * ``runtime_logs/outcomes.jsonl`` — WARN+ outcomes from PR1.
  * ``trade_journal.db`` — placed and closed trade rows.
  * ``src/bot/data_loaders.py`` — account balances, open positions,
    per-strategy activity.
  * ``runtime_logs/balance_snapshots.json`` — written by this module so
    we can compute a 1h delta without a balance-history table.

Health checks in this PR are intentionally lightweight — last-tick
freshness, outcome error counts. The full health pass (VM service
status, repo-vs-VM HEAD drift, DB writability, disk free) lands in
PR3 (``src/runtime/health.py``) and the assembler will pick it up
automatically as soon as that helper exists.

This module must NEVER raise. ``build_hourly_report()`` returns a
str, even if every data source is empty or unreachable.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


from src.utils.paths import runtime_logs_dir  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_LOGS = runtime_logs_dir()
SIGNAL_AUDIT_FILE = RUNTIME_LOGS / "signal_audit.jsonl"
OUTCOMES_FILE = RUNTIME_LOGS / "outcomes.jsonl"
BALANCE_SNAPSHOT_FILE = RUNTIME_LOGS / "balance_snapshots.json"


# ---------------------------------------------------------------------------
# Tick + signal stats from signal_audit.jsonl
# ---------------------------------------------------------------------------


def _load_audit_lines_since(
    since: datetime, path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Return parsed JSONL records with ``logged_at_utc`` >= since.

    ``path`` defaults to the module-level ``SIGNAL_AUDIT_FILE`` resolved
    at *call* time (so tests can monkeypatch ``hr.SIGNAL_AUDIT_FILE``).
    """
    path = path or SIGNAL_AUDIT_FILE
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ts_str = rec.get("logged_at_utc") or ""
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= since:
                    out.append(rec)
    except OSError as exc:
        logger.warning("hourly_report: could not read %s: %s", path, exc)
    return out


def summarize_ticks(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Count tick outcomes and extract per-strategy signal counts."""
    pipeline_results = [
        r for r in records if (r.get("event") or "") == "pipeline_result"
    ]
    ticks_ok = sum(
        1
        for r in pipeline_results
        if r.get("status") in {"submitted", "dry_run", "skipped",
                               "halted", "news_veto", "refused",
                               "multi_account_dispatched"}
    )
    ticks_err = sum(
        1
        for r in pipeline_results
        if r.get("status") in {"failed_validation", "failed_exchange",
                               "failed_dispatch", "error"}
    )
    actionable = [
        r for r in pipeline_results
        if (r.get("side") in {"buy", "sell"})
        and r.get("status") not in {"skipped", "halted"}
    ]
    by_strategy: Dict[str, int] = {}
    for r in actionable:
        s = r.get("strategy") or "unknown"
        by_strategy[s] = by_strategy.get(s, 0) + 1
    return {
        "ticks_ok": ticks_ok,
        "ticks_err": ticks_err,
        "signals_total": len(actionable),
        "signals_by_strategy": by_strategy,
        "last_tick_ts": _max_logged_at(pipeline_results),
    }


def _max_logged_at(records: List[Dict[str, Any]]) -> Optional[str]:
    best: Optional[str] = None
    for r in records:
        ts = r.get("logged_at_utc")
        if ts and (best is None or ts > best):
            best = ts
    return best


# ---------------------------------------------------------------------------
# Trade-journal queries
# ---------------------------------------------------------------------------


def _trade_journal_path() -> Optional[Path]:
    """Resolve the canonical trade journal DB; None if it doesn't exist."""
    from src.utils.paths import trade_journal_db_path
    p = Path(trade_journal_db_path())
    return p if p.exists() else None


def trades_in_window(since: datetime) -> Dict[str, Any]:
    """Return ``{placed: [...], closed: [...], realized_pnl: float}`` for
    the trade-journal window since ``since``.

    Empty / safe defaults if the DB is missing or the schema differs.
    """
    empty = {"placed": [], "closed": [], "realized_pnl": 0.0}
    db = _trade_journal_path()
    if db is None:
        return empty
    iso_since = since.astimezone(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(str(db))
        try:
            conn.row_factory = sqlite3.Row
            # Filter out refusal rows so "placed" reflects real exchange
            # submissions (CP-2026-05-03-14). Rejected/exchange_rejected
            # rows are visible in /packages instead.
            placed_rows = conn.execute(
                "SELECT id, timestamp, symbol, direction, entry_price,"
                " position_size, strategy_name, status FROM trades"
                " WHERE COALESCE(is_backtest, 0) = 0"
                " AND COALESCE(status, 'open')"
                " NOT IN ('rejected', 'exchange_rejected', 'rejected_too_small', 'orphaned')"
                " AND COALESCE(created_at, timestamp) >= ?"
                " ORDER BY datetime(COALESCE(created_at, timestamp)) DESC",
                (iso_since,),
            ).fetchall()
            closed_rows = conn.execute(
                "SELECT id, timestamp, symbol, direction, entry_price,"
                " exit_price, pnl, strategy_name FROM trades"
                " WHERE COALESCE(is_backtest, 0) = 0 AND status = 'closed'"
                " AND COALESCE(timestamp, created_at) >= ?"
                " ORDER BY datetime(COALESCE(timestamp, created_at)) DESC",
                (iso_since,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("hourly_report: trade_journal query failed: %s", exc)
        return empty

    realized = 0.0
    for r in closed_rows:
        try:
            realized += float(r["pnl"] or 0.0)
        except (TypeError, ValueError):
            pass
    return {
        "placed": [dict(r) for r in placed_rows],
        "closed": [dict(r) for r in closed_rows],
        "realized_pnl": realized,
    }


# ---------------------------------------------------------------------------
# Account snapshots — balances + 1h delta
# ---------------------------------------------------------------------------


def _load_balance_snapshots() -> Dict[str, Any]:
    if not BALANCE_SNAPSHOT_FILE.exists():
        return {}
    try:
        return json.loads(BALANCE_SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("hourly_report: balance snapshot read failed: %s", exc)
        return {}


def _save_balance_snapshots(data: Dict[str, Any]) -> None:
    try:
        RUNTIME_LOGS.mkdir(parents=True, exist_ok=True)
        BALANCE_SNAPSHOT_FILE.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("hourly_report: balance snapshot write failed: %s", exc)


def _record_balance_snapshot_to_db(
    account_id: str,
    *,
    balance: Optional[float],
    delta_1h: Optional[float],
    open_positions: Optional[int],
    api_ok: bool,
    ts: str,
) -> None:
    """Best-effort append of one balance reading to the canonical DB table
    (WC-5, dashboard-truth 2026-06-16).

    The JSON snapshot above keeps only the LATEST reading per account; this
    appends to ``trade_journal.db::balance_snapshots`` so balances get a real
    DB home + history (the audit's "balances have no DB table" gap). Swallows
    all exceptions — the hourly report must never fail because the DB is
    momentarily locked or the column set drifted.
    """
    try:
        from src.units.db.database import Database

        Database().insert_balance_snapshot(
            account_id,
            balance=balance,
            delta_1h=delta_1h,
            open_positions=open_positions,
            api_ok=api_ok,
            ts=ts,
        )
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort DB dual-write of an already-rendered balance; the hourly report (and JSON snapshot) must never fail because the DB is momentarily locked. The reading is still returned + saved to JSON; the warning is logged.
        logger.warning(
            "hourly_report: balance snapshot DB write(%s) failed: %s",
            account_id, exc,
        )


def account_snapshots() -> Optional[List[Dict[str, Any]]]:
    """Return one dict per account: balance, 1h delta, API ok/err, open pos count.

    Sentinel semantics (S-067 follow-up D2 — see
    docs/audits/silent-empty-reporting-2026-05-10.md § Phase-2 #2):

    * ``[]`` — no accounts configured **or** ``data_loaders`` import
      failed (optional dependency; legitimate per the audit).
    * ``None`` — ``list_accounts()`` raised. The renderer surfaces
      this as "Accounts — data unavailable" rather than collapsing
      to "no accounts configured".
    * ``List[...]`` populated — normal path.
    """
    try:
        from src.bot.data_loaders import (
            account_balance,
            account_open_positions,
            list_accounts,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hourly_report: data_loaders unavailable: %s", exc)
        return []

    try:
        accounts = list_accounts()
    except (OSError, RuntimeError, AttributeError) as exc:
        logger.warning("hourly_report: list_accounts failed: %s", exc)
        return None

    prev = _load_balance_snapshots()
    now_iso = datetime.now(timezone.utc).isoformat()
    new_snap: Dict[str, Any] = {}

    out: List[Dict[str, Any]] = []
    for acc in accounts:
        aid = acc.get("account_id") or "unknown"
        try:
            bal = account_balance(acc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hourly_report: balance(%s) raised: %s", aid, exc)
            bal = None

        if bal is None:
            out.append({
                "account_id": aid,
                "balance": None,
                "delta_1h": None,
                "api_ok": False,
                "open_positions": None,
            })
            _record_balance_snapshot_to_db(
                aid, balance=None, delta_1h=None,
                open_positions=None, api_ok=False, ts=now_iso,
            )
            continue

        total = float(bal.get("total_usdt") or 0.0)
        prev_entry = (prev.get(aid) or {})
        prev_total = prev_entry.get("balance")
        delta = total - float(prev_total) if isinstance(prev_total, (int, float)) else None
        new_snap[aid] = {"balance": total, "ts": now_iso}

        try:
            positions = account_open_positions(acc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hourly_report: positions(%s) raised: %s", aid, exc)
            positions = None
        open_count = len(positions) if isinstance(positions, list) else None

        out.append({
            "account_id": aid,
            "balance": total,
            "delta_1h": delta,
            "api_ok": True,
            "open_positions": open_count,
        })
        _record_balance_snapshot_to_db(
            aid, balance=total, delta_1h=delta,
            open_positions=open_count, api_ok=True, ts=now_iso,
        )

    if new_snap:
        _save_balance_snapshots(new_snap)
    return out


# ---------------------------------------------------------------------------
# Strategies — daily snapshot
# ---------------------------------------------------------------------------


def strategy_snapshots() -> Optional[List[Dict[str, Any]]]:
    """Return per-strategy daily snapshots, or a sentinel.

    Sentinel semantics (S-067 follow-up D3 — see
    docs/audits/silent-empty-reporting-2026-05-10.md § Phase-2 #3):

    * ``[]`` — no strategies reported activity **or** ``data_loaders``
      import failed (optional dependency; legitimate per the audit).
    * ``None`` — ``strategy_dashboard_data()`` raised. The renderer
      surfaces this as "Strategies (today) — data unavailable"
      rather than collapsing to "(none active)".
    * ``List[...]`` populated — normal path.
    """
    try:
        from src.bot.data_loaders import strategy_dashboard_data
    except Exception as exc:  # noqa: BLE001
        logger.warning("hourly_report: data_loaders unavailable: %s", exc)
        return []

    try:
        return strategy_dashboard_data() or []
    except (OSError, RuntimeError, AttributeError) as exc:
        logger.warning("hourly_report: strategy_dashboard_data failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Outcomes — WARN+ events in the last hour from outcomes.jsonl
# ---------------------------------------------------------------------------


def outcomes_in_window(since: datetime) -> Dict[str, Any]:
    """Aggregate WARN+ outcomes from runtime_logs/outcomes.jsonl since ``since``.

    Returns ``{warn_count, error_count, critical_count, top_errors: [(fingerprint, n)...]}``.
    """
    counts = {"warn_count": 0, "error_count": 0, "critical_count": 0}
    fingerprint_counts: Dict[str, int] = {}
    if not OUTCOMES_FILE.exists():
        return {**counts, "top_errors": []}
    try:
        with OUTCOMES_FILE.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ts_str = rec.get("ts") or ""
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < since:
                    continue
                level = rec.get("level") or ""
                if level == "warn":
                    counts["warn_count"] += 1
                elif level == "error":
                    counts["error_count"] += 1
                    key = f"{rec.get('action')}:{rec.get('reason') or rec.get('status')}"
                    fingerprint_counts[key] = fingerprint_counts.get(key, 0) + 1
                elif level == "critical":
                    counts["critical_count"] += 1
                    key = f"{rec.get('action')}:{rec.get('reason') or rec.get('status')}"
                    fingerprint_counts[key] = fingerprint_counts.get(key, 0) + 1
    except OSError as exc:
        logger.warning("hourly_report: outcomes read failed: %s", exc)
    top = sorted(fingerprint_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {**counts, "top_errors": top}


# ---------------------------------------------------------------------------
# Training / ML activity (operator directive 2026-07-08 — the hourly snapshot
# should include anything that happened training-wise in the last hour)
# ---------------------------------------------------------------------------


def _parse_ts(ts: Any) -> Optional[datetime]:
    """Lenient ISO-8601 → aware-UTC datetime; None on anything unparseable."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def training_activity_in_window(since: datetime) -> Dict[str, Any]:
    """Training / ML activity in the last window, from the trainer mirror.

    Reads the file-based trainer mirror the trainer VM rsyncs onto the live box
    (``runtime_logs/trainer_mirror/``) directly — no sidecar rebuild, no
    trader→trainer SSH. Returns a summary of:

      * ``cycles``  — training-cycle rows (ts/status/head) newer than ``since``
      * ``builds``  — dataset-build rows (ts/status/family) newer than ``since``
      * ``latest_sweep_date`` — the newest backtest-sweep date on disk
      * ``trainer_present`` / ``mirror_age_s`` — trainer heartbeat liveness

    Best-effort: a missing mirror (trainer not publishing) returns
    ``present=False`` with empty lists. Never raises.
    """
    out: Dict[str, Any] = {
        "present": False, "cycles": [], "builds": [],
        "latest_sweep_date": None, "trainer_present": False,
        "mirror_age_s": None,
    }
    try:
        mirror = RUNTIME_LOGS / "trainer_mirror"
        if not mirror.is_dir():
            return out
        out["present"] = True

        def _recent(path: Path, fields: tuple) -> List[Dict[str, Any]]:
            rows: List[Dict[str, Any]] = []
            if not path.is_file():
                return rows
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                        except (ValueError, TypeError):
                            continue
                        dt = _parse_ts(r.get("ts") or r.get("timestamp"))
                        if dt is None or dt < since:
                            continue
                        rows.append({k: r.get(k) for k in fields})
            except OSError:
                return rows
            return rows

        out["cycles"] = _recent(mirror / "training_cycle.jsonl",
                                 ("ts", "status", "head"))
        out["builds"] = _recent(mirror / "trainer" / "dataset_builds.jsonl",
                                 ("ts", "status", "family", "version"))

        # Newest backtest-sweep date (dirs named by UTC date).
        sweeps_dir = mirror / "backtests"
        if sweeps_dir.is_dir():
            dates = sorted(
                (p.name for p in sweeps_dir.iterdir() if p.is_dir()),
                reverse=True,
            )
            if dates:
                out["latest_sweep_date"] = dates[0]

        # Trainer heartbeat: the publish timer rsyncs trainer_status.json ~2m.
        status_path = mirror / "trainer_status.json"
        if status_path.is_file():
            try:
                age = (
                    datetime.now(timezone.utc).timestamp()
                    - status_path.stat().st_mtime
                )
                out["mirror_age_s"] = int(age)
                out["trainer_present"] = age <= 1200  # 20 min ≈ 10 missed pubs
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001  # allow-silent: the hourly report must never raise — a trainer-mirror read failure degrades the Training section to "not present", logged at WARNING, never a 5xx/exception
        logger.warning("hourly_report: training activity read failed: %s", exc)
    return out


# ---------------------------------------------------------------------------
# Health (PR2: thin — last-tick freshness + outcome counts)
# ---------------------------------------------------------------------------


def health_summary(
    last_tick_ts: Optional[str],
    outcomes: Dict[str, Any],
    tick_interval_s: int,
    now_utc: datetime,
    health_checks: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Health snapshot.

    PR3 replaced the thin slice with the full set in
    ``src/runtime/health.py``. The legacy fields (``tick_age_s``,
    ``tick_stale``, outcome counts, ``overall``) remain so the renderer
    and tests can keep their existing shape; the new
    ``checks`` field carries the full HealthCheck list.

    ``health_checks`` may be passed in by callers/tests; if omitted,
    ``run_all_checks`` is invoked at call time.
    """
    tick_age_s: Optional[float] = None
    tick_stale = False
    if last_tick_ts:
        try:
            ts = datetime.fromisoformat(last_tick_ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            tick_age_s = (now_utc - ts).total_seconds()
            tick_stale = tick_age_s > 2 * tick_interval_s
        except ValueError:
            pass

    has_critical = (outcomes.get("critical_count") or 0) > 0
    has_error = (outcomes.get("error_count") or 0) > 0

    if health_checks is None:
        try:
            from src.runtime.health import run_all_checks
            health_checks = run_all_checks()
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, AttributeError) as exc:
            # S-067 follow-up D4 — see
            # docs/audits/silent-empty-reporting-2026-05-10.md § Phase-2 #4.
            # Returning [] read as "all checks healthy" downstream.
            # Synthesize an "unknown" sentinel so the Health section
            # surfaces the failure rather than rendering "no critical /
            # no warn". SimpleNamespace avoids needing HealthCheck —
            # its module may itself have failed to import.
            import types
            logger.warning("hourly_report: run_all_checks failed: %s", exc)
            health_checks = [
                types.SimpleNamespace(
                    name="health_checks",
                    status="unknown",
                    detail=(
                        f"run_all_checks failed: {type(exc).__name__}: {exc}. "
                        "See bot.log for the underlying error."
                    ),
                ),
            ]

    checks_critical = any(c.status == "critical" for c in health_checks)
    checks_warn = any(c.status == "warn" for c in health_checks)
    # "unknown" is the D4 sentinel for "we couldn't run the checks at
    # all". It signals warn-level uncertainty (not critical — the
    # checks themselves might have been clean), but the report MUST
    # NOT render as "ok / all checks healthy".
    checks_unknown = any(c.status == "unknown" for c in health_checks)

    overall = "ok"
    if has_critical or tick_stale or checks_critical:
        overall = "degraded"
    elif has_error or checks_warn or checks_unknown:
        overall = "warn"

    return {
        "tick_age_s": tick_age_s,
        "tick_stale": tick_stale,
        "tick_interval_s": tick_interval_s,
        "warn_count": outcomes.get("warn_count", 0),
        "error_count": outcomes.get("error_count", 0),
        "critical_count": outcomes.get("critical_count", 0),
        "checks": health_checks,
        "overall": overall,
    }


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _fmt_money(v: Optional[float], width: int = 0, sign: bool = False) -> str:
    if v is None:
        return "—"
    sym = "+" if (sign and v >= 0) else ""
    s = f"{sym}${v:,.2f}"
    return s.rjust(width) if width else s


def _fmt_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    return f"{h}h{m % 60}m"


def _overall_glyph(overall: str) -> str:
    return {"ok": "OK", "warn": "WARN", "degraded": "DEGRADED"}.get(
        overall, overall.upper()
    )


def _build_strategy_sections(
    *,
    ticks: Dict[str, Any],
    strategies: Optional[List[Dict[str, Any]]],
    outcomes: Dict[str, Any],
    health: Dict[str, Any],
):
    """Sections for the strategy-focused hourly report.

    Each section's *summary* is the short headline the operator sees
    at-a-glance ("Performance — 3 errors in past hour"). The *body*
    is the detail collapsed inside an expandable blockquote.

    ``strategies is None`` is the "data unavailable" sentinel from
    ``strategy_snapshots`` (S-067 follow-up D3). Render an explicit
    "data unavailable" Strategies section rather than collapsing to
    "(none active)".
    """
    from src.units.ui.telegram_format import Section, bullet_list, kv_block

    perf_summary = (
        f"Performance — {ticks['ticks_ok']} ok / {ticks['ticks_err']} errored"
    )
    sigs = ticks["signals_by_strategy"]
    perf_rows = [
        ("Ticks ok", ticks["ticks_ok"]),
        ("Ticks errored", ticks["ticks_err"]),
        ("Signals fired", ticks["signals_total"]),
    ]
    if sigs:
        perf_rows.append(("Signals by strategy",
                          ", ".join(f"{k}×{v}" for k, v in sorted(sigs.items()))))

    if strategies is None:
        strat_section = Section(
            summary="Strategies (today) — data unavailable",
            body=(
                "strategy_dashboard_data() raised — see bot.log for "
                "the underlying error. Per-strategy signals/open/PnL "
                "could not be loaded for this report cycle."
            ),
            priority=20,
        )
    else:
        strat_lines = []
        for s in strategies:
            name = s.get("strategy") or "unknown"
            sigs_today = s.get("signals_today") or 0
            pnl = s.get("pnl")
            opn = s.get("open_pos") or 0
            strat_lines.append(
                f"{name}: {sigs_today} signals, {opn} open, "
                f"PnL {_fmt_money(pnl, sign=True)}"
            )
        strat_section = Section(
            summary=f"Strategies (today) — {len(strategies)} active",
            body=bullet_list(strat_lines, empty="(none active)"),
            priority=20,
        )

    err_summary = (
        f"Errors — {health['critical_count']} critical, "
        f"{health['error_count']} error, {health['warn_count']} warn"
    )
    err_rows = [
        ("Critical", health["critical_count"]),
        ("Error",    health["error_count"]),
        ("Warn",     health["warn_count"]),
    ]
    err_body_lines = [kv_block(err_rows)]
    if outcomes.get("top_errors"):
        err_body_lines.append("")
        err_body_lines.append("Top fingerprints:")
        for fp, n in outcomes["top_errors"][:5]:
            err_body_lines.append(f"- {fp} ({n}x)")

    health_summary = f"Health — {_overall_glyph(health['overall'])}"
    health_lines = [
        f"Last tick: {_fmt_age(health['tick_age_s'])} ago "
        f"(expected <= {health['tick_interval_s'] // 60}m)"
        + ("  STALE" if health["tick_stale"] else ""),
    ]
    for c in (health.get("checks") or []):
        marker = {"ok": "OK", "warn": "WARN", "critical": "CRIT",
                  "unknown": "UNK"}.get(
            getattr(c, "status", "?"), "?"
        )
        health_lines.append(
            f"[{marker}] {getattr(c, 'name', '?')}: "
            f"{getattr(c, 'detail', '')}"
        )

    return [
        Section(summary=perf_summary, body=kv_block(perf_rows), priority=10),
        strat_section,
        Section(
            summary=err_summary, body="\n".join(err_body_lines), priority=30,
        ),
        Section(
            summary=health_summary, body="\n".join(health_lines), priority=40,
        ),
    ]


def _build_training_section(training: Optional[Dict[str, Any]]):
    """Collapsible Training / ML section for the hourly report.

    Summarises what happened training-wise in the last hour (cycles run,
    dataset builds, newest backtest sweep) + trainer liveness. Rendered as an
    expandable blockquote like the other sections. Absent trainer mirror →
    an explicit "trainer mirror not present" body rather than silence.
    """
    from src.units.ui.telegram_format import Section

    t = training or {}
    cycles = t.get("cycles") or []
    builds = t.get("builds") or []
    sweep = t.get("latest_sweep_date")

    if not t.get("present"):
        return Section(
            summary="Training / ML — mirror not present",
            body=("No trainer_mirror/ on this host — the trainer VM has not "
                  "published (or the mirror path is missing)."),
            priority=25,
        )

    trainer_glyph = "🟢" if t.get("trainer_present") else "🔴"
    age = t.get("mirror_age_s")
    age_str = f"{age // 60}m" if isinstance(age, int) else "?"
    summary = (
        f"Training / ML — {len(cycles)} cycle(s), {len(builds)} build(s) in "
        f"the last hour · trainer {trainer_glyph}"
    )

    lines = [f"Trainer heartbeat: {trainer_glyph} (mirror age {age_str})"]
    if cycles:
        lines.append("")
        lines.append("Training cycles this hour:")
        for c in cycles[:10]:
            lines.append(
                f"- {c.get('ts')}: {c.get('status') or '?'} "
                f"[{c.get('head') or 'n/a'}]"
            )
    if builds:
        lines.append("")
        lines.append("Dataset builds this hour:")
        for b in builds[:10]:
            lines.append(
                f"- {b.get('ts')}: {b.get('status') or '?'} "
                f"{b.get('family') or ''} {b.get('version') or ''}".rstrip()
            )
    if not cycles and not builds:
        lines.append("")
        lines.append("No training cycles or dataset builds in the last hour.")
    if sweep:
        lines.append("")
        lines.append(f"Newest backtest sweep on disk: {sweep}")

    return Section(
        summary=summary,
        body="\n".join(lines),
        priority=25,
    )


def _build_account_sections(
    *,
    trades: Dict[str, Any],
    accounts: Optional[List[Dict[str, Any]]],
):
    """Sections for the accounts-focused hourly report.

    Mirrors the strategy report but groups detail by account: per-
    account balance / delta / open positions, and the trades placed +
    closed in the last hour with realized PnL.

    ``accounts is None`` is the "data unavailable" sentinel from
    ``account_snapshots`` (S-067 follow-up D2). Render an explicit
    "data unavailable" section rather than collapsing to "no accounts
    configured".
    """
    from src.units.ui.telegram_format import Section, bullet_list

    placed = trades.get("placed", [])
    closed = trades.get("closed", [])

    # 1. Trades section
    placed_lines = [
        f"{t.get('strategy_name') or '?'} {t.get('symbol')} "
        f"{t.get('direction')} qty={t.get('position_size')} "
        f"@ {t.get('entry_price')}"
        for t in placed
    ]
    closed_lines = [
        f"{t.get('strategy_name') or '?'} {t.get('symbol')} "
        f"{t.get('direction')} entry={t.get('entry_price')} "
        f"exit={t.get('exit_price')} pnl="
        f"{_fmt_money(t.get('pnl'), sign=True)}"
        for t in closed
    ]
    trades_summary = (
        f"Trades — {len(placed)} placed / {len(closed)} closed / "
        f"realized {_fmt_money(trades.get('realized_pnl'), sign=True)}"
    )
    trades_body_lines = []
    if placed:
        trades_body_lines.append("Placed:")
        trades_body_lines.extend(f"- {ln}" for ln in placed_lines)
    if closed:
        if trades_body_lines:
            trades_body_lines.append("")
        trades_body_lines.append("Closed:")
        trades_body_lines.extend(f"- {ln}" for ln in closed_lines)
    if not trades_body_lines:
        trades_body_lines.append("(no trades in window)")

    # 2. Per-account section
    if accounts is None:
        return [
            Section(summary=trades_summary,
                    body="\n".join(trades_body_lines), priority=10),
            Section(
                summary="Accounts — data unavailable",
                body=(
                    "list_accounts() raised — see bot.log for the "
                    "underlying error. Per-account balance and open-"
                    "position counts could not be loaded for this "
                    "report cycle."
                ),
                priority=20,
            ),
        ]

    acct_lines = []
    api_errors = 0
    for a in accounts:
        aid = a["account_id"]
        if not a.get("api_ok"):
            acct_lines.append(f"{aid}: API ERROR")
            api_errors += 1
            continue
        bal = _fmt_money(a["balance"])
        delta = (
            _fmt_money(a["delta_1h"], sign=True)
            if a["delta_1h"] is not None
            else "(no prev)"
        )
        op = a["open_positions"]
        op_str = f"open {op}" if isinstance(op, int) else "open ?"
        acct_lines.append(
            f"{aid}: bal {bal} | 1h {delta} | {op_str} | API OK"
        )
    accounts_summary = (
        f"Accounts — {len(accounts)} configured"
        + (f" / {api_errors} API errors" if api_errors else "")
    )

    return [
        Section(summary=trades_summary,
                body="\n".join(trades_body_lines), priority=10),
        Section(summary=accounts_summary,
                body=bullet_list(acct_lines, empty="(no accounts configured)"),
                priority=20),
    ]


def render_strategy_report(report: Dict[str, Any]) -> str:
    """Render the strategy-focused hourly report (HTML, collapsable)."""
    from src.units.ui.telegram_format import render_html

    now: datetime = report["now_utc"]
    health = report["health"]
    glyph = _overall_glyph(health["overall"])
    sections = _build_strategy_sections(
        ticks=report["ticks"],
        strategies=report["strategies"],
        outcomes=report["outcomes"],
        health=health,
    )
    # Training / ML section (operator directive 2026-07-08) — folded into the
    # strategy-focused hourly snapshot so the hour's training activity rides
    # the same message. `.get` keeps older report dicts (no training key) safe.
    sections.append(_build_training_section(report.get("training")))
    footer = {
        "ok": "All systems normal",
        "warn": "WARN: errors logged but no critical issues",
        "degraded": "ACTION NEEDED: see Errors / Health sections",
    }.get(health["overall"], "")
    return render_html(
        header=f"[{glyph}] Strategies — {now.strftime('%Y-%m-%d %H:00 UTC')}",
        sections=sections,
        footer=footer,
    )


def render_accounts_report(report: Dict[str, Any]) -> str:
    """Render the accounts-focused hourly report (HTML, collapsable)."""
    from src.units.ui.telegram_format import render_html

    now: datetime = report["now_utc"]
    health = report["health"]
    glyph = _overall_glyph(health["overall"])
    sections = _build_account_sections(
        trades=report["trades"], accounts=report["accounts"],
    )
    return render_html(
        header=f"[{glyph}] Accounts — {now.strftime('%Y-%m-%d %H:00 UTC')}",
        sections=sections,
    )


def render_report(report: Dict[str, Any]) -> str:
    """Back-compat: return the strategy-focused HTML rendering.

    Pre-S-telegram-format callers (``main.py``, ``/hourly`` command,
    tests) called ``render_report(..)`` and got a single plain-text
    string covering everything. The accounts-focused half now lives
    in its own renderer so the two can ride the hourly cycle as
    parallel messages — see ``render_accounts_report``.

    The legacy plain-text shape is preserved by
    ``render_report_plain`` for callers that target ``parse_mode=None``.
    """
    return render_strategy_report(report)


def render_report_plain(report: Dict[str, Any]) -> str:
    """Plain-text rendering of the combined hourly report.

    Kept so callers that don't want HTML (legacy ``send_scheduled``
    path with ``parse_mode=None``) still get a usable summary. The
    body is the strategy + account sections expanded inline.
    """
    from src.units.ui.telegram_format import render_plain

    now: datetime = report["now_utc"]
    health = report["health"]
    glyph = _overall_glyph(health["overall"])
    sections = _build_strategy_sections(
        ticks=report["ticks"],
        strategies=report["strategies"],
        outcomes=report["outcomes"],
        health=health,
    ) + [_build_training_section(report.get("training"))] + _build_account_sections(
        trades=report["trades"], accounts=report["accounts"],
    )
    footer = {
        "ok": "All systems normal",
        "warn": "WARN: errors logged but no critical issues",
        "degraded": "ACTION NEEDED: see Errors / Health sections",
    }.get(health["overall"], "")
    return render_plain(
        header=f"[{glyph}] Hourly Report - {now.strftime('%Y-%m-%d %H:00 UTC')}",
        sections=sections,
        footer=footer,
    )


# ---------------------------------------------------------------------------
# Top-level assembler
# ---------------------------------------------------------------------------


def assemble_hourly_data(
    *,
    now_utc: Optional[datetime] = None,
    tick_interval_s: int = 900,
) -> Dict[str, Any]:
    """Run the four data-gathering passes and return the assembled dict.

    Both the strategy-focused and account-focused renderers consume
    the same shape, so the data sweep runs once per hourly cycle.
    Returns the dict ready for ``render_strategy_report`` /
    ``render_accounts_report``. Never raises.
    """
    now = now_utc or datetime.now(timezone.utc)
    since = now - timedelta(hours=1)
    audit_records = _load_audit_lines_since(since)
    ticks = summarize_ticks(audit_records)
    trades = trades_in_window(since)
    accounts = account_snapshots()
    strategies = strategy_snapshots()
    outcomes = outcomes_in_window(since)
    training = training_activity_in_window(since)
    health = health_summary(
        last_tick_ts=ticks["last_tick_ts"],
        outcomes=outcomes,
        tick_interval_s=tick_interval_s,
        now_utc=now,
    )
    return {
        "now_utc": now,
        "ticks": ticks,
        "trades": trades,
        "accounts": accounts,
        "strategies": strategies,
        "outcomes": outcomes,
        "training": training,
        "health": health,
    }


def build_hourly_report(
    *,
    now_utc: Optional[datetime] = None,
    tick_interval_s: int = 900,
) -> str:
    """Assemble + render the strategy-focused hourly report.

    Back-compat: callers (e.g. ``/hourly`` command,
    ``main.py`` legacy path) get the strategy view as a single
    HTML-formatted string. To get the parallel accounts view, use
    ``build_accounts_hourly_report``.
    """
    try:
        return render_strategy_report(
            assemble_hourly_data(
                now_utc=now_utc, tick_interval_s=tick_interval_s,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("hourly_report.build_hourly_report failed: %s", exc)
        ts = (now_utc or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:00 UTC")
        return (
            f"[WARN] Hourly Report - {ts}\n"
            f"Report assembly failed: {type(exc).__name__}: {exc}\n"
            f"Check runtime_logs/ for details."
        )


def build_accounts_hourly_report(
    *,
    now_utc: Optional[datetime] = None,
    tick_interval_s: int = 900,
) -> str:
    """Render the parallel account-focused hourly report.

    The operator wanted two recurring messages per hour: one for
    strategies (signals fired, errors, health) and one for accounts
    (trades placed/closed in the last hour, per-account balance +
    open positions). This is the second.
    """
    try:
        return render_accounts_report(
            assemble_hourly_data(
                now_utc=now_utc, tick_interval_s=tick_interval_s,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "hourly_report.build_accounts_hourly_report failed: %s", exc,
        )
        ts = (now_utc or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:00 UTC")
        return (
            f"[WARN] Accounts Hourly Report - {ts}\n"
            f"Report assembly failed: {type(exc).__name__}: {exc}\n"
            f"Check runtime_logs/ for details."
        )
