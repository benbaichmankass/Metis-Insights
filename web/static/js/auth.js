/* ICT Trading Bot — client-side auth helper.
 *
 * Storage contract:
 *   localStorage key `ict_session_token` holds the JWT issued by
 *   /api/auth/login. M2 PR #1 wires the login form, persists the token,
 *   and schedules a 60-second pre-expiry redirect. M2 PR #2 (separate)
 *   adds the 401-driven HTMX redirect.
 *
 * Always-safe invariants (M1):
 *   1. Every HTMX-originated request carries
 *      `Authorization: Bearer <token>` if a token is in storage.
 *   2. `/home` (and any other auth-required page) redirects to `/login`
 *      immediately if there is no token.
 *   3. The "Sign out" button on /home clears storage and bounces to
 *      /login.
 *
 * M2 PR #1 additions:
 *   4. The login form submits JSON to /api/auth/login via fetch, stores
 *      the returned access_token, and redirects to /home.
 *   5. A pre-expiry timer fires 60 seconds before the JWT `exp` claim
 *      and redirects to /login (no client-side signature check — we
 *      trust the server's issuance).
 *
 * M2 PR #2 additions:
 *   6. `htmx:responseError` handler — on 401, clear the token and
 *      redirect to /login (a server-side session expired before the
 *      pre-expiry timer fired). On 403, surface a "Not allowlisted"
 *      toast so the operator knows the allowlist changed under them.
 *      Other status codes are left to HTMX's default error path.
 */
(function () {
  "use strict";

  const TOKEN_KEY = "ict_session_token";
  const LOGIN_PATH = "/login";
  const HOME_PATH = "/home";
  const LOGIN_API = "/api/auth/login";
  const PRE_EXPIRY_MS = 60 * 1000;

  let expiryTimer = null;

  function getToken() {
    try {
      return window.localStorage.getItem(TOKEN_KEY) || "";
    } catch (_e) {
      return "";
    }
  }

  function setToken(t) {
    try { window.localStorage.setItem(TOKEN_KEY, t || ""); }
    catch (_e) { /* ignore */ }
  }

  function clearToken() {
    try {
      window.localStorage.removeItem(TOKEN_KEY);
    } catch (_e) {
      /* ignore */
    }
  }

  function decodeJwtPayload(token) {
    if (!token || typeof token !== "string") return null;
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    let b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    try {
      const json = window.atob(b64);
      return JSON.parse(json);
    } catch (_e) {
      return null;
    }
  }

  function scheduleExpiryRedirect() {
    if (expiryTimer) {
      window.clearTimeout(expiryTimer);
      expiryTimer = null;
    }
    const claims = decodeJwtPayload(getToken());
    if (!claims || typeof claims.exp !== "number") return;
    const msUntilExp = claims.exp * 1000 - Date.now();
    const fireIn = msUntilExp - PRE_EXPIRY_MS;
    if (fireIn <= 0) {
      clearToken();
      if (window.location.pathname !== LOGIN_PATH) {
        window.location.replace(LOGIN_PATH);
      }
      return;
    }
    expiryTimer = window.setTimeout(function () {
      clearToken();
      window.location.replace(LOGIN_PATH);
    }, fireIn);
  }

  function onConfigRequest(evt) {
    const token = getToken();
    if (token) {
      evt.detail.headers["Authorization"] = "Bearer " + token;
    }
  }

  const TOAST_MS = 4000;

  function showToast(msg) {
    let el = document.getElementById("ict-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "ict-toast";
      el.className = "ict-toast";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("ict-toast--visible");
    if (showToast._t) window.clearTimeout(showToast._t);
    showToast._t = window.setTimeout(function () {
      el.classList.remove("ict-toast--visible");
    }, TOAST_MS);
  }

  function onResponseError(evt) {
    const xhr = evt && evt.detail && evt.detail.xhr;
    if (!xhr) return;
    if (xhr.status === 401) {
      clearToken();
      window.location.replace(LOGIN_PATH);
      return;
    }
    if (xhr.status === 403) {
      showToast("Not allowlisted");
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

  function showLoginError(msg) {
    const el = document.getElementById("login-error");
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
  }

  function clearLoginError() {
    const el = document.getElementById("login-error");
    if (!el) return;
    el.textContent = "";
    el.hidden = true;
  }

  async function submitLogin(form) {
    clearLoginError();
    const fd = new window.FormData(form);
    const email = (fd.get("email") || "").toString().trim();
    const password = (fd.get("password") || "").toString();
    let resp;
    try {
      resp = await window.fetch(LOGIN_API, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ email: email, password: password }),
        credentials: "same-origin",
      });
    } catch (_e) {
      showLoginError("Service unavailable. Try again.");
      return;
    }
    if (resp.status === 401) {
      showLoginError("Invalid credentials.");
      return;
    }
    if (resp.status === 403) {
      showLoginError("Not allowlisted.");
      return;
    }
    if (!resp.ok) {
      showLoginError("Service unavailable. Try again.");
      return;
    }
    let payload;
    try { payload = await resp.json(); }
    catch (_e) {
      showLoginError("Service unavailable. Try again.");
      return;
    }
    const token = payload && payload.access_token;
    if (!token) {
      showLoginError("Service unavailable. Try again.");
      return;
    }
    setToken(token);
    window.location.replace(HOME_PATH);
  }

  function wireLoginForm() {
    const form = document.getElementById("login-form");
    if (!form) return;
    form.addEventListener("submit", function (evt) {
      evt.preventDefault();
      submitLogin(form);
    });
  }

  document.addEventListener("htmx:configRequest", onConfigRequest);
  document.addEventListener("htmx:responseError", onResponseError);
  document.addEventListener("DOMContentLoaded", function () {
    gateHomePage();
    wireLogout();
    wireLoginForm();
    scheduleExpiryRedirect();
  });

  /* Exposed for tests + login wiring + HTMX response-error coverage. */
  window.IctAuth = {
    getToken: getToken,
    clearToken: clearToken,
    setToken: setToken,
    decodeJwtPayload: decodeJwtPayload,
    scheduleExpiryRedirect: scheduleExpiryRedirect,
    submitLogin: submitLogin,
    onResponseError: onResponseError,
    showToast: showToast,
    TOKEN_KEY: TOKEN_KEY,
    LOGIN_PATH: LOGIN_PATH,
    HOME_PATH: HOME_PATH,
    LOGIN_API: LOGIN_API,
    PRE_EXPIRY_MS: PRE_EXPIRY_MS,
    TOAST_MS: TOAST_MS,
  };
})();
