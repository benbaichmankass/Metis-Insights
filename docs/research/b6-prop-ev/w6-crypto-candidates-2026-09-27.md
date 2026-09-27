# W6 — crypto-leg candidates for `breakout_1`, scored against the B6 EV bar

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Lane: W6 (checklist row B6), dispatched by manager session
> `session_01Ljhs6sFAdWHdMDhJpL5aBP`. Tier-1 research — no roster, execution,
> sizing or config change. Adding a leg to `breakout_1` is Tier-3 and this
> document PROPOSES, it does not decide.

> ## ⚠️ CORRECTION 2026-09-27 ~12:30Z — operator directive: candidates come ONLY from Breakout's tradable symbol list, and §5 is now DISQUALIFIED
>
> The operator (joint review, relayed by the manager): *"Why was it even checked if it can't be traded? We are only supposed to checking candidates from the symbols list I gave you."* Two things follow, both now resolved on `main`:
>
> 1. **`breakout_1` is CONFIRMED a DXtrade account** — the operator logs in at `https://app.breakoutprop.com/`, which Breakout's own FAQ (S3, `intercom.help/breakoutprop/en/articles/14215629`) maps to DXTrade. Landed as PR [#13124](https://github.com/benbaichmankass/Metis-Insights/pull/13124) (`docs/research/prop-automation-options-2026-09-27.md`), a parallel lane, merged 2026-09-27T10:23:57Z.
> 2. **First-party source (S7, `intercom.help/breakoutprop/en/articles/16188026`, Breakout, dated 2026-08-06), quoted verbatim: "available exclusively on the Breakout Terminal and are not available on DXTrade."** That source names S&P500, XYZ100, SILVER and CL as the Terminal-exclusive set. **Every §5 candidate (`spy_*`, `qqq_*`, `tqqq_trend_long_1d`, `qld_trend_long_1d`, `slv_pullback_1d`, `uso_trend_1h`, `mes_trend_long_1d`) is therefore NOT TRADABLE on `breakout_1` as it exists today** — §5 is retained below for the record (the scoring work and basis caveats stand as an analysis of what a *separate, future Breakout Terminal account* could carry) but **every one of its candidates is WITHDRAWN, not proposed.**
> 3. **Correction to an earlier draft of this note**: this session's own `WebSearch` (not this repo's citation trail) initially returned a synthesized claim that XYZ100 specifically is available on DXTrade, contradicting S7's first-party, directly-quoted quote. Per RULE ONE (field/first-party source beats an AI-synthesized summary), **S7's direct quote wins** — XYZ100 is treated as Terminal-only same as the other three, and the earlier WebSearch claim is retracted rather than left standing.
> 4. **§2's crypto candidates are NOT affected** — BTC/ETH/SOL/XRP/ADA/AVAX are DXTrade-native crypto pairs (the same platform `breakout_1` already trades SOL/ETH on), confirmed by this repo's own existing DXTrade wiring (`config/prop_rulesets/breakout_routing.yaml`) for BTC/ETH/SOL and by Breakout's own crypto-majors product list (operator screenshot + WebSearch corroboration, `docs/integrations/breakout-instruments-2026-09-27.md`) for XRP/ADA/AVAX. See the per-candidate symbol-mapping table added to §2.
> 5. **No permission refusal was hit** obtaining any of this — the correction was reachable entirely from already-merged `main` content (PR #13124) plus `WebSearch` (no blocked call).

## 0. What this answers, and what tool scored it

Row B6 ([`docs/claude/work/MANAGER-CHECKLIST.json`](../../claude/work/MANAGER-CHECKLIST.json))
is the operator's EV bar (quoted verbatim from the checklist row; not a fresh
measurement, so no population applies):
*"we want to promote strategies that create a
portfolio that we would statistically expect to produce a profit (over the
<!-- population-ok: verbatim quote of the operator's own directive, not a fresh measurement -->
initial account cost of about 1%) before the account dies."* A parallel lane
(P1, branch `claude/w6-prop-ev`) built the canonical simulator for this —
`scripts/research/prop_ev_sim.py`, registering
`RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2`: **PASS** if the 5th percentile of the
outer-bootstrap (evidence) distribution of mean net-$-per-life under the
`path` intra-trade model is `> 0`; **FAIL** if the 95th percentile is `< 0`;
else **indeterminate**.

That branch was not yet merged to `main` when this lane ran (confirmed:
`git fetch origin claude/w6-prop-ev` resolves to `2d7e65fff1`, not an
ancestor of `origin/main`). Per the W6 brief ("fetch that branch; coordinate
on the schema by reading its code") this lane read
`scripts/research/prop_ev_sim.py` from that branch, ran it unmodified
(`--self-test` passes, 0 failures) against the candidate legs below, and
lands the result here rather than re-implementing a second EV/survival
engine — the input JSONL contract (`entry_time`, `exit_time`, `gross_r`,
`entry`, `sl` per trade) is satisfied VERBATIM by the existing
`comms/strategy_evidence/runs/<date>/<leg>__trades.jsonl` corpus, so no
repricing step was needed on this lane's side; the tool reprices to
Breakout's real cost stack internally (`--costs breakout`, its default).
**Once P1's branch lands on `main`, re-run this scoring from the merged
tool and reconcile — filed as a pipeline follow-up.**

All results are committed at
[`comms/strategy_evidence/runs/2026-09-27-w6-prop/`](../../../comms/strategy_evidence/runs/2026-09-27-w6-prop/)
(one full JSON per portfolio scored) and landed under the E5 research-result
contract at
[`research/results/_unattributed/w6-prop-candidates-session.jsonl`](../../../research/results/_unattributed/w6-prop-candidates-session.jsonl)
(0 validation problems).

**Scored for a FRESH $5,000 account**, per the operator's 2026-09-27
instruction relayed by the manager: the live `breakout_1` account has only
$24 of headroom above its static-DD floor and is being scored separately.

## 1. Which crypto instruments Breakout actually offers, and their constraints

**Confirmed IN THIS REPO** (`config/prop_rulesets/breakout_routing.yaml`,
operator-confirmed DXTrade symbol mapping): BTCUSDT → BTCUSD, ETHUSDT →
ETHUSD, SOLUSDT → SOLUSD. These are the only three instruments this repo has
a wire-ready mapping for today. `contract_value_usd_per_point` for all three
is the crypto-native default `1.0`, itself flagged in that file as
"VERIFY... at wire time" — never independently confirmed for any symbol.

**NOT WEB/FAQ-CONFIRMED, WEB-ONLY** (Breakout's own `breakoutprop.com/symbols`
and FAQ pages returned HTTP 403 to an unauthenticated fetch on 2026-09-27, so
this is corroborated only by two independent third-party sources —
quantvps.com and proptradingvibes.com, both read 2026-09-27 — the same
confidence tier `docs/integrations/breakout-compliance-2026-06-16.md` already
marks `[WEB; CONFIRM]` rather than `[CONFIRMED — FAQ]`):

- Breakout's crypto product list is broad: ADA, APT, ARB, ASTER, AVAX, BCH,
  BNB, BTC, CRV, DOGE, DOT, ETC, ETH, FIL, HBAR, INJ, LDO, LINK, LTC, OP,
  SOL, SUI, TAO, TRUMP, TRX, UNI, XRP (plus non-crypto SP500/XYZ100 index
  CFDs). **ADA and XRP are offered but have NO confirmed DXTrade symbol
  mapping in this repo** — before either could be wired live, someone needs
  to confirm the venue symbol (by the estabished convention, likely `ADAUSD`
  / `XRPUSD`, dropping the perp `T` suffix the way SOL/ETH/BTC already do)
  and the contract size, in the Breakout terminal, the same way SOL/ETH/BTC
  were confirmed 2026-06-23. **This is a real gap, not a formality**: no
  prop ticket should be emitted for an unmapped symbol without that step.
- Leverage is symbol-tiered: BTC 10x; ETH/SOL/ADA/XRP and similar liquid
  alts ~5x ("second tier"); lower-liquidity altcoins 2x–3x. AVAX's tier is
  NOT stated precisely by either source — it appears in the general product
  list but not by name in either source's leverage breakdown, so its tier is
  UNCONFIRMED (could be the 5x tier or the 2-3x tier).
- Costs (independently corroborates what
  `config/prop_rulesets/breakout.yaml` already had CONFIRMED for swap, and
  is NEW information for commission):
  <!-- population-ok: fee/swap RATES (cost parameters quoted from the venue), not sample statistics with a denominator -->
  **0.04% notional per side (0.08%
  round-trip) commission**, plus the already-known **0.033%/day static
  swap**.
  `scripts/research/prop_ev_sim.py`'s `--costs breakout` default
  (`commission-bps-rt=8.0`, `swap-daily=0.00033`) already matches this
  exactly — it was derived independently and agrees.
- Per-instrument position caps (`breakout.yaml::limits.max_position_pct`)
  remain UNCONFIRMED for every symbol, ours included — still "pull at wire
  time," unchanged by this lane.

**Slippage** on the manual Telegram-ping → human-places-the-trade bridge has
never been measured. Every cost model in this repo (bybit-cost corpus,
`prop_ev_sim.py`'s `--costs breakout` default) carries an inherited 3 bps
round-trip placeholder that predates the prop bridge and is not
bridge-specific. Filed to the pipeline (below) rather than asserted as fact.

## 2. Candidates scored

Per-leg evidence source: `comms/strategy_evidence/runs/2026-09-25/` (latest
committed run for every leg except `trend_donchian_eth_prop`, which has a
2026-09-26 re-run — used instead, since it is the newer window for a
baseline leg). n and window are the evidence record's own (single ~363-day
window per leg, 2025-09-25 → 2026-09-24/25).

**Breakout symbol mapping and platform reachability** (operator directive
2026-09-27: candidates must map to a symbol on Breakout's actual tradable
list, on `breakout_1`'s actual platform — DXTrade, confirmed above):

| leg | symbol | n | honest n (≥20)? | Breakout symbol | DXTrade-reachable? | alone verdict | alone EV p5/p50/p95 ($) | baseline+leg verdict |
|---|---|--:|---|---|---|---|---|---|
| `trend_donchian_sol_prop` (baseline) | SOLUSDT | 65 | yes | SOLUSD | yes — confirmed, already routed (`breakout_routing.yaml`) | — | — | — |
| `trend_donchian_eth_prop` (baseline) | ETHUSDT | 169 | yes | ETHUSD | yes — confirmed, already routed | — | — | — |
| **`xrp_pullback_2h`** | XRPUSDT | 57 | yes | XRPUSD | yes — Breakout crypto-majors list (operator screenshot); not individually DXTrade-confirmed like SOL/ETH | **pass** (seed 20260927) / indeterminate (seed 999, p5 −3.34) | 15.17 / 279 / 996 | indeterminate (−45.0 / −11.2 / 53.5) |
| `ada_pullback_2h` | ADAUSDT | 57 | yes | ADAUSD | yes, same caveat as XRP | indeterminate | −15.65 / 147.7 / 668 | indeterminate (−43.9 / −25.6 / 111.3) |
| `trend_donchian` (BTC) | BTCUSDT | 73 | yes | BTCUSD | yes — confirmed (`breakout_routing.yaml`), not yet routed | indeterminate | −43.43 / 13.5 / 102.4 | indeterminate (−45.0 / −29.0 / 11.5) |
| `trend_donchian_eth_4h`\* | ETHUSDT | 44 | yes | ETHUSD | yes — confirmed | indeterminate | −43.19 / 114.5 / 733.2 | indeterminate (−45.0 / −31.3 / 18.7) |
| `avax_pullback_2h` | AVAXUSDT | 57 | yes | AVAXUSD | yes, same caveat as XRP/ADA | indeterminate | −40.92 / 21.6 / 531.7 | indeterminate (−41.4 / −18.9 / 20.5) |
| `trend_donchian_xrp_4h`\* | XRPUSDT | **16** | **NO** | XRPUSD | yes, same caveat | indeterminate (unmeasured) | −38.05 / 520 / 1817 | indeterminate |
| `sol_pullback_2h` | SOLUSDT | **18** | **NO** | SOLUSD | yes — confirmed | indeterminate (unmeasured) | −45.0 / 157.8 / 1026 | indeterminate |
| `eth_pullback_prop_2h` | ETHUSDT | 70 | yes | ETHUSD | yes — confirmed | indeterminate | −45.0 / −23.0 / 146.1 | **FAIL** (−45.0 / −36.0 / −4.4) |
| baseline alone (today's 2 legs) | — | 234 | — | indeterminate | −42.08 / −23.29 / 61.75 | — |
| `xrp_pullback_2h` + `ada_pullback_2h` (no baseline) | — | 114 | — | indeterminate | −37.96 / −8.8 / 63.2 | — |
| baseline + xrp + ada | — | 348 | — | indeterminate | −45.0 / −21.5 / 31.5 | — |

\* `trend_donchian_eth_4h`/`trend_donchian_xrp_4h`'s evidence-record
`timeframe` field reads `"1h"` despite the filename's `_4h` suffix — an
unresolved discrepancy against how these legs are documented elsewhere
(bybit_2 roster notes call them 4h). Not investigated further this lane;
flagged rather than silently trusted either way (field-vs-label, RULE ONE).

## 3. Findings — stated plainly, including the negative ones

1. **No candidate, alone or added to the baseline, ROBUSTLY clears
   RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2's PASS bar.** `xrp_pullback_2h` alone
   is the closest — it PASSED under the lane's primary seed
   (evidence-CI p5 = +$15.17) but flipped to `indeterminate` under a second
   seed (p5 = −$3.34), a few dollars on either side of zero. **Read this as
   "promising, at the edge, not decision-grade" — not as a pass.** Per RULE
   ONE / "never lower a pre-registered bar to manufacture a verdict," this
   lane is NOT rounding a borderline result up to a Tier-3 proposal.
2. **`eth_pullback_prop_2h` stays excluded, and the fresh evidence makes the
   case stronger, not just unchanged.** Per the W6 brief's own instruction
   (reconsider only on a retune clearing its own base): the 2026-09-25
   re-run of the SAME config still reads `net_total_r −3.52` (worse
   directionally than flat) — no retune was run or is proposed. Under
   `prop_ev_sim.py` it is the only candidate that actively flips the
   baseline from `indeterminate` to **FAIL** when added (CI entirely
   negative, −$45.0/−$36.0/−$4.4). `config/strategies.yaml` already carries
   `execution: shadow` on this leg (field-checked, not assumed) — no ticket
   is emitted today; this finding is a reason to leave it that way, not new
   information the live system needs acted on.
3. **Diversifying did NOT help.** `xrp_pullback_2h` + `ada_pullback_2h`
   together score WORSE than `xrp_pullback_2h` alone (CI widens back to
   straddling zero), and adding both to the baseline is still
   `indeterminate` with a lower median than either half alone. B6's own
   note says correlation is the mechanism that should RAISE survival on
   this account; empirically, for this specific pair, it does not — the two
   alt-pullback legs likely share enough regime exposure that pooling them
   onto one shared daily-loss/DD budget costs more in concurrent risk than
   it buys in path smoothing. Reported because it contradicts the a priori
   expectation, not because it was hoped for.
4. **Two candidates are UNMEASURED, not merely weak**: `sol_pullback_2h`
   (n=18) and `trend_donchian_xrp_4h` (n=16) are both below the backtesting
   skill's own ~20-trade honesty floor. Their point-estimate medians look
   striking ($157.8 and $520) — **do not read those as evidence.** A thin
   sample in this repo has been optimistic before (macro M3: 0.7364 at n=11
   vs 0.5885 at n=1263). These need a longer window before they mean
   anything.
5. **Net disposition: zero Tier-3 roster-addition proposals from this
   lane.** Filing a "viable candidate" that does not clear its own
   pre-registered bar would be exactly the failure RULE ONE's worked
   examples warn about. Instead, filed to the pipeline (below) as research
   follow-ups: extend `xrp_pullback_2h`'s window to resolve the
   seed-sensitivity, and confirm the ADA/XRP DXTrade mapping so that IF a
   future re-run does clear the bar, wiring is not blocked on a missing
   symbol confirmation.

## 4. E16 mechanics check — "no prop fill since 2026-08-30"

**This finding is RELAYED, not independently pulled by this lane.** Per the
manager's 2026-09-27T07:24Z check-in (routine `trig_014urxXpM5pbhvzfjSVoYPry`,
sourced from a fresh operator report): fill #45 (ETH short 1.3 @ 2666.11 →
2711.10, −$58.49 gross, 2026-09-25T09:28Z, stop hit) had gone unreported,
and a fresh account status (#21, 2026-09-27T07:23Z) reads balance $4,724,
flat, $24 above the $4,700 static-DD floor. The unlogged close had been
suppressing new ETH prop tickets (the suppression is
`_reticket_suppress_reason` in `src/prop/breakout_executor.py` — an
already-open-looking position blocks a fresh one); a ticket
(`prop-manual-c02321dfa3c2`, 2026-09-26 20:45Z) was in fact emitted once the
prior fill was reported and the position read flat again.

**Disposition: tickets ARE being emitted and fills ARE logged when the
operator reports them. The 2026-08-30 gap was an UNREPORTED CLOSE, not a
defect in the ticket pipeline.** This is consistent with, not a
contradiction of, checklist row E17 (already `done`: the underlying
election-sort-key bug that stranded `breakout_1` from receiving intents at
all was fixed and observed flowing again 2026-09-22/25). This lane did not
independently re-pull `/api/bot/prop/tickets` or `/prop/status` — the
manager's instruction was explicit ("record that as your E16 finding rather
than re-deriving it") — so this section states what was relayed and marks
it as such rather than as this lane's own diag read.

## 5. Non-crypto candidates — WITHDRAWN 2026-09-27 ~12:30Z, not reachable on `breakout_1`

> ⚠️ **Every candidate in this section is DISQUALIFIED, not proposed.**
> `breakout_1` is confirmed DXTrade (PR #13124); S&P500, XYZ100, SILVER and CL
> are confirmed Terminal-exclusive, first-party (S7,
> `intercom.help/breakoutprop/en/articles/16188026`: *"available exclusively
> on the Breakout Terminal and are not available on DXTrade"*). None of
> `spy_trend_long_1d`, `spy_pullback_1h`, `qqq_trend_long_1d`,
> `qqq_pullback_1h`, `tqqq_trend_long_1d`, `qld_trend_long_1d`,
> `slv_pullback_1d`, `uso_trend_1h`, or `mes_trend_long_1d` can be routed to
> `breakout_1` as it exists today. The scoring below is kept as a record of
> work done (and as input if a *separate* Breakout Terminal account is ever
> opened) — it answers "how would this signal perform on that CFD", not
> "should this be added to `breakout_1`". Treat every "clears the bar" verdict
> in this section as **not a `breakout_1` candidate**, per the operator's
> correction at the top of this document.

**NEW PRIORITY, operator direction relayed 2026-09-27T08:29Z**: before widening crypto
past SOL/ETH, check whether anything already running on `alpaca_paper`/`alpaca_live`/`ib_paper`
maps onto Breakout's non-crypto CFDs (traded on the "Breakout Terminal", not DXTrade).
Confirmed 2026-09-27 (`config/prop_rulesets/breakout.yaml`'s comment trail + the operator's
transcription, cross-checked): Breakout Terminal instruments carry the SAME 0.04%/side
commission and 0.033%/day financing as the DXTrade crypto pairs, but the financing is
DEDUCTED every 4 hours instead of once daily at UTC midnight — `scripts/research/prop_ev_sim.py`
already has a lever for exactly this distinction (`--swap-model prorated` vs the crypto legs'
`--swap-model dxtrade`), used throughout this section rather than re-deriving a second cost
model.

**Instrument mapping and basis caveats — read before trusting any number below:**

| Breakout symbol | our leg(s) | instrument today | basis relationship | material gap |
|---|---|---|---|---|
| S&P500 | `spy_trend_long_1d`, `spy_pullback_1h` | SPY ETF (alpaca_paper) | SPY tracks the S&P 500 index within its ~0.09%/yr expense ratio; for short-horizon swing R-multiples this is a close proxy | SPY only trades/gates on US cash-session hours (`market_hours us_equity`); a Breakout S&P500 CFD trades far closer to 24h — after-hours gap risk this evidence never saw is unpriced |
| S&P500 | `mes_trend_long_1d` | MES micro futures (ib_paper) | MES **is** an S&P-500-tracking instrument (not an ETF proxy) — the repo's own leg comment already validates it against "SPX500-CFD" as the same underlying (docs/research/overnight-strategy-research-2026-06-01.md) | live MES history is short (~16 months per that same comment); this evidence window is a fraction of that |
| XYZ100 | `qqq_trend_long_1d`, `qqq_pullback_1h`, `tqqq_trend_long_1d`, `qld_trend_long_1d` | QQQ / TQQQ (3x) / QLD (2x) ETFs (alpaca_paper) | XYZ100 = Nasdaq-100 index CFD, WEB-corroborated by two independent sources (2026-09-27) — Breakout's own page could not be fetched (see §6). QQQ tracks Nasdaq-100 closely; TQQQ/QLD are LEVERAGED ETFs whose daily-reset compounding path diverges from the raw index over any multi-day hold, so their R-multiples are NOT a clean XYZ100 proxy — reported for completeness, not treated as decision-grade for the unleveraged CFD | same US cash-session gating as SPY |
| SILVER | `slv_pullback_1d` | SLV ETF (**alpaca_live real money**, also alpaca_paper) | SLV tracks spot silver minus a small (~0.50%/yr) management/storage fee — close proxy for short holds | same cash-session gating; SLV is already REAL MONEY on alpaca_live today, which the fresh-$5k-account framing here does not touch |
| CL (crude oil) | `uso_trend_1h` | USO ETF (alpaca_paper) | ⚠️ **USO is a POOR proxy for CL/WTI.** USO's methodology rolls near-month futures and has a well-documented history of diverging materially from spot/futures WTI under contango (most visibly during the 2020 negative-WTI episode, when USO restructured its holdings across the curve). This is a structural, not cosmetic, basis gap — the R-series below reflects USO's ETF-roll behavior, not CL futures/CFD behavior, and should not be read as evidence for the CL CFD without independently re-pricing against CL futures data | same cash-session gating |

**Scored** (fresh $5,000 account, `--swap-model prorated`, same
`RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2` bar; evidence source
`comms/strategy_evidence/runs/2026-09-24-e55/` except `qqq_trend_long_1d`
which only has a 2026-09-24 run):

| leg | n | honest n (≥20)? | alone verdict | alone EV p5/p50/p95 ($) | baseline+leg verdict |
|---|--:|---|---|---|---|
| `uso_trend_1h` | 56 | yes | **PASS** | 42.7 / 395.3 / 1055.1 | indeterminate (−39.8 / 16.0 / 193.8) |
| `qqq_pullback_1h` | 55 | yes | indeterminate | −45.0 / −3.5 / 266.5 | indeterminate (−39.9 / −22.0 / 38.5) |
| `spy_pullback_1h` | 61 | yes | indeterminate | −45.0 / −41.4 / 17.4 | **FAIL** (−45.0 / −41.4 / −25.4) |
| `slv_pullback_1d` | 11 | **NO** | indeterminate (unmeasured) | −45.0 / −6.9 / 290.0 | indeterminate |
| `spy_trend_long_1d` | 11 | **NO** | indeterminate (unmeasured) | −45.0 / −43.6 / 1.8 | indeterminate |
| `tqqq_trend_long_1d` | 12 | **NO** | indeterminate (unmeasured) | −45.0 / −37.6 / 105.1 | indeterminate |
| `qld_trend_long_1d` | 9 | **NO** | indeterminate (unmeasured) | −45.0 / −19.9 / 211.1 | indeterminate |
| `qqq_trend_long_1d` | **5** | **NO** | fail (unmeasured — do not trust) | −45.0 / −45.0 / −20.3 | indeterminate |
| `mes_trend_long_1d` | **5** | **NO** | fail (unmeasured — do not trust) | −45.0 / −45.0 / −16.5 | indeterminate |

**Findings:**

1. **`uso_trend_1h` alone clears the PASS bar cleanly and by a wide margin** (evidence-CI
   entirely positive, p5 = +$42.7) — a stronger, less borderline result than any crypto
   candidate in §2. **But this is exactly the leg with the worst basis caveat**: it is
   priced off USO's ETF-roll behavior, which is known to diverge from actual CL futures
   under contango. This result is NOT read as "CL clears the bar" — it is read as "USO's
   *own* historical path clears the bar," which is a different and much weaker claim.
   Filed to the pipeline (below) as a naming-the-gap follow-up: re-price against CL
   futures/CFD data before treating this as decision-grade for Breakout's CL symbol.
2. **`spy_pullback_1h` flips the baseline to FAIL when added** — the same shape as
   `eth_pullback_prop_2h` in §3: a leg whose own alone-evidence already leans negative
   (median −$41.4) makes the combined book actively worse, not merely neutral.
3. **Five of nine legs are below the 20-trade honesty floor** (n=5 to n=12) — this
   reflects that most of these ETF/futures legs are daily-bar strategies on a ~1-year
   window, which structurally caps trade count. `mes_trend_long_1d` and
   `qqq_trend_long_1d` at n=5 read "fail" from the tool, but **n=5 is not evidence of
   anything** — do not act on either verdict.
4. **Leveraged-ETF legs (`tqqq_trend_long_1d`, `qld_trend_long_1d`) are reported but not
   decision-grade for XYZ100**: their daily-reset compounding means their multi-day R
   distribution is a property of the leveraged ETF, not of the underlying Nasdaq-100
   index a CFD would track.
5. **Correlation risk, stated rather than measured this lane** (no combo run of
   multiple index-linked legs together — out of this lane's remaining time budget):
   `spy_*`, `qqq_*`, `tqqq_*`, and `qld_*` are all large-cap-US-equity-beta legs: S&P500
   and XYZ100 (Nasdaq-100) are themselves highly correlated indices. Combining several
   of them on one $5k book concentrates one macro factor, echoing §3's xrp+ada finding
   that pooling correlated legs onto one shared drawdown budget can make the combined
   book WORSE, not better — the manager's own instruction to "watch the correlation
   between S&P500 and XYZ100" is right to flag this before any Tier-3 proposal, and it
   remains unanswered by this lane's numbers, not resolved by them.
6. **`slv_pullback_1d` is already REAL MONEY on `alpaca_live` today.** This lane's
   fresh-$5k framing does not bear on that live position at all — it only asks "if this
   same leg's signal history were replayed on a *fresh* Breakout account, would it
   clear the bar" — and at n=11 the honest answer is "not enough evidence either way."

## 5a. "Crypto beyond SOL/ETH" — checked against §6's full symbol list, nothing new to score

The manager's re-prioritized order of work named this step 2, after non-crypto. **Checked
this lane** (`grep` over `config/strategies.yaml` and `comms/strategy_evidence/` for every
crypto ticker in §6's instrument table beyond the six already scored in §2): **no strategy
config and no evidence record exists for any of the ~40 remaining Breakout-listed crypto
symbols** — BNBUSD, HYPEUSD, TRXUSD, AAVEUSD, FILUSD, ONDOUSD, DOGEUSD, LINKUSD, LTCUSD,
SUIUSD, UNIUSD, ZECUSD, or any of the smaller alts. This is a negative result stated with
its probe, not a silent skip: this repo has never built a `trend_donchian`/pullback variant
for any of them, so there is no per-trade R-series to reprice or score — that would be new
strategy authoring (`new-strategy` skill scope, off-the-shelf data acquisition included),
not candidate scoring, and is out of this Tier-1 research lane's scope. §2's 8 crypto
candidates (BTC, ETH-4h, XRP-4h/pullback, ADA-pullback, SOL-pullback, AVAX-pullback) are
therefore the FULL set of "crypto beyond SOL/ETH" this lane can check without building new
strategies first.

## 6. Instrument list verification

The operator supplied a transcription of `https://www.breakoutprop.com/symbols/` (screenshot,
2026-09-27) and separately asked this lane to verify it against the live page. **The live page
could not be fetched from this sandbox — confirmed, not merely attempted-and-skipped**: both
`WebFetch` and a direct `curl` (with a browser User-Agent, through the environment's proxy) on
`https://www.breakoutprop.com/symbols/` at 2026-09-27T08:39:05Z returned HTTP 403 with response
header `cf-mitigated: challenge` — Cloudflare's interactive bot-challenge, which no non-browser
HTTP client can pass. This is a verifiable technical block, not an assumption: the header is
in the raw response. Every figure attributed to the operator's screenshot below is marked
**`[operator screenshot, unverified against the live page]`** per that fallback, and the two
that were independently corroborated via `WebSearch` (a different retrieval path than the
blocked direct fetch) are marked accordingly. Filed to the pipeline (below) so a session with a
real browser or a different network path re-attempts the live fetch.

See [`docs/integrations/breakout-instruments-2026-09-27.md`](../../integrations/breakout-instruments-2026-09-27.md)
for the full committed instrument table.

## 7. Pipeline follow-ups filed

See `docs/claude/work/pipeline/` for the individual records (ids below).
Each carries `due_when` + `origin.rerun` per the pipeline schema.
