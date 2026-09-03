#!/usr/bin/env python3
"""`claude-pr-automerge` must never arm a PR that did not ask.

This guard exists because the class has now recurred TWICE, and each time the
remedy was to narrow the `paths:` filter — which is not a gate at all:

  1. 2026-08-22 — the filter globbed `**` and the directory's own README.md
     armed auto-merge on the PR that added it.
  2. 2026-09-02 — the filter globbed `*.txt`, and three PRs (#10788 and the
     branches behind #10797 / #10783) were un-drafted and armed having asked
     for nothing. Each had merged `origin/main`. GitHub computes a push's
     changed-file set as the before-head→after-head diff, so the merge dragged
     in the nine request files that landed on `main` that day.

⚠️ THE SECOND INCIDENT WAS MIS-ATTRIBUTED AT FIRST, WHICH IS WHY THIS GUARD
CHECKS THE JOB BODY AND NOT JUST THE FILTER. The dispatch and the original
backlog row both blamed the legacy shared path `.github/pr-automerge-request`
still sitting in the filter. Measured, that path is in ZERO of the three push
diffs — it had not been modified on `main` since 2026-08-21, so it could not
match. Removing it fixes nothing on its own. A guard that only pinned the filter
would have passed the whole time the defect was live.

So the invariant is: A `paths:` FILTER MAY NOT BE THE ONLY GATE. The job body
must prove the ask against the branch's OWN name, and must not silently un-draft.

Checks:
  C1  the trigger is exactly `.github/pr-automerge-requests/*.txt` — no `**`,
      no legacy path, nothing else.
  C2  the legacy shared marker is absent from the tree (an inert file whose
      name still reads like a request is a trap).
  C3  the job body derives a slug FROM THE BRANCH and looks up a request file
      named for it — the ask is branch-scoped.
  C4  the job body compares the request file at the pushed head against `main`,
      so a file merely inherited by a merge is not an ask.
  C5  the job body's executable code never calls `markPullRequestReadyForReview`
      (comments naming it are fine and are stripped before the check — an
      assertion that punished the explanation would train the next editor to
      delete it).

Run standalone, or `--self-test` to plant each defect and prove the guard fails.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW_REL = ".github/workflows/claude-pr-automerge.yml"
LEGACY_REL = ".github/pr-automerge-request"
EXPECTED_PATHS = [".github/pr-automerge-requests/*.txt"]


def _script_of(doc: dict) -> str:
    steps = doc["jobs"]["open-and-automerge"]["steps"]
    bodies = [s["with"]["script"] for s in steps if "script" in s.get("with", {})]
    if len(bodies) != 1:
        raise ValueError(f"expected exactly one github-script step, found {len(bodies)}")
    return bodies[0]


def _code_only(script: str) -> str:
    """The script with `//` comment lines removed."""
    return "\n".join(ln for ln in script.splitlines()
                     if not ln.lstrip().startswith("//"))


def check(root: Path) -> list[str]:
    fails: list[str] = []
    wf = root / WORKFLOW_REL
    if not wf.exists():
        return [f"{WORKFLOW_REL} is missing — the relay this guard pins is gone."]

    doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
    # PyYAML parses a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True, {}))
    paths = list((on.get("push") or {}).get("paths") or [])

    # C1
    if paths != EXPECTED_PATHS:
        fails.append(
            f"C1 trigger paths are {paths!r}, expected {EXPECTED_PATHS!r}. "
            "A '**' glob lets a doc edit arm auto-merge (2026-08-22); the legacy "
            "shared path is dead. Adding any path here widens what can arm a merge.")

    # C2
    if (root / LEGACY_REL).exists():
        fails.append(
            f"C2 {LEGACY_REL} is back in the tree. It is no longer a trigger, so a "
            "session writing to it would believe it had asked for auto-merge and get "
            "silence. Delete it.")

    try:
        script = _script_of(doc)
    except (KeyError, ValueError) as exc:
        return fails + [f"C3-C5 cannot read the job's script: {exc}"]

    code = _code_only(script)

    # C3 — the ask is scoped to THIS branch's name.
    slug_derived = re.search(r"replace\(\s*/\^claude\\?/\s*/", code) is not None
    slug_used = "pr-automerge-requests/${slug}.txt" in code
    if not (slug_derived and slug_used):
        fails.append(
            "C3 the job body does not derive a slug from the branch and look up "
            "`.github/pr-automerge-requests/${slug}.txt`. Without a branch-scoped "
            "request file, another branch's ask arms yours — the 2026-09-02 defect.")

    # C4 — presence is not an ask; it must differ from main.
    if "blobSha('main')" not in code or "blobSha(context.sha)" not in code:
        fails.append(
            "C4 the job body does not compare the request file against `main`. A file "
            "inherited unchanged by a merge of `main` is not a request, and treating "
            "presence alone as the ask reintroduces the defect through the front door.")

    # C5 — never silently un-draft.
    if "markPullRequestReadyForReview" in code:
        fails.append(
            "C5 the job body can call `markPullRequestReadyForReview`. A draft is this "
            "repo's 'prepared, not approved' marker for Tier-2/Tier-3; un-drafting "
            "deletes the one signal holding a PR back, and branch protection does not "
            "help because it gates on checks. #10788 and #10764 were both armed this "
            "way while their own bodies said not to merge.")

    return fails


# ---------------------------------------------------------------------------
# self-test: plant each defect, prove the guard FAILS on it.
# ---------------------------------------------------------------------------

def _sandbox(tmp: Path) -> Path:
    root = tmp / "repo"
    (root / ".github/workflows").mkdir(parents=True)
    (root / WORKFLOW_REL).write_text((REPO / WORKFLOW_REL).read_text(encoding="utf-8"),
                                     encoding="utf-8")
    return root


def _mutate_script(root: Path, old: str, new: str) -> None:
    p = root / WORKFLOW_REL
    s = p.read_text(encoding="utf-8")
    assert old in s, f"self-test plant is stale, {old!r} not in the workflow"
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


def self_test() -> int:
    plants = {
        "C1 legacy path restored to the filter": lambda r: _mutate_script(
            r, '      - ".github/pr-automerge-requests/*.txt"',
            '      - ".github/pr-automerge-requests/*.txt"\n      - ".github/pr-automerge-request"'),
        # ⚠️ Both of these plants MUST target the yaml list entry, not the first
        # textual occurrence — the path is also NAMED in the comments above the
        # filter, and an earlier version of this self-test replaced a comment and
        # reported the guard broken when the guard was fine. A plant that does not
        # plant the defect is worse than no plant: it fails loudly for the wrong
        # reason and invites someone to "fix" a working check.
        "C1 '**' glob restored": lambda r: _mutate_script(
            r, '      - ".github/pr-automerge-requests/*.txt"',
            '      - ".github/pr-automerge-requests/**"'),
        "C2 legacy marker back in the tree": lambda r: (
            r / LEGACY_REL).write_text("x", encoding="utf-8"),
        "C3 slug lookup removed": lambda r: _mutate_script(
            r, "pr-automerge-requests/${slug}.txt", "pr-automerge-request"),
        "C4 main comparison removed": lambda r: _mutate_script(
            r, "await blobSha('main')", "null"),
        "C5 un-draft restored": lambda r: _mutate_script(
            r, "            // 3. enable native auto-merge.",
            "            await github.graphql(`mutation($id:ID!){ "
            "markPullRequestReadyForReview(input:{pullRequestId:$id}){ pullRequest{ "
            "number } } }`, { id: pr.node_id });"),
    }

    with tempfile.TemporaryDirectory() as td:
        clean = _sandbox(Path(td))
        base = check(clean)
        if base:
            print("::error::self-test FAILED — the CURRENT workflow does not pass:")
            for f in base:
                print(f"  - {f}")
            return 1
        print("self-test: clean tree passes (the positive control)")

    bad = 0
    for name, plant in plants.items():
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(Path(td))
            plant(root)
            fails = check(root)
            if not fails:
                print(f"::error::self-test FAILED — planted '{name}' and the guard "
                      f"still passed. Its failure path is broken, so a green means "
                      f"nothing.")
                bad += 1
            else:
                print(f"self-test: '{name}' correctly caught")
    if bad:
        return 1
    print(f"self-test OK — all {len(plants)} planted defects fail the guard")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true",
                    help="plant each defect and prove the guard fails on it")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    fails = check(REPO)
    if fails:
        print("::error::automerge-trigger-guard FAILED")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("automerge-trigger: OK — the trigger is branch-scoped, proven against "
          "main, and cannot un-draft a PR it did not open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
