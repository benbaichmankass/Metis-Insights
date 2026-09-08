"""The un-carried-spec probe must stay calibrated, and its states must stay uncollapsed.

The probe's first cut passed a naive check and still graded the KNOWN-STRANDED artifact as
carried — the dangerous direction, since it under-reports the pile the probe exists to
measure. These tests pin the properties that failure violated.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ops"))

import uncarried_specs as us  # noqa: E402


def test_positive_control_is_found_carried():
    """A probe whose silence we trust must first be shown to find a carried spec."""
    ok, problems = us.self_test()
    assert ok, "positive control failed: " + "; ".join(problems)


def test_dormant_object_does_not_carry():
    """The exact defect the control caught: a `dormant` object names an artifact but,
    by the store's own semantics, nothing is scheduled to pick it up."""
    c = {"active": [], "queued": [], "dormant": ["objects/BL-x.yaml[dormant]"], "mentioned": []}
    assert us.state_for(c) == "dormant_only"
    assert us.state_for(c) not in us.CARRIED_STATES


def test_mention_is_not_a_carrier():
    """The motivating incident had exactly one incidental mention and sat 14 days."""
    c = {"active": [], "queued": [], "dormant": [], "mentioned": ["docs/claude/health-review-backlog.json"]}
    assert us.state_for(c) == "mentioned"
    assert us.state_for(c) not in us.CARRIED_STATES


def test_states_are_not_collapsed():
    """Five distinct states; none may alias another."""
    seen = {
        us.state_for({"active": ["a"], "queued": [], "dormant": [], "mentioned": []}),
        us.state_for({"active": [], "queued": ["q"], "dormant": [], "mentioned": []}),
        us.state_for({"active": [], "queued": [], "dormant": ["d"], "mentioned": []}),
        us.state_for({"active": [], "queued": [], "dormant": [], "mentioned": ["m"]}),
        us.state_for({"active": [], "queued": [], "dormant": [], "mentioned": []}),
    }
    assert seen == {"active", "queued", "dormant_only", "mentioned", "uncarried"}


def test_unknown_lifecycle_is_not_silently_active():
    """An object with no readable lifecycle must not be credited as a carrier."""
    assert us.object_lifecycle("id: X\ntitle: y\n") == "unknown"
    assert "unknown" not in us.ACTIVE_LIFECYCLES
    assert "unknown" not in us.QUEUED_LIFECYCLES


def test_classifier_catches_both_tiers():
    """Tier A is the filename; tier B is the artifact declaring its own work unbuilt."""
    assert us.TEXT_DIRECTIVE.search("Paste this whole file as the opening message")
    assert us.TEXT_DIRECTIVE.search("the runner has never been built")
    assert us.TEXT_DIRECTIVE.search("this lever is not yet shipped")
    # a bare recommendation is tier C, deliberately NOT a spec
    assert not us.TEXT_DIRECTIVE.search("our recommendation is to widen the allowlist")
    assert any(s in "M20-exit-head-PROGRAM.md".upper() for s in us.FILENAME_SIGNALS)


def test_population_exclusions_all_carry_a_reason():
    """An unexplained exclusion is how a silence list forms."""
    assert us.POPULATION_EXCLUDE
    for name, reason in us.POPULATION_EXCLUDE.items():
        assert reason.strip(), f"{name} excluded with no reason"


def test_probe_writes_nothing():
    """It is a measurement. It must not mutate a register."""
    src = (pathlib.Path(us.__file__)).read_text()
    for forbidden in ("write_text(", "open(", "os.remove", "shutil."):
        if forbidden == "open(":
            continue  # read_text is used; no bare open() writes
        assert forbidden not in src, f"probe appears to write: {forbidden}"
