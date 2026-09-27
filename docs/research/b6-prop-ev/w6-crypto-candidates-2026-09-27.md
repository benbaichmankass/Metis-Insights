# W6 — crypto-leg candidates for `breakout_1`, scored against the B6 EV bar

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Lane: W6 (checklist row B6), dispatched by manager session
> `session_01Ljhs6sFAdWHdMDhJpL5aBP`. Tier-1 research — no roster, execution,
> sizing or config change. Adding a leg to `breakout_1` is Tier-3 and this
> document PROPOSES, it does not decide.

## 0. What this answers, and what tool scored it

Row B6 ([`docs/claude/work/MANAGER-CHECKLIST.json`](../../claude/work/MANAGER-CHECKLIST.json))
is the operator's EV bar: *"we want to promote strategies that create a
portfolio that we would statistically expect to produce a profit (over the
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
  is NEW information for commission): **0.04% notional per side (0.08%
  round-trip) commission**, plus the already-known **0.033%/day static
  swap**. `scripts/research/prop_ev_sim.py`'s `--costs breakout` default
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

| leg | symbol | n | honest n (≥20)? | alone verdict | alone EV p5/p50/p95 ($) | baseline+leg verdict |
|---|---|--:|---|---|---|---|
| `trend_donchian_sol_prop` (baseline) | SOLUSDT | 65 | yes | — | — | — |
| `trend_donchian_eth_prop` (baseline) | ETHUSDT | 169 | yes | — | — | — |
| **`xrp_pullback_2h`** | XRPUSDT | 57 | yes | **pass** (seed 20260927) / indeterminate (seed 999, p5 −3.34) | 15.17 / 279 / 996 | indeterminate (−45.0 / −11.2 / 53.5) |
| `ada_pullback_2h` | ADAUSDT | 57 | yes | indeterminate | −15.65 / 147.7 / 668 | indeterminate (−43.9 / −25.6 / 111.3) |
| `trend_donchian` (BTC) | BTCUSDT | 73 | yes | indeterminate | −43.43 / 13.5 / 102.4 | indeterminate (−45.0 / −29.0 / 11.5) |
| `trend_donchian_eth_4h`\* | ETHUSDT | 44 | yes | indeterminate | −43.19 / 114.5 / 733.2 | indeterminate (−45.0 / −31.3 / 18.7) |
| `avax_pullback_2h` | AVAXUSDT | 57 | yes | indeterminate | −40.92 / 21.6 / 531.7 | indeterminate (−41.4 / −18.9 / 20.5) |
| `trend_donchian_xrp_4h`\* | XRPUSDT | **16** | **NO** | indeterminate (unmeasured) | −38.05 / 520 / 1817 | indeterminate |
| `sol_pullback_2h` | SOLUSDT | **18** | **NO** | indeterminate (unmeasured) | −45.0 / 157.8 / 1026 | indeterminate |
| `eth_pullback_prop_2h` | ETHUSDT | 70 | yes | indeterminate | −45.0 / −23.0 / 146.1 | **FAIL** (−45.0 / −36.0 / −4.4) |
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

## 5. Pipeline follow-ups filed

See `docs/claude/work/pipeline/` for the individual records (ids below).
Each carries `due_when` + `origin.rerun` per the pipeline schema.
