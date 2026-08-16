#!/usr/bin/env python3
"""collapsed-state-guard — a declared three-state contract must stay three-state.

THE CLASS (docs/CLAUDE-RULES-CANONICAL.md § "Collapsed states"): two distinct
conditions share one value, and the missing one is the dangerous one. Five
instances in two days across two concurrent sessions
(BL-20260809-COLLAPSED-STATES-NO-CANONICAL-HOME):

  * gross-exposure ceiling   — "no policy declared" == "no data"        (#8665)
  * netting allowlist        — "not staged for writes" == "not observed" (#8666)
  * pairs executor           — "exactly one leg open" == "flat"          (#8667)
  * harness cost basis       — None "unresolved" == 0.0 "fee-only"       (#8685)
  * exit-refinement coverage — "live" == "validated"                     (#8687)

The remedy already existed in ONE module — src/runtime/exit_anchor.py, whose
anchored/deferred/no_anchor docstring says outright that collapsing any two
reintroduces a defect — and was rediscovered incident-by-incident everywhere
else. This guard generalises it.

WHAT IS CHECKED, and why this shape. The guard is the sibling of
`provenance-consumer-guard`, whose insight is that a signal WRITTEN and never
READ is worse than a missing one, because reviewers see the field and assume
something acts on it. A state is exactly that: producing `deferred` and having
no consumer branch on it means every caller is treating it as one of the other
two. So, per declared contract:

  1. PRODUCER INTEGRITY — every declared state literally appears in the
     producing module. A contract naming a state its own module never emits is
     a dead claim.
  2. CONSUMER COVERAGE — every declared state is branched on by at least one
     consumer somewhere in the repo. A state nothing reads IS the collapse.
  3. NO SINGLE-STATE CONSUMER — a file that consumes the contract must
     reference >= 2 of its states. Branching on one state and letting the
     other two fall into a single `else` is the defect in miniature.

THE OVERRIDE IS VERIFIED, NOT PRESENCE-ONLY. A file may opt out of (3) with

    # collapsed-state: <state> — <why this site legitimately sees only one>

but the named `<state>` must be one of the contract's declared states AND the
annotation line is excluded from its own evidence. This is the direct lesson
from `new-table-wiring-guard`, whose presence-only marker made the cheapest way
to silence a real finding naming a table that did not exist: a guard cheaper to
lie to than to satisfy is worse than no guard.

Usage:
    python3 scripts/ci/check_collapsed_states.py [--verbose]

Exit 0 clean, 1 on a finding. Tier-1 CI tooling; reads the repo, writes nothing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# The declared registry. Adding a three-state contract here is how it becomes
# enforced. Keep `states` to the literal tokens the code emits/branches on.
# ---------------------------------------------------------------------------
CONTRACTS: List[Dict[str, object]] = [
    {
        "name": "db_explorer.filter_state",
        "producer": "src/web/api/routers/db_explorer.py",
        "producer_field": "filter_state",
        "consumer_token": r"\bfilter_state\b|\bdb_table\b|\bdb/table\b",
        "states": ["applied", "not_requested", "ignored_unknown_column"],
        "why": (
            "applied = a WHERE ran and `total` is a FILTERED count; "
            "not_requested = no filter was sent; ignored_unknown_column = a "
            "filter WAS sent and DROPPED, so `total` is the WHOLE TABLE. "
            "Collapsing the last two into the first is not a cosmetic loss: "
            "measured 2026-08-13 against the live journal, four different "
            "filters on a misspelled column each returned total 4639 (all of "
            "`trades`), indistinguishable from a filter that matched every "
            "row. The route is on the diag-relay allowlist, so its callers "
            "include analysis sessions that cannot see the query they got. "
            "BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN."
        ),
    },
    {
        "name": "db_explorer.order_state",
        "producer": "src/web/api/routers/db_explorer.py",
        "producer_field": "order_state",
        "consumer_token": r"\border_state\b|\bdb_table\b|\bdb/table\b",
        "states": ["applied", "not_requested", "ignored_unknown_column"],
        "why": (
            "The ORDER-side twin of `filter_state`, and it was unguarded "
            "entirely until 2026-08-14 — which is how it came to be the thing "
            "that silently satisfied its sibling's evidence under the old "
            "file-wide producer check. Same three states, same consequence in "
            "miniature: an unknown `order_by` is IGNORED, so the rows come "
            "back in the table's natural order while the caller believes they "
            "are sorted. That is quieter than the filter bug (no count is "
            "wrong) and therefore easier to build a conclusion on — a caller "
            "reading 'the newest N rows' is really reading 'some N rows'."
        ),
    },
    {
        "name": "exit_loop_health.requirement_state",
        "producer": "src/runtime/exit_loop_health.py",
        "producer_field": "requirement_state",
        # Deliberately NARROW. `\bexit_loop_health\b` would also match `src/main.py`,
        # `routers/diag.py` and the diag-reachability test, which merely PASS THE
        # PAYLOAD THROUGH — they never branch on the grade, so demanding they read
        # every state would only buy three override annotations that assert nothing.
        # The guard is stronger keyed to the field itself.
        "consumer_token": r"\brequirement_state\b",
        "states": ["within", "breached", "not_measured", "unknown"],
        "why": (
            "within = every MEASURED interval between exit evaluations was "
            "inside the 60s requirement; breached = at least one was not, so a "
            "live trade went unevaluated past it; not_measured = fewer than two "
            "passes have completed, so NO interval exists yet; unknown = the "
            "read itself failed. The two that must never collapse into `within` "
            "are the last two: a process that has evaluated almost nothing, and "
            "one we could not read, would both report COMPLIANCE with the "
            "guarantee M20 exists to provide. This field is also deliberately "
            "NOT `state` — the loop can be `fresh` and `breached` at the same "
            "time, and that is exactly the condition that was invisible: "
            "stale_threshold_s is 180s, so a 59s interval and a 179s interval "
            "both read healthy while the requirement sits at 60s. Measured "
            "2026-08-16 at a 58940.8ms worst pass (n=694), 1.1s inside the "
            "requirement, alarming nowhere. "
            "BL-20260816-EXIT-EVAL-INTERVAL-AT-60S-REQUIREMENT."
        ),
    },
    {
        "name": "exit_anchor.bar_close_at",
        "producer": "src/runtime/exit_anchor.py",
        "consumer_token": r"\bbar_close_at\b|\bexit_anchor\b",
        "states": ["anchored", "deferred", "no_anchor"],
        "why": (
            "anchored = we priced it from the bar at closed_at (ESTIMATED); "
            "deferred = we did NOT look, so retry; no_anchor = the venue was "
            "asked and has nothing, so declare the gap. Collapsing deferred "
            "into no_anchor declares a gap we never checked for; collapsing "
            "either into anchored fabricates a price."
        ),
    },
    {
        "name": "pairs_executor.leg_state",
        "producer": "src/units/strategies/pairs_executor.py",
        "consumer_token": r"\bpairs_soak\b|\bpairs_executor\b|\brun_pairs_tick\b",
        "states": ["half_open", "shadow_open", "skip_flat"],
        "why": (
            "half_open = EXACTLY ONE leg open. It read as flat, so the "
            "executor opened a fresh pair on top of a stranded un-hedged leg "
            "(BL-20260808-PAIRS-DIVERGENCE-UNOWNED)."
        ),
    },
    {
        "name": "pairs_executor.open_state_read",
        "producer": "src/units/strategies/pairs_executor.py",
        "consumer_token": r"\bstate_read\b|\b_open_pkg_meta\b|\b_reconstruct_open_state\b",
        "states": ["found", "absent", "error"],
        "why": (
            "found = the spread bookkeeping is there and usable; absent = we "
            "looked and open legs carry no package (an anomaly); error = we "
            "COULD NOT LOOK. Collapsing absent+error into a bare None is what "
            "disabled the sleeve's entire close path: the read failed on every "
            "open pair (a query against columns that do not exist), the caller "
            "skipped, and 29 pairs were opened with ZERO ever closed while "
            "max_hold_bars went unevaluated. See "
            "BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED."
        ),
    },
    {
        "name": "bybit_available.read_state",
        "producer": "src/units/accounts/execute.py",
        "consumer_token": r"\bread_linear_available_balance\b|\bavailable_margin\b|\bAVAILABLE_STATE_",
        "states": ["venue_available", "coin_derived", "deprecated_withdrawable",
                   "unavailable"],
        "why": (
            "venue_available = the account-level totalAvailableBalance, the "
            "only broker-labelled one; coin_derived = equity - totalPositionIM "
            "- totalOrderIM from the USDT coin block, which is where Bybit "
            "publishes margin for an account whose account-level aggregates "
            "come back empty (the measured bybit_2 state) — it is OUR "
            "arithmetic over the venue's fields, not the venue's own "
            "'available', and collapsing it into venue_available would lose "
            "exactly the distinction this investigation was about; "
            "deprecated_withdrawable = a SUBSTITUTE (a "
            "withdrawal-eligibility figure Bybit deprecated for UNIFIED "
            "accounts in 2025-01) standing in for new-order margin; "
            "unavailable = we COULD NOT LOOK, which is not 'the account has "
            "no margin'. All three used to arrive at the sizer as one bare "
            "Optional[float] with no log on either non-venue branch, so a "
            "cap sized from total equity was indistinguishable from one sized "
            "from broker truth — establishing which had happened on bybit_2 "
            "took four diag pulls and a proof by contradiction, and still "
            "could not separate the two non-venue branches. See "
            "BL-20260701-BYBIT-AVAILABLE-FIELD and "
            "BL-20260813-ICTSCALP-BTC-BYBIT2-BALANCE-REJECTS."
        ),
    },
    {
        "name": "netting_attribution.anchor_status",
        "producer": "src/runtime/order_monitor.py",
        "consumer_token": r"\banchor_status\b|\bnetting_anchor_basis\b",
        "states": ["anchored", "no_anchor", "deferred"],
        "why": (
            "The price-provenance ladder for a netting partial close. An "
            "anchorless 'estimate' is FABRICATED — the class behind the "
            "phantom -$6,358 exit leak."
        ),
    },
]

# `# collapsed-state: <state> — <reason>`
_OVERRIDE = re.compile(r"#\s*collapsed-state:\s*([A-Za-z_][A-Za-z0-9_]*)\s*[-—:]\s*(\S.*)")

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "artifacts"}


def _py_files() -> List[Path]:
    out = []
    for p in REPO.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def _states_in(text: str, states: List[str], field: str = "") -> set:
    """Which declared states this text references, ignoring override lines.

    The annotation is excluded from its own evidence — otherwise writing the
    override would itself satisfy the coverage it is opting out of.

    ``field`` narrows the evidence to LINES THAT ALSO NAME THE FIELD, which is
    the fix for a file-scoped false negative measured 2026-08-14. Producer
    integrity searched the whole producer FILE, so when one module carries two
    contracts whose state vocabularies overlap, either one satisfies the
    other's evidence. Demonstrated on `db_explorer.py`: collapsing
    `filter_state` so it could only ever say ``"applied"`` left the guard
    **clean**, because the sibling `order_state` still contained the literals
    ``"not_requested"`` and ``"ignored_unknown_column"``. That is the guard's
    own "cheaper to lie to than to satisfy" failure one level up — not a false
    annotation, but a *neighbouring field* standing in as evidence.

    Line-scoping (not assignment-parsing) is deliberate: producers in this repo
    emit states as bare returns (``return close, "anchored"``), tuple returns
    (``return ("absent", None)``) and module constants
    (``AVAILABLE_STATE_VENUE = "venue_available"``), so a ``<field> = "<state>"``
    pattern would match almost none of them. A contract omitting ``producer_field``
    keeps the file-wide behaviour, so registering the narrower check is opt-in
    per contract and no existing contract changes meaning.
    """
    keep = [ln for ln in text.splitlines() if not _OVERRIDE.search(ln)]
    if field:
        keep = [ln for ln in keep if re.search(rf"\b{re.escape(field)}\b", ln)]
    body = "\n".join(keep)
    return {s for s in states if re.search(rf"[\"']{re.escape(s)}[\"']", body)}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv[1:])

    files = _py_files()
    findings: List[str] = []
    if a.verbose:
        print(f"collapsed-state-guard: {len(CONTRACTS)} contract(s) over "
              f"{len(files)} python files")

    for c in CONTRACTS:
        name, states = str(c["name"]), list(c["states"])  # type: ignore[arg-type]
        prod_path = REPO / str(c["producer"])
        if not prod_path.exists():
            findings.append(
                f"{name}: declared producer {c['producer']} does not exist. "
                f"Either the module moved (update the registry) or the "
                f"contract is a dead claim.")
            continue

        # (1) producer integrity. `producer_field`, when declared, requires the
        # state literal to sit on a line that also names the field — so a
        # SIBLING field in the same module can no longer stand in as evidence
        # (measured false negative, 2026-08-14; see `_states_in`).
        prod_field = str(c.get("producer_field") or "")
        prod_text = prod_path.read_text(encoding="utf-8", errors="replace")
        emitted = _states_in(prod_text, states, prod_field)
        missing = [s for s in states if s not in emitted]
        if missing:
            scope = (f"on any line naming `{prod_field}`" if prod_field
                     else "anywhere in the file")
            findings.append(
                f"{name}: producer {c['producer']} never emits "
                f"{missing} {scope} — a contract naming a state its own module "
                f"does not produce is a dead claim, not a guarantee.")

        # (2)+(3) consumers.
        #
        # A "consumer" is a file that references the contract's own token —
        # NOT merely one containing a state word. Scoping this properly is
        # load-bearing: the first cut counted any file holding the string
        # "deferred" or "anchored", which matched unrelated English in tests
        # and an old experiment. A guard that fires on coincidence gets
        # routinely overridden, and a routinely-ignored alarm is itself the P1
        # this repo names in CLAUDE.md § "If you see something, say something".
        token = re.compile(str(c["consumer_token"]))
        consumers = []
        for f in files:
            if f == prod_path:
                continue
            txt = f.read_text(encoding="utf-8", errors="replace")
            if not token.search(txt):
                continue
            seen = _states_in(txt, states)
            if not seen:
                continue
            rel = f.relative_to(REPO).as_posix()
            consumers.append((rel, seen))
            if len(seen) < 2:
                ov = _OVERRIDE.search(txt)
                if ov and ov.group(1) in states:
                    if a.verbose:
                        print(f"  ok(override) {rel}: {sorted(seen)} — {ov.group(2)[:60]}")
                    continue
                if ov and ov.group(1) not in states:
                    findings.append(
                        f"{name}: {rel} carries a collapsed-state override "
                        f"naming {ov.group(1)!r}, which is not one of "
                        f"{states}. The override must name a real declared "
                        f"state — an unverified marker is cheaper to lie to "
                        f"than to satisfy.")
                    continue
                findings.append(
                    f"{name}: {rel} branches on only {sorted(seen)} of "
                    f"{states} — the other states fall together. If that is "
                    f"legitimate here, annotate: "
                    f"'# collapsed-state: {sorted(seen)[0]} — <why>'.")

        covered = set().union(*(s for _, s in consumers)) if consumers else set()
        unread = [s for s in states if s not in covered]
        if unread:
            findings.append(
                f"{name}: state(s) {unread} are produced but NO consumer "
                f"branches on them. A state nothing reads IS the collapse — "
                f"every caller is silently treating it as one of the others. "
                f"({c['why']})")
        elif a.verbose:
            print(f"  ok {name}: {len(consumers)} consumer(s), all states read")

    if findings:
        print("\ncollapsed-state-guard: FINDINGS\n" + "=" * 60)
        for f in findings:
            print(f"  - {f}\n")
        print("Rule: docs/CLAUDE-RULES-CANONICAL.md § 'Collapsed states'.")
        print("Design test: for any field encoding a condition, ask whether "
              "'we did not look' and 'we looked and found nothing' are "
              "distinguishable. If not, that is the bug.")
        return 1

    print(f"collapsed-state-guard: clean ({len(CONTRACTS)} contracts)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
