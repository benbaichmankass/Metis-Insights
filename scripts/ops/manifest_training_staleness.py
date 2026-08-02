"""Escalate manifests that have not produced a REGISTERED run in N days.

P1.3 of the 2026-07-31 full-system-audit plan
(`BL-20260731-AUDIT-0731-NEW-FINDINGS` items (7)/(9)): the cycle has four
independent "correctly skip this manifest" paths (OOM quarantine,
dataset-unchanged, audit-FLAGGED, empty/absent dataset) and each is
individually right — but nothing ever added them up, so a manifest could
skip EVERY cycle for months and the log would read as a clean green stream
(the outcome families sat like this since 2026-05-22). This script is the
adder-up: after each cycle it reports every roster manifest whose newest
registered run is older than the threshold, plus the always-printed
denominator summary so an empty scan can never read as a clean one.

Usage:
    python scripts/ops/manifest_training_staleness.py \
        <registry_root> <threshold_days> <manifest.yaml> [...]

Prints ready-to-emit JSONL cycle events:
  - one ``manifest_untrained_stale`` line per stale manifest
    (last_trained_at null == the model_id has 0 registered runs across the
    scanned registry files);
  - always exactly one ``training_staleness_summary`` line with
    scanned/stale/never_trained/unresolvable counts + registry_files, so
    the consumer can see what the denominator was.

Never-trained manifests get a grace window: a manifest FILE younger than the
threshold is not stale yet (a freshly-added experiment needs a first cycle,
not an alarm). `model_id` is parsed from the manifest text by regex rather
than by importing `ml.manifest` — the escalation must keep working when a
manifest is exactly the kind of broken that stops it training (the vt004
ManifestDatasetMismatch class); an unparseable manifest is counted as
`unresolvable`, never silently dropped.

Fail-open: exits 0 always; a registry/manifest read error degrades that one
entry, not the report.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sys

_MODEL_ID_RE = re.compile(r"^model_id:\s*[\"']?([A-Za-z0-9._-]+)", re.MULTILINE)
# Operator-accepted "cannot train yet — source data does not exist" waiver.
# Declared INSIDE the manifest's free-text notes: block (the manifest loader
# is a strict dataclass, so a new top-level key would break ml.manifest) as a
# line `training-wait: awaiting_source_trades — <reason>`. A never-trained
# manifest carrying the marker reports the distinct, non-alarming
# `manifest_awaiting_source` status instead of `manifest_untrained_stale`
# (an accepted wait nagging nightly is the alarm-fatigue class,
# MB-20260719-DATASET-AUDIT-NOISE). A model that HAS trained and then goes
# stale still alarms normally — the waiver only covers the never-trained
# branch, and only while the marker stays in the manifest.
_AWAITING_SOURCE_RE = re.compile(r"^\s*training-wait:\s*awaiting_source_trades\b(.*)$", re.MULTILINE)


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _newest_registered_run(registry_root: str, model_id: str) -> dt.datetime | None:
    """Newest run timestamp across ALL registry entries for *model_id*.

    Mirrors scripts/ops/dataset_unchanged_check.py: registry filenames may
    not equal model_id, so entries are content-matched.
    """
    newest: dt.datetime | None = None
    # provenance: _newest_registered_run — max over EVERY run of EVERY
    # registry file whose model_id matches; not a newest-file pick.
    for path in glob.glob(os.path.join(registry_root, "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                entry = json.load(fh)
        except (OSError, ValueError):
            continue  # sibling artifacts are not registry entries
        if not isinstance(entry, dict) or entry.get("model_id") != model_id:
            continue
        for run in entry.get("runs") or []:
            try:
                at = dt.datetime.fromisoformat(str(run["at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=dt.timezone.utc)
            if newest is None or at > newest:
                newest = at
    return newest


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(json.dumps({
            "ts": _iso_now(), "status": "training_staleness_summary",
            "scanned": 0, "stale": 0, "never_trained": 0, "unresolvable": 0, "awaiting_source": 0,
            "registry_files": 0, "threshold_days": None,
            "detail": "called with too few arguments — scanned NOTHING "
                      "(an absent report, not a clean one)",
        }))
        return 0
    registry_root = argv[1]
    try:
        threshold_days = float(argv[2])
    except ValueError:
        threshold_days = 7.0
    manifests = argv[3:]
    now = dt.datetime.now(dt.timezone.utc)
    registry_files = len(glob.glob(os.path.join(registry_root, "*.json")))

    stale = never_trained = unresolvable = awaiting_source = 0
    for manifest in manifests:
        try:
            with open(manifest, encoding="utf-8") as fh:
                _mf_text = fh.read()
            match = _MODEL_ID_RE.search(_mf_text)
        except OSError:
            _mf_text = ""
            match = None
        if match is None:
            unresolvable += 1
            print(json.dumps({
                "ts": _iso_now(), "status": "manifest_untrained_stale",
                "manifest": manifest, "model_id": None,
                "last_trained_at": None, "days_untrained": None,
                "threshold_days": threshold_days,
                "detail": "model_id unresolvable from the manifest text — "
                          "cannot match a registry entry; treat as needing "
                          "a look, not as trained",
            }))
            continue
        model_id = match.group(1)
        last_at = _newest_registered_run(registry_root, model_id)
        if last_at is None:
            # Grace: a manifest file younger than the threshold is a fresh
            # experiment awaiting its first cycle, not an alarm.
            try:
                mf_age_days = (now.timestamp() - os.path.getmtime(manifest)) / 86400.0
            except OSError:
                mf_age_days = threshold_days + 1.0  # unreadable → report it
            if mf_age_days <= threshold_days:
                continue
            wait_m = _AWAITING_SOURCE_RE.search(_mf_text)
            if wait_m:
                awaiting_source += 1
                print(json.dumps({
                    "ts": _iso_now(), "status": "manifest_awaiting_source",
                    "manifest": manifest, "model_id": model_id,
                    "threshold_days": threshold_days,
                    "detail": (
                        "never trained, by accepted design — manifest carries "
                        "the awaiting_source_trades waiver"
                        + (" —" + wait_m.group(1) if wait_m.group(1).strip() else "")
                    ),
                }))
                continue
            never_trained += 1
            stale += 1
            print(json.dumps({
                "ts": _iso_now(), "status": "manifest_untrained_stale",
                "manifest": manifest, "model_id": model_id,
                "last_trained_at": None, "days_untrained": None,
                "threshold_days": threshold_days,
                "detail": (
                    f"0 registered runs for {model_id} across "
                    f"{registry_files} registry file(s), and the manifest is "
                    f"{mf_age_days:.1f}d old — it has been skipped/failed "
                    f"every cycle since it landed"
                ),
            }))
            continue
        days = (now - last_at).total_seconds() / 86400.0
        if days > threshold_days:
            stale += 1
            print(json.dumps({
                "ts": _iso_now(), "status": "manifest_untrained_stale",
                "manifest": manifest, "model_id": model_id,
                "last_trained_at": last_at.isoformat(),
                "days_untrained": round(days, 1),
                "threshold_days": threshold_days,
                "detail": (
                    f"newest registered run for {model_id} is "
                    f"{days:.1f}d old (threshold {threshold_days:g}d) — the "
                    f"cycle's skip paths have not let it train since then"
                ),
            }))

    print(json.dumps({
        "ts": _iso_now(), "status": "training_staleness_summary",
        "scanned": len(manifests), "stale": stale,
        "never_trained": never_trained, "unresolvable": unresolvable,
        "awaiting_source": awaiting_source,
        "registry_files": registry_files, "threshold_days": threshold_days,
    }))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-open: report, never break the cycle
        print(json.dumps({
            "ts": _iso_now(), "status": "training_staleness_summary",
            "scanned": 0, "stale": 0, "never_trained": 0, "unresolvable": 0, "awaiting_source": 0,
            "registry_files": 0, "threshold_days": None,
            "detail": f"staleness reporter crashed ({type(exc).__name__}: {exc}) "
                      f"— scanned NOTHING this cycle",
        }))
        raise SystemExit(0)
