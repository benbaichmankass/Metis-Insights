#!/usr/bin/env python3
# wiring: .github/workflows/dashboard-edge-watch.yml
"""Outside-in probe for the three unwatched ways the live dashboard dies.

WHY THIS EXISTS
----------------
The Svelte SPA (`benbaichmankass/ict-trader-dashboard`, GitHub Pages) is the
ONLY live consumer of the bot API (CLAUDE.md, operator decision 2026-09-01).
It reaches the API through: GitHub Pages -> HTTPS -> ict-bot.duckdns.org ->
Caddy (:443) -> ict-web-api (:8001). `ict-web-api-watchdog` watches the
FastAPI process; nothing on this repo's side watches the three links in front
of it:

  1. Caddy itself (`caddy.service` is not an `ict-*` unit and carries no
     watchdog of its own).
  2. The Let's Encrypt certificate Caddy renews automatically -- a renewal
     failure is silent until the cert actually expires.
  3. The DuckDNS `A` record for `ict-bot.duckdns.org` -- DuckDNS records lapse
     if nothing pings them; if the record drifts off the live VM's IP, the
     domain the SPA is hardcoded to resolves nowhere useful.

This script is the OUTSIDE-IN half: it runs from a GitHub-hosted runner (not
the VM), so a wedge that also kills VM-side monitoring (Caddy hung, the VM
firewalled, the box itself down) is still observed from here.

WHAT IT CHECKS (three required, one optional)
-----------------------------------------------
  1. https   -- GET a cheap, ungated endpoint over HTTPS; expects 200 with a
                chain that verifies against the system trust store (an
                ``urllib``/``ssl`` default-context GET fails closed on a bad
                chain -- see ``fetch_https_status``).
  2. cert    -- days remaining on the certificate Caddy is actually serving,
                read directly off the TLS handshake (not off ``https``'s
                success/failure, so an *about-to-expire-but-still-valid-today*
                cert is caught before it becomes an outage).
  3. dns     -- the live ``A`` record(s) for the host, compared against the
                VM IP this repo already treats as canonical
                (``docs/runbooks/live-vm-ip-single-source.md``: the
                ``VM_SSH_HOST`` repo variable, falling back to the same
                literal every other live-VM workflow falls back to).
  4. cors    -- (optional, ``--check-cors``) the preflight the operator
                already used to verify the browser-direct SPA path
                (``docs/runbooks/webapp-https-caddy.md`` Verify section):
                an OPTIONS request from the Pages origin must come back 200
                and echo the origin with ``Authorization`` in
                ``Access-Control-Allow-Headers``.

COLLAPSED-STATE CONTRACT (docs/CLAUDE-RULES-CANONICAL.md "Collapsed states")
------------------------------------------------------------------------------
Every check grades into one of FOUR states, never collapsed into three:

  ok           the thing being checked is healthy.
  warn         degraded but not yet an outage (cert inside the warn window).
  alert        broken now, or inside the alert window.
  unreachable  THE PROBE ITSELF DID NOT COMPLETE (DNS timeout, socket refused,
               an exception mid-check) -- "we could not look", never folded
               into "ok" and never silently swallowed. A probe error is its
               own state.

The GRADING functions (``grade_https`` / ``grade_cert_expiry`` / ``grade_dns``
/ ``grade_cors``) take already-fetched data and contain NO network I/O, so
``--self-test`` can plant an expired-cert fixture, a wrong-IP fixture and a
non-200 fixture and assert each produces its alert without touching the
network. The FETCH functions (``fetch_https_status`` / ``fetch_cert_not_after``
/ ``resolve_a_records`` / ``fetch_cors_preflight``) do the real I/O and always
return a plain dict -- they catch and report a failed connection, they do not
raise into a caller that could paper over it.

Usage:
    python3 scripts/ops/dashboard_edge_watch.py                  # real run
    python3 scripts/ops/dashboard_edge_watch.py --check-cors
    python3 scripts/ops/dashboard_edge_watch.py --self-test      # no network
    python3 scripts/ops/dashboard_edge_watch.py --out receipt.json --json
"""
from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_HOST = "ict-bot.duckdns.org"
DEFAULT_HEALTH_PATH = "/api/health"
DEFAULT_EXPECTED_IP = "141.145.193.91"  # canonical fallback: docs/runbooks/live-vm-ip-single-source.md
DEFAULT_DASHBOARD_ORIGIN = "https://benbaichmankass.github.io"  # docs/runbooks/webapp-https-caddy.md
DEFAULT_CORS_PATH = "/api/bot/stats"

WARN_DAYS = 14
ALERT_DAYS = 7

STATE_ORDER = ["ok", "warn", "unreachable", "alert"]


def _severity(state: str) -> int:
    try:
        return STATE_ORDER.index(state)
    except ValueError:
        return len(STATE_ORDER)  # unknown state sorts as worse than everything


# --------------------------------------------------------------------------
# FETCH — real network I/O. Every function catches its own failures and
# returns a plain dict; none of these raise into the caller.
# --------------------------------------------------------------------------

def fetch_https_status(url: str, timeout: float = 10.0) -> dict[str, Any]:
    """GET ``url``. Distinguishes an HTTP-level result from a TLS/connection failure."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": resp.status, "tls_error": None, "error": None}
    except urllib.error.HTTPError as exc:
        # A real HTTP response came back (chain verified) -- just a non-2xx code.
        return {"status": exc.code, "tls_error": None, "error": None}
    except ssl.SSLCertVerificationError as exc:
        return {"status": None, "tls_error": str(exc), "error": None}
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY" in str(reason).upper():
            return {"status": None, "tls_error": str(reason), "error": None}
        return {"status": None, "tls_error": None, "error": str(reason)}
    except Exception as exc:  # noqa: BLE001 - report, never crash the run
        return {"status": None, "tls_error": None, "error": f"{type(exc).__name__}: {exc}"}


def fetch_cert_not_after(host: str, port: int = 443, timeout: float = 10.0) -> dict[str, Any]:
    """Read the ``notAfter`` field directly off the TLS handshake."""
    try:
        ctx = ssl.create_default_context()
        with (
            socket.create_connection((host, port), timeout=timeout) as sock,
            ctx.wrap_socket(sock, server_hostname=host) as ssock,
        ):
            cert = ssock.getpeercert()
        not_after = cert.get("notAfter") if cert else None
        if not not_after:
            return {"not_after": None, "error": "certificate carried no notAfter field"}
        dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return {"not_after": dt.isoformat(), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"not_after": None, "error": f"{type(exc).__name__}: {exc}"}


def resolve_a_records(host: str, timeout: float = 10.0) -> dict[str, Any]:
    """Resolve every IPv4 A record for ``host``."""
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(host, None, socket.AF_INET)
        ips = sorted({info[4][0] for info in infos})
        if not ips:
            return {"ips": [], "error": "resolution returned zero A records"}
        return {"ips": ips, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ips": [], "error": f"{type(exc).__name__}: {exc}"}
    finally:
        socket.setdefaulttimeout(None)


def fetch_cors_preflight(url: str, origin: str, timeout: float = 10.0) -> dict[str, Any]:
    """OPTIONS preflight the way the operator verified it (webapp-https-caddy.md)."""
    try:
        req = urllib.request.Request(url, method="OPTIONS")
        req.add_header("Origin", origin)
        req.add_header("Access-Control-Request-Method", "GET")
        req.add_header("Access-Control-Request-Headers", "authorization")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {
                "status": resp.status,
                "allow_origin": resp.headers.get("Access-Control-Allow-Origin"),
                "allow_headers": resp.headers.get("Access-Control-Allow-Headers"),
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        headers = exc.headers
        return {
            "status": exc.code,
            "allow_origin": headers.get("Access-Control-Allow-Origin") if headers else None,
            "allow_headers": headers.get("Access-Control-Allow-Headers") if headers else None,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "allow_origin": None, "allow_headers": None, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# GRADE — pure functions, no network I/O. Self-tested directly.
# --------------------------------------------------------------------------

def grade_https(fetched: dict[str, Any]) -> dict[str, Any]:
    if fetched.get("error"):
        return {"check": "https", "state": "unreachable", "detail": fetched["error"]}
    if fetched.get("tls_error"):
        return {"check": "https", "state": "alert", "detail": f"TLS chain did not verify: {fetched['tls_error']}"}
    status = fetched.get("status")
    if status == 200:
        return {"check": "https", "state": "ok", "http_status": status}
    return {"check": "https", "state": "alert", "http_status": status, "detail": f"non-200 response: {status}"}


def grade_cert_expiry(fetched: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    if fetched.get("error") or not fetched.get("not_after"):
        return {"check": "cert_expiry", "state": "unreachable", "detail": fetched.get("error") or "no notAfter"}
    now = now or datetime.now(timezone.utc)
    not_after = datetime.fromisoformat(fetched["not_after"])
    days_remaining = (not_after - now).total_seconds() / 86400.0
    if days_remaining < ALERT_DAYS:
        state = "alert"
    elif days_remaining < WARN_DAYS:
        state = "warn"
    else:
        state = "ok"
    return {
        "check": "cert_expiry",
        "state": state,
        "days_remaining": round(days_remaining, 2),
        "not_after": fetched["not_after"],
    }


def grade_dns(fetched: dict[str, Any], expected_ip: str) -> dict[str, Any]:
    if fetched.get("error"):
        return {"check": "dns", "state": "unreachable", "detail": fetched["error"], "expected_ip": expected_ip}
    ips: list[str] = fetched.get("ips") or []
    if expected_ip in ips:
        return {"check": "dns", "state": "ok", "observed_ips": ips, "expected_ip": expected_ip}
    return {
        "check": "dns",
        "state": "alert",
        "observed_ips": ips,
        "expected_ip": expected_ip,
        "detail": f"A record {ips} does not include the canonical live VM IP {expected_ip}",
    }


def grade_cors(fetched: dict[str, Any], origin: str) -> dict[str, Any]:
    if fetched.get("error"):
        return {"check": "cors", "state": "unreachable", "detail": fetched["error"]}
    status = fetched.get("status")
    if status != 200:
        return {"check": "cors", "state": "alert", "detail": f"preflight returned {status}, expected 200"}
    allow_origin = fetched.get("allow_origin") or ""
    if origin not in allow_origin:
        return {"check": "cors", "state": "alert", "detail": f"Access-Control-Allow-Origin={allow_origin!r} does not echo {origin!r}"}
    allow_headers = (fetched.get("allow_headers") or "").lower()
    if "authorization" not in allow_headers:
        return {"check": "cors", "state": "alert", "detail": f"Access-Control-Allow-Headers={fetched.get('allow_headers')!r} does not allow Authorization"}
    return {"check": "cors", "state": "ok", "allow_origin": allow_origin, "allow_headers": fetched.get("allow_headers")}


def overall_state(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "unreachable"
    return max((c["state"] for c in checks), key=_severity)


# --------------------------------------------------------------------------
# Run + report
# --------------------------------------------------------------------------

def run(
    host: str,
    expected_ip: str,
    dashboard_origin: str,
    check_cors: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    health_url = f"https://{host}{DEFAULT_HEALTH_PATH}"

    https_check = grade_https(fetch_https_status(health_url))
    cert_check = grade_cert_expiry(fetch_cert_not_after(host), now=now)
    dns_check = grade_dns(resolve_a_records(host), expected_ip)

    checks = [https_check, cert_check, dns_check]

    cors_check = None
    if check_cors:
        cors_url = f"https://{host}{DEFAULT_CORS_PATH}"
        cors_check = grade_cors(fetch_cors_preflight(cors_url, dashboard_origin), dashboard_origin)
        checks.append(cors_check)

    receipt = {
        "generated_at": now.isoformat(),
        "target": {
            "host": host,
            "health_url": health_url,
            "expected_ip": expected_ip,
            "dashboard_origin": dashboard_origin,
            "cors_checked": check_cors,
        },
        "checks": {
            "https": https_check,
            "cert_expiry": cert_check,
            "dns": dns_check,
            "cors": cors_check,
        },
        "overall_state": overall_state(checks),
        "run_state": "completed",
    }
    return receipt


def render_summary(receipt: dict[str, Any]) -> str:
    lines = [f"dashboard-edge-watch: {receipt['overall_state'].upper()} (generated {receipt['generated_at']})"]
    for name in ("https", "cert_expiry", "dns", "cors"):
        c = receipt["checks"].get(name)
        if c is None:
            continue
        detail = c.get("detail") or ""
        extra = ""
        if name == "cert_expiry" and "days_remaining" in c:
            extra = f" days_remaining={c['days_remaining']}"
        if name == "dns" and "observed_ips" in c:
            extra = f" observed={c['observed_ips']} expected={c.get('expected_ip')}"
        if name == "https" and "http_status" in c:
            extra = f" http_status={c.get('http_status')}"
        lines.append(f"  [{c['state']:>11}] {name}{extra} {detail}".rstrip())
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Self-test — plants failures, asserts each produces its alert. No network.
# --------------------------------------------------------------------------

def self_test() -> int:
    failures: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"{label}: {detail}")

    # --- https ---
    check("https/ok", grade_https({"status": 200, "tls_error": None, "error": None})["state"] == "ok")
    check("https/non-200", grade_https({"status": 503, "tls_error": None, "error": None})["state"] == "alert")
    check("https/bad-chain", grade_https({"status": None, "tls_error": "self-signed", "error": None})["state"] == "alert")
    check("https/unreachable", grade_https({"status": None, "tls_error": None, "error": "Connection refused"})["state"] == "unreachable")

    # --- cert expiry ---
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expired = (now - timedelta(days=3)).isoformat()
    near = (now + timedelta(days=3)).isoformat()
    warn = (now + timedelta(days=10)).isoformat()
    healthy = (now + timedelta(days=60)).isoformat()
    check("cert/expired", grade_cert_expiry({"not_after": expired, "error": None}, now=now)["state"] == "alert")
    check("cert/near-expiry-under-7d", grade_cert_expiry({"not_after": near, "error": None}, now=now)["state"] == "alert")
    check("cert/warn-window-10d", grade_cert_expiry({"not_after": warn, "error": None}, now=now)["state"] == "warn")
    check("cert/healthy-60d", grade_cert_expiry({"not_after": healthy, "error": None}, now=now)["state"] == "ok")
    check("cert/unreachable", grade_cert_expiry({"not_after": None, "error": "handshake timed out"}, now=now)["state"] == "unreachable")

    # --- dns ---
    check("dns/match", grade_dns({"ips": ["141.145.193.91"], "error": None}, "141.145.193.91")["state"] == "ok")
    check("dns/wrong-ip", grade_dns({"ips": ["203.0.113.5"], "error": None}, "141.145.193.91")["state"] == "alert")
    check("dns/stale-plus-live", grade_dns({"ips": ["203.0.113.5", "141.145.193.91"], "error": None}, "141.145.193.91")["state"] == "ok")
    check("dns/unreachable", grade_dns({"ips": [], "error": "DNS resolution timed out"}, "141.145.193.91")["state"] == "unreachable")

    # --- cors ---
    origin = "https://benbaichmankass.github.io"
    check("cors/ok", grade_cors({"status": 200, "allow_origin": origin, "allow_headers": "Authorization, Content-Type", "error": None}, origin)["state"] == "ok")
    check("cors/non-200", grade_cors({"status": 400, "allow_origin": None, "allow_headers": None, "error": None}, origin)["state"] == "alert")
    check("cors/wrong-origin", grade_cors({"status": 200, "allow_origin": "https://evil.example", "allow_headers": "Authorization", "error": None}, origin)["state"] == "alert")
    check("cors/no-authorization-header", grade_cors({"status": 200, "allow_origin": origin, "allow_headers": "Content-Type", "error": None}, origin)["state"] == "alert")
    check("cors/unreachable", grade_cors({"status": None, "allow_origin": None, "allow_headers": None, "error": "connection reset"}, origin)["state"] == "unreachable")

    # --- overall rollup never collapses "unreachable" into "ok" ---
    check(
        "overall/unreachable-is-not-healthy",
        overall_state([{"state": "ok"}, {"state": "unreachable"}]) != "ok",
    )
    check(
        "overall/alert-outranks-unreachable",
        overall_state([{"state": "unreachable"}, {"state": "alert"}]) == "alert",
    )
    check("overall/all-ok-is-healthy", overall_state([{"state": "ok"}, {"state": "ok"}]) == "ok")

    if failures:
        print(f"SELF-TEST FAILED ({len(failures)} of {len(failures) + 0} listed failures):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELF-TEST PASSED — every planted failure (expired cert, wrong IP, non-200, "
          "bad TLS chain, missing CORS header, unreachable probe) produced its alert; "
          "healthy fixtures produced ok; unreachable never collapsed into ok.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--expected-ip", default=DEFAULT_EXPECTED_IP)
    p.add_argument("--dashboard-origin", default=DEFAULT_DASHBOARD_ORIGIN)
    p.add_argument("--check-cors", action="store_true", help="also run the optional CORS preflight check")
    p.add_argument("--out", default=None, help="write the JSON receipt to this path")
    p.add_argument("--json", action="store_true", help="print the receipt as JSON instead of the human summary")
    p.add_argument("--self-test", action="store_true", help="run planted-failure assertions, no network, exit 1 on any miss")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        return self_test()

    receipt = run(
        host=args.host,
        expected_ip=args.expected_ip,
        dashboard_origin=args.dashboard_origin,
        check_cors=args.check_cors,
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, indent=2, sort_keys=True)
            fh.write("\n")

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(render_summary(receipt))

    # Exit non-zero whenever the run is not clean healthy, so the workflow's
    # own job status is red on anything worth a human looking — the same
    # convention macro-producer-liveness.yml uses.
    return 0 if receipt["overall_state"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
