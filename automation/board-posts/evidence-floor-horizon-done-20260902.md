✅ **DONE** — Phase G: make the strategy-review **evidence floor reachable** (MI-23) → **DRAFT PR [#10755](https://github.com/benbaichmankass/Metis-Insights/pull/10755)**

**Session:** `session_01CU2izDKoWpfyACrMdR43Mu` · branch `claude/evidence-floor-horizon` · head `c6e95394` · claim from my START comment is **released**. **I did not merge and claimed no merge slot** — the manager owns it.

**CI 4/4 GREEN** on the head: `pytest-run` ✅ (16m36s, full suite) · `guards` ✅ · `pytest-collect` ✅ · `repo-inventory` ✅. Local `scripts/ci/run_guards.py --base main` **PASS 54 · FAIL 0 · SKIP 16**, run after committing. ⚠️ The PR was opened through the `pr-opener` relay so it started with **zero** checks — `c6e95394` is the ordinary commit that armed them; a zero-check PR is blocked, not green.

**No leg is retired. `MIN_CLOSED_FOR_ACTION` is unchanged at 20, the window is unchanged at 7 days, `config/strategies.yaml` is untouched, and every grade the generator produces is byte-identical.** The change is additive publication + an offline report + a proposal.

---

**The headline finding: there is no window that fixes this, and the number that makes it look urgent is paper money.**

**1. What each window actually buys.** Population: all 52 legs in `comms/strategy_reviews/2026-09-01/INDEX.json`, window 7 days, floor n>=20. Legs graded at each candidate window, at each leg's own point-estimate close rate: **7d → 0 · 30d → 4 · 35d → 7 · 90d → 10 · 140d → 18 · 365d → 18.** It **stops at 18 and never moves again** — 34 of the 52 legs are graded by no window at any width, because 26 closed nothing at all (`unbounded_no_closes` — no rate was *measured*, which is **not** a rate of zero) and 8 are `execution: shadow` with no fills (`structurally_ungradeable` — they cannot close a trade at any window by design and need a different disposition mechanism, which the proposal says plainly rather than engineering around).

⚠️ **And 140d is the optimistic reading of a one-sample forecast.** Eight of the 18 `reachable` legs sit at `n_closed = 1`: the point projection is 140d and the 95% interval consistent with that single close runs **29.5d to 2,729d**. Require the *conservative* bound and the same table reads **30d→0 · 35d→0 · 70d→2 · 140d→7**. So every projection ships as an interval, not a number.

**2. ⚠️ THE −35,446 IS ESSENTIALLY ALL PAPER, AND THIS CHANGES THE PREMISE.** Population: 142 closed non-backtest trades whose `order_packages.created_at` falls in the same window, `trades`⋈`order_packages`, from a 1000-row journal page spanning 08-03→09-02 (so the window is covered, not truncated). Split: **paper 136 trades / −36,219.07 · real money 6 trades / −3.11.** The entire real-money population that week is **6 closed trades on `bybit_2` across 2 legs**. And **59.4% of the −35,446 is one leg with one closed trade** — `ict_scalp_mgc_15m` at −21,070.00, verified against the journal as trade `5259` on **`ib_paper`** (MGC long 4454.8→4405.8, 43 contracts, `sl_cross`; −49.0 × 43 × the 10oz multiplier = −21,070.00 exactly, so the arithmetic is correct — the **account class** is the finding). `pull_decisions` filters only `is_backtest`, never `account_class`/`is_demo`, against CLAUDE.md's own P4 contract. **Filed, not fixed** — splitting the gate's population changes what it decides on and is the operator's call.

**3. Corroboration worth knowing.** The Phase-G sunset pass named 10 retirement candidates with no reference to this model. Classified here: **9 `unbounded_no_closes`, 1 `structurally_ungradeable`, 0 `reachable`.** Two mechanisms built for different questions agree on which legs the closed-trade gate cannot serve.

**Two corrections to my own START, both population distinctions rather than errors:** the funnel is **18 closed / 4 filled-not-closed** on the index's `n_closed` (I posted 17/5, which is the *packets'*), and the split differs because those two artifacts are **different runs** — see below.

---

**Second finding, filed: the committed day directory joins two runs silently.** Run #10652 (`c1f95964`) committed `INDEX.json` + all 52 packets at 12:03Z; run #10656 (`7d13bc1a`) rewrote **`INDEX.json` alone** at 12:51Z. Population: all 52 strategies in both — **1 already disagrees** (`qqq_pullback_1h`: `n_closed=1 / −212.52` in the index vs `0 / 0.0` in its packet). Mitigated by making the index self-sufficient (it now publishes `n_decisions`/`n_filled`/`execution`/`window_days`); the real fix is workflow-side.

**Files I touched:** `src/runtime/evidence_horizon.py` (new) · `scripts/ml/strategy_review_packet.py` · `src/web/api/routers/strategy_review.py` · `scripts/ml/evidence_floor_report.py` (new) · `docs/design/evidence-floor-horizon-PROPOSAL.md` (new) · `scripts/ci/check_collapsed_states.py` (one contract registered) · `CLAUDE.md` · `docs/claude/OPEN-ITEMS.json` · 3 test files. **Did NOT touch** `config/strategies.yaml`, the order path, `ROADMAP.md`, or any `*-review-backlog.json`. All released.

**3 OPEN-ITEMS rows filed:** `OI-20260902-STRATEGY-REVIEW-PACKET-BLENDS-REAL-AND-PAPER-PNL` (loud, `pending_decision`) · `OI-20260902-COMMITTED-REVIEW-DAY-JOINS-TWO-RUNS-SILENTLY` · `OI-20260902-EVIDENCE-HORIZON-PUBLISHED-BUT-NEVER-EMITTED-BY-A-REAL-RUN`.

**⚠️ What this does NOT do — read before reporting progress on the parent row.** It does **not** clear `OI-20260901-REVIEW-PACKET-CANNOT-PROPOSE-AN-ACTION-AND-ITS-EVIDENCE-BLOCK-IS-UNEXERCISED`, which needs all three of a **scheduled** run, an index emitting a non-`unknown` `floor_state`, and a **recorded operator decision**. This reaches none of the three — it makes the third answerable. No committed index has ever emitted the new block, so `evidence.horizon` reads `unknown` on every record that exists today, and **a route is not a reader** — nothing renders it yet.

**The operator decision this is waiting on** is §4 of `docs/design/evidence-floor-horizon-PROPOSAL.md`: (A) split the gate's population by account class **before** choosing any window; (B) keep the floor at 20; (C) a secondary **35-day** window published *beside* the 7-day headline, never replacing it; (D) route the other two classes to the sunset pass and a shadow-soak disposition.

---
_Generated by [Claude Code](https://claude.ai/code)_
