#!/usr/bin/env python3
# wiring: manual-only - a CENSUS, run on demand and quoted in a backlog row or
# a review. STILL NOT registered in scripts/ci/run_guards.py as of row E4
# (docs/claude/work/MANAGER-CHECKLIST.json, 2026-09-25): row E4 drained 16 of
# the 17 sites BL-20260912-FIFTEEN-MORE-HARNESSES... named, but
# scripts/ml/build_calibration_corpus.py -- the 17th -- sits outside
# scripts/ci/check_pr_landing.py's TIER1_SURFACE (scripts/ml/** is not on it),
# so wiring it could not self-land in the same PR without either widening that
# guard's allowlist (which fires R12 and forces a human-read hold on the PR
# that does it) or holding this whole PR for one file. Deferred rather than
# either. main()'s exit code DOES already reflect the count (this module was
# changed to fail closed at >0 sites, in case a future session gates it before
# reading this note) -- what is missing is only the run_guards.py
# registration, held until a follow-up PR lands the 17th site and the count is
# genuinely 0. See docs/reference/backtest-data-loading.md.
"""How many harnesses let `--symbol` be a LABEL over a defaulted data file?

THE SHAPE
---------
A CLI that declares BOTH ``--symbol`` and a ``--data`` whose default resolves to
a CONCRETE FILE cannot let the symbol constrain the data: argparse supplies the
file, the symbol decorates the output, and nothing compares them. That is
UNPROVENANCED DIAGNOSTIC OUTPUT **sub-class B** (implicit input selection) from
``CLAUDE.md``, and its measured consequence on this tree was a complete
result set - 144 trades, a win rate, a net-R - headed ``SOLUSDT`` and computed
from the BTC fixture, exit 0.

``BL-20260813-HARNESS-SYMBOL-IS-A-LABEL-DATA-DEFAULTS-TO-BTC`` named **two**
instances. This script exists because nobody had asked how many there are, and
"two" was an artefact of which two a session happened to run.

WHY AST AND NOT GREP - AND THIS IS NOT PEDANTRY, IT CHANGED THE ANSWER
---------------------------------------------------------------------
Two regex probes written minutes apart over the same tree returned **20** and
**10**. The first matched ``--data-dir``/``--dataset-version`` as well (a
DIRECTORY default is a different and safer shape - it is resolved per symbol);
the second required the whole ``add_argument(...)`` call to fit on one line and
silently dropped every multi-line declaration. Neither number was reportable.

Parsing the file and reading the actual ``add_argument`` calls answers the
question asked, and evaluates the common default idioms
(``os.environ.get(VAR, "file.csv")``, ``str(REPO / "x.csv")``, ``Path(...) /
"x.csv"``) rather than pattern-matching their spelling.

⚠️ WHAT IT DOES NOT CLAIM. A site it reports is a site where the symbol CANNOT
bind the data; it is NOT a claim that anyone has run it mislabelled. And a file
whose default is an expression this cannot evaluate is **not counted** - that is
*we could not look*, reported separately as ``unevaluated``, never folded into
the clean count.
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys
from collections import Counter
from typing import Optional

REPO = pathlib.Path(__file__).resolve().parents[2]
DATA_SUFFIXES = (".csv", ".parquet")


def _concrete_file(node: ast.AST) -> Optional[str]:
    """A concrete data filename this default can evaluate to, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if node.value.endswith(DATA_SUFFIXES) else None
    if isinstance(node, ast.Call):
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name in ("get", "getenv") and len(node.args) >= 2:
            return _concrete_file(node.args[1])      # the FALLBACK, not the var
        if name == "str" and node.args:
            return _concrete_file(node.args[0])
    if isinstance(node, ast.BinOp):                   # Path(...) / "x.csv"
        return _concrete_file(node.right)
    return None


def census(root: pathlib.Path) -> dict:
    files = [p for p in root.rglob("*.py")
             if ".git" not in p.parts and "site-packages" not in str(p)]
    parsed = symbol_files = 0
    hits, unevaluated, unparseable = [], [], []
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            unparseable.append(str(p.relative_to(root)))
            continue
        parsed += 1
        flags: dict[str, Optional[ast.AST]] = {}
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call)
                    and getattr(n.func, "attr", "") == "add_argument"):
                continue
            names = [a.value for a in n.args
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            dflt = next((k.value for k in n.keywords if k.arg == "default"), None)
            for nm in names:
                flags[nm] = dflt
        if "--symbol" not in flags:
            continue
        symbol_files += 1
        dflt = flags.get("--data")
        if dflt is None:
            continue                                   # explicit-or-nothing: fixed
        if isinstance(dflt, ast.Constant) and dflt.value is None:
            continue
        fn = _concrete_file(dflt)
        rel = str(p.relative_to(root))
        if fn:
            hits.append({"file": rel, "fallback": fn})
        else:
            unevaluated.append({"file": rel, "default": ast.unparse(dflt)[:80]})
    return {
        "files_parsed": parsed,
        "files_unparseable": unparseable,
        "files_declaring_symbol": symbol_files,
        "bound_to_a_concrete_file": hits,
        "unevaluated_defaults": unevaluated,
        "fallback_histogram": dict(Counter(h["fallback"] for h in hits)),
    }


def _self_test() -> int:
    """Planted controls, graded in BOTH directions."""
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"  self-test {'ok  ' if cond else 'FAIL'}: {label}")

    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        (d / "bad_inline.py").write_text(
            'import argparse\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--symbol", default="BTCUSDT")\n'
            'p.add_argument("--data", default="data/fixture.csv")\n')
        (d / "bad_envfallback.py").write_text(
            'import argparse, os\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--symbol")\n'
            'p.add_argument(\n'
            '    "--data",\n'
            '    default=os.environ.get("BACKTEST_DATA_PATH", "data/fixture.csv"))\n')
        (d / "good_none.py").write_text(
            'import argparse\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--symbol", default=None)\n'
            'p.add_argument("--data", default=None)\n')
        (d / "good_datadir.py").write_text(
            'import argparse\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--symbol")\n'
            'p.add_argument("--data-dir", default="data")\n')
        (d / "no_symbol.py").write_text(
            'import argparse\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--data", default="data/fixture.csv")\n')
        (d / "unevaluated.py").write_text(
            'import argparse\n'
            'def pick(): return "x.csv"\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--symbol")\n'
            'p.add_argument("--data", default=pick())\n')
        (d / "broken.py").write_text("def (:\n")

        r = census(d)
        found = {h["file"] for h in r["bound_to_a_concrete_file"]}
        check("an inline file default is FOUND", "bad_inline.py" in found)
        check("a MULTI-LINE env-fallback default is FOUND (the regex blind spot)",
              "bad_envfallback.py" in found)
        check("default=None is NOT reported (this is the fixed shape)",
              "good_none.py" not in found)
        check("--data-dir is NOT reported (a directory is a different, safer shape)",
              "good_datadir.py" not in found)
        check("a file with no --symbol is NOT reported",
              "no_symbol.py" not in found)
        une = {u["file"] for u in r["unevaluated_defaults"]}
        check("an UNEVALUATABLE default is reported separately, never as clean",
              "unevaluated.py" in une and "unevaluated.py" not in found)
        check("an unparseable file is NAMED, never silently skipped",
              "broken.py" in r["files_unparseable"])
        check("the denominator is reported", r["files_declaring_symbol"] >= 5)

    print("symbol-data-binding census self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=str(REPO))
    a = ap.parse_args(argv[1:])
    if a.self_test:
        return _self_test()
    r = census(pathlib.Path(a.root))
    hits = len(r["bound_to_a_concrete_file"])
    if a.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 1 if hits else 0
    print(f"symbol-data binding census over {a.root}")
    print(f"  {r['files_parsed']} .py parsed · "
          f"{r['files_declaring_symbol']} declare `--symbol` (the denominator)")
    print(f"  {len(r['bound_to_a_concrete_file'])} declare `--data` with a default "
          f"resolving to a CONCRETE FILE — the symbol cannot bind the data there:")
    for h in sorted(r["bound_to_a_concrete_file"], key=lambda x: x["file"]):
        print(f"     {h['fallback']:30} {h['file']}")
    if r["unevaluated_defaults"]:
        print(f"  {len(r['unevaluated_defaults'])} default(s) this census COULD NOT "
              f"evaluate — *we did not look*, NOT a clean verdict:")
        for u in r["unevaluated_defaults"]:
            print(f"     {u['default']:40} {u['file']}")
    if r["files_unparseable"]:
        print(f"  {len(r['files_unparseable'])} file(s) would not parse: "
              f"{', '.join(r['files_unparseable'][:5])}")
    print(f"  fallback histogram: {r['fallback_histogram']}")
    if hits:
        print(f"\nsymbol-data-binding: FAIL — {hits} site(s) let `--symbol` be a "
              f"label over a defaulted `--data` file. Wire the harness through "
              f"scripts/ops/backtest_data_source.py::resolve_or_refuse rather "
              f"than re-deriving a refusal (see "
              f"docs/reference/backtest-data-loading.md).")
        return 1
    print("symbol-data-binding: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
