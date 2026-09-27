# Code audit 2026-09-27 — consolidated report (living)

> **Doc status:** `unknown` · category `evidence` · last verified `2026-09-27` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) (audits are measurements; the index does not assess them)
>
> Living report · owner: lane **CA-LEAD** (Opus) ·
> operator decision: checklist row **CA** (verbatim there) · method:
> [`docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md`](../plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md) §3, §5 ·
> per-lane outputs: `docs/audits/code-audit-2026-09-27/<ID>.findings.jsonl` + `<ID>.md`

**Last consolidation:** 2026-09-27 ~13:15Z against `origin/main` @ `da9e68d`.
**Lanes consolidated so far: 0 of 24 findings files landed.** The only audit
output on `main` is one pipeline item from CA-A01 (below). This report fills in
as lanes land; each update is its own small docs PR.

**What this report is not.** It is not "every line was read" (§3.3 — never
true, never the target). It is not the lanes' own verdicts: every CRITICAL and
HIGH below is re-run by the lead against a surface the lane did not produce,
and marked CONFIRMED / NOT-REPRODUCED / DOWNGRADED with that evidence.

---

## 1. Scope

Budget: $1,100 (row CA), separate from the wave-6 cap. Lane rows and scopes are
in `docs/claude/work/MANAGER-CHECKLIST.json` (`CA-*`); scopes are summarised,
not restated.

| Lane | Scope (summary) | Model | Status (row) | Findings on main |
|---|---|---|---|---|
| CA-A01 | `src/runtime/order_monitor.py` + its tests | Opus | in_flight | partial — 1 pipeline item (CA-A01-001); no `.findings.jsonl` yet |
| CA-A02 | execution core I: `main.py`, pipeline, intents, sizing, costs, gates, `multi_account_execute`, pairs executor | Opus | in_flight | n |
| CA-A03 | execution core II: exits, positions, fills, invariants, protection, broker truth | Opus | in_flight | n |
| CA-A04 | `src/units/accounts/**` (brokers, executors, risk) | Opus | in_flight | n |
| CA-A05 | signal builders, strategies, `ict_detection`, regime | Sonnet | in_flight | n |
| CA-A06 | rest of `src/runtime` (alerts, telegram, reports, health, provenance, insights, soaks) | Sonnet | in_flight | n |
| CA-A07 | `src/prop` + `scripts/prop` (Breakout) | Sonnet | in_flight | n |
| CA-A08 | `src/web`, `src/bot`, `src/comms` (security focus) | Sonnet | in_flight | n |
| CA-A09 | config, mandates, auto-land route, landing & permission controls | Opus | in_flight | n |
| CA-A10 | `deploy/**` + VM-mutating `*_action.sh` | Sonnet | in_flight | n |
| CA-A11 | `scripts/ops` part 1 | Sonnet | in_flight | n |
| CA-A12 | `scripts/ops` part 2 | Sonnet | in_flight | n |
| CA-A13 | `.github/workflows` part 1 | Sonnet | in_flight | n |
| CA-A14 | `.github/workflows` part 2 | Sonnet | in_flight | n |
| CA-A15 | remaining small `src/` modules | Sonnet | in_flight | n |
| CA-B01 | `ml/**` | Sonnet | queued | n |
| CA-B02 | `scripts/ml` + `scripts/training` | Sonnet | queued | n |
| CA-B03 | `scripts/research` part 1 | Sonnet | queued | n |
| CA-B04 | `scripts/research` part 2 | Sonnet | queued | n |
| CA-B05 | CI guards part 1 — each proven to fail on its defect | Haiku | queued | n |
| CA-B06 | CI guards part 2 — each proven to fail on its defect | Haiku | queued | n |
| CA-B07 | tests coverage vs `src/**` | Sonnet | queued | n |
| CA-B08 | leftover scripts | Sonnet | queued | n |
| CA-B09 | skills + canonical docs vs code | Sonnet | queued | n |
| CA-LEAD | this report | Opus | in_flight | — |

"Status" is the checklist row's `state` as read at the consolidation time
above, not a `get_session` read.

## 2. Method

1. Each turn: `git fetch origin main`; read every
   `docs/audits/code-audit-2026-09-27/*.findings.jsonl` and every pipeline
   record whose `origin.ref` names a CA lane.
2. **Dedupe** on mechanism, not wording: two lanes reporting the same root
   cause from different files become one entry listing both ids.
3. **Re-verify every CRITICAL/HIGH independently** — re-run the lane's
   evidence command *or* an independent one against a surface the audited
   code does not control (live `/api/diag/*` read directly, the venue's own
   order book, a grep of call sites, a test run). The lane's own output is
   never the proof. Verdicts: **CONFIRMED** / **NOT-REPRODUCED** /
   **DOWNGRADED** (with the new severity and why).
4. **Split each confirmed finding into exactly one** of:
   - **(a) evidence-settled fix** — unambiguous, bounded, tier stated; the
     brief below is written to be dispatched verbatim by the manager
     (files, change, test that proves it, detector, tier);
   - **(b) judgment call** — needs the operator (strategy / risk / roster /
     spend trade-off): options + the lead's recommendation, for daily-brief
     section 2.
5. Every CRITICAL/HIGH must have a `docs/claude/work/pipeline/` item; the
   lead files one where the lane did not.
6. MEDIUM/LOW are consolidated without independent re-verification (stated
   per entry), and never silently dropped.

## 3. Coverage roll-up

Per §3.3: **behavioral coverage is primary** (declared capabilities exercised
end-to-end against real data this pass), **reading coverage secondary**
(files read / files explicitly not read). Both are taken from each lane's
`.md` coverage statement; a lane that does not state both is marked
*unstated*, not assumed complete.

| Lane | Behavioral coverage (exercised / declared) | Reading coverage (read / explicitly not read) |
|---|---|---|
| — | *no lane coverage statements landed yet* | — |

## 4. CRITICAL / HIGH (independently re-verified)

### CA-A01-001 — CRITICAL — watchdog flat-close leaves the trade's IB OCA protection resting on a flat book → **CONFIRMED**

- **Claim (lane):** trade 5836 (`ib_paper`, MES long 15) was closed by
  `_watchdog_stuck_strategies`; its keyed OCA group `oca-protect-t5836`
  (SELL STP 15 @7602.5 + SELL LMT 15 @8390.5, GTC) still rests while the venue
  holds no MES. If either leg fills it **opens** an unintended 15-lot MES
  short. Pipeline item: `PI-20260927-KFWRL9R1-0001`.
- **Lead re-verification (2026-09-27 ~13:12Z, direct diag reads, not the
  lane's captures):**
  - `scripts/ops/diag_fetch.sh 'ib_open_orders'` → account `DUQ325724`
    (ib_paper): order 907 `LMT SELL 15 MESZ6 @8390.5 oca_group=oca-protect-t5836
    PreSubmitted GTC`, order 906 `STP SELL 15 MESZ6 aux 7602.5
    oca_group=oca-protect-t5836`, both `client_id 497`.
  - `diag_fetch.sh 'exchange_positions'` → ib_paper positions: `MHG short 22`,
    `MGC long 90`. **No MES.**
  - `diag_fetch.sh 'journal?table=trades&limit=500'` (ids 5726–6225) → trade
    5836: `status=closed`, `exit_reason=stuck_strategy_watchdog`,
    `closed_at=2026-09-19T01:44:44Z`. Resting for **8 days**.
  - `diag_fetch.sh 'version'` → deployed `git_sha 9adda355f`, no restart
    pending (so this is the running code, not an old build).
  - Code: `grep -n _cancel_resting_protection_after_flat src/runtime/order_monitor.py`
    → defined L1825, called only at L3394 and L3630 (orphan reconcile).
    `_watchdog_stuck_strategies` spans L5453–~L5955 and contains **no**
    `cancel` call at all.
  - Why nothing else clears it: `src/runtime/stray_oca_groups.py` classifies a
    **keyed** group (`oca-protect-t<id>`) as `SIBLING_KEYED` and keeps it by
    design (it cancels only non-keyed strays), so the stray sweep does not
    reach a keyed group whose owning trade is closed. This is the gap, not a
    bug in the sweep's stated rule.
- **Blast radius:** money-at-risk class. Today paper (`ib_paper`); `ib_live` is
  `dry_run`, so the defect reaches real money on the first live IB close that
  goes through the watchdog path.
- **Split: (a) EVIDENCE-SETTLED FIX** — see brief **FIX-CA-01** in §5. The
  immediate cancel of 906/907 is part of that brief (Tier-2 ops action, paper
  account).
- **Open for lanes still running:** the lane also names `reconciler_filled`
  in `_reconcile_open_trades` and `close_from_order_status` as missing the
  sweep. The lead has **not yet** verified those two paths this turn; the fix
  brief covers them by requiring a test per path, which settles it either way.
  CA-A03 (`stray_oca_groups`, `protection_reassert`) may add to this entry.

## 5. Evidence-settled fixes (for the manager to dispatch)

### FIX-CA-01 — cancel closing trade's keyed IB protection on every flat-close path (Tier 2, order path)

- **Finding:** CA-A01-001 (CONFIRMED above). Pipeline: `PI-20260927-KFWRL9R1-0001`.
- **Part A — remediate the live stray now (Tier-2 ops, paper account).**
  Cancel `ib_paper` orders **906** and **907** (perm 624635376 / 624635377,
  OCA `oca-protect-t5836`) with the `cancel-ib-order` system action
  (`docs/claude/system-actions.md`), dry-run first, then `apply: true`.
  Both legs are protective and owned by the trader's clientId **497**, so the
  action needs `force_protective: true` and `force_client_id: true`; IBKR
  refuses a duplicate clientId while the trader holds it (Error 326), so the
  apply needs the bounded window `pause-autoheal` → `stop-bot-service` →
  cancel → restart. **Verify:** `diag_fetch.sh 'ib_open_orders'` shows no
  `oca-protect-t5836` leg. (Alternative with no stop window: none found this
  turn — the code fix in Part B does not retroactively cancel a group whose
  trade is already closed.)
- **Part B — the code fix (Tier 2, order path).**
  - **Files:** `src/runtime/order_monitor.py`; new/extended test under `tests/`
    (grep `tests/` for `_watchdog_stuck_strategies`).
  - **Change:** on every path that finalises a trade closed because the venue
    is flat — `_watchdog_stuck_strategies` flat-confirmed branch (~L5752+),
    `_reconcile_open_trades` `reconciler_filled`, `close_from_order_status` —
    call `_cancel_resting_protection_after_flat(aid, symbol)` scoped to the
    closing trade's keyed group `oca-protect-t<trade_id>` so a sibling open
    trade's group on the same symbol is preserved
    (`BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY`).
  - **Test that proves it:** for each of the three paths, close a trade with a
    mocked IB client holding its keyed group plus a sibling's keyed group;
    assert the closing trade's group is cancelled and the sibling's is not.
    Must fail on current `main` for the watchdog path.
  - **Detector (permanent):** (1) the test above; (2) a read-side invariant —
    any resting `oca-protect-t<id>` group whose trade `<id>` is not `open`, or
    any protective leg on a symbol with no venue position, raises an alert
    (fits `closed_flat_invariant` / the stray-OCA soak; the fix lane should
    pick the existing module rather than add one).
  - **Tier:** 2 — prepare + validate, then ship under the data-backed Tier-2
    standing authorization with immediate operator notice (evidence: the
    venue read above), or one operator OK.
  - **Closes when:** pipeline item's `clears_when` — no `oca-protect-t5836`
    legs, fix merged **and** deployed (`/api/diag/version` sha contains it).

## 6. Judgment calls (for the operator — daily brief section 2)

*None yet.*

## 7. MEDIUM / LOW

*None landed yet.* (Consolidated from lane files; not independently
re-verified unless stated.)

## 8. Verified non-issues

*None landed yet.* Lanes' `verified-non-issue` dispositions are listed here
with the evidence that closed them, so a later audit does not re-derive them.

## 9. Not covered

Filled from each lane's explicit "not read / not exercised" list, plus any
scope no lane owned. Known at dispatch:

- Nothing in the lane table owns `comms/` data files, `research/queue/`
  units, or `docs/` other than the canonical set CA-B09 checks — intended
  (this is a code audit), stated so it is not read as covered.
- Live-venue behavior is exercised only where a lane does it against real
  data; reading coverage of an order path is not behavioral coverage of it.

## 10. Change log

- 2026-09-27 ~13:15Z — report created (CA-LEAD turn 1). 0/24 findings files
  on main; CA-A01-001 re-verified CONFIRMED; FIX-CA-01 brief written.
