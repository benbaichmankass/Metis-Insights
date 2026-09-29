# JC-SA-08 population (since 2026-09-01) and the re-arm-through-a-breached-stop loop

> **Doc status:** `unknown` · category `evidence` · measured `2026-09-29T14:1xZ` · lane FIX-SA-03 (session_01Pr6YRTQYauPEEkK1xNyyAQ) for manager session_01HYq6XtfesZ57VyaQrK6CL1 · closes the population question of `PI-20260929-PR6YRTQY-0004`; follows `jc-sa-08-alpaca-mirror-exit-divergence-2026-09-29.md`

## Sources (read this session)
- **Journal:** `/api/diag/journal?table=trades&limit=1000&offset={0,1000}&envelope=true`. This covers ids 4287–6286+ and reaches back to 2026-07-31.
- **Venue:** `/api/diag/alpaca_order_history?account_id=<acc>&after=2026-09-01T00:00:00Z&limit=500`. It was read for all three alpaca accounts and paged to exhaustion:
  - alpaca_live: 6 orders
  - alpaca_portfolio: 123 orders
  - alpaca_paper: 85 orders

## 1. Live↔paper exit pairs since 2026-09-01
There are **3 `alpaca_live` closes** since 2026-09-01: 5924 TLT, 6097 IAUM and 5875 SLV. There are no others in either journal page.

| live | paper counterpart(s) |
|---|---|
| 5875 SLV `slv_pullback_1d` | alpaca_portfolio 5874 and alpaca_paper 5873 (measured in the first evidence file) |
| 6097 IAUM `iaum_pullback_1d` | none: no alpaca_paper row on IAUM at that time, and IAUM is a mirror proxy |
| 5924 TLT `tlt_pullback_1h` | none: no paper row of that leg open at the time |

**Pairs: 1 live exit with 2 paper counterparts, and it is the one already reported.** Moving the window back to 09-01 adds none. The data does not exist, so the (a)/(b) decision cannot be settled from history. The only way to get the pairs is to **accrue** them. Every alpaca_live stop exit that has a paper twin is a sample, and PR6YRTQY-0004 stays open as that accrual.

## 2. The mechanism, from every venue STOP fill since 2026-09-01
Slippage here is signed: `(fill − stop)/stop` in bps, with negative meaning worse than the stop. It covers every filled stop-type leg (bracket, OCO or standalone).

| book | stop fills | median | mean | 2026-09-28 opening gap |
|---|---|---|---|---|
| alpaca_live | 3 | −17.5 | −61.8 | SLV −17.5 (13:30:03), IAUM −168.4 (13:30:02) |
| alpaca_portfolio (excl. the §3 loop) | 14 | −0.65 | −22.3 | GDX −233.9 (13:34:58), SLV −5.1 (13:35:06) |
| alpaca_paper | 8 | −0.85 | −33.0 | GDX −254.4 (13:34:15), SLV −2.6 (13:34:07) |

What this shows:
- **The paper engine does reproduce gap-through.** GDX filled 234–254 bps through its stop on both paper books.
- **Its stop fills land 4–5 min after the live opening print.** On 09-28, every paper stop fill landed between 13:34:07 and 13:35:06, while live filled at 13:30:02–13:30:03.
- **So the SLV divergence is TIMING.** The paper engine triggers and fills later than the live venue, and on 09-28 that later price happened to be better. It is not a consistently favourable paper fill.
- With 3 live fills, the sign of the gap is **not established**.

## 3. FINDING: a re-arm loop liquidated a sibling's position through a stop already breached (alpaca_portfolio, 2026-09-24)
Venue record for alpaca_portfolio QQQ on 2026-09-24: a **2-share** sell stop at **736.35** filled **29 times** between 13:32:10 and 15:29:42, for 58 shares in total. That is exactly the netted position: 5928 (2 sh) plus 6024 (56 sh).

- **13:32:10.** 5928's own GTC OCO stop fills, genuinely. The position is now 56 sh, all of it 6024's.
- **Why 6024 read as naked: it WAS naked.** 6024's entry-bracket legs lapsed at the 2026-09-21 close (day TIF): take-profit 1989e18b… `expired`, stop 1445d7ee… `canceled` at 20:01:43Z. From then on 6024 had no resting stop of its own, and 5928's OCO was the only protection on the symbol.
- **The loop.** Journal row 5928 was still `open`, so the naked sweep re-armed **5928** each time: a 2-share OCO at 736.35, with QQQ already below 736.35. Every re-arm stop was marketable on arrival and filled at once. That happened **28 more times**, about every 2 minutes, and each re-arm's pre-cancel removed whatever had been placed.
- **Result.** 6024's 56 shares were sold at **734.96–736.42**, far above 6024's own stop at 716.80, with no strategy exit. Both rows then closed `exchange_flat_reconciled` at 15:33:36. The journal's `protection_repairs = 29` on 5928 is this loop.

### What #14123/#14127/#14177 change, and what they do not
- **#14123 would NOT have prevented this instance.** (An earlier draft of this file claimed it would; that was wrong.) 6024 had no resting stop to make visible, so the symbol still reads stop-naked and 5928 is still re-armed.
- **The missing guards, now built in #14241.** Nothing checked, before re-arming a row, that:
  1. the venue position still holds the row's quantity on the row's side. A row whose own stop has filled is gone at the venue, even though the journal still says open; or
  2. the re-arm stop is not already through the market. A marketable stop is an immediate exit, not protection.
- **Still reachable on alpaca_live.** The live account holds one row per symbol, so the multi-row path above cannot happen there. But a live row whose stop fills while the journal still shows it open would be re-armed as a **sell OCO on a flat position**. That risks an unintended short, if the venue accepts it, or a refused order every tick.
- **Why it has not happened on live yet.** The 09-28 live stop fills were followed by the rows closing within about 3.5 min, and the venue history shows no re-arm order after them. Why the sweep did not fire in that window is **not established** in this file.

Filed as `PI-20260929-PR6YRTQY-0005` (Tier 2, high). Fix: #14241 (held), which replays this sequence in `tests/test_alpaca_rearm_preflight.py`.
