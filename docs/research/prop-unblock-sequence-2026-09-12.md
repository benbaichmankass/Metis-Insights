# Unblocking `breakout_1` — the fan-out has never dispatched, so the allowlist is not the lever

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> unit MI-282 · object `WO-20260912-UNBLOCK-THE-PROP-ACCOUNT-IT-IS-STARVED`
> · row `OI-20260911-THE-PROP-ACCOUNT-IS-STARVED-NOT-QUIET-AND-THE-UNBLOCK-IS-A-SEQUENCE-NOT-A-FIX`
> · operator-directed 2026-09-12 · **prepares a Tier-2/3 proposal; enacts nothing.**

## What this unit inherited and did NOT re-derive

[`prop-account-silence-2026-09-11.md`](prop-account-silence-2026-09-11.md) (MI-274, PR #11816)
established that `breakout_1` is **starved at dispatch** rather than quiet: its legs
evaluate (997/995/1000 `*_eval`) and signal at timestamps identical to their `bybit_1`
siblings to the second, and the low cushion is **refuted as the cause**. That
attribution is cited here, not re-run.

**Two of its subsidiary claims are wrong, and this document says so loudly** — that is
the more valuable finding, and it changes what the unblock actually is. Its headline
verdict survives both corrections intact.

## Verdict

**The per-account arbitration fan-out — the declared remedy for the starvation — has
never dispatched a single order, on any account, since it was built.** Its plan is
computed correctly, recorded correctly, and then **rejected by the pipeline's own
reader** because the writer drops the geometry the reader validates.

So the Tier-2 lever everything was queued behind — *add `breakout_1` to
`ARBITRATION_FANOUT_ACCOUNTS`* — **is inert**. Worse: applying it would flip the
account's `apply_scope` to `allowlisted` and put it in `rounds_applied`, so **every
observability surface would report the account as routed while it stayed starved.**

## The defect

| side | file | what it does |
|---|---|---|
| writer | `src/runtime/intent_multiplexer.py:1019-1026` | builds `apply_rounds` as `{"strategy": …, "accounts": […]}` — a **two-key projection** |
| source | `src/runtime/arbitration_fanout.py:425-432` | `plan["rounds"]` entries carry `strategy, accounts, side, entry, sl, tp, confidence` |
| reader | `src/runtime/pipeline.py:58-89` | returns `[]` unless each round has `side ∈ {long,short}` **and** non-`None` `entry`/`sl`/`tp` |

The writer drops exactly the four keys the reader requires. `_fanout_apply_rounds`
therefore returns `[]` on every tick, and `pipeline.py:1077` takes the `else:` branch —
`coord.multi_account_execute(pkg)`, the **global winner's** package.

The reader's fail-closed posture is correct and is not the bug: *"acting on a plan we
could not read is a live order on unverified routing."* The bug is that the writer never
produces a plan it can read.

### Verified four ways, none of them by reading the code

**1 — Ran the real source text.** Both functions extracted with `ast.get_source_segment`
(not retyped) and executed against the real `plan_per_account_election`:

```
rounds       : [{"strategy": "trend_donchian_sol", "accounts": ["bybit_1"], "side": "long", "entry": 100.0, …},
                {"strategy": "trend_donchian_sol_prop", "accounts": ["breakout_1"], "side": "long", …}]
apply_rounds : [{"strategy": "trend_donchian_sol", "accounts": ["bybit_1"]}]
applied      : True
_fanout_apply_rounds(signal) -> []
```

**2 — Live production data. ⚠️ STATE THE POPULATION: 400 soak rows, the tail of a
555,838-byte file, `2026-09-02T15:42:52Z → 2026-09-12T01:27:39Z` — a tail, not the
lifetime.** Read from `/api/diag/log_file?name=arbitration_fanout_soak`:

* **317** rows carry `applied: true` with a non-empty `rounds_applied`
* every entry in all 317 has **exactly** the key-set `('accounts', 'strategy')` —
  **one distinct key-set, zero carrying `side`**
* `fanout_schema` present on 400/400, so none of these are pre-2026-08-30 rows

The projection is visible in production, in the very rows MI-274 quoted as evidence that
the fan-out was working.

**3 — Congenital, not drift.** Writer projection, reader validation and round geometry
all landed in the **same commit**: PR #10544, `12e4a9eb4`, 2026-08-31. There is no
window in which this worked.

**4 — The deployed code is the analysed code.** `/api/diag/version` read
2026-09-12T05:50:57Z: `git_sha == git_sha_on_disk == c5a0ba993`, `restart_pending: false`.

### Why it went unseen: the seam test constructs the producer's output

`tests/test_arbitration_fanout_seam.py::test_a_plan_written_by_the_multiplexer_is_readable_by_the_pipeline`
— docstring *"End-to-end on the real structures, not a hand-made dict"* — **never calls
`_attach_fanout_plan`.** It calls the planner, then:

```python
plan["apply_rounds"] = plan["rounds"]          # what apply mode attaches
```

which is precisely what apply mode does **not** attach. Its sibling,
`test_the_multiplexer_writes_the_key_the_pipeline_reads`, asserts only that the *string*
`"apply_rounds"` occurs in the source — presence, never payload.

A test that builds the artifact itself cannot see a producer that builds it differently.
This is the `new-table-wiring-guard` presence-only lesson in test form: **green, and
proving nothing.**

## ⚠️ Correction 1 — MI-274 hop 4b is false; its verdict is not

> *"`apply_rounds` REPLACES the global dispatch — it does not supplement it … A partial
> arming closed both doors."*

**False.** `apply_rounds` never survives validation, so the global dispatch has run
**unchanged** throughout. The prop twin's pre-existing route — occasionally winning the
global symbol slot (`BL-20260827-PROP-ONLY-TWIN-WINS-THE-GLOBAL-SYMBOL-SLOT-AND-STARVES-ITS-PAPER-SIBLING`) — was
**never removed**, and arming `bybit_1` closed no door at all.

`BL-20260911-ARMING-THE-ARBITRATION-FANOUT-ON-BYBIT1-ALONE-REMOVED-THE-PROP-ACCOUNT-S-ONLY-ROUTE-AND-THE-SOAK-RECORDED-IT-EVERY-TIME`
is filed on a false premise and is corrected in place rather than duplicated.

**MI-274's core verdict is untouched:** `breakout_1` IS starved. It is starved by the
*original* global one-winner-per-symbol election, exactly as `BL-20260827` says. The
allowlist was never the cause; the **remedy** has never worked.

That the prop leg's own route still exists is directly evidenced: `trend_donchian_eth_prop`
produced three `pipeline_result` rows and three ticket rows in MI-274's own window. Those
came through the global path.

## ⚠️ Correction 2 — the stale balance does not bind, and hop 5 disproves hop 6

> *"clearing hop 4 alone will not restore trading: the very next gate is
> `prop_balance_stale`, which hard-refuses."*

**Not established, and contradicted by MI-274's own hop-5 table.** Two
`trend_donchian_eth_prop` tickets reached `emit_prop_ticket` on **2026-09-05**
(`16:34:52Z`, `17:01:01Z`), each carrying an `order_package_id`, with the account
snapshot **already ~141h stale**. `emit_prop_ticket` is called from
`execute.py:291`, downstream of the coordinator's sizing, and reaching it requires a
positive qty — so **sizing did not refuse.**

The reason is by design, not by accident. `PropRiskManager.position_size`
(`src/units/accounts/prop_risk.py`) states it in terms:

> *"when `balance_usd` … come through unavailable (`<= 0`/`None`), substitute the nominal
> equity and defer to the base sizer … the coordinator only needs a positive qty to route
> the package to `emit_prop_ticket`."* — operator design, 2026-06-19

**Corroborated by the ticket data.** `risk_usd` census over **163 ticket rows**
(`/api/bot/prop/tickets?limit=200`; 163 < 200, so this is not a truncated page):
**48 rows at `$75.00`, 6 at `$25.00`, none at `$71.81`.**
`$75.00 = 0.015 × $5,000` **nominal** — not `0.015 × $4,787.34` (the reported snapshot),
which would be `$71.81`. MI-274 noticed this figure and did not draw the conclusion.

⚠️ **So "send a fresh `bal` or it will not trade" would be a false thing to tell the
operator.** A fresh `bal` is still wanted — it is what the cushion display and the
rule-distance guard read, and the snapshot is **298.3h old against a 24h limit** — but it
is **not on the critical path** and must not be presented as a precondition.

The `prop_balance_stale` raise in `Coordinator._default_balance_fetcher` is reachable
only when the account is present in `live_balances` with a `None` value **and**
`cached_balance_usd` is `None`. Whether that holds for `breakout_1` on a given tick was
**not** established here; what is established is that on 2026-09-05 it did not, because a
ticket was emitted.

## Nothing in the order path enforces the prop drawdown floor — a SECOND blind spot beside an already-filed one

⚠️ **SELF-CORRECTION.** This section was first written under the heading *"🆕 … not previously recorded
anywhere"*. **That was false and is withdrawn.**
`BL-20260911-PROP-TICKET-RISK-IS-75-DOLLARS-AGAINST-87-DOLLARS-OF-DRAWDOWN-FLOOR-SO-TWO-LOSSES-PERMANENTLY-DISABLE-THE-ACCOUNT`
already says, in terms: *"THE RULE-DISTANCE PANEL COMPUTES THIS CORRECTLY AND NOTHING ACTS ON IT.
distance_to_dd_floor_usd is right there in the payload; the ticket sizing does not consult it."* That is
half of what follows, filed a day earlier, and `BL-20260827-PROP-CUSHION-IGNORES-COMMITTED-OPEN-RISK`
covers a third related defect in the same cushion.

**Why the duplicate check missed it, recorded because it is the instructive part:**
`scripts/ops/backlog_search.py` returned 8 overlapping rows for my query and none was that one — the probe
is **token overlap only**, and I searched *"drawdown floor not enforced order path risk manager"* against a
row phrased *"75 dollars against 87 dollars"*. Its own docstring warns *"silence here is not proof of
novelty"*; this is that warning coming true. What caught it was reading the 54 open prop rows by hand at
the end of the unit, not the tool.

**What is genuinely additive — and the only reason this section survives — is a SECOND, INDEPENDENT
mechanism that neither prior row names:**

* `distance_to_dd_floor_usd` / `rule_distance` have **display-only consumers** —
  `src/web/api/routers/prop.py` and `src/prop/telegram_report_handler.py`. **No branch
  anywhere in the order path reads the cushion.**
* The generic `RiskManager` **cannot see prop outcomes.** `daily_risk_state` is rebuilt
  by `SELECT … FROM trades` (three sites in `src/units/accounts/risk.py`); the prop
  executor writes only `prop_journal.record_ticket` → `prop_tickets` / `prop_fills`; and
  `risk.py` never reads a prop table. **Positive control:** the same grep pattern returns
  hits in `breakout_executor.py`, so the probe finds prop reads where they exist.

⇒ `breakout_1`'s declared `max_dd_pct 0.06` / `daily_loss_pct 0.03` / `daily_usd 150`
**can never trip from prop activity.**

Live, `2026-09-12T05:52Z`: cushion to the static `$4,700` floor is **`$87.34`**, against a
nominal-sized risk of **`$75.00`**. One trade fits, with `$12.34` to spare; two do not.

⚠️ **This is NOT a proposal to stop trading.** The operator has ruled that a low cushion
must not stop trading, that ruling is recorded, and it is not re-litigated. It is raised
so the decision is made **knowingly**, and because sizing off a `$5,000` nominal is
**4.4% above the account's actual equity** — which makes the cushion smaller than the
sizer believes.

The prior rows establish that the **cushion** is not consulted; this establishes that the **generic
caps are inert too**, so there is no second line of defence behind the one they describe. The two
should be resolved together — fixing one and leaving the other would close one of two independent
blind spots and read as done.

## The gate sequence — U1, as asked

Every gate between an elected signal and an emitted `prop_signal`, read off the dispatch
code rather than the docs about it.

| # | gate | where | state for `breakout_1` |
|---|---|---|---|
| 1 | strategy `enabled` + `execution: live` | `config/strategies.yaml` | ✅ 2 of 3 legs live (`eth_pullback_prop_2h` is `shadow`) |
| 2 | account `mode: live` | `config/accounts.yaml` | ✅ live |
| 3 | regime hard gate | `intents.py` | ✅ not firing (0 rows, MI-274) |
| 4 | **global election — one winner per SYMBOL** | `aggregate_intents` | ❌ **BINDING** — prop twin loses to its `bybit_1` sibling |
| 5 | fan-out plan written | `_attach_fanout_plan` | ⚠️ computed correctly, `apply_scope: not_allowlisted` |
| 6 | **fan-out plan READ** | `_fanout_apply_rounds` | ❌ **BINDING — returns `[]` for every account, always** |
| 7 | per-account eligibility (`strategy in assigned`) | `multi_account_execute` | ❌ drops `breakout_1` when the global winner is a `bybit_1`-only leg |
| 8 | balance fetch → sizing | `_default_balance_fetcher` → `PropRiskManager` | ✅ **not binding** — nominal substitution (correction 2) |
| 9 | reticket suppression | `breakout_executor:184` | ✅ **currently clear** — see below |
| 10 | leg build / `skip` on zero size | `build_account_leg` | not reached |

**Gate 9 is clear right now.** Census of **163** ticket rows: **0** in a blocking status
(`placed` / `expiry_prompted` / `awaiting_report`); all **17** `emitted` rows carry a
`valid_until` in the past (2026-08-19 → 08-21), which does not block. Consistent with
MI-274's report that a human cleared both latches at `2026-09-11T16:36Z`. It re-latches on
the next unanswered prompt, so the fix is still owed — it simply is not binding today.

⚠️ **A latent second permanent-latch path**, not previously named: in
`_reticket_suppress_reason`, an `emitted` ticket whose `valid_until` is missing or
unparseable takes `if vu_dt is None or vu_dt > now` and blocks **forever**. Measured
**0 live instances**, so this is latent, not live.

## Proposal — U3

**Tier-2/3. Prepared and routed to the manager. Nothing here is enacted, and no prop
ticket was emitted by hand. Every production read in this document was a GET.**

### Step 1 — [Tier-2, code] Carry the round's geometry into `apply_rounds`. **FIRST.**

`intent_multiplexer.py:1019-1026` — scope the `accounts` list, keep the round:

```python
scoped = [{**r, "accounts": [a for a in r["accounts"] if a in allow]} for r in rounds]
```

…plus a seam test that **calls `_attach_fanout_plan`** and asserts the pipeline reader
accepts its output. The existing test must be fixed, not supplemented: while it
hand-builds `apply_rounds` it will keep passing over any future divergence.

⚠️ **This step changes live order routing on every allowlisted account** — today
`bybit_1`. It moves that account from global dispatch to fan-out dispatch for contested
symbols, which is the behaviour the fan-out was approved for but has never actually
performed. It is Tier-2 and needs its own operator OK; it is **not** covered by the
fan-out's original approval, because that approval was given for a mechanism nobody knew
was inert.

### Step 2 — [Tier-2, routing] Add `breakout_1` to `ARBITRATION_FANOUT_ACCOUNTS`. **Only after step 1 is deployed and observed.**

Needs a trader restart; read the value back off `/proc/<MainPID>/environ` via `get-env`,
never from `.env`.

**What it changes for the other accounts on that allowlist:** nothing about *their*
election — the planner already computes a round per account independently, and
`bybit_1`'s own round is unchanged. What changes is that a contested symbol now produces
**two dispatch rounds instead of one**, so both accounts trade where previously only the
global winner did. That is the declared intent of the fan-out (`BL-20260827`: *"the
correct outcome is that BOTH trade"*), and it is also a genuine increase in concurrent
exposure across accounts — which the operator should price in, since `bybit_2` and
`bybit_portfolio` are also on the roster and `bybit_2` is real money.

### Step 3 — [Tier-2] Bound `expiry_prompted` / `awaiting_report` in `_reticket_suppress_reason`.

An unanswered prompt latches a book indefinitely. The guard's own docstring already
reasons that *"an EXPIRED unacted ticket does NOT block"*, and `expiry_prompted` is
exactly an expired unacted ticket the bot happened to ask about. Fix the null
`valid_until` path in the same change. **Not binding today** (0 of 163), so this is
third, not first.

### Step 4 — [Tier-2] Make the refusal loud, whichever gate ends up binding.

Per `BL-20260909-PROP-SIZING-REFUSAL-ON-A-STALE-BALANCE-IS-JOURNALED-BUT-NEVER-PINGED`.
`prop_balance_unreported` is a recognised cause in `silent_refusal_alert.classify_cause`
but has **no `CAUSE_MIN_ROWS` override**, so it needs 5 rows in 24h — and a prop account
that takes one trade a week will never produce five refusals in a day. Whether to lower
that floor is an **open operator decision**, not a defect to fix on precedent: the
existing `balance_unreadable: 1` override was justified by 11 measured occurrences, and
this cause has occurred **zero** times ever.

### What this unit deliberately did NOT do

* Did not widen the allowlist, edit `config/accounts.yaml`, or touch
  `config/prop_rulesets/**`.
* Did not emit, cancel or modify a prop ticket.
* Did not propose a cushion-based stop — the operator has ruled on that.
* Did not re-run MI-274's attribution.

## U4 — the done-condition, and what is now spent

The observation this unit owes is **`breakout_1` observed routing**: an
`arbitration_fanout_soak` row with `breakout_1` in `rounds_applied` **and** a real ticket
produced by the dispatch path, against a stated window in which its legs signalled.

⚠️ **After this finding, `rounds_applied` alone is no longer sufficient evidence** — it is
a claim about the plan, and 317 rows carried it while nothing dispatched. The done-
condition is therefore tightened: a **`prop_tickets` row with status `emitted`** whose
`order_package_id` traces to a fan-out round, with the positive control that the leg was
signalling in the same window.

**Detector observation — spent, and it cleared.** MI-276 asked that `breakout_1` not be
made to route before its starved-account detector had been seen grading the only live
instance. Measured `2026-09-12T05:48Z`, ~6 minutes after #11827 deployed:

* `starved_account_observed_state` → `breakout_1`: `state: starved_persistent`,
  `starved: 8`, `routed: 0`, `gradeable_rows: 48`, `below_min_rows: false`
* `/api/bot/notifications` → banner `starved_account`: *"breakout_1 is taking no trades —
  starved by arbitration"*
* latch `starved_account_alert_state` = `{"breakout_1": 1789192100.19}` = `05:48:20Z`

Verdict cross-checked against the underlying data rather than taken on trust: the
detector's 8/0 over its 48-row window agrees with this unit's independent census
(39 starved / 39 elected / 0 routed over 400 rows) and with MI-274's 8 of 8.

⚠️ **The detector needs no correction from this finding and independently corroborates
it**: it grades off the soak's per-account `routed` state — global-path routing —
**deliberately not** off `rounds_applied`, which its own docstring calls a trap. It was
right, and for the right reason.

## What this unit did NOT establish

* **`CENTRALIZED_ALLOCATOR`'s live value.** It defaults false and is provisioned nowhere
  in `deploy/` or any `.env` template (grep clean; **positive control**: it hits in
  `ROADMAP.md` and docs), but it has **no read surface** — absent from
  `get_env.py::ALLOWED_KEYS` — and the journal window pulled was **5.5 minutes with no
  dispatch in it**, so that probe had no denominator. Recorded as *could not look*, never
  as a clean negative. If it were on, the fan-out would be bypassed entirely and would
  still never dispatch, so the finding holds either way — but it changes **which** fix is
  right and should be settled before step 1 lands.
* **`daily_risk_state`'s contents.** Not in `diag.py::_JOURNAL_TABLES`, and
  `/api/bot/db/table/...` returns `invalid_session` from this container. A first read
  returned "0 rows" and the **positive control caught it as a broken probe, not an empty
  table**. The prop-isolation conclusion rests on the code, which is authoritative.
* **Whether `breakout_1` appears in `live_balances` on a given tick** — see correction 2.
* **The 17-day `bybit_1` losing streak.** Reported because the detector surfaced it; it
  corroborates MI-271 at +1 day. **Not re-derived here** and not claimed as this unit's
  measurement.
