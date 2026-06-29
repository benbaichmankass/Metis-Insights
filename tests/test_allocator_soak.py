"""M18 P0c — allocator shadow-soak (observe-only).

Verifies the pure regret builder (would-pick vs executed), the ≥2-candidate
gate, fail-permissive behaviour, and the read envelope/summary.
"""
from __future__ import annotations

from src.core.signal_contract import SignalPackage
from src.runtime.allocator_soak import (
    build_allocator_soak_record,
    read_soak_records,
    record_allocator_soak,
)


def _cand(strategy: str, side: str, conf: float, *, priority: int = 0) -> SignalPackage:
    return SignalPackage(
        strategy_id=strategy,
        symbol="BTCUSDT",
        account_id="",
        side=side,
        entry_price=50_000.0,
        stop_loss=49_500.0 if side == "long" else 50_500.0,
        take_profit=51_500.0 if side == "long" else 48_500.0,
        timestamp_utc="2026-06-29T00:00:00+00:00",
        source_context={"confidence": conf, "priority": priority},
    )


class TestBuildRecord:
    def test_needs_two_actionable_candidates(self):
        assert build_allocator_soak_record([], symbol="BTCUSDT", executed_strategy_id=None) is None
        one = [_cand("vwap", "long", 0.6)]
        assert build_allocator_soak_record(one, symbol="BTCUSDT", executed_strategy_id="vwap") is None
        # a "none"-side candidate doesn't count toward the ≥2 actionable gate
        mixed = [_cand("vwap", "long", 0.6), _cand("turtle_soup", "none", 0.9)]
        assert build_allocator_soak_record(mixed, symbol="BTCUSDT", executed_strategy_id="vwap") is None

    def test_regret_zero_when_executed_is_top(self):
        cands = [_cand("vwap", "long", 0.8), _cand("turtle_soup", "short", 0.4)]
        rec = build_allocator_soak_record(cands, symbol="BTCUSDT", executed_strategy_id="vwap")
        assert rec is not None
        assert rec["agree"] is True
        assert rec["regret_score"] == 0.0
        assert rec["allocator_choice"]["strategy_id"] == "vwap"
        assert rec["n_candidates"] == 2

    def test_regret_positive_when_a_better_candidate_was_passed_over(self):
        # aggregator routed the weaker candidate (turtle_soup); the allocator
        # would have picked the stronger one (ict_scalp) → positive regret.
        cands = [_cand("turtle_soup", "short", 0.4), _cand("ict_scalp", "long", 0.9)]
        rec = build_allocator_soak_record(cands, symbol="BTCUSDT", executed_strategy_id="turtle_soup")
        assert rec["agree"] is False
        assert rec["allocator_choice"]["strategy_id"] == "ict_scalp"
        assert abs(rec["regret_score"] - 0.5) < 1e-9

    def test_executed_none_yields_full_top_regret(self):
        cands = [_cand("vwap", "long", 0.7), _cand("turtle_soup", "short", 0.5)]
        rec = build_allocator_soak_record(cands, symbol="BTCUSDT", executed_strategy_id=None)
        assert rec["agree"] is False
        assert rec["executed_score"] is None
        assert abs(rec["regret_score"] - 0.7) < 1e-9

    def test_never_raises_on_garbage(self):
        # objects missing attributes must be skipped by the side filter, not raise
        assert build_allocator_soak_record([object(), object()], symbol="X", executed_strategy_id=None) is None

    def test_custom_score_fn_is_used(self):
        cands = [_cand("a", "long", 0.1), _cand("b", "long", 0.2)]
        # invert: score by 1 - confidence → "a" becomes the top
        rec = build_allocator_soak_record(
            cands, symbol="BTCUSDT", executed_strategy_id="b",
            score_fn=lambda c: 1.0 - float((c.source_context or {}).get("confidence", 0.0)),
            score_kind="inverted",
        )
        assert rec["allocator_choice"]["strategy_id"] == "a"
        assert rec["score_kind"] == "inverted"


class TestWriteAndRead:
    def test_record_and_read_roundtrip(self, tmp_path, monkeypatch):
        import src.utils.paths as paths
        monkeypatch.setattr(paths, "runtime_logs_dir", lambda: tmp_path)

        # one agreeing tick + one disagreeing tick
        record_allocator_soak(
            [_cand("vwap", "long", 0.8), _cand("turtle_soup", "short", 0.4)],
            symbol="BTCUSDT", executed_strategy_id="vwap",
        )
        record_allocator_soak(
            [_cand("turtle_soup", "short", 0.4), _cand("ict_scalp", "long", 0.9)],
            symbol="BTCUSDT", executed_strategy_id="turtle_soup",
        )

        out = read_soak_records()
        assert out["present"] is True
        assert out["count"] == 2
        assert out["summary"]["total_scanned"] == 2
        assert out["summary"]["disagree"] == 1
        assert out["summary"]["disagree_pct"] == 50.0
        assert out["summary"]["mean_regret"] == round((0.0 + 0.5) / 2, 6)

        # regret-only filter returns just the disagreeing row
        only = read_soak_records(only_regret=True)
        assert only["count"] == 1
        assert only["records"][0]["executed_strategy_id"] == "turtle_soup"

    def test_read_absent_log(self, tmp_path, monkeypatch):
        import src.utils.paths as paths
        monkeypatch.setattr(paths, "runtime_logs_dir", lambda: tmp_path)
        out = read_soak_records()
        assert out["present"] is False
        assert out["count"] == 0
