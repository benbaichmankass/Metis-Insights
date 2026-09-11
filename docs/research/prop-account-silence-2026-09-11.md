# Why has the prop account not traded? — `breakout_1`, measured 2026-09-11

> **Doc status:** `live` · category `research` · created `2026-09-11` ·
> unit MI-274 · object `WO-20260911-WHY-HAS-THE-PROP-ACCOUNT-NOT-TRADED`
> · operator-raised.

## The ask

> "there hasn't been a trade on the prop… the cushion is low. That's not a reason
> to not be trading. So if that's it then we did something wrong. Other than that
> we need to figure out why. It could just be that there were no signals, but that
> seems unlikely for such a long period when the rest of the accounts are trading."
> — operator, 2026-09-11

## Verdict

**The prop account is not quiet. It is STARVED, upstream of execution, by a
mechanism that records its own action every time it fires and that nobody reads.**

The legs evaluated, signalled, and were ELECTED for `breakout_1` — and the
election was then discarded because `breakout_1` is absent from
`ARBITRATION_FANOUT_ACCOUNTS`. The operator's own hypothesis (the low cushion) is
**refuted as the cause**: the flow dies four hops before anything reads a balance.
The cushion is real and is the *next* thing that will bind, not this.

**Both of the operator's stated possibilities are wrong, and the true cause is
worse than either**, because it is a mechanism that produces no journal row, no
ticket, no refusal and no alarm — it is invisible to every per-account detector
the repo has.

## The chain, hop by hop

Every count below states its population. Window is **2026-08-30T19:39:00Z**
(the newest prop fill) **→ 2026-09-11T~17:50Z** unless stated otherwise.

| hop | prop legs | sibling control | verdict |
|---|---|---|---|
| 1. evaluate | 997 / 995 / 1000 `*_eval` | — | ✅ alive |
| 2. actionable signal | 8 / 18 / 19 | 8 / 17 / 19 | ✅ **identical** |
| 3. dispatch (`pipeline_result`) | **0 / 3** | **4 / 13** | ❌ **dies here** |
| 4. ticket emitted | 0 | — | ❌ |
| 5. sizing reached | 0 rows ever | 388 refusals in-window | ❌ never reached |

### Hop 1 — the legs evaluate

`/api/diag/audit_query?strategy=<leg>&since=…&limit=1000`. Population: the
1000-row cap per leg (newest-first), so these are lower bounds.

* `trend_donchian_sol_prop` — 997 `trend_donchian_sol_prop_eval`
* `trend_donchian_eth_prop` — 995 `trend_donchian_eth_prop_eval`
* `eth_pullback_prop_2h` — 1000 `eth_pullback_prop_2h_eval`

### Hop 2 — the legs SIGNAL, at a rate identical to their bybit siblings

This is the **positive control**, and it is what makes the measurement a
measurement. The prop legs are entry-clones of their `bybit_1` siblings: diffed
against `config/strategies.yaml`, `donchian`, `atr_period`, `atr_stop_mult`,
`min_confidence`, `symbols`, `timeframe` and `long_only` are **identical** — only
the EXIT geometry (`tp_r`, `trail_mult`, `stale_exit_*`, `exit_head_*`) differs.
So the two must signal together, and they do.

Population: rows with `side != none` over the window, via
`audit_query?strategy=…&side=long|short`.

| leg | long | short | sibling | long | short |
|---|---|---|---|---|---|
| `trend_donchian_sol_prop` | 8 | 0 | `trend_donchian_sol` | 8 | 0 |
| `trend_donchian_eth_prop` | 10 | 8 | `trend_donchian_eth` | 9 | 8 |
| `eth_pullback_prop_2h` | 4 | 15 | `eth_pullback_2h` | 4 | 15 |

**They fire at the same timestamps to the second** — e.g. both SOL legs at
`2026-09-03T14:51:37`, `15:00:55`, `2026-09-05T12:55:05`, … 8 for 8. The most
recent prop signal is **`2026-09-11T14:01:40Z`**, about four hours before this
was written.

> **"There were no signals" is REFUTED.** The strategies were never quiet, on any
> day of the twelve.

### Hop 3 — the signals never become a dispatch

`event=pipeline_result`, same window:

| leg | n | sibling | n |
|---|---|---|---|
| `trend_donchian_sol_prop` | **0** | `trend_donchian_sol` | 4 |
| `trend_donchian_eth_prop` | **3** | `trend_donchian_eth` | 13 |

`regime_hard_gate` and `regime_shadow_gate` are **0 for all four legs**, so the
regime router is not the cause.

### Hop 4 — WHY: the per-account arbitration fan-out elects the prop leg and then throws the election away

`aggregate_intents` elects ONE winner **per SYMBOL, globally, before account
fan-out** — `src/runtime/intents.py` documents this and **names this exact
pair**:

> ⚠️ RAISING A VALUE HERE CANNOT FIX A DISJOINT-ACCOUNT TWIN PAIR […]
> `trend_donchian_sol` (bybit_1) and `trend_donchian_sol_prop` (breakout_1) share
> entries by design and route to accounts with NO overlap, so the correct outcome
> is that BOTH trade. This map elects exactly one winner per symbol, so whichever
> way the pair is ordered, one of them is starved […] The remedy is the
> per-account fan-out (Lane P/P3), not a number here.

**The remedy is built, armed, and computing the right answer — and the answer is
discarded.** Live soak row, `/api/diag/log_file?name=arbitration_fanout_soak`,
`2026-09-11T14:01:40Z`:

```json
"symbol": "SOLUSDT",  "global_mode": "apply",  "applied": true,
"rounds_planned": [
  {"strategy": "trend_donchian_sol",      "accounts": ["bybit_1"]},
  {"strategy": "trend_donchian_sol_prop", "accounts": ["breakout_1"]}
],
"elected_by_account": {"bybit_1": "trend_donchian_sol",
                       "breakout_1": "trend_donchian_sol_prop"},
"apply_scope":    {"breakout_1": "not_allowlisted"},
"rounds_applied": [{"strategy": "trend_donchian_sol", "accounts": ["bybit_1"]}],
"per_account":    {"breakout_1": {"state": "starved", "holds_winner": false}},
"starved_accounts": ["breakout_1"], "starved_count": 1
```

**Population: the 100-row tail of `arbitration_fanout_soak.jsonl`
(543,967 bytes total — this is a TAIL, not the lifetime), spanning
`2026-09-09T00:00:13Z → 2026-09-11T17:10:08Z`.**

* `breakout_1` **elected** a strategy: **8** rows (`trend_donchian_eth_prop` ×5,
  `trend_donchian_sol_prop` ×3)
* `breakout_1` **starved**: **8** — *8 of 8*
* `breakout_1` in `rounds_applied`: **0** — *0 of 8*
* `apply_scope` verdict for `breakout_1`: `not_allowlisted` ×8, **no other value**

### ⚠️ Hop 4b — arming the fan-out on ONE account REMOVED the prop account's last route

This is the part that turns a known gap into a regression, and it is visible only
by reading the dispatch, not the soak. `src/runtime/pipeline.py:1077`:

```python
_rounds = _fanout_apply_rounds(signal)
if _rounds:
    for _round in _rounds:            # allowlisted accounts ONLY
        coord.multi_account_execute(_rp, account_scope=frozenset(_round["accounts"]))
else:
    coord.multi_account_execute(pkg)  # the GLOBAL winner's package
```

**`apply_rounds` REPLACES the global dispatch — it does not supplement it.** So on
any tick where an allowlisted account (`bybit_1`) elects anything, `_rounds` is
non-empty and the global path is **skipped entirely**. Before the fan-out was
armed, the prop-only twin occasionally WON the global symbol slot and routed to
`breakout_1` (that is `BL-20260827-PROP-ONLY-TWIN-WINS-THE-GLOBAL-SYMBOL-SLOT-AND-STARVES-ITS-PAPER-SIBLING`, measured 15 of 60 SOLUSDT ticks). After arming,
that route is gone too, and the replacement route is held back by the allowlist.

**A partial arming closed both doors.** This is the same self-defeating shape
`NETTING_ATTRIBUTION_ACCOUNTS` already paid for on 2026-08-09 — staging on one
account made the account being staged *toward* invisible — except here it does not
merely blind the measurement, it **removes live routing**. The soak recorded it
correctly and completely, every single time. Nobody read the soak.

### Hop 5 — the three signals that DID get through were suppressed by a 12-day latch

The 3 `trend_donchian_eth_prop` `pipeline_result` rows map exactly to the only 3
prop ticket rows in the window, and all three are `suppressed`:

| signal | ticket | status | reason |
|---|---|---|---|
| `08-30T23:34:17` | `08-30T23:34:21` | `suppressed` | `outstanding_ticket:expiry_prompted: prop-manual-8de04c2be111` |
| `09-05T16:34:49` | `09-05T16:34:52` | `suppressed` | `outstanding_ticket:expiry_prompted: prop-manual-cc0a462ab8d1` |
| `09-05T17:00:58` | `09-05T17:01:02` | `suppressed` | `outstanding_ticket:expiry_prompted: prop-manual-cc0a462ab8d1` |

`breakout_executor._reticket_suppress_reason` treats `expiry_prompted` as
"operator mid-dialog" and suppresses every further ticket for that
`(account, symbol, direction)`. **`expiry_prompted` has no timeout** — an
unanswered Yes/No prompt latches the book indefinitely.

**Both latches were live for 12–14 days and were cleared by a human, today:**
`prop_fills` shows `prop-manual-cc0a462ab8d1` (ETH long, emitted 08-30T16:16) and
`prop-manual-8de04c2be111` (ETH short, emitted 08-28T16:13) both reported
`skipped` at **`2026-09-11T16:36:43Z` / `16:36:56Z`**, reason *"never placed.
Operator confirmed 2026-09-11…"*. As of now **zero** tickets sit in a blocking
status.

> **`SOLUSDT` is the clean control that isolates hop 4 from hop 5.** SOL never had
> a blocking ticket in this window, produced 8 actionable signals, and emitted
> **zero ticket rows of any kind** — not even a `suppressed` one. The arbitration
> starvation alone is sufficient to kill a leg silently.

### Hop 6 — what would bind NEXT, and the operator's cushion hypothesis

`/api/bot/prop/status`, read 2026-09-11:

```
balance / equity     4787.34        static_dd_floor_usd   4700.00
distance_to_dd_floor    87.34       status_age_hours     286.37  (11.9 days)
status_freshness     stale          status_max_age_hours  24.0
```

* **Cushion: $87.34.** Sized risk at `risk_pct 0.015` × equity 4787.34 = **$71.81**
  (the emitted tickets carry `risk_usd: 75.0`, sized off the nominal $5,000).
* **The cushion gate is NEVER REACHED.** `Coordinator._default_balance_fetcher`
  calls `prop_sizing_balance` → snapshot 286.4h old against a 24h limit → state
  `stale` → `RuntimeError("prop_balance_stale: …")` → a `sizing_failed` refusal.
  **That refusal has never once been written.**
* **Positive control for that claim:** over the 1000 newest `trades` rows
  (ids 4701–5700) there are **388** refusal/`sizing_failed` rows — so the probe
  demonstrably finds refusals — and **0** rows for `breakout_1` or any `*_prop`
  strategy, and **0** rows containing the string `prop_balance`.

> **The operator's hypothesis is refuted as the CAUSE and confirmed as the NEXT
> BLOCKER.** Nothing stopped trading because the cushion was low. But clearing
> hop 4 alone will *not* restore trading: the very next gate is
> `prop_balance_stale`, which hard-refuses, and whose refusal reaches nobody.

## Nothing alarmed, for twelve days — three detectors, all blind

Each with a positive control, so this is measured absence and not an unread probe.

| detector | state for `breakout_1` | why it is blind | control |
|---|---|---|---|
| `silent_refusal_alert` | **no key at all** in `runtime_logs/silent_refusal_alert_state.json` | needs journal rows to grade; zero rows is its *"we observed nothing"* bucket, never a finding | `bybit_1` and `alpaca_live` keys present |
| `prop_fills_staleness` | `{"findings": {}}`, last check `17:33:03Z` | detector A needs an open position, detector B needs two snapshots — the account is flat and no new snapshot exists, so **both** are structurally unarmed | the file is being written, on cadence |
| strategy-silence (`/health-review`) | healthy | reads `*_eval`, which is 997/995/1000 — the legs ARE evaluating | — |
| `/api/bot/notifications` | 5 banners, **none prop** | no banner exists for this class | other banners firing |

**And the last surface that would have surfaced it went quiet on 2026-09-09.**
`prop_status_request` now grades activity rather than the wall clock (MI-214,
2026-09-09). Read live at `2026-09-11T17:58:43Z`:

```json
{"breakout_1": {"last_request": "2026-09-09T07:47:07Z", "last_verdict": "quiescent",
 "last_assessed_at": "2026-09-11T17:58:43Z",
 "last_detail": {"open_positions": 0, "tickets_since_snapshot": 0}}}
```

`NON_EXPOSING_TICKET_STATUSES = frozenset({"suppressed"})`, so the three suppressed
tickets do not count as activity → `quiescent` → the balance ask is suppressed →
the snapshot stays 286h stale → the operator is never told. MI-189b recorded the
ask "firing correctly" on 2026-09-08 (`last_request 2026-09-07T19:46Z`); it has
not fired since **2026-09-09T07:47Z**.

⚠️ **This is not a bug in `assess_activity`'s own terms** — a suppressed ticket
takes no risk, so it genuinely does not move the cushion, and the suppression is
doing exactly what the operator asked for. The defect is that **a fully-blocked
account and a genuinely idle one now render identically**, and nothing else
watches the difference.

## Timeline

| when | what |
|---|---|
| `08-28T16:13` | ETH **short** ticket emitted, never placed → `expiry_prompted` → ETH-short book latched |
| `08-30T16:16` | ETH **long** ticket emitted, never placed → `expiry_prompted` → ETH-long book latched |
| `08-30T19:39` | last prop fill closes (+$35.28). Account flat. |
| `08-31T07:47Z` | `ARBITRATION_FANOUT_ACCOUNTS=bybit_1` armed *(per `CLAUDE.md`; **not** re-verified this session — the soak tail only reaches 09-09)* |
| `09-05T16:34`, `17:01` | two ETH signals win globally, both suppressed by the latch |
| `09-09T07:47Z` | last balance ask. MI-214 suppression lands → verdict `quiescent` |
| `09-09 → 09-11` | `breakout_1` elected 8×, starved 8×, routed 0× |
| `09-11T14:01Z` | most recent prop signal — still firing |
| `09-11T16:36Z` | operator clears both latches to `skipped` |

## What to do — a SEQUENCE, not a single fix

Clearing any one of these alone leaves the account dead.

1. **[Tier-2, order routing] Add `breakout_1` to `ARBITRATION_FANOUT_ACCOUNTS`.**
   The plan already elects the correct strategy for it; only `apply_scope` holds it
   back. Read the current value off `/proc/<MainPID>/environ` via `get-env` first
   (this session measured the **effect** — `apply_scope: not_allowlisted`, which is
   the live process's own resolved verdict — **not** the raw variable) and needs a
   trader restart. ⚠️ Widening the allowlist changes live routing on a
   `mode: live` prop account and must not be done on a session's judgement.
2. **[operator action] Send a fresh `bal <balance> <equity>`.** Until then
   `prop_balance_stale` hard-refuses sizing, and step 1 converts silent starvation
   into a silent refusal — strictly better, but still silent.
3. **[Tier-2] Give `expiry_prompted` / `awaiting_report` a bounded life in
   `_reticket_suppress_reason`.** An unanswered prompt currently latches a book
   forever. A ticket whose `valid_until` passed by more than
   `PROP_EXPIRY_PROMPT_MAX_AGE_HOURS` should stop blocking a *fresh* signal — the
   guard's own docstring already reasons that "an EXPIRED unacted ticket does NOT
   block", and `expiry_prompted` is precisely an expired unacted ticket that the
   bot happened to ask about.
4. **[Tier-1/2] Make the starvation ALARM.** The soak has recorded
   `starved_accounts: ["breakout_1"]` on every occurrence for days and nothing
   reads it. A declared-live account starved N consecutive times, or emitting no
   ticket while its legs signal, should page. **This is the finding that matters
   most: the operator should never have to notice a dead account themselves.**

### On the operator's rule — a low cushion must not stop trading

The operator has already ruled on this, so it is recorded as a decision and not
re-litigated: **stopping on a low cushion is wrong.** It did not happen here, but
the gate that *would* have is the stale-balance refusal, which refuses
**silently**. Per `BL-20260909-PROP-SIZING-REFUSAL-ON-A-STALE-BALANCE-IS-JOURNALED-BUT-NEVER-PINGED` the dedicated ping path is not reached, and
`silent_refusal_alert` needs 5 rows in 24h with **no per-cause override** for
`prop_balance_unreported`. So when step 1 lands, the account will refuse and say
nothing. **Whichever gate ends up binding, refusing in silence is the actual
defect.** The proposal: size down to what the cushion supports, or refuse LOUDLY —
never refuse quietly.

### A second-order question for the operator

`eth_pullback_prop_2h` is `execution: shadow`, so it can never emit a live ticket —
the effective prop roster is **two** legs, not three. And those two are
entry-identical clones of `bybit_1` legs, which is exactly what makes them
contest the symbol slot. Whether a prop roster built entirely from clones of
another account's legs is the right roster is a design question this unit
surfaces but does not answer.

## What this unit did NOT establish

* **When `ARBITRATION_FANOUT_ACCOUNTS` was armed.** The soak tail reaches only
  `2026-09-09T00:00`; the 543,967-byte file was not read in full. The `08-31T07:47Z`
  date is quoted from `CLAUDE.md` and is **not a measurement of mine**.
* **The raw env value.** Measured the resolved effect, not the variable.
* **Who cleared the two latches at `16:36Z`** — the reason string says "Operator
  confirmed", which is an attribution written by the reporter, not one observed.
* **Whether the 2026-08-30 convergence with the directional-leg break (MI-271) is
  causal.** The prop account went flat on the same day, but by the chain above its
  silence is fully explained by arbitration + the latch. **Treated as coincidence;
  no shared upstream cause found.** MI-271 keeps the bleed.
