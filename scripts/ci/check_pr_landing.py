#!/usr/bin/env python3
"""Every PR declares its TIER and how it intends to LAND — and the declaration is checked.

WHY THIS EXISTS
---------------
Measured on the morning of 2026-09-03: seven of the night shift's PRs sat open,
green and unlanded, waiting on the manager. Three of their bodies blamed the
tooling — *"pr-opener.yml creates every PR as a draft regardless of
`draft:false`"*. **That claim is false.** `pr-opener.yml`'s draft line is
``[ "$(jq -r '.draft // true' "$req")" = "true" ] && draft_flag="--draft"`` —
`draft:false` is honoured; `true` is merely the DEFAULT, and the request files
those sessions wrote asked for `"draft": true`. They asked for drafts and got
drafts.

The real cause is a permissions asymmetry on top of a blanket convention:

  * a sub-session frequently 403s on `update_pull_request`, so it often cannot
    un-draft its own PR, while the manager can; and
  * `scripts/ops/session_registry.py`'s spawn prompt ended, unconditionally and
    at every tier, with *"Open the PR as a DRAFT; the manager merges."*

So Tier-1 work — which `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers says
needs **no** human OK to merge — was routed through a human anyway, by
convention, on every single session. The route that lands it without a manager
already existed and was simply not being used.

TWO MECHANISMS, AND THEY ARE NOT ALTERNATIVES
---------------------------------------------
Read from the two workflows rather than assumed. They answer different
questions, and either one alone leaves the work sitting:

  * ``"draft": false`` (via `pr-opener.yml`, or a direct `create_pull_request`)
    decides READINESS — "this is approved to land". On its own it produces a
    ready, green PR that waits for somebody to click Merge. That is exactly the
    failure being fixed, not a fix for it.
  * ``.github/pr-automerge-requests/<slug>.txt`` decides LANDING —
    `claude-pr-automerge.yml` enables native auto-merge, and GitHub then merges
    **only when the required checks pass**. On its own, against a DRAFT PR, it
    is REFUSED by that workflow's draft refusal — correctly, and this guard does
    not weaken that refusal.

Tier-1 self-landing therefore needs BOTH, and this guard requires both together
(R4/R5/R6). Tier-2 and Tier-3 need a human, so they may have NEITHER (R4, R10).

WHY THIS GUARD CAN BITE AT ALL
------------------------------
Auto-merge merges on GREEN. This guard is a required check. So a branch that
arms auto-merge while under-declaring its tier holds *itself* out of `main` by
failing its own guard — no manager, no reminder, no race. The teeth are not an
alarm; they are the merge gate the branch already asked to be judged by.

THE OVERRIDE IS VERIFIED, NOT PRESENCE-ONLY
-------------------------------------------
The lesson of `new-table-wiring-guard`, whose presence-only `# data-wiring:`
marker made the cheapest way to silence a real finding *naming a table that does
not exist*: a guard cheaper to lie to than to satisfy is worse than no guard.
So nothing here is satisfied by a field merely being present.

  * ``tier`` is checked against the DIFF, not taken on trust (R5). Declaring
    tier 1 while touching `config/strategies.yaml` fails.
  * ``hold_reason: changes_landing_machinery`` is checked against the diff too
    (R8) — and the branch's OWN declaration file is EXCLUDED from the machinery
    set, because every branch touches that file and an excuse every branch
    satisfies is not an excuse.
  * ``hold_reason: depends_on_unmerged_pr`` must name a PR number (R9).
  * ``why`` / ``hold_text`` must carry real text (R3/R7). A one-word `"why": "x"`
    is refused.

AND THE ROUTE MAY NOT LAND A CHANGE TO ITSELF (R12)
---------------------------------------------------
A PR that edits the landing machinery is Tier-1 by the canonical doc's own list
(*"GitHub Actions workflow fixes"*, *"CI scripts"*), so tier is not the
objection — but it may not self-land, because the change and its own approval
would be the same act and a mistake in it disarms the very check that would
have caught the mistake. Such a PR holds, with `changes_landing_machinery`,
and a human reads it once. This file's own PR is the first instance.

⚠️ THE TIER-1 SURFACE IS AN ALLOWLIST, AND THAT POLARITY IS DELIBERATE
----------------------------------------------------------------------
`TIER1_SURFACE` enumerates the Tier-1 EXAMPLES from
`docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers — docs, tests, CI, GitHub
Actions, `comms/`, lint config. A path it does not recognise is **not** thereby
Tier-1; it is a path this guard cannot vouch for, and it blocks self-landing.

That is the opposite polarity from a denylist of dangerous paths, and it is
chosen for the reason this repo keeps writing down about `PROTECTION_REASSERT_ACCOUNTS`
and `BYBIT_GRADED_COVERAGE_ACCOUNTS`: an unrecognised value must not arm
anything. A denylist would let a path nobody thought of self-land onto `main`;
an allowlist makes the unknown case fail closed and cost one line of review.

`TIER2_PATHS` / `TIER3_PATHS` exist ON TOP of that, so the failure message can
say *why* a path is barred by name ("config/strategies.yaml is Tier-3") rather
than only "not in the Tier-1 surface", and so that widening the allowlist by
mistake still trips the named check.

⚠️ AND THE GUARD NEVER CLAIMS A DIFF *IS* TIER-1. It reports the paths it could
not vouch for. Silence from the Tier-2/3 name lists is this guard not
recognising anything — a negative with no denominator — never proof the change
is safe. The session's own `why` is where that judgement is recorded, and a
human reads it on any PR that does not self-land.

SELF-ARMING, WITH NO FLAG TO UNSET
----------------------------------
Requiring a declaration on EVERY PR would red every branch already open on the
day this merges — measured at **6 open PRs** (population: every open PR
returned by `list_pull_requests` state=open, 2026-09-03), whose authoring
sessions are mostly dead and cannot add the file. Failing them is how a guard
gets disabled instead of fixed; `check_pr_queue_watch.py` records that exact
reasoning.

So R11 asks a question no flag can fake: **did this guard exist at the branch's
merge-base with `main`?** A branch cut from a `main` that already carried the
rule had the rule available and must declare. One cut before it reports
`undeclared_predates_guard` — a PASS, printed loudly and counted, never a
silent one. The guard arms itself as those branches drain, and there is nothing
to switch on.

⚠️ THE DANGEROUS DIRECTION IS NOT GRANDFATHERED. R10 — arming auto-merge with no
valid Tier-1 self-land declaration — fires even on a branch that predates the
guard, because arming is an affirmative act performed today through a file the
branch adds today. Age excuses not knowing the rule; it does not excuse asking
to merge without approval.

STATES, NEVER COLLAPSED
-----------------------
  ``not_a_pr``                    — no base ref to diff against (a push /
                                    `--all` run). Nothing was graded. NOT a pass.
  ``undeclared_predates_guard``   — no declaration, and the branch could not
                                    have known. Passes, counted, loud.
  ``undeclared``                  — no declaration and the branch could have
                                    known. FAILS.
  ``declared_self_land``          — Tier-1, armed, diff inside the Tier-1
                                    surface. Lands itself on green.
  ``declared_hold``               — a typed, verified reason to stay held.
  ``declared_needs_approval``     — Tier-2/3, correctly not armed.

Run standalone with ``--base origin/main``, or ``--self-test`` to plant each
defect and prove the guard fails on it.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]

LANDING_DIR = ".github/pr-landing"
AUTOMERGE_DIR = ".github/pr-automerge-requests"
GUARD_REL = "scripts/ci/check_pr_landing.py"

# The Tier-1 EXAMPLES from docs/CLAUDE-RULES-CANONICAL.md § Permission Tiers.
# An allowlist: a path not matched here cannot self-land. See the module
# docstring for why this polarity rather than a denylist.
TIER1_SURFACE = [
    "docs/**",
    "tests/**",
    "comms/**",
    ".github/**",
    "scripts/ci/**",
    "scripts/ops/**",
    "scripts/research/**",
    "scripts/reports/**",
    "*.md",
    ".ruff.toml",
    "ruff.toml",
    "pyproject.toml",
    ".gitignore",
]

# Named so a failure can say WHY a path is barred, and so that widening
# TIER1_SURFACE by mistake still trips a named check.
TIER3_PATHS = [
    "config/strategies.yaml",
    "config/accounts.yaml",
    "config/risk_caps.yaml",
    "config/regime_policy.yaml",
    "config/pairs.yaml",
    "config/prop_rulesets/**",
    "src/runtime/orders.py",
    "src/runtime/risk_counters.py",
    "src/units/accounts/execute.py",
    "src/units/accounts/risk.py",
    "src/units/strategies/**",
    "deploy/ict-trader-live.*",
    "deploy/ict-web-api.*",
]
TIER2_PATHS = [
    "src/**",
    "config/**",
    "deploy/**",
    "ml/**",
]

# For R8 and R12. The branch's OWN declaration file is excluded by the caller —
# every branch writes one, and an excuse every branch satisfies excuses nothing.
#
# ⚠️ `.github/pr-automerge-requests/**` is deliberately NOT here. Writing a
# request file is USING the landing route, not CHANGING it, and every
# self-landing branch writes one — including it would hand the
# `changes_landing_machinery` excuse to exactly the branches that must not have
# it. Editing that directory's README is not machinery either; the machinery is
# the code and the workflows that decide.
LANDING_MACHINERY = [
    ".github/workflows/claude-pr-automerge.yml",
    ".github/workflows/pr-opener.yml",
    ".github/workflows/board-post.yml",
    ".github/pr-landing/*.json",
    "scripts/ci/check_pr_landing.py",
    "scripts/ci/check_automerge_trigger.py",
    "scripts/ops/session_registry.py",
]

HOLD_REASONS = {
    "changes_landing_machinery":
        "this PR edits the landing route itself, so it wants a human read "
        "(VERIFIED against the diff — see R8)",
    "depends_on_unmerged_pr":
        "must land after another PR (must NAME it as #N — see R9)",
    "awaiting_evidence":
        "the change is prepared but an observation must land first; say WHICH",
    "operator_asked_to_hold":
        "an explicit operator instruction; quote it",
    "tier_2_3_needs_approval":
        "Tier-2/Tier-3 work; say what approval is being sought and from whom",
}

MIN_TEXT = 20


def _match(path: str, globs: list[str]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(path, g):
            return True
        # `dir/**` should match `dir/a` as well as `dir/a/b`.
        if g.endswith("/**") and (path == g[:-3] or path.startswith(g[:-2])):
            return True
    return False


def _git(root: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def branch_slug(branch: str) -> str:
    """Same derivation `claude-pr-automerge.yml` uses, so the two agree."""
    return re.sub(r"^claude/", "", branch).replace("/", "-")


def current_branch(root: Path) -> Optional[str]:
    # On a `pull_request` event the checkout is a detached merge ref, so
    # `rev-parse --abbrev-ref HEAD` reads `HEAD`. GITHUB_HEAD_REF is the branch.
    for env in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME"):
        v = (os.environ.get(env) or "").strip()
        if v and v != "HEAD":
            return v
    rc, out = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if rc == 0 and out and out != "HEAD":
        return out
    return None


def changed_paths(root: Path, base: str) -> Optional[list[str]]:
    rc, mb = _git(root, "merge-base", base, "HEAD")
    if rc != 0 or not mb:
        return None
    rc, out = _git(root, "diff", "--name-only", f"{mb}...HEAD")
    if rc != 0:
        return None
    return [ln for ln in out.splitlines() if ln.strip()]


def guard_existed_at_merge_base(root: Path, base: str) -> Optional[bool]:
    """Could this branch have known the rule? None = we could not look."""
    rc, mb = _git(root, "merge-base", base, "HEAD")
    if rc != 0 or not mb:
        return None
    rc, _ = _git(root, "cat-file", "-e", f"{mb}:{GUARD_REL}")
    return rc == 0


def _added_or_modified(root: Path, base: str, rel: str) -> bool:
    """Present at HEAD and not byte-identical to `base` — the automerge gate's own test."""
    rc_head, head_sha = _git(root, "rev-parse", f"HEAD:{rel}")
    if rc_head != 0:
        return False
    rc_base, base_sha = _git(root, "rev-parse", f"{base}:{rel}")
    if rc_base != 0:
        return True          # absent on base, added here
    return head_sha != base_sha


def check(root: Path, base: str, branch: Optional[str]) -> tuple[str, list[str], list[str]]:
    """Return (state, failures, notes)."""
    fails: list[str] = []
    notes: list[str] = []

    if not branch:
        return ("not_a_pr", [], ["no branch to grade — nothing was checked here"])

    slug = branch_slug(branch)
    decl_rel = f"{LANDING_DIR}/{slug}.json"
    arm_rel = f"{AUTOMERGE_DIR}/{slug}.txt"

    changed = changed_paths(root, base)
    if changed is None:
        return ("not_a_pr", [],
                [f"could not diff against {base} — nothing was checked here"])

    armed = _added_or_modified(root, base, arm_rel)
    decl_path = root / decl_rel

    # ---------------------------------------------------------------- R11 / R10
    if not decl_path.exists():
        # R10 fires regardless of age: arming is an affirmative act done today.
        if armed:
            return ("undeclared", [
                f"R10 this branch ARMS auto-merge ({arm_rel} is added/modified) "
                f"but carries no landing declaration at {decl_rel}. Arming asks "
                f"GitHub to merge this PR with no human in the loop; the tier "
                f"that permits that is declared, or it is not permitted. "
                f"Write {decl_rel} (see {LANDING_DIR}/README.md)."], notes)
        knew = guard_existed_at_merge_base(root, base)
        if knew is False:
            return ("undeclared_predates_guard", [], [
                f"NO DECLARATION at {decl_rel}, and {GUARD_REL} did not exist at "
                f"this branch's merge-base — the branch was cut before the rule, "
                f"so it is not failed for it. This is a PASS on age, NOT a "
                f"finding that the PR is fine. Merge `main` and declare."])
        if knew is None:
            return ("undeclared_predates_guard", [], [
                f"NO DECLARATION at {decl_rel}, and the merge-base could not be "
                f"read, so whether this branch could have known is UNKNOWN — not "
                f"established as either. Passing on the unreadable case rather "
                f"than failing a branch we did not establish anything about."])
        return ("undeclared", [
            f"R11 no landing declaration at {decl_rel}. This branch was cut from "
            f"a `main` that already carried {GUARD_REL}, so the rule was "
            f"available to it. Every PR states its tier and how it means to "
            f"land; see {LANDING_DIR}/README.md for the four-line file."], notes)

    # ---------------------------------------------------------------- parse
    try:
        decl = json.loads(decl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ("undeclared", [f"R0 {decl_rel} is unreadable/invalid JSON: {exc}"], notes)
    if not isinstance(decl, dict):
        return ("undeclared", [f"R0 {decl_rel} must be a JSON object."], notes)

    tier = decl.get("tier")
    landing = decl.get("landing")
    why = str(decl.get("why") or "").strip()

    # R1 / R2 / R3
    if tier not in (1, 2, 3):
        fails.append(f"R1 {decl_rel} `tier` is {tier!r}; must be 1, 2 or 3.")
    if landing not in ("self", "hold"):
        fails.append(f"R2 {decl_rel} `landing` is {landing!r}; must be "
                     f'"self" (lands itself on green) or "hold" (a human merges).')
    if len(why) < MIN_TEXT:
        fails.append(
            f"R3 {decl_rel} `why` is {len(why)} chars; needs at least {MIN_TEXT}. "
            f"It is the tier judgement in the author's own words — the part no "
            f"path list can make for them, and the part a reviewer reads.")
    if fails:
        return ("undeclared", fails, notes)

    # ---------------------------------------------------------------- diff floor
    barred3 = [p for p in changed if _match(p, TIER3_PATHS)]
    barred2 = [p for p in changed if _match(p, TIER2_PATHS) and p not in barred3]
    unvouched = [p for p in changed
                 if not _match(p, TIER1_SURFACE) and p not in barred3 and p not in barred2]

    if barred3:
        notes.append(f"diff touches {len(barred3)} path(s) named Tier-3: "
                     + ", ".join(sorted(barred3)[:5]))
    if barred2:
        notes.append(f"diff touches {len(barred2)} path(s) named Tier-2: "
                     + ", ".join(sorted(barred2)[:5]))

    if landing == "self":
        # R4
        if tier != 1:
            fails.append(
                f"R4 {decl_rel} declares tier {tier} with `landing: \"self\"`. "
                f"Only Tier-1 lands without a human — Tier-2 needs an operator OK "
                f"and Tier-3 explicit approval (docs/CLAUDE-RULES-CANONICAL.md "
                f"§ Permission Tiers). Set `landing: \"hold\"`.")
        # R5
        if barred3 or barred2 or unvouched:
            bits = []
            if barred3:
                bits.append("Tier-3 by name: " + ", ".join(sorted(barred3)[:5]))
            if barred2:
                bits.append("Tier-2 by name: " + ", ".join(sorted(barred2)[:5]))
            if unvouched:
                bits.append("outside the Tier-1 surface: "
                            + ", ".join(sorted(unvouched)[:5]))
            fails.append(
                f"R5 {decl_rel} declares tier 1 and asks to self-land, but the "
                f"diff contains paths this guard cannot vouch for — "
                + "; ".join(bits)
                + ". A path outside TIER1_SURFACE is not thereby dangerous; it is "
                  "one the guard cannot certify, and self-landing is refused on it "
                  "rather than granted by default. Either narrow the PR, or set "
                  "`landing: \"hold\"` and let a human read it.")
        # R12 — a change to the landing route may not land itself by that route.
        machinery = [p for p in changed
                     if _match(p, LANDING_MACHINERY) and p != decl_rel]
        if machinery:
            fails.append(
                f"R12 {decl_rel} asks to self-land, but the diff CHANGES THE "
                f"LANDING MACHINERY itself — "
                + ", ".join(sorted(machinery)[:5])
                + ". A PR that edits the rules by which PRs land must not be "
                  "merged by those rules unread: the change and its own approval "
                  "would be the same act, and a mistake in it disarms the check "
                  "that would have caught the mistake. This is Tier-1 work and "
                  "the tier is not the objection — set `landing: \"hold\"` with "
                  "`hold_reason: \"changes_landing_machinery\"` and let a human "
                  "read it once.")
        # R6
        if not armed:
            fails.append(
                f"R6 {decl_rel} says `landing: \"self\"` but this branch has not "
                f"armed the route: {arm_rel} is absent or unchanged from `{base}`. "
                f"A readiness declaration lands nothing on its own — that is "
                f"precisely how 7 green PRs sat unmerged on 2026-09-03. Add "
                f"{arm_rel} (any contents; its PATH is the signal) and push. "
                f"The PR must also be OPEN AND NOT A DRAFT — `claude-pr-automerge` "
                f"refuses to un-draft a PR it did not itself open, by design.")
    else:  # landing == "hold"
        # R10 — the bite.
        if armed:
            fails.append(
                f"R10 this branch ARMS auto-merge ({arm_rel} is added/modified) "
                f"while {decl_rel} declares `landing: \"hold\"`"
                + (f" at tier {tier}" if tier != 1 else "")
                + ". Those are opposite instructions and the arming is the one "
                  "that would take effect. Remove the request file, or declare "
                  "tier 1 with `landing: \"self\"` and satisfy R5.")
        # R7
        reason = str(decl.get("hold_reason") or "").strip()
        text = str(decl.get("hold_text") or "").strip()
        if reason not in HOLD_REASONS:
            fails.append(
                f"R7 {decl_rel} `hold_reason` is {reason!r}; must be one of: "
                + ", ".join(sorted(HOLD_REASONS))
                + ". A closed vocabulary, so a hold is a stated kind of hold and "
                  "not an adjective — the `operator_decision` discipline in "
                  "docs/claude/work/OPEN-PRS.json.")
        if len(text) < MIN_TEXT:
            fails.append(
                f"R7 {decl_rel} `hold_text` is {len(text)} chars; needs at least "
                f"{MIN_TEXT}. The typed reason stays checkable against the "
                f"author's own wording only if the wording is there.")
        # R8 — verified, not presence-only.
        if reason == "changes_landing_machinery":
            machinery = [p for p in changed
                         if _match(p, LANDING_MACHINERY) and p != decl_rel]
            if not machinery:
                fails.append(
                    f"R8 {decl_rel} claims `changes_landing_machinery`, but no "
                    f"changed path is landing machinery. Recognised: "
                    + ", ".join(LANDING_MACHINERY)
                    + f". (This branch's own {decl_rel} is deliberately EXCLUDED "
                      "— every branch writes one, so counting it would make this "
                      "excuse free for everybody, which is the presence-only "
                      "marker `new-table-wiring-guard` was bitten by.)")
            else:
                notes.append(f"R8 verified — landing machinery in the diff: "
                             + ", ".join(sorted(machinery)[:5]))
        # R9
        if reason == "depends_on_unmerged_pr" and not re.search(r"#\d+", text):
            fails.append(
                f"R9 {decl_rel} claims `depends_on_unmerged_pr` but `hold_text` "
                f"names no PR (`#123`). A dependency nobody can look up is not a "
                f"dependency a reader can clear.")

    if fails:
        return ("undeclared" if landing == "self" else "declared_hold", fails, notes)
    if landing == "self":
        return ("declared_self_land", [], notes)
    return ("declared_needs_approval" if tier != 1 else "declared_hold", [], notes)


# ---------------------------------------------------------------------------
# self-test: plant each defect, prove the guard FAILS on it.
# ---------------------------------------------------------------------------

def _sandbox(tmp: Path, *, tier1_only: bool = True, with_guard_at_base: bool = True) -> Path:
    """A real git repo with a `main` and a branch, so merge-base logic is exercised."""
    root = tmp / "repo"
    root.mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)

    (root / "docs").mkdir()
    (root / "docs/seed.md").write_text("seed\n", encoding="utf-8")
    if with_guard_at_base:
        (root / "scripts/ci").mkdir(parents=True)
        (root / GUARD_REL).write_text("# the guard\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)

    subprocess.run(["git", "-C", str(root), "checkout", "-qb", "claude/demo"], check=True)
    (root / "docs/change.md").write_text("a documentation change\n", encoding="utf-8")
    if not tier1_only:
        (root / "config").mkdir(exist_ok=True)
        (root / "config/strategies.yaml").write_text("x: 1\n", encoding="utf-8")
    (root / LANDING_DIR).mkdir(parents=True, exist_ok=True)
    return root


def _declare(root: Path, **fields) -> None:
    (root / LANDING_DIR).mkdir(parents=True, exist_ok=True)
    (root / f"{LANDING_DIR}/demo.json").write_text(
        json.dumps(fields, indent=2) + "\n", encoding="utf-8")


def _arm(root: Path) -> None:
    (root / AUTOMERGE_DIR).mkdir(parents=True, exist_ok=True)
    (root / f"{AUTOMERGE_DIR}/demo.txt").write_text("land it\n", encoding="utf-8")


def _commit(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "work"], check=True)


_GOOD_WHY = "documentation-only change to the landing route contract"


def self_test() -> int:
    # ---- positive controls: the shapes that MUST pass ----------------------
    positives = {
        "tier-1 armed self-land on a docs-only diff": (
            lambda r: (_declare(r, tier=1, landing="self", why=_GOOD_WHY), _arm(r)),
            True, "declared_self_land"),
        "tier-3 diff held with a typed reason": (
            lambda r: _declare(r, tier=3, landing="hold",
                               hold_reason="tier_2_3_needs_approval",
                               hold_text="touches config/strategies.yaml; needs "
                                         "explicit operator approval before merge",
                               why="a strategy parameter change, Tier-3 by path"),
            False, "declared_needs_approval"),
        "verified changes_landing_machinery hold": (
            lambda r: ((r / "scripts/ci").mkdir(parents=True, exist_ok=True),
                       (r / "scripts/ci/check_automerge_trigger.py").write_text(
                           "x\n", encoding="utf-8"),
                       _declare(r, tier=1, landing="hold",
                                hold_reason="changes_landing_machinery",
                                hold_text="edits the automerge guard itself, so a "
                                          "human should read it before it lands",
                                why="changes the landing machinery")),
            True, "declared_hold"),
    }
    bad = 0
    for name, (setup, tier1_only, want) in positives.items():
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(Path(td), tier1_only=tier1_only)
            setup(root)
            _commit(root)
            state, fails, _ = check(root, "main", "claude/demo")
            if fails or state != want:
                print(f"::error::self-test FAILED — positive control '{name}' did "
                      f"not pass cleanly (state={state}, fails={fails}). A guard "
                      f"that fails correct work is worse than none.")
                bad += 1
            else:
                print(f"self-test: positive control '{name}' passes (state={state})")

    # ---- the escape hatch, and the hole it must NOT open -------------------
    with tempfile.TemporaryDirectory() as td:
        root = _sandbox(Path(td), with_guard_at_base=False)
        _commit(root)
        state, fails, _ = check(root, "main", "claude/demo")
        if fails or state != "undeclared_predates_guard":
            print(f"::error::self-test FAILED — a branch cut before the guard "
                  f"existed should PASS undeclared (state={state}, fails={fails}).")
            bad += 1
        else:
            print("self-test: pre-guard branch passes undeclared, loudly")

    with tempfile.TemporaryDirectory() as td:
        root = _sandbox(Path(td), with_guard_at_base=False)
        _arm(root)
        _commit(root)
        state, fails, _ = check(root, "main", "claude/demo")
        if not fails:
            print("::error::self-test FAILED — a branch that ARMS auto-merge with "
                  "no declaration passed because it predates the guard. Age "
                  "excuses not knowing the rule; it must not excuse asking to "
                  "merge without approval.")
            bad += 1
        else:
            print("self-test: arming without a declaration fails even when the "
                  "branch predates the guard (R10 is not grandfathered)")

    # ---- planted defects: each MUST fail -----------------------------------
    plants = {
        "R10 armed while declaring hold": (
            lambda r: (_declare(r, tier=1, landing="hold",
                                hold_reason="operator_asked_to_hold",
                                hold_text="the operator asked to hold this one back",
                                why=_GOOD_WHY), _arm(r)), True),
        "R4 tier-2 asking to self-land": (
            lambda r: (_declare(r, tier=2, landing="self", why=_GOOD_WHY), _arm(r)), True),
        "R5 tier-1 self-land over a Tier-3 path": (
            lambda r: (_declare(r, tier=1, landing="self", why=_GOOD_WHY), _arm(r)), False),
        "R6 self-land declared but route not armed": (
            lambda r: _declare(r, tier=1, landing="self", why=_GOOD_WHY), True),
        "R3 a one-word `why`": (
            lambda r: (_declare(r, tier=1, landing="self", why="x"), _arm(r)), True),
        "R7 hold_reason outside the vocabulary": (
            lambda r: _declare(r, tier=1, landing="hold", hold_reason="because",
                               hold_text="a perfectly reasonable sounding excuse",
                               why=_GOOD_WHY), True),
        "R8 unverified changes_landing_machinery": (
            lambda r: _declare(r, tier=1, landing="hold",
                               hold_reason="changes_landing_machinery",
                               hold_text="claims to touch the landing route but "
                                         "the diff is a docs file",
                               why=_GOOD_WHY), True),
        "R9 depends_on_unmerged_pr naming no PR": (
            lambda r: _declare(r, tier=1, landing="hold",
                               hold_reason="depends_on_unmerged_pr",
                               hold_text="waiting on the other one to land first",
                               why=_GOOD_WHY), True),
        "R12 self-landing a change to the landing machinery": (
            lambda r: ((r / "scripts/ci").mkdir(parents=True, exist_ok=True),
                       (r / "scripts/ci/check_automerge_trigger.py").write_text(
                           "edited\n", encoding="utf-8"),
                       _declare(r, tier=1, landing="self", why=_GOOD_WHY),
                       _arm(r)), True),
        "R11 no declaration on a branch that could have known": (
            lambda r: None, True),
        "R1 a tier outside 1-3": (
            lambda r: _declare(r, tier=0, landing="self", why=_GOOD_WHY), True),
        "R0 unparseable declaration": (
            lambda r: (r / LANDING_DIR / "demo.json").write_text("{nope", encoding="utf-8"),
            True),
    }
    for name, (plant, tier1_only) in plants.items():
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(Path(td), tier1_only=tier1_only)
            plant(root)
            _commit(root)
            _, fails, _ = check(root, "main", "claude/demo")
            if not fails:
                print(f"::error::self-test FAILED — planted '{name}' and the guard "
                      f"still passed. Its failure path is broken, so a green from "
                      f"it means nothing.")
                bad += 1
            else:
                print(f"self-test: '{name}' correctly caught")

    if bad:
        return 1
    print(f"self-test OK — {len(positives) + 2} positive controls hold and all "
          f"{len(plants)} planted defects fail the guard")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main",
                    help="base ref to diff against (default origin/main)")
    ap.add_argument("--branch", default=None,
                    help="override the branch name (default: GITHUB_HEAD_REF or git)")
    ap.add_argument("--self-test", action="store_true",
                    help="plant each defect and prove the guard fails on it")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    branch = args.branch or current_branch(REPO)
    state, fails, notes = check(REPO, args.base, branch)

    for n in notes:
        print(f"  note: {n}")
    if fails:
        print(f"::error::pr-landing-guard FAILED (state={state})")
        for f in fails:
            print(f"  - {f}")
        return 1
    if state == "not_a_pr":
        print("pr-landing: SKIPPED — not a PR context, so nothing was graded. "
              "This is not a pass.")
        return 0
    if state == "undeclared_predates_guard":
        print("pr-landing: PASSED ON AGE — this branch predates the rule and was "
              "not graded against it. This is not a clean bill of health.")
        return 0
    print(f"pr-landing: OK — state={state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
