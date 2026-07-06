"""Health checks — S-022 PR3.

A small collection of independent health checks. Each returns a
``HealthCheck`` dataclass so callers can render them uniformly:

  * Hourly report Health section (``src/runtime/hourly_report.py``).
  * Standalone VM-side ping script (PR5).
  * Future ``/health`` Telegram command.

Design constraints:

* Every check function MUST NEVER raise. Failures inside a check
  return a "warn" / "critical" ``HealthCheck`` describing the
  problem. A health-check tool that crashes is itself an outage.
* Subprocess calls (``systemctl``, ``git``) get short timeouts so
  one slow check can't stall the whole report.
* Read-only — no side effects, no file writes, no API mutations.
* Stdlib + already-vendored deps only — must not require pyaml or ccxt
  to import; both are optional on the VM image.

Severity model (matches ``src/runtime/outcomes.py``):

  ``ok``       — system is healthy.
  ``warn``     — non-critical degradation (worth noting but trader
                 can keep running). Example: 1h-stale git pull.
  ``critical`` — operator action required. Example: trader service
                 not running, or HEAD drifted by hours behind main.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.utils.paths import runtime_logs_dir


logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SERVICE = "ict-trader-live.service"
_DEFAULT_BRANCH = "origin/main"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class HealthCheck:
    name: str
    status: str  # "ok" | "warn" | "critical"
    detail: str
    ctx: Dict[str, Any] = field(default_factory=dict)


def _ok(name: str, detail: str, **ctx: Any) -> HealthCheck:
    return HealthCheck(name=name, status="ok", detail=detail, ctx=dict(ctx))


def _warn(name: str, detail: str, **ctx: Any) -> HealthCheck:
    return HealthCheck(name=name, status="warn", detail=detail, ctx=dict(ctx))


def _critical(name: str, detail: str, **ctx: Any) -> HealthCheck:
    return HealthCheck(name=name, status="critical", detail=detail, ctx=dict(ctx))


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def _run(cmd: List[str], *, timeout: float = 5.0) -> subprocess.CompletedProcess:
    """Run a command capturing stdout/stderr, with a hard timeout.

    Returns a CompletedProcess even on failure (returncode set, stderr
    populated). Raises only on the truly exceptional cases (cmd is not
    a list, signal handler interrupts) — callers may still wrap in
    try/except for paranoia.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# ---------------------------------------------------------------------------
# Check 1 — systemd service
# ---------------------------------------------------------------------------


def check_service(service: str = _DEFAULT_SERVICE) -> HealthCheck:
    """Check whether the trader service is active under systemd.

    On hosts without systemctl (e.g. CI sandboxes, dev laptops), returns
    a 'warn' rather than 'critical' — the operator's actual VM has
    systemctl, and the absence of it elsewhere is not a real outage.
    """
    name = "service"
    try:
        proc = _run(["systemctl", "is-active", service], timeout=5.0)
    except FileNotFoundError:
        return _warn(name, "systemctl unavailable on this host", service=service)
    except subprocess.TimeoutExpired:
        return _critical(
            name, f"systemctl is-active {service} timed out", service=service,
        )
    except Exception as exc:  # noqa: BLE001
        return _warn(name, f"systemctl call failed: {exc}", service=service)

    state = (proc.stdout or "").strip()
    if state == "active":
        return _ok(name, f"{service} active", service=service)
    return _critical(
        name, f"{service} is '{state or 'unknown'}'",
        service=service, state=state,
    )


# ---------------------------------------------------------------------------
# Check 2 — repo vs VM HEAD drift
# ---------------------------------------------------------------------------


def check_git_drift(
    *,
    branch: str = _DEFAULT_BRANCH,
    repo_dir: Optional[Path] = None,
    fetch: bool = False,
    critical_age_hours: float = 24.0,
) -> HealthCheck:
    """Check whether the local HEAD matches `origin/main`.

    Counts the number of commits the VM is behind, and surfaces the age
    of the most recent commit on `origin/main` that the VM doesn't
    have. Drift > 0 with a fresh commit → WARN; drift with an aged
    commit → CRITICAL.

    `fetch=False` is the default — the VM's existing
    ``ict-git-sync.timer`` already runs `git fetch` every 5 minutes,
    so a passive `git rev-list` is enough and avoids piling another
    network call onto the hourly path.
    """
    name = "git_drift"
    cwd = str(repo_dir or _REPO_ROOT)
    # Run git with safe.directory=* so the health-snapshot context (a service
    # user reading a repo it doesn't own, or via the /opt symlink) doesn't fail
    # with "fatal: detected dubious ownership" -> "rev-parse HEAD failed"
    # (BL-20260623-005). Harmless when ownership is already fine.
    _git = ["git", "-C", cwd, "-c", "safe.directory=*"]

    try:
        if fetch:
            fetch_proc = _run([*_git, "fetch", "--quiet", "origin"], timeout=10.0)
            if fetch_proc.returncode != 0:
                return _warn(
                    name, "git fetch failed",
                    stderr=(fetch_proc.stderr or "").strip()[:200],
                )

        head = _run([*_git, "rev-parse", "HEAD"], timeout=5.0)
        if head.returncode != 0:
            return _warn(name, "git rev-parse HEAD failed",
                         stderr=(head.stderr or "").strip()[:200])

        upstream = _run([*_git, "rev-parse", branch], timeout=5.0)
        if upstream.returncode != 0:
            return _warn(
                name, f"could not resolve {branch}",
                stderr=(upstream.stderr or "").strip()[:200],
            )

        head_sha = (head.stdout or "").strip()
        upstream_sha = (upstream.stdout or "").strip()

        if head_sha == upstream_sha:
            return _ok(
                name, f"in sync with {branch} ({head_sha[:7]})",
                head=head_sha[:7], upstream=upstream_sha[:7],
            )

        # Count commits the VM is behind.
        behind_proc = _run(
            [*_git, "rev-list", "--count", f"HEAD..{branch}"],
            timeout=5.0,
        )
        try:
            behind = int((behind_proc.stdout or "0").strip())
        except ValueError:
            behind = -1

        # Get the timestamp of the latest origin/main commit (in UTC).
        ts_proc = _run(
            [*_git, "log", "-1", "--format=%cI", branch],
            timeout=5.0,
        )
        upstream_ts = (ts_proc.stdout or "").strip()
        age_hours: Optional[float] = None
        try:
            ts = datetime.fromisoformat(upstream_ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        except (ValueError, TypeError):
            pass

        ctx = {
            "head": head_sha[:7],
            "upstream": upstream_sha[:7],
            "behind": behind,
            "age_hours": age_hours,
        }
        if age_hours is not None and age_hours >= critical_age_hours:
            return _critical(
                name,
                f"{behind} commits behind {branch}, oldest unmerged is {age_hours:.1f}h old",
                **ctx,
            )
        return _warn(
            name, f"{behind} commits behind {branch}", **ctx,
        )
    except FileNotFoundError:
        return _warn(name, "git binary unavailable")
    except subprocess.TimeoutExpired:
        return _warn(name, "git command timed out")
    except Exception as exc:  # noqa: BLE001
        return _warn(name, f"check failed: {exc}")


# ---------------------------------------------------------------------------
# Check 3 — last fetch recency
# ---------------------------------------------------------------------------


def check_last_fetch(
    *,
    repo_dir: Optional[Path] = None,
    stale_minutes: float = 15.0,
) -> HealthCheck:
    """Look at .git/FETCH_HEAD mtime to gauge sync recency.

    The VM's ict-git-sync.timer runs every 5 min. If FETCH_HEAD hasn't
    been touched in 3× that interval, something is wrong with the timer.
    """
    name = "git_fetch"
    cwd = repo_dir or _REPO_ROOT
    fetch_head = cwd / ".git" / "FETCH_HEAD"
    if not fetch_head.exists():
        return _warn(name, ".git/FETCH_HEAD not present (never fetched?)")
    try:
        age_s = time.time() - fetch_head.stat().st_mtime
    except OSError as exc:
        return _warn(name, f"could not stat FETCH_HEAD: {exc}")
    age_min = age_s / 60.0
    if age_min > stale_minutes:
        return _warn(
            name,
            f"last fetch was {age_min:.1f}m ago (> {stale_minutes}m threshold)",
            age_minutes=age_min,
        )
    return _ok(
        name, f"last fetch {age_min:.1f}m ago", age_minutes=age_min,
    )


# ---------------------------------------------------------------------------
# Check 4 — tick freshness via signal audit log mtime
# ---------------------------------------------------------------------------


def check_tick_freshness(
    *,
    audit_path: Optional[Path] = None,
    tick_interval_s: int = 900,
) -> HealthCheck:
    """Use ``runtime_logs/heartbeat.txt`` mtime if available, else fall
    back to ``signal_audit.jsonl``.

    PR5 introduced the dedicated heartbeat file (written by
    ``src/runtime/heartbeat.py`` from ``src/main.py`` after each tick).
    The jsonl fallback exists so a fresh deploy that hasn't written a
    heartbeat yet still has a signal to read from.
    """
    name = "tick"
    if audit_path is not None:
        path = audit_path
    else:
        # Aligned with the writers (heartbeat.py, signal_audit_logger.py)
        # which both resolve through runtime_logs_dir(). The 2026-05-11
        # silent-freeze incident traced to this reader hardcoding the
        # repo path while the writer landed under DATA_DIR.
        logs_dir = runtime_logs_dir()
        heartbeat = logs_dir / "heartbeat.txt"
        path = heartbeat if heartbeat.exists() else (
            logs_dir / "signal_audit.jsonl"
        )
    if not path.exists():
        return _critical(
            name,
            "no heartbeat or signal_audit.jsonl — has the tick loop ever run?",
        )
    try:
        age_s = time.time() - path.stat().st_mtime
    except OSError as exc:
        return _warn(name, f"could not stat {path.name}: {exc}")
    if age_s > 2 * tick_interval_s:
        return _critical(
            name,
            f"last tick {int(age_s)}s ago (> 2x interval {tick_interval_s}s)",
            age_s=age_s, source=path.name,
        )
    return _ok(
        name, f"last tick {int(age_s)}s ago",
        age_s=age_s, source=path.name,
    )


# ---------------------------------------------------------------------------
# Check 5 — per-account API connectivity
# ---------------------------------------------------------------------------


def check_accounts_api() -> HealthCheck:
    """Reuse data_loaders.account_balance() per account; any None → WARN."""
    name = "accounts_api"
    try:
        from src.bot.data_loaders import account_balance, list_accounts
    except Exception as exc:  # noqa: BLE001
        return _warn(name, f"data_loaders unavailable: {exc}")

    try:
        accounts = list_accounts()
    except Exception as exc:  # noqa: BLE001
        return _warn(name, f"list_accounts failed: {exc}")

    if not accounts:
        return _ok(name, "no accounts configured")

    # Manual-bridge / stub integrations (e.g. the breakout prop account) have
    # NO broker balance API by design — they execute via Telegram/FCM tickets.
    # Probing them always returns None, which must not be counted as an outage
    # (BL-20260623-003). Detect via an EXPLICITLY-empty management-cap set;
    # an unknown/absent exchange is still probed (fail-open).
    try:
        from src.units.accounts.clients import EXCHANGE_MANAGEMENT_CAPS
    except Exception:  # noqa: BLE001
        EXCHANGE_MANAGEMENT_CAPS = {}

    def _has_broker_api(acc: Dict[str, Any]) -> bool:
        ex = str(acc.get("exchange") or "").strip().lower()
        caps = EXCHANGE_MANAGEMENT_CAPS.get(ex)
        # caps is None (unknown exchange) -> probe it; a declared empty set
        # (breakout stub) -> no API, skip; non-empty -> real API, probe.
        return caps != frozenset()

    def _is_declared_live(acc: Dict[str, Any]) -> bool:
        # BL-20260705-HEALTHCHECK-SHELVED-ACCOUNTS: a dry/shelved account
        # (``mode != live`` — the 2FA-blocked ``ib_live`` / ``oanda_practice``)
        # reads unreachable BY DESIGN, so counting it as "API down" pinned the
        # health roll-up at a permanent WARN and trained everyone to ignore
        # WARN. Skip it the same way ``breakout_1`` is skipped. Mirrors the SAME
        # rule the reachability latch uses
        # (``account_reachability_alert._checkable_accounts``: ``mode == live``),
        # reading the single source of truth — the account's ``mode`` field.
        # Default ``live`` when omitted (two-gates default-permissive: an account
        # is demoted only by an EXPLICIT ``dry_run``).
        return str(acc.get("mode") or "live").strip().lower() == "live"

    failed: List[str] = []
    skipped: List[str] = []
    shelved: List[str] = []
    for acc in accounts:
        aid = acc.get("account_id") or "unknown"
        if not _has_broker_api(acc):
            skipped.append(aid)
            continue
        if not _is_declared_live(acc):
            shelved.append(aid)
            continue
        try:
            bal = account_balance(acc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("check_accounts_api: balance(%s) raised: %s", aid, exc)
            bal = None
        if bal is None:
            failed.append(aid)

    total = len(accounts) - len(skipped) - len(shelved)
    if not failed:
        detail = f"all {total} broker-API accounts ok"
        extras: List[str] = []
        if skipped:
            extras.append(f"{len(skipped)} manual-bridge skipped: {', '.join(skipped)}")
        if shelved:
            extras.append(f"{len(shelved)} dry/shelved skipped: {', '.join(shelved)}")
        if extras:
            detail += " (" + "; ".join(extras) + ")"
        return _ok(name, detail, total=total, skipped=skipped, shelved=shelved)
    return _warn(
        name,
        f"{len(failed)}/{total} accounts API down: {', '.join(failed)}",
        failed=failed, total=total, skipped=skipped, shelved=shelved,
    )


# ---------------------------------------------------------------------------
# Check 6 — DB writability
# ---------------------------------------------------------------------------


def check_db(*, db_path: Optional[Path] = None) -> HealthCheck:
    """Open the trade-journal DB and run a trivial SELECT."""
    name = "db"
    from src.utils.paths import trade_journal_db_path
    candidates: List[Path] = []
    if db_path:
        candidates.append(db_path)
    # Canonical resolver (env-first, then $DATA_DIR, then repo-root).
    candidates.append(Path(trade_journal_db_path()))

    for path in candidates:
        if not path.exists():
            continue
        try:
            conn = sqlite3.connect(str(path), timeout=2.0)
            try:
                conn.execute("SELECT 1").fetchone()
            finally:
                conn.close()
            return _ok(name, f"SELECT 1 ok on {path.name}", path=str(path))
        except sqlite3.Error as exc:
            return _warn(name, f"DB query failed on {path.name}: {exc}", path=str(path))
    return _warn(name, "no trade-journal DB found in candidates")


# ---------------------------------------------------------------------------
# Check 7 — disk free
# ---------------------------------------------------------------------------


def check_disk(
    *,
    path: str = "/",
    warn_pct: float = 10.0,
) -> HealthCheck:
    """Warn if free disk on `path` is below `warn_pct`%."""
    name = "disk"
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return _warn(name, f"disk_usage({path}) failed: {exc}")
    if usage.total == 0:
        return _warn(name, "disk_usage returned total=0", path=path)
    free_pct = 100.0 * usage.free / usage.total
    free_gb = usage.free / (1024 ** 3)
    if free_pct < warn_pct:
        return _warn(
            name,
            f"only {free_pct:.1f}% free ({free_gb:.1f} GB) on {path}",
            free_pct=free_pct, free_gb=free_gb, path=path,
        )
    return _ok(
        name,
        f"{free_pct:.1f}% free ({free_gb:.1f} GB) on {path}",
        free_pct=free_pct, free_gb=free_gb, path=path,
    )


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------


_DEFAULT_CHECKS: List[Callable[[], HealthCheck]] = [
    check_service,
    check_git_drift,
    check_last_fetch,
    check_tick_freshness,
    check_accounts_api,
    check_db,
    check_disk,
]


def run_all_checks(
    checks: Optional[List[Callable[[], HealthCheck]]] = None,
) -> List[HealthCheck]:
    """Run every check, swallowing exceptions per check.

    A check that raises (instead of returning a HealthCheck) is itself
    a bug — but we still return a `warn` placeholder so the caller sees
    the failure rather than the whole report blowing up.
    """
    out: List[HealthCheck] = []
    for check in checks or _DEFAULT_CHECKS:
        try:
            out.append(check())
        except Exception as exc:  # noqa: BLE001
            logger.exception("health.run_all_checks: %s raised", check.__name__)
            out.append(_warn(
                getattr(check, "__name__", "unknown"),
                f"check raised: {type(exc).__name__}: {exc}",
            ))
    return out


def overall_status(results: List[HealthCheck]) -> str:
    """Reduce a list of check results to one of ok/warn/critical."""
    if any(r.status == "critical" for r in results):
        return "critical"
    if any(r.status == "warn" for r in results):
        return "warn"
    return "ok"
