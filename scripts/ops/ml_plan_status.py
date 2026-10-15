#!/usr/bin/env python3
"""ml_plan_status -- plan-vs-measured check for the ML / AI-trader build plan.

WHY. ROADMAP.md carries the ML plan; docs/plans/ml-plan-items.yaml is its machine-readable form.
A plan that nobody re-reads is a memo (CLAUDE.md § "How work is organised"), so the recurring
ML plan loop (ml-review skill § "The ML plan loop") runs this first. It answers, mechanically:

  ORPHAN       an item names a row/unit that does not exist (the plan item can be forgotten)
  CLOSED-OPEN  the carrying row/unit is done/killed/retired but the plan item is still open
  OPEN-DONE    the plan item is done but its row/unit is still open
  STALE        an open item's row has not moved in `review_days`
  UNPLANNED    an open ML-ish checklist row that no plan item points at (work outside the plan)

and, with --plumbing, reads the PUBLIC read-only API (/api/bot/ml/status, /api/bot/shadow/stats)
and reports trainer/registry/shadow-log findings. A silent or constant head is a finding unless
`plumbing.tracked` names the row that owns it.

With --diversity it also answers the FRONTIER question (frontier-research skill): is the ML work spread over
genuinely different model FAMILIES, or piling onto one? It counts trainable manifests (and, when the public
registry is reachable, live/shadow/candidate models) and the open ML plan items by family, and flags
CONCENTRATION (> `diversity.max_family_share` of open units in one family) and FAIL-STREAK (a family whose last
`diversity.max_fail_streak` graded units all failed that is still getting new units).

EXIT CODES. 0 clean, 1 findings, 2 COULD NOT MEASURE (e.g. API unreachable) -- never 0 when
nothing was checked (RULES-CANONICAL: red-or-green while measuring nothing is the same sin).
Read-only. No writes anywhere.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import yaml

REPO = Path(__file__).resolve().parents[2]
PLAN = REPO / "docs/plans/ml-plan-items.yaml"
API = "https://ict-bot.duckdns.org/api/bot"
CLOSED = {"done", "killed", "retired", "archived"}
ML_ROW = re.compile(r"^(ML-|REGIME-|EXIT-HEAD|TP-PREDICT|FUNDING-OI)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_plan(path: Path = PLAN) -> dict:
    return yaml.safe_load(path.read_text())


def rows(repo: Path = REPO) -> dict:
    out = {}
    for p in (repo / "docs/claude/work/checklist").glob("*.json"):
        try:
            d = json.loads(p.read_text())
        except (
            OSError,
            ValueError,
        ) as e:  # an unreadable row is reported, never silently dropped
            print(f"WARN unreadable checklist row {p.name}: {e}", file=sys.stderr)
            continue
        if isinstance(d, dict) and d.get("id"):
            out[d["id"]] = d
    return out


def unit_state(rq: str, repo: Path = REPO):
    """(state, updated) for a research/queue unit, or None when no such unit exists."""
    for sub in ("", "blocked/"):
        p = repo / "research/queue" / f"{sub}{rq}.yaml"
        if p.exists():
            d = yaml.safe_load(p.read_text()) or {}
            st = d.get("status") or ("blocked" if sub else "queued")
            return st, None
    if (repo / "research/results" / rq).exists():
        return "done", None
    return None


def resolve(item: dict, rowmap: dict, repo: Path = REPO):
    ref = str(item.get("ref", ""))
    if ref.startswith("recurring:"):
        ok = (repo / ".claude/skills" / ref.split(":", 1)[1] / "SKILL.md").exists()
        return ("recurring", None) if ok else None
    if ref.startswith("RQ-"):
        r = unit_state(ref, repo)
        return None if r is None else (r[0], None)
    r = rowmap.get(ref)
    return None if r is None else (r.get("state"), _parse(r.get("updated_at")))


def analyse(plan: dict, rowmap: dict, repo: Path = REPO, now=None) -> list[dict]:
    now = now or _now()
    lim = int(plan.get("review_days", 14))
    out, planned = [], set()
    for it in plan["items"]:
        planned.add(str(it["ref"]))
        res = resolve(it, rowmap, repo)
        flags = []
        if res is None:
            flags.append("ORPHAN")
            st, upd = None, None
        else:
            st, upd = res
            closed = st in CLOSED
            if it["state"] in ("open",) and closed:
                flags.append("CLOSED-OPEN")
            if it["state"] == "done" and st not in CLOSED | {"recurring", None}:
                flags.append("OPEN-DONE")
            if (
                it["state"] == "open"
                and upd
                and (now - upd).days >= lim
                and st not in CLOSED
            ):
                flags.append("STALE")
        out.append(
            {
                "id": it["id"],
                "track": it["track"],
                "plan": it["state"],
                "ref": it["ref"],
                "ref_state": st,
                "flags": flags,
            }
        )
    for rid, r in rowmap.items():
        if (
            ML_ROW.match(rid)
            and r.get("state") not in CLOSED
            and rid not in planned
            and rid != "ML-ROADMAP-LOOP"
            and not re.fullmatch(r"ML-REVIEW-\d{8}", rid)  # the weekly ml-review lane's own dated row (plan item ML-V2)
        ):
            out.append(
                {
                    "id": "-",
                    "track": "?",
                    "plan": "-",
                    "ref": rid,
                    "ref_state": r.get("state"),
                    "flags": ["UNPLANNED"],
                }
            )
    return out


def _get(path: str):
    with urlopen(f"{API}/{path}", timeout=30) as r:
        return json.load(r)


def plumbing(plan: dict, status: dict, stats: dict, now=None) -> list[str]:
    now = now or _now()
    cfg = plan.get("plumbing", {})
    tracked = cfg.get("tracked", {})
    f: list[str] = []
    s = status.get("status", {})
    lc = s.get("last_cycle", {})
    ts = _parse(lc.get("ts"))
    if ts is None:
        f.append("CYCLE-UNREADABLE: last_cycle.ts missing")
    elif (now - ts).total_seconds() / 3600 > cfg.get("max_cycle_age_hours", 36):
        f.append(f"CYCLE-STALE: last cycle {lc.get('ts')}")
    if lc.get("failed"):
        f.append(f"CYCLE-FAILED: {lc.get('failed')} manifests failed")
    b = s.get("dataset_builds_24h", {})
    if b.get("failed"):
        f.append(
            f"BUILDS-FAILED-24H: {b['failed']} (ok {b.get('ok')}, last_overall_rc {b.get('last_overall_rc')}) -- read /api/bot/ml/builds for the family; one transient exchange error is a note, a repeat is a row"
        )
    d = s.get("disk", {})
    if d.get("measured") and d.get("used_pct", 0) >= cfg.get("max_disk_used_pct", 85):
        f.append(f"DISK: trainer {d.get('used_pct')}% used")
    reg = s.get("registry", {}).get("stages", {})
    exp = cfg.get("expected_registry_stages", {})
    if exp and reg != exp:
        f.append(
            f"REGISTRY-STAGES-CHANGED: now {reg}, plan expects {exp} -- explain by a recorded decision or file it"
        )
    exp_ref = cfg.get("expected_refusing", {})
    for mf in s.get("refusing_manifests_24h", []):
        if mf in exp_ref:
            f.append(f"NOTE REFUSED(tracked by {exp_ref[mf]}): {mf}")
        else:
            f.append(
                f"REFUSED(UNTRACKED): {mf} -- the dataset audit refuses it and no row owns that"
            )
    ts_sum = s.get("training_staleness", {}).get("summary", {})
    if ts_sum.get("stale") or ts_sum.get("never_trained"):
        f.append(
            f"TRAINING-STALENESS: stale {ts_sum.get('stale')}, never_trained {ts_sum.get('never_trained')} of {ts_sum.get('scanned')} scanned (threshold {ts_sum.get('threshold_days')}d)"
        )
    for r in stats.get("records", []):
        m, stg = r["model_id"], r.get("stage")
        last = _parse(r.get("last_seen"))
        silent = last is None or (now - last).total_seconds() / 3600 > cfg.get(
            "max_silent_hours", 6
        )
        const = r.get("score_read_state") == "constant"
        for bad, label in ((silent, "SILENT"), (const, "CONSTANT")):
            if not bad:
                continue
            t = tracked.get(m)
            sev = "ADVISORY-" if stg == "advisory" else ""
            if t:
                f.append(f"NOTE {sev}{label}(tracked by {t['by']}): {m} -- {t['why']}")
            else:
                f.append(
                    f"{sev}{label}(UNTRACKED): {m} stage={stg} last_seen={r.get('last_seen')} -- file a row or add to plumbing.tracked"
                )
    return f


TRAINER_FAMILY = {
    "LightGBMMulticlassTrainer": "gbdt",
    "LightGBMRegressionTrainer": "gbdt",
    "CausalHMMRegimeTrainer": "bayesian_probabilistic",
    "RegimeClassifierTrainer": "rule_baseline",
    "PerStrategyWinRateTrainer": "rule_baseline",
    "ConstantBaselineTrainer": "rule_baseline",
    "ConstantPredictionTrainer": "rule_baseline",
    "TorchSequenceTrainer": "deep_sequence",
    "SSLCorpusEncoderTrainer": "ssl_representation",
}
# The taxonomy the frontier scan reasons in. A family with zero manifests AND zero open units is the signal
# the scan exists to act on, so every name here is reported even at 0.
FAMILIES = (
    "gbdt",
    "linear_logit",
    "deep_sequence",
    "ts_foundation_model",
    "tabular_foundation_model",
    "rl_bandit",
    "bayesian_probabilistic",
    "conformal_calibration",
    "ssl_representation",
    "ensemble_stack",
    "rule_baseline",
)


def manifest_families(repo: Path = REPO) -> dict:
    """model_id -> family for every trainable manifest (ml/configs/*.yaml, retired/ excluded)."""
    out = {}
    for p in sorted((repo / "ml/configs").glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            print(f"WARN unreadable manifest {p.name}: {e}", file=sys.stderr)
            continue
        t = str(d.get("trainer", "")).rsplit(".", 1)[-1]
        out[str(d.get("model_id", p.stem))] = TRAINER_FAMILY.get(t, "UNMAPPED:" + t)
    return out


def diversity(plan: dict, mf: dict, registry_stages: dict | None = None) -> tuple[list[str], dict]:
    """Return (findings, table). `registry_stages` is model_id -> stage when the registry was readable."""
    cfg = plan.get("diversity") or {}
    share_max = float(cfg.get("max_family_share", 0.6))
    streak_max = int(cfg.get("max_fail_streak", 3))
    fams = {f: {"manifests": 0, "live_shadow": 0 if registry_stages else None, "open_units": 0, "history": []} for f in FAMILIES}
    findings = []
    for mid, fam in mf.items():
        fams.setdefault(fam, {"manifests": 0, "live_shadow": 0 if registry_stages else None, "open_units": 0, "history": []})
        fams[fam]["manifests"] += 1
        if registry_stages and registry_stages.get(mid) in ("SHADOW", "LIVE"):
            fams[fam]["live_shadow"] += 1
    unmapped = sorted({f for f in mf.values() if f.startswith("UNMAPPED:")})
    if unmapped:
        findings.append(f"UNMAPPED-TRAINER: {', '.join(unmapped)} (add to TRAINER_FAMILY)")
    for it in plan["items"]:
        if it.get("state") != "open" or not it.get("family"):
            continue
        fam = it["family"]
        if fam == "none":   # process / infrastructure item: not about any model family (explicit, so it is not "untagged")
            continue
        if fam not in fams:
            findings.append(f"UNKNOWN-FAMILY: item {it['id']} names '{fam}' (not in FAMILIES)")
            continue
        fams[fam]["open_units"] += 1
    for fam, h in (plan.get("families") or {}).items():
        if fam in fams:
            fams[fam]["history"] = list(h.get("history") or [])
    n_open = sum(f["open_units"] for f in fams.values())
    untagged = [i["id"] for i in plan["items"] if i.get("state") == "open" and not i.get("family")]
    if untagged:
        findings.append(f"NOTE {len(untagged)} open plan items carry no `family:` tag (not counted): {', '.join(untagged)}")
    if n_open:
        for fam, f in fams.items():
            if f["open_units"] / n_open > share_max:
                findings.append(
                    f"CONCENTRATION: {fam} holds {f['open_units']}/{n_open} open family-tagged plan items "
                    f"(> {share_max:.0%})"
                )
    for fam, f in fams.items():
        tail = f["history"][-streak_max:]
        if len(tail) == streak_max and all(v == "FAIL" for v in tail) and f["open_units"]:
            findings.append(
                f"FAIL-STREAK: {fam} last {streak_max} graded units all FAIL and {f['open_units']} new "
                "unit(s) still open -- retire the line by its exhaustion rule or change the lever class"
            )
    n_man = sum(f["manifests"] for f in fams.values())
    if n_man:
        for fam, f in fams.items():
            if f["manifests"] / n_man > share_max:
                findings.append(
                    f"MANIFEST-CONCENTRATION: {fam} is {f['manifests']}/{n_man} registered models "
                    f"(> {share_max:.0%}) -- reported, not failed: the registry predates the frontier scan"
                )
    return findings, fams


def self_test() -> int:
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    plan = {
        "review_days": 14,
        "items": [
            {"id": "A", "track": "S", "state": "open", "ref": "ROW-OK"},
            {"id": "B", "track": "S", "state": "open", "ref": "ROW-GONE"},
            {"id": "C", "track": "S", "state": "open", "ref": "ROW-DONE"},
            {"id": "D", "track": "S", "state": "done", "ref": "ROW-OK"},
            {"id": "E", "track": "S", "state": "open", "ref": "ROW-OLD"},
        ],
        "plumbing": {"tracked": {"t-head": {"why": "w", "by": "ROW-OK"}}},
    }
    rm = {
        "ROW-OK": {
            "id": "ROW-OK",
            "state": "queued",
            "updated_at": "2026-10-10T00:00:00Z",
        },
        "ROW-DONE": {
            "id": "ROW-DONE",
            "state": "done",
            "updated_at": "2026-10-10T00:00:00Z",
        },
        "ROW-OLD": {
            "id": "ROW-OLD",
            "state": "queued",
            "updated_at": "2026-09-01T00:00:00Z",
        },
        "ML-STRAY": {
            "id": "ML-STRAY",
            "state": "queued",
            "updated_at": "2026-10-10T00:00:00Z",
        },
    }
    got = {(r["id"], tuple(r["flags"])) for r in analyse(plan, rm, now=now)}
    want = {
        ("A", ()),
        ("B", ("ORPHAN",)),
        ("C", ("CLOSED-OPEN",)),
        ("D", ("OPEN-DONE",)),
        ("E", ("STALE",)),
        ("-", ("UNPLANNED",)),
    }
    assert got == want, got ^ want
    old = "2026-10-10T00:00:00+00:00"
    st = {
        "status": {
            "last_cycle": {"ts": "2026-10-09T00:00:00+00:00", "failed": 0},
            "dataset_builds_24h": {"failed": 0},
            "disk": {"measured": True, "used_pct": 40},
            "registry": {"stages": {}},
        }
    }
    stats = {
        "records": [
            {"model_id": "t-head", "stage": "shadow", "last_seen": old},
            {
                "model_id": "u-head",
                "stage": "advisory",
                "last_seen": "2026-10-11T11:50:00+00:00",
                "score_read_state": "constant",
            },
            {
                "model_id": "ok",
                "stage": "shadow",
                "last_seen": "2026-10-11T11:50:00+00:00",
                "score_read_state": "varying",
            },
        ]
    }
    out = plumbing(plan, st, stats, now=now)
    assert any(
        x.startswith("NOTE ") and "SILENT(tracked by ROW-OK)" in x for x in out
    ), out
    assert any("ADVISORY-CONSTANT(UNTRACKED): u-head" in x for x in out), out
    assert not any(": ok " in x for x in out), out
    assert any(x.startswith("CYCLE-STALE") for x in out), out
    dplan = {
        "diversity": {"max_family_share": 0.6, "max_fail_streak": 3},
        "families": {"deep_sequence": {"history": ["PASS", "FAIL", "FAIL", "FAIL"]}},
        "items": [
            {"id": "a", "state": "open", "family": "gbdt"},
            {"id": "b", "state": "open", "family": "gbdt"},
            {"id": "c", "state": "open", "family": "gbdt"},
            {"id": "d", "state": "open", "family": "deep_sequence"},
            {"id": "e", "state": "open", "family": "nonsense"},
            {"id": "f", "state": "done", "family": "rl_bandit"},
        ],
    }
    dfind, dtab = diversity(dplan, {"m1": "gbdt", "m2": "gbdt", "m3": "UNMAPPED:XTrainer"})
    assert any(x.startswith("CONCENTRATION: gbdt") for x in dfind), dfind
    assert any(x.startswith("FAIL-STREAK: deep_sequence") for x in dfind), dfind
    assert any(x.startswith("UNKNOWN-FAMILY") and "nonsense" in x for x in dfind), dfind
    assert any(x.startswith("UNMAPPED-TRAINER") for x in dfind), dfind
    assert dtab["rl_bandit"]["open_units"] == 0 and dtab["ts_foundation_model"]["manifests"] == 0
    print(
        "ml_plan_status self-test: OK (5 plan flags + 4 plumbing findings + 4 diversity findings planted and detected, clean item stays clean)"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--plumbing",
        action="store_true",
        help="also read the public API and check trainer/registry/shadow-log",
    )
    ap.add_argument(
        "--diversity",
        action="store_true",
        help="count manifests/models/open plan items by model FAMILY and flag concentration (frontier scan)",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    plan = load_plan()
    res = analyse(plan, rows())
    plumb, rc = [], 0
    if a.plumbing:
        try:
            plumb = plumbing(plan, _get("ml/status"), _get("shadow/stats"))
        except (
            OSError,
            ValueError,
        ) as e:  # could not look != nothing found (URLError is an OSError)
            print(f"COULD NOT MEASURE plumbing: {e!r}", file=sys.stderr)
            rc = 2
    dfind, dtab = [], {}
    if a.diversity:
        stages, mf = None, manifest_families()
        try:  # the registry covers retired models too (96 vs 73 manifests); deployment_bucket is the stage
            reg = _get("ml/registry")["rows"]
            mf = {
                str(r["model_id"]): TRAINER_FAMILY.get(
                    str(r.get("trainer", "")).rsplit(".", 1)[-1], "UNMAPPED:" + str(r.get("trainer"))
                )
                for r in reg
            }
            stages = {str(r["model_id"]): r.get("deployment_bucket") for r in reg}
        except (OSError, ValueError, KeyError, TypeError) as e:  # fall back to manifests; stage column = "-"
            print(f"NOTE registry unreadable ({e!r}); using manifests, live/shadow not measured", file=sys.stderr)
        dfind, dtab = diversity(plan, mf, stages)
    if a.json:
        print(json.dumps({"plan": res, "plumbing": plumb, "diversity": {"findings": dfind, "families": dtab}}, indent=1, default=str))
    else:
        print(f"{'id':8} {'trk':3} {'plan':7} {'ref':34} {'ref_state':14} flags")
        for r in res:
            print(
                f"{r['id']:8} {r['track']:3} {r['plan']:7} {str(r['ref'])[:34]:34} {r['ref_state']!s:14} {','.join(r['flags'])}"
            )
        n_open = sum(1 for r in res if r["plan"] == "open")
        print(
            f"\nplan items {len(plan['items'])} (open {n_open}); flagged {sum(1 for r in res if r['flags'])}"
        )
        for p in plumb:
            print("PLUMBING", p)
        if a.diversity:
            print(f"\n{'family':26} {'manifests':>9} {'live/shadow':>11} {'open items':>10}  graded history")
            for fam, f in sorted(dtab.items(), key=lambda kv: -kv[1]["manifests"]):
                print(
                    f"{fam:26} {f['manifests']:9} {f['live_shadow'] if f['live_shadow'] is not None else '-':>11} {f['open_units']:10}  {','.join(f['history'])}"
                )
            for d in dfind:
                print("DIVERSITY", d)
    bad = (
        any(r["flags"] for r in res)
        or any(not p.startswith("NOTE ") for p in plumb)
        or any(not d.startswith(("MANIFEST-CONCENTRATION", "NOTE ")) for d in dfind)
    )
    return rc or (1 if bad else 0)


if __name__ == "__main__":
    sys.exit(main())
