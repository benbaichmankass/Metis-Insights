"""CA-B04: session_gating.best_subset selects winning killzones in the SAME sample it
scores, 'flipping' truly-losing cells on noise. The holdout selects on the first half
and measures on the second."""
import importlib.util
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "session_gating_t", str(REPO / "scripts/research/session_gating.py"))
sg = importlib.util.module_from_spec(spec)
sys.modules["session_gating_t"] = sg
spec.loader.exec_module(sg)


def _rows(seed, n=60, mean=-0.05):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        h = int(rng.integers(0, 24))
        r = float(rng.normal(mean, 1.0))
        out.append({"entry_time": f"2025-01-{1 + i // 3:02d}T{h:02d}:00:00+00:00",
                    "gross_r": r + 0.1, "net_r": r})
    return out


def test_holdout_present_and_in_sample_flip_rate_is_inflated():
    flips_oos = evaluable = 0
    for seed in range(200):
        rows = _rows(seed)
        h = sg._holdout_subset(rows, 7.5, 0.0)
        if not h.get("evaluable"):
            continue
        evaluable += 1
        flips_oos += h["subset_second_half"]["net_total_r"] > 0 >= h["all_hours_second_half"]["net_total_r"]
    assert evaluable > 150
    # no real hour effect: out-of-sample 'flips' must be near coin-flip, not the
    # ~59% in-sample figure the audit measured.
    assert flips_oos / evaluable < 0.5


def test_holdout_unevaluable_for_tiny_samples():
    assert sg._holdout_subset(_rows(1, n=10), 7.5, 0.0)["evaluable"] is False
