"""TUNE BEFORE DEMOTE — the operator directive made mechanical.

Operator, 2026-08-23: *"we need to do a deeper dive and at least attempt
finetuning before demoting - this should be documented as standard practice,
I shouldn't have to explain each time."*

The reason it had to be said each time was structural, not a lapse of
judgement. `docs/strategy-review-gate.md`'s matrix reaches `demote_shadow` /
`kill` DIRECTLY from win-rate + expectancy, while `tune` occupies only a narrow
middle band — so a leg that is simply losing skipped the tuning attempt every
single time and the operator had to intervene by hand on each packet.

These tests pin the override that removes the need to intervene.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "strategy_review_packet",
    Path(__file__).resolve().parents[1] / "scripts/ml/strategy_review_packet.py",
)
srp = importlib.util.module_from_spec(_SPEC)
sys.modules["strategy_review_packet"] = srp   # register BEFORE exec: @dataclass
_SPEC.loader.exec_module(srp)                 # resolves cls.__module__ from it


def _losing_headline(n=150, win=0.20, exp=-5.0):
    h = srp.Headline()
    h.n_closed, h.win_rate, h.expectancy = n, win, exp
    return h


def _decide(tuning_attempted, *, execution="live", cells=(), n=150):
    return srp.decide(
        _losing_headline(n=n), list(cells), srp.ExecutionDiagnostics(),
        execution, 30, window_end=None, tuning_attempted=tuning_attempted)


class TestTheOverride:
    def test_a_losing_leg_with_NO_tuning_attempt_is_softened_to_tune(self):
        d = _decide(False)
        assert d.action == "tune"
        assert any("NO tuning attempt is on record" in r for r in d.reasons)

    def test_a_losing_leg_WITH_a_tuning_attempt_still_demotes(self):
        """The override defers a disposition; it must not forbid one.

        A leg that was tuned and still fails is exactly the case the gate
        exists for — softening that too would strand losing legs live forever.
        """
        d = _decide(True)
        assert d.action in ("demote_shadow", "kill")
        assert any("tune-before-demote satisfied" in r for r in d.reasons)

    def test_an_UNREADABLE_tune_directory_does_NOT_soften(self):
        """`None` is not `False`.

        Softening a genuine demotion on the strength of a FAILED READ would
        strand a losing leg live on missing evidence — the opposite mistake
        from demoting without an attempt, and exactly the collapse this repo's
        whole three-state family exists to prevent.
        """
        d = _decide(None)
        assert d.action in ("demote_shadow", "kill")
        assert any("NOT verifiable" in r for r in d.reasons)

    def test_the_softened_reason_names_the_verdict_it_replaced(self):
        """A reader must be able to see WHAT was deferred, not just that
        something was — otherwise the packet hides how bad the leg is."""
        d = _decide(False)
        joined = " ".join(d.reasons)
        assert "demote_shadow->tune" in joined or "kill->tune" in joined


class TestItLeavesEverythingElseAlone:
    @pytest.mark.parametrize("attempted", [True, False, None])
    def test_a_HOLD_verdict_is_untouched_whatever_the_evidence(self, attempted):
        h = srp.Headline()
        h.n_closed, h.win_rate, h.expectancy = 0, None, None
        d = srp.decide(h, [], srp.ExecutionDiagnostics(), "live", 30,
                       window_end=None, tuning_attempted=attempted)
        assert d.action == "hold"
        assert not any("tune-before-demote" in r for r in d.reasons)

    @pytest.mark.parametrize("attempted", [True, False, None])
    def test_the_override_adds_no_reason_to_a_non_disposition(self, attempted):
        """It must not narrate on packets it does not act on — a note on every
        verdict is how a signal becomes background noise."""
        h = srp.Headline()
        h.n_closed, h.win_rate, h.expectancy = 0, None, None
        d = srp.decide(h, [], srp.ExecutionDiagnostics(), "live", 30,
                       window_end=None, tuning_attempted=attempted)
        assert all("tuning attempt" not in r for r in d.reasons)

    def test_default_argument_preserves_the_pre_override_behaviour(self):
        """Callers that predate the override must not silently change verdict.

        Omitting it means `None` — we could not look — which by design leaves
        the matrix verdict standing.
        """
        d = srp.decide(_losing_headline(), [], srp.ExecutionDiagnostics(),
                       "live", 30, window_end=None)
        assert d.action in ("demote_shadow", "kill")


class TestTheEvidenceReader:
    def test_three_states_are_distinguishable(self, tmp_path):
        assert srp.tuning_attempt_on_record("x", tmp_path / "absent") is None
        root = tmp_path / "tunes"
        root.mkdir()
        assert srp.tuning_attempt_on_record("x", root) is False
        day = root / "2026-08-23"
        day.mkdir()
        (day / "x__adx_min.json").write_text("{}")
        assert srp.tuning_attempt_on_record("x", root) is True

    def test_another_leg_s_sweep_does_not_count_as_this_leg_s(self, tmp_path):
        root = tmp_path / "tunes"
        (root / "2026-08-23").mkdir(parents=True)
        (root / "2026-08-23" / "other__adx_min.json").write_text("{}")
        assert srp.tuning_attempt_on_record("mine", root) is False

    def test_a_prefix_is_not_a_match(self, tmp_path):
        """`eth_pullback_2h` must not be satisfied by `eth_pullback_2h_v2`'s
        sweep — the separator is `__`, and a prefix match would let one leg's
        work discharge another's."""
        root = tmp_path / "tunes"
        (root / "2026-08-23").mkdir(parents=True)
        (root / "2026-08-23" / "eth_pullback_2h_v2__adx.json").write_text("{}")
        assert srp.tuning_attempt_on_record("eth_pullback_2h", root) is False

    def test_a_non_directory_child_is_skipped_not_fatal(self, tmp_path):
        root = tmp_path / "tunes"
        root.mkdir()
        (root / "README.md").write_text("not a date dir")
        assert srp.tuning_attempt_on_record("x", root) is False
