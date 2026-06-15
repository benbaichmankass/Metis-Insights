"""FCM HTTP v1 notifier (M12 S1).

Sends Firebase Cloud Messaging notifications to a list of device tokens
on behalf of the bot. Wraps:

- OAuth2 access-token acquisition from the service-account JSON
  (``google.oauth2.service_account.Credentials`` if available; otherwise
  the notifier degrades to inert + logs WARNING).
- The FCM HTTP v1 ``messages:send`` call.
- Per-token failure isolation (one bad token never blocks the others).
- Subscription routing — the notifier looks up registered devices in
  ``trade_journal.db::device_tokens`` and filters by the device's
  ``subscriptions`` JSON column.

All publish paths swallow exceptions: a network outage, a malformed
private key, an expired token, or an FCM 5xx must never propagate into
the trader. The notifier's only job is to OBSERVE existing events and
mirror them to the operator's phone; it holds no decision authority.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# OAuth2 scope required for FCM HTTP v1 publishing.
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

# HTTP timeouts. FCM is a Google endpoint with ~99.9% availability; a
# 10s ceiling is generous and bounds the worst case so a stuck request
# can't pin a coordinator thread.
_HTTP_TIMEOUT_S = 10.0

# Access tokens from Google are 1h-lived. Refresh on each publish if the
# remaining lifetime is under this floor; otherwise reuse the cached
# token so we're not minting one per event.
_TOKEN_REFRESH_FLOOR_S = 300  # 5 minutes


def _truthy_subscription(subscriptions_json: str | None, kind: str) -> bool:
    """Return True if ``kind`` is subscribed-to.

    Subscription semantics:

    - ``None`` / empty / missing column → subscribed to *everything*
      (default-permissive; matches the bot's "no third gate" principle).
    - JSON list ``["trade_closed", "signal_high_confidence"]`` →
      subscribed only to those kinds.
    - JSON object ``{"trade_closed": true, "signals": false}`` →
      explicit per-kind toggles; missing keys default to True.
    - Any parse failure → default-permissive (subscribed to everything),
      so a corrupt row never accidentally silences notifications.
    """
    if subscriptions_json is None:
        return True
    s = subscriptions_json.strip()
    if not s:
        return True
    try:
        parsed = json.loads(s)
    except (ValueError, TypeError):
        return True
    if isinstance(parsed, list):
        return not parsed or kind in parsed
    if isinstance(parsed, dict):
        return bool(parsed.get(kind, True))
    return True


@dataclass
class _AccessToken:
    value: str
    expires_at: float  # epoch seconds


class FcmNotifier:
    """Publishes FCM messages on behalf of the bot.

    Instances are cheap; construct one per process and reuse it.
    ``from_env`` is the canonical constructor for the runtime path;
    ``inert`` builds a no-op instance for environments where the feature
    flag is on but credentials are missing.
    """

    def __init__(
        self,
        *,
        service_account_info: dict[str, Any] | None,
        project_id: str | None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._service_account_info = service_account_info
        self._project_id = project_id
        self._http_client = http_client or httpx.Client(timeout=_HTTP_TIMEOUT_S)
        self._token: _AccessToken | None = None
        # ``google-auth`` is optional — when absent, the notifier stays
        # inert. Keeping it optional means importing the runtime module
        # doesn't force a new prod dependency in environments that
        # don't enable mobile push.
        try:
            from google.oauth2 import service_account  # type: ignore

            self._service_account_cls = service_account.Credentials
        except ImportError:
            self._service_account_cls = None

    @classmethod
    def from_env(cls) -> "FcmNotifier":
        """Build a notifier from process-env credentials.

        Prefers ``FCM_SERVICE_ACCOUNT_JSON_PATH`` (file path) — this is
        the production wire because systemd ``EnvironmentFile`` only
        supports single-line ``KEY=VALUE`` and the service-account JSON's
        private_key field is multi-line, which silently breaks the
        ``.env`` parser. Falls back to ``FCM_SERVICE_ACCOUNT_JSON``
        (inline JSON) for tests + sandboxed envs where single-line JSON
        is sufficient. Optionally reads ``FCM_PROJECT_ID`` (else falls
        back to the JSON's own ``project_id`` field).

        On any failure (missing env, unreadable file, bad JSON, missing
        project_id), logs a WARNING and returns an inert notifier —
        never raises.
        """
        import os

        path = os.environ.get("FCM_SERVICE_ACCOUNT_JSON_PATH", "").strip()
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = fh.read().strip()
            except OSError as exc:
                logger.warning(
                    "FcmNotifier.from_env: FCM_SERVICE_ACCOUNT_JSON_PATH=%s "
                    "could not be read (%s); notifier will be inert",
                    path,
                    exc,
                )
                return cls.inert()
            if not raw:
                logger.warning(
                    "FcmNotifier.from_env: %s is empty; notifier will be inert",
                    path,
                )
                return cls.inert()
        else:
            raw = os.environ.get("FCM_SERVICE_ACCOUNT_JSON", "").strip()
            if not raw:
                logger.warning(
                    "FcmNotifier.from_env: neither FCM_SERVICE_ACCOUNT_JSON_PATH "
                    "nor FCM_SERVICE_ACCOUNT_JSON is set; notifier will be inert"
                )
                return cls.inert()
        try:
            info = json.loads(raw)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "FcmNotifier.from_env: service-account JSON not valid (%s); "
                "notifier will be inert",
                exc,
            )
            return cls.inert()
        project_id = (
            os.environ.get("FCM_PROJECT_ID", "").strip()
            or info.get("project_id")
        )
        if not project_id:
            logger.warning(
                "FcmNotifier.from_env: project_id missing from env + "
                "service account JSON; notifier will be inert"
            )
            return cls.inert()
        return cls(service_account_info=info, project_id=project_id)

    @classmethod
    def inert(cls) -> "FcmNotifier":
        """Build an inert notifier whose publish methods are no-ops.

        Used when credentials are missing or unusable. ``is_active``
        returns False on inert instances.
        """
        return cls(service_account_info=None, project_id=None)

    @property
    def is_active(self) -> bool:
        return bool(self._service_account_info and self._project_id)

    def publish_to_subscribers(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Look up registered devices subscribed to ``kind`` and fan out.

        Returns a stats dict ``{attempted, succeeded, failed,
        skipped_unsubscribed}`` for observability. Never raises.
        """
        stats = {
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped_unsubscribed": 0,
        }
        if not self.is_active:
            return stats
        try:
            devices = self._load_devices()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FcmNotifier.publish_to_subscribers: device lookup failed: %s",
                exc,
            )
            return stats
        dead: set[str] = set()
        for token, subs_json in devices:
            if not _truthy_subscription(subs_json, kind):
                stats["skipped_unsubscribed"] += 1
                continue
            stats["attempted"] += 1
            ok = self._publish_one(
                token=token, kind=kind, payload=payload, dead_out=dead
            )
            if ok:
                stats["succeeded"] += 1
            else:
                stats["failed"] += 1
        # Prune permanently-dead tokens (uninstalled app / rotated token /
        # wrong sender) so we stop re-pushing to them on every event. Without
        # this, a single stale token logs an FCM 404/400 on *every* signal of
        # *every* tick forever (the 2026-06-15 log-spam incident). Best-effort:
        # a failed prune just means we retry next event — never raises.
        if dead:
            self._prune_tokens(dead)
        return stats

    def _prune_tokens(self, tokens: set[str]) -> None:
        """Delete permanently-dead device tokens from the journal DB.

        Opens a short-lived read-write connection (the publish cadence is
        low). Swallows every exception — pruning is a cleanup optimisation,
        never load-bearing for the publish path.
        """
        if not tokens:
            return
        try:
            from src.utils.paths import trade_journal_db_path

            db_path = trade_journal_db_path()
            conn = sqlite3.connect(db_path)
            try:
                placeholders = ",".join("?" for _ in tokens)
                conn.execute(
                    f"DELETE FROM device_tokens WHERE token IN ({placeholders})",
                    tuple(tokens),
                )
                conn.commit()
            finally:
                conn.close()
            logger.info(
                "FcmNotifier: pruned %d dead device token(s) (FCM 404/400/403)",
                len(tokens),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("FcmNotifier: dead-token prune failed: %s", exc)

    def _load_devices(self) -> list[tuple[str, str | None]]:
        """Return ``[(token, subscriptions_json), ...]`` from the journal DB.

        Read-only, connection-per-call (cheap for the publish cadence —
        trade closes happen at most a few per minute in live trading).
        """
        from src.utils.paths import trade_journal_db_path

        db_path = trade_journal_db_path()
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT token, subscriptions FROM device_tokens"
            )
            return [(row[0], row[1]) for row in cur.fetchall() if row[0]]
        finally:
            conn.close()

    def _publish_one(
        self,
        *,
        token: str,
        kind: str,
        payload: dict[str, Any],
        dead_out: set[str] | None = None,
    ) -> bool:
        """Publish one message; return True on 2xx, False otherwise.

        When the FCM response identifies the token as **permanently dead**
        and ``dead_out`` is provided, the token is added to it so the caller
        can prune it from ``device_tokens`` (and stop re-pushing every tick).
        Permanently-dead = 404 (UNREGISTERED / NOT_FOUND — app uninstalled or
        token expired), 400 INVALID_ARGUMENT (malformed registration token),
        or 403 SENDER_ID_MISMATCH. 401/429/5xx are transient and never prune.
        """
        try:
            access_token = self._get_access_token()
            if access_token is None:
                return False
            message = self._build_message(token=token, kind=kind, payload=payload)
            resp = self._http_client.post(
                f"https://fcm.googleapis.com/v1/projects/{self._project_id}"
                f"/messages:send",
                json={"message": message},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
            )
            if 200 <= resp.status_code < 300:
                return True
            body = resp.text or ""
            if self._is_dead_token(resp.status_code, body):
                if dead_out is not None:
                    dead_out.add(token)
                # Demote to DEBUG: a dead token is expected churn, and at
                # WARNING it floods the journal on every signal of every tick
                # until pruned. The prune-summary INFO line is the signal.
                logger.debug(
                    "FCM dead token (status=%s) — will prune. kind=%s",
                    resp.status_code,
                    kind,
                )
            else:
                logger.warning(
                    "FCM publish non-2xx: status=%s body=%s kind=%s",
                    resp.status_code,
                    body[:200],
                    kind,
                )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("FCM publish exception (kind=%s): %s", kind, exc)
            return False

    @staticmethod
    def _is_dead_token(status_code: int, body: str) -> bool:
        """True if an FCM non-2xx means the token is permanently unusable.

        404 → UNREGISTERED / NOT_FOUND (the canonical dead-token signal in
        HTTP v1). 400 → only when the body names the registration token /
        INVALID_ARGUMENT (a 400 can also be a malformed message, which we
        must NOT treat as a dead token). 403 → SENDER_ID_MISMATCH.
        """
        if status_code == 404:
            return True
        lowered = body.lower()
        if status_code == 400 and (
            "registration token" in lowered or "invalid_argument" in lowered
        ):
            return True
        if status_code == 403 and "sender_id_mismatch" in lowered:
            return True
        return False

    def _build_message(
        self,
        *,
        token: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the FCM HTTP v1 ``Message`` JSON.

        Data-only message (no ``notification`` field): the Android side
        owns notification rendering so it can route by ``kind`` and
        respect per-channel + quiet-hours preferences. Title/body for
        the system notification are derived from ``payload`` on the
        device.

        ``android.priority = HIGH`` is mandatory for these data-only
        messages: at the default (NORMAL) priority FCM holds data
        messages until the device next leaves Doze / App-Standby, which
        delivers them in delayed *batches* rather than at event time —
        exactly the symptom the operator reported. HIGH tells FCM the
        message is time-critical, so the device is woken and
        ``onMessageReceived`` fires within seconds. (HTTP v1's
        ``AndroidMessagePriority`` enum is uppercase ``NORMAL``/``HIGH``,
        unlike the legacy lowercase API.)

        Per FCM contract, data values MUST be strings — coerce here so
        the caller doesn't have to think about it.
        """
        data: dict[str, str] = {"event_kind": kind}
        for key, value in payload.items():
            if value is None:
                continue
            data[str(key)] = str(value) if not isinstance(value, str) else value
        return {
            "token": token,
            "data": data,
            "android": {"priority": "HIGH"},
        }

    def _get_access_token(self) -> str | None:
        """Return a non-expired OAuth2 access token, refreshing if needed.

        Caches the token between calls; refreshes when remaining
        lifetime is under ``_TOKEN_REFRESH_FLOOR_S``.
        """
        if not self.is_active:
            return None
        if self._service_account_cls is None:
            logger.warning(
                "FcmNotifier: google-auth not installed; install "
                "google-auth>=2.0.0 to enable FCM publishing"
            )
            return None
        now = time.time()
        if self._token and (self._token.expires_at - now) > _TOKEN_REFRESH_FLOOR_S:
            return self._token.value
        try:
            from google.auth.transport.requests import Request  # type: ignore

            creds = self._service_account_cls.from_service_account_info(
                self._service_account_info,
                scopes=[_FCM_SCOPE],
            )
            creds.refresh(Request())
            # ``creds.expiry`` is a naïve UTC datetime. Convert to epoch
            # the same way Google's helper does to avoid TZ drift.
            from datetime import timezone

            expiry_epoch = (
                creds.expiry.replace(tzinfo=timezone.utc).timestamp()
                if creds.expiry
                else now + 3600
            )
            self._token = _AccessToken(value=creds.token, expires_at=expiry_epoch)
            return self._token.value
        except Exception as exc:  # noqa: BLE001
            logger.warning("FcmNotifier: failed to mint access token: %s", exc)
            return None
