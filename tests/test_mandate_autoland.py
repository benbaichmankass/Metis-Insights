"""The mandate auto-land route, end to end — PRODUCER output against CONSUMER clauses.

`scripts/ci/check_mandate_autoland.py --self-test` owns the planted-defect suite
(20 controls, including the five negatives the operator named and two positives).
`run_guards.py` runs it, and `check_selftest_wiring.py` verifies that wiring, so
this file does NOT re-plant those defects. What it adds is the one thing a
planted fixture cannot establish:

**that the bytes `.github/workflows/r4-demotion-gate.yml` actually produces, from
the REAL `config/accounts.yaml` and the REAL `config/mandates.yaml`, are the bytes
the guard admits.**

That join is where this repo's recurring failure lives — a check that passes on
its own fixture while the producer emits something else. So
`test_real_producer_output_clears_every_clause_but_the_arming_switch` runs
`scripts/ops/r4_demotion_gate.py --apply` over a copy of the live config, then
grades the resulting commit, and asserts:

  * with the mandate UNARMED (the shipped state) the only failure is clause A8;
  * with `autoland: true` injected into the fixture's grant, the SAME producer
    output is ADMISSIBLE.

Those two together are the honest statement of "built, not armed": the distance
between the route as it ships and the route running is exactly one field that
only the operator writes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))
sys.path.insert(0, str(REPO / "scripts" / "ops"))

import check_mandate_autoland as autoland  # noqa: E402
import mandate_resolver as mr  # noqa: E402
import r4_demotion_gate as gate  # noqa: E402

#: The leg the fixture demotes. Chosen from the LIVE bybit_2 roster so the
#: producer path exercised here is the one that would really run; asserted
#: below rather than assumed, because a roster edit moves it.
LEG = "xrp_pullback_2h"
LIVE_ACCOUNT = "bybit_2"
MIRROR_ACCOUNT = "bybit_portfolio"
BOT_ENV = {"GIT_AUTHOR_NAME": autoland.BOT_LOGIN, "GIT_AUTHOR_EMAIL": autoland.BOT_EMAIL,
           "GIT_COMMITTER_NAME": autoland.BOT_LOGIN, "GIT_COMMITTER_EMAIL": autoland.BOT_EMAIL}
RUN_ID = "9090909090"
BRANCH = f"automation/r4-demotion-{RUN_ID}"
SLUG = BRANCH.replace("/", "-")


def _git(root: Path, *args: str, env: dict | None = None) -> str:
    import os
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=e)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout


def _perf_payload(leg: str) -> dict:
    """A `/api/bot/performance` body R4 reads as `would_block` with negative R.

    Shaped against `src/runtime/research_results_gate.source_verdict`: trades
    above `MIN_TRADES`, `pnlCoverage` above `COVERAGE_FLOOR`, and a NEGATIVE
    `totalPnlMeasured` — plus `totalR < 0`, which `r4_demotion_gate` requires
    separately so the dollar read and the R read must agree in sign.
    """
    row = {"name": leg, "trades": gate.MIN_TRADES + 21,
           "totalPnlMeasured": -412.50, "totalPnl": -430.0,
           "pnlCoverage": 0.92, "pnlMeasuredCount": 56,
           "totalR": -3.1400, "rTradeCount": 41}
    return {"window": "30d", "since": "2026-08-26T00:00:00Z",
            "perStrategy": [row], "paperPortfolio": {"perStrategy": [dict(row)]}}


def _fixture(tmp_path: Path, *, armed: bool) -> Path:
    """A git repo carrying the REAL config trio, with the arming switch set or not."""
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    for rel in (mr.ACCOUNTS_REL, mr.MANDATES_REL, mr.STRATEGIES_REL):
        shutil.copy2(REPO / rel, root / rel)
    if armed:
        text = (root / mr.MANDATES_REL).read_text(encoding="utf-8")
        marker = "  - id: MD-DEMOTE-S2-S1\n"
        assert marker in text, "MD-DEMOTE-S2-S1 is no longer spelled this way in the store"
        text = text.replace(marker, marker + f"    {autoland.ARM_FIELD}: true\n", 1)
        (root / mr.MANDATES_REL).write_text(text, encoding="utf-8")
    _git(root.parent, "init", "-q", "-b", "main", str(root))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base", env=BOT_ENV)
    _git(root, "branch", "-f", "mainbase")
    return root


def _produce(root: Path) -> dict:
    """Run the REAL producer, then write the paperwork the workflow writes."""
    _git(root, "checkout", "-q", "-b", BRANCH)
    out = gate.run(_perf_payload(LEG), root=root, window="30d", apply=True)
    assert [d["leg"] for d in out["demoted"]] == [LEG], out
    decl = {"tier": 3, "landing": autoland.LANDING_VALUE, "mandate": "MD-DEMOTE-S2-S1",
            "run_id": RUN_ID, "workflow": autoland.WORKFLOW_REL,
            "why": "automated Stage-2 roster cut under the granted derisk_only mandate "
                   "MD-DEMOTE-S2-S1; removals only, live account and mirror cut together"}
    for rel, payload in (
            (f"{autoland.LANDING_DIR}/{SLUG}.json", decl),
            (f"{autoland.SLOT_DIR}/{SLUG}.json",
             {"branch": BRANCH,
              "held_by": f"{autoland.BOT_LOGIN} · r4-demotion-gate run {RUN_ID}",
              "purpose": "MD-DEMOTE-S2-S1 auto-land (pr-landing-guard R16)",
              "claimed_at": "2026-09-25T07:41:00Z"})):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "cut", env=BOT_ENV)
    return decl


def _grade(root: Path, decl: dict):
    changed = autoland._changed(root, "mainbase") or []
    return autoland.verdict(root, "mainbase", BRANCH, decl, changed, SLUG, pre_flight=True)


# ---------------------------------------------------------------------------
def test_the_fixture_leg_is_really_on_the_live_roster_and_its_mirror():
    """The population this file measures over, stated rather than assumed.

    If a roster edit moves `LEG`, the end-to-end test below would silently stop
    exercising the producer and start measuring nothing — the exact shape
    `docs/CLAUDE-RULES-CANONICAL.md` § 'Green is not evidence' names.
    """
    accounts = (yaml.safe_load((REPO / mr.ACCOUNTS_REL).read_text(encoding="utf-8"))
                or {}).get("accounts") or {}
    assert LEG in (accounts[LIVE_ACCOUNT] or {}).get("strategies", []), (
        f"{LEG} is no longer on {LIVE_ACCOUNT}; pick another live leg for this fixture")
    assert LEG in (accounts[MIRROR_ACCOUNT] or {}).get("strategies", [])


def test_the_route_ships_unarmed():
    """No granted mandate carries the arming switch, on `main`, today.

    This is the assertion that makes 'built, not armed' a CHECKED fact rather
    than a sentence in a PR body — and the one that will fail, deliberately and
    loudly, on the operator's arming commit, so whoever arms it meets this test
    and deletes it knowingly.
    """
    doc = yaml.safe_load((REPO / mr.MANDATES_REL).read_text(encoding="utf-8")) or {}
    armed = [m.get("id") for m in (doc.get("mandates") or [])
             if isinstance(m, dict) and m.get(autoland.ARM_FIELD) is True]
    assert armed == [], (
        f"mandate(s) {armed} carry `{autoland.ARM_FIELD}: true`. If the operator has armed "
        f"the route, delete this test in the same commit and say so; if a session added the "
        f"field, that is a session writing its own authorization.")


def test_real_producer_output_refuses_on_the_arming_switch_alone(tmp_path):
    """The shipped state: the producer's real output clears everything but A8."""
    root = _fixture(tmp_path, armed=False)
    decl = _produce(root)
    ok, fails, notes = _grade(root, decl)
    assert ok is False
    clauses = sorted({f.split()[0] for f in fails})
    assert clauses == ["A8"], f"expected the arming switch to be the ONLY gap; got {fails}"
    assert any("resolver FIRE" in n for n in notes), (
        f"the resolver replay never ran, so A5 proved nothing: {notes}")


def test_real_producer_output_is_admissible_once_the_operator_arms_it(tmp_path):
    """…and with `autoland: true` the SAME bytes land. One field, nothing else."""
    root = _fixture(tmp_path, armed=True)
    decl = _produce(root)
    ok, fails, _ = _grade(root, decl)
    assert ok is True, fails


def test_a_hand_edit_on_top_of_the_producer_output_refuses(tmp_path):
    """The forgery case against REAL config: an armed mandate does not excuse a
    session commit on the branch."""
    root = _fixture(tmp_path, armed=True)
    decl = _produce(root)
    (root / mr.ACCOUNTS_REL).write_text(
        (root / mr.ACCOUNTS_REL).read_text(encoding="utf-8"), encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs/extra.md").write_text("a session touched this\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "session edit",
         env={"GIT_AUTHOR_NAME": "a session", "GIT_AUTHOR_EMAIL": "s@example.invalid",
              "GIT_COMMITTER_NAME": "a session", "GIT_COMMITTER_EMAIL": "s@example.invalid"})
    ok, fails, _ = _grade(root, decl)
    assert ok is False
    assert any(f.startswith("A1") for f in fails), fails   # the stray doc
    assert any(f.startswith("A7") for f in fails), fails   # the human commit


def test_planted_defect_suite_passes():
    """`check_mandate_autoland.py --self-test` — the 20 controls, under pytest too.

    `run_guards.py` runs this flag as its own step; this makes the same controls
    visible to anybody running the test suite, which is how most of this repo's
    changes are first checked.
    """
    p = subprocess.run([sys.executable, "scripts/ci/check_mandate_autoland.py", "--self-test"],
                       cwd=REPO, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize("script", ["scripts/ci/check_pr_landing.py",
                                    "scripts/ci/automerge_landing_gate.py"])
def test_the_landing_machinery_self_tests_still_pass(script):
    """R16 and the `mandate` arming state are additions; the suites they were
    added to must still hold, including every positive control."""
    p = subprocess.run([sys.executable, script, "--self-test"],
                       cwd=REPO, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
