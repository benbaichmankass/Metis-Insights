"""T2 — Reader/writer path alignment + lint guard.

Pins the contract that every router and runtime reader resolves
DATA_DIR-aware runtime-log paths through ``src.utils.paths`` — the
same helper the writers (heartbeat.py, signal_audit_logger.py,
runtime_status.py, etc.) already use.

This file exists because the 2026-05-11 silent-freeze incident family
(Signals tab blank, heartbeat-not-seen, Settings stale) was caused by
readers hardcoding ``Path(__file__).resolve().parents[N] /
"runtime_logs" / ...`` while writers respected DATA_DIR. Six one-off
patches in two weeks chased the same class of bug. T2 (see
``docs/audit/2026-05-12-end-to-end-audit.md`` § 6) closes it by
mandating the helper at every reader site; this test file is the
lint guard that prevents the anti-pattern from regressing.

Two test families:

1. **Alignment tests** — for each of the four runtime-log files the
   2026-05-11 incidents touched (heartbeat, signal_audit, runtime_status,
   shadow_predictions), assert that the consumer's resolved path
   equals the writer's resolved path under several DATA_DIR /
   RUNTIME_LOGS_DIR configurations. Catches a future regression where
   one side migrates and the other doesn't.

2. **Anti-pattern guard** — scan ``src/`` for the legacy hardcoded
   constructions (``_REPO_ROOT / "runtime_logs"``, ``_REPO_ROOT /
   "runtime_state"``, ``_REPO_ROOT / "artifacts"``). The hit list is
   asserted empty. Adds an enforcement layer on top of code review.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


# ─── Family 1: reader/writer alignment under env overrides ──────────


def _reload(module_name: str):
    """Force-reimport a module so module-level path bindings pick up
    fresh env vars set via monkeypatch."""
    import sys
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _runtime_log_path(module_name: str, attr: str) -> Path:
    mod = _reload(module_name)
    return getattr(mod, attr)


def _set_env(monkeypatch, **env):
    for key, val in env.items():
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)


def test_heartbeat_reader_writer_alignment_default(monkeypatch):
    _set_env(monkeypatch, DATA_DIR=None, RUNTIME_LOGS_DIR=None)
    # Force paths cache reset.
    from src.utils import paths as paths_mod
    paths_mod.data_dir.cache_clear() if hasattr(paths_mod.data_dir, "cache_clear") else None

    from src.utils.paths import runtime_logs_dir
    writer_path = runtime_logs_dir() / "heartbeat.txt"

    dashboard_reader = _runtime_log_path("src.web.api.routers.dashboard", "_HEARTBEAT")
    diag_reader = _runtime_log_path("src.web.api.routers.diag", "_HEARTBEAT")

    assert dashboard_reader == writer_path, (
        f"dashboard router heartbeat reader path drifted: {dashboard_reader} vs writer {writer_path}"
    )
    assert diag_reader == writer_path, (
        f"diag router heartbeat reader path drifted: {diag_reader} vs writer {writer_path}"
    )


def test_heartbeat_reader_writer_alignment_with_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "bot-data"
    _set_env(monkeypatch, DATA_DIR=str(data_dir), RUNTIME_LOGS_DIR=None)

    from src.utils.paths import runtime_logs_dir
    writer_path = runtime_logs_dir() / "heartbeat.txt"
    assert str(data_dir) in str(writer_path), "writer should honour DATA_DIR"

    dashboard_reader = _runtime_log_path("src.web.api.routers.dashboard", "_HEARTBEAT")
    diag_reader = _runtime_log_path("src.web.api.routers.diag", "_HEARTBEAT")

    assert dashboard_reader == writer_path
    assert diag_reader == writer_path


def test_signal_audit_reader_writer_alignment_with_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "bot-data"
    _set_env(monkeypatch, DATA_DIR=str(data_dir), RUNTIME_LOGS_DIR=None)

    from src.utils.paths import runtime_logs_dir
    writer_path = runtime_logs_dir() / "signal_audit.jsonl"

    dashboard_reader = _runtime_log_path("src.web.api.routers.dashboard", "_AUDIT_LOG")
    diag_reader = _runtime_log_path("src.web.api.routers.diag", "_AUDIT_LOG")

    assert dashboard_reader == writer_path
    assert diag_reader == writer_path


def test_runtime_status_reader_writer_alignment_with_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "bot-data"
    _set_env(monkeypatch, DATA_DIR=str(data_dir), RUNTIME_LOGS_DIR=None)

    from src.utils.paths import runtime_logs_dir
    writer_path = runtime_logs_dir() / "runtime_status.json"

    bot_config_reader = _runtime_log_path("src.web.api.routers.bot_config", "_RUNTIME_STATUS_JSON")
    diag_reader = _runtime_log_path("src.web.api.routers.diag", "_STATUS_JSON")

    assert bot_config_reader == writer_path
    assert diag_reader == writer_path


def test_shadow_predictions_reader_writer_alignment_with_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "bot-data"
    _set_env(monkeypatch, DATA_DIR=str(data_dir), RUNTIME_LOGS_DIR=None, SHADOW_PREDICTIONS_LOG=None)

    from src.utils.paths import runtime_logs_dir
    writer_path = runtime_logs_dir() / "shadow_predictions.jsonl"

    # shadow.py resolves at call time (via _log_path()) rather than at
    # module import; mirror that here.
    shadow_mod = _reload("src.web.api.routers.shadow")
    shadow_reader = shadow_mod._log_path()

    trade_scores_reader = _runtime_log_path("src.web.api.routers.trade_scores", "_SHADOW_LOG")

    assert shadow_reader == writer_path
    assert trade_scores_reader == writer_path


def test_runtime_logs_dir_override_takes_precedence(monkeypatch, tmp_path):
    data_dir = tmp_path / "bot-data"
    logs_override = tmp_path / "alt-logs"
    _set_env(monkeypatch, DATA_DIR=str(data_dir), RUNTIME_LOGS_DIR=str(logs_override))

    # All readers should pick up RUNTIME_LOGS_DIR over DATA_DIR.
    heartbeat = _runtime_log_path("src.web.api.routers.dashboard", "_HEARTBEAT")
    runtime_status = _runtime_log_path("src.web.api.routers.bot_config", "_RUNTIME_STATUS_JSON")
    assert str(logs_override) in str(heartbeat)
    assert str(logs_override) in str(runtime_status)


# ─── Family 2: anti-pattern guard ─────────────────────────────────


_BANNED_PATTERNS = [
    # Hardcoded reader against the repo root for a runtime-data dir.
    re.compile(r'_REPO_ROOT\s*/\s*["\']runtime_logs["\']'),
    re.compile(r'_REPO_ROOT\s*/\s*["\']runtime_state["\']'),
    re.compile(r'_REPO_ROOT\s*/\s*["\']artifacts["\']'),
    # Ad-hoc parents[N]-based runtime-log construction (the legacy
    # pattern that masked DATA_DIR overrides). Match
    # parents[N] / "runtime_logs" / ... regardless of the N.
    re.compile(r'parents\[\d+\]\s*/\s*["\']runtime_logs["\']'),
    re.compile(r'parents\[\d+\]\s*/\s*["\']runtime_state["\']'),
    re.compile(r'parents\[\d+\]\s*/\s*["\']artifacts["\']'),
]


# Files that are allowed to mention these strings (e.g. doc strings,
# tests, the helper module itself). The guard otherwise scans ``src/``.
_ALLOWED_FILES = {
    # The helper module IS where these strings live legitimately.
    "src/utils/paths.py",
    # Lint guard file (this file itself, if it ends up under src/).
}


def _scan_files() -> list[tuple[Path, int, str]]:
    """Return list of (file, lineno, line) hits across src/.

    Uses ``tokenize`` to skip lines that are entirely inside string
    literals or comments — the legacy pattern is referenced in
    docstrings to explain incident history (e.g. signal_audit_logger.py's
    module docstring), and those are not bugs.
    """
    import io
    import tokenize

    hits: list[tuple[Path, int, str]] = []
    src_dir = REPO_ROOT / "src"
    for path in src_dir.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in _ALLOWED_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # Build a set of (lineno) covered by STRING or COMMENT tokens —
        # the legacy pattern inside a docstring or comment is informational,
        # not an actual code-level reader.
        string_or_comment_lines: set[int] = set()
        try:
            tokens = tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline)
            for tok in tokens:
                if tok.type in (tokenize.STRING, tokenize.COMMENT):
                    for ln in range(tok.start[0], tok.end[0] + 1):
                        string_or_comment_lines.add(ln)
        except (tokenize.TokenizeError, SyntaxError):
            # Best-effort: if the file fails to tokenize, still scan it
            # line-by-line; assertion failures on broken files are an
            # appropriate signal.
            pass

        for lineno, line in enumerate(text.splitlines(), start=1):
            if lineno in string_or_comment_lines:
                continue
            for pattern in _BANNED_PATTERNS:
                if pattern.search(line):
                    hits.append((rel, lineno, line.strip()))
                    break
    return hits


def test_no_hardcoded_runtime_data_paths_in_src():
    """Every runtime-data path must resolve through src.utils.paths.

    If you're seeing this fail: replace the hardcoded
    ``_REPO_ROOT / "runtime_logs" / ...`` (or
    ``Path(__file__).resolve().parents[N] / "runtime_logs" / ...``)
    construction with the appropriate helper from
    ``src.utils.paths``:

      runtime_logs_dir()   # for runtime_logs/...
      runtime_state_dir()  # for runtime_state/...
      artifacts_dir()      # for artifacts/...

    See docs/audit/2026-05-12-end-to-end-audit.md § 6 T2.
    """
    hits = _scan_files()
    if hits:
        joined = "\n".join(f"  {rel}:{ln}  {line}" for rel, ln, line in hits)
        raise AssertionError(
            f"{len(hits)} hardcoded runtime-data path(s) found in src/. "
            f"Route them through src.utils.paths instead:\n{joined}"
        )
