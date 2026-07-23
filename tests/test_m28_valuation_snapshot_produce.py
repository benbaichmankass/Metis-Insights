"""M28 P1 — tests for the off-VM FRED valuation-snapshot producer + the
committed-path reader fallback.

No network: the producer's FRED fetch is exercised through an **injected fake
``urlopen``**, so these tests never touch fred.stlouisfed.org and never depend on
``ICT_OFFVM_BUILD_HOST``.
"""

from __future__ import annotations

import importlib.util
import os

from src.units.strategies.macro_thesis import valuation_store
from src.units.strategies.macro_thesis.valuation_store import (
    read_latest_snapshots,
    read_snapshot_records,
)

# Load the producer script by path (it lives under scripts/, not an importable
# package) — the same shape as other script-under-test loads in this suite.
_PRODUCER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "macro", "valuation_snapshot_produce.py",
)
_spec = importlib.util.spec_from_file_location("valuation_snapshot_produce", _PRODUCER_PATH)
producer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(producer)


# A FRED fredgraph.csv body with real history — enough points that value_read can
# return a cheap/rich label rather than honest-null "unknown".
_FRED_CSV = (
    "DATE,VALUE\n"
    "2026-01-01,1.0\n"
    "2026-02-01,1.5\n"
    "2026-03-01,2.0\n"
    "2026-04-01,2.5\n"
    "2026-05-01,3.0\n"
)


class _FakeResp:
    def __init__(self, text: str):
        self._text = text

    def read(self):
        return self._text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(url, timeout=None):  # matches urllib.request.urlopen(url, timeout=)
    return _FakeResp(_FRED_CSV)


# --------------------------------------------------------------------------
# produce()
# --------------------------------------------------------------------------

def test_produce_writes_rows_to_explicit_path(tmp_path):
    out = tmp_path / "snap.jsonl"
    summary = producer.produce(out_path=out, observed_at="2026-07-23T12:00:00Z",
                               urlopen=_fake_urlopen)
    assert summary["rows"] > 0
    assert summary["written"] == summary["rows"]
    assert summary["observed_at"] == "2026-07-23T12:00:00Z"
    # The rows actually landed and read back point-in-time.
    recs = read_snapshot_records(path=out)
    assert len(recs) == summary["rows"]
    # Every row carries the run's observed_at (point-in-time stamp).
    assert all(r.get("observed_at") == "2026-07-23T12:00:00Z" for r in recs)


def test_produce_yields_a_known_read_from_history(tmp_path):
    out = tmp_path / "snap.jsonl"
    summary = producer.produce(out_path=out, urlopen=_fake_urlopen)
    # DFII10-driven real_yield_10y has a 5-point history via the fake ⇒ at least
    # one non-"unknown" cheap/rich read (not everything honest-nulls).
    assert summary["known_reads"] >= 1
    latest = read_latest_snapshots(path=out)
    tlt = latest.get(("TLT", "real_yield_10y"))
    assert tlt is not None and tlt["value"] == 3.0  # latest fetched value


def test_produce_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "snap.jsonl"
    summary = producer.produce(out_path=out, dry_run=True, urlopen=_fake_urlopen)
    assert summary["rows"] > 0
    assert summary["written"] == 0
    assert not out.exists()


def test_produce_is_append_only_point_in_time(tmp_path):
    out = tmp_path / "snap.jsonl"
    producer.produce(out_path=out, observed_at="2026-07-23T00:00:00Z", urlopen=_fake_urlopen)
    n1 = len(read_snapshot_records(path=out))
    # A second run at a later instant APPENDS (revision = new line, not overwrite).
    producer.produce(out_path=out, observed_at="2026-07-24T00:00:00Z", urlopen=_fake_urlopen)
    n2 = len(read_snapshot_records(path=out))
    assert n2 == 2 * n1
    latest = read_latest_snapshots(path=out)
    # The live "latest" read is the newest observed_at per key.
    assert latest[("TLT", "real_yield_10y")]["observed_at"] == "2026-07-24T00:00:00Z"


def test_produce_empty_config_is_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(producer, "load_valuation_config", lambda p=None: {})
    summary = producer.produce(out_path=tmp_path / "snap.jsonl", urlopen=_fake_urlopen)
    assert summary["error"] == "empty_config"
    assert summary["rows"] == 0


# --------------------------------------------------------------------------
# reader fallback — read default prefers the committed comms/macro file
# --------------------------------------------------------------------------

def test_read_prefers_committed_path_when_it_exists(tmp_path, monkeypatch):
    committed = tmp_path / "comms_macro.jsonl"
    valuation_store.write_snapshots(
        [{"symbol": "TLT", "metric": "m", "value": 9.9, "observed_at": "t"}],
        path=committed,
    )
    # committed exists ⇒ the read default resolves to it, no explicit path passed.
    monkeypatch.setattr(valuation_store, "committed_snapshot_log_path", lambda: committed)
    recs = read_snapshot_records()  # path=None
    assert len(recs) == 1 and recs[0]["value"] == 9.9


def test_read_falls_back_to_runtime_logs_when_committed_absent(tmp_path, monkeypatch):
    missing = tmp_path / "does_not_exist.jsonl"
    runtime = tmp_path / "runtime.jsonl"
    valuation_store.write_snapshots(
        [{"symbol": "GLD", "metric": "m", "value": 1.1, "observed_at": "t"}],
        path=runtime,
    )
    monkeypatch.setattr(valuation_store, "committed_snapshot_log_path", lambda: missing)
    monkeypatch.setattr(valuation_store, "snapshot_log_path", lambda path=None: runtime)
    recs = read_snapshot_records()  # path=None ⇒ committed missing ⇒ runtime default
    assert len(recs) == 1 and recs[0]["value"] == 1.1


def test_explicit_read_path_overrides_committed(tmp_path, monkeypatch):
    committed = tmp_path / "committed.jsonl"
    explicit = tmp_path / "explicit.jsonl"
    valuation_store.write_snapshots([{"symbol": "A", "metric": "m", "value": 1.0, "observed_at": "t"}], path=committed)
    valuation_store.write_snapshots([{"symbol": "B", "metric": "m", "value": 2.0, "observed_at": "t"}], path=explicit)
    monkeypatch.setattr(valuation_store, "committed_snapshot_log_path", lambda: committed)
    recs = read_snapshot_records(path=explicit)  # explicit wins
    assert len(recs) == 1 and recs[0]["symbol"] == "B"


def test_committed_path_resolves_under_comms_macro():
    p = valuation_store.committed_snapshot_log_path()
    assert p is not None
    assert p.parts[-3:] == ("comms", "macro", "valuation_snapshots.jsonl")
