/* ICT Trading Bot — client-side auth helper.
 *
 * Storage contract:
 *   localStorage key `ict_session_token` holds the JWT issued by
 *   /api/auth/login. The full login wiring (M2 PR #1) and 401-driven
 *   logout/redirect (M2 PR #2) live in PM-review PRs; this scaffold only
 *   covers the always-safe invariants:
 *
 *     1. Every HTMX-originated request carries
 *        `Authorization: Bearer <token>` if a token is in storage.
 *     2. `/home` (and any other auth-required page) redirects to `/login`
 *        immediately if there is no token. The server-side gate is M3.
 *     3. The "Sign out" button on /home clears storage and bounces to
 *        /login.
 */
(function () {
  "use strict";

  const TOKEN_KEY = "ict_session_token";
  const LOGIN_PATH = "/login";
  const HOME_PATH = "/home";

  function getToken() {
    try {
      return window.localStorage.getItem(TOKEN_KEY) || "";
    } catch (_e) {
      return "";
    }
  }

  function clearToken() {
    try {
      window.localStorage.removeItem(TOKEN_KEY);
    } catch (_e) {
      /* ignore */
    }
  }

  function onConfigRequest(evt) {
    const token = getToken();
    if (token) {
      evt.detail.headers["Authorization"] = "Bearer " + token;
    }
  }

  function gateHomePage() {
    if (window.location.pathname !== HOME_PATH) return;
    if (!getToken()) {
      window.location.replace(LOGIN_PATH);
    }
  }

  function wireLogout() {
    const btn = document.getElementById("logout-btn");
    if (!btn) return;
    btn.addEventListener("click", function () {
      clearToken();
      window.location.replace(LOGIN_PATH);
    });
  }

  document.addEventListener("htmx:configRequest", onConfigRequest);
  document.addEventListener("DOMContentLoaded", function () {
    gateHomePage();
    wireLogout();
  });

  /* Exposed for M2 PR #1 (login form submission) and tests. */
  window.IctAuth = {
    getToken: getToken,
    clearToken: clearToken,
    setToken: function (t) {
      try { window.localStorage.setItem(TOKEN_KEY, t || ""); } catch (_e) { /* ignore */ }
    },
    TOKEN_KEY: TOKEN_KEY,
  };
})();
