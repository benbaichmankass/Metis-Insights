#!/usr/bin/env python3
"""MI-236 — refuse a diff that adds an ``in_flight`` row the register cannot support.

``in_flight`` goes false by time passing and nothing decays it. The grading
lives in ``scripts/ops/owner_liveness.py``; this is the CARRIER — the thing that
runs without anyone choosing to run it, which is the row's done-condition
(*"something other than a manual sweep"*) and MI-246's bar (*"SURFACE or REFUSE
without anyone choosing to look"*).

WHY CI, AND NOT A CRON
----------------------
Every scheduled producer in this repo fires late or not at all — `probes.yml`
+4h53m, due-list +4h07m, macro-valuation +5h22m and nothing on 09-02,
`work-digest` five firings against twenty-four declared. MI-235 chose its
carrier on MEASURED FIRING rather than on a cron expression, and the same
reasoning lands here on a different answer: **a guard step runs on every PR, and
that is the one carrier in this repo measured to actually fire.** It needs no
`mcp__*` tool because the registry is a reliable NEGATIVE (see the module
docstring), so the evidence it grades is already in the repo.

WHY A RATCHET AND NOT A FLAT FAIL
---------------------------------
Measured on `main` @ 4d8b30109, 2026-09-11: **17 of 35** `in_flight` rows across
the two registers are already unsupported. A guard that failed on the absolute
count would red EVERY open PR on day one, which is how a guard gets disabled
instead of fixed — the reasoning `check_pr_queue_watch.py` states in terms, and
`diagnostic-provenance-guard` paid for from the other side (its residue sat at
exactly 52 findings for 26 days behind a diff-scoped step).

So: the census over the WHOLE population is printed every run — nothing is
grandfathered out of VIEW — and the REFUSAL is scoped to what the diff itself
adds. The debt cannot grow, it is visible while it shrinks, and the guard arms
fully the moment it reaches zero, with no flag to unset.

⚠️ **THE BASE IS GRADED WITH THE HEAD'S REGISTRY, DELIBERATELY.** Grading each
side with its own `SESSIONS.json` would fail a PR that merely RECORDS a session
going idle — punishing the act of writing down the truth, which is the one
behaviour this whole mechanism depends on. Holding the registry fixed isolates
the author's actual contribution: the count can then only rise if the diff added
or re-owned an `in_flight` row.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ops"))

import owner_liveness as ol  # noqa: E402

NAME = "stale-in-flight"

NO_INCREASE = "no_increase"
INCREASED = "increased"
BASE_UNREADABLE = "base_unreadable"

#: Three states, never collapsed. `base_unreadable` is *we could not compare* —
#: it does NOT fail (there is nothing to grade) and is never silent, because a
#: guard that cannot resolve its base while printing OK is indistinguishable
#: from one that checked. CLAUDE.md records that exact failure being a FINDING
#: for `session-brief-guard`, not a pass.
RATCHET_STATES = (NO_INCREASE, INCREASED, BASE_UNREADABLE)


def _git(args: list[str], cwd: Path = REPO_ROOT) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return p.returncode, p.stdout


def _materialise_base(base: str, dest: Path) -> bool:
    """Extract the three register paths at ``base`` into ``dest``.

    Extracting and re-running the SAME scanner is deliberate: a bespoke
    "read the old file" path would be a second reader of `lifecycle` and
    `state`, free to disagree with the one that graded HEAD about which rows
    are in flight — which is the one thing a second reader must never do.
    """
    rc, _ = _git(["rev-parse", "--verify", f"{base}^{{commit}}"])
    if rc != 0:
        return False
    tar = dest / "base.tar"
    rc, _ = _git(["archive", "-o", str(tar), base,
                  ol.CHECKLIST_RELPATH, ol.OBJECTS_RELDIR])
    if rc != 0 or not tar.exists():
        return False
    try:
        subprocess.run(["tar", "-xf", str(tar), "-C", str(dest)],
                       capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def run(base: str = "origin/main") -> int:
    registry = ol.read_registry(REPO_ROOT)
    head_rows = ol.in_flight_rows(REPO_ROOT, registry)
    head_bad = ol.unsupported(head_rows)
    counts = ol.census(head_rows)

    print(f"{NAME}: registry="
          f"{'unread' if registry is None else str(len(registry)) + ' rows'}")
    for reg in (*ol.REGISTERS, "total"):
        c = counts.get(reg, {})
        print(f"{NAME}: {reg:<9} in_flight={sum(c.values()):<3} "
              f"supported={c.get(ol.SUPPORTED, 0):<3} "
              f"unsupported={c.get(ol.UNSUPPORTED, 0):<3} "
              f"could_not_establish={c.get(ol.COULD_NOT_ESTABLISH, 0)}")

    for row in head_bad:
        g = row.grade
        age = ("?" if g.observation_age_minutes is None
               else f"{g.observation_age_minutes / 60:.1f}h")
        print(f"{NAME}:   UNSUPPORTED [{row.register}] {row.row_id} "
              f"— owner {g.session_id} is {g.activity} "
              f"(registry {g.registry_state}, observed {g.observation_state} "
              f"{age} ago)")

    # ── the ratchet ──────────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        if not _materialise_base(base, dest):
            print(f"{NAME}: ratchet=base_unreadable — {base!r} could not be "
                  f"resolved, so the diff's own contribution was graded by "
                  f"NOBODY. This is a FINDING about the CI checkout, not a pass.")
            print(f"{NAME}: OK (census only)")
            return 0
        # Base rows graded with the HEAD registry — see the module docstring.
        base_bad = len(ol.unsupported(ol.in_flight_rows(dest, registry)))

    delta = len(head_bad) - base_bad
    print(f"{NAME}: ratchet base={base_bad} head={len(head_bad)} delta={delta:+d}")
    if delta > 0:
        print(f"{NAME}: FAIL — this diff adds {delta} `in_flight` row(s) whose "
              f"owner the registry records as not working. Either name an owner "
              f"the registry shows active, or do not mark the row `in_flight`. "
              f"⚠️ This says the EVIDENCE does not support the claim; it is not "
              f"a claim that the work is abandoned, and the row must NOT be "
              f"auto-closed to satisfy it.")
        return 1
    print(f"{NAME}: OK")
    return 0


def self_test() -> int:
    """Exercises the ratchet's own arithmetic and the load-bearing states."""
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    reg = {
        "session_aaaaaaaa": {"state": "working",
                             "state_observed_at": "2026-09-11T06:00:00Z"},
        "session_bbbbbbbb": {"state": "idle",
                             "state_observed_at": "2026-09-09T06:00:00Z"},
        "session_cccccccc": {"state": "archived"},
        "session_dddddddd": {"state": "wat"},
        "session_eeeeeeee": {},
    }
    g = ol.grade_owner_activity
    check("active", g("session_aaaaaaaa", reg).support, ol.SUPPORTED)
    check("dormant", g("session_bbbbbbbb", reg).activity, ol.DORMANT)
    check("dormant unsupported", g("session_bbbbbbbb", reg).support,
          ol.UNSUPPORTED)
    check("terminal", g("session_cccccccc", reg).activity, ol.TERMINAL)
    check("terminal unsupported", g("session_cccccccc", reg).support,
          ol.UNSUPPORTED)
    # A vocabulary word we do not know must NOT fall into a bucket.
    check("unrecognised", g("session_dddddddd", reg).activity,
          ol.UNRECOGNISED_STATE)
    check("unrecognised not a pass", g("session_dddddddd", reg).support,
          ol.COULD_NOT_ESTABLISH)
    check("stateless row", g("session_eeeeeeee", reg).activity,
          ol.UNRECOGNISED_STATE)
    check("absent from registry", g("session_zzzzzzzz", reg).activity,
          ol.UNKNOWN_TO_REGISTRY)
    # `we could not look` is never a pass and never `unsupported`.
    check("registry unread", g("session_aaaaaaaa", None).activity,
          ol.REGISTRY_UNREAD)
    check("registry unread support", g("session_aaaaaaaa", None).support,
          ol.COULD_NOT_ESTABLISH)
    check("manager owner", g("manager", reg).activity, ol.UNGRADEABLE_OWNER)
    check("null owner", g("null", reg).activity, ol.UNGRADEABLE_OWNER)
    check("empty owner", g(None, reg).activity, ol.UNGRADEABLE_OWNER)
    # The first id in prose wins — the current owner, not the one it was
    # inherited FROM.
    check("prose first id",
          ol.owner_session_id("session_aaaaaaaa (LANE) — inheriting from "
                              "session_bbbbbbbb (idle)"), "session_aaaaaaaa")
    check("prose graded live",
          g("session_aaaaaaaa (LANE) — inheriting from session_bbbbbbbb",
            reg).support, ol.SUPPORTED)

    if failures:
        for f in failures:
            print(f"{NAME}: self-test FAIL — {f}")
        return 1
    print(f"{NAME}: self-test OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--base", default="origin/main")
    args = ap.parse_args()
    return self_test() if args.self_test else run(args.base)


if __name__ == "__main__":
    raise SystemExit(main())
