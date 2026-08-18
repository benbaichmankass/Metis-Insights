#!/usr/bin/env python3
"""Read the open book as a BOOK and report its conflicts.

The CLI half of `src/runtime/portfolio_conflicts.py`. See that module for why
this exists; the short form is the operator's 2026-08-18 directive:

    "Not just the individual levers, but how we create a holistic picture for
    making actual informed decisions."

Input is a JSON array of `/api/bot/positions` rows (or an object with a
`positions` key) — a file, or stdin. This script does NOT fetch: a web session
cannot reach the VM directly (egress is firewalled at the default network
level), so the fetch is the caller's job via the diag relay, and pretending
otherwise would give this script a failure mode that reads like an empty book.

Correlation is OPTIONAL and MEASURED. `--correlation` takes a JSON object of
`{"A|B": rho}`. Without it the correlated-opposition check does not run, and
the report says `correlation_state: not_supplied` rather than reporting zero
correlated conflicts — those are different statements and only one of them is
an answer.

Exit codes: 0 = ran (whether or not conflicts were found), 2 = bad input.
A conflict is deliberately NOT a non-zero exit: this is a prompt to look, not
a gate, and wiring it as a gate would make the first false positive a reason
to stop running it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.runtime import portfolio_conflicts as pc  # noqa: E402


def load_rows(source: str) -> List[Dict[str, Any]]:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text()
    doc = json.loads(raw)
    rows = doc if isinstance(doc, list) else doc.get("positions") or doc.get("rows")
    if not isinstance(rows, list):
        raise ValueError("expected a JSON array of positions, or an object "
                         "with a 'positions'/'rows' array")
    return rows


def load_correlation(path: str | None) -> Dict[Tuple[str, str], float] | None:
    """`{"A|B": rho}` -> `{("A","B"): rho}`. `None` when not supplied."""
    if not path:
        return None
    doc = json.loads(Path(path).read_text())
    out: Dict[Tuple[str, str], float] = {}
    for key, rho in doc.items():
        a, _, b = str(key).partition("|")
        if not b:
            raise ValueError(f"correlation key {key!r} must be 'SYMBOL|SYMBOL'")
        out[(a, b)] = float(rho)
    return out


def render(rep: Dict[str, Any]) -> str:
    out = ["OPEN-BOOK CONFLICT AUDIT", "=" * 62,
           f"open positions: {rep['positions']}"]
    total = sum(rep["counts"].values())
    for kind, n in rep["counts"].items():
        out.append(f"  {kind:<24} {n}")
    out.append(f"  {'TOTAL':<24} {total}")
    out.append("")
    # The denominators. These are printed even when empty, because their
    # absence is what makes a clean report unfalsifiable.
    out.append(f"correlation_state           : {rep['correlation_state']}")
    if rep["correlation_state"] == "not_supplied":
        out.append("  ^ NOT 'no correlated opposition' -- nobody looked. Supply")
        out.append("    --correlation to answer that question.")
    if rep["correlated_pairs_unmeasured"]:
        out.append("  symbol pairs held opposite with NO measured correlation:")
        for a, b in rep["correlated_pairs_unmeasured"]:
            out.append(f"    {a} ~ {b}   (unmeasured, NOT uncorrelated)")
    out.append(f"rows with unreadable side   : {rep['rows_with_unreadable_side'] or 'none'}")
    out.append(f"rows with ungradeable stop  : {rep['rows_with_ungradeable_stop'] or 'none'}")
    out.append("")
    if not rep["conflicts"]:
        out.append("no conflicts found in the graded checks above.")
        return "\n".join(out)
    for c in rep["conflicts"]:
        out.append(f"[{c.kind}] {c.key}")
        out.append(f"  {c.detail}")
        for r in c.positions:
            out.append(f"    {str(r.get('id')):>6} {str(r.get('side')):<5} "
                       f"{str(r.get('account')):<18} {str(r.get('pattern')):<24} "
                       f"entry={r.get('entryPrice')} SL={r.get('stopLoss')}")
        out.append("")
    return "\n".join(out)


def _self_test() -> int:
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    rows = [
        {"id": 1, "symbol": "E", "side": "buy", "account": "a", "pattern": "s",
         "entryPrice": 100.0, "stopLoss": 99.0},
        {"id": 2, "symbol": "E", "side": "sell", "account": "b", "pattern": "s",
         "entryPrice": 100.0, "stopLoss": 101.0},
    ]
    rep = pc.audit(rows)
    check("self-opposing found", rep["counts"][pc.SELF_OPPOSING_STRATEGY], 1)
    text = render(rep)
    # The two denominators must appear in the rendered output, always. A report
    # that omits them when they are empty is the unstated-denominator bug.
    check("correlation state printed", "correlation_state" in text, True)
    check("not-supplied caveat printed", "nobody looked" in text, True)
    check("unreadable-side line printed", "unreadable side" in text, True)

    clean = render(pc.audit([], correlation={}))
    check("clean book says so", "no conflicts found" in clean, True)
    check("clean book still prints state", "correlation_state" in clean, True)

    check("correlation key parse",
          load_correlation(None), None)
    try:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"A|B": 0.9}')
        check("correlation parsed", load_correlation(fh.name), {("A", "B"): 0.9})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh2:
            fh2.write('{"AB": 0.9}')
        try:
            load_correlation(fh2.name)
            fails.append("a malformed correlation key must raise, not be guessed")
        except ValueError:
            pass
    except Exception as exc:  # noqa: BLE001
        fails.append(f"correlation parse: {exc}")

    for f in fails:
        print("FAIL", f)
    print(f"self-test: {8 - len(fails)}/8 passed")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("positions", nargs="?", default="-",
                    help="JSON file of /api/bot/positions rows, or - for stdin")
    ap.add_argument("--correlation", help='JSON {"A|B": rho}; omit to skip that check')
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--nominal-stop-frac", type=float, default=0.5)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    try:
        rows = load_rows(args.positions)
        corr = load_correlation(args.correlation)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rep = pc.audit(rows, corr, threshold=args.threshold,
                   nominal_stop_frac=args.nominal_stop_frac)
    if args.json:
        serialisable = dict(rep)
        serialisable["conflicts"] = [
            {"kind": c.kind, "key": c.key, "detail": c.detail,
             "position_ids": [r.get("id") for r in c.positions]}
            for c in rep["conflicts"]
        ]
        print(json.dumps(serialisable, indent=2, default=str))
    else:
        print(render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
