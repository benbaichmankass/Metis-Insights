# E33 — the partial `ARBITRATION_FANOUT_ACCOUNTS` allowlist SUBTRACTS `bybit_2` and `bybit_portfolio` from rounds they were already winning

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> **TIER 3. NOTHING HERE IS APPLIED.** This document is the measurement and two
> proposals. The `set-env` was not run, `config/accounts.yaml` and
> `config/strategies.yaml` were not touched, and no order path was changed.

## The operator's directive this answers (2026-09-22, verbatim)

> *"Bybit1 is also a paper account. It just has all of every strategy — every
> strategy should be trading on Bybit1 so that it's collecting real data against
> paper money, but with fees and everything. Everything that's in shadow should
> be trading on Bybit1. Period. No ands, no buts, no ifs. Bybit2 is real money,
> and the Bybit portfolio is the paper mirror. Those should be trading the same
> things. And yes, that mechanism that's starving the live trades should only be
> applied at the account level within the context of the actual trades that that
> account is considering. Not all the trades overall, so that the test account
> where the strategies are getting tested out is starving the actual
> money-makers. That's obviously an incorrect application of that mechanism and
> we need to fix that immediately."*

Nothing in the repo stated this before today. It is recorded here as the spec.

---

## Verdict

**The starvation of `bybit_2` and `bybit_portfolio` is not (only) the known
global-election defect `BL-20260827`. It is a SECOND, NEWER mechanism: arming
the per-account fan-out for `bybit_1` ALONE made the routing STRICTLY NARROWER
than leaving it unarmed.**

`src/runtime/pipeline.py:1087` dispatches the fan-out as
`if _rounds: <per-round> else: <global>`. The fan-out **replaces** the global
dispatch; it does not supplement it. `intent_multiplexer._attach_fanout_plan`
narrows each planned round through
`arbitration_fanout.scope_round_to_accounts(r, allow)`, which **intersects the
round's `accounts` with the allowlist**. So the moment `bybit_1` produces one
surviving round, the global dispatch is suppressed for that tick — and any
account not on the allowlist is dropped, *including accounts that were in the
planned round and that the global path would have served*.

`bybit_2` and `bybit_portfolio` are not on the allowlist. `bybit_1` is.

**This inverts the sign of the change for real money.** The whole point of the
fan-out (`BL-20260827`) is *"the correct outcome is that BOTH trade."* Armed
for one account, it delivers the opposite for every other account that shares
a round with it.

---

## 1 — The live measurement (what the soak rows actually say)

**Source, named so it can be re-taken:** diag-request issue
[#12708](https://github.com/benbaichmankass/Metis-Insights/issues/12708),
workflow run `35695727461`, read `2026-09-22T06:38:27Z`, path
`/api/diag/log_file?name=arbitration_fanout_soak&lines=30`.

**Population, stated because a figure without one is not a figure:** the
comment relayed **11 complete rows** plus 1 truncated, spanning
**2026-09-21T14:52:12Z → 2026-09-21T21:17:33Z (~6.4 hours)**. The container
field was cut at the relay's per-path comment budget (47 739 bytes). **This is
a 6.4-hour window, not the ~9-day trade window the row's defect statement was
measured over** — see § 5 for the read I could not take.

**Positive control, in the pull rather than asserted:** `present: true`,
`size_bytes: 1156308`, rows parse. Deployed `git_sha f3746abd1` ==
`git_sha_on_disk`, `restart_pending: false`, `ict-trader-live.service active`.
Every row carries `fanout_schema: 3`, `apply_implemented: true`,
`global_mode: "apply"`, `plan_state: "planned"`, `apply_state: "dispatchable"`
and a **non-empty `rounds_applied`** — so the fan-out is not merely planning,
**it is the live dispatcher**, and the MI-286 projection defect that made it
inert is fixed and deployed.

### 1a — Does the per-account election elect for `bybit_2` / `bybit_portfolio`?

**Yes, and the allowlist then discards it.** One row in the window reaches
those accounts at all:

**`2026-09-21T20:30:08.586710+00:00` · `ETHUSDT`**

| field | value |
|---|---|
| `accounts_planned` / `accounts_elected` | `3` / `3` |
| `elected_by_account` | `bybit_1`, `bybit_2`, `bybit_portfolio` → all `trend_donchian_eth_4h` |
| `rounds_planned[0].accounts` | `["bybit_1", "bybit_2", "bybit_portfolio"]` |
| `rounds_written[0].accounts` | **`["bybit_1"]`** |
| `rounds_applied[0].accounts` | **`["bybit_1"]`** |
| `apply_state` | `dispatchable` |
| `winner_accounts` | `["bybit_1", "bybit_2", "bybit_portfolio"]` |
| `per_account.bybit_2.state` | **`routed`** |
| `starved_count` | **`0`** |

The per-account election elected the same leg for all three accounts and put
all three in one round. **The written round carries one.** `bybit_2` and
`bybit_portfolio` were removed between `rounds_planned` and `rounds_written` —
the only thing that happens between those two fields is
`scope_round_to_accounts(r, allow)`.

⚠️ **Read the last two rows of that table together.** `per_account.bybit_2.state`
is `routed` and `starved_count` is `0` **on the very tick two accounts were
dropped**. That is § 3.

### 1b — Does it behave correctly for a non-prop twin?

**On the one observed case, the election does; the binding does not.** The
election is roster-driven and symmetric — it elected correctly for `bybit_2`
and `bybit_portfolio`, two non-prop accounts, and grouped them with `bybit_1`
exactly as `plan_per_account_election`'s docstring says it will
(*"Accounts electing the same strategy share a round"*). The prop case
(`2026-09-21T20:20:13Z`, `ETHUSDT`, `breakout_1` elected
`trend_donchian_eth_prop`, `apply_scope: {breakout_1: "not_allowlisted"}`)
behaves the same way. **The election generalises; the allowlist is what does
not.** And the two cases differ in a way that matters — see § 2.

### 1c — `per_account.<account>.state` across the window

`per_account` in a soak row is `assess`'s verdict, not the plan's. Over the 11
complete rows:

| account | rows appearing | states |
|---|---|---|
| `bybit_1` | 11 | `routed` × 11 |
| `bybit_2` | **1** | `routed` × 1 |
| `bybit_portfolio` | **1** | `routed` × 1 |
| `breakout_1` | 1 | `starved` × 1 |

The plan's own per-account states (`elected` / `no_candidates` / `elected_flat`
/ `unknown`) are **not written to the row at all** — only
`accounts_planned`, `accounts_elected` and `elected_by_account` survive. A
reader asking "what did the per-account election decide for `bybit_2`" has to
infer it from `elected_by_account`. Worth noting for anyone re-taking this read.

---

## 2 — Reproduced against the real source, both branches

Run locally against `src/runtime/arbitration_fanout.py` as it exists on `main`
(`plan_per_account_election` → `scope_round_to_accounts` → `accepted_rounds`),
not re-typed. Pinned as a test in
`tests/test_arbitration_fanout_allowlist_scope.py`.

**Branch 1 — `bybit_1` and `bybit_2` co-elect the same leg.** Replays the live
`20:30:08` row's geometry exactly; output matches the live row field for field.

```
rounds_planned : [('trend_donchian_eth_4h', ['bybit_1', 'bybit_2', 'bybit_portfolio'])]
  allow=[]                                        -> GLOBAL PATH (no fan-out)
  allow=['bybit_1']                               -> ['bybit_1']
  allow=['bybit_1','bybit_2','bybit_portfolio']   -> ['bybit_1','bybit_2','bybit_portfolio']
```

**Branch 2 — `bybit_1` elects a leg `bybit_2` does not run** (it has 26 legs to
`bybit_2`'s 3, so this is the common case).

```
elected_by_account: {bybit_1: ict_scalp_eth_15m, bybit_2: trend_donchian_eth_4h,
                     bybit_portfolio: trend_donchian_eth_4h}
rounds_planned : [('ict_scalp_eth_15m', ['bybit_1']),
                  ('trend_donchian_eth_4h', ['bybit_2','bybit_portfolio'])]
  allow=[]                                        -> GLOBAL PATH (no fan-out)
  allow=['bybit_1']                               -> [('ict_scalp_eth_15m', ['bybit_1'])]
  allow=['bybit_1','bybit_2','bybit_portfolio']   -> both rounds
```

In branch 2 the *entire* `bybit_2`/`bybit_portfolio` round returns `None` from
`scope_round_to_accounts` and is dropped — and `bybit_1`'s surviving round still
suppresses the global dispatch.

### The before/after, as a table

| ETHUSDT / XRPUSDT tick | unarmed (`allow=∅`) | **live today (`allow={bybit_1}`)** | fully armed |
|---|---|---|---|
| co-elect (branch 1) | `bybit_2` **routed** | `bybit_2` **DROPPED** ⚠️ | `bybit_2` routed |
| divergent (branch 2) | `bybit_2` starved (`BL-20260827`) | `bybit_2` starved | `bybit_2` **routed** |

**The live configuration is the only one of the three that is worse than doing
nothing.** That is the finding. It is also precisely the case the
2026-09-12 unblock memo did not anticipate: its Step 2 says widening the
allowlist changes *"nothing about their election ... `bybit_1`'s own round is
unchanged."* True for accounts **on** the allowlist. For an account **off** it
that shares `bybit_1`'s round, its accounts entry is deleted.

### Why this closes the 12 / 1 / 1 gap

`config/accounts.yaml`, read this session:

* `bybit_2` — 3 legs: `xrp_pullback_2h`, `trend_donchian_eth_4h`, `trend_donchian_xrp_4h`
* `bybit_portfolio` — the identical 3 (CI-enforced by
  `tests/test_paper_portfolio_accounts.py::test_bybit_portfolio_mirrors_bybit_2_exactly`)
* `bybit_1` — 26 legs, **a superset containing all 3**, plus 5 more on ETHUSDT
  and 3 more on XRPUSDT

So on **every** tick where `bybit_2` has a candidate, `bybit_1` has one too, and
one of the two branches above fires. **With `mode=apply` and
`allow={bybit_1}`, there is no tick on either of `bybit_2`'s symbols on which
`bybit_2` can be dispatched.** `bybit_portfolio` is identical by construction,
which is exactly why the mirror agrees with `bybit_2` rather than with
`bybit_1` — and why that agreement rules out a real-money broker fault.

**Bounding the window, honestly.** The regression begins when the MI-286
projection fix deployed, because before it `accepted_rounds` returned `[]` on
every tick and the pipeline always took the global `else:` branch. Landing
declaration `.github/pr-landing/mi286-fanout-apply-projection.json` is present
on `main` at or before **2026-09-17** (the shallow-clone boundary this session
could reach — the exact landing commit was **not** pinned). The soak rows show
it dispatching from at least 2026-09-21T14:52Z. **I did not establish that the
regression covers the whole 2026-09-13 → 09-22 trade window, and it probably
does not cover the earlier part of it.** The earlier part is `BL-20260827`.

---

## 3 — A second defect, found on the way: the soak UNDER-REPORTS this starvation

On the `20:30:08` row, `starved_accounts` is `[]` and `starved_count` is `0`,
while `rounds_applied` shows two accounts dropped.

`arbitration_fanout.assess` grades `starved` against the **global** election:
an account holding the global winner is `routed`. `bybit_2` held it, so it
grades `routed` — true of the global path, **false of the path that actually
ran**. `assess` has no knowledge of the allowlist, so starvation *caused by the
fan-out itself* is invisible to `starved_accounts`, to `starved_count`, and
therefore to `src/runtime/starved_account_alert.py`, which reads them.

**The one field that makes this sayable is `rounds_planned` vs
`rounds_written`**, and no alert reads that pair. A monitoring surface that
reports `starved_count: 0` on a tick it starved real money is the same class as
E22 and E31: a reading surface structurally unable to express the state that
matters.

Not fixed here. Named so it is not re-derived.

---

## 4 — The two proposals

### ⚠️ Which one the directive calls for

**Both, in this order.** They are not alternatives.

The directive contains two separate sentences. *"That mechanism ... should only
be applied at the account level within the context of the actual trades that
that account is considering"* is a statement about the **scope of the
mechanism** — that is (b), and it is what *"obviously an incorrect
application"* means structurally. *"We need to fix that immediately"* is about
**latency** — and (b) is a Tier-3 rewrite of the election's scope that will not
be written, reviewed and deployed today.

But there is a reason (a) is not merely the fast path here: **the live config
is worse than unarmed.** Whatever is decided about (b), the current state
should not be left running, and the cheapest correct state is reachable with
one `set-env`.

**Recommendation: (a) now, (b) as the row that follows it.** And note the third
option below, which is cheaper than both and which the operator should be
offered explicitly.

### (a) Widen the allowlist — exact parameters

⚠️ **`ARBITRATION_FANOUT_ACCOUNTS` IS A CSV AND AN EMPTY VALUE MEANS *NONE*, NOT
*ALL*** (`arbitration_fanout_soak.allowlisted_accounts`, deliberately the
opposite polarity to `CONVICTION_SIZING_ACCOUNTS`). **Read the current value
first and write the UNION.** Clobbering `bybit_1` would silently unarm the one
account the fan-out currently serves.

**Step 1 — READ (Tier 1, no side effect). `system-actions` workflow_dispatch:**

```
action:   get-env
env_key:  ARBITRATION_FANOUT_ACCOUNTS
service:  ict-trader-live
```

Then again with `env_key: ARBITRATION_FANOUT_MODE`. Take the **`process`**
value (`/proc/<MainPID>/environ`), not `declared`, and note any
`pending_restart`. **From the soak rows alone the live allowlist is known only
to satisfy `allow ∩ {bybit_1, bybit_2, bybit_portfolio, breakout_1} =
{bybit_1}`** — it may contain Alpaca accounts this session cannot see, which is
exactly why the read is not optional.

**Step 2 — WRITE (Tier 3). `system-actions` workflow_dispatch:**

```
action:    set-env
env_key:   ARBITRATION_FANOUT_ACCOUNTS
env_value: <the UNION of step 1's process value with:>
           bybit_1,bybit_2,bybit_portfolio,breakout_1
service:   ict-trader-live
env_file:  shared          # the repo .env — what ict-trader-live.service reads
```

`service: ict-trader-live` is required: the value is read from the process
environment, so **the trader must restart or nothing changes.** `set_env.sh`
performs the restart and reports `Post-restart <service> state`.

**Per the directive — *"everything that's in shadow should be trading on
Bybit1. Period"* — and because a partial allowlist is the failure mode this
memo documents, the safer target is EVERY account on the roster, not these
four.** Four is the minimum that fixes the measured harm. The operator should
decide between the two; whichever is chosen, the union with step 1 still
applies.

**Verification after the restart, in order, and not before:**

1. `get-env ARBITRATION_FANOUT_ACCOUNTS` → `process` shows the new value.
2. A soak row on an ETHUSDT/XRPUSDT tick where `rounds_written[].accounts` ==
   `rounds_planned[].accounts` — the decision.
3. **A journal row for `bybit_2`.** Per the soak's own docstring: *"A soak row
   alone proves the decision, never the order."* Merged ≠ deployed ≠ observed.

### (b) Fix the scope at source

Make the election per `(symbol, account)` in `aggregate_intents`
(`src/runtime/intent_multiplexer.py:273` — *"intents are not account-bound at
the multiplexer"*), so no global winner-per-symbol exists to be starved by and
the allowlist becomes unnecessary. The code comment already says *"Fixing the
arbitration scope is Tier-3."*

**This memo does not carry that diff.** Writing it responsibly needs the
audit-emission question answered first: `plan_per_account_election`'s docstring
warns that re-running the aggregator per account *"re-emits a
`regime_hard_gate` row per account per tick, corrupting the one signal that
partitions 'would have gated' from 'did gate'"* — which is why the gate/elect
split exists. A per-account `aggregate_intents` has to preserve that split, and
that is a design question, not a diff. **Proposing a diff I have not reasoned
through the audit consequences of would be the improvisation E17 was told not
to do.**

### (c) The option neither of the above is — and it is the cheapest

**`set-env ARBITRATION_FANOUT_MODE=annotate`.** One variable, no roster
question, and it returns routing to the global path — which § 2's table shows
is **strictly better than today for `bybit_2`** and no worse for anyone. It
does not fix `BL-20260827`; it removes the regression this memo found while (b)
is designed. If the operator wants the smallest change that stops real money
being subtracted, this is it.

---

## 5 — What I could not do, plainly

* **`issue_write` returns `403 Resource not accessible by integration`** in this
  session while `issue_read` on the same objects succeeds — the write-scope
  boundary `docs/claude/diag-relay.md` documents, not the transient token drop.
  **So I could not open a fresh diag-request and could not re-request the soak
  path alone for the untruncated tail.** The measurement above rests on the 11
  complete rows recovered from the existing #12708 comment. There is no
  file-drop relay for the live VM's diag surface (only the trainer VM), and
  `https://ict-bot.duckdns.org/api/diag/*` returns `401` without
  `DIAG_READ_TOKEN`, which this session does not hold.
* **I did not independently verify the 12 / 1 / 1 closed-trade split.** That
  needs `/api/diag/audit_query` over the trade journal, behind the same relay.
  It is consistent with everything measured here, but it is inherited, not
  re-taken.
* **I did not pin the exact MI-286 deploy timestamp** (shallow clone; boundary
  2026-09-17). The regression window is bounded, not dated.

**A session with a working `issue_write` should re-take one read** —
`log_file?name=arbitration_fanout_soak&lines=200` **as the only path in the
issue** — and count, over a stated window: rows where
`rounds_planned[].accounts ⊃ rounds_written[].accounts`, and rows where a
planned round was dropped entirely. That is the denominator this memo is short
of.

---

## Relationship to E17 (`lane/e17-arm-arbitration-fanout-breakout1`, blocked)

E17 is **the same variable, and this row supersedes its framing.** E17 proposed
adding `breakout_1` alone, for the prop-account outage, and is blocked on the
same `403` on PR creation.

* **It does not contradict this memo** — `breakout_1` belongs in the union, and
  § 4(a) includes it.
* **But `breakout_1` alone would not have helped `bybit_2` or
  `bybit_portfolio`, and would have left the regression in § 2 running against
  real money.** E17's own row already warns *"if it turns out the fanout runs
  and the record is merely missing, COME BACK, do not improvise"* — the read
  taken on 2026-09-22 established that the fan-out **runs and dispatches**, so
  that instruction is live.
* E17's branch carries a checklist-note commit only; there is **no `set-env`
  and no code change on it** to conflict with.

**Recommended disposition: fold E17 into this row's (a) as one wider `set-env`,
rather than running two.** Two sequential partial widenings mean an
intermediate state in which some other account is the one being subtracted.
