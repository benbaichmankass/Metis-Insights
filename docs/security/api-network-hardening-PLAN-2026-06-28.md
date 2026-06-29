# Tier-2 Hardening Plan — Public API auth + network exposure (2026-06-28)

> **Status:** PROPOSAL for operator approval. **Nothing here is enacted.** These
> are Tier-2 changes (runtime code in `src/web/api/`, VM firewall/proxy) — per
> `docs/CLAUDE-RULES-CANONICAL.md` they require operator approval *and* proof-of-
> safety before merge/deploy. This doc gives the exact changes, the breakage
> analysis, the prerequisites, and a safe sequencing.
>
> Companion to [`intrusion-surface-audit-2026-06-28.md`](intrusion-surface-audit-2026-06-28.md)
> §5/§8 (the workflow actor-guards from that audit shipped separately as Tier-1
> PRs #4965 + #4967). This doc covers the two Tier-2 items the operator asked to
> scope: **API auth** and **network/firewall** for `http://141.145.193.91:8001`.

---

## 0. Prerequisite that blocks the API-auth flip (must resolve first)

The dashboard's "token-gated" endpoints (`POST /prop/report`, `GET /devices`,
`DELETE /devices/{id}`, `PATCH /devices/{id}/subscriptions`) **fail OPEN when
`DASHBOARD_API_TOKEN` is unset** (`auth.py` permissive-when-unset;
`devices.py:66-82`, `prop.py:43-51`). Making them **fail-closed** is only safe
**after confirming the token is set on the VM** — otherwise the flip instantly
breaks the dashboard's prop-report write and the device endpoints.

**I could not confirm the VM's `DASHBOARD_API_TOKEN` state from this session**
(egress to `141.145.193.91:8001` is firewalled from the sandbox — a direct
`curl` timed out; the read-only diag relay only reaches `/api/diag/*`, not
`/api/bot/*` or the env). **Operator/next-session action — confirm via ONE of:**

- Behavioural probe from a host that can reach the VM (or extend a one-off diag
  step): `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/api/bot/devices`
  **on the VM** — `200` ⇒ token UNSET (fail-open, the bad state); `401` ⇒ set.
- Or check `/etc/ict-trader/web-api.env` on the VM for `DASHBOARD_API_TOKEN=`.

If unset, set it first via `sync-vm-secrets` (declare `DASHBOARD_API_TOKEN` in
its secret set) + restart `ict-web-api`, **and** configure the two consumers
(below) to send it — *then* flip fail-closed.

---

## 1. API auth changes (`src/web/api/`)

Ranked by safety. (1a) is non-breaking and shippable now; (1b)/(1c) are breaking
and need the prereq + consumer coordination.

### 1a. Timing-safe token compare — NON-breaking, ship anytime (Tier-2, low risk)
The dashboard token is compared with plain `!=` (`prop.py:50`, `devices.py:81`),
unlike the diag path / password path which use `hmac.compare_digest`. Switch
both to `hmac.compare_digest(presented, expected)`. No behavioural change when
the token is set or unset — pure hardening. **This is the one piece safe to ship
immediately** (still Tier-2 as it touches `src/web/api/`, so operator OK first).

### 1b. Require auth on `POST /api/bot/devices/register` — BREAKING (needs android coordination)
Today this write is **unauthenticated unconditionally** (`devices.py:140`): anyone
can register an arbitrary FCM token. Options, in order of preference:
- **Preferred:** require the same `DASHBOARD_API_TOKEN` bearer (add the
  `_check_admin_token` call, made mandatory per 1c). **Breaking for the Android
  app**, which currently posts with no token — so it must ship **with** an
  Android change to send the bearer (a new `BOT_API_TOKEN` build secret +
  `BotApiFactory` header). Cross-repo, coordinated release.
- **Lighter alternative (no app change):** keep it open but add **abuse
  controls** — per-IP rate limit + a cap on `device_tokens` rows + dedup on
  token (already idempotent) — so the worst case is bounded table pollution, not
  push-subscription hijack. Pairs well with the reverse proxy (§2) doing the
  rate-limit.
- Either way: the endpoint only ever exposes `token_suffix`, never raw tokens,
  so the read-side leak is limited; the write-side abuse is the real issue.

### 1c. Make `DASHBOARD_API_TOKEN` fail-CLOSED — BREAKING (needs §0 + consumer config)
Change the permissive-when-unset helper so an unset token = **deny** (mirror the
diag model: 503/`misconfigured` when unset, 401 on bad bearer). Gate: only after
§0 confirms the token is set AND both consumers send it:
- **Dashboard (Streamlit):** already sends the bearer for prop-report when
  `DASHBOARD_API_TOKEN` is in its Secrets (per dashboard CLAUDE.md); confirm it's
  populated.
- **Android:** must send it for the device endpoints (the §1b change).

### 1d. Optionally gate the sensitive unauthenticated READs (Tier-2/3, policy call)
`/api/bot/accounts/balances` + `/api/bot/positions` expose real-money balances/
positions with no auth (Tier-1 *by design*). If §2 (reverse proxy + TLS) lands,
the cleanest is to require the bearer on these too and have the dashboard send
it. This is a **policy decision** (it changes the documented Tier-1 read
contract) — flag for operator, don't do unilaterally.

---

## 2. Network / transport (VM)

**Reality check that kills the naive "firewall to Streamlit Cloud":** Streamlit
Community Cloud does **not** publish stable egress IPs, so an OCI security-list
allowlist for the dashboard is **not viable** — it would break on every
Streamlit infra rotation. The exposure must be closed with **auth + TLS**, not
IP-allowlisting. Recommended target state:

1. **Bind uvicorn to loopback** — run `ict-web-api` on `127.0.0.1:8001` (add
   `--host 127.0.0.1` in the unit's ExecStart) so the app port is not on a
   public NIC at all.
2. **TLS reverse proxy in front** — Caddy (auto-TLS via Let's Encrypt on a
   hostname) or nginx terminating HTTPS on 443, proxying to `127.0.0.1:8001`.
   This removes the plain-HTTP transport (today every bearer + JWT travels
   cleartext) and gives one place to enforce a bearer + **rate limiting** (the
   §1b abuse control) + basic request logging (the §3 detection feed).
3. **OCI security list:** then close inbound 8001 entirely; open only 443 (proxy)
   — and optionally restrict 443 to the operator IP for the `/api/diag/*` +
   admin paths while leaving the dashboard read paths open, via proxy path rules.
4. **Update consumers** to the new HTTPS base URL: dashboard `BOT_API_URL`
   (Streamlit secret) and Android `DEFAULT_BOT_URL` + the cleartext-HTTP
   allowlist in `network_security_config.xml` (becomes unnecessary once HTTPS).

All of the above are **Tier-2** (unit/deploy/infra) and should ship as an
allowlisted `system-actions` wrapper + runbook (per the Ship-Autonomously rule),
not a manual SSH session — but only on operator approval.

---

## 3. Detection (Tier-1, can ship independently)

From the audit §7 — pairs with this work:
- **External-issue alert** workflow: ping the Claude channel when an issue is
  opened by anyone other than `benbaichmankass`/`github-actions[bot]`, especially
  matching a dispatch label/title pattern. (Highest-value, cheap.)
- Once §2's reverse proxy lands, feed its access log a simple **anomaly alert**
  (burst of 4xx, unexpected write attempts) to Telegram.

---

## 4. Recommended sequence

1. **Now (Tier-1, independent):** detection — external-issue alert workflow.
2. **Now (Tier-2, non-breaking, operator OK):** §1a timing-safe compare.
3. **Confirm §0** (VM token state). If unset → set via `sync-vm-secrets` + ensure
   dashboard Secret populated.
4. **§2 reverse proxy + TLS + loopback bind** (Tier-2) — the biggest single risk
   reducer (removes plain-HTTP world-readable surface); also unlocks rate-limit.
5. **§1c fail-closed** once §0 done and consumers send the token.
6. **§1b devices/register** auth or abuse-controls (coordinate Android release).
7. **§1d** sensitive-read gating — operator policy call.

## 5. What this PR contains

**Only this doc.** No `src/` or unit changes — those await operator approval per
the sequence above. The one immediately-safe code change (§1a, timing-safe
compare) is described precisely here and can be cut as a tiny follow-up PR on
the operator's go-ahead.
