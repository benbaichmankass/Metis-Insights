# JC-SA-08 — do Stage-2 Alpaca mirror exits match the live exit? (evidence, 2026-09-29)

> **Doc status:** `unknown` · category `evidence` · measured `2026-09-29T11:5xZ` · lane FIX-SA-03 (session_01Pr6YRTQYauPEEkK1xNyyAQ) for manager session_01HYq6XtfesZ57VyaQrK6CL1 · audit item JC-SA-08 (`docs/audits/system-audit-2026-09-29.md`, PR #14108)

## Question
On Stage-2 Alpaca, how often does the paper mirror (`alpaca_portfolio`) not take the identical exit the live account (`alpaca_live`) took, and why?

## Sources (all read this session)
- **Journal:** `/api/diag/journal?table=trades&limit=500` (ids 5787–6286+).
- **Venue:** `/api/diag/alpaca_order_history?account_id=<acc>&symbols=<SYM>&after=&until=`. This is the route added in #14115, deployed 2026-09-29 and first read at 11:45Z. It carries Alpaca's own `status`, `filled_at`, `filled_avg_price` and `stop_price` for each order.

## Population
Every `alpaca_live` trade with `closed_at >= 2026-09-21` (the strict Stage-2 roster date): **n = 3**. A mirror twin is the `alpaca_portfolio` row with the **same `strategy_name`** and symbol that was open over the same interval.

| live id | strategy | live exit (venue) | mirror twin | mirror exit (venue) | gap (mirror − live) |
|---|---|---|---|---|---|
| 5875 | slv_pullback_1d, SLV long 1 | GTC **OCO stop leg** (stop 55.41) filled **13:30:03.144Z** @ **55.3132** (17.5 bps through the stop). Journal: `exchange_flat_reconciled` | 5874 (471 sh) | the **same-level GTC OCO stop leg** (55.41) filled **13:35:06.113Z** @ **55.38189** (5.1 bps through). Journal: `sl_cross` @ 55.38189 | **+12.4 bps** (mirror better), **+5 min 03 s** later |
| 6097 | iaum_pullback_1d, IAUM long 2 | GTC OCO stop leg (41.98) filled 13:30:02.867Z @ 41.2732 (**168.4 bps** through, gap at the open). Journal: `exchange_flat_reconciled` | **none, by design**: IAUM is a declared affordability proxy dropped from the mirror (`test_alpaca_portfolio_mirrors_alpaca_live_exactly_minus_proxies`) | — | — |
| 5924 | tlt_pullback_1h, TLT long 1 | GTC OCO stop leg, moved 81.42 → 81.74 by PATCH (old leg `replaced`), filled 2026-09-22T18:29:09.448Z @ 81.7432. Journal: `sl_cross` @ **81.73**, 1.6 bps off the venue fill | **none**: `tlt_pullback_1h` is not on `alpaca_portfolio`. The mirror's TLT position is `tlt_pullback_1d`, **short** (5912), a different leg. The live trade was opened 09-18, before the 09-21 roster cut. | — | — |

**Twin pairs: 1 of 3. Divergent: 1 of 1.** The other two live closes have no twin, one by the declared proxy carve-out and one by the roster cut.

Context from the soak book: `alpaca_paper` 5873 (SLV, same strategy, 307 sh) was filled by the same-level OCO stop at 13:34:07.134Z @ 55.395342, **+14.8 bps** vs live, +4 min 04 s.

## Mechanism of the one divergence
- **It is not an app-side exit.** The audit's premise was "mirrors closed SLV app-side by `sl_cross`". The venue record shows both mirrors' exits were **Alpaca venue fills of the same GTC OCO stop leg** that closed the live position, at the same stop price (55.41). `sl_cross` is only the label the journal gave the row. The prices match the venue fills exactly.
- **It is not the re-arm cancel window.** All three stops had rested for days as GTC OCOs (re-armed 09-17 and 09-23). The FIX-SA-03 cancel-then-POST gap is ≤ 0.50 s and happened at entry, not at exit.
- **It is paper-venue fill simulation vs the live market.** The same stop, triggered by the same opening gap, filled on live at the first print (13:30:03, 17.5 bps through the stop). Both paper books filled 4–5 min later and closer to the stop (2.6–5.1 bps through). The live account took the real gap-through, and the paper engine did not reproduce it.

## Is it systematic?
- **Not established. n = 1 twin pair**, and one is not a rate.
- The mechanism is structural, though: every Stage-2 Alpaca stop exit is a live venue fill vs a paper-engine fill.
- In this one case, both paper books (portfolio and soak) filled **better** than live, by 12.4 and 14.8 bps. If that sign holds, the mirror understates live exit cost on gap-through stops. That understatement is exactly what Gate 2's demotion signal must not do.
- **What would settle it:** extend the population to every Alpaca stop exit where the live account and a paper book held the same strategy × symbol, back to 2026-09-01, pre-strict roster included. The mechanism does not depend on the roster. This is filed as a research question, not a decision.

## Side findings (filed)
1. **Venue stop fills journaled `exchange_flat_reconciled` on alpaca_live** (5875, 6097). Alpaca records no `sl_order_id` (NULL on 20/20 alpaca rows since 09-15), so the reconciler cannot attribute the close. The venue history now shows the OCO stop leg filled. This is the Alpaca half of FIX-SA-02, whose row names bybit_2 only.
2. **Paper books uncovered overnight after day-TIF legs lapsed** (6023 QQQ 22 sh, 6024 QQQ 56 sh, 6131 SPY 8 sh):
   - Their entry-bracket legs expired or cancelled at 20:00Z on the entry day. The legs were day-TIF, before the 09-27 GTC change.
   - Nothing re-armed them, because a sibling row's OCO on the same symbol made `protection_state` read `stop: True` at symbol level.
   - This is the known quantity-coverage gap (`partially_naked` is detected and never re-armed).
   - #14127's same-size/unknown-size logic does **not** change this case: that logic acts only inside a re-arm, and here no re-arm is ever called.
