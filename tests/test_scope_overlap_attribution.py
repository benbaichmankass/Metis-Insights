"""The scope-overlap audit must not flag a session against ITSELF.

The board exists so two sessions do not collide. This audit compares a PR's
changed files against every declared path on it — and, for its first nine live
fires, **never established whose declaration it had matched**. Every one of
those nine was benign, and six of them were the reader's own.

That inverts the incentive the board exists to create: following the protocol
made the alarm louder, and the cheapest route to a quiet audit was to declare
nothing. `CLAUDE.md` names the endpoint — *"an alarm that fires constantly and
is routinely walked past is not background noise; the desensitized alarm is
ITSELF a P1 bug"*.

TWO INDEPENDENT CAUSES, and a fix for either alone leaves the other firing:

1. **A MENTIONED branch was read as the DECLARER's.** The collector took the
   first backticked ``claude/...`` token anywhere in the body. On
   ``issuecomment-5503070932`` — the manager's own, deliberately precise START —
   the only such token is *another session's branch, quoted in prose complaining
   that that session's declaration keeps matching it*. So the audit stamped an
   innocent third party's name onto the manager's own comment. Not a self-match:
   a **fabricated attribution**. The script had already learned this rule for
   PATHS (``Not touching:``) and never applied it to IDENTITY.

2. **A SESSION IS NOT A BRANCH.** A session posts one START and then opens PRs
   from short-lived branches (``claude/manager-state-0316``), so branch equality
   could never match its own declaration however precisely it declared.

3. **A declaration never goes stale.** On PR #10731 all seven declaring branches
   had already merged — one of them 90 seconds *before* its own START was posted,
   after which it went on generating overlap reports for fifteen hours.

BOTH DIRECTIONS ARE ASSERTED HERE. Suppressing a real overlap is the strictly
worse error, so every silencing path below is paired with a control proving the
audit still fires on a genuine cross-session collision.

Fixtures are the real board comments, quoted; ``_STALE_START`` is reconstructed
from the live audit output and is labelled as such in the script.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ci" / "check_scope_overlap.py"


def _load():
    spec = importlib.util.spec_from_file_location("_scope_overlap", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()


def test_self_test_passes():
    """The committed self-test is the guard CI actually runs."""
    assert G._self_test() == 0


# ── 1. identity is a claim, not a mention ────────────────────────────────────

def test_a_branch_named_in_prose_is_not_the_declarers_branch():
    ident = G.parse_identity(G._MANAGER_START)
    assert ident["sessions"] == ["session_011JWFxuYAaEQKCFCmG6gnHJ"]
    assert ident["branches"] == []
    # Positive control: the branch really is in the body, so the empty result
    # above is discrimination and not a failed parse.
    assert "claude/trading-system-workflow-design-1ln10f" in G._MANAGER_START


def test_the_old_collector_regex_would_have_taken_the_wrong_branch():
    """Pins the DEFECT, so a revert cannot pass this file quietly."""
    import re
    old = re.search(r"`(claude/[^`\s]+)`", G._MANAGER_START)
    assert old and old.group(1) == "claude/trading-system-workflow-design-1ln10f"


# ── 2. a session recognises itself across a different branch ─────────────────

def test_live_pr_10729_no_longer_reports_six_foreign_declarations():
    """The definitive regression case, from the live comment at 03:18:04Z."""
    v = G.assess(
        G._PR10729_FILES,
        [{"body": G._STALE_START, "url": "u1", "created_at": "2026-09-01T11:59:53Z"},
         {"body": G._MANAGER_START, "url": "u2", "created_at": "2026-09-02T01:42:42Z"}],
        my_branch=G._PR10729_BRANCH, my_pr=10729, my_body=G._PR10729_BODY,
        branch_states={"claude/trading-system-workflow-design-1ln10f": "merged"},
    )
    assert v["hits"] == []                 # deployed audit headlined six
    assert v["self_declared"] == 3         # counted, not hidden
    assert len(v["landed_hits"]) == 3      # the other session is NOT silenced
    md = G.render(v, pr=10729, changed_n=3)
    assert "LIVE session declared" not in md
    assert "your OWN declaration" in md


def test_suppression_is_visible_in_the_output():
    """A suppressor that hides its suppressions cannot be audited."""
    v = G.assess(G._PR10729_FILES,
                 [{"body": G._MANAGER_START, "url": "u", "created_at": "t"}],
                 my_branch=G._PR10729_BRANCH, my_pr=10729, my_body=G._PR10729_BODY)
    assert v["state"] == "no_overlap"
    assert v["self_declared"] == 3


# ── 3. THE OTHER DIRECTION: a real overlap still fires ───────────────────────

def test_a_genuine_cross_session_overlap_is_still_headlined():
    v = G.assess(["docs/claude/performance-review-backlog.json"],
                 [{"body": G._DRAIN3_START, "url": "u",
                   "created_at": "2026-09-02T01:22:16Z"}],
                 my_branch=G._PR10729_BRANCH, my_pr=10729, my_body=G._PR10729_BODY)
    assert v["state"] == "overlap"
    assert len(v["hits"]) == 1 and v["self_declared"] == 0
    assert "LIVE session declared" in G.render(v, pr=10729, changed_n=1)


def test_a_sub_sessions_identity_is_its_own_not_its_managers():
    """`- Session: `A` (child of manager `B`)` — taking B too would let the
    manager suppress this very declaration."""
    assert G.parse_identity(G._DRAIN3_START)["sessions"] == \
        ["session_01JXBmVC65hkkoSQ2LcV1ETY"]


def test_a_session_merely_named_in_our_pr_body_is_still_foreign():
    """PR #10729 names two sub-sessions it spawned. Harvesting bare ids from a
    PR body would make their STARTs read as our own."""
    assert G.pr_session_ids(G._PR10729_BODY) == ["session_011JWFxuYAaEQKCFCmG6gnHJ"]
    assert "session_01Au13tQ9BaLKsEU7youUomr" in G._PR10729_BODY   # control
    v = G.assess(["docs/claude/ml-review-backlog.json"],
                 [{"body": "▶️ START\n- Session: `session_01Au13tQ9BaLKsEU7youUomr`\n"
                           "Touching: `docs/claude/ml-review-backlog.json`\n",
                   "url": "u", "created_at": "t"}],
                 my_branch=G._PR10729_BRANCH, my_pr=10729, my_body=G._PR10729_BODY)
    assert len(v["hits"]) == 1 and v["self_declared"] == 0


def test_a_mentioned_pr_number_cannot_suppress_a_self_declared_branch():
    assert G.attribution(
        {"body": "▶️ START · branch `claude/other`\nSee PR #10590."},
        my_branch="claude/mine", my_pr=10590) == "other"


# ── 4. staleness fails toward reporting ──────────────────────────────────────

@pytest.mark.parametrize("states,want", [
    ({}, "active"),                                                   # unknown
    ({"claude/drain-perf-backlog-20260902": "open"}, "active"),
    ({"claude/drain-perf-backlog-20260902": "merged"}, "landed"),
    ({"claude/drain-perf-backlog-20260902": "closed"}, "landed"),
])
def test_branch_state_grades_liveness_and_unknown_stays_active(states, want):
    st = {"body": G._DRAIN3_START, "created_at": "2026-09-02T01:22:16Z"}
    assert G.liveness(st, branch_states=states)[0] == want


def test_a_done_retires_a_start_only_if_it_came_after():
    st = {"body": G._DRAIN3_START, "created_at": "2026-09-02T01:22:16Z"}
    done = {"body": "✅ DONE · branch `claude/drain-perf-backlog-20260902`",
            "created_at": "2026-09-02T02:30:00Z"}
    assert G.liveness(st, done_posts=[done])[0] == "landed"
    earlier = {**done, "created_at": "2026-09-02T00:00:00Z"}
    assert G.liveness(st, done_posts=[earlier])[0] == "active"


def test_the_word_done_in_prose_is_not_a_done_header():
    """A false START only over-reports; a false DONE SUPPRESSES. Hence the
    anchoring asymmetry between `_START_RE` and `_DONE_RE`."""
    assert G.is_done("✅ DONE · branch `claude/x`")
    assert not G.is_done("▶️ START · branch `claude/x`\n\nThe merge train is DONE.")
    assert not G.is_done("Status update\n\nWave 1 is DONE and wave 2 is spawned.")


def test_landed_evidence_is_named_never_implied():
    """`landed` says the BRANCH landed, not that the session ended."""
    st = {"body": G._DRAIN3_START, "created_at": "2026-09-02T01:22:16Z"}
    _, why = G.liveness(st, branch_states={"claude/drain-perf-backlog-20260902": "merged"})
    assert "claude/drain-perf-backlog-20260902" in why and "merged" in why


# ── 5. what must SURVIVE the change ──────────────────────────────────────────

def test_unattributable_is_preserved_and_still_reported():
    anon = "▶️ START — backlog-drain #2\n\nScope: `docs/claude/health-review-backlog.json`.\n"
    assert G.attribution({"body": anon}, my_branch="claude/x", my_pr=1) == "unattributable"
    v = G.assess(["docs/claude/health-review-backlog.json"],
                 [{"body": anon, "url": "u", "created_at": "t"}],
                 my_branch="claude/x", my_pr=1)
    assert v["state"] == "overlap"
    assert len(v["unattributed_hits"]) == 1 and v["hits"] == []
    md = G.render(v, pr=1, changed_n=1)
    assert "could not attribute" in md and "LIVE session declared" not in md


def test_lower_bound_caveat_survives_on_every_reporting_render():
    vague = [{"url": "u", "created_at": "t",
              "body": "▶️ START · branch `claude/other`\n**Touching:** "
                      "`docs/claude/OPEN-ITEMS.json` and `several other files`."}]
    for states in ({}, {"claude/other": "merged"}):
        v = G.assess(["docs/claude/OPEN-ITEMS.json"], vague,
                     my_branch="claude/mine", branch_states=states)
        assert "LOWER BOUND" in G.render(v, pr=1, changed_n=1)


def test_could_not_check_is_never_a_clean_pass():
    for v in (G.assess([], [{"body": "▶️ START", "url": "u", "created_at": "t"}],
                       my_branch="claude/mine"),
              G.assess(["a.py"], [], my_branch="claude/mine")):
        assert v["state"] == "could_not_check"
        assert "not a clean result" in G.render(v, pr=1, changed_n=0)


def test_the_negation_section_rule_is_untouched():
    body = ("Touching: `scripts/ci/run_guards.py`\n\n"
            "Not touching: `docs/claude/INDEX.md`\n")
    dec, exc, _ = G.parse_declared_paths(body)
    assert "docs/claude/INDEX.md" not in dec and "docs/claude/INDEX.md" in exc


def test_state_vocabularies_are_exactly_these():
    assert set(G.STATES) == {"overlap", "no_overlap", "could_not_check"}
    assert set(G.ATTRIBUTIONS) == {"mine", "other_active", "other_landed",
                                   "unattributable"}


def test_a_branch_without_the_claude_prefix_is_now_read():
    """The old regex required `claude/`, so this real START (issuecomment-
    5503056365) was filed `unattributable` on live PR #10731."""
    assert G.parse_identity(G._PING_START)["branches"] == \
        ["diag-pending-pings-delivered-read-surface"]


def test_the_quoted_footer_residual_is_pinned_not_latent():
    """A PR body quoting ANOTHER session's footer URL adopts that identity.

    This is the one declared residual of the fix (see `pr_session_ids`). It is
    asserted rather than left latent, so a future change that closes it has to
    change this test deliberately — and so nobody rediscovers it as a surprise.
    """
    quoted = ("Continuing the handoff from\n"
              "https://claude.ai/code/session_01JXBmVC65hkkoSQ2LcV1ETY\n\n"
              "_Generated by [Claude Code](https://claude.ai/code/session_MINE00)_")
    assert "session_01JXBmVC65hkkoSQ2LcV1ETY" in G.pr_session_ids(quoted)
    # Bounded: it can only silence a session that ALSO declared an overlapping
    # path in the same window, and the audit gates nothing.
    v = G.assess(["docs/claude/performance-review-backlog.json"],
                 [{"body": G._DRAIN3_START, "url": "u", "created_at": "t"}],
                 my_branch="claude/x", my_pr=1, my_body=quoted)
    assert v["self_declared"] == 1 and v["hits"] == []
