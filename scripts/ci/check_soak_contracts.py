#!/usr/bin/env python3
"""soak-contract-guard — nothing enters a soak without a definition of done.

Operator, 2026-10-04: "we can't set something to soak if we don't know when it's
done. It has to have a definition of done so that it knows to create an alert …
that is basic."

REFUSES, against the contract store ``docs/claude/work/SOAKS.json`` (schema and
admissibility: ``scripts/ops/soak_state.py::contract_problems``):

  WHOLE TREE (the roster IS the soak population, so a diff-scoped check would
  pass vacuously on a roster edit that bypassed it):
    * a leg on a Stage-1 roster (bybit_1, alpaca_paper) that is not
      ``execution: shadow`` and has no contract ``SOAK-stage1-<account>-<leg>``;
    * an ``execution: shadow`` strategy with no contract ``SOAK-shadow-<leg>``;
    * any contract that is not admissible (no end date, >14d marked ok, a
      non-ok design with no recommended fix, missing fields).
  DIFF-SCOPED (the existing 77 rows / 600+ items predate the rule; they are
  backfilled as decisions, not grandfathered silently — see the soak report):
    * a checklist row that ENTERS ``landed_unproven`` in this diff without a
      contract whose id is the row id;
    * a NEW pipeline record with ``next_action: check_observation`` (open state)
      carrying neither ``due_when.soak`` nor a contract whose id is the item id.

Like ``check_dry_run_in_diff.py``, removing a leg is free and adding one is
guarded — but here the guard keys on ROSTER MEMBERSHIP, not on a field, so it
cannot be walked around by editing the list (root CLAUDE.md, B1).

Exit: 0 clean · 1 refused · 2 could not check.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
sys.path.insert(0, str(REPO))
import soak_state as ss  # noqa: E402

CHECKLIST = "docs/claude/work/MANAGER-CHECKLIST.json"
PIPE_DIR = "docs/claude/work/pipeline/"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def tree_findings(acc: dict, strat: dict, contracts: List[dict]) -> List[str]:
    by_id = {c.get("id"): c for c in contracts}
    shadow = {n for n, v in strat.items() if isinstance(v, dict) and v.get("execution") == "shadow"}
    out = []
    for a in ss.STAGE1_ACCOUNTS:
        for leg in (acc.get(a) or {}).get("strategies") or []:
            if leg in shadow:
                continue
            cid = f"SOAK-stage1-{a}-{leg}"
            if cid not in by_id:
                out.append(f"{a}/{leg} is on a Stage-1 roster with NO soak contract ({cid}). "
                           f"Run `python3 scripts/ops/soak_state.py --backfill` and commit "
                           f"docs/claude/work/SOAKS.json — it computes n, rate and end date from the "
                           f"leg's Stage-0 evidence record.")
    for leg in sorted(shadow):
        if f"SOAK-shadow-{leg}" not in by_id:
            out.append(f"{leg} is execution: shadow with NO soak contract (SOAK-shadow-{leg}).")
    for c in contracts:
        for p in ss.contract_problems(c):
            out.append(f"contract {c.get('id')}: {p}")
    return out


def observation_problems(obs: object) -> List[str]:
    """The NON-SOAK definition of done (manager, 2026-10-04 20:12Z): a one-shot
    check ("the 05:30Z report was produced", "the tick ran clean on the new sha")
    has one observable event plus a due time, not a trade count. Shape:
    ``observation: {what, how_to_check, due_by}``. ``due_by`` is REQUIRED and an
    ISO date/datetime, so an overdue one alarms (pipeline.is_due) instead of
    sitting quietly in_flight. Empty list = admissible."""
    if not isinstance(obs, dict):
        return ["`observation` must be an object {what, how_to_check, due_by}"]
    p = [f"`observation.{k}` missing or empty" for k in ("what", "how_to_check", "due_by")
         if not str(obs.get(k) or "").strip()]
    if obs.get("due_by"):
        try:
            date.fromisoformat(str(obs["due_by"])[:10])
        except ValueError:
            p.append(f"`observation.due_by` {obs['due_by']!r} is not an ISO date")
    return p


def _prior_next_action(base: str, item_id: str) -> Optional[str]:
    """`next_action` of `item_id`'s LATEST record at `base`, or None if the id is
    new there. Records sort chronologically by filename, so the last one wins
    (same fold as pipeline.read_log). `git grep` keeps this one call per added
    record rather than a read of the ~4.8k-file store."""
    hit = _git("grep", "-l", f'"id": "{item_id}"', base, "--", PIPE_DIR)
    files = sorted(ln.split(":", 1)[1] for ln in hit.stdout.splitlines() if ":" in ln)
    for f in reversed(files):
        blob = _git("show", f"{base}:{f}")
        try:
            rec = json.loads(blob.stdout)
        except ValueError:
            continue
        if rec.get("id") == item_id:
            return rec.get("next_action")
    return None


def diff_findings(base: str, contracts: List[dict]) -> List[str]:
    ids = {c.get("id") for c in contracts}
    out = []
    from src.runtime import checklist_store  # noqa: PLC0415
    new_rows = checklist_store.load_path(REPO / CHECKLIST).get("items") or []
    old_state = {}
    try:  # the monolith before the per-row cutover, row files after
        old_doc = checklist_store.load_at(REPO, base)
    except (ValueError, OSError):
        old_doc = None
    if old_doc is not None:
        old_state = {r.get("id"): r.get("state") for r in old_doc.get("items") or []}
    for r in new_rows:
        if r.get("state") == "landed_unproven" and old_state.get(r.get("id")) != "landed_unproven" \
                and r.get("id") not in ids:
            if "observation" in r:
                out += [f"checklist row {r.get('id')}: {p}" for p in observation_problems(r["observation"])]
                continue
            out.append(f"checklist row {r.get('id')} enters landed_unproven with no soak contract "
                       f"(add a contract with id {r.get('id')!r} to docs/claude/work/SOAKS.json: what "
                       f"observation proves it, n, end date, pass/fail rule) — or, for a one-shot "
                       f"check, an `observation: {{what, how_to_check, due_by}}` on the row.")
    added = _git("diff", "--name-only", "--diff-filter=A", f"{base}...HEAD", "--", PIPE_DIR)
    for name in added.stdout.split():
        try:
            item = json.loads((REPO / name).read_text())
        except (OSError, ValueError):
            continue
        if item.get("next_action") != "check_observation" or item.get("state") in ("done", "killed"):
            continue
        if (item.get("due_when") or {}).get("soak") or item.get("id") in ids:
            continue
        # An UPDATE record is a new FILE but not a new definition-of-done
        # question: the item was already a check_observation at base, so its
        # filing was graded (or grandfathered) then. Only an item that BECOMES a
        # check_observation here, or a brand-new id, is checked.
        # (PI-20261005-WFBREDQP-0001: a plain re-date of 249 items failed CI.)
        if _prior_next_action(base, item.get("id", "")) == "check_observation":
            continue
        if "observation" in item:
            out += [f"new pipeline item {item.get('id')}: {p}" for p in observation_problems(item["observation"])]
            continue
        out.append(f"new pipeline item {item.get('id')} ({name}) is a check_observation with no "
                   f"definition of done: add due_when.soak or a SOAKS.json contract (something "
                   f"that ACCRUES), or `observation: {{what, how_to_check, due_by}}` (a one-shot check).")
    return out


def _self_test() -> int:
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    acc = {"bybit_1": {"strategies": ["a", "s"]}, "alpaca_paper": {"strategies": []}}
    strat = {"a": {"execution": "live"}, "s": {"execution": "shadow"}}
    good = [dict({k: "v" for k in ss.REQUIRED}, id="SOAK-stage1-bybit_1-a", design="too_long",
                 recommended_fix="pool", started="2026-09-01", end_date="2027-01-01"),
            dict({k: "v" for k in ss.REQUIRED}, id="SOAK-shadow-s", design="not_soaking",
                 recommended_fix="retire", started="2026-09-01", end_date=None)]
    ck("a contracted roster passes (positive control)", tree_findings(acc, strat, good) == [])
    ck("a NEW roster leg with no contract is refused",
       any("bybit_1/b" in f for f in tree_findings({"bybit_1": {"strategies": ["a", "s", "b"]}},
                                                   strat, good)))
    ck("a NEW shadow strategy with no contract is refused",
       any("SOAK-shadow-t" in f for f in tree_findings(acc, dict(strat, t={"execution": "shadow"}), good)))
    ck("a contract with no end date and design ok is refused",
       any("end_date" in f for f in tree_findings(acc, strat, [dict(good[0], design="ok", end_date=None),
                                                              good[1]])))
    ck("a removed leg needs nothing (removal is free)",
       tree_findings({"bybit_1": {"strategies": ["s"]}}, strat, good) == [])
    ck("a one-shot observation with due_by is admissible",
       observation_problems({"what": "05:30Z report produced", "how_to_check": "ls comms/x",
                             "due_by": "2026-10-05T06:00Z"}) == [])
    ck("an observation without due_by is refused",
       any("due_by" in p for p in observation_problems({"what": "w", "how_to_check": "h"})))
    ck("an observation with a non-date due_by is refused",
       any("ISO" in p for p in observation_problems({"what": "w", "how_to_check": "h", "due_by": "soon"})))
    import tempfile  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        def sh(*a):
            return subprocess.run(a, cwd=td, capture_output=True, text=True, check=True)
        sh("git", "init", "-q", "-b", "main")
        sh("git", "config", "user.email", "t@t")
        sh("git", "config", "user.name", "t")
        d = Path(td) / PIPE_DIR
        d.mkdir(parents=True)
        (d / "1.json").write_text(json.dumps({"id": "OLD", "next_action": "check_observation"}))
        (d / "2.json").write_text(json.dumps({"id": "ROUTED", "next_action": "dispatch_lane"}))
        sh("git", "add", "-A")
        sh("git", "commit", "-qm", "b")
        global REPO
        saved = REPO
        REPO = Path(td)
        try:
            ck("an id already check_observation at base is recognised as an update",
               _prior_next_action("HEAD", "OLD") == "check_observation")
            ck("an item at base with another next_action is reported as such",
               _prior_next_action("HEAD", "ROUTED") == "dispatch_lane")
            ck("a brand-new id has no prior (still checked)", _prior_next_action("HEAD", "NEW") is None)
        finally:
            REPO = saved
    print("soak-contract-guard self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    # The diff-scoped verdict is about the COMMITTED tree; say so when that is
    # not the tree you edited (shared notice, see check_document_index.py).
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _dirty_tree  # noqa: E402,PLC0415 — path shim above
    _dirty_tree.warn()
    try:
        sys.path.insert(0, str(REPO))
        from src.config.accounts_loader import load_accounts_dict  # noqa: PLC0415
        errs: list = []
        acc = load_accounts_dict(errors=errs)
        if errs or not acc:
            # the loader returns {} on failure — an empty roster would pass this
            # guard vacuously, so it is "could not check", never "clean"
            raise ValueError(f"accounts config unreadable: {errs or 'no accounts'}")
        strat = yaml.safe_load(open(REPO / "config/strategies.yaml"))["strategies"]
        contracts = ss.load_contracts()
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"soak-contract-guard: COULD NOT CHECK — {exc}")
        return 2
    findings = tree_findings(acc, strat, contracts)
    if _git("rev-parse", "--verify", a.base).returncode == 0:
        findings += diff_findings(a.base, contracts)
    else:
        print(f"soak-contract-guard: base {a.base} not found — diff-scoped checks NOT run")
    designs: dict = {}
    for c in contracts:
        designs[c.get("design")] = designs.get(c.get("design"), 0) + 1
    print(f"soak-contract-guard: {len(contracts)} contract(s); design counts {designs}")
    if findings:
        print("::error::a soak without a definition of done:")
        for f in findings:
            print(f"  ✗ {f}")
        return 1
    print("soak-contract-guard: every soak carries a definition of done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
