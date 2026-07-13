"""Tests for the broker-truth ledger (``src/runtime/broker_truth.py``) —
the committed source of truth for an account's authoritative realized PnL
(BL-20260713-BYBIT2-PNL-UNDERRECORD). Mirrors the gpu_spend ledger contract:
best-effort read, never raises; upsert keyed by account_id.
"""
from __future__ import annotations

import json

from src.runtime import broker_truth


def _write(tmp_path, obj):
    p = tmp_path / "broker_truth_ledger.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_load_missing_is_empty(tmp_path):
    p = tmp_path / "nope.json"
    assert broker_truth.load_ledger(p) == {"schema_version": 1, "accounts": [], "updated_at": None}


def test_load_garbled_is_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    got = broker_truth.load_ledger(p)
    assert got["accounts"] == []


def test_summarize_present_and_filter(tmp_path):
    p = _write(tmp_path, {
        "schema_version": 1,
        "accounts": [
            {"account_id": "bybit_2", "realized_usd": -262.52, "fees_usd": -147.8,
             "funding_usd": -0.55, "as_of": "2026-07-13", "sub_accounts": ["MAIN", "SUB"],
             "source": "bybit_um_export_stitched", "note": "wallet-truth"},
            {"account_id": "bybit_1", "realized_usd": 12.0},
        ],
        "updated_at": "2026-07-13T19:40:00+00:00",
    })
    allsum = broker_truth.summarize_broker_truth(p)
    assert allsum["present"] is True
    assert allsum["count"] == 2
    assert allsum["updated_at"] == "2026-07-13T19:40:00+00:00"

    one = broker_truth.summarize_broker_truth(p, account_id="bybit_2")
    assert one["count"] == 1
    rec = one["accounts"][0]
    assert rec["realized_usd"] == -262.52
    assert rec["sub_accounts"] == ["MAIN", "SUB"]

    none = broker_truth.summarize_broker_truth(p, account_id="does_not_exist")
    assert none["present"] is True  # ledger has records; just none for this id
    assert none["count"] == 0


def test_summarize_skips_malformed_records(tmp_path):
    p = _write(tmp_path, {"accounts": [
        {"account_id": "ok", "realized_usd": 1.0},
        {"realized_usd": 5.0},          # no account_id → dropped
        "not-a-dict",                    # not a dict → dropped
        {"account_id": "coerce", "realized_usd": "abc"},  # bad float → null, kept
    ]})
    s = broker_truth.summarize_broker_truth(p)
    ids = {a["account_id"] for a in s["accounts"]}
    assert ids == {"ok", "coerce"}
    coerce = next(a for a in s["accounts"] if a["account_id"] == "coerce")
    assert coerce["realized_usd"] is None


def test_upsert_replaces_by_account_id(tmp_path):
    p = tmp_path / "broker_truth_ledger.json"
    broker_truth.upsert_account_truth(
        {"account_id": "bybit_2", "realized_usd": -262.52}, p, updated_at="2026-07-13T00:00:00+00:00")
    broker_truth.upsert_account_truth(
        {"account_id": "bybit_1", "realized_usd": 3.0}, p, updated_at="2026-07-13T01:00:00+00:00")
    # re-emit bybit_2 with a corrected figure → replaces, no dup
    broker_truth.upsert_account_truth(
        {"account_id": "bybit_2", "realized_usd": -270.0}, p, updated_at="2026-07-14T00:00:00+00:00")

    s = broker_truth.summarize_broker_truth(p)
    assert s["count"] == 2
    assert s["updated_at"] == "2026-07-14T00:00:00+00:00"
    b2 = broker_truth.summarize_broker_truth(p, account_id="bybit_2")["accounts"][0]
    assert b2["realized_usd"] == -270.0


def test_upsert_requires_account_id(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        broker_truth.upsert_account_truth({"realized_usd": 1.0}, tmp_path / "x.json")


def test_committed_ledger_is_valid():
    """The seeded comms/broker_truth_ledger.json parses and carries bybit_2."""
    s = broker_truth.summarize_broker_truth(account_id="bybit_2")
    assert s["present"] is True
    assert s["count"] == 1
    assert s["accounts"][0]["realized_usd"] == -262.52
