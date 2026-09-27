"""Tests for the allow-degraded owner+expiry guard (BL-20260730-ALLOW-DEGRADED-NEEDS-EXPIRY).

WARNING: UPDATED 2026-09-22 (E45) FOR THE RE-POINT, AND CI CAUGHT THAT I HAD NOT.
The guard was UNSATISFIABLE -- `check_backlog_refs.REF` does not match a `PI-`
id, so 0 of 753 open rows in the live intake were citable -- and re-pointing it
at `docs/claude/work/PIPELINE.jsonl` changed two things these tests assert on:

  * `scan()` now resolves against that register, so a fixture root without one
    raises `CouldNotLook`. That is DELIBERATE and must not be softened: reading
    a missing register as an EMPTY universe would turn every correctly-owned
    annotation in a tree into a dangling reference at once. The fixture plants
    the register instead.
  * the "no id" message widened from "names no backlog id" to "names no
    tracking id that resolves", because the accepted id space is no longer
    backlog-only.

WARNING: AND THE OLD FIXTURE MODELLED A WORLD THAT NO LONGER EXISTS. It seeded
`docs/claude/health-review-backlog.json` -- archived by the 2026-09-21 reset --
so the `BL-` id resolved in the fixture and could not resolve in the real repo.
That file is kept below ON PURPOSE, as the control that the legacy `filed_ids`
path still works for a register that IS present, and the pipeline row is added
beside it as the live one.
"""
from __future__ import annotations

import json

from scripts.ops.check_allow_degraded import marker_problems, scan

_FILED = {"BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0"}
_TODAY = "2026-08-01"


# ------------------------------------------------------------- marker_problems
def test_well_formed_annotation_has_no_problems():
    payload = "BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 until:2026-11-01 fetch may fail"
    assert marker_problems(payload, _FILED, _TODAY) == []


def test_naming_no_id_at_all_is_flagged():
    probs = marker_problems("until:2026-11-01 just because", _FILED, _TODAY)
    assert any("names no tracking id" in p for p in probs)
    # WARNING: AND IT IS NOT THE *UNRESOLVED* FINDING. Those two have different
    # fixes -- file a row vs. correct a typo -- and collapsing them sent a
    # reader hunting for a register when the id was simply misspelled.
    assert not any("resolve to NOTHING" in p for p in probs)


def test_unresolvable_backlog_id_is_flagged():
    probs = marker_problems("BL-20260101-NEVER-FILED until:2026-11-01", _FILED, _TODAY)
    assert any("resolve to NOTHING" in p for p in probs)


def test_missing_until_is_flagged():
    probs = marker_problems("BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 no expiry", _FILED, _TODAY)
    assert any("no `until:" in p for p in probs)


def test_expired_until_is_flagged():
    payload = "BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 until:2026-07-01"
    probs = marker_problems(payload, _FILED, "2026-08-01")
    assert any("EXPIRED on 2026-07-01" in p for p in probs)


def test_unexpired_until_on_the_boundary_is_ok():
    # today == until is NOT past it (strict `today > until`), so it still passes.
    payload = "BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 until:2026-08-01"
    assert marker_problems(payload, _FILED, "2026-08-01") == []


# ------------------------------------------------------------------------ scan
def _pipeline_row(rid, state, **extra):
    row = {"id": rid, "state": state, "what": "a degraded path",
           "origin": {"kind": "session", "ref": "s", "rerun": "x"},
           "due_when": {"kind": "date", "due_date": "2026-11-01"},
           "next_action": "check_observation"}
    row.update(extra)
    return row


def _mk_repo(tmp_path, workflow_body):
    (tmp_path / "docs" / "claude").mkdir(parents=True)
    (tmp_path / "docs" / "claude" / "health-review-backlog.json").write_text(
        json.dumps({"items": [{"id": "BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0"}]}),
        encoding="utf-8")
    # The LIVE register the guard resolves against since the E45 re-point. Its
    # absence is `CouldNotLook`, never an empty universe, so every fixture root
    # must carry one -- that refusal is the property, not an inconvenience.
    #
    # ⚠️ RE-POINTED 2026-09-24 (E64): a DIRECTORY, one file per record, not a
    # single flat file — see `scripts/ops/pipeline.py`'s module docstring.
    store = tmp_path / "docs" / "claude" / "work" / "pipeline"
    store.mkdir(parents=True)
    (store / "0001.json").write_text(
        json.dumps(_pipeline_row("PI-20260101-LIVE-ROW", "queued")),
        encoding="utf-8")
    (store / "0002.json").write_text(
        json.dumps(_pipeline_row("PI-20260101-CLOSED-ROW", "done",
                                  terminal_reason="answered")),
        encoding="utf-8")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "x.yml").write_text(workflow_body, encoding="utf-8")
    return tmp_path


def test_scan_accepts_a_well_formed_marker(tmp_path):
    repo = _mk_repo(tmp_path,
                    'run: fetch.py || echo degraded  '
                    '# allow-degraded: BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 until:2026-11-01\n')
    findings, n_valid = scan(repo, _TODAY)
    assert findings == []
    assert n_valid == 1


def test_scan_flags_a_marker_missing_until(tmp_path):
    repo = _mk_repo(tmp_path,
                    'run: fetch.py || true  '
                    '# allow-degraded: BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0\n')
    findings, _ = scan(repo, _TODAY)
    assert len(findings) == 1
    assert any("no `until:" in p for p in findings[0]["problems"])


def test_scan_skips_placeholder_payloads(tmp_path):
    # A syntax reference `# allow-degraded: <reason>` (docs) must NOT be flagged.
    repo = _mk_repo(tmp_path,
                    '# An exception carries `# allow-degraded: <reason>` here\n')
    findings, n_valid = scan(repo, _TODAY)
    assert findings == [] and n_valid == 0


def test_scan_ignores_non_comment_form(tmp_path):
    # A bare `allow-degraded:` inside a string literal is not the comment marker.
    repo = _mk_repo(tmp_path, "run: python -c \"'allow-degraded:' not in buf\"\n")
    findings, n_valid = scan(repo, _TODAY)
    assert findings == [] and n_valid == 0


# ---------------------------------------------- the RE-POINTED subject, E45
# WARNING: THE EXISTING `scan` TESTS ALL CITE A `BL-` ID, WHICH IS THE LEGACY
# PATH. Every one of them passed while the guard was unsatisfiable against the
# LIVE intake, because none of them ever cited a `PI-` id. These two close that
# hole at the test level, and they are the pair: the citation that could not be
# written before must PASS, and a bad one must FAIL as UNRESOLVED.

def test_scan_accepts_a_live_pipeline_row_id(tmp_path):
    """The annotation that was impossible to write before the re-point."""
    repo = _mk_repo(tmp_path,
                    'run: fetch.py || echo degraded  '
                    '# allow-degraded: PI-20260101-LIVE-ROW until:2026-11-01\n')
    findings, n_valid = scan(repo, _TODAY)
    assert findings == [], findings
    assert n_valid == 1


def test_scan_refuses_a_pipeline_id_that_is_not_open(tmp_path):
    """A `done` row cannot force the re-review the annotation exists to force,
    so citing one is refused for the same reason an expired `until:` is."""
    repo = _mk_repo(tmp_path,
                    'run: fetch.py || echo degraded  '
                    '# allow-degraded: PI-20260101-CLOSED-ROW until:2026-11-01\n')
    findings, _ = scan(repo, _TODAY)
    assert len(findings) == 1, findings
    assert any("resolve to NOTHING OPEN" in p for p in findings[0]["problems"])


def test_scan_refuses_a_pipeline_id_that_does_not_exist(tmp_path):
    repo = _mk_repo(tmp_path,
                    'run: fetch.py || echo degraded  '
                    '# allow-degraded: PI-20260101-NEVER-FILED until:2026-11-01\n')
    findings, _ = scan(repo, _TODAY)
    assert len(findings) == 1, findings
    probs = findings[0]["problems"]
    assert any("resolve to NOTHING OPEN" in p for p in probs)
    # ...as UNRESOLVED, not as "you named nothing" -- the distinction the
    # re-point had to add a local id-shape pattern to preserve.
    assert not any("names no tracking id" in p for p in probs)


def test_a_root_without_the_register_is_COULD_NOT_LOOK(tmp_path):
    """WARNING: EXIT 2, NOT AN EMPTY UNIVERSE. Reading a missing register as
    "nothing is filed" would turn every correctly-owned annotation in the tree
    into a dangling reference at once. This is the property the four pre-existing
    `scan` tests tripped over when the guard was re-pointed, and it is deliberate."""
    import pytest

    from scripts.ops.check_allow_degraded import CouldNotLook

    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "x.yml").write_text("run: true\n",
                                                              encoding="utf-8")
    with pytest.raises(CouldNotLook):
        scan(tmp_path, _TODAY)
