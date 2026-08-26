#!/usr/bin/env python3
# wiring: manual (analysis-time) + scripts/ci/run_guards.py (--self-test only)
"""What actually WRITES this column — and does the name describe it?

PREVENTION FOR: RC-STORED-FIELD-READ-AS-ITS-NAME (9 occurrences).

WHY. Every instance of that class reduces to one unasked question. The reader
knew the column's NAME and inferred its MEANING, and the two differed:

* ``trades.pnl`` on ``bybit_2`` read as the account's result — broker truth is
  ~8x larger (2026-08-26; the operator had to correct it from the venue UI).
* ``trades.stop_loss`` read as the DECISION-TIME risk — it carries the TRAILED
  stop, so 95 correctly-trailed rows graded as inverted brackets (2026-08-26).
* ``recorded_exit_price`` read as broker truth — it outnumbered every genuine
  broker source combined and was none of them (2026-08-24).
* ``max(proba)`` printed as ``P(volatile)`` — inverted (2026-07-30).

RULE ONE was in force for all of them and prevented none, because each read WAS
a check, aimed one level too shallow: a journal query is a measurement *of the
journal*.

WHAT THIS DOES. Given ``table.column``, it finds every site that WRITES it and
reports them. Exit status is the signal:

* **2** — the column has writers with *materially different* semantics (more
  than one distinct writing module). The name cannot describe all of them, so
  quoting it as one quantity is the mistake. **Say which writer produced the
  rows you are quoting, or do not quote the column.**
* **1** — no writer found. That is *"we could not look"*, NEVER *"nothing
  writes it"*: a column written through a helper this grep cannot see is
  exactly the case that bites. Treat as unknown.
* **0** — exactly one writing module. The name is as trustworthy as that
  writer.

⚠️ **It is a LOOKUP, and a lookup only prevents what it is pointed at.** It
cannot force itself to be run. What forces it is the SESSION BRIEF: this class
is rendered into ``CLAUDE.md`` while it lacks a prevention, so every session
reads the class before acting. Naming this tool as the prevention is a claim
about the *mechanical half* — the residual is recorded in the ledger's
``residual_risk`` rather than papered over.
"""
from __future__ import annotations

import argparse
import subprocess
from collections import defaultdict
from pathlib import Path

_SEARCH_ROOTS = ("src", "scripts", "ml")


def _rg(pattern: str) -> list[str]:
    for tool in (["rg", "-n", "--no-heading", pattern, *_SEARCH_ROOTS],
                 ["grep", "-rn", "-E", pattern, *_SEARCH_ROOTS]):
        try:
            p = subprocess.run(tool, capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if p.returncode in (0, 1):
            return [ln for ln in p.stdout.splitlines() if ln.strip()]
    return []


def writers(table: str, column: str) -> dict[str, list[str]]:
    """module -> the write sites found in it."""
    pats = [
        rf"UPDATE\s+{table}\b[^;]*\b{column}\s*=",     # SQL update
        rf"INSERT\s+INTO\s+{table}\b[^;]*\b{column}\b",  # SQL insert
        rf"[\"']{column}[\"']\s*:",                      # dict payload key
        rf"\b{column}\s*=\s*[^=]",                       # kwarg / assignment
    ]
    found: dict[str, list[str]] = defaultdict(list)
    for pat in pats:
        for line in _rg(pat):
            try:
                path, lineno, text = line.split(":", 2)
            except ValueError:
                continue
            if "/tests/" in path or path.endswith("_test.py"):
                continue
            found[str(Path(path).parent)].append(f"{path}:{lineno}: {text.strip()[:120]}")
    return dict(found)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", nargs="?", help="table.column, e.g. trades.stop_loss")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.target or "." not in a.target:
        ap.error("give a target as table.column (e.g. trades.stop_loss)")

    table, column = a.target.split(".", 1)
    found = writers(table, column)
    if not found:
        print(f"NO WRITER FOUND for {table}.{column}.")
        print("  This is 'we could not look', NOT 'nothing writes it' — a column written")
        print("  through a helper this grep cannot see is exactly the case that bites.")
        print("  Treat the column's meaning as UNKNOWN until you find the writer by hand.")
        return 1

    total = sum(len(v) for v in found.values())
    print(f"{table}.{column} — {total} write site(s) across {len(found)} module(s):\n")
    for mod in sorted(found):
        print(f"  {mod}/")
        for s in sorted(set(found[mod]))[:6]:
            print(f"    {s}")
        extra = len(set(found[mod])) - 6
        if extra > 0:
            print(f"    ... and {extra} more")
        print()
    if len(found) > 1:
        print("AMBIGUOUS: more than one module writes this column, so its NAME cannot")
        print("describe all of them. Before quoting it as a quantity, say WHICH writer")
        print("produced the rows you are reading — or do not quote the column.")
        return 2
    print("One writing module. The name is exactly as trustworthy as that writer.")
    return 0


def _self_test() -> int:
    """Prove it finds a positive, and that its states are distinguishable."""
    ok = True
    amb = writers("trades", "stop_loss")
    c1 = len(amb) > 1
    print(f"  self-test (a known-ambiguous column reports >1 writing module): "
          f"{'PASS' if c1 else 'FAIL'} -- {len(amb)} module(s)")
    ok &= c1
    none = writers("trades", "column_that_does_not_exist_xyzzy")
    c2 = not none
    print(f"  self-test (an absent column finds nothing, so silence is reachable): "
          f"{'PASS' if c2 else 'FAIL'}")
    ok &= c2
    # The two must be DISTINGUISHABLE, or "unknown" reads as "unambiguous".
    c3 = (len(amb) > 1) and (len(none) == 0)
    print(f"  self-test ('ambiguous' and 'could not look' are distinct states): "
          f"{'PASS' if c3 else 'FAIL'}")
    ok &= c3
    print("column-provenance self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
