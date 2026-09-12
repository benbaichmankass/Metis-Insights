#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (backlog-unresolve-guard)
"""Did this change silently RE-OPEN a backlog row somebody else had closed?

THE DEFECT
----------
`BL-20260814-HAND-RESOLVED-BACKLOG-MERGE-SILENTLY-REVERTED-SIX-ITEMS-INCLUDING-A-RESOLUTION`.
The review backlogs are append-only *in intent*, so a conflict in them looks
purely additive and invites the fast resolution: keep my new rows, keep theirs,
move on. **They are not purely additive** — sessions also EDIT rows: adding
history, flipping `status`, stamping `resolved_at`.

MEASURED 2026-08-14: one hand-resolved conflict took its own side wholesale and
silently discarded main's edits to **six** rows the author had never touched,
including flipping `BL-20260814-IB-EVENTLOOP-CONTENTION` from `resolved` back to
`mitigated_fix_in_pr` and clearing its `resolved_at`.

**A re-opened row is not a loud failure — it looks exactly like a row that was
never closed.** The next reader re-investigates work already done, and the
session that closed it has no way to notice. (That is the same cost
`BL-20260912-A-LANDED-FIX-LEAVES-ITS-BACKLOG-ROW-OPEN-SO-THE-ROW-RECRUITS-THE-NEXT-SESSION-INTO-REBUILDING-IT`
records from the other direction, and it has four measured instances.)

WHY THE OBVIOUS PROOFS ARE BLIND TO IT
--------------------------------------
* **Union-by-id** passes: the id is on both sides.
* **`register-field-loss-guard`** passes: no field was deleted — `status` still
  exists, it just says something else.
* **Item COUNT** passes, and the row's own criteria say in terms that count
  "is not a sufficient invariant and must not be what the guard checks".

WHAT IT GRADES
--------------
Two regressions, per row, diff-scoped against ``--base``:

1. ``status`` moves from TERMINAL to LIVE;
2. a populated ``resolved_at`` is emptied or removed.

⚠️ **TERMINALITY IS NOT RE-DERIVED HERE.** It comes from the ONE owner,
``scripts/reports/backlog_counts.py::is_open_status`` (re-exported by
``scripts/ops/_backlog.py``) — a second copy of "which rows are live" is
precisely the defect
``BL-20260823-BACKLOG-TRIAGE-SEES-47-PERCENT-OF-THE-LIVE-ROWS`` was filed
against, and that row's fix says so in its own docstring. If the predicate
cannot be loaded this guard REFUSES rather than guessing.

THE ESCAPE HATCH, AND WHY IT MUST BE FRESH
------------------------------------------
Re-opening a row is legitimate — a fix that did not hold is one of the most
valuable things this backlog records. The criterion is *"unless the same commit
records why"*, so the head row must carry a reason **that this diff introduced
or changed**:

* ``reopen_reason`` (a non-empty string), or
* the pair ``reopened_at`` + ``reopened_by`` already used by
  ``BL-20260730-PR-CI-NOT-ATTACHING``.

⚠️ **A STALE STAMP GRANTS NOTHING.** If the reason is byte-identical to the
base, it explains an EARLIER re-opening and says nothing about this one — so a
row re-opened once could be re-opened silently forever. The freshness test is
what makes the hatch cost the same as the honesty it stands for.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
# ⚠️ The graded repo (`REPO`) is swapped by the self-test to a temp fixture. The
# canonical predicate must NOT follow it — it lives in THIS checkout, always.
# Conflating the two made the first self-test look for backlog_counts.py inside
# the fixture and fail with a FileNotFoundError that read like a packaging
# fault rather than a test-harness one.
_OWN_REPO = pathlib.Path(__file__).resolve().parents[2]

BACKLOGS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
    "docs/claude/research-review-backlog.json",
)

_REASON_TEXT_FIELDS = ("reopen_reason", "reopened_by", "reopened_reason")
_REASON_PAIR = ("reopened_at", "reopened_by")


_PREDICATE = None


def _is_open_status(raw: object) -> bool:
    """Live? Loaded ONCE from the ONE owner; never re-derived here."""
    global _PREDICATE
    if _PREDICATE is not None:
        return bool(_PREDICATE(raw))
    src = _OWN_REPO / "scripts" / "reports" / "backlog_counts.py"
    spec = importlib.util.spec_from_file_location("_bc_for_unresolve", src)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging fault
        raise RuntimeError(
            f"backlog-unresolve: cannot load the canonical open-status predicate "
            f"from {src} — refusing to guess which statuses are terminal rather "
            f"than shipping a second definition of it")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _PREDICATE = mod.is_open_status
    return bool(_PREDICATE(raw))


def _rows(text: str) -> dict[str, dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    items = data["items"] if isinstance(data, dict) and "items" in data else data
    if not isinstance(items, list):
        return {}
    return {str(r.get("id")): r for r in items if isinstance(r, dict) and r.get("id")}


def _at_ref(ref: str, rel: str) -> dict[str, dict]:
    out = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=REPO,
                         capture_output=True, text=True)
    if out.returncode != 0:
        return {}
    return _rows(out.stdout)


def _nonempty(value: object) -> bool:
    return bool(str(value or "").strip())


def _fresh_reason(base_row: dict, head_row: dict) -> str | None:
    """The reason this diff introduced, or None. A stale stamp grants nothing."""
    for field in _REASON_TEXT_FIELDS:
        head, base = head_row.get(field), base_row.get(field)
        if _nonempty(head) and head != base:
            return field
    if all(_nonempty(head_row.get(f)) for f in _REASON_PAIR):
        if any(head_row.get(f) != base_row.get(f) for f in _REASON_PAIR):
            return "+".join(_REASON_PAIR)
    return None


def comparison_point(base_ref: str) -> tuple[str, str]:
    """``(ref, how)`` — the commit this diff is graded AGAINST.

    ⚠️ IT IS THE MERGE BASE, NOT THE BASE REF'S TIP, AND THE DIFFERENCE IS A
    FALSE POSITIVE THAT REDDENS EVERY STALE BRANCH. Measured 2026-09-12, hours
    after this guard shipped: branch `claude/mi279-u5-channel-separation-clause-a`
    was reported as silently re-opening
    `BL-20260909-PEAK-R-SENTINEL-ROWS-STILL-LIVE-WHILE-ITS-BACKLOG-ROW-READS-RESOLVED`.
    It had changed nothing — the row read `open` at the merge base `331bd2039`
    AND on the branch, and `main` RESOLVED it afterwards in `5079dfefd`. Against
    the tip that is indistinguishable from a re-opening; against the merge base
    it is correctly nothing at all.

    On a register `main` moves every few minutes, so nearly every open branch is
    behind on it — which made this guard red the repo for doing nothing, the
    shape its own docstring calls a guard reddening the repo for the right
    behaviour. It is also a RECURRENCE: the same defect was filed the SAME DAY
    against `register-field-loss`
    (``BL-20260912-REGISTER-FIELD-LOSS-COMPARES-MAINS-TIP-NOT-THE-MERGE-BASE-SO-A-MERELY-STALE-BRANCH-READS-AS-A-ROW-LOSS``)
    and then written into this guard anyway, which is why the fix ships with a
    planted control rather than a note.

    ⚠️ THE FALLBACK IS NAMED, NEVER SILENT. Where no merge base exists (a shallow
    clone, an unrelated history) the tip is used and ``how`` says so, because a
    comparison point nobody can identify is how a verdict becomes unattributable.
    """
    out = subprocess.run(["git", "merge-base", "HEAD", base_ref], cwd=REPO,
                         capture_output=True, text=True)
    base = out.stdout.strip()
    if out.returncode == 0 and base:
        return base, f"merge-base(HEAD, {base_ref})"
    return base_ref, f"{base_ref} TIP (no merge base — a stale branch may false-positive)"


def findings(base_ref: str) -> list[str]:
    base_ref, _how = comparison_point(base_ref)
    out: list[str] = []
    for rel in BACKLOGS:
        head_path = REPO / rel
        if not head_path.exists():
            continue
        base_rows = _at_ref(base_ref, rel)
        if not base_rows:
            continue  # a file new in this diff has nothing to regress FROM
        head_rows = _rows(head_path.read_text(encoding="utf-8"))
        for rid, base_row in base_rows.items():
            head_row = head_rows.get(rid)
            if head_row is None:
                continue  # a REMOVED row is register-removal's subject, not this one
            base_terminal = not _is_open_status(base_row.get("status"))
            head_live = _is_open_status(head_row.get("status"))
            reason = _fresh_reason(base_row, head_row)
            if base_terminal and head_live and reason is None:
                out.append(
                    f"{rel}: {rid} moved status {base_row.get('status')!r} -> "
                    f"{head_row.get('status')!r}, i.e. a CLOSED row is live again, "
                    f"and this change records no reason. A re-opened row reads "
                    f"exactly like one that was never closed, so the next reader "
                    f"re-investigates finished work. If the re-open is deliberate "
                    f"— and a fix that did not hold is a valuable finding — say so "
                    f"in `reopen_reason`, or in `reopened_at` + `reopened_by`.")
            base_stamp, head_stamp = base_row.get("resolved_at"), head_row.get("resolved_at")
            if _nonempty(base_stamp) and not _nonempty(head_stamp) and reason is None:
                out.append(
                    f"{rel}: {rid} had resolved_at {str(base_stamp)[:24]!r} and this "
                    f"change clears it, with no reason recorded. Clearing the stamp "
                    f"erases WHEN the work was done even where `status` survives.")
    return out


# ── SELF-TEST ───────────────────────────────────────────────────────────────
# Planted controls in BOTH directions. A guard whose failure path is never
# exercised is indistinguishable from one that always passes.
def _self_test() -> int:
    import tempfile
    ok = True

    def check(label: str, got: bool) -> None:
        nonlocal ok
        ok &= got
        print(f"  self-test {'ok  ' if got else 'FAIL'}: {label}")

    check("terminal statuses are terminal (from the canonical predicate)",
          all(not _is_open_status(s) for s in ("resolved", "superseded", "invalid", "wont_fix")))
    check("live statuses are live", all(_is_open_status(s) for s in ("open", "kept_open")))

    def run(base_row: dict, head_row: dict) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
            rel = BACKLOGS[0]
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(json.dumps([base_row]), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            (repo / rel).write_text(json.dumps([head_row]), encoding="utf-8")
            global REPO
            saved, REPO = REPO, repo
            try:
                return findings("HEAD")
            finally:
                REPO = saved

    closed = {"id": "BL-X", "status": "resolved", "resolved_at": "2026-08-01"}
    check("a silent re-open FAILS",
          any("live again" in f for f in run(closed, {**closed, "status": "open"})))
    check("a re-open WITH a fresh reason passes",
          run(closed, {**closed, "status": "open", "reopen_reason": "the fix did not hold"}) == [])
    check("a STALE reason grants nothing",
          any("live again" in f for f in run({**closed, "reopen_reason": "old"},
                                             {**closed, "status": "open", "reopen_reason": "old"})))
    check("the reopened_at + reopened_by pair is accepted when fresh",
          run(closed, {**closed, "status": "open", "reopened_at": "2026-09-12",
                       "reopened_by": "MI-279"}) == [])
    check("clearing a populated resolved_at FAILS",
          any("clears it" in f for f in run(closed, {"id": "BL-X", "status": "resolved"})))
    check("an untouched closed row is CLEAN (the control)", run(closed, dict(closed)) == [])
    check("an ordinary open row edited in place is CLEAN",
          run({"id": "BL-Y", "status": "open"}, {"id": "BL-Y", "status": "open", "detail": "more"}) == [])
    check("a row closed IN this diff is CLEAN (the common, correct direction)",
          run({"id": "BL-Z", "status": "open"},
              {"id": "BL-Z", "status": "resolved", "resolved_at": "2026-09-12"}) == [])

    # ⚠️ THE CONTROL THIS GUARD SHIPPED WITHOUT, AND PAID FOR WITHIN HOURS.
    # Every control above builds a LINEAR two-commit repo, where the base ref's
    # tip and the merge base are the same commit — so a guard graded against
    # either passes identically and the distinction is invisible. The real world
    # is not linear: a branch cut from `main`, with `main` then moving on, is
    # the ordinary case on a register that changes every few minutes.
    #
    # MEASURED 2026-09-12: this guard reported branch
    # `claude/mi279-u5-channel-separation-clause-a` as silently re-opening
    # `BL-20260909-PEAK-R-SENTINEL-ROWS-STILL-LIVE-WHILE-ITS-BACKLOG-ROW-READS-RESOLVED` when it had changed nothing at all —
    # the row read `open` at the merge base AND on the branch, and `main`
    # resolved it afterwards. A guard reddening a PR for doing nothing.
    def run_behind() -> list[str]:
        """base -> branch (no change) while MAIN resolves the row afterwards."""
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            def g(*a):
                subprocess.run(["git", *a], cwd=repo, check=True,
                               capture_output=True)
            g("init", "-q", "-b", "main")
            g("config", "user.email", "t@t")
            g("config", "user.name", "t")
            rel = BACKLOGS[0]
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            row = {"id": "BL-BEHIND", "status": "open"}
            (repo / rel).write_text(json.dumps([row]), encoding="utf-8")
            g("add", "-A")
            g("commit", "-qm", "merge base")
            g("checkout", "-q", "-b", "feature")
            # the branch touches something ELSE entirely
            (repo / "unrelated.txt").write_text("x", encoding="utf-8")
            g("add", "-A")
            g("commit", "-qm", "unrelated work")
            g("checkout", "-q", "main")
            (repo / rel).write_text(json.dumps(
                [{**row, "status": "resolved", "resolved_at": "2026-09-12"}]),
                encoding="utf-8")
            g("add", "-A")
            g("commit", "-qm", "main closes the row")
            g("checkout", "-q", "feature")
            global REPO
            saved, REPO = REPO, repo
            try:
                return findings("main")
            finally:
                REPO = saved

    check("PLANTED: a branch merely BEHIND — main closed the row after the merge "
          "base — is CLEAN, not a re-opening", run_behind() == [])

    print(f"backlog-unresolve self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main", help="Base ref to diff against.")
    ap.add_argument("--self-test", action="store_true", help="Planted controls; no repo state needed.")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    _point, _how = comparison_point(args.base)
    found = findings(args.base)
    for f in found:
        print(f"::error::backlog-unresolve: {f}", file=sys.stderr)
    if found:
        print(f"backlog-unresolve: {len(found)} silent re-opening(s) against "
              f"{_point[:12]} [{_how}].", file=sys.stderr)
        return 1
    # Name the comparison point on the PASS too: a verdict whose basis is not
    # printed cannot be checked, and this guard's one false-positive class was
    # precisely a wrong comparison point.
    print(f"backlog-unresolve: OK — no closed row was re-opened without a reason "
          f"(graded against {_point[:12]} [{_how}]).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
