"""The guard registry must refuse a malformed entry AT IMPORT, not seven tests deep.

WHY THIS EXISTS. `automerge-trigger-guard` was added to `run_guards.GUARDS` on
2026-09-02 with no `when` key at all, intending "ungated" — for which this
registry's convention is an explicit `None`. Measured over all 74 entries at
that head, it was the ONLY one missing the key; 35 carried an explicit `None`.

The omission broke the driver in three places that index `g["when"]` directly:
the `--list` scope column, the diff-scoped relevance filter (the ordinary local
/ pre-commit run), and the dirty-worktree warning. ⚠️ **The `guards` CI job was
GREEN throughout** — it invokes the driver in a mode that short-circuits every
one of those reads — so the only thing that spoke was
`tests/test_guards_uncommitted_work.py`, which failed 7 tests with a bare
`KeyError: 'when'` naming neither the guard nor the key, in a file whose
subject is uncommitted work and not registry shape.

So these tests pin the two halves separately, because a validator that cannot
fail proves nothing about the registry it validates:

  * the SHIPPED registry satisfies the invariant (the positive), and
  * a planted omission is REFUSED, with the entry and the key named (the
    negative — the control that makes the positive meaningful).
"""
from __future__ import annotations

import copy
import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_spec = importlib.util.spec_from_file_location(
    "run_guards_registry_shape", os.path.join(REPO, "scripts", "ci", "run_guards.py")
)
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)


def test_shipped_registry_declares_every_dereferenced_key():
    """The positive. Every entry carries the three keys the driver indexes."""
    missing = {
        g.get("name") or f"<entry {i}>": [k for k in rg._REQUIRED_GUARD_KEYS if k not in g]
        for i, g in enumerate(rg.GUARDS)
        if any(k not in g for k in rg._REQUIRED_GUARD_KEYS)
    }
    assert missing == {}, missing
    assert rg.GUARDS, "registry is empty — this test would pass vacuously"


@pytest.mark.parametrize("key", ["name", "when", "steps"])
def test_a_missing_key_is_refused(key):
    """The negative, one per required key.

    `when` is the key that actually shipped missing; `name` and `steps` are
    included because the driver dereferences them too, and a validator that
    only ever fires on the one observed defect is not a rule, it is a patch.
    """
    broken = copy.deepcopy(rg.GUARDS)
    victim = broken[0]
    victim.pop(key)
    with pytest.raises(ValueError) as exc:
        rg._validate_registry(broken)
    assert repr(key) in str(exc.value), str(exc.value)


def test_the_refusal_names_the_offending_entry():
    """A `KeyError: 'when'` is what the old failure looked like: no guard named,
    no key explained. The whole point of moving the check here is the message."""
    broken = copy.deepcopy(rg.GUARDS)
    broken[0].pop("when")
    with pytest.raises(ValueError) as exc:
        rg._validate_registry(broken)
    msg = str(exc.value)
    assert rg.GUARDS[0]["name"] in msg, msg
    # and it must say what "ungated" is spelled as, since that is the mistake
    assert '"when": None' in msg, msg


def test_an_unnamed_entry_is_still_locatable():
    """An entry missing BOTH `name` and `when` must not produce an anonymous
    complaint — the index is what makes it findable in a 74-entry list."""
    broken = copy.deepcopy(rg.GUARDS)
    broken.append({"steps": [["python3", "-c", "pass"]]})
    with pytest.raises(ValueError) as exc:
        rg._validate_registry(broken)
    assert f"index {len(broken) - 1}" in str(exc.value), str(exc.value)


def test_ungated_is_spelled_as_an_explicit_none():
    """The convention this registry actually follows, asserted rather than
    described. If a future entry means 'always', it says so with `None`."""
    always_on = [g["name"] for g in rg.GUARDS if g["when"] is None]
    assert always_on, "no always-on guard in the registry — the convention is untested"
