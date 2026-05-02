"""Tests for scripts/comms_ask.py — the CLI helper Claude uses to author requests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import comms_ask


# ----------------------------------------------------------------------

class TestExpiresIn:
    def test_none(self):
        assert comms_ask._parse_expires_in(None) is None

    @pytest.mark.parametrize("spec", ["24h", "90m", "7d", "30s"])
    def test_valid(self, spec):
        out = comms_ask._parse_expires_in(spec)
        # Should parse back to a datetime in the future.
        dt = datetime.fromisoformat(out)
        assert dt > datetime.now(timezone.utc)

    @pytest.mark.parametrize("spec", ["", "x", "10y", "h24", "24"])
    def test_invalid_raises(self, spec):
        with pytest.raises(SystemExit):
            comms_ask._parse_expires_in(spec)


class TestParseChoice:
    def test_valid(self):
        c = comms_ask._parse_choice("live=Live now")
        assert c.id == "live"
        assert c.label == "Live now"

    def test_missing_equals_raises(self):
        with pytest.raises(SystemExit):
            comms_ask._parse_choice("just_id")


class TestStitchQuestionGroups:
    """The CLI's per-question-flag pairing relies on argv ordering."""

    def test_single_choice_question(self):
        argv = [
            "--topic", "X", "--slug", "abcd",
            "--question", "mode", "--type", "choice",
            "--prompt", "Pick one",
            "--choice", "a=A", "--choice", "b=B",
        ]
        args, qs = comms_ask._parse_args(argv)
        assert len(qs) == 1
        assert qs[0].question_id == "mode"
        assert [c.id for c in qs[0].choices] == ["a", "b"]

    def test_multiple_questions(self):
        argv = [
            "--topic", "X", "--slug", "abcd",
            "--question", "a", "--type", "yes_no", "--prompt", "A?",
            "--question", "b", "--type", "free_text", "--prompt", "B?",
        ]
        args, qs = comms_ask._parse_args(argv)
        assert [q.question_id for q in qs] == ["a", "b"]
        assert qs[0].input_type == "yes_no"
        assert qs[1].input_type == "free_text"

    def test_allow_other_attaches_to_current_question(self):
        argv = [
            "--topic", "X", "--slug", "abcd",
            "--question", "mode", "--type", "choice",
            "--prompt", "Pick", "--choice", "a=A",
            "--allow-other",
        ]
        _, qs = comms_ask._parse_args(argv)
        assert qs[0].allow_other is True

    def test_optional_marks_required_false(self):
        argv = [
            "--topic", "X", "--slug", "abcd",
            "--question", "ideas", "--type", "free_text",
            "--prompt", "?", "--optional",
        ]
        _, qs = comms_ask._parse_args(argv)
        assert qs[0].required is False

    def test_no_question_raises(self):
        argv = ["--topic", "X", "--slug", "abcd"]
        with pytest.raises(SystemExit):
            comms_ask._parse_args(argv)

    def test_question_missing_type_raises(self):
        argv = [
            "--topic", "X", "--slug", "abcd",
            "--question", "mode", "--prompt", "?",
        ]
        with pytest.raises(SystemExit):
            comms_ask._parse_args(argv)


class TestMain:
    def test_print_emits_json(self, tmp_path: Path, capsys, monkeypatch):
        argv = [
            "--topic", "X", "--slug", "smoketest",
            "--question", "mode", "--type", "yes_no", "--prompt", "OK?",
            "--print",
        ]
        rc = comms_ask.main(argv)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["topic"] == "X"
        assert data["request_id"].startswith("REQ-")
        assert data["questions"][0]["question_id"] == "mode"

    def test_writes_artifact(self, tmp_path: Path, capsys, monkeypatch):
        argv = [
            "--topic", "X", "--slug", "writetst",
            "--question", "mode", "--type", "yes_no", "--prompt", "OK?",
            "--repo-root", str(tmp_path),
        ]
        # Pre-create comms area parents so the store can write.
        rc = comms_ask.main(argv)
        assert rc == 0
        files = list((tmp_path / "comms" / "requests").glob("REQ-*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["topic"] == "X"

    def test_expires_in_recorded(self, tmp_path: Path):
        argv = [
            "--topic", "X", "--slug", "ttltest1",
            "--question", "mode", "--type", "yes_no", "--prompt", "?",
            "--expires-in", "1h",
            "--repo-root", str(tmp_path),
        ]
        rc = comms_ask.main(argv)
        assert rc == 0
        files = list((tmp_path / "comms" / "requests").glob("REQ-*.json"))
        data = json.loads(files[0].read_text())
        assert data.get("expires_at")

    def test_default_on_timeout_recorded(self, tmp_path: Path):
        argv = [
            "--topic", "X", "--slug", "deftime",
            "--question", "mode", "--type", "yes_no", "--prompt", "?",
            "--default-on-timeout", "use_defaults",
            "--repo-root", str(tmp_path),
        ]
        rc = comms_ask.main(argv)
        assert rc == 0
        files = list((tmp_path / "comms" / "requests").glob("REQ-*.json"))
        data = json.loads(files[0].read_text())
        assert data["default_on_timeout"] == "use_defaults"
