# Full-System Audit + System Review — 2026-08-23

> Program doc per `.claude/skills/full-system-audit/SKILL.md`.
> Session branch `claude/system-audit-infrastructure-c6hhqp`. Deployed SHA at
> audit time: **`72b254f9`** (= repo HEAD; the trader is current).
>
> **Operator mandate, in substance:** a full audit of everything, with extra
> emphasis on (a) the **M20 exit mechanisms being correctly wired to their
> strategies** and (b) the **backtesting infrastructure**, *"so that we can know
> that what we're building is reliable"*; plus a **performance review weighted
> toward prop** (demote what shouldn't be there, promote what's ready); plus a
> review of everything shipped recently; then **a new work plan**.
> Mid-session addition: **run the system review too**, and make it a permanent
> part of the audit skill.

---

## Phase 0 — Instruments (what I will and will not accept as evidence)

| instrument | state |
|---|---|
| clone depth | shallow on arrival (50 commits) → `git fetch --unshallow` → **3,568 commits**, all three repos |
| **direct VM egress** | ✅ **WORKS at Trusted** — `https://ict-bot.duckdns.org` answers `200` on `/api/health`, bearer'd `/api/diag/*`, and the whole unauthenticated Tier-1 API. **This audit was NOT relay-bound** (2026-08-20's was), so every number below is a live read, not an issue-relay round trip |
| `DIAG_BASE_URL` env | still `http://158.178.210.252:8001` — the **micro terminated 2026-06-16**. `diag_fetch.sh`'s candidate-ordering heals it (`served by https://ict-bot.duckdns.org`). `BL-20260818` stands, unfixed at the env layer |
| guard self-test coverage | **20 of 45** guard scripts (44.4%) have a failure-path self-test — up from 10/41 (24%) at the last audit. The other 25 remain unproven instruments |
| `check_selftest_wiring.py` | ⚠️ reports `POPULATION: 11 self-test(s) registered; 11 covered` → **100% over a denominator that is its own numerator**. It verifies that registered self-tests are *wired*, not that guards *have* them. See F-11 |
| `system_invariants.py` | self-test 28/28 with planted controls — admissible |
| `exit_mechanism_coverage.py` | self-test 6/6 — admissible |
| `/api/diag/journalctl?since=` | ⚠️ **`lines` truncates from the HEAD of the window, not the tail.** A `since=2026-08-22T00:00Z&lines=4000` request returned only 08-22 00:00–00:07. Any claim of the form "N hits since X" made through this route is scoped to the first `lines` entries, not the window. Corrected for below by narrow windows |

---

## Part 1 — M20: are the exit mechanisms correctly wired? (operator priority #1)

**Short answer: the wiring is sound; the *labelling* of what exits do is not, and
that is the input to M20's own research.**

### F-1 · ✅ VERIFIED SOUND — no orphaned lever declares

`scripts/ops/exit_mechanism_coverage.py` over **46 of 47 live legs**: **zero**
orphaned declares (a YAML key no unit module reads). The `htf_pullback_trend_2h`
family gap closed 2026-08-18 (`stale_stop` + `giveback_stop` extracted to
`src/runtime/exit_levers.py`); it now runs 3 of 4 mechanisms. `exit_head` is
genuinely absent there and deliberately so — no advisory head exists for that
family.

⚠️ **One leg is UNGRADED: `ict_scalp_5m` resolves to `no_builder_found`.** The
orphan verdict ranges over 46 of 47 legs, not all of them. `ict_scalp_5m` is a
**real-money** leg (8 trades / 30d, +$8.11) — the one leg the detector cannot
see is not a shadow leg.

### F-2 · M20's live production footprint is 3 legs of 47

| lever | LIVE | DECL (declared, reachability ungraded) | INERT (cannot fire) | not implemented |
|---|---|---|---|---|
| `stale_stop` | **1** | 2 | 0 | 1 |
| `giveback_stop` | **1** | 0 | 0 | 8 |
| `exit_head` | **1** | 2 | 0 | 26 |
| `trail_decay` | **2** | 9 | **2** | 8 |

Three distinct legs carry any LIVE M20 mechanism (`trend_donchian`,
`trend_donchian_xrp_4h`, `uso_trend_1h`). **This is not a defect** — most cells
are honest negatives — but it is the true denominator when reading "M20 is
85.3% resolved": the milestone's *coverage* is nearly done; its *production
footprint* is three legs.

**And the levers do fire.** Lifetime closed non-backtest exits carry
`stale_stop` ×10, `exit_head` ×4, `time_decay` ×4, `giveback_stop` ×3 — 14 of
them in the last 30 days. M20 is live, not shelved.

### F-3 · 🔴 **MONEY-ADJACENT — the exit *label* is wrong on ~52% of gradeable closes, and it feeds M20's own research**

**Population: 578 `reconciler_filled` closed non-backtest trades (all time).**

| measurement | value |
|---|---|
| `reconciler_filled` rows that **never reached the classifier** (no `exit_reason_source`) | **560 / 578 = 96.9%** |
| of those, gradeable (have `stop_loss` + `take_profit_1` + `exit_price`) | 560 |
| **whose exit price actually reached a declared bracket level** — i.e. mislabelled | **294 / 560 = 52.5%** |
| real-money rows among them | **171** |

This independently reproduces the roadmap's own 91/155 = 58.7% figure on a
wider population.

**Item 1.8 (#10151, merged 2026-08-22T18:53Z, deployed) fixed ONE path.** It
re-derives the label inside `_sweep_pending_pnl_from_bybit` — the moment Bybit
broker truth arrives. **Its sibling `_sweep_local_pnl_for_unpriced` does not
reclassify at all**: it writes `exit_price_source` (`candle_at_close`,
`local_markprice`) and never touches `exit_reason`, never calls
`_classify_broker_exit`. Verified by reading the function body (lines
8690–8990): the only `exit_reason`-adjacent writes are `pnl_source` and
`exit_price_source`.

That path is **212 of the 560 rows** (`candle_at_close` 110 + `local_markprice`
102), and it is **forward-live**, not historical: of the 5 `reconciler_filled`
rows closed after the deploy, **4 were priced `candle_at_close` and carry no
label**. (n=5 is thin — the *code-path* proof is the strong evidence; the
post-deploy sample is corroboration.)

⚠️ **TWO CORRECTIONS TO MY OWN NUMBERS ABOVE, both material.**

1. **The relabellable population is 191, not 294.** The 294/560 figure applied
   neither of the guards the live classifier applies. Re-measured with them:
   **497 eligible** rows (578 generic, minus 81 reduce legs — 14% of the
   population, and a reduce's bracket can be *inverted*, so classifying one
   mislabels it), of which **191 (38.4%) resolve to a real bracket level** —
   156 off a MEASURED price, 35 off an ESTIMATED one.
2. **105 of them must NOT be relabelled at all.** Their price is FABRICATED
   (`local_markprice` ×88, `netted_duplicate_unattributed` ×17) — the market
   read at *sweep* time, hours after the exit. Comparing that to the bracket
   does not recover a lost label, it manufactures one out of unrelated later
   price action. My original framing would have written 25 `sl` and 4 `tp`
   labels out of noise.

**Disposition: FIXED + backfill shipped this session** (operator-directed
Tier-2). Forward fix in `_sweep_local_pnl_for_unpriced`, gated on the price
basis; `scripts/ops/backfill_exit_labels.py` (dry-run default, `--apply`,
12/12 planted-control self-test) for history. `price_vs_pkg_bracket_est_price`
and `refused_unmeasured_price` registered in the provenance vocabulary — the
refusal deliberately in **no** source set, because a refusal is not a grade of a
value, it is the statement that no value was produced.

### F-4 · 🟠 **Two live IB positions are target-naked — KNOWN, with TWO operator-approved actions SCHEDULED for tonight**

Measured continuously across today's sweeps (02:02Z, 04:02Z, and the 00:05Z
partial):

| position | size | resting stop | resting target | declared TP |
|---|---|---|---|---|
| `ib_paper` / **MES** | 15 | 15.0 | **0.0** | 8390.59025 |
| `ib_paper` / **MGC** | 95 | 95.0 | **0.0** | 4393.02071429 |

⚠️ **CORRECTION, TWICE — and the second one matters more than the first.**

My first pass called this a live recurrence needing action. My second pass, on
reading #10089's *"the take-profit attach was DECLINED"*, called it a settled
non-action. **Both were wrong**, and the operator corrected me: *"This is not a
non-action decision, and that needs to be clear."*

The actual state, verified against the scheduler and against price history:

| position | decision | why |
|---|---|---|
| **MGC** 95 long | **CLOSE IT OUT** | its declared TP **4393.02 is 233.88 pts BELOW market 4626.9** — the trade blew through its take-profit and is still open. Attaching a target now would be a *marketable* limit that instantly flattens 95 contracts at a worse price than the market. The refusal to attach is not "leave it alone", it is "close it instead". |
| **MES** 15 long | **ATTACH THE TARGET** | declared TP **8390.59 sits ~703 pts ABOVE** market 7687.5 — a genuine resting limit. This is the one that **needs its brackets**. |

Confirmed from price history since each trade opened (1d bars): **MGC's TP WAS
reached** (high 4654.4 vs TP 4393.02); **MES's was not** (high 7838.5 vs
8390.59), and neither breached its declared stop. So exactly one of the two
passed the level it was supposed to exit at, and it is MGC.

**Both are already scheduled.** Routine `trig_014S3NAzMKy2Ac2AM2GgyRE5` — *"MGC
flatten + MES target attach — Sunday reopen"* — is **enabled and fires
2026-08-23T22:30Z**, deliberately deferred to the CME/COMEX Sunday reopen
(18:00 ET / 22:00Z) because the venue was shut and the IB session gate would
have deferred the close anyway.

⚠️ **Three risks that routine carries, worth watching tonight:**

1. **It may fire without MCP tools.** Its own step 0 says so and tells the
   session to STOP rather than improvise — specifically not to `curl
   api.github.com`, which returns a Claude-specific 403 that the usual
   `|| echo '{}'` idiom launders into a clean-looking empty result. If it stops,
   **neither action happens** and it needs re-running by hand.
2. **The MES attach must be re-priced at fire time.** If market runs above
   8390.59 over the reopen, the attach becomes marketable and instantly flattens
   15 contracts. As of this audit, market 7687.5 — safe by ~703 pts.
3. **It is the owed positive control for
   `BL-20260822-ATTACH-IB-TARGET-USES-TRADER-CLIENTID`.** That fix (PR #10140)
   has never run against a live trader; the dry run *cannot* exercise it because
   it never builds a client. An `Error 326` tonight means the fix did not work.

**The alerting is confirmed working end-to-end**: `_emit_target_naked_alert`
fires CRITICAL, and both are on `/api/bot/notifications` right now
(`ib_target_naked · detected`, since 05:08:38Z) → Telegram + both apps' banners.
Not-re-arming *automatically* is by design (`attach-ib-target` is the sanctioned
repair, and a blind re-arm would invent decision-time geometry) — but "the sweep
does not re-arm" is a statement about the sweep, **not** a disposition of the
position, and reading it as one is the mistake this row records.

**What IS new, and is not in #10089:**

1. **MGC is simultaneously monitor-blind.** A live `monitor_blindness` banner —
   `ict_scalp_mgc_15m`, `candles_unavailable` for 3 consecutive ticks. So MGC
   currently has **no resting target AND no monitor-driven exit**; only the
   broker stop stands. #10089 grades resting protection and cannot see this;
   the two surfaces have to be read together for the compound state to appear,
   and nothing joins them.
2. **MES `stop_price_diverges` by 69 ticks** — declares 7533.696429, nearest
   resting stop 7516.500000 (17.196429 away). Captured by #10089 and covered by
   Tier-3 draft #10081; noted here because it compounds with the missing target
   on the same position.

**F-4a (FIXED this session).** The sweep's own summary line printed
`covered=3 naked=0 partially_naked=0 rearmed=0` on the very sweeps that logged
two TARGET-NAKED errors — `summary["target_naked"]` was counted and **omitted
from the format string** while seven other counters were printed. A reader
grepping the summary (which is what a health review does) sees a clean book.
Patched to print `target_naked=%d`.

**F-4b.** The Bybit sibling `_check_broker_naked_bybit_positions` has **no
per-sweep summary line at all** — only per-event lines. The IB comment's own
reasoning (*"the ONLY evidence the sweep runs is a re-arm — visible exactly when
something is wrong and invisible when it is working, which is the wrong way
round"*) was never applied to Bybit. Filed, not fixed (WP-6).

### F-5 · 🔴 **INDEPENDENCE — the surface built to make IB protection falsifiable fails INTERMITTENTLY, and says nothing when it does**

`/api/diag/ib_open_orders` was created 2026-08-16 precisely because
`protection_coverage` was a *reduced verdict* nobody could contradict.

⚠️ **CORRECTION — my first read of this was "the surface is blind." It is worse
in a more useful way: it is FLAKY.** At **2026-08-23T02:11:42Z** the same
endpoint returned `ib_paper` **`reconciled`** with 4 resting legs and 3 graded
findings (#10089). At **05:34–05:35Z**, three consecutive calls returned:

```
read_state: "could_not_look"   orders: null   count: null   error: null
```

**`/api/diag/exchange_positions` returned `null` for `ib_paper` in the same
window**, while `/api/diag/ib_state` reported every IB client `state:
connected`, `likely_wedged: false`, `account_data_ready: true`. So the failure
is per-read and transient, not a configuration or outage state — which is
precisely why it is dangerous: an intermittent blind spot on a protection
surface looks identical to a quiet one.

**Root cause (from the web-api journal at the exact second of my probes):** the
web-api's read-only client opens, and `reqAllOpenOrders` returns openOrder
messages whose contract fields the installed `ib_insync` cannot decode —
`Error handling fields: ['10','5','MHG','FUT',…] → KeyError: 5`, then `8` (MGC),
then `11` (MES) — which kills the message handler and drops the socket
(`Error 1100 … lost`, `ConnectionError: Socket disconnect`,
`liveness probe error`). **The trader process is unaffected** (zero KeyErrors in
its journal; its own sweep reads coverage fine) — this is web-api-process-local.

**Consequences, both real:**
1. **4 of 7 `system_invariants.py` checks returned `not_measured`** on this audit's run —
   `INV-PROTECT-STOP`, `INV-PROTECT-OVERCOVER`, `INV-PROTECT-TARGET`, and
   `INV-EXIT-INTERVAL` — on the only account holding IB positions. The three
   protection invariants could not look at the exact class that produced
   MGC 4487.
2. **`error: null`.** The route declares an `error` field and this path never
   populates it, because `account_ib_open_orders` returns `None` from a branch
   that logs nothing. `could_not_look` cannot say *why* it could not look — the
   collapsed-state defect one level down from the one this route was built to fix.

### F-6 · Live invariant results (what *was* measurable)

`system_invariants.py --payloads <live>`: **0 FAIL, 3 pass, 4 not-measured.**
`INV-JOURNAL-EXCHANGE` **pass** over 23 (account, symbol) pairs observed on both
sides — journal reconciles to exchange. `INV-NETTED-DUP-UPNL` pass (3 netted
groups). `INV-BLIND-COUNT-NULL` pass (11 read_states).

### F-7 · `bybit_1` / SOLUSDT SL legs at **740%** of position

`position size=108.6, resting SL legs total 803.5`. This is the known
detect-only `over_covered` signal (`BL-20260730-…-LEG-OVERACCUM-WORSENING`),
now at 7.4×. Per the repo's own invariant doc, over-coverage is a hazard in its
own right — disjoint stop groups over one long mean either fill flattens and the
survivor sells into a naked short. **Nothing remediates it**; detection only.

---

## Part 2 — Backtesting infrastructure (operator priority #1)

**Short answer: it is more honest than it was, and it still cannot answer the
question ~48% of M20's own matrix asks of it.**

### F-8 · The risk-basis guard is green because the divergence is *registered*, not fixed

`scripts/ci/check_risk_basis_agreement.py` (new since the last audit — good)
reports `11 risk default(s) checked … clean`. Reading it: `KNOWN_DIVERGENCES`
registers **10 of 12** harnesses as running at **0.2×–0.67× of live risk**
(`backtest_system.py`, `build_backtest_panel.py`,
`allocator_multisymbol_backtest.py`, `walkforward_flip_policy.py`,
`evaluate_prop.py`, `record_harness_trades.py`, `backtest_augment_runner.py`,
`account_compat_matrix.py`, `validate_alt_prop.py`, `montecarlo_prop.py`).

"Clean" here means *"every disagreeing harness is on the debt list"*, **not**
*"the harnesses agree with live"*. That is a legitimate design (the per-file
`FILE_UNITS` map is exactly right — the number cannot tell you fraction from
percent), but the debt is real and unpaid: **the fleet default is 0.3% against a
live basis of 1.5%.**

### F-9 · 🔴 **The harness cannot model refusal — and that is load-bearing for 48% of M20's matrix**

`scripts/backtest_system.py:1704` states it plainly: *"`_risk_qty` returns a
CONTINUOUS quantity: no whole-contract floor, no `min_qty`, no margin cap.
Production quantizes and REFUSES sub-1-contract futures orders outright and
floors Alpaca to whole shares… The error is FLATTERING."* Filed as
`BL-20260820-HARNESS-DOES-NOT-MODEL-QUANTIZATION-REFUSAL`. **The repo is honest
about this; what was missing is the denominator.**

Measured against `docs/research/exit-refinement-coverage.json` (n=52 rows):

| venue class | rows | quantization behaviour |
|---|---|---|
| crypto (Bybit) | 27 | fine granularity — largely unaffected |
| **equity/ETF (Alpaca)** | **20** | floors to whole shares |
| **futures (IBKR)** | **5** | whole contracts; **sub-1 REFUSED outright** |

**25 of 52 M20 coverage rows (48.1%) are on venues where production quantizes or
refuses, evaluated by a harness that models neither, at 0.2× the live risk that
determines whether the floor binds.**

This is the assumption the audit skill names as hiding longest: an R-normalized
harness asserts both that PnL is linear in risk *and that the trade set is
invariant to it*. The second is false wherever production refuses. At 1/5 live
risk the harness's positions are 5× smaller — squarely toward the floor — and it
books trades production would decline. **It fails in the flattering direction.**

### F-10 · 🟠 **6 live exit levers are running on a justification that has lapsed**

`shipped_gate_failed` in the coverage matrix means, per its own legend: *"LIVE in
config, but a LATER re-sweep failed its gate and the operator chose to HOLD."*
Current occupants:

| leg | lever | why the re-sweep failed |
|---|---|---|
| `trend_donchian` / BTCUSDT 1h | **`exit_head_ml`** | live-parity re-sweep 2026-08-14: auc **0.5403** (below the 0.55 bar), fails **2 of 3** gate conditions |
| `trend_donchian_sol` / SOLUSDT 1h | `exit_head_ml` | auc 0.6161 passes; fails **both** fold gates (14/23, 12/23) |
| `eth_pullback_2h` / ETHUSDT 2h | `trail_decay` | all cells fail (best 3/6 wf) |
| `xrp_pullback_2h` / XRPUSDT 2h | `trail_decay` | 3/6 |
| `avax_pullback_2h` / AVAXUSDT 2h | `trail_decay` | 2/6 |
| `trend_donchian_sol_4h` / SOLUSDT 4h | `trail_decay` | all cells is_oos_fail |
| `qqq_trend_long_1d` / QQQ 1d | `trail_decay` | fails |

Note the first row: **the single leg running a LIVE ML exit head is running one
whose re-sweep failed its own gate.** The matrix is working exactly as designed
(it refused to absorb these into `honest_negative` or `shipped`) — but a standing
HOLD with no review date is how a lapsed justification becomes permanent. These
need dispositions (WP-3).

### F-11 · The self-test wiring checker reports over its own numerator

See Phase 0. `check_selftest_wiring.py` answers *"is every registered self-test
wired?"* and prints `11/11 … All registered self-tests resolve`. The question a
reader takes from it is *"are the guards self-tested?"*, whose answer is
**20/45**. Sub-class C (unasserted denominator) in the repo's own
diagnostic-provenance taxonomy, in the instrument that audits instruments.

---

## Part 3 — SYSTEM REVIEW (Phase 3.10 — new, per operator directive)

### Health

| | |
|---|---|
| deployed SHA | `72b254f9` = HEAD ✅ |
| trainer mirror | fresh (**31 s**), `ict-trainer.timer` **active**, next 2026-08-24T00:00Z; 3 cycles/24h |
| last training cycle | 2026-08-23T05:25Z, `complete_with_refusals`, rc=0, trained 0 / already_done 76 / **refusals 8** |
| exit loop | `state: fresh` |
| open banners | **6** — 2 × `ib_target_naked` (alert), 2 × orphan-reconciliation (warning), 1 × `monitor_blindness` MGC, 1 × transient MGC market data |

**Flags raised loudly:** F-4 (2 target-naked live positions + MGC monitor-blind),
F-5 (IB read surface blind), F-7 (740% SL over-coverage).

### Performance (measured populations, stated)

| window | real-money trades | winRate | totalPnl | **totalPnlMeasured** | pnlCoverage | profitFactor | expectancyR |
|---|---|---|---|---|---|---|---|
| 7d | 13 | 76.9% | +45.03 | **+45.03** | 0.69 | 6.87 | +2.52 |
| 30d | 27 | 48.1% | +35.58 | **+35.96** | 0.74 | 2.24 | +2.16 |
| all | 401 | 28.2% | −22.34 | **−28.82** | 0.78 | 0.88 | −0.20 |

Real money is **lifetime-negative but recently positive**; the book is small
(401 trades, ~$22 net). Paper aggregates are **not quotable**: lifetime
`pnlCoverage` **0.35**, i.e. two thirds of the paper PnL is manufactured. The
`paperPortfolio` (live-mirror) block is the usable one — 51 trades, coverage
0.55, +$1,518 measured +$4,298.

Real-money per-strategy, 30d (n is small on every leg — none clears the repo's
own `MIN_OOS_TRADES = 25` denominator floor):

| strategy | n | win | measured PnL | expR |
|---|---|---|---|---|
| `trend_donchian_eth_4h` | 5 | 60% | **+21.30** | +3.92 |
| `trend_donchian` | 4 | 50% | +12.82 | −0.76 |
| `ict_scalp_5m` | 8 | 62.5% | +8.11 | +5.52 |
| `xrp_pullback_2h` | 3 | 33% | +3.24 | −0.31 |
| `trend_donchian_xrp_4h` | 2 | 0% | −1.85 | −0.23 |
| **`eth_pullback_2h`** | 5 | 40% | **−7.66** | −0.18 |

### PROP — the operator's emphasis

**Account `breakout_1`** ($5k Breakout; static-DD floor $4,700, daily-loss limit
$149.49):

- balance **$4,982.86**, cushion to floor **$282.86**, to daily loss **$149.49**
- ⚠️ **`status_freshness: "stale"` — the snapshot is 58.7 hours old** against a
  24 h threshold. The freshness machinery (built 2026-08-14) is working
  *correctly*: it reports `stale` and travels the caveat inside `rule_distance`.
  The **outcome** is that the prop safety cushion has been computed from a
  2.4-day-old balance for two days. `unacted_count: 0`, so reconciliation is clean.

**Completed prop trades by strategy** (n=13 closed fills, lifetime):

| strategy | n | PnL | win | still routed? |
|---|---|---|---|---|
| `trend_donchian_eth` | 5 | **+257.03** | 40% | yes |
| `trend_donchian_sol_prop` | 1 | +242.02 | 100% | yes |
| *(unattributed to a ticket)* | 1 | −6.05 | 0% | — |
| `trend_donchian_eth_prop` | 1 | −50.55 | 0% | yes |
| `trend_donchian_sol` | 1 | −73.23 | 0% | yes |
| **`eth_pullback_2h`** | 4 | **−333.26** | **0%** | **NO — last ticket 2026-07-18** |

**Net +$35.96 across 13 trades, 23.1% win rate — and it is two winners deep.**
Remove +$299.66 (ETH 07-04) and +$242.02 (SOL 08-19) and the remaining 11 trades
are **−$505.72**.

**The demotion question has a smaller answer than it looks.** The single worst
prop leg, `eth_pullback_2h` (0/4, −$333.26, 61% of its tickets suppressed), is
**already off the prop roster** — `breakout_1.strategies` declares
`eth_pullback_prop_2h`, and `eth_pullback_2h`'s last prop ticket was 2026-07-18.
Its −$333 is historical. *(It is separately the worst real-money leg at −$7.66 /
expR −0.18 — a consistent two-population signal, but a Tier-3 call on n=5+4.)*

**The real prop finding is the bridge, not the strategies.**

| strategy | tickets | closed | closure |
|---|---|---|---|
| `eth_pullback_prop_2h` | 4 | **0** | **0.0%** |
| `trend_donchian_eth_prop` | 12 | 1 | 8.3% |
| `trend_donchian_sol_prop` | 8 | 1 | 12.5% |
| `trend_donchian_eth` | 13 | 5 | 38.5% |
| **overall** | **72** | **12** | **16.7%** |

**83% of prop tickets never become a completed round trip** (25 `suppressed`,
17 `emitted`-never-acted, 6 `expired`, 6 `invalidated_prompted`, 5 `skipped`).
The purpose-built `*_prop` legs have the **worst** closure rates.

Since the current roster took effect (2026-08-13), prop has produced **two**
completed trades. **There is no evidential basis to promote or demote any prop
leg right now, and the blocker is ticket attrition, not strategy quality.**
Fixing attrition is the prerequisite to ever making a prop routing decision on
evidence (WP-2).

All four active prop legs are alive and evaluating (`*_eval` rows present since
08-16 via `/api/diag/audit_query`) — quiet, **not** dead. And the mandatory
per-account compatibility evidence (`scripts/prop/account_compat_matrix.py`) is
**stale**: the only committed artifact is `2026-06-17`, SOLUSDT/`trend_donchian`
only — 4 of the 5 declared legs have none.

*(Data hygiene: 3 production `prop_tickets` rows carry the strategy name
`trend_donchian_sol [TEST PING — ignore, not a real signal]`.)*

### ML lifecycle

95 registry rows — **3 LIVE/advisory** (`btc-regime-15m-lgbm-fc-pcv-v2`,
`mes-regime-5m-lgbm-v2`, `sol-regime-15m-lgbm-fc-pcv-v2`), 28 SHADOW, 64
OFFLINE. All three advisory heads retrained today. `linked_strategies: []` on
all three is **correct, not a gap** — advisory regime heads wire per-SYMBOL via
`ml_vol_regime_for_symbol`, not via `shadow_model_ids`.

Open: **8 refusals** in the last cycle across 5 manifests
(`baseline-prop-mission-policy`, `btc-regime-15m-lgbm-base-vt003-pcv-v1`,
`mes-regime-1d-lgbm-v2`, `setup-candidates-metalabel-xsym-yz-v1`,
`setup-quality-lgbm-v2`); staleness summary **7 stale, 2 never_trained** of 76
scanned.

⚠️ `stage` is `null` on **all 95** registry rows (bucket is derived from
`stage_history`/`target_deployment_stage`). Any consumer reading the flat
`stage` field renders "—" for the entire fleet.

### Backlog drive

| backlog | items | open | open `critical` | open `high` |
|---|---|---|---|---|
| health | **798** (was 708 on 08-20) | **341** (was 261) | **9** | **96** (was 59) |
| performance | 106 | 40 | — | 5 |
| ml | 104 | 23 | — | — |

✅ **Status vocabulary normalized: 34 distinct → 6.** The schema work landed.
🔴 **+90 items and +80 open in three days**, and open `high` rose 59 → 96.
🔴 **`detector` is present on 0 of 404 open items across all three backlogs.**
The finding schema mandates it and the RECURRENCE axis depends on it; adoption
is zero. This is the anti-treadmill mechanism not being wired to the durable
record.

### `review_coverage`

`strategy_promotion` ✅ · `ml_training_health` ✅ · `soak_status` ✅ (exit-loop
fresh; exposure/pairs/allocator soaks accruing) · `flags_raised` ✅ (F-4, F-5,
F-7) · `backlog_drive` ✅ (measured + F-14 filed).

---

## Part 4 — Pipeline verification (traces)

8 stratified traces over 7 hops (`signal/pkg` → `sizing` → `journal row` →
`protective legs` → `exit mechanism` → `close+pnl provenance` → `analytics`),
across paper/real-money × open/closed:

| death hop | traces |
|---|---|
| reached end | **6** |
| **hop 10 — close+pnl provenance** | **2** |

Both deaths are the same mechanism as F-3: closed with an anchored/estimated
price rather than broker truth. No trace died at sizing, placement, journal, or
protection.

---

## Part 4c — M20 REASSESSMENT: are the lever verdicts broken by the label bug?

*(Operator question, verbatim in substance: "reassess everything that we've done
in M20 so that we know that things aren't broken because of that label. And if
yes, then we need to make a plan for doing those things again.")*

### The answer is NO — and it is a structural answer, not a hopeful one

**M20's sweep verdicts do not read the live journal's `exit_reason` at all.**
I traced every consumer of the field rather than reasoning from the call graph:

| consumer | reads | verdict-bearing? | exposure |
|---|---|---|---|
| `m20_exit_analysis.py` — **the P1 evidence read** | live journal | **no** — `exit_reason` appears only as a per-trade *display* column (`"exit": t["exit_reason"][:24]`). Every metric that drives a lever choice (`real_r`, `mfe`, `mae`, `chop_frac`, `hold_h`) is derived from prices and candles | ⚠️ **narrative only** |
| `build_exit_head_dataset.py` — **exit-head training** | harness emits | **no** — the live branch sets `"exit_reason": None` outright (line 342); only harness rows carry one, and labels are *"pure truncation observables"* from bars (`peak_is_in`, `holding_pays`, `P_win`) | ✅ **none** |
| `strategy_review_packet.py` — **the M7 gate** | live journal | **no** — `t.exit_reason AS trade_exit_reason` appears exactly once, in the SELECT, and is never referenced again | ✅ **none** |
| `e35_barrier_race.py` | harness | **no** — its own fixtures use `take_profit`/`stop`/`trail_stop`/`timeout`, the *harness* vocabulary, not the journal's `sl`/`tp`/`reconciler_filled` | ✅ **none** |
| `peak_banking_basis.py` | harness | reads it as authoritative, but `_TP_EXITS = {"take_profit"}` — harness vocabulary again | ✅ **none** |
| `exit_census.py` | harness spellings | its whole output IS a label distribution | ✅ **none** (harness-oriented) |
| `tp_recovery_counterfactual.py` | live journal | **yes** — buckets `delta` by `exit_reason` | 🔴 **its per-mechanism breakdown is wrong** |

**Why this holds structurally:** the harnesses are a closed loop. A backtest
writes its own `exit_reason` in its own vocabulary; the live reconciler never
touches a harness row. The two label spaces do not even share spellings, which
is what makes the separation checkable rather than assumed.

### So: no M20 work needs redoing

**Nothing in the coverage matrix is invalidated by this bug** — not the 20
`shipped` cells, not the 15 `passed_unshipped`, not the honest negatives, and
not the 7 `shipped_gate_failed`. The gate arithmetic (`net_R`, `maxDD`,
`net_r_per_capital_day`, the walk-forward) never saw the field.

**Two things are genuinely affected, and neither is a verdict:**

1. **`m20_exit_analysis.py`'s per-trade diagnostic dump shows wrong exit
   labels.** The numbers beside them are right. The risk is *human*: this dump
   is what a session reads to decide "which failure mode does this family
   have", and 123 stop-outs currently render as "the reconciler closed it".
   That is a real hazard for lever *selection* judgement, and it is now fixed
   going forward and backfillable for history.
2. **`tp_recovery_counterfactual.py`'s per-mechanism split** should be re-run
   after the backfill applies.

### What I am NOT claiming

I did not re-run any sweep. The claim is narrower and stronger than "the
verdicts reproduce": it is that **the verdicts are not a function of the
corrupted field**, established by reading every consumer. Re-running to confirm
would be theatre — the same inputs produce the same outputs, and the input in
question was never read.

⚠️ **The M20 evidence has a real reliability problem, and it is a different
one** — F-9's quantization/refusal gap, which touches **25 of 52 coverage rows
(48.1%)**. That one is not resolved by this fix and is the thing actually worth
worrying about before trusting a futures or equity cell.

---

## Part 4b — OUTCOME axis: did recent work deliver? (54 commits since 2026-08-20)

Three ships with concrete, checkable promises, graded against measurement:

| ship | promise | grade | evidence |
|---|---|---|---|
| **#10067** — *"retire the `order_packages.id` class"* | sweep the class the 2026-08-20 audit found in 20 test fixtures | ✅ **delivered** | **0** `CREATE TABLE … order_packages` blocks in `tests/` declare `id INTEGER PRIMARY KEY`. Production PK is `order_package_id TEXT`. The class-sweep actually happened, which is the failure mode this repo most often repeats |
| **#10061** — *"give a refused manifest a state between trained and failed"* | a third state, visible | ✅ **delivered** | live `/api/bot/ml/status`: `refusals_total: 8`, `refusing_manifests_24h` naming 5 manifests, `outcome: complete_with_refusals`, `overall_rc: 0`. Neither trained nor failed, and readable |
| **#10076 + #10086** — the declared-vs-resting bracket detector + a scheduled caller *"that cannot become a desensitized alarm"* | catch the price axis nothing checked | ✅ **delivered, with one caveat** | Issue #10089 at 02:11:42Z: 3 real findings on `ib_paper`, including a **69-tick `stop_price_diverges`** on MES that no other surface reports. Design is excellent — a tracking issue rewritten every run, a comment only on fingerprint change, `could_not_look` never collapsed to 0 findings |

**The caveat is worth stating precisely, because it is a genuine gap in an
otherwise exemplary mechanism.** The workflow's fingerprint is *"blind to a
finding's magnitude by design"* — its own words. That is the right call for
comment-spam, and it means **the MES stop divergence can widen from 69 ticks to
690 and the issue will not speak.** Combined with F-5's intermittency (a run
that lands during a flaky read grades `could_not_look`, contributes no
fingerprint entry for that account, and stays silent), the mechanism can be
quiet for two different reasons that a reader cannot tell apart. A magnitude
band in the fingerprint — not the raw number — would close it.

**A note on this audit's own method.** My first write-up of F-4 and F-5 was
wrong in the flattering-to-the-auditor direction: I graded a
already-operator-declined state as a new find, and an intermittent failure as a
permanent one. What corrected both was reading **the detector's own tracking
issue** — i.e. the system's durable record contradicted the auditor. That is
the INDEPENDENCE axis working in the direction it is usually not pointed, and it
is the argument for #10086-style *"record every run, speak only on change"*
tracking issues over ephemeral logs.

---

## Part 6 — The axes the first pass did not reach (added after operator challenge)

The operator's read was that the first pass *"doesn't seem extensive enough"*.
That was correct: I had named the gaps and not closed them. These are the
closures.

### 6.1 LIVENESS — the zombie hunt

| probe | result |
|---|---|
| workflows | **112**; exactly **1** with no trigger that can fire (`claude-run-failure-alert.yml` — it is `workflow_run`-driven, so this is a detector artefact, not a zombie). 13 scheduled. |
| systemd timers | **15 of 16 active.** The one inactive is `ict-ib-gateway-watchdog.timer` — **correct on this host**: it is auto-enabled only where `/etc/ict-vm-role == gateway`. |
| long-running services | **5 of 5 active** — `ict-trader-live`, `ict-web-api`, `ict-telegram-bot`, `ict-claude-bridge`, and **`caddy.service`** (the SPA's transport, the one unit outside the `ict-*` guard's reach). |
| env gates | 25 distinct `*_ENABLED` / `*_DISABLED` / `*_MODE` / `*_SOURCE` names in `src/`. No default-off gate found in front of a required capability. |

**F-13 · A unit deliberately absent from a host is indistinguishable from one
that has failed.** `/api/diag/services` lists `ict-ib-gateway-watchdog.timer`
and reports `inactive` on the live VM, where it is *supposed* to be absent. A
reader — or a health review grepping for non-active units — reads that as the IB
gateway auto-heal being down. It matters more than it looks given F-5: IB reads
are intermittently failing, and **nothing on the live VM can tell you whether
that watchdog is alive on the gateway VM.** The allowlist needs a per-host
expectation (`expected_on: gateway`), so `inactive` there grades as
`not_on_this_host` rather than as a failure. Not fixed — it touches the diag
allowlist contract.

### 6.2 MODULARITY — change amplification and registry completeness

**Change amplification is IMPROVING**, measured from real commits:

| change | files touched |
|---|---|
| add `ict_scalp_mgc_15m` (2026-08-20 measurement) | **17** |
| add `trend_donchian_eth_prop` | **13** |
| add `trend_donchian_sol_prop` | **13** |
| add account `bybit_portfolio` | **10** |

**F-14 · `DEFAULT_PRIORITIES` — the drifted default, re-measured and now
CONCRETE.** The 2026-08-20 audit raised this as a distribution problem; it now
has exactly one named live consequence.

- 50 map entries, **47 live legs**, **0 stale entries** (the map has no dead rows — good).
- **5 live legs are absent from the map**: `gdx_pullback_1d`, `iaum_pullback_1d`, `scha_trend_long_1d`, `slv_pullback_1d`, `splg_trend_long_1d`.
- A miss resolves to `_UNKNOWN_STRATEGY_PRIORITY = 10`, documented as *"deliberately below the in-scope strategies"*. The live distribution is **41 of 50 at priority 0** and **46 of 50 at ≤ 10** — so omission **outranks or ties 46 of 50 declarations**, the inverse of the constant's stated purpose.
- ⚠️ **But the blast radius is ONE leg, not five.** Priority only arbitrates between legs contending on the same symbol. Four of the five are the sole live leg on their symbol, so their priority never arbitrates. The single real instance: **`slv_pullback_1d` (miss → 10) contends with `slv_trend_1h` (declared 0) on SLV**, and the undeclared leg wins.

*(I checked whether the map's two call sites disagree —
`intent_multiplexer` passes `DEFAULT_PRIORITIES.get(name)` with no default while
`StrategyIntent.effective_priority()` resolves the miss itself. They do **not**
disagree: the `None` is deliberate and the miss is single-homed in
`effective_priority`. Reporting a divergence there would have been wrong.)*

### 6.3 RECURRENCE — the anti-treadmill pass, and the audit's sharpest finding

**F-15 · 🔴 Of 596 RESOLVED findings across the three backlogs, 2 carry a
`detector` — 0.3%. Both are from this session.**

| backlog | items | resolved | with a detector |
|---|---|---|---|
| health | 800 | 450 | **2** |
| performance | 106 | 65 | **0** |
| ml | 104 | 81 | **0** |
| **total** | **1,010** | **596** | **2** |

This is the mechanical answer to *"why do we keep finding the same bugs"*. Six
hundred findings have been fixed and essentially none left a permanent check
that fails if they return. The audit program's own finding schema has required
`detector` since 2026-08-20; adoption is ~0.

It is also the cheapest high-leverage fix on the list: the field exists, the
schema mandates it, and nothing enforces it. A `claim-basis`-style guard
requiring `detector` on any item moving INTO a terminal status would convert
the backlog from a queue into a regression suite.

---

## Coverage contract (honest)

**Behavioral (primary):** 7 committed invariants run against live broker truth
(3 pass / 0 fail / **4 not-measured**, cause F-5) · 8 end-to-end traces ·
4 executable coverage tools run with their self-tests verified ·
live reads of 12 API surfaces · 6 journal tables pulled whole
(4,961 trades / 3,979 order packages / 72 prop tickets / 32 prop fills /
56 telemetry / 10 prop status).

**Reading (secondary):** `order_monitor.py` (exit/protection/sweep paths),
`clients.py` (IB read path), `diag.py` (ib_open_orders route),
`system_invariants.py`, `exit_mechanism_coverage.py`,
`check_risk_basis_agreement.py`, `backtest_system.py` (risk-grid + `_risk_qty`
disclaimer), the audit + exit-refinement skills, the coverage matrix and its
rollup.

**CLOSED in the second pass (Part 6):** LIVENESS (workflows, timers, services,
env gates) · MODULARITY (change amplification, registry completeness both
directions) · RECURRENCE (detector coverage over 596 resolved findings).

**STILL not reached — stated, not implied:**
- **Android was not audited** (it is ON ICE per its own CLAUDE.md; a real defect
  would still be filed, and none was looked for).
- **Hop 12 was partially covered.** A targeted consumer-contract pass over three
  fields this audit touched found two of three clean and one divergent:

  | field | Streamlit | Svelte SPA |
  |---|---|---|
  | ML registry `stage` (**null on all 95 rows**) | ✅ falls back to `target_deployment_stage` | ✅ same |
  | `pnlProvenance` | ✅ rendered per-row | ✅ rendered + counted |
  | **prop `status_freshness`** | 🔴 **not read at all** | ✅ four states, uncollapsed |

  **F-12 · The Streamlit prop rule-distance panel ignored `status_freshness`**,
  rendering a `$282.86` cushion off a **58.7-hour-old** snapshot with only a raw
  `As of <iso>` caption — byte-identical on screen to a live cushion. The bot
  added the field 2026-08-14 for exactly this, and the SPA honours it; one
  endpoint had two behaviours and the divergent one is the frontend in use.
  **FIXED this session** — `ict-trader-dashboard` PR **#207** (four states,
  vocabulary matched to the SPA so the two frontends cannot describe one payload
  differently). A full per-trade render trace across all three consumers is
  still not done.
- **Trainer-VM-side** state beyond the mirror (no trainer relay run this session).
- A **per-line `src/` sweep** (retired as a headline metric by the skill).

---

## Design criticism (Phase 6)

**6a — Cohesion.** The recurring shape in this audit is not a broken component;
it is a **fix applied to the reporting instance while its sibling survives**.
Item 1.8 reclassified the Bybit-truth sweep and not the anchor sweep (F-3). The
IB naked sweep got a per-sweep summary line and Bybit did not (F-4b). The
risk-basis guard scans `scripts/` and `src/` and the concept it protects spans
both (F-8). The repo has *named* this pattern — "sweep the class, not the
instance" is in its own audit skill — and it keeps recurring because **nothing
mechanically enumerates the siblings**. A `class-sweep` guard that, given a
changed call site, lists structurally similar sites and requires each to be
addressed-or-declared would kill more defects than the next five bespoke guards.

**6b — Philosophy.** *Observe first, gate later* has produced an
observability estate that is now itself the largest source of unfalsifiable
claims. Three instruments in this audit reported clean while blind: the
selftest-wiring checker (over its own numerator), the IB naked-sweep summary
(omitting the counter that mattered), and `ib_open_orders` (`could_not_look`
with `error: null`). **The system's instruments have outgrown the system's
ability to verify its instruments.** Phase 0c exists for this and is the highest
marginal-value part of the program; it should grow, and `guard_selftests.py`
should reach 45/45 before another guard is written.

**6c — The backlog is the clearest structural signal.** 798 items, +90 in three
days, 341 open, 96 open `high`, **zero detectors**. A backlog growing faster
than it drains, whose items carry no recurrence detector, is not a memory — it
is a queue that will be re-derived by the next audit. The `detector` field going
from mandated to 0% adopted in one program cycle is the finding that predicts the
next audit's findings.

**What I would build differently:** the exit-label path (F-3) should not have two
sweeps with two policies. One `finalize_close(trade, price, price_provenance)`
chokepoint — price write, provenance stamp, and label derivation together — would
have made item 1.8 a one-line change and made F-3 structurally impossible. That
is the same move the repo already made twice, correctly, for the DB path resolver
and for `provenance.py`.


---

## Part 5 — THE WORK PLAN

Ordered by blast radius, then by whether the next step is blocked. Tier in
brackets; **Tier-3 items are proposals and stay draft until you approve.**

### Now — money-at-risk / trust-in-evidence

**WP-1 · Reclassify the exit label on the anchored-price path [Tier-2]**
*Closes F-3.* Add re-classification to `_sweep_local_pnl_for_unpriced`, mirroring
item 1.8's three guards (reduce-leg exclusion, only relabel a still-generic
reason, failure swallowed so the price write cannot be lost). **Do not reuse
`price_vs_pkg_bracket`** — an anchored price is ESTIMATED, so stamp a distinct
`exit_reason_source` (e.g. `price_vs_pkg_bracket_estimated`) and let
`provenance.classify_*` see it. Then run the staged historical relabel the
roadmap already has queued. *Detector:* a guard asserting every path that writes
`exit_price` also writes `exit_reason_source` — the 100% signature that made this
readable becomes the permanent check.
**Why first:** it is the input to M20's own lever selection, the exit-head
training rows, and the M7 gate.

**WP-2 · Make `/api/diag/ib_open_orders` reliable, and make its failure legible [Tier-1/2]**
*Closes F-5.* It is **flaky, not dead** — it graded `ib_paper` fine at
02:11:42Z and failed three consecutive calls at 05:34Z. Two independent halves:
(a) **Populate the error channel** — `account_ib_open_orders` returns `None` from
a branch that logs nothing and the route reports `error: null`. Make
`could_not_look` say *why* (a reason enum, `collapsed-state-guard`-registered).
Tier-1, cheap, and it is what turned a 20-minute diagnosis into a 3-call one.
(b) **Fix the read itself** — `reqAllOpenOrders` crashes the web-api's
`ib_insync` message handler on futures contract fields (`KeyError: 5/8/11`) and
drops the socket. The trader is unaffected, so the likely delta is the web-api
venv's `ib_insync` version. Pin/upgrade it, or route the diag read through the
trader's already-working path.
*Until this lands, any given run — this audit's invariants, or the scheduled
`broker-bracket-reconcile` — can silently draw a blind window on the only
account holding IB positions.*
*Detector:* `system_invariants.py` already returns `not_measured` — promote a
`not_measured` on a protection invariant to a **CI/health-review failure** when
the account holds open positions.

**WP-3 · Fix MGC's compound state, and disposition the 7 lapsed levers [Tier-2 / Tier-3]**
- **The target-naked pair needs no new decision** — you declined the TP attach
  on 2026-08-20 and the stop repair is Tier-3 draft #10081. Left as-is.
- **What does need action: MGC is target-naked AND monitor-blind at the same
  time** (`ict_scalp_mgc_15m`, `candles_unavailable`). The declined-TP decision
  was made when the monitor was still an exit path; it no longer is, so only the
  broker stop stands. Fix the IB candle availability, or revisit the TP decision
  for this position specifically.
- Add a detector for the **compound** state — no resting target AND no live
  monitor exit. #10089 grades resting protection, `monitor_blindness` grades the
  monitor, and nothing joins them.
- Give each of the **7 `shipped_gate_failed` cells** (F-10) a dated disposition:
  re-sweep, revert, or a HOLD with an expiry. **Start with
  `trend_donchian`/BTCUSDT `exit_head_ml`** — the only live ML exit head, and its
  re-sweep failed 2 of 3 gate conditions (auc 0.5403 < 0.55).

**WP-4 · `bybit_1`/SOLUSDT SL legs at 740% [Tier-2]**
Over-coverage is a hazard, not just untidiness (disjoint stop groups → the
survivor sells into a naked short). The `cancel-stale-tpsl-legs` action exists;
detection has been running and nothing remediates (F-7).

### Next — close out M20

The done-condition is *no `pending`/`blocked` rows on live legs*. Current open
cells and what actually unblocks them:

| lever | open | the real blocker |
|---|---|---|
| `bracket_geometry` | **33** (7 pending, 26 blocked) | **23 × `no_free_lane_candle_feed`** |
| `exit_head_ml` | 12 blocked | **11 × arithmetic**, not data |
| `vol_trail` | 4 | thin base |
| `exit_ladder` | 4 | 2 harness/history |
| `giveback_stop` / `regime_flip_exit` | 1 + 1 | — |

**WP-5 · Land the free-lane candle feed [Tier-1] — the single highest-leverage M20 item.**
23 of the 55 open cells (42%) are one missing feed. Source is already decided
(yfinance, proven on a runner because Yahoo 429s from the sandbox), and the same
feed closes M31 P5 precondition 3b. Nothing else on this list unblocks as much.

**WP-6 · Ship the `passed_unshipped` cells that are genuinely awaiting a declare [Tier-3]**
Of 15, most are deliberately held (shadow legs, proxies). The live ones worth a
decision now: `uso_trend_1h` **`trail_geometry`** (Path-A PASS, IS 197 / OOS 27 —
clears `MIN_OOS_TRADES = 25`) and `qqq_trend_long_1d` **`vol_trail`** (6/6
walk-forward on a derived split). Plus the 5 `ict_scalp` **`exit_head_ml`** E1
candidates the 2026-08-13 operator decision unblocked.

**WP-7 · Accept the 11 arithmetic `exit_head_ml` blocks as time, not work [Tier-1]**
`m20_coverage_rollup.py` proves these legs cannot form a *single* fold —
`qld_trend_long_1d` needs 119 more trades, `squeeze_breakout_4h` 52. **No
research unblocks them.** Reclassify from `blocked` to an explicit
`awaiting_trade_accrual` with the per-leg trade count needed, so M20's
done-condition is not permanently hostage to arithmetic. *This is what lets M20
close.*

**WP-8 · Add the Bybit per-sweep summary line [Tier-1]** (F-4b) — same reasoning
the IB comment already gives for why it exists.

**WP-8b · Give `broker-bracket-reconcile`'s fingerprint a magnitude band [Tier-1]**
Today a `stop_price_diverges` can widen from 69 ticks to 690 without the
tracking issue speaking, and a run landing in one of F-5's flaky windows goes
quiet for a *different* reason a reader cannot distinguish. Band the magnitude
(not the raw number — that would reintroduce comment spam) and make a
`could_not_look` on a previously-`reconciled` account its own fingerprint
entry.

### Then — make the evidence trustworthy

**WP-9 · Pay down the harness risk-basis debt [Tier-1 + Tier-3 re-runs]**
Move the fleet default from 0.3% to `--risk-pct live`, or state per harness why
its arm is deliberately off-basis. Then decide which M20 verdicts on the **25
quantization-exposed rows** (F-9) need re-running once the harness models the
whole-contract/whole-share floor and the sub-1 refusal. **Until then, treat every
futures and equity M20 verdict as measured on a trade population production may
not have taken.**

**WP-10 · Prop: fix the bridge before judging the strategies [Tier-1/2]**
83% ticket attrition is why prop has 2 completed trades since 2026-08-13, and
why no promotion or demotion is currently decidable (F-prop). Instrument the
funnel — 25 `suppressed` and 17 `emitted`-never-acted are two *different*
problems, and only one is the operator's response time. Also: refresh the
58.7-hour-stale balance snapshot, re-run
`scripts/prop/account_compat_matrix.py` for the 4 declared legs that have no
compat evidence (only `2026-06-17` SOLUSDT/`trend_donchian` exists), and purge
the 3 `[TEST PING]` rows from `prop_tickets`.

**WP-11 · Guards: 45/45 before the 46th [Tier-1]**
Fix `check_selftest_wiring.py` to report over the **guard** population (F-11),
then close the 25 uncovered guards. Per the repo's own rule, their greens are
currently inadmissible.

**WP-12 · Wire `detector` into the backlogs [Tier-1]**
0 of 404 open items carry one. Make it required on new items and backfill the 9
open `critical`. Without it the RECURRENCE axis has no substrate and the next
audit re-derives this one.

**WP-13 · One `finalize_close()` chokepoint [Tier-2, design]**
The structural fix behind WP-1: one function owning price write + provenance
stamp + label derivation, so the two sweeps cannot hold two policies. Same move
the repo already made for the DB-path resolver and `provenance.py`.

### Deliberately NOT proposed

- **No prop promotion or demotion.** Two completed trades since the roster
  changed; every real-money leg is below n=25. Recommending either way would be
  the cosmetic-decision anti-pattern this repo has a rule against.
- **No M20 lever declares beyond WP-6.** The rest are honest negatives.
- **`eth_pullback_2h` demotion from prop** — already off the roster since
  2026-07-18; its −$333 is historical. Its real-money weakness (n=5) is a
  separate, under-powered question.

---

# Part 7 — ALPACA LIVE READINESS (operator ask, 2026-08-23)

> *"I'm going to fund the alpaca live account now and I want us to flip from dry
> run to live, but first I want us to do a full portfolio performance review and
> pipeline verification to ensure that we are ready to start trading some real
> money there."*

**VERDICT: NOT READY AS CONFIGURED.** The flip is Tier-3 and I am not making it.
Three blockers, one of which is decisive and none of which is fixed by funding.
Every number below states its population.

## 7.1 The decisive blocker — 60.1% of this account's flow cannot execute

`alpaca_live` carries `shorting_enabled: false` (measured this session,
`/api/diag/broker_account_status`; the account reads `ACTIVE`, not blocked,
balance $0.10).

Measured over **all 318 `alpaca_live` journal rows** (2026-06-30 → 2026-08-23,
every row the account has ever produced): **191 SHORT / 127 LONG = 60.1% short.**
The concentration is in the four highest-volume legs:

| leg | n | short | short share |
|---|---:|---:|---:|
| `spy_pullback_1h` | 62 | 36 | 58.1% |
| `tlt_pullback_1h` | 57 | 41 | 71.9% |
| `qqq_pullback_1h` | 55 | 38 | 69.1% |
| `gld_pullback_1h` | 51 | 38 | 74.5% |
| `slv_trend_1h` | 27 | 12 | 44.4% |
| `gdx/gld/iaum/slv_pullback_1d` | 24 | 24 | **100%** |
| `uso_trend_1h` | 32 | 0 | **0.0%** |

**Nothing in the codebase reads `shorting_enabled`.** Verified: the string
appears in exactly three places — `alpaca_client.py` (the read + the field list)
and `diag.py` (the docstring). There is **no short gate on the order path**, no
`can_short` predicate, nothing. So flipping to `live` sends ~60% of this
account's orders to a broker that cannot fill them, and each becomes a venue
rejection at placement time.

This is not a funding problem. It does not improve at any account size.

## 7.2 The paper record does not transfer

The mirror account `alpaca_portfolio` (`paper_role: portfolio`, same broker,
`risk_pct: 0.02`) is the only forward record. Closed-with-pnl, n=21:

| direction | n | total pnl | measured+est | win rate |
|---|---:|---:|---:|---:|
| long | 14 | **+$6,392.80** | +$6,633.80 (n=12) | 36% |
| short | 7 | **−$4,254.23** | −$1,084.91 (n=3) | **0%** |

`alpaca_paper` (the wider soak roster, n=56): long −$1,183.45, short −$9,331.76.

So the mirror's ~+$2,138 headline is **entirely carried by longs**, and the
short book is 0-for-7 on one account and near-total loss on the other. Read
naively, "disable shorts" looks like it *improves* the account. But:

- **n=21 is not a decision population.** Split by direction it is n=14 / n=7.
- **`uso_trend_1h` alone is +$8,822.10 over 10 closed trades** and is 100% long
  — i.e. one leg is larger than the entire positive result, and the rest of the
  long book is net negative without it. A single-leg result at n=10 is a
  hypothesis, not an edge.
- **2 of the 16 `alpaca_live` legs have NO mirror record at all.**
  `alpaca_live` runs `splg_trend_long_1d` and `iaum_pullback_1d`;
  `alpaca_portfolio` runs neither. The "portfolio mirror" does not mirror the
  live roster.

## 7.3 Funding size — the number you actually need

Measured on the real entry/stop geometry of all 318 packages, against
`risk_pct: 0.02` and the round-up-to-one-share rule
(`_ROUND_UP_BUDGET_MULT = 1.5`, `risk.py:118`):

| funding | placeable (round-up) | placeable at TRUE risk | long-only placeable |
|---:|---:|---:|---:|
| $150 | 50.9% | 36.8% | 25.8% |
| $500 | 96.2% | 83.0% | 39.3% |
| **$1,000** | **99.4%** | **99.4%** | 39.3% |
| $2,000+ | 100% | 100% | 39.9% |

**~$1,000 is where the account stops refusing its own signals.** Below ~$500 a
large share of trades that DO place are riding the 1.5× round-up, i.e.
realising up to 150% of the configured per-trade risk.

This is not hypothetical — the account has been funded before. 25 journal rows
read `risk_refused: sized_qty=0 with balance=150.05` and similar: **at $150,
every single signal was refused for size.** Note the long-only column never
exceeds 39.9% at any funding level — that is §7.1's ceiling, restated.

Per-leg medians (balance to place one share): `qqq_trend_long_1d` $1,132 ·
`gld_pullback_1d` $438 · `qqq_pullback_1h` $387 · `spy_pullback_1h` $200 ·
`tlt_pullback_1h` $12.

## 7.4 Pipeline verification — what IS sound

Checked against the code, not the docs:

- ✅ **Whole-share quantization.** `risk.WHOLE_UNIT_QTY_EXCHANGES = {"alpaca"}`;
  `place()` re-quantizes through the shared `whole_unit_qty` helper, so the qty
  placed can never drift from the qty journaled (BL-20260622).
- ✅ **Bracket attach.** `order_class: bracket` with both legs, `oto` with one.
- ✅ **Post-accept rejection confirmation.** `ALPACA_PLACE_CONFIRM_S` (3.0s)
  polls for a terminal `rejected`/`canceled`/`expired` state, so an async venue
  refusal surfaces as a real failure instead of a phantom `open` row
  (BL-20260707). Its docstring names the PDT rule as one of the async rejections
  it catches — **this is the mechanism that would surface the §7.1 short
  rejections rather than journaling phantoms.** Good, but it converts a
  systematic 60% failure into 60% logged failures; it does not make them trade.
- ✅ **Broker-naked sweep, sides graded apart.** Entry brackets are `day` TIF, so
  the legs die at the RTH close — `_check_broker_naked_equity_positions` re-reads
  the broker each tick via `AlpacaClient.protection_state` (**not** the one-sided
  boolean) and re-arms a GTC OCO on a missing STOP, alerting without re-arming on
  a missing TARGET. The Alpaca half of `BL-20260816-COVERAGE-IS-ONE-SIDED` is
  already closed, over 13 live positions.
- ✅ **Extended-hours exit.** `_close_extended_hours` places a marketable LIMIT
  crossed by `ALPACA_EXT_LIMIT_BUFFER_BPS` (25bp) during pre/post market and
  **defers** (retCode 2, bracket left armed) when fully closed.
- ⚠️ **The close path has one real-money precedent and it needed a human.**
  Trade `8e43575f` (IEF, `ief_pullback_1d`, opened 2026-07-02 with
  `is_dry: false`) was closed 13 days later by
  `close_stranded_journal_row_script` with `broker_flat_confirmed: true`. One
  data point, but it is the only live-money round trip this account has and it
  did not close itself.
- ⚠️ **PDT is not modelled anywhere.** Verified: no `PDT` / `day_trade` /
  `daytrading_buying_power` logic in `src/` or `config/` — only prose in
  research docs and one comment. Measured on the closed Alpaca paper trades
  (n=94): **14.9% are same-calendar-day round trips**, median hold 73.7h. Over
  the observed window that is ~1.3 day trades per 5 business days, under the
  3-per-5 limit — but with no headroom modelled and no gate, a volatile week
  breaches it and the account gets flagged. Below $25k this is a live risk.
- ⚠️ **`sizing_failed: balance() returned None`** ×20 —
  `BL-20260813-ALPACA-BALANCE-NONE-WHILE-ACCOUNT-READS-ACTIVE`, still open. On a
  funded live account this is silent refusal, and it is exactly the class
  `SILENT_REFUSAL_CHECK_SECONDS` was shipped for (which now covers it).
- ℹ️ The 5 `exchange_rejected` rows are `Alpaca rejected order … unauthorized`
  and all 5 were **LONG** — an authorization state from 2026-06-30/07-01, not a
  shorting refusal. Do not read them as evidence for §7.1.

## 7.5 What would make this ready

In order. (1) and (2) are the gate; (3) is sizing.

1. **Decide the short question, in code.** Either (a) enable shorting on the
   Alpaca account (a broker-side action — margin account, ≥$2k), or (b) add a
   real short gate so a non-shorting account refuses the signal at the
   coordinator with a logged cause instead of at the venue. Doing neither and
   flipping anyway means 60% of the flow fails at placement. **(b) is Tier-3
   order-path work and should exist regardless of (a)** — `shorting_enabled` is
   read and never consumed, which is precisely the written-and-never-read shape
   `provenance-consumer-guard` exists to catch.
2. **Get a real forward record for the roster that will actually trade.** If the
   answer is (a), the existing short record is 0-for-7 and −$4,254 and needs to
   be much better before it takes real money. If the answer is (b), the live
   roster becomes long-only and the honest population is n=14 dominated by one
   10-trade leg — and `splg_trend_long_1d` / `iaum_pullback_1d` have no mirror
   record at all. Add both to `alpaca_portfolio` and let it soak.
3. **Fund ≥$1,000** so the account is not refusing its own signals or riding the
   1.5× round-up. $150 refuses everything.

**Recommended interim, if you want money in play now:** fund the account, add
the two missing legs to `alpaca_portfolio`, and leave `alpaca_live` at
`dry_run`. That costs nothing and buys the forward record item (2) needs.

**Filed:** `BL-20260823-ALPACA-SHORTING-FLAG-READ-NEVER-CONSUMED` (high),
`BL-20260823-ALPACA-PORTFOLIO-MIRROR-MISSING-TWO-LIVE-LEGS` (medium),
`BL-20260823-NO-PDT-MODELLING-ON-ALPACA` (medium).

---

# Part 8 — WORKPLAN: taking the Alpaca segment live at $200, cash account

> Operator, 2026-08-23: *"we only have 200 usd there, and if I'm not mistaken, we
> are limited to non-margin trading there right now, so we need to verify that we
> are only trading symbols there that are technically feasible (we already did a
> lot of work in the past to find proxy symbols that will work on that account),
> so let's come up with a workplan… Doesn't supersede the rest of this session's
> work, can be held for the end once we are ready to continue M20."*

Queued behind M20. This is the plan, not the execution.

## 8.1 The constraint set, measured

**A cash (non-margin) account changes the diagnosis in Part 7, and mostly for the
better — because it turns an open question into a settled one.**

- **Shorting is structurally impossible, not a disabled toggle.** Reg-T shorting
  requires a margin account, and FINRA's margin minimum is **$2,000**. At $200
  the account cannot be converted. So Part 7's option (a) — "enable shorting" —
  is **off the table**, and option (b) — a real short gate — is the only path.
  That is a simplification: there is now one right answer instead of two.
- **PDT does NOT apply.** The pattern-day-trader rule is a *margin* rule. The
  `BL-20260823-NO-PDT-MODELLING-ON-ALPACA` finding is **not a blocker for this
  account** and should be re-scoped to "applies if/when this account goes
  margin". It stays open, correctly, because the assumption in
  `market-alternatives-2026-06-10.md` ("the equities account will be >$25k") is
  still wrong and would bite on a future funding.
- **What replaces it is T+1 settlement.** In a cash account, proceeds of a sale
  are not available to buy again until settled. Buying with unsettled funds is a
  good-faith violation; selling something bought with unsettled funds is
  free-riding (a 90-day restriction). At $200 the entire balance recycles on
  every trade, so this binds *hard* and it is modelled **nowhere** — the same
  gap as PDT, but this one is live today.

**The affordability wall.** Latest entry prices from the bot's own feed:

| symbol | last entry | 1 share ≤ $200? |
|---|---:|---|
| SPY | $770.15 | **NO** |
| QQQ | $717.68 | **NO** |
| GLD | $402.06 | **NO** |
| IWM | $303.03 | **NO** |
| USO | $132.21 | yes (1.5 sh) |
| IEF | $93.50 | yes (2.1 sh) |
| TLT | $82.11 | yes (2.4 sh) |
| GDX | $74.02 | yes (2.7 sh) |
| SLV | $60.33 | yes (3.3 sh) |
| IAUM | $40.38 | yes (4.9 sh) |
| SPLG | *(no package ever emitted)* | — |

Four of the eleven live symbols cost **more than the entire account**, and those
four carry **55.7%** of its signal volume (177 of 318).

**Composing the constraints against all 318 historical packages:**

| filter | packages | share |
|---|---:|---:|
| all with geometry | 318 | 100% |
| …LONG (cash account) | 127 | 39.9% |
| …AND one share ≤ $200 | 66 | 20.8% |
| …AND stop risk fits the budget | **66** | **20.8%** |

**≈4 of every 5 signals this account produces cannot be acted on**, and the
survivors collapse onto **three symbols**:

| symbol | tradeable packages |
|---|---:|
| USO | 32 |
| TLT | 18 |
| SLV | 15 |
| IEF | 1 |

Two things worth naming, because both cut against the intuitive read:

- **The proxies do not help here.** `IAUM` (6 packages) and `GDX` (7) are
  affordable and were **100% short** — zero tradeable. `SPLG` has emitted **no
  order package at all**. The 2026-07-07 proxy sprint solved the *affordability*
  half correctly (SPLG ≡ SPY, IAUM ≡ GLD, no sub-$100 QQQ proxy exists), but
  affordability was never the binding constraint for those cells — direction is.
- **`SPLG`'s zero is NOT evidence of a dead leg.** Its whole family barely
  fires: SPY 1, QQQ 2, IWM 3, SCHA 1, QLD 1, TQQQ 0, SPLG 0 over ~7 weeks. A 0
  in a family whose median is 1 is not a signal. Do not open a dead-leg
  investigation on it without a denominator.

## 8.2 The workplan

### Phase R — research (no code, no money)

- **R1. Settle the roster against the real constraint set.** The question is not
  "which symbols are affordable" (answered 2026-07-07) but **"which
  (symbol, direction) cells are executable in a $200 cash account"**. Produce the
  cell list from the intersection above, and for each excluded cell record *why*
  (unaffordable / short-only / both) so the exclusion is auditable and reverses
  cleanly when the account grows.
- **R2. Find the long-side gap.** The three survivors are USO, TLT, SLV. Ask
  whether the *pullback* family's short bias is a property of the strategies or
  of the July–August window — a 7-week sample that happened to be one-directional
  is a different fact from a structurally short book, and only the second
  justifies re-rostering. Use the backtest sweeps, not the live journal.
- **R3. Re-run the per-account compat matrix at the real numbers.**
  `scripts/prop/account_compat_matrix.py` at **$200 / long-only / cash**. The
  2026-07-07 sprint ran it at ~$150 with the old 10%/10%/10% caps and found
  survival 0.69 against a 0.90 floor. Nobody has run it at 2%/5%/5% on $200 with
  the direction filter applied. **This is the gate**: if survival fails here,
  nothing downstream matters.
- **R4. Decide the honest evidence base.** The paper mirror's positive result is
  n=14 long trades dominated by one 10-trade leg — and that leg, `uso_trend_1h`,
  is also 48% of the tradeable flow. Concentration this high is a hypothesis.
  Decide explicitly whether that clears the bar, or whether the account trades
  one leg while the others accrue.

### Phase E — engineering / technical validation

- **E1. Short gate (Tier-3).** Refuse a short at the coordinator, with a named
  cause in the journal, for any account whose broker reports
  `shorting_enabled: false`. Today that flag is read and consumed by nothing, so
  ~60% of this account's flow would fail at the venue with no attributable
  refusal. Ship this **before** any flip — it is what makes the other 80% of the
  signal flow legible instead of noisy.
- **E2. Affordability refusal, stated not silent.** A signal whose one-share
  notional exceeds available cash should refuse with its own cause, distinct
  from `risk_refused` (a risk-budget verdict) and from `zero_balance` (an empty
  account). Three different conditions currently land in overlapping buckets;
  at $200 the affordability one is the common case and deserves its own name.
- **E3. Settlement awareness (cash accounts).** Track settled vs unsettled cash
  and refuse an entry that would buy with unsettled proceeds. Alpaca exposes the
  fields; nothing reads them. Without this the account earns a good-faith
  violation and, on the second, a 90-day restriction — an outcome no amount of
  strategy quality survives.
- **E4. Close the mirror gap.** `splg_trend_long_1d` and `iaum_pullback_1d` run
  on `alpaca_live` and on no portfolio-role paper account, so the live roster has
  legs with no forward record. Make `alpaca_portfolio` a superset of
  `alpaca_live` and add a check so the drift cannot recur silently.
  (`BL-20260823-ALPACA-PORTFOLIO-MIRROR-MISSING-TWO-LIVE-LEGS`.)
- **E5. Real-venue revalidation — the step that has never been done.** The
  2026-07-07 sprint recorded this honestly as an open gap ("real-venue
  revalidation is NOT done… unconfirmed that IAUM (and SPLG when affordable)
  actually sizes ≥1 whole share and places a bracket order"), armed a check-in
  for 2026-07-08, and **the loop was never closed**. One real long entry on one
  affordable symbol during RTH, watched end-to-end: sizes ≥1 whole share →
  bracket attaches → both legs rest at the broker → the day-TIF legs are re-armed
  as GTC by the naked sweep after the close → the position exits without a human.
  That last clause is the one with a bad precedent: the account's only live-money
  round trip to date (IEF, July) was closed 13 days later by a manual flatten
  script.
- **E6. Then, and only then, the flip.** `set-account-mode alpaca_live live`,
  Tier-3, operator-approved, with the roster from R1 and the gates from E1–E3 in
  place.

### Sequencing

R3 gates everything (a failed survival check ends it). E1 and E3 are the two
that prevent a *venue-level* bad outcome and should land regardless of whether
the flip happens. E5 is the last thing before E6 and cannot be done outside US
RTH — schedule it deliberately rather than discovering the market is shut, which
is what happened in July.

### What this is not

This plan does not assume the account should trade. It is entirely possible the
honest answer from R3/R4 is *"$200 in a cash account cannot express this book —
leave it in dry_run and let the portfolio mirror accrue"*. That is a legitimate
outcome and should not be treated as a failure of the plan.

---

# Part 9 — ML / soak infrastructure health (operator note #3)

> *"we also need to specifically verify the health of the mls and ml training
> infra — claude recently told me we were having issues there, with daily
> training sessions resetting soak decisions."*

**VERDICT: the soak infrastructure is HEALTHY, and the "daily training resets
soak decisions" hypothesis is REFUTED on both halves.** Measured against the
live registry mirror and `/api/bot/shadow/stats` on 2026-08-23.

## 9.1 Retraining does not reset anything

`ml/registry/model_registry.py::ModelRegistry.register` on a re-train of an
existing `model_id` explicitly carries forward `status`, `target_deployment_stage`
**and** `stage_history`, and appends a `StatusEvent(from_status=existing.status,
to_status=existing.status, reason="re-trained (run_id=…)")` — a **no-op event
that records the retrain**, not a reset. The stage ladder therefore survives a
retrain untouched.

The observation that seeded the worry — *every* one of the 5,015 history entries
carries `to_status: "candidate"` — is fully explained by that line: they write
`to_status = existing.status`, and every row's status IS `candidate`. Which
leads to the one real (minor) finding:

**`status` is a vestigial axis.** All 95 registry rows read `status: candidate`,
including the 3 at `target_deployment_stage: advisory`. Nothing moves it. The
live ladder runs entirely on the orthogonal `target_deployment_stage`
(`ml/cli.py promote-stage`), and `deployment_bucket` derives from that, so
nothing is broken — but a reader who checks `status` sees a fleet that looks
stuck at candidate. Worth collapsing or documenting; **not** a soak defect.

## 9.2 Soaks are accruing, and 29 of 30 are MEASURED

`/api/bot/shadow/stats?stage=shadow`, 30 shadow-stage records:

| `soak_start_basis` | rows |
|---|---:|
| `registry` (a recorded stage transition) | 14 |
| `registry_registration` (registered directly at the stage) | 15 |
| `observed` | 1 |
| **`log_censored` (a LOWER BOUND)** | **0** |
| `unknown` | 0 |

**Zero rows report a lower bound.** Soaks run 5.6 → 96.1 days and are accruing
(`execution-quality-baseline-v0` 96.11d/5563 records at the top;
`exit-head-donchian-peak-1h-v1` 5.56d/64 at the bottom, the newest).

## 9.3 A correction to my own working hypothesis, recorded deliberately

Mid-audit I measured that `stage_history` is **empty for 69 of 95 rows (72.6%),
including 15 of 28 shadow models**, concluded the soak clock was unrecoverable
for over half the fleet, and began implementing a fix (record a birth
`StageEvent` at initial registration). **That was wrong and I reverted it before
committing.**

`ml/shadow/inspector.py` had already solved it, correctly, and had measured the
same 15/29 on 2026-08-11: `stage_registration_times` handles precisely the
"registered directly at the stage, never promoted" case, and reports it under a
**distinct basis** (`registry_registration`) kept apart from a recorded
transition (`registry`) — *"one is an event, the other is an inference from the
state"*. Both count as measured; neither is a lower bound. My "fix" would have
written a birth event that made those rows match the *transition* path, silently
collapsing a distinction the module maintains on purpose.

The lesson is the standing one — **verify your own output, hardest when it
confirms what you expected.** An empty field looked like a gap; the gap was
already closed one module over, with better vocabulary than I was about to add.

## 9.4 Two small things noted while measuring

- **One row carries a non-canonical stage.** `mes-regime-classifier-baseline-v0`
  reads `target_deployment_stage: "research_only"` — a legacy 7-stage name the
  ladder collapsed on 2026-06-16. `deployment_bucket` resolves it correctly to
  `OFFLINE` (the enrichment canonicalises), so nothing downstream is wrong, but
  a consumer reading the raw field gets a name the docs say no longer exists.
- **All 3 advisory heads show `linked_strategies: []`.** This is **expected and
  is not evidence they are unused**: `linked_strategies` is derived from
  strategies naming a model in `shadow_model_ids`, whereas the advisory regime
  gate resolves its head **per-SYMBOL** via `ml_vol_regime_for_symbol`. Do not
  read the empty list as "registered but unused" for an advisory row.

## 8.3 INVERSE PROXIES — the unlock, and the biggest single item in this plan

> Operator, 2026-08-23: *"we can also consider 'short proxies', eg tqqq — we want
> to make sure we can have as much of the portfolio trading as possible."*
> …and: *"We may need to do some backtesting and strategy adjustment before
> flipping live — that's fine, just make sure everything is in the work plan."*

**This is the right idea and it is the highest-leverage item here**, because it
attacks *both* binding constraints at once: an inverse ETF expresses a short view
by **buying**, which a cash account can do, and the inverse funds are **cheap**,
which fixes affordability on exactly the four symbols currently at 0% tradeable.

One factual note on the example: **TQQQ is a 3× LONG fund**, not a short proxy —
it and QLD are the *leveraged long* siblings already sitting on `alpaca_paper`.
The short proxies are the inverse members of that same family (`PSQ`, `SH`,
`RWM`, `TBF`, …). The idea is exactly right; it is that family, other end.

### The measured upside

Direction split per symbol, all 318 `alpaca_live` packages with geometry:

| symbol | long | short | tradeable today | clean −1× inverse |
|---|---:|---:|---:|---|
| SPY | 27 | 36 | 0 | **SH** |
| TLT | 18 | **42** | 18 | **TBF** |
| QQQ | 19 | 38 | 0 | **PSQ** |
| GLD | 13 | **44** | 0 | DGZ — an **ETN**, not an ETF |
| SLV | 15 | 17 | 15 | none at −1× (ZSL is −2×) |
| USO | 32 | 0 | 32 | — |
| GDX | 0 | 7 | 0 | none at −1× (DUST is −2×) |
| IAUM | 0 | 6 | 0 | DGZ (same underlying as GLD) |
| IWM | 2 | 0 | 0 | **RWM** |
| IEF | 1 | 1 | 1 | **TBX** |

- **Today: 66 of 318 tradeable = 20.8%.**
- Short flow on the symbols with a **clean −1× proxy** (SPY, QQQ, IWM, TLT, IEF):
  **117 packages = 36.8% of all flow.**
- **Potential: 66 + 117 = 183 = 57.5%** — nearly **3×** the current coverage,
  using only unleveraged −1× funds.
- Adding GLD + IAUM (50 more) via DGZ would reach ~73%, but DGZ is an **ETN** —
  unsecured bank credit, not a fund holding assets — which is a different risk
  class and should be a separate, explicit decision.

**`TLT` is the single highest-value addition**: 42 shorts, the largest short book
of any symbol, and `TBF` is a clean unleveraged −1×.

### Why this is research, not a config edit

The 2026-07-07 proxy sprint is the precedent and it did this correctly:
*"Backtested each proxy's own price series through the same daily cell params as
its expensive twin."* An inverse proxy needs the same treatment and more, because
four things do **not** carry across:

1. **Daily-rebalance decay is real at our holding periods.** A −1× fund tracks
   the *daily* inverse, not the inverse over a multi-day hold; in a chopping
   market both the index and its inverse can lose. Our measured Alpaca holds are
   **median 73.7 h, p90 312 h** — days to weeks, squarely in the range where the
   path-dependence bites. This is the strongest argument for staying at **−1×**
   and treating −2× / −3× (ZSL, DUST, SCO, SQQQ) as a separate question with a
   much higher bar; those are built for intraday.
2. **The stop/target geometry does not transfer.** A stop derived from SPY's
   series is not a stop on SH's. Entry, SL and TP must be re-derived on the
   proxy's own bars — and the *risk* per share changes, which feeds straight back
   into the affordability arithmetic in §8.1.
3. **Costs are an order of magnitude higher.** Inverse ETFs run ~0.90–1.00%
   expense ratios against 0.03–0.09% for SPY/SPLG. Over a multi-day hold that is
   a real drag and must be in the backtest, not assumed away.
4. **Liquidity and spread** on the smaller inverse funds (RWM, TBX, DNO) are
   thinner than their long twins and will show up as slippage a paper fill hides.

### Added to Phase R

- **R5. Inverse-proxy identification.** For each symbol carrying short flow, find
  the best **unleveraged −1×** instrument and record price, expense ratio, AUM,
  average volume and spread. Prefer an ETF over an ETN (the GLD/DGZ case). Record
  explicitly where **no** acceptable −1× exists (SLV, GDX, USO today) rather than
  silently substituting a −2×.
- **R6. Backtest each proxy on its OWN series, net of its own costs**, through the
  cell params of the strategy that would trade it — the 2026-07-07 method, with
  the expense ratio and a realistic spread included. A proxy that only works
  gross-of-cost has not passed.
- **R7. Decide the expression mapping per cell, and adjust the strategy.** A short
  signal on SPY becomes a long entry on SH — which means the cell needs its own
  geometry, its own risk sizing, and its own name in the roster. This is
  strategy-adjustment work, not a symbol substitution, and it should be
  backtested and rostered like any new cell (skill: `new-strategy`).
- **R8. Re-run R3's compat matrix on the EXPANDED roster.** The survival gate must
  be cleared by the roster that will actually trade, not by the long-only subset.
  Adding legs changes the correlation structure — two inverse funds on correlated
  indices are not two independent bets, and at $200 concentration matters more
  than usual.

### Added to Phase E

- **E7. Roster + wiring for the accepted proxies** — signal builders,
  `monitor_unit` tags, intent-multiplexer registration, `strategies.yaml` cells,
  `instruments.yaml` profiles, descriptions, roster-pin tests. The 2026-07-07
  sprint's file list is the exact template; CI caught a real wiring gap in that
  sprint (`test_strategy_monitor_unit_resolution`), so expect the same guards to
  earn their keep here.
- **E8. Mirror the new cells on `alpaca_portfolio` FIRST** and let them accrue a
  forward record before any of them trade real money — which also closes E4's
  roster-drift gap by construction rather than as a separate chore.

### Revised sequencing

R5 → R6 → R7 gates the roster; **R8 (compat at $200 on the expanded roster) is
the hard gate** and supersedes R3 as the go/no-go. E1–E3 (short gate,
affordability refusal, settlement awareness) still land regardless — note that
**E1 becomes even more important, not less**: once shorts are expressed as long
buys on proxies, any *residual* raw short signal reaching the account is a
mapping gap, and the gate is what makes it visible instead of a venue error.

E5's real-venue revalidation and E6's flip come last, unchanged.

**Expected outcome if R5–R8 clear:** the account goes from expressing **20.8%**
of its signal flow to roughly **57%** on unleveraged instruments — with the
honest possibility that decay and costs knock some of those cells out in R6,
which is exactly what R6 is for.
