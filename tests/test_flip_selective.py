"""Unit tests for the FLIP_POLICY=selective branch (Unit A § 7.1).

selective flips ONLY when BOTH gates pass:
  (a) confidence-gap + age override (reuses the existing FLIP_CONFIDENCE_THRESHOLD
      / FLIP_MIN_POSITION_AGE_HOURS machinery), AND
  (b) the fee-aware EV gate (§ 7.1(b), src/runtime/flip_ev.py).
Otherwise it HOLDS (action="noop"), byte-for-byte the default hold behaviour.
The default policy (FLIP_POLICY=hold) is unaffected — verified here too.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.coordinator import OrderPackage  # noqa: E402
from src.runtime.intents import (  # noqa: E402
    FLIP_POLICIES,
    INTENT_MODE_META_KEY,
    INTENT_MODE_META_VALUE,
    compute_execution_delta_for_package,
    resolve_flip_policy,
)


def _scalp_pkg(direction: str = "long", confidence: float = 0.9) -> OrderPackage:
    """A counter-signal scalp package with a healthy reward:risk."""
    return OrderPackage(
        strategy="ict_scalp_5m",
        symbol="BTCUSDT",
        direction=direction,
        entry=50_000.0,
        sl=49_750.0 if direction == "long" else 50_250.0,   # 250 stop
        tp=51_000.0 if direction == "long" else 49_000.0,    # 1000 reward (4R)
        confidence=confidence,
        meta={
            INTENT_MODE_META_KEY: INTENT_MODE_META_VALUE,
            "aggregated_target_qty": 0.0,
        },
    )


class TestSelectiveRegistered:
    def test_selective_in_policy_set(self):
        assert "selective" in FLIP_POLICIES

    def test_default_still_hold(self, monkeypatch):
        monkeypatch.delenv("FLIP_POLICY", raising=False)
        assert resolve_flip_policy() == "hold"

    def test_resolver_accepts_selective(self, monkeypatch):
        monkeypatch.setenv("FLIP_POLICY", "selective")
        assert resolve_flip_policy() == "selective"


class TestSelectiveGates:
    """Opposite net vote: short held (current=-0.01), long scalp desired."""

    def test_holds_when_confidence_gate_disabled(self, monkeypatch):
        # FLIP_CONFIDENCE_THRESHOLD unset (0) → gate (a) never arms → hold.
        monkeypatch.delenv("FLIP_CONFIDENCE_THRESHOLD", raising=False)
        monkeypatch.delenv("FLIP_EV_MARGIN_USD", raising=False)
        pkg = _scalp_pkg(direction="long", confidence=0.9)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.01, risk_sized_qty=0.01,
            flip_policy="selective",
            existing_confidence=0.2, existing_age_hours=10.0,
        )
        assert delta.action == "noop"
        assert "flip_suppressed_selective_confidence" in delta.reason

    def test_holds_when_confidence_gap_too_small(self, monkeypatch):
        monkeypatch.setenv("FLIP_CONFIDENCE_THRESHOLD", "0.15")
        monkeypatch.delenv("FLIP_MIN_POSITION_AGE_HOURS", raising=False)
        monkeypatch.delenv("FLIP_EV_MARGIN_USD", raising=False)
        pkg = _scalp_pkg(direction="long", confidence=0.30)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.01, risk_sized_qty=0.01,
            flip_policy="selective",
            existing_confidence=0.25, existing_age_hours=10.0,  # gap 0.05 < 0.15
        )
        assert delta.action == "noop"
        assert "flip_suppressed_selective_confidence" in delta.reason

    def test_holds_when_position_too_young(self, monkeypatch):
        monkeypatch.setenv("FLIP_CONFIDENCE_THRESHOLD", "0.15")
        monkeypatch.setenv("FLIP_MIN_POSITION_AGE_HOURS", "4.0")
        monkeypatch.delenv("FLIP_EV_MARGIN_USD", raising=False)
        pkg = _scalp_pkg(direction="long", confidence=0.90)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.01, risk_sized_qty=0.01,
            flip_policy="selective",
            existing_confidence=0.20, existing_age_hours=1.0,  # < 4h
        )
        assert delta.action == "noop"
        assert "flip_suppressed_selective_confidence" in delta.reason

    def test_holds_when_ev_gate_fails(self, monkeypatch):
        # Confidence gate passes, but a huge EV margin makes gate (b) fail.
        monkeypatch.setenv("FLIP_CONFIDENCE_THRESHOLD", "0.15")
        monkeypatch.delenv("FLIP_MIN_POSITION_AGE_HOURS", raising=False)
        monkeypatch.setenv("FLIP_EV_MARGIN_USD", "1000000")  # unreachable
        pkg = _scalp_pkg(direction="long", confidence=0.90)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.01, risk_sized_qty=0.01,
            flip_policy="selective",
            existing_confidence=0.20, existing_age_hours=10.0,
        )
        assert delta.action == "noop"
        assert "flip_suppressed_selective_ev" in delta.reason

    def test_flips_when_both_gates_pass(self, monkeypatch):
        monkeypatch.setenv("FLIP_CONFIDENCE_THRESHOLD", "0.15")
        monkeypatch.delenv("FLIP_MIN_POSITION_AGE_HOURS", raising=False)
        monkeypatch.setenv("FLIP_EV_MARGIN_USD", "0")
        # A 4R scalp at 0.9 confidence on a small position: EV clears the
        # four-fill fee easily.
        pkg = _scalp_pkg(direction="long", confidence=0.90)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.01, risk_sized_qty=0.01,
            flip_policy="selective",
            existing_confidence=0.20, existing_age_hours=10.0,
        )
        assert delta.action == "flip"
        assert delta.side == "long"
        assert delta.reason.startswith("selective_flip")

    def test_same_direction_unaffected_by_selective(self, monkeypatch):
        # selective governs ONLY the opposite-vote branch; same-side tops up.
        monkeypatch.setenv("FLIP_CONFIDENCE_THRESHOLD", "0.15")
        pkg = _scalp_pkg(direction="long", confidence=0.9)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=0.005, risk_sized_qty=0.01,
            flip_policy="selective",
        )
        assert delta.action == "increase"
        assert delta.side == "long"


class TestDefaultUnchanged:
    """FLIP_POLICY=hold (the default) is byte-for-byte unchanged."""

    def test_hold_still_noops_opposite_vote(self, monkeypatch):
        monkeypatch.delenv("FLIP_POLICY", raising=False)
        pkg = _scalp_pkg(direction="long", confidence=0.9)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.01, risk_sized_qty=0.01,
            existing_confidence=0.2, existing_age_hours=10.0,
        )
        assert delta.action == "noop"
        assert "flip_suppressed_hold_policy" in delta.reason

    def test_reverse_still_flips(self):
        pkg = _scalp_pkg(direction="long", confidence=0.9)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.01, risk_sized_qty=0.01,
            flip_policy="reverse",
        )
        assert delta.action == "flip"
        assert not delta.reason.startswith("selective_flip")
