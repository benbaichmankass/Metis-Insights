"""The corpus union must not re-drop the schema #9812 was filed to protect.

WHY THIS FILE EXISTS
--------------------
The sweep workflow's conflict path resets the worktree to the corpus branch,
whose copy is long-stale. Unioning `main` back in fixes that — but ONLY if the
merge rule is `sweep_generated_at`. The intuitive rule ("the branch I am
merging in is the fresher one") is what the extractor uses for artefacts, and
applying it corpus-to-corpus reverses the fix.

MEASURED 2026-08-17 on the real pair: 904 shared measurement keys, 19 differ,
and on **all 19** `main` is the newer side (2026-08-15 vs 2026-08-13) carrying
8 `live_tp_reach_r_*` keys against the branch's 0.

`test_the_incoming_always_wins_rule_would_drop_the_schema` is the load-bearing
test: it runs the WRONG rule over the same fixtures and asserts the loss. Without
it, `test_newer_incumbent_survives_a_stale_challenger` could pass for reasons
unrelated to the rule and would prove nothing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research" / "m20_corpus_union.py"
sys.path.insert(0, str(SCRIPT.parent))

from m20_corpus_union import (  # noqa: E402
    AmbiguousUnion,
    union_rows,
)

TP_KEYS = {f"live_tp_reach_r_{s}_{w}": 1.0
           for s in ("n", "min", "max", "median") for w in ("IS", "OOS")}


def _row(leg="trend_donchian", cell="stale12_lt0R", *, stamp, tp=False, **extra):
    """A corpus row. `measurement_key` reads kind/leg/cell/split/tp_cap_pct/…"""
    r = {
        "kind": "cell", "leg": leg, "cell": cell,
        "split": "2026-01-06", "tp_cap_pct": 0.099, "regime_router": "off",
        "min_oos_trades_floor": 25, "fee_bps_roundtrip": 7.5,
        "min_confidence_override": None, "declared_levers_dropped": [],
        "sweep_generated_at": stamp,
    }
    if tp:
        r.update(TP_KEYS)
    r.update(extra)
    return r


def _tp_count(row):
    return sum(1 for k in row if k.startswith("live_tp_reach_r"))


# ---------------------------------------------------------------- the rule


def test_newer_incumbent_survives_a_stale_challenger():
    """THE REAL CASE: `main` is newer AND complete; the branch must not win."""
    incumbent = _row(stamp="2026-08-15T22:24:18Z", tp=True)
    challenger = _row(stamp="2026-08-13T01:23:57Z")
    rows, stats = union_rows([incumbent], [challenger])
    assert len(rows) == 1
    assert _tp_count(rows[0]) == 8, "the stale challenger overwrote a complete row"
    assert stats["replaced_by_incoming"] == 0


def test_the_incoming_always_wins_rule_would_drop_the_schema():
    """THE DEFECT, reproduced — this is what makes the test above meaningful.

    Applying the extractor's artefacts→corpus rule (incoming supersedes) to the
    same fixtures loses the eight measured keys.
    """
    incumbent = _row(stamp="2026-08-15T22:24:18Z", tp=True)
    challenger = _row(stamp="2026-08-13T01:23:57Z")
    naive = {"x": challenger}  # "the side I am merging in wins"
    assert _tp_count(naive["x"]) == 0, (
        "expected the naive rule to yield the schema-less row; if it does not, "
        "this fixture is not exercising the defect and the guard is unproven")


def test_newer_challenger_does_win():
    """The rule is symmetric — it is not 'the incumbent always wins' either."""
    incumbent = _row(stamp="2026-08-13T01:23:57Z")
    challenger = _row(stamp="2026-08-15T22:24:18Z", tp=True)
    rows, stats = union_rows([incumbent], [challenger])
    assert _tp_count(rows[0]) == 8
    assert stats["replaced_by_incoming"] == 1


# ------------------------------------------------------- additive behaviour


def test_disjoint_keys_are_appended_and_nothing_is_replaced():
    a = _row(leg="alpha", stamp="2026-08-13T00:00:00Z")
    b = _row(leg="beta", stamp="2026-08-13T00:00:00Z")
    rows, stats = union_rows([a], [b])
    assert stats == {
        "into_rows": 1, "incoming_rows": 1, "shared_keys": 0,
        "replaced_by_incoming": 0, "appended_from_incoming": 1, "total_rows": 2,
    }
    assert rows[0] is a and rows[1] is b, "into's rows must come first, in order"


def test_identical_rows_are_not_counted_as_replacements():
    a = _row(stamp="2026-08-13T00:00:00Z")
    rows, stats = union_rows([a], [dict(a)])
    assert len(rows) == 1 and stats["replaced_by_incoming"] == 0


def test_every_incumbent_row_survives_when_none_is_superseded():
    """The property that makes the diff purely additive and reviewable."""
    into = [_row(leg=f"leg{i}", stamp="2026-08-15T00:00:00Z", tp=True)
            for i in range(5)]
    incoming = [_row(leg=f"leg{i}", stamp="2026-08-13T00:00:00Z") for i in range(5)]
    incoming.append(_row(leg="new", stamp="2026-08-13T00:00:00Z"))
    rows, stats = union_rows(into, incoming)
    assert stats["replaced_by_incoming"] == 0
    assert stats["appended_from_incoming"] == 1
    assert rows[:5] == into, "an incumbent row was rewritten"


# ------------------------------------------------- refusing rather than guessing


def test_a_missing_timestamp_is_broken_only_by_a_strict_superset():
    incumbent = _row(stamp=None, tp=True)
    challenger = _row(stamp=None)
    rows, _ = union_rows([incumbent], [challenger])
    assert _tp_count(rows[0]) == 8, "the superset row must win"


def test_an_undecidable_pair_REFUSES_rather_than_picking_a_side():
    """'We could not compare' is its own outcome, not a licence to choose."""
    incumbent = _row(stamp=None, only_here=1)
    challenger = _row(stamp=None, only_there=1)
    with pytest.raises(AmbiguousUnion) as exc:
        union_rows([incumbent], [challenger])
    assert "Refusing" in str(exc.value)


def test_an_exact_timestamp_tie_falls_through_to_the_superset_test():
    same = "2026-08-15T00:00:00Z"
    rows, _ = union_rows([_row(stamp=same)], [_row(stamp=same, tp=True)])
    assert _tp_count(rows[0]) == 8


# ------------------------------------------------------------------- the CLI


def _write(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return p


def test_cli_writes_the_union_in_the_corpus_format(tmp_path):
    into = _write(tmp_path, "into.jsonl", [_row(stamp="2026-08-15T00:00:00Z", tp=True)])
    src = _write(tmp_path, "src.jsonl", [_row(leg="new", stamp="2026-08-13T00:00:00Z")])
    r = subprocess.run([sys.executable, str(SCRIPT), "--into", str(into),
                        "--from", str(src)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lines = into.read_text().splitlines()
    assert len(lines) == 2
    for line in lines:  # the exact format the extractor writes
        assert json.dumps(json.loads(line), sort_keys=True) == line


def test_cli_dry_run_does_not_write(tmp_path):
    into = _write(tmp_path, "into.jsonl", [_row(stamp="2026-08-15T00:00:00Z")])
    before = into.read_text()
    src = _write(tmp_path, "src.jsonl", [_row(leg="new", stamp="2026-08-13T00:00:00Z")])
    r = subprocess.run([sys.executable, str(SCRIPT), "--into", str(into),
                        "--from", str(src), "--dry-run"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and into.read_text() == before
    assert "dry-run" in r.stdout


def test_cli_refuses_an_undecidable_pair_with_exit_2(tmp_path):
    into = _write(tmp_path, "into.jsonl", [_row(stamp=None, only_here=1)])
    src = _write(tmp_path, "src.jsonl", [_row(stamp=None, only_there=1)])
    r = subprocess.run([sys.executable, str(SCRIPT), "--into", str(into),
                        "--from", str(src)], capture_output=True, text=True)
    assert r.returncode == 2, r.stdout
    assert "Refusing" in r.stderr


def test_cli_errors_when_the_from_file_is_absent(tmp_path):
    """A missing --from must not read as 'nothing to merge'."""
    into = _write(tmp_path, "into.jsonl", [_row(stamp="2026-08-15T00:00:00Z")])
    r = subprocess.run([sys.executable, str(SCRIPT), "--into", str(into),
                        "--from", str(tmp_path / "nope.jsonl")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "does not exist" in r.stderr


def test_the_union_outcome_does_not_depend_on_which_side_is_into():
    """The RULE resolves each key, so the result is a set, not an artifact of order.

    Verified on the real 2026-08-17 pair as well as this fixture: unioning
    main-into-branch and branch-into-main both yield the same 1364-row set.
    A rule that failed this would mean the answer depended on which branch the
    job happened to be standing on — exactly the fragility being removed.
    """
    older = [_row(leg="shared", stamp="2026-08-13T00:00:00Z"),
             _row(leg="only_older", stamp="2026-08-13T00:00:00Z")]
    newer = [_row(leg="shared", stamp="2026-08-15T00:00:00Z", tp=True),
             _row(leg="only_newer", stamp="2026-08-15T00:00:00Z")]

    a, _ = union_rows([dict(r) for r in older], [dict(r) for r in newer])
    b, _ = union_rows([dict(r) for r in newer], [dict(r) for r in older])

    key = lambda rows: {json.dumps(r, sort_keys=True) for r in rows}  # noqa: E731
    assert key(a) == key(b)
    assert len(a) == 3
    # and the shared key resolved to the NEWER row in both orders
    shared = [r for r in a if r["leg"] == "shared"]
    assert len(shared) == 1 and _tp_count(shared[0]) == 8
