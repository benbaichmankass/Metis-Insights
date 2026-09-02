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
import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# The declared registry. Adding a three-state contract here is how it becomes
# enforced. Keep `states` to the literal tokens the code emits/branches on.
# ---------------------------------------------------------------------------
#: Contracts whose unread-state finding PRE-DATES the 2026-08-31 registry-self-
#: satisfaction fix and is NOT failed, only REPORTED — loudly, every run.
#:
#: ⚠️ THIS IS A DATED DEBT LIST, NOT AN EXEMPTION. Until 2026-08-31 the
#: unread-state check (3) could not fire at all (see `_REGISTRY_PATH` below):
#: every contract was satisfied by its own registry entry. Turning the check on
#: surfaced four items at once and this list was created to hold them.
#:
#: ⚠️ THAT LIST SAID "four genuine findings"; ON MEASUREMENT ONLY ONE WAS.
#: Three were the guard's own evidence model reading correct code as a
#: collapse — it credited only bare string literals, so a consumer branching on
#: the producer's imported CONSTANT (`v.state == INFEASIBLE`) was invisible,
#: and the only way to "fix" it was to write worse code. Crediting constant
#: names (`_state_constants`) cleared all three with NO consumer change. State
#: the population before calling a guard's output a finding: a guard reporting
#: on its own blind spot is not evidence about the code.
#:
#: What remains is named, printed, and OWED. Adding to this list is not a way
#: to silence a NEW finding: `--strict` fails on everything, and a contract
#: registered after this date has no claim on it.
GRANDFATHERED_UNREAD = {
    # ⚠️ THREE ENTRIES WERE REMOVED 2026-08-31, THE SAME DAY THEY WERE ADDED,
    # and they are named here so the removal is not mistaken for an exemption
    # being quietly widened: `research_owed.state`-style debts are only ever
    # discharged by measurement. `research_queue.power_state`,
    # `operator_owed.state` and `over_cover.state` now pass on their OWN
    # evidence — none of their consumers changed. What changed is the guard:
    # `_states_in` credits the producer's module CONSTANT NAMES, not only bare
    # string literals, so a consumer writing `v.state == INFEASIBLE` is finally
    # visible. Three of the four "findings" the check surfaced on the day it
    # started working were therefore the guard penalising the better practice,
    # which is why they were never fixed by editing the consumers.
    #
    # The one that survives is a real unread state.
    "netting_attribution.anchor_status",
}

#: This file. Excluded from the consumer scan — see the loop below.
_REGISTRY_PATH = Path(__file__).resolve()


CONTRACTS: List[Dict[str, object]] = [
    {
        "name": "qty_legalize.venue_max_state",
        # The producer OWNS the vocabulary: the three states are module
        # constants in qty_legalize and nowhere else.
        #
        # No `producer_field` is declared, deliberately, for the reason the
        # research_queue entry below gives: the states are named constants
        # (`MAX_STATE_ABSENT = "absent"`), so the literal never shares a line
        # with the word `venue_max_state`, and narrowing here would fail for a
        # spelling reason rather than a correctness one.
        "producer": "src/units/accounts/qty_legalize.py",
        # Scoped to this contract's OWN tokens. NOT the bare state words:
        # "absent" and "published" are ordinary English that appear across
        # dozens of unrelated modules, and matching on them would bind this
        # contract to files that have never heard of a venue ceiling (the
        # coincidence-matching failure the package_leg_coverage entry records).
        "consumer_token": (r"\bvenue_max_state\b|\bMAX_STATE_PUBLISHED\b|"
                           r"\bMAX_STATE_ABSENT\b|\bMAX_STATE_COULD_NOT_LOOK\b"),
        "states": ["published", "absent", "could_not_look"],
        "why": (
            "THIS IS THE THIRD OCCURRENCE OF ONE LIVE DEFECT, and it is "
            "registered here because the first two fixes shipped with no "
            "detector. BL-20260810 added the venue-max clamp and was marked "
            "`resolved`; BL-20260821 recorded ict_scalp_avax_5m rejecting "
            "again at ~34,000 against a 22,000 cap; on 2026-09-02 the same leg "
            "sent qty 22995.1 eight times and placed zero orders. THE CLAMP "
            "WAS CORRECT EVERY TIME -- what failed is that `venue_max=None` "
            "was produced by three structurally different conditions and the "
            "clamp treated all three as 'no ceiling exists, send it'. "
            "`published` is the venue naming a cap. `absent` is a source that "
            "CAN speak to ceilings telling us there is none -- the only state "
            "that licenses sending unclamped, and the common path (a WARN "
            "there would be the desensitised-alarm P1). `could_not_look` is "
            "the live lookup failing, or the static map answering (step/min "
            "only), or an InstrumentProfile with no max_qty -- absence of "
            "evidence, and reading it as `absent` is the defect. It "
            "deliberately does NOT refuse: the clamp's safety argument is that "
            "it cannot alter an order the venue would have accepted, and "
            "refusing on an unresolved ceiling would block orders that are "
            "legal today -- a far larger blast radius than the bug. It places, "
            "and is now LEGIBLE rather than silent, which is what lets a "
            "fourth occurrence be seen. Note the tests that passed throughout "
            "all three occurrences monkeypatched the resolver to return a rule "
            "with the ceiling ALREADY present, so they exercised the clamp and "
            "never the resolution -- which is where the None is born."
        ),
    },
    {
        "name": "research_queue.power_state",
        # The producer is the GATE itself: `grade_power` returns a PowerVerdict
        # whose `state` is one of these seven, and the vocabulary is defined as
        # literals in this module and nowhere else.
        #
        # No `producer_field` is declared, deliberately. The states are named
        # module constants (`CLEARED = "cleared"`), so the literal never shares
        # a line with the word `state` — narrowing to a field here would fail
        # for a spelling reason rather than a correctness one, and inviting a
        # rename to satisfy the guard is how a guard starts shaping code badly.
        "producer": "scripts/research/research_queue.py",
        "consumer_token": (r"\bpower_state\b|\bPOWER_STATES\b|"
                           r"\bRUNNABLE_POWER_STATES\b|\bACCRUING_STATE\b"),
        "states": ["cleared", "underpowered", "undeclared", "unverifiable",
                   "not_applicable", "infeasible", "accruing"],
        "why": (
            "The R4 admission gate decides whether an experiment is allowed to "
            "spend runner minutes, and its verdict is STAMPED ONTO EVERY ROW "
            "the run lands (`research_power_state`). So the state is not an "
            "internal grade: it travels into the corpus and is the only thing "
            "separating a row from a job that DECLARED UP FRONT it cannot "
            "answer its question yet (`accruing`) from a real test's row. "
            "Collapsing `accruing` into `cleared` would let the queue claim a "
            "protection that does not exist; collapsing `unverifiable` into "
            "`underpowered` would report 'we could not look' as 'the data "
            "refutes it', which are opposite statements about the same job. "
            "`infeasible` is the one the corpus REFUTES, and it is deliberately "
            "distinct from `underpowered` (author's own declared n too small) "
            "because only one of the two is fixed by waiting."
        ),
    },
    {
        "name": "harness_r.r_cost_basis",
        # The PRODUCER of the persisted field is the recorder: it declares the
        # three states and encodes them into the row's `notes`.
        # record_harness_trades.py DECIDES which state a row gets, but it does
        # so through these constants rather than literals, so pointing the
        # contract there would fail producer-integrity for the right reason —
        # the vocabulary lives in exactly one module, which is the point.
        "producer": "ml/datasets/backtest_recorder.py",
        "producer_field": "r_cost_basis",
        # Scoped to this contract's OWN tokens, never to the state words:
        # "net_r" / "gross_r" / "r_multiple" are ordinary column names that
        # appear across the whole harness fleet, and matching on them would
        # bind this contract to a dozen unrelated files (the coincidence-
        # matching failure the package_leg_coverage entry below records).
        "consumer_token": (r"\br_cost_basis\b|\bR_COST_BASIS_STATES\b|"
                           r"\bR_COST_BASIS_GROSS\b"),
        "states": ["net_r", "gross_r", "r_multiple"],
        "why": (
            "record_harness_trades fell back net_r -> gross_r -> r_multiple "
            "and wrote float(r) with NO record of which key supplied it, so a "
            "GROSS-R row (fees and slippage NOT deducted, systematically "
            "optimistic) was BYTE-INDISTINGUISHABLE from a NET-R row in "
            "backtest_trades.db -- which the trainer's nightly pooled build "
            "merges as is_backtest=1 evidence. `r_multiple` is its own state, "
            "not a synonym for either: the producer used a key that says "
            "NEITHER, so the sample cannot be SHOWN to be net, and 'cannot be "
            "shown' is not 'is'. A fourth reading, None, is deliberately NOT a "
            "declared state -- it means the row PREDATES the stamp (nobody "
            "recorded it), a different fact from a producer telling us the key "
            "was uninformative; encode_backtest_notes keeps legacy rows "
            "byte-identical so no migration is needed and an old row reads "
            "None rather than being promoted to net. The consumer is "
            "backtest_fidelity_calibrate.backtest_fidelity, which already "
            "grades 'is this sample what live would have produced' from the "
            "harness fidelity label; gross/ambiguous now set the same "
            "`approximate` verdict rather than a parallel one nobody reads -- "
            "but deliberately do NOT join `omitted_levers`, which names EXIT "
            "LEVERS and would then describe something it is not. Note the "
            "SIBLING defect in this same function (strategy-label precedence) "
            "was found and fixed in the 2026-07-19 audit while this one "
            "survived: a fix that closed the instance and not the class. "
            "Named r_cost_basis, NOT r_basis, because "
            "backtest_fidelity_calibrate already owns an `r_basis` meaning "
            "which R AXIS was computed (stop_distance vs sign_proxy) -- one "
            "name for two concepts in modules that talk to each other is the "
            "F-113 defect measured in this same audit."
        ),
    },
    {
        "name": "prop_rule_distance.balance_basis",
        "producer": "src/prop/prop_reconcile.py",
        "producer_field": "balance_basis",
        "consumer_token": (r"\bbalance_basis\b|\breconstruct_equity\b|"
                           r"\bBALANCE_BASIS_STATES\b"),
        "states": ["snapshot", "snapshot_plus_fills", "unavailable"],
        "why": (
            "The prop bridge has no broker feed, so the account-status "
            "snapshot ages while CLOSED fills keep arriving with realized pnl. "
            "compute_rule_distance now prefers snapshot+fills for the "
            "static-DD cushion, and the basis must travel with the number or "
            "the improvement is silent: `snapshot_plus_fills` is ESTIMATED "
            "(operator-reported fills, so fees/swap are missing — most of the "
            "$10 residual measured on breakout_1 2026-08-18) while `snapshot` "
            "is the operator's own reported figure. `unavailable` is the one "
            "that must never collapse into `snapshot`: it means the fills "
            "stream could NOT be read, so the cushion is snapshot-only and may "
            "be optimistic by an unknown amount — indistinguishable from a "
            "correct snapshot-only reading unless the state says so. The "
            "measured stakes: a stale snapshot rendered a $125.61 cushion to "
            "an account-killer floor where the truth was $47.00."
        ),
    },
    {
        "name": "package_leg_coverage.verdict",
        "producer": "src/runtime/package_leg_coverage.py",
        "producer_field": "verdict",
        # Scoped to THIS module's own tokens, never to the state words: the
        # states include generic English ("managed", "divergent", "stranded")
        # and the first cut matched three unrelated files — order_monitor's
        # `journal_qty_divergent`, m31_mfe_parity, and a netting test. That is
        # the exact coincidence-matching failure this guard's own comment below
        # records from the `deferred`/`anchored` contract.
        "consumer_token": (r"\bpackage_leg_coverage\b|\brun_package_leg_check\b|"
                           r"\bpackage_leg_gaps\b"),
        "states": ["managed", "divergent", "stranded", "linked_unresolvable"],
        "why": (
            "order_monitor drives exits per ORDER PACKAGE and resolves ONE "
            "trade row from linked_trade_id, so a multi-account package's "
            "sibling legs are unmanaged. The four states are four DIFFERENT "
            "operator actions and collapsing any two loses the action: "
            "`divergent` is a live package whose next modify will still miss a "
            "leg (fixable by the repair); `stranded` is a package already "
            "flipped to closed, which the loop's status=\"open\" filter can "
            "never select again (needs a data repair on top of the fix); and "
            "`linked_unresolvable` is *we could not identify the managed leg* "
            "— emphatically NOT `managed`, which is the collapse that would "
            "make an unreadable package read as a healthy one. `managed` "
            "covers both the single-leg case and the multi-leg case whose "
            "stops agree. A journal-read failure is NOT a verdict at all: "
            "run_package_leg_check returns checked=False and latches nothing, "
            "so an unreadable journal can never present as 'no package has a "
            "gap'."
        ),
    },

    {
        "name": "m20_corpus.lever_in_baseline",
        "producer": "scripts/research/m20_corpus_extract.py",
        "producer_field": "lever_in_baseline",
        "consumer_token": r"\blever_in_baseline\b|\blever_absent_from_baseline\b",
        "states": ["lever_in_baseline", "lever_absent_from_baseline", "unknown"],
        "why": (
            "Once an exit lever is DECLARED on a leg, the fleet sweep's "
            "baseline already runs it, so re-sweeping that lever returns "
            "d_net_r == 0.0 on both windows under gate_reason "
            "'tie_no_improvement' with wf_ran false. That is arithmetically "
            "correct and byte-identical to a lever that WAS measured and does "
            "nothing: 10 rows sit in the structural state while 192 OTHER rows "
            "carry the SAME verdict string as genuine measured no-ops. Found "
            "the dangerous way round — the newest live-parity row for "
            "trend_donchian_xrp_4h/trail_decay, SHIPPED on real-money bybit_2, "
            "reads is_oos_fail with every delta 0.0 on a healthy 108/34-trade "
            "run, one query short of being reported as counter-evidence "
            "against a live lever. `unknown` is a row predating "
            "declared_levers_present and is NOT 'absent' — the baseline's "
            "composition was never recorded, which is a different fact from "
            "knowing the lever was out of it. The consumer branches all three "
            "ways in m20_ack_corpus_disagreements.caveats_for, which is the "
            "one place that writes a `ref` ASSERTING a measurement and so the "
            "one place that must refuse to. "
            "BL-20260817-A-SHIPPED-LEVER-RE-SWEPT-AGAINST-ITSELF-READS-AS-A-MEASURED-NO-OP."
        ),
    },
    {
        "name": "position_telemetry.peak_state",
        "producer": "src/runtime/trail_decay.py",
        "producer_field": "peak_state",
        "consumer_token": r"\bpeak_state\b|\bPEAK_MEASURED\b|\bsince_entry_peak\b",
        "states": ["measured", "unanchored", "thin_window", "no_risk"],
        "why": (
            "MFE in R is the quantity every exit lever is tuned on, and three "
            "distinct conditions make it unavailable: the window is not "
            "anchored to entry (a full-frame fallback FAKES the peak), the "
            "window is under 2 bars (no excursion is observable), or risk is "
            "missing so R is undefined. Collapsing any of them into "
            "`peak_r = 0.0` fabricates a FLAT TRADE — the same class as a "
            "fabricated exit price, and worse here because a flat MFE reads as "
            "'this lever correctly never armed' rather than 'we could not "
            "look'. M31 P2, docs/design/position-telemetry-DESIGN.md § 4.1."
        ),
    },
    {
        "name": "position_telemetry.finality_source",
        "producer": "src/runtime/position_telemetry.py",
        "producer_field": "finality_source",
        "consumer_token": r"\bfinality_source\b|\bfinality_sources\b|\bterminal_state\b",
        "states": ["stamped", "derived_join", "not_final", "unknown"],
        "why": (
            "The sibling of `peak_state` above, one level up: that one asks "
            "whether MFE was measurable, this asks whether the ROW IS FINAL "
            "and on what evidence. `position_telemetry` is UPSERT-on-"
            "order_package_id with no status column, so a closed row was "
            "byte-shaped like an open one and the only in-table hint was a "
            "staler `updated_at` — which is not a signal, since a quiet leg "
            "and a closed leg both go stale (measured 2026-08-17: 14 rows, 13 "
            "open + 1 closed, the closed one findable only by joining "
            "`trades`). The Tier-2 terminal writer makes finality a STORED "
            "fact, and this contract keeps the four evidences apart: "
            "`stamped` = the close path wrote it; `derived_join` = only the "
            "join knows, so the row predates the writer; `not_final` = in "
            "flight; `unknown` = we could not look. Collapsing `derived_join` "
            "into `stamped` is the dangerous direction — it would report the "
            "close hook as firing on rows where it never ran, hiding exactly "
            "the regression the split exists to expose. "
            "PB-20260817-TELEMETRY-HAS-NO-TERMINAL-SNAPSHOT; M31 P5 "
            "precondition 1."
        ),
    },
    {
        # The SAME lever, ported to the pullback harness 2026-08-18. Registered
        # separately rather than by widening the trend contract's `producer`,
        # because a contract naming two producers cannot say WHICH one dropped
        # a state — and the port is precisely where the two are free to
        # diverge. The pullback family is also the one that needs the lever
        # most: it carries 11 of the 22 open trades measured with no
        # decision-driven exit path at all on 2026-08-18, and its unit module
        # implements exactly one of the four M20 close mechanisms.
        "name": "pullback_harness.rr_floor_state",
        "producer": "scripts/backtest_pullback.py",
        "producer_field": "rr_floor_state",
        "consumer_token": r"\brr_floor_state\b|\brr_floor\b",
        "states": ["off", "measurable", "unmeasurable_no_tp_cap"],
        "why": (
            "Identical reasoning to trend_harness.rr_floor_state below: with "
            "`tp_cap_pct <= 0` there is no `r_to_target`, so the lever CANNOT "
            "fire and the run returns exactly-zero deltas byte-identical to a "
            "lever that was measured and genuinely does nothing. `off` = no "
            "floor requested; `measurable` = a floor AND a capped TP, so it "
            "could fire; `unmeasurable_no_tp_cap` = a floor was asked for and "
            "the lever could not run — we did not measure it, which is not "
            "the same as measuring no effect."
        ),
    },
    {
        "name": "trend_harness.rr_floor_state",
        "producer": "scripts/backtest_trend.py",
        "producer_field": "rr_floor_state",
        "consumer_token": r"\brr_floor_state\b|\brr_floor\b",
        "states": ["off", "measurable", "unmeasurable_no_tp_cap"],
        "why": (
            "The M31 P5 candidate lever (`rr_from_here` floor) is "
            "STRUCTURALLY UNMEASURABLE without a capped TP: `tp_price` is None "
            "when `tp_cap_pct <= 0`, so `r_to_target` does not exist and the "
            "lever cannot fire however the floor is set. Such a run returns "
            "exactly-zero deltas that are BYTE-IDENTICAL to a lever that was "
            "measured and genuinely does nothing — and a sweep corpus would "
            "record the second meaning against this lever's name. That is not "
            "hypothetical: it is the shape measured on 2026-08-17, where 10 "
            "rows read `tie_no_improvement` with the lever already in their "
            "own baseline while 192 other rows carried the same verdict "
            "legitimately "
            "(BL-20260817-A-SHIPPED-LEVER-RE-SWEPT-AGAINST-ITSELF-READS-AS-A-MEASURED-NO-OP). "
            "`off` = no floor requested; `measurable` = a floor AND a capped "
            "TP, so a zero delta IS a measurement; `unmeasurable_no_tp_cap` = "
            "we could not look. Collapsing the third into the first two is the "
            "dangerous direction — it manufactures an honest-looking negative "
            "for a lever that never ran. PB-20260817-RR-FROM-HERE-LEVER-ABSENT-FROM-HARNESS."
        ),
    },
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
        # ⚠️ `producer_field` is DELIBERATELY OMITTED, and the omission is
        # argued rather than accidental. Producer integrity credits only
        # LITERALS, and this module declares its vocabulary the way the guard's
        # own docstring asks for — module constants (`REACHABLE = "reachable"`)
        # — so scoping evidence to lines naming `horizon_class` would find none
        # of them and could be satisfied only by sprinkling bare strings into a
        # module that already does the right thing. The false negative
        # `producer_field` exists to close (a SIBLING field standing in as
        # evidence) is bounded here and stated: the file's other vocabulary,
        # `funnel_stage`, shares exactly ONE token with this one — `unknown` —
        # so that single state, and no other, could in principle be satisfied
        # by its sibling. The other four cannot.
        "name": "strategy_reviews.horizon_class",
        "producer": "src/runtime/evidence_horizon.py",
        "consumer_token": r"\bhorizon_class\b|\bevidence_horizon\b",
        "states": [
            "gradeable_now", "reachable", "unbounded_no_closes",
            "structurally_ungradeable", "unknown",
        ],
        "why": (
            "HOW FAR A STRATEGY LEG IS FROM GRADEABLE, and each state names a "
            "DIFFERENT REMEDY — which is the whole reason they are not one "
            "`below_evidence_floor` flag. Measured on the committed 2026-09-01 "
            "run (population: all 52 enabled strategies, window 7 days): 18 "
            "legs `reachable` (a measured close rate, so a wider window "
            "genuinely reaches them), 26 `unbounded_no_closes` (closed "
            "NOTHING, so no rate was measured and no finite window follows), 8 "
            "`structurally_ungradeable` (execution: shadow with no fills — a "
            "leg that cannot close a trade at ANY window by design), 0 "
            "`gradeable_now`. Collapsing them reports 52 legs as one window "
            "problem when 34 of them are not, and invites the one remedy that "
            "is a trap: widening the window until something clears the floor "
            "fires a KILL off an evidence base assembled to make a KILL "
            "fireable — the same low-n hazard the floor exists to prevent, one "
            "level up. ⚠️ `unbounded_no_closes` is NOT `unreachable` and NOT "
            "'a rate of zero': observing zero closes bounds the rate from "
            "above and measures nothing, so the leg may close tomorrow. "
            "`unknown` is WE COULD NOT LOOK (an input was absent) and folding "
            "it into `unbounded_no_closes` would turn 'we did not read "
            "n_closed' into 'we read it and it was zero' — the dangerous "
            "direction, since that state routes a leg toward retirement. "
            "OI-20260901-REVIEW-PACKET-CANNOT-PROPOSE-AN-ACTION-AND-ITS-"
            "EVIDENCE-BLOCK-IS-UNEXERCISED; "
            "docs/design/evidence-floor-horizon-PROPOSAL.md."
        ),
    },
    {
        "name": "strategy_reviews.read_state",
        "producer": "src/web/api/routers/strategy_review.py",
        "producer_field": "read_state",
        # Deliberately NARROW — `read_state` alone fires on the diag order-read
        # routes, whose identically-named field carries DIFFERENT states.
        # Coincidence-firing is what produces a routinely-overridden alarm,
        # the failure this guard's own ib_venue_session contract records.
        "consumer_token": r"\bstrategy-reviews\b|\bget_committed_strategy_reviews\b|\bstrategy_reviews_committed\b",
        "states": ["index_read", "absent", "unreadable"],
        "why": (
            "index_read = we read the committed INDEX and the counts are real; "
            "absent = no record has ever been committed for that day; "
            "unreadable = WE COULD NOT LOOK (the file is there and will not "
            "parse). The collapse to avoid is the last into a zeroed first: a "
            "corrupt index answered as `graded: 0, rows: []` reads exactly like "
            "'the fleet was graded and proposed nothing' — a confident clean "
            "negative over a population nobody looked at, which is sub-class C "
            "of the diagnostic-provenance defect and the CONSUMER side of "
            "silent-empty-guard. It matters more here than on a soak surface "
            "because this record exists to be read BEFORE a Tier-3 decision: "
            "'no strategy needs attention' and 'we could not tell' are opposite "
            "inputs to that decision. Counts are therefore None, never 0, "
            "whenever the state is not index_read — 0 graded is a REAL reading "
            "(a run that graded nothing) and must stay distinguishable. "
            "COVERAGE CAVEAT, stated rather than papered over: this route is "
            "NEW and its only consumer today is the test suite, so the state "
            "coverage above is satisfied by tests. It is registered now anyway "
            "— the states exist and the guard's job is to stop a later change "
            "quietly folding them together."
        ),
    },
    {
        "name": "strategy_reviews.freshness",
        "producer": "src/web/api/routers/strategy_review.py",
        "producer_field": "freshness",
        # Deliberately NARROW for the same reason, and this one was MEASURED: a
        # first cut using a bare `\bfreshness\b` fired on
        # src/prop/standard_account_size.py, scripts/ops/manager_lease.py and
        # tests/test_news_layer.py — three unrelated freshness notions that
        # merely share the word.
        "consumer_token": r"\bstrategy-reviews\b|\b_grade_freshness\b|\bget_committed_strategy_reviews\b",
        "states": ["fresh", "stale", "undateable", "absent"],
        "why": (
            "fresh = inside the daily cadence's tolerance; stale = older than "
            "it; undateable = a timestamp we could not parse, so the record "
            "cannot be SHOWN to be current; absent = no record at all. This is "
            "the same defect /api/bot/prop/status grew `status_freshness` for, "
            "one level up: a decision packet's whole purpose is to be read "
            "BEFORE deciding, and a three-week-old packet rendered beside a "
            "confident KILL/PROMOTE badge is indistinguishable from a current "
            "one. `undateable` must fail SAFE to not-fresh rather than "
            "optimistically passing, matching prop_balance's refusal on an "
            "undateable row — a record whose age is unknown is not a record "
            "shown to be fresh. `age_hours` is None for BOTH `absent` and "
            "`undateable`, so the VERDICT is the field to read and the null "
            "cannot separate them. COVERAGE CAVEAT: as above, the consumer "
            "today is the test suite."
        ),
    },
    {
        "name": "ib_venue_session.state",
        "producer": "src/runtime/ib_trading_hours.py",
        # Deliberately NARROW. The first cut used `\bvenue_session\b|
        # \bsession_state\b`, which fired on tests/test_exposure_soak.py — whose
        # `venue_session="closed"` is an UNRELATED field (the US-equity
        # rth/extended/closed stamp) that merely shares a name. That is the
        # coincidence-firing this guard's own docstring warns produces routinely
        # overridden alarms.
        "consumer_token": r"\bib_trading_hours\b|\b_venue_session\b|\bIB_SESSION_CHECK_DISABLED\b",
        "states": ["open", "closed", "unknown"],
        "why": (
            "open = a session covers this instant; closed = the string covers "
            "this instant and no session does; unknown = WE COULD NOT LOOK — "
            "an unparseable string, an unresolvable timezone, or an instant "
            "OUTSIDE the week IBKR sent. That last one is the collapse a "
            "two-state design makes: an instant outside the covered span "
            "matches no range, which is byte-identical to a real closure, so "
            "a stale cached string would report `closed` and DEFER EVERY "
            "CLOSE on a fully open venue. The consequence runs the other way "
            "too: `unknown` must never read as `closed`, because refusing to "
            "flatten a live position on a failed contract lookup converts an "
            "observability defect into money at risk. `unknown` therefore "
            "proceeds like `open` — and is logged WARNING at the close path "
            "precisely so the two are distinguishable in the record, since "
            "US/Eastern and US/Central are tzdata legacy links absent from "
            "slim installs and COMEX/CME report exactly those: a host whose "
            "tz database regressed would disable the gate for every futures "
            "contract we trade and, without that log, announce nothing. "
            "BL-20260816-IB-CLOSE-HAS-NO-MARKET-HOURS-AWARENESS. "
            "COVERAGE CAVEAT, stated rather than papered over: the production "
            "consumer (src/units/accounts/ib_client.py) branches via the "
            "module CONSTANTS (ib_trading_hours.CLOSED / .UNKNOWN), not quoted "
            "literals, so `_states_in` cannot see it and the state coverage "
            "above is satisfied by the TESTS. Constants are the better "
            "practice — a typo'd attribute raises where a typo'd literal is "
            "silent — so the right reading is that this guard's evidence "
            "mechanism does not fit a constants-based API, not that the "
            "production branch is missing."
        ),
    },
    {
        "name": "operator_owed.state",
        "producer": "src/runtime/operator_owed.py",
        # NARROW on this contract's own tokens. "moved"/"carried"/"resolved"
        # are ordinary English words that appear all over the repo, so matching
        # on the STATE WORDS would bind this contract to a dozen unrelated
        # files — the coincidence-firing this guard's own docstring warns
        # produces routinely-overridden alarms.
        "consumer_token": (r"\boperator_owed\b|\bcheck_operator_owed\b|"
                           r"\bcarries_unchanged\b"),
        "states": ["moved", "carried", "escalate_carried", "escalate_aged",
                   "not_measurable", "snoozed", "resolved"],
        "why": (
            "Operator-owed items were handed forward in PROSE only — three "
            "sessions on 2026-08-25 each closed by listing the SAME four items "
            "with zero state change. The collapse that would make that "
            "invisible again is `not_measurable` vs `moved`: a brand-new "
            "register has no history, so no carry EXISTS to count, and "
            "reporting that as `moved` makes the one state in which the "
            "register has demonstrated NOTHING read as perfect health — "
            "exactly the 'green with zero items ever moved' the filing row "
            "calls unproven rather than successful. The two escalations are "
            "deliberately separate rather than one `stale`: `escalate_carried` "
            "is measured from register commits and UNDER-reports (a session "
            "that never touches the register leaves no commit), while "
            "`escalate_aged` needs nobody to touch anything — an additional "
            "trip path that can only ADD escalation, the "
            "silent_refusal_alert.CAUSE_MIN_ROWS shape. And `snoozed` is not "
            "`resolved`: a deferral behind a named trigger event is still "
            "owed, where a resolution is not. Filed as "
            "BL-20260825-OPERATOR-OWED-ITEMS-HAVE-NO-REGISTER-NO-AGE-AND-NO-ESCALATION."
        ),
    },
    {
        "name": "over_cover.state",
        "producer": "src/runtime/over_cover_decision.py",
        "consumer_token": (r"\bover_cover_decision\b|\bdecide_over_cover\b|"
                           r"\bover_cover_proposal\b"),
        "states": ["cancel_group", "no_over_cover", "ambiguous_no_action",
                   "no_journal_match", "no_declared_stop", "not_graded",
                   "position_absent"],
        "why": (
            "Five of these end in 'do nothing' and only ONE of them means the "
            "venue was asked and answered clean. `no_over_cover` is a measured "
            "all-clear; `not_graded` is WE DID NOT LOOK (unreadable prices, no "
            "tick size, a leg whose side the caller could not classify); "
            "`no_declared_stop` is that there was no question to ask; "
            "`no_journal_match` is that we asked and every candidate is a "
            "stray. Collapsing any of them into `no_over_cover` reports a "
            "blind read as a clean position. And collapsing "
            "`ambiguous_no_action` into `cancel_group` IS the recorded "
            "2026-08-20 failure: a repair picked a leg the journal did not "
            "single out and cancelled the one that MATCHED trades.stop_loss, "
            "leaving a 15-lot MES position protected 69 ticks low ($1,289.73). "
            "BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG."
        ),
    },
    {
        "name": "work_decisions.answer_state",
        "producer": "src/runtime/work_decisions.py",
        "consumer_token": (r"\banswer_state\b|\banswerState\b|\bANSWER_STATES\b|"
                           r"\bgrade_answer_state\b"),
        "states": ["not_submitted", "in_transit", "committed", "unreadable"],
        "why": (
            "Phase H's decision round-trip, and the states carry the transit "
            "contract the schema design states outright: THREE NEVER "
            "COLLAPSED, and transit FAILS BACK, NEVER FORWARD. `committed` is "
            "the ONLY one that means decided, and it is graded from the "
            "`answer` block on the work object IN THE REPO — never from the "
            "transit log — so an answer that does not commit leaves its "
            "question unanswered rather than ambiguous. A question wrongly "
            "shown as answered is a decision nobody made. `in_transit` is an "
            "OPEN WINDOW: submitted, not landed, and enumerable with its age "
            "so it closes observably. And `unreadable` is WE COULD NOT LOOK — "
            "collapsing it into `not_submitted` would report a broken transit "
            "channel as 'the operator has not answered', putting a question "
            "back on the operator that they may already have answered and "
            "making a broken channel indistinguishable from a quiet one. That "
            "is exit_anchor.py's deferred/no_anchor distinction applied to a "
            "write path."
        ),
    },
    {
        "name": "bybit_leg_sides.leg_side_class",
        "producer": "src/runtime/bybit_leg_sides.py",
        "consumer_token": (r"\bbybit_leg_sides\b|\bleg_side_split\b|"
                           r"\bLEG_SIDE_STATES\b"),
        "states": ["reduces_graded_book", "reduces_other_book",
                   "leg_side_unreadable", "position_side_unreadable"],
        "why": (
            "A resting Bybit protective leg is reduce-only, so it acts on the "
            "book it can SHRINK -- and `_bybit_position_protection` summed "
            "every leg on the symbol into ONE side-blind `covered_qty` that "
            "the over-cover page then described as coverage OF THE GRADED "
            "POSITION. MEASURED on bybit_1/BTCUSDT (/api/diag/"
            "bybit_open_orders, read 2026-09-02T03:30:33Z, trader git_sha "
            "68e73de8): position Buy 0.018 positionIdx=1 covered EXACTLY 1.00x "
            "by its own Sell 0.018 leg, paged as '2656%' because a Buy 0.46 "
            "leg acting on a short book landed in the same sum. Collapsing "
            "`reduces_other_book` into `reduces_graded_book` is that page. "
            "`leg_side_unreadable` and `position_side_unreadable` are both WE "
            "DID NOT LOOK and are deliberately separate from each other: the "
            "first leaves the sums a LOWER BOUND, the second means NOTHING on "
            "the symbol is gradeable, and reporting the second as the first "
            "would point a reader at the wrong half of the read."
        ),
    },
    {
        "name": "bybit_leg_sides.other_book_state",
        "producer": "src/runtime/bybit_leg_sides.py",
        "consumer_token": r"\bother_book_state\b|\bOTHER_BOOK_STATES\b",
        "states": ["impossible_one_way", "possible_hedge", "unknown"],
        "why": (
            "Whether a leg acting on the OPPOSITE book is stranded depends on "
            "whether such a book can exist. Under one-way netting "
            "(positionIdx 0) it cannot, so the leg is stranded by "
            "construction; under HEDGE mode -- armed on bybit_1 and bybit_2 "
            "since 2026-08-30 -- it may be a LIVE sibling's protection, and a "
            "page that called it orphaned would invite cancelling a live "
            "position's stop. `unknown` is WE COULD NOT LOOK and must never "
            "default to `impossible_one_way`: CLAUDE.md names that exact "
            "hazard for this same venue field -- 'defaulting an unread mode "
            "to the netting value is precisely the reading that would make a "
            "hedge account look safe to treat as netted'."
        ),
    },
    {
        "name": "share_hold.state",
        "producer": "src/units/accounts/alpaca_client.py",
        # No `producer_field`: the states are emitted as bare tuple returns
        # (`return ("residual_unreadable", "could not read ...")`) and as members
        # of the module constant SHARE_HOLD_STATES, so no literal shares a line
        # with the word "state". Narrowing here would fail for a spelling reason
        # rather than a correctness one — the `research_queue.power_state`
        # reasoning, same shape.
        "consumer_token": (r"\bshare_hold\b|\bSHARE_HOLD_STATES\b|"
                           r"\bclassify_share_hold\b|\bparse_share_hold\b|"
                           r"\bUNCLEARABLE_HOLD_STATE\b"),
        "states": ["residual_unreadable", "no_residual_orders",
                   "broker_cancel_wedged", "orders_still_resting"],
        "why": (
            "WILL RETRYING HELP? Both Alpaca close paths produce a "
            "BYTE-IDENTICAL failure for a transient cancel race and for an order "
            "wedged in `pending_cancel` forever — same retMsg ('insufficient qty "
            "available'), same ERROR, same 'won't flatten' page, every tick, "
            "indefinitely. The four states are the answer, and as of 2026-09-02 "
            "one of them BUYS SILENCE: `broker_cancel_wedged` is the sole "
            "trigger that downgrades a close-failure page out of the paging "
            "channel into the daily digest (src/runtime/close_wedge_standing.py, "
            "operator decision on OI-20260901-ALPACA-SHARE-HOLD-CLASSIFIER-"
            "SHIPPED-NOT-YET-OBSERVED). That is exactly why this contract had to "
            "be registered rather than left as a log string: a state that gates "
            "an alarm must be enforced as a state. "
            "`residual_unreadable` is the one that must never collapse into "
            "`no_residual_orders` — 'we could not read the open orders' and 'we "
            "read them and nothing rests' are opposite claims, and the second "
            "would let a broker we could not reach look like a clean book. "
            "`no_residual_orders` is deliberately distinct from "
            "`orders_still_resting` because only the second says a retry may "
            "work; the first means the shares are held by something this "
            "classifier does not model, which is a different operator action. "
            "A FIFTH reading, `not_classified` (SHARE_HOLD_NOT_CLASSIFIED), is "
            "deliberately NOT a declared state here: it means NOBODY CLASSIFIED "
            "the failure — a non-Alpaca venue, or a path that never reached the "
            "give-up branch — which is a fact about our own coverage, not "
            "something the classifier observed. Registering it would claim "
            "producer integrity for a value the producer cannot emit, the "
            "`trainer_disk_unknown` reasoning in "
            "src/web/api/routers/notifications.py. It is enforced instead by "
            "parse_share_hold's own contract (an unknown token reads as "
            "not_classified, never as one of the four) and by "
            "tests/test_close_wedge_downgrade.py. "
            "The consumer that branches on all four is "
            "execution_diagnostics._share_hold_guidance, which turns each into "
            "the operator action it implies — before it existed every page said "
            "'investigate the venue/connection' regardless, which is the "
            "collapse in miniature: a field written and never read."
        ),
    },
    {
        "name": "close_wedge.transition",
        "producer": "src/runtime/close_wedge_standing.py",
        "consumer_token": (r"\bclose_wedge\b|\bclose_wedge_standing\b|"
                           r"\bTRANSITIONS\b|\bLOUD_TRANSITIONS\b|"
                           r"\bsweep_close_wedges\b|\bwedge_transition\b|"
                           r"\benqueue_close_wedge_state_change\b"),
        "states": ["newly_wedged", "still_standing", "evidence_changed",
                   "cleared_confirmed", "vanished_unattributed"],
        "why": (
            "This contract decides WHETHER THE OPERATOR IS PAGED about a "
            "position that will not flatten, so a collapse here is silence about "
            "a stuck position — the failure the close-failure pager was built to "
            "end, reintroduced through its own de-noising. Exactly ONE state is "
            "quiet (`still_standing`, and even it is floored by "
            "CLOSE_WEDGE_REPAGE_HOURS); LOUD_TRANSITIONS is computed as the "
            "COMPLEMENT of that one so a state added later is loud by default "
            "rather than inheriting silence. "
            "`vanished_unattributed` is the state this repo has already paid for "
            "losing: it means the wedge stopped being observed with NO confirmed "
            "close, and folding it into `cleared_confirmed` would bank a repair "
            "nobody can name. CLAUDE.md's PROTECTION_REASSERT_MODE row is the "
            "precedent — a gate at `annotate` with an empty allowlist got read as "
            "having fixed a divergence it could not have touched — and "
            "OI-20260901-ALPACA-SHARE-HOLD-CLASSIFIER-SHIPPED-NOT-YET-OBSERVED "
            "names the same trap for this exact GLD wedge in its `Clears when`. "
            "`cleared_confirmed` is reachable ONLY from the monitor's "
            "confirmed-close path (order_monitor._resolve_close_wedge_confirmed), "
            "because that is the only place with attribution; the staleness "
            "sweep can produce nothing but `vanished_unattributed`, by "
            "construction. "
            "`evidence_changed` must not collapse into `still_standing`: it means "
            "the same (account, symbol, side) is now wedged on DIFFERENT orders "
            "or a different hold state — a second, unexamined fault, which would "
            "otherwise inherit the first one's suppression budget. "
            "`newly_wedged` is loud on purpose: downgrading the ARRIVAL of a "
            "wedge would hide the condition rather than de-noise it."
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


def _import_line_numbers(text: str) -> set:
    """1-indexed line numbers occupied by `import` / `from ... import` stmts.

    Excluded from constant-name evidence below. Without this, a single
    `from research_queue import (CLEARED, ACCRUING, ...)` would satisfy the
    whole contract while branching on nothing — the "cheaper to lie to than to
    satisfy" hazard this guard exists to prevent, re-introduced by the very
    change that makes constants count. Literals cannot appear in an import, so
    dropping these lines costs the literal path nothing.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            end = getattr(node, "end_lineno", None) or node.lineno
            out.update(range(node.lineno, end + 1))
    return out


def _state_constants(prod_text: str, states: List[str]) -> Dict[str, List[str]]:
    """`{state: [MODULE_CONSTANT_NAMES]}` declared in the producer module.

    Only module-level `NAME = "<state>"` assignments count, and only for states
    the contract declares. A consumer that writes `verdict.state == INFEASIBLE`
    is doing the RIGHT thing and was invisible to a literal-only scan — so the
    guard penalised the better practice and could only be satisfied by
    sprinkling bare strings into modules that import the vocabulary properly.
    """
    out: Dict[str, List[str]] = {}
    try:
        tree = ast.parse(prod_text)
    except SyntaxError:
        return out
    wanted = set(states)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        if value.value not in wanted:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                out.setdefault(value.value, []).append(t.id)
    return out


def _states_in(text: str, states: List[str], field: str = "",
               const_names: Optional[Dict[str, List[str]]] = None) -> set:
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
    lines = text.splitlines()
    skip = _import_line_numbers(text) if const_names else set()
    keep = [ln for i, ln in enumerate(lines, 1)
            if not _OVERRIDE.search(ln) and i not in skip]
    if field:
        keep = [ln for ln in keep if re.search(rf"\b{re.escape(field)}\b", ln)]
    body = "\n".join(keep)

    found = set()
    for s in states:
        pats = [rf"[\"']{re.escape(s)}[\"']"]
        for name in (const_names or {}).get(s, ()):
            pats.append(rf"\b{re.escape(name)}\b")
        if re.search("|".join(pats), body):
            found.add(s)
    return found


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="also fail on GRANDFATHERED_UNREAD contracts")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv[1:])

    files = _py_files()
    findings: List[str] = []
    # Reported every run, but non-failing unless --strict. Kept as its own
    # list rather than a flag on `findings` so a grandfathered item can
    # never be silently counted as clean.
    owed: List[str] = []
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

        # Constant names are derived from the PRODUCER and credited only in the
        # consumer scan below — never in (1). Producer integrity must keep
        # asking "does this module actually emit the literal?", and crediting
        # `CLEARED = "cleared"` for the state `cleared` there would make every
        # contract self-satisfying at its own declaration site, which is the
        # 2026-08-31 registry bug one module over.
        const_names = _state_constants(prod_text, states)

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
            # ⚠️ THIS REGISTRY IS NOT A CONSUMER OF ITSELF — measured
            # 2026-08-31, and without this line the guard's central check was
            # STRUCTURALLY VACUOUS for 19 of its 20 contracts.
            #
            # A contract entry contains its own `consumer_token` pattern as
            # source text (so the token matches) AND every state literal in its
            # `states` list (so `_states_in` returns all of them). So the file
            # that DECLARES a contract automatically satisfied it: check (3),
            # "state(s) X are produced but NO consumer branches on them",
            # could not fire however many states were genuinely unread.
            # Registering a contract was itself the evidence that it held.
            #
            # That is precisely this guard's own stated failure mode — "a guard
            # that is cheaper to lie to than to satisfy is worse than no guard"
            # (the `new-table-wiring-guard` lesson it cites) — reproduced one
            # level up, on the guard rather than on an annotation. Nothing had
            # to be written wrongly for it to happen; declaring the contract
            # was enough.
            if f == _REGISTRY_PATH:
                continue
            txt = f.read_text(encoding="utf-8", errors="replace")
            if not token.search(txt):
                continue
            # ⚠️ CONSTANT NAMES COUNT HERE, LITERALS ONLY IN (1) — a consumer
            # writing `verdict.state == INFEASIBLE` branches correctly and was
            # invisible to a literal-only scan, so the guard penalised the
            # better practice and could be satisfied only by sprinkling bare
            # strings into modules that import the vocabulary properly.
            # Import lines are excluded (`_import_line_numbers`), so a single
            # `from x import (A, B, C)` cannot stand in for a branch.
            seen = _states_in(txt, states, const_names=const_names)
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
            (findings if (a.strict or name not in GRANDFATHERED_UNREAD)
             else owed).append(
                f"{name}: state(s) {unread} are produced but NO consumer "
                f"branches on them. A state nothing reads IS the collapse — "
                f"every caller is silently treating it as one of the others. "
                f"({c['why']})")
        elif a.verbose:
            print(f"  ok {name}: {len(consumers)} consumer(s), all states read")

    if owed:
        print("\ncollapsed-state-guard: GRANDFATHERED (reported, not failed)\n"
              + "-" * 60)
        for o in owed:
            print(f"  ~ {o}\n")
        print("These pre-date the 2026-08-31 self-satisfaction fix. Run with "
              "--strict to fail on them.\n")

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
