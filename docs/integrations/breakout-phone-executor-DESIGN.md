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
   phone app's first job is **measurement**, not clicking. Phase 1 below is a capture-only build.
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
Phase 1 DOM capture, a first-party read of Breakout's terms (§ 6), and the operator's go. Phases 0–2 are read-only and
cost about 6 lane-days; the whole path to a watched live click is about 14–17 lane-days (§ 7.2).

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
terminal and its login work inside a WebView at all is **unmeasured** and is Phase 1's first question. If they do not
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
| `_frames(page)` (the terminal may live in iframes) | **Risk.** `evaluateJavascript` runs in the main frame only. Cross-origin iframes need `androidx.webkit` `addDocumentStartJavaScript` with origin rules and `WebMessageListener` | Unknown whether the terminal uses iframes; Phase 1 finds out. If it does and the support is missing, the phone route is infeasible and that is reported. |
| `classify_page` stop reasons | Run the same marker checks on each load; any of `challenge`, `captcha`, `email_code`, `2fa`, `asn_blocked`, `access_denied`, `login_rejected`, `unknown_page` ⇒ **stop, no retry, alert** | The app never reloads in a loop. |
| `place_bracket(arm=True)` | **Does not exist yet on either side** (rule 9). Built in Phase 3 from the Phase 1 capture | Held Tier-2 change, as `PI-20260928-DRBVUUDJ-0001`. |
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
(itself marked from-memory in places). Phase 2 measures it on the actual phone for 72 hours.

### 5.2 Design

| concern | design |
|---|---|
| **Process lifetime** | A **foreground service** with a pinned notification showing state (`armed / read-only / stopped: <reason>`, last tick age, open-ticket count). The WebView needs a live process and a visible `Activity` or a service holding it; a WebView in a service is delicate. **Open question O2:** whether the WebView must stay in a foreground `Activity` ("keep the screen on, plugged in" kiosk mode) rather than a service. The reliable default is **kiosk mode: a dedicated screen-on, charging phone**. A background-only WebView is expected to be throttled. |
| **Foreground service type** | A type that is not time-limited on Android 14/15. `dataSync` is capped per day at `targetSdk 35` (from memory, unverified); the app must choose and verify the type in Phase 2. |
| **Doze / App Standby** | Charging + screen on avoids most of it. Battery-optimisation exemption requested; `PARTIAL_WAKE_LOCK` held only while armed. FCM **high-priority** data messages wake the app for a new ticket. |
| **OEM battery killers** | The operator must exempt the app per the OEM's settings; verified in Phase 2 by the 72-hour soak. |
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
single most important fact to measure** before any unattended use: in Phase 1/2 the operator places one tiny test position
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

### 7.1 Phases

| phase | what | read-only? | est. (lane-days) | exit gate |
|---|---|---|---|---|
| **0. Decide and prepare** | Operator answers O1–O4 (§ 8). Read the terms (§ 6.1). **Step zero, ~2 min, no login:** open `app.breakoutprop.com` in the phone's browser on home Wi-Fi and mobile data and report which page appears (served / "Just a moment…" / access denied) — what the operator already said works. Confirm FCM delivery to the phone today (runbook check). | yes | 0.5 | Terms read; operator go for Phase 1. |
| **1. Capture-only app** | New app: WebView + foreground/kiosk shell, manual login, **page-state classifier**, and a **redacted DOM capture** (same redaction posture as `scripts/prop/breakout_terminal_probe.py`: no URL paths, tokens, cookies, ids, balances) uploaded to a draft-only endpoint or shared as a file. Answers: does the terminal load and log in inside a WebView; iframes?; the order form's real fields, the submit and its confirmation; where SL/TP toggles are; which input method works. **No ticket intake, no clicks.** | yes | 3–4 | A captured, redacted DOM committed as fixtures; the Q "does a WebView load and log in" answered **yes/no**. If **no**, stop here. |
| **2. Read-only executor** | Server: `prop_devices`, pairing, per-device auth, `claim`/`verify`/`report`, heartbeat, watchdog, mode `read_only`. Phone: ticket intake, terminal read (account, positions, orders), reconcile, heartbeat, intent ledger, alerts. It computes and logs what it **would** do (the VM's `read_only` mode); **no click, no write to the order journal beyond account-status**. 72-hour soak on the real phone: uptime, session expiries, OEM kills, handovers. Measures § 5.4 (does the bracket survive the client). | read-only | 5–6 | 72 h with an uptime/expiry/kill table; server tests incl. two-device claim race; the bracket-survival measurement. |
| **3. Armed click, dry then one watched live** | Held Tier-2: the submit and confirmation built against the Phase 1 capture; dry round trip (D1–D7 analogue), then **one** minimum-size live round trip under the pre-registered L1–L8 criteria (registered **before** the run, may be tightened, not loosened); the operator present. | no | 3–4 | D and L criteria pass; auto-revert armed. |
| **4. Soak** | Probation (first 5 trades or 72 h), trade-by-trade reports, then steady-state. Auto-revert to `read_only` on the same triggers as the VM executor. | no | 2 + observation time | Gate 1-style review. |

### 7.2 Effort

| bucket | lane-days (estimate) |
|---|---|
| Phase 0 | 0.5 |
| Phase 1 | 3–4 |
| Phase 2 (server 2.5–3, app 2.5–3) | 5–6 |
| Phase 3 | 3–4 |
| Phase 4 active work | 2 |
| Independent review rounds (every held Tier-2 PR got several on the VM executor: 5 rounds on `#14216`) | included above at ~15%, but **likely to be the long pole** |
| **Total** | **≈ 14–17 lane-days** (Phases 0–2 ≈ 9–10.5 of those) |

**Biggest uncertainties:** whether the terminal and login work in a WebView at all (Phase 1 gate); iframe reachability; the
armed submit's confirmation flow (still unmeasured); OEM kill behaviour; the unread terms.

### 7.3 What the operator does

1. **Decides** O1–O4 and **reads or forwards the terms** (§ 6.1).
2. **Phase 0 step zero** (browser, ~2 min, no sign-in), on both Wi-Fi and mobile data.
3. **Installs the APK** (sideload from App Distribution or a link): turns on "install unknown apps" for the source, once.
4. **Logs in once by hand** in the app's WebView, and re-does it after each session expiry (the app stops and alerts; it
   never types credentials). Provide any email code or 2FA by hand.
5. **Pairs the device** (the app shows a pairing screen; the one-time code arrives in the operator's Telegram).
6. **Dedicates the phone** (Phase 2 on): screen on, charging, home Wi-Fi, battery-optimisation exemption set, the app
   pinned. Tells us the **phone model** (OEM behaviour).
7. In Phase 2: **place one tiny test position by hand** in the terminal and close the app to measure § 5.4.
8. **Authorises Phase 3** explicitly. It is a real-money click path.

---

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
