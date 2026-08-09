# Sprint Log: S-COLLAPSED-STATES-2026-08-09

## Date Range
- Start: 2026-08-09
- End: 2026-08-09

## Objective
- Primary goal: drive the four deferred Tier-2/3 items from the `/system-review`
  thread to resolution rather than parking them, and settle the open question of
  whether the 307 unresolved fabricated-exit rows are repairable.
- Secondary goals: land the observability that makes each remaining decision an
  informed one instead of a guess; record the negative results so no future
  session re-attempts a dead theory.

## Tier
- **Tier 2** (two live-trader runtime changes + one isolated-order-path change),
  with Tier-1 diagnostics and docs alongside.
- Justification: #8665 touches `src/units/accounts/risk.py` (money path), #8666
  touches the live monitor tick's netting reconciler, #8667 touches the isolated
  pairs order path. **Operator OK obtained in chat before any of the three was
  merged.** No Tier-3 file was touched: no `config/strategies.yaml`,
  `config/accounts.yaml`, `config/risk_caps.yaml`, `src/runtime/orders.py`, or
  `src/runtime/risk_counters.py`.

## Starting Context
- Active roadmap items: none directly — this drains the health-review backlog
  and the residue of the netting/provenance workstream.
- Prior sprint reference: the fills-range-walk + fabricated-exit thread
  (#8621 / #8624 / #8626 / #8628 / #8650).
- Known risks at start: four items were deferred as "Tier-2/3, parked". The
  operator's direction was that parking them was not an acceptable disposition.

## Repo State Checked
- Branch or commit reviewed: `main` at `4199a8c7` → `544ae8e8` at close.
- Deployment state reviewed: `ict-exchange-fills-pull` journal (4 nightly runs,
  08-06 → 08-09) via diag #8670; `backfill-fabricated-exits` dry run via
  system-action #8668.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`.

## Files and Systems Inspected
- Code files inspected: `src/units/accounts/risk.py`,
  `src/runtime/order_monitor.py`, `src/units/strategies/pairs_executor.py`,
  `src/runtime/exchange_funding_puller.py`, `src/runtime/exchange_fills_puller.py`,
  `scripts/ops/backfill_fabricated_exits.py`, `scripts/pull_exchange_fills.py`.
- Config files inspected: none changed. `config/*` untouched throughout.
- Deployment files inspected: `scripts/ops/pull_exchange_funding_action.sh`,
  `.github/workflows/system-actions.yml`.
- Docs inspected: `docs/design/gross-exposure-governance-DESIGN.md`,
  `docs/netting-partial-close-attribution-DESIGN.md`,
  `docs/claude/system-actions.md`, the three review backlogs.
- Services or timers inspected: `ict-exchange-fills-pull.service`.
- GitHub Actions workflows inspected: `system-actions.yml`, `vm-diag-snapshot.yml`.

## Work Completed

**The unifying finding.** All three Tier-2 fixes turned out to be the same
defect: **two states collapsed into one, where the missing state is the
dangerous one.** Each was a control or predicate that quietly switched off the
observation that would have justified using it.

| PR | merged | collapsed | consequence |
|---|---|---|---|
| #8665 | `5c387ba4` | "no policy" = "no data" | the gross-exposure ceiling's own MEASUREMENT was gated on the ceiling being declared, so phase 2 could only ever be a guess — deferred a year, never picked up |
| #8666 | `c0cddd18` | "not staged" = "not observed" | `NETTING_ATTRIBUTION_ACCOUNTS` scoped the whole reconciler pass, so staging on `bybit_1` made real-money `bybit_2` **invisible** — while it was measured non-clean on 2026-08-06 |
| #8667 | `58cf8d83` | "half-open" = "flat" | `_close_pair` leaves a failed leg open, but the tick asked only "are BOTH legs open?" — so a stranded leg read as never-opened and the executor placed a **second pair on top of it** |

- **#8663 (`0bb9432f`, Tier 1)** — per-`(account, symbol)` breakdown on the
  fabricated-exit dry run; funding puller declares the span it was **SERVED**
  beside the one it requested; `days:` made selectable on
  `pull-exchange-funding` (it was hardcoded to 30, which made the one decisive
  measurement impossible to take). The workflow's `ACTION_DAYS` plumbing became
  a list rather than one `if` per action.
- **#8665** — `src/units/accounts/exposure.py`, a new pure module splitting
  observe / policy / verdict. **Enforcement is byte-for-byte unchanged.**
  `report()["exposure"]` is now emitted always, so the exposure multiple is
  readable without declaring a ceiling.
- **#8666** — the allowlist scopes the WRITE only, via one predicate
  `_netting_may_write`. Soak rows carry effective `mode` + `global_mode` +
  `apply_scope`; the summary counts `apply_suppressed_by_allowlist`.
- **#8667** — `_pair_leg_state` returns `open` / `flat` / `half_open`. A
  half-open pair flattens the stranded leg (live only), alerts `pairs_half_open`
  (CRITICAL while naked, WARN once flat), and places nothing that bar.
- **#8671 (`544ae8e8`, Tier 1)** — the negative results, recorded.

## Validation Performed
- Tests run: 24 (`test_risk_gross_exposure`, 12 new) · 20
  (`test_netting_attribution`, 3 new) · 33 (`test_pairs_executor`, 6 new) · 6
  (`test_funding_window_span`, new). 163 passed across the risk-adjacent suite.
  CI green on every merged PR.
- Dry-runs or staging checks: `backfill-fabricated-exits` dry run (#8668) —
  no money-DB write.
- Manual code verification: **every behavioural test was verified to FAIL
  against the restored old behaviour before being accepted.** #8666 had **no
  allowlist test at all** beforehand, which is why the intersection survived its
  original review — a green suite over an untested branch.
- Gaps not yet verified: whether the Bybit funding/transaction-log endpoint
  shares the 7-day range cap. Now *measurable* (see Deferred).

## Documentation Updated
- Rules doc updates: `CLAUDE.md` — `NETTING_ATTRIBUTION_ACCOUNTS` entry
  corrected (it described the old account-scoping, which is the sentence a
  future session would have trusted); pairs-soak event enum gains `half_open`.
- Architecture doc updates: none required.
- Trade pipeline doc updates: not applicable — no pipeline stage changed.
- Roadmap updates: none — no milestone changed state (backlog drain + bug fixes).
- GitHub Actions doc updates: `docs/claude/system-actions.md` —
  `pull-exchange-funding` `days:` selector + the SERVED-span reading guidance.
- Subsystem doc updates: `docs/design/gross-exposure-governance-DESIGN.md` (§ 4
  marked SHIPPED, § 5 still open/Tier-3);
  `docs/netting-partial-close-attribution-DESIGN.md` (implementation note under
  decision 4 — *stage the WRITE, never the measurement*).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **Contradiction 1:** `CLAUDE.md` described `NETTING_ATTRIBUTION_ACCOUNTS` as
  scoping the accounts. Accurate to the old code, wrong after #8666 — corrected
  in the same PR.
- **Contradiction 2:** `docs/netting-partial-close-attribution-DESIGN.md`
  decision 4 recorded the correct *intent* but nothing warned against the
  implementation that broke it. Note added this session.
- **Code/doc mismatch:** `pairs_executor._close_pair`'s comment says "the
  monitor/backstop retries" a leg it leaves open. **No such retry existed** —
  the netting reconciler skips pairs rows by design. Resolved by #8667 making
  the statement true rather than by editing the comment.

## Risks and Follow-Ups
- Remaining technical risks: **if the fills store is ever lost or rebuilt it
  silently comes back 7 days deep**, and `/api/bot/pnl/exchange` + the
  broker-truth cost sweep would serve that as normal. Nothing detects a store
  shallower than the journal it is read against. Filed, not fixed (Tier-2 —
  changes a live timer).
- Remaining product decisions (Tier 3): **choosing the gross-exposure values**
  (design § 5, Option C; § 6 requires the first value be deliberately LOOSE).
  Unblocked — the observed multiples are readable now.
- Blockers: none.

## Deferred Items
- **Widening `NETTING_ATTRIBUTION_ACCOUNTS` to `bybit_2`** — its soak rows exist
  for the first time; let them accrue before deciding.
- **The pre-existing `bybit_1/BNBUSDT` ~2.45× journal excess** — #8667 stops NEW
  divergence but does not retire the old rows. Needs a pairs-aware one-off.
  **The netting reconciler's pairs skip must NOT be deleted to reach them** —
  that skip is correct, and #8667 is what makes it true.
- **The Bybit funding range cap** — still unverified, no longer unverifiable.
  The next ordinary nightly run's `SERVED span` line is the evidence.

## Two hypotheses falsified (honest negatives, recorded so they are not re-run)

1. **Phantom netting rows.** *Population: 894 scanned, 587 already
   measured/estimated, 307 unresolved, 273 `no_fill_in_window`; top-15 buckets
   cover 96.0%.* If these were rows that never had a fill, they would cluster on
   the divergent symbols. They do not: BTCUSDT (**not** divergent) is
   131/273 = **48.0%**; all known-divergent pairs are 49/273 = 17.9%; and
   `bybit_1/BNBUSDT` — the live 2.45× symbol — is **4 rows = 1.5%**, the
   smallest bucket shown. The tail spans 23 pairs across 4 venue types including
   non-netting ones. **The residual tracks trading volume.** Two problems, not
   one.
2. **Page-capping.** Zero FULL-page warnings across four nightly runs; peak 61
   candidates against `PAGE_LIMIT=200`. Dead. The actual cause is that every
   nightly run is `days=7` — the range-walk exists but nothing exercises it on a
   schedule.

## One fix verified live, against its own failing runs
`BL-20260807-BYBIT-DEMO-FILLS-NEVER-PULLED` → **`resolved_verified_live`**.
BEFORE (08-06/07): `bybit_1` + `bybit_portfolio` raise `retCode 10003` on every
request, `candidates=0`, while the run prints `ran=3/3 total_inserted=0` and
systemd logs "Deactivated successfully" — two accounts 100% uncovered behind a
green unit. AFTER (08-08 on): demo-host routing appears, candidates 0 → 61 and
0 → 13, summary becomes `ok=3 failed=0 skipped=0`. Both halves confirmed. This
had previously only been *reasoned about*.

## Part 2 — the reviewer-side instances (same day, after the operator asked to finish here)

The three fixes above were defects in the CODE. What followed were two of the
same defect in MY OWN REVIEW, which is why they are recorded at equal weight.

**#8678 — `#8665` shipped with no reader.** `report()["exposure"]` reached
`TradingAccount.status()` via `**risk_report`, and the sole consumer (the
Telegram `/accounts_status` renderer) had zero references to the key. The number
the PR existed to reveal was unreadable by anyone. I had verified no consumer
would **break** on the new key — `status()` splats the dict without branching —
and never verified one would **display** it. Different questions; only the
second was the point. Fixed with the renderer line + `/api/diag/exposure`, which
serves the identical `report()["exposure"]` rather than a reconstruction (a
second definition of "exposure" would be free to drift from the enforcing one).

**#8683 — I called the 2.03× brick live, on a Sunday.** Alpaca and IB showed no
opens since Friday while bybit traded Sunday 01:14Z. That is exactly the shape
of the originating incident, and `alpaca_paper` read 2.0238× against a 2.00×
Reg-T limit at the time. It was the weekend. **Venue-closed silence and refusal
silence are indistinguishable in the trades table; only the calendar separates
them.** I had also read a clean `broker_account_status` (`trading_blocked:false`)
as reassurance — it reports AUTHORIZATION (permitted to trade) while the
incident is CAPACITY (`available_usd 0.00`, no room to trade).

**#8684 — the observation soak**, so the ceiling question stops depending on a
single read. Samples every account every `EXPOSURE_SOAK_SECONDS` (default 900s)
from the trader tick; `summary.by_account[*].max_multiple` is the load-bearing
statistic (§ 6 needs the ceiling above normal operation, and a ceiling clearing
the mean but not the peak clamps correctly-sized trades at the peak). Three
lessons are built in rather than left to discipline: unmeasured never becomes
`0.0`; every row stamps `venue_session_us_equity` so quiet-because-shut is
distinguishable from quiet-because-refusing; and the reader ships in the same PR
as the writer.

### Measured, 2026-08-09T14:45Z (diag #8680) — ONE Sunday read, not a distribution

| account | class | multiple |
|---|---|---:|
| `alpaca_paper` | paper | 2.0238 |
| `alpaca_portfolio` | paper | 2.0126 |
| `bybit_portfolio` | paper | 1.0575 |
| **`bybit_2`** | **real money** | **0.9939** |
| `ib_paper` | paper | 0.4395 |
| `bybit_1` | paper | 0.2540 |
| `oanda_practice` · `alpaca_options_paper` · `alpaca_live` | — | 0.0 |
| `ib_live` · `breakout_1` | — | **unmeasured** (`equity_unavailable`) |

The two unmeasured accounts are the CORRECT state — both shelved/API-less — and
the three-state design earns its keep by refusing to report them as flat.

**The one analytic result that survives without the soak:** § 6 requires the
ceiling BELOW the venue limit and ABOVE normal operation. For Alpaca those are
INVERTED in this read (2.0238× measured vs a 2.00× Reg-T limit). If a weekday
soak reproduces that, **no value satisfies § 6 for those accounts** and a ceiling
is the wrong instrument — it would mask a sizing problem rather than govern one.
That must be answered from measurement before any Alpaca value is proposed.

## Part 3 — the flag I raised, then closed (#8688)

While merging the exposure soak I noticed that `src/main.py`'s tick had become a
chain of **twelve** best-effort hooks — order monitor, pairs executor, macro
thesis, five prop prompts, two reachability alerts, the IB-state dump, and the
soak I had just added. Each is individually cadence-gated, exception-swallowing,
documented as cheap. **Nothing measured the sum.**

That is the shape of BOTH June 2026 wedges (`MB-20260609-001` steady-state,
`BL-20260609-001` cold-start): each new component was cheap in isolation, and
the defence each time bounded the NEW component rather than the total — so the
total grew un-observed, and the next hook added would be individually cheap too.

I first logged it to the coordination board as a flag I could not close, being
careful to say I had **no measurement and was not claiming a problem**. The
operator's direction was to resolve it before wrapping rather than hand it off,
which was the right call: an un-measurable concern handed to a fresh session is
just a rumour.

**#8688 (`0044bdbd`)** closes it by MEASURING, not bounding.
`begin_tick`/`end_tick` bracket the chain, a fixed-size accumulator carries
last/max/mean, and `/api/diag/tick_cost` serves it. Two properties are carried
over from #8684 for identical reasons: the max updates EVERY tick while the file
persists on a cadence (so a spike between writes is never lost), and an untimed
tick reports `None` rather than `0.0`.

**It deliberately enforces no budget, and a test asserts none exists.** Setting a
cap with no distribution behind it is precisely the exposure-ceiling mistake
this sprint spent the day on — § 6: a ceiling below normal operation silently
throttles correct work, and you cannot know where normal operation sits until
you have measured it. If a budget is ever warranted it is a separate change with
this measurement behind it.

Scoped to the AGGREGATE (two touch points) rather than wrapping all twelve
hooks: the measured gap was "nothing watches the total", and twelve mechanical
edits to the live trader's main loop at the tail of a long session is a risk the
marginal attribution detail did not justify. Per-hook attribution is the
follow-up, and will be better targeted once a distribution exists.

## Next Recommended Sprint
- Suggested next sprint: read the observed exposure multiples off `report()`
  and bring the operator a **specific** proposed fleet-default
  `max_gross_exposure_pct` (Tier-3 decision), plus the `bybit_2` allowlist
  widening once its soak has rows.
- Why next: both are now blocked only on data that this sprint made available,
  which is the definition of ready.
- **UPDATE (end of session):** the soak (#8684) now accumulates what this
  prerequisite was really asking for. Re-read it after a full TRADING week —
  `/api/bot/exposure/soak` `summary.by_account[*].max_multiple` beside
  `measured_n`, and `summary.venue_sessions` to confirm the window actually
  contains `rth` rows. A soak that is mostly `closed` has observed nothing.
- Required verification before starting: ~~confirm the deploy landed~~ **DONE
  2026-08-09T12:21Z (diag #8674)** — `/api/diag/version` reports
  `git_sha: 49f4442f`, which is `main` including all three Tier-2 merges;
  `ict-trader-live.service` and `ict-web-api.service` both `active`. The one
  `inactive` timer, `ict-ib-gateway-watchdog.timer`, is CORRECT on the trader —
  it is auto-enabled only where `/etc/ict-vm-role == gateway`. **Still to
  confirm before the next sprint:** that `bybit_2` netting soak rows are
  actually appearing (the #8666 change is deployed, but rows accrue only when a
  divergence is observed), and that the exposure block populates on a real
  account rather than only in tests.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needs no update.
- [x] Roadmap status was checked — no milestone changed state.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
