# S-MI195 — QLD/TQQQ examined: the block was a routing gap, and the answer is a measured `none`

> **Doc status:** `historical` · category `history` · last verified `2026-09-11` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md)

## Date Range

2026-09-08, single session. Base `origin/main` `417bd6b6`, merged forward to `1af36093`.

## Objective

Act on the operator's answer to `DEC-20260907-TP-UNEXAMINED`. Offered three ways to *publish* that
`qld_trend_long_1d` and `tqqq_trend_long_1d` had never been swept, they rejected all three:
**"Why don't we just examine them?"** So: source the candle substrate, get both legs into the e3.5
sweep, run them, and report an honest denominator — including `insufficient_n` if that is the answer.

## Tier

**Tier-1 throughout.** Research tooling, docs, backlog. `config/strategies.yaml` opened read-only and
not modified. No `src/`, no order path, no strategy params, no risk caps.

## Starting Context

The dispatch named `operator_answers_2026_09_08` in
`docs/claude/work/objects/WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE.yaml` as the carrier of three
Tier-3 verdicts **and their conditions**, and said to read it first. **It does not exist** — zero
occurrences in that file or any `.yaml`/`.json`/`.md` in the repo, against passing positive controls
in the same file (`decision_requests` 1, `review_trigger` 1, `operator` 6), and the file has exactly
two commits, neither adding it. Third recurrence in this thread. Proceeded on the dispatch prose,
which is one session's transcription rather than a committed record, and said so on the board, in the
PR, and in `BL-20260908-DISPATCH-NAMES-AN-OPERATOR-ANSWERS-BLOCK-THAT-WAS-NEVER-WRITTEN`.

## Repo State Checked

- `git fetch --deepen=2000` first — the clone arrived shallow (50 commits), which defeats the
  mandated Tier-2/3 history check.
- Coordination board #11336 read; START posted before the first substantive change; a second comment
  posted mid-session warning every live session off `document_index.py --write` (below).
- `merge_slot` in `docs/claude/session-board.json` was held by a **released** claim
  (`claude/mi191-…`, whose PR #11357 was already main's HEAD). Claimed it; on the forward-merge it
  conflicted with another released claim (`claude/mi194-…`, PR #11360, by then main's HEAD) and was
  resolved to this branch's own claim per R13.

## Files and Systems Inspected

`scripts/research/e35_shard_plan.py` · `scripts/ops/fetch_backtest_candles.py` ·
`scripts/ops/dukascopy_instruments.py` · `ml/datasets/adapters/yf_symbols.py` ·
`.github/workflows/e35-bracket-sweep.yml` · `scripts/research/e35_bracket_geometry_sweep.py` ·
`scripts/research/bracket_expectation_census.py` · `src/runtime/target_expectation.py` ·
`config/strategies.yaml` (read-only) · both e3.5 corpora · `docs/claude/OPEN-ITEMS.json`.

## Work Completed

**The block was a routing gap, not a missing dataset.** `resolve_feed_source` reached yfinance by
exactly one route (`if sym in fleet.PROXY_DATA`); QLD/TQQQ are not `PROXY_DATA` — correctly, they
need no proxy — so they fell to Dukascopy, whose refusal is explicitly a refusal **to proxy**
(*"a QQQ series is not a substitute at any horizon"*). Their own tickers were mapped in `yf_symbols`
the whole time. Same shape `MHG` was already rescued from, which the function's own docstring
conceded one paragraph above the refusal.

Added a **last rung** — reached only when Dukascopy cannot serve, gated on the yfinance ticker being
the symbol itself so a proxy row can never ride it — plus `_dk_state_for` and 3 new self-test cases.
Corrected the now-stale docstring and comment that still said QLD/TQQQ *"stay refused"*.

Filed four backlog rows and one `OPEN-ITEMS` monitoring row. Wrote
`docs/research/qld-tqqq-examined-2026-09-08.md` and landed the sweep artifacts under
`docs/research/e35-qld-tqqq-2026-09-08/`.

## Validation Performed

| check | result |
|---|---|
| `e35_shard_plan.py --selftest` | **66 pass, 0 fail** (was 63) |
| planner matrix before/after | 41 → **43** jobs · **0 removed** · **0** pre-existing legs change `feed_source` |
| substrate depth | QLD **5084** bars from 2006-06-21 · TQQQ **4167** from 2010-02-11 · QQQ control 6709 from 2000-01-03 · identical **1255** in-window |
| **positive control** | `qqq_trend_long_1d` on a same-built CSV reproduced the corpus's qualitative verdict — zero passing TP cells, its one wf candidate failing |
| the sweep | QLD **175** TP cells / **0** pass · TQQQ **175** / **0** pass |
| `run_guards.py --base main` | **PASS 73 · FAIL 0 · SKIP 18**; six affected guards re-run on the post-merge head |
| backlog forward-merge | **asserted** a strict union — 1310 ∪ 1307 = 1311, zero dropped, zero invented |

**THE RESULT.** `unexamined` becomes a **measured `none`** on both legs. The cause is sharper than
"no edge": every failing TP cell graded `tie_no_improvement` at `d_net_r` **exactly 0.0** — identical
to base, i.e. the target never fired — and QLD's one wf candidate `tp2` scored wins 5/6 with
**`wins_effective` 0, `inert_wins` 5**. Measured `cap_r` median **1.40** (QLD) / **0.99** (TQQQ)
against **3.14** on the QQQ control: a 2×/3× fund runs 1.8×/2.6× QQQ's ATR/entry, so **on TQQQ the
9.9% venue clamp binds below 1R** — the target rests nearer than the stop.

**Deliverables 2 and 3 of the dispatch were already done by MI-158 (#11232)** — verified by *running*
the census, not reading it (22 deliberate / 2 unexamined / 0 undeclared, never summed;
`gld_pullback_1h target_r=4.0` surfaced as explicitly not calibrated). Not re-touched.

## Documentation Updated

`docs/research/qld-tqqq-examined-2026-09-08.md` (new, registered in `DOCUMENT-INDEX.md`) ·
`docs/claude/OPEN-ITEMS.json` (+1 monitoring row with a declared `probe_absent_reason`) ·
`docs/claude/health-review-backlog.json` (+4) · `docs/claude/session-board.json` · this log.

## Contradictions or Drift Found

1. **`scripts/ops/document_index.py --write` stamps a false `last verified` on 436 documents** —
   including `CLAUDE-RULES-CANONICAL.md`, `CLAUDE.md` and `ARCHITECTURE-CANONICAL.md` — and
   `document-index-guard`'s own failure message prescribes running it. Measured on a branch that
   added **one** document: 439 files changed. Reverted; worked around by deleting the generated
   artifact instead of registering it. Filed **high**; warned the board.
2. **`resolve_feed_source`'s docstring contradicted its own behaviour** after the rung landed. Caught
   re-reading my own diff, fixed in its own commit.
3. **Two of my own numbers were wrong**, both flattering, both corrected: an ambiguous "4 of 6 legs
   produced zero" (3/6 for gate-pass, 4/6 for shippable — now a table), and "QLD 1.40 … below the
   bottom of the fleet range", which is **false for QLD** (second-lowest, above `gdx`'s 1.28). The
   correction also states that the comparison is not like-for-like.

## Risks and Follow-Ups

- `OI-20260908-QLD-TQQQ-EXAMINED-LOCALLY-AND-THE-CI-FEED-PATH-HAS-NEVER-RUN` — the sweep ran
  in-sandbox off Yahoo's chart API taken directly (the repo fetcher was HTTP-429 from this IP).
  `e35-bracket-sweep.yml` has **never** fetched these legs on a runner; merging the rung is not
  running it.
- `BL-20260908-TARGET-EXPECTATION-COLLAPSES-THE-THREE-TP-INTENT-STATES-THE-CENSUS-KEEPS-APART` —
  MI-156 § 7b required **two** readers; MI-158 landed one.
- `BL-20260908-TP-INTENT-READER-LANDED-WITH-ZERO-TESTS`.

## Deferred Items

- **`config/strategies.yaml` is unchanged.** Moving both legs `unexamined` → `none` is **Tier-3** and
  is put back to the operator in the memo's § 6, with the honest reason being *no reachable target* —
  **not** the `trail_is_the_profit_exit` the other 20 legs carry, which would lose the fact this
  session bought.
- **QLD cell `sm2` passed Path B (5/6 effective) and is NOT proposed** — a stop cell carrying no
  take-profit, the same shape as the `sm1.5` this session was told not to ship.
- A corpus-comparable **1830 d** run. This one used full history, so its verdicts are better-powered
  (113/117 lifetime trades) and **not** mergeable into `e35-bracket-corpus.jsonl`.

## Next Recommended Sprint

Dispatch `e35-bracket-sweep.yml` with `only=qld_trend_long_1d,tqqq_trend_long_1d` on a runner to close
clause (a) of the OPEN-ITEMS row, and put § 6 to the operator. Independently, fix
`document_index.py --write` — it is a trap the guard actively points sessions at.

## Wrap-Up Check

- [x] Board START posted before the first substantive change; hazard warning posted mid-session
- [x] Guards green (**73 pass / 0 fail**) before push; six affected guards re-verified post-merge
- [x] Backlog rows filed via `backlog_append.py::append_row` with `resolution_criteria`
- [x] `OPEN-ITEMS.json` updated; no row cleared (none of this session's work met a `clears_when`)
- [x] No canonical doc touched; no Tier-2/3 change made
- [x] Every quantitative claim states its population; two were corrected on re-check
