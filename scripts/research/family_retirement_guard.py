#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (family-retirement-guard)
"""family-retirement-guard -- a FAMILY (an idea) cannot be retired without a review record.

WHY (operator directive 2026-10-16, via the manager, lane RESEARCH-ENGINE-1016): "do not give up on an
IDEA because one configuration failed a few tests ... it may mean we tested it wrong. Equally, don't sink
resources into ideas with no potential: if an idea is viable there should be outside information on how
to do it better." A cell FAIL closes the CONFIGURATION it tested -- automatic and fine. Retiring the
FAMILY is a different act and needs evidence of a different kind: a check that the test design was
sound, breadth across configs / windows / instruments, outside evidence with sources, and a stated
reason the idea itself is dead. Policy and thresholds: scripts/research/family_retirement_policy.yaml
(PROVISIONAL; lane IDEA-REVIEW-1016 proposes the protocol details).

WHAT IT REFUSES
  R1  a STRUCTURED retirement whose family has no valid review record at research/family_reviews/<f>.yaml:
        docs/plans/ml-plan-items.yaml  families.<f>.state in policy.retired_states
        research/queue/**/*.yaml        retires_family: <f>
        research/programmes/*.yaml      a node with retires_family: <f>
      (whole-tree: a structured retirement is never grandfathered, it is a claim the record must back)
  R2  a review record missing any of the SIX protocol items (IDEA-REVIEW-1016, PR #18560): (1) a test-design
      review answering every check in policy.test_design_checks; (2) breadth >= the minimums on configs /
      disjoint windows or folds / instruments, each entry with n + power, indeterminate counting zero; (3) a
      passing positive control (or none_exists_because); (4) >= outside_sources sources or the searches run,
      and NO source naming an untested condition (that is verdict A, never C); (5) every not-tested dimension
      covered_by a unit/row or irrelevant_because a reason; (6) a one-sentence mechanism-level reason. Only
      verdict C retires; A (tested wrong) and B (not yet testable) never do.
  R3  (with --base) an ADDED line on a research text surface that uses family-closure language
      (policy.closure_language) without citing `family_review: <f>` / research/family_reviews/<f>.yaml
      for a family whose review is valid. Added lines only: history is not re-litigated.

Config-level language is never refused: "this cell FAILED", "honest negative for this config", "this
lever setting does not earn its place on <leg>".
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[2]
POLICY = Path("scripts/research/family_retirement_policy.yaml")
REVIEWS = Path("research/family_reviews")
ML_PLAN = Path("docs/plans/ml-plan-items.yaml")
_NEG_RE = re.compile(r"\b(not|never|no longer|nor|doesn't|does not|do not|don't|cannot|isn't|is not)\b[^.;]*$", re.I)
_CITE_RE = re.compile(r"(?:family_review:\s*|research/family_reviews/)([A-Za-z0-9_.\-]+?)(?:\.ya?ml)?\b")


def _yaml():
    import yaml
    return yaml


def load_policy(root: Path) -> Dict[str, Any]:
    return _yaml().safe_load((root / POLICY).read_text(encoding="utf-8"))


def _counted(entries: Any) -> List[Dict[str, Any]]:
    """Breadth entries that COUNT: a mapping with n and power stated and a verdict that is not indeterminate."""
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        if e.get("n") in (None, "") or not str(e.get("power") or "").strip():
            continue
        if str(e.get("verdict") or "").lower() in ("indeterminate", "underpowered", "accruing", ""):
            continue
        out.append(e)
    return out


def review_problems(root: Path, family: str, policy: Dict[str, Any]) -> List[str]:
    """Empty when research/family_reviews/<family>.yaml carries all six protocol items at the policy minimums."""
    p = root / REVIEWS / f"{family}.yaml"
    if not p.is_file():
        return [f"no review record {p.relative_to(root).as_posix()}"]
    try:
        r = _yaml().safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, _yaml().YAMLError) as e:   # reported as a problem: an unreadable record is no record
        return [f"{p.name} unreadable ({e})"]
    return record_problems(r, family, policy)


def record_problems(r: Dict[str, Any], family: str, policy: Dict[str, Any]) -> List[str]:
    mins = policy.get("minimums") or {}
    out = []
    for k in ("family", "decided_at", "reviewed_by", "verdict", "test_design_review", "breadth", "positive_control",
              "outside_evidence", "not_tested", "reason_idea_dead"):
        if not r.get(k):
            out.append(f"missing `{k}`")
    if r.get("family") and str(r["family"]) != family:
        out.append(f"`family` is {r['family']!r}, file is {family}")
    if r.get("verdict") and str(r["verdict"]) not in [str(v) for v in policy.get("retiring_verdicts") or ["C"]]:
        out.append(f"verdict {r['verdict']!r} does not retire (A = tested wrong -> re-test; B = not testable -> a row)")
    # (1) test-design review
    tdr = r.get("test_design_review") or {}
    if isinstance(tdr, dict):
        if not str(tdr.get("first_skeptic_flaw") or "").strip():
            out.append("test_design_review.first_skeptic_flaw: name the flaw a skeptic raises first and why it does not apply")
        checks = tdr.get("checks") if isinstance(tdr.get("checks"), dict) else {}
        for c in policy.get("test_design_checks") or []:
            if not str(checks.get(c) or "").strip():
                out.append(f"test_design_review.checks.{c} unanswered")
        if str(tdr.get("verdict") or "sound") == "flawed":
            out.append("test_design_review.verdict is `flawed`: we tested it wrong -- fix the test, do not retire")
    # (2) breadth: counted entries only (n + power stated; indeterminate counts zero)
    br = r.get("breadth") or {}
    if isinstance(br, dict):
        if not str(br.get("primary_axis") or "").strip():
            out.append("breadth.primary_axis: name the family's main axis")
        nc = len(_counted(br.get("configs")))
        if nc < int(mins.get("configs_tested") or 0):
            out.append(f"breadth.configs: {nc} counted (n + power, not indeterminate) < {mins.get('configs_tested')}")
        nw, nf = len(_counted(br.get("windows"))), len(_counted(br.get("folds")))
        if nw < int(mins.get("windows_disjoint") or 0) and nf < int(mins.get("folds_alternative") or 0):
            out.append(f"breadth: {nw} disjoint window(s) < {mins.get('windows_disjoint')} and {nf} fold(s) < "
                       f"{mins.get('folds_alternative')}")
        ni = len(_counted(br.get("instruments")))
        if ni < int(mins.get("instruments_tested") or 0) and not str(br.get("instrument_specific_why") or "").strip():
            out.append(f"breadth.instruments: {ni} counted < {mins.get('instruments_tested')} and no instrument_specific_why")
    # (3) positive control
    pc = r.get("positive_control") or {}
    if isinstance(pc, dict) and not (pc.get("passed") is True and str(pc.get("test") or "").strip()) \
            and not str(pc.get("none_exists_because") or "").strip():
        out.append("positive_control: name a test that should pass on the same instrument/data/join and show it passing "
                   "(passed: true + test), or none_exists_because -- a negative needs a denominator")
    # (4) outside evidence
    oe = r.get("outside_evidence") or {}
    if isinstance(oe, dict):
        srcs = [s for s in (oe.get("sources") or []) if isinstance(s, dict)
                and str(s.get("source") or "").strip() and str(s.get("finding") or "").strip()]
        searches = [q for q in (oe.get("searches") or []) if str(q).strip()]
        no_source_record = not srcs and len(searches) >= int(mins.get("searches_if_no_sources") or 0)
        if len(srcs) < int(mins.get("outside_sources") or 0) and not no_source_record:
            out.append(f"outside_evidence: {len(srcs)} source(s) < {mins.get('outside_sources')} and not a no-source "
                       f"record with >= {mins.get('searches_if_no_sources')} searches listed")
        named = [str(s.get("source")) for s in srcs if s.get("names_untested_condition")]
        if named:
            out.append(f"outside_evidence names an UNTESTED condition ({', '.join(named)}): verdict is A "
                       "(tested wrong), never C -- register the re-test")
    # (5) not-tested list
    nt = r.get("not_tested") if isinstance(r.get("not_tested"), dict) else {}
    for d in policy.get("not_tested_dimensions") or []:
        e = nt.get(d)
        if not (isinstance(e, dict) and (str(e.get("covered_by") or "").strip()
                                         or str(e.get("irrelevant_because") or "").strip())):
            out.append(f"not_tested.{d}: covered_by a unit/row, or irrelevant_because a reason")
    # (6) the reason the IDEA is dead: one sentence about the mechanism
    reason = str(r.get("reason_idea_dead") or "").strip()
    if len(reason) < int(mins.get("reason_chars") or 0):
        out.append(f"reason_idea_dead shorter than {mins.get('reason_chars')} chars")
    if re.search(r"\b(every|all)\s+(the\s+)?(cell|config\w*|unit|test)s?\b.*\bfail", reason, re.I):
        out.append("reason_idea_dead restates the breadth ('every cell failed') -- name the MECHANISM")
    return out


def structured_retirements(root: Path, policy: Dict[str, Any]) -> List[Dict[str, str]]:
    """[{family, where}] for every structured retirement in the tree."""
    yaml = _yaml()
    states = {str(s) for s in policy.get("retired_states") or []}
    out: List[Dict[str, str]] = []
    plan = root / ML_PLAN
    if plan.is_file():
        doc = yaml.safe_load(plan.read_text(encoding="utf-8")) or {}
        for fam, f in (doc.get("families") or {}).items():
            if isinstance(f, dict) and str(f.get("state") or "") in states:
                out.append({"family": str(fam), "where": f"{ML_PLAN.as_posix()} families.{fam}.state={f['state']}"})
    for p in sorted((root / "research" / "queue").rglob("*.yaml")):
        try:
            u = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            # allow-silent: an unreadable unit is reported by the queue validators
            # (check_research_queue_decision_rule.py / queue_replenish existing_units `bad`), not here
            continue
        if isinstance(u, dict) and u.get("retires_family"):
            out.append({"family": str(u["retires_family"]), "where": p.relative_to(root).as_posix()})
    for p in sorted((root / "research" / "programmes").glob("*.yaml")):
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for n in doc.get("nodes") or []:
            if isinstance(n, dict) and n.get("retires_family"):
                out.append({"family": str(n["retires_family"]), "where": f"{p.relative_to(root).as_posix()} {n.get('id')}"})
    return out


def _added_lines(root: Path, base: str, globs: List[str]) -> List[tuple]:
    mb = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    if not mb:
        return []
    diff = subprocess.run(["git", "diff", "-U0", "--no-color", mb, "HEAD", "--", *globs], cwd=root,
                          capture_output=True, text=True).stdout
    out, path = [], None
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            path = ln[6:] if ln.startswith("+++ b/") else None
        elif ln.startswith("+") and not ln.startswith("+++") and path:
            out.append((path, ln[1:]))
    return out


def closure_text_problems(lines: List[tuple], policy: Dict[str, Any], valid: set) -> List[str]:
    pats = [re.compile(p, re.I) for p in policy.get("closure_language") or []]
    out = []
    for path, text in lines:
        if not any(fnmatch.fnmatch(path, g) for g in policy.get("text_surfaces") or []):
            continue
        hits = [m for p in pats for m in p.finditer(text)]
        # A NEGATED closure ("this does not kill the idea", "not that the family is dead") is the opposite
        # claim -- exactly the wording the directive wants -- so it never needs a review.
        hits = [m for m in hits if not _NEG_RE.search(text[max(0, m.start() - 30):m.start()])]
        if not hits:
            continue
        cited = set(_CITE_RE.findall(text))
        if cited and cited <= valid:
            continue
        why = (f"cites {sorted(cited - valid)} with no valid review" if cited else "cites no family review")
        out.append(f"R3 {path}: family-closure language {why}: {text.strip()[:140]!r} -- say what CONFIG failed, "
                   "or write research/family_reviews/<family>.yaml and cite it")
    return out


def check(root: Path, base: Optional[str]) -> List[str]:
    policy = load_policy(root)
    problems: List[str] = []
    valid = set()
    for p in sorted((root / REVIEWS).glob("*.yaml")):
        fam = p.stem
        bad = review_problems(root, fam, policy)
        if bad:
            problems += [f"R2 {p.relative_to(root).as_posix()}: {b}" for b in bad]
        else:
            valid.add(fam)
    for r in structured_retirements(root, policy):
        if r["family"] not in valid:
            problems.append(f"R1 {r['where']}: retires family {r['family']!r} without a valid review record "
                            f"({'; '.join(review_problems(root, r['family'], policy))})")
    if base:
        problems += closure_text_problems(_added_lines(root, base, ["research", "docs", "comms"]), policy, valid)
    return problems


def _good_record(family: str = "deep_sequence") -> Dict[str, Any]:
    ent = lambda i, n=300: {"id": i, "n": n, "power": 0.8, "verdict": "fail"}  # noqa: E731
    return {
        "family": family, "decided_at": "2030-01-01", "reviewed_by": "lane X", "verdict": "C",
        "test_design_review": {
            "first_skeptic_flaw": "window too short for the mechanism; it spans both vol regimes, so it does not apply",
            "checks": {"window_vs_mechanism": "two years, both regimes", "metric_vs_baseline": "logloss vs prior",
                       "label_vs_payoff": "R-weighted", "gate_vs_size": "fixed size, no gate",
                       "harness_validity": "no known defect at run time", "reference_distribution": "block bootstrap"}},
        "breadth": {"primary_axis": "architecture", "configs": [ent("c1"), ent("c2"), ent("c3")],
                    "windows": [ent("w1"), ent("w2")], "instruments": [ent("BTC"), ent("ETH")]},
        "positive_control": {"test": "RQ-POS", "passed": True},
        "outside_evidence": {"sources": [{"source": s, "primary": True, "finding": "no edge at this horizon"}
                                         for s in ("paper A", "repo B", "model card C")]},
        "not_tested": {d: {"covered_by": f"RQ-{d}"} for d in
                       ("horizon", "instrument", "conditioning", "cost", "label", "sizing", "exit", "regime", "holding_period")},
        "reason_idea_dead": "the edge was lookahead in the label join and no independent test of the concept exists",
    }


def _self_test() -> int:
    import copy
    import shutil
    import tempfile
    yaml = _yaml()
    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        for d in (r / "research" / "queue", r / "research" / "programmes", r / REVIEWS, r / "docs" / "plans",
                  (r / POLICY).parent):
            d.mkdir(parents=True, exist_ok=True)
        shutil.copy(_REPO / POLICY, r / POLICY)
        pol = load_policy(r)
        (r / ML_PLAN).write_text("families:\n  deep_sequence: {history: [FAIL], state: retired}\n  gbdt: {history: []}\n")
        (r / "research" / "queue" / "RQ-1.yaml").write_text("id: RQ-1\nstatus: retired\nretires_family: giveback_stop\n")
        (r / "research" / "queue" / "RQ-2.yaml").write_text("id: RQ-2\nstatus: done\nretires_family: false\n")  # default: no claim
        p = check(r, None)
        assert sum(x.startswith("R1") for x in p) == 2, p           # two structured retirements, no records
        good = _good_record()
        assert record_problems(good, "deep_sequence", pol) == [], record_problems(good, "deep_sequence", pol)
        (r / REVIEWS / "deep_sequence.yaml").write_text(yaml.safe_dump(good))
        p = check(r, None)
        assert not any("deep_sequence" in x for x in p) and any("giveback_stop" in x for x in p), p

        def bad(mutate) -> List[str]:
            rec = copy.deepcopy(good)
            mutate(rec)
            return record_problems(rec, "deep_sequence", pol)
        # each of the six items, and the verdict letter, is enforced by name
        assert any("does not retire" in b for b in bad(lambda x: x.update(verdict="A")))
        assert any("flawed" in b for b in bad(lambda x: x["test_design_review"].update(verdict="flawed")))
        assert any("checks.gate_vs_size" in b for b in bad(lambda x: x["test_design_review"]["checks"].pop("gate_vs_size")))
        assert any("breadth.configs" in b for b in bad(lambda x: x["breadth"]["configs"][2].update(verdict="indeterminate")))
        assert any("breadth.configs" in b for b in bad(lambda x: x["breadth"]["configs"][1].pop("power")))
        assert not any(b.startswith("breadth:") for b in bad(lambda x: x["breadth"].update(
            windows=[], folds=[{"id": f"f{i}", "n": 50, "power": 0.6, "verdict": "fail"} for i in range(4)])))
        assert any(b.startswith("breadth:") for b in bad(lambda x: x["breadth"].update(windows=good["breadth"]["windows"][:1])))
        assert any("breadth.instruments" in b for b in bad(lambda x: x["breadth"].update(instruments=[x["breadth"]["instruments"][0]])))
        assert not bad(lambda x: x["breadth"].update(instruments=[x["breadth"]["instruments"][0]],
                                                     instrument_specific_why="the idea is MES-session specific"))
        assert any("positive_control" in b for b in bad(lambda x: x["positive_control"].update(passed=False)))
        assert not bad(lambda x: x.update(positive_control={"none_exists_because": "no instrument has a known-good signal"}))
        assert any("verdict is A" in b for b in bad(lambda x: x["outside_evidence"]["sources"][0].update(
            names_untested_condition=True)))
        assert any("outside_evidence" in b for b in bad(lambda x: x["outside_evidence"]["sources"].pop()))
        assert not bad(lambda x: x.update(outside_evidence={"sources": [], "searches": ["q1", "q2", "q3"]}))
        assert any("not_tested.regime" in b for b in bad(lambda x: x["not_tested"].pop("regime")))
        assert not bad(lambda x: x["not_tested"].update(regime={"irrelevant_because": "no regime conditioning in the idea"}))
        assert any("MECHANISM" in b for b in bad(lambda x: x.update(
            reason_idea_dead="every config we ran failed at declared power on every window")))
        # an invalid record does not back its retirement
        (r / REVIEWS / "giveback_stop.yaml").write_text(yaml.safe_dump({**good, "family": "giveback_stop", "verdict": "A"}))
        assert any(x.startswith("R1 research/queue/RQ-1.yaml") for x in check(r, None))
        # R3: closure language on an added line; config language and negations pass; a valid citation passes
        lines = [("research/queue/RQ-2.yaml", "result: the giveback family is dead after one FAIL"),
                 ("research/queue/RQ-3.yaml", "result: this cell FAILED -- honest negative for this config"),
                 ("docs/plans/ml-plan-items.yaml", "close: line is exhausted (family_review: deep_sequence)"),
                 ("src/x.py", "# the family is dead"),                  # not a research surface
                 ("research/programmes/03.yaml", "retire the whole family (family_review: nope)"),
                 ("research/queue/RQ-4.yaml", "A NULL/FAIL here does not kill the idea"),
                 ("research/queue/RQ-5.yaml", "INDETERMINATE says n was the limit, not that the family is dead.")]
        t = closure_text_problems(lines, pol, {"deep_sequence"})
        assert len(t) == 2 and "RQ-2" in t[0] and "nope" in t[1], t
    print("family-retirement-guard self-test OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--base", default=None, help="also grade ADDED lines vs this base (R3)")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    problems = check(_REPO, args.base)
    for p in problems:
        print(f"::error::family-retirement-guard: {p}")
    print("family-retirement-guard: " + ("OK" if not problems else f"FAIL ({len(problems)})"))
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
