"""Scope-guard + result-contract tests for the LLM burst-worker delegation.

The guard is the security boundary for sending repo content to a third-party
model, so it is tested adversarially: the interesting cases are the ones that
try to get something past it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.llm.delegate import envelope, run
from scripts.llm.scope_guard import classify, resolve_paths


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src/web").mkdir(parents=True)
    (tmp_path / "src/web/main.py").write_text("print('hi')\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/guide.md").write_text("# guide\n")
    (tmp_path / "comms").mkdir()
    (tmp_path / "comms/report.json").write_text('{"pnl": -6358}')
    (tmp_path / "config").mkdir()
    (tmp_path / "config/accounts.yaml").write_text("bybit_2:\n  mode: live\n")
    (tmp_path / "runtime_logs").mkdir()
    (tmp_path / "runtime_logs/audit.jsonl").write_text("{}\n")
    (tmp_path / ".env").write_text("SECRET=hunter2\n")
    (tmp_path / "trade_journal.db").write_text("sqlite")
    return tmp_path


# --- things that must be allowed -------------------------------------------

@pytest.mark.parametrize("rel", ["src/web/main.py", "docs/guide.md"])
def test_code_and_docs_are_allowed(repo, rel):
    assert classify(rel, repo).verdict == "allowed"


# --- things that must be refused -------------------------------------------

@pytest.mark.parametrize(
    "rel",
    [
        ".env",                       # credentials
        "trade_journal.db",           # the money DB
        "comms/report.json",          # committed, but holds PnL dossiers
        "config/accounts.yaml",       # account topology
        "runtime_logs/audit.jsonl",   # live runtime data
    ],
)
def test_sensitive_paths_are_denied(repo, rel):
    v = classify(rel, repo)
    assert v.verdict == "denied", f"{rel} must be denied, got {v.verdict}: {v.reason}"


def test_public_is_not_the_test(repo):
    """comms/ and config/ are committed to a public repo and still denied.

    'Already public' is not the authorised scope — the operator authorised
    code + docs, excluding live trading data.
    """
    for rel in ("comms/report.json", "config/accounts.yaml"):
        assert classify(rel, repo).verdict == "denied"


def test_traversal_is_denied(repo):
    for rel in ("../../etc/passwd", "src/../../.env", "/etc/passwd"):
        assert classify(rel, repo).verdict == "denied"


def test_unknown_extension_defaults_to_deny(repo):
    (repo / "src/blob.bin").write_bytes(b"\x00")
    v = classify("src/blob.bin", repo)
    assert v.verdict == "denied"
    assert "default is deny" in v.reason


def test_oversized_file_is_refused_not_truncated(repo):
    big = repo / "src/big.py"
    big.write_text("x" * (300 * 1024))
    v = classify("src/big.py", repo)
    assert v.verdict == "denied" and "exceeds" in v.reason


# --- the three states must stay distinct -----------------------------------

def test_missing_is_distinct_from_denied(repo):
    """A permitted-but-absent file must not read as a policy refusal."""
    assert classify("src/nope.py", repo).verdict == "missing"
    assert classify(".env", repo).verdict == "denied"


# --- batch behaviour: fail closed ------------------------------------------

def test_one_denied_path_refuses_the_whole_batch(repo):
    _, refusal = resolve_paths(["src/web/main.py", ".env"], repo)
    assert refusal is not None and "outside the authorised scope" in refusal


def test_clean_batch_is_permitted(repo):
    verdicts, refusal = resolve_paths(["src/web/main.py", "docs/guide.md"], repo)
    assert refusal is None
    assert all(v.ok for v in verdicts)


def test_empty_batch_is_refused(repo):
    _, refusal = resolve_paths([], repo)
    assert refusal is not None


# --- result contract -------------------------------------------------------

def test_non_completed_status_always_carries_a_reason():
    for status in ("failed", "not_attempted"):
        assert envelope("t", status)["reason"]


def test_scope_refusal_never_calls_the_model(repo, monkeypatch):
    """A refused batch must be not_attempted — never a bare empty success."""
    monkeypatch.setenv("LLM_DELEGATE_API_KEY", "k")

    def explode(*a, **k):  # pragma: no cover
        raise AssertionError("model was called despite a scope refusal")

    monkeypatch.setattr("scripts.llm.delegate.call_model", explode)
    res = run({"task_id": "t", "instruction": "go", "paths": [".env"]}, repo)
    assert res["status"] == "not_attempted"
    assert "scope guard refused" in res["reason"]


def test_missing_key_is_not_attempted_not_failed(repo, monkeypatch):
    monkeypatch.delenv("LLM_DELEGATE_API_KEY", raising=False)
    res = run({"task_id": "t", "instruction": "go", "paths": ["src/web/main.py"]}, repo)
    assert res["status"] == "not_attempted"


def test_empty_completion_is_failed_not_completed(repo, monkeypatch):
    monkeypatch.setenv("LLM_DELEGATE_API_KEY", "k")
    monkeypatch.setattr(
        "scripts.llm.delegate.call_model",
        lambda *a, **k: ({"choices": [{"message": {"content": "   "}}]}, None, 1),
    )
    res = run({"task_id": "t", "instruction": "go", "paths": ["src/web/main.py"]}, repo)
    assert res["status"] == "failed", "an empty completion must never read as an answer"


def test_rate_limit_surfaces_loudly(repo, monkeypatch):
    monkeypatch.setenv("LLM_DELEGATE_API_KEY", "k")
    monkeypatch.setattr(
        "scripts.llm.delegate.call_model",
        lambda *a, **k: (None, "rate limited / quota exhausted (HTTP 429): ...", 3),
    )
    res = run({"task_id": "t", "instruction": "go", "paths": ["src/web/main.py"]}, repo)
    assert res["status"] == "failed" and "429" in res["reason"]


def test_envelope_is_json_serialisable(repo, monkeypatch):
    monkeypatch.setenv("LLM_DELEGATE_API_KEY", "k")
    monkeypatch.setattr(
        "scripts.llm.delegate.call_model",
        lambda *a, **k: ({"choices": [{"message": {"content": "ok"}}],
                          "usage": {"total_tokens": 5}}, None, 1),
    )
    res = run({"task_id": "t", "instruction": "go", "paths": ["src/web/main.py"]}, repo)
    assert res["status"] == "completed" and res["output"] == "ok"
    json.dumps(res)


def test_truncated_response_is_failed_not_completed(repo, monkeypatch):
    """A mid-sentence truncation must not be reported as a completed answer."""
    monkeypatch.setenv("LLM_DELEGATE_API_KEY", "k")
    monkeypatch.setattr(
        "scripts.llm.delegate.call_model",
        lambda *a, **k: (
            {"choices": [{"message": {"content": "partial ans"}, "finish_reason": "length"}],
             "usage": {"completion_tokens": 47, "prompt_tokens": 1817, "total_tokens": 3013}},
            None, 1,
        ),
    )
    res = run({"task_id": "t", "instruction": "go", "paths": ["src/web/main.py"]}, repo)
    assert res["status"] == "failed"
    assert "truncated" in res["reason"]
    # the partial text is preserved rather than discarded
    assert res["output"] == "partial ans"
    assert res["finish_reason"] == "length"


def test_normal_stop_is_completed_and_records_finish_reason(repo, monkeypatch):
    monkeypatch.setenv("LLM_DELEGATE_API_KEY", "k")
    monkeypatch.setattr(
        "scripts.llm.delegate.call_model",
        lambda *a, **k: ({"choices": [{"message": {"content": "full answer"},
                                       "finish_reason": "stop"}]}, None, 1),
    )
    res = run({"task_id": "t", "instruction": "go", "paths": ["src/web/main.py"]}, repo)
    assert res["status"] == "completed" and res["finish_reason"] == "stop"


def _fake_response(status, text='{"e":1}', payload=None):
    class R:
        status_code = status
        def __init__(self): self._p = payload or {}
        @property
        def text(self): return text
        def json(self): return self._p
    return R()


def test_transient_status_is_retried(monkeypatch):
    """503 is retried up to MAX_ATTEMPTS before being reported."""
    from scripts.llm import delegate as D
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        return _fake_response(503, "high demand")

    monkeypatch.setattr(D.time, "sleep", lambda *_: None)
    monkeypatch.setattr("httpx.post", post)
    payload, err, attempts = D.call_model("p", base_url="http://x", model="m",
                                          api_key="k", max_tokens=10)
    assert payload is None
    assert calls["n"] == D.MAX_ATTEMPTS and attempts == D.MAX_ATTEMPTS
    assert "after 3 attempts" in err


def test_non_retryable_status_fails_on_first_attempt(monkeypatch):
    """A 402/404 is the request being wrong — retrying burns quota and hides it."""
    from scripts.llm import delegate as D
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        return _fake_response(402, "payment required")

    monkeypatch.setattr(D.time, "sleep", lambda *_: None)
    monkeypatch.setattr("httpx.post", post)
    payload, err, attempts = D.call_model("p", base_url="http://x", model="m",
                                          api_key="k", max_tokens=10)
    assert payload is None and calls["n"] == 1 and attempts == 1
    assert "402" in err


def test_transient_then_success(monkeypatch):
    from scripts.llm import delegate as D
    seq = [_fake_response(503), _fake_response(200, payload={"choices": [{"message": {"content": "ok"}}]})]
    monkeypatch.setattr(D.time, "sleep", lambda *_: None)
    monkeypatch.setattr("httpx.post", lambda *a, **k: seq.pop(0))
    payload, err, attempts = D.call_model("p", base_url="http://x", model="m",
                                          api_key="k", max_tokens=10)
    assert err is None and attempts == 2 and payload["choices"][0]["message"]["content"] == "ok"


# --- regression: gaps found by the delegate reviewing this guard -------------
# Source: issue #9944, guard-review-006 (2026-08-18). The worker was asked to
# find paths the filter would ALLOW but that fall outside the authorised scope.
# Five of its six findings were valid; each is pinned here so the broad
# extension globs cannot come back.

@pytest.mark.parametrize(
    "rel",
    [
        "webapp/src/config/accounts.json",  # bare webapp/src/* admitted any type
        "settings.toml",                    # repo-wide *.toml
        "trades.txt",                       # repo-wide *.txt
        "connections.ini",                  # repo-wide *.ini
        "reports/pnl_summary.md",           # repo-wide *.md outside docs/
    ],
)
def test_delegate_found_gaps_are_now_denied(repo, rel):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    v = classify(rel, repo)
    assert v.verdict == "denied", f"{rel} must be denied, got {v.verdict}: {v.reason}"


@pytest.mark.parametrize(
    "rel",
    ["docs/guide.md", "README.md", "src/web/main.py", "webapp/src/lib/config.ts"],
)
def test_tightening_did_not_break_legitimate_paths(repo, rel):
    """The narrowing must not cost real code + docs — that is the point of the tool."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    assert classify(rel, repo).verdict == "allowed", f"{rel} should still be allowed"


# --- regression: coverage gaps found by the delegate (issue #9944,
#     n3-testgap-guard). All five were real gaps in this suite.

def test_resolve_paths_refuses_a_batch_containing_a_missing_file(repo):
    """classify's `missing` was tested; resolve_paths' branch for it was not."""
    _, refusal = resolve_paths(["src/web/main.py", "src/nope.py"], repo)
    assert refusal is not None and "not found" in refusal


def test_resolve_paths_enforces_the_batch_byte_ceiling(repo):
    """Each file under the per-file cap, the batch over the total cap."""
    from scripts.llm.scope_guard import MAX_FILE_BYTES, MAX_TOTAL_BYTES
    names = []
    per = MAX_FILE_BYTES - 1024
    for i in range((MAX_TOTAL_BYTES // per) + 2):
        n = f"src/big{i}.py"
        (repo / n).write_text("x" * per)
        names.append(n)
    _, refusal = resolve_paths(names, repo)
    assert refusal is not None and "MAX_TOTAL_BYTES" in refusal


def test_refusal_message_truncates_a_long_denied_list(repo):
    _, refusal = resolve_paths([f".env{i}" for i in range(15)], repo)
    assert refusal is not None and "and 5 more" in refusal


@pytest.mark.parametrize("rel", ["", "   ", "./", "."])
def test_empty_or_dot_paths_are_denied(repo, rel):
    assert classify(rel, repo).verdict == "denied"
