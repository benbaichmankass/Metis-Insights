"""Tests for src/utils/json_notes.dump_capped — the safe replacement for the
`json.dumps(payload)[:N]` truncation footgun (BL-20260619).

Core guarantees under test:
  1. Output ALWAYS parses as valid JSON (never a half-token).
  2. Output length is <= max_len.
  3. Short payloads pass through byte-identical to json.dumps.
  4. Protected keys (closed_at, ...) survive trimming.
  5. The lossy path is marked with _truncated: true.
"""
from __future__ import annotations

import json

import pytest

from src.utils.json_notes import dump_capped, sanitize_nonfinite


def test_short_payload_passthrough():
    obj = {"closed_at": "2026-06-19T10:00:00+00:00", "closed_by": "reconciler"}
    out = dump_capped(obj, 2000)
    assert json.loads(out) == obj
    # Identical to a plain dumps when within budget.
    assert out == json.dumps(obj, ensure_ascii=False, default=str)


def test_oversized_signal_logic_stays_valid_and_capped():
    obj = {
        "closed_at": "2026-06-19T10:00:00+00:00",
        "trade_id": "abc-123",
        "signal_logic": "x" * 5000,  # the long field that used to get sliced
    }
    out = dump_capped(obj, 500)
    parsed = json.loads(out)          # (1) valid JSON
    assert len(out) <= 500            # (2) within budget
    assert parsed["_truncated"] is True
    # (4) protected keys survive verbatim.
    assert parsed["closed_at"] == "2026-06-19T10:00:00+00:00"
    assert parsed["trade_id"] == "abc-123"
    # The long field is shrunk, not dropped, and marked with an ellipsis.
    assert parsed["signal_logic"].endswith("…")
    assert len(parsed["signal_logic"]) < 5000


def test_naive_slice_would_have_been_invalid():
    """Demonstrate the bug this fixes: the naive slice is invalid JSON,
    dump_capped is not."""
    obj = {"closed_at": "2026-06-19T10:00:00+00:00", "reason": "y" * 1000}
    naive = json.dumps(obj, ensure_ascii=False)[:200]
    with pytest.raises(ValueError):
        json.loads(naive)             # the old pattern → malformed JSON
    out = dump_capped(obj, 200)
    json.loads(out)                   # the new path → always parses
    assert len(out) <= 200


def test_multiple_long_strings_all_trimmed():
    obj = {
        "closed_at": "2026-06-19T10:00:00+00:00",
        "a": "a" * 2000,
        "b": "b" * 2000,
    }
    out = dump_capped(obj, 300)
    parsed = json.loads(out)
    assert len(out) <= 300
    assert parsed["closed_at"] == "2026-06-19T10:00:00+00:00"
    assert parsed["_truncated"] is True


def test_protected_keys_preserved_even_when_tiny_budget():
    obj = {
        "closed_at": "2026-06-19T10:00:00+00:00",
        "pnl_source": "local_compute",
        "junk": "z" * 4000,
    }
    # Budget big enough for the protected set + envelope, but not the junk.
    out = dump_capped(obj, 120)
    parsed = json.loads(out)
    assert len(out) <= 120
    assert parsed["closed_at"] == "2026-06-19T10:00:00+00:00"
    assert parsed["pnl_source"] == "local_compute"
    assert parsed["_truncated"] is True


def test_barest_marker_when_budget_below_protected_set():
    obj = {"closed_at": "2026-06-19T10:00:00+00:00", "big": "q" * 999}
    out = dump_capped(obj, 20)  # too small even for closed_at
    parsed = json.loads(out)
    assert len(out) <= 20
    assert parsed == {"_truncated": True}


def test_non_dict_payload_wrapped_validly():
    out = dump_capped(["item"] * 1000, 100)
    parsed = json.loads(out)
    assert len(out) <= 100
    assert parsed["_truncated"] is True
    assert "_repr" in parsed


def test_non_string_bloat_falls_back_to_protected_envelope():
    # A huge nested list under an unprotected key can't be string-trimmed;
    # the fallback keeps the protected key and drops the bloat.
    obj = {
        "closed_at": "2026-06-19T10:00:00+00:00",
        "rows": list(range(1000)),
    }
    out = dump_capped(obj, 80)
    parsed = json.loads(out)
    assert len(out) <= 80
    assert parsed["closed_at"] == "2026-06-19T10:00:00+00:00"
    assert parsed["_truncated"] is True
    assert "rows" not in parsed


def test_ensure_ascii_passthrough_for_unicode():
    obj = {"closed_at": "2026-06-19T10:00:00+00:00", "note": "café " + "n" * 1000}
    out = dump_capped(obj, 200, ensure_ascii=True)
    parsed = json.loads(out)
    assert len(out) <= 200
    assert parsed["_truncated"] is True


def test_nonfinite_floats_become_null_and_stay_valid():
    """The other json.dumps footgun: NaN/Infinity/-Infinity are emitted as bare
    tokens (invalid JSON, json_valid=0). dump_capped maps them to null — the
    BL-20260709 root cause (a std_dev/z-score with a zero denominator)."""
    obj = {
        "strategy_name": "vwap",
        "std_dev": 0.0,          # a finite zero survives as-is
        "deviation": float("nan"),
        "up": float("inf"),
        "down": float("-inf"),
    }
    out = dump_capped(obj, 2000)
    # Demonstrate the footgun: the old json.dumps default emits the bare tokens
    # NaN/Infinity (Python's own json.loads is lenient and accepts them, but a
    # STRICT parser — and sqlite json_valid() — rejects them). dump_capped must
    # never emit those tokens.
    assert "NaN" in json.dumps(obj) and "Infinity" in json.dumps(obj)
    assert "NaN" not in out and "Infinity" not in out
    parsed = json.loads(out)          # dump_capped output ALWAYS parses
    assert parsed["strategy_name"] == "vwap"
    assert parsed["std_dev"] == 0.0
    assert parsed["deviation"] is None
    assert parsed["up"] is None
    assert parsed["down"] is None
    # strict=True (the json_valid() equivalent) also accepts it.
    assert json.loads(out) == parsed


def test_nonfinite_nested_in_list_and_dict():
    obj = {"rows": [{"z": float("nan")}, {"z": 1.5}], "top": float("inf")}
    parsed = json.loads(dump_capped(obj, 2000))
    assert parsed["rows"][0]["z"] is None
    assert parsed["rows"][1]["z"] == 1.5
    assert parsed["top"] is None


def test_sanitize_nonfinite_is_noop_on_finite_data():
    obj = {"a": 1, "b": 2.5, "c": "x", "d": [1, 2, {"e": 3.0}], "f": True, "g": None}
    # Structure + values identical; only the container types are rebuilt.
    assert sanitize_nonfinite(obj) == obj
    # And the serialization is byte-identical to a plain dumps (passthrough).
    assert dump_capped(obj, 2000) == json.dumps(obj, ensure_ascii=False, default=str)
