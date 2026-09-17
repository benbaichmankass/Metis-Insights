"""The local test merge must predict GITHUB, not this clone.

`BL-20260910-AN-ARMED-CLONES-LOCAL-TEST-MERGE-IS-A-FALSE-NEGATIVE-ON-GITHUB-MERGEABILITY`
demands the defect be reproduced and then watched NOT to happen, in a clone
where the driver is ARMED and `.gitattributes` maps the path -- because that is
the only shape in which it appears. Every fixture here builds exactly that shape
and asserts the false negative is reachable before asserting the helper avoids
it. A suite that only checked the helper's own answer would be satisfied by a
helper that always says CONFLICT.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "ops"))
import github_equivalent_merge as gem  # noqa: E402


# --------------------------------------------------------------------------
# fixtures: a repo shaped like this one -- a register mapped to a custom driver
# --------------------------------------------------------------------------

def _run(args, cwd, env=None):
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"{args} -> {proc.returncode}\n{proc.stderr}"
    return proc.stdout


def _rows(n, start=0):
    return {"items": [{"id": f"BL-2026010{start}-ROW-{i:04d}", "text": f"row {i}"}
                      for i in range(n)]}


def _write_register(root, data):
    with open(os.path.join(root, "reg.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
        fh.write("\n")


def _armed_repo(tmp_path, *, map_register=True):
    """A repo whose `reg.json` is mapped to a custom driver that ALWAYS succeeds.

    The driver is deliberately a trivial "take ours" script rather than the real
    row-aware merger: what this suite is about is the driver being RUN at all,
    and a driver that always succeeds makes the false negative maximally sharp.
    """
    root = tmp_path / "src"
    root.mkdir()
    root = str(root)
    _run(["git", "init", "--quiet", "-b", "main"], root)
    _run(["git", "config", "user.name", "t"], root)
    _run(["git", "config", "user.email", "t@e"], root)

    driver = os.path.join(root, "always_ok_driver.py")
    with open(driver, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent("""\
            import sys
            # %O %A %B -- git reads the result back out of %A, so KEEPING OURS
            # means writing nothing and reporting success. That is the sharpest
            # possible false negative: a driver that always resolves.
            sys.exit(0)
            """))
    attrs = "reg.json merge=alwaysok\n" if map_register else "# nothing mapped\n"
    with open(os.path.join(root, ".gitattributes"), "w", encoding="utf-8") as fh:
        fh.write(attrs)
    _write_register(root, _rows(3))
    with open(os.path.join(root, "unrelated.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "--quiet", "-m", "base"], root)

    # ARM the driver in THIS clone's local config -- the shape that bites.
    _run(["git", "config", "merge.alwaysok.name", "always ok"], root)
    _run(["git", "config", "merge.alwaysok.driver",
          f"{sys.executable} '{driver}' %O %A %B %P"], root)
    return root


def _branch_appending(root, branch, row_id):
    _run(["git", "checkout", "--quiet", "-b", branch, "main"], root)
    with open(os.path.join(root, "reg.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    data["items"].append({"id": row_id, "text": row_id})
    _write_register(root, data)
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "--quiet", "-m", f"append {row_id}"], root)
    _run(["git", "checkout", "--quiet", "main"], root)


def _armed_plain_merge(root, base, head):
    """What an ARMED clone's own `git merge` says -- the answer that misleads."""
    _run(["git", "checkout", "--quiet", "--detach", base], root)
    proc = subprocess.run(["git", "merge", "--no-commit", "--no-ff", head],
                          cwd=root, capture_output=True, text=True)
    subprocess.run(["git", "merge", "--abort"], cwd=root, capture_output=True)
    subprocess.run(["git", "reset", "--hard", "--quiet"], cwd=root, capture_output=True)
    subprocess.run(["git", "checkout", "--quiet", "main"], cwd=root, capture_output=True)
    return proc.returncode


# --------------------------------------------------------------------------
# the required reproduction
# --------------------------------------------------------------------------

class TestTheFalseNegativeIsReproducedAndThenAvoided:
    def test_the_armed_clone_really_does_return_a_false_CLEAN(self, tmp_path):
        """The premise, asserted rather than assumed.

        Without this the next test is satisfied by a helper that says CONFLICT
        about a pair nothing ever disagreed on.
        """
        root = _armed_repo(tmp_path)
        _branch_appending(root, "a", "BL-20260101-AAA")
        _branch_appending(root, "b", "BL-20260101-BBB")
        assert _armed_plain_merge(root, "a", "b") == 0, (
            "the armed clone was expected to resolve this via the driver; if it "
            "conflicts, the fixture no longer reproduces the defect")

    def test_the_helper_reports_the_conflict_github_would(self, tmp_path):
        root = _armed_repo(tmp_path)
        _branch_appending(root, "a", "BL-20260101-AAA")
        _branch_appending(root, "b", "BL-20260101-BBB")
        result = gem.predict(root, "a", "b")
        assert result["state"] == gem.WOULD_CONFLICT, result
        assert result["conflict_paths"] == ["reg.json"], result
        assert result["source_clone_armed"] is True, result

    def test_the_exit_code_separates_the_three_verdicts(self, tmp_path):
        root = _armed_repo(tmp_path)
        _branch_appending(root, "a", "BL-20260101-AAA")
        _branch_appending(root, "b", "BL-20260101-BBB")
        assert gem.main(["--repo", root, "--base", "a", "--head", "b"]) == 1
        assert gem.EXIT[gem.CLEAN] == 0
        assert gem.EXIT[gem.WOULD_CONFLICT] == 1
        assert gem.EXIT[gem.COULD_NOT_LOOK] == 2


# --------------------------------------------------------------------------
# the opposite failure: a helper that only ever says CONFLICT
# --------------------------------------------------------------------------

class TestCleanIsReachable:
    def test_disjoint_changes_merge_clean(self, tmp_path):
        root = _armed_repo(tmp_path)
        _run(["git", "checkout", "--quiet", "-b", "a", "main"], root)
        with open(os.path.join(root, "unrelated.txt"), "w", encoding="utf-8") as fh:
            fh.write("side a\n")
        _run(["git", "add", "-A"], root)
        _run(["git", "commit", "--quiet", "-m", "a"], root)
        _run(["git", "checkout", "--quiet", "-b", "b", "main"], root)
        with open(os.path.join(root, "other.txt"), "w", encoding="utf-8") as fh:
            fh.write("side b\n")
        _run(["git", "add", "-A"], root)
        _run(["git", "commit", "--quiet", "-m", "b"], root)
        _run(["git", "checkout", "--quiet", "main"], root)
        result = gem.predict(root, "a", "b")
        assert result["state"] == gem.CLEAN, result

    def test_a_mapped_file_edited_far_apart_on_both_sides_still_merges_clean(self, tmp_path):
        """⚠️ THE OVER-REPORT CONTROL, and it is the reason for the whole module.

        `BL-20260910`'s other suggested spelling is `-c merge.<name>.driver=`.
        An EMPTY driver command is not an absent one: git runs it, it fails, and
        a failed driver is booked as a conflict -- so that spelling calls THIS
        pair conflicted when the text merges perfectly. Measured on three real
        PRs, it disagreed with GitHub once in three. This input is the shape
        that separates the two: both sides touch the mapped register, so the
        driver IS invoked, but at opposite ends of the file.
        """
        root = _armed_repo(tmp_path)
        big = _rows(40)
        _write_register(root, big)
        _run(["git", "add", "-A"], root)
        _run(["git", "commit", "--quiet", "-m", "grow"], root)

        for name, idx in (("a", 0), ("b", 39)):
            _run(["git", "checkout", "--quiet", "-b", name, "main"], root)
            with open(os.path.join(root, "reg.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            data["items"][idx]["text"] = f"edited by {name}"
            _write_register(root, data)
            _run(["git", "add", "-A"], root)
            _run(["git", "commit", "--quiet", "-m", name], root)
            _run(["git", "checkout", "--quiet", "main"], root)

        result = gem.predict(root, "a", "b")
        assert result["state"] == gem.CLEAN, result

        # And the empty-driver spelling really does get this wrong, which is why
        # the module does not use it.
        _run(["git", "checkout", "--quiet", "--detach", "a"], root)
        proc = subprocess.run(
            ["git", "-c", "merge.alwaysok.driver=", "merge", "--no-commit",
             "--no-ff", "b"], cwd=root, capture_output=True, text=True)
        unmerged = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"],
                                  cwd=root, capture_output=True, text=True).stdout
        subprocess.run(["git", "merge", "--abort"], cwd=root, capture_output=True)
        assert proc.returncode != 0, (
            "the empty-driver spelling was expected to over-report here; if it "
            "no longer does, this control has stopped discriminating")
        assert unmerged.split() == ["reg.json"], (
            "the empty-driver failure is expected to report the path as unmerged",
            unmerged)
        with open(os.path.join(root, "reg.json"), encoding="utf-8") as fh:
            markers = sum(1 for line in fh if line.startswith("<<<<<<<"))
        subprocess.run(["git", "reset", "--hard", "--quiet"], cwd=root,
                       capture_output=True)
        assert markers == 0, (
            "⚠️ THE MEASURED SIGNATURE: a FAILED driver leaves the path unmerged "
            "with NO conflict markers in it, where a genuine textual conflict "
            "leaves markers. A caller reading only the unmerged-path list cannot "
            "tell the two apart -- which is why this module does not use that "
            "spelling at all")


# --------------------------------------------------------------------------
# could-not-look is a third state, never folded into either verdict
# --------------------------------------------------------------------------

class TestCouldNotLookIsNeverAVerdict:
    @pytest.mark.parametrize("base,head", [
        ("no-such-ref-000", "main"),
        ("main", "no-such-ref-000"),
    ])
    def test_an_unresolvable_ref_exits_2_and_claims_nothing(self, tmp_path, base, head, capsys):
        root = _armed_repo(tmp_path)
        rc = gem.main(["--repo", root, "--base", base, "--head", head])
        out = capsys.readouterr().out
        assert rc == 2, out
        assert "COULD NOT LOOK" in out
        assert "CLEAN" not in out.split("COULD NOT LOOK")[0]

    def test_a_merge_that_fails_BEFORE_merging_is_not_reported_as_a_conflict(self, tmp_path):
        """⚠️ ADDED AFTER A PLANT WAS ABSORBED, and the escape is the finding.

        Deleting the ``if not paths`` guard -- the branch that refuses to call a
        conflict git cannot name -- changed nothing, because no test constructed
        an input where the merge fails BEFORE it merges. It looked covered and
        proved nothing.

        Unrelated histories are that input, and it is not synthetic: git exits
        non-zero, names no unmerged path, and a caller keying on the exit code
        alone books it as a conflict. It is *we could not look*.
        """
        root = _armed_repo(tmp_path)
        _run(["git", "checkout", "--quiet", "--orphan", "island"], root)
        _run(["git", "rm", "-rq", "--cached", "."], root)
        with open(os.path.join(root, "island.txt"), "w", encoding="utf-8") as fh:
            fh.write("no common ancestor\n")
        _run(["git", "add", "island.txt"], root)
        _run(["git", "commit", "--quiet", "-m", "island"], root)
        # --force: the orphan un-tracked main's files, which are still on disk.
        _run(["git", "checkout", "--quiet", "--force", "main"], root)

        result = gem.predict(root, "main", "island")
        assert result["state"] == gem.COULD_NOT_LOOK, result
        assert result["conflict_paths"] == [], result
        assert result["reason"], "could_not_look must say why"

    def test_a_directory_that_is_not_a_repo_is_could_not_look(self, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        result = gem.predict(str(d), "main", "HEAD")
        assert result["state"] == gem.COULD_NOT_LOOK, result
        assert result["reason"]


# --------------------------------------------------------------------------
# the mechanism itself
# --------------------------------------------------------------------------

class TestTheMechanismIsTheOneThatWorks:
    def test_a_driver_defined_GLOBALLY_cannot_leak_into_the_verdict(self, tmp_path, monkeypatch):
        """The local config is handled by cloning; this closes the other scope.

        A driver in `~/.gitconfig` would otherwise reach the throwaway clone and
        restore the exact false negative this module removes.
        """
        root = _armed_repo(tmp_path)
        _branch_appending(root, "a", "BL-20260101-AAA")
        _branch_appending(root, "b", "BL-20260101-BBB")

        gcfg = tmp_path / "global.gitconfig"
        driver = os.path.join(root, "always_ok_driver.py")
        gcfg.write_text(
            "[merge \"alwaysok\"]\n"
            f"\tdriver = {sys.executable} '{driver}' %O %A %B %P\n",
            encoding="utf-8")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gcfg))

        result = gem.predict(root, "a", "b")
        assert result["state"] == gem.WOULD_CONFLICT, (
            "a globally-defined driver leaked into the throwaway clone", result)

    def test_the_module_does_not_rely_on_core_attributesFile(self):
        """⚠️ STRUCTURAL, and it pins the measured correction.

        `core.attributesFile` names the USER-LEVEL attributes file. The in-repo
        `.gitattributes` is untouched by it, so an armed clone still runs the
        driver -- measured 2026-09-17 as 3-of-3 false CLEAN over three real PRs
        GitHub graded `dirty`. If a later edit reaches for that spelling as the
        mechanism, this fails.
        """
        src = open(gem.__file__, encoding="utf-8").read()
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        body = code.split('"""', 2)[-1]  # drop the module docstring, which cites it
        assert "attributesFile" not in body, (
            "core.attributesFile appears outside the docstring; it does not "
            "disable the in-repo .gitattributes and must not be the mechanism")

    def test_builtin_driver_names_are_not_treated_as_custom(self):
        assert gem.custom_drivers("a.json merge=union\n") == set()
        assert gem.custom_drivers("a.json merge=binary\nb merge=ours\n") == set()
        assert gem.custom_drivers("a.json merge=jsonregister\n") == {"jsonregister"}

    def test_comments_do_not_contribute_driver_names(self):
        attrs = "# docs/x.json merge=ghostdriver\nreal.json merge=realdriver\n"
        assert gem.custom_drivers(attrs) == {"realdriver"}

    def test_this_repos_own_gitattributes_declares_the_driver_it_is_built_for(self):
        """A vacuity control on the pure function, keyed on the real file.

        If `.gitattributes` ever stops mapping a custom driver, this module's
        premise is gone and the suite should say so rather than pass quietly.
        """
        here = os.path.join(os.path.dirname(__file__), "..", ".gitattributes")
        names = gem.custom_drivers(open(here, encoding="utf-8").read())
        assert "jsonregister" in names, names
        assert "union" not in names, "union is a git built-in and needs no arming"
