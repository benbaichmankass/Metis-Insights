// Cloudflare Worker — proxies /api/* from a stable *.workers.dev hostname
// to the bot's FastAPI on the Oracle VM.
//
// Why this exists: Vercel rewrites + Vercel Edge Functions cannot reach
// `http://158.178.210.252:8001` (plain HTTP, raw IP+port). Cloudflare
// Workers' fetch has no such restriction. See
// docs/audit/vercel-edge-vs-cf-worker.md for the investigation trail.
//
// Routing:
//   /api/*              → ${ORIGIN}/api/* (method, headers, body forwarded)
//   /__worker/health    → Worker-side liveness probe (does NOT hit origin)
//   anything else       → 404
//
// ORIGIN is wired from `[vars]` in wrangler.toml so the operator can
// repoint the proxy without re-deploying source.

const STRIPPED_REQUEST_HEADERS = new Set([
  "host",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-proto",
  "x-real-ip",
  "cdn-loop",
]);

function filterRequestHeaders(headers) {
  const filtered = new Headers();
  for (const [name, value] of headers) {
    const lower = name.toLowerCase();
    if (lower.startsWith("cf-")) continue;
    if (STRIPPED_REQUEST_HEADERS.has(lower)) continue;
    filtered.set(name, value);
  }
  return filtered;
}

async function proxy(request, origin) {
  const inboundUrl = new URL(request.url);
  const upstreamUrl = origin.replace(/\/+$/, "") + inboundUrl.pathname + inboundUrl.search;

  const upstreamInit = {
    method: request.method,
    headers: filterRequestHeaders(request.headers),
    redirect: "manual",
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    upstreamInit.body = request.body;
  }

  let upstreamResponse;
  try {
    upstreamResponse = await fetch(upstreamUrl, upstreamInit);
  } catch (err) {
    return new Response(
      JSON.stringify({
        error: "upstream_unreachable",
        upstream: upstreamUrl,
        detail: String(err && err.message ? err.message : err),
      }),
      {
        status: 502,
        headers: { "content-type": "application/json" },
      },
    );
  }

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: upstreamResponse.headers,
  });
}

export default {
  async fetch(request, env) {
    const origin = (env && env.ORIGIN) || "http://158.178.210.252:8001";
    const url = new URL(request.url);

    if (url.pathname === "/__worker/health") {
      return new Response(
        JSON.stringify({ ok: true, origin, worker: "ict-trader-bot-proxy" }),
        { headers: { "content-type": "application/json" } },
      );
    }

    if (!url.pathname.startsWith("/api/")) {
      return new Response("Not found", { status: 404 });
    }

    return proxy(request, origin);
  },
};
