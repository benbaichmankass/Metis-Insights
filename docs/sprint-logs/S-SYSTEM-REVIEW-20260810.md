# Sprint Log: S-SYSTEM-REVIEW-20260810

## Date Range
2026-08-10 (single session)

## Objective
Run `/system-review --window=since-last` (window 2026-08-07T14:55Z → 2026-08-10),
with the operator's emphasis: **drain the backlog, prioritize by urgency**. Mid-session
the operator additionally directed wiring the two Tier-2 findings.

## Tier
Mixed. Tier-1 for the reviews, backlog triage, CI/tooling fixes and the report.
**Tier-2, operator-approved in chat**, for (a) the per-tick market-data fix in
`src/runtime/market_data.py` and (b) `NETTING_ATTRIBUTION_ACCOUNTS=bybit_1,bybit_2`.

## Starting Context
Prior report `RPT-20260807-145500-since-last`. Backlogs at 81 / 4 / 3 open
(health / performance / ml). Two Tier-2 items had been sitting undecided.

## Repo State Checked
`main` @ f91e32b at session start. Branch `claude/system-review-backlog-xsg81e`.
Live VM + trainer both reachable; live `git_sha` f91e32b6 matched `main`.

## Files and Systems Inspected
Live VM via the diag relay (services, status, tick_cost, db_info, positions,
balances, exchange_positions, notifications, performance 24h/7d/30d, pnl/exchange,
pnl/broker-truth, shadow/stats, ml/status, and the pairs / allocator / exit-ladder /
fc-geometry / netting / exposure soaks). Trainer via `trainer-vm-diag`
(`m20_exit_analysis` 4d + 14d). Repo: `market_data.py`, `bybit_connector.py`,
`strategy_signal_builders.py`, `intent_multiplexer.py`, `vm-diag-snapshot.yml`,
the three required-check workflows, all three backlogs.

## Work Completed

### The 251s tick — found, root-caused, fixed (Tier-2)
`/api/diag/tick_cost`'s FIRST real reading: **251s mean / 296s max** over 10 ticks.
Root-caused from `journalctl`, not guessed: every strategy builder constructs a
FRESH ccxt client, and ccxt lazily downloads the full market catalogue on that
client's first `fetch_ohlcv`. Measured ~3.2s per builder across 9 consecutive
builders; 52 strategies x 3.2 = ~166s of the 251s, no unexplained residual.

Two measurements reordered the fix and are worth keeping:
- **(symbol, timeframe) dedup is only 55 → 48 pairs (~13%)** — my first proposal,
  and NOT the lever.
- The operator's suggestion (resample high TFs from the smallest) is the bigger
  fetch-count win (48 → 24) but was **deferred with reasons**: 200 4h bars needs
  9,600 5m bars against Bybit's 1000/request cap (16 of 55 legs are `1d`), and
  MES/MGC/equity `1d` bars are trading SESSIONS, so resampling would produce
  different bars than the venue's own.

Shipped: connector memo per credential identity (+ **IB excluded** — live socket /
clientId collision) and a TTL-bounded candle cache (fraction of the bar's own
period; never caches `since=`; `.copy()` per caller).

### Netting attribution un-inerted (Tier-2)
Soak rows read `global_mode: apply` / `apply_scope: not_allowlisted` on EVERY row
including real-money `bybit_2` — shipped but writing nothing while divergence
accrued (trade 4529: journal 26.7 vs exchange 3.1). Operator chose bybit_1 +
bybit_2; `set-env` applied with `service: none` so the deploy restarts once.

### CI / tooling drained
- Relay allowlist was missing `exposure/soak` + `pnl/exchange/fills` — they shipped
  UNREACHABLE by the only session type that reviews them, blocking
  BL-20260809-EXPOSURE-SOAK-NOT-YET-TAKEN.
- The relay's rejection comment **invented a cause** (computed the real reason,
  discarded it, asserted an unrelated one). Sub-class A unprovenanced diagnostic.
- Concurrency groups added to the three required workflows.
- `workflow_dispatch`: verified ALREADY present on all required checks — resolved
  as already-satisfied rather than patched blindly.

## Validation Performed
- Grading ran (mandatory): 9 closed packages, B x4 / C x5, newest `reviewed_at`
  at/after `window_start`.
- Report rendered with `--strict`; the coverage guard REJECTED two payloads until
  the aged execution-capture anomaly was escalated — working as designed.
- 16 assertions across both caches; `test_s033_market_data` 10→14 passing.
- Full local suite vs a clean-`main` baseline: **48 failed / 9604 passed on main**
  vs **48 failed / 9607 passed on branch** — identical failures (sandbox dep gaps),
  +3 = the new regression tests.
- All four CI checks green.

## Documentation Updated
`CLAUDE.md` (new `CANDLE_CACHE_TTL_FRACTION` env row), all three backlogs,
`comms/reports/**` + index, `comms/claude_strategy_scores.jsonl`.

## Contradictions or Drift Found
- `docs/claude/diag-relay.md` describes a body format the workflow rejects in
  practice (the batch JSON array failed on an allowlist miss, and the error blamed
  the format). The allowlist was the real gap; fixed.
- `BL-20260730-PR-CI-NOT-ATTACHING`'s premise is REFUTED — 4 `pull_request` checks
  attached. Likely cause of the false premise: `get_status` returns `total_count 0`
  for Actions checks while `get_check_runs` returns 4, on the same SHA.

## Risks and Follow-Ups
- **The tick fix is NOT live-verified.** `BL-20260810-TICK-CHAIN-260S-PER-TICK`
  stays OPEN until a post-deploy `/api/diag/tick_cost` read shows `max_ms` falling,
  judged against `ticks_measured` (counters reset per process).
- **Netting `apply` now writes to the REAL-money journal.** Review the first
  `apply_scope: allowlisted` rows in the soak.
- `BL-20260810-SHADOW-STATS-FIRSTSEEN-IS-LOG-ROTATION-NOT-SOAK-START` (high): all
  19 shadow models report `first_seen` in a two-minute band because the log rotates,
  so the shadow→advisory promotion denominator is unreadable.
- FCM push channel still dark (`device_tokens = 0`) — needs the phone.

## Deferred Items
79 of 88 backlog items were carried forward with an explicit "this run produced no
evidence bearing on this" note rather than being stamped re-validated. Resampling
higher TFs from the smallest TF, scoped to crypto with a ratio bound.

## Next Recommended Sprint
Verify the tick fix on the live trader and review the first netting `apply` rows;
then fix the shadow-stats soak denominator so model promotion becomes decidable.


---

## Session Part 2 — operator-authorised autonomous continuation (2026-08-10 evening)

The operator authorised merging and autonomous overnight work, with **Tier-3
decisions explicitly reserved for the morning**. Three PRs merged.

### What shipped

| PR | Merged | Content |
|---|---|---|
| **#8744** | `64622091` | Conviction-arbitration null-qty fix · shadow-soak censoring (`soak_start_basis`) · the `get-env` read surface |
| **#8754** | `cdf61d18` | `get-env` ENV_KEY forwarding + a guard for "declared in every registry but never wired" |
| **#8757** | `51c71727` | `get-env` reads the unit's inline `Environment=` too · both first-run findings |

### The thing this was all for — CLOSED BY MEASUREMENT

`CONVICTION_SIZING_ACCOUNTS = 'bybit_1'`, agreeing on **both** sides (running
process `/proc/<MainPID>/environ` AND the unit's declared env), alongside
`MODE=apply` / `DIRECTION=reductive`. That matches `ROADMAP.md` M16 exactly:
the reductive apply path is live and **DEMO-ONLY**; real-money `bybit_2` is not
in scope, and the empty-allowlist hazard (empty = ALL accounts) is absent.
`BL-20260810-CONVICTION-SIZING-APPLY-LIVE-VS-DOC` → **resolved**.

Same run confirmed against their documented values: `BYBIT_TPSL_MODE=partial`,
`NETTING_ATTRIBUTION_MODE=apply` on `bybit_1,bybit_2`,
`REGIME_ML_VERDICT_MODE=use`, `NEWS_SOURCE=rss`.

**It took three PRs to read one string.** That is the honest measure of how
unreachable the value was, and the reason the read surface was worth building.

### ⚠️ Tier-3 finding — RAISED, NOT ENACTED

`BL-20260810-FLIP-CONFIDENCE-OVERRIDE-LIVE-WITHOUT-RECORD` (high, tier 3).

```
FLIP_CONFIDENCE_THRESHOLD    process '0.15'  declared '0.15'
FLIP_MIN_POSITION_AGE_HOURS  process '4.0'   declared '4.0'
FLIP_POLICY                  <unset> -> defaults to 'hold'
```

`CLAUDE.md` documents both as default `0.0`, disabled, and *"requires operator
approval (Tier-3 order-routing change) before deploying"*.
`docs/research/M26-regime-transition-conflict-DESIGN.md:45` calls them *"dormant
… built, default-off … never validated"*. The 2026-07-17 system report states
the threshold *"is 0.0 = disabled"*.

They are on, re-opening the flip path against the `hold` default on **every
account including real money**.

**Limits of the check, stated so the next reader does not over-trust it:**
searched `ROADMAP.md`, `docs/sprint-logs/*.md`, `docs/claude/*.md` — nothing.
Could NOT check the `.env`'s edit history (not in git) or the operator's chat.
So the supportable claim is **"no authorization record exists in the repo"**,
NOT "this was unauthorized".

### Two defects the work found in ITSELF

Both fixed, both with regression tests **verified to catch the original defect
by planting it**:

1. **`get-env` failed its first dispatch** — allowlisted, tier-classified,
   script-mapped, in `EXPECTED_ACTIONS` and `notify_run.sh`, documented, and
   still non-functional: the `ENV_KEY` forwarding branch was `set-env`-only, so
   the parameter never crossed the SSH boundary. **A missing wire renders as a
   bad request.** New guard: every action VALIDATED as needing a param must also
   FORWARD it — the suite checked actions were *declared*, never that their
   inputs *arrive*.
2. **`get-env` then invented a pending restart.** `TICK_INTERVAL_SECONDS` /
   `HEARTBEAT_INTERVAL_SECONDS` are pinned inline on the unit
   (`deploy/ict-trader-live.service:29-30`) and absent from `.env`; reading only
   `EnvironmentFiles` reported `declared: <unset>` against a live `60`. A
   diagnostic that INVENTS a discrepancy is worse than one that reports nothing.
   `declared` now merges `Environment=` with `EnvironmentFiles=`; a key defined
   in both with different values reports `both_conflict` rather than being
   resolved, because systemd applies them in directive order that
   `systemctl show` does not expose.

### Also filed

- `BL-20260730-PR-CI-NOT-ATTACHING` **escalated medium → high**: recurred on
  PR #8744 hours after the same PR attached checks normally, which closes the
  "stale observation?" question — delivery IS intermittent on a single PR. Three
  consecutive pushes attached ZERO checks; a later push attached 3 of 4. The
  failure **renders identically to green** (`total_count: 0` is an unasserted
  denominator), and recovery is the `workflow_dispatch` hatch — which
  `repo-inventory` only had because this session added it that morning.
- `BL-20260810-IMPOSSIBILITY-GUARD-LINENO-VS-WORKTREE-OFFSET` (new): the guard
  takes line numbers from a `base...HEAD` **commit** diff but resolves its ±6
  window against the **working tree**, so uncommitted edits above a claim
  produce a false failure that clears on commit. Reproduced twice. Filed with
  the fix that must NOT be used (widening the window trades a false positive for
  a false negative its own comments warn about).

### Process note worth keeping

A green I reported to the operator was computed on a **stale population** — the
full-suite run predated the `get-env` commit, and I reported it as if it covered
the tree I was describing. CI caught the gap *because I had not*. Re-measured
against the real head: 48 failed / 9707 passed, the 48 matching the clean-`main`
baseline, 44 of them `ModuleNotFoundError` (httpx ×37, dotenv ×5, psutil,
fastapi) and the rest the same import class; zero touching the changed surface.


## Wrap-Up Check
- [x] Grading ran and is committed
- [x] Report rendered `--strict` + committed
- [x] All 88 open backlog items triaged (`count_untriaged` 0)
- [x] Coordination board START + DONE posted
- [x] CI green before merge
- [x] **Live verification of the tick fix — PERFORMED 2026-08-10T07:23Z** (n=3, decomposed:
      cold 128.4s / warm 104.4s / warm 107.6s → warm mean **106.0s** vs pre-fix 251.1s, a 58%
      reduction; `TICK_INTERVAL_SECONDS` confirmed **60s** by arithmetic, not assumption).
      **The verification was performed; the FIX IS PARTIAL.** 106s against a 60s interval is
      still a **1.8x overrun** (down from ~4.2x), so
      `BL-20260810-TICK-CHAIN-260S-PER-TICK` is deliberately **left OPEN** — this box means
      "the measurement was taken", NOT "the problem is gone". Residual is ~50 candle fetches
      per tick.
- [x] **Live verification of the netting allowlist — PERFORMED 2026-08-10T07:20:40Z.** First
      applied row confirmed on REAL-MONEY `bybit_2`: `apply_scope: allowlisted`, ETHUSDT
      trade 4134, 0.02 attributed of 0.06 on a `fifo` basis, `anchor_status: anchored` at a
      fresh post-restart price, `anchor_basis: divergence_first_observed`. Correct shape;
      item left OPEN because one row is not a soak.
