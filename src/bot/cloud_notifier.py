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

from src.utils.paths import repo_root as _repo_root

logger = logging.getLogger(__name__)

REPO_ROOT = _repo_root()

# ── Pending pings inbox ────────────────────────────────────────────────────
# Any process on the VM drops a JSON file here to ping the operator without
# re-implementing the Telegram client.
# Schema: {"priority": "normal|high|urgent|low", "body": "..."}

PENDING_PINGS_DIR = os.path.join(REPO_ROOT, "runtime_logs", "pending_pings")
PING_DRAIN_INTERVAL_S = 5

_PRIORITY_ICONS = {
    "urgent": "🚨 URGENT",
    "high":   "🔔",
    "normal": "ℹ️",
    "low":    "·",
}


async def _drain_pending_pings(context, chat_id: str | None = None,
                                pings_dir: str | None = None) -> None:
    """JobQueue task — scan the inbox, send each ping, delete on success.

    Failures (Telegram 4xx, malformed JSON) move the offending file aside
    with a .broken suffix so the drainer never loops on the same bad file.
    """
    pings_dir = pings_dir or PENDING_PINGS_DIR
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID") or ""
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

    for name in names:
        path = os.path.join(pings_dir, name)
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

        prefix = _PRIORITY_ICONS.get(priority, _PRIORITY_ICONS["normal"])
        text = f"{prefix} {body}"

        try:
            await context.bot.send_message(
                chat_id=chat_id, text=text,
                disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ping inbox: send failed for %s — %s", name, exc)
            continue

        try:
            os.unlink(path)
        except OSError:
            pass


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
