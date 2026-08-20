"""Admission and identity must cover the same fields — the anti-drift lock.

The operator reported this symptom three times: *"long after I close a prop
trade, I'm still getting monitoring things on Telegram … we keep on fixing the
one sequence but not fixing the root problem."*

Two prior fixes each closed an instance and left the class open — keying moved
off `ticket_id`, then the direction **normalizer** was hardened
(`BL-20260708-PROP-PULSE-DIRECTION-ALIAS`) — because neither made the two
modules AGREE. `prop_report.ingest_report` owned admission and validated two
fields; `_position_key` owned identity and required three. A fill admitted
without a direction is permanently unclosable.

These tests are the lock: if the identity contract grows a field and admission
is not taught about it, they fail. That is the only thing here that prevents a
fourth recurrence — the validation itself would have been a third instance fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.prop import prop_monitor_pulse  # noqa: E402
from src.prop.prop_position_identity import (  # noqa: E402
    IDENTITY_FIELDS,
    POSITION_BEARING_STATUSES,
    canonical_direction,
    missing_identity_fields,
    position_key,
)

# The exact live row that produced the phantom (population: all 32 prop fills,
# read 2026-08-20). Kept as a named fixture so the regression is concrete.
LIVE_ROW_30 = {"account_id": "breakout_1", "symbol": "SOLUSDT",
               "direction": None, "status": "open", "qty": 83.0}
LIVE_ROW_31 = {"account_id": "breakout_1", "symbol": "SOLUSDT",
               "direction": "long", "status": "closed", "qty": 83.0}


# ---------------------------------------------------------------------------
# the lock
# ---------------------------------------------------------------------------
def test_admission_validates_every_field_the_KEY_is_built_from():
    """The whole finding, as one assertion.

    If `IDENTITY_FIELDS` grows and `ingest_report` is not taught about it, the
    new field becomes admittable-but-unmatchable — a fresh instance of the same
    bug. This test reads the SHIPPING admission code rather than restating it.
    """
    src = (REPO / "src" / "prop" / "prop_report.py").read_text(encoding="utf-8")
    assert "missing_identity_fields(" in src, (
        "ingest_report does not validate against the identity contract at all"
    )
    assert "IDENTITY_FIELDS" in src, (
        "admission does not reference IDENTITY_FIELDS — if it validates a "
        "hardcoded list instead, the two will drift again, which is exactly "
        "how this survived two prior fixes"
    )


def test_the_key_and_the_contract_use_the_same_fields():
    """`position_key` must be built from IDENTITY_FIELDS, not a parallel list."""
    a = position_key({f: "x" for f in IDENTITY_FIELDS})
    b = position_key({f: "y" for f in IDENTITY_FIELDS})
    assert a != b, "the key ignores at least one declared identity field"
    for field in IDENTITY_FIELDS:
        base = {f: "x" for f in IDENTITY_FIELDS}
        changed = dict(base, **{field: "zzz"})
        assert position_key(base) != position_key(changed), (
            f"changing {field!r} does not change the key, so it is declared as "
            f"identity but does not identify anything"
        )


def test_the_pulse_keys_through_the_one_owner_not_a_private_copy():
    """A second implementation of the key is a second thing to keep in sync."""
    assert prop_monitor_pulse._position_key(LIVE_ROW_31) == position_key(LIVE_ROW_31)
    assert prop_monitor_pulse._canonical_direction is canonical_direction


# ---------------------------------------------------------------------------
# the defect itself
# ---------------------------------------------------------------------------
def test_the_live_phantom_row_would_now_be_REFUSED():
    assert missing_identity_fields(LIVE_ROW_30) == ["direction"]


def test_a_complete_row_is_accepted():
    """Negative control — proves the check discriminates on the FIELD, not on
    being called."""
    assert missing_identity_fields(LIVE_ROW_31) == []


def test_the_phantom_and_its_close_really_did_key_apart():
    """The mechanism, asserted rather than described."""
    assert position_key(LIVE_ROW_30) != position_key(LIVE_ROW_31)
    assert position_key(LIVE_ROW_30) == "akd:breakout_1|SOLUSDT|"


@pytest.mark.parametrize("bad", [None, "", "   ", "\t"])
def test_blank_directions_count_as_missing(bad):
    """A key built from `" "` is as unmatchable as one built from `None`."""
    assert missing_identity_fields(dict(LIVE_ROW_31, direction=bad)) == ["direction"]


# ---------------------------------------------------------------------------
# scope: refuse position-bearing reports, never the legitimate skip
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", sorted(POSITION_BEARING_STATUSES))
def test_every_position_bearing_status_is_gated(status):
    from src.prop.prop_report import ingest_report
    with pytest.raises(ValueError, match="direction"):
        ingest_report({"account_id": "breakout_1", "symbol": "SOLUSDT",
                       "status": status, "qty": 1})


def test_skipped_is_NOT_gated():
    """A skipped ticket was never placed, so it never becomes a position.

    Gating it would reject a legitimate report — 5 of the 32 live fills are
    `skipped`. This is the control that keeps the fix from over-reaching.
    """
    assert "skipped" not in POSITION_BEARING_STATUSES


def test_the_alias_map_is_still_load_bearing_on_live_data():
    """One of the 32 live fills carries `buy`, not `long`.

    It shares a key with its siblings ONLY because of the alias map. A future
    'simplification' that drops it silently re-splits that position.
    """
    assert canonical_direction("buy") == "long"
    assert canonical_direction("sell") == "short"
    assert position_key(dict(LIVE_ROW_31, direction="buy")) == position_key(LIVE_ROW_31)


def test_an_unknown_direction_passes_through_rather_than_being_coerced():
    """Coercing an unrecognised side into long/short would fabricate the field
    that decides which position a row belongs to."""
    assert canonical_direction("sideways") == "sideways"


# ---------------------------------------------------------------------------
# The two paths that must NOT be broken by requiring a direction.
#
# The gate is only defensible if the operator's real habits still work. Both of
# these were promises made when the grammar change was chosen (operator
# decision 2026-08-20, "add a direction word"), and neither had a test — the
# `close`-inherits path had ZERO references to `resolve_open_ticket` anywhere
# under tests/, which is exactly the kind of un-covered promise that turns into
# a regression the next time someone "simplifies" the resolver.
# ---------------------------------------------------------------------------

@pytest.fixture
def _prop_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


def test_bare_close_still_works_by_inheriting_the_open_ticket(_prop_env):
    """A `close` with no direction word inherits it from the open position.

    This is the back-compat half of the grammar change: the operator types the
    direction on the OPEN (where nothing else can supply it) and may keep
    typing a bare close, because by then a matching open exists to resolve from.
    Requiring it on both would be the change refusing reports it can answer.
    """
    from src.prop.telegram_report_handler import handle_command

    opened = handle_command("open ETHUSD 1812 1 long", default_account="breakout_1")
    assert opened is not None and opened.startswith("✅"), opened

    # No direction token anywhere in this line.
    closed = handle_command("close ETHUSD 1850 +38 tp", default_account="breakout_1")
    assert closed is not None and closed.startswith("✅"), closed


def test_screenshot_path_supplies_its_own_direction(_prop_env):
    """The photo path satisfies the gate without the operator typing anything.

    `screenshot_parse` extracts `direction` (buy|sell) and routes through the
    same `ingest_report` chokepoint, so the identity gate is transparent to it.
    Asserted rather than assumed, because if the extractor ever stopped
    emitting the field the gate would start refusing every screenshot and the
    failure would look like a vision problem, not an admission one.
    """
    from src.prop import screenshot_parse

    assert hasattr(screenshot_parse, "_norm_direction")
    # buy/sell is the vocabulary the extractor is prompted for; it must land on
    # a side the identity owner recognises, not pass through as a stray word.
    for raw, expected in (("buy", "long"), ("sell", "short")):
        norm = screenshot_parse._norm_direction(raw)
        assert norm is not None, raw
        assert canonical_direction(norm) == expected, (raw, norm)
