r"""The closed→flat invariant must be able to SAY something.

Audit findings F-11 / F-12 / F-13 (2026-09-09), operator-approved Tier-2 the
same day (``chosen: all_three`` on
``WO-20260909-DECISION-CLOSED-FLAT-INVARIANT-CANNOT-SEE-A-FAILED-READ``), plus
a **fourth** blindness found while building the fix and pinned here.

⚠️ **A GREEN RUN OF THIS FILE CLEARS NOTHING ON THE FLEET.** The whole finding
is that a harness cannot reach a failed exchange read on a live account. What
these tests do establish is narrower and still worth having: the classifier
that ships can tell the three outcomes apart, and it can read the dict shape
production actually emits.
"""
from __future__ import annotations

import pytest

from src.runtime import closed_flat_invariant as cfi
from src.runtime import _closed_flat_wiring as wiring


# ---------------------------------------------------------------------------
# F-11 — a failed read is `could_not_look`, never `flat`
# ---------------------------------------------------------------------------


def test_none_is_could_not_look_not_flat():
    """``None`` in ⇒ we did not look.

    ``clients.py::account_open_positions`` returns ``None`` BY CONTRACT on
    every failure path — including an empty IB snapshot from a Gateway that is
    not verified logged-in — so callers can tell "no positions" from "could not
    read". This module was the consumer that dropped the distinction.
    """
    read = cfi._residual_from_positions(None, "ETHUSDT", "long")
    assert read.residual_state == cfi.RESIDUAL_STATE_COULD_NOT_LOOK
    assert read.residual_state != cfi.RESIDUAL_STATE_FLAT
    assert read.qty == 0.0, (
        "qty must stay 0.0 on an ungradeable read — the STATE, not the number, "
        "is what callers branch on"
    )


def test_empty_list_is_flat_not_could_not_look():
    """``[]`` in ⇒ we looked and the book is empty. The OPPOSITE error matters
    as much: grading a genuinely-flat account ``could_not_look`` would put a
    permanent unread banner on every quiet tick."""
    read = cfi._residual_from_positions([], "ETHUSDT", "long")
    assert read.residual_state == cfi.RESIDUAL_STATE_FLAT


@pytest.mark.parametrize(
    "resolver, expected_reason",
    [
        (lambda _aid: None, "account_unresolvable"),
        (lambda _aid: _Boom(), "open_positions_raised"),
    ],
)
def test_every_fetch_failure_path_grades_could_not_look(resolver, expected_reason):
    """Population: the early-return sites in the exchange-read path. All of
    them returned ``0.0`` (= flat) before 2026-09-09."""
    read = cfi._fetch_exchange_residual(resolver, "bybit_2", "ETHUSDT", "long")
    assert read.residual_state == cfi.RESIDUAL_STATE_COULD_NOT_LOOK
    assert read.reason == expected_reason


class _Boom:
    def open_positions(self):
        raise RuntimeError("simulated exchange outage")


def test_an_unreadable_read_does_not_clear_the_invariant(monkeypatch, tmp_path):
    """THE FINDING, end to end through ``check_detailed``.

    A recently-closed trade whose account cannot be read must NOT be counted
    as flat. Before the fix this pass returned zero violations and nothing
    anywhere recorded that the exchange had never answered.
    """
    conn = _db_with_one_closed_trade()
    result = cfi.check_detailed(
        conn, lambda _aid: None,
        window_seconds=600.0, cadence_basis="measured",
        violations_log=tmp_path / "v.jsonl", coverage_log=tmp_path / "c.jsonl",
        alerter=lambda *a, **k: None,
    )
    assert result.violations == []
    assert result.state_counts[cfi.RESIDUAL_STATE_COULD_NOT_LOOK] == 1
    assert result.state_counts[cfi.RESIDUAL_STATE_FLAT] == 0, (
        "an unreadable account must not be banked as flat — that is F-11"
    )
    assert len(result.unreadable) == 1


def test_the_wiring_branches_on_could_not_look(monkeypatch):
    """The consumer half of the contract: zero violations + one unreadable
    read is NOT a clean tick, and the summary must say so."""
    wiring._last_invocation_monotonic = None
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"account_id": "bybit_2"}},
    )
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check_detailed",
        lambda db, account_resolver=None, **kw: cfi.ClosedFlatCheckResult(
            [], [cfi.UnreadableRead(1, "bybit_2", "ETHUSDT", "read_returned_none")],
            state_counts={"flat": 0, "residual": 0, "could_not_look": 1},
            examined=1, controls_ok=True,
        ),
    )
    entry = wiring.maybe_run_closed_flat_check(db=object())
    assert entry is not None, (
        "a tick that could not read the exchange must not report as clean"
    )
    assert entry["state_counts"]["could_not_look"] == 1
    assert entry["violations"] == 0


def test_an_all_flat_tick_is_the_only_clean_one(monkeypatch):
    wiring._last_invocation_monotonic = None
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"account_id": "bybit_2"}},
    )
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check_detailed",
        lambda db, account_resolver=None, **kw: cfi.ClosedFlatCheckResult(
            [], [],
            state_counts={"flat": 3, "residual": 0, "could_not_look": 0},
            examined=3, controls_ok=True,
        ),
    )
    assert wiring.maybe_run_closed_flat_check(db=object()) is None


# ---------------------------------------------------------------------------
# The FOURTH blindness — the size key (found 2026-09-09 building this fix)
# ---------------------------------------------------------------------------


def test_the_production_position_shape_is_graded_as_a_residual():
    """``account_open_positions`` emits ``size`` on 4 of 4 venue branches.

    This module read ``qty`` / ``contracts`` only, so ``float(None or None or
    0) == 0.0`` and a live residual graded FLAT — on every account, from the
    module's first commit. The invariant could not have reported a violation
    even with a perfect read, a perfect window and a perfect read surface,
    which is a stronger fact about the 14.7-day zero than F-11/F-12/F-13 are.

    ⚠️ Every fixture in ``test_closed_flat_invariant.py`` uses ``qty``, so the
    suite was green throughout while exercising a shape production never
    produces. That is why this test names the key explicitly.
    """
    canonical = [{
        "symbol": "ETHUSDT", "side": "Buy", "size": 26.05,
        "entry_price": 4300.0, "unrealised_pnl": None,
    }]
    read = cfi._residual_from_positions(canonical, "ETHUSDT", "long")
    assert read.residual_state == cfi.RESIDUAL_STATE_RESIDUAL
    assert read.qty == pytest.approx(26.05)


def test_size_key_is_first_in_precedence():
    """A row carrying both keys is read on ``size`` — the one production
    normalises to."""
    read = cfi._residual_from_positions(
        [{"symbol": "ETHUSDT", "side": "Buy", "size": 5.0, "qty": 99.0}],
        "ETHUSDT", "long",
    )
    assert read.qty == pytest.approx(5.0)


def test_legacy_qty_and_contracts_still_grade():
    """The fallback keys are kept so a caller passing a raw venue payload still
    grades — never as a claim that anything sends them."""
    for key in ("qty", "contracts"):
        read = cfi._residual_from_positions(
            [{"symbol": "ETHUSDT", "side": "Sell", key: 2.0}], "ETHUSDT", "short",
        )
        assert read.residual_state == cfi.RESIDUAL_STATE_RESIDUAL
        assert read.qty == pytest.approx(-2.0), "short residuals come out signed"


# ---------------------------------------------------------------------------
# F-12 — the window is derived from the MEASURED invocation interval
# ---------------------------------------------------------------------------


def test_window_covers_the_measured_interval():
    """The audit's tightest measured period is 101.9 s against a 60 s window,
    leaving >= 41% of every period examined by nobody."""
    window, basis = wiring.derive_window_seconds(101.9)
    assert basis == "measured"
    assert window >= 101.9, (
        "consecutive passes must TILE the timeline — a window shorter than the "
        "interval leaves a gap by arithmetic, not by failure"
    )
    assert window == pytest.approx(101.9 * 1.5)


def test_a_fast_tick_never_shrinks_below_the_floor():
    window, basis = wiring.derive_window_seconds(1.0)
    assert basis == "measured"
    assert window == float(cfi.DEFAULT_WINDOW_SECONDS)


def test_first_pass_in_a_process_uses_the_bootstrap_window():
    """The trader restarts on every merge to ``main``, and a restart is
    precisely when a close is most likely to have been missed — so the
    no-interval case must not fall back to the 60 s constant."""
    window, basis = wiring.derive_window_seconds(None)
    assert basis == "bootstrap"
    assert window > float(cfi.DEFAULT_WINDOW_SECONDS)
    assert window >= 101.9 * 2


def test_the_wiring_measures_the_interval_rather_than_declaring_it(monkeypatch):
    wiring._last_invocation_monotonic = None
    clock = {"t": 1000.0}
    monkeypatch.setattr(wiring.time, "monotonic", lambda: clock["t"])

    first, first_basis = wiring._next_window()
    assert first_basis == "bootstrap"

    clock["t"] += 124.3          # the tick_cost-implied mean period
    second, second_basis = wiring._next_window()
    assert second_basis == "measured"
    assert second == pytest.approx(124.3 * 1.5)
    wiring._last_invocation_monotonic = None


# ---------------------------------------------------------------------------
# F-13 — the alert reaches Telegram, and the output has a read surface
# ---------------------------------------------------------------------------


def test_alert_level_is_forwarded_to_telegram():
    """``outcomes`` restricts Telegram to {ERROR, CRITICAL}; this alert was
    WARN, so a violation reached the operator on no channel at all."""
    from src.runtime import outcomes

    captured = {}
    original = outcomes.report

    def _spy(channel, **kw):
        captured.update(kw)

    outcomes.report = _spy
    try:
        alerter = cfi._default_alerter()
        alerter("closed_flat_invariant", "summary", {"trade_id": 1})
    finally:
        outcomes.report = original

    assert captured["level"] in outcomes._TELEGRAM_LEVELS, (
        f"{captured['level']} is excluded from Telegram by outcomes.py"
    )


def test_both_log_files_are_on_the_diag_allowlist():
    """The falsifier's output must be readable from the surface a session
    actually has. ``invariant_violations`` was on NO allowlist and had no
    alternate reader — its only mention outside ``src/runtime/`` was an
    SSH-only ``tail -f`` line in a shell script."""
    wanted = {
        "invariant_violations": "invariant_violations.jsonl",
        "closed_flat_coverage": "closed_flat_coverage.jsonl",
    }
    try:
        from src.web.api.routers import diag
    except ModuleNotFoundError:
        # ⚠️ NOT `pytest.skip`. A skipped assertion is a silent pass, and this
        # test exists because "nobody could read the output" went unnoticed for
        # 14.7 days. Where the web stack is not installed we read the allowlist
        # out of the SOURCE instead — a weaker check that still fails on a
        # missing entry, rather than no check at all.
        import ast as _ast
        from pathlib import Path as _Path
        tree = _ast.parse(
            _Path("src/web/api/routers/diag.py").read_text(encoding="utf-8"))
        keys = {
            k.value for node in _ast.walk(tree)
            if isinstance(node, _ast.Dict)
            for k in node.keys
            if isinstance(k, _ast.Constant) and isinstance(k.value, str)
        }
        for name in wanted:
            assert name in keys, (
                f"{name} is written by src/runtime/ and readable from nowhere"
            )
        return

    for name, filename in wanted.items():
        assert name in diag._LOG_FILES, (
            f"{name} is written by src/runtime/ and readable from nowhere"
        )
        assert diag._LOG_FILES[name].name == filename


def test_coverage_soak_row_carries_what_makes_a_zero_interpretable(tmp_path):
    conn = _db_with_one_closed_trade()
    soak = tmp_path / "coverage.jsonl"
    cfi.check_detailed(
        conn, lambda _aid: None,
        window_seconds=186.45, cadence_basis="measured",
        violations_log=tmp_path / "v.jsonl", coverage_log=soak,
        alerter=lambda *a, **k: None,
    )
    import json
    row = json.loads(soak.read_text().strip())
    assert row["window_seconds"] == pytest.approx(186.45)
    assert row["cadence_basis"] == "measured"
    assert row["state_counts"]["could_not_look"] == 1
    assert row["controls_ok"] is True
    assert row["controls_scope"] == "pure_classifier_only", (
        "the control's SCOPE ships beside its verdict — controls_ok is not "
        "evidence the invariant works end to end and must never read as it"
    )


def test_planted_controls_hold():
    """A zero-violation soak row carrying no control cannot be told apart from
    a classifier that has stopped classifying. This is the narrow claim: the
    DEPLOYED classifier still grades a planted residual, a planted flat book
    and a planted unreadable read correctly."""
    assert cfi.run_residual_controls() is True


def test_a_broken_classifier_fails_its_own_control(monkeypatch):
    """Positive control on the control — a probe that cannot fail proves
    nothing."""
    monkeypatch.setattr(
        cfi, "_residual_from_positions",
        lambda *a, **k: cfi.ResidualRead(cfi.RESIDUAL_STATE_FLAT),
    )
    assert cfi.run_residual_controls() is False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _db_with_one_closed_trade():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
        "symbol TEXT, direction TEXT, status TEXT, notes TEXT, "
        "is_backtest INTEGER, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE order_packages (linked_trade_id INTEGER, updated_at TEXT)"
    )
    from datetime import datetime, timezone
    conn.execute(
        "INSERT INTO trades VALUES (1, 'bybit_2', 'ETHUSDT', 'long', 'closed', "
        "NULL, 0, ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    return conn
