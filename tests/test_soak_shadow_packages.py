"""PI-20261004-JC8KDKLF-0002: an `execution: shadow` soak must be graded on the
order packages the shadow leg LOGS, not left `could-not-look` forever.

R5 (soak_book_grade) records `mechanics: null` for every shadow leg, so 13
shadow soaks read could-not-look and nobody could tell a leg that logs
packages from one that silently stopped. The fix counts shadow order packages
per strategy over the grade window (`shadow_package_counts`) and soak_report
grades from that (`grade_shadow`).

MEASURED 2026-10-04 on /api/diag/journal?table=order_packages (5,295 rows, the
7-day window): 7 of the 16 shadow strategies had packages, 9 had none.
/api/bot/order-packages IGNORES `offset` (it returned the same 200 rows for
offset=0 and offset=200), so it cannot be paged and is not used.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

import soak_book_grade as sbg  # noqa: E402
import soak_report as sr  # noqa: E402

ROWS = [
    {"order_package_id": "a", "strategy_name": "mgc_trend_1h", "created_at": "2026-10-03T10:00:00+00:00"},
    {"order_package_id": "b", "strategy_name": "mgc_trend_1h", "created_at": "2026-10-04 10:00:00+00:00"},
    {"order_package_id": "b", "strategy_name": "mgc_trend_1h", "created_at": "2026-10-04 10:00:00+00:00"},  # repeated by mutable-key paging
    {"order_package_id": "c", "strategy_name": "vwap", "created_at": "2026-09-01T10:00:00+00:00"},  # outside window
    {"order_package_id": "d", "strategy_name": "live_leg", "created_at": "2026-10-04T10:00:00+00:00"},
]
SINCE = "2026-09-27T00:00:00Z"


def test_counts_only_shadow_strategies_inside_the_window():
    out = sbg.shadow_package_counts(ROWS, {"mgc_trend_1h", "vwap"}, since_iso=SINCE)
    assert out["mgc_trend_1h"]["n"] == 2    # the repeated row is counted once
    assert out["mgc_trend_1h"]["last_created_at"] == "2026-10-04T10:00:00+00:00"
    assert out["vwap"]["n"] == 0            # present in the store, but outside the window
    assert "live_leg" not in out            # not a shadow strategy


def test_unlisted_shadow_strategy_is_zero_not_missing():
    out = sbg.shadow_package_counts(ROWS, {"turtle_soup"}, since_iso=SINCE)
    assert out["turtle_soup"]["n"] == 0


def test_grade_shadow_accruing_when_packages_logged():
    v, why = sr.grade_shadow({"n": 5, "last_created_at": "2026-10-04T10:00:00+00:00"}, 7)
    assert v == sr.ACCRUING and "5" in why


def test_grade_shadow_zero_is_could_not_look_not_dead():
    # A quiet window is not proof of a silent leg (daily/4h legs signal rarely),
    # so zero must stay due as could-not-look, never graded dead or accruing.
    v, why = sr.grade_shadow({"n": 0, "last_created_at": None}, 7)
    assert v == sr.CNL and "0" in why


def test_grade_shadow_unmeasured_is_could_not_look():
    assert sr.grade_shadow(None, 7)[0] == sr.CNL
