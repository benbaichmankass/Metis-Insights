#!/usr/bin/env python3
# wiring: docs/claude/OPEN-ITEMS.json `probe.cmd`; run by scripts/ops/run_probes.py
"""Probe a REPO-LOCAL JSONL corpus for a row satisfying a declared predicate — work-plan item 3.

⚠️ NOT "W3". The 2026-08-31 operations plan's W-sequence already uses W3 for
the MERGE SERIALIZER, which was refuted by measurement and deliberately not
built — `.github/workflows/scope-overlap-audit.yml` carries that record so no
session re-proposes it. This work is item 3 of the artifact's five-item work
plan (probe coverage), a continuation of W2. The two enumerations are
different sequences and a third (`full-system-audit W2`) exists in ROADMAP.md,
so a bare W-number is ambiguous here — say which plan.

WHY A THIRD SOURCE
------------------
`probe_soak.py` reads the live diag soak surface. But not every `monitoring`
row's observable lives on the VM: the research-queue rows clear on something
that lands in a COMMITTED corpus under `docs/research/*.jsonl`, read by
`scripts/research/research_disposition.py`. Those rows carried
`probe_absent_reason` text that said, in as many words, that a probe *"is
genuinely buildable here and is the obvious next one to add — it is absent
because it needs the corpus reader wired as a probe, not because the condition
is unobservable."* That is a merely-unwritten probe, which is the one thing the
coverage rule does not permit a reason to be.

This is that reader. No network, no bearer, no VM.

AN ABSENT FILE IS `could_not_look`, NOT AN EMPTY POPULATION
-----------------------------------------------------------
The tempting reading — "the corpus is not there, so nothing matched" — is the
sub-class **C** defect (`CLAUDE.md` § "Diagnostic provenance"): an empty result
worn as a clean negative. A path that moved, a corpus not yet generated and a
corpus genuinely holding no match are three different facts, and only the third
is a negative. So ANY named file that cannot be read makes the whole verdict an
unread, naming which file — we cannot say "nothing matched" over a population we
did not finish reading.

Exit codes: 0 pass · 1 read-and-nothing-matched · 2 we could not look.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_lib  # noqa: E402


def read_files(paths: list[str], root: Path) -> tuple[list[dict] | None, str]:
    """Return (rows, note). `None` rows means we could not look — never []."""
    rows: list[dict] = []
    read_ok: list[str] = []
    for rel in paths:
        p = root / rel
        if not p.exists():
            return None, (f"{rel} is ABSENT. That is not an empty population — a "
                          f"corpus that moved and a corpus with no match are "
                          f"different facts.")
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"{rel} could not be read: {exc}"
        before = len(rows)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # One malformed line is not an unread of the file, but it IS
                # unread of that line — counted so the denominator stays honest.
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        read_ok.append(f"{rel}:{len(rows) - before}")
    if not read_ok:
        return None, "no --file was given, so nothing was read"
    return rows, f"read {len(rows)} row(s) from {len(read_ok)} file(s) [{', '.join(read_ok)}]"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--file", action="append", default=[],
                    help="repo-relative JSONL corpus. Repeatable; ALL must be readable.")
    ap.add_argument("--require", action="append", default=[],
                    help="condition `path=value`, `path~a,b` or `path>value`; "
                         "ALL must hold on ONE row. Repeatable.")
    ap.add_argument("--positive-control",
                    help="a condition that DOES hold today. If it does not fire, the "
                         "verdict is could_not_look — a reader proven blind must not "
                         "emit a confident negative.")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.file or not args.require:
        ap.error("--file and at least one --require are mandatory")

    try:
        conds = [probe_lib.parse_condition(c) for c in args.require]
        control = (probe_lib.parse_condition(args.positive_control)
                   if args.positive_control else None)
    except ValueError as exc:
        return probe_lib.die_unlooked(str(exc))

    rows, note = read_files(args.file, Path(args.root))
    if rows is None:
        return probe_lib.die_unlooked(note)
    return probe_lib.report(rows, conds, args.require, note,
                            control, args.positive_control or "")


def _self_test() -> int:
    import tempfile
    probe_lib.self_test()
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "good.jsonl").write_text(
            '{"state": "accruing"}\nnot-json\n\n{"state": "ok"}\n', encoding="utf-8")
        rows, note = read_files(["good.jsonl"], root)
        ok(rows is not None and len(rows) == 2,
           "a malformed line is skipped without making the file an unread")
        ok("read 2 row(s)" in note, "the denominator is reported")

        rows, note = read_files(["nope.jsonl"], root)
        ok(rows is None and "ABSENT" in note,
           "an ABSENT file is could-not-look, NEVER an empty population")

        rows, note = read_files(["good.jsonl", "nope.jsonl"], root)
        ok(rows is None,
           "one unreadable file among several makes the WHOLE verdict an unread — "
           "we cannot say 'nothing matched' over a population we did not finish reading")

        rows, _ = read_files([], root)
        ok(rows is None, "no files named is an unread, not a clean zero")

        ok(main(["--root", d, "--file", "good.jsonl", "--require", "state=accruing"]) == 0,
           "end-to-end pass")
        ok(main(["--root", d, "--file", "good.jsonl", "--require", "state=infeasible"]) == 1,
           "end-to-end fail on a real negative")
        ok(main(["--root", d, "--file", "nope.jsonl", "--require", "state=x"]) == 2,
           "end-to-end could_not_look on an absent corpus — and it is NOT 1")
        ok(main(["--root", d, "--file", "good.jsonl", "--require", "state=infeasible",
                 "--positive-control", "state=accruing"]) == 1,
           "a firing control leaves a genuine negative as a negative")
        ok(main(["--root", d, "--file", "good.jsonl", "--require", "state=infeasible",
                 "--positive-control", "nosuchfield=accruing"]) == 2,
           "a control that cannot fire turns the negative into a declared unread")

    print(f"probe-file: self-test OK — {fired} planted controls all fire")
    return 0


if __name__ == "__main__":
    sys.exit(main())
