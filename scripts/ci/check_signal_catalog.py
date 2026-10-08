#!/usr/bin/env python3
"""signal-catalog-guard — every input the system reads must have a catalog entry.

CATALOG: docs/reference/signal-catalog.yaml (SIGNAL-CATALOG checklist row).

KEYING RULE — what "reads an input" means here, per family. Nothing is keyed on
free-text grep of indicator names: the strategies compute indicators inline in
pandas (there is no indicator library to key on), so each family uses the one
DECLARED surface the code already has, and says plainly where there is none.

  F1 ml_feature      Every name in ``trainer_config.feature_columns`` (list) or
                     ``trainer_config.feature_column`` (singular, baselines) of
                     every ``ml/configs/*.yaml`` (retired/ excluded, as the
                     trainer excludes it), plus every literal ``*_FEATURE_COLUMNS``
                     tuple in ``ml/datasets/*_features.py`` and the CROSS_ASSET
                     tuple (built, not literal, so imported). Must match a catalog
                     ``kind: ml_feature`` entry by exact ``name`` or by an
                     fnmatch ``pattern`` (embedding families).
  F2 strategy_module Every ``src/units/strategies/*.py`` module (not ``_*`` /
                     ``__init__`` / ``smoke_test``) needs a
                     ``strategy_modules.<name>`` block. The candle columns it
                     indexes (regex ``["open"|"high"|"low"|"close"|"volume"|
                     "timestamp"]``) and the ``src.runtime.*`` / ``ml.*`` modules
                     it imports must be a SUBSET of the block's ``candle_columns``
                     / ``runtime_imports``. New column or new feed import => add it
                     (and a ``kind: derived_indicator|feed|ml_output`` entry
                     naming the module in ``used_by``).
  F3 config_id       Ids declared in YAML: macro_econ_series.series.<k> ->
                     ``cal.<k>``; macro_events.events.<k> ->
                     ``cal.macro_events.<k>``; macro_valuation
                     (instruments|context).<X>.metrics[].metric ->
                     ``macro.valuation.<X>.<metric>``; cross_asset.<SYM> ->
                     ``cross_asset.<SYM>``; regime_policy trend / trend_vol cells
                     -> ``regime.cell.<...>``; news_symbols.symbols.<B> ->
                     ``news.symbol.<B>``. Each must be a catalog ``id``.

NOT KEYED (stated, not hidden): hand-built live feature rows (vwap.py,
ict_scalp.py, turtle_soup.py, advisory_sizing.py), entry/exit-head feature lists
that live in VM artifact JSON, hardcoded regime constants (ADX-14 cut-points),
news feed URLs, and ``scripts/macro`` producers. A guard keyed on free text would
pass while checking none of them; they are catalogued by hand and listed under
``unguarded_surfaces`` in the catalog.

COULD-NOT-LOOK: if an extractor cannot run (import failure) it prints
``could_not_look`` and exits 1 — it never reports a clean pass over nothing.
Each family also prints its denominator, so a probe that found zero items is
visible. Self-test: ``--self-test`` and tests/test_check_signal_catalog.py prove
the guard fails on a synthetic uncatalogued input.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "docs" / "reference" / "signal-catalog.yaml"

COLUMN_RE = re.compile(
    r"""\[\s*["'](open|high|low|close|volume|timestamp)["']\s*\]""")
IMPORT_RE = re.compile(
    r"^\s*(?:from\s+((?:src\.runtime|ml)(?:\.[A-Za-z_0-9]+)*)\s+import"
    r"|import\s+((?:src\.runtime|ml)(?:\.[A-Za-z_0-9]+)*))", re.M)
SKIP_MODULES = {"__init__", "smoke_test"}
FEATURE_TUPLE_FILES = {
    "ml/datasets/forecast_features.py": "FORECAST_FEATURE_COLUMNS",
    "ml/datasets/macro_features.py": "MACRO_FEATURE_COLUMNS",
}
CROSS_ASSET = ("ml.datasets.cross_asset_features", "CROSS_ASSET_FEATURE_COLUMNS")


class CouldNotLook(Exception):
    pass


# ---------------------------------------------------------------- extractors
def _manifest_features(root: Path) -> Set[str]:
    names: Set[str] = set()
    for f in sorted((root / "ml" / "configs").glob("*.yaml")):
        tc = (yaml.safe_load(f.read_text()) or {}).get("trainer_config") or {}
        for n in tc.get("feature_columns") or []:
            names.add(str(n))
        one = tc.get("feature_column")
        if isinstance(one, str):
            names.add(one)
    return names


def _literal_tuple(path: Path, const: str) -> Set[str]:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == const for t in node.targets):
            value = node.value
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
              and node.target.id == const):
            value = node.value
        if value is not None:
            try:
                return {str(x) for x in ast.literal_eval(value)}
            except (ValueError, SyntaxError) as exc:
                raise CouldNotLook(f"{path}:{const} is not a literal: {exc}")
    raise CouldNotLook(f"{path} defines no {const}")


def ml_features(root: Path = REPO) -> Set[str]:
    names = _manifest_features(root)
    for rel, const in FEATURE_TUPLE_FILES.items():
        names |= _literal_tuple(root / rel, const)
    try:
        sys.path.insert(0, str(root))
        mod = importlib.import_module(CROSS_ASSET[0])
        names |= {str(x) for x in getattr(mod, CROSS_ASSET[1])}
    except Exception as exc:  # noqa: BLE001 — surfaced as could_not_look
        raise CouldNotLook(f"import {CROSS_ASSET[0]}: {exc!r}")
    return names


def scan_strategy_module(path: Path) -> Dict[str, List[str]]:
    src = path.read_text()
    cols = sorted(set(COLUMN_RE.findall(src)))
    imps = sorted({a or b for a, b in IMPORT_RE.findall(src)})
    return {"candle_columns": cols, "runtime_imports": imps}


def strategy_modules(root: Path = REPO) -> Dict[str, Dict[str, List[str]]]:
    out = {}
    for f in sorted((root / "src" / "units" / "strategies").glob("*.py")):
        if f.stem.startswith("_") or f.stem in SKIP_MODULES:
            continue
        out[f.stem] = scan_strategy_module(f)
    return out


def _load(root: Path, name: str) -> Dict[str, Any]:
    return yaml.safe_load((root / "config" / name).read_text()) or {}


def config_ids(root: Path = REPO) -> Set[str]:
    ids: Set[str] = set()
    for k in (_load(root, "macro_econ_series.yaml").get("series") or {}):
        ids.add(f"cal.{k}")
    for k in (_load(root, "macro_events.yaml").get("events") or {}):
        ids.add(f"cal.macro_events.{k}")
    val = _load(root, "macro_valuation.yaml")
    for grp in ("instruments", "context"):
        for x, body in (val.get(grp) or {}).items():
            for m in (body or {}).get("metrics") or []:
                ids.add(f"macro.valuation.{x}.{m['metric']}")
    for sym in _load(root, "cross_asset.yaml"):
        ids.add(f"cross_asset.{sym}")
    for b in (_load(root, "news_symbols.yaml").get("symbols") or {}):
        ids.add(f"news.symbol.{b}")
    pol = _load(root, "regime_policy.yaml")
    for trend in ("trending", "transitional", "chop"):
        for strat, cell in (pol.get(trend) or {}).items():
            if isinstance(cell, dict):
                ids.add(f"regime.cell.{trend}.{strat}")
    for trend, vols in (pol.get("trend_vol") or {}).items():
        for vol, strats in (vols or {}).items():
            for strat in (strats or {}):
                ids.add(f"regime.cell.{trend}.{vol}.{strat}")
    return ids


# -------------------------------------------------------------------- checks
def load_catalog(path: Path = CATALOG) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text())


def check(catalog: Dict[str, Any], root: Path = REPO,
          verbose: bool = False) -> Tuple[List[str], List[str]]:
    """Return (failures, denominators)."""
    fails: List[str] = []
    dens: List[str] = []
    inputs = catalog.get("inputs") or []
    ids = {e["id"] for e in inputs}
    exact = {e["name"] for e in inputs if e.get("kind") == "ml_feature" and "name" in e
             and "pattern" not in e}
    pats = [e["pattern"] for e in inputs if e.get("pattern")]

    feats = ml_features(root)
    dens.append(f"F1 ml_feature names read: {len(feats)}")
    if not feats:
        fails.append("F1 could_not_look: extractor found 0 ml features")
    for n in sorted(feats):
        if n not in exact and not any(fnmatch.fnmatch(n, p) for p in pats):
            fails.append(f"F1 uncatalogued ml_feature: {n}")

    mods = strategy_modules(root)
    dens.append(f"F2 strategy modules scanned: {len(mods)}")
    if not mods:
        fails.append("F2 could_not_look: found 0 strategy modules")
    declared = catalog.get("strategy_modules") or {}
    for m, got in mods.items():
        d = declared.get(m)
        if d is None:
            fails.append(f"F2 strategy module without catalog block: {m}")
            continue
        for key in ("candle_columns", "runtime_imports"):
            extra = sorted(set(got[key]) - set(d.get(key) or []))
            if extra:
                fails.append(f"F2 {m} reads uncatalogued {key}: {extra}")
    for m in declared:
        if m not in mods:
            fails.append(f"F2 catalog block for missing module: {m}")

    cids = config_ids(root)
    dens.append(f"F3 config-declared ids: {len(cids)}")
    if not cids:
        fails.append("F3 could_not_look: found 0 config ids")
    for i in sorted(cids - ids):
        fails.append(f"F3 uncatalogued config id: {i}")
    return fails, dens


def self_test() -> int:
    cat = {"inputs": [], "strategy_modules": {}}
    try:
        fails, _ = check(cat)
    except CouldNotLook as exc:
        print(f"self-test: could_not_look {exc}")
        return 1
    ok = (any(f.startswith("F1") for f in fails)
          and any(f.startswith("F2") for f in fails)
          and any(f.startswith("F3") for f in fails))
    print("self-test:", "PASS (empty catalog fails all three families)" if ok
          else "FAIL (empty catalog did not fail every family)")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    try:
        fails, dens = check(load_catalog(), verbose=a.verbose)
    except (CouldNotLook, OSError, yaml.YAMLError) as exc:
        print(f"signal-catalog-guard: could_not_look: {exc}")
        return 1
    for d in dens:
        print(d)
    for f in fails:
        print("FAIL", f)
    print(f"signal-catalog-guard: {'FAIL' if fails else 'ok'} ({len(fails)} problems)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
