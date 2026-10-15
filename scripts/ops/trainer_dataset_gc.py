"""Garbage-collect unreferenced dataset version dirs under datasets-out/.

Written for P1.4 of the 2026-07-31 audit plan; its disk figures went stale
twice (the "45 GB root at 86%" of 2026-07-31, then the 2026-08-25 pre-resize
reading). They are removed from this docstring on purpose: **re-measure,
never quote** — `df` and `du -sb datasets-out/*` through the trainer-vm-diag
relay, or this tool's own byte totals.

Last measured 2026-10-15 (trainer-vm-diag #18154, HEAD 381c0cea7), for dating
the next reading only: root 47.3 of 103.9 GB used (46%), datasets-out 12.6 GB,
of which market_features 10.7 GB. The largest market_features versions are
manifest-pinned, so a GC of unreferenced versions frees little; the format
change (data.parquet beside data.jsonl, `ml/datasets/parquet_sidecar.py`) is
the other half of DATA-STORE-PARQUET-GC.

Safety model — a version dir (`<family>/<scope>/<tf>/<version>`) is KEPT, with
the reason printed, when ANY of these holds:
  - `manifest`  — a manifest in ml/configs/*.yaml names it;
  - `nightly`   — the nightly builder writes it: its version is
    --keep-versions (default v001,v002), or its literal path appears in
    --builder-scripts (default build_trainer_datasets.sh);
  - `input_of`  — the metadata.json of a KEPT version names its path (e.g. a
    market_features build's market_raw_path / forecast_path): deleting an
    input would break the rebuild of something kept. Resolved to a fixpoint;
  - `repo_ref`  — its `<family>/<scope>/<tf>/<version>` path appears in a
    code/config/research file under --ref-roots (scripts, ml, config,
    research, src, .github);
  - `doc_ref`   — that path appears, as a FIXED STRING, in any text file
    under --doc-roots (docs) outside --doc-exclude (docs/archive): an
    evidence record or sprint log naming a version as its data source
    (manager 2026-10-15, after a hand strike caught two such versions —
    market_raw/MES/5m/v001 and market_features/BTCUSDT/1h/v001 — that the
    code-only check had passed as candidates);
  - `young`     — its NEWEST FILE is younger than --min-age-days. (The dir
    mtime is not used: a nightly `open("w")` rewrite of data.jsonl does not
    touch it, so a dir rebuilt last night can read as months old.)
Everything else is a candidate, listed with its measured size. Dirs that are
not four levels deep (funding_oi/<SYM>/<ver>, macro/<SYM>/<ver>) are never
scanned, so never deleted.

Default is REPORT-ONLY; --apply deletes. --list-kept also prints every kept
dir with its reasons. The summary carries scanned/kept/candidate counts and
byte totals so an empty result is distinguishable from a scan that saw
nothing.

Usage:
    python scripts/ops/trainer_dataset_gc.py \
        [--datasets-root datasets-out] [--configs ml/configs] \
        [--min-age-days 14] [--keep-versions v001,v002] [--list-kept] [--apply]
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
# A dataset path anywhere in text: <family>/<scope>/<tf>/<vNNN>.
_PATH_RE = re.compile(r"([A-Za-z0-9_]+)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_]+)/(v\d+)(?![A-Za-z0-9])")
_DEFAULT_BUILDER_SCRIPTS = ("scripts/ops/build_trainer_datasets.sh",)
_DEFAULT_REF_ROOTS = ("scripts", "ml", "config", "research", "src", ".github")
# Run outputs (experiment dirs, the registry, logs) copy manifests verbatim: a
# historical run naming a version is not a consumer of it.
_SKIP_DIRS = (".git", "__pycache__", ".venv", "experiments-runs", "registry-store",
              "runtime_logs", "datasets-out")
_MAX_REF_FILE_BYTES = 5_000_000
_TEXT_EXT = (".py", ".sh", ".yaml", ".yml", ".json", ".jsonl", ".toml", ".txt", ".cfg")


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


def _paths_in(text: str) -> set[tuple[str, str, str, str]]:
    return {m.groups() for m in _PATH_RE.finditer(text)}


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def repo_refs(roots: list[str], skip: set[str]) -> dict[tuple[str, str, str, str], set[str]]:
    """Dataset paths named in code/config/research files -> the files naming them."""
    refs: dict[tuple[str, str, str, str], set[str]] = {}
    skip_abs = {os.path.abspath(s) for s in skip}
    for root in roots:
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for f in files:
                p = os.path.join(dirpath, f)
                if not f.endswith(_TEXT_EXT) or os.path.abspath(p) in skip_abs:
                    continue
                try:
                    if os.path.getsize(p) > _MAX_REF_FILE_BYTES:
                        continue
                except OSError:
                    continue
                for key in _paths_in(_read(p)):
                    refs.setdefault(key, set()).add(p)
    return refs


def doc_refs(keys: list[tuple[str, str, str, str]], roots: list[str],
             exclude: list[str]) -> dict[tuple[str, str, str, str], set[str]]:
    """Fixed-string search for each `<family>/<scope>/<tf>/<version>` in docs.

    Every file type is read (docs are mostly .md); `exclude` prefixes and
    files over the size cap are skipped.
    """
    needles = {k: "/".join(k) for k in keys}
    excl = [os.path.abspath(e) for e in exclude]
    refs: dict[tuple[str, str, str, str], set[str]] = {}
    for root in roots:
        for dirpath, dirnames, files in os.walk(root):
            here = os.path.abspath(dirpath)
            if any(here == e or here.startswith(e + os.sep) for e in excl):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for f in files:
                p = os.path.join(dirpath, f)
                try:
                    if os.path.getsize(p) > _MAX_REF_FILE_BYTES:
                        continue
                except OSError:
                    continue
                text = _read(p)
                for k, needle in needles.items():
                    if needle in text:
                        refs.setdefault(k, set()).add(p)
    return refs


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _newest_mtime(path: str) -> float:
    newest = os.path.getmtime(path)
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, f)))
            except OSError:
                pass
    return newest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets-root", default="datasets-out")
    ap.add_argument("--configs", default="ml/configs")
    ap.add_argument("--min-age-days", type=float, default=14.0)
    ap.add_argument("--keep-versions", default="v001,v002",
                    help="comma-separated nightly versions always kept")
    ap.add_argument("--builder-scripts", default=",".join(_DEFAULT_BUILDER_SCRIPTS),
                    help="scripts whose literal dataset paths are nightly targets")
    ap.add_argument("--ref-roots", default=",".join(_DEFAULT_REF_ROOTS),
                    help="dirs searched for a textual reference to a version path")
    ap.add_argument("--doc-roots", default="docs",
                    help="dirs fixed-string searched for a version path (any file type)")
    ap.add_argument("--doc-exclude", default="docs/archive",
                    help="comma-separated prefixes skipped by the doc search")
    ap.add_argument("--list-kept", action="store_true",
                    help="also print every kept dir with its reasons")
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
    builder_scripts = [s for s in args.builder_scripts.split(",") if s.strip()]
    nightly_paths: set[tuple[str, str, str, str]] = set()
    for s in builder_scripts:
        nightly_paths |= _paths_in(_read(s))
    refs = repo_refs([r for r in args.ref_roots.split(",") if os.path.isdir(r)],
                     skip=set(builder_scripts) | {__file__})
    now = dt.datetime.now(dt.timezone.utc).timestamp()

    # Pass 1: every version dir, its own (non-dependency) keep reasons, and
    # the dataset paths its metadata.json names as inputs.
    dirs: dict[tuple[str, str, str, str], dict] = {}
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
                    key = (family, scope, tf, version)
                    age_days = (now - _newest_mtime(vdir)) / 86400.0
                    reasons: list[str] = []
                    if key in pins:
                        reasons.append("manifest")
                    if version in keep_versions or key in nightly_paths:
                        reasons.append("nightly")
                    if key in refs:
                        reasons.append("repo_ref:" + ",".join(sorted(refs[key])[:3]))
                    if age_days < args.min_age_days:
                        reasons.append("young")
                    inputs = _paths_in(_read(os.path.join(vdir, "metadata.json"))) - {key}
                    dirs[key] = {"path": vdir, "age_days": round(age_days, 1),
                                 "reasons": reasons, "inputs": inputs}

    # Pass 1b: docs (evidence records, sprint logs) naming a version path.
    drefs = doc_refs(list(dirs), [r for r in args.doc_roots.split(",") if os.path.isdir(r)],
                     [e for e in args.doc_exclude.split(",") if e.strip()])
    for key, files in drefs.items():
        dirs[key]["reasons"].append("doc_ref:" + ",".join(sorted(files)[:3]))

    # Pass 2: anything a KEPT dir names as an input is kept too (fixpoint).
    changed = True
    while changed:
        changed = False
        for key, d in dirs.items():
            if not d["reasons"]:
                continue
            for inp in d["inputs"]:
                dep = dirs.get(inp)
                if dep is not None and not dep["reasons"]:
                    dep["reasons"].append("input_of:" + "/".join(key))
                    changed = True

    kept_rows = [(k, d) for k, d in dirs.items() if d["reasons"]]
    candidates = [{"path": d["path"], "bytes": _dir_size(d["path"]),
                   "age_days": d["age_days"]}
                  for d in dirs.values() if not d["reasons"]]

    if args.list_kept:
        for _k, d in kept_rows:
            print(json.dumps({"status": "gc_kept", "path": d["path"],
                              "mb": round(_dir_size(d["path"]) / 1e6, 1),
                              "reasons": d["reasons"]}))
    reclaim = sum(c["bytes"] for c in candidates)
    for c in sorted(candidates, key=lambda c: -c["bytes"]):
        print(json.dumps({"status": "gc_candidate", **c,
                          "mb": round(c["bytes"] / 1e6, 1)}))
        if args.apply:
            shutil.rmtree(c["path"], ignore_errors=True)

    print(json.dumps({
        "status": "gc_summary",
        "mode": "APPLIED" if args.apply else "report-only",
        "scanned": len(dirs),
        "kept": len(kept_rows),
        "candidates": len(candidates),
        "reclaim_gb": round(reclaim / 1e9, 2),
        "manifest_pins": len(pins),
        "manifests_parsed": int(stats_row[1]),
        "manifests_total": int(stats_row[2]),
        "nightly_literal_paths": len(nightly_paths),
        "repo_ref_paths": len(refs),
        "doc_ref_versions": len(drefs),
        "min_age_days": args.min_age_days,
        "keep_versions": sorted(keep_versions),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
