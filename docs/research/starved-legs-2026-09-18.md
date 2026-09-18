# Which legs are starved, how often, and does the fan-out cover them — the answer is four prop legs and one account

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> MI-29 · `2026-09-18` · answers `DEC-20260910-MI-29-MEASURE-THE-STARVED-LEGS-FIRST`
> · re-measures `MI-29-LEGS-ARE-STARVED-BY-SIBLINGS-ON-ONE-NETTED-SYMBOL` (#10717) and `PB-20260902-MGC-TREND-1H-IS-LIVE-AND-STRUCTURALLY-CANNOT-TRADE`

**⚠️ MEASUREMENT ONLY, per the operator's own scope. Nothing in `config/` or the environment is
touched. Widening `ARBITRATION_FANOUT_ACCOUNTS` is Tier-2 and re-rostering a leg is Tier-3; both are
put back as options in § 6 and neither is taken.**

---

## 0. The answer in one paragraph

**Starvation — a leg losing its symbol because ANOTHER ACCOUNT took the winner — is four legs and
essentially one account, and the number is smaller than the framing implied.** Over the **complete**
`arbitration_fanout_soak` (771 rows, 889,195 bytes, `2026-08-30T14:25:15` → `2026-09-18T20:00:35`),
the starved account is **`breakout_1` on 88 of 90 gradings**, and the candidates it was holding are
**`eth_pullback_prop_2h`** (shadow), **`trend_donchian_eth_prop`** and **`trend_donchian_sol_prop`**
(both `execution: live`), plus two singletons. **No non-prop `execution: live` leg is starved more
than twice in either epoch.** The much larger number sitting beside it — **208 account-ticks** — is
`no_winner`, which `arbitration_fanout.py`'s own docstring says is **not** starvation and which
already overstated this finding **6.5×** once before; the first draft of this measurement made that
error again and it is recorded in § 2 rather than quietly fixed. **The fan-out does not cover them:**
it now genuinely dispatches — **89 order packages carry `meta.arbitration_fanout_round`**, all on
`bybit_1`, from `2026-09-13T01:41` — but **0 of 89 dispatched a leg other than the tick's global
winner**, so it is exercised and has never yet changed an outcome, and `breakout_1` reads
`not_allowlisted` on **all 88** of its starved gradings. And the two live starved prop legs are
exactly the **only two** `execution: live` legs in the fleet that produced order packages and **zero
fills** in the 27.4-day journal window.

---

## 1. Population, stated three times because there are three of them

| source | population |
|---|---|
| `arbitration_fanout_soak` | **the COMPLETE file** — 771 rows returned against a 1000-row cap, 889,195 B, `2026-08-30T14:25:15` → `2026-09-18T20:00:35`. Split **514 schema-2 / 244 schema-3 / 13 pre-schema**. |
| `order_packages` + `trades` | the **overlap window** in which both are complete under their own 1000-row caps: `2026-08-22T10:45:24` → `2026-09-18T20:00:39` (27.4 d), **834 packages / 999 trades rows**. |
| the roster | `config/strategies.yaml` at `b6f27788a` — 52 enabled legs, of which **44 `execution: live`**. |

⚠️ **The two journal tables are capped INDEPENDENTLY, so they do not span the same time.** Unrestricted,
`order_packages` reaches back to `2026-06-02` while `trades` reaches only `2026-08-22` — so a leg's
June package would be compared against a fill table that cannot contain its fill. MI-29's own text
called this out for its 30.9-day window and this memo uses the intersection for the same reason.

⚠️ **And the soak's INCLUSION RULE changed mid-file, under the same filename.** A row is written only
when the tick is *notable*: `bool(plan["apply_rounds"]) or starved or no_winner or
winner_unattributed`. Before 2026-09-12 `apply_rounds` was a two-key projection its own reader
refused, so it was **always empty** and only a starved/no-winner tick could put a row on disk. After
the repair it is populated on every planned tick, so **quiet ticks now appear**. A rate taken across
the whole file therefore divides post-repair numerators by a pre-repair denominator. **Every table
below is split on `fanout_schema` for that reason and no other.**

---

## 2. The distinction that changes the answer — and the error I made first

`src/runtime/arbitration_fanout.py` states it in its own docstring, and records what ignoring it cost:

> ⚠️ `starved` MEANS *ANOTHER ACCOUNT TOOK THE WINNER FROM ME*, AND NOTHING WIDER. A tick on which NO
> strategy won the symbol at all is graded `no_winner` … Until 2026-08-30 those ticks were graded
> `starved`: on the whole live file that was **11 of the 13 starved gradings, overstating the finding
> 6.5×** in the sole evidence base for the Tier-3 change. Do not re-merge the two.

**The first pass of this measurement re-merged them.** It reported `trend_donchian` — an
`execution: live` leg — as losing **27 of its 42 candidate appearances in the schema-2 epoch (64.3%)**,
and would have put that in an options packet. **Every one of those 27 was `no_winner`**: the leg lost
to **nobody**, its account simply elected no one, so its true `lost_to_sibling` count over that same
population of 42 is **0**. Three outcomes, kept apart from here on:

| outcome | meaning |
|---|---|
| `elected` | this leg won its account |
| **`lost_to_sibling`** | a **different named leg** was elected for this account — a contest, genuinely lost |
| **`no_winner`** | **nothing** was elected for this account — no other leg took anything, and the cause is upstream (candidates held, gated or flat) where a per-account fan-out is not the remedy |

**Measured totals, which is why it matters:**

| epoch | `elected` | `lost_to_sibling` | `no_winner` |
|---|---:|---:|---:|
| schema 2 (514 rows) | 605 | **33** | **141** |
| schema 3 (244 rows) | 269 | **16** | **67** |

Pooling the last two columns inflates "starvation" by **4.3×** and **4.2×** respectively.

---

## 3. Which legs are starved, and how often

Read from `per_account[*].state == "starved"` — the account-level state the fan-out exists to fix —
and reported as the candidate that account was holding when another account took the symbol.

**Schema 2 · `2026-08-30` → `2026-09-12` · 514 rows**

| starved account | gradings |
|---|---:|
| `breakout_1` | **50** |
| `bybit_1` | 1 |

| candidate held by the starved account | execution | count | the winner that took the symbol |
|---|---|---:|---|
| `eth_pullback_prop_2h` | **shadow** | 27 | `eth_pullback_2h` ×27 |
| `trend_donchian_eth_prop` | **live** | **15** | `trend_donchian_eth` ×15 |
| `trend_donchian_sol_prop` | **live** | **8** | `trend_donchian_sol` ×8 |
| `trend_donchian_eth` | live | 1 | `trend_donchian_eth_prop` ×1 |

**Schema 3 · `2026-09-12` → `2026-09-18` · 244 rows**

| starved account | gradings |
|---|---:|
| `breakout_1` | **38** |
| `bybit_2` | 1 |
| `bybit_portfolio` | 1 |

| candidate held by the starved account | execution | count |
|---|---|---:|
| `eth_pullback_prop_2h` | **shadow** | 14 |
| `trend_donchian_eth_prop` | **live** | **14** |
| `trend_donchian_sol_prop` | **live** | **10** |
| `trend_donchian_eth_4h` | live | 2 |

**So the starved set is stable across the repair, it is dominated by the prop legs, and the account is
`breakout_1`.** That is the same condition
`OI-20260911-THE-PROP-ACCOUNT-IS-STARVED-NOT-QUIET-AND-THE-UNBLOCK-IS-A-SEQUENCE-NOT-A-FIX` already
carries — **MI-29 resolves into it**, with the per-leg breakdown that row does not have.

⚠️ **The headline loss rates belong to SHADOW legs and cost nothing real.** `htf_pullback_trend_2h`
is a candidate 60 times post-repair and elected twice — but it is `execution: shadow`, and 58 of
those 58 non-elections are `no_winner`, not losses. `eth_pullback_prop_2h` loses exactly **50.0%** in
both epochs (27/54, 14/28), which is not a coincidence to explain: it is a candidate on two accounts
and elected on one each time.

---

## 4. Does the fan-out cover them? It dispatches, and it has never changed an outcome

**POPULATION: all 1,000 rows of `order_packages` (the diag cap), spanning `2026-06-02` →
`2026-09-18`, joined to the complete soak on `(symbol, strategy, entry)`.**

| | value |
|---|---:|
| packages carrying `meta.arbitration_fanout_round` | **89 of 1,000** |
| span | `2026-09-13T01:41` → `2026-09-18T20:00` |
| accounts named in the round | **`bybit_1` ×89** — and no other |
| status | 50 closed · 32 rejected · 7 open |
| unjoinable to a soak round | **0** |
| **dispatched leg == the tick's global winner** | **89** |
| **dispatched leg != the tick's global winner** | **0** |

**Both halves matter and they are different facts.** The first day a package carried a fan-out round
is `2026-09-13`, the day after the projection repair (`1baff7a1f`, 2026-09-12) — so the mechanism is
**exercised end to end**. But it has **never dispatched a leg the global election would not have
picked anyway**, so it has not yet rescued anybody. That is consistent rather than contradictory:
`bybit_1` is the account that generally *holds* the winner, and the account that does not —
`breakout_1` — reads **`not_allowlisted` on all 88 of its starved gradings**, read from the
`apply_scope` the **running process** stamps on each row rather than from a `.env` that says only
what the next restart would pick up.

⚠️ **A candidate finding that dissolved, recorded rather than dropped.** 21 of the 89 packages carry
`sl`/`tp` that match no soak round. **All 21 have `updated_at != created_at`** — they were amended
after dispatch (a trailing stop), so the package row carries the *current* geometry while the soak
carries the *dispatched* one. **Unexplained mismatches: 0.** Anyone joining these two surfaces needs
this or they will file a geometry divergence that is not there. (An earlier count of 23 was a join
artifact from taking the first matching soak row where the key recurs across ticks; corrected by
comparing against **every** matching row.)

### 4.1 This also settles a standing row, on both of its clauses

`OI-20260912-FANOUT-APPLY-PATH-REPAIRED-AND-STILL-HAS-NEVER-DISPATCHED` says *"No `fanout_schema:3`
soak row has been written at all"* and requires two observations. Both are now met:

- **(1) writer** — 244 `fanout_schema: 3` rows exist, `apply_state: dispatchable` on 183 of them, and
  `rounds_applied[0]` carries `side`/`entry`/`sl`/`tp`/`confidence` (verbatim sample in the artifact).
- **(2) dispatch** — 89 packages carry the round, with `linked_trade_id` populated, and their
  geometry matches the round rather than the global winner's (§ 4).

⚠️ **The row's own warning still binds and is why § 4 reports the two counts separately:** a
`rounds_applied` entry is a claim about the PLAN. What clears clause (2) is the package, and that is
what is measured.

---

## 5. The other layer, which the fan-out cannot see

**POPULATION: the 27.4-day journal overlap window; 44 `execution: live` legs.**

**11 of 44 produced zero genuine fills.** Nine produced **zero order packages** as well —
`ief_pullback_1d`, `iwm_trend_long_1d`, `mes_trend_long_1d`, `qld_trend_long_1d`, `qqq_trend_long_1d`,
`scha_trend_long_1d`, `splg_trend_long_1d`, `spy_trend_long_1d`, `tqqq_trend_long_1d` — so they are
**silent, not starved**: nothing took anything from them. **Exactly two produced packages and no
fills, and they are the two live prop legs from § 3**: `trend_donchian_eth_prop` (12 packages) and
`trend_donchian_sol_prop` (8). The two measurements meet on the same two legs from opposite ends.

⚠️ **A ROW IN `trades` IS NOT A FILL, AND COUNTING ROWS REVERSES THIS RESULT.** Of the 999 in-window
rows, **365 are journalled REFUSALS** (`status='rejected'`/`'exchange_rejected'`) and **27 are
`adopted_orphan`** adoptions; only **607 are fills**. `mes_trend_long_1d` shows 2 trade rows and both
are adoptions.

⚠️ **And that is not hypothetical — it reverses MI-29's own sharpest case.** `mgc_trend_1h` shows
**51 trade rows** in this window. The joint distribution is `('rejected', 'mgc_trend_1h') × 50` and
`('closed', 'adopted_orphan') × 1`: **zero fills.** A session counting rows per leg would report
`PB-20260902-MGC-TREND-1H-IS-LIVE-AND-STRUCTURALLY-CANNOT-TRADE` as resolved. It is not.

**What HAS changed for that leg is its execution gate.** `mgc_trend_1h` now reads
**`execution: shadow`** in `config/strategies.yaml`, where MI-29 filed it as `execution: live`. It is
therefore no longer *"declared live and structurally cannot trade"* — a shadow leg placing no live
order is the gate working. `ict_scalp_mgc_15m` and `mgc_pullback_1d` remain `live` and both trade.
**This is the operator's own caution borne out** — *"some 'never traded' legs may now be trading"* —
though what moved here was the roster, not the leg.

---

## 6. The options, brought back as the decision asked

**Ranked, with what each rests on. None is taken.**

1. **[Tier-2] Add `breakout_1` to `ARBITRATION_FANOUT_ACCOUNTS`.** This is the lever aimed exactly at
   the measured condition: 88 starved gradings, one account, `not_allowlisted` on every one.
   ⚠️ **MI-282 argued against doing this and its objection is now DISCHARGED, which is the new
   information.** It held that arming `breakout_1` would be *"inert and harmful"* because
   `apply_rounds` was refused by its own reader, so the account would read as routed while staying
   starved. That projection defect is repaired and the repair is **observed dispatching** (§ 4), so
   the objection no longer applies. ⚠️ **It is still not a proven remedy:** the fan-out has dispatched
   only on `bybit_1` and only ever the global winner, and a prop "dispatch" is a Telegram ticket
   rather than an order, so the path this would newly exercise **has never been exercised**. Expect
   the next binding gate to be the prop sequence
   `OI-20260911-THE-PROP-ACCOUNT-IS-STARVED-NOT-QUIET-AND-THE-UNBLOCK-IS-A-SEQUENCE-NOT-A-FIX`
   names, not a trade.
2. **[Tier-3] Re-roster the prop legs so they are not entry-clones of `bybit_1` legs.** Three of the
   four starved candidates lose to their own non-prop twin (`trend_donchian_eth_prop` → `trend_donchian_eth`,
   `trend_donchian_sol_prop` → `trend_donchian_sol`, `eth_pullback_prop_2h` → `eth_pullback_2h`).
   The symbol contest exists because the roster creates it. `OI-20260911`'s clears_when already names
   this as a legitimate decision that must be **recorded** rather than left looking like an allowlist
   widening about to happen.
3. **[no change] Accept it.** The starved set is 4 legs, 2 of them `execution: live` and both prop,
   on an account whose own unblock is a sequence nobody has finished. Legitimate — and it must be
   **recorded as a decision**, because the failure mode this row exists inside is a measurement that
   gets taken and then sits.

**Explicitly NOT an option here:** dispositioning the nine silent legs. They are silent rather than
starved (§ 5), which is a different question with a different answer, and MI-29's scope is
measurement.

---

## 7. What this does **not** establish

- **Not a verdict on the nine silent legs.** Zero packages means nothing contested them; why they are
  silent is unmeasured here.
- **Not a measure of occupancy starvation.** MI-29's original axis — a sibling already *holding* the
  netted position, `mgc_trend_1h`'s 73.3% occupancy — is invisible to this instrument: the soak
  covers 7 symbols (`ADAUSDT`, `AVAXUSDT`, `BTCUSDT`, `ETHUSDT`, `GLD`, `SOLUSDT`, `XRPUSDT`) and
  `mgc_trend_1h` appears in **0** of 771 rows. The two mechanisms are disjoint and the fan-out
  addresses only the second.
- **Not a claim that the fan-out works.** It dispatches; it has never changed an outcome (§ 4).
- **Not a full-history reading of the journal.** Both tables are at or near their 1000-row caps, so
  the window is **cap-bounded, not data-bounded**.
- **Not a Tier-2 or Tier-3 action.** § 6 is options.

---

## 8. Filed, not fixed

- `BL-20260918-A-TRADES-ROW-IS-NOT-A-FILL-AND-THE-DIAG-JOURNAL-OFFERS-NO-WAY-TO-ASK-FOR-ONLY-FILLS-SO-A-PER-LEG-ROW-COUNT-REVERSES-THE-VERDICT` — § 5.
- `OI-20260912-FANOUT-APPLY-PATH-REPAIRED-AND-STILL-HAS-NEVER-DISPATCHED` is **cleared on both
  clauses** and its row updated with this evidence (§ 4.1), not deleted.

---

## 9. Reproduce

```bash
python3 scripts/research/mi29_starved_legs.py --selftest   # 26 checks, 10 negative controls
python3 scripts/research/mi29_starved_legs.py --run        # reads the live VM through the diag relay
```

It reads live fleet state, so a later run measures a later fleet. Artifact:
[`mi29-starved-legs-2026-09-18.json`](mi29-starved-legs-2026-09-18.json)
