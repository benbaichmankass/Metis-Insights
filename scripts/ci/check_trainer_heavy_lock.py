#!/usr/bin/env python3
"""Every heavy trainer entrypoint must acquire the shared heavy-job queue.

`BL-20260815-RESEARCH-TRAINERS-BYPASS-THE-HEAVY-JOB-QUEUE`, criterion (b).
`docs/claude/trainer-resource-protocol.md` § Rule 1 is binding, and it had two
enforcement halves — the shell timer wrappers, and `ml/cli.py` for
`python -m ml train|build-dataset`. A **research** script shelling out to
another research script is neither, so the protocol read as enforced while a
whole family of ~5 GB jobs was exempt. Measured 2026-08-15: 6 of 7 in-scope
scripts bypassed it, and the one 4.08 GB unqueued job that collided with a
screen tripled its arm times (7.9 → 25.8 min on work that cannot differ in
duration).

WHAT THIS CHECKS, and why it is the entrypoints rather than the callers.
`train_exit_head.py` alone has five in-repo callers plus every ad-hoc relay
invocation, and the direct `python scripts/ml/train_exit_head.py …` path is how
most relays run it — so locking callers leaves the common path open and must be
redone for the next caller. Locking the entrypoint covers all of them at once.
The guard therefore asserts: **every entrypoint in `HEAVY_ENTRYPOINTS` contains
a resolved call to the queue helper.**

⚠️ NOT A PRESENCE-ONLY MARKER CHECK. The direct lesson from
`new-table-wiring-guard`, whose `# data-wiring:` marker made the cheapest way to
silence a real finding *naming a table that does not exist*: a guard cheaper to
lie to than to satisfy is worse than no guard. So this resolves an actual
**call node** via AST — `take_heavy_queue(...)` or `acquire_heavy_lock(...)` —
inside a function body. A comment saying the file locks, a string mentioning the
helper, or a bare import all FAIL.

DRIFT — the enumeration must not silently go stale. A guard over a hand-written
list answers "are the listed ones locked", never "is the list complete", and an
unasserted denominator is exactly the sub-class C defect CLAUDE.md names. So the
second pass CLASSIFIES every `__main__` script under `scripts/ml/` and
`scripts/research/`: anything that imports a heavy ML library (lightgbm /
xgboost / sklearn / torch) must appear in `HEAVY_ENTRYPOINTS` **or** in
`DECLARED_LIGHT` with a stated reason. A new heavy trainer added tomorrow fails
this guard until someone classifies it.

Exit 0 clean, 1 on any gap. `--self-test` proves the failure path fires.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# The queue helpers. A resolved call to either satisfies the guard.
_LOCK_CALLS = {"take_heavy_queue", "acquire_heavy_lock"}

# Heavy trainer entrypoints: memory-heavy jobs that run on the 6 GB trainer and
# are invoked DIRECTLY (not through `python -m ml`, which ml/cli.py already
# locks, and not through a shell timer wrapper, which takes the flock itself).
HEAVY_ENTRYPOINTS = [
    "scripts/ml/train_exit_head.py",
    "scripts/ml/train_entry_head.py",
    "scripts/ml/build_exit_head_dataset.py",
    # Found by THIS GUARD's drift pass, not by the hand enumeration that
    # preceded it — the backlog row's "7 scripts in scope" missed it. It runs
    # three `lgb.train` arms, so it fits models and is heavy by the protocol's
    # own definition. Recorded here because it is the direct evidence that a
    # hand-written list needs a completeness pass behind it.
    "scripts/ml/spike_a_pooled_labels.py",
]

# Scripts that import a heavy ML library but are NOT heavy trainer jobs. Each
# needs a reason, so "not heavy" is a stated claim rather than an omission.
#
# THE LINE IS "DOES IT FIT", checked by reading the code rather than by the
# filename. Both entries below load an already-trained booster and call
# `.predict()`; neither allocates a training matrix. That is a real distinction
# on a 6 GB box — but it is a claim about today's code, so if either grows a
# `.fit(`/`lgb.train(` it belongs above, and the reason recorded here is what
# makes that reviewable.
DECLARED_LIGHT = {
    "scripts/ml/export_exit_head.py":
        "reads a trained model and writes an artifact; fits nothing. Its heavy "
        "work happens inside train_exit_head.py, which locks.",
    "scripts/ml/exit_head_replay.py":
        "SCORING, not fitting: loads the published exit-head artifact via "
        "lgb.Booster(model_str=...) and calls booster.predict() per trade. Its "
        "footprint is one booster plus a trade frame, not a training matrix.",
}

_HEAVY_LIBS = {"lightgbm", "xgboost", "sklearn", "torch"}


def _module_root(name: str | None) -> str:
    return (name or "").split(".")[0]


def imports_heavy_lib(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(_module_root(a.name) in _HEAVY_LIBS for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if _module_root(node.module) in _HEAVY_LIBS:
                return True
    return False


def is_main_script(tree: ast.AST) -> bool:
    """Has an `if __name__ == "__main__":` guard — i.e. it is run, not imported."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for sub in ast.walk(node.test):
            if isinstance(sub, ast.Name) and sub.id == "__name__":
                return True
    return False


def calls_the_queue(tree: ast.AST) -> bool:
    """A RESOLVED call node, not a mention.

    Deliberately requires `ast.Call` — a string, a comment, or an import of the
    helper without calling it does not count.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (fn.id if isinstance(fn, ast.Name)
                else fn.attr if isinstance(fn, ast.Attribute) else None)
        if name in _LOCK_CALLS:
            return True
    return False


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def scan() -> list[str]:
    gaps: list[str] = []

    # Pass 1 — every declared heavy entrypoint actually locks.
    for rel in HEAVY_ENTRYPOINTS:
        path = REPO / rel
        if not path.is_file():
            gaps.append(f"{rel}: declared a heavy entrypoint but the file does "
                        f"not exist — fix the list rather than deleting the check")
            continue
        tree = _parse(path)
        if tree is None:
            gaps.append(f"{rel}: could not be parsed, so the guard could not "
                        f"look — this is NOT a pass")
            continue
        if not calls_the_queue(tree):
            gaps.append(
                f"{rel}: heavy trainer entrypoint with no resolved call to "
                f"{' / '.join(sorted(_LOCK_CALLS))}. It can collide with a "
                f"training cycle on the 6 GB box. Add "
                f"`take_heavy_queue(\"<label>\")` right after parse_args and "
                f"BIND the result (the flock releases when the fd closes). See "
                f"scripts/ml/_heavy_queue.py.")

    # Pass 2 — the enumeration is complete. A list nobody re-derives goes stale,
    # and a guard over a stale list reports a denominator it never measured.
    declared = set(HEAVY_ENTRYPOINTS) | set(DECLARED_LIGHT)
    for d in ("scripts/ml", "scripts/research"):
        for path in sorted((REPO / d).rglob("*.py")):
            rel = str(path.relative_to(REPO))
            if rel in declared or path.name.startswith("_"):
                continue
            tree = _parse(path)
            if tree is None or not is_main_script(tree):
                continue
            if imports_heavy_lib(tree) and not calls_the_queue(tree):
                gaps.append(
                    f"{rel}: runnable script importing a heavy ML library, "
                    f"classified as NEITHER a heavy entrypoint nor declared "
                    f"light, and it does not take the queue. Add it to "
                    f"HEAVY_ENTRYPOINTS (and lock it) or to DECLARED_LIGHT "
                    f"WITH A REASON — silence here is what let 6 of 7 scripts "
                    f"bypass the protocol unnoticed.")
    return gaps


def _self_test() -> int:
    """Plant the defect this guard exists to catch and require it to fire."""
    import tempfile
    import textwrap

    with tempfile.TemporaryDirectory() as td:
        good = Path(td) / "good.py"
        good.write_text(textwrap.dedent("""
            import lightgbm
            def main():
                _h = take_heavy_queue("x")
            if __name__ == "__main__":
                main()
        """))
        bad = Path(td) / "bad.py"
        bad.write_text(textwrap.dedent("""
            import lightgbm
            # this file totally take_heavy_queue()s, honest
            HELP = "call take_heavy_queue first"
            def main():
                pass
            if __name__ == "__main__":
                main()
        """))
        g, b = _parse(good), _parse(bad)
        if not calls_the_queue(g):
            print("::error::self-test FAILED — a real call was not resolved",
                  file=sys.stderr)
            return 1
        if calls_the_queue(b):
            print("::error::self-test FAILED — a COMMENT and a STRING mentioning "
                  "the helper satisfied the guard. That is the presence-only "
                  "marker failure this guard was written to avoid.",
                  file=sys.stderr)
            return 1
        if not (imports_heavy_lib(b) and is_main_script(b)):
            print("::error::self-test FAILED — the drift classifier would not "
                  "even consider this file", file=sys.stderr)
            return 1
    print("self-test OK — a mention does not pass; only a resolved call does.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="print what is covered, then check")
    a = ap.parse_args(argv[1:])

    if a.self_test:
        return _self_test()

    if a.list:
        print(f"heavy entrypoints ({len(HEAVY_ENTRYPOINTS)}):")
        for rel in HEAVY_ENTRYPOINTS:
            print(f"  {rel}")
        print(f"declared light ({len(DECLARED_LIGHT)}):")
        for rel, why in sorted(DECLARED_LIGHT.items()):
            print(f"  {rel} — {why}")

    gaps = scan()
    if gaps:
        for g in gaps:
            print(f"::error::trainer-heavy-lock-guard: {g}", file=sys.stderr)
        print(f"\n{len(gaps)} gap(s). Protocol: "
              f"docs/claude/trainer-resource-protocol.md § Rule 1.",
              file=sys.stderr)
        return 1
    print(f"trainer-heavy-lock guard: OK — {len(HEAVY_ENTRYPOINTS)} heavy "
          f"entrypoint(s) all take the queue, and no unclassified heavy script "
          f"under scripts/{{ml,research}}/ bypasses it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
