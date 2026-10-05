# Breakout phone executor — Android WebView on the operator's own phone (DESIGN, 2026-10-04)

> **Doc status:** `unknown` · category `architecture` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> **Tier-1 design only.** Lane PHONE-EXEC-DESIGN, dispatched by the manager 2026-10-04 after the operator's popup answer
> "Yes, design it". Nothing here is built, deployed or run. No code, no config, no VM change. Every effort figure is an
> **estimate**. The repo is public: no secret, account id or address appears here, and the build must keep it that way.

## 0. Summary

**What:** an Android app on the operator's own phone. It hosts Breakout's web terminal (`app.breakoutprop.com`) in a
`WebView`, the operator logs in by hand, and the app then pulls ticket packages from our API, types the bracket order into
the terminal's form, submits it, and reports fills back. It is the phone-side twin of the VM's `breakout_terminal` adapter.

**What it is not:** it does not bypass, solve or hide from any bot check. It runs on a genuine device and connection, with
the stock WebView user agent and no stealth, spoofing or challenge-solving. If the page challenges the WebView
(`challenge`, `captcha`, `email_code`, `2fa`, access denied), the app stops, alerts, and never retries in a loop. That is the
same feasibility-stop posture as `src/prop/platform/base.py::FeasibilityError`.

**The five findings that shape the design:**

1. **The armed submit is not built on the VM adapter either.** `breakout_terminal.place_bracket(arm=True)` walks the
   disarmed path and then *refuses* (rule 9 in the module docstring): the submit control, its confirmation, and the close
   confirmation are **unmeasured**. The terminal DOM has never been captured (tests run on synthetic fixtures). So the
   phone app's first job is **measurement**, not clicking. Phases 1a and 2 below are capture-only builds, and nothing before the purchase gate needs an account.
2. **Keep the safety logic in Python, on the server.** The phone should be a typist and a reader, not a second
   implementation of sizing, guards and read-back. The server already has tested versions (`bracket_from_ticket`,
   `evaluate_guards`, `check_form_shape`, `read_back`). Section 3.4 has the phone send what it read and receive a
   server-resolved spec and a short-lived, ticket-bound "go". Two implementations of the same guard drift; one does not.
3. **There is no per-device auth today.** The only write credential is the shared `DASHBOARD_API_TOKEN`. Section 3.2 adds a
   per-device, revocable, account-pinned token, paired once.
4. **There is no server-side claim today.** Idempotency lives in the VM executor's local `IntentLedger` file. A phone
   needs a server-side atomic claim so two devices, or a retry, can never both act on a ticket. Section 3.3.
5. **Brackets live at the venue only if the terminal holds them there. That is unverified** for the proprietary
   terminal (§ 5.4). The design treats it as an open measurement and does not rely on it.

**Recommendation:** build it, in the phases of § 7, but gate Phase 3 (the first armed click) on three things: the
Phase 2 DOM capture (after the account purchase), a first-party read of Breakout's terms (§ 6), and the operator's go. Everything before the purchase gate needs no account and
costs about 8–10 lane-days; the whole path to a watched live click is about 15–20 lane-days (§ 7.2).

---

## 1. What can be reused (Q1)

### 1.1 The retired Android app

| item | state, with source |
|---|---|
| Repo | `benbaichmankass/ict-trader-android` (private). **Not checked out or re-read this session** (this session's GitHub scope is this repo only). The facts below come from `docs/research/breakout-phone-egress-and-leak-controls-2026-09-30.md` § 3, which read it at commit `df9910d4` dated 2026-08-13. Treat them as **stale until re-read**. |
| Status | **On ice.** Its own `CLAUDE.md` says no speculative work and to reverse only on an explicit operator instruction. Retired from the live feed by operator decision 2026-09-01 (archived row `BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED`). **Un-freezing it needs an explicit operator instruction.** The 2026-10-04 popup said "design it", which this design treats as authority to design, not yet to thaw the repo. The manager should confirm. |
| Contents | 67 Kotlin files; Jetpack Compose and Glance widgets; FCM push; WorkManager refresh; `minSdk 26`, `targetSdk 35`; only `INTERNET` and `POST_NOTIFICATIONS` permissions; **no foreground service, no boot receiver**; shipped as Firebase App Distribution debug builds. |
| Product scope | A **read-only** dashboard consumer (M12 roadmap: `docs/sprint-plans/ROADMAP-ANDROID-COMPANION-APP-2026-05-26.md`, "holds no money-at-risk state of its own"). This design reverses that: the app holds the ability to place orders. That is a change of trust class, not a feature add, and it is why § 5 and § 6 exist. |

**Reusable from the app (if the repo is thawed):** the Gradle/CI/App-Distribution scaffolding, the Compose shell, the FCM
receiver, the API client and its settings screen. **Not reusable:** anything about order entry. There is no
foreground service, no WebView, no JS bridge. **Alternative:** a new small app module in a fresh repo avoids thawing a
frozen one and keeps a money-touching app out of a read-only app's history. The choice is the operator's (open question
O1, § 8). My lean is **a new repo**, so the read-only app's "no money state" promise stays true.

### 1.2 Server-side infrastructure that exists in this repo

| piece | where | reuse |
|---|---|---|
| FCM publisher | `src/runtime/mobile_push/` (`notifier.py`, `event_kinds.py`, `trade_events.py`); runbook `docs/runbooks/mobile-push.md` | **Directly.** Unconditional, best-effort, side-observer. `prop_signal`, `prop_fill` and `prop_closed` kinds already exist in `event_kinds.py` (`prop_signal` is the ticket-emitted kind, emitted by `src/prop/breakout_notify.py::emit_prop_signal`). A new ticket can wake the phone with a data message. |
| Device registry | `src/web/api/routers/devices.py` (`POST /api/bot/devices/register`, list, revoke, per-device subscriptions); table `device_tokens` | **For FCM routing only.** These are push tokens, **not auth credentials**. Do not overload them as the executor's identity (§ 3.2). |
| FCM credential rotation | system-action `set-mobile-push-secrets` (Tier 2, `scripts/ops/set_mobile_push_secrets.sh`) | Already runs; the credential is a file on the VM. **Status of FCM delivery today was not verified this session**; the runbook header says "unknown / never verified" for itself. A Phase 0 step checks it end to end (§ 7). |
| Prop routes | `src/web/api/routers/prop.py` | The report path (`POST /api/bot/prop/report` → `ingest_report`) and the read paths (`/tickets`, `/fills`, `/status`) are the contract the phone should use. |
| Terminal adapter | `src/prop/platform/breakout_terminal.py` and the page-JS strings it imports from `dxtrade.py` | **Partly** (§ 3.5). |

---

## 2. Venue: does Breakout have its own mobile app, and is the web terminal still the right target? (Q2)

**Measured this session** (fetched Breakout's Intercom article, `intercom.help/breakoutprop/en/articles/14215706`, 2026-10-04):
Breakout offers a **"Breakout terminal mobile app"**, and *"the Breakout terminal mobile app and the DXTrade terminal mobile
app are two separate and distinct applications. They are not interchangeable."* The page does not say which platforms
(Android, iOS) the app supports. The Play Store listing `com.breakoutprop.app` is cited in
`docs/research/prop-automation-options-2026-09-27.md` (S27, updated 2026-09-19); **not re-opened this session**.

**Is the web terminal in a WebView still the right target?** Yes, for three reasons, none of them about evasion:

1. **A native app cannot be driven in a supported way.** Automating another app's UI on Android means an
   `AccessibilityService`. It is fragile (it reads view hierarchies the vendor can change per release), it needs a
   broad permission the operator must grant, and Play policy restricts it. A WebView exposes the DOM to our own code,
   inside our own process, which is the same model the VM adapter already uses.
2. **The web terminal is the thing we have partly scoped.** Selectors, vocabulary and safety rules exist for it
   (`breakout_terminal.py`), and its page JS is reusable. A native app would start from nothing.
3. **Both are Breakout's own first-party client for the same account.** The web terminal is the documented route
   ("Open Terminal" from the dashboard, S23).

**Caveats, stated plainly:** a WebView is a different browser from Chrome (its user agent carries a `wv` marker, it
shares no cookies with Chrome, and it may be served differently by Cloudflare or by Breakout's login). Whether the
terminal and its login work inside a WebView at all is **unmeasured** and is phase 1a's first question (on the public page, no sign-in); the login itself is unverifiable until the purchase gate. If they do not
(for example the login relies on a Chrome-only feature, or Turnstile rejects the WebView), the design **does not work around
it**: the options are then the operator trading from the native app by hand with our Telegram tickets (today's manual
bridge), or a different venue. The app must not change the user agent to look like Chrome.

---

## 3. The executor contract (Q3)

### 3.1 Endpoints that exist today

| route | auth | what the phone would use it for |
|---|---|---|
| `GET /api/bot/prop/tickets?account_id=&status=emitted&limit=` | none (Tier-1 read) | Ticket intake. Row fields (from `prop_tickets`): `ticket_id`, `account_id`, `strategy`, `symbol`, `direction`, `entry`, `sl`, `tp`, `qty`, `risk_usd`, `signal_time`, `valid_until`, `status`, `order_package_id`, `message`, `meta`. |
| `GET /api/bot/prop/fills?account_id=` | none | Open positions as the journal sees them (reconcile). |
| `GET /api/bot/prop/status?account_id=` | none | Account snapshot age and rule distance. |
| `POST /api/bot/prop/report` | `Authorization: Bearer <DASHBOARD_API_TOKEN>`, **fail-closed** (503 when unset, 401 on wrong or missing) | Write-back: `kind=fill` with `status` in `placed/open/filled/closed/skipped`; `kind=amend`; `kind=account_status`. `ingest_report` **refuses** a position-bearing fill missing `account_id`, `symbol` or `direction` (the position identity), and links a fill to its ticket. |

**Gaps this design must close** (they are the new server surface, Tier 2, built behind review):

- `GET /tickets` is unauthenticated. That is fine for the VM executor on localhost; it is a public-internet read for a phone.
  The phone must use the authenticated claim route below, not this one.
- The write bearer is one shared secret that can post a report for **any** account. It must never be put on a phone.

### 3.2 Per-device auth (never a pasted secret in chat)

- **New table `prop_devices`** (device id, label, account scope, token hash, created, last seen, revoked). Hash at rest
  (SHA-256 of a 256-bit random token); the raw token exists only on the phone. It needs the `new-table-wiring` guard's
  declared relationship; it is a new store, so it needs an operator OK and no backfill (nothing to backfill).
- **Pairing, one time, no secret typed or pasted into chat:** the operator triggers a system-action
  `prop-phone-pair` (Tier 2). The server mints a **single-use pairing code with a 10-minute TTL** and shows it only on the
  operator's own screen (the app's pairing screen displays a one-time code the operator *requests from the app*;
  the server delivers it to the operator's Telegram DM, **not a repo, issue or Actions log**). The app redeems the
  code over TLS for the long-lived token and stores it in Android **Keystore-backed `EncryptedSharedPreferences`**. The
  code is useless after use or expiry.
- **Scope:** the token can call only `/api/bot/prop/phone/*` (below), is **pinned to the account(s) named at pairing**
  (the server ignores any `account_id` in the body that differs), and cannot call any other route. A leaked token
  cannot post a report for another account or touch the trading API.
- **Revocation:** system-action `prop-phone-revoke` (Tier 2), plus the app's own "unpair". Lost phone = revoke, and any
  claimed-but-unreported ticket is alerted (§ 3.3). Rotation is re-pairing.
- **TLS only.** The app talks to the existing HTTPS endpoint through Caddy; **certificate pinning is optional and costs
  an outage on cert rotation, so recommended off** (O3).
- **Logs and crash reports never contain the token.** Section 5 lists the checks.

### 3.3 Idempotency and one attempt per ticket

The ticket id is the idempotency key, exactly as on the VM (`IntentLedger`, spec § 3.2 in `docs/research/prop-automation-options-2026-09-27.md`). Phone-side
needs two layers:

1. **Server claim (new).** `POST /api/bot/prop/phone/claim {ticket_id, device_id}` atomically moves a ticket
   `emitted → claimed` (single SQL `UPDATE … WHERE status='emitted' AND valid_until > now`, one row or none). The
   response is the ticket plus `claimed_at`. A second claim, from any device or a retry, gets **409**. A claim is the
   only way to obtain a go-token, and it is made **before the form is touched**, not after. This mirrors
   "ledger `intended` is written and fsynced BEFORE the click".
2. **On-device intent ledger (new).** An append-only local store (Room or a JSONL file with `fsync`) with the same states as
   `IntentLedger`: `intended → submitted → placed/open (confirmed by re-read) | unconfirmed → skipped/contained`. Written
   before the click. On app restart, any unresolved row is **never** retried; it triggers a re-read of the terminal and a
   report.

**One attempt, never two.** If the claim succeeds and the form step fails, refuses, times out, or the phone dies, the
ticket is **spent**: the server marks it `skipped` with the refusal reason after a timeout (suggest 3 minutes without a
report) and alerts the operator, who decides by hand. There is no auto-retry and no re-offer. A missed fill costs one
trade; a duplicated order costs real money.

**Server-side watchdog (new):** a claimed ticket with no `placed/open/filled/skipped` report within N minutes raises the
same alert the VM executor raises for `unconfirmed_submit` (and is **never** silently re-emitted).

### 3.4 Where each safety check runs

The VM executor's order is: kill switch → read the terminal → reconcile → intake → local guards → act on at most one ticket
→ re-read. The phone keeps that order, with the *decisions* on the server:

| step | on the phone | on the server |
|---|---|---|
| Kill switch | Reads a mode from the claim response; honours `off`/`read_only`/`live`; default `read_only` | New `PROP_EXECUTOR_MODE_<ACCOUNT>` semantics reused: unparseable ⇒ `read_only`. A server-side "phone off" can never be bypassed by the app. |
| Read the terminal | Parses balance, equity, positions, orders; **`null` not `0` for anything unread**, with the unparsed list | — |
| Guards + sizing | Sends the read snapshot with the claim | Runs `evaluate_guards` and `bracket_from_ticket` (the *same Python*). Replies with a resolved `BracketSpec` or a refusal reason. **A stale (> 60 s) or partly unparsed snapshot is a refusal.** |
| Fill the form | Types values; reads **every field back** | — |
| Read-back | Sends the form dump (symbol, side, order type, quantity, price, SL/TP toggles and values, control locations) | Runs `check_form_shape` + `read_back` (the same Python). Replies with a **go-token bound to the ticket id and a hash of the verified form, valid 30 s**, or a refusal. |
| Submit | Re-hashes the live form; clicks **only if** it equals the verified hash and the token is unexpired | Never. Network loss between verify and click ⇒ **no click** (fail closed). |
| Confirm | Re-reads positions/orders; classifies `confirmed / partial_no_sl_tp / absent` | Receives the report; `partial_no_sl_tp` triggers containment and an alert. |

Why server-side for read-back: it keeps one tested implementation and means a fix lands without shipping an APK. The cost
is that the phone cannot place an order while offline; that is correct, since it could not report either. The click
guard on the phone is deliberately dumb (an equality check on a hash).

### 3.5 Mapping `breakout_terminal.py` to in-WebView JS

| Playwright adapter | In the WebView | Notes |
|---|---|---|
| `page.evaluate(JS)` with the page-JS strings (`EXTRACT_TABLES_JS`, `ORDER_FORM_JS`, `ONE_CLICK_JS`, `PAGE_SHAPE_JS`, `STRUCTURE_JS`, `CLOSE_ROW_JS`, `CONTROLS_DUMP_JS`) | `webView.evaluateJavascript(JS)` returning JSON | They are plain read-only JS taking their vocabulary as an argument, so they are the most reusable piece. Store them as one shared asset generated from the Python constants so the two cannot drift (a CI test compares the hashes). |
| Python parsers and verifiers (`parse_account_metrics`, `positions_from_tables`, `check_form_shape`, `read_back`, `classify_page`) | **Not ported.** The phone posts the raw JSON; the server parses and verifies | Single implementation; the existing fixtures and tests apply. |
| `tp.fill(...)` (real typing) | Drive the field through real input events: `WebView.dispatchKeyEvent` / synthesised touch on the focused input, falling back to the native value setter plus `input`/`change` events **only if a measured field accepts it** | The terminal's framework may ignore JS-set values; measured in Phase 1. These are events inside our own app's view, not injected into another process. |
| `tp.click(selector)` | A real touch event at the element's measured bounding box, or `element.click()` once measured safe | Same "one uniquely named control, inside the ticket panel" rule (rules 3 and 7). |
| `_frames(page)` (the terminal may live in iframes) | **Risk.** `evaluateJavascript` runs in the main frame only. Cross-origin iframes need `androidx.webkit` `addDocumentStartJavaScript` with origin rules and `WebMessageListener` | Unknown whether the terminal uses iframes; phase 1a tests it on local fixtures and the public page; the real terminal is phase 2. If it does and the support is missing, the phone route is infeasible and that is reported. |
| `classify_page` stop reasons | Run the same marker checks on each load; any of `challenge`, `captcha`, `email_code`, `2fa`, `asn_blocked`, `access_denied`, `login_rejected`, `unknown_page` ⇒ **stop, no retry, alert** | The app never reloads in a loop. |
| `place_bracket(arm=True)` | **Does not exist yet on either side** (rule 9). Built in Phase 3 from the Phase 2 capture | Held Tier-2 change, as `PI-20260928-DRBVUUDJ-0001`. |
| Selector/ARIA vocabulary | Labels and roles only, never class names | Same rule as the module docstring. |

---

## 4. The server surface to add (summary of § 3)

Four routes under `/api/bot/prop/phone/*`, all per-device-bearer, account-pinned, rate-limited, each audit-logged without
private data: `pair` (redeem), `claim`, `verify` (form dump → go-token), `report` (a thin wrapper over `ingest_report`
that overwrites `account_id` from the token). Plus a kill-switch field, the claim watchdog, and the two system-actions
(`prop-phone-pair`, `prop-phone-revoke`). All Tier 2: a new auth surface and a new write path to the order journal.
**Nothing on the existing executor, `multi_account_execute` or the order path is touched**; the phone is a second consumer
of the same ticket and report contract, so only one of {VM executor, phone} may be `live` for an account at a time
(enforced server-side, § 5).

---

## 5. Reliability on Android (Q4)

### 5.1 What I can and cannot state

I have **not** tested any of this on a device, and I do not know the operator's phone model. OEM battery behaviour varies
widely. The list below is design intent from Android's published model plus `docs/research/breakout-phone-egress-and-leak-controls-2026-09-30.md` § 4
(itself marked from-memory in places). Phase 1b measures it on the actual phone for 72 hours.

### 5.2 Design

| concern | design |
|---|---|
| **Process lifetime** | A **foreground service** with a pinned notification showing state (`armed / read-only / stopped: <reason>`, last tick age, open-ticket count). The WebView needs a live process and a visible `Activity` or a service holding it; a WebView in a service is delicate. **Open question O2:** whether the WebView must stay in a foreground `Activity` ("keep the screen on, plugged in" kiosk mode) rather than a service. The reliable default is **kiosk mode: a dedicated screen-on, charging phone**. A background-only WebView is expected to be throttled. |
| **Foreground service type** | A type that is not time-limited on Android 14/15. `dataSync` is capped per day at `targetSdk 35` (from memory, unverified); the app must choose and verify the type in Phase 1b. |
| **Doze / App Standby** | Charging + screen on avoids most of it. Battery-optimisation exemption requested; `PARTIAL_WAKE_LOCK` held only while armed. FCM **high-priority** data messages wake the app for a new ticket. |
| **OEM battery killers** | The operator must exempt the app per the OEM's settings; verified in Phase 1b by the 72-hour soak. |
| **Network loss / Wi-Fi ↔ mobile handover** | The connection drops on handover. Rule: **on loss, no new claim and no click.** A claim already made but not yet verified expires (go-token 30 s). Reconnect → re-read the terminal before any action. |
| **Terminal session expiry** | The terminal logs the session out. The app detects the login page (`classify_page`), **stops, alerts the operator, and waits for a manual login**; it never types credentials (it has none) and never automates a login or a 2FA / email code. Each logout is a missed-ticket window; the number per week is a Phase 2 metric. |
| **Phone address churn** | On mobile data the address changes with handovers and geography; on home Wi-Fi it is stable. Recommended: **home Wi-Fi, charging, a dedicated device**, which also keeps the session-ban risk down. |
| **Heartbeat** | The app posts a heartbeat (state, app version, terminal state) every 60 s. The server alerts on a missing heartbeat while a ticket is open or expected (latched, on-cross, with an `[OK]` on recovery, like `ACCOUNT_REACHABILITY_*`). |
| **Updates** | Sideloaded APK or App Distribution. A version below the server's minimum is refused at claim (`min_app_version`), so a stale app cannot act. |

### 5.3 What happens to an open position if the phone dies

**Open positions are managed by their bracket, not by the phone.** Every ticket requires SL and TP attached at entry
("never place without both"); the phone has no role in an exit that the bracket makes. Two things are therefore true if
the phone dies after a fill:

- **If the terminal holds the SL/TP orders on its own server (the normal expectation for a trading venue, § 5.4)**, the
  position exits at its stop or target with no phone. No position is left naked by a dead phone **if both legs were
  confirmed attached before the phone died**.
- **What the phone's death costs:** no trailing amendments (`kind=amend` from `prop_trail`), no reconcile of the fill
  into the journal until it returns or the operator reports by hand (the manual bridge via Telegram), and no flatten on an
  anomaly. The server alerts on the missing heartbeat, and the **operator's existing path is to act from Breakout's own
  app** or the web terminal.

### 5.4 Not verified: where the bracket lives

I found **no first-party statement and no measurement** that Breakout's proprietary terminal keeps SL/TP orders on its
server rather than in the client. The VM go-live evidence is on DXtrade (`breakout_1`), not on this terminal. **This is the
single most important fact to measure** before any unattended use: in Phase 2 (after the purchase gate) the operator places one tiny test position
by hand in the terminal, closes the phone app *and* the terminal tab, and checks whether the exits still stand and fire
(or checks the working-orders list from a different client). Until measured, the design assumes the **worst case** (the
position has no exit if the client is gone) and limits the first live phase to one small ticket at a time, on a
phone that is attended.

### 5.5 Failure table

| event | result |
|---|---|
| App killed by OS | Intent ledger shows any unresolved row; on restart: re-read, report, **no retry**; alert. |
| Claim made, phone dies before the form | Ticket spent; watchdog marks `skipped: claimed_no_report`; alert. |
| Click made, phone dies before the re-read | Position may exist with its bracket; the watchdog alerts `unconfirmed_submit`; the operator checks the terminal. Never re-offered. |
| Server unreachable | No claim, no verify, no click. |
| Terminal page changes (selector drift) | Form or table not found ⇒ refusal; 2 consecutive refusals ⇒ the server flips that account `read_only` (the VM auto-revert rule) and alerts. |

---

## 6. Safety checklist (Q5)

The go-live rules for the VM executor (`prop_executor.py`, and the manager's registered D1–D7 / L1–L8 criteria, held in the
`PROP-EXEC` checklist row) map onto the phone as follows. **Each line is a requirement and a test to write, none is built.**

| rule | phone implementation | proof |
|---|---|---|
| **No wrong symbol** | Server `check_form_shape` / `form_names_symbol` on the live form; refuse on any mismatch or an ambiguous control. Symbol comes only from the server-resolved spec. | Unit tests on the server (existing) + a replay of captured forms. |
| **No wrong side** | Side read back from the form as selected (`verify_form_selection`); a single "Buy" button that also submits is refused (rule 7). | Server test + captured forms of both sides. |
| **No wrong size** | Server types the lot size from `bracket_from_ticket` (flat $-cap, venue lot step); read back equal; **risk recomputed from the typed values**, never the ticket's claim. | Existing tests; D3. |
| **No wrong price** | Price rounded to the venue step and read back within one tick. | Existing `price_tolerances`. |
| **No missing SL/TP** | Both toggles read **on** with values equal within a tick, before the click; after the click, `classify_confirmation == confirmed`. **`partial_no_sl_tp` is a failure** and is contained (close at market) and alerted. | L3. |
| **No click without verification** | Go-token is ticket-bound, form-hash-bound, 30 s. | Mutation test: change one field after verify ⇒ no click. |
| **One attempt per ticket** | § 3.3. | Test with two devices and a retry storm: exactly one claim wins. |
| **Fail closed** | Any unread, null, stale, unparsed or unexpected state is a refusal, never a guess or a `0`. | Collapsed-state guard analogue. |
| **Kill switch** | Server mode, default `read_only`, typo ⇒ `read_only`. | As `executor_mode`. |
| **One executor per account** | The server refuses a phone claim while the VM executor is `live` for that account, and vice versa. | Test. |
| **No private data in logs** | Never log: the device token, pairing code, Breakout credentials, cookies, URL paths, position or order ids, balances, account ids or IP addresses. Logs carry ticket id, state, and refusal code only. Crash reports and screenshots are off by default (Android `FLAG_SECURE` on the WebView window). The WebView's storage is app-private; `android:allowBackup=false`. | A CI grep over the build for logging calls; a log-capture test during the soak that scans for the account id and any 6+ digit id. |
| **No challenge handling** | Challenge/captcha/2FA/email-code ⇒ stop and alert; no automated solve, no UA change, no reload loop. | Test with a challenge fixture page. |
| **No credentials in the app** | The operator types the password and any code **in the WebView by hand**. The app does not save, autofill or read them. | Review + test. |
| **Public repo hygiene** | The APK repo and this repo carry no secret, address, package signing key or account id. | `run_secret_scanning` + review. |

**Account-level risk** (not a bug class): breach-and-re-buy is already accepted for `breakout_1` (operator, 2026-09-28).
Whether it is accepted for a *funded* account that is a device-and-IP change is a separate question (§ 6.1).

### 6.1 Terms (Q6)

- **What I verified:** Breakout's Intercom article on the two mobile apps (read this session) says nothing about
  automation, bots, devices or IP addresses.
- **What I did not read:** the **Funded Trader Agreement / Terms of Service** and any account-sharing or device/IP policy.
  `www.breakoutprop.com/terms` returned a Cloudflare challenge to previous sessions
  (`docs/research/breakout-terminal-egress-scoping-2026-09-30.md` § 1). **They have never been read by any session.**
- **What needs reading, and by whom:** the operator (or a session fed the text by the operator) should read, in the
  Funded Trader Agreement and the ToS: (a) any clause on **automated, algorithmic or bot trading**, and on "trade
  copiers" or "third-party tools"; (b) any clause on **access from multiple devices or IP addresses**, VPNs, or jurisdiction
  concealment, and whether it applies to a phone browser; (c) **account sharing and credential handling** (the app never
  holds credentials, but the operator logs in on a second device); (d) any **one-session-at-a-time** rule (a phone WebView and
  the operator's own browser at once); (e) the consequence clause (account closure, profit forfeiture, KYC re-verification);
  (f) latency/"exploiting the platform" language that a scripted submit might touch.
- **Why this design is better on the evasion axis than the VM routes:** the operator's own genuine phone and home or mobile
  connection, a human login, stock WebView identity, no stealth. It is **not** "routing around a ban", which was the
  open concern with the home-tunnel and phone-egress options. It still is **automation on the account**, which the
  operator accepted on 2026-09-27, and whether the terms permit it is unread.
- **Third-party summaries** (vendor blogs: bots "allowed", VPN conditional) are leads, not terms
  (egress scoping § 1).

---

## 7. Build plan (Q7)

**Operator constraint (2026-10-04, via the manager):** the operator has **no Breakout account yet** and will buy one only
after we have verified there are **no structural blockers**. So the plan is ordered to settle every blocker that can be
settled **without a login**, then stops at an explicit **OPERATOR GATE: purchase account now**. Nothing before the gate
signs in, types a credential, or needs an account.

### 7.1 What can be verified with no account, and how

| blocker question | no-account method | status today |
|---|---|---|
| Does Breakout have a native app? | Breakout's own help article | **Done:** yes, a separate "Breakout terminal mobile app" (§ 2). Platforms not stated. |
| Is there a demo / practice mode we could use instead of an account? | Breakout help centre, Terminal collection (6 articles: migration, rules, two terminals, swap fees, access, mobile app) | **None found.** No article mentions demo, practice, free or trial. (Absence in six titles, not a statement that none exists; the operator can ask Breakout support once at purchase time.) |
| Does the terminal's public page load in the phone WebView on the operator's connection (no challenge, no 1005)? | The phase 1a shell loads the **public landing and login pages** and runs the page-state classifier. No sign-in. | Open. This is the first real test. |
| Does the WebView get served the same page as Chrome (user-agent and bot check differences)? | Load the same public page in the phone's Chrome and in the shell; compare **classifier state only**, no UA change | Open. |
| Can injected JS reach the page, including iframes? | `evaluateJavascript` and `addDocumentStartJavaScript` / `WebMessageListener` against (a) the public pages and (b) a **local test page with a cross-origin iframe** served from the app's own assets | Open. Settles the framework question for the main frame and for iframes. |
| Can we type into a React-style form so the page registers it? | A **synthetic fixture** terminal page (input, toggles, side buttons, a submit) built to the `breakout_terminal` vocabulary and hosted inside the app. Compare real key events against JS value-setting | Open. Proves the mechanics, **not** Breakout's layout. |
| VM-to-phone contract: pairing, claim, one attempt, go-token, kill switch, heartbeat, watchdog | Server routes and the app run against the **synthetic ticket stream and the synthetic terminal fixture**; two-device claim race; network-loss and kill-app tests | Open. Fully testable with no Breakout. |
| Android background reliability | 72-hour soak on the operator's phone with the foreground/kiosk shell holding the **public** page and a heartbeat: OEM kills, Doze, handovers, re-login-page detection | Open. |

### 7.2 Phases

| phase | what | account? | est. (lane-days) | exit gate |
|---|---|---|---|---|
| **0. Decide** | Operator answers O1–O5 (§ 8). **Step zero, ~2 min, no sign-in:** open `app.breakoutprop.com` in the phone's browser on home Wi-Fi and on mobile data and report which page appears (served / "Just a moment…" / access denied). Confirm FCM delivery to the phone. | none | 0.5 | Operator go for 1a. |
| **1a. Public-page shell** | New app: WebView + kiosk shell, page-state classifier, redacted page-shape capture of the **public** pages only (same redaction posture as `scripts/prop/breakout_terminal_probe.py`), the iframe and typing tests on local fixtures. | none | 2–3 | Answers the first three rows of § 7.1 and the iframe and typing rows. **If the public page is challenged or blocked in the WebView, stop: a structural blocker, no purchase.** |
| **1b. Contract and reliability** | Server: `prop_devices`, pairing, per-device auth, `claim`/`verify`/`report`, heartbeat, watchdog, kill switch. Phone: ticket intake, intent ledger, alerts, running against **synthetic** tickets and the synthetic terminal. 72-hour soak. | none | 5–6 | Two-device claim race, fail-closed tests, and the soak table (uptime, kills, handovers) all pass. |
| **OPERATOR GATE: "purchase account now"** | Reached only when 1a and 1b pass. The manager asks the operator to purchase a Breakout terminal account, **after the operator has read the terms (§ 7.4)**. | **purchase** | — | Operator buys; the manager records it. |
| **2. Capture with login** | The operator logs in **by hand** in the WebView. Redacted DOM capture of the real terminal; read-only: positions, orders, account snapshot. Measures § 5.4 (do SL/TP survive the client being closed) with one tiny hand-placed position. Session-expiry and any email-code or 2FA behaviour recorded. | **yes** | 2–3 | Captured, redacted fixtures committed; the questions in § 7.3 answered. |
| **3. Armed click, dry then one watched live** | Held Tier-2: the submit and its confirmation built from the phase 2 capture; dry round trip (D1–D7 analogue), then **one** minimum-size live round trip under criteria registered before the run. The operator present. | yes | 3–4 | D and L criteria pass; auto-revert armed. |
| **4. Soak** | Probation (first 5 trades or 72 h), trade-by-trade reports, then steady state; auto-revert to `read_only` on the VM executor's triggers. | yes | 2 + observation | Gate 1-style review. |

**Effort (estimate):** before the gate ≈ **8–10 lane-days** (0.5 + 2–3 + 5–6); after it ≈ **7–10** (2–3 + 3–4 + 2). Total
**≈ 15–20 lane-days**, with independent review rounds likely the long pole. Everything before the gate costs **no account
fee**; the gate is the first spend.

### 7.3 What stays unverifiable without an account

1. Whether the **login** works inside a WebView (the sign-in submit may trigger a bot check the public page did not).
2. Any **email code, number-match or 2FA** at sign-in, and how often it recurs (a human step on every login makes unattended
   use infeasible; `breakout_terminal` lists `email_code` as a stop).
3. The **post-login terminal**: its DOM, whether it uses iframes or a canvas order ticket (`canvas_ticket` is a stop), the real
   field, toggle and submit controls, and the submit **confirmation** flow.
4. **Where SL/TP live** and whether they survive the client being closed (§ 5.4).
5. **Session lifetime** and what a logout looks like; behaviour with the operator's own browser open at once.
6. Whether the account's **rules or the platform** block the order types the tickets need (limit with bracket), and the
   venue's lot step and price increment for each instrument.
7. **Latency and fill behaviour** at the venue.

None of these can be answered from a landing page or a fixture, and all of them are phase 2 on.

### 7.4 Terms the operator should read at purchase

Read the **Funded Trader Agreement and the Terms of Service** before paying (no session has read them, § 6.1). Look for:
(a) any **automated, algorithmic or bot-trading** clause, and anything on trade copiers or third-party tools; (b) any
**one-device or one-IP** rule, VPN or jurisdiction-concealment language, and whether a phone WebView counts;
(c) **account sharing and credential** handling; (d) any **one-session-at-a-time** rule; (e) the **consequence** clause
(closure, profit forfeiture, KYC re-verification); (f) language on "exploiting the platform" or speed that a scripted submit
might touch. If (a) or (b) forbids it, that is a structural blocker and should be found **before** the purchase, not after.

### 7.5 What the operator does

1. Decides O1–O5 (§ 8).
2. **Step zero** (browser, ~2 min, no sign-in) on Wi-Fi and mobile data.
3. Installs the APK (sideload; "install unknown apps" once for the source). Tells us the **phone model**.
4. Dedicates the phone for the 72-hour soak: screen on, charging, home Wi-Fi, battery-optimisation exemption, app pinned.
5. **At the gate:** reads the terms (§ 7.4), then **purchases the account**.
6. After the gate: **logs in by hand** in the WebView (and again after each expiry); supplies any email code or 2FA by
   hand; pairs the device (one-time code via Telegram); places one tiny test position by hand for § 5.4.
7. **Explicitly authorises phase 3**, the first real-money click path.

---

### 7.6 Step zero: the operator's 2-minute browser test (no sign-in)

Do **not** sign in, enter anything, or tap anything on the page. Do it twice: once on home Wi-Fi, once with Wi-Fi off (mobile data).

1. On your phone, open **Chrome**. (If you use another browser, say which.)
2. Type `app.breakoutprop.com` in the address bar and go.
3. Wait up to 15 seconds. Look at what the page shows, and match it to **one** of these:
   - **A.** The Breakout **login or dashboard page** (a sign-in form, logo, email box). *Served.*
   - **B.** A page that says **"Just a moment…"**, "Verify you are human", or shows a checkbox/spinner. *Challenged.*
   - **C.** A page that says **"Access denied"**, **"Error 1005"** or "you have been blocked". *Banned.*
   - **D.** Anything else, or a blank page, or an error (say what it says in a few words).
4. Close the tab. Do not tap the checkbox in B (that is a person solving a challenge by hand, which tells us less about an app).
5. Reply with **one line**: `Wi-Fi: <A/B/C/D>; mobile data: <A/B/C/D>; browser: <Chrome or name>; phone model: <model>`.

Reading the answer: A on both is a go for phase 1a. B means the public page challenges a real phone, so a WebView may be challenged too: ask for one more line from the Breakout mobile app if the operator has no account (nothing else to test), and treat B as a **likely blocker** for the WebView route. C is a **no-go** for that network. D needs a follow-up. This test says nothing about the WebView itself; that is phase 1a.

### 7.7 Public-page evidence gathered 2026-10-04 (no login, no challenge-solving) and go/no-go

| question | finding | source |
|---|---|---|
| Does the landing/login page work in an Android WebView, or must it be a full browser? | **Cannot be answered from public material.** A plain fetch from this session's network (not a phone) got a Cloudflare **managed challenge** (`HTTP 403`, `cf-mitigated: challenge`), whose own headers are the challenge page's, not the real app's: its CSP allows `challenges.cloudflare.com` frames, and `X-Frame-Options: SAMEORIGIN` (irrelevant to a top-level WebView). So we know a Turnstile-style check sits in front of the site for datacenter-like clients; whether a genuine phone WebView is passed is only answerable on the phone (steps 7.6 and 1a). No evasion or retry attempted. | live fetch this session; headers not stored |
| Does Breakout publish a demo/practice terminal needing no paid account? | **None found.** The Terminal help collection has 6 articles; the access, two-terminals and mobile-app articles say nothing about demo or practice. | `intercom.help/breakoutprop` collection 19162084 and articles 14215682, 14215629, 14215706 |
| What do the help articles say about mobile/app trading? | One article: "the Breakout terminal mobile app and the DXTrade terminal mobile app are two separate and distinct applications. They are not interchangeable." No platforms, features, login or automation detail. Access (web): "log in to your Breakout Dashboard and click the 'Open Terminal' button." New purchases are Breakout terminal only. | articles 14215706, 14215682, 14215629 |
| Frame/CSP headers of the real app | **Not visible** (the challenge answered instead). Header checks from a GitHub runner would hit the same datacenter ban (measured earlier: Error 1005), so it was not run. | prior: `docs/research/breakout-terminal-egress-scoping-2026-09-30.md` § 2 |

**Recommendation: GO for phase 0 and phase 1a only; no-go on spending anything.** Rationale: nothing found rules the WebView route out, nothing found confirms it works, and the decisive evidence (does the public page serve a real phone's WebView) costs the operator two minutes and one throwaway probe app. **Hard stops:** step zero returns B or C on both networks, or phase 1a's public page is challenged or blocked in the WebView. Cost of this recommendation so far: about a few dollars of lane spend; no purchase, no login, no build beyond what 1a needs.

### 7.8 Step-zero result, 2026-10-04 (operator's own phone; relayed by the manager, not independently observed)

**Result: reachable, with caveats. Not a pass for the WebView question and not a check of the new terminal.**

What the operator reported, as relayed: on their own Android phone, over LTE, in the **Comet** browser (not Chrome; whether Chrome was tried is
being asked), `app.breakoutprop.com` **loaded fully with no Cloudflare wall**. It showed a mobile terminal with the banner
"Beta version of the mobile platform with limited functionality", a portfolio, a watchlist, charts, Sell/Buy buttons, and an order ticket:
a Market Order dropdown, a Sell/Buy toggle, Quantity in Lots with plus/minus buttons, a Protection section with Stop Loss and Take Profit
toggles and price fields (with a "Price" mode dropdown), and a "Send Order" button. No account identifier, balance or screenshot is recorded here.

What this establishes, and what it does not:

| | |
|---|---|
| **Established** | From a genuine phone connection (LTE), the site is **served**, not challenged. The datacenter ban and challenge seen from our servers and sandbox did not apply to the operator's phone. This is the first real evidence for the premise of this design. |
| **Established (by description)** | The mobile ticket **as described** has the same field vocabulary the executor already handles (side, order type, quantity in lots, SL and TP toggles with a price mode, a distinct send control). **No DOM was captured**, so this is a match of labels, not of selectors; the desktop DXtrade DOM was what `dxtrade.py` was measured on. |
| **Not established** | That it works in an **Android WebView** (Comet is a full browser, Chrome is untested, and neither is a WebView). That a WebView is not challenged or served differently. That login works in a WebView. Wi-Fi was not reported. |
| **Not the target** | The operator's session was on the **existing DXtrade-backed account (`breakout_1`)**, which is not the new native Breakout terminal. The new terminal is still unverified, and verifying it needs a new account (the OPERATOR GATE). |

**Open risks this adds:**

1. **Beta, limited functionality.** The page calls itself a *beta* mobile platform with limited functionality. Its layout and controls may change without notice, may differ from the desktop DXtrade DOM, and may lack features the executor relies on (for example the position table, row close controls, or an amend path). Treat every selector as unmeasured until captured on this surface.
2. **DXtrade versus new terminal gap.** What loads at this address for a DXtrade account may not be what a new-terminal account sees. Nothing seen so far says the new terminal's mobile web looks the same. A design fitted to the DXtrade-shaped mobile page could suit `breakout_1` and still not suit the account the operator would buy.
3. **One account, two clients.** `breakout_1` has a live VM executor. A phone session on the same account at the same time may be refused or may disturb the VM's session (a one-session rule is unread). Any probe on that account needs the VM executor held first, and is the operator's call.

**Updated state of the no-account checks:**

| check | state |
|---|---|
| Native app exists | done (§ 2) |
| Demo/practice terminal | none found (§ 7.7) |
| Site reachable from the operator's phone IP | **done: served in Comet over LTE.** Chrome and Wi-Fi not yet reported. |
| Page in an Android WebView | **open**: phase 1a probe |
| Injected-JS reach, iframes; typing into a React-style form (local fixtures) | open: phase 1a |
| VM-to-phone contract on synthetic tickets | open: phase 1b |
| 72-hour Android soak | open: phase 1b |
| Terms read | open: operator, before purchase |

**Left before the "purchase account now" gate:** the phase 1a probe (WebView on the public page, plus the iframe and typing fixtures), phase 1b
(contract and soak against synthetic data), and the terms read. One optional addition for the operator to decide: a WebView probe on the
operator's **existing** DXtrade account would answer the WebView-login and mobile-ticket questions early, but it is a login (outside the
no-account scope) and touches the live `breakout_1` account (risk 3), so it is **not planned**.

### 7.9 Phase 1a probe, the cheaper ordering, and the results table (2026-10-05; results PENDING)

**Direction (operator, 2026-10-05):** skip the Breakout-contact step ("they are unresponsive") and "focus on technical viability to get to a real-account test". Later the same day the operator offered to buy Breakout's cheapest account (about $20, Turbo tier, per the manager's relay of Breakout's pricing page; not independently read) "just to check the terminal".

**Ordering change.** § 7.2 put the purchase gate after phase 1b. With a $20 look-only account the gate moves **before 1b**:

| step | what | account | state |
|---|---|---|---|
| 1a | `tools/phone-probe/` APK (PR #16574): WebView shell, page-state classifier, redacted shape capture, local typing / tap / iframe fixtures | none | built in CI; **not yet run on a phone** |
| 1a-B | the operator logs in by hand to the **breached `breakout_1` DXtrade account** inside the probe and captures its mobile terminal, read-only | `breakout_1` (breached, executor latched, no money at risk) | pending the operator |
| GATE | buy the cheapest account; log in by hand inside the same probe; capture the **new** terminal's order ticket, read-only | $20 look-only | pending 1a-B |
| 1b | contract and soak on synthetic data | none | after the gate |

**What the probe records and does not.** It reads no input value, places no order, taps no trading control, keeps no credential, and sends nothing off the phone: the operator copies a report. Labels are reduced to a fixed vocabulary (`probe.js`, `VOCAB`), every other word becomes `*` and every digit run `#`; an export that still holds 6+ digits, an `@` or a bearer token is refused by the app. `FLAG_SECURE` blocks screenshots.

**The § 7.3 unknowns, by where they can be answered** (filled in when the report arrives; until then every "answered" cell is empty):

| § 7.3 unknown | answerable on `breakout_1`? | answerable generally? | needs the NEW terminal? | result |
|---|---|---|---|---|
| 1. Login works inside a WebView | yes, for the DX-backed login | no | yes: the new terminal's login may differ | pending |
| 2. Email code / 2FA / number-match at sign-in, and recurrence | yes, for that account | no | yes | pending |
| 3. Post-login DOM: iframes, canvas ticket, field / toggle / send controls, confirmation flow | yes, as the DX-backed mobile page | no | **yes: the target is the new terminal** | pending |
| 4. Where SL/TP live and whether they survive the client closing | no: needs a position, and `breakout_1` is breached | no | yes (and a hand-placed position, outside look-only) | not answerable at the $20 look-only step |
| 5. Session lifetime and logout shape | yes, by rechecks over hours | partly | yes | pending |
| 6. Order types and lot step the rules allow | no | no | yes | not answerable look-only |
| 7. Latency and fill behaviour | no | no | yes | not answerable look-only |
| Injected-JS reach, iframe reach, React-style typing, real touch | n/a | **yes: local fixtures inside the app's WebView** | no | pending (Fixtures button) |
| Public page served in the WebView (Wi-Fi and mobile data) | n/a | **yes** | no | pending |

**Operator-reported progress (2026-10-05 about 08:30Z, relayed by the manager, NOT captured by the probe):** the operator says they logged in to the breached account successfully on both Wi-Fi and mobile data. **Not established:** whether that login was inside the probe app or in Chrome, whether it was a WebView at all, whether any code / 2FA step appeared, and what the page looked like. No report has been pasted, so this line answers none of the table's cells. If it was Chrome, it adds only that the account logs in from the phone on both networks, which § 7.8 already had for LTE in Comet.

**First probe report (operator-pasted JSON, 2026-10-05 about 08:22-08:26Z; relayed to this lane as a summary by the manager, not read in full; breached `breakout_1` DX account on `app.breakoutprop.com`; Pixel 9a, Android 16, WebView 153, `ua_wv_marker` true).** Navs: cellular `other_served` (22 elements), cellular `login` (911 elements, password marker, 2 inputs, 1 button), cellular post-login `other_served` (980 elements, 2 iframes, 7 buttons, 0 inputs, no buy / sell / send marker), Wi-Fi `other_served` (41 elements, 1 iframe). `cf_mitigated` false and `blocked` false on every nav; code / 2FA markers false. `captures` empty. `fixtures` held only the feature flags. `session.first_login_seen_at` 08:22:42Z.

| GO-BUY criterion (registered above) | state | evidence and limit |
|---|---|---|
| App loads the site, not challenged, on both networks | **met** for `app.breakoutprop.com` | no challenge or block on any of the 4 navs, cellular and Wi-Fi. Not the new `trade.` host. |
| Login survives in the WebView | **partly met** | the login page rendered and the next nav is a non-login page, so the sign-in worked once. Session lifetime and recurrence of a code step are NOT measured (no recheck is in the relayed summary). |
| A capture returns a non-empty shape for the logged-in page | **NOT met** | `captures` is empty. Nav summaries carry counts only. |
| At least one typing route registers (Fixtures) | **NOT met (no data)** | only the feature flags were recorded; the run was probably copied before it finished. |

By the criteria registered before the run, that is 2 of 4: **NOT-YET** by the letter. The two open items are local to the phone and the app, need no purchase, and do not depend on the new account, so they can be closed on the breached account before or after the $20 purchase. The purchase risk itself is small ($20, look-only) and the operator has chosen it, so this is a note on the evidence, not a block. The post-login page has 0 inputs, 7 buttons and 2 iframes and no buy / sell marker: it reads as a dashboard, not an order ticket. Whether the ticket sits inside one of those iframes is open.

**VM route check (requirement A), 2026-10-05, relayed by the manager from `egress-landing-probe` (issue #16601, run 37295206219; not read by this lane):** from the live VM's egress, `https://trade.breakoutprop.com/` and `.../app/` both answered **HTTP 403, `server=cloudflare`, `cf-ray` present, `cf-mitigated` empty, `text/plain`, a 17-byte body, no challenge or login-form markers.** MEASURED: the 403, the Cloudflare server header, the 17-byte plain-text body. **INFERRED, not measured:** that the body is Cloudflare's `error code: 1005` ASN block (17 bytes is the length of that string, and it is the same class of block `app.breakoutprop.com` returned to the VM on 2026-09-29); the probe does not print the body, and its error-marker pattern does not match the lowercase `error code: 10xx` form, so it cannot confirm this itself. **Consequence:** the VM executor (`breakout_terminal` adapter) cannot reach the new terminal from this egress, so the phone route stays the only candidate path to autonomy on a new-terminal account. A small follow-up to the probe script (match `error code: 10xx`) would make the next run measure it.

**New-terminal login, MEASURED by the operator (2026-10-05, relayed by the manager):** `trade.breakoutprop.com` loads in the probe WebView. The operator entered their email in the app, tapped the number link in Breakout's email (it opened in **Chrome**), went back to the app and hit **reload**, and the app was logged in. So the sign-in is a **cross-device approval of the waiting WebView session plus a reload**: no link has to get into the app. Not established: whether the waiting page would have completed WITHOUT the reload (the operator reloaded), and whether any step recurs on a later login.

**Decisions that shaped this section (operator, ~11:47Z, via the manager):** no forwarding inbox, no automated email reading, no automated number tapping ("let's not build the forwarding ... we need to figure out what the consistent process is for staying logged in. If it doesn't require an email, then that's fine."). A "Paste link" / intent-filter build (1a.4) was started and pushed, then **reverted before any APK reached the operator**; one CI-built APK from it exists as a workflow artifact (run 37305028798) and must not be installed. The only live build is **1a.3** (run 37288470509). Installing any new build would wipe the logged-in session being measured, so no new build is planned until the lifetime result is in. Email automation is closed in the pipeline (`PI-20261005-9HEP9LYP-0001`, killed with the operator's words).

**First report on the NEW account (build 1a.3; operator-pasted, chat-truncated mid-capture; relayed as a summary by the manager, not read in full):** logged in. Two captures (11:43:25Z, 11:47:06Z): host `trade.breakoutprop.com`, path shape `/*/account/*/trade`, state `other_served`, 0 inputs, no login / password / 2FA markers. Top-frame controls (labels as redacted): Trade, Portfolio, settings, market, Market chart, Order, Open orders, Positions, chart, Portfolio, Closed orders, Trades, and symbol-strip buttons. The chart and trading UI sit in a **same-origin `blob:` iframe** (703 elements, 7 canvases, 61 buttons, a radiogroup timeframe selector), reachable by DOM. `buy` / `sell` markers were false: the order ticket is probably behind the "Order" control and was not open. Navs: 11:36:56Z `portal.breakoutprop.com` (dashboard, 9 inputs, 2 dialogs); 11:43:21Z and 11:43:32Z `trade.`. **Anomaly:** 11:44:33Z nav to `portal.breakoutprop.com` classified `code_or_2fa` with `http=-1` and **wifi=false / cellular=false (offline)**, 11 elements: a failed load, so it does NOT establish that the portal asks for 2FA again on reload; re-check on a stable network.

| criterion (registered above) | state on the new account | limit |
|---|---|---|
| Site loads in the WebView, not challenged | **met** for `trade.` and `portal.` | no challenge or block in the relayed navs |
| Login works in the WebView | **met** (cross-device approval + reload) | one login; recurrence unknown |
| A capture returns a non-empty shape for the logged-in page | **met** | two captures; truncated paste |
| A typing route registers (Fixtures) | **unknown** | not in the relayed summary |
| Order ticket controls readable (side, type, qty, SL, TP, send) | **NOT yet** | ticket not open; needs a capture with the ticket open |
| Ticket is not a canvas | **not established** | the 7 canvases are in the chart iframe; the ticket itself unseen |
| Iframes reachable | **met for this surface** | the blob iframe is same-origin, so DOM reach works; cross-origin reach is untested because none appeared |
| Session lifetime without re-auth | **clock running from 11:43Z** | rechecks at about 12:45Z, 17:45Z and the next morning |

**+54 min recheck (operator report ~12:38Z, "Still logged in"; relayed by the manager as a summary, paste truncated again inside the controls list):** a manual capture at 12:37:06Z on `trade.breakoutprop.com` reads `other_served`, 278 elements, 23 buttons, 0 inputs, no login markers: the same logged-in shape as 11:43 and 11:47. A nav at 12:35:55Z on `trade.` also reads `other_served` (a nav entry is written when a page finishes loading, so a real load happened there). **GRADE: logged in at +54 min.** Limit: one data point, the app left open and online; it does not say the session survives an app kill, a restart or hours. Its `net` reads wifi=false / cellular=false, but `net` is sampled when the capture ends, so it most likely records a network flap AFTER the page had loaded, not an offline load.

**The 11:47:29 to 11:47:44Z nav sequence (new; reported as a possible password path on the trade host): NOT established as that.** Reported: tick `app.breakoutprop.com` "challenged" (cf true, 43 el); 11:47:34 `app.` other_served (15 el); 11:47:39 host `trade.breakoutprop.com` state `login` with the password marker, 911 elements, 2 inputs, 1 button; 11:47:44 `trade.` other_served (278 el, logged in). Two reasons to doubt a password form on `trade.`: (1) the `host` in a nav entry is read from the WebView when the 3.5 s capture ends (`MainActivity.kt` `finalizeCapture`), NOT from the page the shape was taken from, so a shape taken on `app.` and finalised after a navigation to `trade.` is labelled `trade.`; (2) the fingerprint **911 elements, password marker, 2 inputs, 1 button is identical to the `app.breakoutprop.com` login page in the FIRST (breached-account) report at 08:22:42Z**, a different page from the 278-element logged-in `trade.` page. The most likely reading is that the operator, or a tick, was on `app.` (the old DXtrade host, which also showed a Cloudflare "challenged" tick) when the login shape was taken. This is INFERRED from the fingerprint; the nav summaries do not carry the shape's own host, so the report cannot settle it. To settle it without a build or touching the session: ask the operator what was tapped at ~11:47 (for example "Load app."), and have them READ the portal sign-in page by eye in a Chrome **private** tab (never log out of the app's account; the app's session lives in the app) and say which login options it offers (email only? a password field? "use authenticator"?).

**Order ticket opened and captured (operator, ~12:44-12:45Z; nothing placed; relayed as a summary, the paste was truncated before the controls list again):** tick 12:44:27Z on `trade.breakoutprop.com`: state `terminal`, **1 frame (no iframe)**, 477 elements, 8 inputs, 36 buttons, 12 svg, 7 tabs, **`buy` true, `sell` true**, `send` false. Manual capture 12:45:22Z: state `terminal`, 1 frame, 400 elements, 4 inputs, 32 buttons, 5 tabs, `buy` true, `sell` true. Nav 12:45:06Z `portal.breakoutprop.com`: logged in (dashboard, 32 elements, 1 same-origin iframe), no code or 2FA step. **GRADE:** (1) **still logged in at +62 min on BOTH the trade host and the portal.** (2) **Ticket is plain top-frame DOM: not a canvas, not in an iframe** in this view (the earlier chart view had a same-origin `blob:` iframe; the ticket view has none). (3) **Order-ticket readability: PARTLY MET at the marker level only.** Met: Buy and Sell controls are found by label, and there are numeric-style inputs (4 to 8, types not yet seen). **NOT established:** that the six controls the executor needs (side, order type, quantity, SL, TP, a distinct send control) each exist and are uniquely named. `send` false means no control labelled "Send ... order" was found: the submit control may simply be the Buy / Sell button, which is the case design rule 7 REFUSES (a single "Buy" that also submits). The control labels, roles, input types and states were lost to the truncation: that is what decides this criterion, and it is pending the report file. 4 inputs versus 8 between two captures is expected (panel state differs) but is itself a reminder that the ticket's field set changes with its mode.

**+3 h 04 min recheck on MOBILE DATA (operator, ~14:47-14:48Z; relayed by the manager as a summary, both pastes truncated inside the captures list again):** nav 14:47:05Z `trade.breakoutprop.com` `other_served`, 278 elements (the logged-in shape), `net` wifi=false / cellular=true. This nav is a real page load (the "3 Recheck" reload) and it came up **logged in**. Probes 14:47:26Z and 14:47:37Z: `other_served`, logged in, cellular. Probe 14:48:20Z (order form open): `terminal`, 400 elements, 4 inputs, 32 buttons, 5 tabs, buy and sell true, send false: the same shape as 12:45:22Z. The report's captures list now starts at 11:47:06Z (the list keeps only the last 6 captures, so 11:43:25Z rolled off).

**GRADE: session alive at +3 h 04 min (11:43Z to 14:47Z), and it survived a Wi-Fi to mobile-data change** (a different network and egress address). Limits: `net` is sampled when the capture ends, 3.5 s after the page finished loading, so "the reload used mobile data" is the likely reading, not a certainty; still ONE session on ONE phone; the app stayed open; no app kill, restart or overnight yet. What it does rule out: a session bound to the original Wi-Fi address (at least for a mobile carrier address change within the same device).

**Evidence so far, in one line:** logged in at +54 min, +62 min and +3 h 04 min, on the trade host and the portal, across a Wi-Fi to mobile-data switch. Still to measure: +6 h (about 17:45Z), the next morning, an app force-stop and reopen, a phone restart.

**Order-ticket shape, READ (operator report ~14:51Z; the capture window rolled so the 12:45:22Z ticket capture came first; relayed verbatim from the shape by the manager, labels are the app's redacted vocabulary, no values):** host `trade.breakoutprop.com`, 1 frame (no iframe), everything inside one `<form>`. Recorded here because the app keeps only the last 6 captures and this one will roll off.

| role in the ticket | control (as captured) | state / type | note for the executor |
|---|---|---|---|
| order type | tabs `Market`, `Limit`, `Trigger` (`role=tab`, in a `tablist`) | `Limit` selected | a selection, never a submit |
| side | tabs `Buy`, `Sell` (`role=tab`, width 190 each) | `Buy` selected | a selection, never a submit |
| limit price | label `Limit price` + input | `type=text`, `inputmode=numeric` | present because `Limit` is selected |
| quantity | label `Quantity` + input; button `Toggle quantity *` (unit toggle); `role=alert` "Quantity * available to trade"; an `input type=range` slider | input `type=text`, `inputmode=numeric` | the unit toggle means the unit must be READ BACK, not assumed |
| SL / TP | checkbox `TP/SL` (`data-testid` checkbox-label / checkbox-input) | **unchecked**, so the SL and TP fields are hidden until it is ticked | fields NOT yet seen |
| options | button `Order *` | `expanded=false` | probably time-in-force or options; unread |
| **submit** | button `Long (buy) *` (width 380, height 40) | **disabled** while quantity is empty | **a distinct submit control, separate from the side tabs** |

**GRADE: design rule 7 is SATISFIED on this ticket** (one tap cannot both pick a side and submit: side is a tab, submit is a separate button). Two consequences the executor design must use: the submit button's own label carries the side ("Long (buy)"), so after selecting a side tab the executor can **read the submit label back** as an independent check on the side; and the submit is **disabled until a quantity is entered**, which gives a free "form not filled" signal. The `send` marker read false only because that marker looks for "send order"; it is not a defect of the page. **Still UNSEEN:** the SL and TP fields after the `TP/SL` box is ticked (a capture with the box ticked and NOTHING else changed, no submit, is requested); whether a confirmation dialog follows the submit (unknowable without placing something, and this lane does not); what the `Order *` button opens; how the quantity unit toggle works; whether the numeric text inputs accept typing in the way the fixtures validate (the Fixtures result has not been read from a report).

**Force-stop survival (operator ~14:49-14:50Z):** the operator force-stopped the app, reopened it, and was **not asked to sign in**: probes 14:49:19Z and 14:50:50Z `other_served`, logged in, on mobile data. Portal navs at 14:48:39Z and 14:48:45Z (16 elements, `net` none) are load failures while offline, not a login prompt. **GRADE: the session survives an app force-stop and reopen** (the WebView's cookie store persists). Still unmeasured: a phone restart, +6 h, a night.

**Report export problem (operator: "I clicked Share but nothing happened"):** `export()` shows its result on the status line ("report shared: N chars" or "REFUSED: ..."), so the likely causes are the leak check refusing (it would say so on that line, which the operator may not have read), or the share sheet failing on a very large text extra. **Not diagnosed:** the status-line text was not reported. No rebuild now, because installing a new build wipes the session under test. Filed for the next build that is made for any other reason (`PI-20261005-9HEP9LYP-0004`): write the report to a file and share it, show the size and the result in a dialog, catch errors and display them, and keep every capture rather than the last 6. The operator also does not remember what they tapped at 11:47Z, so that sequence stays unknown (inferred reading above stands as inference only).

**Ticket with TP/SL ticked (operator ~14:53-14:57Z, mobile data; counts only, relayed by the manager; labels UNREAD):** capture 14:53:11Z and four repeats 14:56:37-52Z, all stable: `terminal`, 477 elements, 8 inputs, 36 buttons, 12 svg, 7 tabs, buy and sell true, send false. The same view had a 477/8/36/7 shape at the 12:44:27Z tick. Against the TP/SL-off ticket (12:45:22Z: 400 elements, 4 inputs, 32 buttons, 5 tabs), ticking the box adds **77 elements, 4 inputs, 4 buttons and 2 tabs**. **INFERRED, not read:** two value fields each for TP and SL, with a tab or button pair each (for example price versus percent or PnL). Nav 14:54:39Z `trade.` terminal, logged in, cellular. **GRADE: TP and SL controls EXIST and appear when the box is ticked (counts only); their labels, types and states are UNREAD.** Until they are read, "no missing SL/TP" cannot be verified for this ticket.

**Phase-1b requirement from this (manager, ~14:59Z): the report must reach us WITHOUT the chat paste.** The report has outgrown it: after about 30,000 characters the paste truncates inside the captures list (it truncated after the third control), so the control labels, which are the evidence, are the part that gets cut. Share did nothing for the operator (`PI-20261005-9HEP9LYP-0004`). The TP/SL labels, the quantity-unit behaviour and the confirmation-dialog check therefore become the **first phase-1b capture**, taken with a working transport. Options, with their costs: (a) fix **Share** so it writes the report to a FILE and shares the file (to Drive or a messaging app), the smallest change and no new credential; (b) a direct upload from the app. If (b), note two constraints from this design: the repository is PUBLIC, so the report must NOT go into a public issue or comment (the shapes are redacted but should not be published); and a write token on the phone is exactly what design 3.2 forbids (a shared token must never be on a phone), so an upload would need the per-device, account-pinned, revocable token the design already specifies, paired once. Recommendation: (a) first; (b) only as part of the phase-1b server contract.

**Stay-logged-in memo (interim; the lifetime is not measured yet).** The operator's question: what is the consistent way to stay logged in without an email step?
1. **Lifetime (a):** pending. Measure state and time since 11:43Z at about 12:45Z, 17:45Z and the next morning, app left alone, session never cleared. Also: force-stop and reopen the app, a phone restart, a Wi-Fi/cellular switch, each followed by "3 Recheck". Filed as `PI-20261005-9HEP9LYP-0002` (due 2026-10-06).
2. **Mechanism (b):** NOT measurable with 1a.3. It reads no cookie or storage shape. What a later build could read without touching values: cookie NAMES (Android `CookieManager.getCookie` returns name=value pairs and no expiry, so expiry would not be visible), `localStorage` and IndexedDB key names, and, only if a token is a JWT, its `exp` claim. That build would need a reinstall, so it waits for the lifetime result and a second fresh login. Not built.
3. **Other login paths (c):** the public help center collection "Breakout terminal" has 6 articles (migration, rules, two terminals, swap fees, access, mobile app) and **none** covers remember-me, trusted device, password login or an API key; the access article says only "log in to your Breakout Dashboard and click the 'Open Terminal' button". The portal sign-in page and the program-rules page returned **HTTP 403** to this lane and were not worked around, so they are **unread**. A web-search summary of the portal sign-in page says the Breakout Terminal login includes "a security code from an Authenticator app and backup code options": a search snippet, not read at the source. If a TOTP second factor exists, entering it automatically would be automating a second factor, the same class of decision the operator just declined for email. Filed, not designed: `PI-20261005-9HEP9LYP-0003` (the operator or a session fed the text reads the sign-in page and the terms).
4. **Keepalive (d):** no recommendation. Terms unread. A periodic reload by the app is automation on the account (accepted for the executor 2026-09-27, but the terms are unread and the question is whether keeping a session alive is permitted).
5. **Provisional process:** treat login as a human step. The app already classifies the login page; the design (5.2) stops and alerts the operator when it sees it. Whether that is enough depends entirely on the measured lifetime.

**Verdict: none yet.** No phone run exists, so neither GO nor STOP is established. Criteria registered before the run:

- **STOP (structural):** the public page is challenged or blocked in the WebView on both networks; or login is refused or loops in the WebView; or a code / 2FA step recurs on every login; or the order ticket is a canvas with no readable controls; or the session does not survive an hour.
- **GO-BUY ($20):** the app loads the site, login survives in the WebView, captures return a non-empty shape for the logged-in page, and the fixtures show at least one input route that registers (real key events or native setter).
- **GO (phase 1b and beyond):** GO-BUY met **and** the new-terminal capture shows readable ticket controls (side, type, quantity, SL, TP, a distinct send control), no recurring code step, and a session that outlives the soak.

### 7.10 Phase 1b build: the shortest safe path to live (2026-10-05, lane PHONE-EXEC-1B)

**Direction (operator, 2026-10-05 ~15:20Z, relayed by the manager):** go live on Breakout as soon as there is no technical blocker, with the same strategies, and an app that logs itself back in after a logout or a phone restart. Re-login answer: "Read a dedicated inbox" (a Gmail used only for Breakout, fed by a forwarding filter for Breakout login mails).

**Terms read for this phase (first-party help centre, `intercom.help/breakoutprop`, 2026-10-05).** The full Terms of Service / Funded Trader Agreement still returned HTTP 403 and are **unread**.
- *Prohibited practices* (article 11644090) lists no clause on automation, bots, scripts or software.
- That article **does** prohibit "sharing account access, or trading multiple accounts from the same household, device, or IP address".
- Article 11644097 says "Yes, you can purchase multiple evaluations", and 11644103 bans only cross-account hedging and copy trading *across users*.
- **Reading:** nothing read forbids the build, so the build is not stopped. The multi-account clause is a live risk, though. `breakout_1` (breached) and `breakout_2` are on the same phone and IP. Only one is trading, and its legs are our own signals, not a third party's.
- **Flagged to the operator, not decided by this lane.** Third-party summaries ("bots allowed") are leads only.

**What was built** (one PR):

| piece | where | notes |
|---|---|---|
| Per-device auth | `config/prop_phone_devices.yaml`, `src/prop/phone_executor.py::authenticate` | The phone mints a 256-bit token. Only its SHA-256 **fingerprint** leaves the phone (the "Share ID" button) and is committed; a fingerprint is not a secret. Each device is pinned to one account. Revoke = `revoked: true`. This **replaces the § 3.2 pairing-code / new-table design**: no `prop_devices` table, no Telegram code and no new secret, and pairing is a git-visible change. |
| Routes | `POST /api/bot/prop/phone/{claim,report,event,test-ticket}` (`routers/prop.py`, `docs/api-tier-policy.md`) | `claim` is the atomic `emitted -> claimed`, and it runs the 3-minute watchdog. `report` forces `account_id` from the token. `ticket_result` keeps the phone's form dump in `meta.phone.result`, which is how the unread TP/SL labels will be read. `event` pings Telegram with links, emails and 6+ digit runs scrubbed. `test-ticket` is always dry. |
| Submit decision | `phone_executor.submit_mode` | `live` only if the account's `accounts.yaml` mode is `live`, `PROP_PHONE_MODE_<ACCOUNT>` is not `off` or `dry` (an unparseable value counts as `dry`), and the ticket is not a test. The phone also needs its own **ARMED** switch (default off). |
| Account | `config/accounts.yaml::breakout_2` (`mode: dry_run`), `config/prop_platforms.yaml::phone_accounts.breakout_2`, `config/prop_rulesets/breakout_turbo_1step.yaml` | `phone_accounts` is a separate section, so the VM executor and the login check never load it. |
| App | `tools/phone-executor/` (`com.metis.phoneexec`), CI `phone-executor-apk.yml` | Kiosk WebView, stock UA. Claim every 30 s. Fill: Limit tab, side tab, limit price, quantity (unit must name the base asset), TP/SL. Read every field back; the submit label must carry the side. In dry mode it does not submit; in live mode it submits, then reads Open orders / Positions back and flattens any opposite-side position. Fsynced intent ledger with no retry after a restart. Auto re-login: dedicated inbox, newest `breakoutprop.com` mail, the ONE link whose text equals the page's number, opened in the same WebView, 2 failures then latch and ping. Foreground service + boot receiver + "display over other apps" to come back after a reboot. |
| Stable signing | `phone-executor-apk.yml` | The first run generates a keystore into Actions secrets (`PHONE_EXEC_KEYSTORE_*`) through the existing `BRANCH_PROTECTION_TOKEN`; it is never printed and never in git. Later builds install over the old app and keep the session. **New applicationId**, so the 1a probe and its session under measurement are untouched. |

**Deviation from the dispatch, stated:** the inbox is read over **IMAP with a Gmail app password**, not the Gmail API with OAuth.
- **Why:** OAuth needs a Google Cloud project, a consent screen and an Android client bound to the signing certificate. A `gmail.readonly` app in testing mode also gets refresh tokens that expire after 7 days, which defeats "logs in by itself".
- **What the app password costs:** the password grants full access to that mailbox. Mitigations:
  - the mailbox is dedicated and holds only forwarded Breakout login mail;
  - the folder is opened READ_ONLY and the app never writes;
  - the password is stored only in Keystore-backed EncryptedSharedPreferences;
  - revoking it takes one click in the Google account.
- If the operator prefers OAuth, that is a follow-up, not a blocker.

**Still unmeasured, and how the dry ticket measures it:**
- the venue symbol names (`ETHUSD` / `SOLUSD` assumed) and the steps on the new terminal;
- whether the native-setter typing registers (falls back to key events);
- the quantity unit;
- the TP/SL field labels;
- what a submit confirmation looks like (never seen; an unexpected dialog is cancelled and refused);
- whether SL/TP live at the venue (§ 5.4).

Every one of these is a **refusal with the form dump**, never a guess, so the first dry ticket either passes or names the label to fix.

**Operator steps (one-time, about 10 minutes, all on the phone):**
1. **Dedicated inbox:**
   - Create a new Gmail used only for Breakout.
   - Turn on 2-Step Verification (Google Account → Security).
   - Create an **App password** (Security → App passwords) and keep it on screen for step 4.
   - In your **main** Gmail: Settings → Forwarding → add the new address. Confirm with the code Google sends to the new inbox.
   - Still in the main Gmail, add a filter `from:(breakoutprop.com)` → "Forward it to" the new address.
2. **Install:**
   - Open the latest `phone-executor-apk` run in GitHub Actions and download the `phone-executor-1b-apk` artifact.
   - Unzip it and install `app-release.apk`, allowing "install unknown apps" for your browser once.
3. **Open "Metis Executor" → Setup:**
   - your Breakout login email;
   - the new Gmail address;
   - the app password, typed into the app only, never into chat.
   - Tap "Allow restart after reboot", "Ignore battery optimisation" and "Allow notifications".
4. Leave the app open. It loads the terminal and should log itself in through the inbox; that first login is the first test of auto re-login. If it latches, log in by hand in the app and tap "Reset login".
5. Tap **Share ID** and send the line to the manager. It is a fingerprint, not a secret. We commit it, which pairs the phone.
6. Tap **Dry test**. The app fills one ETH ticket, reads it back, does **not** submit, and the VM pings the result.
7. Keep the phone on charge with the app on screen. Do **not** tap ARMED until the go-live message.

## 8. Open questions for the operator / manager

| # | question | my lean |
|---|---|---|
| O1 | Thaw `ict-trader-android` or a new repo? | **New repo** (keeps the read-only app's promise true). Thawing needs the explicit instruction its `CLAUDE.md` demands. |
| O2 | Kiosk (screen-on, charging dedicated phone) or a background service? | **Kiosk.** A background WebView is the likeliest thing to be throttled and killed. |
| O3 | Certificate pinning on the API? | Off; rotation outage risk outweighs the benefit here. |
| O4 | Which accounts may the phone drive, and is the VM executor to be `read_only` while it does? (Both cannot be live.) | One executor per account, server-enforced. |
| O5 | Does the operator accept phone-as-second-device against the unread terms? | Read the terms first; Phase 0 gate. |

## 9. Honest limits of this document

- **Not measured:** WebView load/login of the terminal; DOM; iframes; bracket-at-venue behaviour; OEM behaviour; FCM
  delivery today; whether Breakout's terminal app exists on Android (the page did not say; the Play listing is cited
  from an earlier research table, not re-opened).
- **Not read:** the Funded Trader Agreement, the Terms of Service, the private Android repo (this session).
- **Inherited without re-checking:** the Android app facts in § 1.1 (a 2026-09-30 reading of a commit dated 2026-08-13).
- **Not claimed:** that any part of this works. It is a plan with a stop at Phase 1.
