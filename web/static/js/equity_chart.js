/* ICT Trading Bot — equity sparkline (S-014 M3 PR #3).
 *
 * Renders a 7-day cumulative-realised P&L line chart into the
 * <canvas id="equity-chart"> element on /home, and refreshes every
 * 5 minutes (daily realised P&L doesn't move tick-by-tick).
 *
 * Data source: GET /api/pnl/history?days=7 — auth-gated by
 * Depends(require_session) on the server. The Authorization header is
 * injected from IctAuth.getToken() (auth.js).
 *
 * Failure modes:
 *   - 401 → clear token + redirect to /login (matches HTMX behaviour
 *     wired in M2 PR #2).
 *   - 403 → log + render empty state ("Not allowlisted").
 *   - Empty points → render empty state ("No P&L history yet").
 *   - Network / 5xx → log + render empty state.
 */
(function () {
  "use strict";

  const HISTORY_URL = "/api/pnl/history?days=7";
  const REFRESH_MS = 5 * 60 * 1000;
  const CANVAS_ID = "equity-chart";
  const LOGIN_PATH = "/login";

  let chartInstance = null;

  function getToken() {
    if (window.IctAuth && typeof window.IctAuth.getToken === "function") {
      return window.IctAuth.getToken();
    }
    try { return window.localStorage.getItem("ict_session_token") || ""; }
    catch (_e) { return ""; }
  }

  function clearTokenAndRedirect() {
    if (window.IctAuth && typeof window.IctAuth.clearToken === "function") {
      window.IctAuth.clearToken();
    }
    window.location.replace(LOGIN_PATH);
  }

  function cumulative(points) {
    let running = 0;
    return points.map(function (p) {
      running += Number(p.realized_usd || 0);
      return { date: p.date, equity: Math.round(running * 100) / 100 };
    });
  }

  function renderEmptyState(canvas, message) {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#888";
    ctx.font = "13px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(message, canvas.width / 2, canvas.height / 2);
  }

  function renderChart(canvas, points) {
    if (typeof window.Chart === "undefined") {
      renderEmptyState(canvas, "Chart library failed to load");
      return;
    }
    const series = cumulative(points);
    const labels = series.map(function (p) { return p.date; });
    const values = series.map(function (p) { return p.equity; });

    if (chartInstance) {
      chartInstance.data.labels = labels;
      chartInstance.data.datasets[0].data = values;
      chartInstance.update("none");
      return;
    }
    chartInstance = new window.Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          label: "Cumulative realised P&L (USD)",
          data: values,
          borderColor: "#4ea3ff",
          backgroundColor: "rgba(78, 163, 255, 0.15)",
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.25,
          fill: true,
        }],
      },
      options: {
        responsive: false,
        animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#aaa", maxTicksLimit: 4 }, grid: { display: false } },
          y: { ticks: { color: "#aaa" }, grid: { color: "rgba(255,255,255,0.06)" } },
        },
      },
    });
  }

  async function loadEquity() {
    const canvas = document.getElementById(CANVAS_ID);
    if (!canvas) return;

    const token = getToken();
    const headers = { "Accept": "application/json" };
    if (token) headers["Authorization"] = "Bearer " + token;

    let resp;
    try {
      resp = await window.fetch(HISTORY_URL, { headers: headers, credentials: "same-origin" });
    } catch (_e) {
      renderEmptyState(canvas, "P&L history unavailable");
      return;
    }

    if (resp.status === 401) {
      clearTokenAndRedirect();
      return;
    }
    if (resp.status === 403) {
      renderEmptyState(canvas, "Not allowlisted");
      return;
    }
    if (!resp.ok) {
      renderEmptyState(canvas, "P&L history unavailable");
      return;
    }

    let payload;
    try { payload = await resp.json(); }
    catch (_e) {
      renderEmptyState(canvas, "P&L history unavailable");
      return;
    }

    const points = (payload && Array.isArray(payload.points)) ? payload.points : [];
    if (points.length === 0) {
      renderEmptyState(canvas, "No P&L history yet");
      return;
    }
    renderChart(canvas, points);
  }

  function start() {
    if (!document.getElementById(CANVAS_ID)) return;
    loadEquity();
    window.setInterval(loadEquity, REFRESH_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.IctEquityChart = { loadEquity: loadEquity };
})();
