"""FIX-SA-01: RG4 stage-2 replay reads rotated shadow-log archives.

``ict-shadow-log-rotate.timer`` moves the active log to a gzipped archive every
~25-29 days. ``replay_pregate_live.run`` used to read the active log only, so
right after a rotation the ``labels_accruing`` gate saw 0 rows, and the empty
case raised ``SystemExit`` (a BaseException) straight past
``ml/cli.py::_compute_regime_live_replay``'s ``except Exception``.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("pandas")  # replay_pregate imports pandas at module load

import scripts.ml.replay_pregate_live as R  # noqa: E402

MODEL = "eth-regime-1h"


def _rec(model_id: str, when: datetime) -> dict:
    return {
        "predicted_at_utc": when.isoformat(),
        "model_id": model_id,
        "stage": "shadow",
        "score": 0.5,
        "feature_row": {"vol_bucket": "low"},
    }


def _write(path, rows, *, gz=False):
    text = "".join(json.dumps(r) + "\n" for r in rows)
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _plant(tmp_path, n_archive: int):
    """Active log: 0 rows for MODEL (one for another model). Archive: N."""
    now = datetime.now(timezone.utc)
    active = tmp_path / "shadow_predictions.jsonl"
    _write(active, [_rec("other-model", now - timedelta(hours=1))])
    archive = tmp_path / "shadow_predictions.2026-09-01.jsonl.gz"
    _write(
        archive,
        [_rec(MODEL, now - timedelta(days=5, minutes=i)) for i in range(n_archive)]
        + [_rec("other-model", now - timedelta(days=5))],
        gz=True,
    )
    return active


def test_reads_archived_records_when_active_log_has_none(tmp_path):
    active = _plant(tmp_path, n_archive=7)
    recs = R._read_model_records(str(active), MODEL)
    assert len(recs) == 7
    assert {r["model_id"] for r in recs} == {MODEL}
    assert recs[0]["feature_row"] == {"vol_bucket": "low"}


def test_lookback_excludes_rows_older_than_window(tmp_path):
    now = datetime.now(timezone.utc)
    active = tmp_path / "shadow_predictions.jsonl"
    _write(active, [_rec(MODEL, now - timedelta(days=120)),
                    _rec(MODEL, now - timedelta(days=1))])
    assert len(R._read_model_records(str(active), MODEL, now=now)) == 1


def _stub_registry(monkeypatch):
    monkeypatch.setattr(R, "ModelRegistry", lambda root: object())
    monkeypatch.setattr(R._factory, "_resolve_default_registry_root", lambda: "x")
    monkeypatch.setattr(R, "resolve_predictor",
                        lambda mid, reg, log_path=None: SimpleNamespace())
    monkeypatch.setattr(R, "regime_spec_of", lambda b: {"symbol": "ETHUSDT"})


def test_run_counts_archived_records(tmp_path, monkeypatch):
    _stub_registry(monkeypatch)
    active = _plant(tmp_path, n_archive=7)
    candles = tmp_path / "data.jsonl"
    candles.write_text("", encoding="utf-8")
    report = R.run(MODEL, shadow_log=str(active), candles=str(candles),
                   forward_m=5, vol_threshold=0.005, positive_class="volatile",
                   bar_seconds=3600.0)
    assert "error" not in report
    assert report["n_records"] == 7


def test_run_empty_returns_error_state_and_does_not_exit(tmp_path, monkeypatch):
    _stub_registry(monkeypatch)
    active = tmp_path / "shadow_predictions.jsonl"
    _write(active, [_rec("other-model", datetime.now(timezone.utc))])
    report = R.run(MODEL, shadow_log=str(active), candles="unused",
                   forward_m=5, vol_threshold=0.005, positive_class="volatile",
                   bar_seconds=3600.0)  # must not raise SystemExit
    assert report["n_records"] == 0
    assert MODEL in report["error"]


def test_cli_treats_error_report_as_unmeasured_not_zero_rows():
    """An error report must not reach labels_accruing as n_records=0."""
    import inspect

    import ml.cli as cli

    src = inspect.getsource(cli._cmd_gate_check)
    assert 'rg4_report is not None and not rg4_report.get("error")' in src
