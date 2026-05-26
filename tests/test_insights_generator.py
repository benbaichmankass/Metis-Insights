"""Tests for the AI Analyst generator (M13 S1 / PR C).

The router is cache-only; the generator is the only process that
calls Anthropic. These tests cover:

- ``INSIGHTS_ENABLED=0`` short-circuits cleanly (kill switch).
- Monthly budget gate skips the call + writes a ``budget_skipped``
  usage row + leaves the last-good cache untouched.
- Successful call writes the cache atomically, appends history, and
  records the ``ok`` usage row with the right token counts.
- Anthropic exception → no cache write, no history row, ``error``
  usage row.
- The model output is parsed; fenced-block wrappers are stripped.
- The static system prompt carries the prompt-caching marker
  (``cache_control: ephemeral``) so the cost model holds.
- Cost estimate is the sum of input + output (cached + uncached)
  per the public price table.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime.insights import generator, prompts, usage


@pytest.fixture
def isolated_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """Point runtime_logs + the trade-journal DB at tmp paths.

    Each test gets a fresh DB (so the lazy schema bootstrap creates
    both insights_usage + insights_history tables on first connect)
    and a fresh cache dir.
    """
    runtime_logs = tmp_path / "runtime_logs"
    runtime_logs.mkdir()
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(runtime_logs))
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    monkeypatch.setenv("INSIGHTS_ENABLED", "1")
    monkeypatch.setenv("INSIGHTS_MONTHLY_BUDGET_USD", "5.00")
    return {
        "runtime_logs": runtime_logs,
        "db": db_path,
        "insights_dir": runtime_logs / "insights",
    }


def _ok_response(text: str = '{"summary_md":"Two trades, both small wins (id 1, 2). No anomalies.","grade":"good","signals":[]}'):
    """Build a fake-anthropic response dict the generator expects."""
    return {
        "text": text,
        "input_tokens": 6000,
        "output_tokens": 80,
        "cache_creation_input_tokens": 5500,
        "cache_read_input_tokens": 0,
    }


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


def test_generator_disabled_returns_none_and_writes_nothing(
    isolated_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSIGHTS_ENABLED", "0")
    calls: list[tuple] = []

    def boom(*a, **kw):
        calls.append(a)
        raise AssertionError("Anthropic must not be called when disabled")

    result = generator.generate("summary", anthropic_call=boom)
    assert result is None
    assert calls == []
    # Cache dir should be empty.
    assert not list(isolated_dirs["insights_dir"].glob("*.json")) \
        if isolated_dirs["insights_dir"].exists() else True


# ---------------------------------------------------------------------------
# Successful call — cache written, history + usage rows appended
# ---------------------------------------------------------------------------


def test_successful_summary_writes_cache_history_and_usage(
    isolated_dirs: dict[str, Path],
) -> None:
    captured: dict = {}

    def fake_anthropic(model_id, system_blocks, user_text):
        captured["model_id"] = model_id
        captured["system_blocks"] = system_blocks
        captured["user_text"] = user_text
        return _ok_response()

    payload = generator.generate("summary", anthropic_call=fake_anthropic)

    assert payload is not None
    assert payload["grade"] == "good"
    assert "Two trades" in payload["summary_md"]
    assert payload["model_id"] == "claude-haiku-4-5-20251001"
    assert payload["generated_at"]  # iso timestamp set
    # Cache file is on disk.
    cache_path = isolated_dirs["insights_dir"] / "summary.json"
    assert cache_path.exists()
    on_disk = json.loads(cache_path.read_text())
    assert on_disk["summary_md"] == payload["summary_md"]

    # The static system prompt carries the prompt-caching marker —
    # this is load-bearing for the cost model.
    blocks = captured["system_blocks"]
    assert any(
        b.get("cache_control", {}).get("type") == "ephemeral" for b in blocks
    ), "static system block missing cache_control marker"

    # History + usage tables populated.
    from src.runtime.insights import history as history_mod

    hist = history_mod.recent_history("summary", hours=1, limit=10)
    assert len(hist) == 1
    assert hist[0]["model_id"] == "claude-haiku-4-5-20251001"
    assert hist[0]["grade"] == "good"
    assert hist[0]["payload"]["summary_md"] == payload["summary_md"]

    summary = usage.summarize_usage()
    assert summary["current_month_calls"] == 1
    assert summary["current_month_tokens"] == 6000 + 80
    assert summary["current_month_usd"] > 0
    assert summary["table_present"] is True


def test_strategy_endpoint_writes_per_strategy_cache_file(
    isolated_dirs: dict[str, Path],
) -> None:
    payload = generator.generate(
        "strategy",
        strategy_name="vwap",
        anthropic_call=lambda m, s, u: _ok_response(
            '{"summary_md":"vwap is quiet, 0 fills.","grade":"mixed","signals":[]}'
        ),
    )
    assert payload is not None
    cache_path = isolated_dirs["insights_dir"] / "strategy_vwap.json"
    assert cache_path.exists()
    assert "vwap is quiet" in cache_path.read_text()


def test_strategy_endpoint_requires_strategy_name(
    isolated_dirs: dict[str, Path],
) -> None:
    with pytest.raises(ValueError, match="strategy_name"):
        generator.generate("strategy", anthropic_call=lambda *a: _ok_response())


# ---------------------------------------------------------------------------
# Output parsing — fenced blocks stripped, malformed → text-in-summary
# ---------------------------------------------------------------------------


def test_generator_strips_fenced_json(
    isolated_dirs: dict[str, Path],
) -> None:
    fenced = (
        '```json\n'
        '{"summary_md":"fenced ok","grade":"good","signals":[]}\n'
        '```'
    )
    payload = generator.generate(
        "summary", anthropic_call=lambda *a: _ok_response(fenced)
    )
    assert payload["summary_md"] == "fenced ok"


def test_generator_malformed_output_falls_back_to_text(
    isolated_dirs: dict[str, Path],
) -> None:
    # Not JSON at all — generator still writes the cache rather than
    # erroring out on a bad model response.
    payload = generator.generate(
        "summary",
        anthropic_call=lambda *a: _ok_response("just some markdown, no JSON"),
    )
    assert payload is not None
    assert "just some markdown" in payload["summary_md"]
    assert payload["grade"] == "good"


# ---------------------------------------------------------------------------
# Anthropic exception → error usage row, no cache, no history
# ---------------------------------------------------------------------------


def test_anthropic_failure_leaves_cache_untouched_records_error(
    isolated_dirs: dict[str, Path],
) -> None:
    # First a successful call to populate the cache file.
    generator.generate("summary", anthropic_call=lambda *a: _ok_response())
    cache_path = isolated_dirs["insights_dir"] / "summary.json"
    last_good_mtime = cache_path.stat().st_mtime
    last_good_text = cache_path.read_text()

    # Second call fails.
    def boom(*a):
        raise RuntimeError("anthropic 503")

    result = generator.generate("summary", anthropic_call=boom)
    assert result is None
    # Cache untouched (same mtime + same content).
    assert cache_path.stat().st_mtime == last_good_mtime
    assert cache_path.read_text() == last_good_text

    # Usage table has the error row recorded.
    s = usage.summarize_usage()
    # Two calls total — one ok + one error. Tokens accumulate from the
    # ok call only; the error row was logged with zeros.
    assert s["current_month_calls"] == 2
    assert s["current_month_tokens"] == 6000 + 80


# ---------------------------------------------------------------------------
# Budget gate
# ---------------------------------------------------------------------------


def test_budget_exhausted_skips_call_writes_budget_skipped_row(
    isolated_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tighten the budget so even a near-zero spend trips it.
    monkeypatch.setenv("INSIGHTS_MONTHLY_BUDGET_USD", "0.000001")

    def boom(*a):
        raise AssertionError("Anthropic must not be called when over budget")

    payload = generator.generate("summary", anthropic_call=boom)
    assert payload is None
    # Usage table has the budget_skipped row.
    s = usage.summarize_usage()
    assert s["current_month_calls"] == 1
    assert s["current_month_tokens"] == 0


def test_budget_recovers_when_env_raised(
    isolated_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set tight, observe skip.
    monkeypatch.setenv("INSIGHTS_MONTHLY_BUDGET_USD", "0.000001")
    generator.generate("summary", anthropic_call=lambda *a: _ok_response())
    # Loosen, observe a real call.
    monkeypatch.setenv("INSIGHTS_MONTHLY_BUDGET_USD", "10.00")
    payload = generator.generate(
        "summary", anthropic_call=lambda *a: _ok_response()
    )
    assert payload is not None
    assert (isolated_dirs["insights_dir"] / "summary.json").exists()


# ---------------------------------------------------------------------------
# Cost estimate sanity
# ---------------------------------------------------------------------------


def test_cost_estimate_matches_price_table() -> None:
    # 1M input + 1M output on Haiku → $1 + $5 = $6.
    cost = usage.estimate_cost_usd(
        "claude-haiku-4-5-20251001",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == pytest.approx(6.00, rel=1e-6)

    # 1M cached read on Sonnet → $0.30.
    cost = usage.estimate_cost_usd(
        "claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=1_000_000,
    )
    assert cost == pytest.approx(0.30, rel=1e-6)


def test_unknown_model_falls_back_to_sonnet_pricing() -> None:
    # Conservative: unknown models cost no LESS than Sonnet.
    sonnet = usage.estimate_cost_usd(
        "claude-sonnet-4-6", input_tokens=1000, output_tokens=1000
    )
    unknown = usage.estimate_cost_usd(
        "claude-future-model-9000", input_tokens=1000, output_tokens=1000
    )
    assert unknown == sonnet


# ---------------------------------------------------------------------------
# Prompt structure (cache_control marker)
# ---------------------------------------------------------------------------


def test_every_endpoint_prompt_marks_static_block_as_cacheable() -> None:
    """If any of these regress, the cost model breaks silently."""
    for fn in (
        prompts.summary_prompt,
        lambda d: prompts.recent_prompt(d, 20),
        lambda d: prompts.strategy_prompt("vwap", d),
        prompts.health_prompt,
    ):
        sys_blocks, _ = fn({"window": {}, "row_counts": {}, "rows": {}})
        assert sys_blocks, "prompt produced empty system blocks"
        assert any(
            b.get("cache_control", {}).get("type") == "ephemeral"
            for b in sys_blocks
        ), f"prompt builder {fn} missing cache_control marker"
