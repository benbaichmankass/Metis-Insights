"""``system_invariants.py``'s planted controls must RUN, not merely exist.

WHY THIS FILE EXISTS
--------------------
``scripts/ops/system_invariants.py`` ships a ``--self-test`` that plants a
known-BAD, a known-GOOD and an ABSENT fixture for every invariant. Measured
2026-09-10 with a positive control (``check_collapsed_states`` resolves to six
CI files; ``system_invariants`` resolves to none): **that self-test runs in no
workflow and no guard registry, and the suite itself has no scheduled caller.**
The 2026-09-09 full-system audit names arming it as its highest-leverage change.

So the planted controls were *registered and never executed* — the defect class
``check_selftest_wiring.py`` was written for, one directory over. This file is
the cheap half: ``pytest-run`` is a REQUIRED branch-protection context
(``branch-protection-sync.yml``), so asserting the self-test's RETURN CODE here
puts every one of those controls in a gate that actually blocks a merge.

⚠️ **IT ASSERTS THE RETURN CODE, NOT THE OUTPUT.** A self-test that prints
``FAIL`` lines and exits 0 is the presence-only failure this repo has already
paid for (``new-table-wiring-guard``'s marker; MI-226's finding). ``_self_test``
returns 1 when any check fails and ``main`` propagates it — this pins that
contract from outside the process.

⚠️ **WHAT THIS DOES NOT DO — stated so nobody reads it as more than it is.** It
proves the invariants can still FAIL on planted fixtures. It does NOT run the
suite against the live fleet: nothing does, and a green test here is not
evidence that any invariant holds in production. That gap is the audit's
separate recommendation (schedule the suite behind a receipt that lands on
main, ``broker_bracket_reconcile.yml`` as the template) and is deliberately not
smuggled in here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUITE = REPO / "scripts" / "ops" / "system_invariants.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SUITE), *args],
        cwd=str(REPO), capture_output=True, text=True, timeout=300,
    )


def test_self_test_exits_zero() -> None:
    """The planted controls all pass — asserted on the EXIT CODE."""
    proc = _run("--self-test")
    assert proc.returncode == 0, (
        f"system_invariants --self-test failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    # The suite must not have quietly lost its controls: a self-test reduced to
    # zero checks would also exit 0.
    assert "self-test:" in proc.stdout
    ran = int(proc.stdout.split("self-test:")[1].split("/")[1].split()[0])
    assert ran >= 47, f"self-test shrank to {ran} checks — controls were dropped"


def test_suite_returns_nonzero_on_a_planted_violation(tmp_path: Path) -> None:
    """THE NEGATIVE CONTROL — without it, ``returncode == 0`` proves nothing.

    Plants the live F-39 shape (a CLOSED package still holding an open
    real-money leg) as payload files and requires the SUITE — not the
    self-test — to exit 1. Then removes the condition with a one-field edit
    (``closed`` -> ``open``) and requires exit 0. If a change ever makes the
    suite unable to fail, this test fails instead of going quietly green.
    """
    def write(status: str) -> None:
        (tmp_path / "order_packages.json").write_text(json.dumps([{
            "order_package_id": "pkg-plant", "strategy_name": "planted",
            "symbol": "XRPUSDT", "status": status, "sl": 1.3, "tp": 1.5,
            "linked_trade_id": 9001, "close_reason": "reconciler_filled"}]))
        (tmp_path / "journal_trades.json").write_text(json.dumps([{
            "id": 9001, "account_id": "bybit_2", "symbol": "XRPUSDT",
            "status": "open", "is_backtest": 0, "position_size": 58.5,
            "stop_loss": 1.3, "order_package_id": "pkg-plant"}]))
        (tmp_path / "positions.json").write_text(json.dumps([{
            "id": "9001", "account": "bybit_2", "symbol": "XRPUSDT",
            "qty": 58.5, "stopLoss": 1.3, "accountClass": "real_money"}]))

    write("closed")
    bad = _run("--payloads", str(tmp_path))
    assert bad.returncode == 1, (
        "the suite did NOT fail on a planted stranded real-money leg — it can "
        f"no longer detect F-39\nstdout:\n{bad.stdout}")
    assert "INV-PACKAGE-LEG-REACHABLE" in bad.stdout
    assert "9001" in bad.stdout and "real_money" in bad.stdout

    write("open")
    good = _run("--payloads", str(tmp_path))
    assert good.returncode == 0, (
        "the suite still fails after the condition was removed — it is not "
        f"keying on the stranded package\nstdout:\n{good.stdout}")


def test_f39_invariant_is_registered() -> None:
    """The F-39 invariant must stay in the registry.

    Operator-approved Tier-2 on
    ``WO-20260909-DECISION-PACKAGES-CLOSED-WHILE-HOLDING-OPEN-LEGS``. Dropping
    it from ``INVARIANTS`` would leave every fixture above in place and
    silently stop grading the fleet, which is the shape this suite exists to
    refuse.
    """
    sys.path.insert(0, str(REPO))
    from scripts.ops.system_invariants import INVARIANTS  # noqa: E402

    ids = [i["id"] for i in INVARIANTS]
    assert "INV-PACKAGE-LEG-REACHABLE" in ids, ids
