# MI-278 U11 — a control that CAN report exists; the thing that blocks the test is the outcome, not the control

> **Doc status:** `live` · category `research` · last verified `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U11 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Discharges** `BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS` — by correcting its premise rather than by taking either of the two exits it offered.
> **Tier-1.** Read-only: `config/strategies.yaml`, `config/accounts.yaml`, `git show` on the ship commit, one `/api/diag/journal` pull. **No config file is written and no parameter change is proposed.**

---

## 0. The answer, in five sentences

That row says e35's two declared controls have no data, and offers two exits: **(a)** record the experiment as unfalsifiable on its own controls, or **(b)** choose a control that CAN report and re-run the dose-response. **Neither is right, and the reason is worth more than either would have been.** A within-family control that can report **does exist and has been trading the whole time** — `trend_donchian_eth`, held at `atr_stop_mult` 2.5 continuously across the e35 boundary, same family and same symbol and same timeframe as the declared control `trend_donchian_eth_prop`, differing from it only in that it routes to `bybit_1` instead of the prop bridge — so (a) would record a falsehood. But **the sample it gives is already sufficient and the test still cannot be run**: 23 treated against 11 control closes detects a 27-point difference, comfortably inside MI-275's observed 55-point dose effect, and **16 of those 23 treated closes do not record what ended the trade**, which collapses the usable arms to 7 and 7 and the resolution to 71 points. **The binding constraint moved from the control to the outcome variable**, and that is a defect already filed, already measured, and already carrying a written Tier-2 remedy.

**A third structural finding the row does not have:** `avax_pullback_2h` is `execution: shadow`. The row treats the AVAX within-symbol dose comparison as unavailable *in this window*; it is unavailable **permanently**, for the same reason as the widened arm. **Two of e35's nine treated legs are shadow legs** — e35 shipped a live bracket-geometry cell to two legs that cannot act on it, where the row names one.

---

## 1. Population, stated first

| | |
|---|---|
| leg census | `config/strategies.yaml` + `config/accounts.yaml` at `origin/main` `01aa8d367`, read 2026-09-12 — **55 legs, 11 accounts** |
| treated set | diffed out of the **ship commit itself**, `892c9a2c8` (#10450, 2026-08-30) vs its parent, on `atr_stop_mult` + `tp_r` — **9 legs, 10 field edits** |
| family map | `docs/research/e35-bracket-corpus.jsonl`, the e35 sweep's **own** output — **41 of 55 legs carry a family** |
| journal | `/api/diag/journal?table=trades&limit=1000`, read 2026-09-12, **1000 rows, all non-backtest**, opened 2026-08-18T01:53:40Z → 2026-09-12T12:01:21Z |
| the e35 deploy instant | **2026-08-30T08:53Z** (sprint log `S-E35-SHIP-REVERSED-GEOMETRY-2026-08-30`); rows split on `created_at`, i.e. on when the trade was **OPENED**, because a trade opened before the deploy carries the OLD geometry however late it closes |

⚠️ **This is a 1000-row TAIL, not the lifetime.** Every count below is a property of that window.

⚠️ **The treated set is derived from the commit, never from a comment.** The legs carry inline `# M20 e35 re-check 2026-08-29` annotations, and reading those would be reading a claim about the event instead of the event. Field beats comment.

**Positive control for the probe** — before trusting any negative, the census was required to find the known e35 legs with their documented values, and it does: `htf_pullback_trend_2h` at 3.0 / `shadow`, `trend_donchian_eth_prop` at 2.5 / `breakout_1`, `trend_donchian_eth_4h` at 2.0, `trend_donchian_avax_4h` at 1.5.

---

## 2. Can a leg report at all? — 42 of 55

A leg produces a row in `trades` only if it can open a live position **and** at least one account it routes to both executes and writes there. Three independent ways that fails, kept apart because they have three different remedies:

| state | n | what it means |
|---|--:|---|
| `can_report` | **42** | `execution: live` and ≥1 route with `mode: live` and a non-prop class |
| `blocked_shadow` | 10 | cannot open a live position, however it is routed |
| `blocked_prop_only` | 2 | live, but every route is the prop bridge — fills land in `prop_tickets`/`prop_fills`, **isolated from `trades` by design** |
| `blocked_dry_run_only` | 0 | — |
| `blocked_unrouted` | 1 | named by no account (`xauusd_trend_1h`) |
| `unknown_execution` | 0 | *we could not look* |

`execution` is graded **before** routing, deliberately: a shadow leg cannot open a position however well it is routed, so reporting a routing cause for one would name a blocker that is not the binding one.

The two `blocked_prop_only` legs are `trend_donchian_eth_prop` and `trend_donchian_sol_prop` — **both of e35's declared within-family controls are in this bucket**, which is the row's own finding, now with the census behind it.

---

## 3. The control the experiment needed already exists

| | declared control | the control that reports |
|---|---|---|
| leg | `trend_donchian_eth_prop` | **`trend_donchian_eth`** |
| family | donchian | donchian |
| symbol | ETHUSDT | ETHUSDT |
| timeframe | 1h | 1h |
| `atr_stop_mult` | 2.5 (held) | 2.5 (held) |
| `execution` | live | live |
| routes to | `breakout_1` (prop) | `bybit_1` (live, paper) |
| **rows in the window** | **0, ever** | **13 · 8 closed after the deploy** |

The same holds for `trend_donchian_sol` (SOLUSDT 1h, 2.5, 3 closed after the deploy) against `trend_donchian_sol_prop`.

**They are held, not merely untreated.** Only **two** commits have touched `config/strategies.yaml` between the e35 ship commit's parent and `main`: e35 itself, and MI-158 (`3f88bfff0`), which only wrote out ten **pullback** legs' inherited `tp_r: 50.0` sentinel explicitly — runtime-identical by its own diff, and it touches neither control. So both controls read `atr_stop_mult: 2.5` at `892c9a2c8^`, at `892c9a2c8`, and at `main`.

**This refutes one of the row's claims directly.** It says the dose-response is "CONFOUNDED BY SYMBOL AND TIMEFRAME with no way to remove it from this window". There is a way, and it is a *crossing* one:

- **within-symbol, across-timeframe** — `trend_donchian_eth_4h` (2.0) against `trend_donchian_eth` (2.5); `trend_donchian_sol_4h` (1.5) against `trend_donchian_sol` (2.5)
- **within-timeframe, across-symbol** — `trend_donchian` (BTC 1h, 2.0) against `trend_donchian_eth` + `trend_donchian_sol` (both 1h, 2.5)

Neither alone removes both confounds. **Together they cross**: an effect appearing in both is unlikely to be driven by symbol and by timeframe at once. That is strictly more than the row believed was available.

---

## 4. And it still does not buy the test — because the outcome is not recorded

Reproduce: `python3 scripts/research/e35_control_reachability.py --power <journal.json> --control trend_donchian_eth --control trend_donchian_sol`

| arm | treated n | control n | minimum detectable difference (two-sided Fisher, α=0.05) |
|---|--:|--:|---|
| all closes opened after the deploy | 23 | 11 | **27 points** |
| closes whose exit label names a mechanism | **7** | **7** | **71 points** |

**The sample is sufficient and that is the surprise.** To detect MI-275's observed dose effect — a stop rate of 0.80 against 0.25, 55 points — at this arm ratio needs ≥17 treated and ≥8 control. The window supplies 23 and 11. **Waiting for more trades is not what this test is short of.**

What it is short of is an outcome. **16 of the 23 treated closes carry `reconciler_filled` or `netting_attributed`** — labels that record that the row was closed, not what closed it (`BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL`, and MI-278 U2/U10). A stop *rate* cannot be computed over rows whose exit mechanism is unrecorded (`checked: docs/research/RESEARCH-CAPABILITY-INDEX.md` and `scripts/research/` — `m20_u2_winner_close_attribution.py` recovers a mechanism for *some* such rows at a stated tolerance and MI-278 U10's `venue_mechanism_recovery.py` recovers one from the venue for 11 of 17 tested, so this is a claim about the JOURNAL's labels and not about recoverability in principle), and dropping them takes the resolution from 27 points to 71 — past MI-275's own effect size, so the comparison stops being able to see the thing it exists to see.

⚠️ **And dropping them is not a neutral filter, so the attributable subset must not be quoted as a rate.** An unlabelled stop is exactly what a `reconciler_filled` close looks like, so the missingness is correlated with the outcome. On the attributable subset the treated arm reads 7 of 7 `sl` and the control 2 of 7 — **that pair of figures is not reported here as a finding, and must not be lifted out of this paragraph as one.** It is what a 70%-censored arm looks like when the censoring mechanism is the outcome.

---

## 5. The third structurally-blocked arm, which the row does not have

The row records the AVAX within-symbol dose comparison as lost to this window: *"all 46 `avax_pullback_2h` rows in the window are `rejected`"*, filed as a *could not look*. **Measured on config: `avax_pullback_2h` is `execution: shadow`.** It is not a leg that happened not to trade — it is structurally incapable of trading, exactly like the widened arm, and the 46 rejections are the consequence rather than the cause. Confirmed on the journal: 46 rows, **0 closed**, in a window in which 42 legs did close something.

So **two of e35's nine treated legs are shadow** — `htf_pullback_trend_2h` (widened 2.5→3.0) and `avax_pullback_2h` (2.5→2.0). The row's observation that *"a geometry cell was shipped to a leg that cannot act on it"* is not a one-off; it is **22% of the treated set**, and nothing checked before the ladder shipped.

---

## 6. Disposition — the row stays open, with its premise corrected

Neither offered exit is taken, and the reasons are different for each:

- **Not (a).** Recording e35 as unfalsifiable on its own declared controls would be true of the *declared* controls and false as a statement about the experiment: a control that reports exists, was named in the same config, and has been trading throughout.
- **Not (b).** The control is chosen and named, but re-running the dose-response against it today produces a number and not evidence, because the outcome variable is 70% unrecorded in the arm that matters.

What the row needs instead is what this unit supplies: its premise corrected, its controls named, and its exit re-pointed at the constraint that actually binds. **The dose-response becomes runnable when the exit mechanism is recorded** — which is the Tier-2 persistence change written out in `docs/research/m20-u10-venue-mechanism-recovery-2026-09-12.md` § 5 and **not taken**, and which the venue has already been shown to be able to supply for 11 of 17 tested rows.

**Nothing here is proposed as a config change.** Routing `htf_pullback_trend_2h` to `execution: live` would make the widened arm report and is **Tier-3 and deliberately not proposed** — shadow may well be the right state for that leg, and the row says so too.

**No exit-refinement coverage-matrix row is owed.** The matrix records a verdict per *lever* (`trail_geometry`, `stale_stop`, `giveback_stop`, `exit_ladder`, `exit_head_ml`); e35 is bracket geometry shipped by a Tier-3 config edit, not a lever cell, and this unit produces no lever verdict. Stated rather than silently skipped.

---

## 7. What this unit does NOT establish

- **It does not say the e35 geometry change was right or wrong.** It says the experiment's falsifier is reachable and the measurement that would run it is not yet trustworthy.
- **It does not re-grade MI-275's inverted dose result.** That stands as MI-275 reported it, with MI-275's own caveat that it is corroboration and never standalone evidence.
- **It does not establish that the two named controls are GOOD controls** beyond family, symbol, timeframe and held geometry. They differ from their treated counterparts in `tp_r` (the controls carry the 50.0 sentinel, so the venue clamp sets their target) and in whatever else their leg blocks declare. A dose-response run against them owes its own covariate check.
- **It is one window and mostly paper.** `bybit_1` is a paper account.
