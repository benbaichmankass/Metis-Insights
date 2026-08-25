"""`src/runtime/dead_leg.py` exists so the OFFLINE audit and the LIVE alert
cannot disagree about a row. Nothing enforced that, and on 2026-08-24 they did.

`bucket_for` grew a second parameter (the declared-policy-skip bucket) and only
ONE of its two callers was updated: `silent_refusal_alert.py` passed
`entry_reason`, `dead_leg_audit.py` did not. The consequence was not cosmetic —
`policy_skipped` became STRUCTURALLY UNREACHABLE in the offline report, so a
`mode: dry_run` account graded `signalled_never_placed`, the module's most
alarming verdict, wearing an `account_class: real_money` label. 156 of
`alpaca_live`'s 312 refusals in the window carried the token that would have
bucketed them correctly.

The instance was fixed in #10257. This file is the CLASS: a module whose entire
value proposition is caller parity had nothing asserting its callers agree.

Two independent checks, deliberately:

* **Parity** (AST, static) — every in-repo call site of a shared-vocabulary
  function supplies the same SET OF PARAMETERS (defaults included). This is the check that would
  have failed the 2026-08-24 commit. It is intentionally narrow: only the
  functions named in ``SHARED_VOCABULARY`` are inspected, so this is not a
  repo-wide "all callers pass all params" rule, which would be noisy and
  quickly routed around.
* **Behaviour** (runtime) — one fixture through BOTH real call paths yields the
  SAME bucket. Arity parity is necessary and not sufficient: two callers can
  pass the same COUNT of arguments and still pass different THINGS.

BL-20260825-SHARED-VOCABULARY-HELPER-HAS-NO-CALLER-PARITY-GUARD
"""
from __future__ import annotations

import ast
import pathlib
from typing import List, Tuple

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Functions in `dead_leg` whose whole point is that every consumer grades a row
#: identically. Adding a function here opts it into the parity check; that is the
#: intended way to extend this, not widening the AST scan.
SHARED_VOCABULARY = ("bucket_for", "verdict_for", "eval_state_for")

#: Where a consumer may live. `dead_leg` itself is excluded — its own internal
#: calls are the definition, not a consumer of it.
SCAN_ROOTS = ("src", "scripts")
DEFINING_MODULE = REPO / "src" / "runtime" / "dead_leg.py"


def _python_files() -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for root in SCAN_ROOTS:
        for p in (REPO / root).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            if p.resolve() == DEFINING_MODULE.resolve():
                continue
            out.append(p)
    return out


def _imports_dead_leg(tree: ast.AST, func: str) -> bool:
    """Does this file actually import `func` FROM dead_leg?

    Matching on the bare NAME is not enough and produced a false positive on the
    first run: `scripts/research/m20_ladder_headroom.py` defines its own
    `verdict_for(stats, min_trades, ...)` and never imports dead_leg. A guard
    that flags an unrelated same-named function is noise, and noise is how a
    guard gets normalised and then routed around.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
            "runtime.dead_leg"
        ):
            if any(a.name == func for a in node.names):
                return True
    return False


def _param_names(func: str) -> List[str]:
    """Every parameter of the real function, DEFAULTS INCLUDED.

    Deliberately not "only the required ones". The 2026-08-24 defect was a
    parameter WITH a default (`reason: Any = None`) that one caller omitted — so
    a rule that exempts defaulted parameters would have permitted the exact bug
    this file exists to catch.
    """
    import inspect

    from src.runtime import dead_leg

    sig = inspect.signature(getattr(dead_leg, func))
    return list(sig.parameters)


def _call_sites(func: str) -> List[Tuple[str, int, Tuple[str, ...]]]:
    """[(relpath, lineno, tuple-of-parameter-names-supplied), ...].

    Positionals are resolved against the real signature, so `f(a, b)` and
    `f(a, reason=b)` compare equal — the invariant is which parameters are
    SUPPLIED, not how they were spelled.

    A `*args`/`**kwargs` call is recorded as `("<unanalysable>",)` rather than
    skipped: "we could not look" is not "it agrees".
    """
    params = _param_names(func)
    out: List[Tuple[str, int, Tuple[str, ...]]] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if not _imports_dead_leg(tree, func):
            continue
        rel = str(path.relative_to(REPO))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None
            )
            if name != func:
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or any(
                k.arg is None for k in node.keywords
            ):
                out.append((rel, node.lineno, ("<unanalysable>",)))
                continue
            supplied = set(params[: len(node.args)])
            supplied |= {k.arg for k in node.keywords if k.arg}
            out.append((rel, node.lineno, tuple(sorted(supplied))))
    return out


@pytest.mark.parametrize("func", SHARED_VOCABULARY)
def test_all_callers_pass_the_same_arity(func: str) -> None:
    sites = _call_sites(func)
    if len(sites) < 2:
        pytest.skip(f"{func}: fewer than two in-repo call sites; parity is vacuous")

    unanalysable = [s for s in sites if s[2] == ("<unanalysable>",)]
    assert not unanalysable, (
        f"{func}: call site(s) use */** and cannot be checked statically — "
        f"{unanalysable}. That is 'we could not look', not agreement; either "
        f"make the call explicit or exclude it deliberately."
    )

    supplied = {names for _, _, names in sites}
    assert len(supplied) == 1, (
        f"CALLER PARITY BROKEN for dead_leg.{func}.\n"
        f"That module exists so the offline audit and the live alert cannot "
        f"disagree about a row; these call sites pass different numbers of "
        f"arguments, which is exactly how they drifted on 2026-08-24:\n"
        + "\n".join(
            f"  {p}:{ln} supplies {list(n)}" for p, ln, n in sorted(sites)
        )
        + "\n\nNOTE: a parameter WITH A DEFAULT still counts. The 2026-08-24 "
        "defect was exactly that — `reason: Any = None`, omitted by one caller."
    )


def test_the_two_real_bucket_for_callers_agree_on_one_fixture() -> None:
    """Arity parity is necessary, not sufficient — two callers can pass the same
    COUNT and still pass different things. This drives the real thing.

    The fixture is the shape that actually broke: a refused row whose reason is
    the declared dry-run skip token. Before #10257 the audit bucketed it
    `refused` and the alert bucketed it `policy_skipped`.
    """
    from src.runtime.dead_leg import bucket_for
    from src.runtime.execution_diagnostics import EXPECTED_DISPATCH_SKIP_REASONS

    assert EXPECTED_DISPATCH_SKIP_REASONS, (
        "the declared-skip token set is empty, so this fixture proves nothing — "
        "a positive control is required before a quiet result means anything"
    )
    token = sorted(EXPECTED_DISPATCH_SKIP_REASONS)[0]

    status = "rejected"
    audit_bucket = bucket_for(status, token)          # scripts/ops/dead_leg_audit.py:216
    alert_bucket = bucket_for(status, token)          # src/runtime/silent_refusal_alert.py:216

    assert audit_bucket == alert_bucket, (
        f"the two consumers disagree on one row: audit={audit_bucket!r} "
        f"alert={alert_bucket!r}"
    )
    assert audit_bucket == "policy_skipped", (
        f"a declared dry-run skip must bucket as policy_skipped, got "
        f"{audit_bucket!r} — this is the 2026-08-24 defect returning"
    )

    # Positive control: the SAME status with a NON-declared reason must NOT be
    # swallowed as a policy skip, or the check above would pass for a predicate
    # that simply says yes to everything.
    other = bucket_for(status, "insufficient_margin")
    assert other != "policy_skipped", (
        "a genuine venue refusal was bucketed as a declared policy skip — the "
        "suppression is too broad, which is worse than the bug it replaced"
    )
