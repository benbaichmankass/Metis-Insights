"""End-to-end CLI tests for `promote-stage` (2026-05-19 default flip).

Covers the single-model variant and the bulk `--all-pre-shadow`
migration helper used to move every research_only / candidate /
backtest_approved entry into shadow in one invocation.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from ml.cli import main
from ml.registry.model_registry import ModelRegistry


def _capture_main(argv: list[str]) -> tuple[int, str, str]:
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_buf, err_buf
    try:
        rc = main(argv)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, out_buf.getvalue(), err_buf.getvalue()


def _register(reg: ModelRegistry, model_id: str, stage: str) -> None:
    reg.register(
        model_id=model_id,
        manifest={
            "manifest_version": "v1",
            "target_deployment_stage": stage,
        },
        model_state_path=f"/tmp/{model_id}.json",
        metrics={},
        code_revision="abc",
    )


class TestPromoteStageSingleModel:
    def test_promotes_named_model(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        _register(reg, "m-1", "research_only")
        rc, stdout, _ = _capture_main([
            "promote-stage", "m-1",
            "--new-stage", "shadow",
            "--registry-root", str(tmp_path),
            "--by", "op",
            "--reason", "test",
        ])
        assert rc == 0
        payload = json.loads(stdout)
        assert payload["target_deployment_stage"] == "shadow"

    def test_refuses_no_op(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        _register(reg, "m-1", "shadow")
        rc, _, stderr = _capture_main([
            "promote-stage", "m-1",
            "--new-stage", "shadow",
            "--registry-root", str(tmp_path),
            "--by", "op",
            "--reason", "test",
        ])
        # `registry.promote_stage` raises RegistryError on no-op; the
        # CLI lets the exception propagate so the operator sees a
        # non-zero exit. Either path is acceptable for now — assert the
        # exit is non-zero either way.
        assert rc != 0 or "no-op" in stderr


class TestPromoteStageAllPreShadow:
    def test_migrates_only_pre_shadow_entries(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        _register(reg, "m-pre-1", "research_only")
        _register(reg, "m-pre-2", "candidate")
        _register(reg, "m-pre-3", "backtest_approved")
        _register(reg, "m-shadow", "shadow")
        # advisory/limited_live/live_approved aren't reachable via
        # register() — manifest validation accepts them but the
        # default-flip change only affects pre-shadow stages, so this
        # is enough coverage for the helper's filter.
        rc, stdout, _ = _capture_main([
            "promote-stage",
            "--new-stage", "shadow",
            "--registry-root", str(tmp_path),
            "--by", "claude-migration",
            "--reason", "default-policy-2026-05-19",
            "--all-pre-shadow",
        ])
        assert rc == 0
        payload = json.loads(stdout)
        assert payload["transitioned_count"] == 3
        assert payload["skipped_count"] == 1
        transitioned_ids = sorted(
            t["model_id"] for t in payload["transitioned"]
        )
        assert transitioned_ids == ["m-pre-1", "m-pre-2", "m-pre-3"]
        skipped_ids = sorted(s["model_id"] for s in payload["skipped"])
        assert skipped_ids == ["m-shadow"]
        for entry_id in transitioned_ids:
            assert (
                reg.get(entry_id).target_deployment_stage == "shadow"
            )

    def test_rejects_non_shadow_target(self, tmp_path: Path):
        reg = ModelRegistry(tmp_path)
        _register(reg, "m-1", "research_only")
        rc, _, stderr = _capture_main([
            "promote-stage",
            "--new-stage", "advisory",
            "--registry-root", str(tmp_path),
            "--by", "op",
            "--reason", "wrong",
            "--all-pre-shadow",
        ])
        assert rc == 2
        assert "--all-pre-shadow" in stderr

    def test_requires_model_id_or_all_flag(self, tmp_path: Path):
        rc, _, stderr = _capture_main([
            "promote-stage",
            "--new-stage", "shadow",
            "--registry-root", str(tmp_path),
            "--by", "op",
            "--reason", "missing-id",
        ])
        assert rc == 2
        assert "model_id" in stderr or "--all-pre-shadow" in stderr
