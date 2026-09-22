#!/usr/bin/env python3
"""Does this diff put a strategy leg onto a RISK-BEARING roster, and if so does
a committed evidence record clear the operator's four-clause bar?

WHY THIS EXISTS — THE ASYMMETRY, MEASURED
-----------------------------------------
`scripts/check_dry_run_in_diff.py` fails any PR that ADDS a `mode: dry_run` or
`execution: shadow` line without an operator marker. Across the whole guard
fleet nothing blocked turning a leg ON. That is the mechanical fact behind the
roster going 36 -> 55 while every memo said cut.

⚠️ AND IT IS SHARPER THAN "off is guarded, on is free". There are TWO SPELLINGS
OF OFF and the existing guard sees only one. It matches ADDED LINES, so
`execution: shadow` is caught and REMOVING A LEG FROM AN ACCOUNT'S `strategies:`
ROSTER IS NOT — verified 2026-09-21 against the A6 diff, which removed two legs
from `alpaca_live` and reported `clean`.

**SO THIS GUARD KEYS ON ROSTER MEMBERSHIP, NOT ON THE `mode` / `execution`
FIELDS.** It compares the PARSED roster at the fork point against the parsed
roster at HEAD. A guard watching only the fields can be walked around by editing
the list, in either direction; a guard watching set membership cannot. That also
covers the mandate case: `MD-PROMOTE-S1-S2` adds a leg to a roster, which is
exactly the edit the line-matching guard cannot see.

THE BAR — DECIDED BY THE OPERATOR 2026-09-21
--------------------------------------------
A leg may reach a real-money roster only on a COMMITTED EVIDENCE RECORD carrying
exactly these four clauses:

  C1  a named harness
  C2  a stated n
  C3  net of the FULL cost stack
  C4  clearing a rule registered BEFORE the run

⚠️ A CLAIM IN A PR BODY IS NOT A RECORD. That distinction is the entire safety
property once `B5` takes the human out of the path, and it is why this guard
reads `comms/strategy_evidence/<leg>.json` — a committed artifact — and nothing
else. It does not read the PR body, the commit message, or a checklist note.

⚠️ THERE IS NO MINIMUM `n`, AND ITS ABSENCE IS A DECISION, NOT AN OVERSIGHT.
The operator was offered a fifth clause setting a sample-size floor and DECLINED
it on 2026-09-21. C2 therefore checks that n is STATED and real, never that it is
large. Do not add a floor here; that is a Tier-3 change to a decided bar, not a
tightening a later session may make on its own initiative. (Context, not licence:
`tlt_pullback_1d` reached a real-money roster on n=8.)

FAIL CLOSED — THE POLARITY IS BORROWED, NOT INVENTED
----------------------------------------------------
`check_pr_landing.py::TIER1_SURFACE` and `check_manager_scope.py::MANAGER_SURFACE`
are both ALLOWLISTS of the SAFE value, so a value the guard cannot vouch for
arms nothing. Same polarity here: `NON_RISK_CLASSES` allowlists the ONE
`account_class` this guard vouches for as carrying no real risk. Every other
value — `real_money`, `prop`, a new venue category nobody has told this guard
about, a typo, an absent field — is RISK-BEARING and requires evidence.

⚠️ THE RISK-BEARING SET IS DERIVED, NEVER HARDCODED. Hardcoding
`{"bybit_2", "alpaca_live"}` is how a guard silently goes stale the day an
account is added. The classification reads `config/accounts.yaml::<acct>.account_class`
and is cross-checked against the canonical vocabulary in
`src/units/accounts/account.py::_VALID_ACCOUNT_CLASSES` — parsed statically, so
this guard never imports the trading runtime.

⚠️ `account_class`, NOT `mode:`. `ib_live` is `account_class: real_money` while
`mode: dry_run`. Keying on `mode` would let a leg be listed on a real-money
roster with no evidence and then armed by a separate `set-account-mode` action
that never re-checks the roster. The funding category is the stable fact; the
mode is a switch someone flips later.

DEMOTION STAYS FREE — AND THAT IS THE POINT OF INVERTING
--------------------------------------------------------
A leg REMOVED from a roster produces no finding, ever. An account moved from a
risk-bearing class to `paper` produces no finding. Taking risk OFF requires
nothing; putting risk ON requires evidence. A change that makes removal harder
has broken this guard's purpose, and `--self-test` plants both directions.

THE PAIRS SLEEVE IS A SECOND DOOR, SO IT IS COVERED
---------------------------------------------------
The M22 market-neutral sleeve is an ISOLATED 2-leg order path
(`src.units.strategies.pairs_executor`) whose gate lives in `config/pairs.yaml`,
not in `strategies.yaml`. Measured 2026-09-21: `account_id: bybit_1` (paper) with
`pairs_bnb_btc` and `pairs_sol_eth` at `execution: live`. So repointing ONE
`account_id:` line arms two live pairs on a real-money account, touching neither
roster this guard would otherwise read. That is the same question through a
different file, so it is the same guard.

WHAT THIS GUARD DOES NOT DO — STATED, NOT HIDDEN
------------------------------------------------
  * It does not grade the QUALITY of an edge. It checks that a record exists,
    is about this leg, and carries the four clauses. A record can clear the bar
    and still describe a bad strategy; that is a research question, not a CI one.
  * It does not guard `mode: dry_run -> live` on an account. That is the
    `set-account-mode` system-action, Tier-3 and operator-gated, and it already
    has a human in the path. Flipping `ib_live` to live today arms zero legs
    because its roster is empty.
  * It is DIFF-SCOPED: the existing roster is grandfathered and the future is
    not. Same polarity as `check_backlog_criteria.py`. A whole-tree version
    would fail `main` on day one over 12 legs nobody is proposing to promote,
    which is the desensitised alarm this repo treats as its own P1. The standing
    census is reported by `--population` instead, which never fails a build.
  * `--population` MEASURES the existing roster against the same four clauses.
    Run it to see the debt; it exits 0 by design.

Exit codes: 0 clean · 1 findings · 2 COULD NOT MEASURE (an unreadable base or an
unparseable config — never reported as defects, per
docs/CLAUDE-RULES-CANONICAL.md § "'could not measure' is its own outcome").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _git_base import ABSENT_AT_BASE, READ, UNREADABLE, read_at, resolve_base  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - environment problem, not a finding
    print("roster-promotion-evidence: COULD NOT MEASURE — pyyaml is not installed",
          file=sys.stderr)
    sys.exit(2)

REPO = Path(__file__).resolve().parents[2]

ACCOUNTS_REL = "config/accounts.yaml"
STRATEGIES_REL = "config/strategies.yaml"
PAIRS_REL = "config/pairs.yaml"
EVIDENCE_DIR_REL = "comms/strategy_evidence"
ACCOUNT_MODULE_REL = "src/units/accounts/account.py"

#: The ONLY `account_class` this guard vouches for as carrying no real risk.
#: An ALLOWLIST of the SAFE value — see the module docstring. Anything else,
#: including a value this guard has never seen and a missing field, is
#: risk-bearing and requires evidence.
NON_RISK_CLASSES = frozenset({"paper"})

#: Components that must ALL be explicitly declared for C3 ("net of the FULL
#: cost stack") to hold. `fee_bps_roundtrip` alone is FEE-ONLY: CLAUDE.md records
#: that the harnesses default slippage and funding to 0.0, so today's corpus is
#: optimistic by an unknown amount (+0.57R on the one leg measured) and the
#: real-money promotion mandate does not arm until D1 lands. This constant is the
#: consumer side of D1; the producer side is D1's to build.
REQUIRED_COST_COMPONENTS = ("fees", "slippage", "funding")

#: C1 holds only in this `coverage_state`. The other three each say we did NOT
#: get a number, and they say so distinctly on purpose — see the
#: comms/strategy_evidence README. `no_harness` in particular means "nothing
#: routes this leg", which is a statement about US, never about the strategy.
MEASURED_STATE = "measured"

MACHINE_PREFIX = "ROSTER_PROMOTION_EVIDENCE"


# --------------------------------------------------------------------------
# config reading
# --------------------------------------------------------------------------
class CouldNotMeasure(Exception):
    """Raised when an input is unreadable. NEVER reported as a finding."""


def _load_yaml(text: Optional[str], what: str) -> Dict[str, Any]:
    if text is None:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CouldNotMeasure(f"{what} does not parse as YAML: {exc}") from exc
    return data if isinstance(data, dict) else {}


def rosters(accounts_text: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """`{account_id: {"class": str|None, "mode": str|None, "legs": [str]}}`.

    `class` is returned VERBATIM — including `None` for an absent field — so the
    caller can fail closed on it rather than receiving a guessed default.
    """
    data = _load_yaml(accounts_text, ACCOUNTS_REL)
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for acct, cfg in accounts.items():
        if not isinstance(cfg, dict):
            continue
        raw_class = cfg.get("account_class")
        legs = cfg.get("strategies")
        out[str(acct)] = {
            "class": None if raw_class is None else str(raw_class).strip().lower(),
            "mode": None if cfg.get("mode") is None else str(cfg.get("mode")).strip().lower(),
            "legs": [str(x) for x in legs] if isinstance(legs, list) else [],
        }
    return out


def is_risk_bearing(account_class: Optional[str]) -> bool:
    """Fail closed: only an explicitly vouched-for class is exempt."""
    return account_class not in NON_RISK_CLASSES


def pairs_state(pairs_text: Optional[str]) -> Dict[str, Any]:
    """`{"account_id": str|None, "live_pairs": [name]}` for config/pairs.yaml."""
    data = _load_yaml(pairs_text, PAIRS_REL)
    acct = data.get("account_id")
    live: List[str] = []
    entries = data.get("pairs")
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            # Default-permissive, exactly like strategies.yaml: omitting
            # `execution` means live. Treating a missing field as shadow here
            # would make the guard quiet on the very edit that adds a pair
            # without declaring one.
            if str(item.get("execution", "live")).strip().lower() != "shadow":
                live.append(str(item.get("name") or "<unnamed>"))
    return {"account_id": None if acct is None else str(acct), "live_pairs": live}


def canonical_account_classes(module_text: Optional[str]) -> Optional[frozenset]:
    """Parse `_VALID_ACCOUNT_CLASSES` statically. `None` ⇒ could not read it.

    Static, so this CI guard never imports the trading runtime. Used only to
    make a vocabulary change VISIBLE — the classification itself does not depend
    on it, because an unrecognised class is already risk-bearing.
    """
    if not module_text:
        return None
    m = re.search(r"_VALID_ACCOUNT_CLASSES\s*=\s*frozenset\(\{([^}]*)\}\)", module_text)
    if not m:
        return None
    return frozenset(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)))


# --------------------------------------------------------------------------
# evidence records
# --------------------------------------------------------------------------
# ⚠️ MUST STAY IDENTICAL to `build_strategy_evidence.py::_GATE_FIELDS`.
# Execution gates, excluded from the fingerprint because they decide whether a
# leg trades and not what a harness would produce for it. Added 2026-09-22 (E40)
# after the first promotion attempted since this guard landed was refused by it:
# flipping `execution: shadow -> live` moved the digest, so every promotion
# invalidated its own evidence. Do NOT widen this set to quiet a complaint.
_GATE_FIELDS: frozenset = frozenset({"execution", "enabled"})


def config_fingerprint(cfg: Dict[str, Any]) -> str:
    """Stable digest of a leg's STRATEGY PARAMETERS.

    ⚠️ DELIBERATELY BYTE-IDENTICAL to
    `scripts/ops/build_strategy_evidence.py::config_fingerprint`. It is
    re-implemented rather than imported so this guard does not pull the producer
    (and its research dependencies) into CI. Verified 2026-09-21 by recomputing
    against all 52 committed records: 52 match, 0 drift. `--self-test` plants a
    drift case so the check is shown capable of turning red.

    ⚠️ 2026-09-22 (E40): now digests the strategy PARAMETERS, excluding
    `_GATE_FIELDS`. The 52 records were migrated in the same commit, and the
    migration PRESERVED the staleness signal rather than washing it out — a
    record whose params had genuinely drifted was left carrying its old digest
    so it still reads STALE.
    """
    params = {k: v for k, v in (cfg or {}).items() if k not in _GATE_FIELDS}
    blob = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _iso(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def clause_verdicts(record: Optional[Dict[str, Any]]) -> List[Tuple[str, bool, str]]:
    """`[(clause, ok, detail)]` for the operator's four clauses, in order."""
    if record is None:
        absent = "no committed record at comms/strategy_evidence/<leg>.json"
        return [(c, False, absent) for c in ("C1", "C2", "C3", "C4")]

    out: List[Tuple[str, bool, str]] = []

    # C1 — a named harness.
    state = str(record.get("coverage_state") or "").strip().lower()
    harness = _iso(record.get("harness"))
    if state != MEASURED_STATE:
        out.append(("C1", False, f"coverage_state={state or 'absent'!r} (not {MEASURED_STATE!r}) "
                                 f"— no harness produced a number"))
    elif not harness:
        out.append(("C1", False, "coverage_state=measured but `harness` is empty — unnamed harness"))
    else:
        out.append(("C1", True, f"harness={harness!r}"))

    # C2 — a stated n. STATED and real; NO FLOOR (operator declined, 2026-09-21).
    n = record.get("n_trades_oos")
    if isinstance(n, bool) or not isinstance(n, int):
        out.append(("C2", False, f"n_trades_oos={n!r} is not a stated integer"))
    elif n < 1:
        out.append(("C2", False, f"n_trades_oos={n} — a verdict over zero trades is vacuous"))
    else:
        out.append(("C2", True, f"n_trades_oos={n} (no minimum is applied — see module docstring)"))

    # C3 — net of the FULL cost stack.
    stack = record.get("cost_stack")
    if not isinstance(stack, dict):
        have = "fee_bps_roundtrip only" if record.get("fee_bps_roundtrip") is not None else "nothing"
        out.append(("C3", False,
                    f"no `cost_stack` block ({have}) — fee-only is not net of the full cost stack; "
                    f"needs {', '.join(REQUIRED_COST_COMPONENTS)} (producer side is D1)"))
    else:
        missing = []
        for comp in REQUIRED_COST_COMPONENTS:
            val = stack.get(comp)
            # An ABSENT or null component is UNRESOLVED, and unresolved is not
            # zero. Collapsing those two is the harness-cost-basis defect (#8685):
            # `None` "we did not model it" read as `0.0` "explicitly costless".
            if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
                missing.append(f"{comp}={val!r}")
        if missing:
            out.append(("C3", False, "cost_stack components unresolved: " + ", ".join(missing)))
        else:
            out.append(("C3", True, "cost_stack: " + ", ".join(
                f"{c}={stack[c]}" for c in REQUIRED_COST_COMPONENTS)))

    # C4 — clearing a rule REGISTERED BEFORE THE RUN.
    rule = record.get("decision_rule")
    generated_at = _iso(record.get("generated_at"))
    if not isinstance(rule, dict):
        prov = record.get("param_selection_provenance")
        out.append(("C4", False,
                    f"no `decision_rule` block (param_selection_provenance={prov!r}) "
                    f"— nothing says a rule existed before the run"))
    else:
        rid, text = _iso(rule.get("id")), _iso(rule.get("rule"))
        registered_at, verdict = _iso(rule.get("registered_at")), _iso(rule.get("verdict"))
        if not rid or not text:
            out.append(("C4", False, "decision_rule needs a non-empty `id` and `rule`"))
        elif not registered_at:
            out.append(("C4", False, f"decision_rule {rid!r} has no `registered_at`"))
        elif not generated_at:
            out.append(("C4", False, "record has no `generated_at` — the run has no time, so "
                                     "'registered before' is unevaluable"))
        elif registered_at >= generated_at:
            # ISO-8601 UTC strings order lexicographically. This is the arithmetic
            # that makes the clause checkable rather than a claim: a rule written
            # after the run is post-hoc, which is what C4 exists to refuse.
            out.append(("C4", False,
                        f"decision_rule {rid!r} registered_at={registered_at} is NOT before "
                        f"generated_at={generated_at} — post-hoc registration"))
        elif verdict != "pass":
            out.append(("C4", False, f"decision_rule {rid!r} verdict={verdict!r} (not 'pass') "
                                     f"— the record does not say the rule was cleared"))
        else:
            out.append(("C4", True, f"decision_rule {rid!r} registered {registered_at} "
                                    f"< run {generated_at}, verdict=pass"))
    return out


def identity_verdict(record: Optional[Dict[str, Any]], leg: str,
                     strategies: Dict[str, Any]) -> Tuple[bool, str]:
    """Is the record ABOUT the leg being armed, as currently configured?

    ⚠️ THIS IS NOT A FIFTH CLAUSE OF THE BAR, and it is reported separately so it
    cannot be mistaken for one. The operator set four clauses and declined a
    fifth. This checks a precondition all four depend on: without it, C1–C4 can
    all hold for a record describing a DIFFERENT configuration of the leg, and
    the bar is satisfied by evidence about something else.
    """
    if record is None:
        return False, "no record"
    cfg = strategies.get(leg)
    if not isinstance(cfg, dict):
        return False, f"{leg!r} has no block in {STRATEGIES_REL} — cannot bind the record to a config"
    want, have = config_fingerprint(cfg), _iso(record.get("config_fingerprint"))
    if not have:
        return False, "record carries no config_fingerprint"
    if have != want:
        return False, (f"config_fingerprint {have} != current config {want} — the record is about "
                       f"a different configuration of this leg (STALE)")
    return True, f"config_fingerprint {have} matches current config"


def load_record(leg: str, evidence_dir: Path) -> Optional[Dict[str, Any]]:
    path = evidence_dir / f"{leg}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------
def promotions(base_accounts: Optional[str], head_accounts: Optional[str],
               base_pairs: Optional[str] = None,
               head_pairs: Optional[str] = None) -> List[Dict[str, str]]:
    """Every leg this diff puts onto a RISK-BEARING roster.

    Three shapes, because there are three ways in and a guard that sees one of
    them is walked around by the other two:

      `roster_add`   a leg appended to a risk-bearing account's `strategies:`
      `class_flip`   an account moved INTO a risk-bearing class while carrying
                     legs — one line that arms the whole roster at once
      `pairs_repoint` `config/pairs.yaml::account_id` repointed at a
                     risk-bearing account while any pair is not `shadow`

    Removals return NOTHING, in every shape. Demotion is free.
    """
    base, head = rosters(base_accounts), rosters(head_accounts)
    found: List[Dict[str, str]] = []
    for acct, cur in sorted(head.items()):
        if not is_risk_bearing(cur["class"]):
            continue  # a `paper` roster is not a promotion surface
        prev = base.get(acct)
        was_risk = prev is not None and is_risk_bearing(prev["class"])
        if prev is not None and not was_risk:
            # The account crossed INTO a risk-bearing class. Every leg it
            # carries is newly real-money, including ones that were already
            # listed — nothing about them was evidenced for this class.
            for leg in cur["legs"]:
                found.append({"kind": "class_flip", "account": acct, "leg": leg,
                              "why": f"account_class {prev['class']!r} -> {cur['class']!r}"})
            continue
        known = set(prev["legs"]) if prev is not None else set()
        for leg in cur["legs"]:
            if leg not in known:
                found.append({"kind": "roster_add", "account": acct, "leg": leg,
                              "why": f"added to {acct} (account_class={cur['class']!r})"})

    if head_pairs is not None:
        ph, pb = pairs_state(head_pairs), pairs_state(base_pairs)
        target = ph["account_id"]
        if target and ph["live_pairs"]:
            acct_cfg = head.get(target)
            cls = acct_cfg["class"] if acct_cfg else None
            was_target = pb["account_id"]
            was_safe = was_target != target or set(pb["live_pairs"]) != set(ph["live_pairs"])
            if is_risk_bearing(cls) and was_safe:
                for name in ph["live_pairs"]:
                    found.append({"kind": "pairs_repoint", "account": target, "leg": name,
                                  "why": f"{PAIRS_REL} account_id={target!r} "
                                         f"(account_class={cls!r}), pair not shadow"})
    return found


def grade(found: Sequence[Dict[str, str]], strategies: Dict[str, Any],
          evidence_dir: Path) -> List[str]:
    """Findings — one multi-line block per leg that fails the bar."""
    findings: List[str] = []
    for item in found:
        leg = item["leg"]
        record = load_record(leg, evidence_dir)
        clauses = clause_verdicts(record)
        ident_ok, ident_detail = identity_verdict(record, leg, strategies)
        failed = [(c, d) for c, ok, d in clauses if not ok]
        if not failed and ident_ok:
            continue
        lines = [f"{leg} -> {item['account']} [{item['kind']}]: {item['why']}"]
        for c, ok, detail in clauses:
            lines.append(f"      {c} {'PASS' if ok else 'FAIL'}: {detail}")
        lines.append(f"      identity {'PASS' if ident_ok else 'FAIL'}: {ident_detail}")
        findings.append("\n".join(lines))
    return findings


def census(accounts_text: Optional[str], strategies: Dict[str, Any],
           evidence_dir: Path) -> Dict[str, Any]:
    """The STANDING population: today's risk-bearing roster against the same bar.

    This is the headline number and it is measured, not asserted. It never fails
    a build — the existing roster is grandfathered (see the module docstring).
    """
    accounts = rosters(accounts_text)
    risk = {a: c for a, c in accounts.items() if is_risk_bearing(c["class"])}
    slots: List[Dict[str, Any]] = []
    for acct, cfg in sorted(risk.items()):
        for leg in cfg["legs"]:
            record = load_record(leg, evidence_dir)
            clauses = clause_verdicts(record)
            ident_ok, ident_detail = identity_verdict(record, leg, strategies)
            slots.append({
                "account": acct, "leg": leg, "account_class": cfg["class"],
                "mode": cfg["mode"], "has_record": record is not None,
                "clauses": {c: ok for c, ok, _ in clauses},
                "failed": [f"{c}: {d}" for c, ok, d in clauses if not ok],
                "identity_ok": ident_ok, "identity": ident_detail,
                "passes_bar": all(ok for _, ok, _ in clauses) and ident_ok,
            })
    return {
        "risk_bearing_accounts": sorted(risk),
        "paper_accounts": sorted(a for a in accounts if not is_risk_bearing(accounts[a]["class"])),
        "roster_slots": len(slots),
        "distinct_legs": len({s["leg"] for s in slots}),
        "passing": sum(1 for s in slots if s["passes_bar"]),
        "failing": sum(1 for s in slots if not s["passes_bar"]),
        "slots": slots,
    }


# --------------------------------------------------------------------------
# self-test — planted violations AND controls
# --------------------------------------------------------------------------
_ACC = """
accounts:
  paper_acct:
    account_class: paper
    mode: live
    strategies: [leg_ok, leg_bad]
  real_acct:
    account_class: real_money
    mode: live
    strategies: [leg_ok]
  dry_real_acct:
    # ⚠️ NO `mode:` LINE HERE, DELIBERATELY. This account exists to show that a
    # real-money account requires evidence regardless of its mode — and this
    # guard never branches on `mode` at all, so spelling one in a FIXTURE buys
    # nothing. It would also trip `dry-run-guard`, which matches the literal
    # line anywhere outside tests/ and docs/. Satisfying that with its
    # operator-permission allow-marker would spend an escape hatch meant for a
    # deliberate DEMOTION on a string that is not an account, so the property is
    # asserted against the REAL config instead, where the line already exists
    # and this change does not touch it:
    # tests/test_roster_promotion_evidence.py::test_a_real_money_account_in_dry_run_is_still_risk_bearing
    # pins `ib_live` (account_class: real_money, mode: dry_run) as risk-bearing.
    account_class: real_money
    strategies: []
  mystery_acct:
    account_class: quantum_money
    mode: live
    strategies: []
  noclass_acct:
    mode: live
    strategies: []
"""


def _acc(**edits: List[str]) -> str:
    """Rebuild the fixture with per-account roster / class overrides."""
    data = yaml.safe_load(_ACC)
    for acct, spec in edits.items():
        if spec is None:
            data["accounts"].pop(acct, None)
        elif isinstance(spec, dict):
            data["accounts"][acct].update(spec)
        else:
            data["accounts"][acct]["strategies"] = spec
    return yaml.safe_dump(data)


_GOOD_RECORD = {
    "strategy": "leg_ok", "coverage_state": "measured", "harness": "pullback",
    "n_trades_oos": 8,  # deliberately LOW: the bar has no floor, by decision.
    "cost_stack": {"fees": 7.5, "slippage": 2.0, "funding": 0.0},
    "decision_rule": {"id": "DR-TEST-1", "rule": "net_r_oos > 0 over >=4 folds",
                      "registered_at": "2026-09-01T00:00:00+00:00", "verdict": "pass"},
    "generated_at": "2026-09-10T00:00:00+00:00",
}
_STRATS = {"leg_ok": {"a": 1}, "leg_bad": {"b": 2}, "leg_new": {"c": 3}}


def _write_record(tmp: Path, name: str, rec: Dict[str, Any]) -> None:
    (tmp / f"{name}.json").write_text(json.dumps(rec), encoding="utf-8")


def self_test() -> int:
    import tempfile

    failures: List[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
        if not ok:
            failures.append(label)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        good = dict(_GOOD_RECORD)
        good["config_fingerprint"] = config_fingerprint(_STRATS["leg_ok"])
        _write_record(tmp, "leg_ok", good)
        bad = dict(good)
        bad.update({"strategy": "leg_bad", "coverage_state": "no_harness", "harness": None,
                    "n_trades_oos": None, "cost_stack": None, "decision_rule": None,
                    "config_fingerprint": config_fingerprint(_STRATS["leg_bad"])})
        _write_record(tmp, "leg_bad", bad)

        base = _acc()

        print("POSITIVE CONTROLS — each must produce a finding:")
        # P1: a leg appended to a real-money roster with no record at all.
        p = promotions(base, _acc(real_acct=["leg_ok", "leg_new"]))
        check("P1 roster_add to real_money is detected", [(x["kind"], x["leg"]) for x in p],
              [("roster_add", "leg_new")])
        check("P1 no record -> finding", len(grade(p, _STRATS, tmp)), 1)

        # P2: THE SPELLING THE OLD GUARD CANNOT SEE — no mode/execution line changes.
        p2 = promotions(base, _acc(real_acct=["leg_ok", "leg_bad"]))
        g2 = grade(p2, _STRATS, tmp)
        check("P2 roster_add detected with NO mode/execution edit", len(g2), 1)
        check("P2 names every failed clause", all(c in g2[0] for c in ("C1", "C2", "C3", "C4")), True)

        # P3: one line flips a paper account into a real-money one, arming its
        #     whole existing roster. No leg was 'added' anywhere.
        p3 = promotions(base, _acc(paper_acct={"account_class": "real_money"}))
        check("P3 class_flip arms the whole roster", sorted(x["leg"] for x in p3),
              ["leg_bad", "leg_ok"])
        check("P3 class_flip yields a finding for the unevidenced leg", len(grade(p3, _STRATS, tmp)), 1)

        # P4: FAIL CLOSED — a class this guard has never heard of is risk-bearing.
        p4 = promotions(base, _acc(mystery_acct=["leg_new"]))
        check("P4 unrecognised account_class is risk-bearing", len(p4), 1)
        # P4b: an ABSENT account_class is risk-bearing too.
        p4b = promotions(base, _acc(noclass_acct=["leg_new"]))
        check("P4b absent account_class is risk-bearing", len(p4b), 1)

        # P5: each clause fails ON ITS OWN, so a pass is not an accident of one
        #     dominant check. Every variant below is otherwise compliant.
        for label, mutate, want_fail in (
            ("C1 unnamed harness", {"harness": ""}, "C1"),
            ("C1 coverage_state=harness_failed", {"coverage_state": "harness_failed"}, "C1"),
            ("C2 n absent", {"n_trades_oos": None}, "C2"),
            ("C2 n=0 is vacuous", {"n_trades_oos": 0}, "C2"),
            ("C3 fee-only (no cost_stack)", {"cost_stack": None, "fee_bps_roundtrip": 7.5}, "C3"),
            ("C3 slippage unresolved", {"cost_stack": {"fees": 7.5, "slippage": None, "funding": 0.0}}, "C3"),
            ("C4 no decision_rule", {"decision_rule": None}, "C4"),
            ("C4 rule registered AFTER the run",
             {"decision_rule": {**_GOOD_RECORD["decision_rule"],
                                "registered_at": "2026-09-20T00:00:00+00:00"}}, "C4"),
            ("C4 rule not cleared",
             {"decision_rule": {**_GOOD_RECORD["decision_rule"], "verdict": "fail"}}, "C4"),
        ):
            rec = {**good, **mutate}
            bad_ones = [c for c, ok, _ in clause_verdicts(rec) if not ok]
            check(f"P5 {label} -> exactly [{want_fail}]", bad_ones, [want_fail])

        # P6: identity — a record about a DIFFERENT configuration of the leg.
        stale = {**good, "config_fingerprint": "sha256:" + "0" * 32}
        check("P6 stale config_fingerprint fails identity", identity_verdict(stale, "leg_ok", _STRATS)[0], False)
        check("P6 ...while all four clauses still PASS (so identity is load-bearing)",
              [ok for _, ok, _ in clause_verdicts(stale)], [True, True, True, True])

        # P7: the pairs sleeve — one `account_id:` line.
        pairs_base = yaml.safe_dump({"account_id": "paper_acct",
                                     "pairs": [{"name": "p1", "execution": "live"}]})
        pairs_head = yaml.safe_dump({"account_id": "real_acct",
                                     "pairs": [{"name": "p1", "execution": "live"}]})
        p7 = promotions(base, base, pairs_base, pairs_head)
        check("P7 pairs account_id repointed at real money is detected",
              [(x["kind"], x["leg"]) for x in p7], [("pairs_repoint", "p1")])
        # P7b: a pair with NO `execution` key is live by default.
        p7b = promotions(base, base, pairs_base,
                         yaml.safe_dump({"account_id": "real_acct", "pairs": [{"name": "p2"}]}))
        check("P7b pair with absent execution treated as live", len(p7b), 1)

        print("NEGATIVE CONTROLS — each must stay silent:")
        # N1: DEMOTION IS FREE. This is the one the guard must never break.
        n1 = promotions(base, _acc(real_acct=[]))
        check("N1 removing a leg from a real-money roster is NOT a finding", n1, [])
        # N2: moving an account OUT of a risk-bearing class is free.
        n2 = promotions(base, _acc(real_acct={"account_class": "paper"}))
        check("N2 real_money -> paper is NOT a finding", n2, [])
        # N3: adding a leg to a PAPER roster is free — Stage 1 must stay wide.
        n3 = promotions(base, _acc(paper_acct=["leg_ok", "leg_bad", "leg_new"]))
        check("N3 adding a leg to a paper roster is NOT a finding", n3, [])
        # N4: an unchanged config.
        check("N4 identical base/head is clean", promotions(base, base), [])
        # N5: THE BAR IS SATISFIABLE. A guard nothing can pass is a ban, not a
        #     gate, and would be walked around within a week.
        n5 = promotions(base, _acc(real_acct=["leg_ok", "leg_ok2"]))
        _write_record(tmp, "leg_ok2", {**good, "strategy": "leg_ok2",
                                       "config_fingerprint": config_fingerprint({"d": 4})})
        check("N5 a fully-compliant record PASSES the bar",
              grade(n5, {**_STRATS, "leg_ok2": {"d": 4}}, tmp), [])
        # N6: a pair repoint onto a PAPER account is free.
        n6 = promotions(base, base, pairs_base,
                        yaml.safe_dump({"account_id": "paper_acct",
                                        "pairs": [{"name": "p1", "execution": "live"}]}))
        check("N6 pairs repoint onto paper is NOT a finding", n6, [])
        # N7: a pairs sleeve that is entirely shadow is free even on real money.
        n7 = promotions(base, base, pairs_base,
                        yaml.safe_dump({"account_id": "real_acct",
                                        "pairs": [{"name": "p1", "execution": "shadow"}]}))
        check("N7 all-shadow pairs on a real-money account is NOT a finding", n7, [])

        print("INSTRUMENT CONTROLS:")
        check("fingerprint is deterministic", config_fingerprint({"a": 1}), config_fingerprint({"a": 1}))
        check("fingerprint separates configs",
              config_fingerprint({"a": 1}) != config_fingerprint({"a": 2}), True)
        classes = canonical_account_classes(
            (REPO / ACCOUNT_MODULE_REL).read_text(encoding="utf-8")
            if (REPO / ACCOUNT_MODULE_REL).is_file() else None)
        if classes is None:
            print(f"  NOTE  could not read _VALID_ACCOUNT_CLASSES from {ACCOUNT_MODULE_REL} "
                  f"— classification is unaffected (unknown ⇒ risk-bearing)")
        else:
            print(f"  NOTE  canonical account_class vocabulary = {sorted(classes)}; "
                  f"this guard vouches for {sorted(NON_RISK_CLASSES)} only")
            check("every canonical class except `paper` is risk-bearing",
                  sorted(c for c in classes if is_risk_bearing(c)),
                  sorted(c for c in classes if c != "paper"))

    print(f"\nself-test: {'PASS' if not failures else 'FAIL — ' + ', '.join(failures)}")
    return 1 if failures else 0


# --------------------------------------------------------------------------
def _read_pair(base_ref: str, rel: str) -> Tuple[Optional[str], Optional[str]]:
    """`(base_text, head_text)`; raises CouldNotMeasure on an unreadable base."""
    state, text = read_at(base_ref, rel, repo=REPO)
    if state == UNREADABLE:
        raise CouldNotMeasure(f"base ref {base_ref!r} is unreadable — nothing was checked")
    base_text = text if state == READ else None
    path = REPO / rel
    head_text = path.read_text(encoding="utf-8") if path.is_file() else None
    if state == ABSENT_AT_BASE and head_text is None:
        return None, None
    return base_text, head_text


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main", help="base ref (fork point is resolved)")
    ap.add_argument("--self-test", action="store_true", help="planted violations + controls")
    ap.add_argument("--population", action="store_true",
                    help="census the STANDING risk-bearing roster against the bar; always exits 0")
    ap.add_argument("--json", action="store_true", help="machine-readable census (with --population)")
    args = ap.parse_args(argv)

    # The verdict below is about the COMMITTED tree. Say so when that is not the
    # tree you edited — this guard takes `--base`, so it grades a commit range,
    # and a session chasing a CI failure types THIS file rather than
    # run_guards.py. See
    # BL-20260917-THE-DIRTY-TREE-NOTICE-LIVES-ONLY-IN-RUN-GUARDS-SO-ALL-18-DIRECTLY-INVOCABLE-DIFF-SCOPED-GUARDS-STILL-GRADE-THE-WRONG-TREE-SILENTLY
    # ⚠️ It is a NOTICE: no stashing, no committing, no exit-code change. A
    # dirty tree is not a guard failure, it is a verdict about a different tree.
    import _dirty_tree  # noqa: PLC0415 — the path shim is at module level
    _dirty_tree.warn()

    if args.self_test:
        return self_test()

    evidence_dir = REPO / EVIDENCE_DIR_REL
    try:
        strategies = _load_yaml(
            (REPO / STRATEGIES_REL).read_text(encoding="utf-8")
            if (REPO / STRATEGIES_REL).is_file() else None, STRATEGIES_REL).get("strategies") or {}
    except CouldNotMeasure as exc:
        print(f"roster-promotion-evidence: COULD NOT MEASURE — {exc}", file=sys.stderr)
        return 2

    if args.population:
        head_accounts = (REPO / ACCOUNTS_REL).read_text(encoding="utf-8") \
            if (REPO / ACCOUNTS_REL).is_file() else None
        try:
            rep = census(head_accounts, strategies, evidence_dir)
        except CouldNotMeasure as exc:
            print(f"roster-promotion-evidence: COULD NOT MEASURE — {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(rep, indent=2, sort_keys=True))
            return 0
        print("roster-promotion-evidence — STANDING POPULATION "
              f"(as of {ACCOUNTS_REL} + {EVIDENCE_DIR_REL} on this tree)")
        print(f"  risk-bearing accounts : {', '.join(rep['risk_bearing_accounts']) or '(none)'}")
        print(f"  paper accounts        : {', '.join(rep['paper_accounts']) or '(none)'}")
        print(f"  roster slots          : {rep['roster_slots']} "
              f"({rep['distinct_legs']} distinct legs)")
        print(f"  clear the four-clause bar : {rep['passing']}/{rep['roster_slots']}")
        print(f"  do NOT clear it           : {rep['failing']}/{rep['roster_slots']}")
        for s in rep["slots"]:
            mark = "PASS" if s["passes_bar"] else "FAIL"
            print(f"    [{mark}] {s['account']}/{s['leg']} ({s['account_class']}, mode={s['mode']})")
            for f in s["failed"]:
                print(f"           {f}")
            if not s["identity_ok"]:
                print(f"           identity: {s['identity']}")
        print("\nThis census NEVER fails a build: the existing roster is grandfathered "
              "and the guard is diff-scoped. See the module docstring.")
        return 0

    base_ref, _state = resolve_base(args.base, repo=REPO)
    try:
        base_acc, head_acc = _read_pair(base_ref, ACCOUNTS_REL)
        base_pairs, head_pairs = _read_pair(base_ref, PAIRS_REL)
        found = promotions(base_acc, head_acc, base_pairs, head_pairs)
    except CouldNotMeasure as exc:
        print(f"roster-promotion-evidence: COULD NOT MEASURE — {exc}", file=sys.stderr)
        return 2

    if not found:
        print("roster-promotion-evidence: clean (this diff puts no leg onto a risk-bearing roster)")
        return 0

    findings = grade(found, strategies, evidence_dir)
    if not findings:
        print(f"roster-promotion-evidence: clean ({len(found)} promotion(s), all carrying a "
              f"committed evidence record that clears the four-clause bar)")
        for item in found:
            print(f"  armed: {item['leg']} -> {item['account']} [{item['kind']}]")
        return 0

    print("🚨 ROSTER PROMOTION GUARD: a leg reaches a RISK-BEARING roster without a "
          "committed evidence record clearing the operator's four-clause bar.", file=sys.stderr)
    print("   The bar (operator, 2026-09-21): a named harness · a stated n · net of the FULL "
          "cost stack · clearing a rule registered BEFORE the run.", file=sys.stderr)
    print("   A claim in a PR body is not a record. Fix the record at "
          f"{EVIDENCE_DIR_REL}/<leg>.json, or drop the leg from the roster "
          "(demotion needs nothing).\n", file=sys.stderr)
    for f in findings:
        print(f"  - {f}", file=sys.stderr)
    for f in findings:
        print(f"{MACHINE_PREFIX}\t{f.splitlines()[0]}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
