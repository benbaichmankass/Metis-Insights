"""The diag `log_file` allowlist, its documentation, and the alert latches.

TWO MEASURED DEFECTS, 2026-08-23.

1. THE DOC WAS STALE BY EIGHT NAMES. `CLAUDE.md`'s
   `GET /api/diag/log_file?name={...}` row listed 21 names while
   `diag.py::_LOG_FILES` carried 26. A relay-bound analysis session reads the
   doc to learn what it may ask for, so an undocumented surface is an
   unreachable one -- written and unreadable, the shape this repo already
   pays for elsewhere.

2. THREE ALERT LATCHES HAD NO READ SURFACE. Two of five durable
   `*_alert_state.json` latches were allowlisted and three were not, which is
   an inconsistency rather than a policy. It matters because each latch
   SUPPRESSES an operator page and each fails LOUD when its state file cannot
   be read (alerting is the only safe direction for a safety page). So a
   permanently-unwritable latch reproduces exactly the spam it exists to stop,
   and from outside the two are INDISTINGUISHABLE -- the alert rate looks the
   same either way. Reading the latch is what tells them apart.

This test pins the doc/code coherence, which is the general recurrence
detector: any future name added to `_LOG_FILES` without documenting it fails
here, whatever it is.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _code_names() -> set:
    src = (ROOT / "src/web/api/routers/diag.py").read_text(encoding="utf-8")
    block = re.search(r"_LOG_FILES\s*[:=].*?\{(.*?)\n\}", src, re.S)
    assert block, "could not locate _LOG_FILES in diag.py"
    names = set(re.findall(r'"([a-z_]+)"\s*:', block.group(1)))
    assert names, "parsed _LOG_FILES but found no names -- the probe is broken, "\
                  "not the allowlist (a negative needs a denominator)"
    return names


def _documented_names() -> set:
    doc = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    m = re.search(r"GET /api/diag/log_file\?name=\{([^}]*)\}", doc)
    assert m, "could not locate the log_file row in CLAUDE.md"
    # the row lives in a markdown table, so its pipes are backslash-escaped
    return set(m.group(1).replace("\\", "").split("|"))


def test_every_allowlisted_log_file_is_documented():
    code, doc = _code_names(), _documented_names()
    assert not (code - doc), (
        "these log_file names are reachable but UNDOCUMENTED, so a session "
        f"reading CLAUDE.md cannot know to ask for them: {sorted(code - doc)}"
    )


def test_the_doc_does_not_promise_a_surface_that_does_not_exist():
    code, doc = _code_names(), _documented_names()
    assert not (doc - code), (
        "CLAUDE.md documents log_file names the allowlist does not serve; a "
        f"caller would get a refusal for a surface the doc promised: {sorted(doc - code)}"
    )


def test_the_three_alert_latches_stay_readable():
    """The specific regression: a latch that suppresses a page must be readable."""
    code = _code_names()
    for latch in ("silent_refusal_alert_state",
                  "prop_fills_staleness_state",
                  "target_naked_alert_state"):
        assert latch in code, (
            f"{latch} suppresses an operator page and fails LOUD when unreadable, "
            "so a broken latch is indistinguishable from a working one without a "
            "read surface"
        )


def _cooldown_kinds() -> set:
    """Every ``kind`` passed to ``_cooldown_admits`` in the trader source.

    DERIVED, not enumerated. ``_alert_state_path`` resolves a kind to
    ``runtime_logs/<kind>_alert_state.json``, so each kind implies a latch file
    that must be readable — and deriving the set is the whole point: the
    hardcoded list below it could not see a kind that did not exist when it was
    written.
    """
    import re
    src = (ROOT / "src/runtime/order_monitor.py").read_text(encoding="utf-8")
    kinds = set(re.findall(r'_cooldown_admits\(\s*"([a-z0-9_]+)"', src))
    assert kinds, (
        "parsed order_monitor.py but found no _cooldown_admits kinds -- the "
        "probe is broken, not the source (a negative needs a denominator)"
    )
    return kinds


def test_every_cooldown_latch_has_a_read_surface():
    """THE RECURRENCE DETECTOR the enumerated test above could not be.

    Measured 2026-08-30, during a /system-review backlog drive: the Bybit
    over-cover page latches through `_cooldown_admits("bybit_over_cover", ...)`,
    which resolves to `bybit_over_cover_alert_state.json` -- a DIFFERENT file
    from its IB sibling `stop_over_cover_alert_state.json`, and it was never
    allowlisted. `?name=stop_over_cover_alert_state` returned `present: true`
    while `?name=bybit_over_cover_alert_state` returned nothing.

    That was the FOURTH instance of the class
    (`BL-20260825-ALERT-AND-CADENCE-STATE-FILES-SHIP-WITHOUT-A-READ-SURFACE`),
    and the existing latch test could not catch it because it names three files
    literally. This one derives the expectation from the code, so the NEXT new
    kind fails here on the commit that adds it.

    It matters most on this one: the IB page it was modelled on covers
    `ib_paper`, but the Bybit page covers `bybit_2`, which is REAL MONEY.
    """
    code = _code_names()
    missing = sorted(
        f"{k}_alert_state" for k in _cooldown_kinds()
        if f"{k}_alert_state" not in code
    )
    assert not missing, (
        f"alert latch(es) with no diag read surface: {missing}. Each SUPPRESSES "
        "an operator page and fails LOUD when unreadable, so from outside a "
        "permanently-broken latch is indistinguishable from a working one. Add "
        "the name to _LOG_FILES in diag.py (and to the CLAUDE.md log_file row, "
        "which test_every_allowlisted_log_file_is_documented pins)."
    )
