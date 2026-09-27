"""E52 — tests for the outside-in dashboard edge-watch probe.

No network: exercises only the GRADE layer (pure functions over already-fetched
data), mirroring the ``--self-test`` planted-failure assertions the script
carries for a CI runner that has no Python test-discovery of its own. The FETCH
layer (real HTTPS/TLS/DNS I/O) is deliberately not exercised here — it is
proven live by running the script against ict-bot.duckdns.org, not by a unit
test that would otherwise need to fake sockets.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta, timezone

_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ops"
)


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(_DIR, name + ".py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watch = _load("dashboard_edge_watch")

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# grade_https
# ---------------------------------------------------------------------------


def test_https_200_is_ok():
    r = watch.grade_https({"status": 200, "tls_error": None, "error": None})
    assert r["state"] == "ok"


def test_https_non_200_is_alert():
    r = watch.grade_https({"status": 503, "tls_error": None, "error": None})
    assert r["state"] == "alert"
    assert "503" in r["detail"]


def test_https_bad_chain_is_alert_not_unreachable():
    r = watch.grade_https({"status": None, "tls_error": "self-signed certificate", "error": None})
    assert r["state"] == "alert"


def test_https_connection_error_is_unreachable_not_ok():
    r = watch.grade_https({"status": None, "tls_error": None, "error": "Connection refused"})
    assert r["state"] == "unreachable"


# ---------------------------------------------------------------------------
# grade_cert_expiry — the collapsed-state contract: warn/alert thresholds and
# "we could not look" never reads as healthy.
# ---------------------------------------------------------------------------


def test_cert_expired_is_alert():
    not_after = (_NOW - timedelta(days=1)).isoformat()
    r = watch.grade_cert_expiry({"not_after": not_after, "error": None}, now=_NOW)
    assert r["state"] == "alert"
    assert r["days_remaining"] < 0


def test_cert_under_7_days_is_alert():
    not_after = (_NOW + timedelta(days=6)).isoformat()
    r = watch.grade_cert_expiry({"not_after": not_after, "error": None}, now=_NOW)
    assert r["state"] == "alert"


def test_cert_under_14_over_7_days_is_warn():
    not_after = (_NOW + timedelta(days=10)).isoformat()
    r = watch.grade_cert_expiry({"not_after": not_after, "error": None}, now=_NOW)
    assert r["state"] == "warn"


def test_cert_healthy_window_is_ok():
    not_after = (_NOW + timedelta(days=60)).isoformat()
    r = watch.grade_cert_expiry({"not_after": not_after, "error": None}, now=_NOW)
    assert r["state"] == "ok"


def test_cert_handshake_failure_is_unreachable():
    r = watch.grade_cert_expiry({"not_after": None, "error": "handshake timed out"}, now=_NOW)
    assert r["state"] == "unreachable"


# ---------------------------------------------------------------------------
# grade_dns
# ---------------------------------------------------------------------------


def test_dns_matching_ip_is_ok():
    r = watch.grade_dns({"ips": ["141.145.193.91"], "error": None}, "141.145.193.91")
    assert r["state"] == "ok"


def test_dns_wrong_ip_is_alert():
    r = watch.grade_dns({"ips": ["203.0.113.5"], "error": None}, "141.145.193.91")
    assert r["state"] == "alert"
    assert r["observed_ips"] == ["203.0.113.5"]


def test_dns_resolution_failure_is_unreachable_not_ok():
    r = watch.grade_dns({"ips": [], "error": "DNS resolution timed out"}, "141.145.193.91")
    assert r["state"] == "unreachable"


# ---------------------------------------------------------------------------
# grade_cors (optional check 4)
# ---------------------------------------------------------------------------

_ORIGIN = "https://benbaichmankass.github.io"


def test_cors_healthy_preflight_is_ok():
    r = watch.grade_cors(
        {"status": 200, "allow_origin": _ORIGIN, "allow_headers": "Authorization, Content-Type", "error": None},
        _ORIGIN,
    )
    assert r["state"] == "ok"


def test_cors_non_200_is_alert():
    r = watch.grade_cors({"status": 403, "allow_origin": None, "allow_headers": None, "error": None}, _ORIGIN)
    assert r["state"] == "alert"


def test_cors_missing_authorization_header_is_alert():
    r = watch.grade_cors(
        {"status": 200, "allow_origin": _ORIGIN, "allow_headers": "Content-Type", "error": None},
        _ORIGIN,
    )
    assert r["state"] == "alert"


# ---------------------------------------------------------------------------
# overall_state — never collapses "we could not look" into "healthy"
# ---------------------------------------------------------------------------


def test_overall_all_ok_is_ok():
    assert watch.overall_state([{"state": "ok"}, {"state": "ok"}]) == "ok"


def test_overall_unreachable_check_is_not_ok():
    assert watch.overall_state([{"state": "ok"}, {"state": "unreachable"}]) != "ok"


def test_overall_alert_outranks_unreachable_and_warn():
    assert watch.overall_state([{"state": "warn"}, {"state": "unreachable"}, {"state": "alert"}]) == "alert"


def test_self_test_entrypoint_passes():
    assert watch.self_test() == 0
