"""WHICH REGIME BOOK did the M20 sweep measure?

`m20_fleet_exit_sweep` passes no `--regime-router`, so `backtest_system` takes
its own default (`"off"`) and sets `REGIME_ROUTER_DISABLED=1`. The LIVE router is
BASELINE-ON. So every base book in the corpus is the **ungated** book — and until
2026-08-11 nothing recorded that: 604 rows, zero `regime` keys. A function
default standing in for the live input with nothing in the output revealing the
substitution is `diagnostic-provenance-guard` sub-class **B** (implicit input
selection) plus **C** (unasserted denominator).

What that costs is narrow and worth pinning precisely, because the tempting
overcorrection ("the corpus is void") is also wrong:

* **Delta comparisons survive.** Both arms of a cell share one base, so
  `d_net_r`, `d_max_dd`, Path A's `beats()` and the walk-forward all compare over
  a single consistent population.
* **Base-book LEVEL reads do not**, for a policy-named leg: `base_net_r`,
  `base_rate`, and therefore Path B's derived tolerance `D_b x (dN/N_b)`,
  describe a book production refuses to trade.

Measured 2026-08-11: 6 of 51 legs / 56 of 604 corpus rows are policy-named. The
motivating case is `ict_scalp_5m`, which carries TWO fully-off `trend_vol` cells
(trending/volatile and chop/volatile) and whose ungated backtest base reads
-48.88R IS while its live real-money record over the SAME period is +$13.53 at
75% win rate with `pnlCoverage` 1.0. Two different books, not two opinions.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


extract = _load("m20_corpus_extract", "scripts/research/m20_corpus_extract.py")
sweep = _load("m20_fleet_exit_sweep", "scripts/research/m20_fleet_exit_sweep.py")


# --- the three states are genuinely three ------------------------------------

def test_gate_delta_unknown_when_policy_unreadable():
    """An unreadable policy must NOT read as "no leg is gated" — that is the
    collapsed state this field exists to keep open."""
    assert sweep.regime_gate_delta("anything", None) == sweep.GATE_DELTA_UNKNOWN


def test_gate_delta_none_for_a_leg_the_policy_never_names():
    """base == live on this axis, so a base-level read IS a live claim."""
    assert sweep.regime_gate_delta("eth_pullback_2h", {"trend_donchian"}) == (
        sweep.GATE_DELTA_NONE)


def test_gate_delta_narrower_for_a_policy_named_leg():
    assert sweep.regime_gate_delta("ict_scalp_5m", {"ict_scalp_5m"}) == (
        sweep.GATE_DELTA_NARROWER)


# --- the real policy file, read as the field claims to read it ---------------

def test_real_policy_names_ict_scalp_5m_off():
    """Anchored on the ACTUAL config, not a restatement of it: if the OFF cell is
    ever removed, this test says the P1 refutation's premise has changed."""
    off = sweep._policy_off_legs()
    assert off is not None, "config/regime_policy.yaml unreadable"
    assert "ict_scalp_5m" in off
    assert sweep.regime_gate_delta("ict_scalp_5m", off) == sweep.GATE_DELTA_NARROWER


def test_real_policy_scan_reaches_the_nested_trend_vol_section():
    """`ict_scalp_5m` appears ONLY under trend_vol.<trend>.<vol>, two levels
    deeper than the trending/transitional/chop sections. A scanner that missed
    that nesting would return a plausible, smaller, wrong set."""
    off = sweep._policy_off_legs()
    doc = json.loads(json.dumps(  # normalise via the same yaml the scanner uses
        __import__("yaml").safe_load(
            (REPO / "config" / "regime_policy.yaml").read_text())))
    flat = set()
    for sec in ("trending", "transitional", "chop"):
        flat |= set((doc.get(sec) or {}).keys())
    assert "ict_scalp_5m" not in flat, "premise gone: it is now top-level too"
    assert "ict_scalp_5m" in off


def test_every_off_leg_really_has_a_refused_side_in_the_policy():
    """The scanner must not over-report: being NAMED is not being refused. Each
    entry mixes True and False sides (`trend_donchian` is long:true in `trending`
    and long:false in `trend_vol.trending.volatile`), so a scanner keying on
    presence rather than on a `False` value would return a larger, wrong set.
    Verified against the real file, independently of the scanner's own logic."""
    import yaml
    doc = yaml.safe_load((REPO / "config" / "regime_policy.yaml").read_text())
    assert doc, "policy empty"
    off = sweep._policy_off_legs()
    assert off is not None
    # every name in `off` must have at least one False side somewhere
    for name in off:
        found_false = False
        for sec in ("trending", "transitional", "chop"):
            sides = (doc.get(sec) or {}).get(name)
            if isinstance(sides, dict) and any(v is False for v in sides.values()):
                found_false = True
        for vols in (doc.get("trend_vol") or {}).values():
            for strats in (vols or {}).values():
                sides = (strats or {}).get(name)
                if isinstance(sides, dict) and any(v is False for v in sides.values()):
                    found_false = True
        assert found_false, f"{name} in off_legs with no False side"


# --- the extractor carries it onto every row ---------------------------------

def _doc(**kw):
    base = {
        "generated_at": "2026-08-11T00:00:00Z", "split": "2025-07-01",
        "tp_cap_pct": 0.099, "regime_router": "off",
        "regime_policy_readable": True,
        "regime_policy_off_legs": ["ict_scalp_5m"],
        "skipped": [{"leg": "skipped_leg", "reason": "no_candles"}],
        "verdicts": {
            "ict_scalp_5m": {"regime_gate_delta": "narrower_live",
                             "base_book": {}, "levers": {
                                 "stale_stop": [{"cell": "stale8"}]}},
            "eth_pullback_2h": {"regime_gate_delta": "none",
                                "base_book": {}, "levers": {
                                    "stale_stop": [{"cell": "stale8"}]}},
        },
    }
    base.update(kw)
    return base


def test_every_row_carries_the_regime_book():
    rows = extract.rows_from_verdicts(_doc(), "run1")
    assert rows, "no rows"
    for r in rows:
        assert "regime_router" in r, r
        assert "regime_gate_delta" in r, r


def test_skipped_leg_gets_a_delta_even_though_it_has_no_verdict():
    """A skipped leg never reaches `verdicts`, so its delta must be derived from
    the doc-level list — otherwise part of the fleet denominator is silent."""
    rows = extract.rows_from_verdicts(_doc(), "run1")
    sk = [r for r in rows if r.get("leg") == "skipped_leg"]
    assert sk and sk[0]["regime_gate_delta"] == "none"


def test_named_leg_rows_read_narrower_live():
    rows = extract.rows_from_verdicts(_doc(), "run1")
    named = [r for r in rows if r.get("leg") == "ict_scalp_5m"]
    other = [r for r in rows if r.get("leg") == "eth_pullback_2h"]
    assert named and all(r["regime_gate_delta"] == "narrower_live" for r in named)
    assert other and all(r["regime_gate_delta"] == "none" for r in other)


def test_unreadable_policy_propagates_as_unknown_not_none():
    rows = extract.rows_from_verdicts(
        _doc(regime_policy_readable=False, regime_policy_off_legs=None,
             verdicts={"x": {"base_book": {}, "levers": {"s": [{"cell": "c"}]}}}),
        "run1")
    assert rows and all(r["regime_gate_delta"] == "unknown" for r in rows)


def test_legacy_run_records_none_not_an_assumed_off():
    """A verdicts file predating the field must not be relabelled `"off"`. Every
    run to date WAS off — asserting it of a row we never recorded is exactly the
    substitution this field exists to stop."""
    doc = _doc()
    del doc["regime_router"]
    del doc["regime_policy_readable"]
    del doc["regime_policy_off_legs"]
    for v in doc["verdicts"].values():
        v.pop("regime_gate_delta", None)
    rows = extract.rows_from_verdicts(doc, "run1")
    assert rows
    for r in rows:
        assert r["regime_router"] is None, r
        assert r["regime_gate_delta"] is None, r


# --- the merge identity ------------------------------------------------------

def test_measurement_key_separates_gated_from_ungated():
    """A gated and an ungated measurement of the same cell must not merge."""
    a = {"kind": "cell", "leg": "l", "cell": "c", "split": "s",
         "tp_cap_pct": 0.099, "regime_router": "off"}
    b = dict(a, regime_router="on")
    legacy = dict(a, regime_router=None)
    assert extract.measurement_key(a) != extract.measurement_key(b)
    assert extract.measurement_key(a) != extract.measurement_key(legacy)


def test_measurement_key_ignores_the_gate_delta():
    """The delta describes the CURRENT policy, not what the run measured — a
    policy edit must not retroactively split rows that measured one book."""
    a = {"kind": "cell", "leg": "l", "cell": "c", "split": "s",
         "tp_cap_pct": 0.099, "regime_router": "off",
         "regime_gate_delta": "none"}
    b = dict(a, regime_gate_delta="narrower_live")
    assert extract.measurement_key(a) == extract.measurement_key(b)


# --- the committed corpus is honest about its own vintage --------------------

def test_committed_corpus_predates_the_field_and_says_so():
    """The 604 committed rows were all measured ungated. They must read `None`
    (unrecorded), NOT `"off"` — we are inferring their state from the code path,
    which is evidence, not a recording. A future re-sweep records it for real.
    """
    path = REPO / "docs" / "research" / "m20-sweep-corpus.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert rows, "corpus empty"
    stamped = [r for r in rows if r.get("regime_router") is not None]
    assert not stamped, (
        f"{len(stamped)} committed rows claim a recorded router state; the "
        "existing corpus predates the field and must read None")


# --- the floor analysis READS the field (not a write-only signal) ------------
#
# A field written and never read is worse than a missing one — reviewers see it
# and assume something acts on it (`provenance-consumer-guard`'s whole premise).
# The floor analysis is the natural consumer: every axis it tests is derived from
# the base book, so how much of its population has an UNGATED base is part of the
# verdict's denominator.

floor = _load("m20_path_b_floor", "scripts/research/m20_path_b_floor.py")


def _cell(leg, rate, wins=5, usable=6, **kw):
    row = {"kind": "cell", "leg": leg, "cell": f"{leg}_{rate}",
           "base_rate_IS": rate, "wf_ran": True, "wf_wins": wins,
           "wf_usable": usable}
    row.update(kw)
    return row


def test_population_reports_the_ungated_share():
    rows = [_cell(f"gated{i}", 1.0 + i, regime_gate_delta="narrower_live")
            for i in range(3)]
    rows += [_cell(f"clean{i}", 2.0 + i, regime_gate_delta="none")
             for i in range(3)]
    pop = floor.analyse(rows, "base_rate_IS", "floor")["population"]
    assert pop["cells_ungated_base"] == 3
    assert pop["legs_ungated_base"] == 3
    assert pop["ungated_base_why"] is None


def test_population_distinguishes_unrecorded_from_zero():
    """A corpus predating the field must read `None`, never 0 — "we did not
    record it" and "none are gated" are opposite claims."""
    unrecorded = floor.analyse(
        [_cell(f"l{i}", 1.0 + i) for i in range(4)], "base_rate_IS", "floor")
    assert unrecorded["population"]["cells_ungated_base"] is None
    assert unrecorded["population"]["ungated_base_why"]

    none_gated = floor.analyse(
        [_cell(f"l{i}", 1.0 + i, regime_gate_delta="none") for i in range(4)],
        "base_rate_IS", "floor")
    assert none_gated["population"]["cells_ungated_base"] == 0
    assert none_gated["population"]["ungated_base_why"] is None


def test_render_states_the_book_in_both_states():
    """Printed unconditionally: a reader must not have to know to ask."""
    unrecorded = floor.render(floor.analyse(
        [_cell(f"l{i}", 1.0 + i) for i in range(4)], "base_rate_IS", "floor"))
    assert "not recorded" in unrecorded
    assert "--regime-router off" in unrecorded

    gated = floor.render(floor.analyse(
        [_cell(f"g{i}", 1.0 + i, regime_gate_delta="narrower_live")
         for i in range(4)], "base_rate_IS", "floor"))
    assert "ungated base book" in gated
    assert "NOT excluded" in gated


def test_ungated_rows_are_reported_not_dropped():
    """The count must not change the analysed population — a silent exclusion
    would be selection over an unstated denominator."""
    rows = [_cell(f"g{i}", 1.0 + i, regime_gate_delta="narrower_live")
            for i in range(3)]
    rows += [_cell(f"c{i}", 2.0 + i, regime_gate_delta="none") for i in range(3)]
    with_flags = floor.analyse(rows, "base_rate_IS", "floor")["population"]
    bare = floor.analyse(
        [{k: v for k, v in r.items() if k != "regime_gate_delta"} for r in rows],
        "base_rate_IS", "floor")["population"]
    assert with_flags["analysed"] == bare["analysed"] == 6
    assert with_flags["legs_represented"] == bare["legs_represented"] == 6


# --- an unreadable policy is LOUD, not just three-state ----------------------
#
# `silent-empty-guard` rejected the original broad `except Exception` here and was
# right to. Returning None made the STATE honest ("we did not look") while leaving
# the CAUSE silent: a run whose policy read failed would stamp every leg `unknown`
# with nothing saying why. Legible-but-unactionable is not good enough.

def test_unreadable_policy_announces_itself_on_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sweep, "REGIME_POLICY_PATH", tmp_path / "does_not_exist.yaml")
    assert sweep._policy_off_legs() is None
    err = capsys.readouterr().err
    assert "regime policy unreadable" in err
    # it must say what the consequence is, not just that something failed
    assert "unknown" in err and "`none`" in err


def test_malformed_policy_is_also_announced(tmp_path, monkeypatch, capsys):
    """A YAML file that parses to a LIST, not a mapping — reads fine, is unusable.
    Distinct from the read failure above and must not be mistaken for `none`."""
    p = tmp_path / "policy.yaml"
    p.write_text("- not\n- a\n- mapping\n")
    monkeypatch.setattr(sweep, "REGIME_POLICY_PATH", p)
    assert sweep._policy_off_legs() is None
    assert "expected a mapping" in capsys.readouterr().err


def test_a_malformed_nested_section_degrades_it_does_not_raise(tmp_path, monkeypatch):
    """trend_vol as a scalar would have raised past the now-narrow except. The
    walker is isinstance-guarded at both levels, so it degrades to the sections it
    CAN read instead of taking the whole sweep down."""
    p = tmp_path / "policy.yaml"
    p.write_text("trending:\n  a_leg:\n    long: false\ntrend_vol: nonsense\n")
    monkeypatch.setattr(sweep, "REGIME_POLICY_PATH", p)
    off = sweep._policy_off_legs()
    assert off == {"a_leg"}


def test_a_readable_policy_prints_nothing(tmp_path, monkeypatch, capsys):
    """The happy path stays quiet — an announcement that fires every run is the
    desensitized-alarm failure mode."""
    p = tmp_path / "policy.yaml"
    p.write_text("trending:\n  a_leg:\n    long: false\n")
    monkeypatch.setattr(sweep, "REGIME_POLICY_PATH", p)
    assert sweep._policy_off_legs() == {"a_leg"}
    out = capsys.readouterr()
    assert out.err == "" and out.out == ""


# --- the MIN-OOS-TRADES FLOOR (operator decision 2026-08-11: 25) --------------
#
# A DENOMINATOR REQUIREMENT, not a fitted threshold. Its own verdict state,
# because "we did not look at enough trades" and "we looked and the lever failed"
# are opposite findings — collapsing them makes a thin book indistinguishable from
# a refuted lever.

def test_floor_value_is_25_and_declared_once():
    assert sweep.MIN_OOS_TRADES == 25


def test_floor_travels_in_the_measurement_identity():
    """A cell graded with no floor and graded at 25 can carry DIFFERENT verdicts,
    so merging the vintages would let an ungraded thin cell and a floor-refused
    one share a row."""
    a = {"kind": "cell", "leg": "l", "cell": "c", "split": "s",
         "tp_cap_pct": 0.099, "regime_router": "off", "min_oos_trades_floor": 25}
    unfloored = dict(a, min_oos_trades_floor=None)
    floor10 = dict(a, min_oos_trades_floor=10)
    assert extract.measurement_key(a) != extract.measurement_key(unfloored)
    assert extract.measurement_key(a) != extract.measurement_key(floor10)


def test_extractor_propagates_the_floor_onto_every_row():
    rows = extract.rows_from_verdicts(_doc(min_oos_trades_floor=25), "run1")
    assert rows
    for r in rows:
        assert r["min_oos_trades_floor"] == 25, r


def test_a_legacy_run_records_no_floor_not_floor_zero():
    """`None` is "ungraded by any floor". Recording 0 would assert that every
    thin cell in the existing 604-row corpus had been considered and admitted."""
    rows = extract.rows_from_verdicts(_doc(), "run1")
    assert rows
    for r in rows:
        assert r["min_oos_trades_floor"] is None, r
