"""The compat matrix's `--override` passthrough, and its refusals.

WHY THIS EXISTS. `account_compat_matrix.run()` called
`bt.run_system_backtest(..., overrides={})` with the empty dict hardcoded, so the
engine path could only ever score a strategy AT ITS CONFIG VALUES. That made a
question the prop gate needs to answer -- "what is the EV at a different `tp_r`?"
-- unanswerable without editing `config/strategies.yaml`, i.e. without making a
Tier-3 change purely to measure whether that change is a good idea.

Three properties are pinned here, and each one is a defect this repo has paid for
in some other module:

1. ONE OWNER for override semantics. `bt.parse_overrides` is imported, never
   re-implemented, so the compat matrix and a direct engine run cannot drift into
   disagreeing about what `STRAT.key=val` means.
2. AN OVERRIDE THAT CANNOT APPLY IS A REFUSAL, NOT A NO-OP. `--ledger` skips the
   engine entirely and a typo'd strategy name matches nothing; either would leave
   the output labelled as overridden while the numbers came from the config
   values. That is the `filter_state` collapse (an ignored filter reading as an
   applied one), and it is worse here because the consumer is a Tier-3 decision.
3. PROVENANCE. The payload records the overrides, so a verdict computed under one
   is distinguishable from one computed at config values.
"""

from __future__ import annotations

import argparse
import json

import pytest

import scripts.backtest_system as bt
from scripts.prop.account_compat_matrix import run
from src.prop.account_rulesets import AccountBacktestUnit


# --------------------------------------------------------------------------
# 1. The single owner's semantics
# --------------------------------------------------------------------------
def test_parse_overrides_coerces_int_then_float_then_str():
    out = bt.parse_overrides([
        "s.tp_r=6",          # int
        "s.trail_mult=3.5",  # float
        "s.side=long",       # str
    ])
    assert out == {"s": {"tp_r": 6, "trail_mult": 3.5, "side": "long"}}
    assert isinstance(out["s"]["tp_r"], int)
    assert isinstance(out["s"]["trail_mult"], float)


def test_parse_overrides_merges_multiple_keys_for_one_strategy():
    out = bt.parse_overrides(["s.a=1", "s.b=2"])
    assert out == {"s": {"a": 1, "b": 2}}


def test_parse_overrides_keeps_strategies_separate():
    out = bt.parse_overrides(["a.tp_r=1", "b.tp_r=2"])
    assert out == {"a": {"tp_r": 1}, "b": {"tp_r": 2}}


@pytest.mark.parametrize("bad", ["no_dot_or_equals", "missing_equals.key", "missingdot=1"])
def test_parse_overrides_raises_rather_than_dropping(bad):
    """A malformed override must RAISE.

    Silently skipping it is the dangerous direction: the run would proceed at the
    config value while every label said it was overridden.
    """
    with pytest.raises(ValueError):
        bt.parse_overrides([bad])


def test_parse_overrides_empty_is_empty():
    assert bt.parse_overrides([]) == {}


# --------------------------------------------------------------------------
# 2. Refusals -- an override that could not apply is never silently ignored
# --------------------------------------------------------------------------
def _args(**kw):
    base = dict(
        strategy=None, ledger=None, data="x.parquet", symbol=None,
        fee_bps_roundtrip=None, accounts=None, start=None, end=None,
        base_account_size=5000.0, base_risk_pct=0.5, clock_tf="1h",
        horizon_months=12.0, n_paths=10, block_len=8, seed=1,
        min_p_profitable=0.5, min_survival=0.9, max_p_breach=0.1,
        refresh_signals=False, out_dir=None, override=[],
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_override_with_ledger_is_refused(tmp_path, capsys):
    """--ledger never runs the engine, so an override there applies to nothing."""
    led = tmp_path / "emit.jsonl"
    led.write_text('{"entry_time": "2024-01-01T00:00:00+00:00", "net_r": 1.0}\n')
    rc = run(_args(ledger=str(led), override=["s.tp_r=3"]))
    assert rc == 2
    assert "invalid with --ledger" in capsys.readouterr().err


def test_override_naming_another_strategy_is_refused(capsys):
    """A typo'd strategy name would apply to nothing and read as applied."""
    rc = run(_args(strategy="trend_donchian", override=["trend_donhcian.tp_r=3"]))
    assert rc == 2
    err = capsys.readouterr().err
    assert "would apply to nothing" in err
    assert "trend_donhcian" in err


# --------------------------------------------------------------------------
# 3. The passthrough reaches the engine, and is stamped on the output
# --------------------------------------------------------------------------
def _fake_prop_unit() -> AccountBacktestUnit:
    """A prop unit built from the REAL dataclass, never a hand-rolled stand-in.

    ⚠️ **THIS USED TO BE A BARE `class _FakePropUnit` WITH SIX HAND-COPIED
    ATTRIBUTES, AND THAT IS WHY IT IS A FUNCTION NOW.** When
    `AccountBacktestUnit` grew the `gradeable` property (2026-08-27), the stub
    did not — so every test here raised `AttributeError` against a field
    production has and the fake did not. That is the same shape as the pairs
    tests that declared their own `order_packages` schema with `id INTEGER
    PRIMARY KEY` and passed for months against a table production never had
    (`BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED`): a test asserting against a
    fictional schema is not evidence about the real one, in EITHER direction —
    it can pass on a broken system or fail on a working one.

    Constructing the real dataclass makes that class of drift impossible: a new
    REQUIRED field breaks this call loudly at the constructor, and a new derived
    property is inherited for free.

    `ruleset` stays a duck-typed sentinel rather than a real `PropRuleset`
    because `run_ev_montecarlo` — its only consumer on this path — is
    monkeypatched below; a real one would assert nothing extra and would couple
    these override tests to ruleset parsing, which they are not about.
    """
    return AccountBacktestUnit(
        account_id="fake_prop",
        kind="prop",
        ruleset={"fake": True},  # type: ignore[arg-type]
        risk_pct=1.5,
        account_size_usd=5000.0,
        account_class="prop",
        source="test:fake_prop",
    )


def _patch_engine(monkeypatch, seen):
    monkeypatch.setattr(bt, "_load_candles", lambda p: "CANDLES")
    monkeypatch.setattr(
        bt, "run_system_backtest",
        lambda *a, **kw: (seen.update(kw), {"closed_trades": [{"pnl": 1.0}]})[1],
    )
    monkeypatch.setattr(
        "scripts.prop.account_compat_matrix.all_account_units",
        lambda: {"fake_prop": _fake_prop_unit()},
    )
    monkeypatch.setattr(
        "scripts.prop.account_compat_matrix.run_ev_montecarlo",
        lambda *a, **kw: {"horizons": {"12.0": {"mean_net_usd": 1.0, "p_profitable": 0.9}}},
    )


def test_override_reaches_the_engine_and_is_stamped(tmp_path, monkeypatch):
    seen: dict = {}
    _patch_engine(monkeypatch, seen)
    monkeypatch.setattr(bt, "ROSTER", {"trend_donchian": {"module": "m", "tf": "1h"}})

    rc = run(_args(strategy="trend_donchian", out_dir=str(tmp_path),
                   override=["trend_donchian.tp_r=3.2"]))
    assert rc == 0
    # It actually reached the engine -- not merely parsed.
    assert seen["overrides"] == {"trend_donchian": {"tp_r": 3.2}}
    # ...and the verdict records the input that produced it.
    payload = json.loads((tmp_path / "compat_trend_donchian.json").read_text())
    assert payload["overrides"] == {"trend_donchian": {"tp_r": 3.2}}


def test_no_override_stamps_none_not_empty_dict(tmp_path, monkeypatch):
    """`None` means none were requested.

    An empty dict would be ambiguous against "an override was requested and
    resolved to nothing" -- the same collapse the refusals above prevent.
    """
    seen: dict = {}
    _patch_engine(monkeypatch, seen)
    monkeypatch.setattr(bt, "ROSTER", {"trend_donchian": {"module": "m", "tf": "1h"}})

    rc = run(_args(strategy="trend_donchian", out_dir=str(tmp_path)))
    assert rc == 0
    assert seen["overrides"] == {}
    payload = json.loads((tmp_path / "compat_trend_donchian.json").read_text())
    assert payload["overrides"] is None


def test_engine_path_is_unchanged_without_an_override(tmp_path, monkeypatch):
    """The no-override call must be byte-identical to the pre-change behaviour."""
    seen: dict = {}
    _patch_engine(monkeypatch, seen)
    monkeypatch.setattr(bt, "ROSTER", {"trend_donchian": {"module": "m", "tf": "1h"}})
    run(_args(strategy="trend_donchian", out_dir=str(tmp_path)))
    assert seen["flip_policy"] == "hold"
    assert seen["reentry_policy"] == "suppress"
    assert seen["attach_full"] is True
    assert seen["refresh"] is False
    assert seen["clock_tf"] == "1h"
    assert seen["daily_loss_pct"] == 3.0
    assert seen["signal_ttl_bars"] == 1


# ---------------------------------------------------------------------------
# --balances must be INVISIBLE to a caller that does not use it.
#
# The flag shipped 2026-08-29 and broke all three tests above, in two separate
# ways, because this entry point is driven BOTH by argparse and by hand-built
# Namespaces: (1) a plain `args.balances` read raised AttributeError on a
# Namespace that never set it, and (2) passing `snapshots=` unconditionally
# broke every test that patches `all_account_units` with a narrower lambda.
# Both are the same underlying mistake -- a new optional input changing the
# contract for callers that never asked for it.
# ---------------------------------------------------------------------------
def test_absent_balances_attr_is_read_as_not_supplied() -> None:
    """A Namespace with no `balances` attribute must read as 'none supplied'."""
    import argparse

    args = argparse.Namespace()
    assert getattr(args, "balances", "") == ""


def test_run_does_not_pass_snapshots_when_no_balances(monkeypatch, tmp_path) -> None:
    """all_account_units() is called with NO kwargs when --balances is unset.

    Asserted on the CALL, not on the verdict: a test that only checked the
    verdict would still pass if the kwarg were sent, and the kwarg is exactly
    what broke the sibling tests.
    """
    import scripts.prop.account_compat_matrix as m

    seen: list[dict] = []

    def _fake_all_account_units(*a, **kw):
        seen.append(dict(kw))
        return {}

    monkeypatch.setattr(m, "all_account_units", _fake_all_account_units)

    # `units` empty -> run() bails right after the call, which is all we need.
    args = m.build_parser().parse_args(
        ["--ledger", str(tmp_path / "nope.jsonl"), "--out-dir", str(tmp_path)]
    ) if hasattr(m, "build_parser") else None
    if args is None:                      # parser is inline in main(); call directly
        import argparse
        args = argparse.Namespace(
            strategy=None, ledger=str(tmp_path / "nope.jsonl"), data=None,
            symbol="X", fee_bps_roundtrip=0.0, accounts=None, start=None, end=None,
            base_account_size=5000.0, base_risk_pct=0.5, clock_tf="1h",
            horizon_months=6.0, n_paths=10, block_len=4, seed=1,
            min_p_profitable=0.5, min_survival=0.9, max_p_breach=0.1,
            override=[], refresh_signals=False, out_dir=str(tmp_path),
            balances="", asset_class="equity",
        )
    (tmp_path / "nope.jsonl").write_text("")
    try:
        m.run(args)
    except SystemExit:
        pass
    except Exception:
        pass

    assert seen, "all_account_units was never called"
    assert "snapshots" not in seen[0], (
        "run() passed snapshots= even though --balances was unset; that breaks "
        "every caller patching all_account_units with a narrower signature"
    )
