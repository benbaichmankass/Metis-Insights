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


def _recent_payload(leg: str) -> dict:
    """A `/api/bot/performance/recent?n=40` body the gate reads as a T3 DEMOTE.

    Shaped against `src/runtime/research_results_gate.source_verdict` over the
    last 40 (trades >= 40, `pnlCoverage` above `COVERAGE_FLOOR`, NEGATIVE
    `totalPnlMeasured`, `totalR < 0`) and against MD-DEMOTE-S2-S1's T3 rule:
    both 20-trade windows at -0.3R/trade (-6R), below the leg's REAL Stage-0
    p10 (asserted in `_fixture`, not assumed).
    """
    def row(n, r):
        return {"name": leg, "trades": n, "totalPnlMeasured": -412.50 * n / 40,
                "totalPnl": -430.0 * n / 40, "pnlCoverage": 0.92,
                "pnlMeasuredCount": int(n * 0.92), "totalR": r, "rTradeCount": n}
    ent = {"closedAvailable": 55, "nUsed": 40, "complete": True,
           "last": {"perStrategy": [row(40, -12.0)]},
           "blocks": [{"perStrategy": [row(20, -6.0)], "closedFrom": "2026-09-01T00:00:00",
                       "closedTo": "2026-09-12T00:00:00"},
                      {"perStrategy": [row(20, -6.0)], "closedFrom": "2026-09-13T00:00:00",
                       "closedTo": "2026-09-27T00:00:00"}]}
    return {"n": 40, "block": 20, "error": False,
            "realMoney": {"readState": "ok", "perStrategy": {leg: ent}},
            "mirror": {"readState": "ok", "accountIds": [MIRROR_ACCOUNT],
                       "perStrategy": {leg: json.loads(json.dumps(ent))}}}


def _fixture(tmp_path: Path, *, armed: bool) -> Path:
    """A git repo carrying the REAL config trio, with the arming switch set or not."""
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    for rel in (mr.ACCOUNTS_REL, mr.MANDATES_REL, mr.STRATEGIES_REL):
        shutil.copy2(REPO / rel, root / rel)
    # The T3 threshold is the leg's REAL Stage-0 record and its committed
    # source_run -- copied, so the resolver replay re-derives the real p10.
    ev_rel = f"{mr.EVIDENCE_DIR_REL}/{LEG}.json"
    src_rel = json.loads((REPO / ev_rel).read_text(encoding="utf-8"))["source_run"]
    for rel in (ev_rel, src_rel):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, root / rel)
    p10 = mr.stage0_block_p10(LEG, REPO)["p10"]
    assert p10 is not None and -6.0 < p10, f"{LEG}'s real p10 {p10} no longer sits above -6R"
    # The real store has been ARMED on MD-DEMOTE-S2-S1 since 2026-09-27
    # (operator). Normalise to the requested state either way, so the fixture
    # tests the route rather than whatever the live store happens to hold.
    text = (root / mr.MANDATES_REL).read_text(encoding="utf-8")
    marker = "  - id: MD-DEMOTE-S2-S1\n"
    assert marker in text, "MD-DEMOTE-S2-S1 is no longer spelled this way in the store"
    arm_line = f"    {autoland.ARM_FIELD}: true\n"
    text = text.replace(arm_line, "")
    if armed:
        text = text.replace(marker, marker + arm_line, 1)
    (root / mr.MANDATES_REL).write_text(text, encoding="utf-8")
    _git(root.parent, "init", "-q", "-b", "main", str(root))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base", env=BOT_ENV)
    _git(root, "branch", "-f", "mainbase")
    return root


def _produce(root: Path) -> dict:
    """Run the REAL producer, then write the paperwork the workflow writes."""
    _git(root, "checkout", "-q", "-b", BRANCH)
    out = gate.run(_recent_payload(LEG), root=root, window=gate.WINDOW_LABEL, apply=True)
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
    """⚠️ `event_path` / `in_actions` are passed EXPLICITLY, never inherited.

    Reading them from the environment made these cases depend on which PR was
    running them: under GitHub Actions the ambient `GITHUB_EVENT_PATH` names the
    HUMAN who opened the PR, so A7 failed here and passed locally. That is the
    guard's own `pre_flight` path, exercised under the workflow's real
    conditions — a schedule/dispatch event with no `pull_request` key — and it
    is stated rather than inherited."""
    changed = autoland._changed(root, "mainbase") or []
    return autoland.verdict(root, "mainbase", BRANCH, decl, changed, SLUG,
                            pre_flight=True, event_path="", in_actions=False)


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


# test_the_route_ships_unarmed was DELETED 2026-09-27 in the operator's arming
# commit, as its own docstring required: the operator (Ben) armed
# MD-DEMOTE-S2-S1 by popup in manager session session_01Ljhs6sFAdWHdMDhJpL5aBP
# ("Arm MD-DEMOTE-S2-S1 only (Recommended)"). The replacement below pins that
# EXACTLY ONE entry is armed, so a second arming still meets a failing test.


def test_only_the_operator_armed_entry_is_armed():
    doc = yaml.safe_load((REPO / mr.MANDATES_REL).read_text(encoding="utf-8")) or {}
    armed = [m.get("id") for m in (doc.get("mandates") or [])
             if isinstance(m, dict) and m.get(autoland.ARM_FIELD) is True]
    assert armed == ["MD-DEMOTE-S2-S1"], (
        f"armed mandates are {armed}; the operator armed ONLY MD-DEMOTE-S2-S1 on "
        f"2026-09-27. Arming another is the operator's act -- update this test in "
        f"that commit and cite the grant.")

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


# ── the two network reads A7 now depends on (review of #13609) ─────────────
# Mocked urlopen only: these prove the READERS fail closed and parse what the
# real API returns. They never touch the network.
import io as _io  # noqa: E402
import urllib.error as _ue  # noqa: E402
import zipfile as _zf  # noqa: E402


class _Resp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_run_without_a_repository_could_not_look(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    run, how = autoland.fetch_run("1")
    assert run is None and "GITHUB_REPOSITORY" in how


def test_fetch_run_http_error_is_could_not_look_never_a_run(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

    def boom(req, timeout=0):
        raise _ue.HTTPError(req.full_url, 404, "nope", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    run, how = autoland.fetch_run("1")
    assert run is None and "404" in how


def test_fetch_run_returns_the_parsed_run(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    payload = {"path": autoland.WORKFLOW_REL, "head_branch": "main"}
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=0: _Resp(json.dumps(payload).encode()))
    run, _ = autoland.fetch_run("1")
    assert run == payload


def test_fetch_provenance_with_no_artifact_refuses(monkeypatch):
    """A dry (apply=false) dispatch uploads nothing: its genuine id buys nothing."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=0: _Resp(b'{"artifacts": []}'))
    rec, how = autoland.fetch_provenance("1")
    assert rec is None and "uploaded no" in how


def test_fetch_provenance_reads_the_record_and_never_forwards_the_token(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    buf = _io.BytesIO()
    with _zf.ZipFile(buf, "w") as z:
        z.writestr(autoland.PROVENANCE_FILE, json.dumps({"run_id": "1", "head_sha": "abc"}))
    seen = []

    def fake(req, timeout=0):
        seen.append(req)
        if "/artifacts?" in req.full_url:
            return _Resp(json.dumps({"artifacts": [
                {"id": 9, "name": autoland.PROVENANCE_ARTIFACT, "expired": False,
                 "created_at": "2026-09-28T00:00:00Z"}]}).encode())
        return _Resp(buf.getvalue())
    monkeypatch.setattr("urllib.request.urlopen", fake)
    rec, _ = autoland.fetch_provenance("1")
    assert rec == {"run_id": "1", "head_sha": "abc"}
    # The Authorization header is UNREDIRECTED, so the 302 to blob storage never carries it.
    assert all("Authorization" not in r.headers and "Authorization" in r.unredirected_hdrs
               for r in seen)
