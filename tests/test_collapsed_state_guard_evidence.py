"""What counts as EVIDENCE that a state is branched on.

`collapsed-state-guard` decides whether a declared state has a real consumer.
Its evidence model has been wrong twice, both times in the direction that makes
the guard *report* rather than *find*:

1. **The registry satisfied itself** (fixed 2026-08-31). A contract entry
   contains its own `consumer_token` pattern and every state literal, so the
   declaring file counted as a consumer and check (3) could never fire.
2. **Only bare string literals counted** (fixed here). A consumer branching on
   the producer's imported constant — `v.state == INFEASIBLE` — was invisible,
   so the guard penalised the better practice and could only be satisfied by
   sprinkling literals into modules that import the vocabulary properly. Three
   of the four "findings" surfaced by fixing (1) were this, not real collapses.

The fix for (2) opens its own hole, which is why the import test below is not
optional: if an `import` line counted, one
`from research_queue import (CLEARED, ACCRUING, ...)` would satisfy a whole
contract while branching on nothing — "cheaper to lie to than to satisfy",
the exact failure this guard cites as its reason for existing.
"""
from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_collapsed_states.py"


def _load():
    spec = importlib.util.spec_from_file_location("_csg", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

PRODUCER = textwrap.dedent(
    '''
    CLEARED = "cleared"
    INFEASIBLE = "infeasible"
    ACCRUING = "accruing"
    '''
)
STATES = ["cleared", "infeasible", "accruing"]


def _consts():
    return G._state_constants(PRODUCER, STATES)


def test_module_constants_are_derived_from_the_producer():
    assert _consts() == {
        "cleared": ["CLEARED"],
        "infeasible": ["INFEASIBLE"],
        "accruing": ["ACCRUING"],
    }


def test_a_branch_on_an_imported_constant_counts_as_evidence():
    txt = "if verdict.power_state == INFEASIBLE:\n    return None\n"
    assert G._states_in(txt, STATES, const_names=_consts()) == {"infeasible"}


def test_a_bare_literal_still_counts():
    txt = 'if row["power_state"] == "accruing":\n    pass\n'
    assert G._states_in(txt, STATES, const_names=_consts()) == {"accruing"}


def test_an_import_alone_is_not_evidence_of_a_branch():
    """The anti-lie property of the constant-name credit.

    Without this, satisfying any contract costs one import line.
    """
    txt = textwrap.dedent(
        '''
        from research_queue import (
            CLEARED,
            INFEASIBLE,
            ACCRUING,
        )
        power_state = None
        '''
    )
    assert G._states_in(txt, STATES, const_names=_consts()) == set()


def test_a_plain_import_statement_is_not_evidence_either():
    txt = "import CLEARED  # nonsense, but it is still an import line\n"
    assert G._states_in(txt, STATES, const_names=_consts()) == set()


def test_constants_are_not_credited_when_no_const_map_is_passed():
    """Producer integrity (check 1) passes no map, deliberately.

    Crediting `CLEARED = "cleared"` as evidence that the module emits
    `cleared` would make every contract self-satisfying at its own declaration
    site — bug (1) one module over.
    """
    txt = "if v.state == INFEASIBLE:\n    pass\n"
    assert G._states_in(txt, STATES) == set()


# --- end-to-end: the guard still FINDS a real collapse -----------------------


def _run_against(tmp_path, producer_src, consumer_src, states, monkeypatch):
    prod = tmp_path / "prod.py"
    prod.write_text(producer_src)
    cons = tmp_path / "cons.py"
    cons.write_text(consumer_src)
    monkeypatch.setattr(G, "REPO", tmp_path)
    monkeypatch.setattr(G, "_py_files", lambda: [prod, cons])
    monkeypatch.setattr(
        G,
        "CONTRACTS",
        [
            {
                "name": "t.state",
                "producer": "prod.py",
                "consumer_token": r"\bthing_state\b",
                "states": states,
                "why": "test",
            }
        ],
    )
    monkeypatch.setattr(G, "GRANDFATHERED_UNREAD", set())
    return G.main(["prog"])


def test_a_state_no_consumer_branches_on_still_fails(tmp_path, monkeypatch):
    rc = _run_against(
        tmp_path,
        PRODUCER + '\ndef f():\n    return "cleared", "infeasible", "accruing"\n',
        "if thing_state == CLEARED:\n    pass\nif thing_state == ACCRUING:\n    pass\n",
        STATES,
        monkeypatch,
    )
    assert rc == 1, "`infeasible` is unread — the guard must still fail"


def test_the_same_case_passes_once_the_missing_branch_exists(tmp_path, monkeypatch):
    rc = _run_against(
        tmp_path,
        PRODUCER + '\ndef f():\n    return "cleared", "infeasible", "accruing"\n',
        (
            "if thing_state == CLEARED:\n    pass\n"
            "if thing_state == ACCRUING:\n    pass\n"
            "if thing_state == INFEASIBLE:\n    pass\n"
        ),
        STATES,
        monkeypatch,
    )
    assert rc == 0


def test_a_consumer_that_only_imports_the_vocabulary_does_not_satisfy_it(
    tmp_path, monkeypatch
):
    rc = _run_against(
        tmp_path,
        PRODUCER + '\ndef f():\n    return "cleared", "infeasible", "accruing"\n',
        "from prod import CLEARED, INFEASIBLE, ACCRUING\nthing_state = None\n",
        STATES,
        monkeypatch,
    )
    assert rc == 1, "an import list must never stand in for a branch"


def test_the_registry_is_not_a_consumer_of_itself():
    """Regression pin for the 2026-08-31 self-satisfaction bug.

    Not a source-grep for the skip line — that would be presence-only evidence,
    which is the failure mode this guard's own docstring names. Instead it
    MEASURES the property that made the bug possible: a contract entry carries
    its own `consumer_token` pattern as source text and every state literal in
    its `states` list, so the registry would satisfy that contract outright if
    it were scanned.

    Population, because the count is the point: measured 2026-08-31 over the
    live registry, **19 of 20 contracts** self-satisfy. The single exception is
    `pairs_executor.open_state_read`, whose token names private helper
    functions that do not appear in the registry text. So the exclusion is
    load-bearing for all but one contract, and the assertion is written as a
    floor rather than an all-quantifier so a future contract with a
    helper-shaped token does not read as a regression.
    """
    import re

    txt = GUARD.read_text()
    self_satisfying = [
        str(c["name"])
        for c in G.CONTRACTS
        if re.search(str(c["consumer_token"]), txt)
        and G._states_in(txt, list(c["states"])) == set(c["states"])
    ]
    assert len(self_satisfying) >= len(G.CONTRACTS) - 1, (
        "the registry stopped self-satisfying its own contracts — if that is "
        "real the exclusion may be dead code, so re-measure before deleting it"
    )
    assert G._REGISTRY_PATH == GUARD.resolve()


def test_the_live_registry_is_clean_apart_from_declared_debt():
    assert G.main(["prog"]) == 0
