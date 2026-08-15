"""`--total-sort` must remove the leg-order dependence, and change NOTHING by default.

BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER. `sorted` is stable,
so trades sharing an entry `bar_t` keep their `rows.jsonl` order — which is the
order the legs were passed on the command line. On a 2h family every leg entering
on the same bar carries an identical `bar_t`, so those tie groups span every
pooled leg and the argument order moves fold membership.

Two properties matter and they pull against each other:

  * ON  — the partition must be invariant to input order.
  * OFF — the partition must be BYTE-FOR-BYTE what it was before the flag
          existed, because the entire committed corpus was measured that way and
          a silent change would leave old and new verdicts pooled in one file
          with nothing marking which convention produced them.

The second is the one a careless implementation breaks, so it is tested first.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _mod():
    sys.path.insert(0, str(REPO / "scripts" / "research"))
    spec = importlib.util.spec_from_file_location(
        "_teh", REPO / "scripts" / "ml" / "train_exit_head.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _entry(bars):
    return bars[0]["bar_t"]


# Each trade's entry time is a property of the TRADE, fixed here, so that
# reordering the dict is a pure permutation of the same data. An earlier version
# of this fixture derived bar_t from the insertion index, which meant the two
# "orders" were different datasets and the invariance test failed for a reason
# that had nothing to do with the code under test.
_BAR_T = {"eth_1": 1_000_000.0, "eth_2": 1_000_000.0, "sol_1": 1_000_000.0,
          "sol_2": 2_000_000.0, "xrp_1": 2_000_000.0, "xrp_2": 2_000_000.0}


def _tied_pool(order):
    """A pool where every trade shares one of two entry timestamps.

    That is the real 2h shape — all legs entering on the same bar tie — pushed to
    its limit so the tie-break is the only thing that can order them within a
    group. `order` permutes the dict's insertion order and nothing else, which is
    exactly what a different `--legs` string does to `rows.jsonl`.
    """
    return {key: [{"bar_t": _BAR_T[key], "age_bars": 0, "year": 2026}]
            for key in order}


A = ["eth_1", "eth_2", "sol_1", "sol_2", "xrp_1", "xrp_2"]
B = ["xrp_2", "sol_1", "eth_2", "xrp_1", "eth_1", "sol_2"]


def _order_of(m, pool, total_sort):
    blocks = m.fold_blocks(pool, "trades", 2, _entry, total_sort=total_sort)
    return [sorted(test) for _, _, test, _ in blocks]


def test_DEFAULT_is_byte_for_byte_the_pre_flag_behaviour() -> None:
    """The flag must be inert unless asked for — the corpus depends on it."""
    m = _mod()
    pool = _tied_pool(A)
    explicit_off = m.fold_blocks(pool, "trades", 2, _entry, total_sort=False)
    defaulted = m.fold_blocks(pool, "trades", 2, _entry)
    assert [sorted(t) for _, _, t, _ in explicit_off] == \
           [sorted(t) for _, _, t, _ in defaulted]


def test_OFF_the_partition_still_depends_on_input_order() -> None:
    """Pins the DEFECT, so the flag cannot be quietly made unnecessary.

    If this ever fails, the underlying sort changed and the migration story in
    the backlog row needs rewriting — that is a finding, not a passing test.
    """
    m = _mod()
    a = _order_of(m, _tied_pool(A), total_sort=False)
    b = _order_of(m, _tied_pool(B), total_sort=False)
    assert a != b, (
        "the unstable-tie defect is gone without the flag — if intentional, "
        "BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER and its "
        "migration plan are now stale")


def test_ON_the_partition_is_INVARIANT_to_input_order() -> None:
    """The whole point of the flag."""
    m = _mod()
    a = _order_of(m, _tied_pool(A), total_sort=True)
    b = _order_of(m, _tied_pool(B), total_sort=True)
    assert a == b, f"total sort still order-dependent:\n{a}\n{b}"


def test_ON_and_OFF_actually_differ_on_a_tied_pool() -> None:
    """Guards against a flag that is wired but does nothing."""
    m = _mod()
    pool = _tied_pool(B)
    assert _order_of(m, pool, True) != _order_of(m, pool, False), (
        "--total-sort produced the same partition as the default on a fully "
        "tied pool — the flag is not reaching the sort")


def test_untied_data_is_unaffected_either_way() -> None:
    """A total sort must only break TIES, never reorder distinct timestamps."""
    m = _mod()
    pool = {f"t{i}": [{"bar_t": 1000.0 + i, "age_bars": 0, "year": 2026}]
            for i in range(6)}
    assert _order_of(m, pool, True) == _order_of(m, pool, False)


# --------------------------------------------------------------------------
# The flag must be REACHABLE from the round driver, and the convention must be
# RECORDED. A flag only `train_exit_head.py` accepts is unreachable in practice,
# because rounds are launched through `m20_exit_head_round.py` — the
# written-but-never-wired shape this repo has guards for one level down.

_DRIVER = REPO / "scripts" / "research" / "m20_exit_head_round.py"


def test_the_round_driver_ACCEPTS_and_FORWARDS_total_sort() -> None:
    src = _DRIVER.read_text()
    assert '"--total-sort", action="store_true"' in src, \
        "the round driver does not accept --total-sort, so the flag is unreachable"
    assert 'train_cmd += ["--total-sort"]' in src, \
        "the driver accepts --total-sort but never forwards it to train_exit_head"


def test_the_convention_is_RECORDED_in_round_meta_and_on_the_row() -> None:
    """Unrecorded, a re-measured round is indistinguishable from a legacy one.

    That is the same defect the flag exists to end, one level up: the migration
    is only auditable if each artifact says which convention produced it.
    """
    src = _DRIVER.read_text()
    assert src.count('"total_sort": bool(a.total_sort),') == 2, (
        "expected the convention stamped in BOTH _round_meta and the emitted "
        "evidence row; found "
        f"{src.count('chr(34)total_sort(chr(34)): bool(a.total_sort),')}")


def test_it_is_stamped_UNCONDITIONALLY_not_only_when_true() -> None:
    """`bool(...)` not `if a.total_sort`. An absent key would make a legacy
    round and a False round indistinguishable — the collapsed-state shape."""
    src = _DRIVER.read_text()
    assert 'if a.total_sort:\n            train_cmd' in src, \
        "forwarding should be conditional (no flag = no arg)"
    assert '"total_sort": bool(a.total_sort),' in src, \
        "but the STAMP must be unconditional, recording False explicitly"
