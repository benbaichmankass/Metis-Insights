# WS-A S3b — Fee/Commission Headroom (2026-06-02)

> Meantime Expansion Program, WS-A S3b. Quantifies the last autonomous
> unknown: how much real round-trip commission the two S3 passers can
> absorb before the edge dies. Driver:
> `scripts/research/ws_a_s3b_fee_breakeven.py` (trainer-vm-diag #2638).

## Result — both passers are deeply fee-robust

| round-trip bps | Copper/pullback net-R | Gold/pullback net-R |
|---|---|---|
| 0 | +88.5 | +63.6 |
| 2 (modelled) | +87.6 | +61.7 |
| 7.5 | +85.1 | +56.4 |
| 15 | +81.8 | +49.2 |
| 30 | +75.1 | +34.7 |

**Breakeven cost: >30 bps round-trip for both** — the edge survives the
entire grid. Copper barely degrades (−15% net-R from 0→30 bps); Gold
keeps ~55% of its edge even at 30 bps.

## Why, and what it means

These are **daily** swing systems (n=145 / 189) capturing multi-day moves
with large R per trade, so per-trade commission drag is negligible
relative to the move. The 2.0 bps placeholder that caveated S1–S3 is
therefore **not a load-bearing risk** — even pessimistic NinjaTrader micro
(MGC/MHG) commissions sit far below the 30 bps headroom. Commission is
removed from the WS-A risk list.

## WS-A risk ledger — final state

| Risk | Status |
|---|---|
| Is the edge real (not luck)? | ✅ Resolved — S3 bootstrap p05 exp > 0 (both) |
| Does it generalize / overfit? | ✅ Resolved — pullback wins on both metals, same param neighborhood, broad grid plateau (S2) |
| Commission sensitivity | ✅ Resolved — survives >30 bps (this doc) |
| Continuous-contract (`=F`) roll artifact | ⏳ Open — needs roll-adjusted data (autonomous, but better via IBKR) |
| Live forward performance | ⏳ Open — demo-execute ladder, needs a futures paper venue |
| Real per-contract commissions | ⏳ Low-priority — headroom is large; verify at venue wiring |
