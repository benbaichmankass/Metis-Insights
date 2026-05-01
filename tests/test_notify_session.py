"""Tests for scripts/notify_session.py — focused on the alert subcommand."""
from __future__ import annotations

import sys
import types
import unittest
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
    def test_returns_zero_when_send_raises(self):
        def raising_send(msg: str) -> int:
            raise RuntimeError("no creds")

        parser = build_parser()
        args = parser.parse_args(["alert", "--summary", "blocked", "--link", "https://example.com"])

        with patch("scripts.notify_session._send", side_effect=raising_send):
            # _cmd_alert calls _send, which raises — but _send itself wraps
            # send_via_alert_manager. We test the _cmd_alert -> _send boundary
            # by replacing _send entirely.  When _send raises, _cmd_alert
            # should propagate — so we verify the no-creds path at the
            # send_via_alert_manager level instead.
            pass

        # Simulate the actual no-creds path: send_via_alert_manager raises,
        # _send catches it and returns 0, so _cmd_alert returns 0.
        def send_via_raises(msg: str) -> None:
            raise Exception("TELEGRAM_BOT_TOKEN not set")

        fake_notify = types.ModuleType("src.runtime.notify")
        fake_notify.send_via_alert_manager = send_via_raises  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"src.runtime.notify": fake_notify}):
            # Re-import to pick up patched module inside _send's try/except.
            import importlib
            import scripts.notify_session as ns_mod
            importlib.reload(ns_mod)
            result = ns_mod._cmd_alert(args)
            self.assertEqual(result, 0)
            # Restore
            importlib.reload(ns_mod)


class TestNotifyImportIsLightweight(unittest.TestCase):
    """Regression for the matplotlib leak (CP-2026-05-01-04).

    `src.runtime.notify` is on the Stop-hook ping path. Pulling matplotlib
    through it makes the path silently exit 0 on any sandbox without
    matplotlib installed, so the operator sees zero signal that pings
    aren't being delivered. Lock the import surface down.
    """

    FORBIDDEN = ("matplotlib", "pandas", "src.runtime.signal_notifications")

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


if __name__ == "__main__":
    unittest.main()
