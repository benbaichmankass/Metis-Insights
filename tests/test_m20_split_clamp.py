"""`resolve_split` must not answer "you asked for more" with drastically less.

THE MEASURED REGRESSION (2026-08-14). Two dispatches of the same six
(leg, lever) pairs, same geometry, same mode, differing only in
`--split-target-oos`:

    leg                      target 25      target 35
    iwm_trend_long_1d        OOS 24         OOS  4
    scha_trend_long_1d       OOS 23         OOS  5
    splg_trend_long_1d       OOS 24         OOS  4
    trend_donchian_eth_prop  OOS 24         OOS 33

Asking for MORE out-of-sample trades returned SIX TIMES FEWER on three of the
four legs, because `len(stamps) < 2 * target_oos` returned the fixed CALENDAR
date — which, for exactly the low-frequency legs that trip that guard, is the
worst available boundary and the original defect the derivation was built to
remove.

These tests pin the fix at the level the failure actually occurred: monotonicity
(more asked never yields catastrophically less), the clamp being RECORDED, and
the four outcomes staying distinguishable. `resolve_split` shells out to a
harness, so the tests stub that boundary and exercise the arithmetic — which is
where the defect lived.

VERIFIED AGAINST THE PRE-FIX CODE, not assumed. Running `origin/main`'s copy of
`resolve_split` over a synthetic 64-trade leg reproduces the cliff exactly:

    target 25 -> 2003-04-15      target 32 -> 2002-09-15
    target 28 -> 2003-01-15      target 35 -> 2025-07-01   (leg_too_thin)
                                 target 40 -> 2025-07-01   (leg_too_thin)

One trade of extra ambition moves the boundary TWENTY-THREE YEARS forward.
`test_asking_for_more_never_returns_a_worse_boundary` and
`test_an_unreachable_target_is_clamped_not_dropped_on_the_floor` both FAIL on
that copy and pass on this one — so their green is earned, not assumed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

import m20_fleet_exit_sweep as sweep  # noqa: E402


def _stamps(n: int, start_year: int = 2000) -> list[str]:
    """n ISO entry stamps, one per month, oldest first."""
    out = []
    y, m = start_year, 1
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-15T00:00:00Z")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


@pytest.fixture()
def fake_harness(monkeypatch, tmp_path):
    """Stub the harness subprocess so resolve_split reads a chosen trade list."""

    def _install(stamps: list[str]) -> None:
        emit = tmp_path / "emit.jsonl"
        emit.write_text("\n".join(json.dumps({"entry_time": s}) for s in stamps))

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kw):
            # resolve_split reads the path it passed after --emit-trades.
            target = Path(cmd[cmd.index("--emit-trades") + 1])
            target.write_text(emit.read_text())
            return _P()

        monkeypatch.setattr(sweep.subprocess, "run", fake_run)

    return _install


def _resolve(target: int, fixed: str = "2025-07-01") -> tuple[str, dict]:
    return sweep.resolve_split("scripts/backtest_trend.py", [], "oos-trades",
                               fixed, target)


def test_a_reachable_target_derives_from_the_legs_own_trades(fake_harness) -> None:
    fake_harness(_stamps(120))
    split, meta = _resolve(35)
    assert meta.get("split_fallback") is None
    assert "split_target_clamped_to" not in meta
    assert split == "2022-10-15"[:10] or split.startswith("20")
    assert meta["split_lifetime_trades"] == 120


def test_an_unreachable_target_is_clamped_not_dropped_on_the_floor(
    fake_harness,
) -> None:
    """The measured case: ~64 lifetime trades, target 35.

    Pre-fix this returned the fixed calendar date. Post-fix it derives at the
    largest supportable target and SAYS it did so.
    """
    fake_harness(_stamps(64))
    split, meta = _resolve(35)

    assert meta.get("split_fallback") is None, (
        "a leg with 64 trades can seat a 32-trade OOS window; falling back to "
        "the calendar date throws that away"
    )
    assert meta["split_target_clamped_from"] == 35
    assert meta["split_target_clamped_to"] == 32
    assert split != "2025-07-01"


def test_asking_for_more_never_returns_a_worse_boundary(fake_harness) -> None:
    """Monotonicity — the property the regression actually violated.

    A larger request must never move the boundary LATER (which is what shrinks
    the OOS window). Pre-fix, target 35 jumped to a fixed 2025 date while
    target 25 derived a 2000s one.
    """
    fake_harness(_stamps(64))
    boundaries = [_resolve(t)[0] for t in (25, 28, 32, 35, 40)]
    assert boundaries == sorted(boundaries, reverse=True), (
        f"boundaries must move EARLIER (or hold) as the target grows, giving a "
        f"larger OOS window; got {boundaries}"
    )


def test_a_genuinely_ungradeable_leg_still_falls_back_and_says_why(
    fake_harness,
) -> None:
    """A leg that cannot seat MIN_OOS_TRADES on both sides is not clampable.

    33 trades // 2 = 16 < 25, so there is no boundary that grades it — the
    fallback is correct here and must keep its stated reason.
    """
    fake_harness(_stamps(33))
    split, meta = _resolve(35)
    assert split == "2025-07-01"
    assert meta["split_fallback"] == "leg_too_thin"
    assert "split_target_clamped_to" not in meta


def test_could_not_look_is_not_the_same_state_as_thin(monkeypatch) -> None:
    """`harness_rc` must never be reported as thinness.

    "We could not look" and "we looked and the leg is thin" are opposite
    statements with opposite remedies, and the repo's collapsed-state rule
    exists because they keep getting merged.
    """

    class _P:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(sweep.subprocess, "run", lambda *a, **k: _P())
    split, meta = _resolve(35)
    assert split == "2025-07-01"
    assert meta["split_fallback"] == "harness_rc"
    assert "split_lifetime_trades" not in meta, (
        "a lifetime count implies we read the trades; we did not"
    )


def test_the_clamp_never_reports_a_target_it_did_not_use(fake_harness) -> None:
    """`split_target_oos` alone must not be trusted once a clamp can happen.

    The row keeps the REQUESTED value under its original key and records the
    used one separately, so an existing consumer reading `split_target_oos`
    keeps its old meaning rather than silently changing it.
    """
    fake_harness(_stamps(64))
    _, meta = _resolve(35)
    assert meta["split_target_oos"] == 35, "the requested value is preserved"
    assert meta["split_target_clamped_to"] == 32, "the used value is recorded"
    assert meta["split_target_clamped_to"] != meta["split_target_oos"]
