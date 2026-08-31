#!/usr/bin/env python3
"""CI guard: every cron'd workflow must be watched by claude-run-failure-alert.

WHY (the defect class this guard exists to prevent recurring)
-------------------------------------------------------------
``.github/workflows/claude-run-failure-alert.yml`` is the only thing that
notices a **scheduled** run dying. Its own header states the argument: with a
relay "at least someone is waiting and will eventually poke the issue. On a
nightly, NOBODY is waiting, so a failure is not merely un-notified — it is
unobservable until an audit happens to read the Actions API."

That list is maintained **by hand**, and it has drifted twice.

* 2026-08-13 — the cron'd workflows were added after ``replay-pregate-nightly``
  had failed 3 of 3 consecutive nights emitting nothing.
* 2026-08-21 — the population was re-counted (by parsing each ``on:`` block
  rather than grepping) and the header was corrected to **"There are 12 cron'd
  workflows, not 10"**, with "ALL 12 are now listed."
* 2026-08-31 — measured again: there are **14**, and **2 are unlisted** —
  ``alpaca-settlement-soak-watch`` and ``research-queue-dispatch``. The second
  is the research queue's scheduler, and it had **already failed twice**
  (runs on 2026-08-29 and 2026-08-30) with nothing pinging.

So the list has been asserted-complete and been false at least twice, which is
this repo's standing argument for a detector over another manual sweep — the
same reasoning as ``workflow-catalog`` (whose doc index was 45.9% incomplete),
``api-tier-policy`` and ``provenance-consumer-guard``. Every inventory here
that STAYS correct has CI behind it; this one never did, so every cron added
since could land unwatched and **none of them announced itself**.

WHAT IT CHECKS — two directions, because the gap runs both ways
---------------------------------------------------------------
**A. COVERAGE.** Every workflow with a live ``schedule:``/``cron:`` in its
``on:`` block must have its ``name:`` in the listener's ``workflows:`` list, or
carry an explicit exemption (below).

**B. NO PHANTOMS.** Every entry in the listener's ``workflows:`` list must
resolve to the ``name:`` of a real workflow file. GitHub matches
``workflow_run.workflows`` on the workflow's **name**, not its filename — so a
renamed workflow silently stops being watched and the entry becomes inert.
There is no error, no warning, and no run: exactly the shape of an alarm that
looks armed and is not.

THE EXEMPTION IS VERIFIED, NOT PRESENCE-ONLY
--------------------------------------------
A cron may be exempted with a line in the listener:

    # cron-watch-exempt: <workflow name> — <reason>

The named workflow must actually BE a cron'd workflow and the reason must be
non-empty. This follows ``diagnostic-provenance-guard``'s rule and the direct
lesson of ``new-table-wiring-guard``, whose presence-only marker made the
cheapest way to silence a real finding *naming a table that does not exist*.
A guard that is cheaper to lie to than to satisfy is worse than no guard.

Parsing note: the ``on:`` block is parsed structurally rather than grepped,
because a commented-out ``cron:`` reads like a live one to grep — the exact
error the 2026-08-21 re-count was correcting.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections.abc import Sequence

LISTENER = ".github/workflows/claude-run-failure-alert.yml"
WORKFLOW_GLOB = ".github/workflows/*.y*ml"

_EXEMPT_RE = re.compile(r"#\s*cron-watch-exempt:\s*(.+?)\s+[—-]{1,2}\s*(.+?)\s*$")


def _strip_comments(block: str) -> str:
    """Drop comment-only lines so a commented-out `cron:` is not read as live."""
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


def on_block(text: str) -> str:
    """Return the workflow's `on:` block (up to the next top-level key, or EOF).

    The EOF case is not academic: a file whose `on:` block is its last section
    would otherwise read as having no schedule at all — a false NEGATIVE, i.e.
    the guard silently exempting a cron. Caught by control 6 of the self-test.
    """
    m = re.search(r"^on:\s*$", text, re.MULTILINE)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^\S", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def workflow_name(text: str, path: str) -> str:
    """The `name:` GitHub matches `workflow_run.workflows` against."""
    m = re.search(r"^name:\s*(.+?)\s*$", text, re.MULTILINE)
    if not m:
        return os.path.basename(path)
    return m.group(1).strip().strip("\"'")


def has_live_cron(text: str) -> bool:
    blk = _strip_comments(on_block(text))
    return bool(re.search(r"^\s+schedule:", blk, re.MULTILINE)) and bool(
        re.search(r"^\s*-\s*cron:", blk, re.MULTILINE)
    )


def scan_workflows(root: str = ".") -> tuple[dict[str, str], set[str]]:
    """Return ({name: path} for all workflows, {names of cron'd workflows})."""
    names: dict[str, str] = {}
    crons: set[str] = set()
    for path in sorted(glob.glob(os.path.join(root, WORKFLOW_GLOB))):
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        name = workflow_name(text, path)
        names[name] = path
        if has_live_cron(text):
            crons.add(name)
    return names, crons


def watched_names(listener_text: str) -> set[str]:
    """Parse `on.workflow_run.workflows:` — the names actually watched."""
    m = re.search(r"workflow_run:\s*\n\s+workflows:\s*\n(.*?)\n\s*types:", listener_text, re.DOTALL)
    if not m:
        return set()
    out: set[str] = set()
    for line in m.group(1).splitlines():
        s = line.strip()
        if s.startswith("#") or not s.startswith("-"):
            continue
        out.add(s[1:].strip().strip("\"'"))
    return out


def exemptions(listener_text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in listener_text.splitlines():
        m = _EXEMPT_RE.search(line.strip())
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def evaluate(names: dict[str, str], crons: set[str], listener_text: str):
    """Return (unwatched, phantoms, bad_exemptions, exempted)."""
    watched = watched_names(listener_text)
    exempt_rows = exemptions(listener_text)
    exempted = {n for n, _ in exempt_rows}

    bad_exemptions: list[str] = []
    for name, reason in exempt_rows:
        if name not in crons:
            bad_exemptions.append(
                f"{name!r} is exempted but is not a cron'd workflow "
                f"(the exemption names nothing this guard would have flagged)"
            )
        if not reason:
            bad_exemptions.append(f"{name!r} is exempted with an empty reason")

    unwatched = sorted(crons - watched - exempted)
    phantoms = sorted(w for w in watched if w not in names)
    return unwatched, phantoms, bad_exemptions, sorted(exempted)


def _report(unwatched, phantoms, bad_exemptions, exempted, n_crons, n_watched) -> int:
    print(
        f"cron-failure-watch: {n_crons} cron'd workflows · "
        f"{n_watched} watched entries · {len(exempted)} exempt"
    )
    failed = False
    if unwatched:
        failed = True
        print("\nFAIL — cron'd workflows NOT watched by claude-run-failure-alert:")
        for n in unwatched:
            print(f"  - {n}")
        print(
            "\n  A scheduled run that dies here notifies NOBODY. Add the workflow's\n"
            "  `name:` to `on.workflow_run.workflows` in\n"
            f"  {LISTENER}, or add:\n"
            "      # cron-watch-exempt: <name> — <why a failure is observed elsewhere>"
        )
    if phantoms:
        failed = True
        print("\nFAIL — watched entries that match no workflow `name:` (inert):")
        for n in phantoms:
            print(f"  - {n}")
        print(
            "\n  GitHub matches workflow_run.workflows on the workflow NAME. An entry\n"
            "  naming nothing never fires, with no error — an alarm that looks armed."
        )
    if bad_exemptions:
        failed = True
        print("\nFAIL — invalid `cron-watch-exempt` declarations:")
        for m in bad_exemptions:
            print(f"  - {m}")
    if not failed:
        print("cron-failure-watch: OK — every cron'd workflow is watched or exempt")
    return 1 if failed else 0


# ── self-test: planted controls, so a vacuous pass is impossible ────────────
_LISTENER_FIXTURE = """name: claude-run-failure-alert
on:
  workflow_run:
    workflows:
      - alpha
      - beta
    types: [completed]
"""


def _self_test() -> int:
    names = {"alpha": "a.yml", "beta": "b.yml", "gamma": "c.yml"}
    crons = {"alpha", "beta"}

    # control 1 — a clean state passes
    u, p, b, _ = evaluate(names, crons, _LISTENER_FIXTURE)
    assert (u, p, b) == ([], [], []), f"clean state should pass, got {u} {p} {b}"

    # control 2 — an unwatched cron fires
    u, _, _, _ = evaluate(names, crons | {"gamma"}, _LISTENER_FIXTURE)
    assert u == ["gamma"], f"unwatched cron not caught: {u}"

    # control 3 — a phantom watch entry fires
    _, p, _, _ = evaluate({"alpha": "a.yml"}, {"alpha"}, _LISTENER_FIXTURE)
    assert p == ["beta"], f"phantom entry not caught: {p}"

    # control 4 — an exemption silences it, but only a valid one
    txt = _LISTENER_FIXTURE + "\n# cron-watch-exempt: gamma — pings the operator itself\n"
    u, _, b, ex = evaluate(names, crons | {"gamma"}, txt)
    assert u == [] and b == [] and ex == ["gamma"], f"valid exemption failed: {u} {b} {ex}"

    # control 5 — an exemption naming a NON-cron is rejected (cannot lie cheaply)
    txt = _LISTENER_FIXTURE + "\n# cron-watch-exempt: delta — made up\n"
    _, _, b, _ = evaluate(names, crons, txt)
    assert b and "delta" in b[0], f"bogus exemption not rejected: {b}"

    # control 6 — a commented-out cron must NOT count as live
    assert not has_live_cron("name: x\non:\n  workflow_dispatch:\n  # schedule:\n  #   - cron: '0 1 * * *'\n")
    assert has_live_cron("name: x\non:\n  schedule:\n    - cron: '0 1 * * *'\n")

    print("cron-failure-watch: self-test OK — 6 planted controls all fire")
    return 0


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    listener_path = os.path.join(args.root, LISTENER)
    if not os.path.exists(listener_path):
        print(f"cron-failure-watch: FAIL — listener not found at {listener_path}")
        return 1
    with open(listener_path, encoding="utf-8", errors="replace") as fh:
        listener_text = fh.read()
    names, crons = scan_workflows(args.root)
    unwatched, phantoms, bad, exempted = evaluate(names, crons, listener_text)
    return _report(
        unwatched, phantoms, bad, exempted, len(crons), len(watched_names(listener_text))
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
