"""Regression tests for scripts/secret_scan.py's patterns.

Born from BL-20260801-TELEGRAM-BOT-TOKEN-COMPROMISE: the telegram pattern's
leading \\b could not match a token embedded in the Telegram API URL
("api.telegram.org/bot<TOKEN>/...") because there is no word boundary between
't' and the first digit — so the REQUIRED secret-scan check was structurally
blind to tokens in their single most common leaked form, and two real tokens
sat in committed httpx logs on the public repo for ~4 months. The
URL-embedded case below FAILS against the pre-fix pattern by construction.

Fixture tokens here are fake (and the word 'fake' rides each carrier line,
matching the scanner's ALLOW_WORDS contract, so the scanner itself never
flags this test file).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from secret_scan import PATTERNS, is_allowed  # noqa: E402

_TELEGRAM = dict(PATTERNS)["telegram_bot_token"]

# fake token for tests
_FAKE = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


class TestTelegramTokenPattern:
    def test_bare_token_matches(self):
        assert _TELEGRAM.search(_FAKE)  # fake

    def test_url_embedded_token_matches(self):
        # THE regression: 'bot' immediately before the digits (no word
        # boundary) — the form httpx logs and the form that actually leaked.
        line = f"POST https://api.telegram.org/bot{_FAKE}/getUpdates"  # fake
        assert _TELEGRAM.search(line)

    def test_env_assignment_matches(self):
        assert _TELEGRAM.search(f"TELEGRAM_BOT_TOKEN={_FAKE}")  # fake

    def test_plain_number_does_not_match(self):
        assert not _TELEGRAM.search("epoch 1722441600123: processing done")

    def test_longer_digit_run_not_matched_mid_number(self):
        # (?<!\d) keeps a 13+-digit run from yielding a 12-digit suffix match.
        assert not _TELEGRAM.search(
            "9" * 13 + ":" + "A" * 35 + " checksum-looking, not a token")

    def test_fixture_lines_ride_the_allowlist(self):
        # The scanner skips lines carrying an allow word — the contract this
        # test file (and test_log_redaction.py's FAKE_TOKEN) depends on.
        assert is_allowed(f'FAKE_TOKEN = "{_FAKE}"')
