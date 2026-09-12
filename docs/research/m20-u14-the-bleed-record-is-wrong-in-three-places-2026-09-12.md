# MI-278 U14 — the bleed record was wrong in three places, and the repo already held the right answer in a fourth

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U14 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Discharges** `BL-20260911-THE-BLEED-PREDATES-E35-BY-THREE-DAYS-AND-THE-BRIEFED-08-30-ONSET-IS-WRONG`, `BL-20260911-THE-PROFITABLE-FORTNIGHT-BASELINE-RESTS-ON-A-WEEK-WITH-ZERO-MEASURED-ROWS` (clause (b)) and `BL-20260912-MI-271-S-HEADLINE-DECOMPOSITION-TABLE-INCLUDES-THE-PAIRS-SLEEVE-EVERY-OTHER-SECTION-EXCLUDES`.
> **Tier-1.** Read-only over the repo, one `/api/diag/journal` pull and three relay requests. No `src/` write, no config, no order path, nothing enacted.

---

## 0. The answer, in seven sentences

The cycle's priority is to make the instruments trustworthy **before** acting on what they say, and this unit is the smallest version of that: three registers describing the same bleed disagreed with the journal and with each other, and every disagreement pushed in the direction that makes the leading suspect look guiltier. **The onset is 2026-08-27, not 2026-08-28** — 17 consecutive losing days to 2026-09-12, **−$38,851.81** — and the correction matters because it widens the gap to the e35 deploy (`2026-08-30T08:53:19Z`, `892c9a2c`) from two days to **three**. **The "+$17,951 profitable fortnight" baseline is withdrawn as ungradeable rather than restated**, because the pre-period is measured at **3.4%** broker-truth coverage against the streak's **20.4%**, and on broker-truth rows alone the pre-arm *loses* $4,178.35. **`bleed-attribution-2026-09-11.md` §4.3 computes its headline decomposition with the pairs sleeve INCLUDED — 59.9% of that population — while every other section of the memo excludes it**, and on the memo's own declared filter the win-rate fall is **19.0–21.2pp, not 4.9pp**, so its negative claim ("NOT a win-rate collapse") inverts while its positive claim (average win −71% to −74%) survives every cell. **The most useful finding is the one I nearly got wrong: `CLAUDE.md` was right all along** — its 16-days-from-08-27 row checks out against the journal to within one day of re-pricing — so this was not three registers disagreeing but two wrong ones agreeing with each other while the right answer sat in a third, unread. And the reason §4.3 drifted is structural rather than careless: **it is the one table in that memo its own reproducer does not compute.**

---

## 1. Population, stated first

| | |
|---|---|
| **Source** | `/api/diag/journal?table=trades&limit=1000`, pulled 2026-09-12T16:0xZ (`git_sha 855c7cc8d`) |
| **Span** | ids 4728–5727 · `closed_at` 2026-08-18 → 2026-09-12 |
| **Streak population** | `account_id=bybit_1` AND `status=closed` AND `NOT is_backtest` AND `pnl IS NOT NULL` → **462 rows**, bucketed by `closed_at` date |
| **§4.3 reproduction population** | the same, restricted to ids 4701–5700 → **441 of the memo's 442 rows (99.8%)** |
| **PnL provenance** | `src.runtime.provenance.classify_pnl`, imported not re-derived — measured 69 / estimated 338 / unverified 55 / **fabricated 0** |

⚠️ **The window is a 1000-row TAIL and it now reaches back only ~25 days.** Everything about the *pre-period* below is bounded by that; everything about the *streak* is not, for the reason in §2.

⚠️ **Positive control on the provenance probe, run before any provenance claim.** `classify_pnl` returns a `(bucket, why)` **tuple**; comparing it directly to `provenance.MEASURED` is always `False` and yields a clean, plausible, entirely fabricated table of zeroes. The probe was therefore asked first whether it can find a positive anywhere — it finds 69 of 462 — so a per-day zero is a reading and not a broken comparison. The journal's provenance keys live inside the row's `notes` JSON string, not at top level, which is the second way this silently returns nothing.

---

## 2. The onset is 2026-08-27, and it is bounded by data rather than by the window

| | value |
|---|---|
| **Run** | 2026-08-27 → 2026-09-12 |
| **Days** | **17**, every one negative |
| **Total** | **−$38,851.81** (313 rows) |
| **Day before** | 2026-08-26: **+$4,172.64** (19 rows) |

**That last row is what makes this a measurement rather than an artifact.** A trailing run read off a truncated tail is normally suspect precisely at its start — the window could be cutting it off. Here it is not: the immediately preceding day is a *winning* day inside the same window, so the start is bounded from below by data. (The pre-period **total** is a different quantity and *is* window-bounded — see §3.)

The register said **15 days from 2026-08-28, −$36,098**. The direction of the error is the dangerous one:

```
2026-08-27  onset (measured)        2026-08-30T08:53:19Z  e35 ships (892c9a2c)
     |------------ 3 days ------------|
2026-08-28  onset (as briefed)
     |-------- 2 days --------|
```

The live question this whole investigation exists to answer is whether the bleed **predates** the change that is its leading candidate. A briefed onset one day late shrinks the gap by a third, and a session re-deriving from it would read e35 as very nearly coincident with the break.

### 2.1 ⚠️ I nearly filed a false claim here, and the correction is the more useful finding

My first draft recorded a *third* disagreeing figure: `CLAUDE.md`'s `LOSING_STREAK_*` row says **"16 consecutive days (2026-08-27 → 2026-09-11, −$36,997)"**. Three registers, three numbers, all wrong — a tidy finding, and false.

Checked against the journal **over that row's own stated window**:

| | `CLAUDE.md` | journal, 2026-08-27..2026-09-11 |
|---|---|---|
| days | 16 | **16**, all losing |
| total | −$36,997 | **−$36,274.67** |

It is right. The 17th day is simply **2026-09-12 (−$2,577.15)**, which had not closed when that row was written, and the $722 residual is one day of reconciler re-pricing on prose written yesterday — `pnl` and `exit_price` are rewritten in place, so a figure quoted from a row is a figure as of a moment.

So the shape of the defect is not *"three registers disagree"*. It is:

- **`docs/claude/OPEN-ITEMS.json`** — wrong onset (08-28), corrected here.
- **`docs/claude/health-review-backlog.json`**, the twin row — same wrong onset, corrected here.
- **`CLAUDE.md`** — right, untouched, and **not the one to reconcile down**.

Two wrong registers agreeing with each other while the right answer sits unread in a third. Filed as `BL-20260912-TWO-REGISTERS-CARRIED-A-WRONG-BLEED-ONSET-WHILE-A-THIRD-CARRIED-THE-RIGHT-ONE-AND-NOTHING-COMPARES-THEM`, deliberately **without** proposing a coherence guard: a checker cannot know which of two figures is right, and one that merely demanded they match would have been satisfied by the two wrong ones.

---

## 3. The pre-period baseline is withdrawn as ungradeable, not restated

`BL-20260911-THE-PROFITABLE-FORTNIGHT-BASELINE-RESTS-ON-A-WEEK-WITH-ZERO-MEASURED-ROWS` offers two exits. **Say which one you took** — this took (b), and (a) is *established unreachable* rather than skipped.

### 3.1 Clause (a) cannot be run today by any available path

| path | offset? | result |
|---|---|---|
| `/api/diag/journal` | **no** — `table` and `limit` only, clamped to the newest 1000 | cannot reach past 2026-08-18 |
| `/api/bot/db/table/trades` | **yes**, and it *is* on the relay allowlist | **HTTP 401** through the relay (issue #12011) |

**Positive control, same relay run (issue #12012), because a bare 401 is exactly what a correct gate returns to an anonymous caller:**

| path | result |
|---|---|
| `/api/bot/db/tables` | 401 |
| `/api/bot/performance?window=7d` | **200**, full payload |
| `/api/diag/version` | **200** (`git_sha 855c7cc8d`) |

So the 401 is those two routes — not the relay, not the VM. `Depends(require_session)` landed on both `db_explorer` routes on 2026-09-09 (correctly: before it, an unauthenticated `GET /api/bot/db/table/trades` returned real rows over 41 columns against `total: 5589` on a public host), while `vm-diag-snapshot.yml` still allowlists them under an in-workflow comment reading *"These are unauthenticated GET reads server-side, so the Bearer header is simply ignored on them."* **Field beats comment, and here the comment is the load-bearing justification for the allowlist entry.** Filed as `BL-20260912-THE-RELAY-ALLOWLISTS-DB-TABLE-AND-DB-TABLES-UNDER-A-COMMENT-THAT-STOPPED-BEING-TRUE-ON-2026-09-09-SO-BOTH-401`.

### 3.2 Clause (b), recorded: the pre-period must not be quoted as a profitable control

Two independent reads, agreeing:

| read | pre-period coverage | streak coverage |
|---|---|---|
| day-level, all `bybit_1` closed rows | **5 of 149 = 3.4%** (9 days, +$16,970.84) | **64 of 313 = 20.4%** |
| the bleed memo's own reproducer, `accounts.bybit_1`, split at the deploy instant | **13 of 76 = 17.1%** | **51 of 107 = 47.7%** |

A **6×** asymmetry on the first basis, **2.8×** on the second. The before/after contrast the entire "something broke" framing rests on is comparing a near-entirely-reconstructed arm against a far better-measured one.

And on broker truth alone, from the reproducer's own output: **`pre.pnl_sum_measured_only` = −$4,178.35.** The "profitable" pre-period *loses money* on the rows that are actually measured.

⚠️ **That does not establish the pre-period was unprofitable, and this memo does not claim it.** Thirteen measured rows is not a verdict on seventy-six. It is exactly why the disposition is **ungradeable** rather than *"it was actually negative"* — the honest answer is that nobody can currently say either way, which is what the row asked for. Enacted rather than merely stated: `OI-20260911`'s summary now **withdraws** the figure instead of replacing it with $16,970.84.

⚠️ **Reopen if the relay gap closes.** Clause (a) is the better answer and becomes runnable the moment `/api/bot/db/table/*` is reachable.

---

## 4. §4.3 is computed on a different population from the rest of its own memo

`bleed-attribution-2026-09-11.md` §4.3 is headed *"`bybit_1`, all closed rows"* and reports n = 235 + 207 = **442**. Its §1 declares the decision population as pairs-**excluded**, and `scripts/research/bleed_attribution_2026_09_11.py::population` enforces that in code (`exit_reason NOT LIKE 'pairs_%'` OR a `pairs_*` `strategy_name`). **442 is the pairs-INCLUDED count.** It is not a rounding difference: the sleeve is **264 of the 441 reachable rows, 59.9%** — a majority read of the order path §4 had already exonerated.

**Three measurements, three pulls, two split conventions:**

| reading | source | split | pairs share | win-rate fall | avg win |
|---|---|---|---|---|---|
| −19.5pp | [`winner-size-collapse-2026-09-12.md`](winner-size-collapse-2026-09-12.md) (MI-279) | deploy instant | 266/446 | 53.2% → 33.7% | −72% |
| **−21.2pp** | the memo's **own reproducer**, re-run unmodified | deploy instant | — | 54.0% → 32.7% | **−74%** |
| −19.0pp | hand computation over the raw tail | date `2026-08-30` | 264/441 | 53.3% → 34.3% | −71% |
| *−3.3pp* | *hand, pairs INCLUDED (what §4.3 computes)* | *date* | *264/441* | *44.7% → 41.4%* | *−71%* |

**Independently corroborated by a session that was not looking for it.** MI-279 set out to *decompose* the winner-size fall, imported this memo's `population()` rather than restating it, and hit the same wall from the other side. Its −19.5pp and my −21.2pp come from different pulls hours apart; the spread is drift, not disagreement.

**What survives and what does not:**

- ✅ **Average win fell 71–74% on every population and every split tested.** The winner-size diagnosis, PROPOSAL A and everything in §5 of that memo stand unchanged. This is the load-bearing claim and it is now *more* strongly supported than before, having survived a filter change that inverted its neighbour.
- ❌ ***"It is NOT a win-rate collapse."*** A 19–21pp fall is a win-rate collapse. It arrives **alongside** the winner-size collapse rather than instead of it.
- ❌ ***"Average loss worsened 20%."*** On the declared population average loss **improves** 39%.
- ❌ ***"Entries still get their initial push."*** On the legs under investigation, they do not.

### 4.1 Why it drifted: §4.3 is the one table its own reproducer does not compute

The reproducer is a good one — population enforced in code, split instant as a constant, positive control, Fisher tests with a stated falsifier. **It computes no `avg_win` and no pre/post decomposition.** So the single table carrying the memo's headline conclusion was produced by hand, outside it, on a population the script would have refused.

The consequence is that **the original cells cannot be reproduced on any tested split**: `n = 235/207` is recovered only by `created_at < 2026-08-31` (which puts all of deploy day in PRE, contradicting §2.1's declared `2026-08-30T08:53:19Z`), while the totals are recovered only by the date split. No combination gives both. Three innocent causes suffice — 27 rows aged out of the tail, reconciler re-pricing, and the hand computation itself — **and that is the problem**: with no reproducer there is no way to tell an innocent cause from an error. Filed as `BL-20260912-THE-BLEED-MEMO-S-HEADLINE-TABLE-IS-THE-ONE-TABLE-ITS-REPRODUCER-DOES-NOT-COMPUTE`.

---

## 5. What this does and does not establish

**Establishes:** the onset date and its three-day gap to the e35 deploy; that the gap is bounded by data at its lower edge; that the pre-period baseline is ungradeable and why; that clause (a) is unreachable, with a positive control; that §4.3's population differs from its memo's; that the win-rate half of §4.3 inverts on the declared filter while the winner-size half survives; and that `CLAUDE.md` was correct.

**Does NOT establish — and none of this moved:**

- **The attribution.** The cause of the break remains unattributed. `OI-20260911`'s `clears_when` is untouched: it still wants per-leg stop-out rate and MFE-at-stop, e35 legs vs non-e35, before vs after. **This unit corrected the instrument's dials; it did not read them.**
- **That e35 is exonerated.** A three-day lead says e35 is not the *whole* story. It says nothing about whether e35 made an existing bleed worse from 08-30 on, which is a separate and unmeasured question.
- **That the pre-period was unprofitable.** See §3.2.
- **That the win-rate collapse has a cause.** It has a *size* now. Nothing here says why.
- **Anything about real money.** `bybit_1` is paper. `bybit_portfolio` (−$15,632) is the size-scaled forecast of what `bybit_2` would have lost, not what it did.

---

## 6. Rows

| row | backlog | disposition |
|---|---|---|
| `BL-20260911-THE-BLEED-PREDATES-E35-BY-THREE-DAYS-AND-THE-BRIEFED-08-30-ONSET-IS-WRONG` | performance | **resolved** — §2 |
| `BL-20260911-THE-PROFITABLE-FORTNIGHT-BASELINE-RESTS-ON-A-WEEK-WITH-ZERO-MEASURED-ROWS` | performance | **resolved on clause (b)** — §3 |
| `BL-20260912-MI-271-S-HEADLINE-DECOMPOSITION-TABLE-INCLUDES-THE-PAIRS-SLEEVE-EVERY-OTHER-SECTION-EXCLUDES` | performance | **resolved** — §4 |
| `BL-20260911-A-DATED-REGIME-BREAK-ON-2026-08-30-COLLAPSED-THE-DIRECTIONAL-LEGS-WIN-RATE-AND-NOBODY-NOTICED-FOR-TWO-WEEKS` | health | **kept open**, onset corrected — its subject is that nothing noticed, which a date fix does not touch |
| `BL-20260912-THE-RELAY-ALLOWLISTS-DB-TABLE-AND-DB-TABLES-UNDER-A-COMMENT-THAT-STOPPED-BEING-TRUE-ON-2026-09-09-SO-BOTH-401` | health | **filed** — §3.1 |
| `BL-20260912-TWO-REGISTERS-CARRIED-A-WRONG-BLEED-ONSET-WHILE-A-THIRD-CARRIED-THE-RIGHT-ONE-AND-NOTHING-COMPARES-THEM` | health | **filed** — §2.1 |
| `BL-20260912-THE-BLEED-MEMO-S-HEADLINE-TABLE-IS-THE-ONE-TABLE-ITS-REPRODUCER-DOES-NOT-COMPUTE` | performance | **filed** — §4.1 |
