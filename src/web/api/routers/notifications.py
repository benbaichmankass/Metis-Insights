"""System notifications banner (operator-requested 2026-07-08).

`GET /api/bot/notifications` — one Tier-1, read-only, connection-free surface
the SPA polls to render a **banner at the top of the
Overview page** for the important, can't-miss conditions (not routine pings):

  * **trainer_down** (severity ``alert``) — the trainer VM's 2-min mirror
    heartbeat has gone stale (SSH-dead / OOM-hung). From
    ``trainer_reachability_alert.status()``.
  * **account_down** (severity ``alert``) — a declared-live broker account is
    reading unreachable (IB gateway logged out, exchange API 401-ing). One
    banner per latched-down account, from
    ``account_reachability_alert.down_accounts()``.
  * **prop_fills_stale** (severity ``alert``) — the prop journal is missing
    trades the venue already took: either a balance that moved between two
    operator reports with no fills reported in between, or a bracket already
    announced as crossed whose position is still open in the journal. Never
    fires on an unacted ticket — that is the expected shape on a manual bridge.
    From ``prop_fills_staleness.stale_fill_accounts()``.
  * **trade_open** (severity ``info``) — a compact "recently opened trades"
    notice (best-effort, last ``TRADE_OPEN_BANNER_WINDOW_MIN`` minutes), so a
    fresh entry surfaces on the banner too. Never fails the endpoint.

Response::

    {
      "generated_at": "<iso>",
      "count": <int>,
      "has_alerts": <bool>,           # any severity=="alert" present
      "banners": [
        {"severity": "alert|warning|info", "kind": "...",
         "message": "<short>", "detail": "<longer|null>", "since": "<iso|null>"}
      ]
    }

Severity ordering in ``banners``: alert first, then warning, then info — so a
consumer can render the top-most as the prominent banner. Best-effort: any
source failure is swallowed and simply omits that kind's banner; the endpoint
never 5xxs. See ``docs/api-tier-policy.md`` Tier 1.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from src.utils.paths import runtime_logs_dir, trade_journal_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["notifications"])

_SEVERITY_RANK = {"alert": 0, "warning": 1, "info": 2}


def _trainer_banner() -> Optional[Dict[str, Any]]:
    try:
        from src.runtime.trainer_reachability_alert import status as trainer_status
        st = trainer_status()
        if not st.get("down"):
            return None
        age = st.get("age_seconds")
        if age is None:
            detail = "No trainer_status.json in the mirror — the trainer has not published (or the mirror is missing)."
        else:
            detail = f"Trainer mirror stale ~{int(age // 60)}m (the 2-min publish heartbeat has stopped)."
        return {
            "severity": "alert",
            "kind": "trainer_down",
            "message": "Trainer VM is DOWN — ML training has stalled.",
            "detail": detail
            + " Live shadow/advisory inference is unaffected; probable OCI-console reboot needed.",
            "since": st.get("since"),
        }
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: trainer banner failed: %s", exc)
        return None


# --- Trainer disk thresholds -------------------------------------------------
# CHOSEN, NOT MEASURED. Declared here rather than derived, because exactly ONE
# reading of this disk exists (2026-08-20, trainer-vm-diag #10057: 45G total,
# 3.2G free, 94% used, `datasets-out/` alone 12G) and one point is not a
# distribution. Shipping a threshold with no distribution behind it is the
# exposure-ceiling mistake (`gross-exposure-governance-DESIGN.md` § 6/§ 7) —
# so these are stated as a choice, not passed off as calibration, and
# `BL-20260820-TRAINER-DISK-THRESHOLDS-UNCALIBRATED` carries the calibration
# path (accumulate the published series, then set the warn level ABOVE the
# free-space a dataset build actually needs and BELOW the point a build fails).
#
# Absolute GB, deliberately not a percentage: the operational question is
# "can the next dataset build complete?", and that is a size in GB, not a
# fraction of whatever volume the trainer happens to have been provisioned
# with. A percentage would mean different things on a 45G and a 450G disk.
_DISK_FREE_ALERT_GB = 2.0
_DISK_FREE_WARN_GB = 5.0


def _trainer_disk_banner(trainer_down: bool) -> Optional[Dict[str, Any]]:
    """Trainer-VM disk pressure, read from the SAME mirror `trainer_down` reads.

    Why this exists: nothing anywhere published a trainer disk metric. Verified
    three ways on 2026-08-20 — `publish_trainer_mirror.sh` emitted no disk field,
    the live `/api/bot/ml/status` payload carried 12 keys and none was disk, and
    `src/runtime/health.py::check_disk` exists but runs on the LIVE trader. So a
    trainer at 94% was invisible to every surface the operator reads.

    Four states, never collapsed — *"we did not look"* is not *"the disk is
    fine"*, which is the whole point of the banner:

      * ``not_published``  — the mirror carries no ``disk`` block at all (a
        trainer running a publish script older than the writer). We did not look.
      * ``measure_failed`` — the writer ran and ``shutil.disk_usage`` raised;
        the reason travels with it. We looked and could not see.
      * ``ok``             — measured, above both thresholds. No banner.
      * ``low``            — measured, at or below a threshold. The finding.

    The first two surface as one ``info`` banner (kind ``trainer_disk_unknown``)
    rather than silence, because a disk fact that is missing reads exactly like
    a disk fact that is healthy, and that equivalence is the bug. They are one
    KIND but keep distinct ``detail`` text, so the payload never collapses them.

    Suppressed entirely when the trainer is DOWN: a stale mirror has no current
    disk fact to report, and `trainer_down` already owns that condition — two
    banners for one cause is the desensitized-alarm pattern this repo calls a P1.

    NOT registered with `collapsed-state-guard`, deliberately, and the reason is
    recorded rather than left as a silent omission. That guard binds a PRODUCER
    field to its consumers, and only two of the three states here are producer-
    observable: the writer can emit `measured:true` or `measured:false`+reason,
    but `not_published` is by construction the absence of the writer — nothing
    can emit it. Registering the contract would report full coverage of a
    three-state field while enforcing two, which is worse than no registration.
    Adding a redundant `state:` string to the payload to satisfy the guard would
    give the same fact two sources of truth and invite exactly the drift this
    repo keeps paying for. The three states are enforced instead by
    `tests/test_trainer_disk_visibility.py`, whose state assertions were each
    verified to fail under a surgical break (collapsing `not_published` to
    healthy fails exactly 2 of 17 tests; removing the DOWN suppression fails
    exactly 1) — a discriminating control, not merely an import error.
    """
    if trainer_down:
        return None
    try:
        from src.web.api.routers.training_center import _mirror_root, _read_json

        payload = _read_json(_mirror_root() / "trainer_status.json")
        if not isinstance(payload, dict):
            # No mirror at all. Not this banner's business — either the trainer
            # has never published (there is nothing to grade) or it is down and
            # the caller already suppressed us. Never report "disk unknown" for
            # a trainer we have no contact with.
            return None

        disk = payload.get("disk")
        if not isinstance(disk, dict):
            return {
                "severity": "info",
                "kind": "trainer_disk_unknown",
                "message": "Trainer disk usage is NOT being reported.",
                "detail": (
                    "The trainer is publishing (its mirror is fresh) but its "
                    "status payload carries no `disk` block — so it is running a "
                    "publish script older than the disk writer. This is 'we did "
                    "not look', NOT 'the disk is fine'. Redeploy the trainer to "
                    "pick up scripts/ops/publish_trainer_mirror.sh."
                ),
                "since": payload.get("ts"),
            }

        if not disk.get("measured"):
            return {
                "severity": "info",
                "kind": "trainer_disk_unknown",
                "message": "Trainer disk usage could not be measured.",
                "detail": (
                    f"The trainer ran the disk check and it failed: "
                    f"{disk.get('reason') or 'no reason recorded'} "
                    f"(path {disk.get('path') or 'unknown'}). This is 'we looked "
                    f"and could not see', NOT 'the disk is fine'."
                ),
                "since": payload.get("ts"),
            }

        free_gb = disk.get("free_gb")
        if not isinstance(free_gb, (int, float)):
            # measured:true with an unreadable figure — still not a clean read.
            return {
                "severity": "info",
                "kind": "trainer_disk_unknown",
                "message": "Trainer disk reading is unusable.",
                "detail": (
                    f"The mirror reports measured:true but free_gb is "
                    f"{free_gb!r}, which cannot be graded against a threshold."
                ),
                "since": payload.get("ts"),
            }

        if free_gb > _DISK_FREE_WARN_GB:
            return None

        severity = "alert" if free_gb <= _DISK_FREE_ALERT_GB else "warning"
        used_pct = disk.get("used_pct")
        used_txt = f"{used_pct}% used" if isinstance(used_pct, (int, float)) else "usage unknown"
        return {
            "severity": severity,
            "kind": "trainer_disk_low",
            "message": f"Trainer VM disk is low: {free_gb} GB free ({used_txt}).",
            "detail": (
                f"Threshold {_DISK_FREE_ALERT_GB} GB (alert) / {_DISK_FREE_WARN_GB} GB "
                f"(warning) — both CHOSEN, not calibrated "
                f"(BL-20260820-TRAINER-DISK-THRESHOLDS-UNCALIBRATED). A full trainer "
                f"disk stops dataset builds and training cycles, which the trainer's "
                f"own systemd state will still report as green. Note the dataset "
                f"garbage collector is NOT the remedy: measured 2026-08-20 it "
                f"reclaims 0.09 GB of 115 version dirs (111 held by 41 manifest "
                f"pins). See BL-20260820-TRAINER-DATASET-GC-HAS-NO-CALLER."
            ),
            "since": payload.get("ts"),
        }
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: trainer disk banner failed: %s", exc)
        return None


def _account_down_banners() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from src.runtime.account_reachability_alert import down_accounts
        for aid, st in (down_accounts() or {}).items():
            out.append({
                "severity": "alert",
                "kind": "account_down",
                "message": f"Broker account DOWN: {aid}",
                "detail": "Reading unreachable — trades on this account may be unprotected or going dark.",
                "since": (st or {}).get("last_change"),
            })
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: account-down banners failed: %s", exc)
    return out


def _prop_fills_stale_banners() -> List[Dict[str, Any]]:
    """One banner per latched prop fills-staleness finding.

    The read half of :mod:`src.prop.prop_fills_staleness`. It exists here rather
    than only as a review-skill accessor because the condition — *the prop
    journal is missing trades the venue already took* — is exactly the
    can't-miss shape this feed is for, and because a latch nothing renders is
    the written-and-never-read failure this repo keeps paying for.

    ⚠️ It reports **unrecorded closes**, never unacted tickets: an unanswered
    ticket is the expected shape on a manual bridge (operator, 2026-08-23) and
    a banner for it would be the desensitized-alarm P1.
    """
    out: List[Dict[str, Any]] = []
    try:
        from src.prop.prop_fills_staleness import stale_fill_accounts
        for aid, findings in (stale_fill_accounts() or {}).items():
            for f in (findings or {}).values():
                if not isinstance(f, dict):
                    continue
                if f.get("kind") == "balance_moved_unreported":
                    delta = f.get("delta")
                    amount = "an unexplained amount" if delta is None else f"${abs(float(delta)):,.2f}"
                    message = f"Prop journal is missing trades: {aid}"
                    detail = (
                        f"Balance moved by {amount} between two reports with no "
                        "fills reported in between — closes happened that the "
                        "journal has no record of."
                    )
                else:
                    message = f"Prop trade may have closed unrecorded: {aid}"
                    detail = (
                        f"{f.get('symbol')} {f.get('direction')} — its "
                        f"{str(f.get('level') or '').upper()} was crossed "
                        f"{f.get('hours_since_crossing')}h ago and the journal "
                        "still has it open."
                    )
                out.append({
                    "severity": "alert",
                    "kind": "prop_fills_stale",
                    "message": message,
                    "detail": detail,
                    "since": f.get("crossed_at") or f.get("window_end"),
                })
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: prop fills-stale banners failed: %s", exc)
    return out


def _trade_open_window_min() -> int:
    try:
        n = int(os.environ.get("TRADE_OPEN_BANNER_WINDOW_MIN", "30"))
        return n if n > 0 else 30
    except (TypeError, ValueError):
        return 30


def _recent_trade_open_banner() -> Optional[Dict[str, Any]]:
    """Best-effort: a compact 'recently opened trades' info banner.

    Reads real-money + paper open, non-backtest rows opened within the window.
    Connection-free (read-only DB). Any failure → no banner (never raises).
    """
    try:
        window_min = _trade_open_window_min()
        db = trade_journal_db_path()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
        try:
            con.row_factory = sqlite3.Row
            cols = {r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()}
            if "status" not in cols:
                return None
            open_ts_col = next(
                (c for c in ("timestamp", "created_at", "opened_at") if c in cols),
                None,
            )
            sym_col = "symbol" if "symbol" in cols else None
            strat_col = next(
                (c for c in ("setup_type", "strategy", "pattern") if c in cols), None
            )
            if not open_ts_col or not sym_col:
                return None
            bt = "AND COALESCE(is_backtest, 0) = 0" if "is_backtest" in cols else ""
            rows = con.execute(
                f"SELECT {sym_col} AS symbol, {open_ts_col} AS opened_at"
                f"{(', ' + strat_col + ' AS strategy') if strat_col else ''} "
                f"FROM trades WHERE status = 'open' {bt} "
                f"ORDER BY {open_ts_col} DESC LIMIT 25"
            ).fetchall()
        finally:
            con.close()

        now = datetime.now(timezone.utc)
        recent: List[sqlite3.Row] = []
        for r in rows:
            dt = _parse_ts(r["opened_at"])
            if dt is not None and (now - dt).total_seconds() <= window_min * 60:
                recent.append(r)
        if not recent:
            return None
        syms = []
        for r in recent:
            s = r["symbol"]
            if s and s not in syms:
                syms.append(s)
        n = len(recent)
        head = f"{n} trade{'s' if n != 1 else ''} opened in the last {window_min}m"
        return {
            "severity": "info",
            "kind": "trade_open",
            "message": f"{head}: {', '.join(syms[:6])}" + (" …" if len(syms) > 6 else ""),
            "detail": None,
            "since": None,
        }
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: recent-trade-open banner failed: %s", exc)
        return None


def _orphan_unreconciled_banner() -> Optional[Dict[str, Any]]:
    """Best-effort: real-money orphaned rows awaiting reconciliation.

    An orphaned row is in NEITHER the Positions view (``status='open'``)
    NOR the Trades view (``status='closed'``) — without this banner it is
    invisible to both apps, which is exactly how the bybit_2 BTC pair
    (trades 3171/3088) went unnoticed from 2026-07-06 to 2026-07-13
    (BL-20260713-BYBIT2-BTC-ORPHANS-UNRECONCILED). Real-money only —
    paper orphans are journal hygiene, not a can't-miss condition.
    Connection-free (read-only DB). Any failure → no banner.
    """
    try:
        db = trade_journal_db_path()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
        try:
            con.row_factory = sqlite3.Row
            cols = {r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()}
            if "status" not in cols or "reconcile_status" not in cols:
                return None
            real_pred = (
                "AND account_class = 'real_money'"
                if "account_class" in cols
                else "AND COALESCE(is_demo, 0) = 0"
            )
            bt = "AND COALESCE(is_backtest, 0) = 0" if "is_backtest" in cols else ""
            rows = con.execute(
                "SELECT id, symbol, account_id, direction FROM trades "
                "WHERE status = 'orphaned' "
                "AND COALESCE(reconcile_status, 'unreconciled') = 'unreconciled' "
                # An investigated orphan (reconcile_orphan_history stamped
                # reconcile_investigated_at + no_recoverable_order_package)
                # is the honest terminal state for pre-package-era rows —
                # permanently un-clearable, so alerting on it forever would
                # be noise. The banner covers NEW/uninvestigated orphans.
                "AND (notes IS NULL OR notes NOT LIKE '%reconcile_investigated_at%') "
                f"{real_pred} {bt} ORDER BY id DESC LIMIT 50"
            ).fetchall()
        finally:
            con.close()
        if not rows:
            return None
        n = len(rows)
        sample = ", ".join(
            f"#{r['id']} {r['account_id']}/{r['symbol']}" for r in rows[:3]
        )
        return {
            "severity": "alert",
            "kind": "orphan_unreconciled",
            "message": (
                f"{n} real-money orphaned trade{'s' if n != 1 else ''} awaiting "
                "reconciliation (invisible in Positions/Trades)"
            ),
            "detail": (
                f"{sample}{' …' if n > 3 else ''} — reconcile via the "
                "backfill-orphan-pnl / reconcile-orphan-history operator actions"
            ),
            "since": None,
        }
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: orphan-unreconciled banner failed: %s", exc)
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO or epoch-ms/epoch-s trade timestamp to aware UTC. None on failure."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            secs = float(value)
            if secs > 1e11:  # epoch ms
                secs /= 1000.0
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        s = str(value).strip()
        if not s:
            return None
        if s.isdigit():
            secs = float(s)
            if secs > 1e11:
                secs /= 1000.0
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001  # allow-silent: best-effort ts parse — unparseable timestamp yields no banner, never raises
        return None


def _warning_window_min() -> int:
    try:
        n = int(os.environ.get("NOTIF_WARNING_WINDOW_MIN", "60"))
        return n if n > 0 else 60
    except (TypeError, ValueError):
        return 60


_WARN_MAX_BANNERS = 3
# BL-20260813-OPERATOR-WARNING-BANNER-CANNOT-MATCH-WARN. "WARN" is the string
# the producer ACTUALLY writes: src/runtime/outcomes.py persists
# ``record["level"] = level.value`` and ``Level.WARN.value == "warn"``. The
# enum has exactly four members — info / warn / error / critical — and NO
# "WARNING" member, so the original set could never match a single WARN row:
# "warn".upper() == "WARN", which was absent here. Every Level.WARN outcome
# (20 call sites in src/) was persisted to outcomes.jsonl by _PERSIST_LEVELS
# and then silently dropped by this filter — the whole middle tier of the
# banner contract, gone, while ERROR/CRITICAL kept working and made the
# feature look healthy.
#
# "WARNING" is KEPT, not replaced: it costs nothing, and a set that accepts
# only the spelling one particular producer happens to use is how this broke.
# Match on the VALUE the producer writes, and do not re-derive that value from
# the docs — CLAUDE.md's banner row says "WARNING" too, and it is describing a
# level string that does not exist.
_WARN_LEVELS = {"WARN", "WARNING", "ERROR", "CRITICAL"}


def _tail_lines(path: Any, max_bytes: int = 65536) -> List[str]:
    """Return the trailing lines of a text file (last ``max_bytes``)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the partial first line
            data = fh.read()
        return data.decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _recent_warning_banners() -> List[Dict[str, Any]]:
    """Recent operator WARN+ outcomes as banners.

    The "certainly anything that's a warning" half of the operator's banner ask
    (2026-07-08): surface the last hour's persisted ``outcomes.jsonl`` rows at
    level WARNING/ERROR/CRITICAL (the same feed that Telegrams ERROR/CRITICAL) —
    so a live operational condition like a stuck position-close ("Position CLOSE
    failing — won't flatten") shows on the app banner, not only in Telegram.
    CRITICAL/ERROR → ``alert``, WARNING → ``warning``. Deduped (a per-tick
    repeat like a close-retry collapses to one banner), newest-first, capped.
    Best-effort — a missing/garbled log yields no banners, never raises.
    """
    out: List[Dict[str, Any]] = []
    try:
        path = runtime_logs_dir() / "outcomes.jsonl"
        if not path.is_file():
            return out
        cutoff = datetime.now(timezone.utc).timestamp() - _warning_window_min() * 60
        seen: set = set()
        rows: List[tuple] = []
        for line in _tail_lines(path):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except (ValueError, TypeError):
                continue
            lvl = str(r.get("level") or "").upper()
            if lvl not in _WARN_LEVELS:
                continue
            dt = _parse_ts(r.get("ts"))
            if dt is None or dt.timestamp() < cutoff:
                continue
            reason = str(r.get("reason") or r.get("action") or "").strip()
            if not reason:
                continue
            # Dedup a per-tick repeat: normalise digits (e.g. "failures: 3/4/5").
            key = (lvl, re.sub(r"\d+", "#", reason)[:80])
            if key in seen:
                continue
            seen.add(key)
            rows.append((dt, lvl, reason, r.get("action"), r.get("status")))
        rows.sort(key=lambda x: x[0], reverse=True)
        for dt, lvl, reason, action, status in rows[:_WARN_MAX_BANNERS]:
            sev = "alert" if lvl in ("ERROR", "CRITICAL") else "warning"
            msg = reason if len(reason) <= 160 else reason[:157] + "…"
            detail = None
            if action:
                detail = f"{action}" + (f" · {status}" if status else "")
            out.append({
                "severity": sev,
                "kind": "operator_warning",
                "message": msg,
                "detail": detail,
                "since": dt.isoformat(),
            })
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: operator-warning banners failed: %s", exc)
    return out


def _operator_alert_banners() -> List[Dict[str, Any]]:
    """Recent durable operator alerts as banners.

    The trader's ``execution_diagnostics.enqueue_*`` alerts (stuck close, orphan
    flag, failed dispatch, …) Telegram via transient pending-ping files that the
    sender consumes + deletes — so they can't back this banner. Every such alert
    now ALSO appends a structured row to ``runtime_logs/operator_alerts.jsonl``
    (a bounded ring); this reads its recent tail so a live operational condition
    — e.g. the ``alpaca_paper`` QQQ "Position CLOSE failing — won't flatten" —
    surfaces on the Overview banner, not only in Telegram. ``priority=="critical"``
    → ``alert``, anything else → ``warning``. Deduped by (kind, digit-normalised
    first line) so a per-tick close-retry collapses to one banner; newest-first,
    capped. Best-effort — a missing/garbled log yields no banners, never raises.
    """
    out: List[Dict[str, Any]] = []
    try:
        path = runtime_logs_dir() / "operator_alerts.jsonl"
        if not path.is_file():
            return out
        cutoff = datetime.now(timezone.utc).timestamp() - _warning_window_min() * 60
        seen: set = set()
        rows: List[tuple] = []
        for line in _tail_lines(path):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except (ValueError, TypeError):
                continue
            dt = _parse_ts(r.get("ts"))
            if dt is None or dt.timestamp() < cutoff:
                continue
            body = str(r.get("body") or "").strip()
            if not body:
                continue
            kind = str(r.get("kind") or "operator_alert").strip() or "operator_alert"
            body_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            head = body_lines[0] if body_lines else body
            key = (kind, re.sub(r"\d+", "#", head)[:80])
            if key in seen:
                continue
            seen.add(key)
            prio = str(r.get("priority") or "high").lower()
            rows.append((dt, kind, prio, head, body_lines[1:]))
        rows.sort(key=lambda x: x[0], reverse=True)
        for dt, kind, prio, head, rest in rows[:_WARN_MAX_BANNERS]:
            sev = "alert" if prio == "critical" else "warning"
            msg = head if len(head) <= 160 else head[:157] + "…"
            detail = " · ".join(rest[:4]) or None
            if detail and len(detail) > 300:
                detail = detail[:297] + "…"
            out.append({
                "severity": sev,
                "kind": kind,
                "message": msg,
                "detail": detail,
                "since": dt.isoformat(),
            })
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort banner feed — omit this kind on any source failure, the endpoint never 5xxs (documented contract)
        logger.debug("notifications: operator-alert banners failed: %s", exc)
    return out


@router.get("/notifications")
def get_notifications() -> Dict[str, Any]:
    """Aggregate the active banner-worthy conditions (Tier 1, best-effort)."""
    banners: List[Dict[str, Any]] = []

    tb = _trainer_banner()
    if tb:
        banners.append(tb)
    # Passed the DOWN verdict rather than recomputing it: the disk fact and the
    # liveness fact must come from one reading of one mirror, or the feed can
    # report a fresh disk figure beside a "trainer is down" banner.
    tdb = _trainer_disk_banner(trainer_down=tb is not None)
    if tdb:
        banners.append(tdb)
    banners.extend(_account_down_banners())
    banners.extend(_prop_fills_stale_banners())
    banners.extend(_operator_alert_banners())
    orb = _orphan_unreconciled_banner()
    if orb:
        banners.append(orb)
    banners.extend(_recent_warning_banners())
    ob = _recent_trade_open_banner()
    if ob:
        banners.append(ob)

    banners.sort(key=lambda b: _SEVERITY_RANK.get(b.get("severity"), 9))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(banners),
        "has_alerts": any(b.get("severity") == "alert" for b in banners),
        "banners": banners,
    }
