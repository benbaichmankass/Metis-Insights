# MI-124 — why each of the ten sunset candidates produces nothing

Object: `WO-20260905-NEVER-FIRING-LEGS-NEED-A-REPAIR-DIAGNOSIS-NOT-A-RETIREMENT`
Operator-funded on `DEC-20260904-DEMOTE-AND-TUNE-FLOW` (`agree_flow_and_fund_repair`).
**READ-AND-DIAGNOSE ONLY. Nothing here retires, disables or shadows any leg.**

## Headline

**The sunset packet's stated basis is FALSE for 5 of the 10 candidates.** The packet
says of each: *"has never closed a single trade in its life."* Five of them had
**closed trades in `trade_journal.db::trades` before the packet was generated**.

The mechanism is a collapsed state, and it is fully traced (§2). It is not a
transcription slip — it is load-bearing and it manufactured `retire_candidate`
verdicts from an absence.

Only **1** leg lands `not_established`.

## 1. Population — stated on every count below

| | |
|---|---|
| Journal | live `trade_journal.db` via `https://ict-bot.duckdns.org` (`/api/diag/*` + `/api/bot/db/table/*`) |
| Read at | 2026-09-05T01:16Z–02:4xZ, trader `git_sha 5b6c2b38` |
| Packet under test | `comms/sunset/2026-09-01/INDEX.json`, generated 2026-09-01T23:13:46Z |
| Legs graded by packet | 52 enabled (10 `retire_candidate`, 39 `watch`, 3 `not_assessed`) |
| `signals` table | 2,451,715 rows, reaching back to at least 2026-05-10 |
| `order_packages` / `trades` | 4,404 / 5,488 rows |
| Counting method | `/api/bot/db/table/{t}?filter_col=strategy_name&filter_op=eq` — **every count below asserted `filter_state: "applied"`** before being trusted (an unknown column silently returns the whole-table count) |

### Positive controls — the negatives have a denominator

No verdict here rests on a silent query. Controls were chosen *inside the same
strategy families* as the candidates:

| control | why it controls | actionable signals (lifetime) | order pkgs | closed trades |
|---|---|---|---|---|
| `iwm_trend_long_1d` | same family as 5 candidates | 271 buy | 3 | 7 |
| `qqq_trend_long_1d` | same family | 130 buy | 2 | 2 |
| `qld_trend_long_1d` | same family, 2x-levered Nasdaq (vs TQQQ 3x) | 3 buy | 1 | 1 |
| `trend_donchian_sol_4h` | **same symbol + same account** as `trend_donchian_sol` | 340 buy | 24 | 17 |
| `gld_pullback_1h` | same symbol as `gld_pullback_1d` | 438 buy / 571 sell | 54 | — |
| `ict_scalp_5m` | busiest leg in the fleet | 144 buy / 141 sell | 82 | — |

**One control result changed the whole method.** Over the most recent ~4 days,
`iwm/qqq/qld_trend_long_1d` show `side=none` on 1000/1000 audit rows — identical to
the candidates — while each has lifetime closes. A 4-day window therefore **cannot**
distinguish a trading `trend_long` leg from a non-trading one. Every count in this
document is consequently taken **lifetime**, via `audit_query?strategy=X&side=buy`,
not from a recent tail.

## 2. The packet's basis is defective — the full chain

`scripts/ops/sunset_pass.py:288`:

```python
life = lifetime.get(name, 0 if lifetime_state == "read" else None)
```

with this comment defending the default:

> `/api/bot/performance` lists every strategy with any closed trade, so under
> `read` an absent leg genuinely closed ZERO — a real measurement.

**That claim is false.** `src/web/api/routers/performance.py:324-326`:

```sql
WHERE t.status = 'closed'
  AND COALESCE(t.is_backtest, 0) = 0
  AND t.pnl IS NOT NULL          -- <-- the packet's comment does not account for this
```

The capture lists every strategy with a **pnl-bearing** close, not with *any* close.
So the chain is:

```
every close has pnl NULL  ->  absent from /api/bot/performance
                          ->  lifetime.get(name, 0) yields 0
                          ->  basis "never_closed_lifetime"
                          ->  verdict retire_candidate
                          ->  note "has never closed a single trade in its life"
```

Measured against the live capture (`/api/bot/performance?window=all`, read 2026-09-05):
**52 enabled legs, 46 in the capture, 11 absent and silently defaulted to `0`.**
Of the ten candidates, nine are absent; only `trend_donchian_sol` is present.

*"We did not observe a pnl-bearing close"* and *"the leg never closed a trade"* are
different facts, and the default collapses them. This is the exact class
`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states" exists to catch, and
`sunset_pass.py` already implements the three-state discipline correctly one level
up (`read` / `not_read` / `unreadable`) — the missing state is at the **per-leg**
level, not the capture level.

### The control that makes it conclusive

`iwm_trend_long_1d` — a leg the packet passed as `watch` — has 7 closed trades of
which **4 carry real pnl and 3 carry `pnl=None`, all via the same
`exchange_flat_reconciled` exit path**:

```
id=2771 alpaca_paper  pnl=-2.81449  exchange_flat_reconciled
id=3268 alpaca_paper  pnl=-46.26    exchange_flat_reconciled
id=2772 alpaca_paper  pnl=None      exchange_flat_reconciled
id=4422 alpaca_paper  pnl=None      exchange_flat_reconciled
```

So a NULL pnl is **not** intrinsic to that close path. A leg is visible to the gate
if *at least one* close happens to get pnl stamped, and invisible if none does. The
five falsified candidates are on the wrong side of that coin-flip — they are not
different in kind from `iwm`, only in luck.

## 3. Per-leg causes

Vocabulary: `declining_correctly` · `starved` · `mis_routed` · `refused_at_risk` ·
`broken` · `not_established`.

⚠️ **A note on `broken`, rather than quietly stretching it.** The contract defines
`broken` as *"it raises, or its builder is unreachable"*. For legs 1–5 **the builder
is healthy and the strategy is fine — what is broken is the PnL/exit accounting
around it.** No vocabulary term covers "accounting-broken", so `broken` is used with
its scope named explicitly on every row. It must **not** be read as "the strategy is
broken"; the remedy is in the exit/pnl path, not the strategy.

| # | leg | cause | evidence (lifetime) |
|---|---|---|---|
| 1 | `gdx_pullback_1d` | `broken` — pnl accounting | 200 buy + 332 sell actionable; 8 pkgs; 31 trade rows; **2 closed 2026-08-05 (pre-packet)**, both `pnl=None`; 2 open since 2026-09-02 |
| 2 | `gld_pullback_1d` | `broken` — pnl accounting | 412 buy + 340 sell; 9 pkgs; 20 rows; **3 closed 2026-07-07 & 2026-08-05 (pre-packet)**, all `pnl=None`; 1 open |
| 3 | `iaum_pullback_1d` | `broken` — pnl accounting | 414 buy + 190 sell; 7 pkgs; 13 rows; **1 closed 2026-08-05 (pre-packet)**, `pnl=None`; 1 open |
| 4 | `scha_trend_long_1d` | `broken` — pnl accounting | 112 buy; 1 pkg (`closed`); **1 closed 2026-08-24 (pre-packet)**, `pnl=None` |
| 5 | `spy_trend_long_1d` | `broken` — pnl accounting | 237 buy; 1 pkg (`closed`); **1 closed 2026-08-18 (pre-packet)**, `pnl=None`; **1 trade open since 2026-08-03 (33 days)** |
| 6 | `mes_trend_long_1d` | `broken` — exit/lifecycle accounting | 686 buy (**2.5x the `iwm` control's 271**); 8 pkgs of which **7 `orphaned`**; exactly 1 trade, **open since 2026-08-03, still open at read (33 days)** |
| 7 | `splg_trend_long_1d` | `broken` — candle starvation | **0 actionable ever**; every eval reads `need at least 46 candles for the donchian(30)/atr(14) windows; got 15` |
| 8 | `tqqq_trend_long_1d` | **`not_established`** | builder healthy, valid channels, **0 actionable ever**; see §4 |
| 9 | `trend_donchian_sol` | `starved` by **`trend_donchian_sol_4h`** | 378 buy; 10 pkgs, **5 `no_fill_all_accounts`**; sibling on the *same symbol + same account* has 24 pkgs / 17 closes |
| 10 | `turtle_soup` | `mis_routed` | routed to **no account** in `accounts.yaml` **and** `execution: shadow`; 8 actionable lifetime, last 2026-07-01; 3 pkgs (1 `no_fill_all_accounts`, 2 `orphaned`) |

**`not_established`: 1 of 10** — below the contract's "more than half" review trigger.

### Legs 6, 9, 10 — detail

**6 · `mes_trend_long_1d`.** Already-filed context, not re-derived:
`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG` covers
trade 4350's protection state. Its single trade is **id 4350 on `ib_paper`, sl
`7533.69642857`** — the exact trade `CLAUDE.md` names in the
`PROTECTION_REASSERT_MODE` row as having sat with a diverged protective leg. It is
still open 33 days on. 7 of 8 order packages are `orphaned`. The packet's literal
claim (never closed a trade) is TRUE here; its *framing* as a silent leg is not — it
is the fleet's loudest signaller in this group and holds a live position.

**9 · `trend_donchian_sol`.** Starvation is **already established** by
`MI-29` and `OI-20260831-PER-ACCOUNT-ARBITRATION` (measured: won 0 of 60 SOLUSDT
buy-side ticks 2026-08-01..08-27, zero journal rows on `bybit_1` since 2026-07-07).
Not re-derived. What this pass adds is the sibling contrast on identical
symbol+account. ⚠️ **The packet was CORRECT about this leg at generation time** — its
one close (`id 5419`, pnl `163.133`) is dated **2026-09-04, after the 09-01 packet**.
This is why my count of falsified premises is **5, not 6**.

**10 · `turtle_soup`.** Two independent reasons it cannot produce a live trade, and
**the packet records only one**. Its basis is `unrouted`; it does not mention that
the leg is also `execution: shadow`, which by design never sends a live order. A leg
in shadow producing no live trades is the execution gate **working exactly as
declared** — the sunset pass grades shadow legs on a criterion they are declared
exempt from.

## 4. Why `tqqq_trend_long_1d` is `not_established`

Its builder is demonstrably healthy — it emits valid channels every tick, identical
in shape to controls that fire:

```
tqqq: close=72.42  within channel [57.6, 77.89]
qld : close=90.725 within channel [77.46, 95.015]   <- sibling; 3 buys, 1 close
```

Discriminating measurement — channel headroom `(hi-close)/(hi-lo)`, `0.0` = at the
breakout line. Sampled 60 evals on each of 7 dates spanning 2026-07-01..2026-09-04:

| leg | n | min headroom | median | ever fired? |
|---|---|---|---|---|
| `tqqq_trend_long_1d` | 360 | **0.114** | 0.421 | **no** |
| `qld_trend_long_1d` (control) | 360 | 0.063 | 0.375 | yes (3 buys) |
| `iwm_trend_long_1d` (control) | 350 | 0.001 | 0.356 | yes (271 buys) |
| `scha_trend_long_1d` | 300 | 0.060 | 0.451 | yes (112 buys) |
| `spy_trend_long_1d` | 420 | 0.056 | 0.193 | yes (237 buys) |
| `mes_trend_long_1d` | 420 | 0.065 | 0.240 | yes (686 buys) |

TQQQ never came closer than 11.4% of channel width to its breakout line, while every
leg that fired reached ≤6.5% (population: the 5 legs tabulated directly above, n=360/350/300/420/420 sampled evals each). That is **consistent with** correctly declining — but
**it does not establish it**, for a reason I will not paper over: 360 sampled evals
are a small fraction of the eval stream, and a minimum over a sample understates the
true minimum. I cannot separate *"TQQQ genuinely never closed outside its 30-day
channel"* from *"a parameter or data issue keeps close inside it"* on this evidence.

`not_established` is the honest terminal state. It is **not** "I did not look".

## 5. Recommendations — one per leg, none of them a retirement

| leg | recommendation |
|---|---|
| `gdx_pullback_1d` | **Repair the accounting, not the leg.** Fix pnl stamping on alpaca reconciler closes, then re-grade. Leave config alone. |
| `gld_pullback_1d` | As above. |
| `iaum_pullback_1d` | As above. |
| `scha_trend_long_1d` | As above. |
| `spy_trend_long_1d` | As above, **plus** investigate the trade open since 2026-08-03. |
| `mes_trend_long_1d` | **Repair.** Resolve trade 4350 and the 7 orphaned packages. A leg holding a 33-day position cannot re-enter; this is a lifecycle defect, not a dead strategy. |
| `splg_trend_long_1d` | **Repair.** Restore the SPLG 1d candle supply (needs 46, gets 15). Until then it cannot signal at all. |
| `tqqq_trend_long_1d` | **Bounded follow-up.** Compare TQQQ daily history against its own donchian(30) offline — one backtest answers it. Do not act on silence. |
| `trend_donchian_sol` | **Leave the leg; fix the arbitration.** It is behaving correctly and losing a first-come race to `trend_donchian_sol_4h`. Remedy belongs to MI-29. |
| `turtle_soup` | **Decide the intent, then re-route or exempt.** If it is a shadow research leg it is behaving exactly as declared and should be excluded from the sunset pass; if it is meant to trade, route it. |

## 6. Findings about the machinery itself

1. **`sunset_pass.py:288` collapses "absent from capture" into "closed zero".** The
   defended assumption about `/api/bot/performance` omits its `pnl IS NOT NULL`
   filter. 11 of 52 enabled legs are currently absent and defaulted to `0`.
2. **The sunset pass grades `shadow` legs on live-trade criteria.** `turtle_soup` is
   `execution: shadow`; the packet's note does not mention it.
3. **A large family of closes lands `pnl=None`.** **Already filed — not restated
   here.** `BL-20260807-BULK-RECONCILER-CLOSE-NO-EXIT-NO-PNL` records 7 rows bulk-closed
   on **2026-08-05** with null exit price and null realized pnl — the same date as
   three of the five falsified legs (`gdx`, `gld`, `iaum`), so that event is very
   likely the direct upstream cause. `BL-20260825-EXIT-PROVENANCE-IS-STRUCTURED-BY-EXIT-PATH-SIX-PATHS-AT-ZERO`
   covers the wider class. Same exit path yields pnl
   sometimes and not others (`iwm` above). This is upstream of the sunset pass and
   affects the M7 gate identically — a leg invisible to `/api/bot/performance` is
   ungradeable everywhere, not just here.
4. **Not one of the ten was silent.** Eight of ten emitted actionable signals; the
   two that did not (`splg`, `tqqq`) did so for opposite reasons — one demonstrably
   broken, one not established.


## 7. Filed

- `BL-20260905-SUNSET-PASS-DEFAULTS-AN-ABSENT-LEG-TO-ZERO-CLOSES-AND-CALLS-IT-NEVER-TRADED` (high)
- `BL-20260905-SUNSET-PASS-GRADES-SHADOW-LEGS-ON-A-LIVE-TRADE-CRITERION` (medium)

Deliberately **not** filed, because each restates an existing open row:
the pnl-NULL close class (`BL-20260807`, `BL-20260825`) and trade 4350's
protection state (`BL-20260820`).

## 8. On the predecessor's lead

The lost predecessor session reported *"6 false premises, 2 broken (exit accounting,
candle starvation), 1 not_established, 1 mis_routed"*. Its evidence was gone, so this
was treated as a hypothesis and re-measured independently. It **substantially holds**,
with one correction that is mine:

- falsified premises: **5, not 6.** `trend_donchian_sol` is the difference — its only
  close (`id 5419`) is dated **2026-09-04**, *after* the 2026-09-01 packet, so the
  packet was **correct** about that leg when it was written. Crediting it would have
  been an unfair reading of a dated artifact. That leg is `starved`, which is a real
  cause and not a false premise.
- `broken` ×2 (exit accounting `mes`, candle starvation `splg`): **confirmed**, and I
  would add that legs 1–5 are also accounting-broken rather than strategy-broken.
- `not_established` ×1 (`tqqq`): **confirmed**.
- `mis_routed` ×1 (`turtle_soup`): **confirmed**, plus the shadow finding the packet
  omits.

## 9. Landing note — why PR #11019's own description is boilerplate

`claude-pr-automerge.yml` fires on any push to a `claude/**` branch touching
`.github/pr-automerge-requests/`, and opens the PR itself. The Tier-1 protocol
requires that file, so pushing the mandated landing pair opened #11019 with
`title = head-commit subject` and a two-line body before `pr-opener.yml` could
supply the real ones; `pr-opener` then exited `FAILED: ... already exists`, and
`update_pull_request` 403s from this session.

Filed as `BL-20260905-AUTOMERGE-RELAY-WINS-THE-RACE-WITH-PR-OPENER-SO-EVERY-TIER-1-PR-GETS-A-BOILERPLATE-BODY`.
It is **not** the `pr-opener` draft cluster (`BL-20260903-*`, `BL-20260904-*`) —
there `pr-opener` opened the PR and got the draft flag wrong; here it opened
nothing and the PR is correctly not a draft.

**This document is the PR description.** Read §§1–8 as the change's rationale.
