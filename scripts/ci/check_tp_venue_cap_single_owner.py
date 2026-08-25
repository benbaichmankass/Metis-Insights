#!/usr/bin/env python3
"""tp-venue-cap-single-owner — the TP clamp must ALIAS the owner, not restate it.

WHY THIS EXISTS
---------------
`src/runtime/tp_venue_cap.py` owns the venue take-profit clamp (``0.099``, Bybit
ErrCode 10001). Before 2026-08-25 that value was declared **thirteen** times under
**three** names -- ``_TP_SENTINEL_CAP_PCT`` (4 strategy units + position_telemetry),
``TP_VENUE_CAP_PCT`` (target_expectation + 2 research scripts) and
``LIVE_TP_CAP_PCT`` (5 scripts) -- with **no import, no test and no guard** binding
any of them. `m20_fleet_exit_sweep.py` stated the consequence itself, and was right:

    "NOTHING CHECKS THAT THIS STILL MATCHES THE LIVE VALUE. ... If the live
    constant moves, this silently keeps measuring the OLD book, and the sweep
    will look correct while doing it."

This guard is the missing check. It is the sibling of `cost-model-single-owner`,
and it reads the **expression**, not the name, for the reason that guard records:
an alias is the remedy, not the offence -- a name-driven sweep would "fix" files
that are already correct.

⚠️ THERE IS NO GRANDFATHER REGISTER, DELIBERATELY. `cost-model-single-owner` needs
`KNOWN_DUPLICATES` because its 11 duplicates were not migrated. All 13 here were,
so the clean population is 1 and any second declaration is a genuine regression.
Adding an empty debt register would invite the next session to append to it.

WHAT IS CHECKED
---------------
1. **SINGLE DECLARATION** — exactly one module-level assignment of a TP-cap name to
   a numeric literal exists, and it is in the owner.
2. **NO SHADOWING** — a file that imports the owner may not also assign a literal.
3. **THE REGISTRY IS SOURCE-TRUE** — `CLAMPING_UNIT_MODULES` must equal the set of
   unit modules whose source actually carries the cap symbol. Without this the
   registry is the allowlist restating itself, which is precisely what
   `test_the_scalp_unit_really_has_no_cap_so_the_allowlist_is_not_arbitrary`
   exists to prevent; that test stays anchored on the unit source for the same
   reason, and this check covers the set the test does not read.

Exit 0 clean, 1 with findings. `--self-test` runs planted controls.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
OWNER_REL = "src/runtime/tp_venue_cap.py"
OWNER_NAME = "TP_VENUE_CAP_PCT"
UNITS_DIR = REPO / "src" / "units" / "strategies"

#: Names meaning "the venue TP clamp". Narrow on purpose: a max-drawdown or
#: position-size cap is a different quantity and must not be swept in here.
# NOTE the leading `[A-Z_]*` is optional-width on purpose. An earlier draft
# wrote `^_?[A-Z][A-Z_]*TP...`, which forces a character BEFORE the `TP` and
# so never matched `TP_VENUE_CAP_PCT` itself -- the owner's own name. The
# planted controls caught it; a name-shaped regex is easy to get subtly wrong.
_CAP_NAMES = re.compile(r"^_?[A-Z_]*TP[A-Z_]*CAP_PCT$")

_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "build", "dist"}


def _literal_assignments(tree: ast.AST) -> list[tuple[str, int]]:
    """Module-level `NAME = <numeric literal>` for TP-cap names."""
    out: list[tuple[str, int]] = []
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None or not isinstance(value, ast.Constant):
            continue
        if not isinstance(value.value, (int, float)) or isinstance(value.value, bool):
            continue
        for t in targets:
            if isinstance(t, ast.Name) and _CAP_NAMES.match(t.id):
                out.append((t.id, node.lineno))
    return out


def _imports_owner(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith("tp_venue_cap"):
                return True
    return False


def _py_files() -> list[pathlib.Path]:
    return [p for p in REPO.rglob("*.py")
            if not _SKIP_DIRS & set(p.relative_to(REPO).parts)]


def scan() -> list[str]:
    findings: list[str] = []
    declarers: list[str] = []
    for path in sorted(_py_files()):
        rel = path.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        lits = _literal_assignments(tree)
        if not lits:
            continue
        declarers.append(rel)
        if rel == OWNER_REL:
            continue
        for name, line in lits:
            findings.append(
                f"{rel}:{line}: `{name}` is assigned a numeric literal. The venue "
                f"TP clamp has ONE owner ({OWNER_REL}); import it instead:\n"
                f"    from src.runtime.tp_venue_cap import "
                f"{OWNER_NAME} as {name}")
            if _imports_owner(tree):
                findings.append(
                    f"{rel}:{line}: SHADOWING — this file imports the owner AND "
                    f"assigns `{name}` a literal. Two homes, one name.")

    if OWNER_REL not in declarers:
        findings.append(
            f"{OWNER_REL}: the owner declares no TP-cap constant. Either it moved "
            f"(update this guard) or the single source of truth is gone.")

    findings.extend(_check_registry())
    return findings


def _check_registry() -> list[str]:
    """`CLAMPING_UNIT_MODULES` must match what the unit sources actually carry."""
    try:
        import sys
        sys.path.insert(0, str(REPO))
        from src.runtime.tp_venue_cap import CLAMPING_UNIT_MODULES
    except Exception as exc:  # pragma: no cover - import failure is itself a finding
        return [f"{OWNER_REL}: could not import CLAMPING_UNIT_MODULES: {exc!r}"]

    if not UNITS_DIR.is_dir():
        return [f"{UNITS_DIR}: unit directory missing — registry unverifiable"]

    observed = {p.stem for p in UNITS_DIR.glob("*.py")
                if "_TP_SENTINEL_CAP_PCT" in p.read_text(encoding="utf-8")}
    # Positive control: a probe finding nothing proves nothing.
    if not observed:
        return [f"{UNITS_DIR}: the probe finds the cap symbol in NO unit module — "
                f"it is broken, so any comparison below would be meaningless."]

    declared = set(CLAMPING_UNIT_MODULES)
    out = []
    for extra in sorted(declared - observed):
        out.append(f"{OWNER_REL}: CLAMPING_UNIT_MODULES lists `{extra}`, whose unit "
                   f"source does not carry the cap. The registry is stale.")
    for missing in sorted(observed - declared):
        out.append(f"{OWNER_REL}: `{missing}` carries the cap but is absent from "
                   f"CLAMPING_UNIT_MODULES — consumers keyed on the registry will "
                   f"under-claim on it (the 2026-08-16 equity-leg failure).")
    return out


def _self_test() -> int:
    ok = total = 0

    def chk(label: str, got, want) -> None:
        nonlocal ok, total
        total += 1
        if got == want:
            ok += 1
        else:
            print(f"  FAIL {label}: got {got!r} want {want!r}")

    def lits(src: str):
        return _literal_assignments(ast.parse(src))

    # planted positives — each MUST fire
    chk("bare literal fires", bool(lits("LIVE_TP_CAP_PCT = 0.099")), True)
    chk("underscored fires", bool(lits("_TP_SENTINEL_CAP_PCT = 0.099")), True)
    chk("venue-named fires", bool(lits("TP_VENUE_CAP_PCT = 0.1")), True)
    chk("int literal fires", bool(lits("TP_VENUE_CAP_PCT = 1")), True)
    chk("annotated fires", bool(lits("TP_VENUE_CAP_PCT: float = 0.099")), True)
    # planted negatives — each MUST stay silent
    chk("alias is clean", lits(
        "from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT as LIVE_TP_CAP_PCT"), [])
    chk("attribute alias clean", lits("LIVE_TP_CAP_PCT = owner.TP_VENUE_CAP_PCT"), [])
    chk("unrelated cap clean", lits("MAX_DD_CAP_PCT = 0.06"), [])
    chk("default arg clean", lits("def f(cap_pct: float = TP_VENUE_CAP_PCT): pass"), [])
    chk("nested assign clean", lits("def f():\n    TP_VENUE_CAP_PCT = 0.099"), [])
    chk("owner import detected", _imports_owner(ast.parse(
        "from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT")), True)
    chk("unrelated import undetected", _imports_owner(ast.parse("import json")), False)
    # the real repo must be clean
    chk("live repo scan is clean", scan(), [])
    print(f"self-test: {ok}/{total} passed")
    return 0 if ok == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tp-venue-cap-single-owner guard")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    findings = scan()
    for f in findings:
        print(f"tp-venue-cap-single-owner: {f}")
    if findings:
        print(f"\n{len(findings)} finding(s).")
        return 1
    print("tp-venue-cap-single-owner: clean (1 owner, 0 duplicate declarations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
