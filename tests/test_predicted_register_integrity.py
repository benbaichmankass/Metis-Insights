"""A CLEAN MERGE IS NOT A CORRECT ONE.

These cover the gap between `github_equivalent_merge.predict`'s question ("will
GitHub call this conflicted?") and the one that actually reddened `main` twice
on 2026-09-17 ("will the result be correct?"). The answer to the first was
`clean` on both occasions.

⚠️ **THE FIXTURE IS THE POINT, NOT DECORATION.** `test_the_fallback_is_a_plain
_text_merge_and_NOT_union` builds the duplication from scratch and, in the same
breath, REFUTES the mechanism the backlog row was originally filed with. See its
docstring.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

import github_equivalent_merge as gem  # noqa: E402
import predicted_register_integrity as pri  # noqa: E402


# ── fixture helpers ──────────────────────────────────────────────────────────

def _env() -> dict:
    return dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def _git(args, cwd, check=True):
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                       text=True, env=_env())
    if check and p.returncode:
        raise AssertionError(f"git {args} failed: {p.stderr}")
    return p


def _reg(rows) -> str:
    return json.dumps({"items": [{"id": r, "title": r} for r in rows]}, indent=2) + "\n"


def _build(tmp: str, feat_rows, main_rows, base_rows):
    """A repo whose register is BOUND to the row-aware driver but has none."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(repo, "docs", "claude"))
    path = os.path.join(repo, "docs", "claude", "health-review-backlog.json")
    _git(["init", "-q", "-b", "main"], repo)
    Path(repo, ".gitattributes").write_text(
        "docs/claude/health-review-backlog.json merge=jsonregister\n")
    Path(path).write_text(_reg(base_rows))
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "base"], repo)
    _git(["checkout", "-q", "-b", "feat"], repo)
    Path(path).write_text(_reg(feat_rows))
    _git(["commit", "-qam", "feat"], repo)
    feat = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    _git(["checkout", "-q", "main"], repo)
    Path(path).write_text(_reg(main_rows))
    _git(["commit", "-qam", "main"], repo)
    main = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    return repo, path, main, feat


BASE20 = [f"R{i:02d}" for i in range(1, 21)]


# ── the mechanism, and the correction to how it was first filed ──────────────

def test_the_fallback_is_a_plain_text_merge_and_NOT_union():
    """⚠️ CORRECTS `BL-20260917-GITHUBS-SQUASH-MERGE-...`, WHICH SAYS `union`.

    It does not fall back to union. A named-but-undefined merge driver falls
    back to git's ORDINARY three-way text merge, and the discriminator is
    decisive: union NEVER conflicts, by construction. Two arms of one fixture,
    identical but for WHERE the shared row is inserted:

      separated insertions -> merges clean, keeps both -> DUPLICATE
      adjacent insertions  -> CONFLICTS

    The second arm is impossible under union, so this is not a wording quibble.
    It also explains why the class is intermittent rather than constant, which
    a union account cannot: duplication needs the two copies far enough apart to
    merge cleanly, and `.gitattributes` uses real `merge=union` deliberately and
    separately for `pending-pings.jsonl`.
    """
    tmp = tempfile.mkdtemp()
    try:
        # Arm A — separated: X near the top on one side, at the end on the other.
        repo, path, main, feat = _build(
            tmp, feat_rows=BASE20[:2] + ["X"] + BASE20[2:],
            main_rows=BASE20 + ["X"], base_rows=BASE20)
        _git(["checkout", "-q", "--detach", main], repo)
        merged = _git(["merge", "--no-commit", "--no-ff", feat], repo, check=False)
        assert merged.returncode == 0, "the separated arm must merge CLEAN"
        ids = [r["id"] for r in json.loads(Path(path).read_text())["items"]]
        assert len(ids) - len(set(ids)) == 1, f"expected one duplicate, got {ids}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp2 = tempfile.mkdtemp()
    try:
        # Arm B — adjacent: both sides insert at the SAME anchor.
        repo, path, main, feat = _build(
            tmp2, feat_rows=["A", "B", "C", "X"],
            main_rows=["A", "B", "X"], base_rows=["A", "B"])
        _git(["checkout", "-q", "--detach", main], repo)
        merged = _git(["merge", "--no-commit", "--no-ff", feat], repo, check=False)
        assert merged.returncode != 0, (
            "the adjacent arm must CONFLICT — union never conflicts, so a clean "
            "result here would mean the fallback really is union")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)


def test_predict_reports_CLEAN_for_the_very_merge_that_duplicates():
    """The defect, stated as a test: the conflict question answers `clean` here."""
    tmp = tempfile.mkdtemp()
    try:
        repo, _p, main, feat = _build(
            tmp, BASE20[:2] + ["X"] + BASE20[2:], BASE20 + ["X"], BASE20)
        res = gem.predict(repo, main, feat)
        assert res["state"] == gem.CLEAN
        assert res["inspection"] is None, "no hook passed -> nothing inspected"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_inspect_hook_catches_what_the_conflict_question_cannot():
    tmp = tempfile.mkdtemp()
    try:
        repo, _p, main, feat = _build(
            tmp, BASE20[:2] + ["X"] + BASE20[2:], BASE20 + ["X"], BASE20)
        res = gem.predict(repo, main, feat, inspect=pri.inspect_tree)
        assert res["state"] == gem.CLEAN, "the MERGE verdict must stay clean"
        assert res["inspection"]["state"] == pri.WOULD_DUPLICATE
        assert any("'X'" in f for f in res["inspection"]["findings"])
        assert gem.overall_state(res) == gem.WOULD_CONFLICT
        assert gem.EXIT[gem.overall_state(res)] == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_merge_that_lands_no_duplicate_still_PASSES():
    """The control against 'refuses everything'. Without it, a check that always
    reported a duplicate would satisfy every other test here."""
    tmp = tempfile.mkdtemp()
    try:
        repo, _p, main, feat = _build(
            tmp, BASE20[:2] + ["Y"] + BASE20[2:], BASE20 + ["X"], BASE20)
        res = gem.predict(repo, main, feat, inspect=pri.inspect_tree)
        assert res["state"] == gem.CLEAN
        assert res["inspection"]["state"] == pri.CLEAN
        assert gem.overall_state(res) == gem.CLEAN
        assert gem.EXIT[gem.overall_state(res)] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_conflicted_merge_is_never_inspected():
    """A conflicted merge has no result to grade, so the hook must not run."""
    tmp = tempfile.mkdtemp()
    try:
        repo, _p, main, feat = _build(
            tmp, ["A", "B", "C", "X"], ["A", "B", "X"], ["A", "B"])
        calls = []
        res = gem.predict(repo, main, feat, inspect=lambda w: calls.append(w))
        assert res["state"] == gem.WOULD_CONFLICT
        assert calls == [], "the hook ran on a tree that does not exist"
        assert res["inspection"] is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_hook_that_raises_is_could_not_look_and_never_a_pass():
    tmp = tempfile.mkdtemp()
    try:
        repo, _p, main, feat = _build(
            tmp, BASE20[:2] + ["Y"] + BASE20[2:], BASE20 + ["X"], BASE20)
        def boom(_work):
            raise RuntimeError("disk gone")
        res = gem.predict(repo, main, feat, inspect=boom)
        assert res["state"] == gem.CLEAN
        assert res["inspection"]["state"] == gem.COULD_NOT_LOOK
        assert gem.overall_state(res) == gem.COULD_NOT_LOOK
        assert gem.EXIT[gem.overall_state(res)] == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── inspect_tree's own states ────────────────────────────────────────────────

def test_zero_registers_read_is_could_not_look_not_clean():
    """An empty denominator is not a clean result — the sub-class C defect."""
    tmp = tempfile.mkdtemp()
    try:
        r = pri.inspect_tree(tmp)
        assert r["state"] == pri.COULD_NOT_LOOK
        assert r["registers_read"] == 0
        assert "not a clean result" in r["reason"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_path_that_is_not_a_directory_is_could_not_look():
    assert pri.inspect_tree("/definitely/not/here")["state"] == pri.COULD_NOT_LOOK


def test_an_unparseable_register_is_counted_not_silently_clean():
    tmp = tempfile.mkdtemp()
    try:
        d = Path(tmp, "docs", "claude")
        d.mkdir(parents=True)
        (d / "health-review-backlog.json").write_text("{ not json")
        r = pri.inspect_tree(tmp)
        assert r["state"] == pri.COULD_NOT_LOOK
        assert any("health-review-backlog" in u for u in r["registers_unreadable"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_real_repo_grades_clean():
    """A positive control: the probe can return CLEAN, so a finding means something."""
    r = pri.inspect_tree(str(ROOT))
    assert r["state"] == pri.CLEAN, r
    assert r["registers_read"] >= 5


# ── the single-owner rule ────────────────────────────────────────────────────

def test_registers_and_collision_come_from_the_guard_ITSELF():
    """IDENTITY, not equality. A second copy of 'what is a register' or 'what is
    a collision' would drift silently and in the dangerous direction — which is
    exactly how the mandated writer and the mandated guard drifted apart."""
    owner = pri._load_owner()
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_cri_direct", ROOT / "scripts" / "ci" / "check_register_ids.py")
    direct = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(direct)
    assert [r.label for r in owner.REGISTERS] == [r.label for r in direct.REGISTERS]
    assert owner.check_uniqueness.__module__ == owner.__name__
    # and the inspector must not carry its own copy of either
    src = (ROOT / "scripts" / "ops" / "predicted_register_integrity.py").read_text()
    assert "REGISTERS: Tuple" not in src and "def check_uniqueness" not in src


# ── the preflight check's verdicts ───────────────────────────────────────────

def _pf():
    sys.path.insert(0, str(ROOT / "scripts" / "ops"))
    import manager_preflight as mp
    return mp


def test_preflight_fails_on_a_clean_merge_that_would_duplicate():
    mp = _pf()
    c = mp.check_predicted_register_integrity(
        {"b": {"state": gem.CLEAN,
               "inspection": {"state": pri.WOULD_DUPLICATE,
                              "findings": ["items[id]: id 'BL-X' appears at rows 1 AND 2."]}}})
    assert c["state"] == mp.FAIL
    assert "is NOT fixed by merging the base in" in c["message"]


def test_preflight_is_unknown_when_nothing_was_mergeable_to_inspect():
    mp = _pf()
    c = mp.check_predicted_register_integrity({"b": {"state": gem.WOULD_CONFLICT}})
    assert c["state"] == mp.UNKNOWN


def test_preflight_is_unknown_when_the_hook_never_ran():
    mp = _pf()
    c = mp.check_predicted_register_integrity({"b": {"state": gem.CLEAN}})
    assert c["state"] == mp.UNKNOWN


def test_preflight_is_omitted_entirely_when_no_branch_is_named():
    mp = _pf()
    assert mp.check_predicted_register_integrity(None) is None
    assert mp.check_predicted_register_integrity({}) is None


def test_the_two_questions_are_separate_checks():
    """A conflict and a duplicate have OPPOSITE remedies, so one check row
    reporting both would be the collapse this module family refuses."""
    mp = _pf()
    preds = {"b": {"state": gem.WOULD_CONFLICT}}
    merge = mp.check_github_mergeability(("b",), "origin/main", predictions=preds)
    reg = mp.check_predicted_register_integrity(preds)
    assert merge["check"] != reg["check"]
    assert merge["state"] == mp.FAIL and reg["state"] == mp.UNKNOWN


def test_branches_are_predicted_once_not_twice():
    """Each prediction clones and merges a multi-megabyte register."""
    mp = _pf()
    calls = []
    def counting(repo, base, head, **kw):
        calls.append(head)
        return {"state": gem.CLEAN, "inspection": {"state": pri.CLEAN, "note": "x"}}
    preds = mp.predict_branches(("a", "b"), "origin/main", predictor=counting)
    assert calls == ["a", "b"]
    mp.check_github_mergeability(("a", "b"), "origin/main", predictions=preds)
    mp.check_predicted_register_integrity(preds)
    assert calls == ["a", "b"], "a check re-predicted instead of reading the shared result"


# ── the real incident, as a permanent regression fixture ─────────────────────

INCIDENT_HEAD = "fae00214f"      # PR #12374's branch head
INCIDENT_CI_BASE = "5979618dc"   # main when its CI ran  -> correctly green
INCIDENT_SQUASH_BASE = "07f1417e6"  # main when it squashed -> duplicated


def _have(sha: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                          cwd=ROOT, capture_output=True).returncode == 0


@pytest.mark.skipif(
    not all(_have(s) for s in (INCIDENT_HEAD, INCIDENT_CI_BASE, INCIDENT_SQUASH_BASE)),
    reason="shallow clone: the 2026-09-17 incident commits are not present")
def test_the_real_incident_both_bases():
    """⚠️ NOT A SYNTHETIC CASE — these are the commits of the 2026-09-17 event.

    Both arms matter and they are the same branch:

      into 5979618dc (what CI graded, 09:11:38Z) -> clean; CI was RIGHT
      into 07f1417e6 (what the squash used, 09:29:15Z) -> would duplicate

    So the check does not merely fire on a known-bad input; it reproduces the
    exact pair that made the incident invisible. `07f1417e6` did not exist until
    09:28:07Z — 16.5 minutes after CI finished — which is why no PR-time check
    binding its own base can close this class on its own.
    """
    good = gem.predict(str(ROOT), INCIDENT_CI_BASE, INCIDENT_HEAD,
                       inspect=pri.inspect_tree)
    assert good["state"] == gem.CLEAN
    assert good["inspection"]["state"] == pri.CLEAN, good["inspection"]

    bad = gem.predict(str(ROOT), INCIDENT_SQUASH_BASE, INCIDENT_HEAD,
                      inspect=pri.inspect_tree)
    assert bad["state"] == gem.CLEAN, "the merge itself really is clean"
    assert bad["inspection"]["state"] == pri.WOULD_DUPLICATE
    assert any("MISSPELLED-REGISTER-REMOVAL" in f
               for f in bad["inspection"]["findings"]), bad["inspection"]["findings"]
