"""MI-150 — the ict_scalp exit-head consumer, exercised through `monitor()`.

The parity suite asserts the DECISION matches donchian's. This one asserts the
CALL SITE: that the consumer is reached, that it is inert in its shipped
state, that it writes an observation on every outcome, and that it inherits
the in-distribution guard rather than restating it.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.units.strategies import ict_scalp


def _candles(n=60, price=100.0):
    ts = pd.date_range("2026-09-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "open": [price] * n, "high": [price + 1] * n,
        "low": [price - 1] * n, "close": [price] * n,
        "volume": [10.0] * n,
    })


def _pkg(**kw):
    base = {
        "order_package_id": "pkg-test", "symbol": "SOLUSDT",
        "direction": "long", "entry": 100.0, "sl": 95.0, "tp": 107.5,
        "strategy_name": "ict_scalp_sol_5m",
        "meta": {"timeframe": "5m", "risk_per_unit": 5.0,
                 "entry_time": "2026-09-01T00:00:00+00:00",
                 "strategy_label": "ict_scalp_sol_5m"},
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _soak_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path)
    return tmp_path


def _soak_rows(tmp_path):
    p = tmp_path / "ict_scalp_exit_head_soak.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def test_shipped_default_closes_nothing_and_records_why(_soak_to_tmp,
                                                        monkeypatch):
    """Default mode + no YAML declaration ⇒ the monitor's behaviour is
    unchanged, and the reason is WRITTEN DOWN rather than merely absent."""
    monkeypatch.delenv("ICT_SCALP_EXIT_HEAD_MODE", raising=False)
    # A head that WOULD fire, so a pass here is the gate holding, not the
    # absence of a signal.
    monkeypatch.setattr(
        "src.runtime.exit_head_shadow.maybe_score_exit_head",
        lambda *a, **k: {"stage": "advisory", "score": 0.01, "tau": 0.10,
                         "below_r": 0.5, "policy": "below_half_r",
                         "model_id": "m1", "family_state": "matched",
                         "feature_row": {"open_r": -0.9}})
    out = ict_scalp.monitor({"timeframe": "5m"}, _candles(), _pkg())
    assert not (isinstance(out, dict) and out.get("reason") == "exit_head")

    rows = _soak_rows(_soak_to_tmp)
    assert rows, "annotate must produce an observation, not silence"
    r = rows[-1]
    assert r["mode"] == "annotate"
    assert r["acted"] is False
    assert r["leg_declares_action"] is None
    # It COMPUTED the decision and discarded it — the annotate contract.
    assert r["would_close"] is False, (
        "no YAML declaration ⇒ the verdict itself is None")


def test_declaring_the_yaml_alone_still_does_not_arm(_soak_to_tmp, monkeypatch):
    """Both halves are required. The declaration without the mode is inert,
    and the soak shows the decision was made and held back."""
    monkeypatch.delenv("ICT_SCALP_EXIT_HEAD_MODE", raising=False)
    monkeypatch.setattr(
        "src.runtime.exit_head_shadow.maybe_score_exit_head",
        lambda *a, **k: {"stage": "advisory", "score": 0.01, "tau": 0.10,
                         "below_r": 0.5, "policy": "below_half_r",
                         "model_id": "m1",
                         "feature_row": {"open_r": -0.9}})
    out = ict_scalp.monitor({"timeframe": "5m", "exit_head_action": "close"},
                            _candles(), _pkg())
    assert not (isinstance(out, dict) and out.get("reason") == "exit_head")
    r = _soak_rows(_soak_to_tmp)[-1]
    assert r["would_close"] is True, "the decision must still be COMPUTED"
    assert r["acted"] is False, "and held back by the mode"
    assert r["apply_scope"] == "not_apply"


def test_apply_plus_declaration_closes(_soak_to_tmp, monkeypatch):
    """The lever works when — and only when — both halves are set."""
    monkeypatch.setenv("ICT_SCALP_EXIT_HEAD_MODE", "apply")
    monkeypatch.setattr(
        "src.runtime.exit_head_shadow.maybe_score_exit_head",
        lambda *a, **k: {"stage": "advisory", "score": 0.01, "tau": 0.10,
                         "below_r": 0.5, "policy": "below_half_r",
                         "model_id": "m1",
                         "feature_row": {"open_r": -0.9}})
    out = ict_scalp.monitor({"timeframe": "5m", "exit_head_action": "close"},
                            _candles(), _pkg())
    assert out == {"action": "close", "reason": "exit_head",
                   "exit_price": 100.0}
    r = _soak_rows(_soak_to_tmp)[-1]
    assert r["acted"] is True and r["apply_scope"] == "applied"


def test_shadow_stage_never_closes_even_at_apply(_soak_to_tmp, monkeypatch):
    """The operator promotion gate outranks the mode."""
    monkeypatch.setenv("ICT_SCALP_EXIT_HEAD_MODE", "apply")
    monkeypatch.setattr(
        "src.runtime.exit_head_shadow.maybe_score_exit_head",
        lambda *a, **k: {"stage": "shadow", "score": 0.01, "tau": 0.10,
                         "below_r": 0.5, "policy": "below_half_r",
                         "model_id": "m1",
                         "feature_row": {"open_r": -0.9}})
    out = ict_scalp.monitor({"timeframe": "5m", "exit_head_action": "close"},
                            _candles(), _pkg())
    assert not (isinstance(out, dict) and out.get("reason") == "exit_head")


def test_unscorable_leg_records_not_scored_not_a_quiet_negative(_soak_to_tmp,
                                                                monkeypatch):
    """The state every ict_scalp leg is in TODAY.

    The live mirror publishes 1h artifacts only, so the scorer's tf guard
    refuses every 5m/15m leg and returns None. That must record as *we did not
    look*, never as *the head declined to fire* — the two are different facts
    and only one of them is evidence about the lever.
    """
    monkeypatch.setenv("ICT_SCALP_EXIT_HEAD_MODE", "apply")
    monkeypatch.setattr("src.runtime.exit_head_shadow.maybe_score_exit_head",
                        lambda *a, **k: None)
    out = ict_scalp.monitor({"timeframe": "5m", "exit_head_action": "close"},
                            _candles(), _pkg())
    assert not (isinstance(out, dict) and out.get("reason") == "exit_head")
    r = _soak_rows(_soak_to_tmp)[-1]
    assert r["decision_state"] == "not_scored"
    assert r["model_id"] is None and r["score"] is None


def test_consumer_declares_its_family_to_the_shared_scorer(_soak_to_tmp,
                                                           monkeypatch):
    """The in-distribution guard is INHERITED, so the unit must actually ask
    for it — passing `family` is what makes the artifact-side check run."""
    seen = {}

    def _spy(meta, open_pkg, candles_df, direction, family=None):
        seen["family"] = family
        return None

    monkeypatch.setattr("src.runtime.exit_head_shadow.maybe_score_exit_head",
                        _spy)
    ict_scalp.monitor({"timeframe": "5m"}, _candles(), _pkg())
    assert seen["family"] == "ict_scalp", (
        "a consumer that does not declare its family silently opts out of the "
        "#6201-class guard")


def test_scoring_failure_never_reaches_the_monitor(_soak_to_tmp, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("mirror unreadable")

    monkeypatch.setenv("ICT_SCALP_EXIT_HEAD_MODE", "apply")
    monkeypatch.setattr("src.runtime.exit_head_shadow.maybe_score_exit_head",
                        _boom)
    out = ict_scalp.monitor({"timeframe": "5m", "exit_head_action": "close"},
                            _candles(), _pkg())
    assert not (isinstance(out, dict) and out.get("reason") == "exit_head")


def test_off_mode_writes_nothing_and_changes_nothing(_soak_to_tmp, monkeypatch):
    monkeypatch.setenv("ICT_SCALP_EXIT_HEAD_MODE", "off")
    monkeypatch.setattr(
        "src.runtime.exit_head_shadow.maybe_score_exit_head",
        lambda *a, **k: {"stage": "advisory", "score": 0.01, "tau": 0.10,
                         "below_r": 0.5, "policy": "below_half_r",
                         "model_id": "m1",
                         "feature_row": {"open_r": -0.9}})
    out = ict_scalp.monitor({"timeframe": "5m", "exit_head_action": "close"},
                            _candles(), _pkg())
    assert not (isinstance(out, dict) and out.get("reason") == "exit_head")
    assert _soak_rows(_soak_to_tmp) == []


def test_exit_head_does_not_pre_empt_a_crossed_stop(_soak_to_tmp, monkeypatch):
    """Stop-first ordering: a bar that already crossed SL closes as `sl_cross`,
    not as `exit_head`. The head must never take credit for a stop."""
    monkeypatch.setenv("ICT_SCALP_EXIT_HEAD_MODE", "apply")
    monkeypatch.setattr(
        "src.runtime.exit_head_shadow.maybe_score_exit_head",
        lambda *a, **k: {"stage": "advisory", "score": 0.01, "tau": 0.10,
                         "below_r": 0.5, "policy": "below_half_r",
                         "model_id": "m1",
                         "feature_row": {"open_r": -0.9}})
    out = ict_scalp.monitor({"timeframe": "5m", "exit_head_action": "close"},
                            _candles(price=90.0), _pkg())
    assert out["reason"] == "sl_cross"
