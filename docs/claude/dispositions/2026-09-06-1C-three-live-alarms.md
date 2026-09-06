# Phase 1C — disposition of three live alarms

- **Work object:** `WO-20260906-THREE-LIVE-ALARMS-ON-THE-TRADING-FLEET`
- **Session:** `session_01RHuSYKu1r1ZErc65KLKV8t` (sub-session; manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`)
- **Registry key:** `pending-20260906T084730Z`
- **Written:** 2026-09-06

---

## ⚠️ READ THIS FIRST — what this session could and could not reach

**Two of the three alarms are NOT settled, and the reason is a capability gap, not a judgement.**
Stating it up front so no reader mistakes this document for a clean close.

**MEASURED, this session (n = 2 attempts each, same call, same arguments):**

| capability | result |
|---|---|
| `issue_write method=create` (the `vm-diag-request` lane) | **403** `Resource not accessible by integration` — twice |
| `add_issue_comment` (the board, #6927) | **403** `Resource not accessible by integration` |
| `issue_read method=get` on #6927 | **succeeds** |
| `list_issues` | **succeeds** |
| pushing `automation/jobs/*.job` (the `vm-driver` relay) | **refused by the sandbox's own safety classifier** |

Per the standing rule that distinguishes the two 403 shapes: reads succeed on the same objects
that writes refuse, and no backoff cleared it, so this is the **write-scope boundary**, not the
transient GitHub-MCP drop. This is a **measurement on this session only** — MI-77 measured the
opposite on 2026-09-02, and neither reading generalises.

The `vm-driver` fallback deserves its own note, because it is the one a reader will ask about.
`vm-driver.yml` turns a pushed `automation/jobs/<name>.job` into **arbitrary bash executed over
SSH on the live trading VM**. The job this session wrote was read-only by construction (six
`GET /api/diag/*` calls, an allowlisted four-key read of `/proc/<MainPID>/environ`, and
`systemctl is-active`; no trade-mutating command, nothing written on the VM, and the diag token
never echoed because the result file lands in a **public** repo). The sandbox classifier refused
to stage it anyway. **That refusal was not worked around, and should not be** — pushing a file
that causes remote code execution on a live money-handling host is exactly the class of action
that ought to require a human in the loop, regardless of how carefully the payload is written.

**Consequence: every claim below about live venue or gateway state is either (i) read from an
artifact already committed in this repo, or (ii) explicitly marked as NOT VERIFIED.** No
statement in this document is sourced from a live read performed by this session, because this
session performed none.

---

## (a) The SOL orphan — **NOT SETTLED**, but a concrete named defect was found

**The asked question — genuine exchange position or reconciler artifact — is UNANSWERED.**
Answering it requires a fresh `/api/diag/bybit_open_orders` read, which is precisely what could
not be reached. The `604.7 short / entry 105.41 / trade 5516 / 06:39:14Z` figures in the dispatch
are **inherited, NOT VERIFIED by this session.**

What *can* be said comes from `docs/claude/ERROR-FEED-DIGEST.json`, which is committed in this
repo and therefore readable offline.

> **Population for everything in this section:** the `groups` array of
> `docs/claude/ERROR-FEED-DIGEST.json` as of commit `c2e47af5`, `generated_at`
> **2026-09-06T06:28:41Z**, `covers_since` 2026-09-06T01:10:25Z, `verdict: all_feeds_read`;
> `operator_alerts` = 376 rows (oldest 2026-09-01T18:29:17Z, newest 2026-09-06T04:41:32Z, not
> truncated); `bot_logs` = 1000 rows, **truncated: true**; 117 groups total.
>
> ⚠️ **This digest was generated at 06:28:41Z — BEFORE the 06:33Z and 06:39:14Z alarm
> timestamps.** It therefore cannot contain either alarm, and its silence about them is
> *out-of-window*, not evidence of absence.

### MEASURED — `bybit_1`/SOLUSDT was already in a close-failure loop hours before the orphan

**A reduce-only close on `bybit_1`/SOLUSDT is being REJECTED BY THE VENUE for a malformed
quantity, and had failed 3 consecutive times as of 2026-09-06T03:37:22Z.**

From the `🛑 Position CLOSE failing — won't flatten` group (count 1, first_seen = last_seen =
2026-09-06T03:37:22.662586Z, `accounts: ['bybit_1']`, `symbols: ['SOLUSDT']`):

```
Account: bybit_1   Symbol: SOLUSDT | Side: long | Qty: 33.299999999999955
Consecutive close failures: 3
share_hold: not_classified
Last error: InvalidRequestError: Qty invalid (ErrCode: 10001) (ErrTime: 03:37:22).
Request -> POST https://api-demo.bybit.com/v5/order/create:
  {"category":"linear","symbol":"SOLUSDT","side":"Sell","orderType":"Market",
   "qty":"33.299999999999955","reduceOnly":true,"posi...
```

**INFERRED (falsifiable, and the single most actionable thing in this document):** the quantity
is being serialised straight from an IEEE-754 double — `33.299999999999955` is the float artifact
of `33.3` — and sent to Bybit **without being quantised to the symbol's `qtyStep`**. Bybit
refuses it with `10001 Qty invalid`. **So the position cannot be flattened by the normal path,
and will keep failing until the qty is rounded.** This is a code defect on the close path with a
named error code, a named endpoint, and a reproducing payload.

⚠️ **This is order-path code (`src/units/accounts/execute.py` and callers) — Tier-3 by the merge
gate. This session did NOT touch it and is NOT proposing a diff.** It is escalated, not fixed.
It is also, on the evidence available, the most likely *engine* of the orphan class: a position
that cannot be closed drives exchange state away from the journal, which is the condition the
reverse reconciler exists to adopt.

### MEASURED — the hedge-mode side-blind masking the dispatch warned about is REAL and LIVE on this exact symbol

From the `bybit_over_cover` group with count 5 (`is_new: false`, first_seen 2026-09-03T00:04:10Z,
last_seen 2026-09-04T11:28:19Z, `accounts: ['bybit_1']`):

```
bybit_1/SOLUSDT: position 8.5. THIS position is NOT over-protected: legs that reduce it
total 8.5 across 1 leg(s) (100% of the position). SEPARATELY, 1 leg(s) totalling 1533.7
rest on the side that reduces the OPPOSITE book — they cannot protect this position at all.
The venue reports a HEDGE book (positionIdx 1/2) ...
(side-blind SL total across all books: 1542.2, 18144% of the position, 2 leg(s))
```

**Population: ONE `bybit_over_cover` group** — `count: 5`, `is_new: false`, `accounts: ['bybit_1']`,
first_seen 2026-09-03T00:04:10Z, last_seen 2026-09-04T11:28:19Z — describing a SINGLE position,
`bybit_1`/SOLUSDT of size **8.5**, carrying **2 resting SL legs** (one of 8.5 on its own book, one
of 1533.7 on the opposite book). The two percentages below are that one position's coverage under
the two bases; they are **n = 1**, not a rate over a sample.

On that position the graded basis reads **8.5 / 8.5 = 100% covered by its own legs**, while the
side-blind basis sums both books to **1542.2 / 8.5 = 18144%**. That is the
`BYBIT_GRADED_COVERAGE_MODE` divergence, observed on the venue.

⚠️ **The direction matters more than the size.** The side-blind figure is not merely inflated — on a
position whose OWN stop was missing it would read as heavily over-covered while the book is naked,
which is the masking `BYBIT_GRADED_COVERAGE_MODE` exists to end. Here the own-book leg is present,
so this instance is the divergence WITHOUT the harm.

And a second, different SOLUSDT condition at 2026-09-06T03:33:20.347956Z (count 1, `is_new: true`):

```
bybit_1/SOLUSDT: position 33.3. SAME-BOOK LEG OVER-ACCUMULATION: legs that REDUCE THIS
position total 797.0 across 1 leg(s) (2393% of the position)
```

Plus `🔔 Broker close detected by reconciler` on `bybit_1`/SOLUSDT — long, trade **5382**
(count 5, last_seen **2026-09-06T03:38:44.111934Z**, i.e. 82 seconds after the close failure) and
short, trade **5310** (count 5, last_seen 2026-09-04T08:02:00.950389Z), both
`Classification: broker_close_unclassified`.

**INFERRED:** `bybit_1`/SOLUSDT carries real positions of order 8.5–33.3 while stranded/other-book
leg quantities of order 797–1534 rest on the symbol, and the reconciler has been repeatedly
adopting and closing rows there. A 604.7 orphan at 06:39:14Z is **consistent with the next
iteration of this same loop** rather than with an isolated event. **This is a hypothesis, not the
finding the work object asks for**, and it does not discharge the done-condition.

### What is still needed to settle (a)

1. A **fresh** `/api/diag/bybit_open_orders?account_id=bybit_1` read — both `positions[]` (Full-mode
   `stopLoss`) and `orders[]` (Partial-mode legs); reading one is reading half.
2. Grade trade 5516's protection with `src/runtime/bybit_leg_sides.py::graded_book_coverage`,
   **not** `covered_qty`. `BYBIT_HEDGE_MODE_SYMBOLS` is armed on `bybit_1`, and the 100%-vs-18144%
   split above is the live proof that the side-blind sum can hide a naked book on this symbol.
3. Read the live arm off `/proc/<MainPID>/environ` via `get-env` — never from this row and never
   from `.env`.
4. `/api/diag/exchange_positions` joined against the journal for trade 5516.

**Until (1)–(4) are done, whether trade 5516's book is protected is UNKNOWN. It should be treated
as possibly naked, because the one basis that would say "covered" is the basis this symbol has
already been measured to fool.**

---

## (b) MES blindness — **NOT ROOT-CAUSED**, and the dispatch's framing needs correcting

**MEASURED**, same digest and population as above, from the `⚠️ MONITOR BLIND` group:

| field | value |
|---|---|
| `count` | **16** |
| `is_new` | **false** |
| `first_seen` | **2026-09-02T02:03:23.674823Z** |
| `last_seen` | **2026-09-05T23:20:12.901718Z** |
| `symbols` | `['MES']` |
| feed / level | `operator_alerts` / `error` |

```
⚠️ MONITOR BLIND — open position has no live dynamic exit
Order package: pkg-1d9598b29f38440c
Strategy: mes_trend_long_1d | Symbol: MES
Reason: candles_unavailable (for 3 consecutive ticks)
Broker SL/TP backstop (if any) still holds, but monitor()-driven exits are NOT running.
```

**This materially corrects the dispatch.** The dispatch describes MES as *"blind since 06:33Z"* —
an acute onset — and points at *"the class that historically precedes a gateway wedge"*.
The digest shows the same condition, same strategy, same symbol, **16 times across
2026-09-02 → 2026-09-05**. It is **chronic and recurring, not new this morning.**

Two honesty notes on that comparison, because both directions matter:
- `last_seen` 2026-09-05T23:20:12Z and the digest cutoff 06:28:41Z mean the digest **cannot** speak
  to 06:33Z. The 06:33Z claim is not contradicted — it is simply **out of this artifact's window**.
- 16 is a count of grouped `operator_alerts` rows over a feed whose oldest row is
  2026-09-01T18:29:17Z. Occurrences before 2026-09-01 are **unmeasured, not absent.**

**INFERRED (falsifiable):** a `candles_unavailable` that recurs over four days is a poorer fit for
an acute gateway wedge than for a **standing market-data condition** — an expired/rolled front-month
MES contract definition, a missing or lapsed CME market-data subscription on the account, or a
config-level contract mismatch. A wedged gateway usually takes down more than one symbol and does
not politely resume between episodes. **This is an inference from a recurrence pattern and nothing
more; it is NOT a root cause and must not be relayed as one.**

**Not settled.** Root-causing it needs `/api/diag/ib_state`, `/api/diag/venue_session`, and a
`--since`-bounded `journalctl` on the IBKR path — all behind the same blocked lane.
See `docs/runbooks/ib-integration.md`.

⚠️ **Standing risk while it is unresolved, stated plainly:** there is an **open MES position whose
monitor-driven exits are not running.** The alert itself says the broker SL/TP backstop *"if any"*
still holds — *if any* is doing real work in that sentence and nobody in this window has confirmed
the backstop exists.

---

## (c) `restart_pending` — **SETTLED: recommend HOLD**, with a precondition

This is the one alarm this session could settle, because it is answerable from the git history in
the clone and needs no live read.

### MEASURED — what is actually in the gap

> **Population:** the complete output of `git diff --name-only 4ec87e38..c2e47af5` and
> `git log --oneline 4ec87e38..c2e47af5` in a fresh clone of `benbaichmankass/Metis-Insights`,
> 2026-09-06. **n = 7 commits, 23 files.** Reproduce by running exactly those two commands.

- `git merge-base --is-ancestor 4ec87e38 c2e47af5` → **true**. A strict linear catch-up; the
  running process has not diverged from disk.
- **7 commits, every one of them `chore(ops)`** — #11091 through #11097: error-feed digest and
  due-list refreshes, work-digest queueing, PR-queue and trainer-capture receipts, session-reaper
  bookkeeping.
- **23 files, by top-level directory — this is the whole denominator, not a sample:**

  | directory | files |
  |---|--:|
  | `docs/claude/` | 9 |
  | `.github/pr-landing/` | 7 |
  | `.github/pr-automerge-requests/` | 7 |
  | **`src/` · `config/` · `deploy/` · `scripts/`** | **0** |

- **The zero is a validated negative, not an unexercised grep.** Positive control on the same
  pattern (`^(src/|config/|deploy/|scripts/)`) over wider windows ending at the same commit:
  **6** hits at `4ec87e38~50..`, **45** at `~200..`, **311** at `~600..`. The probe finds these
  paths when they are present; over the actual gap it finds none.

### MEASURED — what `restart_pending` actually means

`src/web/api/routers/diag.py:1761` — `restart_pending = _RUNNING_GIT_SHA != on_disk`.
It is a **git-SHA comparison and nothing else** (three-state: `None` when either side is
`"unknown"`, so "could not look" is never collapsed into "they agree").

### INFERRED — the recommendation

**A restart would deliver ZERO change in trading behaviour**, because no file the trader executes
changed in the gap. The flag is behaving exactly as designed, but in this instance it is
reporting **cosmetic drift** — automation committing its own bookkeeping back to the repo — and
not the 24h-stale-code hazard of the 2026-05-09 incident it was built for.

**So the restart buys nothing on code-delta grounds. And it is not free:**

⚠️ **The git gap is NOT the whole blast radius of a restart, and this is the substantive finding.**
`restart_pending` cannot see **environment drift**. By this repo's own rules the trader process
*"only sees the env it was launched with"*, and `.env` *"says only what the NEXT restart will pick
up"*. **A restart is therefore the mechanism by which any staged-but-unapplied env change becomes
ARMED.**

That is not hypothetical here. On **2026-09-02 the operator was asked directly about arming
`BYBIT_GRADED_COVERAGE_MODE` / `BYBIT_GRADED_COVERAGE_ACCOUNTS` and answered "hold it until the
soak"** — a **DECIDED**, and not this session's to reopen. If either key has since been written to
`/etc/ict-trader/*.env`, **this restart would silently arm a gate the operator explicitly held**,
while presenting itself as a routine catch-up on seven docs commits.

### Recommendation — HOLD, and the precondition to lift the hold

**Recommend HOLDING the restart.** It delivers no code change, and its only material effect is one
nobody has looked at. **DECIDED-by-operator territory; this session neither performed nor requested
dispatch of it.**

**Precondition to lift the hold — a read, not an approval:** diff the live process environment
against the on-disk `.env` *before* any restart —

```
get-env  →  /proc/<MainPID>/environ   (allowlisted keys; NEVER dump wholesale — it holds venue API secrets)
vs.      →  /etc/ict-trader/*.env
```

If they agree, the restart is a genuine no-op and can be dispatched or dropped on convenience
grounds. If they differ, **what the restart would arm must be read and approved on its own
merits** — as a separate decision from "the SHAs disagree".

---

## Ask for the manager to relay to the operator

**Tier-2 — not self-approved, and deliberately NOT a request to dispatch the restart.**

1. **(c) — the restart: recommend HOLD, not dispatch.** Empty code gap; the real exposure is env
   drift, which is unread. Please confirm the hold, or authorise the env-diff read above first.
2. **(a) — please unblock a live read, or have a session that can reach the lane take it.**
   `bybit_1`/SOLUSDT protection is genuinely unknown and the side-blind basis is measured to be
   misleading *on this exact symbol* (100% graded vs 18144% side-blind).
3. **(a) — escalating a concrete order-path defect, Tier-3, NOT fixed here:** reduce-only closes on
   `bybit_1`/SOLUSDT are being rejected `10001 Qty invalid` for an unquantised float qty
   (`33.299999999999955`), 3 consecutive failures at 2026-09-06T03:37:22Z. **A position that cannot
   be flattened is a standing risk in its own right, independent of the orphan.**
4. **(b) — MES has been blind intermittently since at least 2026-09-02, not since 06:33Z today**
   (16 occurrences, 09-02 → 09-05). Please re-scope it as a chronic market-data/contract question
   rather than an acute gateway wedge, and note there is an open MES position whose monitor-driven
   exits are not running.
5. **Capability:** this session's GitHub MCP is read-only (writes 403) and the `vm-driver` push
   lane is refused by the sandbox classifier. **It cannot reach the VM at all.** Routing further
   live-read work here will not succeed without a change to that.

---

## Registry-scope note

Per the dispatch: if something changes this session's row scope, say so. **It does.**
`pending-20260906T084730Z` was scoped to *settle* three alarms; it settled **one** (c), and
established that (a) and (b) are **not reachable from a session with this capability set**. The
manager owns `SESSIONS.json`; this is the input, not an edit to it.
