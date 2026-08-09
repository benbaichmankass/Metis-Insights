"""RiskManager gross-exposure ceiling — the per-account exposure policy.

Covers the behaviour that motivated it (BL-20260807-ALPACA-PAPER-ZERO-BUYING-POWER-REFUSES-ALL):
seven independently-correct trades summed to 2.03x equity against a 2.00x
broker limit, because the RiskManager owned LOSS risk but delegated EXPOSURE to
the broker's ``available_usd`` — a wall, not a gradient.

The invariants under test:
  * unset ceiling changes nothing (every existing account);
  * an account already at/over its multiple is REFUSED by evaluate();
  * a trade that would merely cross the ceiling is DOWNSIZED, not refused;
  * unmeasurable exposure never refuses and never clamps (fail-open — an
    unreadable journal must not become a self-inflicted trading outage);
  * the broker margin cap still wins when it binds tighter.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts import exposure as _exposure
from src.units.accounts.risk import RiskManager


def close_to(actual: float, expected: float, tol: float = 1e-6) -> bool:
    """Explicit tolerance instead of ``pytest.approx``.

    NOT a style preference. ``pytest.approx.__eq__`` branches on
    ``sys.modules.get("numpy")``; this repo's test env leaves a truthy stub
    there when numpy is absent, so ``approx`` raises inside ``is_bool`` rather
    than comparing — turning a correct value into a failure, but ONLY when
    another test in the same session has triggered the stub. That makes it a
    heisenbug: these assertions pass file-alone and fail in a full run.
    Filed as BL-20260807-PYTEST-APPROX-NUMPY-STUB.
    """
    return abs(float(actual) - float(expected)) <= tol


def _pkg(entry: float = 100.0, sl: float = 95.0) -> OrderPackage:
    # BTCUSDT so contract_value_usd resolves to 1.0 — keeps the notional
    # arithmetic in the assertions equal to qty x price.
    return OrderPackage(
        strategy="vwap", symbol="BTCUSDT", direction="long",
        entry=entry, sl=sl, tp=entry * 1.1, confidence=1.0,
        meta={"strategy_name": "vwap"},
    )


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    """A minimal trades table the RiskManager can read exposure from."""
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
        "status TEXT, position_size REAL, entry_price REAL, pnl REAL, "
        "created_at TEXT, is_backtest BOOLEAN DEFAULT 1)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(RiskManager, "_risk_db_path", staticmethod(lambda: str(db)))
    return db


def _open_position(db, account: str, qty: float, price: float) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO trades (account_id, status, position_size, entry_price, "
        "is_backtest) VALUES (?, 'open', ?, ?, 0)", (account, qty, price),
    )
    conn.commit()
    conn.close()


def _rm(journal, *, pct: float, equity: float = 10_000.0) -> RiskManager:
    cfg = {"risk_pct": 0.015, "min_qty": 0.001, "qty_precision": 3}
    if pct:
        cfg["max_gross_exposure_pct"] = pct
    rm = RiskManager(cfg, account_id="acct_test")
    rm.current_equity = equity
    rm.daily_high_equity = equity
    return rm


# ── the ceiling is opt-in ────────────────────────────────────────────────────

def test_unset_ceiling_never_gates_and_never_clamps(journal):
    """No declared ceiling => no gate, no clamp. The ENFORCEMENT contract.

    Unchanged from the original ``test_unset_ceiling_is_a_noop``, minus its
    report assertion, which is now the separate test below. The split is
    deliberate: this half is a safety invariant and must never be edited to
    accommodate a feature. The other half was a statement about observability,
    and observability is exactly what changed.
    """
    _open_position(journal, "acct_test", qty=1_000, price=100.0)  # 10x equity
    rm = _rm(journal, pct=0.0)
    assert rm.gross_exposure() is None
    assert rm.exposure_headroom_usd() is None
    ok, reason = rm.evaluate(_pkg())
    assert ok and reason is None
    # And the sizing path is untouched — the second, missed halt vector.
    assert rm.position_size(_pkg(entry=100.0, sl=95.0), balance_usd=10_000.0) > 0


def test_exposure_is_observable_without_declaring_a_ceiling(journal):
    """The change this design exists to make.

    Previously ``report()["exposure"]`` was ``None`` until a ceiling was
    declared — so an operator asked to choose one had to guess a value in order
    to discover whether that value was right. Measurement is now independent of
    policy: this account reads 10.0x with NO ceiling set, which is precisely the
    number needed to choose a sane ceiling, and it is served from a path
    enforcement never reads (asserted above: still no gate, still no clamp).
    """
    _open_position(journal, "acct_test", qty=1_000, price=100.0)  # 100k / 10k
    rm = _rm(journal, pct=0.0)
    exposure = rm.report()["exposure"]
    assert exposure["policy_declared"] is False
    # Null, never 0.0 — a ceiling of zero is the absence of a policy, and
    # reading it as a real ceiling is the halt vector this whole split exists
    # to make unreachable.
    assert exposure["max_gross_exposure_pct"] is None
    assert exposure["measured"] is True
    assert close_to(exposure["exposure_multiple"], 10.0)
    # No policy => nothing to have headroom against.
    assert exposure["headroom_usd"] is None


# ── measurement ──────────────────────────────────────────────────────────────

def test_gross_exposure_is_absolute_and_entry_priced(journal):
    """Offsetting positions still consume the ceiling — gross, not net."""
    _open_position(journal, "acct_test", qty=50, price=100.0)    # +5,000
    _open_position(journal, "acct_test", qty=-30, price=100.0)   # |-3,000|
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    notional, equity, multiple = rm.gross_exposure()
    assert close_to(notional, 8_000.0)   # NOT 2,000 (net)
    assert close_to(equity, 10_000.0)
    assert close_to(multiple, 0.8)


def test_other_accounts_do_not_count(journal):
    _open_position(journal, "acct_test", qty=10, price=100.0)
    _open_position(journal, "someone_else", qty=900, price=100.0)
    rm = _rm(journal, pct=2.0)
    assert close_to(rm.gross_exposure()[0], 1_000.0)


def test_closed_positions_do_not_count(journal):
    _open_position(journal, "acct_test", qty=10, price=100.0)
    conn = sqlite3.connect(journal)
    conn.execute(
        "INSERT INTO trades (account_id, status, position_size, entry_price, "
        "is_backtest) VALUES ('acct_test', 'closed', 500, 100.0, 0)"
    )
    conn.commit()
    conn.close()
    rm = _rm(journal, pct=2.0)
    assert close_to(rm.gross_exposure()[0], 1_000.0)


# ── the gate: refuse only AT the boundary ────────────────────────────────────

def test_evaluate_refuses_at_or_over_the_ceiling(journal):
    _open_position(journal, "acct_test", qty=200, price=100.0)   # 20,000 = 2.0x
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    ok, reason = rm.evaluate(_pkg())
    assert not ok
    assert reason == "GROSS_EXPOSURE_CAP"


def test_evaluate_allows_below_the_ceiling(journal):
    _open_position(journal, "acct_test", qty=100, price=100.0)   # 10,000 = 1.0x
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    ok, reason = rm.evaluate(_pkg())
    assert ok and reason is None


# ── the gradient: downsize rather than refuse ────────────────────────────────

def test_position_size_clamps_into_remaining_headroom(journal):
    """The core fix: a crossing trade is TRIMMED, not rejected.

    Headroom is 2.0x*10,000 - 19,000 = 1,000 USD, so at entry 100 the most
    that may be opened is 10 units — regardless of what risk-based sizing
    wanted.
    """
    _open_position(journal, "acct_test", qty=190, price=100.0)   # 19,000 = 1.9x
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    assert close_to(rm.exposure_headroom_usd(), 1_000.0)
    qty = rm.position_size(_pkg(entry=100.0, sl=95.0), balance_usd=10_000.0)
    assert qty > 0, "a crossing trade must be downsized, never zeroed"
    assert qty <= 10.0 + 1e-9


def test_position_size_returns_zero_when_headroom_below_min_lot(journal):
    _open_position(journal, "acct_test", qty=199.9999, price=100.0)
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    assert rm.position_size(_pkg(entry=100.0, sl=95.0), balance_usd=10_000.0) == 0.0


def test_unclamped_when_well_below_ceiling(journal):
    """Plenty of headroom => the exposure clamp must not bind at all."""
    _open_position(journal, "acct_test", qty=1, price=100.0)
    rm_capped = _rm(journal, pct=2.0, equity=10_000.0)
    rm_free = _rm(journal, pct=0.0, equity=10_000.0)
    pkg = _pkg(entry=100.0, sl=95.0)
    assert close_to(rm_capped.position_size(pkg, balance_usd=10_000.0), 
        rm_free.position_size(pkg, balance_usd=10_000.0)
    )


# ── fail-open on unmeasurable exposure ───────────────────────────────────────

def test_unreadable_journal_does_not_refuse_or_clamp(journal, monkeypatch):
    """An unreadable journal must not become a trading outage."""
    monkeypatch.setattr(
        RiskManager, "_open_gross_notional_from_db", lambda self: None
    )
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    assert rm.gross_exposure() is None
    assert rm.exposure_headroom_usd() is None
    ok, reason = rm.evaluate(_pkg())
    assert ok and reason is None


def test_unknown_equity_does_not_refuse(journal, monkeypatch):
    _open_position(journal, "acct_test", qty=1_000, price=100.0)
    rm = _rm(journal, pct=2.0)
    rm.current_equity = None
    monkeypatch.setattr(
        RiskManager, "_account_equity_from_snapshot", lambda self: None
    )
    assert rm.gross_exposure() is None
    ok, _ = rm.evaluate(_pkg())
    assert ok, "unmeasurable equity must not refuse — report it, don't guess"


def test_report_distinguishes_unmeasured_from_flat(journal, monkeypatch):
    """`measured: False` is not the same statement as an exposure of 0."""
    monkeypatch.setattr(
        RiskManager, "_open_gross_notional_from_db", lambda self: None
    )
    rm = _rm(journal, pct=2.0, equity=10_000.0)
    exposure = rm.report()["exposure"]
    assert exposure["measured"] is False
    assert exposure["exposure_multiple"] is None
    assert exposure["max_gross_exposure_pct"] == 2.0


# ── composition with the broker's own ceiling ────────────────────────────────

def test_broker_margin_cap_still_binds_when_tighter(journal):
    """Our policy layers INSIDE the venue's mechanical limit, never over it."""
    _open_position(journal, "acct_test", qty=1, price=100.0)  # ample headroom
    rm = _rm(journal, pct=10.0, equity=10_000.0)
    qty = rm.position_size(
        _pkg(entry=100.0, sl=95.0), balance_usd=10_000.0, available_usd=200.0
    )
    assert qty <= 2.0 + 1e-9, "broker available_usd must still cap the size"


# ── the three-way split: observe / policy / verdict ──────────────────────────
# The unit tests for the structural fix. These exercise the pure verdict
# function directly, so the halt vectors are asserted unreachable at the level
# they live at rather than only through a RiskManager that happens to call it
# correctly today.

def test_no_policy_allows_before_any_arithmetic_runs():
    """The first halt vector, killed at the source.

    An undeclared ceiling must short-circuit to ALLOW *before* any comparison,
    because `multiple >= 0.0` is true for every exposure that exists including
    a flat account — which is a fleet-wide trading halt.
    """
    obs = _exposure.measured(notional=100_000.0, equity=10_000.0)  # 10x!
    verdict = _exposure.exposure_verdict(obs, None)
    assert verdict.action == _exposure.ALLOW
    assert verdict.reason == _exposure.REASON_NO_POLICY
    # And no headroom, which is the second halt vector: a 0.0 here would clamp
    # every position to nothing.
    assert verdict.headroom_usd is None


@pytest.mark.parametrize("bad_policy", [0.0, -1.0, -0.0])
def test_a_nonpositive_ceiling_is_an_absence_not_a_ceiling(bad_policy):
    """Belt-and-braces: even a caller that passes 0.0 cannot halt the fleet.

    `exposure_policy()` is supposed to return None for an undeclared ceiling,
    so this should be unreachable. It is asserted anyway because the entire
    incident class is `0.0` being read as a real ceiling, and a safety property
    that depends on every caller remembering something is not a safety property.
    """
    obs = _exposure.measured(notional=100_000.0, equity=10_000.0)
    verdict = _exposure.exposure_verdict(obs, bad_policy)
    assert verdict.action == _exposure.ALLOW
    assert verdict.headroom_usd is None


def test_unmeasurable_allows_and_says_why():
    """We did not look. That is not evidence of a breach."""
    obs = _exposure.unmeasurable(_exposure.REASON_NO_EQUITY)
    verdict = _exposure.exposure_verdict(obs, 2.0)
    assert verdict.action == _exposure.ALLOW
    assert verdict.reason == _exposure.REASON_UNMEASURABLE
    assert verdict.headroom_usd is None


def test_unmeasurable_is_not_flat():
    """The distinction that keeps an at-limit account from reading as empty."""
    unmeas = _exposure.unmeasurable(_exposure.REASON_NO_NOTIONAL)
    flat = _exposure.measured(notional=0.0, equity=10_000.0)
    assert unmeas.measured is False and unmeas.multiple is None
    assert flat.measured is True and close_to(flat.multiple, 0.0)
    # Same policy, opposite instructions to the caller: one must not clamp
    # (None), the other has the full ceiling available.
    assert _exposure.exposure_verdict(unmeas, 2.0).headroom_usd is None
    assert close_to(_exposure.exposure_verdict(flat, 2.0).headroom_usd, 20_000.0)


def test_measured_refuses_an_equity_it_cannot_divide_by():
    """A zero equity is an unmeasurable observation, never a measured one.

    Constructing it as `measured` would produce a ZeroDivisionError or, if
    someone "fixed" that with a fallback, a fabricated multiple — the exact
    manufactured-number class `src/runtime/provenance.py` exists to stop.
    """
    with pytest.raises(ValueError):
        _exposure.measured(notional=1_000.0, equity=0.0)


def test_verdict_refuses_at_the_boundary_and_clamps_below_it():
    at = _exposure.measured(notional=20_000.0, equity=10_000.0)   # exactly 2.0x
    below = _exposure.measured(notional=19_000.0, equity=10_000.0)  # 1.9x
    refuse = _exposure.exposure_verdict(at, 2.0)
    clamp = _exposure.exposure_verdict(below, 2.0)
    assert refuse.action == _exposure.REFUSE
    # 0.0, not None: measured, and there is genuinely none left. The null means
    # "do not clamp"; the zero means "clamp to nothing".
    assert close_to(refuse.headroom_usd, 0.0)
    assert clamp.action == _exposure.CLAMP
    assert close_to(clamp.headroom_usd, 1_000.0)


def test_observe_exposure_never_consults_policy(journal):
    """The property that makes the measurement safe to surface anywhere.

    Two managers, same positions, ceilings 0.0 and 2.0 — identical observation.
    If observe_exposure() ever reads policy again, this fails.
    """
    _open_position(journal, "acct_test", qty=100, price=100.0)
    no_policy = _rm(journal, pct=0.0, equity=10_000.0).observe_exposure()
    with_policy = _rm(journal, pct=2.0, equity=10_000.0).observe_exposure()
    assert no_policy == with_policy
    assert no_policy.measured and close_to(no_policy.multiple, 1.0)


def test_exposure_policy_reports_absence_as_none(journal):
    assert _rm(journal, pct=0.0).exposure_policy() is None
    assert _rm(journal, pct=2.0).exposure_policy() == 2.0
