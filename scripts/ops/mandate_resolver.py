#!/usr/bin/env python3
"""The mandate resolver (plan item B5): may this ladder transition fire WITHOUT
asking the operator, and if not, why -- does the data settle it AGAINST, or is
the data simply not there yet?

    python3 scripts/ops/mandate_resolver.py --leg ada_pullback_2h \\
        --from S1 --to S2 --account bybit_2 [--json] [--file-needs-data SESSION_REF]

Given ``(leg, from_stage, to_stage, account)`` it returns one of THREE verdicts
plus the ONE deciding clause, the evidence it read, and -- on FIRE -- the roster
edit it PROPOSES. It never writes a roster, a config or a PR. Whatever acts on a
FIRE opens a PR carrying the proposal; nothing does yet (see "NOT LIVE" below).

THREE OUTCOMES, NOT TWO (operator directive, 2026-09-29, on PR #13698)
-----------------------------------------------------------------------
Until this change the resolver only knew FIRE and REFUSE, and that binary
collapsed two facts that must not share one value: *"the evidence says no"* and
*"there is no decisive evidence yet."* #13698 asked whether to promote
``slv_trend_1h`` to ``alpaca_live`` and the resolver, run for real against the
committed tree, answered **FIRE** -- on a leg that is ``execution: shadow``
(zero real exits ever) and whose Stage-1 cost-fidelity verdict is
``insufficient_n``. Neither fact is "the evidence is bad"; both are "there is no
evidence yet". The operator's ruling is the fix:

    "either we have enough data to decide, or we don't and then getting that
    data becomes a task which needs to happen so that a decision can be made"

So there are three verdicts, never two:

  ``FIRE``        every clause read a DECISIVE measurement, and all of them
                  passed. Act -- open the PR the proposal names.
  ``REFUSE``      a clause read a DECISIVE measurement and it FAILED (a
                  negative expectancy, a divergent cost verdict, a mandate
                  that is not granted, config drift, a positive mirror
                  window on a demotion). Never fires; re-running it against
                  the same evidence answers the same way.
  ``NEEDS_DATA``  (string value ``"NEEDS-DATA"``) a clause could not find a
                  decisive measurement at all -- the record is missing, the
                  sample is below the stated floor, or R3 grades the leg
                  ``inconclusive`` / ``insufficient_n`` / ``no_record`` / a
                  stale verdict. This is the state a shadow-executed or
                  never-soaked leg is actually in, and it is the state
                  ``slv_trend_1h`` was in. Never fires; carries a
                  ``data_task`` describing exactly what would clear it, and
                  the CLI (``--file-needs-data``) or ``file_needs_data()``
                  auto-files that task into ``scripts/ops/pipeline.py`` --
                  the mechanism that pulls it back to a decision instead of
                  letting it sit as a silent, indefinitely-repeatable FIRE
                  risk. **A caller must never treat NEEDS_DATA as a pass**:
                  both downstream consumers (``r4_demotion_gate.py``,
                  ``check_mandate_autoland.py``) already gate on
                  ``verdict != FIRE``, so this is safe by construction, but a
                  NEW consumer must not "helpfully" treat NEEDS_DATA as FIRE
                  because a caveat-only reading of "provisional" is exactly
                  the #13698 defect.

**Where the line is drawn, clause by clause**, stated so it can be audited
rather than inferred: a leg not on its Stage-1 soak roster, or on it at
``execution: shadow`` (``R-NOT-SOAKED`` / ``R-EXECUTION-SHADOW``); a Stage-0
evidence record that is simply absent (``R-RECORD-MISSING``); a closed-trade
count below the mandate's own floor (``R-N``); and every R3 cost-fidelity
verdict except ``consistent`` (pass) and ``divergent`` (REFUSE) --
``inconclusive`` / ``insufficient_n`` / ``no_record`` / a stale verdict / no
committed R3 record at all -- are all ``NEEDS_DATA``. ``R-AFFORD`` (S1->S2,
added 2026-09-30, PI-20260928-E8Y3BGBS-0003) splits the same way: an equity
reading that is absent or older than ``ACCOUNT_SNAPSHOT_MAX_AGE_DAYS`` is
``NEEDS_DATA``; a measured account on which NONE of the leg's permitted setups
sizes above zero through ``RiskManager.position_size`` is ``REFUSE``, and it is
decided BEFORE cost fidelity so an unaffordable leg never files a data task. Everything else that
was a REFUSE before this change (mandate not granted or blocked, wrong
account or account-class drift, an unknown transition, a config-fingerprint
mismatch, B1's own four clauses, a computed expectancy or fold count that is
negative or not a majority, the cap exceeded, a source_run not committed)
stays REFUSE: each of those is a DECISIVE fact, not an absence of one, and
"give it more time" would not change the answer.

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
                                        (for S2->S1: its two T3 windows; the
                                        threshold is RE-DERIVED from the leg's
                                        Stage-0 record above -- ``stage0_block_p10``)
  * ``comms/mandate_firings/*.json``    what earlier firings added (the cap)
  * ``comms/mandate_evidence/account_snapshot/<account>.json``  the Stage-2
                                        account's MEASURED equity + buying power
                                        (S1->S2 affordability, ``R-AFFORD``),
                                        written from the diag read by
                                        ``scripts/ops/snapshot_account_for_mandate.py``
  * the record's committed ``source_run``  its setups (entry/sl/direction),
                                        re-sized through the live sizer

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
# NEEDS_DATA is filed on request (--file-needs-data / file_needs_data()), not on
# a schedule either -- see that function's docstring.

Exit codes: 0 FIRE · 1 REFUSE · 2 bad arguments (argparse) · 3 NEEDS_DATA.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ci"))
import check_roster_promotion_evidence as b1  # noqa: E402  (B1's bar, reused)
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import pipeline  # noqa: E402  (the follow-through store; see file_needs_data())

import yaml  # noqa: E402

MANDATES_REL = "config/mandates.yaml"
ACCOUNTS_REL = "config/accounts.yaml"
STRATEGIES_REL = "config/strategies.yaml"
EVIDENCE_DIR_REL = "comms/strategy_evidence"
R3_DIR_REL = "comms/research/r3_cost_fidelity"
MIRROR_DIR_REL = "comms/mandate_evidence/mirror_window"
FIRINGS_DIR_REL = "comms/mandate_firings"

FIRE, REFUSE, NEEDS_DATA = "FIRE", "REFUSE", "NEEDS-DATA"
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

#: R-AFFORD (S1->S2): can the Stage-2 account size the leg AT ALL?
#: PI-20260928-E8Y3BGBS-0003 -- on 2026-09-28 the armed MD-PROMOTE-S1-S2 FIREd
#: gld_pullback_1h onto alpaca_live, a ~$194 whole-share CASH book, while one
#: GLD share was ~$393: every signal would size to 0, so the roster edit could
#: land and never trade. The measurement is the LIVE SIZER itself
#: (RiskManager.position_size, the function the SPLG/IAUM affordability proxies
#: were screened through), run over the leg's own committed setups at the
#: account's measured equity -- not a second hand-written copy of "can it buy
#: one share", which misses the round-up-to-one-share path (accounts.yaml
#: alpaca_live risk comment: "the sizer is authoritative").
ACCOUNT_SNAPSHOT_DIR_REL = "comms/mandate_evidence/account_snapshot"
#: An equity reading older than this is not a measurement of the account today.
ACCOUNT_SNAPSHOT_MAX_AGE_DAYS = 7
#: Clock skew tolerated on a captured_at ahead of now (1 hour).
ACCOUNT_SNAPSHOT_FUTURE_SKEW_DAYS = 1.0 / 24.0

#: MD-DEMOTE-S2-S1's firing rule, T3 AND net<0 -- OPERATOR DECISION 2026-09-29
#: ~14:17Z, popup in manager session session_01HYq6XtfesZ57VyaQrK6CL1, verbatim
#: "Last 20 + strict test (Recommended)" (pipeline row
#: PI-20260929-VOLSKIP-SIGNAL-0007; priced in
#: scripts/research/r4_trigger_pricing_sim.py / SIGNAL-0005). A leg's LAST 40
#: closed trades are read as two non-overlapping 20-trade windows; it demotes
#: only when BOTH windows' net R is below that leg's own p10 of the 20-trade
#: bootstrap from its Stage-0 evidence record AND below 0, and the 40-trade net
#: is < 0 (the mandate's never-on-a-non-negative-window clause, kept).
#:
#: The seed and draw count are FIXED so the threshold is a pure function of the
#: committed Stage-0 record: the gate that writes a mirror-window record and
#: every replay of this resolver (check_mandate_autoland A5) compute the same
#: number, and a record whose stated threshold disagrees is refused.
T3_RULE_ID = "MD-DEMOTE-S2-S1/T3-AND-NET-NEG-v1"
T3_BLOCK = 20
T3_WINDOWS = 2
T3_N = T3_BLOCK * T3_WINDOWS
T3_PERCENTILE = 10.0
T3_BOOTSTRAP_SEED = 20260929
T3_BOOTSTRAP_DRAWS = 20000
#: Fewer Stage-0 trades than this and there is nothing to resample -- a single
#: value's "bootstrap" is a constant, not a distribution.
T3_MIN_SOURCE_TRADES = 2
#: Rounding slack between a record's 40-trade net and the sum of its two
#: windows (the API rounds each totalR to 4 dp).
T3_SUM_TOLERANCE = 1e-3


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


def _leg_execution(root: Path, leg: str) -> str:
    """`config/strategies.yaml::<leg>.execution`, normalized, default-permissive
    ("live") to match every other reader of this field in the repo -- omitting
    `execution` means live, per CLAUDE.md § "The two execution gates"."""
    strategies = (_yaml(root, STRATEGIES_REL) or {}).get("strategies") or {}
    cfg = strategies.get(leg) if isinstance(strategies, dict) else None
    return str((cfg or {}).get("execution", "live")).strip().lower()


def _soak_data_task(leg: str, soak: Optional[str], mid: str, account: str) -> Dict[str, Any]:
    """The data task for `R-NOT-SOAKED` / `R-EXECUTION-SHADOW` -- #13698's case.

    A leg can sit on a Stage-1 paper roster's `strategies:` list while carrying
    `execution: shadow`: it logs order packages and is never actually filled,
    so its presence on the list is not evidence of a soak. This is the ONE data
    task both clauses point at -- soaking is a single fact, whichever way it is
    missing.
    """
    return {
        "what": (f"{leg} needs a genuine Stage-1 soak on {soak!r} before {mid} can decide "
                 f"{leg} -> {account}: it must be `execution: live` (not `shadow`) on that "
                 f"roster, then accrue real fills there -- 'Never a leg that has not soaked "
                 f"at Stage 1' (the mandate's own `never:`)."),
        "clears_when": (f"config/strategies.yaml::{leg}.execution is not 'shadow' AND {leg} "
                        f"is on {soak!r}'s roster, with enough real fills there to clear "
                        f"RULE-R3-GATE1-COST-FIDELITY-v1's floor (>=10 measured entries and "
                        f">=10 exits per side)"),
        "check_every_days": 7,
        "next_action": "dispatch_lane",
    }


def _record_missing_data_task(leg: str, frm: str, to: str) -> Dict[str, Any]:
    return {
        "what": (f"{leg} has no committed Stage-0 evidence record at "
                 f"{EVIDENCE_DIR_REL}/{leg}.json -- nothing has backtested it net of the full "
                 f"cost stack, so {frm} -> {to} cannot be decided either way."),
        "clears_when": (f"{EVIDENCE_DIR_REL}/{leg}.json exists, is committed, and passes B1's "
                        f"four clauses ({b1.__name__}.clause_verdicts)"),
        "check_every_days": 14,
        "next_action": "dispatch_lane",
    }


def _n_floor_data_task(leg: str, n: Any, min_n: int) -> Dict[str, Any]:
    return {
        "what": (f"{leg}'s Stage-0 record states n_trades_oos={n!r}, below the mandate's floor "
                 f"of {min_n} closed trades -- accrue more history or re-run the harness over a "
                 f"longer window."),
        "clears_when": f"{EVIDENCE_DIR_REL}/{leg}.json states n_trades_oos >= {min_n}",
        "check_every_days": 14,
        "next_action": "dispatch_lane",
    }


def _cost_fidelity_data_task(leg: str, r3: Dict[str, Any], tol: Any, provisional: bool) -> Dict[str, Any]:
    verdict_or_why = r3.get("verdict") or r3.get("why")
    basis = ("the operator-accepted PROVISIONAL 5.0 bps placeholder (no measured baseline exists "
            "on this venue yet)" if provisional else "a measured venue baseline")
    return {
        "what": (f"{leg}'s Stage-1 cost fidelity ({R3_RULE_ID}) reads {verdict_or_why!r} against "
                 f"{basis} -- not enough measured fills to grade it `consistent` or `divergent`."),
        "clears_when": (f"the newest {R3_DIR_REL}/<date>.json grades {leg} `consistent` or "
                        f"`divergent` (not inconclusive/insufficient_n/no_record, and not stale "
                        f"against the record's current modelled slippage) at "
                        f"bar.cost_tolerance_bps={tol}"),
        "check_every_days": 7,
        "next_action": "check_observation",
    }


def _afford_data_task(account: str, why: str) -> Dict[str, Any]:
    rel = f"{ACCOUNT_SNAPSHOT_DIR_REL}/{account}.json"
    return {
        "what": (f"{account}'s equity and buying power are not measured for the S1->S2 "
                 f"affordability clause (R-AFFORD): {why}. Read them with "
                 f"`scripts/ops/diag_fetch.sh 'broker_account_status?account_id={account}' | "
                 f"python3 scripts/ops/snapshot_account_for_mandate.py` and commit {rel}."),
        "clears_when": (f"{rel} exists, is committed, and its captured_at is within "
                        f"{ACCOUNT_SNAPSHOT_MAX_AGE_DAYS} days of the resolver run"),
        "check_every_days": 7,
        "next_action": "dispatch_lane",
    }


def _parse_ts(v: Any):
    from datetime import datetime, timezone
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _account_snapshot(root: Path, account: str) -> Dict[str, Any]:
    """The committed equity reading for `account`, or `_NeedsData` naming it."""
    from datetime import datetime, timezone
    rel = f"{ACCOUNT_SNAPSHOT_DIR_REL}/{account}.json"
    snap = _json(root / rel)
    if snap is None:
        raise _NeedsData("R-AFFORD", f"no readable committed equity snapshot at {rel}",
                         _afford_data_task(account, f"{rel} is absent or unreadable"))
    if snap.get("account_id") != account:
        raise _NeedsData("R-AFFORD", f"{rel} is for account_id={snap.get('account_id')!r}",
                         _afford_data_task(account, f"{rel} names another account"))
    eq = snap.get("equity_usd")
    if not _finite(eq) or eq < 0:
        raise _NeedsData("R-AFFORD", f"{rel} equity_usd={eq!r} is not a measurement",
                         _afford_data_task(account, "equity_usd missing"))
    ts = _parse_ts(snap.get("captured_at"))
    if ts is None:
        raise _NeedsData("R-AFFORD", f"{rel} carries no parseable captured_at",
                         _afford_data_task(account, "captured_at missing"))
    age_d = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    # A captured_at in the FUTURE gives a negative age and would never go
    # stale. Allow 1h of clock skew; anything further ahead is not a reading.
    if age_d < -ACCOUNT_SNAPSHOT_FUTURE_SKEW_DAYS:
        raise _NeedsData("R-AFFORD", f"{rel} captured_at={snap['captured_at']} is in the future "
                                     f"({-age_d * 24:.1f}h ahead)",
                         _afford_data_task(account, "the committed reading is future-dated"))
    if age_d > ACCOUNT_SNAPSHOT_MAX_AGE_DAYS:
        raise _NeedsData("R-AFFORD", f"{rel} captured_at={snap['captured_at']} is {age_d:.1f} "
                                     f"days old (> {ACCOUNT_SNAPSHOT_MAX_AGE_DAYS})",
                         _afford_data_task(account, f"the committed reading is {age_d:.1f} days old"))
    return {**snap, "_rel": rel, "_age_days": round(age_d, 2)}


def _affordability(leg: str, account: str, exchange: str, record: Dict[str, Any],
                   acfg: Dict[str, Any], root: Path, ctx: Dict[str, Any]) -> None:
    """R-AFFORD: would `account`'s own sizer size this leg's setups at all?

    Population: every setup in the record's committed `source_run` whose
    direction `account`'s `side_filter` permits (a suppressed direction is a
    trade the account never takes, so it is not evidence the leg can trade
    there). Each setup is RE-PRICED to the latest committed entry in that same
    run, keeping its stop distance as a fraction of price -- a 2025 GLD setup at
    $307 says nothing about buying GLD at $393. Each is then sized by
    `RiskManager.position_size` with `account`'s declared `risk:` block, its
    measured equity and buying power, and the exchange's whole-unit rule.

    REFUSE when that population is empty or NONE of it sizes above zero: the
    edit would put a leg on a real-money roster that cannot place an order.
    NEEDS_DATA when the equity reading is absent, stale, future-dated or not
    finite. A partial fraction passes and is reported, never hidden.

    ⚠️ NECESSARY, NOT SUFFICIENT. A pass does NOT guarantee the leg sizes live:
    the RiskManager here is fresh (daily_pnl=0, no drawdown state), ignores
    open exposure, pledged margin and the T+1 cash-settlement basis, defaults
    a missing confidence to 1.0, and applies only the ACCOUNT side_filter, not
    a strategy-level long_only/side_filter. Every one of those can only make
    the live sizer refuse MORE, so a REFUSE here is decisive and a pass is not.
    """
    snap = _account_snapshot(root, account)
    cfg = acfg.get(account) or {}
    sys.path.insert(0, str(REPO))
    from src.core.coordinator import OrderPackage
    from src.runtime.strategy_signal_builders import _side_filter_suppresses
    from src.units.accounts.risk import RiskManager, requires_whole_unit_qty

    side_filter = str(cfg.get("side_filter") or "both").strip().lower()
    rows: List[Dict[str, Any]] = []
    src_path = root / str(record.get("source_run") or "")
    for line in src_path.read_text(encoding="utf-8").splitlines() if src_path.is_file() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and _finite(r.get("entry")) and _finite(r.get("sl")) \
                and r["entry"] > 0 and r.get("direction") in ("long", "short"):
            rows.append(r)
    ev: Dict[str, Any] = {"snapshot": snap["_rel"], "captured_at": snap.get("captured_at"),
                          "snapshot_age_days": snap["_age_days"],
                          "equity_usd": snap.get("equity_usd"),
                          "buying_power_usd": snap.get("buying_power_usd"),
                          "side_filter": side_filter, "setups_in_run": len(rows)}
    ctx["evidence"]["affordability"] = ev
    if not rows:
        raise _Refuse("R-AFFORD", f"source_run {record.get('source_run')!r} carries no setup with "
                                  "entry/sl/direction -- nothing to size")
    latest = max(rows, key=lambda r: str(r.get("entry_time") or ""))
    ref = float(latest["entry"])
    ev.update({"ref_price": round(ref, 4), "ref_price_at": latest.get("entry_time"),
               "ref_price_source": record.get("source_run")})
    permitted = [r for r in rows if not _side_filter_suppresses(r["direction"], side_filter)]
    ev["setups_permitted"] = len(permitted)
    if not permitted:
        raise _Refuse("R-AFFORD", f"{account} is side_filter:{side_filter} and none of {leg}'s "
                                  f"{len(rows)} setups is in a permitted direction")
    rm = RiskManager(cfg.get("risk") or {}, dry_run=True, account_id="")
    equity = float(snap["equity_usd"])
    bp = snap.get("buying_power_usd")
    if bp is not None and not _finite(bp):
        raise _NeedsData("R-AFFORD", f"{snap['_rel']} buying_power_usd={bp!r} is not a finite "
                                     "measurement", _afford_data_task(account, "buying_power_usd not finite"))
    bp = float(bp) if bp is not None else None
    whole = requires_whole_unit_qty(exchange)
    sized = []
    for r in permitted:
        scale = ref / float(r["entry"])
        sl = float(r["sl"]) * scale
        dist = abs(ref - sl)
        tp = ref + 2 * dist if r["direction"] == "long" else ref - 2 * dist
        conf = r.get("confidence")
        pkg = OrderPackage(strategy=leg, symbol=str(r.get("symbol") or ""),
                           direction=r["direction"], entry=ref, sl=sl, tp=tp,
                           confidence=float(conf) if _finite(conf) else 1.0, meta={})
        qty = rm.position_size(pkg, equity, available_usd=bp, total_account_usd=equity,
                               whole_units=whole,
                               market_type=str(cfg.get("market_type") or "spot"))
        sized.append(float(qty or 0.0))
    ok = sum(1 for q in sized if q > 0)
    ev.update({"setups_sized": ok, "max_qty": max(sized), "whole_units": whole,
               "one_unit_notional_usd": round(ref, 4)})
    if ok == 0:
        raise _Refuse("R-AFFORD", f"0 of {len(permitted)} permitted setups size above zero on "
                                  f"{account} (equity ${equity:.2f}, buying power "
                                  f"{'$%.2f' % bp if bp is not None else 'unread'}, "
                                  f"{'whole-unit' if whole else 'fractional'} venue) at "
                                  f"{leg}'s latest committed price {ref:.2f} "
                                  f"({latest.get('entry_time')}) -- the edit would land a leg "
                                  "that cannot place an order")
    if ok < len(permitted):
        ctx["caveats"].append(
            f"AFFORDABILITY PARTIAL: {ok} of {len(permitted)} permitted setups size on {account}; "
            "the rest refuse at the sizer")


def _percentile(sorted_vals: List[float], q: float) -> float:
    """numpy's default (linear) percentile over an already-sorted list, so the
    threshold matches scripts/research/r4_trigger_pricing_sim.py's definition
    without importing numpy into the gate's runner."""
    pos = (len(sorted_vals) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def stage0_block_p10(leg: str, root: Path = REPO, *, seed: int = T3_BOOTSTRAP_SEED,
                     draws: int = T3_BOOTSTRAP_DRAWS, block: int = T3_BLOCK,
                     pct: float = T3_PERCENTILE) -> Dict[str, Any]:
    """The leg's own p10 of ``block``-trade net-R sums, bootstrapped from the
    per-trade ``net_r`` of its Stage-0 evidence record's COMMITTED source_run.

    Returns ``{"p10", "evidence_record", "source_run", "source_n", "seed",
    "draws", "block", "why"}``; ``p10`` is None (and ``why`` says what was
    missing) when the record, its source_run, or enough numeric ``net_r``
    rows are absent. A None is "we could not look" -- never a guessed
    threshold."""
    rel = f"{EVIDENCE_DIR_REL}/{leg}.json"
    out: Dict[str, Any] = {"p10": None, "evidence_record": rel, "source_run": None,
                           "source_n": 0, "seed": seed, "draws": draws, "block": block,
                           "percentile": pct, "why": None}
    rec = _json(root / rel)
    if rec is None or not in_repo(root, rel):
        out["why"] = f"no committed Stage-0 evidence record at {rel}"
        return out
    src = rec.get("source_run")
    out["source_run"] = src
    if not in_repo(root, src):
        out["why"] = f"{rel} cites source_run {src!r}, which is not a committed file"
        return out
    xs: List[float] = []
    try:
        for line in (root / src).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            v = json.loads(line).get("net_r")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                xs.append(float(v))
    except (OSError, ValueError, AttributeError) as e:
        out["why"] = f"{src} is not a readable per-trade JSONL: {e}"
        return out
    out["source_n"] = len(xs)
    if len(xs) < T3_MIN_SOURCE_TRADES:
        out["why"] = (f"{src} carries {len(xs)} numeric net_r row(s); at least "
                      f"{T3_MIN_SOURCE_TRADES} are needed to resample")
        return out
    import random  # local: only this function draws
    rng = random.Random(seed)
    n = len(xs)
    sums = sorted(sum(xs[rng.randrange(n)] for _ in range(block)) for _ in range(draws))
    out["p10"] = round(_percentile(sums, pct), 6)
    return out


def _mirror_window_data_task(leg: str, rel: str) -> Dict[str, Any]:
    return {
        "what": (f"{leg} has no usable Stage-2 mirror-window record at {rel} -- MD-DEMOTE-S2-S1 "
                 f"needs a stated net_r_net_of_full_cost from a live mirror window before it can "
                 f"decide a demotion."),
        "clears_when": (f"{rel} exists, is committed, and states a numeric net_r_net_of_full_cost "
                        f"plus {T3_WINDOWS} full {T3_BLOCK}-trade T3 windows ({T3_RULE_ID})"),
        "check_every_days": 7,
        "next_action": "check_observation",
    }


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
    """A clause read a DECISIVE measurement and it failed. Re-running against
    the same evidence answers REFUSE again -- see the module docstring's
    "THREE OUTCOMES" section for the line between this and `_NeedsData`."""

    def __init__(self, clause: str, detail: str):
        super().__init__(f"{clause}: {detail}")
        self.clause, self.detail = clause, detail


class _NeedsData(Exception):
    """A clause could not find a decisive measurement at all -- missing,
    below a stated floor, or a non-decisive R3 verdict. Carries `data_task`,
    a dict `needs_data_pipeline_item()` turns directly into a pipeline.py
    item: {"what", "clears_when", "check_every_days", "next_action"}."""

    def __init__(self, clause: str, detail: str, data_task: Dict[str, Any]):
        super().__init__(f"{clause}: {detail}")
        self.clause, self.detail, self.data_task = clause, detail, data_task


def _result(verdict: str, clause: str, detail: str, ctx: Dict[str, Any],
           data_task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # `data_task` is ALWAYS a key, never omitted -- present and `None` on FIRE
    # and REFUSE, present and a dict only on NEEDS_DATA. A key that only shows
    # up on one branch is the same collapsed-state defect this repo already
    # names (docs/CLAUDE-RULES-CANONICAL.md § "Collapsed states").
    return {"verdict": verdict, "clause": clause, "detail": detail,
           "data_task": data_task, **ctx}


def resolve(leg: str, from_stage: str, to_stage: str, account: str, *,
            root: Path = REPO, mandate_id: Optional[str] = None) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"leg": leg, "from_stage": from_stage, "to_stage": to_stage,
                           "account": account, "mandate": None, "evidence": {},
                           "caveats": [], "proposal": None}
    try:
        _decide(leg, from_stage, to_stage, account, root, mandate_id, ctx)
    except _NeedsData as nd:
        ctx["proposal"] = None
        return _result(NEEDS_DATA, nd.clause, nd.detail, ctx, data_task=nd.data_task)
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
    elif mid == S1_CUT_MANDATE_ID:
        if (frm, to) != ("S1", "OFF"):
            raise _Refuse("R-TRANSITION", f"{mid} covers S1 -> OFF only, not {frm} -> {to}")
        _s1_cut_on_stage0_fail(leg, account, exchange, m, rosters, acfg, root, ctx)
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
            raise _NeedsData("R-NOT-SOAKED", f"{leg} is not on the Stage-1 soak roster {soak!r}; "
                                             "Stage 2 is reached only through Stage 1",
                             _soak_data_task(leg, soak, m["id"], account))
        # ⚠️ #13698: roster MEMBERSHIP is not the same fact as having SOAKED.
        # `execution: shadow` logs order packages and places no real order, so
        # a shadow leg can sit on the soak roster's `strategies:` list forever
        # while accruing zero real fills. The mandate's own `never:` reads
        # "Never a leg that has not soaked at Stage 1" -- a shadow leg has not.
        if _leg_execution(root, leg) == "shadow":
            raise _NeedsData("R-EXECUTION-SHADOW",
                             f"{leg} is `execution: shadow` in {STRATEGIES_REL} -- it logs order "
                             f"packages on {soak!r} but places no real order, so roster membership "
                             f"there is not a Stage-1 soak",
                             _soak_data_task(leg, soak, m["id"], account))

    # ── the committed record ────────────────────────────────────────────────
    rec_rel = f"{EVIDENCE_DIR_REL}/{leg}.json"
    record = b1.load_record(leg, root / EVIDENCE_DIR_REL)
    if record is None:
        raise _NeedsData("R-RECORD-MISSING", f"no readable committed record at {rec_rel}",
                         _record_missing_data_task(leg, frm, to))
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
        raise _NeedsData("R-N", f"n_trades_oos={n} < {min_n} closed",
                         _n_floor_data_task(leg, n, min_n))
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

    # ── affordability (S1 -> S2 only) ───────────────────────────────────────
    # Before cost fidelity on purpose: a leg the real-money account cannot
    # size is a DECISIVE refusal, and filing a "collect more fills" data task
    # for it would be work towards a promotion that can never trade.
    if (frm, to) == ("S1", "S2"):
        _affordability(leg, account, exchange, record, acfg, root, ctx)

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

    Reads the R3 PER-LEG verdict (see the module docstring). `consistent` is
    the only pass and `divergent` is the only REFUSE -- both are DECISIVE. Every
    other outcome -- `inconclusive`, `insufficient_n`, `no_record`, a stale
    verdict, or no R3 record committed at all -- is NEEDS_DATA: "we cannot yet
    tell" is not evidence either way, and this mandate routes real money.
    """
    tol = (m.get("bar") or {}).get("cost_tolerance_bps")
    if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol < 0:
        raise _Refuse("R-COST-FIDELITY", f"bar.cost_tolerance_bps={tol!r} is not stated")
    modelled = (record.get("cost_stack") or {}).get("slippage")
    if venue is None:
        raise _Refuse("R-COST-FIDELITY", "the account's exchange maps to no known venue")

    provisional = venue in PROVISIONAL_VENUES
    # The placeholder floor is the mandate's own `never:` and is checked FIRST.
    # A measurement never buys a way around it -- see the docstring. This is a
    # DECISIVE fact about the record itself (it assumed less cost than the
    # operator-accepted floor), not an absence of data, so it stays REFUSE.
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
    if verdict is not None:
        stale = _stale_against_record(r3, modelled)
        if stale:
            # A STALE VERDICT IS NOT A VERDICT -- it answered a question about a
            # record that no longer exists, which is exactly "no decisive
            # measurement", never a pass and never a refusal.
            cf["stale"] = stale
            raise _NeedsData("R-COST-FIDELITY", stale,
                             _cost_fidelity_data_task(leg, r3, tol, provisional))
        if verdict == R3_CONSISTENT:
            return  # the Gate-1 pass, on any venue
        if verdict == R3_DIVERGENT:
            raise _Refuse("R-COST-FIDELITY",
                          f"{R3_RULE_ID} grades {leg} `divergent` on "
                          f"{r3.get('from_account')} ({r3.get('basis')}): realized cost exceeds "
                          f"the modelled {modelled} bps + tolerance {tol} with 95% confidence "
                          f"({r3.get('record')})")
        # `inconclusive` / `insufficient_n` / `no_record`: a real per-leg
        # measurement exists and does not yet decide -- NEEDS_DATA, never a pass
        # (2026-09-29 correction: this used to fall through to a FIRE-with-
        # caveat on a provisional venue -- see the module docstring's #13698
        # account of why that was wrong).
        raise _NeedsData("R-COST-FIDELITY",
                         f"{R3_RULE_ID} grades {leg} `{verdict}` ({r3.get('record')}) -- not "
                         f"decisive either way",
                         _cost_fidelity_data_task(leg, r3, tol, provisional))
    # verdict is None: no R3 record committed at all, the leg is absent from
    # the newest one, the record is unreadable, or its verdict string is
    # outside the rule's vocabulary -- every one of those is "we could not
    # look", never a pass.
    raise _NeedsData("R-COST-FIDELITY", r3.get("why") or "no per-leg cost-fidelity verdict",
                     _cost_fidelity_data_task(leg, r3, tol, provisional))


def _num(v: Any) -> bool:
    """A real, FINITE number. ⚠️ NaN and inf are rejected on purpose: every bar
    comparison in this module is written `x < cap` / `x > cap`, which is False
    for NaN, so a NaN that reached one would PASS it. json round-trips NaN, so a
    corpus row can carry one. Non-finite is "we could not read it", never a pass."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _fin(v: Any) -> float:
    """float(v), or ValueError if it is not a finite real number."""
    if isinstance(v, bool):
        raise ValueError("bool is not a number")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"non-finite: {v!r}")
    return f


def _finite(v: Any) -> bool:
    """`_num` AND not NaN/Inf. R-AFFORD's inputs use this: `json` accepts
    `NaN` / `Infinity` literals, and an infinite equity would size anything."""
    import math
    return _num(v) and math.isfinite(v)


def _demote_s2(leg: str, root: Path, ctx: Dict[str, Any]) -> None:
    """MD-DEMOTE-S2-S1, rule T3 AND net<0 (operator 2026-09-29; see T3_RULE_ID).

    Order matters and is stated: the never-clauses (record absent/unreadable,
    source_run absent, a non-negative 40-trade net) are checked FIRST and
    exactly as before, so the T3 rule can only ever NARROW when this fires."""
    rel = f"{MIRROR_DIR_REL}/{leg}.json"
    rec = _json(root / rel)
    if rec is None:
        raise _NeedsData("R-RECORD-MISSING", f"no committed mirror-window record at {rel} -- "
                                             "the demotion signal has not accrued yet",
                         _mirror_window_data_task(leg, rel))
    net = rec.get("net_r_net_of_full_cost")
    ctx["evidence"].update({"record": rel, "n_closed": rec.get("n_closed"),
                            "net_r_net_of_full_cost": net, "source_run": rec.get("source_run"),
                            "rule": rec.get("rule")})
    if not in_repo(root, rec.get("source_run")):
        raise _Refuse("R-SOURCE-RUN-ABSENT", f"source_run {rec.get('source_run')!r} is not in the repo")
    if not _num(net):
        raise _NeedsData("R-EXPECTANCY", f"net_r_net_of_full_cost={net!r} is not stated",
                         _mirror_window_data_task(leg, rel))
    if net >= 0:
        raise _Refuse("R-EXPECTANCY", f"mirror window net {net} R is not negative -- the evidence "
                                      "does not support a demotion")

    # ── T3: two non-overlapping 20-trade windows, each below the leg's p10 ──
    if rec.get("rule") != T3_RULE_ID:
        raise _NeedsData("R-T3-WINDOWS", f"{rel} does not declare rule {T3_RULE_ID!r} (got "
                                         f"{rec.get('rule')!r}) -- it carries no T3 windows to judge",
                         _mirror_window_data_task(leg, rel))
    wins = rec.get("windows")
    if (not isinstance(wins, list) or len(wins) != T3_WINDOWS
            or not all(isinstance(w, dict) for w in wins)):
        raise _NeedsData("R-T3-WINDOWS", f"{rel} does not carry exactly {T3_WINDOWS} windows",
                         _mirror_window_data_task(leg, rel))
    short = [w.get("n_closed") for w in wins if w.get("n_closed") != T3_BLOCK]
    if short or rec.get("n_closed") != T3_N:
        raise _NeedsData("R-N", f"T3 needs {T3_WINDOWS} full {T3_BLOCK}-trade windows ({T3_N} "
                                f"closed); record states n_closed={rec.get('n_closed')!r}, "
                                f"window n_closed={[w.get('n_closed') for w in wins]}",
                         _mirror_window_data_task(leg, rel))
    nets = [w.get("net_r") for w in wins]
    if not all(_num(x) for x in nets):
        raise _NeedsData("R-T3-WINDOWS", f"window net_r not stated: {nets!r}",
                         _mirror_window_data_task(leg, rel))
    if abs(sum(nets) - net) > T3_SUM_TOLERANCE:
        raise _Refuse("R-T3-WINDOWS", f"windows sum to {sum(nets):+.4f} R but the record's "
                                      f"40-trade net is {net:+.4f} R -- the record contradicts itself")

    p = stage0_block_p10(leg, root)
    ctx["evidence"].update({"t3_p10": p["p10"], "t3_evidence_record": p["evidence_record"],
                            "t3_evidence_source_run": p["source_run"],
                            "t3_evidence_n": p["source_n"], "t3_seed": p["seed"],
                            "t3_window_net_r": nets})
    if p["p10"] is None:
        raise _NeedsData("R-T3-NO-EVIDENCE", f"no usable Stage-0 threshold for {leg}: {p['why']}",
                         {"what": (f"{leg} has no usable Stage-0 evidence to price its T3 "
                                   f"threshold: {p['why']}"),
                          "clears_when": (f"{p['evidence_record']} and its source_run are committed "
                                          f"with >= {T3_MIN_SOURCE_TRADES} numeric net_r rows"),
                          "check_every_days": 7, "next_action": "check_observation"})
    stated = rec.get("p10_threshold")
    if not _num(stated) or abs(float(stated) - p["p10"]) > 1e-6 \
            or rec.get("bootstrap_seed") != T3_BOOTSTRAP_SEED \
            or rec.get("evidence_record") != p["evidence_record"]:
        raise _Refuse("R-T3-THRESHOLD", f"record states p10_threshold={stated!r} seed="
                                        f"{rec.get('bootstrap_seed')!r} from "
                                        f"{rec.get('evidence_record')!r}; recomputed {p['p10']} "
                                        f"(seed {T3_BOOTSTRAP_SEED}) from {p['evidence_record']}")
    bar = min(p["p10"], 0.0)
    above = [x for x in nets if not x < bar]
    if above:
        raise _Refuse("R-T3", f"window net R {nets} not both below min(p10 {p['p10']:+.4f}, 0) "
                              f"-- T3 is not met, the evidence does not support a demotion")


def _demote_s1_off(leg: str, venue: Optional[str], m: Dict[str, Any], root: Path,
                   ctx: Dict[str, Any]) -> None:
    """MD-DEMOTE-S1-OFF: the LEG's realized cost diverges from what it modelled.

    `divergent` is the only firing verdict and `consistent` is the only REFUSE
    -- both DECISIVE. Everything else (`inconclusive`, `insufficient_n`,
    `no_record`, a stale verdict, or no verdict to read at all) is NEEDS_DATA:
    that asymmetry is the mandate's own `never:` clause -- *"an unmeasurable
    venue shows no divergence, and 'could not measure' must not read as
    'diverged'"* -- and, symmetrically, must not read as "does not diverge"
    either. Reading either as a demotion (or a refusal to demote) would decide
    on the strength of a measurement that said it could not decide.
    """
    rec_rel = f"{EVIDENCE_DIR_REL}/{leg}.json"
    record = b1.load_record(leg, root / EVIDENCE_DIR_REL)
    if record is None:
        raise _NeedsData("R-RECORD-MISSING", f"no readable committed record at {rec_rel}",
                         _record_missing_data_task(leg, "S1", "OFF"))
    ctx["evidence"]["record"] = rec_rel
    tol = (m.get("bar") or {}).get("cost_tolerance_bps")
    if not isinstance(tol, (int, float)) or isinstance(tol, bool):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{m['id']} does not state bar.cost_tolerance_bps")
    modelled = (record.get("cost_stack") or {}).get("slippage")
    r3 = r3_leg_verdict(leg, root)
    cf = dict(r3, venue=venue, modelled_bps=modelled, tolerance_bps=tol,
              rule_doc=R3_RULE_DOC)
    ctx["evidence"]["cost_fidelity"] = cf
    provisional = venue in PROVISIONAL_VENUES
    verdict = r3.get("verdict")
    if verdict is None:
        raise _NeedsData("R-RECORD-MISSING",
                         f"{r3.get('why')} -- no per-leg cost-fidelity verdict to demote on, "
                         f"and 'could not measure' is not 'diverged'",
                         _cost_fidelity_data_task(leg, r3, tol, provisional))
    stale = _stale_against_record(r3, modelled)
    if stale:
        cf["stale"] = stale
        raise _NeedsData("R-COST-FIDELITY", stale, _cost_fidelity_data_task(leg, r3, tol, provisional))
    if verdict == R3_DIVERGENT:
        return  # the demotion FIRE path
    if verdict == R3_CONSISTENT:
        raise _Refuse("R-COST-FIDELITY",
                      f"{R3_RULE_ID} grades {leg} `consistent` ({r3.get('record')}) -- realized "
                      f"cost matches what the record modelled; the evidence does not support a "
                      f"demotion")
    raise _NeedsData("R-COST-FIDELITY",
                     f"{R3_RULE_ID} grades {leg} `{verdict}`, not `divergent` ({r3.get('record')}) "
                     f"-- not decisive either way",
                     _cost_fidelity_data_task(leg, r3, tol, provisional))


# --------------------------------------------------------------------------
# MD-S1-CUT-ON-STAGE0-FAIL -- remove a Stage-1 leg whose OFFLINE evidence fails
# (operator grant 2026-10-04 ~21:25Z, popup in manager session
# session_01MM8o5js6TcDFeNAPBY4Ntv, verbatim "Grant it (Recommended)"; proposed
# in docs/plans/soak-verdicts-2026-10-04.md § "Proposed mandate").
#
# The gap it closes, MEASURED in that doc: a leg whose Stage-1 soak is complete
# and whose Stage-0 record fails had NO mandate that removes it --
# MD-DEMOTE-S1-OFF fires only on an R3 `divergent` cost verdict, and
# MD-DERISK-ONLY-ROSTER-CUT covers real-money rosters only. So the same Tier-3
# question reached the operator once per leg.
#
# It is reached ONLY through `--mandate MD-S1-CUT-ON-STAGE0-FAIL` on an
# S1 -> OFF transition: TRANSITION_MANDATE still maps S1 -> OFF to
# MD-DEMOTE-S1-OFF, so no existing consumer (r4_demotion_gate,
# check_mandate_autoland) changes behaviour.
#
# FIRE needs ALL of:
#   (1) `account` is in the mandate's `scope_accounts` (bybit_1, alpaca_paper);
#   (2) the leg is on NO roster outside paper soak books -- a leg also on a
#       real-money roster or a Stage-2 mirror is MD-DEMOTE-S2-S1's (REFUSE);
#   (3) the leg is `execution: live` -- a shadow leg is not a Stage-1 soak
#       (REFUSE, out of scope; its disposition is a separate question);
#   (4) a DECISIVE Stage-0 failure (any one, every one is listed):
#         R-CUT-RULE       decision_rule.verdict == fail, registered before the run
#         R-CUT-EXPECTANCY expectancy_r_oos <= 0 or net_r_oos <= 0, net of full cost
#         R-CUT-FOLDS      positive folds not a majority (bar.fold_majority)
#         R-CUT-N          n_trades_oos < bar.min_n_closed
#         R-CUT-AFFORD     the exchange's Stage-2 account sizes NONE of the leg's
#                          setups (the S1->S2 R-AFFORD measurement, reused)
#       read only off a record that is DECISIVE: named harness, stated n, the
#       FULL cost stack (B1 C1-C3), bound to the leg's current config (B1
#       identity), source_run committed. A record that is not is NEEDS_DATA --
#       "could not look" is never a cut signal;
#   (5) the leg's Stage-1 soak contract (SOAK-stage1-<account>-<leg>) reads a
#       state in bar.soak_states (ready | overdue) in the newest committed soak
#       snapshot under comms/mandate_evidence/soak_state/, captured within
#       bar.soak_snapshot_max_age_days. Absent, stale, or any other state is
#       NEEDS_DATA.
# A record with no failing clause REFUSEs (R-STAGE0-PASSES): the evidence must
# support the cut, not merely fail to forbid it.
# --------------------------------------------------------------------------
S1_CUT_MANDATE_ID = "MD-S1-CUT-ON-STAGE0-FAIL"
SOAK_SNAPSHOT_DIR_REL = "comms/mandate_evidence/soak_state"
#: The Stage-2 account per exchange whose sizer R-CUT-AFFORD asks.
STAGE2_ACCOUNT = {"bybit": "bybit_2", "alpaca": "alpaca_live", "interactive_brokers": "ib_live"}


def latest_soak_snapshot(root: Path) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """The newest DATED (YYYY-MM-DD.json) committed soak-state snapshot."""
    d = root / SOAK_SNAPSHOT_DIR_REL
    if not d.is_dir():
        return None, None
    dated = sorted(p for p in d.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", p.name))
    if not dated:
        return None, None
    rel = f"{SOAK_SNAPSHOT_DIR_REL}/{dated[-1].name}"
    return rel, _json(dated[-1])


def _soak_snapshot_data_task(leg: str, account: str, why: str) -> Dict[str, Any]:
    return {
        "what": (f"{leg}@{account}'s Stage-1 soak state is not readable for "
                 f"{S1_CUT_MANDATE_ID}: {why}. Capture it with "
                 f"`python3 scripts/ops/snapshot_soak_state_for_mandate.py` (reads the live "
                 f"brief's SOAKS section, computed by soak_state.soak_states()) and commit it."),
        "clears_when": (f"the newest {SOAK_SNAPSHOT_DIR_REL}/<date>.json is fresh and lists "
                        f"SOAK-stage1-{account}-{leg} as ready or overdue"),
        "check_every_days": 7,
        "next_action": "check_observation",
    }


def _soak_complete(leg: str, account: str, bar: Dict[str, Any], root: Path,
                   ctx: Dict[str, Any]) -> None:
    want = bar.get("soak_states")
    max_age = bar.get("soak_snapshot_max_age_days")
    if not isinstance(want, list) or not want or not _num(max_age):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{S1_CUT_MANDATE_ID} does not state "
                                               "bar.soak_states / bar.soak_snapshot_max_age_days")
    sid = f"SOAK-stage1-{account}-{leg}"
    rel, snap = latest_soak_snapshot(root)
    ev = {"snapshot": rel, "soak_id": sid}
    ctx["evidence"]["soak"] = ev
    if snap is None:
        raise _NeedsData("R-SOAK-INCOMPLETE", f"no readable committed soak snapshot under "
                                              f"{SOAK_SNAPSHOT_DIR_REL}/",
                         _soak_snapshot_data_task(leg, account, "no snapshot committed"))
    cap = _parse_ts(snap.get("captured_at"))
    if cap is None:
        raise _NeedsData("R-SOAK-INCOMPLETE", f"{rel} carries no parseable captured_at",
                         _soak_snapshot_data_task(leg, account, "captured_at unparseable"))
    from datetime import datetime, timezone
    age = (datetime.now(timezone.utc) - cap).total_seconds() / 86400.0
    ev.update({"captured_at": snap.get("captured_at"), "age_days": round(age, 2),
               "source": snap.get("source")})
    if age > float(max_age) or age < -ACCOUNT_SNAPSHOT_FUTURE_SKEW_DAYS:
        raise _NeedsData("R-SOAK-INCOMPLETE", f"{rel} captured_at={snap.get('captured_at')} is "
                                              f"{age:.1f} days old (limit {max_age})",
                         _soak_snapshot_data_task(leg, account, "snapshot stale"))
    row = next((r for r in snap.get("soaks") or [] if isinstance(r, dict) and r.get("id") == sid),
               None)
    if row is None:
        raise _NeedsData("R-SOAK-INCOMPLETE", f"{rel} lists no {sid} (the brief lists accruing "
                                              f"soaks only as a count, so absent is not complete)",
                         _soak_snapshot_data_task(leg, account, f"{sid} not listed"))
    ev.update({"state": row.get("state"), "progress": row.get("progress")})
    if row.get("state") not in want:
        raise _NeedsData("R-SOAK-INCOMPLETE", f"{sid} reads state={row.get('state')!r} "
                                              f"({row.get('progress')}); the cut needs one of {want}",
                         _soak_snapshot_data_task(leg, account, f"state {row.get('state')!r}"))


def _s1_cut_on_stage0_fail(leg: str, account: str, exchange: str, m: Dict[str, Any],
                           rosters: Dict[str, Dict[str, Any]], acfg: Dict[str, Any],
                           root: Path, ctx: Dict[str, Any]) -> None:
    bar = m.get("bar")
    if not isinstance(bar, dict):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{m['id']} does not state its `bar:`")
    scope = m.get("scope_accounts")
    if not isinstance(scope, list) or account not in scope:
        raise _Refuse("R-ACCOUNT", f"{account!r} is outside {m['id']}.scope_accounts={scope!r}")
    stage2_mirrors = set(MIRROR_OF.values())
    outside = sorted(a for a, r in rosters.items() if leg in r["legs"]
                     and (r["class"] != "paper" or a in stage2_mirrors))
    if outside:
        raise _Refuse("R-REAL-MONEY", f"{leg} is also on {outside} -- a real-money or Stage-2 "
                                      "mirror roster; that cut belongs to MD-DEMOTE-S2-S1")
    if _leg_execution(root, leg) == "shadow":
        raise _Refuse("R-EXECUTION-SHADOW", f"{leg} is `execution: shadow` -- roster membership "
                                            "is not a Stage-1 soak, so this mandate does not cover it")

    # ── the Stage-0 record, and is it DECISIVE ──────────────────────────────
    rec_rel = f"{EVIDENCE_DIR_REL}/{leg}.json"
    record = b1.load_record(leg, root / EVIDENCE_DIR_REL)
    if record is None:
        raise _NeedsData("R-RECORD-MISSING", f"no readable committed record at {rec_rel}",
                         _record_missing_data_task(leg, "S1", "OFF"))
    ev = ctx["evidence"]
    dr = record.get("decision_rule") or {}
    ev.update({"record": rec_rel, "harness": record.get("harness"),
               "n_trades_oos": record.get("n_trades_oos"),
               "expectancy_r_oos": record.get("expectancy_r_oos"),
               "net_r_oos": record.get("net_r_oos"), "folds": record.get("folds"),
               "folds_positive": record.get("folds_positive"),
               "cost_stack": record.get("cost_stack"), "decision_rule": dr.get("id"),
               "decision_rule_verdict": dr.get("verdict"),
               "source_run": record.get("source_run")})
    if not in_repo(root, record.get("source_run")):
        raise _Refuse("R-SOURCE-RUN-ABSENT", f"source_run {record.get('source_run')!r} is not a "
                                             "committed file in the repo")
    verdicts = {c: (ok, d) for c, ok, d in b1.clause_verdicts(record)}
    for c in ("C1", "C2", "C3"):
        if not verdicts.get(c, (False, ""))[0]:
            raise _NeedsData(f"R-B1-{c}", f"the record is not decisive: {verdicts.get(c, (0, '?'))[1]}",
                             _record_missing_data_task(leg, "S1", "OFF"))
    strategies = (_yaml(root, STRATEGIES_REL) or {}).get("strategies") or {}
    ok, detail = b1.identity_verdict(record, leg, strategies)
    if not ok:
        raise _NeedsData("R-B1-IDENTITY", f"the record does not describe {leg}'s current config: "
                                          f"{detail}", _record_missing_data_task(leg, "S1", "OFF"))

    # ── the failing clauses -- every one is recorded, any one is decisive ───
    fails: List[Tuple[str, str]] = []
    reg, gen = _parse_ts(dr.get("registered_at")), _parse_ts(record.get("generated_at"))
    if str(dr.get("verdict") or "").lower() == "fail" and reg is not None and gen is not None \
            and reg <= gen:
        fails.append(("R-CUT-RULE", f"decision_rule {dr.get('id')!r} (registered "
                                    f"{dr.get('registered_at')}, before the run) verdict=fail"))
    exp, net, n = record.get("expectancy_r_oos"), record.get("net_r_oos"), record.get("n_trades_oos")
    if _num(exp) and _num(net) and (exp <= 0 or net <= 0):
        fails.append(("R-CUT-EXPECTANCY", f"expectancy_r_oos={exp}, net_r_oos={net} -- not > 0 "
                                          "net of the full cost stack"))
    folds, pos = record.get("folds"), record.get("folds_positive")
    if bar.get("fold_majority") and isinstance(folds, int) and isinstance(pos, int) and folds >= 1:
        detail_rows = record.get("fold_detail")
        if isinstance(detail_rows, list):
            counted = sum(1 for f in detail_rows if isinstance(f, dict)
                          and _num(f.get("net_r")) and f["net_r"] > 0)
            if len(detail_rows) != folds or counted != pos:
                raise _Refuse("R-FOLDS", f"fold_detail shows {counted}/{len(detail_rows)} positive "
                                         f"but the record states {pos}/{folds}")
        if not 2 * pos > folds:
            fails.append(("R-CUT-FOLDS", f"positive in {pos} of {folds} folds -- not a majority"))
    min_n = bar.get("min_n_closed")
    if isinstance(min_n, int) and not isinstance(min_n, bool) and isinstance(n, int) and n < min_n:
        fails.append(("R-CUT-N", f"n_trades_oos={n} < bar.min_n_closed={min_n}"))
    afford_pending: Optional[_NeedsData] = None
    if not fails and bar.get("affordability"):
        s2 = STAGE2_ACCOUNT.get(exchange)
        if s2 and s2 in acfg:
            try:
                _affordability(leg, s2, exchange, record, acfg, root, ctx)
            except _Refuse as r:
                # A source_run with no sizable setup measured nothing: that is
                # "could not look", never "unaffordable".
                if not (ctx["evidence"].get("affordability") or {}).get("setups_in_run"):
                    afford_pending = _NeedsData("R-CUT-AFFORD", r.detail,
                                                _record_missing_data_task(leg, "S1", "OFF"))
                else:
                    fails.append(("R-CUT-AFFORD", f"{s2}: {r.detail}"))
            except _NeedsData as nd:
                afford_pending = nd
    ev["stage0_failures"] = [{"clause": c, "detail": d} for c, d in fails]
    if not fails:
        if afford_pending is not None:
            raise afford_pending
        raise _Refuse("R-STAGE0-PASSES", f"{leg}'s Stage-0 record fails no clause of "
                                         f"{m['id']} -- the evidence does not support a cut")

    # ── the Stage-1 soak is complete ────────────────────────────────────────
    _soak_complete(leg, account, bar, root, ctx)


# --------------------------------------------------------------------------
# MD-SOAK-EXIT-CELL-PATHB -- a CONFIG edit, not a roster move (proposed
# 2026-09-30, pipeline row PI-20260929-AQRK6CL1-0013).
#
# It lives beside `resolve()` rather than inside `_decide()` on purpose: `_decide`
# is the ladder's roster-edit evaluator (adds/removes, stages, cost fidelity) and
# this mandate edits ONE key of `config/strategies.yaml` on a leg that stays
# where it is. Folding it in would have meant a fifth transition that is not a
# transition. `resolve()`, `TRANSITION_MANDATE` and every existing consumer
# (r4_demotion_gate, check_mandate_autoland) are untouched.
#
# ⚠️ A PROPOSED entry never authorizes. `granted()` reads only `mandates:`, so
# without `allow_proposed` this REFUSEs R-MANDATE-NOT-GRANTED exactly like every
# other mandate. `allow_proposed=True` is the DRY evaluation the operator needs
# to see what a grant would have done; it stamps `authorized: False` and the
# caveat into the result so a FIRE it returns cannot be read as a grant.
# --------------------------------------------------------------------------
EXIT_CELL_MANDATE_ID = "MD-SOAK-EXIT-CELL-PATHB"
BRACKET_CORPUS_REL = "docs/research/e35-bracket-corpus.jsonl"
#: The corpus `family` -> harness map that carries a venue-aware slippage
#: resolution (docs/research/d1-cost-stack-verification-2026-09-25.md). Anything
#: else -- and above all the four families that lack a full stack
#: (backtest_orb.py, src/backtest/backtester.py, backtest_xsec_momentum.py,
#: backtest_vol_target.py), none of which is reachable from a bracket-corpus
#: family today -- is REFUSEd, so a harness added later starts OUT of scope.
EXIT_CELL_COST_COMPLETE_FAMILIES = frozenset({"pullback", "donchian"})
PATHB_VERDICT = "path_b_wf_pass"
COVERAGE_MATRIX_REL = "docs/research/exit-refinement-coverage.json"


def _leg_rows(root: Path, leg: str) -> List[Dict[str, Any]]:
    """EVERY corpus row for `leg` (the audit needs the base and to400 arms)."""
    p = root / BRACKET_CORPUS_REL
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip() or leg not in line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("leg") == leg:
            rows.append(r)
    return rows


def _rank_key(r: Dict[str, Any]) -> tuple:
    """B4's rule, stated in advance and carried by the mandate: highest
    `wf_wins_effective`, tie-break `d_net_r`. A non-finite value ranks LAST (it is
    "could not read", not a big number), and a residual tie goes to the
    lexicographically smallest cell name so the choice is a total order, not an
    accident of file order."""
    def f(v: Any) -> float:
        return float(v) if _num(v) else float("-inf")
    return (-f(r.get("wf_wins_effective")), -f(r.get("d_net_r")), str(r.get("cell")))


def select_cells(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """leg -> the ONE top-ranked pure-stop `path_b_wf_pass` cell (its newest row).
    Single-homed here so the resolver and the runner cannot disagree."""
    newest: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        if (r.get("gate_verdict") != PATHB_VERDICT or r.get("axis") != "stop"
                or r.get("timeout") is not None or r.get("tp_r") is not None):
            continue
        k = (r.get("leg"), r.get("cell"))
        if k not in newest or str(r.get("sweep_generated_at") or "") > str(
                newest[k].get("sweep_generated_at") or ""):
            newest[k] = r
    best: Dict[str, Dict[str, Any]] = {}
    for (leg, _cell), r in newest.items():
        if leg not in best or _rank_key(r) < _rank_key(best[leg]):
            best[leg] = r
    return best


def timeout_binding(root: Path, leg: str) -> Dict[str, Any]:
    """Does the harness's timeout force-close CONTAMINATE this leg's verdicts?

    TWO INDEPENDENT READINGS, and the bound trips on EITHER (fail closed):
      * `audit`  -- scripts/research/timeout_binding_audit.audit() re-run over the
                    corpus rows NOW (the field): `contaminated` / `clean` /
                    `no_power`, or None if it could not run.
      * `matrix` -- the coverage matrix's own per-leg `timeout_binding` note
                    (a claim about the field): `CONTAMINATED` / `CLEAN` / None.
    ⚠️ THEY DISAGREE ON spy_pullback_1h (2026-09-30): the note says CONTAMINATED
    ("bound on 17 of 39 pairs"); audit() over BOTH the 08-29 and 08-31 sweeps says
    clean, 0 of 39. Neither is silently preferred: the union refuses.
    """
    out: Dict[str, Any] = {"audit": None, "binding": None, "graded_pairs": None, "matrix": None}
    try:
        sys.path.insert(0, str(REPO / "scripts" / "research"))
        import timeout_binding_audit as tba  # noqa: WPS433 (local: research module)
        per = tba.audit(_leg_rows(root, leg)).get(leg)
        if per:
            out.update(audit=per["verdict"], binding=per["binding"], graded_pairs=per["graded_pairs"])
    except Exception:  # noqa: BLE001 -- "could not audit" is its own answer, never a pass
        pass
    mp = root / COVERAGE_MATRIX_REL
    if mp.is_file():
        try:
            for row in json.loads(mp.read_text(encoding="utf-8")).get("rows", []):
                if row.get("strategy") == leg:
                    note = (row.get("bracket_geometry") or {}).get("timeout_binding")
                    m = re.search(r"leg (CONTAMINATED|CLEAN)", note) if isinstance(note, str) else None
                    out["matrix"] = m.group(1) if m else None
        except (OSError, json.JSONDecodeError):
            pass
    return out


def _load_corpus_rows(root: Path, leg: str, cell: str) -> List[Dict[str, Any]]:
    p = root / BRACKET_CORPUS_REL
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip() or leg not in line:  # cheap prefilter; exact match below
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("leg") == leg and r.get("cell") == cell:
            rows.append(r)
    return rows


def resolve_exit_cell(leg: str, cell: str, *, root: Path = REPO,
                      mandate_id: str = EXIT_CELL_MANDATE_ID,
                      allow_proposed: bool = False) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"leg": leg, "cell": cell, "mandate": mandate_id,
                           "from_stage": None, "to_stage": None, "account": None,
                           "authorized": None, "evidence": {}, "caveats": [], "proposal": None}
    try:
        _decide_exit_cell(leg, cell, root, mandate_id, allow_proposed, ctx)
    except _NeedsData as nd:
        ctx["proposal"] = None
        return _result(NEEDS_DATA, nd.clause, nd.detail, ctx, data_task=nd.data_task)
    except _Refuse as r:
        ctx["proposal"] = None
        return _result(REFUSE, r.clause, r.detail, ctx)
    return _result(FIRE, "ALL-CLAUSES-PASS", "every clause of the mandate held", ctx)


def _exit_cell_data_task(leg: str, cell: str, what: str, clears: str) -> Dict[str, Any]:
    return {"what": f"{leg} cell {cell}: {what}", "clears_when": clears,
            "check_every_days": 7, "next_action": "dispatch_lane"}


def _decide_exit_cell(leg: str, cell: str, root: Path, mid: str, allow_proposed: bool,
                      ctx: Dict[str, Any]) -> None:
    ev = ctx["evidence"]
    # -- which mandate, and is it granted -----------------------------------
    mdoc = _yaml(root, MANDATES_REL)
    if mdoc is None:
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{MANDATES_REL} is absent or unreadable -- "
                                               "could not look, so nothing is authorized")
    m = granted(mdoc, mid)
    ctx["authorized"] = m is not None
    if m is None and allow_proposed:
        m = next((x for x in (mdoc.get("proposed") or [])
                  if isinstance(x, dict) and x.get("id") == mid), None)
        if m is not None:
            ctx["caveats"].append(
                f"DRY EVALUATION under a PROPOSED entry: {mid} sits under `proposed:` and "
                "authorizes NOTHING. A FIRE here is what a grant WOULD have done.")
    if m is None:
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{mid} is not in the granted `mandates:` list "
                                               "(`proposed:` never authorizes)")
    if ctx["authorized"] and (not m.get("granted_by") or not m.get("granted_at")):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{mid} lacks granted_by/granted_at")
    if m.get("blocked_until"):
        raise _Refuse("R-MANDATE-BLOCKED", f"{mid} blocked_until: {m['blocked_until']}")
    bar = m.get("bar")
    need = ("min_oos_trades", "min_wf_usable", "min_wf_win_fraction",
            "max_dd_worsening_frac_of_base", "max_leverage_multiple")
    if not isinstance(bar, dict) or any(not _num(bar.get(k)) for k in need):
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{mid} does not state a numeric bar.{{{', '.join(need)}}}")
    if bar.get("refuse_timeout_contaminated") is not True:
        raise _Refuse("R-MANDATE-NOT-GRANTED", f"{mid} does not state bar.refuse_timeout_contaminated: true "
                                               "-- the timeout-binding bound is part of the grant")

    # -- clause 1: soak-only, read from accounts.yaml NOW --------------------
    accounts_text = (root / ACCOUNTS_REL).read_text(encoding="utf-8") \
        if (root / ACCOUNTS_REL).is_file() else None
    if accounts_text is None:
        raise _Refuse("R-SOAK-ONLY", f"{ACCOUNTS_REL} is absent -- could not look")
    rosters = b1.rosters(accounts_text)
    on = sorted(a for a, r in rosters.items() if leg in r["legs"])
    ev["rosters"] = on
    if not on:
        raise _Refuse("R-SOAK-ONLY", f"{leg} is on no account roster -- nothing soaks it")
    # ⚠️ NOT `account_class == paper`: bybit_portfolio and alpaca_portfolio are
    # class `paper` AND are the Stage-2 mirrors. The test is Stage-1 MEMBERSHIP.
    outside = [a for a in on if STAGE_OF_ACCOUNT.get(a) != "S1"
               or rosters[a]["class"] != STAGE_CLASS["S1"]]
    if outside:
        raise _Refuse("R-SOAK-ONLY", f"{leg} is also on {outside}, which is not a Stage-1 "
                                     f"paper soak account (Stage-1: bybit_1, alpaca_paper, ib_paper). "
                                     "This mandate never touches a leg present on a Stage-2 roster, "
                                     "a mirror, a prop account or any other account.")

    # -- clause 2: the corpus row -------------------------------------------
    rows = _load_corpus_rows(root, leg, cell)
    if not rows:
        raise _NeedsData("R-RECORD-MISSING", f"no {BRACKET_CORPUS_REL} row for {leg} cell {cell}",
                         _exit_cell_data_task(leg, cell, "no bracket-corpus row",
                                              f"a graded row for ({leg}, {cell}) is committed to {BRACKET_CORPUS_REL}"))
    graded = [r for r in rows if r.get("gate_verdict") is not None]
    bad = sorted({str(r.get("gate_verdict")) for r in graded} - {PATHB_VERDICT})
    if bad:  # reproducibility: any run that graded this cell differently vetoes it
        raise _Refuse("R-CORPUS-DISAGREES", f"{len(graded)} graded corpus rows for ({leg}, {cell}); "
                                            f"verdicts other than {PATHB_VERDICT}: {bad}")
    if not graded:
        raise _NeedsData("R-RECORD-MISSING", f"({leg}, {cell}) has {len(rows)} corpus rows and none was graded",
                         _exit_cell_data_task(leg, cell, "never reached the gate",
                                              f"a graded {PATHB_VERDICT} row exists"))
    row = max(graded, key=lambda r: str(r.get("sweep_generated_at") or ""))
    ev["record"] = BRACKET_CORPUS_REL
    ev["measurement_key"] = row.get("measurement_key")
    ev["source_run"] = row.get("source")
    ev["graded_rows"] = len(graded)
    if row.get("state") != "measured":
        raise _NeedsData("R-RECORD-MISSING", f"row state is {row.get('state')!r}, not 'measured'",
                         _exit_cell_data_task(leg, cell, "unmeasured row", "state == measured"))
    if row.get("family") not in EXIT_CELL_COST_COMPLETE_FAMILIES:
        raise _Refuse("R-COST-STACK", f"family {row.get('family')!r} is not in "
                                      f"{sorted(EXIT_CELL_COST_COMPLETE_FAMILIES)}, the harnesses verified to "
                                      "resolve slippage/funding through the venue-aware policy")
    if row.get("gate_path") != "B" or row.get("axis") != "stop" or row.get("timeout") is not None \
            or row.get("tp_r") is not None or not _num(row.get("stop_mult")):
        raise _Refuse("R-CELL-SHAPE", "the mandate covers a pure stop-multiplier cell "
                                      "(axis=stop, gate_path=B, no tp_r, no timeout -- no live unit "
                                      f"implements a bar-count exit); got axis={row.get('axis')!r} "
                                      f"path={row.get('gate_path')!r} tp_r={row.get('tp_r')!r} "
                                      f"timeout={row.get('timeout')!r}")

    # -- clause 2a: ONE cell per leg per fire, the top-ranked (B4, as granted) ----
    top = select_cells(_leg_rows(root, leg)).get(leg)
    ev["top_cell"] = top.get("cell") if top else None
    if top is None or top.get("cell") != cell:
        raise _Refuse("R-NOT-TOP-CELL", f"{cell} is not this leg's top-ranked gate-passing cell "
                                        f"({ev['top_cell']!r}; highest wf_wins_effective, tie-break d_net_r): "
                                        "one cell per leg per fire, chosen by the stated rule")

    # -- clause 2b: timeout-binding contamination (operator bound, 2026-09-30) --
    tb = timeout_binding(root, leg)
    ev["timeout_binding"] = tb
    if tb["audit"] == "contaminated" or tb["matrix"] == "CONTAMINATED":
        raise _Refuse("R-TIMEOUT-BINDING",
                      f"the harness's timeout force-close CONTAMINATES this leg's verdicts (audit "
                      f"{tb['audit']}, binding {tb['binding']}/{tb['graded_pairs']}; matrix note "
                      f"{tb['matrix']}); the cell was measured under an exit production does not have")
    if tb["audit"] != "clean":
        raise _NeedsData("R-TIMEOUT-BINDING", f"timeout-binding audit reads {tb['audit']!r} (matrix "
                                              f"{tb['matrix']}); 'could not look' is not 'clean'",
                         _exit_cell_data_task(leg, cell, "timeout-binding audit not gradeable",
                                              "timeout_binding_audit.audit() returns `clean` for the leg"))

    # -- clause 3: both windows, net R and capital efficiency ---------------
    try:
        d_is, d_oos = _fin(row["gate_is_d_net_r"]), _fin(row["gate_oos_d_net_r"])
        cap_pd = _fin(row["capital_oos_d_net_r_per_capital_day"])
    except (KeyError, TypeError, ValueError):
        raise _NeedsData("R-RECORD-MISSING", "IS/OOS d_net_r or capital_oos d_net_r_per_capital_day unreadable",
                         _exit_cell_data_task(leg, cell, "window deltas unreadable", "all three fields present"))
    if not (d_is > 0 and d_oos > 0 and cap_pd > 0):
        raise _Refuse("R-NET-R", f"needs d_net_r > 0 in BOTH windows and capital/day > 0; got "
                                 f"IS {d_is}, OOS {d_oos}, capital/day {cap_pd}")
    n_oos = row.get("base_oos_trades")
    if not _num(n_oos):
        raise _NeedsData("R-BASE-N", "base_oos_trades is null on the newest graded row -- the MIN_OOS_TRADES "
                                     "denominator cannot be read",
                         _exit_cell_data_task(leg, cell, "base_oos_trades null",
                                              "re-swept row carries base_oos_trades"))
    if n_oos < bar["min_oos_trades"]:
        raise _Refuse("R-BASE-N", f"base_oos_trades {n_oos} < {bar['min_oos_trades']}")

    # -- clause 4: the fold rule --------------------------------------------
    usable, wins = row.get("wf_usable"), row.get("wf_wins_effective")
    if not (row.get("wf_ran") and _num(usable) and _num(wins)):
        raise _NeedsData("R-FOLDS", "walk-forward did not run or its tallies are unreadable",
                         _exit_cell_data_task(leg, cell, "no walk-forward", "wf_ran with usable/wins present"))
    ev["walkforward"] = f"{wins}/{usable} (wf_wins_effective/wf_usable)"
    if usable < bar["min_wf_usable"] or wins < bar["min_wf_win_fraction"] * usable:
        raise _Refuse("R-FOLDS", f"{wins}/{usable} folds; need usable >= {bar['min_wf_usable']} and "
                                 f"wins >= {bar['min_wf_win_fraction']} x usable")

    # -- clause 5: drawdown --------------------------------------------------
    try:
        n_b, d_b = _fin(row["base_net_total_r"]), _fin(row["base_max_drawdown_r"])
        dn, dd = _fin(row["d_net_r"]), _fin(row["d_max_dd"])
    except (KeyError, TypeError, ValueError):
        raise _NeedsData("R-DRAWDOWN", "base/cell net_R or maxDD unreadable",
                         _exit_cell_data_task(leg, cell, "drawdown fields unreadable", "fields present"))
    if n_b <= 0 or d_b <= 0:
        raise _Refuse("R-DRAWDOWN", f"base net_R {n_b} / base maxDD {d_b}: the exchange rate is ungradeable "
                                    "on a losing or drawdown-free base, and ungradeable is not a pass")
    allowed = min(d_b * dn / n_b, d_b)  # m20_fleet_exit_sweep.drawdown_exchange_rate, incl. the grant cap
    frac = dd / d_b
    ev["drawdown"] = {"d_max_dd": dd, "base_max_dd": d_b, "frac_of_base": round(frac, 4),
                      "rate_allowance": round(allowed, 4)}
    if dd > allowed:
        raise _Refuse("R-DRAWDOWN", f"d_max_dd {dd} exceeds the exchange-rate allowance {allowed:.4f}")
    if frac > bar["max_dd_worsening_frac_of_base"]:
        raise _Refuse("R-DRAWDOWN", f"maxDD worsens by {frac:.3f} of the base drawdown; cap "
                                    f"{bar['max_dd_worsening_frac_of_base']} -- route to the operator")
    lev = (row.get("leverage") or {}).get("leverage_multiple")
    if not _num(lev):
        raise _NeedsData("R-LEVERAGE", "leverage_multiple unmeasured",
                         _exit_cell_data_task(leg, cell, "leverage unmeasured", "leverage.state == measured"))
    ev["leverage_multiple"] = lev
    if lev > bar["max_leverage_multiple"]:
        raise _Refuse("R-LEVERAGE", f"a tighter stop raises notional per unit risk by {lev}x; cap "
                                    f"{bar['max_leverage_multiple']}x")

    # -- the edit ------------------------------------------------------------
    strategies = (_yaml(root, STRATEGIES_REL) or {}).get("strategies") or {}
    cfg = strategies.get(leg) if isinstance(strategies, dict) else None
    if not isinstance(cfg, dict):
        raise _Refuse("R-NO-LEG", f"{leg} is not in {STRATEGIES_REL}")
    cur = cfg.get("atr_stop_mult")
    ev["current_atr_stop_mult"] = cur
    if _num(cur) and float(cur) == float(row["stop_mult"]):
        raise _Refuse("R-NO-OP", f"{leg} already declares atr_stop_mult {cur}")
    ctx["proposal"] = {
        "writes_nothing": True,
        "config_edit": {"file": STRATEGIES_REL, "leg": leg, "key": "atr_stop_mult",
                        "from": cur, "to": float(row["stop_mult"])},
        "also": ["docs/research/exit-refinement-coverage.json bracket_geometry cell -> shipped",
                 f"comms/strategy_evidence/{leg}.json regenerated by the harness, never hand-edited"],
        "landing": "hold",
        "pr_title": f"{mid}: {leg} atr_stop_mult {cur} -> {row['stop_mult']} (cell {cell})",
        "then": "ping in realtime; show the evidence in §1 of the next daily brief",
    }


def file_exit_cell_needs_data(entries: List[Dict[str, Any]], clause: str, session_ref: str,
                              store: Path = pipeline.STORE) -> Optional[str]:
    """ONE pipeline row per NEEDS-DATA *clause* (not per leg: nine legs sharing
    R-BASE-N are one cause), skipped when an OPEN row already names this mandate
    and that clause -- PI-20260930-39SDYWCO-0002 covers R-BASE-N today. Single-homed
    here; the CLI and scripts/ops/exit_cell_runner.py both call it."""
    for it in pipeline.load(store).items.values():
        w = str(it.get("what") or "")
        if it.get("state") in pipeline.OPEN_STATES and EXIT_CELL_MANDATE_ID in w and clause in w:
            return None
    legs = ", ".join(f"{e['leg']}:{e['cell']}" for e in entries)
    task = entries[0]["result"].get("data_task") or {}
    rerun = "python3 scripts/ops/exit_cell_runner.py --json"
    item = {"id": pipeline.mint_id(session_ref, store=store),
            "what": f"{EXIT_CELL_MANDATE_ID}: {len(entries)} cell(s) are NEEDS-DATA at {clause} -- "
                    f"{task.get('what') or entries[0]['detail']} [{legs}]",
            "origin": {"kind": "session", "ref": session_ref, "rerun": rerun},
            "due_when": {"kind": "observation",
                         "clears_when": f"{rerun} lists no cell NEEDS-DATA at {clause}",
                         "check_every_days": task.get("check_every_days", 7)},
            "next_action": task.get("next_action", "dispatch_lane"), "state": "queued"}
    return pipeline.append(item, store, intent="new")["id"]


# --------------------------------------------------------------------------
# NEEDS_DATA -> the pipeline (operator directive, 2026-09-29): "getting that
# data becomes a task which needs to happen so that a decision can be made".
# --------------------------------------------------------------------------
def needs_data_pipeline_item(result: Dict[str, Any], session_ref: str) -> Dict[str, Any]:
    """Build (never write) a `scripts/ops/pipeline.py` item for a NEEDS_DATA
    result. Kept separate from `file_needs_data()` so every caller that only
    wants to INSPECT the item this would file -- every test in this suite
    included -- never touches the filesystem.

    The item's `due_when` is an `observation` whose `clears_when` is
    `result["data_task"]["clears_when"]` -- the exact condition named by the
    clause that could not decide, never a generic "check on this leg". Its
    `origin.rerun` is the exact resolver invocation that produced this
    result, so a later session (or A3, once it exists) can re-ask the same
    question rather than trusting this sentence.
    """
    if result.get("verdict") != NEEDS_DATA:
        raise ValueError(f"needs_data_pipeline_item: not a NEEDS_DATA result "
                         f"(verdict={result.get('verdict')!r})")
    task = result.get("data_task") or {}
    leg, mid = result["leg"], result["mandate"]
    frm, to, account = result["from_stage"], result["to_stage"], result["account"]
    rerun = (f"python3 scripts/ops/mandate_resolver.py --leg {leg} --from {frm} "
            f"--to {to} --account {account} --json")
    return {
        "id": pipeline.mint_id(session_ref),
        "what": (f"{mid}: {leg} ({frm} -> {to}, {account}) is NEEDS-DATA at "
                f"{result['clause']} -- {task.get('what') or result['detail']}"),
        "origin": {"kind": "session", "ref": session_ref, "rerun": rerun},
        "due_when": {"kind": "observation",
                    "clears_when": task.get("clears_when") or
                                   f"{rerun} no longer returns NEEDS-DATA at {result['clause']}",
                    "check_every_days": task.get("check_every_days", 7)},
        "next_action": task.get("next_action", "check_observation"),
        "state": "queued",
    }


def file_needs_data(result: Dict[str, Any], session_ref: str, *,
                    store: Path = pipeline.STORE) -> Dict[str, Any]:
    """Side-effecting: append the NEEDS_DATA item this result implies."""
    item = needs_data_pipeline_item(result, session_ref)
    return pipeline.append(item, store, intent="new")


# --------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leg", required=True)
    ap.add_argument("--from", dest="frm", choices=STAGES)
    ap.add_argument("--to", choices=STAGES)
    ap.add_argument("--account")
    ap.add_argument("--exit-cell", metavar="CELL", default=None,
                    help=f"evaluate {EXIT_CELL_MANDATE_ID} for this bracket-corpus cell (a config "
                         "edit, not a roster move); --from/--to/--account are not used")
    ap.add_argument("--dry-proposed", action="store_true",
                    help="with --exit-cell: evaluate a `proposed:` entry (authorizes nothing)")
    ap.add_argument("--mandate", default=None, help="evaluate under this mandate id instead of "
                                                     "the one the transition implies")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--file-needs-data", metavar="SESSION_REF", default=None,
                    help="on a NEEDS-DATA verdict, file the data task into "
                         "scripts/ops/pipeline.py under this session ref")
    a = ap.parse_args(argv)
    if a.exit_cell:
        res = resolve_exit_cell(a.leg, a.exit_cell, root=Path(a.root),
                                mandate_id=a.mandate or EXIT_CELL_MANDATE_ID,
                                allow_proposed=a.dry_proposed)
        if res["verdict"] == NEEDS_DATA and a.file_needs_data:
            pid = file_exit_cell_needs_data(
                [{"leg": a.leg, "cell": a.exit_cell, "detail": res["detail"], "result": res}],
                res["clause"], a.file_needs_data)
            print(f"needs-data row: {pid or 'none filed (an open row already names this clause)'}",
                  file=sys.stderr)
    elif not (a.frm and a.to and a.account):
        ap.error("--from, --to and --account are required unless --exit-cell is given")
    else:
        res = resolve(a.leg, a.frm, a.to, a.account, root=Path(a.root), mandate_id=a.mandate)
    filed = None
    if res["verdict"] == NEEDS_DATA and a.file_needs_data and not a.exit_cell:  # exit-cell files above
        filed = file_needs_data(res, a.file_needs_data)
    if a.json:
        out = dict(res)
        if filed is not None:
            out["filed_pipeline_id"] = filed["id"]
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
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
        if res.get("data_task"):
            print(f"  data task: {res['data_task'].get('what')}")
            print(f"  clears when: {res['data_task'].get('clears_when')}")
        if filed is not None:
            print(f"  filed: {filed['id']}")
    return {FIRE: 0, REFUSE: 1, NEEDS_DATA: 3}[res["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
