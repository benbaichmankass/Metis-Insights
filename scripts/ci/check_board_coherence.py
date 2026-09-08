#!/usr/bin/env python3
"""CI guard: the coordination board is resolved from ONE file, and nothing hardcodes it.

WHY THIS EXISTS
---------------
On 2026-09-07T11:28:17Z issue #6927 hit GitHub's hard 2500-comment cap. Writes
began returning 403; **reads kept succeeding**, against a board permanently
frozen at that instant.

Retiring it required editing **13 references across 6 files** (measured
2026-09-08 over every literal ``6927`` under ``.github/workflows/`` and
``scripts/``). A sweep that wide is one a session finishes halfway — and the
half it skips is invisible, because a stale hardcoded number does not look
stale. That is exactly what the work order for this change describes: two
workflows still pointed at the dead board, one writing audits that could not
land and one reading it as its INPUT.

So the number lives in ``docs/claude/board-pointer.json`` and this guard keeps
it that way. **The guard is the point, not the successor issue** — a successor
with the number sprayed back across six files is the same bug with a later fire
date.

WHAT IT CHECKS
--------------
R1  The pointer parses and validates (delegated to ``board_pointer.load``).
R2  ``board_state`` is not ``unprovisioned``. A half-finished rotation — the
    successor exists but nothing points at it, or nothing exists at all — MUST
    NOT reach ``main``: every board consumer degrades to ``could_not_check``,
    which is honest but means the repo has no working coordination board. This
    rule is the forcing function that makes provisioning and repointing land
    together.
R3  No **executable** line under ``.github/workflows/`` or ``scripts/`` contains
    a retired board number. Comments and prose are explicitly ALLOWED and
    wanted — the history of why #6927 died is the most useful thing in those
    files. What is banned is a number a machine will act on:
    ``BOARD_ISSUE: '6927'``, ``gh issue comment 6927``. That distinction is what
    keeps this guard from being the kind nobody can satisfy honestly.
R4  Every doc that a session actually reads before acting still NAMES the
    pointer. A doc that quietly stops mentioning it is how the next session
    reinvents a hardcoded number.

Usage::

    python3 scripts/ci/check_board_coherence.py            # the standing audit
    python3 scripts/ci/check_board_coherence.py --all      # identical (explicit)
    python3 scripts/ci/check_board_coherence.py --self-test
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_pointer  # noqa: E402

#: Trees whose lines are EXECUTED. Prose lives in docs/ and is not scanned for
#: R3 at all — a doc naming the dead board in its own history is correct.
SCAN_DIRS = (".github/workflows", "scripts")
SCAN_SUFFIXES = (".yml", ".yaml", ".py", ".sh")

#: R4: read before a session acts, so each must still point at the pointer.
MUST_NAME_POINTER = (
    "CLAUDE.md",
    "docs/claude/coordination-board.md",
    ".claude/skills/session-coordination/SKILL.md",
    ".claude/settings.json",
)

POINTER_NAME = "board-pointer.json"

#: This file and the pointer itself legitimately carry retired numbers as data.
R3_EXEMPT = {
    "scripts/ci/check_board_coherence.py",
    "scripts/ci/board_pointer.py",
}


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for cand in [here, *here.parents]:
        if (cand / board_pointer.POINTER_PATH).exists():
            return cand
    return Path.cwd()


def _is_comment(line: str, suffix: str) -> bool:
    """A line whose content a machine will not act on.

    Deliberately CRUDE and deliberately biased toward calling a line
    EXECUTABLE: a false 'executable' costs a reviewer one look, while a false
    'comment' is a hardcoded board number shipped silently — which is the whole
    defect this guard exists for. So only an unambiguous leading `#` counts.
    """
    stripped = line.lstrip()
    # `#` for YAML/shell/Python, `//` for the JavaScript inside a
    # github-script `script:` block — those blocks are real code embedded in a
    # .yml file, so their comment marker has to count as one too.
    return stripped.startswith("#") or stripped.startswith("//")


def retired_numbers(ptr: dict) -> list[int]:
    out = []
    for row in ptr.get("previous_boards") or []:
        n = row.get("issue")
        if isinstance(n, int) and not isinstance(n, bool):
            out.append(n)
    return out


def scan(root: Path, numbers: list[int]) -> list[str]:
    """R3 over the executable trees. Returns human-readable findings."""
    if not numbers:
        return []
    pat = re.compile(r"\b(" + "|".join(str(n) for n in numbers) + r")\b")
    findings = []
    for d in SCAN_DIRS:
        base = root / d
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or f.suffix not in SCAN_SUFFIXES:
                continue
            rel = f.relative_to(root).as_posix()
            if rel in R3_EXEMPT:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if _is_comment(line, f.suffix):
                    continue
                m = pat.search(line)
                if m:
                    findings.append(
                        f"{rel}:{i} hardcodes retired board #{m.group(1)} on an "
                        f"EXECUTABLE line — resolve it through "
                        f"scripts/ci/board_pointer.py instead:\n      {line.strip()[:160]}")
    return findings


def audit(root: Path | None = None) -> tuple[int, list[str]]:
    root = root or _repo_root()
    problems: list[str] = []

    # R1
    try:
        ptr = board_pointer.load(root / board_pointer.POINTER_PATH)
    except board_pointer.PointerError as e:
        return 1, [f"R1 the board pointer does not validate: {e}"]

    # R2
    if ptr["board_state"] == "unprovisioned":
        problems.append(
            "R2 board_state is 'unprovisioned' — there is NO live coordination "
            "board, so every board-reading check degrades to could_not_check and "
            "no session can post a claim. Provisioning and repointing must land "
            "together: run board-rotate.yml (or push "
            "automation/board-requests/<name>.json) and let it write the pointer. "
            "This rule is why a half-finished rotation cannot reach main.")

    # R3
    problems.extend(f"R3 {p}" for p in scan(root, retired_numbers(ptr)))

    # R4
    for rel in MUST_NAME_POINTER:
        f = root / rel
        if not f.exists():
            problems.append(f"R4 {rel} is missing — it is one of the surfaces a "
                            f"session reads before acting")
            continue
        if POINTER_NAME not in f.read_text(encoding="utf-8"):
            problems.append(
                f"R4 {rel} never mentions {POINTER_NAME}. A session reads this "
                f"file before its first tool call; if it does not say where the "
                f"board number comes from, the next one will hardcode it again.")

    return (1 if problems else 0), problems


# --------------------------------------------------------------------------- #
def _self_test() -> int:
    import json
    import tempfile
    fails = []

    def ok(cond, label):
        if not cond:
            fails.append(label)

    live = {"board_issue": 111, "board_state": "live", "cap": 2500,
            "warn_at": 2000, "rotate_at": 2400, "heartbeat_every_hours": 6,
            "stale_after_hours": 18,
            "previous_boards": [{"issue": 6927}]}

    def build(ptr, files):
        root = Path(tempfile.mkdtemp())
        (root / "docs" / "claude").mkdir(parents=True)
        (root / board_pointer.POINTER_PATH).write_text(json.dumps(ptr))
        for rel in MUST_NAME_POINTER:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"mentions {POINTER_NAME} here")
        for rel, body in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        return root

    # positive control: a clean tree passes
    code, probs = audit(build(live, {".github/workflows/a.yml": "env:\n  X: '1'\n"}))
    ok(code == 0 and not probs, f"a clean tree passes (got {probs})")

    # R3 fires on an executable line — the exact shape that shipped broken
    code, probs = audit(build(live, {
        ".github/workflows/a.yml": "        env:\n          BOARD_ISSUE: '6927'\n"}))
    ok(code == 1 and any("R3" in p for p in probs), "R3 catches BOARD_ISSUE: '6927'")
    ok(any("a.yml:2" in p for p in probs), "R3 names the file and LINE")

    code, probs = audit(build(live, {
        "scripts/x.sh": 'gh issue comment 6927 --repo "$R"\n'}))
    ok(code == 1 and any("R3" in p for p in probs), "R3 catches a gh comment call")

    # ...and does NOT fire on a comment. This is the half that keeps the guard
    # honest: the history of why #6927 died is worth keeping in these files.
    code, probs = audit(build(live, {
        ".github/workflows/a.yml": "# #6927 hit the 2500-comment cap on 2026-09-07\n"}))
    ok(code == 0, f"R3 allows a retired number in a COMMENT (got {probs})")
    code, probs = audit(build(live, {
        "scripts/x.py": "    # the board was 6927 until it capped\n"}))
    ok(code == 0, "R3 allows a retired number in an indented comment")
    code, probs = audit(build(live, {
        ".github/workflows/a.yml": "            // #6927 capped here\n"}))
    ok(code == 0, "R3 allows a retired number in a github-script // comment")
    code, probs = audit(build(live, {
        ".github/workflows/a.yml": "            const board = 6927;\n"}))
    ok(code == 1 and any("R3" in p for p in probs),
       "R3 STILL catches a retired number in real JS code")

    # a number that is not a retired board is not this guard's business
    code, probs = audit(build(live, {".github/workflows/a.yml": "  timeout: 6927000\n"}))
    ok(code == 0, "word boundary: 6927000 does NOT match #6927")
    code, probs = audit(build(live, {".github/workflows/a.yml": "  timeout: 12345\n"}))
    ok(code == 0, "an unrelated number passes")

    # R2: a half-finished rotation cannot land
    unp = dict(live, board_state="unprovisioned", board_issue=None)
    code, probs = audit(build(unp, {}))
    ok(code == 1 and any(p.startswith("R2") for p in probs),
       "R2 refuses an unprovisioned pointer on main")

    # R1: a malformed pointer is refused, and stops there
    bad = dict(live, board_state="live", board_issue=None)
    code, probs = audit(build(bad, {}))
    ok(code == 1 and any(p.startswith("R1") for p in probs),
       "R1 refuses a malformed pointer")

    # R4: a doc that stops naming the pointer
    root = build(live, {})
    (root / "CLAUDE.md").write_text("no mention at all")
    code, probs = audit(root)
    ok(code == 1 and any(p.startswith("R4") for p in probs),
       "R4 catches a doc that stopped naming the pointer")

    for f in fails:
        print(f"FAIL: {f}", file=sys.stderr)
    print(f"board-coherence self-test: {'FAILED' if fails else 'ok'} "
          f"({len(fails)} failure(s))", file=sys.stderr)
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="the standing audit (default)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    code, problems = audit()
    if not problems:
        print("board-coherence: OK — the board is resolved from "
              f"{board_pointer.POINTER_PATH} and nothing hardcodes it")
        return 0
    print("board-coherence: FAILED", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
