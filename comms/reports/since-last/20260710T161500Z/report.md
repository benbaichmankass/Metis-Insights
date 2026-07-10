# System report — since-last

- Generated: 2026-07-10T16:15:00+00:00
- Window: 2026-07-08T07:47:00+00:00 → 2026-07-10T16:15:00+00:00
- Roll-up grade: healthy

Core system healthy. Prop pipeline verified sound — the 'no prop trades' was market conditions (SOL/ETH range-bound + chop), not the notification consolidation. Shadow-prop 'orphaned' mislabeling fixed (PR #6103). ETF pullback cells backtest-confirmed edge — no demotes.

## P&L by class
- **real**: window +$5.07 (prior —, up)
- **paper**: window $-6,628.22 (prior —, down)
- **prop**: window +$0.00 (prior —, flat)

## Operator priorities
1. Prop pipeline healthy — no missed trades — Last real prop ticket 2026-07-06 (eth_pullback_2h ETH long), filled + closed 07-08 at SL (-$78.76), clean report-back; reconcile 0 unacted. All 4 live prop strategies evaluate every bar but SOL/ETH have been range-bound (Donchian no-breakout) / chop (ADX ~20, no pullback setup) — no entry setup. Notification consolidation (#5967) only touched the monitor pulse; emit path byte-identical. Nothing missed.
2. Shadow-prop 'orphaned' mislabeling — FIXED — Shadow _prop variants' order-packages were mis-swept 'orphaned' on /prop/tickets. execute_pkg dry branch now stamps status='shadow'. PR #6103 (CI green, draft). Removes a real source of future prop-investigation confusion.
3. ETF pullback cells backtest-confirmed edge — KEEP — gld_pullback_1h + qqq_pullback_1h looked like the worst paper losers (0/9, 0/7) but 9yr net-of-fee backtest shows strong robust edge (GLD +113.6R/exp+0.32; QQQ +90.7R/exp+0.34; both +8/10 years incl. 2024-2026). Live paper 0-win is small-sample + artifact closes. Demote hypothesis REJECTED — kept live.
4. alpaca_live underfunded (gld_pullback dispatch refusals) — alpaca_live balance $149.61 / avail $55.93 can't size GLD (~$376/share) -> recurring 'sized_qty=0' dispatch warning. Operator chose leave-as-is. Not an error (paper leg still places); account-funding issue, orthogonal to strategy edge.
5. SOL/AVAX alt cells lagging on paper — keep soaking — sol/avax pullback_2h + trend_donchian_avax_4h are the persistent per-symbol laggards (SRQ-20260618 cohort); ETH/ADA/XRP siblings net-positive over 30d. Operator chose keep soaking; SRQ items refreshed with 07-10 evidence.

## Review coverage
- Strategy promotion: No promotions or demotes this window. ETF pullback cells: demote hypothesis tested via backtest and REJECTED (strong 9yr edge) -> KEEP. SOL/AVAX alt cells: operator-directed keep-soaking. Formal M7 packets not re-generated this run.
- ML training health: —
- Soak `prop manual-bridge`: — — 0 unacted tickets; account within all rule limits; awaiting next SOL/ETH setup.
- Soak `SRQ-20260618 alt cohort`: — — ETH/ADA/XRP net-positive 30d; SOL/AVAX lagging (watch).
- 🚩 alpaca_live underfunded -> recurring gld_pullback_1h dispatch refusal (operator: leave-as-is)
- 🚩 trend_donchian_avax_4h -$3.7k/0-3 this week (variance-vs-decay watch; SRQ-618)

## Monitoring (soaking / awaiting decision)
- `PROP-SOL-ETH-SETUP` [performance · market-dependent] Watch for the next SOL/ETH breakout/pullback setup to confirm the prop emit->fill->report loop end-to-end on a fresh trade (last confirmed 07-06). (next: next actionable prop signal)
- `SRQ-20260618-001/002` [performance · soaking] SOL/AVAX alt cells accruing paper soak; trend_donchian_avax_4h is the leading demote candidate if the next window confirms. (next: next /performance-review)
- `MB-20260705-TRAINER-OOM` [ml · watch] Trainer swap-thrash recurred 07-10 (auto-recovered via OCI RESET, 8G swap). Watch for repeat; subproc-isolation follow-up open. (next: next /ml-review)

_report_id RPT-20260710-161500-since-last_