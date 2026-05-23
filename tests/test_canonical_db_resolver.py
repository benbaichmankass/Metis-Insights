"""Tests for the canonical DB-path resolver in scripts/ops/_lib.sh
and its CI guard at scripts/check_canonical_db_resolver.py.

What's load-bearing
-------------------
The resolver's layering must match the systemd unit-load order
(drop-in defaults → .env overrides → live systemctl) and pre-set
caller env must win over every layer. The guard must catch the
exact bug idiom from the 2026-05-16 incident (the
``${TRADE_JOURNAL_DB:-${REPO_DIR}/trade_journal.db}`` fallback) but
NOT flag log strings or filename references that aren't path
constructions.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIB_SH = _REPO_ROOT / "scripts" / "ops" / "_lib.sh"


def _run_in_shell(repo_dir: Path, extra: str = "", env: dict | None = None) -> str:
    """Source ``_lib.sh`` under ``REPO_DIR=repo_dir`` and run *extra*
    bash. Returns trimmed stdout. Errors propagate as
    ``CalledProcessError``."""
    script = (
        f'set -e\n'
        f'export REPO_DIR={repo_dir!s}\n'
        f'source {_LIB_SH!s}\n'
        f'{extra}\n'
    )
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, env=full_env, check=True,
    )
    return result.stdout.strip()


def _write_dropin(repo_dir: Path, lines: list[str]) -> None:
    dropin = repo_dir / "deploy" / "dropins" / "data-dir.conf"
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text("[Service]\n" + "\n".join(lines) + "\n", encoding="utf-8")


class TestLoadRuntimeEnv:
    """``load_runtime_env`` must layer drop-in → .env → systemctl,
    with pre-set vars in the caller's env taking precedence over
    every layer."""

    def test_dropin_pins_data_dir_and_db(self, tmp_path):
        _write_dropin(tmp_path, [
            "Environment=DATA_DIR=/data/bot-data",
            "Environment=TRADE_JOURNAL_DB=/data/bot-data/trade_journal.db",
        ])
        out = _run_in_shell(tmp_path, 'load_runtime_env; echo "$DATA_DIR|$TRADE_JOURNAL_DB"')
        assert out == "/data/bot-data|/data/bot-data/trade_journal.db"

    def test_env_file_overrides_dropin(self, tmp_path):
        _write_dropin(tmp_path, [
            "Environment=DATA_DIR=/data/bot-data",
            "Environment=TRADE_JOURNAL_DB=/data/bot-data/trade_journal.db",
        ])
        (tmp_path / ".env").write_text(
            "TRADE_JOURNAL_DB=/tmp/override.db\n", encoding="utf-8",
        )
        out = _run_in_shell(tmp_path, 'load_runtime_env; echo "$TRADE_JOURNAL_DB"')
        assert out == "/tmp/override.db"

    def test_preset_caller_env_wins_over_dropin(self, tmp_path):
        _write_dropin(tmp_path, [
            "Environment=TRADE_JOURNAL_DB=/data/bot-data/trade_journal.db",
        ])
        out = _run_in_shell(
            tmp_path,
            'load_runtime_env; echo "$TRADE_JOURNAL_DB"',
            env={"TRADE_JOURNAL_DB": "/pre/set/path.db"},
        )
        assert out == "/pre/set/path.db"

    def test_quotes_in_env_file_stripped(self, tmp_path):
        (tmp_path / ".env").write_text(
            'TRADE_JOURNAL_DB="/quoted/path.db"\n', encoding="utf-8",
        )
        out = _run_in_shell(tmp_path, 'load_runtime_env; echo "$TRADE_JOURNAL_DB"')
        assert out == "/quoted/path.db"

    def test_whitelist_blocks_unrelated_keys(self, tmp_path):
        """Non-whitelisted keys in the drop-in must not be exported
        — load_runtime_env should be tight about what it touches."""
        _write_dropin(tmp_path, [
            "Environment=DATA_DIR=/data/bot-data",
            "Environment=NOT_A_PATH_VAR=value",
        ])
        out = _run_in_shell(
            tmp_path,
            'load_runtime_env; echo "DATA=$DATA_DIR NOT=${NOT_A_PATH_VAR-unset}"',
        )
        assert out == "DATA=/data/bot-data NOT=unset"

    def test_missing_dropin_is_quiet(self, tmp_path):
        # No deploy/dropins/data-dir.conf at all — helper must not
        # error, just return without setting anything.
        out = _run_in_shell(
            tmp_path,
            'load_runtime_env; echo "TJD=${TRADE_JOURNAL_DB-unset}"',
        )
        assert out == "TJD=unset"

    def test_load_runtime_env_is_idempotent(self, tmp_path):
        _write_dropin(tmp_path, [
            "Environment=DATA_DIR=/data/bot-data",
        ])
        out = _run_in_shell(
            tmp_path,
            'load_runtime_env; load_runtime_env; load_runtime_env; echo "$DATA_DIR"',
        )
        assert out == "/data/bot-data"


class TestRuntimeDbPath:
    """``runtime_db_path`` is the single public entry point wrappers
    must call. It must always print a non-empty path."""

    def test_returns_canonical_path_when_dropin_present(self, tmp_path):
        _write_dropin(tmp_path, [
            "Environment=TRADE_JOURNAL_DB=/data/bot-data/trade_journal.db",
        ])
        out = _run_in_shell(tmp_path, 'echo "$(runtime_db_path)"')
        assert out == "/data/bot-data/trade_journal.db"

    def test_falls_back_to_repo_local_when_nothing_canonical_available(self, tmp_path):
        """Dev box / fresh checkout with no deploy/dropins, no .env,
        no systemd — must still return a usable path (the pre-2026-05-12
        layout)."""
        out = _run_in_shell(tmp_path, 'echo "$(runtime_db_path)"')
        assert out == f"{tmp_path}/trade_journal.db"

    def test_preset_caller_env_wins(self, tmp_path):
        out = _run_in_shell(
            tmp_path, 'echo "$(runtime_db_path)"',
            env={"TRADE_JOURNAL_DB": "/explicit/override.db"},
        )
        assert out == "/explicit/override.db"


# --- Guard tests --------------------------------------------------------


def _load_guard():
    path = _REPO_ROOT / "scripts" / "check_canonical_db_resolver.py"
    spec = importlib.util.spec_from_file_location(
        "check_canonical_db_resolver", path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDbResolverGuard:
    def test_main_repo_clean(self):
        result = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "scripts" / "check_canonical_db_resolver.py"), "--list"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"guard failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_catches_inline_fallback(self, tmp_path, monkeypatch):
        offender = tmp_path / "scripts" / "ops" / "evil_action.sh"
        offender.parent.mkdir(parents=True)
        offender.write_text(
            "#!/usr/bin/env bash\n"
            'DB_PATH="${TRADE_JOURNAL_DB:-${REPO_DIR}/trade_journal.db}"\n',
            encoding="utf-8",
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        offenders = mod._gather_offenders()
        assert len(offenders) == 1
        path, hits = offenders[0]
        assert path == offender
        assert len(hits) == 1

    def test_runtime_db_path_call_passes_guard(self, tmp_path, monkeypatch):
        clean = tmp_path / "scripts" / "ops" / "clean_action.sh"
        clean.parent.mkdir(parents=True)
        clean.write_text(
            "#!/usr/bin/env bash\n"
            'source scripts/ops/_lib.sh\n'
            'DB_PATH="$(runtime_db_path)"\n',
            encoding="utf-8",
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        offenders = mod._gather_offenders()
        assert offenders == []

    def test_log_string_mentioning_db_does_not_trip(self, tmp_path, monkeypatch):
        """A wrapper that emits an error message naming the DB file
        for diagnostics is NOT constructing a path; it's printing
        a literal filename. False positives like this would push
        operators to disable the guard."""
        ok = tmp_path / "scripts" / "ops" / "diag_action.sh"
        ok.parent.mkdir(parents=True)
        ok.write_text(
            "#!/usr/bin/env bash\n"
            'log "ERROR: trade_journal.db not present at $DB_PATH"\n',
            encoding="utf-8",
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        assert mod._gather_offenders() == []

    def test_respects_allowlist(self, tmp_path, monkeypatch):
        allowed = tmp_path / "scripts" / "ops" / "_lib.sh"
        allowed.parent.mkdir(parents=True)
        allowed.write_text(
            '# canonical helper — it owns the fallback expression\n'
            'echo "${TRADE_JOURNAL_DB:-default}"\n',
            encoding="utf-8",
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        assert mod._gather_offenders() == []


class TestPythonDbResolverGuard:
    """The Python scan forbids the CWD-relative fallback + inline
    TRADE_JOURNAL_DB env-reads outside the canonical resolver."""

    def _make(self, tmp_path, rel, body):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
        return f

    def test_main_repo_python_clean(self):
        """The real repo must pass the Python scan (full guard run)."""
        result = subprocess.run(
            [sys.executable,
             str(_REPO_ROOT / "scripts" / "check_canonical_db_resolver.py"),
             "--list"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"guard failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_catches_cwd_or_fallback(self, tmp_path, monkeypatch):
        self._make(
            tmp_path, "src/foo.py",
            'db_path = os.environ.get("X") or "trade_journal.db"\n',
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        offenders = mod._gather_python_offenders()
        assert len(offenders) == 1

    def test_catches_db_path_default(self, tmp_path, monkeypatch):
        self._make(
            tmp_path, "src/bar.py",
            'def __init__(self, db_path="trade_journal.db"):\n    pass\n',
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        assert len(mod._gather_python_offenders()) == 1

    def test_catches_inline_env_read(self, tmp_path, monkeypatch):
        self._make(
            tmp_path, "ml/baz.py",
            'p = os.environ.get("TRADE_JOURNAL_DB", "/x/trade_journal.db")\n',
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        assert len(mod._gather_python_offenders()) == 1

    def test_allows_repo_anchored_join(self, tmp_path, monkeypatch):
        """``os.path.join(_REPO_ROOT, "trade_journal.db")`` is absolute,
        not CWD-relative — must NOT trip."""
        self._make(
            tmp_path, "src/ok.py",
            'p = os.path.join(_REPO_ROOT, "trade_journal.db")\n'
            'q = _REPO_ROOT / "trade_journal.db"\n',
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        assert mod._gather_python_offenders() == []

    def test_allows_unrelated_filename_kwarg(self, tmp_path, monkeypatch):
        """A Telegram-upload ``filename="trade_journal.db"`` is not a
        path resolution — must NOT trip (regression for the false
        positive on telegram_query_bot.py)."""
        self._make(
            tmp_path, "src/upload.py",
            'bot.send_document(document=f, filename="trade_journal.db")\n',
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        assert mod._gather_python_offenders() == []

    def test_allows_canonical_resolver_call(self, tmp_path, monkeypatch):
        self._make(
            tmp_path, "src/clean.py",
            'from src.utils.paths import trade_journal_db_path\n'
            'db = Database(db_path=trade_journal_db_path())\n',
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        assert mod._gather_python_offenders() == []

    def test_allowlisted_module_may_read_env(self, tmp_path, monkeypatch):
        """src/utils/paths.py IS the resolver — allowed to read the env."""
        self._make(
            tmp_path, "src/utils/paths.py",
            'env = os.environ.get("TRADE_JOURNAL_DB")\n',
        )
        mod = _load_guard()
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        assert mod._gather_python_offenders() == []
