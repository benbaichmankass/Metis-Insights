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
