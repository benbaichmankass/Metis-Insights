#!/usr/bin/env python3
"""The ONE route by which a PR lands ITSELF while touching a real-money roster.

Operator decision, 2026-09-25 ("Build auto-land route"), against pipeline item
`PI-20260925-WCUZRAA3-0002`: *"No landing route exists for a MANDATE-AUTHORIZED
roster edit … so the cut still waits on a human merge -- the ladder is 'decided
+ proposed automatically, landed by hand'."*

B3 (#12963) made the DECISION automatic: on a `mandate_resolver` FIRE under the
granted `MD-DEMOTE-S2-S1` (`direction: derisk_only`),
`.github/workflows/r4-demotion-gate.yml` pushes `automation/r4-demotion-{run}`,
writes the evidence, opens a PR and pings the operator. What it could not do is
LAND it, because `check_pr_landing.py` R4 says TIER-3 NEVER SELF-LANDS and a
Stage-2 roster edit is Tier-3 by `TIER3_PATHS`.

⚠️ **R4 IS NOT WEAKENED AND MUST NOT BE.** R4's rule is that *a change and its
own approval must not be the same act*. R15 already replaced that rule's PROXY
("a human clicks Merge") with the property itself for Tier-2, via an approval
record the branch demonstrably could not have written. This module does the same
thing for exactly one Tier-3 shape, and the separation it rests on is STRONGER
than R15's, not weaker:

  * the authorization is `config/mandates.yaml`, which the operator grants and
    which this diff MAY NOT CONTAIN (A1 refuses the path outright, and A4 reads
    the grant from the MERGE-BASE blob regardless);
  * the evidence is a committed record the RESOLVER re-reads here, from the
    merge-base configs, and must independently FIRE on (A5);
  * the author is the workflow, not a session (A7);
  * and the edit may only ever REMOVE (A2). The worst case of a wrong verdict is
    less exposure, never more.

⚠️ **AND IT IS NOT ARMED BY EXISTING.** A8 requires `autoland: true` on the
GRANTED mandate entry. No entry in `config/mandates.yaml` carries it as this
lands. Adding it is the OPERATOR's act, exactly as clearing
`MD-PROMOTE-S1-S2`'s `blocked_until` is — a session writing its own arming
switch is the same failure class as a session writing its own authorization.
Until then every clause below can pass and the route still refuses, loudly, at
A8. **Merged is not armed is not observed.**

THE EIGHT CLAUSES
-----------------
Each is checked against the DIFF and the two git trees, never against the PR
body, a commit message or a claim in the declaration. Every one of them must
hold; there is no override and no marker that buys a pass.

  A1 PATHS      every changed path is on a closed allowlist: `config/accounts.yaml`,
                the mirror-window records and their source runs, the firing
                records, and this branch's OWN landing declaration and merge-slot
                claim. `config/mandates.yaml`, `config/strategies.yaml`, `src/**`,
                a test, a doc — anything at all else — refuses.
  A2 CUT-ONLY   `config/accounts.yaml` changes by REMOVALS FROM `strategies:`
                ROSTERS AND BY NOTHING ELSE. Proven by reconstruction: filter the
                removed legs out of the MERGE-BASE document and require the result
                to equal the HEAD document exactly. An addition, a reorder, a
                `mode:` flip, a `risk_pct` edit, a new `symbols:` entry — each
                makes the reconstruction differ and refuses.
  A3 EVIDENCE   every removed leg carries a committed mirror-window record at HEAD
                and a firing record ADDED BY THIS DIFF that cites it, names a
                mandate, and whose `accounts` match the removal exactly.
  A4 GRANTED    that mandate is in the MERGE-BASE `mandates:` list (`proposed:`
                never authorizes), carries `granted_by`/`granted_at`, is not
                `blocked_until`, and its `direction` is `derisk_only`.
  A5 FIRE       the resolver, replayed over the MERGE-BASE configs plus this
                diff's evidence, returns FIRE for (leg, S2 -> S1, account) and
                proposes EXACTLY the removal this diff makes and no addition.
  A6 MIRROR     the live account and its mirror are cut together, so the
                strict-equality mirror tests stay green: for each pair, the legs
                removed from the live roster that WERE on the mirror are exactly
                the legs removed from the mirror.
  A7 AUTHOR     the branch is `automation/r4-demotion-<run_id>`, the declaration
                names that run and workflow, every commit in the range is authored
                AND committed by `github-actions[bot]`, and — the part a pushed
                branch cannot forge — GitHub's API says run <run_id> is a run of
                `.github/workflows/r4-demotion-gate.yml` on `main` in THIS repo,
                started by `schedule` or `workflow_dispatch`, whose `head_sha` is
                exactly the commit this branch was cut from (the merge-base).
                The PR opener's login is NOT trusted (operator re-decision
                JC-CA-03, 2026-09-28: "Keep PAT, verify run").
  A8 ARMED      the granted mandate carries `autoland: true`. The operator's
                switch; absent means refuse. ⚠️ CHECKED LAST, so an unarmed
                mandate still gets every other clause graded and reported —
                including the A5 replay. A refusal that measured nothing else
                would leave the clauses that do the authorizing exercised by
                nothing, since no mandate is armed today.

WHAT THIS DOES NOT ESTABLISH, SAID PLAINLY
------------------------------------------
A7 USED TO trust the PR opener's login, and that was forgeable both ways. It was
measured by lane JC-GIT on 2026-09-28 (PI-20260928-TURC5MJC-0004):
`r4-demotion-gate.yml` opens its PR with `BRANCH_PROTECTION_TOKEN`, so the
genuine author is the PAT OWNER, not the bot, and the route refused its own
producer. Meanwhile `pr-opener.yml` opens ANY session-pushed branch as
`github-actions[bot]`, so the bot login proved nothing. What A7 now checks is
the RUN: a session cannot mint a run of `r4-demotion-gate.yml` on `main`, and
the run's `head_sha` must be the exact commit the branch was cut from.

THE CONTENT IS BOUND TO THE RUN (review of #13609, 2026-09-28). After pushing,
and before opening the PR, the producer uploads the `r4-demotion-provenance`
artifact to ITS OWN run. The artifact records {run_id, branch, head_sha,
removals, window, event}. A7 lists that run's artifacts through the API and
requires:
  * head_sha and removals equal to the PR's;
  * event equal to the run's event;
  * window equal to the producer's DECLARED window, which is the `window`
    input default in `.github/workflows/r4-demotion-gate.yml` read at the base
    ref and never re-typed here. `config/mandates.yaml` MD-DEMOTE-S2-S1
    declares no window of its own ("over the window the record declares").
Only a job of that run can upload to it, and no REST upload path exists. So:
  * a replayed run id carries some other head;
  * a dry (apply!=true) dispatch uploads nothing;
  * a dispatch with a non-declared window (e.g. 1d) is refused.

⚠️ "UPDATE BRANCH" ON A PRODUCER PR PERMANENTLY REFUSES IT, AND THAT IS THE
POINT. Updating the branch adds a merge commit that the run did not push, so
the PR head no longer equals the provenance head_sha and the new commit is not
bot-authored. The route fails closed, and a human merges or the next run
supersedes it.

The residual that remains: a genuine scheduled run whose evidence is itself
wrong is bounded only by A1-A6 (removals only, a granted derisk_only mandate,
a resolver FIRE). This is the same shape as `_APPROVAL_RESIDUAL` in
`check_pr_landing.py`, and it travels with the grant for the same reason.

⚠️ A2 compares PARSED documents, so a pure COMMENT change in
`config/accounts.yaml` is invisible to it. That is deliberate: a comment cannot
change what trades, and `r4_demotion_gate.remove_from_roster` deliberately keeps
a removed line's trailing comment as a bare comment line so no prose is lost.
The thing A2 must catch is a behavioural edit smuggled alongside the cut, and a
parsed comparison catches every one of those.

Exit codes: 0 the diff is an admissible auto-land · 1 it is not · 2 could not run.
Run ``--self-test`` to plant each defect (and three positive controls) in a real
git repository and prove the verdicts.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parents[2]
for _p in (str(REPO), str(REPO / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

import mandate_resolver as mr  # noqa: E402

#: The `landing:` value this route answers to. `"self"` keeps its meaning
#: untouched — R4 still refuses Tier-3 there, and nothing below widens it.
LANDING_VALUE = "mandate"

ACCOUNTS_REL = mr.ACCOUNTS_REL
MANDATES_REL = mr.MANDATES_REL
MIRROR_DIR = mr.MIRROR_DIR_REL                       # comms/mandate_evidence/mirror_window
RUNS_DIR = f"{mr.MIRROR_DIR_REL}/runs"
FIRINGS_DIR = mr.FIRINGS_DIR_REL                     # comms/mandate_firings
LANDING_DIR = ".github/pr-landing"
SLOT_DIR = ".github/merge-slots"
WORKFLOW_REL = ".github/workflows/r4-demotion-gate.yml"

#: The only branch shape this route admits, and the only workflow that writes it.
BRANCH_RE = re.compile(r"^automation/r4-demotion-(\d+)$")
BOT_LOGIN = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
#: The events that legitimately start the producer: its own cron, or
#: schedule-keeper / an operator dispatching it.
RUN_EVENTS = ("schedule", "workflow_dispatch")
#: The artifact the producer run uploads AFTER pushing its branch and BEFORE
#: opening the PR (review BLOCK B2 on #13609). Only a job of run <run_id> can
#: upload an artifact to run <run_id>, and the run is verified to be the
#: reviewed producer on main. So the artifact is the one statement about the
#: PR's CONTENT that a pusher cannot write: the exact head SHA it pushed and the
#: exact legs it cut. A run dispatched with apply!=true never pushes and never
#: uploads, so its id buys nothing.
PROVENANCE_ARTIFACT = "r4-demotion-provenance"
PROVENANCE_FILE = "provenance.json"

#: The arming switch, read from the GRANTED mandate entry at the merge-base.
#: Absent or not exactly `True` refuses. See the module docstring.
ARM_FIELD = "autoland"

#: Stage-2 real-money accounts and their mirrors, taken from the resolver so the
#: two can never disagree about what Stage 2 is.
STAGE2_LIVE = tuple(a for a, s in mr.STAGE_OF_ACCOUNT.items() if s == "S2")
MIRROR_OF = dict(mr.MIRROR_OF)
STAGE2_ANY = set(STAGE2_LIVE) | set(MIRROR_OF.values())

_FIRING_RE = re.compile(r"^[0-9TZ]+-(?P<leg>.+)-demote\.json$")


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------
def _git(root: Path, *args: str) -> Tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return p.returncode, p.stdout


#: The commit every clause grades. NOT always "HEAD" (review BLOCK B1 on #13609):
#: under `pull_request`, actions/checkout puts GitHub's SYNTHETIC MERGE COMMIT at
#: HEAD. Its first parent is main's tip at sync time, not the commit the branch
#: was cut from, and it is authored by the PR opener, so grading HEAD made A7
#: refuse every genuine PR. `verdict()` resolves the PR head (`resolve_head`)
#: and sets this for the duration of one grade. Single-threaded CLI; the
#: previous value is always restored.
_GRADED = "HEAD"


def merge_base(root: Path, base: str) -> Optional[str]:
    rc, out = _git(root, "merge-base", base, _GRADED)
    return out.strip() or None if rc == 0 else None


def resolve_head(root: Path, event_path: Optional[str] = None) -> Tuple[str, str]:
    """(rev, how): the PR HEAD commit to grade, never GitHub's merge ref.

    1. `pull_request.head.sha` from the event payload, if that commit is present;
    2. else, on a pull_request event whose HEAD is a two-parent merge, `HEAD^2`;
    3. else `HEAD` (a local run, or a push/dispatch with no PR)."""
    path = event_path if event_path is not None else (os.environ.get("GITHUB_EVENT_PATH") or "")
    if path and Path(path).is_file():
        try:
            sha = ((json.loads(Path(path).read_text(encoding="utf-8")) or {})
                   .get("pull_request") or {}).get("head", {}).get("sha")
        except (OSError, ValueError, AttributeError):
            sha = None
        if isinstance(sha, str) and sha and _git(root, "cat-file", "-e", f"{sha}^{{commit}}")[0] == 0:
            return sha, "pull_request.head.sha from the event payload"
    if (os.environ.get("GITHUB_EVENT_NAME") or "").startswith("pull_request"):
        rc, out = _git(root, "rev-list", "--parents", "-n", "1", "HEAD")
        if rc == 0 and len(out.split()) == 3:
            return "HEAD^2", "second parent of GitHub's synthetic merge commit"
    return "HEAD", "HEAD (no pull_request head to resolve)"


def blob(root: Path, rev: str, rel: str) -> Optional[str]:
    rc, out = _git(root, "show", f"{rev}:{rel}")
    return out if rc == 0 else None


def _yaml_text(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if text is None:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _json_text(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if text is None:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _rosters(doc: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    accounts = (doc or {}).get("accounts") or {}
    out: Dict[str, List[str]] = {}
    for name, cfg in accounts.items():
        legs = (cfg or {}).get("strategies")
        out[str(name)] = [str(x) for x in legs] if isinstance(legs, list) else []
    return out


# --------------------------------------------------------------------------
# A1 — the path allowlist
# --------------------------------------------------------------------------
def allowed_paths(slug: str) -> Dict[str, str]:
    """`{description: glob}` — a CLOSED allowlist, keyed to THIS branch's slug."""
    return {
        "the roster file": ACCOUNTS_REL,
        "a mirror-window evidence record": f"{MIRROR_DIR}/*.json",
        "a mirror-window source run": f"{RUNS_DIR}/*.json",
        "a mandate firing record": f"{FIRINGS_DIR}/*.json",
        "this branch's landing declaration": f"{LANDING_DIR}/{slug}.json",
        "this branch's merge-slot claim": f"{SLOT_DIR}/{slug}.json",
    }


def _path_ok(path: str, slug: str) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(path, g) for g in allowed_paths(slug).values())


def check_paths(changed: List[str], slug: str) -> Tuple[List[str], List[str]]:
    fails, notes = [], []
    stray = sorted(p for p in changed if not _path_ok(p, slug))
    if stray:
        fails.append(
            "A1 the diff touches path(s) this route does not admit: "
            + ", ".join(stray[:8])
            + ". An auto-landing roster cut carries the cut, its evidence and its own "
              "landing paperwork and NOTHING else — most sharply, it may not contain "
              f"`{MANDATES_REL}`, because a PR that grants its own authorization is the "
              "one thing no mandate may ever buy. Allowed: "
            + ", ".join(sorted(allowed_paths(slug).values())) + ".")
    if ACCOUNTS_REL not in changed:
        fails.append(f"A1 the diff does not touch {ACCOUNTS_REL}. This route exists to land "
                     f"a roster CUT; a diff that cuts nothing has nothing to auto-land.")
    else:
        notes.append(f"A1 diff confined to the allowlist ({len(changed)} path(s))")
    return fails, notes


# --------------------------------------------------------------------------
# A2 — removals from `strategies:` rosters and nothing else
# --------------------------------------------------------------------------
def roster_removals(base_doc: Optional[Dict[str, Any]],
                    head_doc: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """`{account: [legs removed]}`, order-preserving, from the two parsed docs."""
    base_r, head_r = _rosters(base_doc), _rosters(head_doc)
    out: Dict[str, List[str]] = {}
    for acct, legs in base_r.items():
        gone = [lg for lg in legs if lg not in set(head_r.get(acct, legs))]
        if gone:
            out[acct] = gone
    return out


def check_cut_only(root: Path, mb: str) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    fails, notes = [], []
    base_doc = _yaml_text(blob(root, mb, ACCOUNTS_REL))
    head_doc = _yaml_text(blob(root, _GRADED, ACCOUNTS_REL))
    if base_doc is None or head_doc is None:
        return ([f"A2 {ACCOUNTS_REL} could not be parsed at "
                 f"{'the merge-base' if base_doc is None else 'HEAD'} — that is *we could "
                 f"not look*, never *the diff is a clean cut*. Refusing."], notes, {})

    removals = roster_removals(base_doc, head_doc)
    if not removals:
        fails.append("A2 no leg was removed from any `strategies:` roster. This route lands "
                     "cuts; there is nothing here to land.")

    # The reconstruction. Filtering the removed legs out of the merge-base document
    # must reproduce HEAD byte-for-byte AS PARSED — so an addition, a reorder, a
    # `mode:` flip or any other key edit shows up as a difference and refuses.
    expected = copy.deepcopy(base_doc)
    for acct, gone in removals.items():
        cfg = (expected.get("accounts") or {}).get(acct) or {}
        cfg["strategies"] = [lg for lg in (cfg.get("strategies") or []) if lg not in set(gone)]
    if expected != head_doc:
        fails.append(
            f"A2 {ACCOUNTS_REL} changes by MORE than roster removals. Filtering the removed "
            f"legs out of the merge-base document does not reproduce HEAD, so this diff "
            f"adds a leg, reorders one, or edits another key (`mode:`, `risk_pct:`, "
            f"`symbols:`, an account block). Only a pure removal auto-lands; everything "
            f"else is `landing: \"hold\"`.")

    off_ladder = sorted(a for a in removals if a not in STAGE2_ANY)
    if off_ladder:
        fails.append(f"A2 removals touch account(s) that are not Stage-2 or a Stage-2 mirror: "
                     f"{', '.join(off_ladder)}. MD-DEMOTE-S2-S1 cuts Stage 2; a Stage-1 or "
                     f"prop roster edit is not this route's to land.")
    if not fails:
        notes.append("A2 pure roster removal: "
                     + "; ".join(f"{a} -{','.join(v)}" for a, v in sorted(removals.items())))
    return fails, notes, removals


# --------------------------------------------------------------------------
# A6 — the live account and its mirror are cut together
# --------------------------------------------------------------------------
def check_mirror(root: Path, mb: str, removals: Dict[str, List[str]]) -> Tuple[List[str], List[str]]:
    fails, notes = [], []
    base_r = _rosters(_yaml_text(blob(root, mb, ACCOUNTS_REL)))
    for live, mirror in sorted(MIRROR_OF.items()):
        cut_live = set(removals.get(live, []))
        cut_mirror = set(removals.get(mirror, []))
        on_mirror = set(base_r.get(mirror, []))
        # RELATIVE, not absolute: whatever relationship the two rosters had at the
        # merge-base (strict equality on Bybit, equality-minus-the-two-declared
        # proxies on Alpaca) is PRESERVED by cutting exactly the shared legs.
        want = cut_live & on_mirror
        if want != cut_mirror:
            missing = sorted(want - cut_mirror)
            extra = sorted(cut_mirror - want)
            bits = []
            if missing:
                bits.append(f"cut from {live} but NOT from {mirror}: {', '.join(missing)}")
            if extra:
                bits.append(f"cut from {mirror} but NOT from {live}: {', '.join(extra)}")
            fails.append(
                f"A6 {live} and {mirror} are not cut together — " + "; ".join(bits)
                + ". Stage 2 is ONE stage: the mirror carries the identical roster and "
                  "`tests/test_paper_portfolio_accounts.py` asserts it by strict ORDERED "
                  "equality. A one-sided cut lands a red `main`, and on the live-only side "
                  "it also destroys the very demotion signal Gate 2 reads.")
        elif cut_live or cut_mirror:
            notes.append(f"A6 {live}/{mirror} cut together: {', '.join(sorted(want)) or '—'}")
    return fails, notes


# --------------------------------------------------------------------------
# A3 / A4 / A5 / A8 — evidence, the grant, the replayed FIRE, the arming switch
# --------------------------------------------------------------------------
def _firings_added(root: Path, mb: str, changed: List[str]) -> Dict[str, List[str]]:
    """`{leg: [rel]}` for firing records ADDED by this diff (absent at merge-base)."""
    out: Dict[str, List[str]] = {}
    for rel in changed:
        if not rel.startswith(f"{FIRINGS_DIR}/"):
            continue
        if blob(root, mb, rel) is not None:
            continue                      # not new — a firing is by definition new
        m = _FIRING_RE.match(Path(rel).name)
        if m:
            out.setdefault(m.group("leg"), []).append(rel)
        else:
            out.setdefault("", []).append(rel)
    return out


def _scratch_tree(root: Path, mb: str, leg: str, record: Dict[str, Any]) -> Optional[Path]:
    """MERGE-BASE configs + THIS DIFF's evidence, so the resolver is replayed
    against the pre-cut rosters and the operator's own grant."""
    tmp = Path(tempfile.mkdtemp(prefix="mandate-autoland-"))
    for rel in (MANDATES_REL, ACCOUNTS_REL, mr.STRATEGIES_REL):
        text = blob(root, mb, rel)
        if text is None:
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text(text, encoding="utf-8")
    rec_rel = f"{MIRROR_DIR}/{leg}.json"
    (tmp / rec_rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp / rec_rel).write_text(json.dumps(record), encoding="utf-8")
    # MD-DEMOTE-S2-S1's T3 threshold (operator 2026-09-29) is re-derived by the
    # resolver from the leg's Stage-0 evidence record and ITS source_run -- read
    # at the MERGE-BASE, like the configs, so a PR cannot plant the evidence its
    # own threshold is measured against. Absent there, the replay reads nothing
    # and the resolver answers NEEDS-DATA, which refuses.
    ev_rel = f"{mr.EVIDENCE_DIR_REL}/{leg}.json"
    ev_text = blob(root, mb, ev_rel)
    if ev_text is not None:
        (tmp / ev_rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / ev_rel).write_text(ev_text, encoding="utf-8")
        ev_src = (_json_text(ev_text) or {}).get("source_run")
        if isinstance(ev_src, str) and ev_src.strip() and ".." not in Path(ev_src).parts \
                and not ev_src.startswith("/"):
            src_text = blob(root, mb, ev_src)
            if src_text is not None:
                (tmp / ev_src).parent.mkdir(parents=True, exist_ok=True)
                (tmp / ev_src).write_text(src_text, encoding="utf-8")
    src = record.get("source_run")
    if isinstance(src, str) and src.strip() and ".." not in Path(src).parts \
            and not src.startswith("/"):
        text = blob(root, _GRADED, src)
        if text is not None:
            (tmp / src).parent.mkdir(parents=True, exist_ok=True)
            (tmp / src).write_text(text, encoding="utf-8")
    return tmp


def check_evidence(root: Path, mb: str, changed: List[str],
                   removals: Dict[str, List[str]]) -> Tuple[List[str], List[str]]:
    fails, notes = [], []
    mdoc = _yaml_text(blob(root, mb, MANDATES_REL))
    if mdoc is None:
        return ([f"A4 {MANDATES_REL} is absent or unparseable at the merge-base — that is "
                 f"*we could not look*, so NOTHING is authorized. Refusing."], notes)

    legs: Set[str] = {lg for v in removals.values() for lg in v}
    firings = _firings_added(root, mb, changed)

    unmatched = sorted(set(firings) - legs)
    if unmatched:
        fails.append(f"A3 firing record(s) added for leg(s) this diff does not cut: "
                     f"{', '.join(x or '<unparseable filename>' for x in unmatched)}. A firing "
                     f"record is the receipt for an edit; one with no edit behind it is not a "
                     f"receipt.")

    for leg in sorted(legs):
        accts = sorted(a for a, v in removals.items() if leg in v)
        live = [a for a in accts if a in STAGE2_LIVE]
        # Per-leg, so an unrelated leg's defect can never suppress THIS leg's
        # resolver replay -- the check that actually decides.
        leg_fails: List[str] = []

        rec_rel = f"{MIRROR_DIR}/{leg}.json"
        record = _json_text(blob(root, _GRADED, rec_rel))
        if record is None:
            leg_fails.append(f"A3 {leg}: no readable committed mirror-window record at {rec_rel} "
                             f"at HEAD. 'We could not look' is not a demotion signal.")
            fails += leg_fails
            continue

        rels = firings.get(leg) or []
        if len(rels) != 1:
            leg_fails.append(f"A3 {leg}: this diff adds {len(rels)} firing record(s) under "
                             f"{FIRINGS_DIR}/ matching `*-{leg}-demote.json`; expected exactly 1. "
                             f"A cut without its firing record is an unrecorded mandate action, "
                             f"and section 1 of the daily brief reads those records.")
            fails += leg_fails
            continue
        firing = _json_text(blob(root, _GRADED, rels[0]))
        if firing is None:
            leg_fails.append(f"A3 {leg}: {rels[0]} is not readable JSON at HEAD.")
            fails += leg_fails
            continue

        if firing.get("action") != "remove":
            leg_fails.append(f"A3 {leg}: {rels[0]} declares action={firing.get('action')!r}; this "
                             f"route lands removals only.")
        if sorted(firing.get("accounts") or []) != accts:
            leg_fails.append(f"A3 {leg}: {rels[0]} names accounts {sorted(firing.get('accounts') or [])} "
                             f"but the diff cuts it from {accts}. The receipt must match the edit.")
        if firing.get("evidence") != rec_rel:
            leg_fails.append(f"A3 {leg}: {rels[0]} cites evidence {firing.get('evidence')!r}, not "
                             f"{rec_rel!r}.")
        src = record.get("source_run")
        if not isinstance(src, str) or blob(root, _GRADED, src) is None:
            leg_fails.append(f"A3 {leg}: the record's source_run {src!r} is not a file in this tree "
                             f"at HEAD. A measurement nobody can reach is not a measurement.")

        mid = firing.get("mandate")
        m = mr.granted(mdoc, mid) if isinstance(mid, str) else None
        if m is None:
            leg_fails.append(f"A4 {leg}: {mid!r} is not in the MERGE-BASE `mandates:` list of "
                             f"{MANDATES_REL} (`proposed:` never authorizes, and a grant this PR "
                             f"wrote would not be at the merge-base). Refusing.")
            fails += leg_fails
            continue
        if not m.get("granted_by") or not m.get("granted_at"):
            leg_fails.append(f"A4 {leg}: {mid} lacks granted_by/granted_at.")
        if m.get("blocked_until"):
            leg_fails.append(f"A4 {leg}: {mid} is blocked_until: {str(m['blocked_until'])[:120]}")
        if str(m.get("direction") or "").strip() != "derisk_only":
            leg_fails.append(
                f"A4 {leg}: {mid} direction={m.get('direction')!r}, not 'derisk_only'. Only a "
                f"mandate that can ONLY remove exposure may land itself — an `add_risk` "
                f"mandate's worst case is more real money on a wrong verdict, and that stays "
                f"with a human whatever its evidence says.")
        if len(live) != 1:
            leg_fails.append(f"A5 {leg}: the cut names {len(live)} Stage-2 real-money account(s) "
                             f"({accts}); expected exactly one of {list(STAGE2_LIVE)}.")
        if leg_fails:
            # A5 replays the resolver over the merge-base configs. Doing so on a
            # record that already failed A3/A4 would report a SECOND, derived
            # failure for the same defect; the first one is the finding.
            fails += leg_fails
            continue

        tmp = _scratch_tree(root, mb, leg, record)
        if tmp is None:
            fails.append(f"A5 {leg}: the merge-base config trio could not be reconstructed, so "
                         f"the resolver could not be replayed. Refusing rather than assuming.")
            continue
        try:
            res = mr.resolve(leg, "S2", "S1", live[0], root=tmp, mandate_id=mid)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if res["verdict"] != mr.FIRE:
            fails.append(
                f"A5 {leg}: replaying `mandate_resolver` over the MERGE-BASE configs and this "
                f"diff's evidence REFUSES — [{res['clause']}] {res['detail']}. The decision is "
                f"the resolver's, and this route lands nothing the resolver does not re-affirm "
                f"here, on its own reading of the committed record.")
            continue
        prop = res.get("proposal") or {}
        if prop.get("roster_add"):
            fails.append(f"A5 {leg}: the resolver's proposal ADDS to {sorted(prop['roster_add'])}. "
                         f"This route may only remove.")
        proposed = sorted(prop.get("roster_remove") or {})
        if proposed != accts:
            fails.append(f"A5 {leg}: the resolver proposes removing from {proposed} but the diff "
                         f"removes from {accts}. The edit must be exactly what was authorized — "
                         f"no more, and no less.")
        else:
            notes.append(f"A5 {leg}: resolver FIRE under {mid}, proposal matches the diff "
                         f"({', '.join(accts)})")

        # ── A8, THE ARMING SWITCH — CHECKED LAST, ON PURPOSE ───────────────
        # ⚠️ IT MUST NOT SHORT-CIRCUIT A5, AND AN EARLIER DRAFT OF THIS FILE
        # DID. Caught by this route's own end-to-end test
        # (`tests/test_mandate_autoland.py`) rather than by reading: with A8
        # grouped among the evidence clauses, an UNARMED mandate — which is
        # every mandate today — skipped the resolver replay entirely, so the
        # clause that does the actual authorizing was exercised by nothing in
        # production and the workflow's own pre-flight could never report what
        # the resolver said. A guard that reports "not armed" having measured
        # nothing else is the "green while measuring nothing" shape wearing a
        # red hat. Order matters: measure first, then say whether you may act.
        if m.get(ARM_FIELD) is not True:
            fails.append(
                f"A8 {leg}: {mid} does not carry `{ARM_FIELD}: true` in {MANDATES_REL} at the "
                f"merge-base, so the auto-land route is BUILT BUT NOT ARMED. Every other "
                f"clause above has been graded and is reported beside this one, so what is "
                f"missing is the AUTHORIZATION, not the evidence. Arming it is the OPERATOR's "
                f"act — the same asymmetry that makes granting a mandate, and clearing "
                f"MD-PROMOTE-S1-S2's `blocked_until`, theirs and not a session's. A session "
                f"that adds this field to authorize its own next merge has written its own "
                f"authorization.")
    return fails, notes


# --------------------------------------------------------------------------
# A7 — authored by the workflow, not by a session
# --------------------------------------------------------------------------
def pr_author_login(event_path: Optional[str] = None) -> Tuple[Optional[str], str]:
    """(login, how). `None` means we could not look — never 'it is the bot'.

    ⚠️ `event_path` IS INJECTABLE, AND IT HAD TO BECOME SO. Reading
    `GITHUB_EVENT_PATH` straight from the environment made this module's own
    self-test read the AMBIENT payload of whatever PR was running it — so in CI
    both POSITIVE controls saw that PR's human author and failed A7, while the
    same suite passed locally where the variable is unset. MEASURED: the
    `guards` job on PR #12974 (run 36188298373) failed on exactly that, having
    passed on the same commit locally. An instrument whose verdict depends on
    the environment it is measured in is not an instrument
    (docs/CLAUDE-RULES-CANONICAL.md § 'Green is not evidence' — assume the class
    applies to your instruments, not only to your data). The fixtures now plant
    their own payload and the production path is unchanged."""
    path = event_path if event_path is not None else (os.environ.get("GITHUB_EVENT_PATH") or "")
    if not path or not Path(path).is_file():
        return None, f"GITHUB_EVENT_PATH {path!r} is not readable"
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        login = (((payload or {}).get("pull_request") or {}).get("user") or {}).get("login")
    except (OSError, ValueError) as exc:
        return None, f"the event payload could not be parsed ({exc})"
    if not isinstance(login, str) or not login:
        return None, "the event payload carries no pull_request.user.login"
    return login, "from the GitHub event payload"


RunFetch = Callable[[str], Tuple[Optional[Dict[str, Any]], str]]


def fetch_run(run_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """(run, how). `None` means WE COULD NOT LOOK, never 'the run is fine'.

    GET /repos/{GITHUB_REPOSITORY}/actions/runs/{run_id}, authenticated with the
    job's GITHUB_TOKEN when present (`guards.yml` grants `actions: read`)."""
    import urllib.error
    import urllib.request
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not repo:
        return None, "GITHUB_REPOSITORY is unset, so there is no repository to ask"
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}",
        headers={"Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    tok = os.environ.get("GITHUB_TOKEN") or ""
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")), f"GET {repo} actions/runs/{run_id}"
    except urllib.error.HTTPError as exc:
        return None, f"GET actions/runs/{run_id} returned HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"GET actions/runs/{run_id} failed ({type(exc).__name__}: {exc})"


ProvFetch = Callable[[str], Tuple[Optional[Dict[str, Any]], str]]


def _api_get(url: str, *, auth: bool = True, raw: bool = False):
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    tok = os.environ.get("GITHUB_TOKEN") or ""
    if auth and tok:
        # Unredirected: the artifact download 302s to blob storage, which must
        # never receive the repository token.
        req.add_unredirected_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return body if raw else json.loads(body.decode("utf-8"))


def fetch_provenance(run_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """(record, how). `None` means we could not look OR the run uploaded nothing;
    both refuse in CI. Lists run <run_id>'s artifacts by name and reads
    provenance.json out of the newest non-expired one."""
    import io
    import urllib.error
    import zipfile
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not repo:
        return None, "GITHUB_REPOSITORY is unset, so there is no repository to ask"
    base = f"https://api.github.com/repos/{repo}/actions"
    try:
        listing = _api_get(f"{base}/runs/{run_id}/artifacts?name={PROVENANCE_ARTIFACT}&per_page=10")
        arts = [a for a in (listing or {}).get("artifacts") or []
                if a.get("name") == PROVENANCE_ARTIFACT and not a.get("expired")]
        if not arts:
            return None, (f"run {run_id} uploaded no {PROVENANCE_ARTIFACT!r} artifact. A dry "
                          f"run (apply!=true), or a run that never pushed a branch, has none")
        art = max(arts, key=lambda a: a.get("created_at") or "")
        blob_ = _api_get(f"{base}/artifacts/{art['id']}/zip", raw=True)
        with zipfile.ZipFile(io.BytesIO(blob_)) as zf:
            rec = json.loads(zf.read(PROVENANCE_FILE).decode("utf-8"))
        return (rec if isinstance(rec, dict) else None), f"artifact {art['id']} of run {run_id}"
    except urllib.error.HTTPError as exc:
        return None, f"reading run {run_id}'s artifacts returned HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        return None, f"reading run {run_id}'s artifacts failed ({type(exc).__name__}: {exc})"


def declared_window(root: Path, rev: str) -> Optional[str]:
    """The producer's DECLARED demotion window: the `window` input default of
    `.github/workflows/r4-demotion-gate.yml` at `rev`. `None` means we could not
    read it, and the caller refuses. (PyYAML parses the bare `on:` key as True.)"""
    doc = _yaml_text(blob(root, rev, WORKFLOW_REL))
    if not doc:
        return None
    on = doc.get("on", doc.get(True)) or {}
    try:
        val = on["workflow_dispatch"]["inputs"]["window"]["default"]
    except (KeyError, TypeError):
        return None
    return str(val) if val else None


def check_provenance(prov: Dict[str, Any], run_id: str, branch: str, head_sha: str,
                     removals: Dict[str, List[str]], *, run_event: Optional[str] = None,
                     window: Optional[str] = None) -> List[str]:
    """Every way the run-produced record fails to describe THIS PR."""
    fails: List[str] = []
    # N1 (re-review of #13609): the run's INPUTS decide the evidence, so they
    # are bound too. A dispatch with window=1d would otherwise mint a genuine
    # run and genuine provenance over a noisy window.
    if window is None:
        fails.append("A7 the producer's declared window could not be read from "
                     f"{WORKFLOW_REL} at the base ref. We could not look, so refusing.")
    elif str(prov.get("window") or "") != window:
        fails.append(f"A7 run {run_id} measured window {prov.get('window')!r}, not the declared "
                     f"demotion window {window!r}. A dispatch may not choose its own evidence "
                     f"window.")
    if run_event is not None and prov.get("event") != run_event:
        fails.append(f"A7 the provenance records event {prov.get('event')!r}, but run {run_id} "
                     f"was started by {run_event!r}.")
    if str(prov.get("run_id") or "") != run_id:
        fails.append(f"A7 the provenance artifact names run {prov.get('run_id')!r}, not {run_id}.")
    if prov.get("branch") != branch:
        fails.append(f"A7 run {run_id} pushed branch {prov.get('branch')!r}, not {branch!r}.")
    if str(prov.get("head_sha") or "") != head_sha:
        fails.append(f"A7 run {run_id} pushed head {str(prov.get('head_sha'))[:8]}, but this PR's "
                     f"head is {head_sha[:8]}. Content the run did not push does not land "
                     f"under its id.")
    want = {k: sorted(v) for k, v in (prov.get("removals") or {}).items()}
    got = {k: sorted(v) for k, v in removals.items()}
    if want != got:
        fails.append(f"A7 run {run_id} recorded removals {want}, but this diff removes {got}.")
    return fails


def check_run(run: Dict[str, Any], run_id: str, mb: str) -> List[str]:
    """Every way run `run_id` fails to be THE producer run for a branch cut at `mb`."""
    fails: List[str] = []
    # `path` may carry an `@<ref>` suffix; the ref itself is graded by head_branch.
    path = str(run.get("path") or "").split("@", 1)[0]
    if path != WORKFLOW_REL:
        fails.append(f"A7 run {run_id} is a run of {path or '(no path)'!r}, not {WORKFLOW_REL!r}. "
                     f"Only the demotion gate's own run may land this route.")
    if run.get("head_branch") != "main":
        fails.append(f"A7 run {run_id} ran on branch {run.get('head_branch')!r}, not `main`. "
                     f"A workflow edited on a branch is not the reviewed producer.")
    if run.get("event") not in RUN_EVENTS:
        fails.append(f"A7 run {run_id} was started by event {run.get('event')!r}; the "
                     f"producer runs only on {', '.join(RUN_EVENTS)}.")
    want_repo = (os.environ.get("GITHUB_REPOSITORY") or "").lower()
    got_repo = str(((run.get("repository") or {}).get("full_name")) or "").lower()
    if want_repo and got_repo != want_repo:
        fails.append(f"A7 run {run_id} belongs to {got_repo or '(unknown)'!r}, not {want_repo!r}.")
    if str(run.get("head_sha") or "") != mb:
        fails.append(f"A7 run {run_id} ran at main {str(run.get('head_sha'))[:8]}, but this "
                     f"branch was cut from {mb[:8]}. The producer commits on top of the "
                     f"exact commit its run checked out, so a mismatch is a branch that run "
                     f"did not write.")
    return fails


def check_author(root: Path, mb: str, branch: str, decl: Dict[str, Any],
                 *, pre_flight: bool = False, event_path: Optional[str] = None,
                 in_actions: Optional[bool] = None,
                 run_fetch: Optional[RunFetch] = None,
                 prov_fetch: Optional[ProvFetch] = None,
                 removals: Optional[Dict[str, List[str]]] = None,
                 base: Optional[str] = None,
                 ) -> Tuple[List[str], List[str]]:
    """`pre_flight` is the PRODUCER's own run, inside
    `.github/workflows/r4-demotion-gate.yml`, BEFORE the PR exists — so there is
    no `pull_request.user.login` to read and demanding one would make the
    workflow refuse its own correct output every time.

    ⚠️ IT WEAKENS NOTHING THAT DECIDES A MERGE. The authorization is the
    `pr-landing-guard` run ON THE PR, a required status check, which never
    passes this flag and therefore fails closed on an unreadable author exactly
    as before. Pre-flight decides only whether the workflow ARMS; the guard
    decides whether GitHub MERGES, and it re-derives all eight clauses itself."""
    fails, notes = [], []
    m = BRANCH_RE.match(branch or "")
    if not m:
        return ([f"A7 branch {branch!r} is not `automation/r4-demotion-<run_id>`. This route is "
                 f"scoped to the one workflow that writes that name ({WORKFLOW_REL}); a lane "
                 f"branch, a manual branch or any other automation branch declares "
                 f"`landing: \"hold\"` and a human reads it."], notes)
    run_id = m.group(1)
    if str(decl.get("run_id") or "") != run_id:
        fails.append(f"A7 the declaration's run_id={decl.get('run_id')!r} does not match the "
                     f"branch's {run_id}. The paperwork must name the run that produced it.")
    if decl.get("workflow") != WORKFLOW_REL:
        fails.append(f"A7 the declaration's workflow={decl.get('workflow')!r}; expected "
                     f"{WORKFLOW_REL!r}.")

    rc, out = _git(root, "log", "--format=%H%x1f%ae%x1f%ce%x1f%an", f"{mb}..{_GRADED}")
    if rc != 0:
        return (fails + [f"A7 the commit range {mb[:8]}..HEAD could not be read, so authorship "
                         f"was not established. Refusing rather than assuming."], notes)
    rows = [ln.split("\x1f") for ln in out.splitlines() if ln.strip()]
    if not rows:
        fails.append("A7 no commits between the merge-base and HEAD — nothing to attribute.")
    bad = [r[0][:8] for r in rows if len(r) == 4 and not (r[1] == BOT_EMAIL and r[2] == BOT_EMAIL)]
    if bad:
        fails.append(
            f"A7 {len(bad)} of {len(rows)} commit(s) are not authored AND committed by "
            f"`{BOT_EMAIL}`: {', '.join(bad[:6])}. A hand-edited commit on this branch means a "
            f"session wrote part of a real-money roster cut, and the whole point of this route "
            f"is that no session is in the path.")

    in_ci = ((os.environ.get("GITHUB_ACTIONS") or "").lower() == "true"
             if in_actions is None else in_actions)
    # The opener is REPORTED, never trusted (JC-CA-03 re-decision 2026-09-28).
    login, how = pr_author_login(event_path)
    if login is not None:
        notes.append(f"A7 PR opened by {login} ({how}). Reported only: the opener login is "
                     f"forgeable both ways and is not what this clause trusts.")
    if pre_flight:
        notes.append(f"A7 originating run NOT CHECKED: this is the producer's own pre-flight, "
                     f"i.e. run {run_id} itself. The authoritative read is pr-landing-guard R16 "
                     f"on the PR, which fails closed on it. Commit authorship above still applied.")
        return fails, notes
    run, rhow = (run_fetch or fetch_run)(run_id)
    if run is None:
        if in_ci:
            fails.append(f"A7 running in GitHub Actions but run {run_id} could not be read "
                         f"({rhow}). That is *we could not look*, and this route fails closed "
                         f"on it: the originating run is the one fact a pushed branch cannot "
                         f"forge.")
        else:
            notes.append(f"A7 originating run NOT CHECKED here ({rhow}). This run is outside "
                         f"CI. The commit-authorship check above still applied.")
        return fails, notes
    rfails = check_run(run, run_id, mb)
    fails += rfails
    if rfails:
        return fails, notes
    notes.append(f"A7 run {run_id} verified ({rhow}): {WORKFLOW_REL} on main via "
                 f"{run.get('event')}, head_sha {mb[:8]} == this branch's base")
    # The run is genuine. Did IT push THIS content? (review BLOCK B2)
    prov, phow = (prov_fetch or fetch_provenance)(run_id)
    if prov is None:
        if in_ci:
            fails.append(f"A7 run {run_id} is genuine, but its provenance could not be read "
                         f"({phow}). A genuine run id says nothing about who wrote this "
                         f"branch; only the run's own artifact does. Refusing.")
        else:
            notes.append(f"A7 run provenance NOT CHECKED here ({phow}); outside CI.")
        return fails, notes
    rc, head_sha = _git(root, "rev-parse", _GRADED)
    pfails = check_provenance(prov, run_id, branch, head_sha.strip(), removals or {},
                              run_event=run.get("event"),
                              window=declared_window(root, base or mb))
    fails += pfails
    if not pfails:
        notes.append(f"A7 provenance {phow}: run {run_id} pushed exactly this head "
                     f"{head_sha.strip()[:8]} and exactly these removals; "
                     f"{len(rows)} commit(s) all by the bot")
    return fails, notes


# --------------------------------------------------------------------------
# the merge-slot claim (arming IS the merge — R13's property, on this route)
# --------------------------------------------------------------------------
def check_grant_at_base(root: Path, base: str, mb: str,
                       changed: List[str]) -> Tuple[List[str], List[str]]:
    """A4/A8 AGAIN at the base ref (main NOW), not only at the branch's cut point.

    Without this, disarming `autoland` or revoking the grant AFTER a run fired
    would not stop the PR that run already opened (re-review of #13609)."""
    rc, base_sha = _git(root, "rev-parse", base)
    if rc != 0 or base_sha.strip() == mb:
        return [], []
    mdoc = _yaml_text(blob(root, base, MANDATES_REL))
    if mdoc is None:
        return ([f"A8 {MANDATES_REL} could not be read at {base}; refusing."], [])
    granted = {m.get("id"): m for m in (mdoc.get("mandates") or []) if isinstance(m, dict)}
    fails: List[str] = []
    for leg, rels in _firings_added(root, mb, changed).items():
        for rel in rels:
            mid = (_json_text(blob(root, _GRADED, rel)) or {}).get("mandate")
            m = granted.get(mid)
            if not m or not m.get("granted_by") or m.get("blocked_until") \
                    or m.get("direction") != "derisk_only" or m.get(ARM_FIELD) is not True:
                fails.append(f"A8 {leg}: {mid} is no longer granted, derisk_only and armed with "
                             f"`{ARM_FIELD}: true` in {MANDATES_REL} at {base} (main now). A "
                             f"disarm or revocation after the run fired stops its PR too.")
    return fails, ([] if fails else [f"A4/A8 re-checked at {base}: still granted and armed"])


def check_slot(root: Path, mb: str, changed: List[str], slug: str,
               branch: str) -> Tuple[List[str], List[str]]:
    rel = f"{SLOT_DIR}/{slug}.json"
    if rel not in changed:
        return ([f"A7 this branch lands itself without writing a merge-slot claim at {rel}. "
                 f"Arming is not a request to merge, it IS the merge (R13), and an automated "
                 f"merge with no attributable, timestamped claim is the state that took the "
                 f"repo down on 2026-09-09. Write it with `scripts/ops/claim_merge_slot.py "
                 f"--branch-claim`."], [])
    claim = _json_text(blob(root, _GRADED, rel))
    if claim is None:
        return ([f"A7 {rel} is not readable JSON at HEAD."], [])
    held = str(claim.get("held_by") or "")
    m = BRANCH_RE.match(branch or "")
    run_id = m.group(1) if m else ""
    if BOT_LOGIN not in held or (run_id and run_id not in held):
        return ([f"A7 {rel} is held_by {held!r}; it must name `{BOT_LOGIN}` and run {run_id} so "
                 f"the claim is attributable to the run that made it — a `session_01…` id here "
                 f"means a human intervened, which this route does not admit."], [])
    return ([], [f"A7 merge-slot claim {rel} held_by {held}"])


# --------------------------------------------------------------------------
# the verdict
# --------------------------------------------------------------------------
def verdict(root: Path, base: str, branch: Optional[str], decl: Dict[str, Any],
            changed: List[str], slug: str, *, pre_flight: bool = False,
            event_path: Optional[str] = None,
            in_actions: Optional[bool] = None,
            run_fetch: Optional[RunFetch] = None,
            prov_fetch: Optional[ProvFetch] = None,
            head: Optional[str] = None) -> Tuple[bool, List[str], List[str]]:
    """(ok, failures, notes). Every clause runs, so one PR reports every defect.

    `head` is the commit to grade. `None` resolves the PR head (`resolve_head`)."""
    global _GRADED
    prev = _GRADED
    rev, how = (head, "given") if head else resolve_head(root, event_path)
    _GRADED = rev
    try:
        ok, fails, notes = _verdict(root, base, branch, decl, changed, slug,
                                    pre_flight=pre_flight, event_path=event_path,
                                    in_actions=in_actions, run_fetch=run_fetch,
                                    prov_fetch=prov_fetch)
    finally:
        _GRADED = prev
    if rev != "HEAD":
        notes.insert(0, f"graded commit {rev[:12]} ({how}), not HEAD")
    return ok, fails, notes


def _verdict(root: Path, base: str, branch: Optional[str], decl: Dict[str, Any],
             changed: List[str], slug: str, *, pre_flight: bool = False,
             event_path: Optional[str] = None,
             in_actions: Optional[bool] = None,
             run_fetch: Optional[RunFetch] = None,
             prov_fetch: Optional[ProvFetch] = None) -> Tuple[bool, List[str], List[str]]:
    fails: List[str] = []
    notes: List[str] = []
    if not branch:
        return (False, ["A7 no branch to grade — this route fails closed on an unknown branch."],
                notes)
    mb = merge_base(root, base)
    if not mb:
        return (False, [f"A2 the merge-base with {base!r} could not be read, so nothing was "
                        f"graded. *We could not look* refuses on a self-landing route."], notes)

    f, n = check_paths(changed, slug)
    fails += f
    notes += n
    f, n, removals = check_cut_only(root, mb)
    fails += f
    notes += n
    if removals:
        f, n = check_mirror(root, mb, removals)
        fails += f
        notes += n
        f, n = check_evidence(root, mb, changed, removals)
        fails += f
        notes += n
    f, n = check_author(root, mb, branch, decl, pre_flight=pre_flight,
                        event_path=event_path, in_actions=in_actions, run_fetch=run_fetch,
                        prov_fetch=prov_fetch, removals=removals, base=base)
    fails += f
    notes += n
    f, n = check_grant_at_base(root, base, mb, changed)
    fails += f
    notes += n
    f, n = check_slot(root, mb, changed, slug, branch)
    fails += f
    notes += n
    return (not fails, fails, notes)


def _changed(root: Path, base: str, head: Optional[str] = None) -> Optional[List[str]]:
    global _GRADED
    if head:
        prev, _GRADED = _GRADED, head
        try:
            return _changed(root, base)
        finally:
            _GRADED = prev
    mb = merge_base(root, base)
    if not mb:
        return None
    rc, out = _git(root, "diff", "--name-only", f"{mb}...{_GRADED}")
    return [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else None


def _slug(branch: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", re.sub(r"^claude/", "", branch))


# ---------------------------------------------------------------------------
# self-test: plant each defect in a REAL git repository, prove the verdict.
#
# ⚠️ THE POSITIVE CONTROLS RUN FIRST AND MATTER MOST. Every clause here REFUSES
# work, so an over-broad implementation reads green while reinstating exactly the
# hand-merge this route exists to end — the same reasoning `guard_selftests.py`
# records for `pr-landing-guard`. If the admissible shape does not PASS, none of
# the plants below mean anything.
#
# ⚠️ AND THE FIXTURES ARE REAL REPOSITORIES, not stubs, for the reason
# `check_pr_landing.py`'s suite gives: merge-base, blob and commit-authorship
# questions faked in a fixture would be testing the fake.
# ---------------------------------------------------------------------------
RUN_ID = "4242424242"
LEG = "xrp_pullback_2h"


def _fixture_branch() -> Tuple[str, str]:
    """The fixture branch name, built INSIDE a function on purpose.

    ⚠️ NOT COSMETIC. `scripts/ci/check_guard_liveness.py` reads a guard's
    MODULE-LEVEL string constants as its DECLARED SUBJECTS and reports any that
    do not exist at HEAD — and it deliberately does not walk into function
    bodies, because a literal inside a self-test is a FIXTURE a guard is
    supposed to plant at a path that does not exist. `automation/r4-demotion-…`
    is a BRANCH name, not a repo path: it will never exist as a file, so a
    module-level literal of it made this guard read `NEW DEGRADED` on a subject
    it was never looking for. (MEASURED: the first push of this file failed
    `guard-liveness` for exactly that.) The shape the branch must match lives in
    `BRANCH_RE` above, where it belongs; this is the fixture.
    """
    branch = "automation/" + f"r4-demotion-{RUN_ID}"
    return branch, _slug(branch)


BRANCH, SLUG = _fixture_branch()

_MANDATES = """\
mandates:
  - id: MD-DEMOTE-S2-S1
    grants: demote a Stage-2 leg when the mirror goes net-negative net-of-cost
    direction: derisk_only
    granted_by: operator (Ben)
    granted_at: '2026-09-24'
{arm}
  - id: MD-PROMOTE-S1-S2
    grants: add a leg to a REAL-MONEY roster
    direction: add_risk
    granted_by: operator (Ben)
    granted_at: '2026-09-24'
    autoland: true
    bar: {{min_n_closed: 30, expectancy_gt: 0, fold_majority: true, cost_tolerance_bps: 0.0}}
    cap: {{basis: uniform_account_risk_pct_share_of_roster, max_auto_share: 0.25}}
proposed:
  - id: MD-DEMOTE-UNGRANTED
    direction: derisk_only
    autoland: true
"""

_ACCOUNTS = """\
accounts:
  bybit_1:
    exchange: bybit
    account_class: paper
    mode: live
    risk: {{risk_pct: 0.015}}
    strategies: [trend_donchian, {leg}]
  bybit_2:
    exchange: bybit
    account_class: real_money
    mode: live
    risk: {{risk_pct: 0.015}}
    strategies: [{leg}, trend_donchian_eth_4h]
  bybit_portfolio:
    exchange: bybit
    account_class: paper
    mode: live
    risk: {{risk_pct: 0.015}}
    strategies: [{leg}, trend_donchian_eth_4h]
"""

_STRATEGIES = f"strategies:\n  {LEG}:\n    execution: live\n"


def _run(root: Path, *args: str, env: Optional[dict] = None) -> None:
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=e)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


_BOT_ENV = {"GIT_AUTHOR_NAME": BOT_LOGIN, "GIT_AUTHOR_EMAIL": BOT_EMAIL,
            "GIT_COMMITTER_NAME": BOT_LOGIN, "GIT_COMMITTER_EMAIL": BOT_EMAIL}
_HUMAN_ENV = {"GIT_AUTHOR_NAME": "a session", "GIT_AUTHOR_EMAIL": "session@example.invalid",
              "GIT_COMMITTER_NAME": "a session", "GIT_COMMITTER_EMAIL": "session@example.invalid"}


def _base_repo(tmp: Path, *, arm: bool = True) -> Path:
    root = tmp / "repo"
    root.mkdir(parents=True)
    _run(root, "init", "-q", "-b", "main")
    # A fixture repo must not spawn background maintenance: a detached `git gc --auto` /
    # `git maintenance` child still writing .git/objects when the TemporaryDirectory is
    # cleaned up made cleanup fail with `[Errno 39] Directory not empty: 'objects'`
    # (pytest-run, PR #14180, 2026-09-29). Turn it off at the source.
    _run(root, "config", "gc.auto", "0")
    _run(root, "config", "maintenance.auto", "false")
    _write(root, MANDATES_REL, _MANDATES.format(arm="    autoland: true" if arm else ""))
    _write(root, ACCOUNTS_REL, _ACCOUNTS.format(leg=LEG))
    _write(root, mr.STRATEGIES_REL, _STRATEGIES)
    # Stage-0 evidence at the BASE: the T3 threshold's source, which the replay
    # reads at the merge-base only.
    for leg in (LEG, "proxy_leg_1d"):
        src = f"{mr.EVIDENCE_DIR_REL}/runs/fixture/{leg}__trades.jsonl"
        _write(root, src, "".join(json.dumps({"net_r": x}) + "\n" for x in _STAGE0_NET_R))
        _write(root, f"{mr.EVIDENCE_DIR_REL}/{leg}.json",
               json.dumps({"strategy": leg, "source_run": src}))
    # The producer's DECLARED window lives in its workflow's `window` default.
    _write(root, WORKFLOW_REL, 'on:\n  workflow_dispatch:\n    inputs:\n      window:\n'
                               '        default: "30d"\n')
    # The planted event payload lives in the work tree; it must never reach the
    # diff, or A1 would refuse every fixture for a file the fixture invented.
    _write(root, ".gitignore", ".fixture-event.json\n")
    _run(root, "add", "-A")
    _run(root, "commit", "-qm", "base", env=_BOT_ENV)
    _run(root, "branch", "-f", "mainbase")
    return root


def _cut(root: Path, accounts: List[str], leg: str = LEG) -> None:
    doc = yaml.safe_load((root / ACCOUNTS_REL).read_text(encoding="utf-8"))
    for a in accounts:
        doc["accounts"][a]["strategies"] = [x for x in doc["accounts"][a]["strategies"]
                                            if x != leg]
    _write(root, ACCOUNTS_REL, yaml.safe_dump(doc, sort_keys=False))


#: The fixture's Stage-0 per-trade stream (mean +0.2R): its 20-trade p10 sits
#: between -6R and 0, so two -6R windows clear T3.
_STAGE0_NET_R = (1.0, -0.8, 0.6, -1.0, 1.2)


def _evidence(root: Path, *, leg: str = LEG, net: float = -12.0,
              mandate: str = "MD-DEMOTE-S2-S1", accounts: Optional[List[str]] = None,
              windows: Optional[List[float]] = None) -> None:
    run_rel = f"{RUNS_DIR}/20260925T000000Z-30d.json"
    _write(root, run_rel, json.dumps({"kind": "r4_demotion_gate_source_run", "window": "30d"}))
    windows = windows if windows is not None else [net / 2, net / 2]
    th = mr.stage0_block_p10(leg, root)
    _write(root, f"{MIRROR_DIR}/{leg}.json", json.dumps({
        "leg": leg, "account": "bybit_2", "mandate": mandate, "window": "30d",
        "rule": mr.T3_RULE_ID, "source_run": run_rel, "n_closed": mr.T3_N,
        "net_r_net_of_full_cost": net,
        "windows": [{"n_closed": mr.T3_BLOCK, "net_r": w} for w in windows],
        "p10_threshold": th["p10"], "bootstrap_seed": mr.T3_BOOTSTRAP_SEED,
        "evidence_record": th["evidence_record"]}))
    _write(root, f"{FIRINGS_DIR}/20260925T000000Z-{leg}-demote.json", json.dumps({
        "mandate": mandate, "action": "remove", "leg": leg,
        "accounts": accounts if accounts is not None else ["bybit_2", "bybit_portfolio"],
        "fired_at": "2026-09-25T00:00:00Z", "evidence": f"{MIRROR_DIR}/{leg}.json",
        "source_run": run_rel}))


def _paperwork(root: Path, *, run_id: str = RUN_ID, slot: bool = True,
               workflow: str = WORKFLOW_REL, held_by: Optional[str] = None) -> dict:
    decl = {"tier": 3, "landing": LANDING_VALUE, "mandate": "MD-DEMOTE-S2-S1",
            "run_id": run_id, "workflow": workflow,
            "why": "automated Stage-2 roster cut under the granted derisk_only mandate "
                   "MD-DEMOTE-S2-S1; every clause of check_mandate_autoland applies"}
    _write(root, f"{LANDING_DIR}/{SLUG}.json", json.dumps(decl, indent=2))
    if slot:
        _write(root, f"{SLOT_DIR}/{SLUG}.json", json.dumps({
            "branch": BRANCH, "held_by": held_by or f"{BOT_LOGIN} · r4-demotion-gate run {RUN_ID}",
            "claimed_at": "2026-09-25T00:00:00Z"}, indent=2))
    return decl


def _event(root: Path, login: Optional[str], head_sha: Optional[str] = None) -> Optional[str]:
    """A PLANTED GitHub event payload, so no fixture ever reads the ambient one.

    ⚠️ THIS IS THE FIX FOR THE ONE FAILURE THIS SUITE SHIPPED WITH. Passing
    `None` means *no payload* — the local case — and is NOT the same as a
    payload naming somebody else; both are exercised below."""
    if login is None:
        return ""
    path = root / ".fixture-event.json"
    pr: Dict[str, Any] = {"user": {"login": login}}
    if head_sha:
        pr["head"] = {"sha": head_sha}
    path.write_text(json.dumps({"pull_request": pr}), encoding="utf-8")
    return str(path)


_FIXTURE_REPO = "fixture-owner/fixture-repo"  # the planted run's repository


def _genuine_run(mb: str, **over: Any) -> Dict[str, Any]:
    """What GET actions/runs/<RUN_ID> returns for the real producer run."""
    run = {"id": int(RUN_ID), "path": WORKFLOW_REL, "head_branch": "main",
           "event": "schedule", "head_sha": mb,
           "repository": {"full_name": _FIXTURE_REPO}}
    run.update(over)
    return run


def _finish(root: Path, decl: dict, *, branch: str = BRANCH, env: Optional[dict] = None,
            author: Optional[str] = BOT_LOGIN, in_actions: bool = True,
            run: Any = "genuine", prov: Any = "genuine", merge_ref: bool = False,
            payload_head: bool = True,
            repo_env: str = "fixture-owner/fixture-repo",
            ) -> Tuple[bool, List[str], List[str]]:
    """Commit the branch and grade it, with the run API and the provenance
    artifact PLANTED. No fixture ever reads the network or the ambient env.

    `run` / `prov`: "genuine" (what the real producer run returns for THIS
    branch), a dict of overrides applied to it, or None (could not read /
    nothing uploaded). `merge_ref=True` reproduces the CI checkout: main moves
    on, HEAD becomes a two-parent merge commit authored by a PERSON, and the
    event payload names the PR head (review BLOCK B1 on #13609)."""
    _run(root, "checkout", "-q", "-b", branch)
    _run(root, "add", "-A")
    _run(root, "commit", "-qm", "cut", env=env or _BOT_ENV)
    rc, tip = _git(root, "rev-parse", "HEAD")
    tip = tip.strip()
    mb = merge_base(root, "mainbase") or ""
    removals = roster_removals(_yaml_text(blob(root, mb, ACCOUNTS_REL)),
                               _yaml_text(blob(root, tip, ACCOUNTS_REL)))
    event_path = _event(root, author, tip if (merge_ref and payload_head) else None)
    if merge_ref:
        _run(root, "checkout", "-q", "mainbase")
        _write(root, "docs/unrelated.md", "main moved on\n")
        _run(root, "add", "-A")
        _run(root, "commit", "-qm", "main moves", env=_HUMAN_ENV)
        _run(root, "checkout", "-q", "--detach", "mainbase")
        _run(root, "merge", "-q", "--no-ff", "-m", "Merge PR into main", tip, env=_HUMAN_ENV)
    changed = _changed(root, "mainbase", tip) or []

    def fetch_r(run_id: str):
        if run is None:
            return None, "planted: the run API could not be read"
        return _genuine_run(mb, **(run if isinstance(run, dict) else {})), "planted run API"

    def fetch_p(run_id: str):
        if prov is None:
            return None, "planted: the run uploaded no provenance artifact"
        rec = {"run_id": RUN_ID, "branch": branch, "head_sha": tip, "removals": removals,
               "window": "30d", "event": "schedule"}
        rec.update(prov if isinstance(prov, dict) else {})
        return rec, "planted artifact"
    saved = {k: os.environ.get(k) for k in ("GITHUB_REPOSITORY", "GITHUB_EVENT_NAME")}
    os.environ["GITHUB_REPOSITORY"] = repo_env
    os.environ["GITHUB_EVENT_NAME"] = "pull_request" if merge_ref else "fixture"
    try:
        return verdict(root, "mainbase", branch, decl, changed, _slug(branch),
                       event_path=event_path, in_actions=in_actions,
                       run_fetch=fetch_r, prov_fetch=fetch_p,
                       head=None if merge_ref else tip)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _case(label: str, build, *, expect_ok: bool, expect_clause: Optional[str] = None,
          results: Optional[list] = None, quiet: bool = False) -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        ok, fails, _notes = build(tmp)
    blob_text = " | ".join(fails)
    good = (ok is expect_ok) and (expect_clause is None or
                                  any(f.startswith(expect_clause) for f in fails))
    if results is not None:
        results.append((label, good, blob_text))
    if not quiet:
        print(f"  {'ok  ' if good else 'FAIL'} {label}"
              + ("" if good else f"\n         got ok={ok} fails={blob_text[:400]}"))


def self_test(quiet: bool = False) -> int:
    results: list = []

    # ── POSITIVE CONTROLS ──────────────────────────────────────────────────
    def admissible(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root))
    _case("POSITIVE: a bot-authored, mirror-matched, resolver-FIRING cut LANDS ITSELF",
          admissible, expect_ok=True, results=results, quiet=quiet)

    def proxy_shape(tmp: Path):
        """The Alpaca carve-out shape: a leg the mirror never carried. The
        relative mirror rule must ADMIT it, not demand a cut that cannot exist."""
        root = _base_repo(tmp)
        doc = yaml.safe_load((root / ACCOUNTS_REL).read_text(encoding="utf-8"))
        doc["accounts"]["bybit_2"]["strategies"].append("proxy_leg_1d")
        doc["accounts"]["bybit_1"]["strategies"].append("proxy_leg_1d")
        _write(root, ACCOUNTS_REL, yaml.safe_dump(doc, sort_keys=False))
        _run(root, "add", "-A")
        _run(root, "commit", "-qm", "proxy", env=_BOT_ENV)
        _run(root, "branch", "-f", "mainbase")
        _cut(root, ["bybit_2"], leg="proxy_leg_1d")
        _evidence(root, leg="proxy_leg_1d", accounts=["bybit_2"])
        return _finish(root, _paperwork(root))
    _case("POSITIVE: a live-only leg the mirror never carried cuts from the live book alone",
          proxy_shape, expect_ok=True, results=results, quiet=quiet)

    # ── THE FIVE NEGATIVE CONTROLS THE OPERATOR NAMED ──────────────────────
    def also_adds(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        doc = yaml.safe_load((root / ACCOUNTS_REL).read_text(encoding="utf-8"))
        doc["accounts"]["bybit_2"]["strategies"].append("trend_donchian_sol_4h")
        doc["accounts"]["bybit_portfolio"]["strategies"].append("trend_donchian_sol_4h")
        _write(root, ACCOUNTS_REL, yaml.safe_dump(doc, sort_keys=False))
        _evidence(root)
        return _finish(root, _paperwork(root))
    _case("NEGATIVE 1: a diff that ALSO ADDS a leg refuses (A2)",
          also_adds, expect_ok=False, expect_clause="A2", results=results, quiet=quiet)

    def ungranted(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root, mandate="MD-DEMOTE-UNGRANTED")
        return _finish(root, _paperwork(root))
    _case("NEGATIVE 2: a PROPOSED (ungranted) mandate refuses (A4)",
          ungranted, expect_ok=False, expect_clause="A4", results=results, quiet=quiet)

    def promote_direction(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root, mandate="MD-PROMOTE-S1-S2")
        return _finish(root, _paperwork(root))
    _case("NEGATIVE 3: an add_risk (promote-direction) mandate refuses (A4)",
          promote_direction, expect_ok=False, expect_clause="A4", results=results, quiet=quiet)

    def session_branch(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), branch="claude/hand-edited-cut",
                      env=_HUMAN_ENV)
    _case("NEGATIVE 4: a hand-edited PR from a SESSION branch refuses (A7)",
          session_branch, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def live_only(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2"])
        _evidence(root, accounts=["bybit_2"])
        return _finish(root, _paperwork(root))
    _case("NEGATIVE 5: a LIVE-ONLY cut without the mirror cut refuses (A6)",
          live_only, expect_ok=False, expect_clause="A6", results=results, quiet=quiet)

    # ── THE REST OF THE SURFACE ────────────────────────────────────────────
    def not_armed(tmp: Path):
        root = _base_repo(tmp, arm=False)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root))
    _case("the route is BUILT BUT NOT ARMED: no `autoland: true` refuses (A8)",
          not_armed, expect_ok=False, expect_clause="A8", results=results, quiet=quiet)

    def grants_itself(tmp: Path):
        root = _base_repo(tmp, arm=False)
        _write(root, MANDATES_REL, _MANDATES.format(arm="    autoland: true"))
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root))
    _case("a PR that ARMS ITSELF by editing config/mandates.yaml refuses (A1)",
          grants_itself, expect_ok=False, expect_clause="A1", results=results, quiet=quiet)

    def stray_file(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        _write(root, "src/runtime/orders.py", "# smuggled\n")
        return _finish(root, _paperwork(root))
    _case("a file outside the allowlist (src/runtime/orders.py) refuses (A1)",
          stray_file, expect_ok=False, expect_clause="A1", results=results, quiet=quiet)

    def other_key(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        doc = yaml.safe_load((root / ACCOUNTS_REL).read_text(encoding="utf-8"))
        doc["accounts"]["bybit_2"]["risk"]["risk_pct"] = 0.05
        _write(root, ACCOUNTS_REL, yaml.safe_dump(doc, sort_keys=False))
        _evidence(root)
        return _finish(root, _paperwork(root))
    _case("a risk_pct edit smuggled alongside the cut refuses (A2)",
          other_key, expect_ok=False, expect_clause="A2", results=results, quiet=quiet)

    def positive_window(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root, net=+0.40)
        return _finish(root, _paperwork(root))
    _case("a mirror window that is NOT negative refuses — the resolver is replayed (A5)",
          positive_window, expect_ok=False, expect_clause="A5", results=results, quiet=quiet)

    def t3_one_window(tmp: Path):
        """MD-DEMOTE-S2-S1's T3 rule (operator 2026-09-29): a negative 40-trade
        net whose OLDER window is healthy does not fire -- only one of the two
        20-trade windows is below the leg's Stage-0 p10."""
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root, net=-4.0, windows=[+2.0, -6.0])
        return _finish(root, _paperwork(root))
    _case("a negative window that fails T3 (one of two windows below p10) refuses (A5)",
          t3_one_window, expect_ok=False, expect_clause="A5", results=results, quiet=quiet)

    def t3_no_stage0(tmp: Path):
        """No Stage-0 evidence at the MERGE-BASE: the threshold cannot be
        derived, the resolver answers NEEDS-DATA, and nothing lands."""
        root = _base_repo(tmp)
        _run(root, "rm", "-q", f"{mr.EVIDENCE_DIR_REL}/{LEG}.json")
        _run(root, "commit", "-qm", "drop evidence", env=_BOT_ENV)
        _run(root, "branch", "-f", "mainbase")
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root))
    _case("no Stage-0 evidence at the merge-base refuses -- a threshold is never guessed (A5)",
          t3_no_stage0, expect_ok=False, expect_clause="A5", results=results, quiet=quiet)

    def no_firing(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        (root / f"{FIRINGS_DIR}/20260925T000000Z-{LEG}-demote.json").unlink()
        return _finish(root, _paperwork(root))
    _case("a cut with NO firing record refuses (A3)",
          no_firing, expect_ok=False, expect_clause="A3", results=results, quiet=quiet)

    def no_source_run(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        (root / f"{RUNS_DIR}/20260925T000000Z-30d.json").unlink()
        return _finish(root, _paperwork(root))
    _case("an evidence record citing a source_run absent from the tree refuses (A3)",
          no_source_run, expect_ok=False, expect_clause="A3", results=results, quiet=quiet)

    def wrong_run_id(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root, run_id="1"))
    _case("a declaration whose run_id does not match the branch refuses (A7)",
          wrong_run_id, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def no_slot(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root, slot=False))
    _case("arming with no merge-slot claim refuses (A7/R13)",
          no_slot, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def session_held_slot(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root, held_by="session_01ABCDEF"))
    _case("a merge-slot claim held by a SESSION refuses (A7)",
          session_held_slot, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def stage1_cut(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_1"])
        _evidence(root, accounts=["bybit_1"])
        return _finish(root, _paperwork(root))
    _case("a Stage-1 soak-roster cut is not this route's to land (A2)",
          stage1_cut, expect_ok=False, expect_clause="A2", results=results, quiet=quiet)

    def orphan_firing(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        _write(root, f"{FIRINGS_DIR}/20260925T000000Z-trend_donchian_eth_4h-demote.json",
               json.dumps({"mandate": "MD-DEMOTE-S2-S1", "action": "remove",
                           "leg": "trend_donchian_eth_4h", "accounts": ["bybit_2"]}))
        return _finish(root, _paperwork(root))
    _case("a firing record for a leg the diff does not cut refuses (A3)",
          orphan_firing, expect_ok=False, expect_clause="A3", results=results, quiet=quiet)

    def preflight_still_checks_commits(tmp: Path):
        """--pre-flight waives the PR-AUTHOR read and NOTHING else."""
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        decl = _paperwork(root)
        _run(root, "checkout", "-q", "-b", BRANCH)
        _run(root, "add", "-A")
        _run(root, "commit", "-qm", "cut", env=_HUMAN_ENV)
        changed = _changed(root, "mainbase") or []
        return verdict(root, "mainbase", BRANCH, decl, changed, SLUG, pre_flight=True,
                       event_path="", in_actions=False)
    _case("--pre-flight still refuses a hand-authored commit (it waives only the "
          "PR-author read, which no PR yet has)",
          preflight_still_checks_commits, expect_ok=False, expect_clause="A7",
          results=results, quiet=quiet)

    # ── the ORIGINATING-RUN clause (JC-CA-03 re-decision 2026-09-28, "Keep PAT,
    # verify run"). Each case plants its OWN event payload AND its own run API
    # answer; none reads the ambient environment or the network.
    def pat_opened_genuine(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), author="benbaichmankass")
    _case("POSITIVE: the GENUINE producer PR, opened with the PAT (so its author is the PAT "
          "owner), lands on a verified originating run (A7)",
          pat_opened_genuine, expect_ok=True, results=results, quiet=quiet)

    def forged_opener(tmp: Path):
        """The measured forgery: pr-opener.yml opens a session-pushed branch as
        github-actions[bot]. Everything a push can write is right, but the run
        id names a run of a DIFFERENT workflow."""
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), author=BOT_LOGIN,
                       run={"path": ".github/workflows/pr-opener.yml", "event": "push"})
    _case("FORGED: a bot-opened PR whose run id is not a demotion-gate run refuses, "
          "however bot-shaped the rest is (A7)",
          forged_opener, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def run_on_branch(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), run={"head_branch": "claude/edited-producer"})
    _case("a demotion-gate run on a BRANCH (an edited producer) refuses (A7)",
          run_on_branch, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def replayed_run(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), run={"head_sha": "0" * 40})
    _case("a REAL run id whose head_sha is not this branch's base refuses: a replayed "
          "run did not write this branch (A7)",
          replayed_run, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def run_unreadable_in_ci(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), run=None, in_actions=True)
    _case("an UNREADABLE run API inside CI fails closed. 'We could not look' is "
          "not 'it is the producer' (A7)",
          run_unreadable_in_ci, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def via_merge_ref(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), author="benbaichmankass", merge_ref=True)
    _case("POSITIVE: graded through GitHub's SYNTHETIC MERGE REF, the way CI checks it out. "
          "The PR head is graded, not the merge commit or its person-authored parent (B1)",
          via_merge_ref, expect_ok=True, results=results, quiet=quiet)

    def wrong_event(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), run={"event": "push"})
    _case("a demotion-gate run started by `push` refuses (A7)",
          wrong_event, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def wrong_repo(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), run={"repository": {"full_name": "fork/fork"}})
    _case("a run belonging to ANOTHER repository refuses (A7)",
          wrong_repo, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def dry_run_id(tmp: Path):
        """B2: any session can dispatch r4-demotion-gate on main with
        apply=false and get a GENUINE run id. That run pushes nothing and
        uploads nothing."""
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), prov=None)
    _case("FORGED: a genuine run id from a dry (apply=false) dispatch, which uploaded no "
          "provenance, refuses (A7/B2)",
          dry_run_id, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def other_head(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), prov={"head_sha": "f" * 40})
    _case("FORGED: content the run did not push (provenance names another head) refuses (A7/B2)",
          other_head, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def other_removals(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root),
                       prov={"removals": {"bybit_2": ["some_other_leg"]}})
    _case("FORGED: removals the run did not record refuse (A7/B2)",
          other_removals, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def via_head2_fallback(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), author="benbaichmankass", merge_ref=True,
                       payload_head=False)
    _case("POSITIVE: with NO head sha in the payload, a pull_request merge ref is graded "
          "at HEAD^2, the PR head (B1 fallback)",
          via_head2_fallback, expect_ok=True, results=results, quiet=quiet)

    def short_window(tmp: Path):
        """N1: a dispatch on main with apply=true, window=1d mints a GENUINE run
        and GENUINE provenance over a noisy window."""
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), run={"event": "workflow_dispatch"},
                       prov={"window": "1d", "event": "workflow_dispatch"})
    _case("FORGED: a genuine dispatch with window=1d (not the declared 30d) refuses (A7/N1)",
          short_window, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def event_mismatch(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), prov={"event": "workflow_dispatch"})
    _case("a provenance event that disagrees with the run's own event refuses (A7)",
          event_mismatch, expect_ok=False, expect_clause="A7", results=results, quiet=quiet)

    def disarmed_after_run(tmp: Path):
        """(1) The run fired while armed; main has since been DISARMED."""
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        decl = _paperwork(root)
        _run(root, "stash", "-u", "-q")
        _run(root, "checkout", "-q", "-b", "cutpoint")
        _run(root, "checkout", "-q", "mainbase")
        _write(root, MANDATES_REL, _MANDATES.format(arm=""))
        _run(root, "add", "-A")
        _run(root, "commit", "-qm", "disarm", env=_HUMAN_ENV)
        _run(root, "checkout", "-q", "cutpoint")
        _run(root, "stash", "pop", "-q")
        _run(root, "checkout", "-q", "-b", BRANCH)
        _run(root, "add", "-A")
        _run(root, "commit", "-qm", "cut", env=_BOT_ENV)
        _, tip = _git(root, "rev-parse", "HEAD")
        mb = merge_base(root, "mainbase") or ""
        removals = roster_removals(_yaml_text(blob(root, mb, ACCOUNTS_REL)),
                                   _yaml_text(blob(root, tip.strip(), ACCOUNTS_REL)))
        changed = _changed(root, "mainbase") or []
        os.environ["GITHUB_REPOSITORY"] = _FIXTURE_REPO
        try:
            return verdict(root, "mainbase", BRANCH, decl, changed, SLUG, event_path="",
                           in_actions=True, head=tip.strip(),
                           run_fetch=lambda r: (_genuine_run(mb), "planted"),
                           prov_fetch=lambda r: ({"run_id": RUN_ID, "branch": BRANCH,
                                                  "head_sha": tip.strip(), "removals": removals,
                                                  "window": "30d", "event": "schedule"},
                                                 "planted"))
        finally:
            os.environ.pop("GITHUB_REPOSITORY", None)
    _case("a mandate DISARMED on main after the run fired stops its open PR (A8 at base)",
          disarmed_after_run, expect_ok=False, expect_clause="A8", results=results, quiet=quiet)

    def run_unreadable_locally(tmp: Path):
        root = _base_repo(tmp)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        return _finish(root, _paperwork(root), author=None, run=None, in_actions=False)
    _case("POSITIVE: outside CI the same cut passes with the run read NOTED as unchecked, "
          "never silently assumed",
          run_unreadable_locally, expect_ok=True, results=results, quiet=quiet)

    # ⚠️ THE REGRESSION CONTROL FOR THE ONE REAL DEFECT THIS ROUTE SHIPPED WITH.
    # An earlier draft grouped A8 among the evidence clauses, so an UNARMED
    # mandate -- which is every mandate today -- skipped the A5 resolver replay
    # and the guard refused having graded almost nothing. Caught by
    # tests/test_mandate_autoland.py, not by reading.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = _base_repo(Path(td), arm=False)
        _cut(root, ["bybit_2", "bybit_portfolio"])
        _evidence(root)
        ok, fails, notes = _finish(root, _paperwork(root))
        good = (ok is False
                and sorted({f.split()[0] for f in fails}) == ["A8"]
                and any("resolver FIRE" in n for n in notes))
        results.append(("an UNARMED mandate still gets every other clause GRADED — "
                        "A8 does not short-circuit the A5 resolver replay", good, str(fails)))
        if not quiet:
            print(f"  {'ok  ' if good else 'FAIL'} an UNARMED mandate still gets every other "
                  f"clause GRADED — A8 does not short-circuit the A5 resolver replay"
                  + ("" if good else f"\n         fails={fails} notes={notes}"))

    bad = [r for r in results if not r[1]]
    if not quiet:
        print(f"\nmandate-autoland self-test: {'PASS' if not bad else 'FAIL'} "
              f"({len(results) - len(bad)}/{len(results)} controls held)")
        for label, _ok, detail in bad:
            print(f"  FAIL  {label} :: {detail[:400]}")
    return 0 if not bad else 1


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/main", help="the ref to diff against")
    ap.add_argument("--branch", default=None, help="override the branch name")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--if-declared", action="store_true",
                    help="exit 0 with an explicit NOT-APPLICABLE line when the PR's own "
                         "landing declaration does not ask for this route. ⚠️ That line is "
                         "printed, never silent: a green from this flag on an ordinary PR "
                         "means NOTHING WAS GRADED, not that the diff is admissible.")
    ap.add_argument("--pre-flight", action="store_true",
                    help="the PRODUCER's own run, before a PR exists: report the originating-"
                         "run and provenance reads as NOT CHECKED instead of failing closed. ⚠️ Only "
                         "r4-demotion-gate.yml passes this, and it decides only whether that "
                         "workflow ARMS — pr-landing-guard R16 on the PR never passes it and "
                         "is what gates the merge.")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    # The verdict below is about the COMMITTED tree. Say so when that is not
    # the tree you edited -- this guard grades a commit RANGE, and a session
    # chasing a named CI failure types IT rather than run_guards.py. See
    # BL-20260917-THE-DIRTY-TREE-NOTICE-LIVES-ONLY-IN-RUN-GUARDS-SO-ALL-18-DIRECTLY-INVOCABLE-DIFF-SCOPED-GUARDS-STILL-GRADE-THE-WRONG-TREE-SILENTLY
    # and `tests/test_dirty_tree_notice.py`, which asserts every --base-accepting
    # guard calls the ONE shared owner rather than re-implementing it.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _dirty_tree  # noqa: E402,PLC0415 -- path shim above
    _dirty_tree.warn()

    if a.self_test:
        return self_test()

    root = Path(a.root)
    branch = a.branch or (os.environ.get("GITHUB_HEAD_REF")
                          or os.environ.get("GITHUB_REF_NAME") or "").strip() or None
    if not branch:
        rc, out = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        branch = out.strip() if rc == 0 and out.strip() != "HEAD" else None
    if not branch:
        print("error: no branch to grade", file=sys.stderr)
        return 2
    slug = _slug(branch)
    head, _how = resolve_head(root)
    decl = _json_text(blob(root, head, f"{LANDING_DIR}/{slug}.json")) or {}
    if a.if_declared and decl.get("landing") != LANDING_VALUE:
        msg = (f"mandate-autoland: NOT APPLICABLE — {LANDING_DIR}/{slug}.json declares "
               f"landing={decl.get('landing')!r}, not {LANDING_VALUE!r}. NOTHING WAS GRADED "
               f"here; `pr-landing-guard` grades this PR. (A PR that wanted this route and "
               f"forgot to declare it would land nowhere, not land unchecked.)")
        print(json.dumps({"ok": None, "state": "not_applicable", "why": msg}, indent=2)
              if a.json else msg)
        return 0
    changed = _changed(root, a.base, head)
    if changed is None:
        print(f"error: could not diff against {a.base}", file=sys.stderr)
        return 2
    ok, fails, notes = verdict(root, a.base, branch, decl, changed, slug,
                               pre_flight=a.pre_flight, head=head)
    if a.json:
        print(json.dumps({"ok": ok, "failures": fails, "notes": notes}, indent=2))
    else:
        for n in notes:
            print(f"  note  {n}")
        for f in fails:
            print(f"  FAIL  {f}")
        print(f"mandate-autoland: {'ADMISSIBLE' if ok else 'REFUSED'} ({len(fails)} failure(s))")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
