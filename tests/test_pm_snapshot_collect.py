"""Offline tests for scripts/macro/pm_snapshot_collect.py (no network)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "macro"))
import pm_snapshot_collect as pm  # noqa: E402

NOW = "2026-10-08T12:00:00Z"
CFG = {
    "max_markets_per_series": 2,
    "kalshi": {"base_url": "https://k.test", "series": {"KXCPI": "cpi"}},
    "polymarket": {"gamma_url": "https://g.test",
                   "queries": [{"q": "fed decision", "topic": "fed_rate_decision", "slug_prefix": "fed-decision-in-"}]},
}
K = {"markets": [
    {"ticker": "B", "title": "b", "close_time": "2026-11-01", "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.40",
     "last_price_dollars": "0.35", "volume_24h_fp": "5", "open_interest_fp": "9"},
    {"ticker": "A", "title": "a", "close_time": "2026-10-01", "yes_bid_dollars": "0.10", "yes_ask_dollars": "0.20"},
    {"ticker": "C", "title": "c", "close_time": "2026-12-01"},
]}
P = {"events": [
    {"slug": "fed-decision-in-october-1", "closed": False, "markets": [
        {"conditionId": "0x1", "question": "q", "endDate": "2026-10-28", "outcomes": '["Yes","No"]',
         "outcomePrices": '["0.8","0.2"]', "bestBid": 0.79, "bestAsk": 0.81},
        {"conditionId": "0x2", "closed": True, "outcomes": "[]", "outcomePrices": "[]"}]},
    {"slug": "unrelated-1", "markets": [{"conditionId": "0x3"}]},
    {"slug": "fed-decision-in-old", "closed": True, "markets": [{"conditionId": "0x4"}]},
]}


def fake(url):
    if "k.test" in url:
        return K
    if "g.test" in url:
        return P
    raise AssertionError(url)


def test_collect_shapes_and_filters():
    recs = pm.collect(CFG, fake, NOW)
    k = [r for r in recs if r["source"] == "kalshi"]
    assert [r["market_id"] for r in k] == ["A", "B"]  # nearest close first, capped at 2
    assert k[0]["implied_prob"] == 0.15 and k[1]["implied_prob"] == 0.35
    p = [r for r in recs if r["source"] == "polymarket"]
    assert [r["market_id"] for r in p] == ["0x1"] and p[0]["implied_prob"] == 0.8
    assert all(r["collected_at_utc"] == NOW for r in recs)


def test_hash_is_content_hash_and_detects_tamper():
    r = pm.collect(CFG, fake, NOW)[0]
    assert r["content_hash"] == pm.content_hash(r)
    r["implied_prob"] = 0.99
    assert r["content_hash"] != pm.content_hash(r)


def test_append_only_never_overwrites(tmp_path):
    recs = pm.collect(CFG, fake, NOW)
    path = pm.append_snapshots(recs, tmp_path, NOW)
    first = path.read_text()
    pm.append_snapshots(recs, tmp_path, "2026-10-08T18:00:00Z")
    assert path.read_text().startswith(first)  # past lines byte-identical
    assert len(path.read_text().splitlines()) == 2 * len(recs)
    assert all(json.loads(ln)["content_hash"] for ln in path.read_text().splitlines())


def test_one_source_failing_keeps_the_other():
    def f(url):
        if "k.test" in url:
            raise OSError("down")
        return fake(url)
    recs = pm.collect(CFG, f, NOW)
    assert recs and {r["source"] for r in recs} == {"polymarket"}


def test_network_refused_without_offvm_env(monkeypatch):
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    with pytest.raises(RuntimeError):
        pm.http_get_json("https://example.invalid")


def test_main_writes_receipt_and_fails_when_nothing_collected(tmp_path, monkeypatch):
    cfgp = tmp_path / "c.yaml"
    import yaml
    cfgp.write_text(yaml.safe_dump(CFG))
    monkeypatch.setattr(pm, "http_get_json", fake)
    assert pm.main(["--config", str(cfgp), "--out-dir", str(tmp_path / "o")]) == 0
    assert json.loads((tmp_path / "o" / "LATEST.json").read_text())["rows"] == 3
    monkeypatch.setattr(pm, "http_get_json", lambda u: (_ for _ in ()).throw(OSError("down")))
    assert pm.main(["--config", str(cfgp), "--out-dir", str(tmp_path / "o2")]) == 1
