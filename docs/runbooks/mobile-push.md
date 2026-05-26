# Mobile push notifications — operator runbook

> M12 S1. The mobile push notifier is a **read-only side-channel
> observer**: it watches the trader's trade-close path and mirrors
> events to the operator's Android phone via Firebase Cloud Messaging.
> It cannot influence trading decisions, sizing, or risk. Failure of
> the notifier (FCM outage, expired token, missing credentials) never
> propagates into the trader.

> **Status (2026-05-26):** the notifier module, `device_tokens`
> table, `/api/bot/devices/*` router, and operator actions are
> landed. The trade-close observer hook in `Database.update_trade`
> that fans events into the notifier is **deferred to a follow-up PR**
> while a CI test interaction is investigated. Until that lands,
> `enable-mobile-push` flips the flag but no events fire — the
> infrastructure is in place, the wire-up is one small commit away.

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

- **`ict-trader-live.service`** holds the notifier. The
  `MOBILE_PUSH_ENABLED` env var lives here.
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

### 2. Add the service-account JSON to the live VM .env

The trader needs the OAuth2 credentials to publish to FCM. Two repo
secrets in `benbaichmankass/ict-trading-bot`:

- `FCM_SERVICE_ACCOUNT_JSON` — entire JSON blob (single line, escape-
  free; GitHub Actions secrets accept multi-line input cleanly)
- `FCM_PROJECT_ID` — optional, defaults to the `project_id` field
  inside the service-account JSON

Pushing those values onto the VM's `.env` is currently a manual edit
of `/home/ubuntu/ict-trading-bot/.env` plus a service restart — there
is **not yet** a `set-env mobile-push` operator action wrapping it.
Filed as a follow-up (small operator-action script) so the secret can
be rotated via the existing action allowlist.

For now: SSH-relay or the trainer VM diag relay can fetch and edit the
`.env` file. The required additions:

```
FCM_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"ict-trader-mobile-app",...}
FCM_PROJECT_ID=ict-trader-mobile-app
```

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

### 4. Enable the notifier

Open a labelled issue in `benbaichmankass/ict-trading-bot`:

- **Label:** `system-action`
- **Body:**
  ```
  action: enable-mobile-push
  reason: <e.g. "M12 S1 — turn on phone notifications for trade closes">
  ```

The `system-actions.yml` workflow runs `scripts/ops/enable_mobile_push.sh`
on the live VM, which sets `MOBILE_PUSH_ENABLED=1` in `.env` and
restarts `ict-trader-live.service`. Posts result back on the issue and
closes it.

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

Open a labelled issue:

- **Label:** `system-action`
- **Body:**
  ```
  action: disable-mobile-push
  reason: <why>
  ```

The script sets `MOBILE_PUSH_ENABLED=0` and restarts the trader. The
`/api/bot/devices/*` router stays available — devices can still
register / be revoked. Push notifications resume the moment
`enable-mobile-push` is re-run.

## Revoking a lost device

Use the device id from `GET /api/bot/devices`:

```bash
curl -X DELETE \
     -H "Authorization: Bearer <DASHBOARD_API_TOKEN>" \
     https://<bot-host>/api/bot/devices/<id>
```

The row is dropped; any future FCM publishes skip that token.

## Per-device subscription preferences

The default is "subscribed to everything." To narrow:

```bash
curl -X PATCH \
     -H "Authorization: Bearer <DASHBOARD_API_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"subscriptions": ["trade_closed", "watchdog_alert"]}' \
     https://<bot-host>/api/bot/devices/<id>/subscriptions
```

Setting `"subscriptions": null` returns to "all kinds." Setting
`"subscriptions": []` (empty list) is intentionally treated the same
as null — an explicit opt-in list is the operator's way to narrow
scope, not a way to accidentally silence everything via an empty
preferences screen.

## Troubleshooting

### No notification arrives after a trade close

Check, in order:

1. **Is `MOBILE_PUSH_ENABLED=1` in `.env`?** Diag relay:
   `grep MOBILE_PUSH_ENABLED /home/ubuntu/ict-trading-bot/.env`.
2. **Is the trader running?** `systemctl is-active ict-trader-live.service`.
3. **Did the close actually land in `trade_journal.db`?**
   `GET /api/diag/journal?table=trades&limit=5` — look for
   `status='closed'` rows in the last few minutes.
4. **Was the trade backtest or demo?** The hook skips both. Confirm
   `is_backtest=0` AND `is_demo=0` on the row.
5. **Is the device registered?** `GET /api/bot/devices` — expect
   `count >= 1`.
6. **Is the device subscribed to `trade_closed`?** Check the row's
   `subscriptions` JSON (null/empty = subscribed to all).
7. **Did the notifier log a warning?**
   `journalctl -u ict-trader-live.service --since '10 min ago' | grep -i 'mobile_push\|fcm'`.
   Common causes: bad service-account JSON, expired token (auto-
   refreshed), FCM 5xx (transient).

If everything above looks correct and notifications still don't
arrive, the FCM token on the device may have been rotated (Android
does this periodically). Re-register through the app — the debug
screen has a "Re-register" button.

### Trader crashed after enabling

This should be impossible by design — the notifier and the observer
hook are both wrapped in `try/except` that swallow every exception
before it can propagate. If a crash genuinely correlates with enabling
the notifier, immediately:

1. Disable: open a `system-action` issue with `action: disable-mobile-push`.
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
- `scripts/ops/enable_mobile_push.sh` / `disable_mobile_push.sh` —
  Tier-2 operator actions
- `tests/test_mobile_push.py`, `tests/test_devices_router.py`,
  `tests/test_mobile_push_observer_hook.py` — coverage

Plan: [`docs/sprint-plans/ROADMAP-ANDROID-COMPANION-APP-2026-05-26.md`](../sprint-plans/ROADMAP-ANDROID-COMPANION-APP-2026-05-26.md).
