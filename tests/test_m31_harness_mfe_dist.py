"""M31 P4/P5 precondition 2 — the COMMITTED harness `mfe_r` distribution.

`PB-20260817-NO-COMMITTED-PER-TRADE-HARNESS-MFE`. Check B needs a harness
`mfe_r` distribution and a live final-MFE population. The live half is a
soak-depth problem; the harness half was simply never committed — the emit
JSONL lives nowhere and the sweep corpus holds cell aggregates only (verified:
zero `mfe`-containing keys over all 1,376 rows).

WHAT THESE PIN
--------------
1. **The end-to-end path works on a REAL sweep** — emit rows → aggregator →
   artifact → parity verdict. Each half self-tests, but a suite where both
   halves pass and the seam is untested is how the seam breaks.
2. **The refusals refuse, and the acceptance accepts.** Every refusal test is
   paired with a positive control; a tool that refuses everything would pass a
   refusal-only suite.
3. **The wrong-regime artifact cannot be committed silently.** The repo's only
   candles are BTCUSDT 1-minute, where the venue cap lands ~10x further out in
   R than the live legs. `--symbol`/`--timeframe` travel with the numbers and
   reach the consumer's report, which is what makes a mismatch visible.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO, os.path.join(_REPO, "scripts", "research")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ⚠️ `parity` is imported NORMALLY, not through `_load`. A second
# `spec_from_file_location` would build a DISTINCT module object, and then
# `dist._pct is parity._pct` compares two copies of the same source and fails
# for a reason that has nothing to do with the code under test — which is
# exactly what happened when this file first ran. The aggregator does
# `from m31_mfe_parity import _pct`, so the test must hold the module that
# import resolves to, or the identity assertion measures the test's own
# loading strategy rather than the production wiring.
import m31_mfe_parity as parity  # noqa: E402

dist = _load("m31_harness_mfe_dist", "scripts/research/m31_harness_mfe_dist.py")

CAP = 0.099


def _emit(tmp_path, rows, name="emit.jsonl"):
    p = tmp_path / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


# --- the vendored self-tests, so pytest fails when they do ---------------- #

def test_aggregator_self_test_passes():
    assert dist.self_test() == 0


def test_parity_self_test_passes():
    assert parity.self_test() == 0


# --- one definition ------------------------------------------------------- #

def test_percentiles_are_the_parity_modules_own():
    """Imported, not re-derived.

    Two definitions of p80 — one writing the artifact, one reading it — would
    drift silently and both would look correct in isolation.
    """
    assert dist._pct is parity._pct


# --- refusals, each paired with its positive control ---------------------- #

def test_capped_sweep_with_identity_is_written(tmp_path):
    """POSITIVE CONTROL for the two refusals below."""
    emit = _emit(tmp_path, [{"strategy": "leg_a", "mfe_r": 2.0},
                            {"strategy": "leg_a", "mfe_r": 4.0}])
    out = tmp_path / "d.jsonl"
    rc = dist.main(["x", "--emit", str(emit), "--symbol", "SOLUSDT",
                    "--timeframe", "4h", "--tp-cap-pct", str(CAP),
                    "--out", str(out)])
    assert rc == 0
    rec = dist.load_dist(out)["leg_a"]
    assert rec["n"] == 2 and rec["max"] == 4.0
    assert rec["symbol"] == "SOLUSDT" and rec["timeframe"] == "4h"
    assert rec["tp_cap_pct"] == CAP


def test_uncapped_sweep_is_refused_and_writes_nothing(tmp_path):
    """An uncapped book has no take-profit path, so its mfe_r is a different
    quantity. Committing it under the name Check B reads would be worse than
    the honest absence."""
    emit = _emit(tmp_path, [{"strategy": "leg_a", "mfe_r": 2.0}])
    out = tmp_path / "d.jsonl"
    rc = dist.main(["x", "--emit", str(emit), "--symbol", "S",
                    "--timeframe", "4h", "--tp-cap-pct", "0", "--out", str(out)])
    assert rc == 2
    assert not out.exists()


def test_missing_instrument_identity_is_refused(tmp_path):
    emit = _emit(tmp_path, [{"strategy": "leg_a", "mfe_r": 2.0}])
    out = tmp_path / "d.jsonl"
    rc = dist.main(["x", "--emit", str(emit), "--tp-cap-pct", str(CAP),
                    "--out", str(out)])
    assert rc == 2
    assert not out.exists()


def test_rows_without_mfe_are_counted_not_dropped(tmp_path):
    """`n` beside `rows_without_mfe` is the denominator that says whether the
    distribution covers the file."""
    emit = _emit(tmp_path, [{"strategy": "leg_a", "mfe_r": 1.0},
                            {"strategy": "leg_a"},
                            {"strategy": "leg_a", "mfe_r": None}])
    out = tmp_path / "d.jsonl"
    assert dist.main(["x", "--emit", str(emit), "--symbol", "S",
                      "--timeframe", "4h", "--tp-cap-pct", str(CAP),
                      "--out", str(out)]) == 0
    rec = dist.load_dist(out)["leg_a"]
    assert rec["n"] == 1 and rec["rows_without_mfe"] == 2


def test_a_file_with_no_usable_mfe_is_refused(tmp_path):
    """"We read rows and none carried mfe_r" must not become an empty
    distribution that reads as a measured zero."""
    emit = _emit(tmp_path, [{"strategy": "leg_a"}, {"strategy": "leg_a"}])
    out = tmp_path / "d.jsonl"
    assert dist.main(["x", "--emit", str(emit), "--symbol", "S",
                      "--timeframe", "4h", "--tp-cap-pct", str(CAP),
                      "--out", str(out)]) == 2
    assert not out.exists()


def test_upsert_preserves_other_legs(tmp_path):
    """A second leg's sweep must not erase the first — the artifact accumulates."""
    out = tmp_path / "d.jsonl"
    a = _emit(tmp_path, [{"strategy": "leg_a", "mfe_r": 1.0}], "a.jsonl")
    b = _emit(tmp_path, [{"strategy": "leg_b", "mfe_r": 2.0}], "b.jsonl")
    for e, sym in ((a, "AAA"), (b, "BBB")):
        assert dist.main(["x", "--emit", str(e), "--symbol", sym,
                          "--timeframe", "4h", "--tp-cap-pct", str(CAP),
                          "--out", str(out)]) == 0
    recs = dist.load_dist(out)
    assert set(recs) == {"leg_a", "leg_b"}
    assert recs["leg_a"]["symbol"] == "AAA" and recs["leg_b"]["symbol"] == "BBB"


# --- the seam: aggregator output actually drives a parity verdict ---------- #

def _live(n, peak=2.0, cap=40.0, leg="leg_a"):
    return [{"strategy": leg, "peak_r": peak, "cap_r": cap, "trade_id": str(i)}
            for i in range(n)]


def _closed(n):
    return [{"id": str(i), "status": "closed"} for i in range(n)]


def test_committed_distribution_drives_a_real_parity_verdict(tmp_path):
    emit = _emit(tmp_path, [{"strategy": "leg_a", "mfe_r": float(i)}
                            for i in range(1, 21)])
    out = tmp_path / "d.jsonl"
    assert dist.main(["x", "--emit", str(emit), "--symbol", "SOLUSDT",
                      "--timeframe", "4h", "--tp-cap-pct", str(CAP),
                      "--out", str(out)]) == 0

    rep = parity.run(_live(12), [], _closed(12), CAP, 8,
                     harness_dist=dist.load_dist(out))
    rec = rep["distribution"]["leg_a"]
    assert rec["parity_state"] == "compared"
    assert rec["harness_source"] == "committed_dist"
    assert rep["harness_side"] == "committed_dist"
    # The instrument identity must SURVIVE into the report — otherwise a reader
    # cannot tell a 4h SOL distribution from a 1m BTC one.
    assert rec["harness_symbol"] == "SOLUSDT"
    assert rec["harness_timeframe"] == "4h"
    assert rec["harness_n"] == 20 and rec["harness_max"] == 20.0


def test_the_artifact_and_raw_rows_agree_on_the_same_data(tmp_path):
    """The two harness paths must not be two answers.

    If the committed distribution disagreed with the emit rows it was built
    from, the artifact would be a quiet re-measurement rather than a record.
    """
    rows = [{"strategy": "leg_a", "mfe_r": float(i)} for i in range(1, 21)]
    emit = _emit(tmp_path, rows)
    out = tmp_path / "d.jsonl"
    dist.main(["x", "--emit", str(emit), "--symbol", "S", "--timeframe", "4h",
               "--tp-cap-pct", str(CAP), "--out", str(out)])

    via_rows = parity.run(_live(12), rows, _closed(12), CAP, 8)
    via_dist = parity.run(_live(12), [], _closed(12), CAP, 8,
                          harness_dist=dist.load_dist(out))
    a, b = via_rows["distribution"]["leg_a"], via_dist["distribution"]["leg_a"]
    for k in ("parity_state", "parity", "harness_n", "harness_p50",
              "harness_p80", "harness_max"):
        assert a[k] == b[k], f"{k}: emit={a[k]} dist={b[k]}"


def test_per_leg_cap_is_not_the_global_flag():
    """An uncapped LEG is refused on its own merits.

    A committed artifact can hold legs swept under different settings, so one
    uncapped leg must neither condemn nor be excused by its neighbours.
    """
    d = {"good": {"leg": "good", "tp_cap_pct": CAP, "n": 50, "p50": 1.0,
                  "p80": 1.0, "max": 1.0},
         "bad": {"leg": "bad", "tp_cap_pct": 0.0, "n": 50, "p50": 1.0,
                 "p80": 1.0, "max": 1.0}}
    live = _live(12, leg="good") + _live(12, leg="bad")
    rep = parity.run(live, [], _closed(12), CAP, 8, harness_dist=d)
    assert rep["distribution"]["good"]["parity_state"] == "compared"
    assert rep["distribution"]["bad"]["parity_state"] == "harness_uncapped"


def test_zero_n_leg_is_absent_not_compared():
    d = {"leg_a": {"leg": "leg_a", "tp_cap_pct": CAP, "n": 0, "p50": None,
                   "p80": None, "max": None}}
    rep = parity.run(_live(12), [], _closed(12), CAP, 8, harness_dist=d)
    assert rep["distribution"]["leg_a"]["parity_state"] == "harness_absent"


def test_both_harness_sources_at_once_is_refused(tmp_path, capsys):
    """Provenance must not be a function of argument order."""
    emit = _emit(tmp_path, [{"strategy": "leg_a", "mfe_r": 1.0}])
    live_p = tmp_path / "live.json"
    live_p.write_text(json.dumps(_live(12)), encoding="utf-8")
    out = tmp_path / "d.jsonl"
    dist.main(["x", "--emit", str(emit), "--symbol", "S", "--timeframe", "4h",
               "--tp-cap-pct", str(CAP), "--out", str(out)])
    with pytest.raises(SystemExit) as exc:
        parity.main(["--live-json", str(live_p), "--harness-dist", str(out),
                     "--harness-emit", str(emit)])
    assert exc.value.code != 0
