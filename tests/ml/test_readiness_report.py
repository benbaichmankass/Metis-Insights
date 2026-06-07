"""Tests for the S-MLOPT-S18 promotion-readiness report (`ml.promotion.readiness_report`)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ml.promotion.attribution import ModelAttribution
from ml.promotion.oos_edge import OOSEdgeResult
from ml.promotion.readiness_report import (
    ReadinessReport,
    build_readiness_report,
    format_markdown,
    format_ping_message,
    write_report,
)
from ml.promotion.stage_guard import Proposal
from ml.registry.model_registry import ModelRegistry


def _good_attr(model_id: str = "m") -> ModelAttribution:
    return ModelAttribution(
        model_id=model_id, stage="shadow", n=300, win_rate=0.5,
        score_mean=0.5, score_min=0.1, score_max=0.9,
        auc=0.62, brier=0.20, baseline_brier=0.25, brier_lift=0.05,
    )


def _good_oos(model_id: str = "m") -> OOSEdgeResult:
    return OOSEdgeResult(
        model_id=model_id, metric="mae", higher_is_better=False,
        candidate_score=0.05, baseline_score=0.07, edge=0.02,
        n_folds=5, n_rows=2000,
        candidate_trainer="ml.trainers.lightgbm_regression.LightGBMRegressionTrainer",
        baseline_trainer="ml.trainers.constant_baseline.ConstantPredictionTrainer",
    )


def _empty_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL, "
        "pnl_percent REAL, status TEXT, timestamp TEXT, notes TEXT, "
        "is_backtest INT, is_demo INT)"
    )
    conn.execute(
        "CREATE TABLE order_packages (id INTEGER PRIMARY KEY, linked_trade_id INT, "
        "updated_at TEXT)"
    )
    conn.commit()
    conn.close()


def test_buckets_proposals_correctly():
    promote = Proposal("p", "shadow", "promote", "advisory", reasons=("ok",))
    demote = Proposal("d", "advisory", "demote", "shadow", reasons=("bad",))
    hold = Proposal("h", "shadow", "hold", None, reasons=("waiting",))
    report = ReadinessReport(
        generated_at_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
        proposals=(promote, demote, hold),
        datasets_root_used="/tmp/ds",
    )
    assert [p.model_id for p in report.promote_ready] == ["p"]
    assert [p.model_id for p in report.demote_proposed] == ["d"]
    assert [p.model_id for p in report.held] == ["h"]
    payload = report.to_dict()
    assert payload["summary"] == {
        "total": 3, "promote": ["p"], "demote": ["d"], "hold_count": 1,
    }
    assert payload["datasets_root_used"] == "/tmp/ds"


def test_format_markdown_lists_each_bucket():
    promote = Proposal("good-model", "shadow", "promote", "advisory",
                       reasons=("all promotion gates pass",))
    demote = Proposal("bad-model", "advisory", "demote", "shadow",
                      reasons=("live discrimination inverted (AUC 0.420 < 0.5)",
                               "live calibration worse than base rate"))
    hold = Proposal("waiting", "shadow", "hold", None,
                    reasons=("gate not met: oos_edge (insufficient_data)",),
                    evidence={"gate_report": {"blocking": ["oos_edge", "shadow_soak"]}})
    report = ReadinessReport(
        generated_at_utc=datetime(2026, 6, 7, tzinfo=timezone.utc),
        proposals=(promote, demote, hold),
        datasets_root_used="/tmp/ds",
    )
    md = format_markdown(report)
    assert "good-model" in md
    assert "shadow → advisory" in md
    assert "bad-model" in md
    assert "live calibration worse than base rate" in md  # demote follow-up reason
    assert "blocking: oos_edge, shadow_soak" in md
    # Tier-3 reminder must be present so a reader can't mistake this for an action log.
    assert "Tier-3" in md


def test_format_markdown_warns_when_datasets_root_missing():
    report = ReadinessReport(
        generated_at_utc=datetime(2026, 6, 7, tzinfo=timezone.utc),
        proposals=(),
        datasets_root_used=None,
    )
    md = format_markdown(report)
    assert "No `datasets_root` supplied" in md
    # No proposals → every section says _none_, never crashes.
    assert md.count("_none_") == 3


def test_format_ping_message_is_none_when_quiet():
    hold_only = ReadinessReport(
        generated_at_utc=datetime.now(timezone.utc),
        proposals=(Proposal("h", "shadow", "hold", None),),
        datasets_root_used=None,
    )
    assert format_ping_message(hold_only) is None


def test_format_ping_message_emits_when_actionable():
    report = ReadinessReport(
        generated_at_utc=datetime.now(timezone.utc),
        proposals=(
            Proposal("p1", "shadow", "promote", "advisory"),
            Proposal("p2", "shadow", "promote", "advisory"),
            Proposal("d1", "advisory", "demote", "shadow"),
        ),
        datasets_root_used=None,
    )
    ping = format_ping_message(report)
    assert ping is not None
    assert "PROMOTE-READY: p1, p2" in ping
    assert "DEMOTE-PROPOSED: d1" in ping


def test_write_report_persists_json_and_markdown(tmp_path: Path):
    report = ReadinessReport(
        generated_at_utc=datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
        proposals=(Proposal("m", "shadow", "hold", None, reasons=("x",)),),
        datasets_root_used="/tmp/ds",
    )
    json_path, md_path = write_report(report, tmp_path / "out")
    assert json_path.is_file() and md_path.is_file()
    payload = json.loads(json_path.read_text())
    assert payload["summary"]["total"] == 1
    text = md_path.read_text()
    assert "# Promotion readiness" in text


def test_build_readiness_report_end_to_end(tmp_path: Path):
    # Two models, both shadow-stage with thin evidence: the report should
    # bucket each as `hold` (no datasets_root → oos_edge insufficient_data;
    # no live trades → live-gate insufficient_data), the same bucketing the
    # stage-guard CLI produces, just wrapped + rendered.
    reg_root = tmp_path / "registry-store"
    registry = ModelRegistry(reg_root)
    registry.register(
        model_id="ready-model",
        manifest={"model_id": "ready-model", "target_deployment_stage": "shadow"},
        model_state_path="x", metrics={"macro_f1": 0.70}, code_revision="a",
    )
    registry.register(
        model_id="holding-model",
        manifest={"model_id": "holding-model", "target_deployment_stage": "shadow"},
        model_state_path="x", metrics={"macro_f1": 0.65}, code_revision="a",
    )

    db_path = tmp_path / "trade_journal.db"
    _empty_db(db_path)
    shadow_log = tmp_path / "shadow_predictions.jsonl"
    shadow_log.write_text("")

    # Without datasets_root, EVERY shadow model holds on oos_edge.
    report = build_readiness_report(
        registry_root=reg_root,
        db_path=db_path,
        shadow_log=shadow_log,
        backfill_log=None,
        datasets_root=None,
        now_utc=datetime(2026, 6, 7, tzinfo=timezone.utc),
    )
    assert len(report.proposals) == 2
    assert report.promote_ready == ()
    assert {p.model_id for p in report.held} == {"ready-model", "holding-model"}
    assert report.datasets_root_used is None
    # The summary the orchestrator surfaces matches what the buckets compute.
    payload = report.to_dict()
    assert payload["summary"]["hold_count"] == 2
    assert payload["summary"]["promote"] == []
