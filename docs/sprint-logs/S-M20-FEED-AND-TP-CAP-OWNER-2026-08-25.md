# S-M20-FEED-AND-TP-CAP-OWNER-2026-08-25

- **Dates:** 2026-08-25 (continuation of the 2026-08-24 session on the same branch)
- **Branch:** `claude/m20-bracket-candle-feed-0k5mb3`
- **Milestone:** M20
- **Tier:** Tier-1 throughout except #10240 and #10248, both operator-approved
- **PRs:** #10238 · #10239 · #10240 · #10241 · #10243 · #10244 · #10246 · #10248

## Objective

Drain the three Tier-1 items scheduled at the end of the 2026-08-24 session, and
answer the prop `tp_r` question the operator had directed be *measured* first.

## Work completed

### 1. The prop `tp_r` gate — measured, and the answer is DO NOT TIGHTEN

`account_compat_matrix` passed `overrides={}` **hardcoded**, so the engine path
could only ever score a strategy at its `config/strategies.yaml` values —
pricing a candidate `tp_r` required first editing the Tier-3 file under
evaluation. That circularity is gone (#10238); `prop-tp-r-gate.yml` consumes it.

Run [`32781427791`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/32781427791),
10/10 arms, criterion fixed **before** any result:

| leg | 6.0 (control) | 5.0 | 4.0 | 3.0 | 2.5 |
|---|---|---|---|---|---|
| `trend_donchian_sol_prop` | **$1,084** | $1,090 | $1,029 | $986 | $950 |
| `trend_donchian_eth_prop` | **$968** | $980 | $795 | $611 | $503 |

Option C's implied values (sol ≈ 3.22, eth ≈ 4.08) land in the losing region on
both legs. Operator chose *"fix the comment, keep `tp_r=6.0`"* → #10240,
comment-only, proved by `yaml.safe_load` equality.

⚠️ Caveats stated rather than buried: single MC, seed 1234, `n_paths=3000` — the
**+$12/+$5 at 5.0 are indistinguishable from zero** and are not a finding; the
criterion's clause (c) *"materially below"* carried no numeric threshold, which
was a weakness in my own writing of it.

⚠️ **Option C's stated rationale was FALSE**, verified not assumed: effective
target is `min(cap_r, tp_r)` with `cap_r = 0.099·entry/risk` — a percent-of-entry
against a multiple-of-risk, so **no `tp_r` reproduces the clamp**, and 3.22/4.08
tighten the real target on half the trades *by the definition of the median*.

### 2. e35 planner + the feed defect it exposed

#10239 gave `e35_shard_plan` an `--ignore-missing-data` (default OFF) so it can
plan on a fresh CI checkout. The sweep then ran for the first time ever
([`32783849276`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/32783849276)):
46 jobs = 1 plan + 43 legs + aggregate + corpus.

**19 succeeded / 24 failed — exactly as predicted from the feed analysis before
the run finished.** `e35-bracket-sweep.yml` hardcoded `BACKTEST_FEED_SOURCE:
binance_vision` for the whole matrix; Binance lists no ETFs, so 24 non-crypto
legs each burned ~10 min failing (~4 runner-hours per dispatch).

Fixed in #10244 by resolving the feed **per leg**: `19 binance_vision + 21
dukascopy + 3 refused = 43`. The planner **refuses at plan time** rather than
falling back — a leg re-routed to a feed carrying a *different instrument* is a
wrong backtest that looks fine.

Depth was **measured first, not assumed** (#10243): the Dukascopy span probe,
run [`32788423940`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/32788423940),
66 probes, zero errors, every mapped instrument carrying bars past the sweep's
own 1830 d request. That measurement is what decided the fix shape.

### 3. The venue TP clamp now has ONE owner (#10248, Tier-3, operator-approved)

**The backlog item said five files. A mechanical census found THIRTEEN
declaration sites under THREE names** — `_TP_SENTINEL_CAP_PCT` (4 strategy units
+ `position_telemetry`), `TP_VENUE_CAP_PCT` (`target_expectation` + 2 research
scripts), `LIVE_TP_CAP_PCT` (5 scripts). All 13 held `0.099`: **they agreed by
luck**, with no import, no test and no guard binding them.

**13 → 1.** `src/runtime/tp_venue_cap.py` imports only `typing`, so the two
modules that advertise dependency-freedom keep that property in substance.

Live order geometry is **unchanged and asserted mechanically**: the units import
the owner under their existing local name, so all 8 clamp expression lines are
byte-identical, and every `src/` consumer resolves to the same OBJECT (`is`,
not `==`).

New guard `tp-venue-cap-single-owner` — one declaration only · no shadowing ·
the `CLAMPING_UNIT_MODULES` registry must match what the unit sources carry.
Controls 13/13, **verified to fire** on a planted duplicate and on planted
registry drift. No grandfather register, deliberately: all 13 were migrated, so
the clean population is 1 and an empty debt list only invites appending to it.

This is the check `m20_fleet_exit_sweep.py` correctly said did not exist
(*"NOTHING CHECKS THAT THIS STILL MATCHES THE LIVE VALUE"*). That comment is now
false on all three counts and was replaced — field beats comment.

⚠️ **DUPLICATION only. The venue-scope question is still OPEN**: `0.099` is a
Bybit ErrCode 10001 boundary applied to legs on venues Bybit does not carry
(the 3 Bybit accounts hold only BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT/ADAUSDT/AVAXUSDT).
Recorded as OPEN in the owner docstring; this change must not be read as having
settled it.

## Validation

| check | result |
|---|---|
| `run_guards.py --base main` | PASS 51 · FAIL 0, exit 0 (captured directly, committed range) |
| `ruff check src/ scripts/ tests/` | exit 0 |
| affected tests (151 files) | 2109 passed, 5 skipped |
| CI on #10248 | 4/4 green, twice (pre- and post- draft flip) |

Excluded and **proven** not mine: 4 failures + 5 collection errors, all
`ModuleNotFoundError` for `httpx`/`fastapi`, absent in this sandbox; none
reference the cap; `test_prop_breakout_notify.py` fails identically on pristine
`origin/main`. `BL-20260824-SANDBOX-TEST-SUITE-DIVERGES-FROM-CI`.

## Mistakes made, and what caught them

- **The same regex bug, twice.** A name pattern requiring a character before
  `TP_` never matched `TP_VENUE_CAP_PCT` — the owner's own constant name. First
  instance made the census report 5 instead of 13 (caught by cross-checking
  `grep`); second instance was in the new guard (caught by its planted
  controls). Both are annotated in-code.
- **A restore that silently did nothing.** After planting a mutation to prove
  the guard fires, `git checkout -- <file> || true` could not restore an
  *untracked* file and the `|| true` ate the error. The mutated registry
  survived into the tree and was caught only by a follow-up read.
- **A whole-file backlog reformat.** A naive `json.dumps(indent=2)` produced
  23,145 insertions on a 3-row edit. Reverted; redone through
  `backlog_append.detect_format` (`indent=1, ensure_ascii=False`) with an
  assertion that **exactly** the three intended rows changed → 21 insertions.
- **A pipeline exit code read as the command's.** `ruff … | tail` reported
  `exit=0` while ruff was returning 1. Re-run capturing the code directly.
- Four guards caught real defects; **none was silenced by weakening it**.

## Docs updated

- `docs/design/tp-sentinel-cap-venue-scope-PROPOSAL.md` §8 — the "five files"
  claim corrected to 13→1, with the venue question explicitly still open.
- `docs/claude/health-review-backlog.json` — TP-SENTINEL canonical row closed
  `resolved`; its superseded duplicate cross-referenced; the M39(B) pairs
  measurement folded into the pairs-sleeve row (inherited from the
  `/system-review` handoff).
