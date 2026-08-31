# Sprint Log: S-WALLET-TRUTH-SCOPE-AND-STRAY-OCA-ARM-2026-08-31

## Date Range
- Start: 2026-08-31
- End: 2026-08-31

## Objective
- Primary goal: Re-sweep the 28 e35 legs at an OOS target that clears the R4 power floor, then DISPOSITION each pass — actioned with the change named, or refused with the reason.
- Secondary goals: Lane P verdict diff on the real ledger; the e35 reversed-leg Tier-3 proposal; establish how far Bybit transaction-log retention actually reaches; investigate the stray-OCA blocker.

## Tier
- Tier 1 for the research, records and proposals; **Tier 2** for the stray-OCA arming (operator-approved in-conversation, "Arm and monitor").
- Justification: everything else is docs/records/tooling with no `src/`, `config/` or order-path change. The one exception is `PROTECTION_STRAY_GROUP_MODE=apply` + `..._ACCOUNTS=ib_paper`, which arms a path that CANCELS a live position's resting protective legs — a runtime mutation, hence Tier 2 and hence gated on an explicit operator OK, which was given.

## Starting Context
- Active roadmap items: M40 (research disposition), e35 bracket geometry, the R4 power gate.
- Prior sprint reference: the 2026-08-29 e35 matrix re-check + resweep verdict diff.
- Known risks at start: two traps flagged in the handoff — a pass-matcher for `pass`/`ok`/`promote` returns zero (the verdicts are `wf_pass`/`path_b_wf_pass`), and `n_oos` is stamped only on gated rows. **Both were real and both were hit** (see Contradictions).

## Repo State Checked
- Branch or commit reviewed: `origin/main` from `f2da099b` through `8d4e3be6`.
- Deployment state reviewed: `ict-trader-live` restarted 20:11:41Z, post-restart `active`; process env verified via `/proc/<MainPID>/environ`.
- Canonical docs reviewed: root `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `ROADMAP.md`, coordination board #6927.

## Files and Systems Inspected
- Code files inspected: `src/runtime/bybit_wallet_truth.py`, `scripts/research/research_disposition.py`, `src/runtime/stray_oca_groups.py`, `src/units/accounts/ib_client.py`, `src/prop/montecarlo.py`, `src/prop/account_rulesets.py`, `scripts/prop/account_compat_matrix.py`, `tests/test_e35_achieved_oos_count.py`.
- Config files inspected: none changed. `.env` on the live VM written via `set-env` (two keys).
- Docs inspected: `docs/research/e35-matrix-recheck-2026-08-29.md`, `exit-refinement-coverage.json`, `comms/broker_truth_ledger.json`.
- Services or timers inspected: `ict-trader-live.service`.
- GitHub Actions workflows inspected: `system-actions.yml` (set-env / get-env parsing), `e35-bracket-sweep.yml`.

## Work Completed
- **Item 1 — e35 re-sweep at `split_target_oos=60` (#10602).** Operator chose 60 ("power first"); at 50, 13 of 14 legs sat under the 49.06 power floor. 17 of 27 cleared at 60. 28 dispositions recorded (1 m20 + 27 e35); `unread` went to **0**.
- **Item 2 — Lane P measured on the REAL ledger (#10608).** The survival gate does not discriminate. Refuted a backlog row's claim that survival/p_breach are constants — by reading the code (`daily_loss` is still terminal; 11/11 accounts declare `daily_loss_pct`) and then empirically (a single 0.9997 reading among 50).
- **Item 3 — Bybit wallet-truth deep pull (#10611).** Both hypotheses in the open item REFUTED. Retention is not the constraint: `days=400` returns exactly the same 631 rows as `days=138`. The cause is **sub-account scope** — over the ledger's own window the live path returns **−1.52**, matching the ledger's own `MAIN -1.52` component to the cent, and cannot see `SUB -261.01`.
- **Item 4 — stray-OCA arming (Tier-2).** `PROTECTION_STRAY_GROUP_MODE=apply`, `PROTECTION_STRAY_GROUP_ACCOUNTS=ib_paper`. Verified on the running process, not the `.env`.
- **Item 5 — the e35 `passed_unshipped` proposal** (`docs/research/e35-passed-unshipped-proposal-2026-08-31.md`).
- **Item 6 — `N_FIELD`'s stale measurement corrected** and its unpinned status stated explicitly.

## Validation Performed
- Tests run: `pytest tests/test_e35_achieved_oos_count.py tests/test_research_chain_end_to_end.py` → 12 passed. CI green on all merged PRs (`guards`, `pytest-run`, `pytest-collect`, `repo-inventory`).
- Dry-runs or staging checks: `research_disposition.py --record --dry-run` before every write; `check_open_items.py`, `check_backlog_refs.py --base`, `check_backlog_criteria.py --base` before each commit.
- Manual code verification: the stray-OCA symbol-scoping line read directly (refuting the cross-symbol hazard); `account_compat_matrix.py`'s `route = positive and survives and low_breach` read directly (`dd_model_state` is written into the result and never enters `route`).
- **Gaps not yet verified:** the stray-OCA sweep has never CANCELLED anything — armed is not exercised. The 9 e35 legs shipped 2026-08-30 are still deployed-not-proven. The full pytest suite cannot run in this sandbox (108 collection errors, `pyo3_runtime.PanicException`), so CI is the only authority for it.

## Documentation Updated
- Rules doc updates: none required.
- Architecture doc updates: none — no schema, contract or API shape changed.
- Trade pipeline doc updates: none — no pipeline stage touched.
- Roadmap updates: M40 outcome paragraph (landed in #10602).
- Subsystem doc updates: `src/runtime/bybit_wallet_truth.py` docstring (sub-account scope); `research_disposition.py` `N_FIELD` comment.
- Historical docs marked superseded: the `OI-…E35-REVERSED-LEGS` row's "15 cells / 10 legs" framing, marked stale in place.

## Contradictions or Drift Found
- **Contradiction 1 — `main` went RED and no single commit was at fault.** #10543 added a test pinning `split_target_oos` to `{50}`; my re-sweep landed target-60 rows via #10604. Each PR green alone; red only once both landed. A semantic merge conflict per-PR CI structurally cannot catch.
- **Contradiction 2 — the `OI-…E35-REVERSED-LEGS` row was stale by 5×.** It claimed 15 cells across 10 legs; the matrix now holds **2** `passed_unshipped` (`shipped` 8 → 17). Corrected in place.
- **Code/doc mismatch — the matrix names the wrong winner.** For both remaining legs the matrix names `sm1.5`, which is `path_b_wf_pass` with BOTH the IS and OOS gates refusing on `maxdd_worse`. The only Path-A pass on either leg is gld's `tp4`, at zero drawdown cost, which the matrix does not name.
- **Also found:** `N_FIELD`'s justifying comment was stale on two of three terms; the `/api/diag/bybit_wallet_truth` route reports the window ASKED FOR, never the data's own span.

## Risks and Follow-Ups
- Remaining technical risks: the stray-OCA `apply` path is live on ib_paper and unexercised; the one prior auto-remediation of this class cancelled the leg that MATCHED the journal.
- Remaining product decisions (Tier 3): the two-leg e35 proposal (recommend DECLINE spy; `tp4` not `sm1.5` for gld, or decline both); the `gld_pullback_1h` geometry decision still wants a walk-forward clearing current live geometry.
- Blockers: none.

## Deferred Items
- Deferred item 1: the Bybit **sub-account join** — new work, not a flip; it is what the wallet-truth row's superseded `clears_when` now requires.
- Deferred item 2: pinning the 97.6% target-vs-achieved measurement (filed as `BL-20260831-N-FIELDS-DECIDING-MEASUREMENT-IS-RECORDED-BUT-NOT-PINNED`, deliberately left to #10610's author rather than opened as a third concurrent edit to that file).

## Post-Wrap Addendum (docs sweep, same session)

A `/doc-freshness` pass after the main wrap found three things the log above predates:

- **Two OPEN-ITEMS rows chased one condition.** `OI-20260826-STRAY-OCA-SWEEP-SHIPPED-BUT-UNARMED` and the
  `OI-20260831-…-ARMED-BUT-HAS-NEVER-CANCELLED` row I filed had the SAME `clears_when`. Merged into the
  older canonical row (it carries the investigation history); my duplicate was removed. Its id still reads
  `UNARMED` and is deliberately not renamed — ROADMAP and several backlog rows link it by name — so its
  summary is authoritative and says so.
- **`ROADMAP.md` still said "NOTHING IS ARMED"** about this sweep, inside a verbatim historical paragraph.
  Corrected in place with a dated marker rather than rewritten, since the paragraph is a record.
  ⚠️ I briefly mis-read that line as evidence I had armed past a live blocker. I had not: the MES finding
  was read first (it is the operator's "investigate the MES blocker" decision, recorded in that row's own
  `observation`), and the venue re-read at 21:09:25Z confirms the analysis rather than contradicting it.
- **The research READ debt was unlogged.** Filed
  `OI-20260831-RESEARCH-READ-DEBT-11-UNREAD-AND-256-SUPERSEDED-UNREAD`. 11 live-unread `gld_compat` units
  from a 17:20:54Z run, which arrived *after* the backlog row asserting "unread 0" — that row's figure was
  corrected in place.

**A near-miss worth recording:** my first edit to `research-review-backlog.json` reformatted the whole file
(202/202) because I wrote `indent=1` where that file uses `indent=2`. Reverted and redone through
`backlog_append.detect_format`, giving **1/1**. `OPEN-ITEMS.json` really is `indent=1`; the two registers
differ, and assuming one format across them is exactly the ~21k-line re-attribution `backlog_append.py`
exists to prevent.

## Next Recommended Sprint
- Suggested next sprint: work the stray-OCA arming to its clears_when — read the soak for a real `acted:true` cancel and confirm the surviving protection against a FRESH `/api/diag/ib_open_orders` read; then take the two-leg e35 decision.
- Why next: the arming is a live order-path capability that is deployed and unproven, and it acts on positions that exist right now.
- Required verification before starting: re-read `OI-20260831-STRAY-OCA-SWEEP-ARMED-ON-IB-PAPER-BUT-HAS-NEVER-CANCELLED`, and re-read the coordination board BEFORE starting any fix — this session duplicated another session's work by reading the board only at session start.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated — N/A, no pipeline stage touched.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
