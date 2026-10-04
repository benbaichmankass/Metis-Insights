# Ops decisions queue — 2026-10-04 (lane OPS-FIXES)

> **Doc status:** `unknown` · category `plan` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · a decision queue written 2026-10-04 by lane OPS-FIXES (dispatched by Manager Session 2026-10-01); the pipeline rows it names are the system of record.

The operator asked on 2026-10-04: "make sure that we've implemented everything that we saw that needs to get fixed". Lane OPS-FIXES fixed every in-tier row it could; the PRs are listed at the bottom. What follows are the rows that are **Tier-3, an operator policy question, or a reversal of an earlier manager decision**. None of them can be settled by more data. Each one gives the exact choice and the evidence it rests on.

## A. Decisions for the operator

| # | Row | Question | Options | Evidence |
|---|---|---|---|---|
| A1 | PI-20261004-GCFA5DOR-0001 (OA-03) | breakout_1 balance is $4,698, below its $4,700 static DD floor. The executor latched AUTO-REVERT at 03:49:36Z after a clicked submit registered nothing on the venue. | (a) Does Breakout treat this as a failed account, and is a new account wanted? (b) The documented revert WAS run by lane OPS-AUDIT (PROP_EXECUTOR_MODE=read_only #16250; breakout timer disabled #16275, per the #16302 note), so the open question is only whether to re-enable after (a), and (c) why a clicked submit registered nothing. | diag #16213/#16216; docs/audits/system-audit-2026-10-04.md § OA-03 |
| A2 | PI-20261004-GCFA5DOR-0005 (OA-10) | The hold-merge alarm has never delivered a ping, because it opens its issue with GITHUB_TOKEN. There are 171 open stale alarm issues. | (1) Deliver as-is: switch to BRANCH_PROTECTION_TOKEN. This pages on every manager merge of a held PR, because the alarm counts only GitHub APPROVED reviews and none exist. (2) Teach the alarm to accept recorded approvals (checklist quote, PR body or mandate), then deliver. (3) Retire the alarm. In every case, close the stale issues. | audit § OA-10 |
| A3 | PI-20261004-GCFA5DOR-0006 (OA-11) | No approval quote was found for #16183 or for #14899/#14643/#14585. Five data-backed autonomous merges (#15593, #15340, #14590, #14577, #15595) have no notification artifact. | The manager confirms which notifications were sent, or sends them now, and records the approvals. A report only; no policy change is proposed. | audit § OA-11 |
| A4 | PI-20261004-GCFA5DOR-0007 (OA-12) | #15830 (restart-safe decision bars) changed **entry timing** on live Stage-1/2 legs inside a Tier-2 restart fix, with no backtest. After a restart, a forming leg can enter up to tf/16 after the close. | (1) Sign off on the current behaviour. (2) Add a per-leg opt-in `decision_bar_catchup: true`, default off. | audit § OA-12; src/runtime/intent_multiplexer.py ~532-629 |
| A5 | PI-20261004-PUQ1APTH-0004 | tradeify_1 tickets are sized at **$75** risk but the executor's flat-mode cap is **$50**. The resize is a no-op at 4.97 lots, so every ETH ticket is REFUSED while the account reads live. | Which number is right: the ruleset's $50 cap or the ticket's $75? Then make the resize actually shrink lots. Tier-3 (prop sizing). | diag journalctl ict-prop-executor@tradeify_1 2026-10-04T08:03:18Z |
| A6 | ML review 2026-10-04 (comms/reviews/ml-review-20261004-125000.json) | Two Tier-3 ML items. (i) The shadow head `setup-quality-audit-baseline-v0` is constant by construction: it keys on `audit_pattern`, which never exists at serve time, so all 1508 rows score -0.0572. (ii) Remove c_setup and c_wr from `expected_optional_features` in ml/configs/conviction-meta-v1-bt.yaml. | (i) Soft-off the head: yes or no. (ii) Approve the 2-line manifest PR: yes or no. | the ML review record |

## B. Manager decisions (review of held PRs)

| # | PR | What needs a decision |
|---|---|---|
| B1 | #16392 (OA-04/05/06, prop order path) | **This reverses an earlier manager decision.** The watchlist-row double-click opener now runs only on a positive one-click `off` read. Before, an unreadable toggle still allowed it: manager comment on #14947/#15138, "a trade must never be missed for want of an opener". On breakout_1, which has no ask_opener, an `unknown` read now falls to the Symbol-cell double-click and then the workspace fallback, not the row double-click. The trade-off: a possible missed entry versus a possible instant UNBRACKETED market order. Accept, or keep the old gate for accounts without an ask_opener. |
| B3 | #16388 (research verdicts) | Results already committed for RQ-20260929-105/106/107 still read `no_action_warranted`, so the grader would close them `done` from those rows. They were graded by hand as pass/fail/indeterminate. Re-dispatch them, or record the hand grades. |
| B2 | #16381 (commit-to-main wait 30→50) | This supersedes STAMP-RACE's "do not raise the timeout". That decision was made against a 13-second miss while pytest still fit inside 30 min. Pytest-run now measures 34.6–37 min, so every normal landing on a busy day reads red. The PR is HELD under R12 (landing machinery). |

## C. Rows this lane did NOT close, and why

- **PI-20261004-GCFA5DOR-0004 (OA-07 a/b, LOW, prop):** the limit-price check in `submit_label_mismatch` and the SL/TP tolerance in `classify_confirmation`. Not done here. They touch the same files as #16392, so they should go in a follow-up branch after #16392 lands. Row stays open.
- **PI-20261004-GCFA5DOR-0010 (a) (trainer OnFailure):** the premise was wrong. `install_systemd_units.sh` never runs on the trainer VM, and a trainer-side ping has no sender. A real fix needs a trainer-side installer plus a delivery path. Row stays open; details are in #16391.
- **PI-20261004-PUQ1APTH-0005 (halted executor reads `failed`):** a Tier-2 prop-executor exit-code and status change. Not done this lane. Row stays open.

## D. What this lane shipped (2026-10-04)

| PR | Tier / landing | Rows |
|---|---|---|
| #16380 | T1 self-land | GCFA5DOR-0008 (OA-13 parity `controls_unproven`) |
| #16381 | T1 HELD (landing machinery) | PUQ1APTH-0008, FOJGFIZF-0003 (commit-to-main wait 30→50) |
| #16383 | T2 HELD | PUQ1APTH-0002 (notify_on_pull journald flood), PUQ1APTH-0003 (notify_run.sh mis-routed ping writer) |
| #16385 | T2 HELD | GCFA5DOR-0009 (OA-15: INV-7, open trade on a dry_run account) |
| #16389 | T2 HELD | GCFA5DOR-0010 (b),(c) |
| #16391 | T2 HELD | PUQ1APTH-0006, PUQ1APTH-0007, GCFA5DOR-0010 (d); (a) not done |
| #16392 | T2 HELD | GCFA5DOR-0002/0003 (OA-04, OA-05/06 prop naked-position guard), see B1 |
| #16388 | T1 self-land | FOJGFIZF-0004 (harness-dispatch applies the unit's rule) |
| #16393 | T1 HELD (landing machinery) | FOJGFIZF-0002 (prop-fit template n-floor) |

Merged ≠ deployed ≠ observed. Every row above stays open until its own `clears_when` observation.
