# Mobile push notifications — operator runbook

> M12 S1. The mobile push notifier is a **read-only side-channel
> observer**: it watches the trader's trade-close path and mirrors
> events to the operator's Android phone via Firebase Cloud Messaging.
> It cannot influence trading decisions, sizing, or risk. Failure of
> the notifier (FCM outage, expired token, missing credentials) never
> propagates into the trader.

> **Status (2026-06-16):** fully live. The notifier, `device_tokens`
> table, `/api/bot/devices/*` router, and the observer hooks in
> `Database.{insert_trade,update_trade}` all landed — trade
> open/close/update events (real **and** paper) fan out to FCM **and**
> the operator's Telegram. Mobile push is now **unconditional**: the
> default-off `MOBILE_PUSH_ENABLED` enable-gate (and the
> `enable-/disable-mobile-push` actions) were scrubbed as a silent
> footgun. Configuring `FCM_SERVICE_ACCOUNT_JSON` is the only setup step.

## Architecture (quick reference)

```
ict-trader-live.service                         operator's phone
  │                                              ▲
  │  Database.update_trade(status='closed')      │  FCM data message
  │       │                                      │  (event_kind=trade_closed)
  │       └─▶ publish_event("trade_closed", …)   │
  │              │                               │
  │              ▼                               │
  │         FcmNotifier (feature-flagged)        │
  │              │                               │
  └──── HTTPS ───┴──▶ fcm.googleapis.com ────────┘
                       (HTTP v1 + OAuth2)

trade_journal.db
  └── device_tokens table   ← upserts via POST /api/bot/devices/register
                              (router in ict-web-api.service)
```

Two units involved:

- **`ict-trader-live.service`** holds the notifier. It is unconditional
  (no enable flag); the FCM credential (`FCM_SERVICE_ACCOUNT_JSON`) lives
  in its `.env`.
- **`ict-web-api.service`** serves the `/api/bot/devices/*` router. No
  env vars required there — it just reads/writes the `device_tokens`
  table.

## First-time setup

### 1. Firebase project (already done — 2026-05-26)

Operator has already:

- Created the Firebase project `ict-trader-mobile-app` (project
  number `891024226160`).
- Registered an Android app with package name
  `com.benbaichmankass.icttradermobileapp` and SDK ID
  `1:891024226160:android:fbaa0a30e35cf3277ac1b5`.
- Downloaded `google-services.json` (goes into the Android app at
  `android/app/google-services.json`).
- Generated a service-account JSON for server-side publishing.

If any of those need redoing, the steps are in the original M12 setup
discussion (search session history for "Firebase setup").

### 2. Push the service-account JSON onto the live VM (file, not .env)

The trader needs the OAuth2 credentials to publish to FCM. The JSON
is stored as a **file** on the VM (`${DATA_DIR}/fcm_service_account.json`,
mode 600), and `.env` only holds a single-line pointer to it
(`FCM_SERVICE_ACCOUNT_JSON_PATH=…`). This is the standard GCP pattern
and is the **only** approach that works — see § Troubleshooting →
"systemd `Ignoring invalid environment assignment`" for why a prior
inline `FCM_SERVICE_ACCOUNT_JSON=…` approach was silently broken.

Two repo secrets in `benbaichmankass/ict-trading-bot`:

- `FCM_SERVICE_ACCOUNT_JSON` — entire JSON blob, **as stored in
  Actions secrets** (multi-line is fine; Actions preserves it intact).
- `FCM_PROJECT_ID` — optional, defaults to the `project_id` field
  inside the service-account JSON.

To push (or rotate) the credential onto the live VM, open a labelled
issue in `benbaichmankass/ict-trading-bot`:

- **Label:** `system-action`
- **Body:**
  ```
  action: set-mobile-push-secrets
  reason: <e.g. "M12 S1 initial push" / "credential rotation 2026-XX-XX">
  ```

The workflow:
1. Pulls the JSON from `secrets.FCM_SERVICE_ACCOUNT_JSON` (never
   transits the issue body or the run log).
2. Validates it parses as JSON before any write.
3. Writes it atomically to `${DATA_DIR}/fcm_service_account.json`
   (mode 600).
4. Sets `FCM_SERVICE_ACCOUNT_JSON_PATH` in the trader's `.env`
   (single-line value — systemd `EnvironmentFile`-safe).
5. Restarts `ict-trader-live.service`.
6. Posts an `[ops]` result on the issue and closes it.

No manual SSH edit is required. Re-running this action is the canonical
rotation path.

### 3. Install the Android app + register a device

1. Sideload the M12 S1 Android debug APK (see
   [`experiments/m12-s1-android-debug-app/README.md`](../../experiments/m12-s1-android-debug-app/README.md)
   for the build instructions; the source lives in the
   `benbaichmankass/ict-trader-android` repo once those files are
   pushed).
2. Open the app. It will show the current FCM token.
3. Tap "Register with bot". The app POSTs to
   `/api/bot/devices/register` and the token lands in
   `trade_journal.db::device_tokens`.

Verify the registration landed:

```bash
curl https://<bot-host>/api/bot/devices
# → {"count": 1, "devices": [{"id": 1, "token_suffix": "...", ...}]}
```

### 4. (Nothing to enable)

Mobile push is **unconditional** as of 2026-06-16 — there is no
`enable-mobile-push` action and no `MOBILE_PUSH_ENABLED` flag. Once the
FCM credentials from step 2 are on the VM (via `set-mobile-push-secrets`)
and a device is registered (step 3), push is live; the next trade event
fans out automatically. (The legacy default-off enable-gate was removed
as a silent footgun — see `src/runtime/mobile_push/__init__.py`.)

### 5. Verify end-to-end

Force a paper-trade close on `bybit_1` (the demo account) — easiest
via existing strategy activity, or trigger one manually via the
exchange UI. Within ~5s of the close landing in `trade_journal.db`,
a push notification should arrive on the phone with payload like:

```
event_kind: trade_closed
trade_id: 12345
symbol: BTCUSDT
direction: buy
pnl: 0.2
pnl_percent: 0.25
exit_reason: tp_hit
strategy: vwap
account: bybit_1
```

If nothing arrives within 30s of confirming the close landed in the
DB, see [Troubleshooting](#troubleshooting) below.

## Disabling

There is **no disable flag** (the gate was scrubbed 2026-06-16). Mobile
push is best-effort and inert when unconfigured, so the supported ways to
stop it are: remove the FCM credential from `.env` (e.g. `set-env`
clearing `FCM_SERVICE_ACCOUNT_JSON`), revoke the device(s) via
`/api/bot/devices`, or, for a code-level kill, revert the notifier change
and deploy. The `/api/bot/devices/*` router stays available regardless.

## Revoking a lost device

Use the device id from `GET /api/bot/devices`:

```bash
curl -X DELETE \
     -H "Authorization: Bearer <DASHBOARD_API_TOKEN>" \
     https://<bot-host>/api/bot/devices/<id>
```

The row is dropped; any future FCM publishes skip that token.

## Per-device subscription preferences

The default is "subscribed to everything." Most operators set this via
the Android **Settings → Notifications** screen (M12 S4) — toggles
persist locally + POST to `/api/bot/devices/register` on each change.
A curl path is provided too for ops scripts:

```bash
curl -X PATCH \
     -H "Authorization: Bearer <DASHBOARD_API_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"subscriptions": ["trade_closed", "telegram"]}' \
     https://<bot-host>/api/bot/devices/<id>/subscriptions
```

Setting `"subscriptions": null` returns to "all kinds." Setting
`"subscriptions": []` (empty list) is intentionally treated the same
as null — an explicit opt-in list is the operator's way to narrow
scope, not a way to accidentally silence everything via an empty
preferences screen.

**Unknown kinds 400 at registration.** As of M12 S4 (PR #TODO) the
canonical taxonomy lives in `src/runtime/mobile_push/event_kinds.py`
and the device endpoints reject any kind not in `ALL_KINDS`. This
catches typos at the wire (the operator's "I toggled it off but I'm
still getting them" bug class) instead of silently never matching a
publish three weeks later. Adding a kind:

1. Add the `Final[str]` constant + `LABELS`/`DESCRIPTIONS` entry +
   `ALL_KINDS` row to `event_kinds.py`.
2. Add it to `IN_FLIGHT` only once a real bot-side call site emits it.
3. Mirror the constant string in `feature/notifications/EventKind.kt`
   on the Android side (toggle row is auto-generated from
   `/api/bot/devices/event-kinds`).

### Canonical kinds (M12 S4)

| Kind | Status | Description |
|---|---|---|
| `trade_closed` | in flight | Every closed real-money trade (not backtest / demo). |
| `telegram` | in flight | Every message the bot would have sent to Telegram. |
| `signal_emitted` | in flight | Each buy/sell ICT detection (mirrors `/api/bot/signals` filter). |
| `health_concern` | reserved (M12 S6) | 7-point health check turned red. |
| `service_down` | reserved (M12 S6) | systemd unit failed. |
| `pnl_digest` | reserved (M12 S7) | Daily/hourly P&L summary. |

The Android app pulls the same list via
`GET /api/bot/devices/event-kinds` so the bot side stays the single
source of truth.

## Troubleshooting

### No notification arrives after a trade close

Check, in order:

1. **(No enable flag to check — push is unconditional.)** Skip straight
   to the FCM credential check.
2. **Is `FCM_SERVICE_ACCOUNT_JSON_PATH` set in `.env` AND does the
   target file exist + readable + valid JSON?**
   - `grep FCM_SERVICE_ACCOUNT_JSON_PATH /home/ubuntu/ict-trading-bot/.env`
   - `cat /data/bot-data/fcm_service_account.json | python3 -m json.tool >/dev/null && echo OK`
   - If either is missing/wrong, dispatch `set-mobile-push-secrets`.
3. **Is the trader running?** `systemctl is-active ict-trader-live.service`.
4. **Did the close actually land in `trade_journal.db`?**
   `GET /api/diag/journal?table=trades&limit=5` — look for
   `status='closed'` rows in the last few minutes.
5. **Was the trade backtest or demo?** The hook skips both. Confirm
   `is_backtest=0` AND `is_demo=0` on the row.
6. **Is the device registered?** `GET /api/bot/devices` — expect
   `count >= 1`.
7. **Is the device subscribed to `trade_closed`?** Check the row's
   `subscriptions` JSON (null/empty = subscribed to all).
8. **Did the notifier log a warning?**
   `journalctl -u ict-trader-live.service --since '10 min ago' | grep -i 'mobile_push\|fcm'`.
   Common causes: bad service-account JSON, expired token (auto-
   refreshed), FCM 5xx (transient).

If everything above looks correct and notifications still don't
arrive, the FCM token on the device may have been rotated (Android
does this periodically). Re-register through the app — the debug
screen has a "Re-register" button.

### systemd: `Ignoring invalid environment assignment` for FCM lines

If `journalctl -u ict-trader-live.service` shows lines like:

```
ict-trader-live.service: Ignoring invalid environment assignment
  '"private_key": "-----BEGIN PRIVATE KEY-----\nMIIE...'
```

…that's a **leftover broken `FCM_SERVICE_ACCOUNT_JSON=` line in
`.env` from before PR #2082 switched to the file-based credential
pattern**. systemd's `EnvironmentFile` parser only supports
single-line `KEY=VALUE`, and the service-account JSON's `private_key`
is multi-line — every continuation line got rejected. (This is what
made the M12 S1 push pipe silently inert until 2026-05-26.)

The new notifier prefers `FCM_SERVICE_ACCOUNT_JSON_PATH` and
ignores the orphan `FCM_SERVICE_ACCOUNT_JSON=…` lines, so pushes
work despite the journal noise. To clean up the spam, dispatch the
**`scrub-env-noncompliant`** system-action (see
[`docs/claude/system-actions.md`](../claude/system-actions.md)) — it
strips the non-compliant multi-line `FCM_SERVICE_ACCOUNT_JSON=` line
(and any orphan continuation lines systemd is rejecting) from the live
`.env` and restarts the trader in one audited run. No SSH, no
trainer-relay (the trainer relay cannot reach the live VM anyway). The
runtime impact is zero, only journal cleanliness.

### Trader crashed after enabling

This should be impossible by design — the notifier and the observer
hook are both wrapped in `try/except` that swallow every exception
before it can propagate. If a crash genuinely correlates with enabling
the notifier, immediately:

1. Kill the fan-out: clear `FCM_SERVICE_ACCOUNT_JSON` from `.env` (via
   `set-env`) so the notifier goes inert, or revert the notifier change.
2. Capture the crash:
   `journalctl -u ict-trader-live.service --since '5 min ago' > /tmp/crash.log`.
3. File the log under `docs/audits/` with a `MOBILE-PUSH-INCIDENT-`
   prefix — that's the signature of a bug worth a follow-up.

But again: by design, this should never happen. The observer hook's
test `test_publish_exception_does_not_propagate` enforces the
invariant.

### Notifications duplicate

A single `status='closed'` write fires one publish. If duplicates
arrive, the most likely cause is the close path being called more
than once for the same trade — that's a trader-side bug, not a
notifier bug. Inspect the trade row's history:

```bash
sqlite3 /data/bot-data/trade_journal.db \
  "SELECT id, status, exit_price, updated_at FROM trades WHERE id = <id>"
```

## Where the code lives

- `src/runtime/mobile_push/__init__.py` — `publish_event(kind, payload)`
- `src/runtime/mobile_push/notifier.py` — `FcmNotifier`
- `src/web/api/routers/devices.py` — `/api/bot/devices/*`
- `src/units/db/database.py` — `_fire_trade_closed_event` (the
  observer hook, inside `update_trade`)
- `src/runtime/notify.py` — `send_telegram_direct` includes a Telegram
  → FCM mirror (every operator-facing Telegram fires
  `publish_event("telegram", {text, parse_mode})`).
- `scripts/ops/set_mobile_push_secrets.sh` — Tier-2 operator action to
  install/rotate the FCM credential (the only push toggle left — push
  itself is unconditional)
- `tests/test_mobile_push.py`, `tests/test_devices_router.py`,
  `tests/test_mobile_push_observer_hook.py`,
  `tests/test_notify_telegram_fcm_mirror.py` — coverage

Plan: [`docs/sprint-plans/ROADMAP-ANDROID-COMPANION-APP-2026-05-26.md`](../sprint-plans/ROADMAP-ANDROID-COMPANION-APP-2026-05-26.md).
