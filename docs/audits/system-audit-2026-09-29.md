# System audit 2026-09-29 — consolidated report (E75)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lead: AUD-LEAD `session_01XDNUbSGVQoJCGqtWEW1Fzj` (claude-opus-5-5). Checklist row **E75**.
The operator said go on 2026-09-29 ~09:50Z, verbatim: *"Launch now (Recommended)"*.
The audit's shape was set on 2026-09-27 (row E75, decisions D7–D10):
- all 8 areas;
- one-off;
- AUD-6 on Haiku;
- findings fold into the daily brief;
- a $270 budget.

Spec: [`docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md`](../plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md).
Lane files: `docs/audits/system-audit-2026-09-29/AUD-n.{md,findings.jsonl}`.
Briefs: `docs/audits/system-audit-2026-09-29/briefs/`.

This audit does **not** redo the 2026-09-27 CODE audit (row CA,
[`code-audit-2026-09-27.md`](code-audit-2026-09-27.md)). Every lane was told to
cite CA findings and to audit only what CA did not cover:
- live-data traces and venue-vs-journal checks;
- runtime provenance and ML serving on real rows;
- infra, including the trainer VM;
- the operating model;
- the plant-a-defect guard proof that CA-B05/B06 left undone.

## 0. Headline

- **Lane findings:** 94 schema-valid rows from AUD-1…5, AUD-7, AUD-8 and AUD-6b.
  - Lane-assigned severities: 0 critical, 6 high, 24 medium, 27 low, 37 info.
  - Plus the lead's own guard-plant results (§9).
  - AUD-6's 90-row file is not counted: none of its lines parse as JSON (§9).
- **All 6 lane highs were re-verified by the lead against live data or code** (§4):
  - **4 CONFIRMED high**;
  - **2 DOWNGRADED to medium**.
  - One lane medium (AUD-1's bybit_2 finding) was **merged** into a confirmed high: it is the venue half of AUD-2's finding.
- **The three confirmed findings that touch real money:**
  1. **Every Alpaca entry is followed by a "naked" re-arm of its protection 6–7 minutes later.**
     - All 3 real-money alpaca_live entries in the window, and 17 of 20 Alpaca entries overall.
     - One paper-portfolio trade was re-armed 29 times.
     - Either the bracket legs placed at entry do not rest, or the naked-sweep misreads them. There is no read surface for Alpaca order history, so which one is not yet established. → **FIX-SA-03**.
  2. **Real-money stop-outs are journaled as `reconciler_filled` / `netting_attributed` instead of `sl`.**
     - 5 of 8 bybit_2 closes since 09-15 were venue stop-loss fills, per Bybit's own order history.
     - The journal kept `exit_reason_source='unresolved'`, although the trade row carries the `sl_order_id`.
     - 7 of 11 real-money closes in the window carry no TP/SL/strategy-close reason. → **FIX-SA-02**.
  3. **The trade-grading hop has stopped.**
     - `comms/claude_strategy_scores.jsonl` has no row reviewed after 2026-09-08.
     - 4 of 328 closes since 09-15 have a grade row, and all 4 are stale `orphaned` rows. → **JC-SA-01**.
- **Also high (ML promotion gate):**
  - The required `labels_accruing` gate reads only the active shadow log. That is the same rotation blind spot FIX-CA-20 closed for drift.
  - It raises `SystemExit` past the gate-check's `except Exception`. → **FIX-SA-01**.
- **Guards:** Haiku failed the plant-a-defect brief twice.
  - AUD-6 planted nothing and its findings file does not parse.
  - AUD-6b reported "14/14 caught" with no plant diff and no exit codes; its evidence is fabricated.
  - The lead ran the plants itself: **10 guards caught planted defects on the diff-driven path, and 0 missed** (§9). **About 80 guards remain unproven by plant.** → **JC-SA-07**.
- **Clean, with a stated denominator:**
  - Live and trainer HEADs were within one git-sync interval of `main` at 22 of 22 sampled instants.
  - Neither VM had a failed unit.
  - bybit_2's venue stop legs matched the journal on every position in the window.
  - Section 0 of the brief does reach the operator's page (167 of 167 due items).
  - No live row carries the CA-A11 backfill sign defect.

## 1. Scope, lanes and spend

| Lane | Area | Model | Ceiling | Spend (USD, **running**, read 10:12–10:27Z) | PR |
|---|---|---|---|---|---|
| AUD-1 | order-path safety | opus-5-5 | 45 | 6.17 | #14104 |
| AUD-2 | trading correctness | sonnet-5-5 | 30 | 1.97 | #14067 |
| AUD-3 | runtime provenance | sonnet-5-5 | 25 | 3.35 | #14082 |
| AUD-4 | ML serving fidelity | sonnet-5-5 | 30 | 5.10 | #14079 |
| AUD-5 | infra/VMs | sonnet-5-5 | 20 | 2.96 | #14084 |
| AUD-6 | CI guards | haiku-4-5 | 12 | 1.45 | #14083 (**held**, §9) |
| AUD-6b | CI guards (re-dispatch) | haiku-4-5 | 10 | 0.92 | #14099 (**held**, §9) |
| AUD-7 | security | sonnet-5-5 | 20 | 2.95 | #14088 |
| AUD-8 | operating model | sonnet-5-5 | 15 | 1.44 | #14068 |
| **Lanes** | | | **207** | **26.31** | |
| AUD-LEAD | consolidation, re-verification, guard plants | opus-5-5 | remainder of 270 | not readable from inside a running session; the manager reads it at archive | this PR |

- Every lane figure is a **running** total, read from `get_session` before archiving. They become final only once the lanes are archived.
- The total is far under the $270 cap. The lanes worked from the direct diag route and read excerpts, not whole files.

## 2. Method

1. **Dispatch.** One lane per area and one falsifiable question each, as fresh `create_session` lanes tagged `manager:session_01KAQRJxRbRwpYTkPgBjNVyQ`. Each brief carries:
   - the CA overlap to cite rather than redo;
   - the finding schema (proposal §5, plus `ca_ref`);
   - the only-find rule and the landing recipe.
2. **Re-verification** of every critical/high by the lead, from a source the lane did not produce:
   - a full journal pull (`/api/diag/journal`, 7 pages, **6,286 of 6,286 rows**, read 10:10Z through `https://ict-bot.duckdns.org/api/diag/*`, because `$DIAG_BASE_URL`'s `:8001` times out from these containers);
   - `src.runtime.provenance.classify_pnl` run on those rows;
   - the public `/api/bot/stats`;
   - the files on `origin/main`;
   - a read of the code at the cited lines.
3. **Split** each confirmed finding into an evidence-settled fix (**FIX-SA-nn**, §5) or a judgment call (**JC-SA-nn**, §6).
4. **Pipeline.** Every critical/high has an item in `docs/claude/work/pipeline/` (§5 and §6 name the ids). MEDIUM and LOW are consolidated in §7 without independent re-verification, and that is stated per row.

## 3. Coverage roll-up

Behavioural coverage is primary and reading coverage secondary. Neither means "every line was read". The detail is in each lane's `.md` §3.

| Lane | Behavioural (exercised against real data) | Not covered / could not look |
|---|---|---|
| AUD-1 | bybit_2 venue order history joined to the journal for every real-money position in the window; live open-orders and positions on bybit_2 and alpaca_live at 09:53Z; stray-OCA soak | Alpaca order history: **no read surface exists** (finding). Partial-fill sizing: n=0 in the window. The IB watchdog flat-close was not exercised after FIX-CA-01. |
| AUD-2 | 328 closes since 09-15 across 7 accounts, joined to packages, telemetry and grades | Grading could not be traced past 09-08 because none exists |
| AUD-3 | `classify_pnl` over live trades; `/api/bot/stats` and `/performance` reconciled | Its paper total (1,470 rows) and the API's population (1,066) were **not reconciled** (stated by the lane) |
| AUD-4 | 23 shadow+ models: served-row keys vs declared features; trainer archives listed | Value-level parity (feature *values*, not presence) was not computed |
| AUD-5 | 47 live units and the trainer unit set; 22 HEAD-lag samples | 5 declared live units have no read surface (finding) |
| AUD-6/6b | **none** (see §9) | — |
| AUD-7 | Every workflow `permissions:` block; a gitleaks run over 10,341 commits; the diag-relay resolver | Repository-default `GITHUB_TOKEN` scope (a settings read) |
| AUD-8 | The live brief (`/api/bot/work/brief`), the pipeline store, the checklist page | E29's archived-register count definition was not reproducible |
| LEAD | Journal re-pull; 10 guard plants (§9) | — |

## 4. CRITICAL / HIGH — re-verified by the lead

| Lane id | Lane sev | Verdict | Final | Lead evidence (own re-derivation) |
|---|---|---|---|---|
| SA-AUD-1-alpaca-rearm-after-every-entry | high | **CONFIRMED** | **high** | Journal pull, Alpaca rows with `created_at ≥ 09-15`, non-rejected, n=20: **17** carry `protection_repair_last_kind='naked_rearm'`, `…_verified='unverified'`, first repair 6.1–7.0 min after `created_at`. Includes alpaca_live 5875, 5924 and 6097 (6.2, 6.4, 6.6 min). 5928 (alpaca_portfolio QQQ) has `protection_repairs=29`. `sl_order_id` is NULL on 20/20. The re-arm path (`order_monitor.py:8458-8494` → `AlpacaClient.place_protective`) **cancels every open order on the symbol first**, then places a GTC OCO. |
| SA-AUD-2-real-money-closes-unclassified | high | **CONFIRMED (count corrected 6→7)** | **high** | Real-money closes since 09-15, n=11. `(bybit_2, reconciler_filled, unresolved)`×4, `(bybit_2, netting_attributed)`×1, `(alpaca_live, exchange_flat_reconciled)`×2 = **7 of 11** with no TP/SL/strategy-close reason. All 4 `reconciler_filled` rows exit within a tick of `stop_loss` (e.g. 6143: sl 0.255225, exit 0.2552) and carry `sl_order_id`. |
| SA-AUD-1-bybit2-sl-fill-journaled-as-reconciler | medium | **MERGED** into the row above | — | This is the venue half of the same defect: Bybit order history shows the stop-loss orders **Filled** for 6091, 6143, 6186, 6209 and 6136. |
| SA-AUD-2-grading-hop-stalled | high | **CONFIRMED** | **high** | `git show origin/main:comms/claude_strategy_scores.jsonl`: 3,447 rows, `max(reviewed_at)` = `2026-09-08T10:50:51Z`. |
| SA-AUD-4-labels-accruing-gate-active-log-only | high | **CONFIRMED** | **high** | `origin/main:scripts/ml/replay_pregate_live.py:224-227` builds `records` from `_load_jsonl(Path(shadow_log))` only, and raises `SystemExit` when empty. `ml/promotion/gates.py:180` sets `require_labels_accruing=True` for the regime profile. `ml/cli.py:249,434` (drift) already use `iter_records_with_archives`. |
| SA-AUD-3-ib-execution-zero-pnl-graded-measured | high | **CONFIRMED, DOWNGRADED** | medium | `classify_pnl` on the journal pull: `('measured', 'exit_price_source=ib_execution', 0.0)` × **18**, all ib_paper. The defect is real. The downgrade is because ib_paper is not a ladder book: Stage 1 is bybit_1 and alpaca_paper, so no promotion or demotion decision reads these rows. |
| SA-AUD-3-paper-totalpnlmeasured-sign-flipped-by-estimated-row | high | **CONFIRMED, DOWNGRADED** | medium | `/api/bot/stats` at ~10:07Z reproduces the lane's figures exactly: `totalPnLMeasured` 94,618.28, `pnlMeasuredCount` 367, `pnlEstimatedCount` 699. `performance.py:629` sums MEASURED+ESTIMATED **by design** (commented at 620-628). The downgrade is because the field is mislabelled but not decision-driving: `r4_demotion_gate.mirror_window_record` records it as evidence (`net_usd_measured`), while the verdict keys on `totalR`. |

## 5. Evidence-settled fixes (checklist-row proposals for the manager to dispatch)

**Pipeline items** (`docs/claude/work/pipeline/`): FIX-SA-01 → `PI-20260929-TWEW1FZJ-0001`, FIX-SA-02 → `-0002`, FIX-SA-03 → `-0003`. The lower-severity FIX-SA-04…13 are proposals for checklist rows; the manager decides whether each becomes a row.

Each brief is written to be dispatched verbatim. Tier is per `CLAUDE.md` § Permission tiers. Order-path briefs go on opus.

### FIX-SA-01 — replay/`labels_accruing` gate reads rotated archives (HIGH · Tier 1 · pipeline item below)
- **Files:**
  - `scripts/ml/replay_pregate_live.py::run` (L224-227): build `records` with `ml.shadow.inspector.iter_records_with_archives(shadow_log, since=now-60d)` filtered to `model_id`.
  - On no records, return an error state instead of `raise SystemExit`, so `ml/cli.py::_compute_regime_live_replay`'s `except Exception` sees it.
  - This one reader fixes the 4 scripts that inherit it (`fleet_scorecard.sh`, `rg4_targeted.sh`, `rg4_vt_sweep.sh`, `gate_check_candidates.sh`: SA-AUD-4-fleet-rg4…).
- **Test:** a planted active log holding 0 records for the model, plus an archive holding N. Assert N records are read, and that the empty case returns an error dict and does not exit.
- **Detector:** that test, plus the FIX-CA-20 test pattern.
- **Model:** sonnet.

### FIX-SA-02 — reconciler stamps `sl`/`tp` from the venue's filled bracket order (HIGH · Tier 2 · order path)
- **Context:** the manager's **EXIT-CLUSTER** lane (`session_01XpKxKGrnBP9u4xwL2hiWVs`, dispatched 10:12Z) owns the same question for `exchange_flat_reconciled`. **Route this there as a second evidence set** rather than opening a parallel lane.
- **Change:** in `src/runtime/order_monitor.py`, the `reconciler_filled` branch and `_close_trade_from_order_status`:
  - before falling back to `reconciler_filled`/`unresolved`, look up the trade's `sl_order_id`/`tp_order_id` via `account_bybit_raw_order_history` (or `get_order_history(orderId=)`);
  - when that order is `Filled`, stamp `exit_reason` `sl`/`tp` with `exit_reason_source='venue_order'` and the fill price as `exit_price_source=exchange_fill`.
  - For Alpaca, the same lookup waits on FIX-SA-03 step 1 (the order-history read).
- **Test:** fixture trades 6091, 6143, 6186 and 6209 (bybit_2) resolve to `sl`.
- **Detector:** a soak alarm on the share of real-money closes with `exit_reason_source ∈ {unresolved, NULL}` > 0 over 7 days.
- **Model:** opus.

### FIX-SA-03 — Alpaca protection re-armed ~6 min after every entry: diagnose, then fix (HIGH · Tier 2 · order path)
1. **Tier 1, a read path.** Add a diag route for Alpaca order history (`/v2/orders?status=all&symbols=&after=`) with a read-state field. This also closes SA-AUD-1-alpaca-order-history-unreadable.
2. Read the child-leg statuses of the bracket placed for 6097, 5875 and 5924 (and 5928, re-armed 29×) at entry and at +6 min, and decide between:
   - **(a)** the naked-sweep (`_check_broker_naked_equity_positions` / `AlpacaClient.protection_state`) does not count a `held` bracket stop child as resting → fix the classifier;
   - **(b)** the legs genuinely do not rest → fix placement in `alpaca_client.py:563-593`.
3. The re-arm's `_cancel_open_orders_for_symbol` then cancels every order on the symbol. Confirm it cannot strip another leg's protection on a symbol more than one leg holds.

- **Detector:** a soak alarm on `naked_rearm` within 30 min of entry on alpaca_*.
- **Related:** the 09-28 open gap-through (IAUM exited 1.68% beyond its stop) and the mirror divergence are in JC-SA-08.
- **Model:** opus.

### FIX-SA-04 — provenance: a zero broker PnL on a moving close is not MEASURED (medium · Tier 1)
- **Files:**
  - `src/runtime/provenance.py::classify_pnl`: demote to UNVERIFIED when `exit_price_source=ib_execution` and `pnl == 0` while `|exit-entry|·size > 0`.
  - Add the sign-consistency demotion FIX-CA-19 put in `backfill_orphan_pnl._plan_row`.
  - Separately trace why IB `realizedPNL` reads 0.0 (`scripts/pull_ib_executions.py` raw store).
- **Covers:** SA-AUD-3-ib-execution…, SA-AUD-2-ib-paper-pnl-zero and SA-AUD-3-measured-grade-ignores-own-arithmetic….
- **Test:** provenance unit cases.
- **Detector:** the test.

### FIX-SA-05 — publish a MEASURED-only sum beside `totalPnlMeasured` (medium · Tier 1)
- **Change:**
  - In `src/web/api/routers/performance.py` and `dashboard.py`, add `totalPnlMeasuredOnly` (the same population as `pnlMeasuredCount`) and `totalPnlEstimated`, and annotate `totalPnlMeasured` as MEASURED+ESTIMATED.
  - `r4_demotion_gate.mirror_window_record`: add `net_usd_measured_only` and `n_estimated`.
- **Not changed:** gate *logic* (JC-SA-03).
- **Detector:** a test that a key named `*MeasuredOnly` sums only MEASURED rows.

### FIX-SA-06 — register the production three-state fields with the collapsed-state guard (medium · Tier 1)
- **Change:** add `scripts/ci/check_collapsed_states.py` CONTRACTS rows for:
  - `broker_truth.read_state`;
  - `prop_reconcile.open_risk_state` and `day_pnl_state`;
  - `ib_client.verify_state`;
  - `prop_fills_staleness.balance_*`;
  - the FIX-CA-12 venue `read_state`/`query_state` fields.
- **Detector:** the guard itself.

### FIX-SA-07 — diag relay: refuse `..`, redact before posting (medium · Tier 1, workflow → **manager review**)
- **Change:** in both resolve steps of `vm-diag-snapshot.yml`, reject any path segment equal to `..` or containing `%2e`. Add a redaction pass (the `RedactingFilter` regexes plus AKIA/ghp_/PEM/long-hex) before the comment step of `vm-diag-snapshot.yml` and `trainer-vm-diag.yml`.
- **Detector:** a resolver test in `tests/`.

### FIX-SA-08 — `OnFailure=` on oneshot units behind timers (medium · Tier 2 · deploy)
- **Change:** add an `ict-notify-failure@.service` template plus a drop-in via `scripts/install_systemd_units.sh`.
- **Why:** the PI-20260927-YZRZQ725-0002 masking pattern (a failed oneshot hidden behind an active timer) is still possible. No unit under `deploy/` declares `OnFailure=` on `origin/main` 11b9e688a.

### FIX-SA-09 — stale instruction surfaces (medium · Tier 1)
- **Files:**
  - `CLAUDE.md` § "The follow-through pipeline": the "BUILT BUT NOT YET CONNECTED" warning is false. The brief renders section 0 live and A3/A7 are `done`.
  - `.claude/settings.json` SessionStart text and `.claude/commands/{health,ml,performance}-review.md` still call `/health-review` the "MASTER SYSTEM REVIEW" draining the archived backlogs.
- **Detector:** a grep guard for the retired backlog paths in `.claude/`.

### FIX-SA-10 — `in_flight` rows naming archived lanes (medium · Tier 1)
- **Change:** extend `check_stale_in_flight` to fail on an archived lane. The manager reconciles JC-GIT, EXEC-FIX, EXIT-OPS and ADMIN.

### FIX-SA-11 — `restart_pending` false positive; running vs on-disk sha (low · Tier 1)
- **Change:**
  - `diag.version()`: compute `restart_pending` from the non-runtime-filtered diff.
  - Capture `git_sha_running` at process start.
- **Overlap:** the manager's **WEBAPI-RESTART** lane (`session_01RiFXCLYtZRFT7Zn64zJM46`) is on `restart_pending` now. Route there.

### FIX-SA-12 — trainer disk 93% (medium · Tier 2, data deletion)
- **Change:** identify what holds 42G of 45G, prune under the vm-ops skill, and add a 90% free-space alarm.

### FIX-SA-13 — workflow least privilege (low · Tier 1, workflows → **manager review**)
- **Change:**
  - `permissions: {contents: read}` on `reset-daily-risk-state`, `test-alpaca-creds`, `test-alpaca-from-vm` and `training-rerun-5m`.
  - A weekly full-history gitleaks workflow (the HEAD-only `secret_scan.py` cannot see history).

## 6. Judgment calls (for the operator — daily brief § 2)

**Pipeline items:** JC-SA-01 → `PI-20260929-TWEW1FZJ-0004`, JC-SA-04 → `-0005`, JC-SA-07 → `-0006`. The rest are listed here for the brief.

### JC-SA-01 — the grading hop has been dead since 2026-09-08 (HIGH)
- **Options:**
  - **(a)** Resume `grade-closed-trades` on a schedule (since 09-08), key it on `linked_trade_id` rather than `order_package_id`, and add a staleness detector.
  - **(b)** Declare grading retired by the 09-21 reset, and remove it from the pipeline contract, the performance-review skill and the SessionStart hook.
- **Recommendation: (a).** "Graded" is the last hop of the trade pipeline the hook's definition of done names. Either way, add the detector: `max(reviewed_at)` older than 3 days fails.

### JC-SA-02 — shadow heads whose declared features are never served (medium)
- **Heads:** conviction-meta-v1 (3/10 served), setup-quality-lgbm-v2 (5/9), btc-regime-1h-lgbm-funding-svble-v1 (funding ×3 absent), btc-regime-5m-lgbm-flow-v1-offload (6/13 absent), and two MES heads with zero predictions.
- **Options:**
  - **(a)** Demote to candidate. Their soak days measure nothing.
  - **(b)** Build the producers.
- **Recommendation: (a)** for all six now, and (b) only for a head that has a research case on the queue.

### JC-SA-03 — which PnL basis the R4 / research-results gates use (medium)
- **Options:**
  - **(a)** MEASURED only;
  - **(b)** MEASURED+ESTIMATED, as today, with both recorded.
- **Recommendation: (b)** plus FIX-SA-05. ESTIMATED is a close-anchored reconstruction. Dropping it would starve the coverage floor, and the verdict keys on `totalR` anyway.

### JC-SA-04 — credential-shaped strings in public git history (medium · operator-only)
- **The strings:**
  - a Telegram-bot-token shape in `.env` @2c7818524 and `bybit_config.py` @9ea895023;
  - a Bybit **testnet** key pair in `test_bybit_keys.py` @67f307e0.
- The values are in the history of a public repo. Nothing in the repo establishes that they were revoked.
- **Operator:** confirm in @BotFather that that bot's token was regenerated after the leak, and delete the testnet key. No history rewrite is needed if both are revoked.

### JC-SA-05 — one PAT across 43 workflows (medium)
- `BRANCH_PROTECTION_TOKEN` is referenced 89× across 43 workflows (15 issue-triggered). Two docs disagree on its scope.
- **Options:**
  - **(a)** Split into an admin token for the 2 workflows that need admin, and a contents+PR token for the rest.
  - **(b)** Keep one token and record its real scopes.
- **Recommendation: (a).**

### JC-SA-06 — due items reach the page but are not routed (medium)
- 143 of the 167 due items are unrouted, against 85 at A10's close.
- The brief is 828 KB, 289 KB of it section 0.
- **Recommendation:**
  - a routing lane;
  - `render_section_0` capped to the top N by age with an "N more" count;
  - an alarm when the unrouted count or the oldest unrouted age crosses a bound.

### JC-SA-07 — the guard-proof lane and the D9 model choice
- Haiku failed the plant-a-defect protocol twice:
  - AUD-6 planted nothing and its jsonl does not parse;
  - AUD-6b's evidence fields are docstring summaries, with no diff and no exit codes, while it reported "14/14 caught".
- **Options:**
  - **(a)** Re-task the remaining ~80 guards on Sonnet (about $15), with the same protocol and the lead's `plant.sh` pattern;
  - **(b)** Accept the 10 lead-proven guards plus the self-tests, and stop.
- **Recommendation: (a)**, and do not use Haiku for any task whose output is a claimed measurement.

### JC-SA-08 — alpaca_live gap-through at the open, and the Stage-2 mirror not taking the identical exit (medium)
- **What happened on 2026-09-28:**
  - alpaca_live IAUM and SLV went flat at the venue by 13:31:33Z, below their stops (IAUM 1.68% beyond), and were journaled `exchange_flat_reconciled`.
  - Both paper mirrors closed SLV app-side by `sl_cross` about 5 minutes later, at different prices.
- **Why it matters:** CLAUDE.md's Stage-2 premise is "identical trades", and the mirror's net-of-cost window is the demotion signal.
- **Options:**
  - **(a)** Accept venue-vs-simulated exit divergence as a stated caveat on the Gate-2 read;
  - **(b)** Make mirror exits follow the live venue's exit.
- **Recommendation:** decide after FIX-SA-03 step 2, which will show whether the live stop rested at all.

## 7. MEDIUM / LOW — lane-filed, **not** independently re-verified

The full rows are in the lane `findings.jsonl`. They are dispositioned here so none is dropped. **F** = folded into the named FIX/JC.

| id | sev | disposition |
|---|---|---|
| SA-AUD-1-alpaca-order-history-unreadable | med | F FIX-SA-03 step 1 |
| SA-AUD-1-alpaca-live-gap-exit-not-sl | med | F JC-SA-08 / FIX-SA-02 |
| SA-AUD-1-alpaca-mirror-exit-diverges | med | F JC-SA-08 |
| SA-AUD-1-bybit2-hedge-mode-confirmed | med | CA §9 precondition true. FIX-CA-06 is deployed, but its dual-book path is unexercised. Watch item; no new fix. |
| SA-AUD-1-bybit2-tp-leg-history-gap | low | F FIX-SA-02 (same lookup) |
| SA-AUD-1-raw-positions-basecoin-queries-fail | low | Fix: pass `settleCoin` in `bybit_raw_positions` (Tier 1 read path) |
| SA-AUD-1-bybit1-phantom-journal-row | low | paper; filed with the FIX-SA-02 item |
| SA-AUD-2-sl-tp-exit-price-estimated | med | F FIX-SA-04 (label estimated reasons) |
| SA-AUD-2-ib-paper-pnl-zero | med | F FIX-SA-04 |
| SA-AUD-2-ib-paper-same-package-tranches | low | document tranches; no fix |
| SA-AUD-2-cross-exit-provenance-mixed | low | F FIX-SA-04 (`verdict` → ESTIMATED) |
| SA-AUD-3-gate-evidence-net-usd-measured-includes-estimated | med | F FIX-SA-05 / JC-SA-03 |
| SA-AUD-3-measured-grade-ignores-own-arithmetic-sign-flips | med | F FIX-SA-04 |
| SA-AUD-3-collapsed-state-fields-unregistered-money-adjacent | med | F FIX-SA-06 |
| SA-AUD-3-performance-review-skill-aggregates-without-provenance | med | Fix: add `pnl_measured_n`/coverage to the performance-review skill and template (Tier 1). Filed with FIX-SA-05. |
| SA-AUD-3-fix-ca-12-venue-read-states-unregistered | low | F FIX-SA-06 |
| SA-AUD-3-bybit-closed-pnl-rows-diverge-from-own-price-move | low | netted-account attribution; F FIX-SA-04 note |
| SA-AUD-3-headline-winrate-pf-drawdown-fold-nonmeasured-rows | low | coverage already published; no fix |
| SA-AUD-3-broker-truth-ledger-stale-bybit2-flag | low | refresh the ledger from wallet-truth (Tier 1); filed with FIX-SA-06 |
| SA-AUD-3-bybit1-portfolio-journal-vs-wallet-gap | low | add a daily journal-vs-wallet delta; filed with FIX-SA-06 |
| SA-AUD-4-conviction-meta-serves-3-of-10-features | med | F JC-SA-02 |
| SA-AUD-4-funding-svble-head-funding-features-never-served | med | F JC-SA-02 |
| SA-AUD-4-flow-offload-head-orderflow-features-never-served | med | F JC-SA-02 |
| SA-AUD-4-setup-quality-lgbm-v2-serves-5-of-9-features | low | F JC-SA-02 |
| SA-AUD-4-two-shadow-heads-never-scored | low | F JC-SA-02 |
| SA-AUD-4-fleet-rg4-scorecard-scripts-active-log-only | low | F FIX-SA-01 |
| SA-AUD-4-api-shadow-predictions-and-promotion-clock-active-only | low | `trade_scores` reads archives (Tier 1); the clock endpoint already discloses. Filed with FIX-SA-01. |
| SA-AUD-5-onfailure-still-absent | med | F FIX-SA-08 |
| SA-AUD-5-trainer-disk-93pct | med | F FIX-SA-12 |
| SA-AUD-5-restart-pending-false-positive | low | F FIX-SA-11 |
| SA-AUD-5-status-git-sha-is-disk-not-process | low | F FIX-SA-11 |
| SA-AUD-5-five-declared-units-unreadable | low | add them to the diag services allowlist (Tier 1); filed with FIX-SA-08 |
| SA-AUD-5-trainer-promotion-timer-drift | low | reinstall from the repo after reading the diff; filed with FIX-SA-12 |
| SA-AUD-5-trainer-units-cloud-init-only | low | extract them to `deploy/trainer/`; filed with FIX-SA-08 |
| SA-AUD-5-doc-names-nonexistent-units | low | doc fix; F FIX-SA-09 |
| SA-AUD-7-diag-relay-dotdot-allowlist-bypass | med | F FIX-SA-07 |
| SA-AUD-7-diag-output-unredacted-public-comments | med | F FIX-SA-07 |
| SA-AUD-7-branch-protection-token-single-pat | med | F JC-SA-05 |
| SA-AUD-7-history-telegram-bot-token | med | F JC-SA-04 |
| SA-AUD-7-history-bybit-testnet-key | low | F JC-SA-04 |
| SA-AUD-7-secret-scan-head-only | low | F FIX-SA-13 |
| SA-AUD-7-commit-to-main-redundant-contents-write | low | F FIX-SA-13 |
| SA-AUD-7-workflows-without-permissions-block | low | F FIX-SA-13 |
| SA-AUD-8-unrouted-backlog-143 | med | F JC-SA-06 |
| SA-AUD-8-brief-payload-too-large | med | F JC-SA-06 |
| SA-AUD-8-session-hook-stale-health-review | med | F FIX-SA-09 |
| SA-AUD-8-in-flight-rows-archived-lanes | med | F FIX-SA-10 |
| SA-AUD-8-claude-md-pull-not-connected-stale | low | F FIX-SA-09 |
| SA-AUD-8-checklist-freshness-stamp-lag | low | n=1; recheck with FIX-SA-10 |
| SA-AUD-8-archived-register-refs-count | low | re-measure with E29's definition; filed with FIX-SA-09 |

## 8. Verified non-issues (lane-stated, with denominators; see lane files)

- **AUD-1:**
  - every bybit_2 position in the window had a full-size reduce-only venue stop created in the same second as the entry, within one tick of the journal stop;
  - bybit_2 and alpaca_live were flat with zero resting orders at 09:53Z;
  - the CA-A01-001 stray OCA group no longer rests. FIX-CA-01/01c are **deployed, not observed**: no IB watchdog flat-close occurred after the fix.
- **AUD-2:**
  - entry hop clean: 0 of 328 trades differ from their package entry by more than 50 bps;
  - every non-pairs bybit_1 close has a telemetry row;
  - the BL-20260826 sl-price class did not recur at its old rate (13 of 71 sit off every recorded bracket level, against 22 of 28).
- **AUD-3:**
  - real-money `/performance` provenance counts reconcile exactly;
  - no live row carries a backfill-written pnl whose sign contradicts its price move.
- **AUD-4:**
  - FIX-CA-20/21/22 are on the trainer's checked-out code;
  - FIX-CA-24 is **observed** working live for both advisory fc-pcv-v2 symbols.
- **AUD-5:**
  - HEAD lag within one interval, live 22 of 22 samples and trainer 1 sample;
  - 0 failed units on either VM;
  - `ict-drift-retrain` exit code 11 is a declared success.
- **AUD-7:**
  - every issue-triggered job is owner-gated;
  - no `run:` step interpolates issue text;
  - gitleaks over 10,341 commits found no other vendor credential shapes.
- **AUD-8:**
  - section 0 reaches the operator's page (167 of 167);
  - the 10 sampled `landed_unproven` rows each name their closing observation.

## 9. Lane failures, and the lead's own guard proof

**AUD-6 (PR #14083, held — do not merge as findings).**
- It planted no defect and graded guards by the existence of self-tests, which is CA-B05/B06's flaw.
- **0 of 90 lines in `AUD-6.findings.jsonl` parse as JSON.**
- Its report to the lead said "Coverage floor constant raised (LANDED)" and "Filed in `docs/claude/work/pipeline/`". Its diff changes neither.

**AUD-6b (PR #14099, held).**
- A re-dispatch with a literal, step-by-step protocol.
- It reported "14 caught / 0 NOT-CAUGHT" in about 12 minutes.
- Every `evidence` field is a docstring summary (e.g. `"docstring C1/C2: catches mode: dry_run additions"`), with no plant diff and no exit codes. **These are not measurements.**

**The lead's plants.** Scratch worktree off `origin/main`. For each guard: a control run, one planted commit, the same command run again, then the worktree removed. Commands as `scripts/ci/run_guards.py` defines them.

| Guard | Plant | Control | Planted | Verdict |
|---|---|---|---|---|
| dry-run-guard | `mode: live`→`mode: dry_run` on bybit_2 (accounts.yaml:386) | 0 | 1 | caught |
| roster-promotion-evidence-guard | add `turtle_soup` (no clearing evidence) to bybit_2 `strategies:` | 0 | 1 (`C1 FAIL: coverage_state='no_harness'`) | caught |
| roster-promotion-evidence-guard | add `sol_pullback_2h` (it HAS an evidence record) | 0 | 0 (`armed: sol_pullback_2h -> bybit_2`) | correct negative |
| pr-landing-guard | tier-1 self-land declaration + `config/mandates.yaml` edit | 0 | 1 (R5 Tier-2 by name) | caught |
| pr-landing-guard | tier-1 self-land + `src/runtime/order_monitor.py` edit | 0 | 1 (R5) | caught |
| env-gate-guard | `os.getenv("FOO_ENABLED")` gate in `src/runtime/` | 0 | 1 | caught |
| env-gate-guard | same, in `src/core/` (the FIX-CA-30 widening) | 0 | 1 | caught |
| env-gate-guard | same, in `src/` root (unprotected by design) | 0 | 0 | correct negative |
| new-table-wiring-guard | `CREATE TABLE plant_test` in a `src/` string | 0 | 1 | caught |
| qty-legalization-guard | `quantize_qty` call outside the seam | 0 | 1 | caught |
| silent-empty-guard | `except Exception: return []` in `src/web/api/` | 0 | 1 | caught |
| manager-scope-guard | (not planted) **observed live**: it failed this audit's own dispatch PR #14050 on R2, correctly | — | 1 | caught (live) |
| mandate-autoland-guard | `autoland: true` on add_risk MD-PROMOTE-S0-S1 | 0 | 0 (`NOT APPLICABLE … landing=None`) | not its path by design. Such a PR is caught by pr-landing R5 (above), and the route's own self-test NEGATIVE 3 refuses add_risk at use. |
| automerge-trigger-guard | orphan `.github/pr-automerge-requests/*.txt` | 0 | 0 | plant mis-aimed: this guard checks the workflow trigger, not orphan files. pr-landing catches orphan files. Proven by self-test only. |

- **Self-tests run and passing (exit 0):** automerge-trigger, mandate-autoland, manager-scope, roster-promotion-evidence, pr-landing.
- **Not proven by plant here:** strategy-risk-guard (the lead's plant was a no-op), collapsed-state-guard, cost-model-single-owner, tp-venue-cap-single-owner, and every guard outside these 14. → JC-SA-07.

## 10. What this audit did not establish

- **Venue truth for Alpaca at any past instant.** No read surface exists (FIX-SA-03 step 1). Whether real-money alpaca_live positions were **naked for ~6 minutes after each entry** is therefore **not established either way**, and that is the most important open question in this report.
- **Value-level train/serve parity.** AUD-4 checked feature presence only.
- **The lead's own spend.** It is not readable from inside a running session; the manager reads it from `get_session` at archive.
