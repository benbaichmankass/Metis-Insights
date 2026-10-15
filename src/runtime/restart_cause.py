"""WHY did ``ict-trader-live`` just start? — the cause line of the restart ping.

Checklist row ``TELEGRAM-RESTART-MSG`` (operator, 2026-10-15): the second line
of the restart ping names the cause — deploy of <sha>/PR, crash / unclean exit,
manual restart, watchdog autoheal, VM reboot, OOM — from evidence, and says
``cause: unknown`` when the evidence does not settle it. **Never a guess.**

Evidence, read once at boot (every read is best-effort and bounded):

1. **The restart-intent marker** ``runtime_logs/trader_restart_intent.json``,
   written by whatever is ABOUT to restart the trader on purpose:
   ``scripts/deploy_pull_restart.sh`` (``deploy``, with the SHA it deploys and
   the SHA the running code was on), ``scripts/ops/restart_bot.sh``
   (``manual``) and ``scripts/check_heartbeat.py`` (``watchdog_autoheal``).
   It is CONSUMED here (renamed to ``.consumed``) so a later unplanned restart
   cannot inherit it, and it only counts when younger than
   :data:`INTENT_MAX_AGE_S`.
2. **systemd's own record of how the previous run ended**, from
   ``journalctl -u ict-trader-live.service`` since the previous start:
   ``oom-kill`` / "killed by the OOM killer" → ``oom``; ``Failed with result``
   / ``Main process exited, code=exited, status=<non-zero>`` / ``code=killed``
   (not TERM) → ``crash``; ``Stopping`` / ``Stopped`` → a clean stop by
   ``systemctl`` (a deploy, a manual restart or the watchdog — the marker says
   which).
3. **Host uptime** (``/proc/uptime``): a trader starting within
   :data:`REBOOT_WINDOW_S` of the host booting is a ``vm_reboot``.

The evidence is combined, never overridden: a marker that says "deploy" with a
journal that says the previous run CRASHED reports the crash (the deploy did
not cause that exit). A journal that cannot be read is ``unknown``, not
``clean``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
UNIT = "ict-trader-live.service"
INTENT_NAME = "trader_restart_intent.json"
#: an intent older than this did not cause THIS start (deploys restart within
#: seconds of writing it; restart_bot.sh within its 30 s poll)
INTENT_MAX_AGE_S = 900
#: a trader start within this many seconds of host boot is a VM reboot
REBOOT_WINDOW_S = 600

#: causes for which a clean state earns the compact one-line ping
PLANNED = frozenset({"deploy", "manual"})
#: causes that are themselves a problem (count toward the ❌ headline)
PROBLEM = frozenset({"crash", "oom"})


@dataclass
class RestartCause:
    cause: str                      # deploy|manual|watchdog_autoheal|vm_reboot|crash|oom|unknown
    detail: str = ""                # human text after "cause: "
    evidence: List[str] = field(default_factory=list)

    @property
    def planned(self) -> bool:
        return self.cause in PLANNED

    @property
    def is_problem(self) -> bool:
        return self.cause in PROBLEM

    def line(self) -> str:
        return f"cause: {self.detail or self.cause}"


def intent_path(repo_root: Optional[Path] = None) -> Path:
    return Path(repo_root or _REPO_ROOT) / "runtime_logs" / INTENT_NAME


def write_intent(cause: str, repo_root: Optional[Path] = None, **detail: Any) -> None:
    """Record that *cause* is about to restart the trader. Never raises."""
    try:
        p = intent_path(repo_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cause": cause, "at": int(time.time()), **detail}),
                       encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001 — an unwritten intent reads as "unknown", never a crash
        logger.warning("restart_cause: could not write intent %s: %s", cause, exc)


def _consume_intent(repo_root: Optional[Path], now: float) -> Optional[Dict[str, Any]]:
    p = intent_path(repo_root)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("restart_cause: unreadable intent marker: %s", exc)
        return None
    try:
        os.replace(p, p.with_name(INTENT_NAME + ".consumed"))
    except OSError:
        pass
    try:
        age = now - float(data.get("at"))
    except (TypeError, ValueError):
        return None
    if age < 0 or age > INTENT_MAX_AGE_S:
        logger.info("restart_cause: intent %r is %.0fs old — ignored", data.get("cause"), age)
        return None
    return data


def _host_uptime_s() -> Optional[float]:
    try:
        with open("/proc/uptime", encoding="ascii") as fh:
            return float(fh.read().split()[0])
    except Exception:  # noqa: BLE001
        return None


def _journal_lines() -> Optional[List[str]]:
    """The unit's systemd journal for the last 2 h, or None if unreadable."""
    try:
        proc = subprocess.run(
            ["journalctl", "-u", UNIT, "--since", "-2h", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("restart_cause: journalctl unavailable: %s", exc)
        return None
    if proc.returncode != 0:
        return None
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return lines or None


_STARTED = re.compile(r"^Started ")
_OOM = re.compile(r"oom-kill|OOM killer", re.I)
_FAILED = re.compile(r"Failed with result '([^']+)'")
_EXITED = re.compile(r"Main process exited, code=(exited|killed|dumped), status=(\S+)")
_STOPPING = re.compile(r"^(Stopping|Stopped) ")


def classify_previous_exit(lines: List[str]) -> Optional[str]:
    """How the PREVIOUS run ended, from the systemd lines before the current
    start: ``oom`` | ``crash`` | ``clean_stop`` | None (no evidence).

    Only systemd's own manager messages are matched (``-o cat`` mixes them with
    the trader's log output); the window is the lines between the previous
    start and the current one.
    """
    # "Started <Description>." is logged once per activation (every Type);
    # "Starting" is not a boundary — ExecStartPre output can sit between them
    starts = [i for i, ln in enumerate(lines) if _STARTED.match(ln)]
    if not starts:
        return None
    end = starts[-1]
    begin = starts[-2] + 1 if len(starts) > 1 else 0
    # only systemd's own lines about this unit — never the trader's log text
    window = [ln for ln in lines[begin:end]
              if ln.startswith(UNIT + ":") or _STOPPING.match(ln)]
    if any(_OOM.search(ln) for ln in window):
        return "oom"
    for ln in window:
        m = _FAILED.search(ln)
        if m:
            return "oom" if "oom" in m.group(1) else "crash"
        m = _EXITED.search(ln)
        if m:
            code, status = m.group(1), m.group(2)
            if code == "exited" and status.split("/")[0] != "0":
                return "crash"
            if code in ("killed", "dumped") and not status.startswith(("15", "TERM")):
                return "crash"
    if any(_STOPPING.match(ln) for ln in window):
        return "clean_stop"
    return None


def _git(repo_root: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(["git", "-C", str(repo_root), *args],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def describe_deploy(intent: Dict[str, Any], repo_root: Optional[Path] = None) -> str:
    """``deploy a1b2c3d (#18216, +3 more commits)`` from the intent marker."""
    root = Path(repo_root or _REPO_ROOT)
    sha = str(intent.get("sha") or "")
    base = str(intent.get("from") or "")
    if not sha:
        return "deploy (sha not recorded)"
    out = f"deploy {sha[:7]}"
    bits = []
    subject = _git(root, "log", "-1", "--format=%s", sha) or ""
    m = re.search(r"\(#(\d+)\)\s*$", subject)
    if m:
        bits.append(f"#{m.group(1)}")
    if base:
        n = _git(root, "rev-list", "--count", f"{base}..{sha}")
        if n and n.isdigit() and int(n) > 1:
            bits.append(f"+{int(n) - 1} more commit(s)")
    return out + (f" ({', '.join(bits)})" if bits else "")


def determine(repo_root: Optional[Path] = None, *, now: Optional[float] = None,
              journal: Optional[List[str]] = ..., uptime_s: Optional[float] = ...,
              ) -> RestartCause:
    """Combine the three evidence sources. Never raises."""
    now = time.time() if now is None else now
    try:
        intent = _consume_intent(repo_root, now)
        lines = _journal_lines() if journal is ... else journal
        up = _host_uptime_s() if uptime_s is ... else uptime_s
        prev = classify_previous_exit(lines) if lines else None
        ev = [f"intent={intent.get('cause') if intent else None}",
              f"journal={'unreadable' if lines is None else prev}",
              f"host_uptime_s={None if up is None else int(up)}"]

        if prev == "oom":
            return RestartCause("oom", "OOM kill (systemd: previous run killed by the OOM killer)", ev)
        if prev == "crash":
            return RestartCause("crash", "crash / unclean exit of the previous run (systemd)", ev)
        if up is not None and up < REBOOT_WINDOW_S:
            return RestartCause("vm_reboot", f"VM reboot (host up {int(up // 60)}m)", ev)
        if intent:
            c = str(intent.get("cause") or "")
            if c == "deploy":
                return RestartCause("deploy", describe_deploy(intent, repo_root), ev)
            if c == "manual":
                by = intent.get("by")
                return RestartCause("manual", "manual restart" + (f" ({by})" if by else ""), ev)
            if c == "watchdog_autoheal":
                return RestartCause("watchdog_autoheal",
                                    "liveness watchdog autoheal (heartbeat went stale)", ev)
        if prev == "clean_stop":
            return RestartCause("unknown", "unknown (clean systemctl stop/start; no deploy, "
                                "manual or watchdog record)", ev)
        return RestartCause("unknown", "unknown", ev)
    except Exception as exc:  # noqa: BLE001
        logger.warning("restart_cause: determination failed: %s", exc)
        return RestartCause("unknown", "unknown", [f"error={type(exc).__name__}"])
