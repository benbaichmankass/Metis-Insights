🔧 **CORRECTION to my MI-107 post above** ([#10980](https://github.com/benbaichmankass/Metis-Insights/pull/10980)) · session_018zzzSLT8UdJ1e1RbFeyvsz

I posted that `turtle_soup` **"has been shadow since 2026-04-29 — four months"** and called it **the oldest** shadow leg. **Both halves were wrong**, and I caught it verifying my own output rather than being told.

**What I actually did wrong:** I derived the date from `git log -S"turtle_soup" -- config/strategies.yaml | tail -1`, which finds when the string was **first added to the file** — i.e. when the leg was *created*, not when it was *demoted*. It never read the `execution:` value at all.

**Re-measured properly**, by walking each leg's `execution:` value through every commit that touched `config/strategies.yaml`:

| leg | shadow since | days @ 2026-09-04 |
|---|---|---|
| `fade_breakout_4h` | 2026-06-01 | **95** |
| `mgc_trend_1h` | 2026-06-18 | 78 |
| `turtle_soup` | **2026-07-07** | **59** |
| `avax_pullback_2h` | 2026-07-23 | 43 |
| `fvg_range_15m` | 2026-07-28 | 38 |
| `htf_pullback_trend_2h` | 2026-08-23 | 12 |
| `eth_pullback_prop_2h` | 2026-08-23 | 12 |
| `slv_trend_1h` | 2026-08-24 | 11 |

So `turtle_soup` is **59 days, not ~128**, and is **third**, not oldest. `fade_breakout_4h` at 95 days is the oldest.

**The argument is unchanged and slightly stronger:** 8 of 52 enabled legs sit in `shadow` between 11 and 95 days and **not one of the eight carries a recorded exit condition** — which is precisely why (c) was built first. `turtle_soup` remains the sharpest *exhibit* (zero closes, routed to zero accounts, surfaced only because a different mechanism flagged it), just not the oldest.

Corrected in `docs/design/strategy-demote-and-tune-DESIGN.md`, `docs/claude/SUNSET-DISPOSITIONS.json` and the MI-107 note in `MANAGER-CHECKLIST.json`. **The counts that carry the actual finding — all ten candidates at `lifetime_closed_trades: 0`, none routed to real money — were read from `comms/sunset/2026-09-01/INDEX.json` directly and are unaffected.**
