#!/usr/bin/env python3
"""Recover the `vol_threshold` of an existing market_features dir BY MEASUREMENT.

WHY (BL-20260730-DATASET-DIRS-MISSING-VOL-THRESHOLD)
----------------------------------------------------
`DatasetBuilder.build` records `effective_build_params` from the kwargs it was
PASSED, so before `vol_threshold` became required a DEFAULTED value never
reached `metadata.json`. Measured on the trainer 2026-07-30: **25 of 38**
on-disk `market_features` dirs record no `vol_threshold` — including
`BTCUSDT/15m/v520`, the dataset the live advisory BTC head pins. Nobody can
read off disk which threshold defined the labels the live real-money vol gate's
head was trained on.

WHY THIS IS A MEASUREMENT, NOT A GUESS
--------------------------------------
`_label_regime` is exactly::

    if forward_vol > vol_threshold: return "volatile"
    return "range"

and `forward_log_return_vol` is an EMITTED column. So over the rows of a built
dir the true threshold is bracketed with certainty::

    max(forward_vol | label == "range")  <=  vol_threshold
    min(forward_vol | label == "volatile") >   vol_threshold

giving ``vol_threshold ∈ [lo, hi)`` where ``lo = max_range`` and
``hi = min_volatile``. That interval is derived from the data itself — it is
not an inference from "which value looks likely". When exactly one known
candidate falls inside it, the recovery is EXACT.

The alternative I explicitly rejected: "v520 reads 4.6% volatile, 0.005 also
reads ~4.6%, therefore v520 is 0.005." That is an inference from an aggregate,
it cannot distinguish nearby thresholds, and writing it into `build_params`
would MANUFACTURE PROVENANCE — the precise failure `src/runtime/provenance.py`
exists to prevent. A recovered value is stamped `vol_threshold_source:
"derived_from_labels"` WITH its bracket, so no reader can mistake it for a
value the builder recorded.

THREE OUTCOMES, AND ONLY ONE NAMES A VALUE
------------------------------------------
* ``derived`` — exactly one known candidate inside the bracket. Writes
  ``vol_threshold`` + ``vol_threshold_source="derived_from_labels"`` + the
  bracket.
* ``bracketed`` — the bracket is measured but matches NO known candidate.
  Writes the **bracket only**, with ``vol_threshold_source=
  "bracket_only_no_candidate"`` and **no** ``vol_threshold`` key, so a consumer
  keying on that field cannot silently pick up an interval midpoint as if it
  were the builder's input. This is the right artifact for MES, which builds
  with a DATA-DRIVEN median (``build_trainer_datasets.sh::mes_median_vt``) that
  by construction matches no fixed candidate — demanding one would throw away a
  real measurement. An interval is not a guess.
* ``refused`` — nothing written. Covers a **degenerate** dir (one class only,
  so the bracket is an open interval), an **ambiguous** bracket (2+ candidates
  inside), and labels that no single threshold explains.

`trend_threshold` is never recovered in any outcome: it is INERT (it affects no
emitted column — see market_features' docstring), so the labels carry zero
information about it. Claiming otherwise would be the same defect one level
down, in the tool built to repair it.

Read-only unless `--apply`. Writes only `metadata.json`, never `data.jsonl`.

    python3 scripts/ml/backfill_dataset_vol_threshold.py --datasets-root datasets-out
    python3 scripts/ml/backfill_dataset_vol_threshold.py --apply
"""
from __future__ import annotations

import argparse
import glob
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Thresholds a production path has actually passed. Every entry is SOURCED
#: from a caller or a committed record — none is reverse-engineered from the
#: brackets this script computes, which would be circular:
#:   0.001  scripts/ops/run_serious_baseline.sh
#:   0.003  the retired code default — exactly what this backfill exists to detect
#:   0.005  build_trainer_datasets.sh (Bybit) · gpu_burst defaults · ETH finetf
#:          · trainer-offload-train.yml
#:   0.01   scripts/ops/run_mes_training.sh
#:   0.004 / 0.006 / 0.007  the RG4 threshold sweep — `rg4_vt_sweep.sh`'s own
#:          usage line sweeps "0.003 0.004 0.005 0.006", and
#:          docs/sprint-logs/S-ETH-REGIME-RG4-RETRAIN-2026-06-28.md records the
#:          result "across 0.003-0.007". The vt003/vt004/vt005-pin manifests in
#:          ml/configs/ are the heads trained on those sweep datasets.
#: MES also builds with a DATA-DRIVEN median (build_trainer_datasets.sh::
#: mes_median_vt), which by construction matches no fixed candidate — those
#: dirs get a bracket and no point value, which is the honest outcome.
KNOWN_CANDIDATES: Tuple[float, ...] = (
    0.001, 0.003, 0.004, 0.005, 0.006, 0.007, 0.01,
)

VOL_COL = "forward_log_return_vol"
LABEL_COL = "regime_label"
VOLATILE = "volatile"


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if not (math.isnan(x) or math.isinf(x)) else None


def bracket_from_rows(path: Path) -> Dict[str, Any]:
    """Scan data.jsonl once → the exact ``[lo, hi)`` bracket for vol_threshold."""
    max_range: Optional[float] = None
    min_volatile: Optional[float] = None
    n = n_range = n_vol = n_usable = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            label = row.get(LABEL_COL)
            vol = _f(row.get(VOL_COL))
            if label is None or vol is None:
                continue
            n_usable += 1
            if label == VOLATILE:
                n_vol += 1
                min_volatile = vol if min_volatile is None else min(min_volatile, vol)
            else:
                n_range += 1
                max_range = vol if max_range is None else max(max_range, vol)
    return {
        "rows": n, "usable": n_usable, "n_range": n_range, "n_volatile": n_vol,
        "lo": max_range, "hi": min_volatile,
    }


def resolve(br: Dict[str, Any]) -> Dict[str, Any]:
    """Bracket → a recovered value, or an explicit refusal with its reason."""
    lo, hi = br["lo"], br["hi"]
    if br["usable"] == 0:
        return {"status": "refused", "reason":
                f"no rows carrying both {LABEL_COL} and {VOL_COL}"}
    if lo is None or hi is None:
        missing = "volatile" if hi is None else "range"
        return {"status": "refused", "reason":
                f"degenerate: no {missing} rows, so the bracket is an open "
                f"interval, not a value (n_range={br['n_range']}, "
                f"n_volatile={br['n_volatile']})"}
    if lo > hi:
        return {"status": "refused", "reason":
                f"inconsistent bracket lo={lo!r} > hi={hi!r} — the labels do "
                f"not match a single threshold on this column; do not write"}
    inside = [c for c in KNOWN_CANDIDATES if lo <= c < hi]
    if len(inside) == 1:
        return {"status": "derived", "value": inside[0], "lo": lo, "hi": hi}
    if len(inside) > 1:
        return {"status": "refused", "lo": lo, "hi": hi, "reason":
                (f"bracket [{lo!r}, {hi!r}) admits {len(inside)} known "
                 f"candidates {inside} — ambiguous, no point value written")}
    # ZERO candidates inside, but the bracket is still a MEASUREMENT — and on
    # the live trainer it is a tight one (the observed refusals pinned the
    # threshold to ~7 significant figures). MES builds with a DATA-DRIVEN
    # median (build_trainer_datasets.sh::mes_median_vt) which by construction
    # matches no fixed candidate, so demanding one would permanently discard a
    # real measurement. Record the interval, withhold the point value, and say
    # which is which. An interval is not a guess.
    return {"status": "bracketed", "lo": lo, "hi": hi, "reason":
            (f"bracket [{lo!r}, {hi!r}) matches no known candidate — likely a "
             f"data-driven threshold. Interval recorded; no point value claimed")}


def scan(datasets_root: str, family: str = "market_features") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for md_path in sorted(glob.glob(f"{datasets_root}/{family}/*/*/*/metadata.json")):
        md_file = Path(md_path)
        try:
            md = json.loads(md_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            out.append({"path": md_path, "state": "unreadable", "detail": str(exc)})
            continue
        bp = md.get("build_params") or {}
        rel = md_path.replace(f"{datasets_root}/{family}/", "").replace(
            "/metadata.json", "")
        if bp.get("vol_threshold") is not None:
            out.append({"path": md_path, "rel": rel, "state": "already_recorded",
                        "value": bp["vol_threshold"]})
            continue
        data = md_file.parent / "data.jsonl"
        if not data.is_file():
            out.append({"path": md_path, "rel": rel, "state": "no_data",
                        "detail": "metadata without data.jsonl"})
            continue
        br = bracket_from_rows(data)
        res = resolve(br)
        out.append({"path": md_path, "rel": rel, "bracket": br, **res,
                    "state": res["status"]})
    return out


def apply_one(md_path: str, value: Optional[float], lo: float, hi: float) -> None:
    """Write the recovered evidence, flagged so it can never read as recorded.

    ``value=None`` writes the BRACKET ONLY — the measurement without a point
    claim. That is the right artifact for a data-driven threshold: the interval
    is known to ~7 significant figures while no named candidate applies.
    """
    p = Path(md_path)
    md = json.loads(p.read_text(encoding="utf-8"))
    bp = dict(md.get("build_params") or {})
    # The bracket is the measurement and is always recorded.
    bp["vol_threshold_bracket"] = [lo, hi]
    if value is None:
        # No `vol_threshold` key: absence of a point value is the honest state,
        # and a consumer keying on `vol_threshold` must not silently pick up an
        # interval midpoint as though it were the builder's input.
        bp["vol_threshold_source"] = "bracket_only_no_candidate"
    else:
        bp["vol_threshold"] = value
        # The flag is the whole point: a reader must be able to tell a
        # recovered value from one the builder recorded.
        bp["vol_threshold_source"] = "derived_from_labels"
    md["build_params"] = bp
    p.write_text(json.dumps(md, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets-root", default="datasets-out")
    ap.add_argument("--family", default="market_features")
    ap.add_argument("--apply", action="store_true",
                    help="write the recovered values (default: report only)")
    a = ap.parse_args()

    rows = scan(a.datasets_root, a.family)
    if not rows:
        print(f"no {a.family} metadata under {a.datasets_root} — "
              f"nothing scanned (this is an ABSENT result, not a clean one)")
        return 1

    by_state: Dict[str, int] = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1

    print(f"scanned {len(rows)} {a.family} dir(s) under {a.datasets_root}")
    for r in rows:
        rel = r.get("rel", r["path"])
        if r["state"] == "already_recorded":
            print(f"  RECORDED  {rel:<44s} vol_threshold={r['value']}")
        elif r["state"] == "derived":
            br = r["bracket"]
            print(f"  DERIVED   {rel:<44s} vol_threshold={r['value']} "
                  f"bracket=[{br['lo']:.8g}, {br['hi']:.8g}) "
                  f"n_range={br['n_range']} n_volatile={br['n_volatile']}")
        elif r["state"] == "bracketed":
            br = r["bracket"]
            print(f"  BRACKET   {rel:<44s} vol_threshold=(no point value) "
                  f"bracket=[{br['lo']:.8g}, {br['hi']:.8g}) "
                  f"n_range={br['n_range']} n_volatile={br['n_volatile']}")
        else:
            print(f"  REFUSED   {rel:<44s} {r.get('reason') or r.get('detail')}")

    derived = [r for r in rows if r["state"] == "derived"]
    bracketed = [r for r in rows if r["state"] == "bracketed"]
    refused = [r for r in rows if r["state"] not in
               ("already_recorded", "derived", "bracketed")]
    print()
    print(f"summary: {by_state}")
    print(f"  point value recoverable exactly: {len(derived)}")
    print(f"  interval measured, no point claimed: {len(bracketed)}")
    print(f"  left wholly ABSENT on purpose: {len(refused)}")

    if not a.apply:
        print("\n(dry run — re-run with --apply to write. Values are stamped "
              "vol_threshold_source='derived_from_labels' with their bracket, "
              "never as if the builder had recorded them.)")
        return 0

    for r in derived:
        apply_one(r["path"], r["value"], r["bracket"]["lo"], r["bracket"]["hi"])
    for r in bracketed:
        apply_one(r["path"], None, r["bracket"]["lo"], r["bracket"]["hi"])
    print(f"\nAPPLIED: {len(derived)} with a point value, {len(bracketed)} "
          f"bracket-only. {len(refused)} left untouched. data.jsonl untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
