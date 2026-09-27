# CA-A07 code audit — src/prop/**, scripts/prop/**, breakout_1 config

**Doc status:** `live` · dated 2026-09-27 · lane `CA-A07` of the 2026-09-27 code
audit (tracked on `docs/claude/work/MANAGER-CHECKLIST.json` row CA-A07, filed
via PR #13156). Read-only analysis; this PR changes no code, config, or
anything live.

## Scope

`src/prop/**` (33 files, including the DXtrade platform adapter merged the
same day in PR #13139) and `scripts/prop/**`, plus `config/accounts.yaml`'s
`breakout_1` block, `config/prop_rulesets/breakout.yaml`,
`config/prop_rulesets/breakout_routing.yaml`, and `config/prop_platforms.yaml`.
Also cross-checked against open PR #13154 (operator sizing decisions,
unmerged) as instructed, to note anything it contradicts.

Method followed `docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md` §3/§5: every
finding carries a falsifiable claim, exact evidence (command/quote + actual
output, with file:line), expected vs. actual, the population measured, blast
radius, tier, and a named detector or a stated reason none is possible.

## How this was run

Three lanes in parallel, one question per lane (per `.claude/skills/manager/SKILL.md`):

- **This session** — verified the environment premise (checklist row, PR
  #13156, #13154, #13139, the audit-proposal doc all cross-checked before
  starting), then read the core money-path files directly: `breakout_executor.py`,
  `prop_risk_gate.py`, `account_rulesets.py`, `evaluator.py`, `symbol_map.py`,
  `multi_account_ticket.py`, `standard_account_size.py`, `platform/dxtrade.py`,
  `platform/base.py`, `breakout_ticket.py`, `funding.py`, `ruleset.py`,
  `prop_reconcile.py`, plus all relevant config (`accounts.yaml`'s breakout_1
  block, `breakout.yaml`, `breakout_routing.yaml`, `prop_platforms.yaml`), the
  PR #13154 diff, and re-verified both subagent lanes' highest-severity claims
  directly against the source before accepting them.
- **Delegated lane A** ("accounting/observability") — `prop_journal.py`,
  `prop_balance.py`, `prop_monitor_pulse.py`, `prop_position_identity.py`,
  `prop_fills_staleness.py`, `prop_expiry_prompt.py`, `prop_invalidation_prompt.py`,
  `prop_sl_tp_alert.py`, `prop_status_request.py`, `montecarlo.py`, all read
  in full, plus their test files.
- **Delegated lane B** ("notify/reporting/CLI") — `breakout_notify.py`,
  `report.py`, `screenshot_parse.py`, `telegram_commands.py`,
  `telegram_report_handler.py`, `prop_identity.py`, `platform/breakout_terminal.py`,
  `platform/__init__.py`, and all of `scripts/prop/**`, all read in full, plus
  their test files.

Test suite: `python3 -m pytest tests/ -k "prop or breakout"` (after installing
missing deps in this sandbox — numpy/pandas/ccxt/email-validator/cffi were
absent) → **1124 passed, 6 skipped**, zero failures, across all three lanes'
combined runs (this lane's own run plus each subagent's). No regression was
introduced or found — this is a read-only audit.

## Findings by severity

- **Critical:** 0
- **High:** 2
- **Medium:** 3
- **Low:** 3

### High

1. **`AUD-20260927-CA-A07-testping-journals-and-can-suppress-real-trade`**
   (money-safety, tier 2, `PI-20260927-CAA07-0001`). The `send-prop-test-ping`
   Tier-1 action is documented in three independent places (its own docstring,
   the wrapper shell script, `docs/claude/system-actions.md`) as journaling
   nothing. It always does: `breakout_executor.emit_prop_ticket` writes a real
   `emitted` ticket to the live `trade_journal.db` regardless of the
   `_emitter` injection, and with the action's own defaults that row exactly
   matches `breakout_1`'s real routed strategy/symbol/direction — so a "safe"
   test ping can silently suppress a genuine `trend_donchian_sol`/`eth` signal
   on SOLUSDT/ETHUSDT for about an hour. No test exists that would have
   caught this.
2. **`AUD-20260927-CA-A07-prop-sl-tp-alert-zero-test-coverage`**
   (observability, tier 1, `PI-20260927-CAA07-0002`). `prop_sl_tp_alert.py` —
   the only real-time SL/TP-crossing alert, and a dependency of
   `prop_fills_staleness.py`'s leading-indicator detector — has zero test
   coverage anywhere in the repo. Manual verification during this audit found
   current behavior correct; this is a coverage gap, not a demonstrated live
   bug, but a silent regression here would degrade two safety mechanisms at
   once on an account where a limit breach is permanent.

### Medium

3. `AUD-20260927-CA-A07-montecarlo-dd-model-state-untested-off-static` —
   `montecarlo.py`'s drawdown-model-state resolution is only ever tested in
   the one branch the single live ruleset (`breakout.yaml`, static/terminal)
   exercises; the other three states have zero test coverage, dormant today
   but live the moment a second, differently-shaped prop ruleset is evaluated.
4. `AUD-20260927-CA-A07-send-test-ping-no-tests` — the script behind finding
   #1 has no test file at all, unlike every sibling prop module.
5. `AUD-20260927-CA-A07-risk-pct-source-comment-backwards` —
   `config/accounts.yaml`'s breakout_1 comment claims live tickets size from
   `breakout_routing.yaml`'s `risk_pct`; the live path never reads that key
   (only a Tier-1 demo CLI does). The two values agree today (1.5%), so
   there's no live sizing error, but the dead key is a landmine. Confirmed
   unchanged by open PR #13154.

### Low

6. `AUD-20260927-CA-A07-prop-fills-staleness-declared-ids-collapse` — a
   config-read failure collapses silently (no log at all on a bare `None`
   return) where the sibling module `prop_status_request.py` logs a WARNING
   for the identical case.
7. `AUD-20260927-CA-A07-breakout1-header-stale-re-dxtrade-read-adapter` —
   `accounts.yaml`'s breakout_1 header still describes only the manual
   Telegram bridge; PR #13139 (merged the same day) added a read-only DXtrade
   browser adapter the header doesn't mention. Not a behavior bug — the
   adapter's order-control methods all raise `NotImplementedError`.
8. `AUD-20260927-CA-A07-telegram-direction-conflict-untested` — a deliberate
   safety refusal (conflicting direction words in a report line) has no test
   locking it.

Full detail, evidence, and proposed fixes for every finding:
[`CA-A07.findings.jsonl`](./CA-A07.findings.jsonl).

## What was read fully / partly / not at all

**Read in full** (this session + both delegated lanes, cross-checked where
noted): `breakout_executor.py`, `prop_risk_gate.py`, `prop_reconcile.py`,
`account_rulesets.py`, `evaluator.py`, `symbol_map.py`,
`multi_account_ticket.py`, `standard_account_size.py`, `funding.py`,
`ruleset.py`, `prop_journal.py`, `prop_balance.py`, `prop_monitor_pulse.py`,
`prop_position_identity.py`, `prop_fills_staleness.py`, `prop_expiry_prompt.py`,
`prop_invalidation_prompt.py`, `prop_sl_tp_alert.py`, `prop_status_request.py`,
`montecarlo.py`, `breakout_notify.py`, `report.py`, `screenshot_parse.py`,
`telegram_commands.py`, `telegram_report_handler.py`, `prop_identity.py`,
`platform/breakout_terminal.py`, `platform/__init__.py`, `platform/base.py`,
`platform/dxtrade.py`, `breakout_ticket.py` — all of `src/prop/**` (32 of 33
files; `__init__.py` is empty/trivial). All of `scripts/prop/**` (9 files,
including `account_compat_matrix.py`, `breakout_login_check.py`,
`evaluate_prop.py`, `montecarlo_prop.py`, `send_test_ping.py`,
`trade_quality_review.py`, `validate_alt_prop.py`, `run_real_validation.sh`,
`emit_breakout_ticket.py`). Every corresponding test file under `tests/`.
`config/accounts.yaml`'s breakout_1 block, `config/prop_rulesets/breakout.yaml`,
`config/prop_rulesets/breakout_routing.yaml`, `config/prop_platforms.yaml` —
all in full. PR #13154's diff (the sizing-relevant files) and PR #13139's
description, via GitHub.

**Not reached / out of scope**: the live VM's runtime state (this audit found
no question that required a live diag pull — every claim above is settled by
the code, config, and tests as committed); `docs/research/prop-automation-options-2026-09-27.md`
beyond the excerpt PR #13139 quotes; the full historical git log of every
file beyond what `git log --oneline` (50-commit shallow clone) and the two
PRs fetched directly could show — deepening history was not needed since no
finding here turned on "was this a load-bearing prior decision", only on
current code/doc/test agreement.

## What could not be settled

Nothing in this scope required live data to settle — every finding is a
code/config/doc/test-coverage claim checkable from the committed tree, and
was checked that way. The one place this audit deliberately declined to
speculate: whether PR #13154's new `sizing:`/`prop_sizing.py` machinery (not
yet merged, not in this audit's scope) is itself correct — only whether it
changes the `risk_pct`-source finding above (it doesn't).

## Pipeline follow-ups filed

- `PI-20260927-CAA07-0001` — the test-ping journaling/suppression finding
  (tier 2, routed to the operator).
- `PI-20260927-CAA07-0002` — the `prop_sl_tp_alert.py` coverage gap (tier 1,
  routed to a build lane).

Both validated clean against `scripts/ops/pipeline.py --check` (the store's
only reported issue is the 3 pre-existing grandfathered `PI-20260921-0002`
collisions, unrelated to this lane).
