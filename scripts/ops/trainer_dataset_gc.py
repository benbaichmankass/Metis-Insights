"""Garbage-collect superseded dataset version dirs under datasets-out/.

P1.4 of the 2026-07-31 full-system-audit plan: the trainer's 45 GB root is at
86% and `datasets-out/` alone is 15 GB — dominated by frozen experiment
version dirs (v1xx/v5xx/v9xx probe arms) whose sweeps concluded weeks ago.
The registry + experiment runs keep the RESULTS; the raw labeled datasets are
rebuildable from the synced journal/candle history, so an unpinned, aged
version dir is reclaimable disk, not evidence.

⚠️ **THAT PARAGRAPH DESCRIBES 2026-07-31 AND IS NO LONGER THE STATE. RUNNING
THIS TOOL WILL NOT FREE THE TRAINER'S DISK** — read this before reaching for it
as the remedy (BL-20260825-TRAINER-DISK-AT-91PCT-AND-ITS-OWN-GC-RECLAIMS-ALMOST-NOTHING).

Measured report-only on the live trainer 2026-08-25 (trainer-vm-diag #10267):

    scanned 115 · kept 111 · candidates 4 · reclaim_gb 0.09
    manifest_pins 41 · manifests_parsed 76 of 76
    and at --min-age-days 30: candidates 0, reclaim_gb 0.0

**This is not a bug in the GC — it is the safety model working.** The keep-set
grew: 41 manifest pins across 76 manifests now hold 111 of 115 version dirs, so
there is almost nothing left this tool is ALLOWED to touch. A correct GC over a
fully-pinned tree reclaims nothing, and that is the right outcome.

WHERE THE SPACE ACTUALLY IS (`du -xh -d 1`, same session; 28 G under the repo of
a 45 G root):

    datasets-out         12 G   — but only 0.09 G is GC-eligible, per above
    .venv               5.4 G   — CUDA wheels; a CPU-only wheel was NOT measured
    ml/experiments-runs 4.5 G   — NO retention tool exists
    runtime_logs        3.3 G   — NO retention tool exists (2.5 G m20_exit_head)
    data                2.0 G   — trade_journal.db

So the two genuinely uncovered trees are `ml/experiments-runs` and
`runtime_logs/m2*_head`. Neither is this tool's job and neither has a tool of
its own. ⚠️ `m20_exit_head` (2.5 G) is the largest deletable-LOOKING item and
the one least safe to assume is dead — establish that nothing reads it before
proposing a deletion.

Safety model (delete only what nothing declares):
  - a version dir referenced by ANY manifest in ml/configs/*.yaml is KEPT
    (the frozen pins v513/v514/v515/v520 etc. stay);
  - the canonical nightly versions (--keep-versions, default v001,v002) are
    KEPT in every family/scope/timeframe (the daily --overwrite targets);
  - anything younger than --min-age-days (default 14, by dir mtime) is KEPT;
  - everything else is a candidate, listed with its measured size.

Default is REPORT-ONLY; --apply deletes. Always prints a summary with the
scanned/kept/candidate counts and byte totals so an empty result is
distinguishable from a scan that saw nothing.

Usage:
    python scripts/ops/trainer_dataset_gc.py \
        [--datasets-root datasets-out] [--configs ml/configs] \
        [--min-age-days 14] [--keep-versions v001,v002] [--apply]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil

# One dataset block per manifest: family/symbol_scope/timeframe/version keys.
_REF_KEYS = ("family", "symbol_scope", "timeframe", "version")
_KEY_RE = {k: re.compile(rf"^\s*{k}:\s*[\"']?([A-Za-z0-9._-]+)", re.MULTILINE)
           for k in _REF_KEYS}


def manifest_pins(configs_dir: str) -> set[tuple[str, str, str, str]]:
    """(family, scope, timeframe, version) tuples declared by manifests.

    Regex-parsed on purpose (no ml.manifest import): the GC must resolve the
    keep-set even when a manifest is broken enough that ml.manifest raises.
    A manifest missing any of the four keys contributes nothing — and the
    caller surfaces how many parsed vs not, so a parse regression shows up
    as a shrinking denominator, not as silent extra deletions.
    """
    pins: set[tuple[str, str, str, str]] = set()
    parsed = 0
    total = 0
    for name in sorted(os.listdir(configs_dir)):
        if not name.endswith(".yaml"):
            continue
        total += 1
        try:
            with open(os.path.join(configs_dir, name), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        values = {}
        for key, rx in _KEY_RE.items():
            m = rx.search(text)
            if m:
                values[key] = m.group(1)
        if len(values) == len(_REF_KEYS):
            parsed += 1
            pins.add((values["family"], values["symbol_scope"],
                      values["timeframe"], values["version"]))
    pins.add(("__manifest_parse_stats__", str(parsed), str(total), ""))
    return pins


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets-root", default="datasets-out")
    ap.add_argument("--configs", default="ml/configs")
    ap.add_argument("--min-age-days", type=float, default=14.0)
    ap.add_argument("--keep-versions", default="v001,v002",
                    help="comma-separated canonical versions always kept")
    ap.add_argument("--apply", action="store_true",
                    help="delete the candidates (default: report only)")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.datasets_root):
        print(json.dumps({
            "status": "gc_summary", "scanned": 0, "kept": 0, "candidates": 0,
            "detail": f"{args.datasets_root} is not a directory — scanned "
                      f"NOTHING (an absent result, not a clean one)",
        }))
        return 1

    pins = manifest_pins(args.configs)
    stats_row = next(p for p in pins if p[0] == "__manifest_parse_stats__")
    pins.discard(stats_row)
    keep_versions = {v.strip() for v in args.keep_versions.split(",") if v.strip()}
    now = dt.datetime.now(dt.timezone.utc).timestamp()

    scanned = kept = 0
    candidates: list[dict] = []
    for family in sorted(os.listdir(args.datasets_root)):
        fam_dir = os.path.join(args.datasets_root, family)
        if not os.path.isdir(fam_dir):
            continue
        for scope in sorted(os.listdir(fam_dir)):
            scope_dir = os.path.join(fam_dir, scope)
            if not os.path.isdir(scope_dir):
                continue
            for tf in sorted(os.listdir(scope_dir)):
                tf_dir = os.path.join(scope_dir, tf)
                if not os.path.isdir(tf_dir):
                    continue
                for version in sorted(os.listdir(tf_dir)):
                    vdir = os.path.join(tf_dir, version)
                    if not os.path.isdir(vdir):
                        continue
                    scanned += 1
                    age_days = (now - os.path.getmtime(vdir)) / 86400.0
                    pinned = (family, scope, tf, version) in pins
                    if pinned or version in keep_versions or age_days < args.min_age_days:
                        kept += 1
                        continue
                    candidates.append({
                        "path": vdir,
                        "bytes": _dir_size(vdir),
                        "age_days": round(age_days, 1),
                    })

    reclaim = sum(c["bytes"] for c in candidates)
    for c in sorted(candidates, key=lambda c: -c["bytes"]):
        print(json.dumps({"status": "gc_candidate", **c,
                          "mb": round(c["bytes"] / 1e6, 1)}))
        if args.apply:
            shutil.rmtree(c["path"], ignore_errors=True)

    print(json.dumps({
        "status": "gc_summary",
        "mode": "APPLIED" if args.apply else "report-only",
        "scanned": scanned,
        "kept": kept,
        "candidates": len(candidates),
        "reclaim_gb": round(reclaim / 1e9, 2),
        "manifest_pins": len(pins),
        "manifests_parsed": int(stats_row[1]),
        "manifests_total": int(stats_row[2]),
        "min_age_days": args.min_age_days,
        "keep_versions": sorted(keep_versions),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
