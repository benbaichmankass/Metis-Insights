# Sprint Log: S-ALPACA-LIVE-FIRST-LEG-2026-08-31

## Date Range
2026-08-31 (single session).

## Objective
Answer why open trades sat on `alpaca_portfolio` and not on real money since `alpaca_live`
went live, then close out the go-live lane so something is actually routed to real money.

## Tier
Mixed. Investigation Tier-1. Two gated changes, **both operator-approved in conversation
this session**: arming the T+1 cash-settlement gate (**Tier-2**) and adding the first leg to
`alpaca_live.strategies` (**Tier-3** — this is the live-trading moment).

## Starting Context
The operator observed four open paper trades on `alpaca_portfolio` (SLV/TLT/QQQ/IEF) and
asked why they had not been placed on real money "even though they fit the budget".
`OI-20260829-ALPACA-GOLIVE-BLOCKED-ON-T1-SETTLEMENT-MODEL` (loud) recorded the lane as
blocked on modelling T+1 settlement before any leg goes live.

## Repo State Checked
- `HEAD` at `634a2e3`; branch `claude/alpaca-live-trades-portfolio-w85zry`. Clone is shallow
  (50 commits), so `git log -S` could not date the roster change — the journal was used
  instead and agrees with the config comment's stated 2026-08-29.
- Deployed config read from `/api/bot/config` (not the repo) — `alpaca_live` `yaml_mode: live`,
  `strategies: []`.

## Files and Systems Inspected
- `config/accounts.yaml` (`alpaca_live`, `alpaca_portfolio`, `alpaca_paper`), `config/strategies.yaml`
- `src/runtime/cash_settlement.py`, `src/core/coordinator.py` (per-account roster filter),
  `src/runtime/account_side_filter.py`
- Live: `/api/bot/config`, `/api/bot/positions`, `/api/bot/db/table/{trades,order_packages}`,
  `/api/bot/db/tables`, `/api/bot/candles`, `/api/diag/log_file?name=cash_settlement_soak`,
  `/api/diag/broker_account_status`
- `docs/research/ALPACA-LIVE-GOLIVE-STATUS-2026-08-29.md`, `docs/claude/OPEN-ITEMS.json`

## Work Completed
1. **Answered the question, with two distinct causes split by date.** Since 2026-08-29
   `alpaca_live` has `strategies: []`; `coordinator.py` treats an explicit empty list as
   `return False  # explicit empty: block all strategies` and **filters silently without
   journalling a refusal**, which is why the account has no rows at all after
   2026-08-28T19:00:38Z. Before that it was `mode: dry_run` — the rows exist and read
   `account_mode_dry_run`. The QQQ case is neither: it reached the account and was refused
   `risk_refused: sized_qty=0 with balance=200.10`, because one share is $718.53 against a
   $200 book. Paper open and live refusal were 12 ms apart on the same signal.
2. **Armed the T+1 gate (Tier-2).** `ALPACA_CASH_SETTLEMENT_ACCOUNTS=alpaca_live` (#10629,
   `service: none`) then `ALPACA_CASH_SETTLEMENT_MODE=apply` (#10630, single
   `ict-trader-live` restart, post-restart `active`).
3. **Routed the first real-money leg (Tier-3).** `alpaca_live.strategies` `[]` →
   `[tlt_pullback_1h]`, with the rationale, the measured basis, and the rollback recorded
   inline in `config/accounts.yaml`.
4. **Declined a mid-task widening, with measurements.** The operator asked to arm the paper
   mirrors too; that was put back with evidence and withdrawn. Filed
   `BL-20260831-CASH-SETTLEMENT-GATE-HAS-NO-ACCOUNT-TYPE-GUARD` (medium, Tier-2).

## CI rounds — three failures, all real, none skipped

The PR did not go green first try. Recording each, because two were findings
rather than chores:

1. **`mergeable_state: dirty` — a MERGE CONFLICT, and zero check runs had
   fired.** `main` moved to `3eeca69` mid-session, so GitHub could not build
   the merge ref and silently skipped every workflow. Zero check runs reads
   identically to *queued* and to *all green*; reading `mergeable_state` first
   is what separated them. Resolved by merging `origin/main` in. The conflict
   was in `OPEN-ITEMS.json`, where PR #10628 had **deduped** a row — a naive
   keep-both resolution would have resurrected it, so the file was rebuilt from
   main's version and this session's two edits re-applied on top.
2. **`guards` → `probe-guard`.** The new monitoring row declared neither
   `probe` nor `probe_absent_reason`. A REAL probe was attempted first and the
   attempt surfaced a worse bug: `probe_soak.py` crashes on a bare-list payload
   and exits **1**, which its own contract defines as "read, nothing matched".
   Its docstring reserves code 2 for exactly that case. On a row whose expected
   state is already `fail`, a crash and the truth render identically. Shipped
   `probe_absent_reason` naming that, and filed
   `BL-20260831-PROBE-SOAK-CRASHES-ON-A-BARE-LIST-PAYLOAD-AND-REPORTS-IT-AS-A-REAL-NEGATIVE`.
   Deliberately did **not** bundle the tooling fix into a real-money PR.
3. **`pytest-run` → 1 failed of 13,898.**
   `test_alpaca_portfolio_mirrors_alpaca_live_minus_proxies` asserts the paper
   mirror EQUALS the live roster minus proxies. With live at
   `['tlt_pullback_1h']` that demanded `alpaca_portfolio` be cut 14 legs → 1,
   deleting the research book the eventual roster selection depends on — the
   same harm the test's own 2026-08-29 note records for the empty case, one
   state over. Rewritten as three states (empty / staged subset / aligned).
   ⚠️ **This gives up the superset direction** and says so in the test: a leg
   added to the MIRROR and not to live no longer fails here. That was never the
   representativeness risk, and the INSTRUMENT-level `symbols` equality is
   unchanged and still exact. What it GAINS: the direction that protects real
   money — a live leg with no paper counterpart — is now asserted in EVERY
   state, including the empty one, where the old code asserted only
   non-emptiness. **Operator review wanted**: this modifies a guard over a
   real-money roster relationship, which is beyond what was approved in
   conversation.

## Validation Performed
- **Env verified authoritatively, not from `.env`.** `get-env` (#10631/#10632) read
  `/proc/<MainPID>/environ`: `ALPACA_CASH_SETTLEMENT_MODE` process `'apply'` / declared
  `'apply'`; `ALPACA_CASH_SETTLEMENT_ACCOUNTS` process `'alpaca_live'` / declared
  `'alpaca_live'` — `set`, not `set_empty`, which for this knob would have meant NONE.
- **Leg selection measured, after catching a false read.** The first per-strategy query used
  `filter_col=strategy` and came back `filter_state: ignored_unknown_column` — the documented
  trap — returning the whole 4,246-row table as five identical "results". Re-run against the
  real column `strategy_name` with `filter_state: applied` asserted on every read:
  `tlt_pullback_1h` n=74 (33 long, 44.6%) versus ≤7 packages for every other affordable
  long-capable leg, and `gdx_pullback_1d` at 0 long of 7.
- Affordability: TLT last $82.45, 0.9 cash wall on $200.10 → $180.09 usable → **2 whole shares**.
- Exposure: median `entry/risk_distance` 234.9 over the 33 long packages ⇒ **4.70×** demanded
  at `risk_pct 0.02`; the cash wall is what clamps it, not the risk setting.
- `python3 scripts/check_account_class.py` exit 0; cross-config check confirms every account
  strategy exists in `strategies.yaml`; `scripts/ci/check_open_items.py` OK at 23 items.
- ⚠️ `pytest` is unavailable in this sandbox — the unit suite ran in CI on the PR, not locally.
- The relaxed ROSTER-SYNC assertion was checked by CONTROL, not by reading: a live leg
  missing from the mirror fails, a live roster swapped to an unmirrored leg fails, an
  emptied mirror fails, and the staged subset passes.
- `probe_soak` was tested against the real endpoint before concluding a probe was
  infeasible — the 1000-row journal tail spans a month and holds 80 `alpaca_live` rows,
  so the predicate had a real denominator; the blocker was the helper, not the data.

## Documentation Updated
- `config/accounts.yaml` — the `alpaca_live` roster block.
- `docs/claude/OPEN-ITEMS.json` — updated `OI-20260829-ALPACA-GOLIVE-BLOCKED-ON-T1-SETTLEMENT-MODEL`
  and added `OI-20260831-ALPACA-LIVE-FIRST-REAL-MONEY-LEG-ROUTED-BUT-HAS-NEVER-TRADED` (loud).
- `docs/claude/health-review-backlog.json` — the new account-type-guard row.

## Contradictions or Drift Found
- **`OPEN-ITEMS.json` claimed `risk_pct` STAYS 0.05; the deployed value is 0.02.** Confirmed on
  `/api/bot/config` and in `config/accounts.yaml`, which records 0.02 as operator-directed
  2026-08-29 restoring the gate-cleared value. Field beats comment — the row was corrected in
  place rather than the config being "fixed" to match the prose.
- `docs/research/ALPACA-LIVE-GOLIVE-STATUS-2026-08-29.md` §5 remains stale by its own admission;
  its superseding block is correct and was the section relied on. Not rewritten this session.

## Risks and Follow-Ups
- **The leg is ROUTED and has never PLACED AN ORDER.** Tracked by the new loud monitoring row.
  Roughly half its signals will be journalled as suppressed would-be trades (long-only on a
  cash book with `shorting_enabled: false`) — that is correct, not a fault.
- The T+1 gate is armed and **has never refused anything**, and cannot until the account makes
  a sale. `would_have_reduced_usd` will read 0.00 on `alpaca_live` until then; circular by
  construction, as the open item already recorded.
- `BL-20260831-CASH-SETTLEMENT-GATE-HAS-NO-ACCOUNT-TYPE-GUARD` — the allowlist is the only
  thing preventing a margin book being halted by cash-account arithmetic.

## Deferred Items
- Wider roster selection on backtest + walk-forward evidence (the operator's 2026-08-29
  standing requirement). This session added ONE leg as a plumbing test, not a roster.
- Mirror representativeness (funding / `risk_pct` / roster parity) — `BL-20260829-ALPACA-MIRROR-DOES-NOT-MIRROR-RISK-PCT`.
- `BL-20260821-ALPACA-LIVE-REFUSES-EVERY-ORDER-127-OF-127` still describes a state that no
  longer holds; not closed here.

## Next Recommended Sprint
Verify the first real order end to end — read `trades` for `account_id=alpaca_live` with a
status other than `rejected`, reconcile the fill against exchange truth, and confirm
`silent_refusal_alert` grades the account healthy rather than `signalled_never_placed`.

## Wrap-Up Check
Board `START` posted on #6927. Tier-2 and Tier-3 approvals both obtained in conversation
before acting. Env verified on the running process. Registers updated. Sprint log written.
