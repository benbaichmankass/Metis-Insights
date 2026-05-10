"""Tests for the `compare` CLI subcommand (S-AI-WS4-FU)."""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ml.cli import main
from ml.registry.model_registry import ModelRegistry


def _seed_registry(tmp_path: Path) -> Path:
    reg = ModelRegistry(tmp_path / "registry")
    reg.register(
        model_id="model-a",
        manifest={"manifest_version": "v1"},
        model_state_path="/tmp/a.json",
        metrics={"accuracy": 0.6, "f1": 0.5, "a_only": 0.99},
        code_revision="aaa",
    )
    reg.register(
        model_id="model-b",
        manifest={"manifest_version": "v1"},
        model_state_path="/tmp/b.json",
        metrics={"accuracy": 0.7, "f1": 0.65, "b_only": 0.42},
        code_revision="bbb",
    )
    return tmp_path / "registry"


def _capture_main(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = main(argv)
    finally:
        sys.stdout = saved
    return rc, buf.getvalue()


def test_compare_basic(tmp_path: Path):
    reg_root = _seed_registry(tmp_path)
    rc, out = _capture_main([
        "compare", "model-a", "model-b",
        "--registry-root", str(reg_root),
    ])
    assert rc == 0
    payload = json.loads(out)
    assert payload["model_a"]["id"] == "model-a"
    assert payload["model_b"]["id"] == "model-b"
    diffs = {d["metric"]: d for d in payload["metric_diffs"]}
    assert diffs["accuracy"]["a"] == 0.6
    assert diffs["accuracy"]["b"] == 0.7
    assert diffs["accuracy"]["delta"] == 0.1
    assert diffs["f1"]["delta"] == 0.65 - 0.5
    assert payload["a_only_metrics"] == ["a_only"]
    assert payload["b_only_metrics"] == ["b_only"]


def test_compare_missing_model(tmp_path: Path):
    reg_root = _seed_registry(tmp_path)
    # Compare against a missing id should raise via ModelRegistry.get
    import pytest
    from ml.registry.model_registry import RegistryError

    with pytest.raises(RegistryError):
        _capture_main([
            "compare", "model-a", "missing",
            "--registry-root", str(reg_root),
        ])
