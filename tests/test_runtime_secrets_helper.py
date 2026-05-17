"""Tests for ``scripts/ops/_lib.sh::load_runtime_secrets``.

What's load-bearing
-------------------
``load_runtime_secrets`` sources ``${REPO_DIR}/.env`` in full so that
operator-action wrappers running via SSH (i.e. NOT as a child of
ict-trader-live.service, and thus NOT inheriting the systemd
EnvironmentFile) can authenticate to exchange APIs. The 2026-05-16
silent-credential failure (issue #1314, post-#1311) was caused by the
wrapper invoking python3 without this step — resolve_credentials()
read os.environ for BYBIT_API_KEY_2, found nothing, and returned None
silently for every candidate trade.

This file pins the helper's contract:
  * .env contents land in the shell's env as exports
  * absence of .env is a no-op (dev box / fresh checkout)
  * the helper is idempotent
  * sister scripts can call it safely without checking for .env first
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIB_SH = _REPO_ROOT / "scripts" / "ops" / "_lib.sh"


def _run_in_shell(repo_dir: Path, extra: str = "", env: dict | None = None) -> str:
    """Source ``_lib.sh`` under ``REPO_DIR=repo_dir`` and run *extra* bash."""
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


def _write_env(repo_dir: Path, contents: str) -> None:
    (repo_dir / ".env").write_text(textwrap.dedent(contents), encoding="utf-8")


class TestLoadRuntimeSecrets:
    def test_sources_dotenv_into_shell(self, tmp_path):
        _write_env(tmp_path, """
            BYBIT_API_KEY_2=ak_abc123
            BYBIT_API_SECRET_2=sk_def456
        """)
        out = _run_in_shell(
            tmp_path,
            'load_runtime_secrets; '
            'echo "$BYBIT_API_KEY_2|$BYBIT_API_SECRET_2"',
        )
        assert out == "ak_abc123|sk_def456"

    def test_missing_env_file_is_silent_noop(self, tmp_path):
        out = _run_in_shell(
            tmp_path,
            'load_runtime_secrets; echo "rc=$? bybit=${BYBIT_API_KEY_2-unset}"',
        )
        assert out == "rc=0 bybit=unset"

    def test_quoted_values_unwrap(self, tmp_path):
        _write_env(tmp_path, """
            BYBIT_API_KEY_2="quoted_key_value"
            BYBIT_API_SECRET_2='single_quoted_secret'
        """)
        out = _run_in_shell(
            tmp_path,
            'load_runtime_secrets; echo "$BYBIT_API_KEY_2|$BYBIT_API_SECRET_2"',
        )
        assert out == "quoted_key_value|single_quoted_secret"

    def test_comments_and_blanks_skipped(self, tmp_path):
        _write_env(tmp_path, """
            # this is a comment
            BYBIT_API_KEY_2=real_key

            # blank line above and another comment here
            BYBIT_API_SECRET_2=real_secret
        """)
        out = _run_in_shell(
            tmp_path,
            'load_runtime_secrets; echo "$BYBIT_API_KEY_2|$BYBIT_API_SECRET_2"',
        )
        assert out == "real_key|real_secret"

    def test_idempotent(self, tmp_path):
        _write_env(tmp_path, "BYBIT_API_KEY_2=abc\n")
        out = _run_in_shell(
            tmp_path,
            'load_runtime_secrets; '
            'load_runtime_secrets; '
            'load_runtime_secrets; '
            'echo "$BYBIT_API_KEY_2"',
        )
        assert out == "abc"

    def test_loads_arbitrary_non_credential_keys_too(self, tmp_path):
        """The helper is not credential-specific. Any KEY=VAL in .env
        gets exported. This matches systemd EnvironmentFile semantics
        — we don't try to be smart about which vars are 'secrets'."""
        _write_env(tmp_path, """
            DASHBOARD_API_TOKEN=tok123
            TELEGRAM_BOT_TOKEN=tg_abc
            LOG_LEVEL=DEBUG
        """)
        out = _run_in_shell(
            tmp_path,
            'load_runtime_secrets; '
            'echo "$DASHBOARD_API_TOKEN|$TELEGRAM_BOT_TOKEN|$LOG_LEVEL"',
        )
        assert out == "tok123|tg_abc|DEBUG"


class TestPythonScriptCredentialWarning:
    """The backfill script warns when 100% of skips share the
    ``account_closed_pnl_for_trade returned None`` reason — that
    pattern is the silent-credential-failure signature."""

    def test_warning_appears_when_all_skips_are_lookup_none(self, tmp_path):
        """Run the script's warning helper directly with a synthetic
        skip list that matches the silent-creds signature. The output
        should mention credential reachability."""
        import importlib.util
        path = _REPO_ROOT / "scripts" / "ops" / "backfill_orphan_pnl.py"
        spec = importlib.util.spec_from_file_location(
            "backfill_orphan_pnl_under_test", path,
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        captured: list[str] = []
        from unittest.mock import patch
        with patch("builtins.print", lambda *a, **kw: captured.append(
                kw.get("sep", " ").join(str(x) for x in a))):
            mod._warn_if_silent_credential_failure(
                plans=[],
                skipped=[
                    (1450, "account_closed_pnl_for_trade returned None"),
                    (1454, "account_closed_pnl_for_trade returned None"),
                    (1465, "account_closed_pnl_for_trade returned None"),
                ],
            )
        full = "\n".join(captured)
        assert "WARNING" in full
        assert "CREDENTIALS NOT REACHABLE" in full
        assert "load_runtime_secrets" in full

    def test_no_warning_when_recoveries_succeeded(self, tmp_path):
        import importlib.util
        path = _REPO_ROOT / "scripts" / "ops" / "backfill_orphan_pnl.py"
        spec = importlib.util.spec_from_file_location(
            "backfill_orphan_pnl_under_test2", path,
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        captured: list[str] = []
        from unittest.mock import patch
        with patch("builtins.print", lambda *a, **kw: captured.append(
                kw.get("sep", " ").join(str(x) for x in a))):
            mod._warn_if_silent_credential_failure(
                plans=[(1465, {"exit_price": 80000.0, "pnl": 1.5})],
                skipped=[
                    (1450, "account_closed_pnl_for_trade returned None"),
                ],
            )
        assert captured == []  # silent — recoveries happened so creds work

    def test_no_warning_when_mixed_skip_reasons(self, tmp_path):
        """If some skips have different reasons (e.g. unparseable timestamps,
        missing account cfg), the silent-creds signature doesn't apply —
        the bug class is something else and we shouldn't false-positive."""
        import importlib.util
        path = _REPO_ROOT / "scripts" / "ops" / "backfill_orphan_pnl.py"
        spec = importlib.util.spec_from_file_location(
            "backfill_orphan_pnl_under_test3", path,
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        captured: list[str] = []
        from unittest.mock import patch
        with patch("builtins.print", lambda *a, **kw: captured.append(
                kw.get("sep", " ").join(str(x) for x in a))):
            mod._warn_if_silent_credential_failure(
                plans=[],
                skipped=[
                    (1450, "account_closed_pnl_for_trade returned None"),
                    (1454, "unparseable created_at='garbage'"),
                ],
            )
        assert captured == []
