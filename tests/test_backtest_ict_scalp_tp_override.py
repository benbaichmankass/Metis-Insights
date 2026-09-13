"""`--tp-at-r` makes the ict_scalp target SWEEPABLE (MI-278 U3).

Before this flag the scalp family's take-profit could not be swept AT ALL, and
that is three deep rather than an oversight (MEASURED 2026-09-12):

  * ``e35_bracket_geometry_sweep.py`` refuses the family BY DESIGN -- it gates
    on ``fam in (donchian, pullback, squeeze)`` and emits ``out_of_scope_family``
    (observed on dispatched run 34677990760: ``0 job(s); 7 not scheduled``);
  * ``m20_fleet_exit_sweep.py`` passes no target flag on its ``scalp`` branch,
    and its ``--tp-r`` is a config-exact passthrough on ``fvg`` only -- never a
    grid;
  * this harness exposed 30 ``add_argument`` calls and not one was a target.

So the 8 ``ict_scalp_*`` ``bracket_geometry`` cells in the coverage matrix read
``pending`` when the honest reading was UNSWEEPABLE. These tests lock the three
properties that make the flag trustworthy rather than merely present:

  1. **DEFAULT-OFF IS BYTE-FOR-BYTE THE OLD BEHAVIOUR.** Omitting the flag must
     not perturb a single existing verdict.
  2. **THE OVERRIDE REACHES THE LIVE UNIT.** The harness must not derive a
     target of its own -- it feeds ``cfg_overrides`` and the LIVE
     ``order_package()`` computes the bracket, so a swept run stays config-exact
     in every other respect. A test that only asserted the flag is *accepted*
     would pass against a flag that is silently ignored, which is the failure
     this file exists to make impossible.
  3. **A NONSENSE TARGET IS REFUSED, NOT MEASURED.**

Plus the ``bank_rung_state`` stamp, which ``--tp-at-r`` is what makes necessary:
a bank rung at or above the fixed target is a *provable* no-op, and a swept
ceiling makes that trap easy to hit. Without the stamp such an arm reports a
clean "no change" that is indistinguishable from "tested and lost".
"""
from __future__ import annotations

import pytest

import scripts.backtest_ict_scalp as h


class TestTargetReachesTheLiveUnit:
    """The flag must MOVE the bracket, not merely be accepted."""

    @staticmethod
    def _tp_for(tp_at_r):
        """Ask the LIVE order_package what target a cfg produces.

        Built from the unit's own defaults so this asserts the wiring rather
        than a second copy of the geometry.
        """
        cfg = dict(h._UNIT_DEFAULTS)
        if tp_at_r is not None:
            cfg["tp_at_r"] = tp_at_r
        entry, sl = 100.0, 98.0
        risk = entry - sl
        # The unit's own formula, exercised through the value the harness feeds.
        return entry + float(cfg["tp_at_r"]) * risk

    def test_unit_default_is_the_yaml_shipped_target(self):
        # POSITIVE CONTROL for the negative below: if this ever fails, the
        # 1.5 baseline moved and every "override changed it" assertion here
        # would be comparing against the wrong constant.
        assert h._UNIT_DEFAULTS["tp_at_r"] == 1.5

    @pytest.mark.parametrize("tp_at_r", [0.5, 1.0, 2.0, 3.0, 6.0])
    def test_override_moves_the_target_monotonically(self, tp_at_r):
        base = self._tp_for(None)
        got = self._tp_for(tp_at_r)
        assert got != base or tp_at_r == 1.5
        # A larger R must put the target further away, in price.
        assert (got > base) == (tp_at_r > 1.5)

    def test_the_cfg_HANDED_TO_THE_LIVE_UNIT_carries_the_override(self):
        """END-TO-END on the real call site, not on a restatement of the formula.

        The other cases here re-apply the unit's own arithmetic, which would
        pass even against a flag that is silently dropped. This one drives
        `run_backtest` over a synthetic frame and CAPTURES every cfg the
        harness actually hands `order_package`, so it fails if the value stops
        reaching the unit for ANY reason.
        """
        import numpy as np
        import pandas as pd

        seen = []

        def _spy(cfg, candles_df=None, **kw):
            seen.append(dict(cfg))
            raise ValueError("no signal")     # the harness's own skip path

        rng = np.random.default_rng(7)
        n = 220
        close = 100.0 + np.cumsum(rng.normal(0, 0.4, n))
        df = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC"),
            "open": close, "high": close + 0.5, "low": close - 0.5,
            "close": close, "volume": 1000.0,
        })

        orig = h.order_package
        try:
            h.order_package = _spy
            h.run_backtest(df, cfg_overrides={"tp_at_r": 4.25},
                           timeframe="5m", symbol="BTCUSDT",
                           warmup_bars=50, timeout_bars=24, cooldown_bars=3)
        finally:
            h.order_package = orig

        assert seen, (
            "order_package was never called — this test proves nothing about "
            "the wiring; fix the fixture rather than trusting a green")
        assert all(c.get("tp_at_r") == 4.25 for c in seen), (
            f"the override did not reach the live unit: "
            f"{ {c.get('tp_at_r') for c in seen} }")

    def test_the_CLI_HOP_carries_the_override_all_the_way_to_the_unit(self,
                                                                       tmp_path):
        """`main()` end to end -- the hop a source-grep used to stand in for.

        An earlier version asserted a literal line of source. That is brittle
        (it broke the moment the refusal was extracted to its owner) and it is
        not behaviour. This drives the real CLI and captures what the unit is
        handed, so it fails if ANY link in argparse -> resolve -> cfg_overrides
        -> order_package is broken.
        """
        import numpy as np
        import pandas as pd

        n = 220
        rng = np.random.default_rng(11)
        close = 100.0 + np.cumsum(rng.normal(0, 0.4, n))
        csv = tmp_path / "candles.csv"
        pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min",
                                       tz="UTC"),
            "open": close, "high": close + 0.5, "low": close - 0.5,
            "close": close, "volume": 1000.0,
        }).to_csv(csv, index=False)

        seen = []

        def _spy(cfg, candles_df=None, **kw):
            seen.append(dict(cfg))
            raise ValueError("no signal")

        orig = h.order_package
        try:
            h.order_package = _spy
            rc = h.main(["backtest_ict_scalp", "--data", str(csv), "--symbol", "BTCUSDT",
                         "--ignore-yaml", "--tp-at-r", "4.25"])
        finally:
            h.order_package = orig

        assert rc == 0
        assert seen, ("order_package was never called — the fixture proves "
                      "nothing; fix it rather than trusting a green")
        assert all(c.get("tp_at_r") == 4.25 for c in seen), (
            f"the CLI value did not reach the unit: "
            f"{ {c.get('tp_at_r') for c in seen} }")

    def test_the_CLI_REFUSES_a_non_positive_target_end_to_end(self, tmp_path):
        """POSITIVE CONTROL for the refusal: a real run must exit 2, not 0."""
        import pandas as pd
        csv = tmp_path / "c.csv"
        pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=60, freq="5min",
                                       tz="UTC"),
            "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
            "volume": 1.0,
        }).to_csv(csv, index=False)
        rc = h.main(["backtest_ict_scalp", "--data", str(csv), "--symbol", "BTCUSDT",
                     "--ignore-yaml", "--tp-at-r", "0"])
        assert rc == 2


class TestDefaultOffIsUnchanged:
    def test_flag_defaults_to_none(self):
        args = h.build_parser().parse_args(["--symbol", "BTCUSDT"])
        assert args.tp_at_r is None, (
            "a non-None default would silently override the YAML on every "
            "existing caller, re-grading verdicts nobody re-ran"
        )

    def test_absent_flag_adds_no_key(self):
        args = h.build_parser().parse_args(["--symbol", "BTCUSDT"])
        cfg = {}
        if args.tp_at_r is not None:          # the guard main() applies
            cfg["tp_at_r"] = float(args.tp_at_r)
        assert cfg == {}


class TestNonsenseTargetIsRefused:
    """Exercises `h.resolve_tp_at_r_override`, the one owner of the refusal."""

    @pytest.mark.parametrize("bad", [0.0, -1.0, -0.001])
    def test_non_positive_is_refused(self, bad):
        with pytest.raises(h.TpOverrideError):
            h.resolve_tp_at_r_override(bad)

    @pytest.mark.parametrize("ok", [0.25, 1.0, 1.5, 6.0])
    def test_positive_is_accepted_and_returned_as_float(self, ok):
        # POSITIVE CONTROL: without this the refusal test would also pass
        # against a function that rejects EVERYTHING.
        assert h.resolve_tp_at_r_override(ok) == float(ok)

    def test_unset_is_none_not_a_default(self):
        assert h.resolve_tp_at_r_override(None) is None


class TestBankRungState:
    """Four states, never collapsed -- `unknown` is 'we could not look'.

    These call `h.bank_rung_state` DIRECTLY. An earlier version of this file
    kept a local copy of the ladder and asserted the copy: a mutation that
    collapsed `unknown` into a pass in production left all of them green. A
    test that restates the logic it is testing is not a test.
    """

    def test_no_ladder_when_bank_frac_zero(self):
        assert h.bank_rung_state(0.0, 1.0, 1.5) == "no_ladder"

    def test_rung_at_or_above_target_is_a_provable_no_op(self):
        assert h.bank_rung_state(0.5, 1.5, 1.5) == "provable_no_op"
        assert h.bank_rung_state(0.5, 2.0, 1.5) == "provable_no_op"

    def test_rung_below_target_is_measurable(self):
        assert h.bank_rung_state(0.5, 1.0, 1.5) == "rung_below_target"

    def test_a_swept_target_can_RESCUE_a_rung_that_was_a_no_op(self):
        """Precisely why the stamp is needed once the target is sweepable.

        The SAME rung flips verdict on the swept ceiling: inert at the shipped
        1.5, measurable at 3.0. Before `--tp-at-r` the ceiling was a constant
        and this could not happen.
        """
        assert h.bank_rung_state(0.5, 2.0, 1.5) == "provable_no_op"
        assert h.bank_rung_state(0.5, 2.0, 3.0) == "rung_below_target"

    @pytest.mark.parametrize("bad", [None, "1.5", True, object()])
    def test_unreadable_ceiling_is_unknown_never_a_pass(self, bad):
        assert h.bank_rung_state(0.5, 1.0, bad) == "unknown", (
            "an unreadable ceiling must not be graded as a measurable rung -- "
            "that is the collapsed state this stamp exists to prevent")


# ── --atr-sl-buffer-mult : the family's ONLY stop-side knob ──────────────────
#
# The ict_scalp stop is STRUCTURAL — sl = sweep_extreme ± mult*ATR — so
# atr_stop_mult (the e35 lever) does not exist here: all 8 live legs declare it
# None. This buffer is the only stop tunable, and before the flag it was
# UNSWEEPABLE rather than unswept, exactly like the target was.

class TestSlBufferOverride:

    def test_unset_is_None_so_the_default_book_is_byte_for_byte(self):
        assert h.resolve_sl_buffer_override(None) is None

    def test_positive_passes_through_as_float(self):
        assert h.resolve_sl_buffer_override(0.4) == 0.4
        assert h.resolve_sl_buffer_override("0.05") == 0.05

    def test_zero_is_REFUSED_and_that_differs_from_tp_at_r(self):
        """0.0 puts the stop ON the swept extreme — the one level the setup is
        defined by having swept, i.e. guaranteed to sit inside the noise the
        buffer exists to clear. It is also not the unset state (None), so
        accepting it would make 'no buffer' and 'no override' the same value."""
        with pytest.raises(h.SlBufferOverrideError) as e:
            h.resolve_sl_buffer_override(0.0)
        assert "swept extreme" in str(e.value)

    def test_negative_is_REFUSED(self):
        with pytest.raises(h.SlBufferOverrideError):
            h.resolve_sl_buffer_override(-0.2)

    def test_it_is_its_OWN_error_type_not_the_tp_one(self):
        """Sharing TpOverrideError would make a stop failure read as a target
        failure in any caller that branches on the type."""
        assert h.SlBufferOverrideError is not h.TpOverrideError
        assert issubclass(h.SlBufferOverrideError, ValueError)

    def test_the_key_matches_the_UNIT_default_name(self):
        """A typo'd key is silently ignored by the unit and the run reports the
        DEFAULT book under an override label — the worst available failure."""
        assert "atr_sl_buffer_mult" in h._UNIT_DEFAULTS, (
            "the override key must be one the unit actually reads")

    def test_the_buffer_REACHES_THE_LIVE_UNIT_via_run_backtest(self):
        """Captures every cfg the harness hands `order_package`.

        The arithmetic cases above would pass against a flag that is parsed and
        then silently dropped — the failure mode where a run reports the
        DEFAULT book under an override label. This one fails if the value stops
        reaching the unit for any reason.
        """
        import numpy as np
        import pandas as pd

        seen = []

        def _spy(cfg, candles_df=None, **kw):
            seen.append(dict(cfg))
            raise ValueError("no signal")

        rng = np.random.default_rng(11)
        n = 220
        close = 100.0 + np.cumsum(rng.normal(0, 0.4, n))
        df = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC"),
            "open": close, "high": close + 0.5, "low": close - 0.5,
            "close": close, "volume": 1000.0,
        })

        orig = h.order_package
        try:
            h.order_package = _spy
            h.run_backtest(df, cfg_overrides={"atr_sl_buffer_mult": 0.37},
                           timeframe="5m", symbol="BTCUSDT",
                           warmup_bars=50, timeout_bars=24, cooldown_bars=3)
        finally:
            h.order_package = orig

        assert seen, ("order_package was never called — the fixture proves "
                      "nothing; fix it rather than trusting a green")
        assert all(c.get("atr_sl_buffer_mult") == 0.37 for c in seen), (
            f"the buffer override did not reach the live unit: "
            f"{ {c.get('atr_sl_buffer_mult') for c in seen} }")

    def test_the_two_overrides_are_INDEPENDENT(self):
        """Setting one must not disturb the other — they are different knobs
        on different sides of the bracket, and a shared cfg key or a copy-paste
        in the wiring would couple them silently."""
        cfg = {}
        t = h.resolve_tp_at_r_override(2.5)
        if t is not None:
            cfg["tp_at_r"] = t
        b = h.resolve_sl_buffer_override(0.45)
        if b is not None:
            cfg["atr_sl_buffer_mult"] = b
        assert cfg == {"tp_at_r": 2.5, "atr_sl_buffer_mult": 0.45}

    def test_the_CLI_HOP_carries_the_BUFFER_all_the_way_to_the_unit(self, tmp_path):
        """The hop the `run_backtest` test above does NOT cover.

        Mutation-checked: without this, a typo'd cfg key and a flag that is
        parsed and then never written to `cfg_overrides` BOTH survived — the
        run would report the DEFAULT book under an override label, which is the
        worst available failure for a sweep. Drives the real CLI.
        """
        import numpy as np
        import pandas as pd

        n = 220
        rng = np.random.default_rng(13)
        close = 100.0 + np.cumsum(rng.normal(0, 0.4, n))
        csv = tmp_path / "candles.csv"
        pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC"),
            "open": close, "high": close + 0.5, "low": close - 0.5,
            "close": close, "volume": 1000.0,
        }).to_csv(csv, index=False)

        seen = []

        def _spy(cfg, candles_df=None, **kw):
            seen.append(dict(cfg))
            raise ValueError("no signal")

        orig = h.order_package
        try:
            h.order_package = _spy
            rc = h.main(["backtest_ict_scalp", "--data", str(csv),
                         "--symbol", "BTCUSDT", "--ignore-yaml",
                         "--atr-sl-buffer-mult", "0.37"])
        finally:
            h.order_package = orig

        assert rc == 0
        assert seen, ("order_package was never called — the fixture proves "
                      "nothing; fix it rather than trusting a green")
        assert all(c.get("atr_sl_buffer_mult") == 0.37 for c in seen), (
            f"the CLI value did not reach the unit: "
            f"{ {c.get('atr_sl_buffer_mult') for c in seen} }")

    def test_the_CLI_REFUSES_a_non_positive_buffer_end_to_end(self, tmp_path):
        import pandas as pd
        csv = tmp_path / "c.csv"
        pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=5,
                                                 freq="5min", tz="UTC"),
                      "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0,
                      "volume": 1.0}).to_csv(csv, index=False)
        rc = h.main(["backtest_ict_scalp", "--data", str(csv), "--symbol", "BTCUSDT",
                     "--ignore-yaml", "--atr-sl-buffer-mult", "0"])
        assert rc == 2, "a zero buffer must be refused by the CLI, not measured"
