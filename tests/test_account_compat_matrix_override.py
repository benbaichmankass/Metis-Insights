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
class _FakePropUnit:
    kind = "prop"
    account_id = "fake_prop"
    account_class = "prop"
    risk_pct = 1.5
    account_size_usd = 5000.0
    ruleset = {"fake": True}


def _patch_engine(monkeypatch, seen):
    monkeypatch.setattr(bt, "_load_candles", lambda p: "CANDLES")
    monkeypatch.setattr(
        bt, "run_system_backtest",
        lambda *a, **kw: (seen.update(kw), {"closed_trades": [{"pnl": 1.0}]})[1],
    )
    monkeypatch.setattr(
        "scripts.prop.account_compat_matrix.all_account_units",
        lambda: {"fake_prop": _FakePropUnit()},
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
