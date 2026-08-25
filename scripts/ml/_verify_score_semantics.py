#!/usr/bin/env python3
# wiring: manual-only - the empirical basis of the 2026-07-30
#     diagnostic-provenance class, run through the trainer-vm-diag relay. It
#     is a COMMITTED file rather than an inline heredoc precisely because the
#     relay preserves the cmd: block's indentation and an indented heredoc
#     terminator never closes — see its own docstring. Invoked by a human
#     pasting a relay command, by design.
"""Verify — against LIVE trainer data — that the shadow log's ``score`` is not
P(volatile), and that the fixed diagnostics report the gate's quantity.

This is the empirical basis of the whole 2026-07-30 diagnostic-provenance
class. It is a COMMITTED helper, not an inline heredoc, because the
trainer-vm-diag relay preserves the indentation of the ``cmd:`` block — an
indented heredoc terminator never closes and the script dies with
``unexpected end of file`` (learned the hard way, twice; the same note is in
``_feature_parity_probe.py``'s docstring).

Checks, in order of what they prove:

  A  For EVERY regime head in the shadow log, ``frac(score < 0.5)`` must be
     0.0000 — the signature of ``max(proba.values())`` on a 2-class head. Any
     non-multiclass head in the SAME log ranging below 0.5 is the control: it
     shows the log is not simply saturated.
  B  On the SAME logged rows, compare ``score`` against the real
     ``predict_proba(row)["volatile"]``. An INVERTED row (score high while
     p_volatile low) is direct proof the old label destroyed the meaning
     rather than merely blurring it.
  C  Do on-disk ``market_features`` dirs record ``vol_threshold`` in
     ``metadata.json::build_params``? Dirs built before the value was passed
     explicitly will not — the provenance hole that made
     ``vol_threshold`` required.

Read-only. Never writes, never touches the order path.

    python3 scripts/ml/_verify_score_semantics.py [--datasets-root datasets-out]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_LOG_CANDIDATES = (
    "runtime_logs/shadow_predictions.jsonl",
    "runtime_logs/trainer_mirror/shadow_predictions.jsonl",
    "runtime_logs/trainer_mirror/live/shadow_predictions.jsonl",
)


def _find_log() -> Optional[str]:
    for c in _LOG_CANDIDATES:
        if os.path.isfile(c):
            return c
    return None


def _check_a(log_path: str) -> int:
    print("=" * 72)
    print("A. Is the logged `score` >= 0.5 by construction for every regime head?")
    print("=" * 72)
    agg: Dict[str, Dict[str, float]] = collections.defaultdict(
        lambda: {"n": 0, "lt": 0, "mn": 9.0, "mx": -9.0})
    for line in open(log_path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, s = str(r.get("model_id") or ""), r.get("score")
        if not mid or s is None:
            continue
        try:
            s = float(s)
        except (TypeError, ValueError):
            continue
        a = agg[mid]
        a["n"] += 1
        a["lt"] += 1 if s < 0.5 else 0
        a["mn"] = min(a["mn"], s)
        a["mx"] = max(a["mx"], s)

    regime = sorted((m, a) for m, a in agg.items() if "regime" in m)
    other = sorted((m, a) for m, a in agg.items() if "regime" not in m)
    if not regime:
        print("  NO regime rows in the log — check A is UNPROVEN here, not passed.")
        return 1

    print(f"  REGIME heads ({len(regime)}) — expect frac<0.5 == 0.0000 for all:")
    violations = 0
    for m, a in regime:
        frac = a["lt"] / a["n"]
        flag = "" if frac == 0.0 else "   <-- NOT max-proba-shaped"
        if frac != 0.0:
            violations += 1
        print(f"    {m:<46s} n={int(a['n']):<7d} frac<0.5={frac:.4f} "
              f"min={a['mn']:.4f} max={a['mx']:.4f}{flag}")
    print(f"  CONTROL — non-regime heads ({len(other)}) in the SAME log; if some "
          f"range below 0.5 the log is not simply saturated:")
    for m, a in other:
        print(f"    {m:<46s} n={int(a['n']):<7d} frac<0.5={a['lt']/a['n']:.4f} "
              f"min={a['mn']:.4f} max={a['mx']:.4f}")
    below = [m for m, a in other if a["lt"] > 0]
    print(f"  => regime heads violating the max-proba signature: {violations}")
    print(f"  => control heads that DO go below 0.5: {len(below)} "
          f"{'(good — the contrast holds)' if below else '(none — contrast UNPROVEN)'}")
    return violations


def _check_b(log_path: str, per_model: int = 400) -> int:
    print()
    print("=" * 72)
    print("B. `score` vs the gate's predict_proba(row)['volatile'] on the SAME rows")
    print("=" * 72)
    try:
        from scripts.ml._regime_score_semantics import p_volatile
    except Exception as exc:  # noqa: BLE001
        print(f"  CANNOT IMPORT the shared accessor: {exc}")
        return 1

    rows: Dict[str, List[dict]] = collections.defaultdict(list)
    for line in open(log_path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = str(r.get("model_id") or "")
        if "regime" not in mid or not r.get("feature_row") or r.get("score") is None:
            continue
        if len(rows[mid]) < per_model:
            rows[mid].append(r)

    if not rows:
        print("  no regime rows carrying feature_row — check B UNPROVEN.")
        return 1

    total_inverted = 0
    for mid, rs in sorted(rows.items()):
        pairs = []
        for r in rs:
            pv = p_volatile(mid, r["feature_row"])
            if pv is not None:
                pairs.append((float(r["score"]), pv))
        if not pairs:
            print(f"  {mid:<46s} predictor UNRESOLVABLE here (no registry entry) "
                  f"— reported, never substituted")
            continue
        inverted = [(s, p) for s, p in pairs if s >= 0.5 and p < 0.5]
        equal = sum(1 for s, p in pairs if abs(s - p) < 1e-9)
        total_inverted += len(inverted)
        print(f"  {mid:<46s} n={len(pairs):<5d} "
              f"score==p_vol on {equal}/{len(pairs)} rows | "
              f"INVERTED (score>=.5 & p_vol<.5): {len(inverted)}")
        for s, p in inverted[:2]:
            print(f"      e.g. score={s:.4f}  p_volatile={p:.4f}  "
                  f"<-- printed as 'P(volatile)' this row read {s:.2f} "
                  f"when the gate saw {p:.2f}")
    print(f"  => total INVERTED rows found: {total_inverted}")
    return 0


def _check_c(datasets_root: str) -> int:
    print()
    print("=" * 72)
    print("C. Do on-disk market_features dirs record vol_threshold in metadata?")
    print("=" * 72)
    dirs = sorted(glob.glob(f"{datasets_root}/market_features/*/*/*/metadata.json"))
    if not dirs:
        print(f"  no market_features metadata under {datasets_root} — UNPROVEN here.")
        return 1
    absent = 0
    shown = 0
    by_value: Dict[str, int] = collections.Counter()
    for md_path in dirs:
        try:
            md = json.loads(Path(md_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        bp = md.get("build_params") or {}
        vt = bp.get("vol_threshold")
        by_value[str(vt)] += 1
        if vt is None:
            absent += 1
        if shown < 20:
            rel = md_path.replace(f"{datasets_root}/market_features/", "")
            print(f"    {rel:<62s} vol_threshold={vt if vt is not None else '** ABSENT **'}")
            shown += 1
    print(f"  => {len(dirs)} dirs scanned; {absent} with NO recorded vol_threshold")
    print(f"  => distinct recorded values: {dict(by_value)}")
    print("     (dirs built before the value was passed explicitly show ABSENT —")
    print("      that is the provenance hole; requiring the kwarg closes it going")
    print("      forward, it does not retro-fix existing dirs.)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets-root", default="datasets-out")
    a = ap.parse_args()

    log = _find_log()
    print(f"shadow log: {log}")
    rc = 0
    if log:
        rc |= 1 if _check_a(log) else 0
        _check_b(log)
    else:
        # State the denominator: WHICH paths were searched. "not found" with no
        # search list is indistinguishable from "did not look" — the C failure
        # mode this script exists to demonstrate. (The guard caught this line.)
        print(f"shadow log not found in any of {len(_LOG_CANDIDATES)} candidate "
              f"paths: {list(_LOG_CANDIDATES)}")
        print("=> checks A and B are UNPROVEN here, NOT passed.")
        rc = 1
    _check_c(a.datasets_root)
    print()
    print("VERIFY_SCORE_SEMANTICS_DONE")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
