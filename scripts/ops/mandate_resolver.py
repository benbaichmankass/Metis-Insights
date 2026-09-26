#!/usr/bin/env python3
"""The mandate resolver (plan item B5): may this ladder transition fire WITHOUT
asking the operator, and if not, which clause refuses it?

    python3 scripts/ops/mandate_resolver.py --leg ada_pullback_2h \\
        --from S1 --to S2 --account bybit_2 [--json]

Given ``(leg, from_stage, to_stage, account)`` it returns ``FIRE`` or ``REFUSE``
plus the ONE deciding clause, the evidence it read, and -- on FIRE -- the roster
edit it PROPOSES. It never writes a roster, a config or a PR. Whatever acts on a
FIRE opens a PR carrying the proposal; nothing does yet (see "NOT LIVE" below).

WHAT IT READS -- AND NOTHING ELSE
---------------------------------
  * ``config/mandates.yaml``            the granted list (``mandates:`` ONLY;
                                        ``proposed:`` is never an authorization)
  * ``config/accounts.yaml``            the rosters and each account's risk_pct
  * ``config/strategies.yaml``          only to bind a record to the leg's
                                        current config (B1's identity check)
  * ``comms/strategy_evidence/<leg>.json``  the committed Stage-0 record
  * ``comms/research/r3_cost_fidelity/<date>.json``  the Stage-1 cost-fidelity
                                        verdict, PER LEG (RULE-R3-GATE1-COST-
                                        FIDELITY-v1)
  * ``comms/mandate_evidence/mirror_window/<leg>.json``  Stage-2 mirror window
  * ``comms/mandate_firings/*.json``    what earlier firings added (the cap)

It does not read the PR body, a commit message, a checklist note, the VM or the
database. "A claim in a PR body is not a record" is the whole safety property
once nobody is in the path, so the resolver is structurally unable to see one.

THE BAR AND THE CAP -- OPERATOR DECISIONS, READ FROM THE MANDATE ENTRY
----------------------------------------------------------------------
Decided 2026-09-21, recorded on checklist row B5:
  BAR  expectancy > 0 net of the FULL cost stack at n >= 30 closed; positive in
       a MAJORITY of walk-forward folds; Stage-1 realized cost within a stated
       tolerance of modelled.
  CAP  total declared risk across AUTO-PROMOTED legs <= 25% of the account's
       risk budget. No rate limit.
The numbers live in the mandate entry (``bar:`` / ``cap:``), not here, so the
config is their one source. An entry that does not state them REFUSES -- a
mandate with no bar is not a mandate.

⚠️ n >= 30 is the MANDATE's floor, not B1's. B1
(``scripts/ci/check_roster_promotion_evidence.py``) deliberately has no minimum n
-- the operator declined one for the CI guard, where a human still merges. The
mandate removes the human, and its bar carries the floor. Both are decided; they
are not in conflict. This resolver REUSES B1's four clauses and identity check
verbatim (imported, not re-implemented) and adds the mandate's clauses on top.

⚠️ HOW "THE ACCOUNT'S RISK BUDGET" IS MEASURED -- AN INTERPRETATION, STATED.
No per-leg risk allocation exists in config: sizing is account-owned
(``accounts.yaml::<acct>.risk.risk_pct``; the per-strategy ``risk_pct``
multiplier was removed 2026-06-29, ``src/runtime/pipeline.py``). So every leg on
an account declares the same per-trade risk, and the budget is the sum of that
declared risk across the account's roster AFTER the edit. The cap is therefore
``auto_promoted_after * risk_pct <= max_auto_share * roster_after * risk_pct``.
Hand-placed legs count in the budget but never against the cap, per the
operator ("a leg the operator placed by hand does not consume the mandate's
budget"). The basis is named in the mandate entry (``cap.basis``); an unknown
basis REFUSES.

THE COST CLAUSE READS A PER-LEG VERDICT, NOT A VENUE POINT ESTIMATE
------------------------------------------------------------------
Until 2026-09-25 both cost clauses compared the leg's modelled
``cost_stack.slippage`` against D3's single ``venue_roundtrip_bps_used[venue]``
number. That is a POINT ESTIMATE over a venue, and it collapsed three states the
decision depends on: a venue figure exists and is favourable / it exists and is
adverse / **there is not enough data for that leg to say either way**. A leg with
three measured exits inherited the whole venue's verdict, in both directions.

R3 (``RULE-R3-GATE1-COST-FIDELITY-v1``,
``docs/research/r3-gate1-cost-fidelity-rule-2026-09-25.md``) grades a
``(leg, account)`` CELL, with a 95% bootstrap CI and an n floor of 10 per side,
and rolls the cells up to one per-leg verdict (the MARKET cell wins; the rule
owns that choice and this module does not re-implement it). The five verdicts
and what this resolver does with each:

  ``consistent``     CI upper <= modelled + tolerance. **The Gate-1 pass.**
  ``divergent``      CI lower  > modelled + tolerance. REFUSES a promotion, and
                     it is the ONLY thing that FIRES ``MD-DEMOTE-S1-OFF``.
  ``inconclusive``   the CI straddles the threshold -- the measurement cannot
                     decide. **Not a pass.**
  ``insufficient_n`` below the floor on a side -- we did not measure enough.
                     **Not a pass**, and emphatically not a demotion signal:
                     the mandate's own `never:` says "could not measure" must
                     not read as "diverged".
  ``no_record``      no Stage-0 record to compare against. Not a pass.

⚠️ ``inconclusive`` AND ``insufficient_n`` ARE THE POINT OF THE CHANGE. They are
the two states D3's point estimate could not express, and they are the two that
must not move real money. Of the 55 legs in the first committed record
(``comms/research/r3_cost_fidelity/2026-09-25.json``, pulled 2026-09-25T13:23Z),
**51 read ``insufficient_n``** -- so under the old clause those 51 were being
graded on a venue number none of them had contributed enough fills to.

⚠️ **A STALE VERDICT IS NOT A VERDICT.** The R3 record pins the
``modelled_slippage_bps`` it graded against. If the leg's committed record has
since been regenerated at a different slippage, the verdict answered a question
about a record that no longer exists, and this resolver REFUSES rather than
reusing it. (That is not hypothetical: the rule says a ``divergent`` verdict
INVALIDATES the Stage-0 record and requires regeneration, so the regeneration
that follows a demotion is exactly what makes the old verdict stale.)

EQUITIES AND FUTURES REST ON A PLACEHOLDER
------------------------------------------
``MD-PROMOTE-S1-S2`` is armed for ALL venues (operator, 2026-09-24). On
``alpaca_equities`` and ``ibkr_futures`` there is still no measured realized cost
(R3, 2026-09-25: **0 measured exits on any Alpaca account**; ``ib_paper`` 22
entries / 7 exits, below the floor), so the cost-fidelity clause is taken on the
PROVISIONAL 5.0 bps basis the operator accepted -- *"the 5.0 assumption is a
working artifact until we get evidence backed numbers"*. Every evidence output
for an equity or futures leg carries that caveat, and the record must have been
costed at >= the placeholder. Checklist row E62 replaces it.

⚠️ **THE PLACEHOLDER IS A FALLBACK, NOT AN EXEMPTION, and the two orders matter.**
The placeholder floor is checked FIRST and is never weakened by a measurement --
the mandate's `never:` ("Never on an equities or futures leg whose record
modelled LESS than the 5.0 bps provisional slippage") is operator-granted text
and a measurement does not buy a way around it. What a measurement DOES do is
bite: if R3 grades such a leg ``divergent``, the promotion REFUSES, where before
this change the provisional branch returned early and never looked. So on these
venues the clause is now ``placeholder floor AND (no decisive verdict OR
consistent)`` -- strictly stricter than it was, in every case.

NOT LIVE
--------
Nothing calls this module on a schedule, nothing acts on a FIRE, and no ping is
wired. Merged is not deployed is not observed. Until a consumer exists, this is
a function that answers when asked.

# wiring: manual-only - B5 PR A ships the decision function alone; no schedule,
# workflow or consumer calls it yet, by design (merged != deployed != observed).
# The consumer (act on FIRE: open the roster PR + realtime ping) is B5's next step.

Exit codes: 0 FIRE · 1 REFUSE · 2 could not run (bad arguments).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ci"))
import check_roster_promotion_evidence as b1  # noqa: E402  (B1's bar, reused)

import yaml  # noqa: E402

MANDATES_REL = "config/mandates.yaml"
ACCOUNTS_REL = "config/accounts.yaml"
STRATEGIES_REL = "config/strategies.yaml"
EVIDENCE_DIR_REL = "comms/strategy_evidence"
R3_DIR_REL = "comms/research/r3_cost_fidelity"
MIRROR_DIR_REL = "comms/mandate_evidence/mirror_window"
FIRINGS_DIR_REL = "comms/mandate_firings"

FIRE, REFUSE = "FIRE", "REFUSE"
STAGES = ("S0", "S1", "S2", "OFF")

#: RULE-R3-GATE1-COST-FIDELITY-v1's five verdicts. Mirrored from
#: `scripts/research/r3_cost_fidelity.py`, which OWNS them; this module only
#: consumes -- `tests/test_mandate_resolver.py` asserts the two agree, so a
#: rename in the producer fails a test here rather than silently matching
#: nothing. A verdict string OUTSIDE this set is an unreadable record, not a
#: sixth state to guess at: `r3_leg_verdict` returns `verdict: None` with a
#: `why` naming what it saw, and both clauses refuse, because "we do not know
#: what this record is saying" is "we could not look".
R3_CONSISTENT, R3_DIVERGENT = "consistent", "divergent"
R3_INCONCLUSIVE, R3_INSUFFICIENT_N, R3_NO_RECORD = (
    "inconclusive", "insufficient_n", "no_record")
R3_VERDICTS = frozenset({R3_CONSISTENT, R3_DIVERGENT, R3_INCONCLUSIVE,
                         R3_INSUFFICIENT_N, R3_NO_RECORD})
R3_RULE_ID = "RULE-R3-GATE1-COST-FIDELITY-v1"
R3_RULE_DOC = "docs/research/r3-gate1-cost-fidelity-rule-2026-09-25.md"

#: The ladder, from CLAUDE.md § "The promotion ladder". S0 is the offline
#: harness and has no roster. ⚠️ `ib_paper` / `ib_live` are NOT named in that
#: diagram; they are placed here because MD-PROMOTE-S1-S2 is armed for "all
#: venues" and the operator's decision names futures. `ib_live` is
#: `mode: dry_run` with an empty roster, so a FIRE there arms nothing until the
#: operator-gated set-account-mode runs. `breakout_1` (prop) is deliberately
#: absent: the prop bar is B6's to register.
STAGE_OF_ACCOUNT = {
    "bybit_1": "S1", "alpaca_paper": "S1", "ib_paper": "S1",
    "bybit_2": "S2", "alpaca_live": "S2", "ib_live": "S2",
}
#: Stage 2 is ONE stage: the mirror carries the identical roster
#: (tests/test_paper_portfolio_accounts.py). A Stage-2 edit proposes both.
MIRROR_OF = {"bybit_2": "bybit_portfolio", "alpaca_live": "alpaca_portfolio"}
#: The Stage-1 soak account per exchange -- where a leg must have soaked.
SOAK_ACCOUNT = {"bybit": "bybit_1", "alpaca": "alpaca_paper", "interactive_brokers": "ib_paper"}
#: The account_class each stage must carry. A mismatch is config drift the
#: resolver will not paper over.
STAGE_CLASS = {"S1": "paper", "S2": "real_money"}
#: Exchange -> the venue key D3 and R3 publish (both use realized_slippage.VENUE_OF).
VENUE_OF_EXCHANGE = {"bybit": "bybit_perps", "alpaca": "alpaca_equities",
                     "interactive_brokers": "ibkr_futures"}
#: Venues whose Stage-1 cost is the operator-accepted PLACEHOLDER, not a
#: measurement.
PROVISIONAL_VENUES = frozenset({"alpaca_equities", "ibkr_futures"})
PROVISIONAL_SLIPPAGE_BPS = 5.0
PROVISIONAL_CAVEAT = (
    "PROVISIONAL COST BASIS: equities/futures slippage is a 5.0 bps PLACEHOLDER, "
    "not a measurement (operator 2026-09-24: \"the 5.0 assumption is a working "
    "artifact until we get evidence backed numbers\"). D3 measured 0 "
    "package-referenced exits on this venue. Checklist row E62 replaces it.")

TRANSITION_MANDATE = {
    ("S0", "S1"): "MD-PROMOTE-S0-S1",
    ("S1", "S2"): "MD-PROMOTE-S1-S2",
    ("S2", "S1"): "MD-DEMOTE-S2-S1",
    ("S1", "OFF"): "MD-DEMOTE-S1-OFF",
}
#: The one cap basis this resolver knows how to measure (see docstring).
CAP_BASIS = "uniform_account_risk_pct_share_of_roster"


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def _yaml(root: Path, rel: str) -> Optional[Dict[str, Any]]:
    p = root / rel
    if not p.is_file():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def in_repo(root: Path, rel: Optional[str]) -> bool:
    """Is `rel` a COMMITTED file under `root`? In a git work tree, tracked-ness
    is required -- an untracked file on a session's disk is not in the repo. In
    a plain directory (the tests' fixtures) existence is the check."""
    if not rel or not isinstance(rel, str):
        return False
    rel = rel.strip()
    if rel.startswith("/") or ".." in Path(rel).parts or not (root / rel).is_file():
        return False
    if (root / ".git").exists():
        r = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
                           capture_output=True, text=True)
        return r.returncode == 0
    return True


def granted(mandates_doc: Optional[Dict[str, Any]], mandate_id: str) -> Optional[Dict[str, Any]]:
    for m in (mandates_doc or {}).get("mandates") or []:
        if isinstance(m, dict) and m.get("id") == mandate_id:
            return m
    return None


#: A dated record's filename, and NOTHING ELSE in the same directory. The R3
#: and D3 producers write three artifacts per run -- ``<date>.json`` (the
#: record), ``<date>__fills_pull_log.json`` and ``<date>__rows.jsonl`` -- so a
#: bare ``*.json`` glob sorts the PULL LOG last and picks it.
#:
#: ⚠️ THAT IS NOT HYPOTHETICAL: it is what `latest_d3` did, and it is why this
#: pattern is a named constant with a test rather than an inline glob.
#: MEASURED 2026-09-25 by running the merged `latest_d3` against the committed
#: tree: it returned `comms/research/d3_realized_slippage/2026-09-24__fills_pull_log.json`,
#: which is a JSON *list*, so `_json` returned None, so `realized` was always
#: None and BOTH cost clauses refused with "cannot compare" / "could not
#: measure" whatever the measurement actually said. It failed CLOSED -- no
#: promotion was ever wrongly fired on it -- but the clause was inert, and a
#: clause that always refuses for a reason unrelated to its evidence is not a
#: clause. Fixed here by construction rather than left for the same glob to
#: reproduce against the R3 directory, which has the identical three-file shape.
DATED_RECORD = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def latest_r3(root: Path) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """The NEWEST dated R3 cost-fidelity record, by filename (YYYY-MM-DD.json).

    The path chosen is returned and surfaced as `evidence.cost_fidelity.record`,
    and the CLI prints it, so which measurement decided the clause is never
    implicit. Only `DATED_RECORD` names are considered -- see its note."""
    d = root / R3_DIR_REL
    # provenance: latest_r3 — newest dated R3 record; path returned + printed as evidence.cost_fidelity.record
    files = sorted(q for q in d.glob("*.json")
                   if DATED_RECORD.match(q.name)) if d.is_dir() else []
    if not files:
        return None, None
    # provenance: latest_r3 — newest dated R3 record; path returned + printed as evidence.cost_fidelity.record
    rel = str(files[-1].relative_to(root))
    return rel, _json(files[-1])


def r3_leg_verdict(leg: str, root: Path) -> Dict[str, Any]:
    """The leg's R3 verdict, as a block both cost clauses put straight into
    `evidence.cost_fidelity` whether they pass or refuse.

    Always returns a dict. `verdict` is one of `R3_VERDICTS` when a usable
    verdict was read, and otherwise `None` with `why` saying which of the
    several distinct ways of NOT having one applies:

      * no committed record under `comms/research/r3_cost_fidelity/`
      * a record that is present but unreadable, or not committed
      * a record that does not carry this leg at all
      * a verdict string outside the rule's vocabulary

    ⚠️ These are deliberately NOT collapsed into `insufficient_n`. "The record
    does not mention this leg" and "the record measured this leg and found too
    few fills" are different facts, and only the second one is a measurement.
    Both refuse, but a reader is told which.
    """
    rel, doc = latest_r3(root)
    out: Dict[str, Any] = {"rule": R3_RULE_ID, "record": rel, "leg": leg,
                           "verdict": None, "from_account": None, "basis": None,
                           "graded_against_modelled_bps": None, "as_of": None}
    if rel is None:
        out["why"] = f"no committed cost-fidelity record under {R3_DIR_REL}"
        return out
    if not in_repo(root, rel):
        out["why"] = f"{rel} is not a committed file in the repo"
        return out
    if doc is None:
        out["why"] = f"{rel} is unreadable"
        return out
    out["as_of"] = doc.get("as_of")
    per_leg = doc.get("per_leg")
    entry = (per_leg or {}).get(leg) if isinstance(per_leg, dict) else None
    if not isinstance(entry, dict):
        out["why"] = (f"{rel} carries no entry for {leg!r} "
                      f"({len(per_leg) if isinstance(per_leg, dict) else 0} legs graded)")
        return out
    out["graded_against_modelled_bps"] = entry.get("modelled_slippage_bps")
    block = entry.get("leg")
    verdict = (block or {}).get("verdict") if isinstance(block, dict) else None
    if verdict not in R3_VERDICTS:
        out["why"] = f"{rel} grades {leg!r} {verdict!r}, which is not one of {sorted(R3_VERDICTS)}"
        return out
    out.update({"verdict": verdict, "from_account": (block or {}).get("from_account"),
                "basis": (block or {}).get("basis")})
    return out


def auto_promoted(root: Path, account: str, roster: List[str]) -> List[str]:
    """Legs a mandate ADDED to `account` that are still on its roster."""
    d = root / FIRINGS_DIR_REL
    out: List[str] = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        rec = _json(p) or {}
        if rec.get("action") == "add" and account in (rec.get("accounts") or []):
            leg = rec.get("leg")
            if leg in roster and leg not in out:
                out.append(leg)
    return out


# --------------------------------------------------------------------------
# the decision
# --------------------------------------------------------------------------
class _Refuse(Exception):
    def __init__(self, clause: str, detail: str):
        super().__init__(f"{clause}: {detail}")
        self.clause, self.detail = clause, detail


def _result(verdict: str, clause: str, detail: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {"verdict": verdict, "clause": clause, "detail": detail, **ctx}


def resolve(leg: str, from_stage: str, to_stage: str, account: str, *,
            root: Path = REPO, mandate_id: Optional[str] = None) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"leg": leg, "from_stage": from_stage, "to_stage": to_stage,
                           "account": account, "mandate": None, "evidence": {},
                           "caveats": [], "proposal": None}
    try:
        _decide(leg, from_stage, to_stage, account, root, mandate_id, ctx)
    except _Refuse as r:
        ctx["proposal"] = None
        return _result(REFUSE, r.clause, r.detail, ctx)
    return _result(FIRE, "ALL-CLAUSES-PASS", "every clause of the mandate held", ctx)


def _decide(leg: str, frm: str, to: str, account: str, root: Path,
            mandate_id: Optional[str], ctx: Dict[str, Any]) -> None:
    # ── which mandate, and is it granted ────────────────────────────────────
    if frm not in STAGES or to not in STAGES:
        raise _Refuse("R-TRANSITION", f"stages must be among {STAGES}; got {frm!r} -> {to!r}")
    implied = TRANSITION_MANDATE.get((frm, to))
    mid = mandate_id or implied
    if not mid:
        raise _Refuse("R-TRANSITION", f"{frm} -> {to} is not a ladder transition any mandate covers")
    ctx["mandate"] = mid
    if mid == "MD-KILL-QUESTION":
        raise _Refuse("R-TRANSITION", "MD-KILL-QUESTION acts on research/queue, not on a roster; "
                                      "this resolver does not evaluate it")
    mdoc = _yaml(root, MANDATES_REL)
    if mdoc is None:
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{MANDATES_REL} is absent or unreadable -- "
                                               "could not look, so nothing is authorized")
    m = granted(mdoc, mid)
    if m is None:
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{mid} is not in the granted `mandates:` list "
                                               "(`proposed:` never authorizes)")
    if not m.get("granted_by") or not m.get("granted_at"):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{mid} lacks granted_by/granted_at")
    if m.get("blocked_until"):
        raise _Refuse("R-MANDATE-BLOCKED", f"{mid} blocked_until: {m['blocked_until']}")
    direction = str(m.get("direction") or "").strip()
    if direction not in ("add_risk", "derisk_only"):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{mid} direction={direction!r} is not "
                                               "add_risk|derisk_only")

    # ── the rosters, and the edit this transition implies ───────────────────
    accounts_text = (root / ACCOUNTS_REL).read_text(encoding="utf-8") \
        if (root / ACCOUNTS_REL).is_file() else None
    rosters = b1.rosters(accounts_text)
    acfg = (_yaml(root, ACCOUNTS_REL) or {}).get("accounts") or {}
    # `account` is the account being EDITED: the destination for a promotion,
    # the source for a demotion.
    target_stage = to if (frm, to) in (("S0", "S1"), ("S1", "S2")) else frm
    if STAGE_OF_ACCOUNT.get(account) != target_stage or account not in rosters:
        raise _Refuse("R-ACCOUNT", f"{account!r} is not a Stage-{target_stage} account on the ladder "
                                   f"(known: {sorted(a for a, s in STAGE_OF_ACCOUNT.items() if s == target_stage)})")
    want_class = STAGE_CLASS[target_stage]
    if rosters[account]["class"] != want_class:
        raise _Refuse("R-ACCOUNT", f"{account} account_class={rosters[account]['class']!r}, "
                                   f"Stage {target_stage} requires {want_class!r} -- config drift")
    exchange = str((acfg.get(account) or {}).get("exchange") or "")
    venue = VENUE_OF_EXCHANGE.get(exchange)
    ctx["evidence"]["venue"] = venue
    if venue in PROVISIONAL_VENUES:
        ctx["caveats"].append(PROVISIONAL_CAVEAT)

    adds, removes = _delta(leg, frm, to, account, exchange, rosters)
    if direction == "derisk_only" and adds:
        raise _Refuse("R-DERISK-ADD", f"{mid} is derisk_only and this edit ADDS {leg} to {adds}")
    if direction == "add_risk" and removes:
        raise _Refuse("R-TRANSITION", f"{mid} is add_risk; this edit removes from {removes}")
    if not adds and not removes:
        raise _Refuse("R-NO-OP", f"{leg} is already where {frm} -> {to} would put it")

    if direction == "add_risk":
        _add_risk(leg, frm, to, account, exchange, venue, m, rosters, acfg, root, ctx)
    elif (frm, to) == ("S2", "S1"):
        _demote_s2(leg, root, ctx)
    else:
        _demote_s1_off(leg, venue, m, root, ctx)
    ctx["proposal"] = {
        "writes_nothing": True,
        "roster_add": {a: [leg] for a in adds},
        "roster_remove": {a: [leg] for a in removes},
        "file": ACCOUNTS_REL,
        "pr_title": f"{mid}: {leg} {frm} -> {to} ({account})",
        "then": "ping in realtime; show the evidence in §1 of the next daily brief",
    }


def _delta(leg: str, frm: str, to: str, account: str, exchange: str,
           rosters: Dict[str, Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """(accounts the leg would be ADDED to, accounts it would be REMOVED from)."""
    on = lambda a: a in rosters and leg in rosters[a]["legs"]  # noqa: E731
    stage2 = [account] + ([MIRROR_OF[account]] if account in MIRROR_OF else [])
    if (frm, to) == ("S0", "S1"):
        return ([account] if not on(account) else []), []
    if (frm, to) == ("S1", "S2"):
        return [a for a in stage2 if not on(a)], []
    if (frm, to) == ("S2", "S1"):
        soak = SOAK_ACCOUNT.get(exchange)
        adds = [soak] if soak and not on(soak) else []
        return adds, [a for a in stage2 if on(a)]
    if (frm, to) == ("S1", "OFF"):
        return [], ([account] if on(account) else [])
    return [], []


def _add_risk(leg: str, frm: str, to: str, account: str, exchange: str,
              venue: Optional[str], m: Dict[str, Any], rosters: Dict[str, Dict[str, Any]],
              acfg: Dict[str, Any], root: Path, ctx: Dict[str, Any]) -> None:
    bar, cap = m.get("bar"), m.get("cap")
    if not isinstance(bar, dict) or not isinstance(cap, dict):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{m['id']} does not state its `bar:` and `cap:`")

    if (frm, to) == ("S1", "S2"):
        soak = SOAK_ACCOUNT.get(exchange)
        if not soak or leg not in rosters.get(soak, {}).get("legs", []):
            raise _Refuse("R-NOT-SOAKED", f"{leg} is not on the Stage-1 soak roster {soak!r}; "
                                          "Stage 2 is reached only through Stage 1")

    # ── the committed record ────────────────────────────────────────────────
    rec_rel = f"{EVIDENCE_DIR_REL}/{leg}.json"
    record = b1.load_record(leg, root / EVIDENCE_DIR_REL)
    if record is None:
        raise _Refuse("R-RECORD-MISSING", f"no readable committed record at {rec_rel}")
    ev = ctx["evidence"]
    ev.update({"record": rec_rel, "harness": record.get("harness"),
               "n_trades_oos": record.get("n_trades_oos"),
               "expectancy_r_oos": record.get("expectancy_r_oos"),
               "net_r_oos": record.get("net_r_oos"), "folds": record.get("folds"),
               "folds_positive": record.get("folds_positive"),
               "cost_stack": record.get("cost_stack"),
               "decision_rule": (record.get("decision_rule") or {}).get("id"),
               "source_run": record.get("source_run")})
    if not in_repo(root, record.get("source_run")):
        raise _Refuse("R-SOURCE-RUN-ABSENT", f"source_run {record.get('source_run')!r} is not a "
                                             "committed file in the repo")

    # ── B1's four clauses + identity, verbatim ──────────────────────────────
    for clause, ok, detail in b1.clause_verdicts(record):
        if not ok:
            raise _Refuse(f"R-B1-{clause}", detail)
    strategies = (_yaml(root, STRATEGIES_REL) or {}).get("strategies") or {}
    ok, detail = b1.identity_verdict(record, leg, strategies)
    if not ok:
        raise _Refuse("R-B1-IDENTITY", detail)

    # ── the mandate's bar ───────────────────────────────────────────────────
    min_n = bar.get("min_n_closed")
    n = record.get("n_trades_oos")
    if not isinstance(min_n, int) or isinstance(min_n, bool):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"bar.min_n_closed={min_n!r} is not an integer")
    if n < min_n:
        raise _Refuse("R-N", f"n_trades_oos={n} < {min_n} closed")
    exp = record.get("expectancy_r_oos")
    net = record.get("net_r_oos")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp <= 0 \
            or not isinstance(net, (int, float)) or net <= 0:
        raise _Refuse("R-EXPECTANCY", f"expectancy_r_oos={exp!r}, net_r_oos={net!r} -- not > 0 "
                                      "net of the full cost stack")
    # Cross-check with arithmetic, not a re-read: expectancy must be net / n.
    if abs(net / n - exp) > 0.01:
        raise _Refuse("R-EXPECTANCY", f"expectancy_r_oos={exp} != net_r_oos/n = {net / n:.4f} -- "
                                      "the record disagrees with itself")
    folds, pos = record.get("folds"), record.get("folds_positive")
    detail_rows = record.get("fold_detail")
    if not isinstance(folds, int) or not isinstance(pos, int) or folds < 1:
        raise _Refuse("R-FOLDS", f"folds={folds!r}, folds_positive={pos!r} -- not stated")
    if isinstance(detail_rows, list):
        counted = sum(1 for f in detail_rows if isinstance(f, dict)
                      and isinstance(f.get("net_r"), (int, float)) and f["net_r"] > 0)
        if len(detail_rows) != folds or counted != pos:
            raise _Refuse("R-FOLDS", f"fold_detail shows {counted}/{len(detail_rows)} positive but "
                                     f"the record states {pos}/{folds}")
    if not 2 * pos > folds:
        raise _Refuse("R-FOLDS", f"positive in {pos} of {folds} folds -- not a majority")

    # ── Stage-1 cost fidelity (S1 -> S2 only) ───────────────────────────────
    if (frm, to) == ("S1", "S2"):
        _cost_fidelity(leg, venue, record, m, root, ctx)

    # ── the cap ─────────────────────────────────────────────────────────────
    if cap.get("basis") != CAP_BASIS:
        raise _Refuse("R-CAP", f"cap.basis={cap.get('basis')!r} -- this resolver measures only "
                               f"{CAP_BASIS!r}")
    share = cap.get("max_auto_share")
    if not isinstance(share, (int, float)) or isinstance(share, bool) or not 0 < share <= 1:
        raise _Refuse("R-CAP", f"cap.max_auto_share={share!r} is not a fraction")
    risk_pct = ((acfg.get(account) or {}).get("risk") or {}).get("risk_pct")
    if not isinstance(risk_pct, (int, float)) or risk_pct <= 0:
        raise _Refuse("R-CAP", f"{account}.risk.risk_pct={risk_pct!r} -- cannot measure declared risk")
    roster = rosters[account]["legs"]
    auto = auto_promoted(root, account, roster)
    roster_after, auto_after = len(roster) + 1, len(auto) + 1
    used, budget = auto_after * risk_pct, roster_after * risk_pct
    ctx["evidence"]["cap"] = {
        "basis": CAP_BASIS, "risk_pct": risk_pct, "roster_after": roster_after,
        "auto_promoted_after": auto_after, "auto_promoted_now": auto,
        "auto_declared_risk": round(used, 6), "budget": round(budget, 6),
        "limit": round(share * budget, 6),
        "ledger": FIRINGS_DIR_REL if (root / FIRINGS_DIR_REL).is_dir()
        else f"{FIRINGS_DIR_REL} absent -- no mandate has ever fired, so 0 auto-promoted legs"}
    if used > share * budget + 1e-12:
        raise _Refuse("R-CAP", f"auto-promoted declared risk {used:.4f} > {share:.0%} of "
                               f"{account}'s budget {budget:.4f} ({auto_after}/{roster_after} legs)")


def _stale_against_record(r3: Dict[str, Any], modelled: Any) -> Optional[str]:
    """Did R3 grade this leg against the slippage its record carries TODAY?

    Returns a sentence when the two disagree, `None` when they agree. A verdict
    computed against a different assumption answers a different question, so
    both clauses treat a disagreement as "could not look" rather than reusing
    the number. Cross-checked with arithmetic, not a re-read.
    """
    graded = r3.get("graded_against_modelled_bps")
    if not isinstance(modelled, (int, float)) or isinstance(modelled, bool):
        return f"the record's cost_stack.slippage is {modelled!r}, not a number"
    if not isinstance(graded, (int, float)) or isinstance(graded, bool):
        return (f"{r3.get('record')} does not say what modelled slippage it graded "
                f"against (got {graded!r})")
    if abs(float(graded) - float(modelled)) > 1e-9:
        return (f"{r3.get('record')} graded {r3.get('leg')!r} against modelled "
                f"{graded} bps, but the committed record now carries {modelled} bps -- "
                "the verdict answers a question about a record that has since changed")
    return None


def _cost_fidelity(leg: str, venue: Optional[str], record: Dict[str, Any],
                   m: Dict[str, Any], root: Path, ctx: Dict[str, Any]) -> None:
    """Clause 3 of the bar: is the realized cost the one the record assumed?

    Reads the R3 PER-LEG verdict (see the module docstring). `consistent` is the
    only pass. `divergent` refuses, and so do the two states a venue point
    estimate could not express -- `inconclusive` and `insufficient_n` -- because
    "we cannot yet tell" is not evidence, and this mandate routes real money.
    """
    tol = (m.get("bar") or {}).get("cost_tolerance_bps")
    if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol < 0:
        raise _Refuse("R-COST-FIDELITY", f"bar.cost_tolerance_bps={tol!r} is not stated")
    modelled = (record.get("cost_stack") or {}).get("slippage")
    if venue is None:
        raise _Refuse("R-COST-FIDELITY", "the account's exchange maps to no known venue")

    provisional = venue in PROVISIONAL_VENUES
    # The placeholder floor is the mandate's own `never:` and is checked FIRST.
    # A measurement never buys a way around it -- see the docstring.
    if provisional and (not isinstance(modelled, (int, float)) or isinstance(modelled, bool)
                        or modelled < PROVISIONAL_SLIPPAGE_BPS):
        ctx["evidence"]["cost_fidelity"] = {"venue": venue, "basis": "PROVISIONAL",
                                            "modelled_bps": modelled, "tolerance_bps": tol}
        raise _Refuse("R-COST-FIDELITY", f"venue {venue} rests on the {PROVISIONAL_SLIPPAGE_BPS} "
                                         f"bps placeholder; the record modelled {modelled!r}")

    r3 = r3_leg_verdict(leg, root)
    cf = dict(r3, venue=venue, modelled_bps=modelled, tolerance_bps=tol,
              basis_of_comparison="PROVISIONAL" if provisional else "MEASURED",
              rule_doc=R3_RULE_DOC)
    ctx["evidence"]["cost_fidelity"] = cf

    verdict = r3.get("verdict")
    note = verdict or r3.get("why")
    if verdict is not None:
        stale = _stale_against_record(r3, modelled)
        if stale:
            # A STALE VERDICT IS NOT A VERDICT -- it is folded back into the
            # same "no decisive measurement" state as a record that never
            # graded this leg, so the two leave by the same exit and neither
            # can slip through silently.
            cf["stale"] = stale
            verdict, note = None, stale
        elif verdict == R3_CONSISTENT:
            return  # the Gate-1 pass, on any venue
        elif verdict == R3_DIVERGENT:
            raise _Refuse("R-COST-FIDELITY",
                          f"{R3_RULE_ID} grades {leg} `divergent` on "
                          f"{r3.get('from_account')} ({r3.get('basis')}): realized cost exceeds "
                          f"the modelled {modelled} bps + tolerance {tol} with 95% confidence "
                          f"({r3.get('record')})")
    if provisional:
        # No decisive measurement on this venue: the operator-accepted 5.0 bps
        # placeholder is the stated basis, and its floor held above. The caveat
        # is NOT optional -- a reader told "provisional" must also be told that
        # no verdict backs it, or the two collapse into one reassuring word.
        ctx["caveats"].append(
            f"NO DECISIVE COST-FIDELITY VERDICT: {R3_RULE_ID} reads "
            f"{note} for {leg}, so this promotion rests on the "
            f"{PROVISIONAL_SLIPPAGE_BPS} bps placeholder, not on a measurement.")
        return
    raise _Refuse("R-COST-FIDELITY",
                  f"{R3_RULE_ID} does not pass {leg}: {note} -- only `consistent` "
                  f"is the Gate-1 pass ({r3.get('record') or 'no record'})")


def _demote_s2(leg: str, root: Path, ctx: Dict[str, Any]) -> None:
    """MD-DEMOTE-S2-S1: the mirror's net-of-cost window is the demotion signal."""
    rel = f"{MIRROR_DIR_REL}/{leg}.json"
    rec = _json(root / rel)
    if rec is None:
        raise _Refuse("R-RECORD-MISSING", f"no committed mirror-window record at {rel} -- "
                                          "the demotion signal cannot be read")
    net = rec.get("net_r_net_of_full_cost")
    ctx["evidence"].update({"record": rel, "n_closed": rec.get("n_closed"),
                            "net_r_net_of_full_cost": net, "source_run": rec.get("source_run")})
    if not in_repo(root, rec.get("source_run")):
        raise _Refuse("R-SOURCE-RUN-ABSENT", f"source_run {rec.get('source_run')!r} is not in the repo")
    if not isinstance(net, (int, float)) or isinstance(net, bool):
        raise _Refuse("R-EXPECTANCY", f"net_r_net_of_full_cost={net!r} is not stated")
    if net >= 0:
        raise _Refuse("R-EXPECTANCY", f"mirror window net {net} R is not negative -- the evidence "
                                      "does not support a demotion")


def _demote_s1_off(leg: str, venue: Optional[str], m: Dict[str, Any], root: Path,
                   ctx: Dict[str, Any]) -> None:
    """MD-DEMOTE-S1-OFF: the LEG's realized cost diverges from what it modelled.

    `divergent` is the only firing verdict, and every other outcome REFUSES.
    That asymmetry is the mandate's own `never:` clause -- *"an unmeasurable
    venue shows no divergence, and 'could not measure' must not read as
    'diverged'"* -- which under a per-leg rule is exactly `insufficient_n` and
    `inconclusive`. Reading either as a demotion would take a leg off its soak
    book on the strength of a measurement that said it could not decide.
    """
    rec_rel = f"{EVIDENCE_DIR_REL}/{leg}.json"
    record = b1.load_record(leg, root / EVIDENCE_DIR_REL)
    if record is None:
        raise _Refuse("R-RECORD-MISSING", f"no readable committed record at {rec_rel}")
    ctx["evidence"]["record"] = rec_rel
    tol = (m.get("bar") or {}).get("cost_tolerance_bps")
    if not isinstance(tol, (int, float)) or isinstance(tol, bool):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{m['id']} does not state bar.cost_tolerance_bps")
    modelled = (record.get("cost_stack") or {}).get("slippage")
    r3 = r3_leg_verdict(leg, root)
    cf = dict(r3, venue=venue, modelled_bps=modelled, tolerance_bps=tol,
              rule_doc=R3_RULE_DOC)
    ctx["evidence"]["cost_fidelity"] = cf
    verdict = r3.get("verdict")
    if verdict is None:
        raise _Refuse("R-RECORD-MISSING", f"{r3.get('why')} -- no per-leg cost-fidelity verdict "
                                          "to demote on, and 'could not measure' is not 'diverged'")
    stale = _stale_against_record(r3, modelled)
    if stale:
        cf["stale"] = stale
        raise _Refuse("R-COST-FIDELITY", stale)
    if verdict != R3_DIVERGENT:
        raise _Refuse("R-COST-FIDELITY",
                      f"{R3_RULE_ID} grades {leg} `{verdict}`, not `divergent` "
                      f"({r3.get('record')}) -- the evidence does not support a demotion")


# --------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leg", required=True)
    ap.add_argument("--from", dest="frm", required=True, choices=STAGES)
    ap.add_argument("--to", required=True, choices=STAGES)
    ap.add_argument("--account", required=True)
    ap.add_argument("--mandate", default=None, help="evaluate under this mandate id instead of "
                                                     "the one the transition implies")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = resolve(a.leg, a.frm, a.to, a.account, root=Path(a.root), mandate_id=a.mandate)
    if a.json:
        print(json.dumps(res, indent=2, sort_keys=True, default=str))
    else:
        print(f"{res['verdict']} [{res['clause']}] {res['mandate']}: {res['detail']}")
        for key in ("record", "source_run"):
            if res["evidence"].get(key):
                print(f"  {key}: {res['evidence'][key]}")
        cf = res["evidence"].get("cost_fidelity") or {}
        if cf.get("record") or cf.get("why"):
            print(f"  cost fidelity: {cf.get('verdict') or cf.get('why')}"
                  + (f" (from {cf['from_account']}, {cf['basis']})" if cf.get("from_account") else "")
                  + (f" -- {cf['record']}" if cf.get("record") else ""))
        for c in res["caveats"]:
            print(f"  ⚠️ {c}")
    return 0 if res["verdict"] == FIRE else 1


if __name__ == "__main__":
    sys.exit(main())
