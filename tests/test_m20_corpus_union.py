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

    Applies the extractor's artefacts→corpus rule (incoming supersedes,
    unconditionally) to the SAME fixtures the real rule is asked about, and
    asserts the eight measured keys are lost. A green suite for `union_rows`
    proves nothing unless the wrong rule is shown to be wrong on these inputs.
    """
    incumbent = _row(stamp="2026-08-15T22:24:18Z", tp=True)
    challenger = _row(stamp="2026-08-13T01:23:57Z")

    def _naive_union(into, incoming):
        """`m20_corpus_extract.main`'s rule: every incoming key supersedes."""
        from m20_corpus_extract import measurement_key
        incoming_keys = {measurement_key(r) for r in incoming}
        kept = [r for r in into if measurement_key(r) not in incoming_keys]
        return kept + list(incoming)

    naive = _naive_union([incumbent], [challenger])
    real, _ = union_rows([incumbent], [challenger])

    assert len(naive) == 1 and _tp_count(naive[0]) == 0, (
        "expected the naive rule to drop the eight live_tp_reach_r_* keys; if "
        "it does not, this fixture is not exercising the defect and the guard "
        "above is unproven")
    assert _tp_count(real[0]) == 8, "the real rule must keep them"


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


# ---------------------------------------------------------------------------
# The corpus selector (2026-08-26).
#
# `BL-20260826-E35-CORPUS-BRANCH-STRANDED-1629-MEASURED-CELLS-…` needs this
# same union for the e35 family. The two corpora differ in exactly one thing —
# which extractor owns `measurement_key` — so the tool was PARAMETERISED rather
# than copied. These tests pin the two properties that make that safe.
# ---------------------------------------------------------------------------

def test_module_import_is_lazy_so_the_m20_runner_path_still_works():
    """Importing this module must not import ANY extractor.

    Load-bearing, not style. The m20 sweep workflow's conflict re-derive copies
    only `m20_corpus_extract.py` onto the runner's sys.path (see the comment at
    `m20-exit-lever-sweep.yml`'s `cp` block). An eager `import
    e35_corpus_extract` at module scope would raise ImportError there and break
    the corpus recovery path at exactly the moment it is needed.

    Asserted by re-importing in a CLEAN interpreter, because this test process
    has almost certainly imported both extractors already for other reasons —
    checking `sys.modules` in-process would pass vacuously.
    """
    probe = (
        "import sys; sys.path.insert(0, %r);"
        "import m20_corpus_union;"
        "bad=[m for m in sys.modules if m.endswith('_corpus_extract')];"
        "print('LEAKED:'+','.join(sorted(bad)) if bad else 'CLEAN')"
        % str(SCRIPT.parent)
    )
    out = subprocess.run([sys.executable, "-c", probe],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "CLEAN", (
        "importing m20_corpus_union pulled in an extractor at module scope; "
        "the m20 runner only has m20_corpus_extract.py available. "
        f"got: {out.stdout.strip()}")


def test_key_fn_resolves_each_family_to_its_own_extractor():
    from m20_corpus_union import CORPUS_EXTRACTORS, key_fn

    assert set(CORPUS_EXTRACTORS) == {"m20", "e35"}
    for family, module in CORPUS_EXTRACTORS.items():
        fn = key_fn(family)
        assert fn.__module__ == module, (
            f"--corpus {family} must key with {module}.measurement_key, "
            f"got {fn.__module__}")


def test_unknown_corpus_refuses_rather_than_defaulting():
    """A typo'd family must NOT silently fall back to m20.

    Keying an e35 corpus with the m20 function would collapse distinct
    measurements onto one key (m20's key reads fields e35 rows do not carry, so
    every row would key identically) and the union would discard nearly all of
    them under a success message.
    """
    from m20_corpus_union import key_fn

    with pytest.raises(ValueError, match="unknown corpus"):
        key_fn("e35_bracket")


def test_default_key_is_m20_so_existing_callers_are_unchanged():
    """`union_rows` with no key argument must behave exactly as before.

    The m20 workflow calls the CLI with no `--corpus`, and the pre-existing
    tests above call `union_rows` positionally.
    """
    from m20_corpus_union import DEFAULT_CORPUS, key_fn

    assert DEFAULT_CORPUS == "m20"
    rows = [_row(stamp="2026-08-15T00:00:00+00:00")]
    implicit, _ = union_rows(list(rows), [])
    explicit, _ = union_rows(list(rows), [],
                             measurement_key=key_fn("m20"))
    assert implicit == explicit


def test_e35_rows_union_on_the_e35_key():
    """An end-to-end union over real e35-shaped rows.

    e35's `measurement_key` reads (leg, cell, tp_cap_pct, split_mode,
    split_target_oos) and returns a STRING, where m20's returns a tuple — so
    this also proves the union does not assume a tuple key anywhere.
    """
    from m20_corpus_union import key_fn

    def e35_row(leg, cell, *, stamp):
        return {"leg": leg, "cell": cell, "tp_cap_pct": 0.099,
                "split_mode": "expanding", "split_target_oos": 0.3,
                "state": "measured", "sweep_generated_at": stamp}

    into = [e35_row("spy_trend_long_1d", "tp2.5", stamp="2026-08-26T00:00:00+00:00")]
    incoming = [
        # same key, OLDER -> incumbent must survive
        e35_row("spy_trend_long_1d", "tp2.5", stamp="2026-08-24T00:00:00+00:00"),
        # new key -> appended
        e35_row("eth_pullback_2h", "sm3.0", stamp="2026-08-24T00:00:00+00:00"),
    ]
    out, stats = union_rows(into, incoming, measurement_key=key_fn("e35"))

    assert stats["shared_keys"] == 1
    assert stats["replaced_by_incoming"] == 0, "a stale challenger won"
    assert stats["appended_from_incoming"] == 1
    assert len(out) == 2
    assert out[0]["sweep_generated_at"] == "2026-08-26T00:00:00+00:00"


def test_e35_rows_keyed_with_the_m20_function_would_collapse():
    """Positive control for `test_unknown_corpus_refuses_rather_than_defaulting`.

    Shows the damage the wrong key does, so the refusal above is protecting
    something real rather than being defensive for its own sake.

    ⚠️ The hazard is NARROWER than "every e35 row collapses", and this test was
    written wrong once by assuming that. m20's key DOES read `leg` and `cell`,
    so e35 rows differing in either are told apart by it. What m20's key cannot
    see is `split_mode` / `split_target_oos` — fields that are part of e35's
    identity and absent from m20's. Two measurements of the SAME leg+cell under
    DIFFERENT splits are distinct e35 measurements and collapse to one key under
    m20's function, and the union then silently discards one under a success
    message. That is the real loss, and it is the one worth refusing on.
    """
    from m20_corpus_union import key_fn

    def e35_row(*, split_mode, oos, stamp):
        return {"leg": "spy_trend_long_1d", "cell": "tp2.5", "tp_cap_pct": 0.099,
                "split_mode": split_mode, "split_target_oos": oos,
                "sweep_generated_at": stamp}

    a = e35_row(split_mode="expanding", oos=0.3,
                stamp="2026-08-26T00:00:00+00:00")
    b = e35_row(split_mode="rolling", oos=0.5,
                stamp="2026-08-24T00:00:00+00:00")

    m20_key, e35_key = key_fn("m20"), key_fn("e35")
    assert m20_key(a) == m20_key(b), (
        "this control assumes m20's key cannot see split_mode/split_target_oos; "
        "if that stopped being true the refusal test needs re-justifying")
    assert e35_key(a) != e35_key(b), (
        "e35's own key must distinguish two splits of the same leg+cell")

    # THE SILENT LOSS. Distinct stamps let the timestamp rule order them, so
    # the union resolves the false collision as a supersede and drops `b`
    # entirely — under a success message reporting one shared key.
    out, stats = union_rows([a], [b], measurement_key=m20_key)
    assert len(out) == 1 and stats["shared_keys"] == 1, (
        "expected the silent collapse this control exists to show")

    out_ok, stats_ok = union_rows([a], [b], measurement_key=e35_key)
    assert len(out_ok) == 2 and stats_ok["shared_keys"] == 0, (
        "the correct key must keep both measurements")


def test_the_wrong_key_can_also_REFUSE_rather_than_lose_a_row():
    """The other half of the wrong-key outcome, recorded because it surprised me.

    Where the falsely-collided rows carry the SAME `sweep_generated_at` — the
    common case for two splits measured in one sweep run — the timestamp cannot
    order them and neither field set is a strict superset, so the union raises
    `AmbiguousUnion` instead of dropping one.

    That is the tool's refusal rule working, and it means the wrong key is not
    uniformly silent. Both outcomes are bad and neither is a reason to relax
    `--corpus` validation: one loses a measurement, the other fails a recovery
    that had nothing wrong with it.
    """
    from m20_corpus_union import key_fn

    def e35_row(*, split_mode, oos):
        return {"leg": "spy_trend_long_1d", "cell": "tp2.5", "tp_cap_pct": 0.099,
                "split_mode": split_mode, "split_target_oos": oos,
                "sweep_generated_at": "2026-08-26T00:00:00+00:00"}

    a = e35_row(split_mode="expanding", oos=0.3)
    b = e35_row(split_mode="rolling", oos=0.5)

    with pytest.raises(AmbiguousUnion):
        union_rows([a], [b], measurement_key=key_fn("m20"))

    out, _ = union_rows([a], [b], measurement_key=key_fn("e35"))
    assert len(out) == 2
