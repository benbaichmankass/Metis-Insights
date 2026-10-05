"""Every harness-dispatch queue unit's data must be fetchable (RESEARCH-PIPELINE-REPAIR 2026-10-04)."""
from pathlib import Path

import yaml

from scripts.ops.fetch_backtest_corpus import CANDIDATE_PAIRS, PAIRS

QUEUE = Path(__file__).resolve().parents[1] / "research" / "queue"
FIXED_BASKET = {"xsec_momentum"}   # fixed ETF basket, symbol/timeframe ignored


def test_candidates_are_not_roster_pairs():
    assert not set(CANDIDATE_PAIRS) & set(PAIRS), "a roster pair belongs in PAIRS only"


def test_every_queued_harness_unit_has_a_declared_pair():
    known = set(PAIRS) | set(CANDIDATE_PAIRS)
    missing = []
    for f in sorted(QUEUE.glob("RQ-*.yaml")):
        u = yaml.safe_load(f.read_text())
        run = u.get("run") or {}
        if run.get("workflow") != "research-harness-dispatch.yml" or u.get("status") != "queued":
            continue
        i = run.get("inputs") or {}
        if i.get("harness") in FIXED_BASKET:
            continue
        for sym in [i.get("symbol")] + ([i.get("symbol_b")] if i.get("harness") == "pairs" else []):
            if (sym, str(i.get("timeframe"))) not in known:
                missing.append((u["id"], sym, i.get("timeframe")))
    assert missing == [], f"units the dispatch `build` job would refuse with 'no declared pair': {missing}"
