#!/usr/bin/env python3
"""Convergence GUARD: there must be exactly ONE trend-harness engine.

WHAT THIS IS NOW
----------------
This file used to be a *measurement instrument* — it ran BOTH ``backtest_trend.py``
copies over identical candles and reported how far apart they were. That job is
finished: the losing engine was retired on 2026-08-09, so there is no second
engine left to compare against. What survives is the part that has to keep
working forever — the check that stops the fork re-opening.

**The guard fails when a file named ``backtest_trend.py`` other than the
canonical ``scripts/backtest_trend.py`` exposes an engine entry point.** A
retired copy is recognised by the **absence of that entry point**, never by
reading its prose: a docstring saying "RETIRED" is a claim, an importable
``backtest`` callable is a fact, and a guard that trusts the claim is cheaper to
lie to than to satisfy (the ``new-table-wiring-guard`` lesson — a presence-only
marker made the cheapest way to silence a real finding *naming a table that does
not exist*).

When a second engine IS found, the report names the flags it declares that the
canonical engine does not — the actionable detail, and the specific regression
``BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE`` is about.

WHY A SECOND ENGINE IS A BUG AND NOT A CONVENIENCE
--------------------------------------------------
The two copies were not one engine with two flag sets. Run over identical
candles with every optional lever OFF they still disagreed about *which trades
exist*, differing on: the **trail ATR basis** (canonical freezes the ENTRY bar's
ATR; the retired copy multiplied the CURRENT bar's rolling ATR every managed
bar), an **opposite-signal flip exit** (retired copy only), **post-exit cooldown
bars** (canonical only), the **fee basis**, warm-up length, ``timeout``
semantics, and the win-rate denominator.

``src/units/strategies/trend_donchian.py`` freezes the entry ATR into the order
package's ``meta["atr"]`` and its ``monitor()`` trails off that frozen value, so
on the load-bearing exit semantic — the trail, this strategy's only profit exit
— **live matches the canonical copy**. A second engine therefore does not add a
research option; it splits fidelity from evidence, and a lever tuned on the
wrong side of that split ends up armed on real money
(``BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL`` is exactly that).

THE MEASUREMENT THIS REPLACES (kept as the record)
--------------------------------------------------
Measured 2026-08-08 by the instrument this file used to be. POPULATION:
``data/backtest_candles.csv``, BTCUSDT 2022-07-23 → 2022-07-27, resampled 5min
→ 1001 bars, n = 21–35 trades per configuration, every optional lever OFF.
Isolating the trail-ATR-basis axis **alone** moved gross R by **−34.0% / −23.0%
/ +41.2%** across three configurations — first-order and sign-unstable. Matched
runs: donchian 20 → ``29 trades / −13.187 net R``; donchian 30 → ``22 / −9.822``.
Flag matrix at convergence: 43 canonical / 36 retired / 36 shared / **0
research-only**. That ~3.5-day corpus establishes the axis is first-order and
nothing about its magnitude; the decision-grade re-sweep is
``BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL``.

Tier-1, read-only: imports modules and reads source; writes nothing but its own
``--json``.

Usage::

    python3 scripts/research/trend_harness_divergence.py            # the guard
    python3 scripts/research/trend_harness_divergence.py --self-test
    python3 scripts/research/trend_harness_divergence.py --json -

Exit 0 = exactly one engine; 1 = a second engine (or the canonical one is
missing/broken); 2 = the guard could not check (its own dependency failed) —
distinct from a finding, per ``docs/CLAUDE-RULES-CANONICAL.md`` § "could not
measure is its own outcome".
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
import textwrap
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

#: The one engine. Everything else named `backtest_trend.py` must be inert.
CANONICAL_REL = "scripts/backtest_trend.py"

#: Names that constitute "this module IS a trend backtest engine". `run_backtest`
#: is the canonical entry point; `backtest` was the retired copy's. Either one
#: being importable and callable makes a file an engine.
ENGINE_ENTRY_POINTS = ("run_backtest", "backtest")

#: Directories with no bearing on which engine the harness runs.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist"}


class GuardUnavailable(RuntimeError):
    """The guard could not perform its check (exit 2, not a finding)."""


def find_engine_files(root: str) -> List[str]:
    """Every ``backtest_trend.py`` in the tree, repo-relative, sorted."""
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if "backtest_trend.py" in filenames:
            out.append(os.path.relpath(
                os.path.join(dirpath, "backtest_trend.py"), root))
    return sorted(out)


def declared_flags(rel: str, root: Optional[str] = None) -> set:
    """The CLI flags a file actually declares (argparse call sites)."""
    with open(os.path.join(root or _REPO_ROOT, rel), encoding="utf-8") as fh:
        return set(re.findall(r"add_argument\(\s*['\"](--[a-z0-9-]+)['\"]", fh.read()))


def engine_entry_points(rel: str, root: Optional[str] = None) -> Dict[str, Any]:
    """Which engine entry points *rel* actually exposes, by importing it.

    THE DETECTION CONTRACT. A retired copy is one from which no engine entry
    point can be obtained. We do not read the file's prose, look for a marker
    comment, or trust a class name — we ask Python for the attribute and record
    what happens. The retired shim raises ``RetiredEngineError`` (an
    ``ImportError``) from a module-level ``__getattr__``, and a plain deletion
    would fail the import outright; both are "absent", which is the point.
    """
    root = root or _REPO_ROOT
    # Salt the module name with the root: the self-test audits several throwaway
    # trees whose files share these paths, and a cached sys.modules entry from an
    # earlier tree would make the probe report the PREVIOUS tree's answer.
    mod_name = ("_trend_engine_probe_" + str(abs(hash(root))) + "_"
                + rel.replace(os.sep, "_").replace(".", "_"))
    path = os.path.join(root, rel)
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:      # pragma: no cover - defensive
            return {"importable": False, "import_error": "no import spec",
                    "entry_points": []}
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
    except Exception as exc:  # allow-silent: the breadth IS the check — a copy that cannot import, for ANY reason, is a fortiori not an engine; nothing is swallowed (the error is captured in `import_error` and printed in the report + --json), and narrowing the type would let an unanticipated import failure crash the guard instead of answering its question
        return {"importable": False,
                "import_error": f"{type(exc).__name__}: {exc}".splitlines()[0],
                "entry_points": []}
    found = []
    for name in ENGINE_ENTRY_POINTS:
        try:
            attr = getattr(mod, name)
        except (AttributeError, ImportError):
            continue                                  # absent == retired
        if callable(attr):
            found.append(name)
    return {"importable": True, "import_error": None, "entry_points": found}


def audit(root: Optional[str] = None) -> Dict[str, Any]:
    """Run the guard. Returns the report; never raises on a finding."""
    root = root or _REPO_ROOT
    files = find_engine_files(root)
    if CANONICAL_REL not in files:
        raise GuardUnavailable(
            f"canonical engine {CANONICAL_REL} not found under {root} — the guard "
            "cannot check convergence against a missing baseline")

    canonical_flags = declared_flags(CANONICAL_REL, root)
    canonical = engine_entry_points(CANONICAL_REL, root)
    findings: List[Dict[str, Any]] = []

    # ASSERT THE DENOMINATOR. Without this, deleting/breaking the canonical
    # engine would leave zero engines and the guard would report a clean pass —
    # "green while measuring nothing". The canonical copy must BE an engine.
    if "run_backtest" not in canonical["entry_points"]:
        findings.append({
            "kind": "canonical_engine_missing_entry_point",
            "file": CANONICAL_REL,
            "detail": (f"{CANONICAL_REL} exposes no callable `run_backtest` "
                       f"(importable={canonical['importable']}, "
                       f"import_error={canonical['import_error']}). The guard's "
                       "baseline is gone, so 'no second engine' would be vacuous."),
        })

    others = []
    for rel in files:
        if rel == CANONICAL_REL:
            continue
        info = engine_entry_points(rel, root)
        flags = declared_flags(rel, root)
        only_here = sorted(flags - canonical_flags)
        others.append({"file": rel, "retired": not info["entry_points"],
                       "entry_points": info["entry_points"],
                       "importable": info["importable"],
                       "import_error": info["import_error"],
                       "declared_flags": len(flags),
                       "flags_only_in_this_copy": only_here})
        if info["entry_points"]:
            findings.append({
                "kind": "second_engine",
                "file": rel,
                "detail": (
                    f"{rel} exposes engine entry point(s) "
                    f"{info['entry_points']} — a second trend engine. The fork "
                    "this guard exists to prevent is re-opening."
                    + (f" It declares {len(only_here)} flag(s) the canonical "
                       f"engine does not: {only_here}." if only_here else
                       " It declares no flags the canonical engine lacks, but a "
                       "second engine is a fidelity split regardless — the two "
                       "copies disagreed about which trades exist even with "
                       "every lever OFF.")),
            })

    return {
        "canonical": {"file": CANONICAL_REL,
                      "entry_points": canonical["entry_points"],
                      "declared_flags": len(canonical_flags)},
        "population": {"files_named_backtest_trend_py": len(files),
                       "scanned": files},
        "other_copies": others,
        "findings": findings,
        "ok": not findings,
    }


def _self_test() -> bool:
    """Prove the guard is not vacuous: plant a second engine, expect a finding.

    A guard nobody has watched fail is indistinguishable from a guard that
    cannot fail. This synthesises a throwaway repo containing the canonical
    engine plus a second copy that really does expose ``backtest``, and asserts
    the guard flags it — and that a retired-shim copy is NOT flagged.
    """
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "scripts", "research"))
        # A minimal stand-in for the canonical engine (must expose run_backtest).
        with open(os.path.join(tmp, CANONICAL_REL), "w", encoding="utf-8") as fh:
            fh.write("import argparse\n"
                     "def run_backtest(df, **kw):\n    return {}\n"
                     "def _cli():\n"
                     "    p = argparse.ArgumentParser()\n"
                     "    p.add_argument('--donchian')\n")
        second = os.path.join("scripts", "research", "backtest_trend.py")

        # Case 1: a real second engine -> MUST be flagged.
        with open(os.path.join(tmp, second), "w", encoding="utf-8") as fh:
            fh.write("import argparse\n"
                     "def backtest(df, *a, **kw):\n    return []\n"
                     "def _cli():\n"
                     "    p = argparse.ArgumentParser()\n"
                     "    p.add_argument('--rolling-atr-trail')\n")
        rep = audit(tmp)
        hit = [f for f in rep["findings"] if f["kind"] == "second_engine"]
        good = bool(hit) and "--rolling-atr-trail" in str(hit)
        print(f"  self-test 1 (planted second engine is flagged, with its "
              f"extra flag named): {'PASS' if good else 'FAIL'}")
        ok &= good

        # Case 2: a retired shim -> MUST NOT be flagged, even though its source
        # still contains argparse flags the canonical engine lacks.
        with open(os.path.join(tmp, second), "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent('''\
                """RETIRED. Historical CLI mentioned add_argument('--rolling-atr-trail')."""
                class RetiredEngineError(ImportError):
                    pass
                def __getattr__(name):
                    if name.startswith("__") and name.endswith("__"):
                        raise AttributeError(name)
                    raise RetiredEngineError("retired")
                '''))
        rep = audit(tmp)
        good = rep["ok"] and rep["other_copies"][0]["retired"]
        print(f"  self-test 2 (retired shim is NOT flagged, detected by absent "
              f"entry point not by prose): {'PASS' if good else 'FAIL'}")
        ok &= good

        # Case 3: canonical engine broken -> MUST be flagged (no vacuous pass).
        with open(os.path.join(tmp, CANONICAL_REL), "w", encoding="utf-8") as fh:
            fh.write("# the engine went away\n")
        rep = audit(tmp)
        good = any(f["kind"] == "canonical_engine_missing_entry_point"
                   for f in rep["findings"])
        print(f"  self-test 3 (missing canonical engine is a finding, not a "
              f"clean pass): {'PASS' if good else 'FAIL'}")
        ok &= good
    return ok


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Guard: exactly one trend-harness engine may exist.")
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write the full report as JSON ('-' for stdout).")
    p.add_argument("--self-test", action="store_true",
                   help="Prove the guard catches a planted second engine.")
    a = p.parse_args(argv[1:])

    if a.self_test:
        print("trend-engine-convergence-guard self-test")
        return 0 if _self_test() else 1

    try:
        report = audit()
    except GuardUnavailable as exc:
        print(f"trend-engine-convergence-guard: COULD NOT CHECK — {exc}",
              file=sys.stderr)
        return 2

    c = report["canonical"]
    print(f"trend-engine-convergence-guard — canonical {c['file']} "
          f"(entry points {c['entry_points']}, {c['declared_flags']} flags); "
          f"{report['population']['files_named_backtest_trend_py']} file(s) named "
          f"backtest_trend.py scanned")
    for o in report["other_copies"]:
        state = "RETIRED (no engine entry point)" if o["retired"] else \
                f"ENGINE — exposes {o['entry_points']}"
        print(f"  {o['file']}: {state}")
    if report["ok"]:
        print("OK — exactly one trend-harness engine.")
    else:
        print(f"\nFAIL — {len(report['findings'])} finding(s):", file=sys.stderr)
        for f in report["findings"]:
            print(f"  [{f['kind']}] {f['file']}\n      {f['detail']}",
                  file=sys.stderr)

    if a.json_out:
        blob = json.dumps(report, indent=2, default=str)
        if a.json_out == "-":
            print(blob)
        else:
            with open(a.json_out, "w", encoding="utf-8") as fh:
                fh.write(blob)
            print(f"JSON -> {a.json_out}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
