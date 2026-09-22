"""Tests for MI-216's per-leg offline edge producer.

The load-bearing properties are the ones that keep a WEAK number from reading as
a strong one: the fingerprint must move when the config moves, the folds must be
contiguous in time, and the four coverage states must stay apart. A pooled net-R
with none of those is exactly the kind of confident number this whole work stream
exists to stop trusting.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "build_strategy_evidence", ROOT / "scripts" / "ops" / "build_strategy_evidence.py"
)
bse = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(bse)


# ---------------------------------------------------------------------------
# config_fingerprint — an edge record is evidence about the leg AS CONFIGURED.
# ---------------------------------------------------------------------------

def test_fingerprint_moves_when_a_param_moves():
    """THE load-bearing property. If this fails, a stale record reads as current."""
    a = bse.config_fingerprint({"atr_stop_mult": 2.0, "symbols": ["ETHUSDT"]})
    b = bse.config_fingerprint({"atr_stop_mult": 2.5, "symbols": ["ETHUSDT"]})
    assert a != b


def test_fingerprint_is_stable_under_key_reordering():
    """Formatting churn must not invalidate every record in the fleet."""
    a = bse.config_fingerprint({"a": 1, "b": 2})
    b = bse.config_fingerprint({"b": 2, "a": 1})
    assert a == b


def test_fingerprint_is_stable_across_calls():
    cfg = {"donchian": 20, "symbols": ["BTCUSDT"], "timeframe": "4h"}
    assert bse.config_fingerprint(cfg) == bse.config_fingerprint(cfg)


# ---------------------------------------------------------------------------
# time_folds — contiguous and time-ordered, or the folds leak into each other.
# ---------------------------------------------------------------------------

def _t(day: int, r: float) -> dict:
    return {"entry_time": f"2026-01-{day:02d} 00:00:00+00:00", "net_r": r}


def test_folds_are_contiguous_and_time_ordered():
    trades = [_t(d, 1.0) for d in range(1, 13)]
    fd = bse.time_folds(list(reversed(trades)), 4)  # unsorted input on purpose
    assert len(fd) == 4
    assert [f["trades"] for f in fd] == [3, 3, 3, 3]
    ends = [f["end"] for f in fd]
    starts = [f["start"] for f in fd]
    assert starts == sorted(starts), "folds must be in time order"
    for i in range(len(fd) - 1):
        assert ends[i] < starts[i + 1], "folds must not overlap in time"


def test_fold_net_r_sums_to_the_pooled_total():
    """Arithmetic cross-check — the repo's own rule about catching what re-reading misses."""
    trades = [_t(d, r) for d, r in zip(range(1, 9), [1, -2, 3, -4, 5, -6, 7, -8])]
    fd = bse.time_folds(trades, 4)
    assert round(sum(f["net_r"] for f in fd), 6) == round(sum(t["net_r"] for t in trades), 6)


def test_folds_positive_distinguishes_one_good_block_from_four():
    """A pooled positive carried by ONE fold is not the same fact as four positives.

    Measured on the first real record: eth_pullback_2h pooled +3.3242 with only
    2 of 4 folds positive (+6.32, -2.92, +2.77, -2.85). The pooled number alone
    cannot tell those apart, which is why folds_positive is published.
    """
    lopsided = bse.time_folds([_t(d, r) for d, r in
                               zip(range(1, 9), [10, 10, -1, -1, -1, -1, -1, -1])], 4)
    assert sum(1 for f in lopsided if f["net_r"] > 0) == 1

    even = bse.time_folds([_t(d, 1.0) for d in range(1, 9)], 4)
    assert sum(1 for f in even if f["net_r"] > 0) == 4


def test_no_trades_yields_no_folds_not_a_zero_fold():
    """Zero trades is an ABSENCE of measurement, never an edge of zero."""
    assert bse.time_folds([], 4) == []


def test_undated_trades_are_dropped_not_dated_to_now():
    """A trade with no entry_time cannot be placed in a fold; guessing would fabricate."""
    fd = bse.time_folds([{"net_r": 5.0}, _t(1, 1.0), _t(2, 1.0)], 2)
    assert sum(f["trades"] for f in fd) == 2


# ---------------------------------------------------------------------------
# The states, and the contract that keeps them apart.
# ---------------------------------------------------------------------------

def test_four_coverage_states_and_basis_is_not_purged_walkforward():
    """`basis` must not borrow a term that means something stricter elsewhere.

    ml/promotion/oos_edge.py does purged WF-CV. These folds are emitted trades
    split after the fact. Naming them the same would be the unprovenanced
    -diagnostic failure this repo has a guard family for.
    """
    assert set(bse.COVERAGE_STATES) == {
        "measured", "no_harness", "harness_failed", "not_attempted"}
    assert bse.BASIS == "harness_timefolds"
    assert "purged" not in bse.BASIS


def test_record_for_an_unclassifiable_leg_is_no_harness_and_carries_no_number():
    """`no_harness` must never leak a pooled number — there is nothing to pool."""
    rec = bse.build_record("fake_leg", {"symbols": ["XXXUSDT"], "timeframe": "1h"},
                           workdir="/tmp", days=5, folds=2)
    assert rec["coverage_state"] == "no_harness"
    assert rec["net_r_oos"] is None
    assert rec["n_trades_oos"] is None
    assert rec["error"] and "classify" in rec["error"]


def test_unclassifiable_record_still_carries_a_fingerprint():
    """A leg we could not measure is still a leg whose config we can identify."""
    rec = bse.build_record("fake_leg", {"symbols": ["XXXUSDT"], "timeframe": "1h"},
                           workdir="/tmp", days=5, folds=2)
    assert rec["config_fingerprint"].startswith("sha256:")


def test_param_provenance_is_declared_unestablished():
    """The folds are OOS w.r.t. each other; that is not the same as params chosen OOS.

    If a leg was tuned on this history the pooled number is optimistic and nothing
    here can detect it — so the record says we did not establish it rather than
    letting `net_r_oos` imply we did.
    """
    rec = bse.build_record("fake_leg", {"symbols": ["XXXUSDT"], "timeframe": "1h"},
                           workdir="/tmp", days=5, folds=2)
    assert rec["param_selection_provenance"] == "not_established"


# ---------------------------------------------------------------------------
# THE OVERWRITE CONTRACT (E46 / PI-20260922-E41-0007).
#
# A failed run must never overwrite a record that currently reads `measured`.
# OBSERVED 2026-09-22: `yfinance` was absent from a sandbox, every leg graded
# `harness_failed`, and the producer rewrote the committed records for
# `gld_pullback_1d` and `qqq_trend_long_1d` -- nulling net_r_oos, n_trades_oos
# and fold_detail. They survived only because the lane read `git diff` first.
#
# ⚠️ THE BAR IS THE BYTES, NOT THE ABSENCE OF A STUB. "the stub was not
# written" is satisfied by a producer that writes a DIFFERENT wrong thing; the
# bytes are satisfied by nothing except leaving the file alone. So the
# end-to-end tests below hash the file and compare `st_mtime_ns` -- the second
# one also refuses a rewrite that happens to be byte-for-byte identical, which
# a hash alone would pass.
# ---------------------------------------------------------------------------
#: A leg that really exists and is really enabled in config/strategies.yaml,
#: with a really committed `measured` record -- one of the two the observed
#: incident destroyed. A synthetic leg name would test the guard against a
#: fixture instead of against the artifact the promotion path reads.
_VICTIM = "gld_pullback_1d"
_COMMITTED_RECORD = ROOT / "comms" / "strategy_evidence" / f"{_VICTIM}.json"


def _plant_harness_failure(monkeypatch):
    """Inject the exact failure that was observed: the feed dep is missing.

    `build_record` does `import regime_debt_matrix as rdm` at call time, so a
    `sys.modules` entry is what the real code path picks up. `classify()`
    returning a harness is deliberate -- the leg must ROUTE, or the record
    would come out `no_harness` and we would be testing a different branch.
    """
    mod = types.ModuleType("regime_debt_matrix")
    mod.classify = lambda cfg: "pullback"

    def run_one(name, cfg, workdir, days=365):
        raise ImportError("No module named 'yfinance'")

    mod.run_one = run_one
    monkeypatch.setitem(sys.modules, "regime_debt_matrix", mod)
    return mod


def _plant_successful_measure(monkeypatch, *, net_r: float = 9.0):
    """The other arm: a run that genuinely measures something."""
    mod = types.ModuleType("regime_debt_matrix")
    mod.classify = lambda cfg: "pullback"

    def run_one(name, cfg, workdir, days=365):
        emit = Path(workdir) / f"{name}__trades.jsonl"
        emit.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"entry_time": f"2026-01-{d:02d} 00:00:00+00:00",
                 "exit_time": f"2026-01-{d:02d} 12:00:00+00:00",
                 "net_r": net_r} for d in range(1, 5)]
        emit.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return {"fidelity": "faithful", "omitted_levers": [],
                "fee_bps_roundtrip": 0.0}

    mod.run_one = run_one
    monkeypatch.setitem(sys.modules, "regime_debt_matrix", mod)
    return mod


def _stage_committed_record(tmp_path: Path) -> tuple[Path, bytes]:
    """Copy the real committed record into a scratch out-dir, byte for byte."""
    out = tmp_path / "evidence"
    out.mkdir()
    target = out / f"{_VICTIM}.json"
    shutil.copyfile(_COMMITTED_RECORD, target)
    before = target.read_bytes()
    assert json.loads(before)["coverage_state"] == "measured", (
        "the fixture must start from a record that actually reads `measured`, "
        "or this test proves nothing"
    )
    return target, before


def test_a_planted_harness_failure_leaves_a_committed_measured_record_byte_identical(
        tmp_path, monkeypatch):
    """THE acceptance bar. Bytes, plus a non-zero exit."""
    _plant_harness_failure(monkeypatch)
    target, before = _stage_committed_record(tmp_path)
    mtime_before = target.stat().st_mtime_ns

    rc = bse.main(["--strategy", _VICTIM,
                   "--out", str(target.parent),
                   "--workdir", str(tmp_path / "run")])

    assert target.read_bytes() == before, "the committed record MOVED"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == \
        hashlib.sha256(before).hexdigest()
    assert target.stat().st_mtime_ns == mtime_before, (
        "the file was rewritten -- identical content is not the same as untouched"
    )
    assert rc == bse.EXIT_REFUSED_OVERWRITE != 0, (
        f"a run that refused to write must exit non-zero, got {rc}"
    )


def test_the_same_planted_failure_DOES_rewrite_a_record_that_is_not_measured(
        tmp_path, monkeypatch):
    """THE NEGATIVE CONTROL, and the test above is worthless without it.

    A guard cannot be trusted on a quiet result until the probe is shown to
    produce a loud one (`docs/CLAUDE-RULES-CANONICAL.md` RULE ONE: a negative
    needs a denominator). Here the ONLY thing changed is the prior record's
    `coverage_state` -- same plant, same leg, same call -- and the write must
    go through. If it does not, the test above is passing because the plant
    never reached the writer.
    """
    _plant_harness_failure(monkeypatch)
    target, _ = _stage_committed_record(tmp_path)
    rec = json.loads(target.read_text())
    rec["coverage_state"] = "harness_failed"
    target.write_text(json.dumps(rec, indent=2) + "\n")
    before = target.read_bytes()

    rc = bse.main(["--strategy", _VICTIM,
                   "--out", str(target.parent),
                   "--workdir", str(tmp_path / "run")])

    assert target.read_bytes() != before, (
        "the plant never reached write_record -- the protective test is vacuous"
    )
    assert json.loads(target.read_text())["coverage_state"] == "harness_failed"
    assert rc == 0, "nothing was refused, so nothing should have failed the run"


def test_a_successful_remeasure_still_overwrites_a_measured_record(
        tmp_path, monkeypatch):
    """The question E46 was fenced OUT of stays answered the way it was.

    `measured -> measured` is a re-measure, not a clobber, and this guard does
    not touch it.
    """
    _plant_successful_measure(monkeypatch)
    target, before = _stage_committed_record(tmp_path)

    rc = bse.main(["--strategy", _VICTIM,
                   "--out", str(target.parent),
                   "--workdir", str(tmp_path / "run")])

    after = json.loads(target.read_text())
    assert target.read_bytes() != before
    assert after["coverage_state"] == "measured"
    assert after["net_r_oos"] == 36.0
    assert rc == 0


def test_the_refusal_writes_the_failure_where_it_can_be_read(tmp_path, monkeypatch):
    """A refusal that leaves no trace of WHY is a silent skip wearing a guard's name."""
    _plant_harness_failure(monkeypatch)
    target, _ = _stage_committed_record(tmp_path)
    wd = tmp_path / "run"

    bse.main(["--strategy", _VICTIM, "--out", str(target.parent), "--workdir", str(wd)])

    side = wd / f"{_VICTIM}__refused_record.json"
    assert side.exists(), "the failure detail was thrown away"
    body = json.loads(side.read_text())
    assert body["refusal"]["decision"] == "refuse_measured"
    assert body["refusal"]["existing_coverage_state"] == "measured"
    assert body["refusal"]["incoming_coverage_state"] == "harness_failed"
    assert body["would_have_written"]["coverage_state"] == "harness_failed"
    assert "yfinance" in body["would_have_written"]["error"]


# --- overwrite_decision / existing_coverage_state, unit level ---------------

def test_every_non_measured_incoming_state_is_refused_over_a_measurement():
    """`no_harness` and `not_attempted` are no more a measurement than `harness_failed`."""
    for incoming in ("harness_failed", "no_harness", "not_attempted"):
        assert bse.overwrite_decision("measured", incoming) == "refuse_measured"
    assert bse.overwrite_decision("measured", "measured") == "write"


def test_a_non_measured_prior_record_never_blocks_a_write():
    for existing in ("absent", "harness_failed", "no_harness", "not_attempted"):
        for incoming in bse.COVERAGE_STATES:
            assert bse.overwrite_decision(existing, incoming) == "write"


def test_unreadable_is_not_absent_and_is_refused(tmp_path):
    """The collapse that would hand the destructive write its worst case.

    A half-written `measured` record is exactly what a clobbering run leaves
    behind, so reading a parse failure as "nothing to protect" would let the
    second run finish what the first one started.
    """
    p = tmp_path / "x.json"
    p.write_text('{"coverage_state": "measu')  # truncated mid-write
    assert bse.existing_coverage_state(p) == "unreadable"
    assert bse.overwrite_decision("unreadable", "measured") == "refuse_unreadable"
    assert bse.overwrite_decision("unreadable", "harness_failed") == "refuse_unreadable"


def test_absent_and_measured_and_unrecognised_are_three_different_readings(tmp_path):
    missing = tmp_path / "nope.json"
    assert bse.existing_coverage_state(missing) == "absent"

    good = tmp_path / "good.json"
    good.write_text(json.dumps({"coverage_state": "measured"}))
    assert bse.existing_coverage_state(good) == "measured"

    # A coverage_state this producer never emits is not "not measured" -- we do
    # not know what it is, so it reads `unreadable`.
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"coverage_state": "probably_fine"}))
    assert bse.existing_coverage_state(odd) == "unreadable"

    nolist = tmp_path / "list.json"
    nolist.write_text("[]")
    assert bse.existing_coverage_state(nolist) == "unreadable"
