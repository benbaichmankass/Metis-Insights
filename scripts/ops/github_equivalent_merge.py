#!/usr/bin/env python3
#
# wiring: a manager/lane tool, invoked directly. It answers ONE question that
# `scripts/ops/pr_mergeability.py` (the API-side reader) cannot answer when
# GitHub has not computed mergeability yet -- which is precisely the moment a
# session reaches for a local test merge.
"""WILL GITHUB CALL THIS A CONFLICT? -- the local test merge that predicts the server.

THE DEFECT THIS EXISTS FOR
--------------------------
`.gitattributes` maps this repo's shared JSON registers to ``merge=jsonregister``,
and `scripts/ops/install_merge_driver.sh` registers that driver in a clone's
**local** git config. A clone that has run it resolves a register collision
correctly and reports ``Automatic merge went well``. **GitHub's servers do not
run custom merge drivers**, so the same pair can be ``mergeable_state: dirty``
there.

Both results are correct for their own merge. Only one predicts the merge button.
`BL-20260910-AN-ARMED-CLONES-LOCAL-TEST-MERGE-IS-A-FALSE-NEGATIVE-ON-GITHUB-MERGEABILITY`
records a manager calling a merge on the armed answer and getting HTTP 405.

⚠️ THE TWO SHORTCUTS THAT ROW OFFERS ARE BOTH WRONG, MEASURED
-------------------------------------------------------------
It names ``-c merge.jsonregister.driver=false`` **or** ``-c
core.attributesFile=/dev/null`` as interchangeable. They are not, and neither is
right. Measured 2026-09-17 over three real PRs against one ``origin/main``
(b76997ba9), one fresh detached checkout per cell with the tree asserted clean
before each:

======================  ==========  ==========  ==========  =================
spelling                PR #12379   PR #12384   PR #12386   agrees w/ GitHub
======================  ==========  ==========  ==========  =================
core.attributesFile=    CLEAN       CLEAN       CLEAN       1 of 3
merge...driver= (empty) CONFLICT    CONFLICT    CONFLICT    2 of 3
**un-armed clone**      CLEAN       CONFLICT    CONFLICT    **3 of 3**
======================  ==========  ==========  ==========  =================

* ``core.attributesFile`` names the **user-level** attributes file. The in-repo
  ``.gitattributes`` is untouched by it, so an armed clone still runs the driver
  and still returns the false CLEAN the row exists to prevent. A session
  published a CLEAN verdict off this spelling on 2026-09-17 before catching it.
* An **empty** driver command is not an absent one: git runs it, it fails, and a
  failed driver is booked as a conflict. The tell is the **marker count, not the
  unmerged-path list** -- measured in a controlled fixture, the path IS reported
  unmerged while the file carries **0** ``<<<<<<<`` markers, against 2 for a
  genuine text conflict. So it over-reports, and it does so in a shape that
  looks exactly like a real conflict to any caller reading only the path list.

WHAT ACTUALLY PREDICTS GITHUB
-----------------------------
A clone that was never armed. ``git clone --shared`` gives a fresh local config
with no ``merge.*`` at all -- which is exactly the state GitHub's server is in --
and the merges here additionally neutralise the **global** and **system** config,
because a driver defined in ``~/.gitconfig`` would otherwise reach the clone and
silently restore the false negative this module removes.

THREE STATES, NEVER COLLAPSED
-----------------------------
``clean``           the merge succeeded. GitHub should accept it.
``would_conflict``  a real textual conflict, with the unmerged paths named.
``could_not_look``  a ref did not resolve, the clone failed, git is missing.
                    **NOT a clean bill and NOT a conflict.** Exits 2, so a
                    caller that only tests ``rc == 0`` cannot read a failed
                    probe as a green one.

⚠️ WHAT IT DOES NOT CLAIM. GitHub's mergeability is authoritative and this is a
prediction. It also cannot see required checks, reviews or branch protection: a
``clean`` here says *no conflict*, never *mergeable now*. For that, read
``mergeable_state`` through `scripts/ops/pr_mergeability.py`, which is the one
owner of that question; this module is for when GitHub answers ``unknown``.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

CLEAN = "clean"
WOULD_CONFLICT = "would_conflict"
COULD_NOT_LOOK = "could_not_look"

EXIT = {CLEAN: 0, WOULD_CONFLICT: 1, COULD_NOT_LOOK: 2}

# Drivers git implements itself. A `.gitattributes` naming one of these needs no
# local config, so its presence says nothing about whether a clone is armed.
BUILTIN_DRIVERS = frozenset({"text", "binary", "union", "ours"})

_ATTR_MERGE = re.compile(r"merge=([A-Za-z0-9_.-]+)")


class GitRead(RuntimeError):
    """A git invocation we needed did not succeed. *We could not look.*"""


def _unarmed_env() -> dict[str, str]:
    """Environment in which no global or system git config can define a driver.

    The local config is handled by cloning; this closes the other two scopes. A
    driver in ``~/.gitconfig`` is not hypothetical -- it is what a session gets
    if it ever runs ``git config --global`` instead of the installer.
    """
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    # Neutralising those two also removes any committer identity, and `git merge`
    # validates one even under --no-commit. Without this the merge dies BEFORE
    # merging and the module correctly refuses to grade it -- which is right, and
    # useless. A harness identity is supplied; it is never committed anywhere,
    # because nothing here ever commits.
    env.setdefault("GIT_AUTHOR_NAME", "github-equivalent-merge")
    env.setdefault("GIT_AUTHOR_EMAIL", "noreply@localhost")
    env["GIT_COMMITTER_NAME"] = "github-equivalent-merge"
    env["GIT_COMMITTER_EMAIL"] = "noreply@localhost"
    return env


def _git(args: list[str], cwd: str, *, env: dict[str, str] | None = None,
         check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", cwd] + args, capture_output=True,
                          text=True, env=env)
    if check and proc.returncode != 0:
        raise GitRead(f"`git {' '.join(args)}` exited {proc.returncode}: "
                      f"{(proc.stderr or '').strip()[:300] or '(no stderr)'}")
    return proc


def custom_drivers(attributes_text: str) -> set[str]:
    """Driver names in a `.gitattributes` body that git does NOT implement itself.

    Pure, so the vocabulary question is arguable in a test rather than against a
    live repo. A non-empty result is what makes an armed clone's plain merge
    untrustworthy as a GitHub prediction.
    """
    names = set()
    for raw in attributes_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        for m in _ATTR_MERGE.finditer(line):
            if m.group(1) not in BUILTIN_DRIVERS:
                names.add(m.group(1))
    return names


def clone_is_armed(repo: str, drivers: set[str],
                   env: dict[str, str] | None = None) -> bool | None:
    """Does THIS clone define any of those drivers? ``None`` = we could not look.

    ⚠️ ``env`` is not optional in spirit. The throwaway clone is checked in the
    SAME neutralised environment the merge runs in; reading it in the ambient one
    sees a driver defined in ``~/.gitconfig`` that the merge will never use, and
    the module then refuses a perfectly good probe. A test plants exactly that.
    """
    try:
        for name in sorted(drivers):
            out = _git(["config", "--get", f"merge.{name}.driver"], repo,
                       env=env, check=False)
            if out.returncode == 0 and out.stdout.strip():
                return True
        return False
    except OSError:
        return None


def _conflict_paths(cwd: str, env: dict[str, str]) -> list[str]:
    out = _git(["diff", "--name-only", "--diff-filter=U"], cwd, env=env, check=False)
    return [p for p in out.stdout.splitlines() if p.strip()]


def predict(repo: str, base: str, head: str) -> dict:
    """Merge ``head`` into ``base`` the way GitHub would, and report the verdict."""
    result: dict = {"state": COULD_NOT_LOOK, "base": base, "head": head,
                    "conflict_paths": [], "reason": None,
                    "drivers": [], "source_clone_armed": None}
    tmp = None
    try:
        try:
            base_sha = _git(["rev-parse", "--verify", f"{base}^{{commit}}"], repo).stdout.strip()
            head_sha = _git(["rev-parse", "--verify", f"{head}^{{commit}}"], repo).stdout.strip()
        except GitRead as exc:
            result["reason"] = f"a ref did not resolve: {exc}"
            return result

        try:
            attrs = _git(["show", f"{base_sha}:.gitattributes"], repo, check=False).stdout
        except OSError as exc:  # pragma: no cover - git absent is caught below
            result["reason"] = str(exc)
            return result
        drivers = custom_drivers(attrs)
        result["drivers"] = sorted(drivers)
        result["source_clone_armed"] = clone_is_armed(repo, drivers) if drivers else False

        env = _unarmed_env()
        tmp = tempfile.mkdtemp(prefix="gh-equiv-merge-")
        work = os.path.join(tmp, "clone")
        proc = subprocess.run(
            ["git", "clone", "--shared", "--no-checkout", "--quiet", repo, work],
            capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            result["reason"] = ("could not build an un-armed clone: "
                                f"{(proc.stderr or '').strip()[:300]}")
            return result

        # A --shared clone carries refs/heads/* only, so fetch both commits by SHA.
        for sha in (base_sha, head_sha):
            f = _git(["fetch", "--quiet", repo, sha], work, env=env, check=False)
            if f.returncode != 0:
                result["reason"] = f"could not fetch {sha[:12]} into the clone: {f.stderr.strip()[:200]}"
                return result

        # Premise, asserted rather than assumed: the clone must NOT be armed.
        still_armed = clone_is_armed(work, drivers, env)
        if still_armed:
            result["reason"] = ("the throwaway clone still defines a custom merge "
                                "driver, so it cannot emulate GitHub")
            return result

        co = _git(["checkout", "--detach", base_sha, "--quiet"], work, env=env, check=False)
        if co.returncode != 0:
            result["reason"] = f"could not check out the base: {co.stderr.strip()[:200]}"
            return result

        merged = _git(["merge", "--no-commit", "--no-ff", head_sha], work,
                      env=env, check=False)
        if merged.returncode == 0:
            result["state"] = CLEAN
            return result
        paths = _conflict_paths(work, env)
        if not paths:
            # A conflict git cannot name is not a textual conflict we can trust,
            # so it is graded could_not_look rather than reported as a verdict.
            # ⚠️ This is NOT what catches the empty-driver over-report: measured,
            # that failure DOES name the path (with zero markers inside it). The
            # module avoids that spelling entirely; this guard is for the other
            # ways a merge can fail before it merges -- a missing committer
            # identity being the one hit while building this.
            result["reason"] = ("git reported a conflict but named no unmerged "
                                "path, so the merge itself could not be graded: "
                                f"{(merged.stderr or merged.stdout).strip()[:300]}")
            return result
        result["state"] = WOULD_CONFLICT
        result["conflict_paths"] = paths
        return result
    except GitRead as exc:
        result["reason"] = str(exc)
        return result
    except OSError as exc:
        result["reason"] = f"git could not be run: {exc}"
        return result
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def render(result: dict) -> str:
    state = result["state"]
    head, base = result["head"], result["base"]
    lines = []
    if state == CLEAN:
        lines.append(f"github-equivalent merge: CLEAN — {head} into {base} "
                     "merges with no conflict")
    elif state == WOULD_CONFLICT:
        lines.append(f"github-equivalent merge: WOULD CONFLICT — {head} into "
                     f"{base}, {len(result['conflict_paths'])} unmerged path(s):")
        for p in result["conflict_paths"]:
            lines.append(f"  - {p}")
        lines.append("  remedy: merge the base branch into the PR head and resolve.")
    else:
        lines.append("github-equivalent merge: COULD NOT LOOK — this is NOT a "
                     "clean bill and NOT a conflict.")
        lines.append(f"  {result['reason']}")
    if result["drivers"]:
        armed = result["source_clone_armed"]
        word = {True: "ARMED", False: "not armed", None: "unreadable"}[armed]
        lines.append(f"  (this clone is {word} for {', '.join(result['drivers'])}; "
                     "the verdict above was taken in an un-armed clone either way)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true", help="emit the raw verdict")
    args = ap.parse_args(argv)

    result = predict(os.path.abspath(args.repo), args.base, args.head)
    if args.json:
        import json as _json
        print(_json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render(result))
    return EXIT[result["state"]]


if __name__ == "__main__":
    sys.exit(main())
