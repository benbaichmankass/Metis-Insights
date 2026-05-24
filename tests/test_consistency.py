"""Unit tests for scripts/ops/consistency.py (S9 month-over-month score)."""
from __future__ import annotations

from scripts.ops.consistency import monthly_consistency


def test_empty_stream():
    c = monthly_consistency([])
    assert c["months"] == 0
    assert c["pct_months_positive"] == 0.0
    assert c["consistency_ratio"] is None
    assert c["by_month"] == {}


def test_groups_by_calendar_month_and_sums():
    ev = [
        ("2023-01-05T00:00:00+00:00", 1.0),
        ("2023-01-20 12:00:00", 0.5),   # same month, different ts format
        ("2023-02-10", -2.0),
    ]
    c = monthly_consistency(ev)
    assert c["months"] == 2
    assert c["by_month"] == {"2023-01": 1.5, "2023-02": -2.0}
    assert c["months_positive"] == 1
    assert c["pct_months_positive"] == 50.0
    assert c["worst_month_r"] == -2.0
    assert c["best_month_r"] == 1.5


def test_consecutive_negative_streak():
    ev = [
        ("2023-01-01", 1.0),
        ("2023-02-01", -1.0),
        ("2023-03-01", -0.5),
        ("2023-04-01", -0.2),
        ("2023-05-01", 0.3),
    ]
    c = monthly_consistency(ev)
    assert c["max_consecutive_negative_months"] == 3


def test_top_month_share_flags_period_dependence():
    # One huge month carries almost all the (positive) return.
    ev = [
        ("2023-01-01", 0.1),
        ("2023-02-01", 0.1),
        ("2023-03-01", 9.8),
    ]
    c = monthly_consistency(ev)
    assert c["best_month_r"] == 9.8
    assert c["top_month_share"] > 0.95   # lopsided → leans on one period


def test_consistency_ratio_steady_vs_lumpy():
    steady = monthly_consistency([(f"2023-{m:02d}-01", 1.0) for m in range(1, 7)])
    assert steady["consistency_ratio"] is None or steady["monthly_std_r"] == 0.0
    lumpy = monthly_consistency(
        [("2023-01-01", 5.0), ("2023-02-01", -4.0), ("2023-03-01", 0.1)]
    )
    # Lumpy: mean small, std large → low/near-zero ratio.
    assert lumpy["consistency_ratio"] is not None
    assert abs(lumpy["consistency_ratio"]) < 1.0


def test_top_month_share_zero_for_net_loser():
    # total <= 0 → share is not meaningful, returns 0.0 (no div-by-≤0).
    ev = [("2023-01-01", -1.0), ("2023-02-01", 0.5)]
    c = monthly_consistency(ev)
    assert c["top_month_share"] == 0.0
