# Sprint Log: S-ROADMAP-STATUS-REVIEW-2026-08-01

## Date Range
2026-08-01 (single session). Review window: the past ~week, **2026-07-24 → 2026-08-01**.

## Objective
Operator-requested **full, verified roadmap status review** to plan the next work
sessions. Requirement: *don't assume — always verify*, and cleanly separate
(a) what is actually **built** vs still to do, (b) research lines **definitively
disproven** vs those that **need more experimentation**, and (c) which **data
sources are trustworthy** vs unstable/error-prone. The decisive deliverable is
the **prioritized, autonomous-ready next-work backlog** in the last section — an
autonomous session should be able to pick up any item and execute it cold.

## Tier
Tier-1 (docs only). No `src/`, `config/`, order-path, live-VM or backlog-JSON
writes. Every material state claim is tagged **[verified-live]** (from a diag-relay
pull or the live coordination board today) or **[repo-record]** (from a same-day,
07-31/08-01 sprint log / research doc that was live-verified at write time).

## Starting Context
This review does **not** duplicate today's weekly `/system-review`
(`RPT-20260801-090000-weekly`, grade **investigate** — a trade-dossier report). It
is roadmap- and work-plan-centric. Prior reconcile of record:
[`S-ROADMAP-RECONCILE-2026-07-28`](S-ROADMAP-RECONCILE-2026-07-28.md) (ROADMAP.md
`Last Updated: 2026-07-28`). Several items this session would otherwise recommend
were **already executed on 2026-08-01** by the concurrent `full-system-review-zajauh`
session — captured below so the next session doesn't redo them.

## Repo State Checked
- `ROADMAP.md` (milestone table + the 2026-07-28 reconcile header) and `ROADMAP_MACRO.md`.
- ~24 sprint logs in `docs/sprint-logs/` dated 2026-07-24 → 08-01.
- The three review backlogs (`docs/claude/{health,performance,ml}-review-backlog.json`).
- `docs/research/` + `docs/audits/` verdict docs.
- Coordination board #6927 (431 comments; read to 2026-08-01 14:29Z).
- **Live diag relay** (issue #8266, run 15:46Z): `services`, `ib_state`, `db_info`,
  `status`, `performance?window=7d|30d`, `positions`, `roadmap`, `notifications`,
  `ml/{registry,cycle,builds,db_pulls}`, `pnl/broker-truth`, `stats`.

## Files and Systems Inspected
Live VM `ict-bot-arm` (141.145.193.91) via the `vm-diag-request` relay; the trainer
mirror surfaced through the live relay's `ml/*` routes; the three-repo git history
(dashboard/android checked read-only). No VM mutation.

---

## Work Completed — the status review

### 1. Executive posture (where we stand)

The system is **operationally healthy and very good at grading + stopping bad
ideas**, but has **one** proven, deployed edge and a research frontier that is now
**mostly data-accrual + trainer-gate + Tier-3-promotion waits, not greenfield
building**.

- **Live health [verified-live 15:46Z]:** trader/web-api/telegram-bot/claude-bridge
  all `active`; trader on current `main` (`054c34b6`), ticking, heartbeat running;
  vm cpu 4.8% / mem 11% / disk 38.5%; IB gateway healthy (3 MES clients connected,
  not wedged); DB single-inode clean (trades 4309, order_packages 3460, signals
  1.3M); **zero alert banners** (no trainer_down / account_down / orphan_unreconciled).
  The 2026-08-01 05:29–06:54Z trader outage (Telegram-token-rotation fallout) is
  **fully recovered** and the token-compromise incident is **closed** (all leaked
  tokens 401).
- **Real-money result [verified-live]:** bybit_2 is the **only** live-money account.
  7d = 10 trades, 20% win, **−$6.99** (profitFactor 0.60); 30d = 34 trades,
  **−$24.62** (profitFactor 0.65). Small numbers, modestly negative, loss
  concentrated in `eth_pullback_2h` / `xrp_pullback_2h` / `trend_donchian`. Lifetime
  journal real `totalPnL` −$64.91, but the **authoritative** bybit_2 wallet-truth is
  **−$262.52** (`as_of 2026-07-13`, journal under-records — see §5).
- **The one proven live edge:** the **BTC ML vol-gate** (Design-A). Everything else
  on the ML/allocator/pairs/meta-label/macro frontier is either disproven (§4) or
  still soaking.

### 2. What is BUILT (done / shipped)

| Area | State |
|---|---|
| **M0–M5, M7, M11, M13 (S1+S2)** | ✅ Closed — foundation, strategy-review gate, multi-strategy refactor, AI-analyst. [repo-record] |
| **M15 platform migration** | Ampere live-trader cutover COMPLETE (2026-06-14); micro terminated. [repo-record] |
| **Provenance overhaul (the week's headline)** | `src/runtime/provenance.py` + `pnlProvenance` API field + CI guards; exit-anchor-to-`closed_at` fix (#8069); IB broker-truth reader. **Live-confirmed holding**: real-money 7d pnlCoverage **90%** measured. [verified-live] |
| **3-repo PnL-provenance surfacing (P0.3)** | Shipped to all three front-ends 2026-07-31 — bot `/trades/closed` `pnlProvenance` (#8180) + dashboard #203 + android #115. [repo-record] |
| **Full-system-audit P0–P2 + W0–W2** | P0 (provenance read-side filter, 3-repo surfacing, authored-cell register), P1 (trainer honesty — the "0-vs-506 starvation" was a **diagnostic lie**, fixed; vt004 mislabeled head retired), W2 (15 required CI contexts, merge_group base_ref fix ×15). [repo-record + verified-live: trade_outcomes builds 511 rows/day] |
| **Security: Telegram token compromise** | Contained + resolved 2026-08-01; leak vector (world-readable committed httpx logs since repo went public 07-07) closed; runtime redaction fixed. [repo-record + verified-live: bots active] |
| **Front-ends (past week)** | dashboard: chart-TZ epoch fix + blocking-refresh removal + ruff pin (#202). android: nullable backtest-count em-dash (#114). Svelte SPA + Learning Center landed just before the window (07-14→16). No open known-bugs. [repo-record] |

### 3. What is IN PROGRESS / TO DO (with the concrete blocker)

| Milestone | Status | The actual blocker |
|---|---|---|
| **M6 Web app UI** | 🔄 in progress | dashboard-repo incremental (section-landing live metrics). Not gating. |
| **M12 Android** | 🔄 S1–S9 done | Play-Store first AAB pending **operator**; **P2b** (bot-side `/ws/market` push for futures + positions/uPnL) design-approved, **not built** — needs a bot-repo WS route; P2c polish. |
| **M16 Unified Confidence Risk** | 🔄 in progress | phased/observe-first; conviction-sizing apply path stays `off` (Design-B disproven, §4). |
| **M20 Exit Refinement** | 🔄 near-done, done-condition NOT literally met | `exit_ladder` un-built fleet-wide; some `exit_head_ml` cells pending; `xauusd_trend_1h` blocked on candle task #27. **Mechanical exit levers on the trend/pullback fleet are DISPROVEN (§4)** — remaining M20 value is the ladder + ML heads, not more levers. |
| **M21 Entry Refinement** | 🟡 table says in-progress → **actually DORMANT** | coverage frozen since 2026-07-14; E-3 closed honest-negative. **Contradiction to fix (§6).** |
| **M24 Net-R / cost-aware** | 🔄 P1/P2 done | P3/P4 **blocked** on broker-truth per-trade cost coverage + Tier-3. |
| **M25 ML promotion & consolidation** | 🔄 P1–P3 done | P4 in progress — **the fc-pcv v2 swap is the live open item (§G1)**. |
| **M27 Scalp Expansion** | 🔄 P0 complete | crypto per-leg audit says **regime-tune, not demote** (§4); XAUUSD/GLD cells venue/evidence-blocked. |
| **M28 Macro/Value** | 🔄 P0 done → **value frontier EXHAUSTED (§4)** | macro-M1 **energy-event** study is the live sub-line (one positive signal) but 404s on wrong series ids (§R1). |
| **M29 System-dynamics** | 🟢 P1a shipped → **gas model disproven (§4)** | P2 genuinely open. |
| **M30 Deep quant-research platform** | 📋 session prompt ready | not started. |
| **M36 Consolidation** | 🔄 scoped | operator directive: go **deeper on M25→M30 before new frontiers**. |
| **macro-M2 event-response backtest** | gate **not passed** | pre-registered net-of-cost-vs-naive thresholds unmet; and the M1 gate currently tests *tracking*, not *usefulness* (§R-flag). |

### 4. Research ledger — DISPROVEN vs NEEDS-MORE vs PROVEN

**DISPROVEN / closed (with the quoted verdict + evidence — do NOT re-open without a new input):**
- **M18 allocator EV cross-candidate selector** — learned ranker OOS AUC ≈ 0.51–0.52; *"no scorer beats dumb symbol-priority … closed for good."* (`docs/research/M18-allocator-backtest-findings-2026-06-29.md`). Only untested input left = `c_ml` P_win (§NEEDS-MORE).
- **Design-B symmetric conviction sizing** — *"FAILS the gate … 4.5× larger max drawdown (11.6%→52.7%) … Do NOT graduate."* Stays `off`/annotate.
- **M19 new model *types*** — frozen-embeddings / TCN / SSL encoder all closed negative; *"only the `fc` forecast feature survived."*
- **M21 entry E-3** — honest-negative (one-bar-ahead leakage); dormant since 07-14.
- **M22 pairs sleeve** — *"not real-money-viable as specified; live on paper only and net-negative on taker fees."* (Live-confirmed: the paper pairs positions are still open on bybit_1 with hedge geometry.) [verified-live]
- **M23 meta-labeling** — P2 honest-negative, NO-GO on P3 (eval-side label wall, below usable-volume floor).
- **M28 value frontier + M32–M35** — *"conclusively exhausted"*; vix_term the one weak lead (*"do not productionize standalone"*); HY-OAS a lead not a cost-surviving edge.
- **M29 gas / system-dynamics model** — *"the mechanistic model does not beat static."*
- **Cross-sectional momentum** — GATE FAILED, net −0.31.
- **Mechanical exit levers on the trend/pullback fleet** — *"mechanical exit levers there don't beat baseline. Their bleed is entry-selection, not exits."* (MFE≥2R giveback lever is a structural no-op because TP sits at 1.5R.)

**PROVEN & LIVE:**
- **Design-A ML vol-gate (BTC)** — *"the ML vol label beats the frozen-edge label decisively … ML beats frozen on net in ALL 4 walk-forward folds."* Live-enforcing on BTC (`btc-regime-15m-lgbm-fc-pcv-v1` advisory). **This is the system's one demonstrated ML edge.**

**NEEDS-MORE-RESEARCH (open / soaking / inconclusive — a real answer is still possible):**
- **Exit-management ML head** (synthetic→real transfer) — live_holdout 0.555, *"inconclusive — 95% CI ≈ ±0.10 on n=62."*
- **M18 `c_ml` P_win** — the only untested allocator input; must beat `shared_priority` before any plumbing.
- **fc-geometry SL/TP sizing (M19 D1)** — soaking observe-only.
- **The research→results gap** — *"the validation layer keeps green-lighting strategies the honest live-execution layer then loses money on."* Fix (paper-mirror-net-positive hard gate + full-cost re-gate) is **proposed, not built** — see §R4, the highest-leverage frontier fix.
- **Crypto per-leg regime-tuning** — the 6-leg audit (2026-07-30) found **no leg genuinely dead**; adverse regime, not dead strategies → regime-tune, not demote.

### 5. Data-source trust matrix (the operator's explicit ask)

**TRUSTWORTHY:**
- **bybit_2 real-money via the Bybit exchange-fills store** (`exchange_fills.sqlite`, FIFO) — the control account, ~2% fabricated; open rows **match the broker to the decimal** [verified-live board W1]. Real-money 7d/30d pnlCoverage **90% / 88%** [verified-live].
- **`comms/broker_truth_ledger.json`** — authoritative bybit_2 wallet-truth (−$262.52). ⚠️ **but `as_of 2026-07-13` — ~3 weeks stale** [verified-live]; refresh is a follow-up (§T-refresh).
- **Bybit candle history** — 100% coverage.
- **Broker-measured paper venues** (IB MGC scalps, alpaca via FIFO exchange-truth) — quotable.

**FABRICATED / UNTRUSTED (do not tune on these):**
- **All demo/paper per-row PnL** — fabricated at scale. [verified-live] the `demo` block reads **7d −$17,343 / lifetime −$60,176** — a poisoned book. bybit_1 47% / bybit_portfolio 92% fabricated; matched-pair proof ~650×; fabricated share of closed trades 0%→24%→**65%** (May→Jul). **Binding directive: do not tune any strategy/exit/promotion gate on paper PnL until the measured/fabricated split is surfaced.** Forward fix (exit-anchor) is holding; **legacy rows stay poisoned** (see §R1 for the residual relabel).
- **bybit_1 open-row *sizes*** — journal 35–155× the exchange (partial-close rows never reduced, `BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED`). Don't trust bybit_1/ib_paper open-row sizes for analytics until remediation runs.
- **IBKR historical candles — 0% coverage** (`ict-mes-ibkr-pull` FAILED; mes-regime-1d audit-quarantined — **not money-at-risk**, mes-5m still scores). Mitigated: IBKR is now a *broker-truth reader* via `CommissionReport.realizedPNL`.
- **yfinance** — flaky; best-effort dashboard fallback only.
- **`docs/research/exit-capture-deepdive-2026-07-30.md` root cause is WRONG** — it blames `BYBIT_TPSL_MODE=full`, but `partial` was already live since ~07-21; the 07-30 flip was a **no-op re-assertion**. Its **MFE/giveback metrics stand; its root-cause attribution does not** (`BL-20260730-EXITCAPTURE-DEEPDIVE-WRONG-TPSL-PREMISE`).

### 6. ML fleet snapshot [verified-live cycle 2026-08-01 01:10Z + board gate-checks]
- Ladder `candidate → shadow → advisory`; only advisory influences orders.
- **BTC vol head:** `btc-regime-15m-lgbm-fc-pcv-v1` advisory, drives the live gate.
- **SOL advisory head:** DEMOTED to shadow 2026-07-26 (drift KS=0.236>0.2); fresh-data `-v2` **soaking** — as of 08-01 blocked ONLY on live_parity accrual (13/20, re-check ~20:30Z). Zero live-order impact (no SOL `trend_vol` cell).
- **fc-pcv v2 swap (M25 P4, ~4d overdue):** **BTC v2 has a genuine `drift_clean` FAIL (KS 0.2543>0.2)** — swap **held** per drift-remediation (re-check after 2–3 days of post-retrain scores). SOL v2 = parity-pending. See §G1.
- **MES manifests:** the nightly cycle trains the mes-regime-5m/15m variants fine; **mes-setup-quality / mes-trade-outcome build 0-row datasets** because **MES has never traded in ~65d** (`mes_trend_long_1d`'s 1d Donchian-24 breakout never triggered) — honest empties, `MB-20260801-MES-BASELINE-MANIFESTS-NEVER-TRAINED` (§G2). mes-regime-1d-lgbm-v2 audit-quarantined (dead feature).
- **Trainer honesty [verified-live]:** `trade_outcomes` builds **511 rows/day**, `cycle_end` now carries honest `trained/skipped/failed/outcome`, `manifest_ok` carries real `model_id`. The "dead since May" claim was a build-log lie — do not cite it.

### 7. Flagged contradictions — APPLIED in this PR (M21) + a parser bug found
- **M21 ROADMAP.md cell — CORRECTED in this PR.** It read `🟡 IN PROGRESS` while verified reality is **Dormant since 2026-07-14** (E-3 closed honest-negative, coverage frozen). Fixed the leading glyph/label to `📋 DORMANT (paused …)`, body preserved. **This also fixed a latent parser bug:** `🟡` is **not** in the roadmap router's `_STATUS_EMOJI` map (`src/web/api/routers/roadmap.py`), so the glyph scan fell through to the keyword scan, which matched "E-3 **CLOSED**" / "E-1 … **DONE**" in the cell body and **mis-bucketed M21 as `done`** on `/api/bot/roadmap`. Leading with the mapped `📋` glyph now correctly buckets it `planned`/pending. Other cells lead with unmapped `🟡`/`🟢` glyphs and may mis-bucket the same way — logged as a follow-up (§ Risks).
- **M20 ROADMAP.md cell — NO edit needed (already reconciled).** Its table status is correctly `🔄 IN PROGRESS` (maps to `in_progress`), and the only overstatement ("essentially COMPLETE", in the "Next" plan Item 1) is **already explicitly corrected** by the 07-28 reconcile ("*Item 1 below overstates M20 as 'essentially COMPLETE' — treat it as 'near-complete, exit_ladder lever + head rounds remain'*"). Manufacturing another edit would be redundant noise — the record is already accurate.

## Validation Performed
Live re-verification via diag-relay issue #8266 (15:46Z) across 15 endpoints (see
§Repo State Checked). Cross-checked each against the repo record; **no material
drift** found since the 07-31/08-01 records except: (a) trader had rolled forward
to `054c34b6` (current main); (b) IB gateway recovered from a 14:32Z probe blip;
(c) broker-truth ledger confirmed stale at `as_of 2026-07-13`. Every §-claim is
tagged verified-live or repo-record above.

## Documentation Updated
This log **and `ROADMAP.md`** — the **M21 status cell was CORRECTED** in this PR
(`🟡 IN PROGRESS` → `📋 DORMANT`, body preserved), which also fixed a latent
`/api/bot/roadmap` parser mis-bucket (§7 + §R6). **M20 needed no edit** (already
reconciled — §7). No other ROADMAP.md rows touched (avoids a concurrent-edit race
with the active `full-system-review-zajauh` session).

## Contradictions or Drift Found
See §7 (M20/M21 roadmap cells) and §5 (the `exit-capture-deepdive` wrong-premise
doc — already carries an in-repo banner + backlog row; no further action needed
beyond not citing its root cause).

## Risks and Follow-Ups — NEXT WORK SESSIONS (the decisive section)

Each item: **objective · why now/evidence · blocker/precondition · Tier · first
concrete action · done-condition.** Ordered *ready-now → gated → operator-Tier-3*.
Items marked **[DONE 08-01]** were executed today by `full-system-review-zajauh` —
listed so they are NOT redone.

### A. READY TO EXECUTE NOW (no gate)

- **R1 · Finish the legacy paper-PnL relabel + hard-block tuning on unlabeled paper.**
  Why: the paper book still reads −$17,343 (7d) fabricated [verified-live]; today's
  apply (#8253) recovered only **14 rows MEASURED from own fills**, mirror tier (11
  more, ESTIMATED) left opt-in, Jun 8–Jul 13 permanently unverifiable. Blocker: none
  (Tier-2 data run needs the same operator-go pattern as #8253, already granted-shape).
  First action: run the opt-in mirror-tier relabel; then verify every paper $ surface
  carries its `pnlCoverage`. Done: no paper dollar figure is quotable without its
  measured/fabricated split shown.
- **R2 · Fix `BL-20260730-EIA-SERIES-IDS-NOT-FRED` (macro-M1 energy events).**
  Why: the two ENERGY event kinds use EIA series codes in a FRED-series config → 404;
  this blocks the **one macro line with a positive signal** (`model_tracks_survey`,
  Spearman 0.59). Tier-1. First action: correct the series-id mapping in the macro
  event config + re-run the producer via the macro workflow; confirm 200s. Done:
  energy event kinds resolve and the calendar populates.
- **R3 · `PB-20260730-REGIME-EVIDENCE-VENUE-FEE-REGRADE`.**
  Why: the shipped `gld_pullback_1h` OFF-cell + equity/ETF regime evidence rest on a
  ~25× fee over-charge (flat crypto-perp bps on commission-free Alpaca equities). The
  close-path estimator was already made venue-aware (07-29); the *regime evidence
  harness* still isn't. Tier-1 evidence (any cell change stays Tier-3). First action:
  re-run the equity/ETF regime evidence with `roundtrip_fee_bps_for` venue-aware. Done:
  gld/equity cells re-graded net-of-correct-cost; confirm or retract.
- **R4 · Build the research→results cost-gate (the highest-leverage frontier fix).**
  Why: validation greenlights strategies the live layer loses on — the cost model
  omits funding+slippage and the gate reads gross/journal, not the paper-portfolio
  mirror net-of-cost (`docs/research/research-to-results-gap-2026-07-30.md`). Tier-1
  to build the gate; Tier-3 to enforce on live promotion. First action: implement a
  `paper-mirror-net-positive` promotion precondition + full-cost re-gate; wire it into
  the M7/M25 gate path. Done: no strategy graduates unless its portfolio-mirror is
  net-positive after full costs.
- **R5 · `BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING`.**
  Why: bybit_1 XRPUSDT resting SL legs diverging 445%→830% over-coverage — same class
  as the real-money 20-leg cap. Tier-2. First action: run `cancel-stale-tpsl-legs`
  (dry-run first) on bybit_1 XRP; confirm the `over_covered` detector clears. Done:
  covered_qty ≈ position size.
- **R6 · Roadmap-parser unmapped-glyph mis-bucket (found this session, Tier-1).**
  `src/web/api/routers/roadmap.py::_normalize_status` maps only
  `✅🔄🔜📋⚠️⛔`; a cell leading with an **unmapped** glyph (`🟡`/`🟢`) falls through
  to a keyword scan that matches any "DONE/COMPLETE/CLOSED" in the cell **body** —
  so M21 (fixed here) was mis-reported as `done`, and any other unmapped-glyph cell
  (e.g. M29 leads `🟢 P0 SCOPE LOCKED`) can mis-bucket too. First action: either add
  `🟡`→pending / `🟢`→in_progress to `_STATUS_EMOJI`, or sweep the ~2 offending cells
  to a mapped glyph; add a CI check that every milestone cell leads with a mapped
  glyph. Done: `/api/bot/roadmap` `summary` counts match the human table for every row.

### B. GATED ON A NAMED PRECONDITION

- **G1 · fc-pcv v2 swap (M25 P4).** Precondition: **SOL** — live_parity accrual
  (13/20 as of 08-01; re-check ~20:30Z tonight). **BTC** — `drift_clean` must settle
  (genuine FAIL KS 0.2543>0.2; re-check after 2–3 days of post-retrain scores). First
  action: at the SOL re-check, if ≥20 parity rows + all gates pass, swap SOL v2 →
  advisory (**restores the SOL advisory head**); hold BTC on drift. Tier-3 swap.
- **G2 · SOL `trend_vol` cell authoring.** Precondition: G1 (SOL advisory head
  restored). Why: the SOL ML vol-gate currently has **zero live-order impact** — no
  cell exists. Tier-3.
- **G3 · MES baseline manifests decision (`MB-20260801-MES-BASELINE-MANIFESTS-NEVER-TRAINED`).**
  Precondition: **operator decision** — teach the staleness sweep an
  `awaiting_source_trades` state vs retire the 3 trade-derived MES manifests. Deeper
  question worth raising: should `mes_trend_long_1d` (never triggered in ~65d) have a
  looser entry, or is MES simply the wrong instrument for a 1d Donchian-24 breakout?

### C. AWAITING OPERATOR TIER-3 / DATA-MUTATION DECISION

- **T1 · `BL-20260801-TELEGRAM-CRED-CRASHLOOPS-MONEY-LOOP` (Tier-3 design).** Today's
  incident: the trader **refuses to boot** without a Telegram token, so a bad rotation
  took the money loop down ~85 min with **both alert channels dead (no page)**.
  Proposal: decouple trader liveness from Telegram-cred validity — a missing alert
  channel should degrade alerting, not halt trading. Operator Tier-3.
- **T2 · `BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED` (Tier-2).** Root cause
  of the bybit_1 journal↔exchange size divergence; remediation operator-gated. (W1
  phantom-row cleanup for bybit_1 + ib_paper is partly in flight by the active session.)
- **T3 · Refresh the broker-truth ledger** (the one genuine operator hand-off —
  only a human can pull the exchange export). The authoritative bybit_2 wallet-truth
  in `comms/broker_truth_ledger.json` is `as_of 2026-07-13` (~3 weeks stale
  [verified-live #8266]); until refreshed, the "real money" authoritative figure
  drifts from reality (the journal per-row pnl under-records — this ledger is the
  only trustworthy lifetime real-money number). Concrete steps:
  1. **Operator (manual):** in the Bybit UM (Unified Trading) account, export the
     **wallet-change / transaction CSV** for `bybit_2` covering **both sub-accounts
     (MAIN + SUB)** over the window `2026-07-13 → today` (the existing ledger's
     `note` records the MAIN −1.52 / SUB −261.01 split and the 2026-05-10
     sub-account switch — same export shape, just the newer window).
  2. **Claude (reviewed run):** feed that export to
     `scripts/ops/reconcile_netting_pnl.py --emit-ledger` (stitches wallet-truth =
     UM-change − transfers across the two sub-accounts) → writes/updates
     `comms/broker_truth_ledger.json` in a normal Tier-1 PR. **NOT a money-DB
     rewrite** — per-row journal `pnl` stays unmodified.
  3. **Verify:** `/api/bot/pnl/broker-truth` `as_of` advances past 2026-07-13 and
     the dashboard/Android "🏦 Broker-truth realized (lifetime)" line updates.
  Runbook precedent: the original ledger was produced this same way (a reviewed
  `--emit-ledger` run from an operator Bybit UM export, per `BL-20260713-BYBIT2-PNL-UNDERRECORD`).

### Already done today (do NOT redo) — from `full-system-review-zajauh`
- **[DONE 08-01]** `backfill-fabricated-exits apply` (#8253) — 14 rows MEASURED.
- **[DONE 08-01]** `slv_trend_1h` min_confidence 0.3 floor — merged + live-deployed
  (#8255, Tier-3 operator-approved, live-verified).
- **[DONE 08-01]** MES baseline datasets built + trained → root-caused to
  "MES never traded" (#8264); decision now pending (G3).
- **[DONE 08-01]** Weekly `RPT-20260801` merged; 29 health backlog rows drained.

## Deferred Items
- The **broker-truth ledger refresh** (§C-T3) — the one genuine operator hand-off
  (needs a human Bybit UM export before the reviewed `--emit-ledger` run).
- **R6 (roadmap-parser unmapped-glyph fix)** — filed as a ready-now Tier-1 item;
  not done here to keep this PR docs-only (an edit to `src/web/api/routers/roadmap.py`
  is code, out of this session's scope).

## Next Recommended Sprint
Pick from §A first (R2 macro-M1 fix and R4 cost-gate are the two highest-leverage
ready-now items). G1 (fc-pcv SOL swap) is time-boxed to tonight's ~20:30Z parity
re-check. Everything in §C needs an operator decision first.

## Wrap-Up Check
- [x] Live state re-verified (diag #8266) — no material drift vs the 07-31/08-01 record.
- [x] Every state claim tagged verified-live or repo-record.
- [x] Disproven vs needs-more-research cleanly separated with quoted verdicts.
- [x] Data-trust matrix explicit (trust bybit_2 exchange-fills + broker-truth + Bybit
      candles; distrust all paper per-row PnL, bybit_1 sizes, IBKR candles, yfinance,
      the exit-capture-deepdive root cause).
- [x] Next-work backlog is autonomous-ready (objective/blocker/Tier/action/done each).
- [ ] doc-freshness run at session end (pending).
