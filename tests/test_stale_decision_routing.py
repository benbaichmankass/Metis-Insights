"""A stale SHIPPED cell only costs money where the leg is routed to live real money.

`m20_coverage_rollup.py`'s ⛔ banner asserted that every stale non-negative cell
"changes exit behaviour on a real-money leg now". Nothing in that script had ever
read `config/accounts.yaml`, so the claim was not computed — it was assumed.

Measured against the field 2026-08-15, it was wrong for **all four** rows:

  htf_pullback_trend_2h  trail_geometry  shipped           bybit_1   paper
  mes_trend_long_1d      trail_geometry  shipped           ib_paper  paper
  mhg_pullback_1d        stale_stop      passed_unshipped  ib_paper  paper
  mhg_pullback_1d        trail_geometry  shipped           ib_paper  paper

⚠️ THE FIRST FIX WAS ALSO WRONG, IN THE SAME SHAPE ONE LEVEL UP. It resolved
routing from the account's `symbols` list, which answers "does some live
real-money account trade this INSTRUMENT" — not "is this LEG routed to one".
`htf_pullback_trend_2h` trades BTCUSDT, which `bybit_2` (real_money) does trade,
so it graded `real_money`; but `bybit_2.strategies` does not list that leg —
only `bybit_1` (paper) declares it. A real-money claim was published to the
operator on that inference before it was caught.

Every account declares `strategies` EXPLICITLY, so the leg->account edge is
exact and there was never a reason to infer it from symbols. The resolver now
keys on that list.

And `account_class` has THREE values in the field, not two: paper x7,
real_money x3, **prop x1** (`breakout_1`, live). A two-state resolver graded
`eth_pullback_prop_2h` — a prop-only leg — as `real_money` because a sibling
account trades ETHUSDT. Prop is a funding class this repo never blends into
either bucket.

That is CLAUDE.md § "Diagnostic provenance" sub-class **A** (the label names a
quantity the code never computed) occurring inside the tool written to stop that
class — which is why it was fixed rather than reworded, and why it is pinned here.

CORRECTED PICTURE: all four stale DECISIONS are paper; ZERO are real-money. The
24 real-money stale cells are all `honest_negative` — stale knowledge, not stale
live behaviour. That is a smaller finding than the one first published, and
saying so is the point.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _rollup():
    spec = importlib.util.spec_from_file_location(
        "m20_coverage_rollup",
        REPO / "scripts" / "research" / "m20_coverage_rollup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROLLUP = _rollup()


# --------------------------------------------------------------------------
# The three states, and the one that must never collapse.
# --------------------------------------------------------------------------

def test_prop_is_its_own_class_not_folded_into_real_money_or_paper() -> None:
    """`breakout_1` is `account_class: prop`, live — a third class.

    A prop-only leg must grade `prop`. Folding it into `real_money` overstates
    money-at-risk; folding it into `paper` understates a fundable account that
    can be lost. The repo never blends prop into either KPI.

    ⚠️ ANCHOR MOVED 2026-09-22 (checklist row R2). ``eth_pullback_prop_2h``
    was this test's prop anchor until the R2 cut removed it from breakout_1's
    roster (Tier-3, operator instruction "Cut the legs that don't clear the
    bar"; it fails clause C4 of B1's four-clause evidence bar). It now resolves
    ``paper`` — it is still declared by bybit_1, the soak book. The anchor moved
    to the two _prop legs that remain routed to breakout_1; the old anchor is
    kept below as a MOVEMENT assertion, because a leg silently reappearing on a
    fundable account is the thing worth catching.
    """
    routing = ROLLUP._funding_by_leg()
    assert ROLLUP._leg_funding("trend_donchian_eth_prop", routing) == "prop"
    assert ROLLUP._leg_funding("trend_donchian_sol_prop", routing) == "prop"
    assert ROLLUP._leg_funding("eth_pullback_prop_2h", routing) == "paper", (
        "eth_pullback_prop_2h graded prop again — R2 cut it from breakout_1 on "
        "2026-09-22 and bybit_1 (paper) is its only remaining route. If it was "
        "deliberately re-promoted, that is Tier-3 and needs a record clearing "
        "the four-clause bar")


def test_routing_is_keyed_on_declared_strategies_not_on_symbols() -> None:
    """The regression that published a false real-money claim.

    `htf_pullback_trend_2h` and `trend_donchian_eth_4h` both trade instruments
    that a live real-money account also trades. Only the SECOND is declared by
    `bybit_2.strategies`. A symbol-keyed resolver grades both `real_money`;
    the leg-keyed one separates them.

    ⚠️ POSITIVE ANCHOR MOVED 2026-09-22 (checklist row R2): it was
    `eth_pullback_2h` until the R2 cut removed that leg from bybit_2. The move
    STRENGTHENS the test rather than weakening it — eth_pullback_2h is now a
    SECOND negative control of exactly the shape this test exists for: it
    trades ETHUSDT, which the real-money bybit_2 does trade, and bybit_2 no
    longer declares it. A symbol-keyed resolver would still grade it
    `real_money` today; the leg-keyed one must not.
    """
    routing = ROLLUP._funding_by_leg()
    assert ROLLUP._leg_funding("trend_donchian_eth_4h", routing) == "real_money"
    assert ROLLUP._leg_funding("eth_pullback_2h", routing) == "paper", (
        "eth_pullback_2h graded real_money — it trades ETHUSDT, which bybit_2 "
        "trades, but since the R2 cut (2026-09-22) bybit_2.strategies does not "
        "list this leg. That is the same symbol-keyed inference that published "
        "a false real-money claim on 2026-08-15")
    assert ROLLUP._leg_funding("htf_pullback_trend_2h", routing) == "paper", (
        "htf_pullback_trend_2h graded real_money again — it trades BTCUSDT, "
        "which bybit_2 trades, but bybit_2.strategies does not list this leg. "
        "That inference is what published a false real-money claim on 2026-08-15")


def test_unreadable_config_is_unresolved_NOT_paper() -> None:
    """`None` from the config read is "we could not look", not "it is paper".

    This is the whole point of the third state. Defaulting an unknown to the
    safe-sounding value is what § "Collapsed states" forbids: a reader who sees
    `paper` stops worrying, and they would be doing so over a routing nobody
    resolved.
    """
    assert ROLLUP._leg_funding("eth_pullback_2h", None) == "unresolved"


def test_an_undeclared_leg_is_unresolved_NOT_paper() -> None:
    """A leg no account declares is unresolved for the same reason."""
    assert ROLLUP._leg_funding("no_such_leg_at_all", {"x": "real_money"}) == "unresolved"


def test_a_leg_with_no_declared_body_is_unresolved() -> None:
    assert ROLLUP._leg_funding(None, {"x": "real_money"}) == "unresolved"


def test_real_money_wins_over_paper_when_both_declare_the_leg() -> None:
    """One live real-money route makes the leg money-at-risk.

    `trend_donchian_eth_4h` is declared by `bybit_2` (real) AND by the paper
    books `bybit_1`/`bybit_portfolio`. Letting a paper mirror mask the real
    route would under-report exactly the cell that matters.

    ⚠️ The example leg was `eth_pullback_2h` until 2026-09-22 (checklist row
    R2), which cut it from bybit_2. This assertion passes a literal map rather
    than reading the config, so it did not fail — the DOCSTRING had gone false
    while the test stayed green, which is precisely the "field beats comment"
    drift this module was written about. Corrected rather than left.
    """
    assert ROLLUP._leg_funding("trend_donchian_eth_4h",
                               {"trend_donchian_eth_4h": "real_money"}) == "real_money"


def test_a_dry_run_real_money_account_does_NOT_make_a_leg_money_at_risk() -> None:
    """`real_money` requires BOTH gates: class real_money AND mode live.

    `ib_live` is `account_class: real_money` at `mode: dry_run` — it places no
    live order. Keying on the class alone would grade every MES leg as
    money-at-risk and re-inflate the exact over-claim this module fixes.
    """
    import yaml
    accounts = yaml.safe_load(
        (REPO / "config" / "accounts.yaml").read_text())["accounts"]
    ib_live = accounts.get("ib_live")
    if not ib_live or ib_live.get("mode") == "live":
        pytest.skip("ib_live absent or now live — the fixture this pins is gone")
    assert ib_live.get("account_class") == "real_money"
    assert "MES" in (ib_live.get("symbols") or [])
    # ...and yet the MES leg resolves to paper: ib_paper is the only LIVE route.
    assert ROLLUP._funding_by_leg().get("mes_trend_long_1d") == "paper", (
        "MES graded money-at-risk. If ib_live was deliberately flipped to "
        "mode: live, then mes_trend_long_1d's stale shipped trail_geometry cell "
        "just became a real-money exposure — re-read the stale-decisions table "
        "before changing this test.")


# --------------------------------------------------------------------------
# Positive controls FIRST — a resolver that resolved nothing would make every
# assertion above vacuously true. A negative needs a denominator.
# --------------------------------------------------------------------------

def test_the_resolver_actually_resolves_something() -> None:
    by_leg = ROLLUP._funding_by_leg()
    assert by_leg, "accounts.yaml resolved to nothing — every routing verdict above is vacuous"
    assert "trend_donchian_eth_4h" in by_leg, "the leg->account map resolved no known leg"
    assert "real_money" in by_leg.values(), (
        "no leg anywhere resolved to real_money, so a real_money verdict "
        "could never be produced and the paper counts prove nothing")


def test_a_known_real_money_leg_resolves_real_money() -> None:
    """Positive anchor — without one, every `paper` verdict below is vacuous.

    Anchor moved from `eth_pullback_2h` on 2026-09-22 (checklist row R2) when
    that leg was cut from bybit_2. `trend_donchian_eth_4h` is one of the three
    legs bybit_2 still carries.
    """
    assert ROLLUP._funding_by_leg().get("trend_donchian_eth_4h") == "real_money"


# --------------------------------------------------------------------------
# The finding itself, held against the live matrix.
# --------------------------------------------------------------------------

def test_the_stale_decision_routing_split_is_not_uniform() -> None:
    """The split is the finding — a uniform answer would mean nothing was computed.

    Deliberately asserts the SHAPE (more than one class present, at least one
    real_money) rather than the exact 1/3 counts, so a re-sweep that clears one
    cell updates the numbers without failing a test that is really about the
    banner having stopped over-claiming.
    """
    # Real-money row moved from `eth_pullback_2h` to `trend_donchian_eth_4h` on
    # 2026-09-22 (checklist row R2) — the former is no longer on any live
    # real-money roster, so it could no longer supply the real_money side of
    # the split this test is about.
    dec = [("trend_donchian_eth_4h", "trail_geometry", "shipped", "2026-07-12", "2026-08-10"),
           ("mes_trend_long_1d", "trail_geometry", "shipped", "2026-08-09", "2026-08-10"),
           ("mhg_pullback_1d", "trail_geometry", "shipped", "2026-08-09", "2026-08-10")]
    split = ROLLUP._stale_decision_funding(dec)
    assert split.get("real_money", 0) >= 1, split
    assert split.get("paper", 0) >= 1, (
        f"every stale decision graded real_money ({split}) — which is what the "
        "banner used to assert unconditionally. If that is now genuinely true, "
        "say so from the routing, not from the old wording")


def test_the_banner_no_longer_asserts_real_money_unconditionally() -> None:
    """The wording fix travels with the computation.

    Pins the retraction the same way `test_exit_head_corpus_exemption_is_honest`
    pins its own: the false sentence may be quoted as corrected, never asserted.
    """
    src = (REPO / "scripts" / "research" / "m20_coverage_rollup.py").read_text()
    bad = "it changes exit behaviour on a real-money leg now"
    if bad in src:
        assert "Until 2026-08-15 this banner asserted" in src, (
            "the unconditional real-money claim is present without the "
            "retraction that makes it a quote")
    assert "ROUTED TO A LIVE REAL-MONEY ACCOUNT" in src
