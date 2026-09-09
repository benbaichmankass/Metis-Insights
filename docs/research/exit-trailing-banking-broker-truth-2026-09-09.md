# Exit trailing and banking — the broker-truth restriction the object asked for

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> **MI-209** · object [`WO-20260908-EXIT-MECHANICS-THE-TRAILING-AND-BANKING-HALF`](../claude/work/objects/WO-20260908-EXIT-MECHANICS-THE-TRAILING-AND-BANKING-HALF.yaml) · cycle `CY-20260906-TRADING-TRUTH`
>
> **Addendum to** [`exit-trailing-and-banking-measurement-2026-09-08.md`](exit-trailing-and-banking-measurement-2026-09-08.md) (MI-188b, PR #11351, `34b82575`). **Read that first** — this does not restate it.

**MEASUREMENT ONLY.** No parameter proposed, no exit-matrix cell re-graded, no lever
armed, no live position touched. Every read was a GET. Findings are FILED.

---

## 0. The premise I was dispatched on was stale — say it before anything else

My dispatch brief said the trailing/banking half *"has never been measured on the
same footing"* and that the operator has it recorded as **still owed**.

**It was measured on 2026-09-08 by MI-188b and it LANDED** — PR #11351, commit
`34b82575`, `docs/research/exit-trailing-and-banking-measurement-2026-09-08.md`,
on `main`. I checked rather than inherited.

**Why it was almost certainly re-dispatched:** the object file on `main` still reads
`lifecycle: dormant`, `owner: null`, `closed_at: null`. **The work landed; the object
was never closed out.** That is the same shape MI-193 measured — a register whose only
writer is the act of *starting*, and whose truth ends at an event nothing writes back.
Closing the object is therefore part of this unit, not housekeeping.

**What was genuinely still owed**, and is what this addendum is:

> the object's own `done_condition` says *"read from broker-truth rows only
> (`provenance.is_measured`), never from journal prose."*

MI-188b computed the provenance split and then ran its decisive test over a population
it reports as **47 measured / 102 estimated / 14 fabricated / 3 unverified**. The
restriction the condition names was never applied to the result. Applying it is cheap,
and — as §3 shows — it **reverses one of the headlines**.

---

## 1. What was read

| source | how | population bound |
|---|---|---|
| `trades` | `GET /api/bot/db/table/trades`, paged `offset` 0→5589, `order_by=id&order_dir=asc` | **complete census, n=5589**; `total` echoed 5589 and 5589 rows were fetched — asserted in the fetch loop, not assumed |
| `order_packages` | same, paged 0→4500 | **complete census, n=4500** |

Read **2026-09-09**, live VM `git_sha` `47f5a981` (`restart_pending: true`, on-disk
`36ac0d57`). MI-188b read 5550 / 4467 on 2026-09-08; the deltas (+39 / +33) are one
day of ordinary trading.

Provenance is `src/runtime/provenance.py`, **imported, not re-derived**.

⚠️ **`is_measured` takes a ROW, not a bucket string.** `provenance.is_measured(row)`
calls `classify_row(row, "exit_price_source")`. Handed a bucket string it reads
`row['exit_price_source']` off a `str`, catches the `TypeError`, and returns
`UNVERIFIED` for **every** row — so the predicate silently and totally returns `0 of
1502`. I made exactly that mistake, caught it only because a `Counter` in the same run
independently showed 585 `measured` rows, and the corrected call returns **585**. This
is the failure mode `pnl_is_trustworthy`'s own docstring warns about, one function
along. **Assert a non-zero positive control before trusting a provenance filter's
silence.**

**Decision population** (closed, non-backtest, `pnl NOT NULL`) = **1502**;
`is_backtest` is 0 on all 5589 rows, so that clause removes nothing.
`is_measured` = **585 of 1502 (38.9 %)**. MI-188b: 577 of 1475 (39.1 %) — reproduces.

---

## 2. MI-188b's structural findings reproduce on a fresh census

Run independently a day later, not read off its doc:

| MI-188b (2026-09-08) | MI-209 (2026-09-09) | verdict |
|---|---|---|
| packages whose stop demonstrably moved: **140** | **146** | reproduces |
| gradeable packages: **2098** → 6.7 % | **2131** → **6.85 %** | reproduces |
| ungradeable (*no entry stop recorded — we cannot look*): **2369** | **2369** (unchanged — the `exit_plan` migration is behind us) | reproduces |
| moves favourable / adverse: **140 / 0** | **146 / 0** (95 long, 51 short) | reproduces |
| partial bank, complete census | **0** — see §4 | reproduces |

Gradeable packages span **2026-06-18 → 2026-09-09**; ungradeable span
**2026-05-02 → 2026-07-16**. So the trailing lower bound reads *"since 2026-06-18"*
and says nothing about May, exactly as MI-188b stated. **The 146 remains a lower bound
on PACKAGES, not a count of amends** — N amends on one package collapse to one
observation, and there is still no durable per-amend record (MI-188b §1, and
`BL-20260908-A-TRAILING-AMEND-HAS-NO-DURABLE-RECORD-SO-ITS-RATE-IS-UNMEASURABLE`).

⚠️ **That row asserts an impossibility, so I re-derived it rather than citing it**
(`checked: src/runtime/order_monitor.py`, and `checked: src/units/db/database.py`).
Of the **19** `update_order_package(` call sites in `src/`, the `KIND_MODIFY` branch
at `order_monitor.py:1460` is the **only** one that writes `sl`; every other site
writes `status` / `close_reason` / `linked_trade_id` / `meta` / `tp`. `update_order_package`
is a plain `UPDATE … SET` with no history table, and `db.update_trade` likewise. So the
claim is **substantiated, not inherited** — the amend rate is unrecoverable from the
schema as it stands, and the remedy is a writer, not a cleverer query.

---

## 3. The broker-truth restriction — and the headline that reverses

**Population: closed trades on the 146 demonstrably-trailed packages = 194.**
(194 > 146 because a multi-leg package carries several trade rows.)

| | n | provenance (`exit_price_source`) |
|---|---:|---|
| all closed trades on trailed packages | **194** | estimated 108 · **measured 51** · unverified 21 · fabricated 14 |
| **broker-truth subset** | **51** | — |

### 3.1 Trail vs declared TP — MI-188b's ordering does not survive

| | stop-out (`sl`+`sl_cross`) | declared TP (`tp`+`tp_cross`) | n |
|---|---:|---:|---:|
| all trailed closed trades | **40** | 33 | 194 |
| **broker-truth only** | **14** | **17** | **51** |

MI-188b concluded: *"on the trailed population it ends trades slightly more often than
the declared TP does (33 confirmed trailed stop-outs vs 32 TP exits)."*

**Restricted to broker truth the ordering reverses — 14 stop-outs against 17 TP exits**
— and after §3.2's grading only **12** of those 14 stop-outs actually landed at the
trailed level, so the gap widens to **12 vs 17** in the other direction.

⚠️ **Do not report either ordering as established.** 14 against 17 at n=51 is well
inside noise, and 12 against 17 is not much better. **The honest statement is that
whether the trail or the declared TP ends more trades is NOT ESTABLISHED**, and that
MI-188b's version of it rests on a population three quarters of which is not broker
truth. This is the repo's own *"a headline whose sign flips on a filter choice must
state the filter"* rule, firing on our own number.

### 3.2 Where the stop-outs actually landed — three states, not two

MI-188b graded each stop-out as **nearest of two** levels (entry stop vs final stop)
and got **33 trailed / 3 original** on n=36. A nearest-of-two rule has no way to say
***neither*** — it forces a row that gapped through or slipped well past both levels
into whichever it happens to be marginally closer to. That is the collapsed-state
shape this repo binds against, so I graded with an explicit tolerance and a fourth
outcome (`indistinguishable`, when the two levels are within tolerance of each other).

**Population: the 40 stop-outs on trailed packages; broker-truth subset n=14.**
Distance is `|exit − level| / entry`. Final stop taken from `order_packages.sl`:

| tolerance | ALL (n=40) | BROKER-TRUTH (n=14) |
|---|---|---|
| 5 bp | at_trailed **26** · at_neither 13 · at_original 1 | at_trailed **12** · at_original 1 · at_neither 1 |
| 10 bp | at_trailed **28** · at_neither 11 · at_original 1 | at_trailed **12** · at_original 1 · at_neither 1 |
| 25 bp | at_trailed **32** · indistinguishable 2 · at_neither 5 · at_original 1 | at_trailed **11** · indistinguishable 2 · at_neither 1 |

**The core claim survives and is now better evidenced: on broker-truth rows, 12 of 14
stop-outs on a demonstrably-trailed package exited AT THE TRAILED LEVEL** — stable
across a 5× tolerance range. Typical `d_trail` is 0.001–0.013 % against `d_orig` of
0.2–4.2 %, i.e. a tick versus percent-scale. **The trail is real and it does end
trades.**

**What the third state buys:** at 5 bp, **13 of 40 rows (32.5 %) landed at NEITHER
level** and MI-188b's rule scored every one of them as trailed-or-original by
proximity. On broker truth that collapses to **1 of 14**. The `at_neither` rows are
therefore concentrated in the *estimated* population — which is what you would predict,
since a `candle_at_close` anchor is a bar close and not a fill, so it has no reason to
sit on a stop level. **The restriction did not just shrink n; it removed a class of row
that the binary rule was silently mis-grading.**

### 3.3 ⚠️ The answer depends on WHICH stop field you call "the final stop"

Same 40 rows, same grading, final stop taken from `trades.stop_loss` instead:

| tolerance | ALL (n=40) | BROKER-TRUTH (n=14) |
|---|---|---|
| 5 bp | at_trailed 22 · at_neither 17 · indistinguishable 1 | at_trailed **9** · at_neither 4 · indistinguishable 1 |
| 25 bp | at_trailed 27 · at_neither 10 · indistinguishable 2 | at_trailed **8** · at_neither 4 · indistinguishable 2 |

**12 or 9 out of 14, depending on a field choice** — and nothing in either doc's method
section forces the choice. MI-188b used the package field (its worked example, trade
3577, quotes a trailed level of 1.1116 that appears **only** in `order_packages.sl`;
that row's `trades.stop_loss` is still 1.0655).

**Which field is right is adjudicated by the exit price, and it favours the package
field.** Over the 194 closed trades on trailed packages, `trades.stop_loss` diverges
from `order_packages.sl` on **46 (23.7 %)**, and **all 46** sit *exactly* on the
entry-time stop — the leg row was never moved at all.

**Most of that divergence is legitimate and I am not filing it as a defect.**
`_apply_update` syncs `trades.stop_loss` only for legs returned by
`_package_open_legs`, so a leg that closed before a later amend correctly keeps its own
entry stop; 40 of the 46 closed before their package's `updated_at`, and all 46 belong
to 2-, 3- or 4-leg packages. That is the design working.

**But 4 of the 46 carry positive evidence against that explanation** — their exit price
sits within 5 bp of `order_packages.sl` while their own row still reads the entry stop,
so the level actually resting at the venue was the package's, not the row's:

| trade | strategy | reason | broker-truth | row `stop_loss` | package `sl` | `exit_price` |
|---|---|---|:---:|---|---|---|
| 2823 | `eth_pullback_2h` | `sl` | ✅ | 1712.03 | 1643.84 | 1643.99 |
| 3433 | `trend_donchian` | `sl` | ✅ | 62906.1 | 63971.7 | 63944 |
| 3577 | `xrp_pullback_2h` | `sl` | ✅ | 1.0655 | 1.1116 | **1.1115** |
| 4469 | `qqq_pullback_1h` | `sl_cross` | ✗ | 704.327 | 716.115 | 716.04 |

Trade 3577 is the clean one: the exit matches the package level to **0.009 %** — one
tick on XRP — four minutes before that package row's `updated_at`. Two independently
set levels agreeing to a tick is not a plausible coincidence.

⚠️ **I did not establish the mechanism and I am not claiming one.** The comment at
`order_monitor.py` ≈ L1397 says the per-leg sync exists precisely so *"a leg whose
amend failed must keep showing its real venue level"*; on these four rows the row shows
a level the venue did not have. **Filed, not diagnosed.** What matters for anyone
reading either doc is narrower and certain: **`trades.stop_loss` is not a safe proxy
for the level resting at the venue, and `/api/bot/positions` reads it.** The other 42
divergent rows were **not adjudicated** — they are consistent with legitimate early
close, which is not the same as shown to be it.

---

## 4. Partial bank — still exactly ZERO, independently confirmed

**Population: all 5589 trade rows, complete census** (MI-188b: 5550, one day earlier).

| probe | n |
|---|---:|
| rows whose `notes.partial_closes` is non-empty | **0** |
| rows where the `partial_closes` key exists at all | **0** |
| rows carrying `notes.original_position_size` (stamped on the first partial) | **0** |
| `exit_reason` containing `partial` | **0** |
| `exit_reason` containing `tp1` | **0** |
| `take_profit_2 > 0` | **0** |
| packages carrying `meta.tp2` (the ladder precondition) | **3 of 4500** — all `turtle_soup`, statuses `orphaned` ×2, `rejected` ×1 |

`original_position_size` is a probe MI-188b did not run and it agrees: the writer
stamps it on the **first** partial, so a zero there independently confirms
`_apply_partial_close` has never executed, rather than only that its output was not
retained.

MI-188b's conclusion stands unchanged and is the important half: **this zero is not a
broken mechanism, it is an unreachable one.** Nothing here re-opens that; its four
gates are unchanged on today's census.

---

## 5. What is NOT established

- **The number of trailing amends.** Only a lower bound on *packages* (≥146 since
  2026-06-18), for the reason MI-188b established: no durable per-amend record exists.
- **Whether the trail or the declared TP ends more trades** (§3.1). Both orderings have
  appeared depending on the population; n=51 broker-truth rows cannot settle it.
- **The mechanism behind the four stale-row cases** in §3.3, and the disposition of the
  other 42 divergent rows.
- **Whether trailing HELPS.** Unchanged from MI-188b: no lever-OFF arm exists
  (`BL-20260814-NINE-SHIPPED-LEVERS-NEVER-GRADED-AGAINST-THEIR-OWN-ABSENCE`).
- **Anything before 2026-06-18.** 2369 of 4500 packages carry no entry-time stop and
  are `ungradeable`, never `unmoved`.

## 6. Does this answer the sibling object `WO-20260907-THE-TRAILING-BANKING-HALF-OF-THE-EXIT`?

**Partly — one of its two halves, and I did not merge the objects.**

- **"Is `exit_plan.py` live or shadow"** — **answered, and it is not mine to claim as
  new.** MI-163 (`scripts/research/banking_mechanism_audit.py`) established by tracing
  readers that `exit_plan` is **not on the live exit path**. This unit adds one
  corroborating fact from a different direction: `order_packages.exit_plan` has no
  update site in `src/`, which is what makes it usable here as a frozen entry-time
  record — a shadow artifact is exactly what an immutable one is.
- **"Does the POOLED corpus have sufficient n for a fleet-wide bank-at-XR rule"** —
  **not answered, and this unit is evidence it is further away than it looks.** The
  broker-truth trailed population is **n=51**, of which 14 are stop-outs; the repo's
  own live-n floor (`MIN_LIVE_N = 30`) is not met for the stop-out question even
  pooled across the whole fleet.

---

## Reproducing

```bash
# complete census; assert fetched == total before calling it one
curl -s "https://ict-bot.duckdns.org/api/bot/db/table/trades?limit=500&offset=N&order_by=id&order_dir=asc"
curl -s "https://ict-bot.duckdns.org/api/bot/db/table/order_packages?limit=500&offset=N&order_by=created_at&order_dir=asc"
```

`scripts/ops/diag_fetch.sh` handles `/api/diag/*` only and 404s on `/api/bot/db/...`
(MI-188b's note, confirmed) — curl the Caddy host directly for those. Provenance via
`from src.runtime import provenance as P; P.is_measured(row)` — **pass the row**.

Derived counts are committed at
[`data/exit-trailing-banking-broker-truth-2026-09-09.json`](data/exit-trailing-banking-broker-truth-2026-09-09.json).
