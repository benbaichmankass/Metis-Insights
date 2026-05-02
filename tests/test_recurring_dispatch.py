"""Unit tests for src/bot/recurring_dispatch.py.

Pure unit tests — no telegram stubs needed since this module has no
telegram dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.bot import recurring_dispatch as rd


class TestLogTrigger:
    def test_writes_jsonl_line_with_required_fields(self, tmp_path: Path):
        entry = rd.log_trigger(tmp_path, "audit")
        assert entry["type"] == "audit"
        assert entry["args"] == []
        assert "triggered_at" in entry

        log = tmp_path / rd.SESSION_LOG_PATH
        assert log.exists()
        line = log.read_text(encoding="utf-8").strip()
        on_disk = json.loads(line)
        assert on_disk == entry

    def test_appends_to_existing_log(self, tmp_path: Path):
        rd.log_trigger(tmp_path, "audit")
        rd.log_trigger(tmp_path, "improve_strategy", args=["vwap"])
        log = tmp_path / rd.SESSION_LOG_PATH
        lines = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["type"] == "improve_strategy"
        assert json.loads(lines[1])["args"] == ["vwap"]

    def test_creates_runtime_logs_dir_if_absent(self, tmp_path: Path):
        # tmp_path doesn't have runtime_logs/
        rd.log_trigger(tmp_path, "train_model")
        assert (tmp_path / "runtime_logs").is_dir()

    def test_rejects_unknown_session_type(self, tmp_path: Path):
        with pytest.raises(ValueError):
            rd.log_trigger(tmp_path, "wrong_type")


class TestBuildStarterPrompt:
    def test_audit_prompt_references_hardening_doc(self):
        p = rd.build_starter_prompt("audit")
        assert "recurring-hardening-prompt.md" in p
        assert "CLAUDE.md" in p
        assert "Phase 1" in p

    def test_improve_strategy_with_no_strategy(self):
        p = rd.build_starter_prompt("improve_strategy")
        assert "recurring-strategy-improvement-prompt.md" in p
        # No strategy clause when none given
        assert "focused on" not in p

    def test_improve_strategy_with_strategy_name(self):
        p = rd.build_starter_prompt("improve_strategy", strategy="vwap")
        assert "vwap" in p
        assert "focused on the 'vwap' strategy" in p

    def test_train_model_includes_ml_policy_reference(self):
        p = rd.build_starter_prompt("train_model", strategy="turtle_soup")
        assert "ml-training-policy.md" in p
        assert "turtle_soup" in p
        assert "NEVER promotes" in p

    def test_strategy_clause_strips_dangerous_chars(self):
        # Keep only [a-zA-Z0-9_-]; everything else gets stripped.
        p = rd.build_starter_prompt("improve_strategy", strategy="vwap; rm -rf /")
        # The bad chars must be stripped before reaching the prompt.
        assert "rm -rf /" not in p
        assert ";" not in p
        # The salvageable identifier survives.
        assert "vwap" in p

    def test_rejects_unknown_session_type(self):
        with pytest.raises(ValueError):
            rd.build_starter_prompt("wrong_type")


class TestRenderRoadmapSummary:
    SAMPLE = """# Roadmap

| Sprint | Title | Status |
|--------|-------|--------|
| S-013 | **Backend Scaffold** | ✅ Done |
| S-014 | **Web Client V1 (Home Dashboard)** — HTMX stack | 🔜 Next |
| S-015 | **Web Client V2** | 📋 Backlog |
| S-016 | **Key Mgmt** | 📋 Backlog |
"""

    def test_counts_status_emojis(self):
        out = rd.render_roadmap_summary(self.SAMPLE)
        assert "✅ 1 done" in out
        assert "🔜 1 next" in out
        assert "📋 2 backlog" in out

    def test_extracts_next_sprint(self):
        out = rd.render_roadmap_summary(self.SAMPLE)
        assert "S-014" in out
        assert "Web Client V1" in out

    def test_handles_in_progress(self):
        text = self.SAMPLE.replace("🔜 Next", "🔄 In Progress", 1)
        out = rd.render_roadmap_summary(text)
        assert "🔄 In Progress: S-014" in out
        assert "Web Client V1" in out

    def test_empty_roadmap_falls_back_gracefully(self):
        out = rd.render_roadmap_summary("# empty\n")
        assert "no sprint currently marked" in out
        assert "✅ 0 done" in out

    def test_output_uses_no_markdown_special_chars(self):
        # Per CLAUDE.md BUG-009/030/031 lesson, dynamic content sent to
        # Telegram must avoid Markdown parse_mode. Make sure our output
        # has no unbalanced * or _ that would break Markdown parsing
        # if someone forgot and added parse_mode=Markdown later.
        out = rd.render_roadmap_summary(self.SAMPLE)
        # Title bolding from the source (** Web Client V1 **) must be
        # stripped, not passed through.
        assert "**" not in out
