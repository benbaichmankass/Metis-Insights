"""The halt flag has ONE path, and every reader reports on that path.

WHY
---
The halt flag is a kill switch: while it exists, `pipeline.py` refuses to trade.
Until 2026-08-13 three modules defined it and two of them pointed somewhere else:

  * `pipeline.py`            HALT_FLAG_PATH env, default /data/bot-data/…  ← the
                             ONLY consumer that halts anything
  * `bot_config.py`          hardcoded /tmp/trader_halt.flag, env never read
  * `telegram_query_bot.py`  hardcoded /tmp/… likewise

So both operator-facing readouts could say RUNNING while the pipeline was
halted, and setting HALT_FLAG_PATH could not reconcile them.

These tests pin the property that matters — the readers and the consumer resolve
the SAME path — rather than pinning a literal, which is what let the drift open.
"""
from __future__ import annotations

import os

import pytest

from src.runtime.runtime_flags import HALT_FLAG_DEFAULT, halt_flag_path, is_halted


def test_default_is_the_pipeline_data_dir_path_not_tmp():
    """The default must be the pipeline's, and it must not be /tmp."""
    os.environ.pop("HALT_FLAG_PATH", None)
    assert halt_flag_path() == HALT_FLAG_DEFAULT
    assert not halt_flag_path().startswith("/tmp/"), (
        "the /tmp default is the drift this module exists to close")


def test_env_override_is_honoured(monkeypatch):
    monkeypatch.setenv("HALT_FLAG_PATH", "/tmp/pinned_probe.flag")
    assert halt_flag_path() == "/tmp/pinned_probe.flag"


def test_empty_env_value_falls_back_rather_than_disabling(monkeypatch):
    """An empty string must not resolve to a path that can never exist.

    `os.environ.get(k, default)` returns "" for a set-but-empty var, and
    `os.path.exists("")` is always False — i.e. a blank value would silently
    make the trader UNHALTABLE. The `or` fallback is load-bearing.
    """
    monkeypatch.setenv("HALT_FLAG_PATH", "")
    assert halt_flag_path() == HALT_FLAG_DEFAULT


def test_is_halted_tracks_the_resolved_file(monkeypatch, tmp_path):
    flag = tmp_path / "trader_halt.flag"
    monkeypatch.setenv("HALT_FLAG_PATH", str(flag))
    assert is_halted() is False
    flag.touch()
    assert is_halted() is True
    flag.unlink()
    assert is_halted() is False


def test_pipeline_and_config_endpoint_resolve_the_same_path(monkeypatch):
    """THE regression. Two readers, one path — checked, not assumed."""
    bc = pytest.importorskip(
        "src.web.api.routers.bot_config",
        reason="fastapi not installed in this environment")
    monkeypatch.setenv("HALT_FLAG_PATH", "/tmp/agreement_probe.flag")
    assert bc._resolve_halt_flag_path() == halt_flag_path()


def test_config_endpoint_reports_halted_from_the_real_flag(monkeypatch, tmp_path):
    """`trading_mode.halted` must follow the file the pipeline actually checks."""
    bc = pytest.importorskip(
        "src.web.api.routers.bot_config",
        reason="fastapi not installed in this environment")
    flag = tmp_path / "trader_halt.flag"
    monkeypatch.setenv("HALT_FLAG_PATH", str(flag))
    monkeypatch.setattr(bc, "_HALT_FLAG_PATH", None)  # no module-level override

    assert bc.build_config()["trading_mode"]["halted"] is False
    flag.touch()
    assert bc.build_config()["trading_mode"]["halted"] is True, (
        "the endpoint is looking at a different file than the pipeline")
    flag.unlink()
    assert bc.build_config()["trading_mode"]["halted"] is False


def test_no_module_hardcodes_a_tmp_halt_path():
    """Static sweep — the drift must not be reintroduced anywhere in src/.

    Reads the **AST**, not the text. A text scan flagged this very file's
    explanatory comment (which quotes the old literal to say it was wrong), and
    a guard that a comment can trip is a guard the next person deletes the
    comment to satisfy — the `new-table-wiring-guard` lesson. A `Constant` node
    is a fact about the code; a comment is prose about it, and comments are not
    AST nodes at all, so they are excluded by construction rather than by an
    exception list.

    Docstrings ARE string nodes, so a module documenting the old path in its
    docstring would still trip this — deliberately: prose that a reader could
    mistake for the live value is exactly what caused the drift.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src"
    bad = "/tmp/trader_halt.flag"
    offenders, resolver_users = [], []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "halt_flag_path" in text:
            resolver_users.append(str(path.relative_to(src)))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == bad:
                offenders.append(f"{path.relative_to(src)}:{node.lineno}")

    # A negative needs a denominator: prove the walk can find a positive.
    assert resolver_users, "probe is broken — it cannot even find the resolver"
    planted = ast.parse(f'x = "{bad}"')
    assert any(isinstance(n, ast.Constant) and n.value == bad for n in ast.walk(planted)), \
        "probe is broken — it cannot detect a planted literal"

    assert offenders == [], f"hardcoded /tmp halt path reintroduced in: {offenders}"
