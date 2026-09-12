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


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _git_base  # noqa: E402  -- path shim above; ONE owner for base resolution


#: Entry tags, so the SUMMARY can count what it actually found. A first version
#: printed "N silent re-opening(s)" over a list that included merge-base notes
#: and unreadable-base notices -- a label describing a quantity it had not
#: computed, which is the very class this repo keeps a guard for. Both still
#: make the run exit 1; they are simply not re-openings.
_NOTE = "[note] "
_UNREADABLE = "[unreadable-base] "


def _rows(text: str) -> dict[str, dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    items = data["items"] if isinstance(data, dict) and "items" in data else data
    if not isinstance(items, list):
        return {}
    return {str(r.get("id")): r for r in items if isinstance(r, dict) and r.get("id")}


def _at_ref(ref: str, rel: str) -> tuple[str, dict[str, dict]]:
    """`(state, rows)` — and the state is never collapsed into the rows.

    ⚠️ THE OLD VERSION RETURNED `{}` FOR THREE DIFFERENT FACTS and the caller
    skipped on all of them: the file is genuinely NEW in this diff (correct to
    skip), `git show` FAILED because the ref does not exist, and the JSON did
    not parse. The last two are *we could not look*, and skipping them makes
    this guard go SILENT exactly when its input is broken — over a 1549-row
    backlog whose un-resolution it is the only thing watching.
    """
    state, text = _git_base.read_at(ref, rel, repo=REPO)
    if state != _git_base.READ:
        return state, {}
    rows = _rows(text or "")
    if not rows and (text or "").strip():
        # Readable but unparseable, or parsed to nothing while carrying bytes:
        # that is NOT "the base had no rows".
        return _git_base.UNREADABLE, {}
    return _git_base.READ, rows


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


def findings(base_ref: str) -> list[str]:
    # ⚠️ THE FORK POINT, NEVER THE TIP. "Did THIS DIFF re-open a closed row?" is
    # only the diff's doing when the comparison is against the branch's
    # ANCESTOR. Read at the tip it also fires when the BASE moved ahead --
    # measured 2026-09-12 on a branch that had not touched the row at all: a row
    # this session had itself RESOLVED on main read as `resolved -> open`, and
    # the guard's remedy line asked for a `reopen_reason` for a re-open that
    # never happened. Following it would have written a FALSE record, which is
    # worse than the false failure.
    base_ref, base_state = _git_base.resolve_base(base_ref, repo=REPO)
    out: list[str] = []
    if base_state == _git_base.TIP_UNRESOLVABLE:
        # Not an error: the tip is the pre-2026-09-12 behaviour, so this can
        # only reproduce the old false FAILURE, never a false pass.
        out.append(_NOTE + "could not resolve a merge base; comparing against the "
                   "ref as given. A finding below may belong to the BASE rather "
                   "than to this diff.")
    for rel in BACKLOGS:
        head_path = REPO / rel
        if not head_path.exists():
            continue
        state, base_rows = _at_ref(base_ref, rel)
        if state == _git_base.UNREADABLE:
            out.append(
                _UNREADABLE + f"{rel}: the base ({base_ref}) could not be READ, so whether a "
                f"closed row was re-opened is UNKNOWN. That is *we did not look*, "
                f"which is NOT the same as 'no row was re-opened'. Failing closed: "
                f"the cheapest way past this guard must not be to break its input.")
            continue
        if state == _git_base.ABSENT_AT_BASE or not base_rows:
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

    # ── the base READ, exercised against the real repo ─────────────────────
    # ⚠️ Behavioural, not pure: the three states live in a git invocation, and
    # the defect they replace (one `{}` meaning three different facts) was
    # invisible to every pure control above.
    rel = "docs/claude/health-review-backlog.json"
    st_read, rows_read = _at_ref("origin/main", rel)
    check("a readable base grades READ and returns rows",
          st_read == _git_base.READ and len(rows_read) > 0)
    st_absent, _ = _at_ref("origin/main", "docs/claude/__no-such-file__.json")
    check("a path ABSENT at the base is absent_at_base, not unreadable "
          "(a new file has nothing to regress from)",
          st_absent == _git_base.ABSENT_AT_BASE)
    st_bad, _ = _at_ref("no-such-ref-anywhere", rel)
    check("a BAD REF is unreadable, NOT absent and NOT empty -- this is the "
          "distinction that stops the guard going silent on a broken input",
          st_bad == _git_base.UNREADABLE)

    # ⚠️ AND THE BASE IS THE FORK POINT. Without this, reading at the tip blames
    # this diff for rows the BASE moved -- measured live on 2026-09-12.
    mb_ref, mb_state = _git_base.resolve_base("origin/main", repo=REPO)
    check("--base resolves to the merge base, not the tip",
          mb_state == _git_base.MERGE_BASE and mb_ref != "origin/main")
    check("an unresolvable base falls back to the ref itself, never to skipping",
          _git_base.resolve_base("no-such-ref-anywhere", repo=REPO)
          == ("no-such-ref-anywhere", _git_base.TIP_UNRESOLVABLE))

    # ⚠️ END-TO-END: does `findings()` ACT on the unreadable state, or merely
    # receive it? A plant disabling that branch ESCAPED the first battery --
    # every control above tested `_at_ref` in ISOLATION, and a state nothing is
    # proven to consume is decoration. This is the second time in one session
    # that exact escape appeared (the other in scripts/ci/check_uncarried_specs.py),
    # which is why it is written down rather than quietly patched.
    blind = findings("no-such-ref-anywhere")
    check("findings() on an UNREADABLE base reports it and does not go silent",
          any(f.startswith(_UNREADABLE) for f in blind))
    check("...and every entry it reports for that base is an unreadable notice, "
          "never a fabricated re-opening",
          blind and all(f.startswith((_UNREADABLE, _NOTE)) for f in blind))

    print(f"backlog-unresolve self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main",
                    help="Base ref to diff against. Resolved to the branch's "
                         "MERGE BASE with that ref, never its tip -- see "
                         "scripts/ci/_git_base.py for why.")
    ap.add_argument("--self-test", action="store_true", help="Planted controls; no repo state needed.")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    found = findings(args.base)
    for f in found:
        lvl = "notice" if f.startswith(_NOTE) else "error"
        print(f"::{lvl}::backlog-unresolve: {f}", file=sys.stderr)
    reopens = [f for f in found if not f.startswith((_NOTE, _UNREADABLE))]
    blind = [f for f in found if f.startswith(_UNREADABLE)]
    if reopens or blind:
        parts = []
        if reopens:
            parts.append(f"{len(reopens)} silent re-opening(s)")
        if blind:
            parts.append(f"{len(blind)} register(s) whose base could NOT BE READ "
                         f"(*we did not look*, not a clean result)")
        print(f"backlog-unresolve: {' and '.join(parts)} against {args.base}.",
              file=sys.stderr)
        return 1
    print(f"backlog-unresolve: OK — no closed row was re-opened without a reason "
          f"(base {args.base}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
