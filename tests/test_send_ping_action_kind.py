"""`kind:` / `why:` on the send-ping action — and passthrough staying the default.

WHY THIS EXISTS (2026-09-01). `src/runtime/claude_ping.py` defines three ping
CLASSES (`decision`, `state_change`, `lifecycle`) with different gating, and
`scripts/send_ping.py` has accepted `--kind`/`--why` since #10669. But
`send_ping_action.sh` passed neither, so the `send-ping` system-action could
only ever fire the passthrough shape: the classes were implemented, documented,
and **unreachable from the only path a session can dispatch**. Same family as
the bug in #10674 — a capability that is configured, resolvable and inert.

⚠️ THE INVARIANT UNDER TEST IS NOT "kind works". It is that adding `kind` did
not cost the passthrough path, which carries the OPERATOR'S OWN TEXT. Format B
is a house style for machine-generated events; rewriting a human's sentence into
"headline / why" is not a formatting improvement, it is a substitution. So the
first test below pins the exact argv of the no-kind call.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "send_ping_action.sh"

# What the fake producer prints on stdout; `withheld:` is the marker the
# wrapper must treat as "not sent" despite an exit code of 0.
_FAKE_OK = "queued\n"


def _run(tmp_path: Path, *, message="hello", kind=None, why=None,
         unproven=None, target="claude", priority="normal",
         producer_stdout=_FAKE_OK, producer_rc=0):
    """Invoke the wrapper against a tmp REPO_DIR holding a FAKE send_ping.py.

    The fake records the argv it was called with, so the assertions are about
    what the wrapper actually invoked rather than about its log lines.
    """
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    argv_dump = tmp_path / "argv.json"
    (tmp_path / "scripts" / "send_ping.py").write_text(
        "import json, sys\n"
        f"open({str(argv_dump)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"sys.stdout.write({producer_stdout!r})\n"
        f"sys.exit({producer_rc})\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["REPO_DIR"] = str(tmp_path)
    env["ACTION_MESSAGE"] = message
    env["ACTION_TARGET"] = target
    env["ACTION_PRIORITY"] = priority
    for name, val in (("ACTION_KIND", kind), ("ACTION_WHY", why),
                      ("ACTION_UNPROVEN", unproven)):
        if val is None:
            env.pop(name, None)
        else:
            env[name] = val

    proc = subprocess.run(["bash", str(_SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)
    argv = json.loads(argv_dump.read_text()) if argv_dump.exists() else None
    return proc, argv


def _audits(tmp_path: Path) -> list[dict]:
    d = tmp_path / "runtime_logs" / "operator_actions"
    if not d.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]


# --------------------------------------------------------------------------
# PASSTHROUGH — the default, and the thing that must not have changed
# --------------------------------------------------------------------------

def test_no_kind_invokes_exactly_the_previous_command_line(tmp_path: Path) -> None:
    """⚠️ The regression guard for this whole change.

    Pinned as an EXACT list, not a subset: an accidental `--kind ''` or a
    stray `--why` would still satisfy "contains --target", and the failure
    mode being prevented is precisely a silent extra flag reshaping an
    operator's own words.
    """
    proc, argv = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert argv == ["--target", "claude", "--priority", "normal", "hello"]
    assert "--kind" not in argv


def test_passthrough_records_its_shape_in_the_audit(tmp_path: Path) -> None:
    proc, _ = _run(tmp_path)
    assert proc.returncode == 0
    rows = _audits(tmp_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    # "passthrough" rather than an empty string: a blank field reads as
    # "we did not record it", which is a different fact from "no class".
    assert rows[0]["kind"] == "passthrough"


# --------------------------------------------------------------------------
# THE THREE CLASSES
# --------------------------------------------------------------------------

def test_each_class_is_forwarded_with_its_why(tmp_path: Path) -> None:
    for cls in ("decision", "state_change", "lifecycle"):
        proc, argv = _run(tmp_path, kind=cls, why="the world moved")
        assert proc.returncode == 0, f"{cls}: {proc.stderr}"
        assert argv == ["--target", "claude", "--priority", "normal",
                        "--kind", cls, "--why", "the world moved", "hello"]


def test_unproven_rides_along_when_given(tmp_path: Path) -> None:
    proc, argv = _run(tmp_path, kind="state_change", why="deployed",
                      unproven="not yet observed live")
    assert proc.returncode == 0
    assert argv == ["--target", "claude", "--priority", "normal",
                    "--kind", "state_change", "--why", "deployed",
                    "--unproven", "not yet observed live", "hello"]


def test_unproven_is_dropped_when_empty_rather_than_sent_blank(tmp_path: Path) -> None:
    """An empty --unproven would render a trailing space onto the why line."""
    proc, argv = _run(tmp_path, kind="decision", why="needs you", unproven="")
    assert proc.returncode == 0
    assert "--unproven" not in argv


# --------------------------------------------------------------------------
# REFUSALS — an invalid kind must NOT degrade to passthrough
# --------------------------------------------------------------------------

def test_an_unknown_kind_is_refused_and_nothing_is_queued(tmp_path: Path) -> None:
    """⚠️ Deliberately unlike `priority`/`target`, which warn and default.

    Those pick a prefix or a destination and a wrong one is visible in the
    delivered message. `kind` selects the FORMAT and the RATE LIMITER, so a
    silent degrade would send an unformatted ping, record nothing against the
    limiter, and report success — the caller could not tell.
    """
    proc, argv = _run(tmp_path, kind="state-change", why="typo'd the class")
    assert proc.returncode == 1
    assert argv is None, "the producer must never be invoked on a bad kind"
    assert "not a class" in proc.stdout + proc.stderr
    rows = _audits(tmp_path)
    assert rows and rows[0]["status"] == "error"


def test_a_kind_without_a_why_is_refused(tmp_path: Path) -> None:
    proc, argv = _run(tmp_path, kind="lifecycle", why="   ")
    assert proc.returncode == 1
    assert argv is None
    assert "requires 'why'" in proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# WITHHELD — exit 0 is not enough to claim a send
# --------------------------------------------------------------------------

def test_a_withheld_ping_is_audited_as_withheld_not_ok(tmp_path: Path) -> None:
    """The rate limiter exits 0 and queues NOTHING.

    `claude_ping.admits()` returns a REASON precisely so "we suppressed it" and
    "there was nothing to say" stay distinguishable; folding a withheld ping
    into `ok` would throw that away at the last hop.
    """
    proc, _ = _run(tmp_path, kind="lifecycle", why="session ended",
                   producer_stdout="withheld: rate-limited: 12s since last (< 300s)\n")
    assert proc.returncode == 0, "a suppressed ping is not a failure"
    rows = _audits(tmp_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "withheld"
    assert "rate-limited" in rows[0]["reason"]


def test_a_producer_failure_is_audited_as_failed(tmp_path: Path) -> None:
    proc, _ = _run(tmp_path, kind="decision", why="x",
                   producer_stdout="boom\n", producer_rc=1)
    assert proc.returncode == 1
    rows = _audits(tmp_path)
    assert rows and rows[0]["status"] == "failed"
