# Sprint Log: S-LANE0-DECISIONS-2026-08-25

## Date Range
- Start: 2026-08-25 ~14:30Z
- End: 2026-08-25

## Objective
Take the two **compounding** Lane 0 decisions the prior session
(`S-LANE0-STANDING-CHECK-BLINDSPOTS-2026-08-25`) left open, having established
that both were *decisions, not work*, and that each *"either pages the operator
or suppresses something that does."* The objective was therefore **not** to
decide them — it was to do the reading the rows themselves said had been
deferred, cost both options against measured evidence, put them to the
operator, and implement whatever came back.

## Tier
**Tier 1 throughout.** One predicate lifted from a nested closure to module
scope; one alert threshold; tests; docs and backlog. No `config/`, no
`src/units/accounts/`, no order path, no VM mutation, no service restart.
`src/core/coordinator.py` was **read but deliberately not modified** — it sits
near the order path and its near-duplicate closure is filed rather than folded
in.

## Starting Context
Four backlog rows open from the prior session, all blocked on decisions. Two of
them compound: the declared-policy vocabulary and the `balance_unreadable`
alert floor. The rows were explicit that a per-token decision and a threshold
decision *"want deciding together"*.

Board tail proven before the first substantive call (`perPage=20, page=74` →
short page of 12, total **1472**, matching the issue's own `comments` count).
Both prior sessions had posted `✅ DONE`; no open `🔒`. `origin/main` at
`f741796` — **one commit past the `e5f0d07` the handoff named**, because
`/system-review` merged #10276 after that handoff was written.

## Repo State Checked
- `git fetch origin main`; branch `claude/metis-lane-0-decisions-7jgnjz` at
  `origin/main`, 0 ahead / 0 behind, clean tree.
- Session-start read: root `CLAUDE.md` → `docs/CLAUDE-RULES-CANONICAL.md`
  (autonomy mandate, RULE ONE, backlog governance, permission tiers,
  multi-session coordination) → `ROADMAP.md` → coordination board #6927.
- Live diag reachable **direct over HTTPS** (`https://ict-bot.duckdns.org`),
  no relay needed. ⚠️ `DIAG_BASE_URL` still ships as
  `http://158.178.210.252:8001` — the micro **terminated 2026-06-16**
  (`BL-20260818-DIAG-BASE-URL-POINTS-AT-TERMINATED-VM`, re-confirmed live
  today); `scripts/ops/diag_fetch.sh` self-heals to the Caddy host and says
  `served by …` so the substitution is visible.
- ⚠️ `/api/diag/version` at 15:00Z: `git_sha a3d8a080` running vs
  `git_sha_on_disk f7417963`, **`restart_pending: true`** — the live trader is
  running older code than disk. Not acted on (Tier-2 deploy, and not this
  sprint's scope); recorded because a session reading live behaviour today is
  reading `a3d8a08`, not `main`.

## Files and Systems Inspected
- `src/runtime/execution_diagnostics.py` — `EXPECTED_DISPATCH_SKIP_REASONS`,
  `is_expected_dispatch_skip`, and the `_is_hold` **closure** at line 652
  inside `enqueue_all_accounts_failed_dispatch` (line 608).
- `src/core/coordinator.py` — the three emit sites (`:1945`, `:1998/:2009`,
  `:2120`) and the `_is_benign_noop` **closure** at `:2447`.
- `src/runtime/dead_leg.py` — `bucket_for`, `_is_declared_policy_skip`,
  `verdict_for`.
- `src/runtime/silent_refusal_alert.py` — `_CAUSE_PATTERNS`, `assess`,
  `_describe`, `_CAUSE_HINTS`, the latch in `run_silent_refusal_check`.
- `src/analysis/paper_record_classifier.py` — `_REFUSAL_MARKERS`.
- `src/runtime/intents.py` — the delta-reason vocabulary.
- Live journal via `/api/bot/db/table/trades` (filter, then **assert
  `filter_state == "applied"`** before trusting any count).

## Work Completed

### PR #10281 — the declared-policy vocabulary
`execution_diagnostics.is_policy_hold` added at **module level**;
`dead_leg._is_declared_policy_skip` re-pointed at it from the narrower
`is_expected_dispatch_skip`. `_is_hold` now delegates (byte-identical, same
four clauses). `tests/test_policy_hold_predicate.py` — 15 tests.

**The row said two modules disagreed. Four vocabularies exist and three
agreed** — `_is_hold`, `_is_benign_noop`, and
`paper_record_classifier._REFUSAL_MARKERS` all treat the three tokens as
non-failures; `dead_leg` alone did not.

⚠️ **And the cause is mechanical, not a judgement.** The broad rule had been
the incumbent since 2026-05/07 — in **two nested closures**, which nothing can
import. `dead_leg`, written later and asking exactly this question, delegated
to the only *importable* predicate in that module. Its docstring claimed it
used *"the one module that owns 'is this refusal deliberate?'"*: **true of the
module, false of the predicate.** That module holds two, and the narrow one is
a strict subset of what the same module applies to its own alerting.

### PR #10282 — the per-cause alert floor
`CAUSE_MIN_ROWS = {"balance_unreadable": 1}`, consulted by `assess` as an
**additional** trip path so the map can only *add* alerting, never suppress.
New non-collapsed `alerting_basis` ∈ `{total_floor, per_cause_floor, both,
None}`; `priority_causes` joins the latch key; the alert body names the rare
cause; `balance_unreadable` gains the `_CAUSE_HINTS` entry it never had.

### PR #10283 — `set-env` gains a scoped `env_file`, and two near-misses at the same gate

The operator approved provisioning `IB_MD_CLIENT_ID`. **I did not execute it as
specified, twice, for two different reasons — and both would have reported
success.**

**Near-miss 1 — the wrong instrument.** `set-env` chooses which SERVICE to
restart and never which FILE to write. `ict-web-api.service` loads the shared
repo `.env` *by design* (line 49, so operator overrides stay aligned between
writer and reader), and `ict-trader-live.service` loads it too. Verified
exhaustively — the only producer of this key into a settings dict is
`routers/candles.py` (web-api only), so the TRADER reads it from the
environment and falls to `exec_client_id + 1` = 498. A shared-file write of
`600` moves the trader onto the web-api's own 600: IB error 326, starving the
exact MES/MGC/MHG candles the reservation protects. **Worse than doing
nothing.** Fixed by adding an allowlisted `env_file:`.

**Near-miss 2 — the undeployed mechanism.** `system-actions` runs
`bash /home/ubuntu/ict-trading-bot/scripts/ops/<script>` from the VM's own
checkout, with **no `git pull`**. Four minutes after merging #10283 the VM was
still on `7438eadc`; dispatching then would have run the OLD `set_env.sh`,
which ignores `ENV_FILE_TARGET` and writes the shared file. **This is the third
instance today of "an undeployed change and a working one render
identically"** — and the first where the consequence was live rather than
cosmetic. Waited for `ict-git-sync` (5-min timer), verified `9c9f8472` on disk,
then dispatched.

### Lane 0 item 0.5 — CONFIRMED BY INTERVENTION

Three issues, in order, each with the state established before the next:

| # | what | result |
|---|---|---|
| #10284 | baseline `get-env` on `ict-web-api` | `process (unset) / declared (unset)`, **both readable** — an observed absence, not an inferred one |
| #10285 | `set-env … env_file=web-api` | `created in /etc/ict-trader/web-api.env`, unit `active` |
| #10286 | `get-env` on `ict-trader-live` | **`(unset)` both sides** — the write did NOT reach the trader |

⚠️ **#10286 is the assertion that matters and the web-api could never have
supplied it.** The web-api reading `600` is consistent with BOTH a correctly
scoped write and a shared one; only the trader reading `(unset)` separates
them. Trader stays on 498, web-api on 600, no collision.

**The 0.5 hypothesis was falsifiable and it held.** Before: all three
`ib_paper` legs read `unavailable` while `/api/bot/candles` returned real bars
*in the same process*. After: MGC short 51 = −$5,202.00 · MHG long 29 =
+$15,478.75 · MES long 15 = +$4,068.75. Regression check passed in the same
window — MES/MGC/MHG candles all still real from `bot-exchange`.

⚠️ **A side effect that touched a DEFERRED decision.** Lifting the mask
re-exposes exactly what `…REPAIRING-THE-UPNL-MASK…` governs, and the operator
had not taken that call. The mask and the collision are the *same mechanism*,
so it could not be avoided while fixing 0.5. Surfaced immediately with the
numbers before anything further; operator chose **leave exposed, decide
provenance next**.

**And the row's own aggregate was hiding its worst case.** Reproducing the
basis error live — journal `entryPrice` vs exchange `entry_price`, with
`contract_value_usd` READ from `config/instruments.yaml` rather than assumed —
gives **+$1,829.55 net, matching the row to the cent**. Decomposed for the
first time: **MGC 36.2% · MES 7.1% · MHG 2.2%**. The entry prices differ by
only 0.05–0.08%; the leverage is that uPnL is a *difference of two large
numbers*, so a leg near break-even has an unbounded relative error. A
single-digit aggregate understates the worst leg ~5×.

⚠️ **Broker-truth uPnL is NOT comparable** — `/api/diag/exchange_positions`
returns `unrealised_pnl: null` for all three legs because the readonly client
routes through `reqPositions()` (the documented cost of avoiding
`BL-20260706-IBACCTUPDATES-COLLISION`). That is an honest "not measured". What
it DID confirm: positions match exactly on direction and size.

## Validation Performed
- `tests/test_policy_hold_predicate.py` — **15 passed** (new).
- `tests/test_silent_refusal_alert.py` — **41 passed** (33 pre-existing + 8
  new).
- **449 passed, 0 failed** across all 22 test files referencing
  `execution_diagnostics` or `dead_leg`.
- ⚠️ **Pre-existing failures ruled out against a baseline rather than
  assumed.** The first full run showed `10 failed / 8 errors`; `git stash`
  reproduced an **identical** count on clean `main`. Cause was
  `ModuleNotFoundError` (`httpx`, `fastapi`) — sandbox deps, not the change.
  Supplying both turned all 18 green.
- Guards run exactly as CI does: **PASS 33 · FAIL 0 · SKIP 18**, including
  `ruff-lint` and `layer-guard`. ⚠️ The first run reported `PASS 17` and said
  so itself — *"3 path(s) are UNCOMMITTED and every guard is scoped to a commit
  range … This is NOT a clean bill of health"*. Re-run after committing.
  `layer-guard` initially exited **127** (`lint-imports` absent) — an
  environment gap, not a finding.
- `check_backlog_criteria.py --base origin/main` and
  `check_backlog_refs.py --base origin/main` — both OK.
- **The over-suppression control, which is the check that matters for a
  widening:** all **48** lifetime `balance() returned None` rows pulled live
  and re-bucketed through the real `bucket_for` — **every one still
  `refused`.** The change removes noise without silencing a single genuine
  capability-failure row.

## Documentation Updated
- `docs/research/WORKPLAN-2026-08-14.md` — Lane 0 **table row 0.3** *and* the
  prose at §"the two rows compound". Both, deliberately: the prior session's
  own finding was that `/doc-freshness` structurally cannot catch a stale
  summary line **inside** the document its records point at.
- `ROADMAP.md` — new session entry (extending the prior session's row would
  have merged two different sprints' records).
- `docs/claude/health-review-backlog.json` — 2 rows resolved with evidence,
  2 new rows filed.

## Contradictions or Drift Found
1. **`dead_leg._is_declared_policy_skip`'s docstring asserted something false
   about its own call** — see above. Corrected in the same change.
2. **The parent row's distribution was an undercount, and in the reassuring
   direction for the wrong reason.** It measured over the capped 1000-row diag
   window (which it stated honestly) and read *6 rows / max run 2 / 0 of 5
   occurrences alerting*. Against the **full journal**: **48 rows / 11
   occurrences / run lengths `[1,1,1,1,1,1,2,3,6,11,20]` / max run 20**, so
   **3 of 11 lifetime occurrences would have alerted at the old floor.** The
   row's *conclusion* survives for the current regime — every occurrence since
   2026-07-01 is a run of 1–3, so 0 of 7 recent ones would have fired — but
   **"max run 2" must not be re-quoted.** Two accounts the capped window never
   saw are in the lifetime population (`alpaca_live` 20, `breakout_1` 18);
   verified those rows carry **no** declared token, so #10281 does not suppress
   them.
3. **`restart_pending: true` on the live trader** (recorded, not acted on).

## Risks and Follow-Ups
- ⚠️ **`BL-20260825-RARE-CAUSE-INVISIBLE-ON-A-PARTIALLY-REFUSED-ACCOUNT`** —
  the floor is **half the gate**. An alert also needs
  `verdict == signalled_never_placed`. Measured across the three accounts
  carrying the condition on 2026-08-13 (calendar-day bucketing, a **proxy** for
  the rolling window the detector uses): `ib_paper` 0/5 and
  `alpaca_portfolio` 0/5 both `signalled_never_placed` — **covered**;
  `alpaca_paper` **1 placed / 4 refused → `partially_refused` → still not
  covered.** So *"the detector now covers 0.3"* is true of **two of the three
  accounts in the very event the row is about**, and is pinned by a test named
  as a residual so it cannot be read as coverage.
- ⚠️ **Neither PR touches the `balance()` defect.** 0.3 is now *visible at its
  real arrival size*; it is not fixed.
- `BL-20260825-BENIGN-NOOP-CLOSURE-STILL-DUPLICATES-THE-POLICY-HOLD-RULE` —
  one closure remains un-extracted, deliberately (order-path adjacency, plus an
  extra `below_venue_min_qty` clause whose inclusion is a real question).

## Deferred Items
- **`IB_MD_CLIENT_ID` provisioning** (Tier-2) — operator-selected as next.
  ⚠️ **Provision BEFORE removing `candles.py`'s hardcoded `"600"`, never
  after**, or candles break.
- **The uPnL mask / provenance call** — repairing it re-exposes an estimate
  with an 11.4% undeclared basis error.
- **0.6** — parked by operator decision; `src/prop/` untouched.

## Operator-Owed (unchanged, none actionable from a session)
- 🔴 **LIVE: `ib_paper` MHG at 200% stop cover across two disjoint OCA
  groups.** OCA cancels only *within* a group, so one stop firing flattens the
  position and the other group's legs still rest to sell 29 more into a naked
  short. Detect-only by design after
  `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`.
  **Cancel the leg that does NOT match `trades.stop_loss`.**
- **`DIAG_READ_TOKEN` rotation — now more urgent.** ⚠️ I reproduced the prior
  session's mistake: a `${VAR:-…}` expansion, which returns the **value** when
  the variable is set, printed the token into this transcript too. It went
  nowhere external, but **two sessions have now leaked it.** Use
  `[ -n "$VAR" ]` for presence checks.
- The `breakout_1` balance report (`bal <balance> <equity>`) — the cushion is
  unmeasured while flat, which is exactly when the next ticket is sized
  against it.
- A decision on `daily_usd: 200`.
- The stray branch `claude/ib-breaker-peer-close` (from `qhpxyh`) still needs
  deleting; nothing in it is unmerged.

## Next Recommended Sprint
`IB_MD_CLIENT_ID` provisioning (Tier-2), ordered: provision the clientId on the
`ict-web-api` unit and **verify it live** first; only then remove the
`candles.py` literal as a separate change.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries — every
      emit site and both closures were read at their line numbers.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needs no
      update and no dashboard verification applies.
- [x] Roadmap status checked and a new entry added.
- [x] Contradictions recorded — including two in the evidence I inherited and
      one in my own inherited claim.
- [x] Remaining unknowns stated: the residual is measured and filed, the
      `balance()` defect is untouched, and the calendar-day bucketing used for
      the verdict-gate measurement is a proxy for the detector's rolling
      window.
