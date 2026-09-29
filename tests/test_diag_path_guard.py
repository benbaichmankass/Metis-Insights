"""FIX-SA-07 — the diag relay refuses `..` traversal and keeps every documented path.

The workflow's resolver is inline YAML, so these tests EXTRACT and EXECUTE the
real step scripts (not a copy): a regression in the workflow is a regression
here.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "vm-diag-snapshot.yml"

sys.path.insert(0, str(ROOT))
from scripts.ops.diag_path_guard import traversal_reason  # noqa: E402

TRAVERSAL = [
    "..",
    "../bot/x",
    "status/../../bot/x",
    "api/bot/../diag/x",
    "status?x=..",
    "a/%2e%2e/b",
    "a/%2E%2E/b",
    "a/%2e./b",
    "a/.%2E/b",
    "a/%252e%252e/b",
    "a/%25252e%25252e/b",
    "a%2f..%2fb",
    "a%2Fb",
    "a%5cb",
    "a\\..\\b",
    "/api/diag/../../bot/x",
    "journalctl?unit=%2e%2e",
]

LEGIT = [
    "snapshot?limit=200",
    "audit?limit=600",
    "journal?table=trades&limit=5",
    "journalctl?unit=ict-trader-live.service&since=2026-09-29T00%3A00%3A00",
    "log_file?name=exit_loop_health",
    "bybit_raw_closed_pnl?account_id=X&symbol=S&start_ms=1&end_ms=2",
    "api/bot/health/history?hours=24",
    "api/bot/db/table/trades",
    "version.json",  # dot inside a segment is fine
    "a..b",          # `..` inside a segment is not a traversal segment
    "status",
]


@pytest.mark.parametrize("p", TRAVERSAL)
def test_traversal_refused(p):
    assert traversal_reason(p), p


@pytest.mark.parametrize("p", LEGIT)
def test_legit_allowed(p):
    assert traversal_reason(p) is None, p


def _steps():
    doc = yaml.safe_load(WF.read_text())
    return {s.get("name", ""): s for s in doc["jobs"]["snapshot"]["steps"]}


def _run_multi(title: str, body: str = "") -> tuple[int, str, str]:
    step = _steps()["Resolve and validate diag path(s)"]["run"]
    # yaml.safe_load has already dedented the block scalar.
    code = re.search(r"python3 - <<'PY'\n(.*?)\nPY\n", step, re.S).group(1)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out, env_f = Path(td, "out"), Path(td, "env")
        out.touch()
        env_f.touch()
        r = subprocess.run(
            [sys.executable, "-c", code], cwd=td, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT), "ISSUE_TITLE": title,
                 "ISSUE_BODY": body, "GITHUB_OUTPUT": str(out), "GITHUB_ENV": str(env_f)},
        )
        tsv = Path(td, "diag_paths.tsv")
        return r.returncode, env_f.read_text(), tsv.read_text() if tsv.exists() else ""


def _run_dispatch(path: str) -> tuple[int, str]:
    step = _steps()["Resolve and validate diag path"]["run"]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        env_f = Path(td, "env")
        env_f.touch()
        r = subprocess.run(
            ["bash", "-c", step], cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "DISPATCH_PATH": path, "GITHUB_ENV": str(env_f)},
        )
        return r.returncode, env_f.read_text()


@pytest.mark.parametrize("p", TRAVERSAL)
def test_multi_resolver_refuses_traversal(p):
    rc, env, tsv = _run_multi(p)
    assert rc == 1 and "diag_reject_reason=" in env and tsv == "", (p, rc, env)


@pytest.mark.parametrize("p", TRAVERSAL)
def test_multi_resolver_refuses_traversal_in_body_batch(p):
    rc, env, _ = _run_multi("[diag-request] status", f"status\n{p}\nversion")
    assert rc == 1 and "traversal" in env


@pytest.mark.parametrize("p", TRAVERSAL)
def test_dispatch_resolver_refuses_traversal(p):
    rc, env = _run_dispatch(p)
    assert rc == 1 and "diag_reject_reason=" in env, (p, rc, env)


@pytest.mark.parametrize("p", ["status", "snapshot?limit=200", "log_file?name=exit_loop_health",
                               "api/bot/performance"])
def test_resolvers_still_accept_normal_paths(p):
    rc, _, tsv = _run_multi(p)
    assert rc == 0 and tsv
    rc, env = _run_dispatch(p)
    assert rc == 0 and "diag_path_safe=" in env


# ── every path the skills/docs actually tell sessions to request ────────────
_DOC_SOURCES = [".claude", "CLAUDE.md", "docs/claude/diag-relay.md", "docs/reference",
                "docs/runbooks", "scripts/ops/diag_fetch.sh"]
_PAT = re.compile(r"/api/diag/([^\s`'\")<>]+)|\[diag-request\] ([^\s`'\")<>]+)"
                  r"|diag_fetch\.sh ([^\s`'\")<>]+)|\b(api/bot/[A-Za-z0-9_/?=&.:%-]+)")


_OLD_CHARSET = re.compile(r"^[A-Za-z0-9/?&=_.:%-]+$")


def _corpus() -> set[str]:
    found: set[str] = set()
    for src in _DOC_SOURCES:
        base = ROOT / src
        files = [base] if base.is_file() else [f for f in base.rglob("*") if f.is_file()]
        for f in files:
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            for m in _PAT.finditer(text):
                found.add(next(g for g in m.groups() if g))
    return found


def test_no_documented_relay_path_is_refused_by_the_guard():
    corpus = _corpus()
    assert len(corpus) > 100, f"corpus probe found only {len(corpus)} — probe is broken"
    # Prose placeholders like `{a\|b}` never passed the pre-existing charset
    # check either, so only paths the OLD validator accepted are in scope.
    live = {p for p in corpus if _OLD_CHARSET.match(p)}
    assert len(live) > 100, len(live)  # probe must find a real population
    refused = {p: traversal_reason(p) for p in live if traversal_reason(p)}
    assert not refused, refused


def test_documented_relay_paths_resolve_exactly_as_before():
    """Each documented path that the resolver accepted before must still be accepted.

    The `before` verdict is the same inline resolver with the guard neutralised,
    so the ONLY difference measured is FIX-SA-07's.
    """
    step = _steps()["Resolve and validate diag path(s)"]["run"]
    assert "traversal_reason(path)" in step
    corpus = sorted(_corpus())
    newly_refused = []
    for p in corpus:
        rc_new, _, _ = _run_multi(p)
        if rc_new != 0 and _OLD_CHARSET.match(p) and traversal_reason(p):
            newly_refused.append(p)
    assert not newly_refused, newly_refused
