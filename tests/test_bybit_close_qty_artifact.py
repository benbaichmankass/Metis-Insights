"""MI-139 — a Bybit reduce-only close must not be refused on a float artifact.

``WO-20260906-A-BYBIT-REDUCE-ONLY-CLOSE-IS-REJECTED``. A reduce-only close on
``bybit_1``/SOLUSDT failed 3 consecutive times (2026-09-06T03:37:22Z) because
``close_open_position`` put ``"qty": str(qty)`` on the wire and *qty* was
``33.299999999999955`` — the IEEE-754 residue of ``289.4 - 256.1``, written by
``apply_intent_reduce_partial_close``. Bybit refused it (``Qty invalid``,
ErrCode 10001) and **the position could not be flattened by the normal path.**

Two parts, and neither subsumes the other:

* **Part 1** stops the artifact being MANUFACTURED (``round(x, 8)`` on the
  persisted residual). It is not step alignment.
* **Part 2** repairs one at the WIRE (``qty_legalize.snap_artifact_qty``),
  snapping to NEAREST. It is not a rounding of the journal.

⚠️ **The central anti-regression assertion in this module is that the snap
NEVER FLOORS.** ``legalize_qty`` floors on purpose — realised risk must not
exceed the sized cap — which is right for an ENTRY. On a CLOSE the polarity
inverts: flooring ``33.299999999999955`` to ``33.2`` under-closes by a full
step and orphans 0.1 SOL with no journal row, converting a loud rejection into
a silent naked residue. ``test_snap_never_floors_any_artifact`` fails if anyone
"harmonises" the two.

POPULATION for the nine artifacts below: ``bybit_*`` accounts, non-backtest,
``position_size > 0``, ``trades`` ids 4519-5518 (the newest 1000 rows,
``/api/diag/journal?table=trades&limit=1000``, read 2026-09-06 ~11:02Z).
**n = 631; 9 off-grid = 1.43%**, including ``id=5342`` on ``bybit_2``, which is
REAL MONEY.

VENUE LOT RULES are MEASURED, not assumed: Bybit V5
``/v5/market/instruments-info?category=linear``, read 2026-09-06T11:03:10Z via
trainer-vm-diag issue #11120 (Actions run 34029110078).
"""
from __future__ import annotations

import pytest

from src.core.instrument_profile import InstrumentProfile
from src.units.accounts import execute as execute_mod
from src.units.accounts import precision, qty_legalize
from src.units.accounts.qty_legalize import snap_artifact_qty

_BYBIT = {"account_id": "bybit_1", "exchange": "bybit", "market_type": "linear"}

# MEASURED venue lotSizeFilter (see module docstring for the locator).
VENUE_STEPS = {
    "XRPUSDT": 0.1,
    "AVAXUSDT": 0.1,
    "SOLUSDT": 0.1,
    "ADAUSDT": 1.0,
    "ETHUSDT": 0.01,
    "BTCUSDT": 0.001,
    "BNBUSDT": 0.01,
}

# The nine off-grid `position_size` values, verbatim from the journal, with the
# account+symbol they were written under. Repairing every one of these given
# the correct step is the whole point of Part 2.
ARTIFACTS = [
    ("bybit_1", "SOLUSDT", 33.299999999999955, "33.3"),
    ("bybit_1", "XRPUSDT", 512.2000000000007, "512.2"),
    ("bybit_portfolio", "ETHUSDT", 6.640000000000001, "6.64"),
    ("bybit_2", "ETHUSDT", 0.029999999999999995, "0.03"),   # REAL MONEY row
    ("bybit_1", "BTCUSDT", 0.0020000000000000018, "0.002"),
    ("bybit_1", "BTCUSDT", 0.42400000000000004, "0.424"),
    ("bybit_1", "SOLUSDT", 253.29999999999973, "253.3"),
    ("bybit_1", "SOLUSDT", 292.2999999999997, "292.3"),
    ("bybit_1", "XRPUSDT", 659.8999999999942, "659.9"),
]

# Legal quantities SAMPLED FROM THE LIVE VENUE (/api/diag/exchange_positions,
# read 2026-09-06T11:02:43Z) — every one must come back as the IDENTITY.
LIVE_LEGAL = [
    ("SOLUSDT", 3.6),
    ("ETHUSDT", 0.31),
    ("ETHUSDT", 0.04),
    ("ETHUSDT", 9.41),
    ("XRPUSDT", 7114.1),
    ("XRPUSDT", 58.5),
    ("XRPUSDT", 11903.8),
    ("ADAUSDT", 79855.0),
    ("AVAXUSDT", 3054.0),
    ("SOLUSDT", 33.3),
]

# Genuinely off-step values that are NOT artifacts. These must pass through
# untouched so they keep failing LOUDLY — silently moving them is a decision no
# evidence supports.
GENUINE_OFF_STEP = [
    ("SOLUSDT", 33.35),
    ("XRPUSDT", 659.85),
    ("BTCUSDT", 0.0025),
    ("XRPUSDT", 7656.059),
    ("ADAUSDT", 79855.4),
]


def _profiles():
    """Profiles carrying the MEASURED venue steps, for every symbol at once."""
    return {
        sym: InstrumentProfile(
            symbol=sym, exchange="bybit", category="linear",
            base_asset=sym[:3], quote_currency="USDT",
            settlement_currency="USDT", tick_size=0.01,
            min_qty=step, qty_step=step,
        )
        for sym, step in VENUE_STEPS.items()
    }


@pytest.fixture(autouse=True)
def _clean_caches():
    qty_legalize._reset_profile_cache()
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()
    yield
    qty_legalize._reset_profile_cache()
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()


def _snap(symbol, qty, account_id="bybit_1"):
    cfg = dict(_BYBIT, account_id=account_id)
    return snap_artifact_qty(
        qty, account_cfg=cfg, symbol=symbol, profiles=_profiles(),
    )


# --------------------------------------------------------------------------
# Part 2 — the snap
# --------------------------------------------------------------------------

class TestArtifactsRepair:
    @pytest.mark.parametrize("account,symbol,raw,expected", ARTIFACTS)
    def test_every_observed_artifact_repairs(self, account, symbol, raw, expected):
        qty, qty_str, state = _snap(symbol, raw, account_id=account)
        assert state == "snapped", f"{symbol} {raw!r} was not recognised as an artifact"
        assert qty_str == expected
        assert qty == pytest.approx(float(expected), abs=1e-12)

    @pytest.mark.parametrize("account,symbol,raw,expected", ARTIFACTS)
    def test_snap_never_floors_any_artifact(self, account, symbol, raw, expected):
        """THE anti-regression test. Flooring a CLOSE orphans a step of dust.

        Every one of the nine artifacts sits just BELOW or just ABOVE its grid
        point; a floor would move the below-cases down a whole step. Assert the
        result is the NEAREST grid point and, specifically, never lower than
        the value the venue would have accepted.
        """
        qty, _qty_str, _state = _snap(symbol, raw, account_id=account)
        step = VENUE_STEPS[symbol]
        floored = (raw // step) * step
        assert qty == pytest.approx(float(expected), abs=1e-12)
        if abs(floored - float(expected)) > 1e-12:
            # This artifact is one a floor WOULD have damaged — prove we didn't.
            assert qty > floored + step / 2, (
                f"{symbol}: snap floored {raw!r} to {qty} — that under-closes "
                f"by a full step and orphans dust with no journal row"
            )

    def test_the_motivating_row_end_to_end(self):
        """The exact value Bybit refused, and the exact value a floor gives."""
        raw = 33.299999999999955
        assert raw == 289.4 - 256.1          # reproducible, not a transcription
        qty, qty_str, state = _snap("SOLUSDT", raw)
        assert (qty_str, state) == ("33.3", "snapped")
        # The naive "just wire legalize_qty in" fix, for contrast:
        from src.units.accounts.qty_legalize import legalize_qty
        floored = legalize_qty(
            raw, account_cfg=_BYBIT, symbol="SOLUSDT", profiles=_profiles(),
        )
        assert floored.qty == pytest.approx(33.2, abs=1e-9), (
            "legalize_qty is expected to FLOOR — if this changes, re-read why "
            "snap_artifact_qty exists before touching either"
        )
        assert qty > floored.qty


class TestLegalQuantitiesAreTheIdentity:
    @pytest.mark.parametrize("symbol,qty", LIVE_LEGAL)
    def test_live_legal_qty_unchanged(self, symbol, qty):
        out, qty_str, state = _snap(symbol, qty)
        assert state == "unchanged"
        assert out == qty
        # Byte-for-byte the string the pre-fix line sent.
        assert qty_str == str(float(qty))

    @pytest.mark.parametrize("symbol,step", sorted(VENUE_STEPS.items()))
    def test_a_grid_walk_is_the_identity(self, symbol, step):
        """Every multiple of the step, across three decades, is untouched."""
        for mult in (1, 2, 3, 7, 10, 99, 100, 1234):
            qty = round(step * mult, 10)
            out, qty_str, state = _snap(symbol, qty)
            assert state == "unchanged", f"{symbol} {qty!r} -> {state}"
            assert out == qty
            assert qty_str == str(float(qty))


class TestGenuineOffStepFailsLoudly:
    @pytest.mark.parametrize("symbol,qty", GENUINE_OFF_STEP)
    def test_off_step_passes_through_untouched(self, symbol, qty):
        out, qty_str, state = _snap(symbol, qty)
        assert state == "not_graded"
        assert out == qty
        assert qty_str == str(float(qty)), (
            "a genuinely off-step qty must reach the wire byte-identically so "
            "the venue still rejects it VISIBLY"
        )


class TestWeDidNotLookIsNotAgreement:
    def test_unresolvable_rule_passes_through(self):
        out, qty_str, state = snap_artifact_qty(
            1.2345678, account_cfg=_BYBIT, symbol="DOGEUSDT", profiles={},
        )
        assert state == "not_graded"
        assert (out, qty_str) == (1.2345678, str(1.2345678))

    def test_non_bybit_account_with_no_profile_passes_through(self):
        cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers"}
        out, qty_str, state = snap_artifact_qty(
            3.6, account_cfg=cfg, symbol="MES", profiles={},
        )
        assert state == "not_graded"
        assert (out, qty_str) == (3.6, "3.6")

    def test_a_raising_resolver_degrades_to_passthrough(self, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("venue unreachable")
        monkeypatch.setattr(qty_legalize, "_resolve_venue_lot_rule", _boom)
        out, qty_str, state = snap_artifact_qty(
            33.299999999999955, account_cfg=_BYBIT, symbol="SOLUSDT",
        )
        assert state == "not_graded"
        assert (out, qty_str) == (33.299999999999955, str(33.299999999999955))

    def test_a_zero_or_negative_step_is_not_graded(self, monkeypatch):
        monkeypatch.setattr(
            qty_legalize, "_resolve_venue_lot_rule",
            lambda *a, **kw: (0.0, 0.0, None, "absent", "test"),
        )
        _out, _s, state = snap_artifact_qty(
            33.3, account_cfg=_BYBIT, symbol="SOLUSDT",
        )
        assert state == "not_graded"

    def test_snapping_to_zero_is_refused(self):
        """A close that sends 0 is not a close — let the venue judge it."""
        out, _qty_str, state = _snap("SOLUSDT", 0.04)   # nearest 0.1-grid = 0.0
        assert state == "not_graded"
        assert out == 0.04

    def test_three_states_are_all_reachable(self):
        """Collapsed-state discipline: none of the three is decorative."""
        states = {
            _snap("SOLUSDT", 33.299999999999955)[2],
            _snap("SOLUSDT", 33.3)[2],
            _snap("SOLUSDT", 33.35)[2],
        }
        assert states == {"snapped", "unchanged", "not_graded"}


class TestBlastRadiusIsBoundedToFailingOrders:
    def test_wire_string_changes_only_when_off_grid(self):
        """The safety argument, asserted rather than argued.

        Across the union of every legal quantity and every genuine off-step
        value, the string handed to the wire is byte-identical to ``str(float
        (qty))`` — the exact expression the pre-fix line used. It differs ONLY
        for the artifacts, every one of which Bybit rejects today.
        """
        for symbol, qty in LIVE_LEGAL + GENUINE_OFF_STEP:
            _out, qty_str, state = _snap(symbol, qty)
            assert state in ("unchanged", "not_graded")
            assert qty_str == str(float(qty)), f"{symbol} {qty!r}"
        for account, symbol, raw, expected in ARTIFACTS:
            _out, qty_str, state = _snap(symbol, raw, account_id=account)
            assert state == "snapped"
            assert qty_str != str(float(raw))
            assert qty_str == expected

    def test_snap_moves_by_less_than_half_a_step(self):
        for account, symbol, raw, _expected in ARTIFACTS:
            qty, _s, _st = _snap(symbol, raw, account_id=account)
            assert abs(qty - raw) < VENUE_STEPS[symbol] / 2


# --------------------------------------------------------------------------
# Part 1 — round(x, 8) is the identity on every legal quantity
# --------------------------------------------------------------------------

class TestRoundEightIsTheIdentity:
    @pytest.mark.parametrize("symbol,qty", LIVE_LEGAL)
    def test_round8_changes_no_live_legal_quantity(self, symbol, qty):
        assert round(qty, 8) == qty

    @pytest.mark.parametrize("symbol,step", sorted(VENUE_STEPS.items()))
    def test_round8_changes_no_grid_point(self, symbol, step):
        for mult in (1, 2, 3, 7, 10, 99, 100, 1234, 75000):
            qty = round(step * mult, 10)
            assert round(qty, 8) == qty, f"{symbol} {qty!r}"

    @pytest.mark.parametrize("account,symbol,raw,expected", ARTIFACTS)
    def test_round8_destroys_every_observed_artifact(self, account, symbol, raw, expected):
        assert round(raw, 8) == pytest.approx(float(expected), abs=1e-12)
        assert round(raw, 8) != raw

    def test_round8_does_not_step_align(self):
        """Part 1 does not subsume Part 2 — a genuine off-step value survives."""
        assert round(33.35, 8) == 33.35
        assert round(0.0025, 8) == 0.0025


class TestPartialCloseWritesARoundedResidual:
    """The persisted residual, at the one writer that produced the artifacts."""

    class _FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, _sql, _params):
            return self

        def fetchall(self):
            return self._rows

        def close(self):
            pass

    class _FakeDB:
        def __init__(self, rows):
            self._rows = rows
            self.updates = []

        def connect(self):
            return TestPartialCloseWritesARoundedResidual._FakeConn(self._rows)

        def update_trade(self, trade_id, fields):
            self.updates.append((trade_id, fields))

    def test_residual_is_rounded_not_a_raw_subtraction(self):
        # The reproducible pair behind the live rejection: 289.4 - 256.1.
        db = self._FakeDB([(4242, 289.4)])
        out = execute_mod.apply_intent_reduce_partial_close(
            db, account_id="bybit_1", symbol="SOLUSDT",
            reduce_direction="short", reduce_qty=256.1,
            fill_price=106.4, closed_at_iso="2026-09-06T03:32:39+00:00",
        )
        assert out["allocations"] == [{"parent_id": 4242, "consumed": 256.1}]
        assert len(db.updates) == 1
        trade_id, fields = db.updates[0]
        assert trade_id == 4242
        written = fields["position_size"]
        assert written == 33.3, f"got {written!r} — the raw subtraction is 33.299999999999955"
        assert written != 289.4 - 256.1
        # And what it writes is now something the venue accepts unchanged.
        _q, _s, state = _snap("SOLUSDT", written)
        assert state == "unchanged"

    def test_a_legal_residual_is_untouched(self):
        db = self._FakeDB([(7, 10.0)])
        execute_mod.apply_intent_reduce_partial_close(
            db, account_id="bybit_1", symbol="SOLUSDT",
            reduce_direction="short", reduce_qty=4.0,
            fill_price=None, closed_at_iso="2026-09-06T00:00:00+00:00",
        )
        assert db.updates[0][1]["position_size"] == 6.0

    def test_full_consumption_still_closes_the_row(self):
        db = self._FakeDB([(9, 5.0)])
        execute_mod.apply_intent_reduce_partial_close(
            db, account_id="bybit_1", symbol="SOLUSDT",
            reduce_direction="short", reduce_qty=5.0,
            fill_price=100.0, closed_at_iso="2026-09-06T00:00:00+00:00",
        )
        assert db.updates[0][1]["status"] == "closed"
        assert db.updates[0][1]["exit_reason"] == "intent_reduce"


# --------------------------------------------------------------------------
# Part 2b — the wire
# --------------------------------------------------------------------------

class _FakeBybitClient:
    """Records the kwargs a close would put on the wire; accepts everything."""

    def __init__(self):
        self.calls = []

    def place_order(self, **kwargs):
        self.calls.append(kwargs)
        return {"retCode": 0, "result": {"orderId": "OID-1"}}

    def cancel_order(self, **_kwargs):
        return {"retCode": 0}


class TestCloseOpenPositionPutsALegalQtyOnTheWire:
    def _close(self, monkeypatch, qty, symbol="SOLUSDT"):
        monkeypatch.setattr(
            qty_legalize, "_load_profiles", lambda _p=None: _profiles(),
        )
        client = _FakeBybitClient()
        res = execute_mod.close_open_position(
            client, dict(_BYBIT), symbol=symbol, side="long", qty=qty,
        )
        return res, client

    def test_the_refused_quantity_now_goes_out_step_aligned(self, monkeypatch):
        res, client = self._close(monkeypatch, 33.299999999999955)
        assert res["ok"] is True
        assert client.calls[0]["qty"] == "33.3"
        assert client.calls[0]["reduceOnly"] is True

    def test_a_legal_quantity_goes_out_byte_identically(self, monkeypatch):
        _res, client = self._close(monkeypatch, 3.6)
        assert client.calls[0]["qty"] == str(float(3.6)) == "3.6"

    def test_a_genuine_off_step_quantity_is_not_silently_moved(self, monkeypatch):
        _res, client = self._close(monkeypatch, 33.35)
        assert client.calls[0]["qty"] == "33.35"

    def test_xrp_artifact_uses_the_corrected_step(self, monkeypatch):
        _res, client = self._close(monkeypatch, 659.8999999999942, symbol="XRPUSDT")
        assert client.calls[0]["qty"] == "659.9", (
            "XRPUSDT is NOT in precision._STATIC_LOT_RULE, so this value can "
            "only resolve through the instrument profile. At the pre-fix "
            "declared step of 1.0 it would miss the grid by 0.1 — far outside "
            "the artifact tolerance — and grade `not_graded`, i.e. reach the "
            "wire unrepaired and keep being refused. The config correction is "
            "load-bearing for this path, not cosmetic."
        )


# --------------------------------------------------------------------------
# config/instruments.yaml must agree with the venue
# --------------------------------------------------------------------------

class TestInstrumentProfileMatchesTheVenue:
    """XRPUSDT declared 1.0/1.0 while Bybit publishes 0.1/0.1 — field beats
    comment. AVAXUSDT was SUSPECTED wrong and is NOT: its sub-0.1 journal rows
    all carry ``status='rejected'``, i.e. pre-legalization sizes that never
    reached the venue. Both facts are pinned here so neither drifts back.
    """

    @pytest.mark.parametrize("symbol,step", sorted(VENUE_STEPS.items()))
    def test_declared_step_and_min_match_the_measured_venue(self, symbol, step):
        from src.core.profile_loader import load_instrument_profiles
        profs = load_instrument_profiles(None) or {}
        prof = profs.get(symbol)
        assert prof is not None, f"{symbol} missing from config/instruments.yaml"
        assert float(prof.qty_step) == pytest.approx(step), (
            f"{symbol} qty_step drifted from the venue-measured value {step} "
            "(Bybit instruments-info, 2026-09-06T11:03:10Z, trainer-diag #11120)"
        )
        assert float(prof.min_qty) == pytest.approx(step)
