"""Cloud/VM notification helpers — extracted from telegram_query_bot.py (PR-4).

Pure system-inspection helpers (systemd, /proc, disk) and the pending-pings
inbox drainer. No trade logic. Async only for _drain_pending_pings.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time

from src.utils.paths import repo_root as _repo_root
from src.utils.paths import runtime_logs_dir as _runtime_logs_dir

logger = logging.getLogger(__name__)

REPO_ROOT = _repo_root()

# ── Pending pings inbox ────────────────────────────────────────────────────
# Any process on the VM drops a JSON file here to ping the operator without
# re-implementing the Telegram client.
# Schema: {"priority": "normal|high|urgent|low", "body": "...", "parse_mode": "HTML"?}
#
# MUST resolve through the canonical `runtime_logs_dir()` (DATA_DIR-aware),
# NOT repo-root-relative: on the live VM DATA_DIR=/data/bot-data, so the
# canonical inbox is /data/bot-data/runtime_logs/pending_pings. Every
# producer (execution_diagnostics, liveness_watchdog, coordinator,
# send_ping) writes there; a repo-relative drainer reads a different,
# empty directory and silently never delivers. (Fixed 2026-05-25 — trade
# pings + send-ping smoke landed in the canonical dir while this drainer
# watched the repo dir.)

PENDING_PINGS_DIR = str(_runtime_logs_dir() / "pending_pings")
# Claude/operational-update inbox (send_ping.py target=claude). Folded into the
# trader bot's drain (2026-06-22) — the separate @claude_ict_comms_bot bridge
# (ict-claude-bridge.service) sat inactive on the Ampere VM (its
# TELEGRAM_CLAUDE_BOT_TOKEN never carried over the cutover), so claude pings
# were silently never delivered. Same file schema as PENDING_PINGS_DIR
# ({priority, body}), so the trader bot drains it with its own working token →
# @bict_trading_bot, where the operator already watches trade alerts.
PENDING_CLAUDE_PINGS_DIR = str(_runtime_logs_dir() / "pending_claude_pings")
PING_DRAIN_INTERVAL_S = 5

# BL-20260726-CLAUDE-PING-INBOX-SPLIT-BRAIN. When DATA_DIR is set (the live VM,
# /data/bot-data) the canonical inboxes above live under $DATA_DIR/runtime_logs.
# A ping *writer* that runs without DATA_DIR in its env (a mis-provisioned
# service, a bare off-cron script invocation) resolves runtime_logs_dir() to the
# REPO-relative <repo>/runtime_logs and drops its ping in the repo-relative twin
# of these dirs, where the canonical drainer never looks — silently stranding it
# (352 such files found in the 2026-07-26 full-system audit). After draining the
# canonical inbox the drainer now ALSO sweeps that repo-relative twin: a FRESH
# file is DELIVERED (a mis-routed live ping is never lost) and a STALE one is
# DISCARDED (the pre-migration backlog is cleaned, not replayed as days-old spam).
LEGACY_PING_STALE_AFTER_S = 2 * 60 * 60  # 2h — older than this = stranded straggler

_PRIORITY_ICONS = {
    "urgent": "🚨 URGENT",
    "high":   "🔔",
    "normal": "ℹ️",
    "low":    "·",
}


def _legacy_repo_ping_dir(pings_dir: str) -> str | None:
    """The repo-relative twin of a canonical (DATA_DIR) inbox — or ``None`` when
    the two are the same directory (DATA_DIR unset → nothing extra to sweep)."""
    legacy = os.path.join(REPO_ROOT, "runtime_logs", os.path.basename(pings_dir.rstrip("/")))
    try:
        if os.path.realpath(legacy) == os.path.realpath(pings_dir):
            return None
    except OSError:
        return None
    return legacy


async def _drain_one_ping_dir(context, chat_id: str, pings_dir: str,
                              discard_older_than_s: float | None = None,
                              bot=None) -> None:
    """Drain one inbox: send each ping, delete on success. Failures (Telegram
    4xx, malformed JSON) move the file aside with a ``.broken`` suffix so the
    drainer never loops on the same bad file.

    ``bot`` overrides which Telegram bot SENDS. It defaults to ``context.bot``
    (the trader bot) so every existing caller is byte-for-byte unchanged; the
    Claude inbox passes its own bot so operational pings land in a separate
    conversation instead of among trade alerts. The CHAT ID is deliberately
    NOT overridden here — in a DM ``chat.id`` IS the operator's user id and is
    identical for every bot, so the separation comes from the TOKEN
    (src/bot/telegram_routes.py records the operator correction that
    established this).

    ``discard_older_than_s`` is set only for the legacy repo-relative twin
    (BL-20260726): a file older than this is a stranded straggler — unlink it
    WITHOUT delivering (days-old status pings are noise), rather than replay it.
    """
    try:
        os.makedirs(pings_dir, exist_ok=True)
        names = sorted(
            n for n in os.listdir(pings_dir)
            if n.endswith(".json") and not n.endswith(".tmp")
        )
    except OSError:
        return

    if not names:
        return

    if not chat_id:
        logger.warning("ping inbox has %d file(s) but TELEGRAM_CHAT_ID is unset", len(names))
        return

    now = time.time()
    for name in names:
        path = os.path.join(pings_dir, name)

        # Legacy-twin cleanup: discard a stranded straggler without delivery.
        if discard_older_than_s is not None:
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                age = 0.0
            if age > discard_older_than_s:
                try:
                    os.unlink(path)
                    logger.warning(
                        "ping inbox: discarded stale stranded ping %s (%.0fh old) "
                        "from the legacy inbox %s (BL-20260726)", name, age / 3600.0, pings_dir,
                    )
                except OSError:
                    pass
                continue

        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("ping inbox: malformed file %s — %s", name, exc)
            try:
                os.rename(path, path + ".broken")
            except OSError:
                pass
            continue

        priority = str(payload.get("priority", "normal")).lower()
        body = str(payload.get("body", "")).strip()
        if not body:
            try:
                os.unlink(path)
            except OSError:
                pass
            continue

        if discard_older_than_s is not None:
            logger.warning(
                "ping inbox: delivering a MIS-ROUTED ping %s found in the legacy "
                "inbox %s — a writer resolved runtime_logs without DATA_DIR "
                "(BL-20260726)", name, pings_dir,
            )

        # Self-titled HTML pings (trade lifecycle events) carry their own
        # header and a parse_mode; plain pings get the priority icon prefix.
        parse_mode = payload.get("parse_mode") or None
        if parse_mode:
            text = body
        else:
            prefix = _PRIORITY_ICONS.get(priority, _PRIORITY_ICONS["normal"])
            text = f"{prefix} {body}"

        try:
            await (bot or context.bot).send_message(
                chat_id=chat_id, text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ping inbox: send failed for %s — %s", name, exc)
            continue

        try:
            os.unlink(path)
        except OSError:
            pass


async def _drain_pending_pings(context, chat_id: str | None = None,
                                pings_dir: str | None = None,
                                bot=None) -> None:
    """JobQueue task — drain the canonical inbox, then (defense in depth) sweep
    the legacy repo-relative twin so a ping written by a process missing DATA_DIR
    is delivered rather than stranded, and the pre-migration backlog is discarded
    (BL-20260726-CLAUDE-PING-INBOX-SPLIT-BRAIN)."""
    pings_dir = pings_dir or PENDING_PINGS_DIR
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID") or ""
    await _drain_one_ping_dir(context, chat_id, pings_dir, bot=bot)
    legacy = _legacy_repo_ping_dir(pings_dir)
    if legacy is not None:
        await _drain_one_ping_dir(
            context, chat_id, legacy,
            discard_older_than_s=LEGACY_PING_STALE_AFTER_S,
            bot=bot,
        )


# ── Shell / systemd helpers ────────────────────────────────────────────────

def run_shell_command(cmd: list) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    return ((result.stdout or "") + (result.stderr or "")).strip()


def get_service_status(service_name: str) -> str:
    try:
        return run_shell_command(["systemctl", "is-active", service_name]) or "unknown"
    except Exception as e:
        return f"error: {e}"


def _known_systemd_units(repo_root: str | None = None) -> set:
    """Return the set of systemd unit stems present in the repo's deploy/."""
    deploy_dir = os.path.join(repo_root or REPO_ROOT, "deploy")
    try:
        return {
            name[: -len(".service")]
            for name in os.listdir(deploy_dir)
            if name.endswith(".service")
        }
    except FileNotFoundError:
        return set()


def toggle_service(service_name: str, action: str,
                   repo_root: str | None = None) -> str:
    known = _known_systemd_units(repo_root)
    if known and service_name not in known:
        return (
            f"❌ Refusing to {action} `{service_name}`: no matching unit "
            f"file in deploy/. Known units: `{', '.join(sorted(known))}`. "
            "If this service should exist, add the unit file in a PR; "
            "otherwise fix the caller."
        )
    try:
        result = subprocess.run(
            ["sudo", "systemctl", action, service_name],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            new_status = get_service_status(service_name)
            return f"✅ `{service_name}` {action}ed. Status: `{new_status}`"
        err = (result.stderr or result.stdout or "unknown error").strip()
        return f"❌ Failed to {action} `{service_name}`:\n{err}"
    except Exception as e:
        return f"❌ Exception toggling `{service_name}`: {e}"


# ── System resource readers ────────────────────────────────────────────────

def _read_loadavg() -> str:
    try:
        with open("/proc/loadavg", encoding="utf-8") as fh:
            parts = fh.read().split()
        return " ".join(parts[:3]) if len(parts) >= 3 else "unknown"
    except OSError:
        return "unknown"


def _read_uptime_human() -> str:
    try:
        with open("/proc/uptime", encoding="utf-8") as fh:
            secs = float(fh.read().split()[0])
    except (OSError, ValueError):
        return "unknown"
    d, secs = divmod(int(secs), 86400)
    h, secs = divmod(secs, 3600)
    m, _ = divmod(secs, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _read_meminfo_mb() -> tuple[int, int]:
    """Return (total_mb, available_mb). (0, 0) on read error."""
    total = avail = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) // 1024
                if total and avail:
                    break
    except (OSError, ValueError, IndexError):
        return 0, 0
    return total, avail


def _disk_usage_repo(repo_root: str | None = None) -> tuple[int, int]:
    """Return (free_gb, total_gb) for the partition holding the repo."""
    try:
        total, _, free = shutil.disk_usage(repo_root or REPO_ROOT)
        return free // (1024 ** 3), total // (1024 ** 3)
    except OSError:
        return 0, 0
