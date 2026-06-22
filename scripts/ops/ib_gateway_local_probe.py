#!/usr/bin/env python3
"""Dep-free local wedge probe for the IB-Gateway container.

Why this exists (BL-20260622-GATEWAY-LOCAL-PROBE):
    The dedicated gateway VM is a MINIMAL box — it runs only the Docker
    IB-Gateway container, with no bot venv (no ``ib_insync`` / ``httpx``), no
    writable ``/data``, and no ``.env``. The account-based probe
    (``scripts/ib_connect_check.py``) therefore cannot run there: it fails to
    import ``ib_insync`` and reports ``connected:false`` — which the watchdog
    would mis-read as a real wedge (verified on-box 2026-06-22). This probe
    needs NOTHING but the ``docker`` CLI: it diagnoses the gateway from the
    container's own state + recent logs, so the reactive
    ``ict-ib-gateway-watchdog`` self-heal can run on the minimal box.

Contract:
    Invoked by ``scripts/check_ib_gateway.py``'s ``run_probe`` exactly like the
    account probe — ``python3 ib_gateway_local_probe.py --json <account>`` — and
    emits the SAME JSON shape ``classify_probe`` understands:

      * healthy   → {"results":[{"connected":true,"net_liquidation":1}]}
      * wedged    → {"results":[{"connected":true,"net_liquidation":null,
                                  "error":"<why>"}]}   (session dead; restartable)
      * down      → {"results":[{"connected":false,"net_liquidation":null,
                                  "error":"container not running"}]}
      * can't run → {"results":[],"error":"<why>"}     (no docker / unreadable)
                    classify_probe maps an empty ``results`` to NON-actionable,
                    so a broken probe ENVIRONMENT never drives a restart.

    The ``<account>`` arg is accepted + ignored (kept for drop-in parity).

Wedge signature (what a logged-out / re-login-pending Gateway looks like from
the host, distinct from a healthy one): the in-container ``socat`` relay can't
reach the IBGateway API on 127.0.0.1:4002 ("Connection refused"), and/or IBC
logs that a full re-authentication is required — with NO recent
"Login has completed". A freshly (re)started Gateway emits a brief burst of the
same socat errors but THEN logs "Login has completed", so a recent login
overrides the wedge verdict (prevents re-restarting a box that just recovered).
"""
from __future__ import annotations

import argparse
import json
import subprocess
from typing import List, Optional, Tuple

CONTAINER = "ib-gateway"
API_PORT = "4002"
LOG_WINDOW = "8m"          # how far back to scan docker logs
SOCAT_REFUSED_MIN = 2      # min socat-refused lines in the window to call it wedged


def _run(cmd: List[str], timeout: int = 15) -> Tuple[int, str]:
    """Run *cmd*; return (rc, combined stdout+stderr). rc=-1 on launch failure."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def _docker(args: List[str]) -> Tuple[int, str]:
    """Run ``docker <args>``, falling back to passwordless ``sudo docker``.

    On the gateway VM the watchdog runs as ``ubuntu``, which reaches docker via
    passwordless sudo (same as restart_ib_gateway.sh); some hosts add ubuntu to
    the docker group instead. Try plain first, then ``sudo -n``.
    """
    rc, out = _run(["docker", *args])
    if rc == 0:
        return rc, out
    rc2, out2 = _run(["sudo", "-n", "docker", *args])
    if rc2 == 0:
        return rc2, out2
    # Return whichever actually ran (plain, if it produced output), else sudo's.
    return (rc, out) if rc != -1 else (rc2, out2)


def _emit(obj: dict) -> None:
    print(json.dumps(obj))


def _healthy(reason: str) -> dict:
    return {"ok": True, "results": [{"connected": True, "net_liquidation": 1,
                                     "error": None, "note": reason}]}


def _wedged(reason: str) -> dict:
    return {"ok": False, "results": [{"connected": True, "net_liquidation": None,
                                      "error": reason}]}


def _down(reason: str) -> dict:
    return {"ok": False, "results": [{"connected": False, "net_liquidation": None,
                                      "error": reason}]}


def _inconclusive(reason: str) -> dict:
    # Empty results → classify_probe treats this as NON-actionable (a broken
    # probe environment, not a gateway a restart could fix).
    return {"ok": False, "results": [], "error": reason}


def diagnose(container: str = CONTAINER) -> dict:
    # Can we even talk to docker?
    rc, ver = _docker(["version", "--format", "{{.Server.Version}}"])
    if rc != 0:
        return _inconclusive(f"docker unavailable on host: {ver.strip()[:160] or 'no docker'}")

    # Is the container present + running?
    rc, running = _docker(["inspect", "-f", "{{.State.Running}}", container])
    if rc != 0:
        return _down(f"container {container} not found (docker inspect rc={rc})")
    if running.strip() != "true":
        return _down(f"container {container} is not running (State.Running={running.strip()})")

    # Scan recent logs for the wedge signature.
    rc, logs = _docker(["logs", "--since", LOG_WINDOW, container])
    if rc != 0 and not logs:
        # Container is up but logs unreadable — can't confirm a wedge, and a
        # restart can't fix an unreadable-logs condition → inconclusive.
        return _inconclusive(f"container up but 'docker logs' unreadable (rc={rc})")

    low = logs.lower()
    socat_refused = sum(
        1 for ln in logs.splitlines()
        if "socat" in ln and API_PORT in ln and "connection refused" in ln.lower()
    )
    reauth = ("full authentication will be required" in low
              or "autorestart file not found" in low)
    recent_login = "login has completed" in low

    if recent_login:
        return _healthy("recent 'Login has completed' in window")
    if socat_refused >= SOCAT_REFUSED_MIN or reauth:
        bits = []
        if socat_refused:
            bits.append(f"{socat_refused}x socat→127.0.0.1:{API_PORT} refused")
        if reauth:
            bits.append("IBC re-authentication pending")
        return _wedged("gateway session wedged (" + ", ".join(bits) + ")")
    # Up, no recent login line (login was hours ago in steady state), no wedge
    # markers → healthy.
    return _healthy("container up, no wedge markers in window")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="emit JSON (always on; accepted for run_probe parity)")
    ap.add_argument("account", nargs="?", default=None,
                    help="ignored — kept for drop-in parity with ib_connect_check.py")
    ap.add_argument("--container", default=CONTAINER)
    ap.parse_args(argv)
    try:
        _emit(diagnose(CONTAINER))
    except Exception as exc:  # noqa: BLE001 — never raise; emit inconclusive
        _emit(_inconclusive(f"probe error: {type(exc).__name__}: {exc}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
