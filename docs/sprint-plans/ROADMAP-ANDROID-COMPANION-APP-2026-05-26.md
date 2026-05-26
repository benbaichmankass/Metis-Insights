# Sprint Roadmap: Native Android Companion App (M12)

**Created:** 2026-05-26
**Initiative ID:** ANDROID-COMPANION-APP
**ROADMAP.md milestone:** M12
**Status tracking:** See `docs/sprint-plans/CURRENT-SPRINT.md` for the active sprint.

---

## 1. Mission

Build a native Android companion app that gives the operator three
phone-resident surfaces for the live ICT trading bot:

1. **Push notifications** — bot-driven alerts the moment a trade fires,
   a stop hits, an account flips dry, the watchdog escalates, etc.
2. **Dashboard** — read-only mirror of the existing Streamlit surfaces
   (stats, positions, recent trades, signals, P&L, health) reachable
   from the home screen.
3. **Home-screen widget** — large (≈4×5) Android widget showing the
   summary the operator wants visible all day: status pill, 24h P&L,
   open positions, recent signals, last-tick freshness.

Same trust contract as the Streamlit dashboard: the app is a **read-only
consumer** of the bot's existing FastAPI on `:8001` (plus one new
device-registration endpoint), and holds no money-at-risk state of its
own. Server-to-app comms ride **Firebase Cloud Messaging (FCM)** — a
small, isolated notifier module on the bot publishes; the phone
receives.

---

## 2. Non-negotiable constraints (all sprints)

These mirror the program-wide rules — they exist so this milestone
cannot regress the live trader.

- **Live trading safety > app features.** A push that doesn't fire, a
  widget that doesn't refresh, or a dashboard tab that returns 500 is
  ALWAYS preferable to anything that delays, blocks, or alters an
  order.
- **Notifier never sits on the execution hot path.** All FCM dispatch
  is `try/except`-wrapped + best-effort + behind a feature flag. A
  failed FCM HTTP call cannot raise into the order/fill/signal flow.
  The notifier is a *side observer*, never a *gate*.
- **No new mutating routes on the bot.** The only new bot endpoint is
  the device-registration POST (writes to a new table; touches nothing
  on the order path). All other reads continue through the existing
  Tier-1 `/api/bot/*` surface that the Streamlit dashboard already
  uses.
- **No order-path code touched.** The notifier subscribes to existing
  signal/fill/event sinks; it does not edit `src/runtime/orders.py`,
  `src/runtime/risk_counters.py`, `src/core/coordinator.py`, or any
  strategy.
- **App lives in a separate repo** (`benbaichmankass/ict-trader-android`,
  to be created at S2 start) — mirrors the dashboard repo separation.
  No Android toolchain in the bot repo.
- **No secrets in the app or in chat.** FCM server key + signing keys
  live in GitHub Actions secrets / Play Console; FCM device tokens
  live in `trade_journal.db::device_tokens` (operator's tokens only,
  no PII beyond what the operator chooses to register).
- **Feature flag every bot-side hook.** `MOBILE_PUSH_ENABLED`
  (default false). A flip back to false silences all dispatch with
  zero code change.
- **No iOS in scope for M12.** A second native app is a full second
  milestone; do not blur the line.

---

## 3. Current-state assessment (2026-05-26)

### What's already there to lean on

| Asset | Path | What we reuse |
|---|---|---|
| Bot FastAPI | `src/web/api/main.py` + `routers/` (:8001) | All read endpoints — `/api/bot/stats`, `/positions`, `/signals`, `/trades/closed`, `/strategies`, `/health/*`, `/pnl/history`, etc. The app calls these directly. |
| Tier-1 read contract | `docs/api-tier-policy.md` | Defines what's safe to consume; the app is purely Tier-1. |
| Canonical SQLite | `trade_journal.db` (`/data/bot-data/`) | New `device_tokens` table lives here, resolved through `src.utils.paths.trade_journal_db_path()`. No new DB file. |
| Heartbeat / liveness contract | `runtime_logs/heartbeat.txt`, `runtime_status.json` | The widget's "is the bot alive" pill reuses the existing freshness logic from `src/runtime/heartbeat.py::heartbeat_label`. |
| Signal/fill audit sinks | `runtime_logs/signal_audit.jsonl`, `trade_journal.db::trades`, `runtime_logs/advisory_decisions.jsonl` | The notifier observes these; it does not introduce new sinks. |
| Trainer-mirror federation | `trainer_store.db` + Data Explorer | If we want a "training cycles" notification in S4, the source already exists. |
| Streamlit dashboard reference | `ict-trader-dashboard/streamlit_app.py` | Behavioral spec for what the app's screens should show — same data, same shapes, same nullability rules. |

### What's missing

| Gap | Sprint |
|---|---|
| No FCM credentials / Firebase project | S1 |
| No `device_tokens` table / registration endpoint | S1 |
| No notifier module | S1 |
| No Android repo / project | S2 |
| No app auth flow against the bot API | S2 |
| No on-device dashboard surfaces | S3 |
| No event→FCM dispatch wiring | S4 |
| No notification subscription preferences | S4 |
| No home-screen widget | S5/S6 |
| No release pipeline (Play / signed APK) | S7 |

---

## 4. Architecture target

```
                                                          ┌─────────────────────────┐
                                                          │  Phone (Android)        │
                                                          │                         │
                                                          │  ┌──────────────────┐   │
              ┌─── new ───┐         ┌────────────────┐    │  │ App (Kotlin +    │   │
              │           ▼         ▼                │    │  │ Compose) — calls │   │
  Bot pipeline ──▶  FcmNotifier ─▶ FCM HTTP v1 ─────────────▶│ same /api/bot/*  │   │
  (order fills,│   (best-effort, │  API           │    │  │ as Streamlit     │   │
  signals,     │   feature-flagd │                │    │  └──────────────────┘   │
  watchdog,    │   `try/except`) │                │    │           │             │
  health)      │                 │                │    │           ▼             │
              │                 │                │    │  ┌──────────────────┐   │
              │ (no order-path  │                │    │  │ FCM service +    │   │
              │  modification)  │                │    │  │ notification ch. │   │
              └─────────────────┘                │    │  └────────┬─────────┘   │
                       │                        │    │           │             │
                       ▼                        │    │           ▼             │
              ┌──────────────────┐               │    │  ┌──────────────────┐   │
              │ device_tokens   │               │    │  │ Glance widget    │   │
              │ (trade_journal) │               │    │  │ (home screen,    │   │
              └────────▲─────────┘              │    │  │  ~4×5)           │   │
                       │                        │    │  └──────────────────┘   │
                       │ POST /api/bot/devices  │    └─────────────────────────┘
                       └────────────────────────┘
```

**Critical isolation property:** every arrow that touches the live
trader (the leftmost block) goes ONE WAY into the notifier; nothing
the app does — token registration, dashboard reads, widget refreshes
— ever flows back into the order pipeline. The notifier reads sinks
the trader is already producing; it never mutates state the trader
reads from.

---

## 5. Sprint roadmap

> **Tier convention** (same as the rest of the program): Tier-1 ships
> autonomously on `main`; Tier-2 is prepared then merged after one
> operator OK; Tier-3 requires explicit operator approval per change.

### S1 — Push-notification stack end-to-end MVP
**ID:** S-ANDROID-S1
**Type:** Tier-1 (bot endpoint, notifier module, schema) + Tier-2 (one notifier hook into the existing fill path, feature-flagged)
**Goal:** Prove the full server→phone path with the smallest possible app.

This is the milestone's first sprint and matches the
"low-risk first milestone" we agreed in chat — validate the whole
notification pipe before investing in dashboard or widget code.

**Bot-side deliverables:**
- New `src/runtime/mobile_push/notifier.py` — `FcmNotifier.publish(event)`.
  Pure stdlib + `httpx` (already a dep). `MOBILE_PUSH_ENABLED` env flag,
  default false. All publishes wrapped in `try/except`; failure logs a
  WARNING and returns — never raises into the caller.
- New `src/runtime/mobile_push/__init__.py` — `publish_event(kind, payload)`
  module-level convenience that pulls the notifier from a process-wide
  singleton built once at startup.
- New `device_tokens` table in `trade_journal.db` (FCM token + label
  + created_at + last_seen_at). Schema migration via the lazy
  table-creation path (`src/units/db/database.py`).
- New router `src/web/api/routers/devices.py` exposing
  `POST /api/bot/devices/register` (body: `{token, platform: "android", label?}`),
  `GET /api/bot/devices` (token-gated; for debugging — never exposes
  tokens), `DELETE /api/bot/devices/{id}`.
- **Exactly one** observer hook wired in this sprint: the existing
  trade-fill writer (`src/runtime/outcomes.py` or equivalent — the
  point where a trade transitions to `closed`). The hook calls
  `publish_event("trade_closed", {...})`. This is the Tier-2 piece —
  it touches a live-path edge, so wraps + flag are mandatory.
- Operator-action allowlist entry: `set-mobile-push-mode {on|off}`
  (mirrors `set-account-mode`). Flips `MOBILE_PUSH_ENABLED` on the VM
  systemd unit's `EnvironmentFile`.
- New runbook `docs/runbooks/mobile-push.md` — Firebase project setup
  the operator has to do once (creates the FCM project, downloads
  `google-services.json`, drops the server key into Actions secrets).

**App-side deliverables (minimal):**
- New repo `benbaichmankass/ict-trader-android` initialized
  (gitignore, Android Studio project skeleton — Kotlin + Compose +
  AGP).
- Single screen: a debug page that registers the device's FCM token
  with the bot API (token + a "label this device" text field) and
  lists the last 10 received push payloads.
- `FirebaseMessagingService` subclass that displays incoming pushes
  on the default notification channel.
- No auth, no real dashboard, no widget yet — proves the pipe.

**Tests:**
- `tests/test_mobile_push_notifier.py` — notifier swallows HTTP
  failures + respects feature flag.
- `tests/test_devices_router.py` — register / list / delete happy
  paths + 401 on missing token.
- One end-to-end test against a mocked FCM endpoint.

**Verification (manual, operator-driven, no auto-merge of the Tier-2 hook):**
- Operator installs the debug app, registers their token.
- Operator forces a paper trade to close on `bybit_1` and confirms a
  notification reaches the phone within ≤ 10 s.
- Operator flips `MOBILE_PUSH_ENABLED=false` and confirms the next
  paper trade close produces no notification but does not raise.

**Constraints / non-goals:**
- No notification preferences (S4).
- No additional event hooks beyond `trade_closed` (S4).
- No app UI beyond the debug page (S2/S3).
- No widget (S5/S6).

---

### S2 — Android project scaffold + auth wiring + first read screen
**ID:** S-ANDROID-S2
**Type:** Out-of-tier (separate repo, no bot-side code).
**Goal:** Stand up the proper app project and validate the read API
contract from a real phone.

**Deliverables (Android repo only):**
- Project structure: `app/`, `core/network`, `core/data`, `feature/status`.
  Single-module-per-feature layout so widget + screens can share `core/`.
- `Retrofit` (or `Ktor`) client wired to the bot's existing FastAPI;
  base URL configurable in app settings (default the live VM hostname).
- Auth: bearer-token field in app settings — token is what's already
  served as `DASHBOARD_API_TOKEN` on the bot side. No new auth surface
  on the bot.
- One real screen: **Status** — calls `GET /api/bot/stats`, renders
  status pill, 24h P&L, open trades count, win rate, datasource, VM
  health (CPU / RAM / disk). Pull-to-refresh + 30 s auto-poll.
- Crash reporter wired (Firebase Crashlytics — same Firebase project
  as FCM, no second SDK).
- App `BuildConfig` flag `IS_PREVIEW` for a future preview channel
  (parallels the dashboard's `claude/web-app-preview`).

**Bot-side:** zero changes.

**Verification:** operator installs over the S1 debug app; status
screen matches the Streamlit dashboard's status row.

---

### S3 — Core dashboard surfaces in-app
**ID:** S-ANDROID-S3
**Type:** Out-of-tier (app repo).
**Goal:** Operator can open the app and see what they'd see on Streamlit.

**Deliverables (Android repo only):**
- Bottom-nav with five tabs mirroring the Streamlit surfaces the
  operator uses daily:
  - **Status** (from S2; promote into the nav)
  - **Positions** — `GET /api/bot/positions`, each row shows symbol,
    side, qty, entry, unrealized P&L, SL/TP, strategy. Null fields
    render as em-dash (matches dashboard's nullability rules per
    `ict-trading-bot/CLAUDE.md` `Position` shape).
  - **Trades** — `GET /api/bot/trades/closed?limit=50`. Tap for
    detail (P&L, strategy, open/close timestamps).
  - **Signals** — `GET /api/bot/signals?limit=50`. Strategy +
    pattern + confidence + zones summary.
  - **Health** — `GET /api/bot/health/latest` + `/services` +
    `/api/bot/stats.vmHealth`. Single glance at "is everything OK".
- Repository pattern in `core/data` so the widget (S5+) can share the
  same fetchers without duplicating parsing.
- Offline / stale handling: show cached payload + `last_fetched_at`
  timestamp; stale > 5 min puts the screen into a muted state with a
  retry CTA.
- Settings screen: API base URL, bearer token, notification toggles
  placeholder (wired in S4), preview channel toggle.

**Bot-side:** zero changes. (If any nullable field we depend on is
under-populated in the API, file a follow-up sprint on the bot side
rather than expanding M12 scope.)

**Verification:** operator runs the app side-by-side with the
Streamlit dashboard for 24h; values match.

---

### S4 — Event-driven notifications + subscription preferences
**ID:** S-ANDROID-S4
**Type:** Tier-2 (additional notifier hooks; same wrapping + flag rules as S1).
**Goal:** Real-time pushes for everything the operator wants to know
about within seconds.

**Bot-side deliverables:**
- Extend `src/runtime/mobile_push/__init__.py` event kinds:
  - `trade_opened` — at fill confirmation
  - `trade_closed` (already in S1)
  - `signal_high_confidence` — confidence ≥ threshold (configurable;
    no notification spam from low-conviction signals)
  - `account_mode_changed` — observe `set-account-mode` action
  - `watchdog_alert` — reuse the liveness-watchdog Telegram path
    (`scripts/check_heartbeat.py`); send to FCM in parallel, not
    instead.
  - `health_concern` — when the cron health-snapshot lands at
    `🟡 watch` / `🚨 concern`
  - `daily_summary` — once per UTC day, summary digest
- Subscription routing: each push carries `event_kind`. Per-device
  preferences stored in `device_tokens.subscriptions` (JSON column);
  the notifier filters at publish time so an unsubscribed device
  doesn't get the FCM call at all.
- One new endpoint: `PATCH /api/bot/devices/{id}/subscriptions` —
  body is the subscription set.
- Each event hook is its own wrapped call site; failure in one event
  type can never cascade into another (defence-in-depth — match the
  WS7 multi-predictor isolation pattern).

**App-side deliverables:**
- Settings → Notifications: per-event toggles + "quiet hours" (no
  pushes between operator-configured local times — purely on-device
  filtering, the bot still publishes).
- Notification channels (per Android channel-per-kind convention):
  `trades`, `signals`, `health`, `watchdog`, `daily`. Operator can
  silence channels in OS settings without uninstalling.
- Deep-link routing: tapping a `trade_closed` notification opens the
  Trades tab scrolled to that trade; `signal_high_confidence` →
  Signals tab; `health_concern` → Health tab.

**Tests:**
- Each notifier hook has its own failure-isolation test (one hook
  raising does not break the others).
- Subscription filter logic unit-tested.

**Verification:** operator subscribes only to `trade_closed` +
`watchdog_alert`; runs for 24h; confirms no other notifications
arrive.

---

### S5 — Home-screen widget v1 (Glance, status-only)
**ID:** S-ANDROID-S5
**Type:** Out-of-tier (app repo).
**Goal:** Minimum viable always-on home-screen surface, learn the
widget refresh contract before scaling the layout.

**Deliverables (Android repo only):**
- `androidx.glance.appwidget` widget. Initial layout: ~2×2 (small),
  ~3×3 (medium), ~4×3 (large) — register all three sizes so the
  operator picks. Content for v1:
  - Status pill (running / paused / stopped, from heartbeat label)
  - 24h P&L (signed, color-coded)
  - Open positions count
  - "Updated 3 min ago" footer
- Data source: `core/data` repository from S3 — widget reuses the
  same fetchers. No new bot endpoints.
- Refresh strategy: `WorkManager` every 15 min (Android's minimum
  practical periodic interval). On every refresh, widget calls
  `/api/bot/stats` once and updates state.
- Tap → launches the app onto the Status tab.
- "Bot unreachable" rendering when the fetch fails (so a dead VM is
  visible, not invisible).

**Bot-side:** zero changes.

**Verification:** operator pins the widget; over 24h, the widget
remains accurate within its 15-min refresh window.

**Why this is a separate sprint from S6:** Glance has gotchas
(deferred recomposition, theming, restricted layout primitives).
Land the 2×2 first so we don't compound learning with layout
complexity.

---

### S6 — Rich widget (4×5) + FCM-triggered refresh
**ID:** S-ANDROID-S6
**Type:** Out-of-tier (app repo).
**Goal:** The widget the operator described — large, content-rich,
"always fresh" feel.

**Deliverables (Android repo only):**
- New large widget size (~4×5, or "extra-large" — Android picks the
  cell count based on launcher; we declare the target dp). Content:
  - Status pill + last-tick freshness
  - 24h P&L + week P&L
  - Open positions: up to 3 rows showing symbol / side / qty /
    unrealized P&L
  - Recent signals: up to 2 rows showing strategy / pattern /
    confidence
  - Stale-data indicator (yellow border when >10 min since last
    successful fetch)
- **FCM data-message refresh path** — bot's notifier publishes a
  silent `widget_refresh` data message after every event the user
  subscribed to. App receives → enqueues an expedited `WorkManager`
  job → widget re-renders within seconds. Replaces "wait 15 min" with
  "refresh-on-event" while keeping the 15-min poll as a safety net.
- New `WidgetActionReceiver` for tap-to-deep-link inside the widget
  (tap position row → Positions tab scrolled to that position).

**Bot-side deliverables:**
- New `widget_refresh` event kind in the notifier (silent FCM data
  message, no user-facing notification). Wired into the same observer
  fan-out as S4. Same isolation + feature flag rules apply.

**Tests:**
- `widget_refresh` publish is OFF when `MOBILE_PUSH_ENABLED=false`.
- Failure to publish `widget_refresh` does not prevent the user-
  facing notification of the same event.

**Verification:** operator runs the bot for a full session; widget
visibly updates within seconds of trade events, not at 15-min
intervals.

---

### S7 — Release readiness + ops runbook
**ID:** S-ANDROID-S7
**Type:** Tier-1 (docs + ops); out-of-tier (release pipeline).
**Goal:** App is something the operator can install reliably + Claude
can support over time.

**Deliverables:**
- Android repo: release-signed APK pipeline via GitHub Actions
  (`.github/workflows/android-release.yml`). Two channels:
  - **Preview** — pushes to `claude/android-preview` branch produce
    a signed debuggable APK + uploads to a release. Mirrors the
    Streamlit `claude/web-app-preview` workflow.
  - **Production** — push to `main` produces the release APK.
- Optional: internal Google Play track. Skipped if the operator
  prefers sideloading from GitHub Releases.
- `docs/runbooks/mobile-app.md` (bot repo) — operator runbook:
  - How to add a new device (install APK → debug page → register).
  - How to revoke a lost device.
  - How to flip `MOBILE_PUSH_ENABLED` if anything misbehaves.
  - Where the FCM server key lives, how to rotate it.
  - What to do if pushes stop arriving (diagnose: device token
    expired? FCM quota? Bot notifier disabled? Watchdog still firing
    Telegram?).
- `docs/CLAUDE-RULES-CANONICAL.md` + `docs/ARCHITECTURE-CANONICAL.md`
  amendments: add the Android app as an authorized read-only
  consumer next to the Streamlit dashboard; document the notifier as
  a non-order-path observer.
- `doc-freshness` pass.
- Sprint log: `docs/sprint-logs/S-ANDROID-S7-2026-MM-DD.md`.

**Verification:** operator installs the production APK from a fresh
device, registers, subscribes to all event kinds, and uses the app +
widget for 7 days without intervention.

---

## 6. Open questions for the operator (decide before S1 starts)

1. **App distribution model** — Google Play internal track (requires
   one-time $25 dev account, easier to push updates) vs sideloaded
   signed APK from GitHub Releases (zero cost, slightly clunkier
   installs)? Recommendation: sideloaded APK for the first 1–2
   months, Play internal track in S7 if it sticks.
2. **Repo name** — `ict-trader-android` (precise, single-platform) vs
   `ict-trader-mobile` (room for an iOS sibling later). Recommendation:
   `ict-trader-android` — the moment scope grows to iOS it's a new
   milestone and likely a new repo anyway.
3. **Bot API token reuse** — share the existing `DASHBOARD_API_TOKEN`
   with the app, or mint a separate `MOBILE_API_TOKEN`? Recommendation:
   separate token so a leaked phone can be revoked without resetting
   the dashboard.
4. **FCM project ownership** — Firebase project under the operator's
   personal Google account vs a dedicated trading-ops Google account.
   Recommendation: dedicated — keeps the trading ops surface separate
   from personal cloud.

---

## 7. Out of scope for M12 (filed for later)

- **iOS / iPhone app.** Full second milestone if/when added.
- **In-app strategy controls** (toggle a strategy on/off from the
  phone). This is a Tier-3 mutating surface and is intentionally not
  in M12 — keep the app read-only-plus-notifications until it's
  proven stable.
- **In-app account-mode flip.** Same as above.
- **In-app `/test <strategy>` runs.** The dashboard exposes
  backtests; the Android app does not need a way to trigger them in
  M12.
- **Two-way operator approval flow on the phone** (approve a Claude
  Tier-2/3 ask from the lock screen). Compelling, but separate
  milestone — needs its own trust contract design.
- **Web-app push notifications** (the original framing of the
  thinking). Superseded by native — Streamlit can keep its `st.toast`
  while-the-tab-is-open behaviour as-is.

---

## 8. What this milestone doesn't touch (the "no interference" guarantee)

For the operator's "what can we get done without doing anything that
will interfere" question, the explicit list:

| Subsystem | M12 touches it? |
|---|---|
| Live order path (`src/runtime/orders.py`, `risk_counters.py`) | **No.** |
| Strategy logic (`src/units/strategies/*`) | **No.** |
| Coordinator dispatch (`src/core/coordinator.py`) | **No.** |
| ML pipeline (`ml/`, shadow / advisory layers) | **No.** |
| Trainer VM | **No.** |
| `config/accounts.yaml`, `config/strategies.yaml`, `config/risk_caps.yaml` | **No.** |
| Existing FastAPI read routes | **No** (additive only — one new route group at `/api/bot/devices/*`). |
| Streamlit dashboard | **No** (independent consumer; same API contract). |
| Telegram alerts / watchdog | **No** (FCM runs *in parallel*, not *instead*). |
| `trade_journal.db` schema | **Additive only** — one new `device_tokens` table. |
| Notifier observer hooks on existing event sinks (`trade_closed`, signals, etc.) | **Yes**, but `try/except`-wrapped, feature-flagged, default-off, Tier-2 per hook. |

If any sprint finds itself needing to modify something in the "No"
rows, **stop and re-scope** — that's a sign the design has drifted.

---

## 9. Sprint sequencing + dependencies

```
S1 ─┬─▶ S2 ──▶ S3 ──▶ S4 ──▶ S5 ──▶ S6 ──▶ S7
    │                  ▲
    │ (S4 builds on    │
    │  S1's notifier)  │
    └──────────────────┘
```

- **S1** is the only sprint that can start immediately; everything
  downstream needs the FCM project + the notifier in place.
- **S2 + S3** can be done by the operator in parallel with S4's
  bot-side work if there are two people. Solo: linear is fine.
- **S5 vs S6 ordering matters** — do S5 before S6; do not skip
  straight to S6. Glance has enough gotchas that an MVP widget pays
  for itself.
- **S7 can begin anytime after S2** but only closes after S6 lands.
```

---

## 10. Cross-references

- ICT bot read API contract: `ict-trading-bot/CLAUDE.md` § Dashboard REST API
- Streamlit dashboard (precedent for "read-only consumer" pattern):
  `benbaichmankass/ict-trader-dashboard`
- Trust contracts: `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers
- Architecture: `docs/ARCHITECTURE-CANONICAL.md` (M12 will add an
  "Android companion app" subsection at S7)
- Existing notification path (Telegram + watchdog):
  `docs/runbooks/liveness-watchdog.md`, `scripts/check_heartbeat.py`
