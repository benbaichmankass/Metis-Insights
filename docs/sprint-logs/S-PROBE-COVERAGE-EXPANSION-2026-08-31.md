# Sprint Log: S-PROBE-COVERAGE-EXPANSION-2026-08-31

## Date Range
- Start: 2026-08-31
- End: 2026-08-31

## Objective
- Primary goal: **Work-plan item 3 of the operating-machinery audit (⚠️ NOT "W3" — see Contradictions) — raise probe coverage on the `monitoring` rows of `docs/claude/OPEN-ITEMS.json`**, which stood at 3 of 11. Eight rows carried no probe. Work the eight ONE AT A TIME so that every unprobed row ends up naming a **specific missing capability** rather than "not yet written", and the count of merely-unwritten probes is **zero**.
- The bar for each probe, set by the operator: it must distinguish *"we did not look"* from *"we looked and found nothing."* **A probe that collapses those is worse than none.**
- Secondary goal (separable arm): dispatch the different-size LLM benchmark via `LLMBENCH_MODEL_URL`, compared on **answer quality against a real repo question**, not tokens/sec.

## Tier
- **Tier 1** throughout. Everything shipped is tooling, CI registration, observability and read paths: `scripts/ops/probe_*.py`, `scripts/ci/run_guards.py`, `.github/workflows/probes.yml`, `docs/claude/OPEN-ITEMS.json`, the health-review backlog, `CLAUDE.md`.
- No `src/`, no `config/`, no order path, no VM mutation. Every live read was a GET. The new `/api/bot/*` reader carries **no credential at all**, asserted by a control.

## Starting Context
- Prior sprint: `docs/sprint-logs/S-OPERATING-MACHINERY-AUDIT-2026-08-31.md`. Its central finding was the **disposition gap** — detect → land → come due → get a disposition, with step 4 unowned. Probes are the half that moves the LOOKING off a review session; this sprint widens them.
- `origin/main` green at start (`8d4e3be`). Board `START` posted to issue #6927 before the first change.
- Coverage at start: **3 of 11**, all three reading diag soak logs through `probe_soak.py`. ⚠️ The denominator became **12** mid-PR when `origin/main` was merged in — see Contradictions.

## Repo State Checked
- Branch: `claude/probe-coverage-expansion-ayloee` off `origin/main` @ `8d4e3be`.
- Live reads (all GET): `/api/health`, `/api/bot/logs`, `/api/bot/config`, `/api/bot/trades/closed`, `/api/diag/log_file` for four soaks. Served by `https://ict-bot.duckdns.org`.
- Canonical docs read: root `CLAUDE.md`, `docs/claude/OPEN-ITEMS.json` (all 11 monitoring rows in full), `scripts/ops/run_probes.py`, `scripts/ops/probe_soak.py`, `scripts/ops/diag_fetch.sh`, `.github/workflows/probes.yml`.

## Files and Systems Inspected
- Code inspected: `scripts/ops/run_probes.py`, `probe_soak.py`, `diag_fetch.sh`, `scripts/ci/run_guards.py`, `scripts/ci/check_matrix_bracket_values.py`, `scripts/research/research_disposition.py`, `src/runtime/stray_oca_groups.py`, `src/units/accounts/ib_client.py` (`_sweep_stray_oca_groups` + its call site), `src/web/api/routers/diag.py` (`_LOG_FILES`).
- Data inspected: `docs/research/{e35-bracket-corpus,m20-sweep-corpus,gld-compat-matrix-verdicts}.jsonl`, `docs/research/exit-refinement-coverage.json`.
- Workflows inspected: `probes.yml`, `guards.yml`, `due-list.yml`, `gpu-burst-train.yml`, `.github/actions/commit-to-main/action.yml`.

## Work Completed

### The shared core (`scripts/ops/probe_lib.py`)
- Extracted the predicate engine + the three-state exit contract out of `probe_soak.py`, which now delegates. A second and third probe SOURCE was arriving; copying the engine would have given the repo two definitions of what `legs[].position_idx~1,2` means, free to drift — the argument `CLAUDE.md` already makes for `provenance.py` and `_regime_score_semantics.py`, where two probes re-derived one answer independently and **both got it wrong on the same day**.
- **A POSITIVE CONTROL, which is the substantive addition rather than plumbing.** Every row in this family is open *because the thing has not been seen*, so `fail` is the expected verdict — and an expected verdict is the one nobody re-checks. A predicate typo, a renamed field or a moved schema all produce that same quiet `fail` indefinitely, and it reads as diligence. A declaration may now name `--positive-control`: a condition that DOES hold today. If it does not fire, the verdict is **`could_not_look`, never `fail`**. That is RULE ONE — *"show the probe can find a positive before trusting that it is quiet"* — made executable rather than promised.
- Added `>` for a criterion turning on a trade being OPENED AFTER a deploy. Its **lexicographic-not-semantic** behaviour is asserted as a control rather than described — after the control caught the author asserting the opposite.

### Three new probe SOURCES
| script | reads | added for |
|---|---|---|
| `probe_file.py` | repo-local JSONL corpora | the research-queue `infeasible` row |
| `probe_api.py` | the **unauthenticated** `/api/bot/*` surface | the E35 row |
| `probe_actions_log.py` | GitHub Actions **job logs** | the session-brief and GPU rows |

- `probe_api.py` accepts a bare list or an object and **says which shape it saw**, so a denominator of 1 (a whole config document) can never be mistaken for a population of 1. **Any non-200 is `could_not_look`** — found the hard way: `?limit=500` on `/trades/closed` really does return HTTP 422.
- `probe_actions_log.py` takes `--workflow` repeatably with literals bound to the preceding one, because two rows require BOTH halves of a criterion and the halves live in **different workflows**; a pass on half a criterion is the over-read this family exists to stop. A **410 (expired log) is `could_not_look`** — the evidence existed and we arrived late, which is not the line being absent.

### Four rows got a probe
- `RESEARCH-QUEUE-INFEASIBLE-STATE` — its own reason admitted the probe was absent *"because it needs the corpus reader wired as a probe, not because the condition is unobservable"*, which is a merely-unwritten probe by admission.
- `E35-GEOMETRY` — **half (b) only**; half (a) is already recorded satisfied, so re-probing it would test a settled question.
- `SESSION-BRIEF-DIFF-SCOPING` — both halves, `guards.yml` + `due-list.yml`.
- `RESEARCH-QUEUE-GPU-ROUTE` — see the category error below.

### Three of the eight reasons were WRONG ON THE FACTS
1. **Two blamed the diag bearer** for `/api/bot/logs` and `/api/bot/config`. Measured from the sandbox: both are the **unauthenticated** Tier-1 read surface and answer **HTTP 200 with no `Authorization` header at all**. There was no bearer to lack. The real blocker was ours and buildable — `diag_fetch.sh` hardcodes the `/api/diag/` prefix, so the family's only fetcher could not address any other route. **A missing FETCHER wearing the label of a missing CREDENTIAL.**
2. **The GPU row was a category error.** It said a probe *"cannot produce that without FIRING a GPU job, which spends real budget — an action, not an observation."* True about firing, irrelevant to probing: **no probe in this family produces the event it watches** (`pairs_soak` does not open a pair). Conflating *producing* an observation with *observing* it turned a buildable probe into a declared impossibility.
3. **The stray-OCA row named a log that does not exist.** It said it becomes probeable once armed, against `protection_reassert_soak` *"(or the stray-group soak)"*. Neither serves — see Contradictions.

### The four that stay unprobed now name a specific missing capability
- **MHG-OVER-COVER** — the owed half (b) is an **action**: `cancel-ib-order` exercised against the real gateway, a Tier-2 mutation on a live order book that no probe may make on a cron. Names its unblock (a soak row carrying the venue read-back beside the action's own verdict).
- **STRAY-OCA-SWEEP** — **the sweep writes no soak log at all** (below).
- **TRAINER-UNMONITORED** — two: no trainer HTTP surface, and — the binding one — the criterion is about an **alarm**, so probing the file's freshness would test the DATA under the ALARM's label (sub-class A substitution) and pass every day while no alarm existed.
- **SESSION-BRIEF-NEVER-READ** — a witness for a session's own **reasoning provenance**, which no artifact carries. Permanently unprobeable; a probe grepping for a citation would read a self-report as evidence of its own cause.

### The 3B arm — throughput and footprint MEASURED, answer quality NOT

Dispatched via `LLMBENCH_MODEL_URL` (#10621). Same Qwen2.5 family and Q4_K_M quant as the 1.5B, so size is the only variable. The positive control in the dispatch confirmed the override served (`model:` names the 3B URL, no fallback warning), so this is genuinely the 3B arm.

| | pp128 t/s | tg64 t/s | peak RSS |
|---|---|---|---|
| Qwen2.5-**1.5B** Q4_K_M | 22.35 ± 0.24 | 7.19 ± 0.34 | 2.86 GB |
| Qwen2.5-**3B** Q4_K_M | **10.57 ± 0.04** | **3.91 ± 0.00** | **4.79 GB** |

2.1× slower on prompt, 1.84× on generation, and **4.79 GB of a 5.9 GB box** — it fits, with ~1.1 GB of headroom.

⚠️ **THE ANSWER WAS NEVER GENERATED, so the operator's actual question is UNANSWERED for the 3B.** `RESULT: benchmarked_throughput_only`, exit status **124** (timeout), 15:01.52 wall clock, and the captured output is fourteen `>` prompts followed by `Exiting...` — llama-cli sat in interactive mode and never produced a completion. Cause: the missing single-shot flag, root-caused in #10625 as SIGPIPE under `pipefail` and fixed in this branch.

**This is the three-state contract working exactly as designed.** #10597 exists so `benchmarked` is never printed over a generation step that did not run, and it wasn't — the run graded itself `benchmarked_throughput_only` and named which half is valid. A reader cannot mistake this for a measured answer.

**What may NOT be concluded from this run:** anything about 3B answer quality. The 1.5B's answer was measured and inadequate (fabricated a causal claim, misattributed the fault, truncated); the 3B's is simply unmeasured. Re-running costs a full rebuild plus a 2 GB re-download on a 1-core box — the EXIT trap cleaned up (disk back to 7.4 G) — and needs the SIGPIPE fix on `main` first.

## Validation Performed
- `probe_lib` 27 planted controls · `probe_file` 10 · `probe_api` 16 · `probe_actions_log` 16 · `run_probes` 17 — all fire.
- **Behavioural regression check on the refactor, not just a green self-test**: the three already-shipped declarations re-run against the LIVE diag surface — `pairs_soak` PASS 7/200, `arbitration_fanout_soak` PASS 19/35, `prop_ticket_risk_soak` FAIL 0/2.
- New probes run live: research corpus **FAIL over 9,954 rows with the positive control matching 995**; E35 **PASS 1 of 200, control 40**.
- Local guard suite: **61 pass**. Three fail on missing sandbox tooling only (`No module named pytest` ×2, `lint-imports` exit 127) — CI runs those properly.
- Full `run_probes.py --run` end to end: **pass 3 · fail 2 · could_not_run 6**, with the two Actions-log probes correctly reporting `could_not_run` (HTTP 403 from the sandbox) rather than a false negative — the polarity working as designed.
- Two controls **caught the author mid-error** and are recorded rather than quietly fixed: the `>` lexicographic assertion, and a credential check that failed on its own assertion line (self-reference; the annotation is now excluded from its own evidence, with a denominator proving the names it hunts really do occur).

## Documentation Updated
- `CLAUDE.md` — the `arbitration_fanout_soak` paragraph corrected (below).
- `docs/claude/OPEN-ITEMS.json` — 4 probes added, 4 reasons rewritten, 2 observations extended.
- `docs/claude/health-review-backlog.json` — **no new row**; the existing `BL-20260831-STRAY-OCA-SWEEP-ANNOTATE-COMPUTES-A-VERDICT-AND-DISCARDS-IT` updated in place (2 lines changed, asserted before writing).
- This log + the `ROADMAP.md` ledger row.

## Contradictions or Drift Found
- ⚠️ **THE DENOMINATOR MOVED DURING THIS PR AND THE HEADLINE IS RESTATED AGAINST IT.** This work took coverage 3→7 PROBED. The denominator was 11 at branch time and is **12** after merging `origin/main`, which added `OI-20260831-RESEARCH-READ-DEBT-11-UNREAD-AND-256-SUPERSEDED-UNREAD` (a monitoring row that arrived with its own declared reason, so it does not re-open the done-condition). **7 of 12**, and the done-condition still holds: zero merely-unwritten probes. Quoting 7/11 after the merge would be a stale population, which is the one thing this repo will not have.
- **I INTRODUCED A LABEL COLLISION AND THEN SHIPPED IT FOUR TIMES — corrected here.** I stamped this work `W3` on five modules, the ROADMAP row, this log and four commit messages. **`W3` already means the MERGE SERIALIZER** in the 2026-08-31 operations plan — refuted by measurement, deliberately not built, and recorded inline in `.github/workflows/scope-overlap-audit.yml` precisely so no session re-proposes it. This work is **item 3 of the artifact's five-item work plan** (probe coverage), which is a continuation of **W2**, not W3. The two enumerations are different sequences, and a THIRD unrelated one exists (`full-system-audit W2`, ROADMAP.md:866) — so a bare W-number is ambiguous in this repo and the plan has to be named. The four module docstrings, the ROADMAP row and this log now say `work-plan item 3`; **the commit messages are immutable and still read `W3`**, which is why this row exists rather than a silent rename.
- **`BL-20260831-STRAY-OCA-SWEEP-ANNOTATE-COMPUTES-A-VERDICT-AND-DISCARDS-IT` — INDEPENDENTLY RE-DERIVED, ALREADY FILED, AND NOT FILED TWICE.** `_sweep_stray_oca_groups` builds the whole classification and its ONE call site (`ib_client.py:1829`) **discards the return value**; the only durable output is a `logger.warning` to journald. `CLAUDE.md` tells a Tier-2 reviewer the allowlist *"scopes the CANCEL, never the MEASUREMENT … so the rows a reviewer needs before widening actually exist"* — **they do not exist**, before arming a path that cancels a live position's protective legs. 5th recurrence of the ships-without-a-read-surface class. ⚠️ **It was already filed at 07:12Z today by `system-review`, with BETTER evidence than mine (three verification methods to my two).** I reached it from the other end — the probe side — wrote it up as a fresh row, and `backlog_append`'s similarity check **refused it at overlap 0.88**. That is the mechanism working, so my row was DROPPED and the existing one updated with the single thing it lacked: the consumer-side consequence, that the OPEN-ITEMS row's stated unblock condition was ARMING when the real blocker is the WRITER — a session following it would have armed a live-cancel path and still had nothing to read. A 4th resolution criterion was appended so the fix closes the probe too.
- **`CLAUDE.md` was stale by ~12 hours in the dangerous direction, and is corrected.** It read *"as of 2026-08-31T07:51Z the soak held zero rows … so the fan-out has never elected or routed anything."* Measured on the COMPLETE file (35 rows, not a truncated tail): **11 rows carry `mode: apply` / `applied: true`**, 08:07:07Z → 20:12:09Z, each naming `bybit_1`. ⚠️ **But no contested symbol has been resolved** — all 11 read `starved_count: 0` on a single account, so the fan-out has only run where per-account election is a **no-op**. Evidence the code path executes; not evidence it routes correctly under contention.
- **A candidate for E35 half (b) surfaced and is recorded, ungraded.** Trade 5250, `bybit_2`, real money, `trend_donchian_xrp_4h`, opened 2026-08-30T23:46:39Z — after the deploy — `pnlProvenance: measured`. It clears both tests that disqualified the two prior near-misses. It does **not** clear the row: `journalTrust` is `known_divergent` (and `clears_when` asks for broker truth), and it exited on its **stop**, so it says nothing about the `tp_r 50→3` change that is why that leg is singled out.

## Risks and Follow-Ups
- `probe_actions_log.py` **has never run against the real Actions API** — `api.github.com` is intercepted at HTTP 403 from a web sandbox, so its controls run against a fake server and its **first scheduled run is its real verification**. Safe only because a broken reader here reports `could_not_run`, never `fail`; its `is_not` says to suspect the reader first.
- The GPU probe's literal is the **step name**, not a decision string, because no decision string has ever occurred in a real run — inventing one would be a predicate matching nothing while looking precise. Tighten it the first time an armed run happens.
- `probes.yml` gained `actions: read` + `GITHUB_TOKEN`. Read-only; it cannot dispatch or cancel a run.

## Deferred Items
- Writing the stray-OCA soak writer (already filed by another session, not built here — it is a `src/` change on the IB order path and belongs with its own review).
- Clearing E35 half (b) — needs the venue-side read on trade 5250, which this session did not do.

## Next Recommended Sprint
- Grade trade 5250 against Bybit truth and close E35 half (b) or record why not.
- Watch the first scheduled `probes` run: if the two Actions-log rows still read `could_not_run`, the reader is the suspect, not the mechanism.
- Build the stray-OCA soak writer + its allowlist entry in one commit, then attach the probe its row now names.

## Wrap-Up Check
- Board `START` posted before the first change; `DONE` at close.
- `run_probes.py --check`: **12 monitoring rows, 7 probed, 5 with a declared reason.** Merely-unwritten probes: **zero**.
