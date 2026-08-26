# Sprint Log: S-WORKPLAN-GATE0-2026-08-26

## Date Range
- Start: 2026-08-26T06:10Z
- End: 2026-08-26T10:20Z

## Objective
- **Primary:** Operator-directed BTC-loss investigation → a coherent work plan
  "based on what actually exists, not what we think exists or what was supposed
  to exist, but doesn't", with GATE 0 (trustworthy measurement) elevated by the
  operator to the number-one blocker.
- **Secondary:** ship `stop-bot-service`/`start-bot-service` (the missing
  lifecycle dispatch path blocking the MHG cleanup); close the session.

## Tier
**Tier 1** for everything shipped. One **Tier-3** change was drafted
(`config/accounts.yaml`, demoting `ict_scalp_5m` from real money) and
**REVERTED unshipped** on operator direction — no config file was changed and
no live config moved. `stop/start-bot-service` are Tier-2 *actions* whose
wrappers ship here; neither was dispatched.

## Starting Context
- Active roadmap items: **M20** Active Trade Management (operator's stated top
  priority), **M16** Unified Confidence, **M31** position telemetry.
- The work plan was **CLOSED to a system audit** with no successor —
  `WORKPLAN-2026-08-21.md`, closed 2026-08-23.
- Prior sprint: `S-OPERATOR-OWED-REGISTER-2026-08-25`.

## What was done

### 1. The BTC investigation (operator: "a lot of losses on BTC")

No demotion. The operator twice intervened to prevent one, correctly.

| measured | population |
|---|---|
| scalp's **exclusive** book +$1.43 / 25 trades — flat, not bleeding | trades no sibling could have moved |
| netting collision **30.9%** of closes — real, but not the cause | `bybit_2`/BTCUSDT, n=55 |
| fees **89% of BTC gross** over the trailing two weeks | `bybit_2` exchange fills, 90d |
| `bybit_2` balance **$296.59**, −9.3% off its 07-05 peak | `balance_snapshots`, n=2,986 |
| **hedge mode NOT needed** — partial mode covers same-direction; the 4 opposite-direction cases are 2 pre-policy + 2 involving a since-demoted strategy | 10 cross-strategy overlapping pairs |

### 2. Shipped

- **`stop-bot-service` / `start-bot-service`** (#10329) with a pairing gate that
  refuses a stop the liveness watchdog would silently undo.
- **Bybit position mode is readable** — `positionIdx` was returned by the venue
  and dropped at extraction; both read paths factored onto one builder.
- **`/api/diag/ib_open_orders` stops claiming a confirmed clean read** (it serves
  a stale monotonic view).

### 3. The work plan

[`WORKPLAN-2026-08-26.md`](../claude/WORKPLAN-2026-08-26.md). **GATE 0 blocks
every build lane**, on the operator's directive that the labelling/measurement
gap is *"priority number one of the blockers"*. Its six items are deliberately
**mechanical** — the rule they enforce is already canonical (`RULE ONE`) and
prevented none of this session's failures.

## Validation
- `scripts/ci/run_guards.py` → **PASS 33 · FAIL 0**, re-run after every commit,
  and once re-run because it warned it had scanned **nothing** (every guard is
  diff-scoped and the change was still uncommitted).
- Three sandbox test failures checked against a clean tree before attribution —
  identical with and without the change, green in CI: pre-existing.

## What went wrong — the reason GATE 0 exists

**Seven corrections, one class:** a stored field read as the quantity it is named
after. In three the deciding value was **in the row already being read**; in two
it was a section further down the same document I was quoting.

| I claimed | the measurement said |
|---|---|
| journal pnl ⇒ BTC fine (+$0.88) | `bybit_2`'s journal under-records ~8×; the operator had to tell me |
| −$2,955 = what the strategy costs | same signals at ~330× size |
| netting explains the losses | 69% of trades are exclusive; the clean book is flat |
| a minimum-R gate will help | it deletes the only profitable cohort (6/6 wins, +$22.52) |
| 83% of exits aren't declared exits | 79% land exactly on a declared level |
| the prior studies' verdicts are suspect | both are backtests; they never touch journal pnl |
| "fade momentum, avoid bad hours" is an open lead | the same doc retracts it two sections down |

An eighth was caught **while writing the plan**: the exit-label row was filed as
a discovery and is a **duplicate** of two 2026-08-22 rows whose mechanism was
already named and half already fixed. Re-statused `duplicate`; it became plan
item **G6** (make the 949-row backlog searchable at filing time).

⚠️ **"Always verify" was in force for all of them.** It is `RULE ONE`. Each read
*felt* like verification — a journal query IS a measurement, of the journal.
That is why GATE 0's items are lookups and guards, not a restatement.

## Docs updated
- `ROADMAP.md` — new `Last Updated`; **M16** annotated with its measured premise.
- `docs/claude/WORKPLAN-2026-08-26.md` — new.
- `docs/claude/health-review-backlog.json` — 9 rows filed, 1 resolved with the
  opposite headline to its filing, 1 re-statused duplicate.

## Follow-ups
- **GATE 0** G1–G6 before any build lane.
- **B1** the non-crypto candle feed on a runner — unblocks 25 M20 cells.
- **C1** partial leg-id capture by content (Tier-2, stage on `bybit_1`).
- **Lane E** three operator-owed items; the MHG one is unblocked by #10329.
