"""The registry must refuse a spawn whose repository nobody stated or observed.

`MI-207`. `scripts/ops/session_registry.py` had no way to express which
repository a child was handed, so `register` AND `confirm` both SUCCEEDED on a
spawn that attached the wrong OWNER's repo — sixteen instances across five days.

⚠️ THIS CLASS IS ALREADY FILED THREE TIMES and the third row says in terms that
a fourth row is not the remedy:
`BL-20260906-A-SUBSESSION-SPAWNED-WITHOUT-SOURCE-URL-GETS-NO-REPO-AND-ITS-WORK-DIES-UNPUSHED`,
`BL-20260908-SPAWNING-WITHOUT-SOURCE-URL-CAN-ATTACH-THE-WRONG-REPO-NOT-MERELY-NO-REPO-AND-THE-2026-09-06-ROW-DID-NOT-PREVENT-IT`,
`BL-20260908-PUSH-DENIED-AND-WRONG-REPO-ARE-ONE-DEFECT-3-OF-3-AND-A-ONE-CALL-PREDICTOR-EXISTS`.
So this file exists to hold a REFUSAL in place, not to describe one.

⚠️ AND A PASSING TEST HERE CLEARS NOTHING ON THOSE ROWS. Their resolution
criteria say so explicitly — a harness cannot reach `create_session`. What
clears them is a refusal observed on a real attempt; this only stops the
refusal from being deleted later.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/ops/session_registry.py"


def _mod():
    spec = importlib.util.spec_from_file_location("session_registry", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("url,want", [
    ("https://github.com/benbaichmankass/Metis-Insights", "benbaichmankass/metis-insights"),
    ("https://github.com/benbaichmankass/metis-insights.git", "benbaichmankass/metis-insights"),
    ("git@github.com:the-lizardking/ict-trading-bot.git", "the-lizardking/ict-trading-bot"),
    ("benbaichmankass/metis-insights", "benbaichmankass/metis-insights"),
    ("", None),
    ("nonsense", None),
    (None, None),
])
def test_normalize_repo(url, want):
    assert _mod().normalize_repo(url) == want


def test_case_is_folded_because_it_is_not_identity_here():
    """This checkout's `origin` is `benbaichmankass/metis-insights` while
    GitHub's canonical spelling is `benbaichmankass/Metis-Insights`. A
    case-sensitive compare would refuse the CORRECT answer."""
    m = _mod()
    assert m.normalize_repo("https://github.com/benbaichmankass/Metis-Insights") == \
           m.normalize_repo("https://github.com/benbaichmankass/metis-insights")


def test_the_four_states_are_not_collapsed():
    m = _mod()
    origin = "benbaichmankass/metis-insights"
    assert m.grade_source_url(None, origin)[0] == "absent"
    assert m.grade_source_url("   ", origin)[0] == "absent"
    assert m.grade_source_url("garbage", origin)[0] == "unreadable"
    assert m.grade_source_url("the-lizardking/ict-trading-bot", origin)[0] == "mismatch"
    assert m.grade_source_url("benbaichmankass/Metis-Insights", origin)[0] == "match"


def test_an_unreadable_origin_is_unknown_never_match():
    """`we could not look` is not `it matches`. Collapsing them would let a
    checkout with no readable `origin` silently pass every spawn."""
    m = _mod()
    state, _ = m.grade_source_url("benbaichmankass/metis-insights", None)
    assert state == "unknown"


def test_the_measured_contamination_value_is_refused():
    """`the-lizardking/ict-trading-bot` is a REAL repo — the pre-rename remote,
    still named in the operator's saved preferences — so the clone SUCCEEDS and
    the contamination is silent. It is the exact value that was observed."""
    m = _mod()
    state, detail = m.grade_source_url(
        "https://github.com/the-lizardking/ict-trading-bot",
        "benbaichmankass/metis-insights")
    assert state == "mismatch"
    assert "the-lizardking/ict-trading-bot" in detail


def test_register_cannot_be_called_without_a_source_url():
    """Keyword-only and undefaulted ON PURPOSE: a caller that never thought
    about the repository must fail at the call rather than write a row that
    silently means 'nobody stated one'."""
    m = _mod()
    with pytest.raises(TypeError):
        m.register(Path("/nonexistent"), title="t", why="", spawned_by="s")


def test_the_spawn_prompt_names_the_repo_and_tells_the_child_to_stop():
    m = _mod()
    prompt = m.spawn_prompt("t", "w", "ref",
                            source_repo="benbaichmankass/metis-insights")
    assert "benbaichmankass/metis-insights" in prompt
    assert "git remote -v" in prompt, "the child needs the assertion, not just the name"
    assert "STOP" in prompt or "stop" in prompt
    # The prompt must put it FIRST — a check after the work is done is a
    # post-mortem, not a prevention.
    assert prompt.index("CONFIRM YOUR REPOSITORY") < prompt.index("## Your unit")


def test_an_unstated_repo_still_produces_a_prompt_that_refuses_to_pretend():
    m = _mod()
    prompt = m.spawn_prompt("t", "w", "ref", source_repo=None)
    assert "UNSTATED" in prompt


def _cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=REPO)


def test_confirm_requires_an_observation():
    """`register` records an INTENTION; `confirm` is the only command that runs
    after the platform has answered, so it is the only place the delivered repo
    is observable."""
    r = _cli("confirm", "--registry-key", "k", "--session-id", "s")
    assert r.returncode != 0
    assert "--observed-source-url" in (r.stderr + r.stdout)


def test_no_repo_and_wrong_repo_are_different_confirm_refusals():
    """They need different remedies: no repo fails loudly within ~90s (nothing
    to compute), a wrong repo does not fail at all."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "you observed NO `sources`" in src
    assert "will run normally and report confident findings" in src


def test_there_is_no_force_flag_on_the_repo_gate():
    """A gate with a flag beside it is a gate that gets flagged past — the same
    reasoning the spawn-priority gate above it records."""
    src = SCRIPT.read_text(encoding="utf-8")
    for bypass in ("--force-source-url", "--skip-source-check",
                   "--allow-foreign-source", "--no-source-check"):
        assert bypass not in src, f"{bypass} defeats the refusal"


def test_the_self_test_still_passes():
    r = _cli("--self-test")
    assert r.returncode == 0, r.stdout + r.stderr
