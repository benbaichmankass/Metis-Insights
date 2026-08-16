#!/usr/bin/env python3
"""Standing dead-man switch for **VACUOUS** and **UNDER-COVERED** research artifacts.

The sibling of ``scripts/macro/check_producer_liveness.py``. That one guards
**staleness** (a producer stopped firing, its ledger froze). This one guards the two
failure modes staleness cannot see:

* **VACUITY** — the producer fires, the ledger grows, the artifact is *fresh*, and
  the measurement inside is **empty**. A verdict gets published from zero inputs.
* **UNDER-COVERAGE** — the artifact is complete *with respect to its own roster*, and
  the roster is the wrong set. "Finished the queue" reads as "finished the audit."

Why this exists (2026-07-30). One bug class, four instances found in a single day:

1. ``econ_event_study_scorecard.json`` published ``verdict`` from **``price_bars: 0``**
   for the producer's entire life, behind a workflow ``|| echo "::warning::"``. Every
   liveness signal was green; `ROADMAP_MACRO` recorded the verdict as one that would
   "self-graduate as history accrues" — at zero bars it never could.
   (``BL-20260730-M1-PRICE-JOIN-DEAD``)
2. The corrected-cost regime re-grade reported **34 rows, 0 errored, 0 skipped** —
   and silently omitted ``gld_pullback_1h``, the one LIVE Tier-3 cell the re-grade
   existed to re-check, because authoring that cell had removed it from the
   ``coverage_debt`` roster the tool iterates.
   (``BL-20260730-REGIME-CELL-UNAUDITABLE``)
3. ``splg_trend_long_1d`` returned **all zeros, no error**, inside an otherwise
   successful run — a row that measured nothing, in a run that reported success.
4. The exit-ladder soak reads **"135 rows / 0 differing"** — fresh, growing, possibly
   measuring nothing. (``BL-20260730-EXIT-LADDER-SOAK-VACUITY``)

An S-067 audit of the *same class* exists from 2026-05-10 and it recurred anyway,
because the guard that came out of it (``check_silent_empty_in_diff.py``) is a
diff-scoped Python-``except`` scanner over three ``src/`` paths — it can see neither
the research/producer layer, nor the shell ``|| echo`` that hid instance 1, nor the
output-side contract of a verdict computed from an empty input set. **A guard scoped
to three paths and one language's syntax is not a guard against a bug class.** This
script guards the *output*, which is where the class actually shows up.

Design
------
**Declarative, not hardcoded per artifact.** ``CHECKS`` names, per artifact, which
keys are *load-bearing inputs* (dotted paths) and their floor. A new producer is one
entry, not a rewrite. Unregistered ``comms/**`` JSON is *also* scanned heuristically
for well-known input keys, so an artifact nobody registered still gets flagged rather
than silently exempt — the registry raises the bar, it is not the only gate.

Exit codes:
    0  every checked artifact measured something (or is absent with --allow-missing)
    1  at least one artifact is VACUOUS / UNDER-COVERED / unreadable
    2  usage error

Read-only. Stdlib-only, so it runs on a bare runner. No order path, no VM touch.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Input-count keys that are load-bearing wherever they appear. Used for the
# heuristic pass over unregistered artifacts, so a new scorecard is not silently
# exempt just because nobody added it to CHECKS.
HEURISTIC_INPUT_KEYS = (
    "price_bars", "releases", "releases_with_value", "max_n", "total_scanned",
    "count", "n", "rows", "roster_rows", "record_count",
)

# Registered artifacts: path -> {inputs: [(dotted_key, floor)], note}
# `floor` is the MINIMUM for the artifact to have measured anything at all — not a
# statistical-power bar (that is the artifact's own min_honest_n), just "> nothing".
CHECKS: dict[str, dict[str, Any]] = {
    # --- econ event-study scorecard FAMILY (all 5 kinds registered, so the ROSTER
    # is asserted, not its existence — BL-20260730-ECON-SCORECARD-NAMING-TRAP). The
    # `eia_natgas_storage` kind lands under the BARE `econ_event_study_scorecard.json`
    # (legacy back-compat name; the producer default is now per-kind suffixed so no NEW
    # bare file is ever written). Registering all 5 by path is the fix: a family glob
    # `econ_event_study_*_scorecard.json` misses the bare natgas file and enumerates
    # 4-of-5 looking complete — this registry is the roster that catches a dropped kind.
    "comms/macro/econ_event_study_scorecard.json": {
        "inputs": [("meta.price_bars", 1), ("meta.releases", 1)],
        "note": "eia_natgas_storage kind (BARE legacy filename — the naming-trap row "
                "BL-20260730-ECON-SCORECARD-NAMING-TRAP). Price join fixed 2026-07-30 "
                "(was price_bars:0 its whole life, BL-20260730-M1-PRICE-JOIN-DEAD); now "
                "non-vacuous.",
    },
    "comms/macro/econ_event_study_crude_scorecard.json": {
        "inputs": [("meta.price_bars", 1), ("meta.releases", 1)],
        "note": "eia_crude_stocks kind (short-alias filename). Sibling of the natgas "
                "scorecard; price join fixed on the same run.",
    },
    "comms/macro/econ_event_study_initial_jobless_claims_scorecard.json": {
        "inputs": [("meta.price_bars", 1), ("meta.releases", 1)],
        "note": "initial_jobless_claims kind — the 3 non-energy kinds complete the "
                "family roster (BL-20260730-ECON-SCORECARD-NAMING-TRAP: assert the COUNT).",
    },
    "comms/macro/econ_event_study_continuing_jobless_claims_scorecard.json": {
        "inputs": [("meta.price_bars", 1), ("meta.releases", 1)],
        "note": "continuing_jobless_claims kind — completes the event-study family roster.",
    },
    "comms/macro/econ_event_study_cpi_yoy_scorecard.json": {
        "inputs": [("meta.price_bars", 1), ("meta.releases", 1)],
        "note": "cpi_yoy kind — completes the event-study family roster.",
    },
    "comms/macro/horizon_ic_scorecard.json": {
        "inputs": [("meta.snapshot_records", 1), ("meta.rebalances", 1),
                   ("meta.symbols_with_candles", 1)],
        "note": "IC scan — a verdict over zero snapshots, zero rebalances or zero "
                "priced symbols is vacuous. Note `symbols_with_candles`: an IC scan "
                "with snapshots but NO priced symbols is the same class of bug as the "
                "event study's price_bars:0.",
    },
    "comms/macro/econ_calendar_snapshots.jsonl": {
        "jsonl_min_rows": 1,
        "note": "The forward PIT calendar ledger.",
    },
    "comms/macro/econ_expectation_validation.json": {
        "inputs": [("report.n_overlap", 1)],
        "note": "M3 — the M1 gate's satisfiability condition. Its verdict is only "
                "meaningful over a non-empty overlap, and this artifact is exactly the "
                "shape that goes vacuous quietly: the survey side is a JOIN, so a "
                "renamed/absent input file yields zero pairs rather than an error. "
                "(It reported insufficient_overlap at n=11 while 1,263 joinable rows sat "
                "committed beside it, because the tool read only one of its two survey "
                "sources.)",
    },
}


# Artifacts KNOWN to be vacuous right now, each with the backlog row that owns the
# fix and a date by which it must be resolved. This is a grandfather list with
# ATTRIBUTION AND AN EXPIRY — deliberately not a silence list:
#   * every entry MUST name a backlog id, so the debt is owned, not hidden;
#   * every entry MUST carry `until`, and the guard FAILS once that date passes —
#     so a known-vacuous artifact cannot quietly become permanent, which is the exact
#     way the original bug survived ("it was already like that");
#   * entries are still REPORTED on every run (as KNOWN), never suppressed from view.
# An entry without an id, or past `until`, is a hard failure.
KNOWN_VACUOUS: dict[str, dict[str, str]] = {
    # REMOVED 2026-08-01 (the entry's own contract — "the fix landed → remove the entry"):
    # the two econ_event_study natgas + crude scorecards CLEARED. The price join was fixed
    # 2026-07-30 and both now read price_bars > 0 (natgas 5428/releases 789, crude
    # 5427/2211), verified against the committed artifacts. They are now regular registered
    # CHECKS above (non-vacuous), so grandfathering them would be dead debt.
    # REMOVED 2026-08-16 (the entry's own contract again — "the fix landed → remove
    # the entry"): the zero-row FMP capture US-20260729T073711Z.fmp.json is PRUNED.
    # Its entry expired 2026-08-15 and the guard correctly hard-failed on 08-16, which
    # is the expiry doing its job rather than a new fault. Evidence that the debt was
    # genuinely dead rather than merely old: the file carried `"rows": []`, NOTHING in
    # the repo referenced it, it was the ONLY *.fmp.json in the capture dir (every
    # later capture is *.fxstreet.json — the producer had already switched source, so
    # the entry's "stop writing empty ones" branch had in fact happened), and the
    # owning row BL-20260730-PRODUCER-VACUITY-GUARD was already `resolved`.
    # The list is intentionally EMPTY. That is the healthy state, not a missing entry —
    # re-adding anything here needs a backlog id and an `until`, per the contract above.
}


def known_vacuous_problems(today: str) -> list[str]:
    """Structural problems with the grandfather list itself.

    The list is only legitimate while every entry is attributed and unexpired.
    """
    problems: list[str] = []
    for rel, spec in sorted(KNOWN_VACUOUS.items()):
        if not spec.get("backlog"):
            problems.append(f"{rel}: KNOWN_VACUOUS entry names no backlog row — "
                            f"unowned debt is hidden debt")
        until = spec.get("until") or ""
        if not until:
            problems.append(f"{rel}: KNOWN_VACUOUS entry has no `until` — a known-"
                            f"vacuous artifact must not be allowed to become permanent")
        elif today > until:
            problems.append(
                f"{rel}: KNOWN_VACUOUS entry EXPIRED on {until} (owner "
                f"{spec.get('backlog')}). Either the fix landed — remove the entry — "
                f"or it did not, and this is now a real failure, not a grandfathered one."
            )
    return problems


def _dig(obj: Any, dotted: str) -> Optional[Any]:
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _numeric(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (list, dict)):
        return float(len(v))
    return None


def check_json_artifact(path: Path, spec: dict) -> list[str]:
    """Problems found in one registered JSON artifact ([] = clean)."""
    problems: list[str] = []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unreadable: {type(exc).__name__}: {exc}"]

    for dotted, floor in spec.get("inputs") or []:
        raw = _dig(obj, dotted)
        if raw is None:
            problems.append(f"MISSING declared input `{dotted}` — cannot prove it "
                            f"measured anything")
            continue
        num = _numeric(raw)
        if num is None:
            problems.append(f"`{dotted}` is not numeric ({raw!r})")
        elif num < floor:
            problems.append(f"VACUOUS: `{dotted}` = {num:g} (floor {floor}) — the "
                            f"verdict in this artifact was computed from nothing")
    return problems


def check_jsonl_artifact(path: Path, spec: dict) -> list[str]:
    min_rows = int(spec.get("jsonl_min_rows") or 1)
    rows = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    rows += 1
    except OSError as exc:
        return [f"unreadable: {exc}"]
    if rows < min_rows:
        return [f"VACUOUS: {rows} rows (floor {min_rows})"]
    return []


def heuristic_scan(root: Path, registered: set[Path]) -> list[tuple[Path, str]]:
    """Flag unregistered comms/** JSON whose well-known input keys are all zero.

    Deliberately conservative: only flags when EVERY well-known input key present in
    the artifact is zero/empty. One zero among several real counts is normal; all of
    them zero means the artifact measured nothing.
    """
    out: list[tuple[Path, str]] = []
    comms = root / "comms"
    if not comms.is_dir():
        return out
    for p in sorted(comms.rglob("*.json")):
        if p in registered:
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # not our business here; the registry covers must-parse files
        if not isinstance(obj, dict):
            continue
        found: list[tuple[str, float]] = []
        for scope_name in ("meta", "summary", None):
            scope = obj if scope_name is None else obj.get(scope_name)
            if not isinstance(scope, dict):
                continue
            for k in HEURISTIC_INPUT_KEYS:
                if k in scope:
                    n = _numeric(scope[k])
                    if n is not None:
                        found.append((f"{scope_name+'.' if scope_name else ''}{k}", n))
        if found and all(n == 0 for _k, n in found):
            keys = ", ".join(f"{k}=0" for k, _n in found)
            out.append((p.relative_to(root), f"VACUOUS (heuristic): {keys}"))
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--allow-missing", action="store_true",
                    help="an absent artifact is OK (a producer that has never run)")
    ap.add_argument("--no-heuristic", action="store_true",
                    help="check only the registered artifacts")
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    ap.add_argument("--today", default=None,
                    help="YYYY-MM-DD for expiry evaluation (default: today, UTC)")
    ap.add_argument("--strict", action="store_true",
                    help="fail on KNOWN_VACUOUS artifacts too (ignore the grandfather list)")
    args = ap.parse_args(argv)

    today = args.today or datetime.now(timezone.utc).date().isoformat()
    root = Path(args.repo_root).resolve()
    findings: list[dict] = []
    known: list[dict] = []
    registered: set[Path] = set()

    # The grandfather list must itself be well-formed and unexpired.
    for prob in known_vacuous_problems(today):
        findings.append({"artifact": "KNOWN_VACUOUS registry", "problems": [prob],
                         "note": "the grandfather list is only legitimate while every "
                                 "entry is attributed and unexpired"})

    for rel, spec in sorted(CHECKS.items()):
        p = root / rel
        registered.add(p)
        if not p.exists():
            if not args.allow_missing:
                findings.append({"artifact": rel, "problems": ["MISSING (producer has "
                                 "never written it, or the path moved)"],
                                 "note": spec.get("note")})
            continue
        problems = (check_jsonl_artifact(p, spec) if p.suffix == ".jsonl"
                    else check_json_artifact(p, spec))
        if problems:
            gf = KNOWN_VACUOUS.get(rel)
            bucket = known if (gf and not args.strict) else findings
            entry = {"artifact": rel, "problems": problems, "note": spec.get("note")}
            if gf and not args.strict:
                entry["known"] = gf
            bucket.append(entry)

    if not args.no_heuristic:
        for rel, problem in heuristic_scan(root, registered):
            gf = KNOWN_VACUOUS.get(str(rel))
            entry = {"artifact": str(rel), "problems": [problem],
                     "note": "unregistered — consider adding it to CHECKS"}
            if gf and not args.strict:
                entry["known"] = gf
                known.append(entry)
            else:
                findings.append(entry)

    if args.json:
        print(json.dumps({"ok": not findings, "count": len(findings),
                          "findings": findings, "known_vacuous": known,
                          "today": today}, indent=2))
    else:
        checked = len(CHECKS)
        if known:
            # Reported ALWAYS, never suppressed — a tracked failure is still a failure,
            # it just has an owner and a deadline.
            print(f"KNOWN-VACUOUS ({len(known)}) — tracked debt, not clean:\n")
            for k in known:
                g = k.get("known") or {}
                print(f"  ● {k['artifact']}")
                for prob in k["problems"]:
                    print(f"      {prob}")
                print(f"      owner: {g.get('backlog')}  ·  must clear by "
                      f"{g.get('until')}")
                if g.get("why"):
                    print(f"      why: {g['why']}")
            print("")
        if not findings:
            print(f"OK — {checked} registered artifact(s) checked; nothing vacuous "
                  f"beyond the {len(known)} tracked item(s) above.")
        else:
            print(f"FAIL — {len(findings)} artifact(s) are vacuous / under-covered / "
                  f"unreadable:\n")
            for f in findings:
                print(f"  ✗ {f['artifact']}")
                for prob in f["problems"]:
                    print(f"      {prob}")
                if f.get("note"):
                    print(f"      note: {f['note']}")
            print("\nA fresh, well-formed, entirely vacuous artifact is the failure "
                  "mode this check exists to surface. Do NOT read a verdict out of "
                  "any artifact listed above.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
