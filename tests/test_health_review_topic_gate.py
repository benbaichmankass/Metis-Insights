"""Pin the comms-handler gate that drops Telegram for health-review topics.

Adopted 2026-05-12 as part of the health-check noise cleanup. The
health-snapshot workflow no longer mints `comms/requests/REQ-*.json`
files automatically — the operator pulls the snapshot artifact from
the Action UI and pastes into Claude directly. This gate quiets any
in-flight backlog that survived the migration: the comms-poller's
deliver pass short-circuits, and the expiry pass declines to alert.

The matcher is a conservative `topic.lower().startswith("health review")`
— the deleted emitter (`scripts/write_health_review_request.py`) was
the only producer, and it set the topic to
``Health review needed — run <run_id> (<STATUS>)``.
"""
from __future__ import annotations

from src.bot.comms_handler import _is_health_review_topic


def test_matches_health_review_needed():
    assert _is_health_review_topic("Health review needed — run 25708699987 (WARNING)") is True


def test_matches_with_lowercase_prefix():
    assert _is_health_review_topic("health review for last 6h") is True


def test_matches_with_trailing_whitespace_safe():
    # We don't strip — that's the caller's job. But the literal prefix
    # still matches even with leading "Health Review" capitalisation.
    assert _is_health_review_topic("Health Review of the night cycle") is True


def test_does_not_match_unrelated_topic():
    assert _is_health_review_topic("M5 backtest result for vwap") is False
    assert _is_health_review_topic("Operator action: pull-and-deploy") is False
    assert _is_health_review_topic("Tier-2 approval needed for restart") is False


def test_does_not_match_none_or_empty():
    assert _is_health_review_topic(None) is False
    assert _is_health_review_topic("") is False


def test_does_not_match_substring_in_middle():
    # The legacy emitter put the marker at the start. Substring matches
    # mid-string aren't legacy artifacts; don't suppress them.
    assert _is_health_review_topic("Daily summary mentions health review") is False
