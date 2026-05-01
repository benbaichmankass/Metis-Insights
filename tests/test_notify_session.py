"""Tests for scripts/notify_session.py — focused on the alert subcommand."""
from __future__ import annotations

import io
import sys
import types
import unittest
import urllib.error
from unittest.mock import patch

# Make the script importable without installing the package.
sys.path.insert(0, ".")
from scripts.notify_session import _cmd_alert, build_parser


class TestAlertArgParsing(unittest.TestCase):
    def test_alert_routes_to_cmd_alert(self):
        parser = build_parser()
        args = parser.parse_args(["alert", "--summary", "needs review", "--link", "https://example.com/pr/1"])
        self.assertIs(args.func, _cmd_alert)
        self.assertEqual(args.summary, "needs review")
        self.assertEqual(args.link, "https://example.com/pr/1")

    def test_alert_requires_summary(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["alert", "--link", "https://example.com"])

    def test_alert_requires_link(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["alert", "--summary", "something"])


class TestAlertMessageFormat(unittest.TestCase):
    def _run_alert(self, summary: str, link: str) -> str:
        captured: list[str] = []

        def fake_send(msg: str) -> None:
            captured.append(msg)

        parser = build_parser()
        args = parser.parse_args(["alert", "--summary", summary, "--link", link])

        with patch("scripts.notify_session._send", side_effect=lambda m: captured.append(m) or 0):
            result = args.func(args)

        self.assertEqual(result, 0)
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_message_contains_alert_header(self):
        msg = self._run_alert("PR needs merge", "https://github.com/foo/bar/pull/99")
        self.assertIn("Alert! - User Action Required", msg)

    def test_message_contains_summary(self):
        summary = "Please merge PR-M0 before I continue"
        msg = self._run_alert(summary, "https://github.com/foo/bar/pull/99")
        self.assertIn(summary, msg)

    def test_message_contains_link(self):
        link = "https://github.com/foo/bar/pull/99"
        msg = self._run_alert("some summary", link)
        self.assertIn(link, msg)

    def test_message_order_header_then_summary_then_link(self):
        summary = "needs review"
        link = "https://example.com/pr/1"
        msg = self._run_alert(summary, link)
        header_pos = msg.index("Alert! - User Action Required")
        summary_pos = msg.index(summary)
        link_pos = msg.index(link)
        self.assertLess(header_pos, summary_pos)
        self.assertLess(summary_pos, link_pos)


class TestAlertNoCredsPath(unittest.TestCase):
    """Per CP-2026-05-01-05, the script no longer swallows send errors.
    Missing creds is the ONLY back-compat exit-0 path (handled inside the
    helper, see TestTelegramDirectMissingCreds). Any other failure surfaces
    as exit 1 so the Stop-hook log shows the real failure mode.
    """

    def test_send_failure_propagates_as_nonzero_exit(self):
        parser = build_parser()
        args = parser.parse_args(
            ["alert", "--summary", "blocked", "--link", "https://example.com"]
        )

        def send_raises(_msg: str) -> None:
            raise RuntimeError("Telegram API returned ok=false")

        fake_notify = types.ModuleType("src.runtime.notify")
        fake_notify.send_telegram_direct = send_raises  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"src.runtime.notify": fake_notify}):
            import importlib
            import scripts.notify_session as ns_mod

            importlib.reload(ns_mod)
            try:
                result = ns_mod._cmd_alert(args)
                self.assertEqual(result, 1)
            finally:
                importlib.reload(ns_mod)


class TestNotifyImportIsLightweight(unittest.TestCase):
    """Regression for the matplotlib leak (CP-2026-05-01-04) and the
    AlertManager→dotenv leak (CP-2026-05-01-05).

    `src.runtime.notify` and `scripts.notify_session` are on the Stop-hook
    ping path. Pulling heavy / optional deps through them makes the path
    silently exit 0 on any sandbox without those deps installed, so the
    operator sees zero signal that pings aren't being delivered. Lock the
    import surface down.
    """

    FORBIDDEN = ("matplotlib", "pandas", "src.runtime.signal_notifications")
    SCRIPT_FORBIDDEN = (
        "matplotlib",
        "pandas",
        "src.runtime.signal_notifications",
        "dotenv",
        "src.bot.alert_manager",
    )

    def test_notify_does_not_pull_heavy_deps(self):
        import importlib

        for mod in self.FORBIDDEN:
            sys.modules.pop(mod, None)
        sys.modules.pop("src.runtime.notify", None)

        importlib.import_module("src.runtime.notify")

        for mod in self.FORBIDDEN:
            self.assertNotIn(
                mod,
                sys.modules,
                f"importing src.runtime.notify pulled in {mod} — Stop-hook ping path "
                f"will silently exit 0 on hosts without it",
            )

    def test_notify_session_script_does_not_pull_dotenv_or_alert_manager(self):
        import importlib

        for mod in self.SCRIPT_FORBIDDEN:
            sys.modules.pop(mod, None)
        sys.modules.pop("scripts.notify_session", None)

        importlib.import_module("scripts.notify_session")

        for mod in self.SCRIPT_FORBIDDEN:
            self.assertNotIn(
                mod,
                sys.modules,
                f"importing scripts.notify_session pulled in {mod} — "
                f"Stop-hook ping path will silently fail on hosts without it",
            )


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers=None):
        self._body = body
        self._status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self._status


class TestTelegramDirectSuccess(unittest.TestCase):
    def test_send_returns_cleanly_and_script_exits_zero(self):
        import importlib
        import json as _json

        sys.modules.pop("src.runtime.notify", None)
        sys.modules.pop("scripts.notify_session", None)
        notify = importlib.import_module("src.runtime.notify")
        ns = importlib.import_module("scripts.notify_session")

        body = _json.dumps({"ok": True, "result": {"message_id": 42}}).encode()
        fake = _FakeResponse(body, status=200)

        env = {"TELEGRAM_BOT_TOKEN": "abc:def", "TELEGRAM_CHAT_ID": "111"}
        with patch.dict("os.environ", env, clear=False):
            with patch("urllib.request.urlopen", return_value=fake) as m:
                # Direct helper returns cleanly.
                self.assertIsNone(notify.send_telegram_direct("hi"))
                self.assertTrue(m.called)
                # Script wrapper exits 0.
                rc = ns._send("hi")
                self.assertEqual(rc, 0)


class TestTelegramDirectMissingCreds(unittest.TestCase):
    def test_warns_and_returns_without_raising_and_script_exits_zero(self):
        import importlib

        sys.modules.pop("src.runtime.notify", None)
        sys.modules.pop("scripts.notify_session", None)
        notify = importlib.import_module("src.runtime.notify")
        ns = importlib.import_module("scripts.notify_session")

        env_clear = {}
        with patch.dict(
            "os.environ",
            env_clear,
            clear=True,
        ):
            with patch.object(notify.logger, "warning") as warn:
                self.assertIsNone(notify.send_telegram_direct("hi"))
                self.assertTrue(warn.called)
            # Script must still exit 0 for back-compat.
            rc = ns._send("hi")
            self.assertEqual(rc, 0)


class TestTelegramDirectNetworkError(unittest.TestCase):
    def test_url_error_yields_nonzero_exit_with_stderr_marker(self):
        import importlib

        sys.modules.pop("src.runtime.notify", None)
        sys.modules.pop("scripts.notify_session", None)
        importlib.import_module("src.runtime.notify")
        ns = importlib.import_module("scripts.notify_session")

        def raising_urlopen(*_a, **_kw):
            raise urllib.error.URLError("connection refused")

        env = {"TELEGRAM_BOT_TOKEN": "abc:def", "TELEGRAM_CHAT_ID": "111"}
        buf = io.StringIO()
        with patch.dict("os.environ", env, clear=False):
            with patch("urllib.request.urlopen", side_effect=raising_urlopen):
                with patch.object(sys, "stderr", buf):
                    rc = ns._send("hi")

        self.assertNotEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("telegram-network-error", out)


class TestTelegramDirectNoTokenInLogs(unittest.TestCase):
    def test_synthetic_token_never_appears_in_log_records(self):
        import importlib
        import json as _json
        import logging as _logging

        sys.modules.pop("src.runtime.notify", None)
        notify = importlib.import_module("src.runtime.notify")

        secret = "TEST_TOKEN_DO_NOT_LOG"
        captured: list[str] = []

        original_handle = _logging.Logger.handle

        def capturing_handle(self, record):
            try:
                captured.append(record.getMessage())
                captured.append(str(record.args))
                if record.exc_info:
                    captured.append(repr(record.exc_info))
            except Exception:  # noqa: BLE001
                pass
            return original_handle(self, record)

        body = _json.dumps({"ok": True, "result": {"message_id": 7}}).encode()

        env = {"TELEGRAM_BOT_TOKEN": secret, "TELEGRAM_CHAT_ID": "111"}
        # Success path
        with patch.dict("os.environ", env, clear=False):
            with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
                with patch.object(_logging.Logger, "handle", capturing_handle):
                    notify.send_telegram_direct("hi")

        # Network-error path (also must not log token)
        with patch.dict("os.environ", env, clear=False):
            with patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("boom"),
            ):
                with patch.object(_logging.Logger, "handle", capturing_handle):
                    try:
                        notify.send_telegram_direct("hi")
                    except urllib.error.URLError:
                        pass

        joined = "\n".join(captured)
        self.assertNotIn(secret, joined)


if __name__ == "__main__":
    unittest.main()
