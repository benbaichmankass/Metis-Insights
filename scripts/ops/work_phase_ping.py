#!/usr/bin/env python3
"""Ping the operator when a work object's ``lifecycle`` changes.

Phase A of the operating-layer build. The notification contract the model
settles is **events on STATE CHANGES, never on activity** — a verdict written, a
decision recorded, a deployment made, a WIP ceiling hit. A phase moving from
``dormant`` to ``in_flight`` to ``done`` is exactly that kind of event; a session
editing a file is not, and must never ping.

⚠️ **THIS IS SESSION-INVOKED, AND THAT IS THE SAME WEAKNESS PHASE E EXISTS TO
FIX.** ``scripts/session_handoff/close_session.py`` has the identical shape (354
lines that do the right things, run only if the session remembers), which is why
a session that dies never closes out. Calling this an autonomous detector would
be a claim the code does not support. What it IS: a deterministic diff over two
git refs, so a later autonomous caller (a push-to-main hook, Phase E's exit
verification) can invoke the same function without re-deriving the comparison.

⚠️ **IT WRITES TO ``docs/claude/pending-pings.jsonl``, NOT TO TELEGRAM.** That
file is the sandbox-side queue; the VM's ``scripts/notify_on_pull.py`` drains it
on the next ``ict-git-sync`` pull and sends. So a ping is **truth in transit**
between the commit and the send, and it fails BACK: an un-committed row is a
ping that never happened, never a ping wrongly shown as delivered. Delivery is
deduped VM-side on a hash of the raw line.
"""
# wiring: manual-only - session-invoked by whoever lands a phase, and that is
# the POINT rather than an omission: this is Phase A of the operating-layer
# build, and making close-out fire without the session's cooperation is Phase E
# (the lease + reaper). Wiring it to a runner now would claim an autonomy the
# design has not built yet. `transitions()` is deliberately a pure diff over two
# git refs so that Phase E's autonomous caller can invoke it unchanged.

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OBJECTS_DIR = "docs/claude/work/objects"
PENDING = REPO_ROOT / "docs" / "claude" / "pending-pings.jsonl"

# The six lifecycle states, never collapsed. A transition INTO one of these is
# worth a ping; everything else is activity.
PING_WORTHY = {"in_flight", "waiting", "done", "accepted"}


def _git_show_dir(ref: str) -> dict[str, str]:
    """Every object file at ``ref``, as {path: text}. Missing ref/dir -> {}."""
    try:
        listing = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", ref, OBJECTS_DIR],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
    except subprocess.CalledProcessError:
        return {}
    out: dict[str, str] = {}
    for path in listing:
        if not path.endswith(".yaml"):
            continue
        try:
            out[path] = subprocess.run(
                ["git", "show", f"{ref}:{path}"],
                cwd=REPO_ROOT, capture_output=True, text=True, check=True,
            ).stdout
        except subprocess.CalledProcessError:
            continue
    return out


def _field(text: str, key: str) -> str | None:
    """Read one top-level scalar out of an object file.

    Deliberately a line scan rather than a YAML parse: this runs over historical
    refs whose files may predate any schema change, and a parse error there would
    turn a notification into a crash. A field we cannot read comes back None —
    *we did not look* — and the caller treats that as 'no transition', never as a
    transition to nothing.
    """
    for line in text.split("\n"):
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip().strip('"') or None
    return None


def transitions(base_ref: str, head_ref: str) -> list[dict]:
    """Lifecycle changes between two refs. Pure — no I/O beyond git reads."""
    before, after = _git_show_dir(base_ref), _git_show_dir(head_ref)
    found = []
    for path, text in sorted(after.items()):
        new = _field(text, "lifecycle")
        old = _field(before.get(path, ""), "lifecycle") if path in before else None
        if new is None or new == old:
            continue
        found.append({
            "object": _field(text, "id") or Path(path).stem,
            "title": _field(text, "title") or "",
            "from": old,          # None == the object is NEW at head_ref
            "to": new,
        })
    return found


def _message(t: dict) -> str:
    origin = t["from"] or "new"
    return (f"[work] {t['object']}: {origin} → {t['to']}"
            + (f" · {t['title']}" if t["title"] else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main", help="ref to compare FROM")
    ap.add_argument("--head", default="HEAD", help="ref to compare TO")
    ap.add_argument("--write", action="store_true",
                    help="append to pending-pings.jsonl (default: print only)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    found = [t for t in transitions(a.base, a.head) if t["to"] in PING_WORTHY]
    if not found:
        # Not an error, and deliberately said out loud: "nothing moved" and
        # "we could not look" must not render identically.
        print(f"work-phase-ping: no ping-worthy lifecycle change {a.base}..{a.head}")
        return 0

    for t in found:
        msg = _message(t)
        print(msg)
        if a.write:
            row = {"at": datetime.now(timezone.utc).isoformat(),
                   "target": "claude", "priority": "normal", "message": msg}
            with PENDING.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if a.write:
        print(f"work-phase-ping: queued {len(found)} — COMMIT pending-pings.jsonl "
              f"or the ping never happened (transit fails back, by design)")
    return 0


def _self_test() -> int:
    """A detector whose failure path is never exercised is indistinguishable
    from one that always passes."""
    ok = True

    got = _field('lifecycle: in_flight\nid: WO-X\n', "lifecycle")
    ok &= got == "in_flight"
    print(f"  self-test 1 (reads a field): {'PASS' if got == 'in_flight' else f'FAIL {got!r}'}")

    got = _field('id: WO-X\n', "lifecycle")
    ok &= got is None
    print(f"  self-test 2 (absent field is None, not a false transition): "
          f"{'PASS' if got is None else f'FAIL {got!r}'}")

    got = _field('lifecycle:\n', "lifecycle")
    ok &= got is None
    print(f"  self-test 3 (empty value is None, never an empty-string state): "
          f"{'PASS' if got is None else f'FAIL {got!r}'}")

    quiet = [s for s in ("dormant", "ready") if s in PING_WORTHY]
    ok &= not quiet
    print(f"  self-test 4 (dormant/ready never ping — they are not events): "
          f"{'PASS' if not quiet else f'FAIL {quiet}'}")

    m = _message({"object": "WO-1", "title": "T", "from": None, "to": "in_flight"})
    ok &= "new → in_flight" in m
    print(f"  self-test 5 (a NEW object reads 'new', not 'None'): "
          f"{'PASS' if 'new → in_flight' in m else f'FAIL {m!r}'}")

    print("work-phase-ping self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
