#!/usr/bin/env python3
"""symbol-resolver-guard — CI entry point for ``src.config.symbol_sets``.

WHY THIS THIN WRAPPER EXISTS, rather than the registry invoking the module.
The first version of the guard entry ran ``python3 -m src.config.symbol_sets
--self-test``, and `tests/ci/test_run_guards_runner_absent.py` failed it:

    these runners appear in the registry with no recorded remedy:
    ['src.config.symbol_sets'] — add them to RUNNER_REMEDY

That test is right and the fix is NOT to add an entry to ``RUNNER_REMEDY``.
That map answers *"this guard's runner is absent — what does the operator
install?"* and its values are ``pip install pytest`` / ``pip install ruff``.
``src.config.symbol_sets`` is repo code: it is never absent when the repo is
checked out, and there is no install command that would be a true answer. An
entry there would have been a FALSE remedy written to silence a correct guard —
the presence-only-marker failure this repo has already paid for once.

So the runner becomes ``python3``, which already has a remedy and is what every
other entry in the registry uses. Running the module by PATH instead would not
work: ``python3 src/config/symbol_sets.py`` puts ``src/config/`` on ``sys.path``
rather than the repo root, and the module's ``from src.config.accounts_loader
import …`` would fail at the first call.

This file adds the repo root to ``sys.path`` and delegates. It holds no logic of
its own on purpose — the self-test is the module's own contract and belongs
beside the code it grades.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def main() -> int:
    ap = argparse.ArgumentParser()
    # Accepted and ignored: the registry passes it, and a wrapper that REFUSED
    # the flag its own registry entry sends would fail for a reason that has
    # nothing to do with the invariant being checked.
    ap.add_argument("--self-test", action="store_true")
    ap.parse_args()

    from src.config.symbol_sets import _self_test
    return _self_test()


if __name__ == "__main__":
    raise SystemExit(main())
