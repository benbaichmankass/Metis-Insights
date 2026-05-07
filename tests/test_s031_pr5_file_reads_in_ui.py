"""S-031 PR5 regression tests
(architecture-audit-2026-05-02 P1-6).

Per CLAUDE.md § Architecture rules § 5 the Telegram bot is a thin
shell. Pre-PR ``src/bot/telegram_query_bot.py`` opened
``CHECKPOINT_LOG.md``, ``ROADMAP.md``, ``/proc/meminfo``,
``/proc/loadavg``, ``/proc/uptime``, and called ``shutil.disk_usage``
inline; ``src/bot/claude_bridge.py`` opened ``ROADMAP.md`` inline.
Post-PR each handler is a one-liner over a UI processor helper:

  * ``processor.get_latest_sprint``
  * ``processor.get_latest_checkpoint_header``
  * ``processor.get_health_summary``
  * ``processor.get_vm_stats``
  * ``processor.get_roadmap_summary``

Tests pin contract + every error path the bot relied on (None /
shape-stable error string).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# get_latest_sprint
# ---------------------------------------------------------------------------


CP_LOG_HAPPY = """\
# Checkpoint log

---

## CP-2026-05-02-99 — example header

- **Session date:** 2026-05-02
- **Sprint:** S-031 PR5 — boundary cleanup.
- More fields here.

---

## CP-2026-05-02-98 — earlier entry

- **Sprint:** S-030 PR4.
"""


class TestGetLatestSprint:
    def test_parses_topmost_entry(self, tmp_path, monkeypatch):
        log = tmp_path / "CHECKPOINT_LOG.md"
        log.write_text(CP_LOG_HAPPY, encoding="utf-8")
        from src.units.ui import processor
        with patch.object(processor, "_checkpoint_log_path",
                          return_value=str(log)):
            info = processor.get_latest_sprint()
        assert info["cp_id"] == "CP-2026-05-02-99"
        assert info["sprint_id"] == "S-031"

    def test_missing_file_returns_unknown(self, tmp_path):
        from src.units.ui import processor
        missing = tmp_path / "missing.md"
        with patch.object(processor, "_checkpoint_log_path",
                          return_value=str(missing)):
            info = processor.get_latest_sprint()
        assert info == {"sprint_id": "unknown", "cp_id": "unknown"}

    def test_no_cp_header_returns_unknown(self, tmp_path):
        from src.units.ui import processor
        empty = tmp_path / "empty.md"
        empty.write_text("# Just a heading\n\nNo CP entries.\n",
                         encoding="utf-8")
        with patch.object(processor, "_checkpoint_log_path",
                          return_value=str(empty)):
            info = processor.get_latest_sprint()
        assert info == {"sprint_id": "unknown", "cp_id": "unknown"}

    def test_cp_header_without_sprint_line(self, tmp_path):
        from src.units.ui import processor
        log = tmp_path / "log.md"
        log.write_text(
            "## CP-2026-05-02-77 — header only\n\n"
            "- no sprint field here\n",
            encoding="utf-8",
        )
        with patch.object(processor, "_checkpoint_log_path",
                          return_value=str(log)):
            info = processor.get_latest_sprint()
        assert info["cp_id"] == "CP-2026-05-02-77"
        assert info["sprint_id"] == "unknown"


# ---------------------------------------------------------------------------
# get_latest_checkpoint_header
# ---------------------------------------------------------------------------


class TestGetLatestCheckpointHeader:
    def test_returns_first_cp_header(self, tmp_path):
        log = tmp_path / "log.md"
        log.write_text(CP_LOG_HAPPY, encoding="utf-8")
        from src.units.ui import processor
        with patch.object(processor, "_checkpoint_log_path",
                          return_value=str(log)):
            header = processor.get_latest_checkpoint_header()
        assert header.startswith("## CP-2026-05-02-99")

    def test_no_cp_header_returns_none_string(self, tmp_path):
        from src.units.ui import processor
        log = tmp_path / "log.md"
        log.write_text("# heading only\n", encoding="utf-8")
        with patch.object(processor, "_checkpoint_log_path",
                          return_value=str(log)):
            header = processor.get_latest_checkpoint_header()
        assert header == "No checkpoint found"

    def test_missing_file_returns_warning_string(self, tmp_path):
        from src.units.ui import processor
        with patch.object(processor, "_checkpoint_log_path",
                          return_value=str(tmp_path / "missing.md")):
            header = processor.get_latest_checkpoint_header()
        assert header.startswith("⚠️")


# ---------------------------------------------------------------------------
# get_health_summary
# ---------------------------------------------------------------------------


class TestGetHealthSummary:
    def test_renders_with_active_services_and_present_files(self, tmp_path):
        from src.units.ui import processor
        # Create the three observed files so the freshness section is real.
        (tmp_path / "runtime_logs").mkdir()
        (tmp_path / "runtime_logs/runtime_status.json").write_text("{}")
        (tmp_path / "runtime_logs/signal_audit.jsonl").write_text("\n")
        (tmp_path / "trade_journal.db").write_text("x")

        out = processor.get_health_summary(
            get_service_status=lambda u: "active",
            repo_root=str(tmp_path),
        )
        assert "ICT Trading Bot — health" in out
        # Every unit appears with the active icon.
        for unit in ("ict-trader-live", "ict-telegram-bot",
                     "ict-web-api", "ict-git-sync.timer"):
            assert unit in out
        assert "🟢" in out
        # Freshness section is rendered.
        assert "Data freshness" in out
        assert "runtime_status.json (last tick)" in out

    def test_failed_services_get_red_icon(self, tmp_path):
        from src.units.ui import processor
        out = processor.get_health_summary(
            get_service_status=lambda u: "failed",
            repo_root=str(tmp_path),
        )
        assert "🔴" in out

    def test_unknown_status_yields_neutral_icon(self, tmp_path):
        from src.units.ui import processor
        out = processor.get_health_summary(
            get_service_status=lambda u: "unknown",
            repo_root=str(tmp_path),
        )
        assert "⚪️" in out

    def test_missing_files_render_as_missing(self, tmp_path):
        from src.units.ui import processor
        out = processor.get_health_summary(
            get_service_status=lambda u: "active",
            repo_root=str(tmp_path),
        )
        # All three configured files are absent in tmp_path.
        assert out.count("missing") >= 3

    def test_get_service_status_exception_does_not_raise(self, tmp_path):
        from src.units.ui import processor

        def boom(_unit):
            raise RuntimeError("systemctl unavailable")

        out = processor.get_health_summary(
            get_service_status=boom, repo_root=str(tmp_path),
        )
        assert "err: RuntimeError" in out


# ---------------------------------------------------------------------------
# get_vm_stats
# ---------------------------------------------------------------------------


class TestGetVmStats:
    def test_renders_block(self):
        from src.units.ui import processor
        out = processor.get_vm_stats()
        assert "VM stats" in out
        assert "Uptime" in out
        assert "Load" in out
        # Memory line either renders a number or "unknown".
        assert "Memory" in out

    def test_meminfo_unreadable_renders_unknown(self):
        from src.units.ui import processor
        with patch.object(processor, "_read_meminfo_mb",
                          return_value=(0, 0)):
            out = processor.get_vm_stats()
        assert "🧠 Memory: unknown" in out

    def test_disk_unreadable_renders_unknown(self):
        from src.units.ui import processor
        with patch.object(processor, "_disk_usage_repo",
                          return_value=(0, 0)):
            out = processor.get_vm_stats()
        assert "💾 Disk: unknown" in out


# ---------------------------------------------------------------------------
# get_roadmap_summary
# ---------------------------------------------------------------------------


ROADMAP_FIXTURE = """\
# ROADMAP

| Sprint | Title | Status |
|---|---|---|
| S-001 | **First** — done | ✅ Done |
| S-029 | **Audit fixes** — | ✅ Done |
| S-031 | **Bot thinning** — | 🔄 In Progress |
| S-032 | **Move data_loaders** — | 🔜 Next |
| S-040 | **Future work** — | 📋 Backlog |
"""


class TestGetRoadmapSummary:
    def test_renders_summary(self, monkeypatch, tmp_path):
        # Patch processor's repo-root discovery to point at tmp_path.
        from src.units.ui import processor
        roadmap = tmp_path / "ROADMAP.md"
        roadmap.write_text(ROADMAP_FIXTURE, encoding="utf-8")

        # Substitute file resolution with monkeypatched __file__-anchor.
        # processor.get_roadmap_summary computes repo_root from its own
        # location; instead patch open via the module's local lookup.
        original_open = open

        def fake_open(p, *a, **kw):
            if str(p).endswith("ROADMAP.md"):
                return original_open(str(roadmap), *a, **kw)
            return original_open(p, *a, **kw)

        with patch("builtins.open", side_effect=fake_open):
            out = processor.get_roadmap_summary()
        assert "Roadmap Status" in out
        # Both in-progress and next sprint pulled out.
        assert "S-031" in out
        assert "S-032" in out
        assert "Sprint counts" in out

    def test_missing_file_returns_warning(self, tmp_path):
        from src.units.ui import processor
        original_open = open

        def fake_open(p, *a, **kw):
            if str(p).endswith("ROADMAP.md"):
                raise FileNotFoundError(str(p))
            return original_open(p, *a, **kw)

        with patch("builtins.open", side_effect=fake_open):
            out = processor.get_roadmap_summary()
        assert out.startswith("⚠️")
        assert "ROADMAP.md" in out


# ---------------------------------------------------------------------------
# Bot back-compat wrapper still works
# ---------------------------------------------------------------------------


class TestBotWrappers:
    def test_latest_sprint_wrapper_returns_tuple(self, tmp_path):
        from src.units.ui import processor
        log = tmp_path / "log.md"
        log.write_text(CP_LOG_HAPPY, encoding="utf-8")
        # Stub the bot's heavy deps before importing.
        import sys
        import types
        for mod_name in (
            "telegram", "telegram.ext", "telegram.error",
            "telegram.constants",
        ):
            sys.modules.setdefault(mod_name, types.SimpleNamespace())
        try:
            from src.bot import telegram_query_bot as bot
        except ModuleNotFoundError as exc:  # missing pandas etc in sandbox
            pytest.skip(f"bot module unavailable: {exc}")

        with patch.object(processor, "_checkpoint_log_path",
                          return_value=str(log)):
            sprint, cp = bot._latest_sprint_from_checkpoint_log()
        assert sprint == "S-031"
        assert cp == "CP-2026-05-02-99"
