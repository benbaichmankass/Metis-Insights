# Restoring the SPA auth gate (MI-266) — the ordered steps, and what to MEASURE

> **Doc status:** `live` · category `lookup` · last verified `2026-09-11` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**This runbook is WRITTEN, NOT EXECUTED.** Nothing in it has been run. It exists
so that the moment the operator has minted the two secret values, restoring the
gated surface is one ordered sequence rather than a project.

---

## 0. What is actually broken, and what is not

Settled by #11780 and re-derived 2026-09-11 for this runbook:

`src/web/api/auth.py` reads three envs **per call** — `JWT_SIGNING_KEY` (line
67), `ALLOWED_EMAIL` (76), `WEBAPP_PASSWORD_SHA256` (83). Any one missing makes
`auth.py` raise `auth misconfigured`. `POST /api/auth/login` is the **only** mint
path and `decode_token` has **no static-token bypass**, so with the envs absent
the gate is closed and there is no way through it.

**The gated surface is FOUR handlers, not the two usually cited:**

| route | file | called by the SPA? |
|---|---|---|
| `GET /api/bot/db/tables` | `routers/db_explorer.py:258` | **yes** — Data Explorer |
| `GET /api/bot/db/table/{name}` | `routers/db_explorer.py:306` | **yes** — Data Explorer |
| `GET /api/pnl` | `routers/pnl.py:211` | **no** |
| `GET /api/status` | `routers/status.py:38` | **no** |

So the operator's picture is **correct**: Data Explorer is the only dark SPA
tab. `/api/pnl` and `/api/status` are gated too, but nothing on the SPA calls
them, which is why their darkness went unnoticed. Record this rather than
rediscovering it.

⚠️ **`db_explorer.py`'s fail-closed behaviour is CORRECT and must not be
touched.** Before the gate, an unauthenticated `GET /api/bot/db/table/trades`
returned real rows over 41 columns against `total: 5589` on a public host.
Failing closed on the money DB is the right side to err on. Reverting it to
"make the tab work" would be the worst available outcome.

---

## 1. The operator originates two values (and only two)

This is the one genuine hand-off — a session cannot mint these.

| Actions secret | what it is | how to generate |
|---|---|---|
| `JWT_SIGNING_KEY` | HS256 signing key | `openssl rand -hex 32` |
| `WEBAPP_PASSWORD_SHA256` | SHA-256 hex of the chosen login password | `printf '%s' '<password>' \| shasum -a 256` |

Put both into **repo → Settings → Secrets and variables → Actions**, under
exactly those names. `init-actions-secrets.yml` can pre-create the empty slots
so they are filled rather than created.

⚠️ **`ALLOWED_EMAIL` is NOT a secret and is NOT part of this step.** It is
already written in the clear at `deploy-candidate.yml:137`
(`ALLOWED_EMAIL=ben.baichmankass@gmail.com`). It rides `env_value` normally in
step 2. Treating it as a secret would make this ask larger than it is — the
operator originates **two** values, not three.

⚠️ **Note the password itself is never stored anywhere.** Only its SHA-256 goes
to Actions. The operator must keep the plaintext; losing it means re-minting the
hash, not recovering it.

---

## 2. Three `set-env` invocations (Tier-2 — needs the operator's OK)

All three target **`env_file: web-api`**, not the default `shared`.
`ict-web-api.service` is the consuming unit and loads
`/etc/ict-trader/web-api.env`; a `shared` write would also hand these keys to
`ict-trader-live.service`, which has no use for them. Both the `web-api` target
and `ict-web-api.service` are already allowlisted in `scripts/ops/set_env.sh` —
no allowlist change is needed.

Open three `system-action`-labelled issues, **in this order**. The first two
carry **no `env_value`** — that is the whole point of the `SECRET_*` mapping:
the value is pulled from Actions and never transits the (public) issue body or
the run log.

**(a)** — note the deliberately absent `env_value`:
```
action: set-env
env_key: JWT_SIGNING_KEY
service: none
env_file: web-api
reason: MI-266 restore SPA auth gate — mint path for /api/auth/login
```

**(b)** — likewise:
```
action: set-env
env_key: WEBAPP_PASSWORD_SHA256
service: none
env_file: web-api
reason: MI-266 restore SPA auth gate — password hash
```

**(c)** — this one **does** carry its value, because it is not a secret:
```
action: set-env
env_key: ALLOWED_EMAIL
env_value: ben.baichmankass@gmail.com
service: ict-web-api
env_file: web-api
reason: MI-266 restore SPA auth gate — allowlisted operator email
```

**Why `service: none` on (a) and (b) and the restart only on (c):** each
`set-env` restarts its named service, so restarting on all three would bounce
`ict-web-api` three times and — worse — the first two bounces would land the
service in a **half-configured** state (one env present, the others still
absent), which still serves 500 and makes an impatient probe read as failure.
Write all three, restart once, at the end.

⚠️ **If a run FAILS with `resolved value is EMPTY`, that is the mechanism
working, not a bug.** It means the Actions secret slot is still empty. Fill it
and re-run. **Do not weaken the guard**, and do not pass the value inline as
`env_value` to get around it — that would put the secret in a public issue body,
which is exactly what the `SECRET_*` mapping exists to prevent.

---

## 3. What to MEASURE — the part that is easy to get wrong

⚠️ **An empty or malformed login body returns `422` from Pydantic validation
BEFORE the handler runs.** #11780 recorded exactly this: a probe using an empty
body would have graded the host healthy while it was broken. **A 422 measures
nothing about auth.** Every probe below sends a **well-formed** body:
`{"email": "<a valid email>", "password": "<a string>"}`.

### 3.1 The status ladder — what each code proves

Read `routers/auth.py:30-66`. The handler checks in a fixed order, so the
status code says precisely how far it got:

| code | body `error` | what it PROVES |
|---|---|---|
| `422` | (Pydantic) | **nothing** — the request never reached the handler |
| `500` | `auth_unavailable` | `ALLOWED_EMAIL` or `WEBAPP_PASSWORD_SHA256` still missing |
| `403` | `email_not_allowlisted` | ✅ both of those are **present** — the handler got past its config read |
| `401` | `invalid_credentials` | ✅ both present **and** the email matched |
| `500` *after* a confirmed 401 | `auth_unavailable` | `JWT_SIGNING_KEY` is the one still missing |
| `200` | — (`access_token`) | ✅ all three present; auth **mints** |

### 3.2 The credential-free probe — run this FIRST

This is the useful one: it proves two of the three envs **without the probe
holding any secret**, so any session can run it.

```bash
curl -sS -o /dev/stdin -w '\nHTTP %{http_code}\n' \
  -X POST 'https://ict-bot.duckdns.org/api/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"email":"ben.baichmankass@gmail.com","password":"deliberately-wrong"}'
```

- **`401 invalid_credentials`** → `ALLOWED_EMAIL` and `WEBAPP_PASSWORD_SHA256`
  are both live on the host. Steps (b) and (c) landed.
- **`500 auth_unavailable`** → at least one of them is still missing. Go back to
  step 2; do not proceed.
- **`403 email_not_allowlisted`** → both envs are present but `ALLOWED_EMAIL`
  holds a *different* value than the one probed. A real finding, and a
  different fix from a 500.

⚠️ **A 401 does NOT prove `JWT_SIGNING_KEY` is set.** The handler never reaches
`issue_token` on a bad password. Only §3.3 can establish that half — so do not
stop here and report the gate restored.

### 3.3 The mint check — "auth now mints a session"

Same request with the **real** password. Expect **`200`** and a body carrying
`access_token`. ⚠️ Run this where the password is not logged — not in a workflow
run log, not in an issue body. If you get a `500 auth_unavailable` **here**
having already seen a `401` in §3.2, the missing env is specifically
`JWT_SIGNING_KEY` (step 2a did not land).

### 3.4 The gate check — the gate accepts what auth minted

```bash
TOKEN='<access_token from 3.3>'
curl -sS -o /dev/null -w 'tables: %{http_code}\n' \
  -H "Authorization: Bearer ${TOKEN}" \
  'https://ict-bot.duckdns.org/api/bot/db/tables'
```
Expect **`200`**. A `401 invalid_session` here with a fresh token means the
signing key the gate verifies with differs from the one that signed — i.e. the
web-api did not restart after step 2a, or restarted between the two writes.

**Also confirm the gate still REFUSES** — the fail-closed half is a separate
fact and is the one that matters most:
```bash
curl -sS -o /dev/null -w 'no-bearer: %{http_code}\n' \
  'https://ict-bot.duckdns.org/api/bot/db/table/trades'
```
Expect **`401`**. A `200` here means the money DB is open to the public
internet again — stop and escalate immediately.

### 3.5 Deploy confirmation

`set-env` writes the file; systemd only re-reads it on restart. Confirm the
restart actually happened before believing any negative above:
```bash
curl -sS -H "Authorization: Bearer ${DIAG_READ_TOKEN}" \
  'https://ict-bot.duckdns.org/api/diag/services' | grep -i web-api
```

---

## 4. ⚠️ Steps 1–3 do NOT make the tab render, and that is a SEPARATE fact

**Do not report "the Data Explorer is back" on the strength of §3.4.** A `200`
from `curl` proves the *host* mints and the *gate* accepts. It says nothing
about the SPA, because **the SPA cannot send a bearer at all.**

Measured 2026-09-11 against `benbaichmankass/ict-trader-dashboard` (population:
all 40 `.svelte`/`.ts`/`.js` files under `webapp/src`; positive control: 5 files
contain `fetch`): there is **no login UI and no token store**. The only
`localStorage` use is `webapp/src/lib/config.ts`, which stores the API base URL.

⚠️ **Point any such probe at `webapp/src`, not `src/`** — `src/` does not exist
in that repo, and a grep against it returns zero **with a zero positive
control**, which reads identically to "no auth code found".

**Size of the remaining work: a form and a localStorage token — NOT an auth
build.** The SPA has exactly ONE network chokepoint, `get<T>()` at
`webapp/src/lib/api.ts:126-130`, through which all ~19 REST paths pass; there is
no other `fetch`, no XHR, no axios. So the work is:

1. a token store mirroring `config.ts`'s existing localStorage shape;
2. one `Authorization: Bearer` header line inside `get<T>()`;
3. a login form (the Settings tab already exists to host it);
4. a 401 handler that surfaces the form.

⚠️ **One thing that would flip that answer:** `/ws/market`
(`webapp/src/lib/ws.ts:78`) is **not** gated today. A browser `WebSocket` cannot
send an `Authorization` header, so if the read gate ever widens to it, a
query-param or subprotocol token scheme is needed and this *does* become an auth
build. Worth knowing before Phase H widens the gate.

---

## 5. Done means

Three facts, established separately — say **which** you established:

1. **Auth mints** — §3.3 returned `200` with an `access_token`.
2. **The gate accepts, and still refuses** — §3.4 returned `200` with the bearer
   **and** `401` without one.
3. **The tab renders** — a human loaded the Data Explorer in the deployed SPA
   and saw rows. ⚠️ This one **cannot be true until the SPA work in §4 ships**,
   so until then the honest report is *"the host is restored; the SPA still
   cannot reach it"* — which is a real, useful state and not a failure.

A green CI run establishes none of the three.
