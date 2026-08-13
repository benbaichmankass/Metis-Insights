# S-SYSTEM-AUDIT-REVIEW-2026-08-13 — combined full-system audit + system review

## Date Range

2026-08-13 (single session, in flight at the time of writing)

## Objective

Operator-directed: run `/full-system-audit` **and** `/system-review` together.
The audit half was explicitly framed as **bidirectional** — the docs are the
yardstick, but where live reality is the deliberate, correct state and the doc
lagged, the **doc** gets fixed. The review half was given one stated priority:
**drain the backlog**, especially the high-severity items blocking other
research sessions.

## Tier

**Tier-1 for the audit half; Tier-3 + Tier-2 ENACTED in the second half** with
explicit operator approval in-conversation ("approved - implement part A and
B"). The first half was Tier-1 throughout — no `src/` order-path file, no
`config/`, no live-VM mutation, no account-mode flip — and this section
originally said so for the whole session. That became false when the operator
approved the bybit_2 margin-basis fix, which changes real-money position
sizing (`risk.py::position_size`, `coordinator.py`, `execute.py`). Corrected
here rather than left standing.

No `config/` change and no account-mode flip at any point.

## Starting Context

- `main` at `a787872` (#8948); the overnight M20 arc had just closed with the
  headline at `360/360 = 100.0%` and the done-condition at **37 open, every
  cell blocked**.
- Backlogs at session start: health **118 open + 49 kept_open** (34 high, 2
  critical), performance **6 + 43**, ml **3 + 32**.

## Repo State Checked

- All three repos on `claude/system-audit-review-oy5kon`.
- **Live VM (diag #8953, 2026-08-13T08:14Z):** trader + web-api **active** on
  `git_sha a7878725` — matches `main` HEAD, so the deploy is current.
  Heartbeat 22 s (`running`). `vm_health` cpu 0.0 / mem 11.1 / disk 41.4.
  Exit-loop decouple **`state: fresh`**, 19 passes, max pass 21.7 s — the M20
  decouple is genuinely running.
- **`ict-exchange-fills-pull.service` = `failed`** on the live VM.
- `ict-ib-gateway-watchdog.timer` reads `inactive` on the trader and that is
  **correct, not a finding** — it is auto-enabled only where
  `/etc/ict-vm-role == gateway`.

## Files and Systems Inspected

Phase 0 (rules) by hand; Phases 3A/3B/3C fanned out to three background agents
over (a) the liveness/zombie inventory, (b) doc-vs-code across the bot's
declared contracts, (c) the two consumer repos. Lead verified every finding
acted on.

Directly re-derived by the lead rather than taken from an agent:
`docs/research/exit-refinement-coverage.json` ↔ `config/strategies.yaml`
set-difference · every halt-flag site in `src/` · the sudoers grant + its
installer · `replay-pregate-nightly`'s notify gating · the cron'd-workflow
population.

## Work Completed

**Shipped (3 draft PRs, one per repo):**

1. **Metis-Insights #8964**
   - `runtime_flags.halt_flag_path()` — single-homed the halt flag. It had
     three definitions and **two paths**: `pipeline.py` (the only consumer that
     halts) checks `/data/bot-data/trader_halt.flag`, while
     `GET /api/bot/config` **and** the Telegram readout both hardcoded
     `/tmp/trader_halt.flag` and never read the env var. Both operator-facing
     surfaces could report **RUNNING while the trader was halted**. 7 tests.
   - `m20_coverage_rollup.validate()` — added the **config→matrix** direction.
     Only matrix→config was enforced, so a live leg with *no row at all* was
     invisible. Ships green (45 = 45, both differences empty).
   - `claude-run-failure-alert.yml` — registered the 9 unwatched cron'd
     workflows.
2. **ict-trader-dashboard #205** — per-strategy `Measured %` column.
3. **ict-trader-android #117** — per-strategy provenance mark + legend.

**Backlog movement:** 1 resolved
(`BL-20260810-COVERAGE-MATRIX-LEG-IDS-DO-NOT-JOIN-TO-CONFIG`), 1
partially-resolved (`…CRON-WORKFLOWS-FAIL-SILENTLY…`), 4 filed. **Net +3 open,
deliberately** — the audit surfaced more than the drain closed, and inflating
the close count by filing thin resolutions would defeat the point.

### The bybit_2 real-money rejection thread (the session's main work)

**Found by widening a filter.** A read of `exchange_rejected` rows across ALL
strategies — rather than the AVAX-scoped reads that preceded it — surfaced a
second, unrelated cause: `ErrCode 110007 "ab not enough for new order"` on
**bybit_2, REAL MONEY**. Every prior read was structurally incapable of seeing
it.

**Root cause (measured, not inferred).** Bybit leaves *every* ACCOUNT-level
margin aggregate as the empty string on that account, so
`totalAvailableBalance` was unusable and the margin pre-flight cap silently
fell back to **total equity** — counting initial margin already pledged to open
positions as though it were free.

Established by contradiction from measured quantities before any code was
touched: inverting `qty == 0.011` across the seven entry prices at 3x/0.9 pins
the cap's basis to a non-empty `$264.71-$278.69`, while the smallest refused
order needed `$229.92` initial margin and Bybit refused it — so the basis was
**not** the venue's available figure. 92 hourly `balance_snapshots` then put
equity at `$271.64-$278.97` with `open_positions == 2` at every row, which is
what the implied band straddles.

**Scope was worse than first filed, and my own first two notes were wrong about
it.** Reading by ACCOUNT rather than by strategy: **9 refusals across 3
strategies and 2 symbols** (`ict_scalp_5m` BTC x7, `trend_donchian` BTC x1,
`xrp_pullback_2h` XRP x1) = **30% of that real-money account's orders** in the
window. The "7 rejections by one strategy" framing was an artifact of filtering
by strategy — the same scoping error that had hidden the whole family.

**The journal proves the mechanism without arithmetic.** Same `0.011` BTC, same
account: trade 3909 (07-23) FILLED with `$0.00` pledged margin; trade 4013
(07-25) REFUSED with `$101.34` pledged. Same size, opposite outcomes, separated
only by whether other positions held margin.

**Three hypotheses raised and REFUTED, recorded so they are not re-run:**
1. *Venue leverage is not the configured 3x* — refuted by the boot log
   (`set_leverage pre-flight: account=bybit_2 symbol=BTCUSDT x3 already set,
   retCode=110043`).
2. *Scope is account-wide, symptom balance-gated* — refuted by the new diag
   arm: `bybit_1` and `bybit_portfolio` BOTH return `venue_available` with sane
   position-aware figures. Only the real-money book is blind.
3. *Available margin can be derived as `totalEquity - totalInitialMargin`* —
   refuted by reading the VALUES: `totalInitialMargin` is `''`. I had inferred
   it was populated from a KEY LIST, which is the present-vs-populated
   confusion I had fixed in code one level up an hour earlier.

**Where the number actually lives.** The per-coin USDT block:
`equity - totalPositionIM - totalOrderIM = $226.90`, all three broker-reported.
Validated against two independent methods — it reproduces a journal
reconstruction from open legs to **0.05%** (`$226.69` vs `$226.80`), and the
venue's own `totalPositionIM` sits **0.22%** from the modelled
`notional/leverage`, confirming the leverage model rather than assuming it. Two
reads 20 min apart gave an internal-consistency proof: `totalPositionIM`
identical, equity moved by **exactly** the uPnL delta (0.20930 both sides).

**Shipped (11 PRs).** #9011 #9016 (diagnosis + scope correction) - #9013 #9022
#9027 #9030 #9032 #9033 (the observability ladder, four states, the Bybit diag
arm, coin block, `coins_other`) - #9035 (decision-ready spec) - **#9039 (Part A
+ B, Tier-3/Tier-2)** - #9046 (honest live status).

**Part A** derives available margin from the USDT coin block, ranked ABOVE the
deprecated `availableToWithdraw`; a missing input REFUSES the derivation rather
than defaulting to zero. When even that is unreadable the equity basis
subtracts ESTIMATED pledged margin, reusing `_open_gross_notional_from_db` —
the same measurement `observe_exposure` uses — scoped to `linear` accounts and
excluding the order's OWN symbol (resizing a position releases its margin
rather than consuming more). **Part B** stamps `margin_basis` onto every
post-sizing rejection row and moves the coordinator's balance line DEBUG -> INFO.

**CI caught eight failures I missed, two of them serious.** Recorded because
the misses are the instructive part:
- `UnboundLocalError` on the failure path — I declared `margin_basis` inside a
  `try` whose first line can raise, and the matching `except` reads it. My AST
  "scope check" verified *assignment line < use line*, which is necessary and
  NOT sufficient: it does not model exception control flow. I reported it as a
  scope check and it was not one.
- **A re-created halt vector.** Subtracting pledged margin unconditionally
  drives the basis to 0 whenever journal open notional >= equity, refusing
  EVERY trade — and the journal is known to over-report open notional under
  netting (451x measured on bybit_1 SOLUSDT). Caught by
  `tests/test_risk_gross_exposure.py`, whose docstring says that half must
  never be edited to accommodate a feature. It is right; the code was fixed.
  An estimate >= equity now leaves the basis unadjusted and warns.
- Plus: over-applying the haircut to spot accounts, a `NameError` because
  `risk.py` had no module logger, a `PropRiskManager` signature mismatch, and a
  stale test double.

Root cause of the misses: I ran `pytest -k "risk or margin or coordinator..."`
locally instead of the full suite, and the keyword filter excluded every test
that failed.

**A null result I nearly misread against my own fix.** Verifying deployment, I
grepped a 400-line `journalctl` tail for the new INFO line and found zero. That
looks like falsification and is not — 400 lines covered **43 SECONDS** on a log
running ~9 lines/sec, and the line only fires on an actual dispatch. Zero hits
over 43s of a two-day-quiet account has no denominator. Sub-class C of the
diagnostic-provenance rule, aimed at my own work.

**Live status: DEPLOYED, UNVERIFIED.** `git_sha 5bbb3416` confirmed to contain
the new code by reading the deployed tree. But bybit_2's newest journal row of
any status is **id 4572, 2026-08-11T12:24:46Z** — two days before the deploy —
so no row carries a `margin_basis` stamp yet. Neither confirmed nor falsified.
The backlog row carries a one-read finishing procedure with all three outcomes,
including the one that REOPENS it.

### Other work

- **Venue-max clamp** (`BL-20260810-ICTSCALP-AVAX-QTY-EXCEEDS-VENUE-MAX`) —
  disproved the filed headline (the leg fills below 22,000 and fails only
  above), derived the cap from Bybit's own error text separating 22 of 22
  outcomes with zero overlap, and clamped in the existing qty-legalization seam.
- **VM-runner purge** — resolved on a root-verified post-state after two failed
  attempts, both of which failed in the VERIFICATION, never the work. Recorded
  that attempt 1 reached the right answer by an invalid method.
- **A 14-day time bomb** reddening `main` — a test fixture with hardcoded dates
  aged out of a rolling SQL window at exactly 2026-08-13T11:00:00Z. Second time
  bomb in six days.
- **A CI short-circuit** — `pytest-run` reported green in 9 seconds for
  `scripts/`-only diffs; #8994 merged having executed no tests. Third
  recurrence of the class.
- **Prop trade logged** — operator-supplied Breakout screenshot, ETHUSDT short
  3.0 @ 1874.34. `pnl` deliberately left NULL (the screenshot's +6.18 is
  labelled OPEN P/L; recording an unrealized figure in the realized field is
  the exact provenance failure this session spent the day on). Read back to
  confirm, not trusted from the 200.

## Validation Performed

- `check_canonical_doc_coherence.py`: **5/5 PASS**.
- Guard suite: **30 pass / 6 diff-scoped** locally; **CI guards green** on
  #8964 after one round-trip.
- 8/8 M20 join self-tests · 7/7 halt-flag tests · 106 pre-existing m20 tests.
- Halt flag verified **end to end**, not merely imported:
  `build_config()["trading_mode"]["halted"]` goes `False → True → False` as the
  real file is created and removed, and the endpoint's resolver and the
  pipeline's agree on the default.
- Dashboard coverage cell exercised across all three states incl. the one that
  matters (all-ungraded renders `—`, **not** `0%`).
- **NOT validated:** the Android Kotlin compile (no toolchain in the sandbox) —
  CI owns it. Stated on the PR.

## Documentation Updated

- `m20_coverage_rollup.py` docstring: it quoted `47 × 8 = 376` and `319/376` as
  *"the one to keep quoting"* in the present tense. Both stale, and stale in
  the direction that matters now the headline and done-condition have
  separated. Population is now computed, not restated in prose.
- `runtime_flags.py` docstring: the line *"the halt flag lives in /tmp … NOT
  managed here"* was both stale and the reason for the defect.
- `claude-run-failure-alert.yml` header: its "scoped to Claude-driven
  workflows" framing no longer described its contents.

## Contradictions or Drift Found

| finding | direction | disposition |
|---|---|---|
| Halt flag: reporters vs consumer | **CODE-DRIFT** | fixed (#8964) |
| M20 join enforced one-way | **CODE-DRIFT** | fixed (#8964) |
| 9 cron'd workflows silent on failure | **CODE-DRIFT** | fixed (#8964) |
| `claude-vm-runner` sudoers root grant | **ZOMBIE** | filed, Tier-2, operator |
| Caddy/duckdns transport in zero docs | **DOC-STALE** (live is correct) | filed |
| Velotrade doc in present tense, says `breakout` is deprecated — exactly backwards | **DOC-STALE** | filed by agent, not yet fixed |
| `binance_connector.py` named in ARCH, file does not exist | **DOC-STALE** | not yet fixed |
| ARCH "54 strategy cells" vs 55 | **DOC-STALE** | not yet fixed |
| 10 live routes absent from the CLAUDE.md API table | **DOC-STALE** | not yet fixed |
| `totalR`/`expectancyR`/`rCoverage`/`totalPnlMeasured`: **zero readers in 3 of 3 frontends** | **CODE-DRIFT** | per-strategy half fixed (#205, #117); the R-family is still unread |

## Risks and Follow-Ups

- **`BL-20260813-VM-RUNNER-ZOMBIE-SUDOERS-ROOT-GRANT` (Tier-2, operator).** The
  file **must be split, not deleted** — it also carries a justified
  `/usr/sbin/ufw` grant.
- **`replay-pregate-nightly` is still failing nightly.** #8964 makes the next
  failure loud; it does not make the run pass. The trainer-SSH-under-load cause
  is open.
- **`ict-exchange-fills-pull.service` is `failed`** on the live VM — observed,
  not yet investigated.
- **Zero of 11 accounts declare a gross-exposure ceiling**, and
  `alpaca_portfolio` / `alpaca_paper` are running ~2.0× exposure. This is the
  distribution the exposure soak exists to produce; the ceiling value is the
  operator's.
- **This session's `DIAG_BASE_URL` points at `158.178.210.252`** — the x86 micro
  terminated 2026-06-16. Harmless today (sandbox egress is firewalled, so the
  relay is the only channel) but it would hit a dead host if a future session
  were created at Full network access.

## Deferred Items

Three probes of my own were wrong and are recorded because the corrections are
the useful part:

1. **"8 guards FAIL"** — missing `pytest`/`import-linter` locally, not real
   failures. Would have been a false alarm.
2. **"10 of 10 cron'd workflows are silent"** — my probe grepped for a literal
   `api.telegram.org`; `Health Snapshot` notifies via
   `scripts/notify_session.py`. Honest count is 9. Committed the sub-class-C
   unasserted negative *while auditing for that class*.
3. **My new guard flagged `xauusd_trend_1h`** — **the matrix was right.** That
   leg is `enabled: false` *with* `execution: live`, and the live trader's
   52-leg loaded list does not contain it. Had I trusted the guard over the
   field, I would have corrupted a correct row.

## Next Recommended Sprint

1. Operator decision on the sudoers grant, then the Tier-1 repo-side purge.
2. The trainer-SSH-under-load cause behind the pre-gate failures.
3. The doc-stale backfill batch (Velotrade, binance, 54→55, the 10 API rows,
   the undocumented kill-switches + `POSITIONS_CACHE_*`).
4. The R-metric family (`totalR`/`expectancyR`/`rCoverage`) — still zero
   readers across all three frontends.

## Wrap-Up Check

- [x] Board `START` posted before the first substantive call; progress posted.
- [x] Findings filed to the correct backlog with evidence + tier.
- [x] Tier-2/3 findings raised, **not** enacted.
- [x] Coverage stated honestly, including what was not reached.
- [ ] `doc-freshness` closing run — session still in flight.
- [ ] ROADMAP entry — pending, lands with the audit report.
- [ ] Audit report to `comms/reports/` (`window: "audit"`) — the program's
      closing deliverable, owed once the fixes are merged + live-verified.
