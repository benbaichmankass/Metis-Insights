"""A guard whose verdict is a function of the CLOCK must not fail a diff for it.

`render_session_brief` calls `datetime.now()`, and `due_items` flips a
`monitoring` row to DUE once `check_every_days` elapses. So the block COMMITTED
in `CLAUDE.md` goes stale at a UTC-midnight boundary **with no commit
anywhere** — and a whole-tree `--check` then reds every open PR until a human
re-renders it. A branch cut inside that window is stranded permanently, because
nothing re-runs its checks.

Measured 2026-08-31, which is why this file exists rather than a comment:
`OI-20260826-MHG-OVER-COVER-MECHANISM-UNVERIFIED` (`verified_at 2026-08-29`,
`check_every_days 2`) crossed into DUE at 00:00Z. Two automation PRs opened at
00:47Z and 01:22Z (#10538, #10539) both failed `session-brief-guard` — on
content they never touched, with `pytest-run` green on both — and sat stranded
(`BL-20260830-A-TRANSIENT-RED-BASE-PERMANENTLY-STRANDS-AN-AUTOMERGE-BRANCH`).

The fix renders BOTH sides with the SAME date, so the time term is identical on
each and cancels. That is the property asserted end-to-end below: a diff that
changes nothing cannot be failed by the clock, while a diff that changes the
registers without re-rendering still IS.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ops" / "render_session_brief.py"


def _load():
    spec = importlib.util.spec_from_file_location("_rsb", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

A, B = "brief-A", "brief-B"


# --- the pure decision --------------------------------------------------------

@pytest.mark.parametrize(
    "kw,want",
    [
        (dict(want_head=A, have_head=A, base_readable=True, want_base=A, have_base=A),
         "clean"),
        # The clock case: the brief IS stale, identically on both sides.
        (dict(want_head=B, have_head=A, base_readable=True, want_base=B, have_base=A),
         "inherited"),
        (dict(want_head=B, have_head=A, base_readable=True, want_base=A, have_base=A),
         "introduced_registers_changed"),
        (dict(want_head=A, have_head=B, base_readable=True, want_base=A, have_base=A),
         "introduced_block_edited"),
        (dict(want_head=A, have_head=None, base_readable=True, want_base=A, have_base=A),
         "no_block"),
        (dict(want_head=B, have_head=A, base_readable=False), "base_unreadable"),
    ],
)
def test_the_verdict_separates_inherited_from_introduced(kw, want):
    assert G.check_verdict(**kw) == want


def test_every_declared_verdict_is_reachable():
    """The denominator — a state nothing can produce is a dead claim."""
    produced = {
        G.check_verdict(want_head=A, have_head=A, base_readable=True, want_base=A, have_base=A),
        G.check_verdict(want_head=B, have_head=A, base_readable=True, want_base=B, have_base=A),
        G.check_verdict(want_head=B, have_head=A, base_readable=True, want_base=A, have_base=A),
        G.check_verdict(want_head=A, have_head=B, base_readable=True, want_base=A, have_base=A),
        G.check_verdict(want_head=A, have_head=None, base_readable=True, want_base=A, have_base=A),
        G.check_verdict(want_head=B, have_head=A, base_readable=False),
    }
    assert produced == set(G.VERDICTS)


def test_an_unreadable_base_fails_closed():
    """`we could not look` must never become `it was already broken`.

    Failing open here would silently disable the guard on a git glitch, which
    is strictly worse than the stranding it replaces.
    """
    assert G.check_verdict(want_head=B, have_head=A, base_readable=False) in G._FAILING


# --- end to end, against a real git repo -------------------------------------

def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _registers(due: bool, extra: bool = False):
    """A monitoring row that is (or is not) past its cadence today."""
    items = [{
        "id": "OI-TEST-ROW",
        "kind": "monitoring",
        "summary": "s",
        "clears_when": "c",
        "observation": "o",
        "verified_at": "2020-01-01" if due else "2999-01-01",
        "check_every_days": 2,
    }]
    if extra:
        items.append(dict(items[0], id="OI-TEST-ROW-2"))
    return {"items": items}, {"classes": []}


def _make_repo(tmp_path, base_due, head_due, head_extra_row):
    """A repo whose base and head differ only as the arguments say."""
    r = tmp_path / "repo"
    (r / "docs" / "claude").mkdir(parents=True)
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")

    oi, rl = _registers(due=base_due)
    (r / "docs/claude/OPEN-ITEMS.json").write_text(json.dumps(oi))
    (r / "docs/claude/RECURRENCE-LEDGER.json").write_text(json.dumps(rl))
    # A CLAUDE.md whose block was rendered when NOTHING was due.
    fresh_oi, fresh_rl = _registers(due=False)
    # ⚠️ PIN ALL THREE REGISTERS, not two. `render()` loads any register it is
    # not handed from disk, CWD-RELATIVELY -- so an unpinned one is read from
    # whatever repo the TEST PROCESS is running in, while `--check` later reads
    # it from the FIXTURE repo. The two disagree and the head grades stale.
    # This bit when A3 added CYCLE-PRIORITY.json as a third register: the two
    # older ones were already pinned here and the new one was not, so the block
    # was built carrying the REAL repo's cycle priority and checked against a
    # fixture that has no priority file at all.
    block = G.render(open_items=fresh_oi, recurrence=fresh_rl, priority={})
    (r / "CLAUDE.md").write_text(f"# doc\n\n{block}\n\ntail\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _git(r, "branch", "-f", "base_ref")

    oi2, rl2 = _registers(due=head_due, extra=head_extra_row)
    (r / "docs/claude/OPEN-ITEMS.json").write_text(json.dumps(oi2))
    (r / "docs/claude/RECURRENCE-LEDGER.json").write_text(json.dumps(rl2))
    (r / "unrelated.txt").write_text("an automation data commit\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "head")
    return r


def _check(repo, base="base_ref"):
    return subprocess.run(
        ["python3", str(SCRIPT), "--check", "--base", base],
        cwd=repo, capture_output=True, text=True,
    )


def test_the_clock_alone_cannot_fail_a_diff(tmp_path):
    """THE REGRESSION THIS FILE EXISTS FOR.

    The row is past its cadence on BOTH sides and the committed block predates
    it, so the brief is genuinely stale — but the head commit touched only an
    unrelated file. That must be reported, not failed.
    """
    repo = _make_repo(tmp_path, base_due=True, head_due=True, head_extra_row=False)
    out = _check(repo)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "verdict=inherited" in out.stdout
    # And it must not read as a clean bill of health.
    assert "STALE" in out.stdout


def test_the_same_repo_still_fails_the_strict_whole_tree_check(tmp_path):
    """Positive control: without `--base` the old behaviour is unchanged.

    Without this, a passing diff-scoped run could mean the check stopped
    detecting staleness at all rather than correctly attributing it.
    """
    repo = _make_repo(tmp_path, base_due=True, head_due=True, head_extra_row=False)
    out = subprocess.run(["python3", str(SCRIPT), "--check"],
                         cwd=repo, capture_output=True, text=True)
    assert out.returncode == 1, out.stdout + out.stderr


def test_a_diff_that_changes_the_registers_without_re_rendering_still_fails(tmp_path):
    """The case the guard must keep catching — including over a stale base.

    A clock-stale base must not become a loophole for a real register change.
    """
    repo = _make_repo(tmp_path, base_due=True, head_due=True, head_extra_row=True)
    out = _check(repo)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "verdict=introduced_registers_changed" in out.stdout


def test_a_clean_head_passes(tmp_path):
    repo = _make_repo(tmp_path, base_due=False, head_due=False, head_extra_row=False)
    out = _check(repo)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "matches the registers" in out.stdout


def test_an_unresolvable_base_ref_fails_closed_end_to_end(tmp_path):
    repo = _make_repo(tmp_path, base_due=True, head_due=True, head_extra_row=False)
    out = _check(repo, base="no/such/ref")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "verdict" not in out.stdout or "base_unreadable" in out.stdout
    assert "could" in out.stdout.lower()
