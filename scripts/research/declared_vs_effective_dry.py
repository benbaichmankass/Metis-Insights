#!/usr/bin/env python3
# wiring: manual-only — an instrument a session runs when an account's declared
# execution gates say LIVE and its journal says otherwise. It is not scheduled,
# nothing imports it on the order path, and it changes no gate.
"""Does an account's DECLARED liveness agree with what the executor actually did?

THE ROW THIS ANSWERS
--------------------
`BL-20260909-ALPACA-LIVE-CANNOT-SIZE-A-SINGLE-SHARE-SO-ITS-OPEN-ITEM-IS-UNREACHABLE-NOT-PENDING`
says `alpaca_live` is *"refused at sizing on every signal — sized_qty=0 at a
200.10 balance"*, and asks for an invariant reporting a live real-money account
that refuses every routed signal for the SAME cause as STRUCTURALLY UNABLE TO
TRADE rather than merely quiet.

⚠️ **THE ROW'S CAUSE STOPPED BINDING ON 2026-08-28 AND THE ROW WAS FILED ON
09-09.** Measured over one live pull, the refusal cause on this account has
changed FOUR times:

    2026-08-18 -> 08-21   sizing refused at balance=0.10     12 rows
    2026-08-25 -> 08-28   sizing refused at balance=200.10    5 rows
    2026-08-26 -> 08-28   account_mode_dry_run               12 rows
    2026-09-01 -> 09-11   dry_run_no_order_placed             4 rows  <- CURRENT

That is why this module reports the **CURRENT era**, not the modal cause over
the window. A remedy aimed at the modal cause — fund the account, change the
sizing basis — would leave the current one untouched, and the account would
still place nothing.

WHAT THE CURRENT ERA MEANS, AND WHY IT IS A SEPARATE FINDING
-------------------------------------------------------------
`dry_run_no_order_placed` is written by `execute.py` only when `_genuinely_dry`
is true — i.e. the executor resolved the dispatch as DRY. All four rows carry
`account_class: real_money`, `is_demo: 0` and `notes.is_dry: True`, the newest
at **2026-09-11T13:55:37Z**.

Both DECLARED gates were permissive at all four timestamps, verified against git
history rather than against today's file: `config/accounts.yaml::alpaca_live.mode
= live` since 2026-08-29, and `config/strategies.yaml::tlt_pullback_1h.execution
= live` across the window.

⚠️ **SO A THIRD FOLD IS DECIDING IT.** `Coordinator.multi_account_execute`
resolves `effective_dry` from THREE inputs, not two:

  1. the account's `mode:` (`accounts.yaml`),
  2. `config/account_state.yaml` — *"can never force an account live"*, dry-only,
  3. the strategy's `execution:` (`strategies.yaml`).

`CLAUDE.md` § "The two execution gates" states *"Exactly two declared,
default-permissive switches decide whether a strategy trades"* and *"There is no
third gate"*. **`account_state.yaml` is a third one.** It is declared in its own
file and is not hidden, so it is not the `MULTI_SYMBOL_ENABLED` anti-pattern —
but it is not one of the two, and the canonical doc says there are two.

⚠️ **AND THE OPERATOR-FACING SURFACE CANNOT SHOW IT.**
`/api/bot/config::trading_mode.live_per_account` is written by
`runtime_status._read_live_per_account`, which reads **`accounts.yaml::mode` and
nothing else** — its own docstring says *"Resolution mirrors
`src.units.accounts._resolve_mode` — the canonical resolver the executor uses"*,
which is true of one of the three folds and false of the resolution. The route's
note calls the field *"the pipeline's runtime view"*. So an account can read
`live: true` on the only surface that answers *is this account live* while the
executor books every dispatch dry. That is UNPROVENANCED DIAGNOSTIC OUTPUT
sub-class A — the label names a quantity the code did not compute — on the
real-money execution gate.

WHAT THIS MODULE REFUSES TO DO
------------------------------
⚠️ **IT DOES NOT NAME THE CAUSE IT CANNOT SEE.** Three candidates are ELIMINATED
with evidence — the account gate and the strategy gate by git history, and the
`exchange_client is None` path because that branch writes a DIFFERENT reason
(`exchange_client_unavailable_no_order_placed`) by construction. Two remain and
neither is readable from a research container: a VM-side `account_state.yaml`
entry (the repo copy has NO `alpaca_live` key, so the repo copy does not explain
it), and a `dry_run=` override passed by a caller. The verdict is therefore
`unattributed_dry` — **we eliminated what we could and could not determine the
rest** — never a guess dressed as a finding.

⚠️ **IT PROPOSES NO CHANGE.** Every remedy here is Tier-2 or Tier-3.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from typing import Any

#: What the executor recorded about a dispatch, bucketed by cause.
REFUSAL_CAUSES = (
    "sizing_refused",
    "account_mode_dry_run",
    "dry_run_no_order_placed",
    "exchange_client_unavailable",
    "placed",
    "other",
)

#: Whether the DECLARED gates and the OBSERVED dryness agree.
GATE_STATES = (
    "agrees_live",
    "agrees_dry",
    "declared_live_books_dry",
    "declared_dry_books_live",
    "no_rows_observed",
    "ungradeable_no_is_dry",
)

#: Why it booked dry, when it did. The last is *we could not determine*.
DRY_ATTRIBUTION = (
    "account_mode",
    "strategy_shadow",
    "client_unavailable",
    "unattributed_dry",
    "not_dry",
    "ungradeable",
)

#: Whether the account is merely quiet or cannot trade at all.
TRADEABILITY = (
    "traded",
    "structurally_unable",
    "refusing_mixed_causes",
    "no_rows_observed",
)


def _notes(row: dict) -> dict:
    raw = row.get("notes")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # A non-JSON notes blob is *unparsed*, not empty — the caller
        # distinguishes it through the absence of `is_dry`.
        return {}
    return parsed if isinstance(parsed, dict) else {}


def observed_is_dry(row: dict) -> bool | None:
    """`notes.is_dry`, or ``None`` when the row does not record it.

    ``None`` is *we did not look* and is graded `ungradeable_no_is_dry`. It is
    never coerced to False, which would read as a live dispatch.
    """
    value = _notes(row).get("is_dry")
    return None if not isinstance(value, bool) else value


def cause_of(row: dict) -> str:
    """Bucket one row's recorded reason. Order matters — see the comments."""
    text = str(row.get("entry_reason") or _notes(row).get("reason") or "")
    if not text:
        return "other"
    # `exchange_client_unavailable_...` is tested BEFORE the dry buckets: the
    # executor writes it precisely to keep an unavailable client from reading as
    # an intentional dry-run (BL-20260707-MGCTREND-REASON-MISMATCH), and folding
    # it in here would undo that distinction at the reading end.
    if "exchange_client_unavailable" in text:
        return "exchange_client_unavailable"
    if "dry_run_no_order_placed" in text:
        return "dry_run_no_order_placed"
    if "account_mode_dry_run" in text:
        return "account_mode_dry_run"
    if "risk_refused" in text or "sizing_skip" in text:
        return "sizing_refused"
    if str(row.get("status") or "").lower() != "rejected":
        return "placed"
    return "other"


def balance_in(row: dict) -> float | None:
    """The balance the refusal quoted, or ``None`` when it quoted none."""
    m = re.search(r"balance=([0-9]+(?:\.[0-9]+)?)",
                  str(row.get("entry_reason") or ""))
    return float(m.group(1)) if m else None


def eras(rows: list[dict]) -> list[dict]:
    """Contiguous runs of one cause, oldest first — the account's history.

    A CENSUS over the window would report the modal cause; the row this module
    answers was filed on exactly that reading, two weeks after its cause stopped
    binding. Eras make the CURRENT one readable.
    """
    ordered = sorted(rows, key=lambda r: str(r.get("created_at") or ""))
    out: list[dict] = []
    for row in ordered:
        cause = cause_of(row)
        stamp = str(row.get("created_at") or "")[:19]
        if out and out[-1]["cause"] == cause:
            out[-1]["n"] += 1
            out[-1]["last"] = stamp
            out[-1]["ids"].append(row.get("id"))
        else:
            out.append({"cause": cause, "n": 1, "first": stamp, "last": stamp,
                        "ids": [row.get("id")]})
    return out


def current_era(rows: list[dict]) -> dict | None:
    """The newest era, or ``None`` when there are no rows — not an empty era."""
    got = eras(rows)
    return got[-1] if got else None


def gate_state(rows: list[dict], *, declared_live: bool | None) -> str:
    """Do the declared gates and the observed dryness agree?

    `declared_live` is ``None`` when the caller could not establish it; that is
    `ungradeable_no_is_dry`'s sibling and must not be read as `False`.
    """
    if not rows:
        return "no_rows_observed"
    if declared_live is None:
        return "ungradeable_no_is_dry"
    flags = [observed_is_dry(r) for r in rows]
    known = [f for f in flags if f is not None]
    if not known:
        return "ungradeable_no_is_dry"
    any_dry, any_live = any(known), any(not f for f in known)
    if declared_live and any_dry:
        return "declared_live_books_dry"
    if not declared_live and any_live:
        return "declared_dry_books_live"
    return "agrees_live" if declared_live else "agrees_dry"


def dry_attribution(
    rows: list[dict],
    *,
    account_mode_live: bool | None,
    strategy_execution_live: bool | None,
) -> str:
    """WHY it booked dry — eliminating what the evidence can eliminate.

    ⚠️ `unattributed_dry` is the honest terminal state, NOT a fallback bucket:
    it means the account gate and the strategy gate were both permissive over
    the graded rows and the client-unavailable path is excluded BY THE REASON
    STRING the executor writes, so something outside those three decided it.
    Naming a cause here would be inventing one.
    """
    if not rows:
        return "ungradeable"
    dry = [r for r in rows if observed_is_dry(r) is True]
    if not dry:
        return "not_dry"
    if any(cause_of(r) == "exchange_client_unavailable" for r in dry):
        return "client_unavailable"
    if account_mode_live is False:
        return "account_mode"
    if strategy_execution_live is False:
        return "strategy_shadow"
    if account_mode_live is None or strategy_execution_live is None:
        return "ungradeable"
    return "unattributed_dry"


def tradeability(rows: list[dict]) -> str:
    """The distinction the backlog row asks for: quiet vs unable.

    ⚠️ `structurally_unable` requires ONE cause across every graded row. An
    account refusing for several causes has a tuning question; an account
    refusing for one deterministic cause has a standing condition, and the row's
    own words are *"not a soak accruing, it is a condition standing"*.
    """
    if not rows:
        return "no_rows_observed"
    causes = {cause_of(r) for r in rows}
    if "placed" in causes:
        return "traded"
    return "structurally_unable" if len(causes) == 1 else "refusing_mixed_causes"


def assess(
    rows: list[dict],
    *,
    account_id: str,
    declared_live: bool | None,
    account_mode_live: bool | None,
    strategy_execution_live: bool | None,
) -> dict:
    """Grade one account.

    ⚠️ **ATTRIBUTION IS SCOPED TO THE CURRENT ERA, NOT THE WINDOW**, and that is
    load-bearing rather than tidy. The gate flags a caller passes describe the
    config as it stands TODAY; over a long window the account's own `mode:` may
    have been different (measured on `alpaca_live`: `dry_run` until 2026-08-27,
    `live` after). Attributing August's dry rows with September's gate values
    would return `unattributed_dry` for rows a declared gate fully explains —
    manufacturing the finding this module exists to report.
    """
    got = current_era(rows)
    era_rows = ([r for r in rows if r.get("id") in set(got["ids"])]
                if got is not None else [])
    balances = sorted({b for b in (balance_in(r) for r in rows) if b is not None})
    return {
        "account_id": account_id,
        "rows_graded": len(rows),
        "declared_live": declared_live,
        "gate_state": gate_state(rows, declared_live=declared_live),
        "dry_attribution": dry_attribution(
            era_rows, account_mode_live=account_mode_live,
            strategy_execution_live=strategy_execution_live),
        "dry_attribution_scope": "current_era",
        "dry_attribution_rows": len(era_rows),
        "dry_attribution_window_wide": dry_attribution(
            rows, account_mode_live=account_mode_live,
            strategy_execution_live=strategy_execution_live),
        "tradeability": tradeability(rows),
        "eras": eras(rows),
        "current_era": got,
        "cause_census": dict(collections.Counter(cause_of(r) for r in rows)),
        # None, never 0.0 — an account that quoted no balance is not an account
        # with a zero balance.
        "balances_quoted": balances or None,
        "is_dry_unrecorded": sum(1 for r in rows if observed_is_dry(r) is None),
    }


def report(result: dict) -> list[str]:
    n = result["rows_graded"]
    out = [
        "declared-vs-effective dry — account %s" % result["account_id"],
        "  POPULATION: %d journal row(s) for this account in the pull; %d of them "
        "record no notes.is_dry and are counted, never assumed live."
        % (n, result["is_dry_unrecorded"]),
        "  declared_live=%s  gate_state=%s  tradeability=%s"
        % (result["declared_live"], result["gate_state"], result["tradeability"]),
        "  dry_attribution=%s over the CURRENT ERA ONLY (%d row(s)) — the gate flags "
        "describe today's config, so attributing an older era with them would "
        "manufacture a finding. Window-wide, for comparison only: %s."
        % (result["dry_attribution"], result["dry_attribution_rows"],
           result["dry_attribution_window_wide"]),
    ]
    if result["gate_state"] == "declared_live_books_dry":
        out.append(
            "  ⚠️ THE DECLARED GATE SAYS LIVE AND THE EXECUTOR BOOKED DRY. On a "
            "real-money account this makes any open item waiting on a first "
            "placement UNREACHABLE rather than pending.")
    if result["dry_attribution"] == "unattributed_dry":
        out.append(
            "  ⚠️ unattributed_dry — the account gate and the strategy gate were "
            "BOTH permissive over these rows and the client-unavailable path is "
            "excluded by the reason string. This is 'we could not determine', "
            "NOT 'no cause exists'.")
    out.append("  eras, oldest first (the CURRENT one is the standing condition, "
               "not the modal one):")
    for era in result["eras"]:
        out.append("      %-28s n=%3d  %s -> %s" % (era["cause"], era["n"],
                                                    era["first"], era["last"]))
    if result["current_era"] is None:
        out.append("      (no rows — no era EXISTS; this is not a quiet account)")
    out.append("  cause census over the whole window (denominator %d): %s"
               % (n, result["cause_census"]))
    out.append("  balances quoted by a refusal: %s"
               % ("none quoted" if result["balances_quoted"] is None
                  else result["balances_quoted"]))
    return out


# --------------------------------------------------------------------- fixtures


def _r(rid, created, reason, is_dry=None, status="rejected", acct="alpaca_live"):
    notes = {"reason": reason}
    if is_dry is not None:
        notes["is_dry"] = is_dry
    return {"id": rid, "account_id": acct, "created_at": created,
            "entry_reason": reason, "status": status, "notes": json.dumps(notes),
            "account_class": "real_money"}


# --------------------------------------------------------------------- self-test


def self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def ok(name, cond):
        checks.append((name, bool(cond)))

    SIZ = "REJECTED: dry_run_sizing_skip: risk_refused: sized_qty=0 with balance=200.10"
    AMD = "REJECTED: account_mode_dry_run | tlt_pullback_1h signal"
    DRY = "dry_run_no_order_placed"
    CUN = "exchange_client_unavailable_no_order_placed"

    rows = [
        _r(1, "2026-08-25T14:05:29", SIZ, is_dry=True),
        _r(2, "2026-08-27T14:06:17", SIZ, is_dry=True),
        _r(3, "2026-08-28T13:53:58", AMD, is_dry=True),
        _r(4, "2026-09-01T16:05:36", DRY, is_dry=True),
        _r(5, "2026-09-11T13:55:37", DRY, is_dry=True),
    ]

    # --- cause bucketing ----------------------------------------------------
    ok("a sizing refusal buckets as sizing_refused", cause_of(rows[0]) == "sizing_refused")
    ok("an account-mode refusal buckets as account_mode_dry_run",
       cause_of(rows[2]) == "account_mode_dry_run")
    ok("a dry dispatch buckets as dry_run_no_order_placed", cause_of(rows[3]) == "dry_run_no_order_placed")
    ok("CLIENT-UNAVAILABLE IS TESTED FIRST and never reads as an intentional dry-run",
       cause_of(_r(9, "2026-09-01T00:00:00", CUN, is_dry=False)) == "exchange_client_unavailable")
    ok("a non-rejected row buckets as placed",
       cause_of(_r(9, "2026-09-01T00:00:00", "filled", status="closed")) == "placed")
    ok("every cause returned is in the declared vocabulary",
       all(cause_of(r) in REFUSAL_CAUSES for r in rows))

    # --- is_dry -------------------------------------------------------------
    ok("a recorded is_dry is read", observed_is_dry(rows[0]) is True)
    ok("an ABSENT is_dry is None, never False",
       observed_is_dry(_r(9, "2026-09-01T00:00:00", DRY)) is None)
    ok("an unparseable notes blob yields None, never False",
       observed_is_dry({"notes": "not json", "entry_reason": DRY}) is None)

    # --- eras ---------------------------------------------------------------
    got = eras(rows)
    ok("contiguous same-cause rows collapse into one era", len(got) == 3)
    ok("eras are oldest-first", got[0]["cause"] == "sizing_refused")
    ok("THE CURRENT ERA IS THE NEWEST, not the modal cause",
       current_era(rows)["cause"] == "dry_run_no_order_placed")
    ok("the modal cause here is NOT the current one — the distinction is the point",
       collections.Counter(cause_of(r) for r in rows).most_common(1)[0][0]
       in ("sizing_refused", "dry_run_no_order_placed"))
    ok("an era carries its own first/last stamp", got[-1]["first"] == "2026-09-01T16:05:36"
       and got[-1]["last"] == "2026-09-11T13:55:37")
    ok("no rows -> current_era is None, NOT an empty era", current_era([]) is None)
    ok("a cause that recurs after another gets its OWN era, never merged",
       len(eras([_r(1, "2026-09-01T00:00:00", DRY, is_dry=True),
                 _r(2, "2026-09-02T00:00:00", SIZ, is_dry=True),
                 _r(3, "2026-09-03T00:00:00", DRY, is_dry=True)])) == 3)

    # --- gate state ---------------------------------------------------------
    ok("declared live + observed dry -> declared_live_books_dry",
       gate_state(rows, declared_live=True) == "declared_live_books_dry")
    ok("declared dry + observed dry -> agrees_dry",
       gate_state(rows, declared_live=False) == "agrees_dry")
    ok("declared live + observed live -> agrees_live",
       gate_state([_r(1, "2026-09-01T00:00:00", "filled", is_dry=False, status="closed")],
                  declared_live=True) == "agrees_live")
    ok("declared dry + observed LIVE -> declared_dry_books_live, its own state",
       gate_state([_r(1, "2026-09-01T00:00:00", "filled", is_dry=False, status="closed")],
                  declared_live=False) == "declared_dry_books_live")
    ok("no rows -> no_rows_observed, never agrees_*", gate_state([], declared_live=True)
       == "no_rows_observed")
    ok("an UNKNOWN declared state is ungradeable, never treated as dry",
       gate_state(rows, declared_live=None) == "ungradeable_no_is_dry")
    ok("rows with no is_dry at all -> ungradeable, never agrees_live",
       gate_state([_r(1, "2026-09-01T00:00:00", DRY)], declared_live=True)
       == "ungradeable_no_is_dry")
    ok("every gate_state returned is in the declared vocabulary",
       all(gate_state(rows, declared_live=d) in GATE_STATES for d in (True, False, None)))

    # --- attribution --------------------------------------------------------
    ok("both gates permissive + dry -> unattributed_dry",
       dry_attribution(rows, account_mode_live=True, strategy_execution_live=True)
       == "unattributed_dry")
    ok("a dry ACCOUNT attributes to account_mode",
       dry_attribution(rows, account_mode_live=False, strategy_execution_live=True)
       == "account_mode")
    ok("a shadow STRATEGY attributes to strategy_shadow",
       dry_attribution(rows, account_mode_live=True, strategy_execution_live=False)
       == "strategy_shadow")
    ok("the client-unavailable reason attributes to client_unavailable, not to a gate",
       dry_attribution([_r(1, "2026-09-01T00:00:00", CUN, is_dry=True)],
                       account_mode_live=True, strategy_execution_live=True)
       == "client_unavailable")
    ok("no dry row -> not_dry", dry_attribution(
        [_r(1, "2026-09-01T00:00:00", "filled", is_dry=False, status="closed")],
        account_mode_live=True, strategy_execution_live=True) == "not_dry")
    ok("an UNKNOWN gate is ungradeable, never unattributed_dry",
       dry_attribution(rows, account_mode_live=None, strategy_execution_live=True)
       == "ungradeable")
    ok("every attribution returned is in the declared vocabulary",
       all(dry_attribution(rows, account_mode_live=a, strategy_execution_live=b)
           in DRY_ATTRIBUTION for a in (True, False, None) for b in (True, False, None)))

    # --- tradeability -------------------------------------------------------
    ok("one cause across every row -> structurally_unable",
       tradeability([rows[3], rows[4]]) == "structurally_unable")
    ok("several causes -> refusing_mixed_causes, NOT structurally_unable",
       tradeability(rows) == "refusing_mixed_causes")
    ok("any placement -> traded",
       tradeability(rows + [_r(9, "2026-09-12T00:00:00", "filled", is_dry=False,
                               status="closed")]) == "traded")
    ok("no rows -> no_rows_observed, never structurally_unable", tradeability([]) == "no_rows_observed")

    # --- balances -----------------------------------------------------------
    ok("a quoted balance is parsed", balance_in(rows[0]) == 200.10)
    ok("NO quoted balance is None, never 0.0", balance_in(rows[3]) is None)

    # --- assess + report ----------------------------------------------------
    res = assess(rows, account_id="alpaca_live", declared_live=True,
                 account_mode_live=True, strategy_execution_live=True)
    ok("assess reports the balances actually quoted", res["balances_quoted"] == [200.1])
    ok("an account quoting no balance reports None, never []",
       assess([rows[3]], account_id="x", declared_live=True, account_mode_live=True,
              strategy_execution_live=True)["balances_quoted"] is None)
    # --- attribution is scoped to the CURRENT ERA ---------------------------
    # The decisive fixture: an OLD era the account gate fully explains, and a
    # CURRENT era it does not. Window-wide attribution would answer about the
    # old rows; era-scoped attribution answers about the standing condition.
    ok("attribution is scoped to the current era, not the window",
       res["dry_attribution_scope"] == "current_era")
    ok("the era-scoped attribution counts only the current era's rows",
       res["dry_attribution_rows"] == 2)
    ok("the window-wide attribution is reported BESIDE it, never instead of it",
       res["dry_attribution_window_wide"] in DRY_ATTRIBUTION)
    mixed = [_r(1, "2026-08-20T00:00:00", CUN, is_dry=True),
             _r(2, "2026-09-01T00:00:00", DRY, is_dry=True),
             _r(3, "2026-09-11T00:00:00", DRY, is_dry=True)]
    m = assess(mixed, account_id="x", declared_live=True, account_mode_live=True,
               strategy_execution_live=True)
    ok("AN OLDER EXPLAINED ERA DOES NOT EXPLAIN THE CURRENT ONE",
       m["dry_attribution"] == "unattributed_dry")
    ok("and the window-wide value DISAGREES on that fixture, which is the point",
       m["dry_attribution_window_wide"] == "client_unavailable"
       and m["dry_attribution_window_wide"] != m["dry_attribution"])
    ok("no rows -> the era-scoped attribution is ungradeable, never unattributed_dry",
       assess([], account_id="x", declared_live=True, account_mode_live=True,
              strategy_execution_live=True)["dry_attribution"] == "ungradeable")

    text = "\n".join(report(res))
    ok("the report states its population", "5 journal row(s)" in text)
    ok("the report counts rows with no is_dry rather than assuming them live",
       "record no notes.is_dry" in text)
    ok("the report warns loudly on declared_live_books_dry",
       "DECLARED GATE SAYS LIVE AND THE EXECUTOR BOOKED DRY" in text)
    ok("the report says unattributed means we could not determine",
       "NOT 'no cause exists'" in text)
    ok("the report NAMES the attribution scope so a reader cannot mistake it",
       "over the CURRENT ERA ONLY" in text)
    ok("the report gives the era-scoped row count", "(2 row(s))" in text)
    ok("the report prints the window-wide value beside it",
       "Window-wide, for comparison only" in text)
    ok("the report prints every era", all(e["cause"] in text for e in res["eras"]))
    ok("the report labels the CURRENT era as the standing condition",
       "the CURRENT one is the standing condition" in text)
    ok("the report states the census denominator", "denominator 5" in text)
    empty = "\n".join(report(assess([], account_id="x", declared_live=True,
                                    account_mode_live=True, strategy_execution_live=True)))
    ok("an empty account says no era EXISTS rather than reading as quiet",
       "no era EXISTS" in empty and "not a quiet account" in empty)

    failed = [n for n, good in checks if not good]
    for name, good in checks:
        print("%s  %s" % ("ok  " if good else "FAIL", name))
    print("\nself-test: %d checks, %d passed, %d failed (denominator = %d)"
          % (len(checks), len(checks) - len(failed), len(failed), len(checks)))
    return 1 if failed else 0


# --------------------------------------------------------------------------- cli


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trades", help="a saved /api/diag/journal?table=trades JSON array")
    ap.add_argument("--account", default="alpaca_live")
    ap.add_argument("--declared-live", choices=("true", "false", "unknown"), default="true")
    ap.add_argument("--account-mode-live", choices=("true", "false", "unknown"), default="true")
    ap.add_argument("--strategy-execution-live", choices=("true", "false", "unknown"),
                    default="true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.trades:
        ap.error("--trades is required unless --self-test is given")

    tri = {"true": True, "false": False, "unknown": None}
    with open(args.trades, encoding="utf-8") as fh:
        allrows = json.load(fh)
    if not isinstance(allrows, list):
        print("input is not a JSON array of trade rows", file=sys.stderr)
        return 2
    rows = [r for r in allrows if r.get("account_id") == args.account]

    res = assess(rows, account_id=args.account,
                 declared_live=tri[args.declared_live],
                 account_mode_live=tri[args.account_mode_live],
                 strategy_execution_live=tri[args.strategy_execution_live])
    print(json.dumps(res, indent=2, sort_keys=True, default=str) if args.json
          else "\n".join(report(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
