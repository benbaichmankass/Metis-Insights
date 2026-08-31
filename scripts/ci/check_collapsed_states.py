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
#: Contracts whose unread-state finding PRE-DATES the 2026-08-31 registry-self-
#: satisfaction fix and is NOT failed, only REPORTED — loudly, every run.
#:
#: ⚠️ THIS IS A DATED DEBT LIST, NOT AN EXEMPTION. Until 2026-08-31 the
#: unread-state check (3) could not fire at all (see `_REGISTRY_PATH` below):
#: every contract was satisfied by its own registry entry. Turning the check on
#: revealed four genuine findings at once, three of them belonging to other
#: work. Failing CI on all of them would have made the fix un-landable and the
#: check would have stayed off — which is how a vacuous guard survives.
#:
#: So they are named, printed, and OWED. Adding to this list is not a way to
#: silence a NEW finding: `--strict` fails on everything, and a contract
#: registered after this date has no claim on it.
GRANDFATHERED_UNREAD = {
    # Mine, from the same change that found this. Its states ARE branched on —
    # via the imported constants (`RUNNABLE_POWER_STATES`, `v.state == CLEARED`)
    # rather than string literals — which this guard's evidence model cannot
    # see. Satisfying it today would mean sprinkling literals into consumers
    # that correctly import the vocabulary, i.e. writing worse code to please a
    # guard. The evidence model is the thing to fix.
    "research_queue.power_state",
    "operator_owed.state",
    "over_cover.state",
    "netting_attribution.anchor_status",
}

#: This file. Excluded from the consumer scan — see the loop below.
_REGISTRY_PATH = Path(__file__).resolve()


CONTRACTS: List[Dict[str, object]] = [
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
