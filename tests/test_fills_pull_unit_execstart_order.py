"""FIX-CA-OPS2 (manager pre-merge review of PR #13316/FIX-CA-31): pin the
ExecStart ordering so the Bybit wallet ledger can never be skipped by an
earlier step's failure.

The defect: FIX-CA-31 made scripts/pull_alpaca_fills.py exit non-zero on a
genuine Alpaca API failure. deploy/ict-exchange-fills-pull.service is a
systemd Type=oneshot unit with multiple plain ExecStart lines; systemd stops
at the first ExecStart that exits non-zero, so with Alpaca as the SECOND of
three ExecStart lines, any Alpaca account failure (a revoked key, a rate
limit, a 5xx) would silently skip the THIRD line,
scripts/ops/pull_bybit_transaction_log.py — the wallet-ledger / broker-truth
feed for real-money bybit_2.

The fix: Alpaca moved to its own unit (ict-alpaca-fills-pull.service), and
the wallet ledger was reordered to ExecStart #1 in the unit it shares with
the Bybit fills pull, so nothing in that unit can ever skip it. This test
must fail on current main before that fix (the pre-fix unit ordered
pull_exchange_fills, pull_alpaca_fills, pull_bybit_transaction_log — the
wallet ledger LAST and Alpaca in front of it) and pass after.
"""
from __future__ import annotations

import re
from pathlib import Path

_DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"
_BYBIT_UNIT = _DEPLOY_DIR / "ict-exchange-fills-pull.service"
_ALPACA_UNIT = _DEPLOY_DIR / "ict-alpaca-fills-pull.service"

_EXEC_START_RE = re.compile(r"^ExecStart=(.*)$", re.MULTILINE)


def _exec_starts(unit_path: Path) -> list[str]:
    text = unit_path.read_text(encoding="utf-8")
    return _EXEC_START_RE.findall(text)


def test_bybit_unit_runs_wallet_ledger_before_fills_pull():
    """The wallet ledger is ExecStart #1 -- nothing in this unit can precede
    (and therefore skip) it."""
    execs = _exec_starts(_BYBIT_UNIT)
    assert execs, f"no ExecStart lines found in {_BYBIT_UNIT}"
    ledger_idx = next(
        (i for i, e in enumerate(execs) if "pull_bybit_transaction_log.py" in e), None
    )
    fills_idx = next(
        (i for i, e in enumerate(execs) if "pull_exchange_fills.py" in e), None
    )
    assert ledger_idx is not None, "pull_bybit_transaction_log.py ExecStart missing"
    assert fills_idx is not None, "pull_exchange_fills.py ExecStart missing"
    assert ledger_idx < fills_idx, (
        "the wallet ledger must run BEFORE the fills pull so a fills-pull "
        "failure cannot skip it"
    )
    assert ledger_idx == 0, "the wallet ledger must be the FIRST ExecStart"


def test_bybit_unit_does_not_run_alpaca():
    """Alpaca must not be able to fail-and-skip anything in this unit at all
    -- it has its own unit now."""
    execs = _exec_starts(_BYBIT_UNIT)
    assert not any("pull_alpaca_fills.py" in e for e in execs), (
        "pull_alpaca_fills.py must not share an ExecStart chain with the "
        "Bybit wallet ledger -- see deploy/ict-alpaca-fills-pull.service"
    )


def test_bybit_unit_execstart_lines_are_not_failure_suppressed():
    """Neither remaining ExecStart is `-`-prefixed: a failure of either must
    still mark the unit failed (visible via status_check.sh's failed-unit
    gate) -- swallowing failures with `-` was the rejected alternative fix,
    because it would have made an Alpaca-class failure invisible at the
    systemd layer instead of merely non-blocking."""
    text = _BYBIT_UNIT.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            assert not line.startswith("ExecStart=-"), (
                f"a `-`-prefixed ExecStart suppresses unit-level failure "
                f"visibility: {line}"
            )


def test_alpaca_has_its_own_unit():
    assert _ALPACA_UNIT.is_file(), f"{_ALPACA_UNIT} missing"
    execs = _exec_starts(_ALPACA_UNIT)
    assert len(execs) == 1
    assert "pull_alpaca_fills.py" in execs[0]
    assert "--all-alpaca-accounts" in execs[0]


def test_alpaca_unit_has_its_own_timer():
    timer = _DEPLOY_DIR / "ict-alpaca-fills-pull.timer"
    assert timer.is_file(), f"{timer} missing"
    text = timer.read_text(encoding="utf-8")
    assert "Unit=ict-alpaca-fills-pull.service" in text
