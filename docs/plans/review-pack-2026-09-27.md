# Review pack — wave-6 OPS lane, for the joint operator/manager session

> **Doc status:** `unknown` · category `plan` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · nobody has verified this document's status — do not act on it as current
>
> Prepared per the operator's 2026-09-27 ~08:15Z request (relayed by the manager): a
> single pack of findings from this wave's `/ml-review`, `/performance-review` and
> the system-audit scoping, each proposal phrased as a decision to accept or
> reject, plus the operator-owed items currently past their due window. This
> document does **not** edit `docs/plans/OPERATING-PLAN-2026-09-21.md`,
> `ROADMAP.md`, or checklist priorities — every row/priority change below is a
> proposal for the joint session, not an action taken here.
>
> Sources: `comms/reviews/ml-review-20260927-063330.json` (PR #13075, open),
> `comms/reviews/performance-review-20260927-072944.json` and
> `docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md` (both PR #13085, open). Both
> PRs are content-complete but not yet merged — see § "Why these PRs are still
> open" at the end; nothing about that blocks reading or deciding on the
> content below.

---

## Decisions (12) — accept/reject, detail below

| # | Source | Decision | Options |
|---|---|---|---|
| **D1** | ml-review | Dispatch a **Tier-1** build lane now for `PI-20260927-YZRZQ725-0001` (shadow-drift gate reads only the active log; blind for ~1–2wk after every ~25–29-day rotation — no past drift-based demote/hold decision, including E72's, was ever computed on a full window). | Dispatch now / Defer |
| **D2** | ml-review | Dispatch a **Tier-2** build lane for `PI-20260926-96NDZSOD-0001` (exit-head shadow-scoring twin-dedup bug) — the sole blocker to evaluating `exit-head-donchian-1h-v1`'s shadow→advisory promotion, which already has a marginal-pass OOS re-grade sitting idle (+1.3127R, n=119, PR #13046). | Dispatch now / Defer |
| **D3** | ml-review | Add a **Tier-3** checklist row: author + walk-forward validate a SOL `trend_vol` OFF-cell (`regime-selectivity` skill) so `sol-regime-15m-lgbm-fc-pcv-v2` (advisory since 08-02) has any real-money lever at all — currently zero `regime_hard_gate` rows for SOL ever. | Add row / Reject |
| **D4** | performance-review | Add a **Tier-1** checklist row: extend `scripts/analysis/classify_paper_records.py` with a pairs-strategy-aware bucket (`PI-20260927-YZRZQ725-0004`). Fixes the classifier coverage gap inflating the window's "86% artifact" headline — ~25 of 44 bucket-B closes are pairs legs' own bilateral management exits, not artifacts. | Add row / Reject |
| **D5** | performance-review | `breakout_1` should drop `iaum_pullback_1d` as a candidate leg (`RQ-20260827-001`: p_profitable=0.0647, EV −$3.65/trade). Route into the active P2 prop-candidate-search lane's scoping. | Drop the leg / Keep evaluating |
| **D6** | performance-review | `SRQ-20260618-001`/`-002` (soft regime weight / recombination orchestrator `regime_filter` axis) has sat un-worked 6+ weeks since the hard-ADX-gate hypothesis was refuted (08-11). | Dispatch a `backtesting`-skill lane / Kill the row / Defer |
| **D7** | audit scoping (E71) | Pick an audit scope: **(a)** all 8 areas, est. $165–270, or **(b)** the 3-area subset order-path + trading-correctness + infra (§4.1/4.2/4.5), est. $75–115. | (a) / (b) / Defer entirely |
| **D8** | audit scoping | Cadence: **one-off** wave now, or a **standing** cadence (e.g. monthly)? The retired skill's monthly cadence still missed a 21-day-live bug — the finding-schema+detector discipline matters more than cadence, per the proposal's own §6.2. | One-off / Standing / Defer |
| **D9** | audit scoping | Widen §4.6 (CI guard-coverage enumeration — mechanical, cheaply verifiable) to `claude-haiku-4-5` per the manager skill's E11 candidate criterion? | Yes / No |
| **D10** | audit scoping | 8 lanes running in parallel could produce Tier-3 findings faster than one daily sync processes. Stand up a dedicated findings-review session, or fold into the regular daily brief? | Dedicated session / Fold into daily brief |
| **D11** | infra (found this session) | `operator-owed-guard` is failing intermittently and repo-wide right now — reproduced on a clean `origin/main` checkout, confirmed independently by two peer PRs (#13084, #13091) today, filed as `PI-20260927-EPMBGUYB-0003`. It is currently the top mechanical blocker to landing *any* Tier-1 self-land PR. | Prioritize a Tier-1 fix lane now / Continue routing around it |
| **D12** | synthesis (this pack) | Three independently-filed findings all point at the same live prop-money gap on `breakout_1`: flat $75 tickets vs a $94.76 DD-floor cushion (`PI-20260924-MQ3CDMU6-0001`), the approved `PROP_TICKET_RISK_GATE_MODE=enforce` not actually implemented in code (`PI-20260924-MQ3CDMU6-0002`), and PR #13084's fresh EV simulation reading the current 1.5%-risk sizing as **INDETERMINATE** (central estimate −$10.08/account-life) vs **+$173** at a cushion-fit 1.0% sizing. | Authorize a Tier-3 sizing fix (implement the gate + resize to the cushion-fit basis) now / Defer |

---

## Detail

### ml-review (PR #13075, `comms/reviews/ml-review-20260927-063330.json`)

**Headline:** both E72 Tier-3 demote proposals (btc/sol regime, advisory→shadow) stay **HELD**, unchanged from PR #12960. The fresh stage-guard read of "no demote trigger tripped" for both is **not** a resolved-drift signal — MEASURED this session: the shadow-prediction log rotated 2026-09-26T06:39:57Z (a normal, healthy, ~monthly rotation), and `drift_clean`/`live_regime_discrimination` now read `insufficient_data` for **every** advisory model, including the previously-clean `mes-regime-5m-lgbm-v2`, because the drift endpoint's 30-day reference window is longer than the observed ~25–29 day rotation cadence with no fallback to the rotated archives. Practical consequence: no drift verdict this repo has ever fed into a Tier-3 promotion/demotion decision — including E72's original trigger — was ever computed on a full, un-truncated window. → **D1**.

- `mes-regime-5m-lgbm-v2`: judged unchanged/healthy — its new `insufficient_data` reads are the same post-rotation accrual gap (E72 measured it clean pre-rotation: 37/37 parity, 0.89 label coverage, AUC 0.738). Self-resolving re-check already filed (`mes-post-rotation-recheck`, Tier-1, no decision needed).
- `exit-head-donchian-1h-v1` (M2-owned): stays shadow/inert. `RQ-20260927-001` (PR #13046, merged) delivered the operator-requested train/test-split OOS re-grade: PASS but marginal (+1.3127R over n=119). Does not license promotion on its own; `PI-20260926-96NDZSOD-0001` is the unmet precondition. → **D2**.
- `sol-regime-15m-lgbm-fc-pcv-v2`: healthy (oos_edge +0.237, non_degenerate pass) but zero real-money order effect since promotion — no SOL `trend_vol` OFF-cell exists. → **D3**.
- Trainer health: both regime heads retrained cleanly on 09-26 and 09-27 (overall_rc=0, 0 failed, 0 manifest_quarantine events).
- No Tier-3 action taken this review; no model stage changed.

### performance-review (PR #13085, `comms/reviews/performance-review-20260927-072944.json`)

**Window:** 2026-09-24T16:00Z–2026-09-27T07:29:44Z. **Headline:** 86% of the window's 51 closed trades (44) are technical artifacts, not clean strategy round-trips (`classify_paper_records.py`: A=7, B=44, C=0). Of the 44, ~25 are `pairs_sol_eth_*`/`pairs_bnb_btc_*` legs closing `other` — their own bilateral management exit, a **classifier coverage gap**, not a bug → **D4**. The remaining ~19 (10 reconciler + 9 non-pairs `other`) are the genuine artifact-health signal. The 7 gradeable (clean sl/tp) trades are 1-for-7, net −$2,693.69 (paper $, all bybit_1) — a small, noisy sample, not evidence of a broken strategy on its own.

**More urgent finding:** `bybit_2` (real money) closed exactly 3 trades this window and **all three** are bucket-B (2 reconciler, 1 other) — zero cleanly-gradeable real-money exits in 63 hours, net −$10.93. All three carry `journalTrust=known_divergent`, which is **not a new regression** — it matches the standing, already-diagnosed `bybit_2` SUB-subaccount credential gap (`PI-20260924-PN2PXMTE-0001`; checklist row `E70`, done — the auto-fix was reverted 2026-09-25 after the operator declined a new credential). No new decision needed here; restated for visibility since it means this review still cannot independently verify bybit_2's real-money PnL against broker truth.

- `RQ-20260827-001` (gld_compat, unread since 09-10): dispositioned. Confirms `breakout_1` should not carry `iaum_pullback_1d`. → **D5**.
- `SRQ-20260618-001`/`-002`: correctly diagnosed, surviving hypothesis un-worked 6+ weeks. → **D6**.
- M13 insights cross-check: clean, no contradiction (trade #6186 matches exactly).
- Not run this window (stated, not silently skipped): the diversified paper-book tracker and the real-money allocation benchmark — both need a separate full diag round trip; no decision needed, just a known gap for the next performance-review.

### System-audit scoping (E71, `docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md`)

Plan only, nothing executed. Motivated by two unrelated findings landing in the same 30 hours — the ml-review's shadow-drift blind spot above, and the retired `full-system-audit` skill's own `IBClient.protection_coverage` case study (missed by two prior audits, caught only when a new read surface was built) — both the same shape: a measurement that looks like a clean verdict is actually a silent capability loss. Eight scope areas (order-path safety, trading correctness, data/provenance integrity, ML serving fidelity, infra/VMs, CI guard health, security, the operating model itself), each with a one-lane-one-question method, a finding schema (falsifiable claim + exact evidence + population + detector, never prose), and a cost table. Four operator questions are already built into the doc — reframed as **D7–D10** above.

### Open operator-owed items (MEASURED this session, `python3 scripts/ci/check_operator_owed.py --verbose`, run 2026-09-27T08:28Z)

Population: 1,354 rows in `docs/claude/work/pipeline/`; 119 owed to the operator; 25 of those DUE now. None is currently past *twice* its own declared cadence (the guard's own hard-fail threshold) — but several sit close to their single-cadence due point and are worth the operator's attention at this joint session rather than waiting for the guard to force it:

| id | age (days) | cadence limit | what |
|---|---|---|---|
| `OI-20260913-A-BYBIT2-HEDGE-BOOK-IS-NEVER-FETCHED-...` | 1.3 | 2.0 | bybit_2's settle-coin page returns one row per symbol; a hedge symbol's second book is never read at all — raised on a real-money naked ETHUSDT short; no repo surface can confirm whether that book is still open. |
| `PI-20260924-MQ3CDMU6-0001` | 1.3 | 2.0 | `breakout_1` sizes every ticket at a flat $75 against a $94.76 DD-floor cushion — see **D12**. |
| `PI-20260924-MQ3CDMU6-0002` | 1.3 | 2.0 | `PROP_TICKET_RISK_GATE_MODE=enforce` (operator-approved 08-31) has no code path that actually caps size — see **D12**. |
| `PI-20260924-GIGNEYV1-0001` | 1.3 | 2.0 | `breakout_1` exit-geometry pre-registered rule (E65) selects no candidate — keep current bracket B0; a Tier-3 proposal awaiting an explicit operator read (not a bug). |

*(Ages read 1.3 days at this run; they were reported as ≥2.0 days — i.e. over the hard-fail threshold — on the `guards` CI runs at 07:39–07:43Z for PRs #13075/#13085, ~45 minutes earlier. This age computation is time-sensitive in a way that does not track simple wall-clock elapsed time 1:1 — see **D11**; the guard's own instability is itself now a filed finding, not assumed to be resolved by this later, passing, local read.)*

### Why these PRs are still open

Both #13075 and #13085 are content-complete, self-land-armed, and green on every check except `operator-owed-guard` (the **D11** condition) — reproduced on a clean `origin/main` at the time of the CI failure, independent of either diff, and corroborated by peer PRs #13084 and #13091 hitting the identical failure on `main` itself today. One comment was posted on each PR establishing this; no further re-dispatch was made per the one-retry rule. They are expected to merge once the underlying pipeline rows move or a fix lands (**D11**), and nothing about their being unmerged affects the content or decisions above.

---

**This document makes no commitment.** Every row above is a decision for the joint session; nothing in this pack edits `OPERATING-PLAN`, `ROADMAP.md`, or checklist priorities.
