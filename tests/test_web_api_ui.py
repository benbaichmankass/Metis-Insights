"""S-014 M1 PR #2 — UI route + static-mount tests.

Covers the contract:

* ``GET /static/css/app.css`` → 200 + CSS content.
* ``GET /static/js/htmx.min.js`` → 200 + JS content (banner + body).
* ``GET /login`` → 200 + HTML, references ``auth.js``.
* ``GET /home`` → 200 + HTML (server-side public; auth.js gates
  client-side). Must reference ``htmx.min.js`` and ``chart.umd.js``.
* ``GET /`` → 307 → ``/home``.
* ``PUBLIC_ROUTES`` documents ``/``, ``/login``; ``/static/*`` covered
  by ``PUBLIC_PREFIXES``.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.web.api import auth as auth_module
from src.web.api import main as api_main


def _client() -> TestClient:
    return TestClient(api_main.app, raise_server_exceptions=False)


def test_static_css_is_served():
    resp = _client().get("/static/css/app.css")
    assert resp.status_code == 200
    assert "ICT Trading Bot" in resp.text
    assert "--bg-card" in resp.text


def test_static_htmx_js_is_served():
    resp = _client().get("/static/js/htmx.min.js")
    assert resp.status_code == 200
    # SHA-256 banner is the first thing in the file; htmx body follows.
    assert "HTMX 2.0.4" in resp.text
    assert "var htmx" in resp.text


def test_static_chart_js_is_served():
    resp = _client().get("/static/js/chart.umd.js")
    assert resp.status_code == 200
    assert "Chart.js v4.4.7" in resp.text


def test_static_equity_chart_js_is_served():
    resp = _client().get("/static/js/equity_chart.js")
    assert resp.status_code == 200
    body = resp.text
    assert "/api/pnl/history?days=7" in body
    assert "ict_session_token" in body or "IctAuth" in body
    assert "equity-chart" in body


def test_login_page_renders_html():
    resp = _client().get("/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "<form" in body
    assert 'id="login-form"' in body
    assert "/api/auth/login" in body
    assert 'id="login-error"' in body
    # Loaded via base.html — auth.js must be referenced.
    assert "/static/js/auth.js" in body
    assert "/static/css/app.css" in body


def test_auth_js_wires_login_form_and_pre_expiry_timer():
    """S-014 M2 PR #1 contract: auth.js must (a) intercept the /login
    form submit, post JSON to /api/auth/login, store the access_token in
    localStorage, and redirect to /home; (b) schedule a redirect to
    /login PRE_EXPIRY_MS before the JWT exp claim."""
    resp = _client().get("/static/js/auth.js")
    assert resp.status_code == 200
    body = resp.text
    assert "/api/auth/login" in body
    assert "login-form" in body
    assert "access_token" in body
    assert "/home" in body
    assert "decodeJwtPayload" in body
    assert "scheduleExpiryRedirect" in body
    assert "PRE_EXPIRY_MS" in body
    assert "setTimeout" in body
    assert "ict_session_token" in body


def test_auth_js_wires_htmx_response_error_handler():
    """S-014 M2 PR #2 contract: auth.js must listen for
    ``htmx:responseError`` and on 401 clear the token + redirect to
    /login; on 403 surface a "Not allowlisted" toast."""
    resp = _client().get("/static/js/auth.js")
    assert resp.status_code == 200
    body = resp.text
    assert "htmx:responseError" in body
    assert "onResponseError" in body
    assert "Not allowlisted" in body
    assert "showToast" in body
    assert "ict-toast" in body


def test_app_css_includes_toast_styles():
    resp = _client().get("/static/css/app.css")
    assert resp.status_code == 200
    body = resp.text
    assert ".ict-toast" in body
    assert ".ict-toast--visible" in body


def test_home_page_renders_without_server_side_auth():
    """``/home`` is *not* in PUBLIC_ROUTES for documentation reasons —
    it's still served without ``require_session`` because auth.js
    handles the redirect-to-login gate client-side. The HTMX fragments
    the page lazy-loads are server-side ``require_session``."""
    resp = _client().get("/home")
    assert resp.status_code == 200
    body = resp.text
    assert "/static/js/htmx.min.js" in body
    assert "/static/js/chart.umd.js" in body
    assert "/static/js/auth.js" in body
    assert "/static/js/equity_chart.js" in body
    # Wired HTMX fragment containers — M3 surface.
    assert "/ui/fragments/status" in body
    assert "/ui/fragments/pnl" in body
    assert 'id="equity-chart"' in body


def test_root_redirects_to_home():
    # follow_redirects=False so we observe the 307 directly.
    resp = _client().get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/home"


def test_public_routes_documents_login_and_root():
    assert "/login" in auth_module.PUBLIC_ROUTES
    assert "/" in auth_module.PUBLIC_ROUTES
    assert "/static/" in auth_module.PUBLIC_PREFIXES


def test_static_mount_404_on_missing_file():
    """StaticFiles must not silently fall through to /home — missing
    files under /static must 404."""
    resp = _client().get("/static/js/does-not-exist.js")
    assert resp.status_code == 404
