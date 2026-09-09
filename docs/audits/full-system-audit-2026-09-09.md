# Full-System Audit — 2026-09-09

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> Program doc (the shared brain) per `.claude/skills/full-system-audit/SKILL.md`.
> Session `session_017windcijwZp3qttYNWCwYP` (MI-225, OPS LANE), branch
> `claude/mi225-full-system-audit`, manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`.
> Board `START` posted to #11336 at 2026-09-09T16:4xZ.
> Work object: `WO-20260909-THE-FULL-SYSTEM-AUDIT-AGREED-AFTER-THE-SYSTEM-REVIEW-WAS-NEVER-RUN`.

## How this audit was scoped, and the one way it deviates from the skill

The skill's **Phase 3.10** says the audit CARRIES the `/system-review` and that it
is *"not deferred to a separate session"*. **We ran them in the opposite order.**
`/system-review` landed first (MI-192B, `comms/reports/since-last/20260908T112000Z/`)
and was used to scope this audit.

That is **not** a licence to skip 3.10. Every one of its coverage items is marked
below as **CITED** (the 2026-09-08 review establishes it — with the citation) or
**RE-ESTABLISHED** (measured this run). Several have moved since, including two
real-money investigations opened on 2026-09-09.

## How this audit was nearly not run at all — carried into the RECURRENCE axis

Three predecessor sessions were spawned for this work and all three went idle on
the same question, having burned ~190k tokens between them. **They were right to
stop and the fault was in their prompts, not in them.** Three classes fall out,
and each is a candidate for a permanent detector rather than an anecdote:

1. **"Do X first so we can scope Y" leaves Y owed by nobody.** X completing hands
   Y to no one. This is how the audit was lost on 2026-09-08 — the same shape as
   `OI-20260906-RESEARCH-THAT-SPECIFIES-WORK-IS-CARRIED-BY-NOTHING`, with the
   manager as the carrier that failed.
2. **A spawn prompt that does not carry its own authorisation produces a session
   that cannot be recovered by messaging it.** Attempt #2 woke on a full
   authorisation poke, consumed 64k tokens, and went idle again with the identical
   question.
3. **The repo-rename ambiguity is a live trap for any cold session that verifies
   honestly.** 244 in-tree references to `ict-trading-bot` against a `CLAUDE.md`
   warning that *"hardcoding either name is a trap"*. A session that does the
   right thing — verify before acting — dead-ends, because the resolution is
   stated only as a warning and never positively. See the CONSISTENCY axis.

---

## Phase 0 — instrument trustworthiness

### 0a. Session tooling — both probes RE-MEASURED this session

| probe | result | note |
|---|---|---|
| `git rev-parse --is-shallow-repository` | `true` → **`false`** after `git fetch --unshallow` | 50 → **4563** commits. `--deepen=2000` was **not** sufficient (gave 2428 commits back to 2026-06-28 and left the repo still marked shallow). Any history claim made before the unshallow would have silently covered ~7 weeks. |
| direct diag reachability | **WORKS** | `bash scripts/ops/diag_fetch.sh '/api/diag/version'` → exit 0, `served by https://ict-bot.duckdns.org`. The 30–60 s issue relay is **not** needed this session. |

`DIAG_BASE_URL` in this environment is `http://141.145.193.91:8001` — raw IP over
plain HTTP, which the sandbox proxy drops. `diag_fetch.sh` self-heals it to the
Caddy host correctly. **But `CLAUDE.md` states the shipped value is
`http://158.178.210.252:8001` (the VM terminated 2026-06-16).** The field says
otherwise. See F-04.

### 0b. Rules audited for internal contradiction

- `python3 scripts/ci/check_canonical_doc_coherence.py` → **exit 0**, all 5 checks pass
  (`dead VM IP single-source`, `removed gates not described as live`, `no 7-stage ML
  ladder in catalog`, `instruction-hierarchy mirror`, `declared values match their source`).
- Contradictions found by reading rather than by the checker are recorded in the
  CONSISTENCY axis.

### 0c. The gauges — WHOSE GREENS ARE ADMISSIBLE

**This is the pass the old skill never had, and it produced the first four findings.**

| measurement | value | population |
|---|---|---|
| guard scripts on disk | **79** | `scripts/check_*.py` + `scripts/ci/check_*.py` |
| **…with a runnable failure-path self-test (bucket a)** | **54** | of 79 = **68.4%** |
| …mentioning a self-test but only exercising the happy path (bucket b) | **0** | of 79 — *measured, not assumed* |
| …with no failure-path self-test at all (bucket c) | **25** | of 79 = 31.6% |
| …registered in `guard_selftests.py::SELFTESTS` | 16 | of 79 |
| `run_guards.py::GUARDS` registry entries | **92** | — |
| …`when: None` (standing, run on every diff) | **53** | of 92 |
| …diff-scoped (inert on a clean tree) | **39** | of 92 |
| guard scripts referenced nowhere in `run_guards.py` | **5** | of 79 (4 are VM watchdogs — correct; **n=1** unaccounted) |

All 54 in bucket (a) were **executed** (`timeout 120 python3 <file> --self-test`); all exited 0.
`python3 scripts/ci/guard_selftests.py --all` exited 0.

**Two `run_guards.py` baselines, and only the second is an audit baseline:**

| invocation | result | what it means |
|---|---|---|
| `run_guards.py` (clean tree) | `PASS 53 · FAIL 0 · SKIP 39` | relevance-gated — **58% of the suite** |
| `run_guards.py --all` | **`PASS 90 · FAIL 2 · SKIP 0`** | the whole suite. Both failures environmental: `layer-guard` exited **127** (`lint-imports` absent — installed, then `Contracts: 6 kept, 0 broken`, exit 0), and `pr-landing-guard` is the documented pre-declaration state. |

**`main` is genuinely green across all 92 guards.** That is a real result and it makes the
findings below sharper rather than softer: the greens are sound, and the open question is
what is *guarded at all*.

---

## Findings — Phase 0

### F-01 · `AUD-20260909-no-instrument-measures-guard-selftest-coverage`

> ⚠️ **This finding was published in an earlier revision of this document with a
> wrong number (`16/79 = 20.3%`) and a claim that was too strong. Both are corrected
> here, and the correction is left visible because the audit's own rule is that you
> verify your own output too — hardest when it confirms what you expected.** The
> registry count of 16 is *not* the self-test population; the true failure-path
> coverage is **54/79**, established by executing all 54.

- **axis:** gauges / independence
- **claim:** Nothing in this repo measures guard self-test COVERAGE against the guard population. The concept exists **only in prose** — in `SKILL.md` and one prior audit doc — and in zero executable checks.
- **evidence:**
  ```
  $ grep -rln "self-test coverage|selftest coverage|guards have a failure-path" \
        --include=*.py --include=*.md --include=*.yml .
  ./docs/audits/full-system-audit-2026-08-23.md
  ./.claude/skills/full-system-audit/SKILL.md
        ^ the concept exists ONLY in prose. Zero executable checks.

  POSITIVE CONTROL — the probe CAN find directory-population scans that exist:
  $ grep -rn "glob(\s*[\"']check_\|iterdir()" scripts/ci/*.py scripts/*.py
  scripts/ci/check_sunset_dispositions.py:101 ... check_workflow_catalog.py:146 ...
  guard_selftests.py:253:  for src in (REPO / "deploy").iterdir():
        ^ none of them enumerates scripts/check_*.py
  ```
- **expected vs actual:** **Expected:** an instrument reporting `n_with_failure_path_selftest / n_guards`, since the skill mandates reporting exactly that ratio. **Actual:** no such instrument. `check_selftest_wiring.py` is the nearest thing and answers a *different* question — and **its docstring says so explicitly and deliberately**: *"SCOPE IS THE REGISTRY, AND THAT BOUNDARY IS CHOSEN — NOT OVERLOOKED … registering there IS the claim 'this control runs in CI'; a script that merely owns a `--self-test` makes no such claim."* Its ratio **can** fall below 100% (a registered-but-unwired name — the 2026-08-17 `collapsed-state` case it was built for), so **it is not vacuous and this is not a defect in it**. The defect is the absence of the other instrument, plus the fact that its `POPULATION: 16 … 16 covered` line reads at a glance as an all-clear on instrument trustworthiness.
- **population:** 79 guard scripts; 54 with a failure-path self-test; 16 registered; repo-wide search for a coverage measurer, with a working positive control → **n=0 found**.
- **blast_radius:** observability — but it is the *meta* instrument, so its absence silently licenses every other axis's greens.
- **tier:** 1
- **detector:** a new `check_guard_selftest_coverage.py` whose **denominator is the filesystem glob** (`scripts/check_*.py` + `scripts/ci/check_*.py`) and whose numerator is (own `--self-test` in AST) ∪ (`guard_selftests.SELFTESTS`), printing `covered/total` so the denominator is visible in the log, with a named+dated exemption file and a ratchet that can rise but not fall. Add the rule that a guard whose blast radius is *accounting* or *money-at-risk* may not be exemption-listed (this is what F-05 needs).
- **disposition:** proposed — Tier-1.

### F-02 · `AUD-20260909-guard-glob-coverage-is-a-proven-detector-that-was-never-armed`

- **axis:** recurrence
- **claim:** `scripts/ci/check_guard_glob_coverage.py` is a self-proving detector for a real, measured defect class, and in the 17 days since it was written it has never run in CI — because it is structurally unarmable as written, and nobody recorded that.
- **evidence:**
  ```
  $ git log --format='%h %ad %s' --date=short -- scripts/ci/check_guard_glob_coverage.py
  9544ced3e 2026-08-23 Retune the stop instead of refusing the trade ...

  $ grep -rn "check_guard_glob_coverage" --include=*.py --include=*.yml .   # excluding itself
  (only: comms/reports/.../report.json, docs/claude/health-review-backlog.json, one work object)
  $ grep -rn "guard_glob_coverage" .github/
  (none)
  POSITIVE CONTROL — check_api_tier_policy is found in: scripts/check_api_tier_policy.py,
      scripts/ci/run_guards.py, scripts/ci/guard_selftests.py, tests/test_check_api_tier_policy.py

  $ python3 scripts/ci/check_guard_glob_coverage.py --self-test
    self-test ok: planted glob removal -> flagged under_scoped
    self-test ok: the real table's entry reads clean
  self-test: PASS   EXIT=0

  $ python3 scripts/ci/check_guard_glob_coverage.py
  ::warning::exit-mechanism-coverage-guard: reads config/lever_reachability.json but its `when` does not match. LEAD, not a verdict — ...
  guard-glob-coverage: 39 scoped guard(s) — 1 under-scoped LEAD(s) · 14 clean · 24 not_verifiable
  EXIT=1
  ```
- **expected vs actual:** **Expected:** a detector that plants a violation and catches it (which this one demonstrably does — it clears the admissibility bar that 63 of 79 guards do not) is wired into the gate. **Actual:** it is wired into nothing, and `main()` returns `1 if bad else 0` where `bad` is the `under_scoped_unconfirmed` verdict — **the very verdict its own message calls "LEAD, not a verdict"**. Wiring it today reds every PR on a lead already investigated and dispositioned as benign on 2026-08-23, which is the desensitized-alarm P1 this repo treats as its own failure mode. So the fix is **not** "wire it".
- **population:** 39 diff-scoped guards graded by it — 1 lead · 14 clean · **24 `not_verifiable`** (the tool is scrupulous that this is *not* verified-clean).
- **blast_radius:** observability, compounding — it is the detector for "a guard is scoped to one side of its own join", which is the shape that let `exit-coverage-matrix-guard` go quiet exactly when it should not have.
- **tier:** 1
- **detector:** separate LEAD from FINDING in the exit code (`--strict` for leads, or an accepted-leads allowlist keyed to the dispositioned row), record the 2026-08-23 disposition as an accepted lead **in the guard's own data** rather than in backlog prose, then wire it into `run_guards.py` with `when: None`. **The follow-up is currently carried by the backlog row's instruction to *"re-run … after any edit to the guard table"* — a reminder, not a mechanism, which is the non-fix this repo has already paid for twice on MI-15.**
- **disposition:** proposed — Tier-1.

### F-03 · `AUD-20260909-a-clean-tree-guards-green-is-53-of-92`

- **axis:** gauges
- **claim:** `run_guards.py` without `--all` reports a green that exercised 53 of 92 guards, and its 39-guard shortfall is announced only as `skipped (not relevant)` on one line — so a session (or an auditor) reporting "guards green" from a clean checkout is reporting a 58% run.
- **evidence:**
  ```
  $ python3 scripts/ci/run_guards.py
  PASS 53 · FAIL 0 · SKIP 39
  skipped (not relevant): account-class-guard, ..., canonical-db-resolver, ...,
    env-gate-guard, ..., collapsed-state-guard, ..., provenance-consumer-guard,
    ..., silent-empty-guard, strategy-risk-guard, ...
  ```
  Registry parse: 92 entries, 53 `when: None`, 39 diff-scoped.
- **expected vs actual:** The skips are **correct behaviour** and the runner announces them deliberately (*"a guard that quietly declines to run is the 'green that checked nothing' this repo already has a rule about"*). **Expected:** an audit uses `--all`, as the file's own docstring instructs (*"`--all` ignores relevance entirely (what `workflow_dispatch` and a local audit run should use)"*). **Actual:** nothing enforces that, the summary line is identical in both modes, and the 39 inert guards include `canonical-db-resolver`, `env-gate-guard`, `silent-empty-guard`, `collapsed-state-guard`, `provenance-consumer-guard`, `strategy-risk-guard`, `risk-basis-agreement`, `qty-legalization-guard`, `dry-run-guard` and `tp-venue-cap-single-owner` — the money-adjacent set.
- **population:** 92 registry entries; 39 skipped on a clean tree; 39/92 = 42.4%.
- **blast_radius:** observability. **This is an audit-hygiene finding and I record it because I made the mistake myself before catching it.**
- **tier:** 1
- **detector:** print the mode in the summary line — `PASS n · FAIL n · SKIP n (relevance-gated)` vs `(--all, no gating)` — so the two runs are not quotable as the same sentence. A diff-scoped guard also cannot see a violation already resident on `main`; the repo has already paid for that once (`diagnostic-provenance-guard` sat at exactly 52 findings for 26 days across five review passes until an ungated `--all` step was added). **How many of the other 38 warrant the same treatment is an open question this audit is carrying.**
- **disposition:** proposed — Tier-1.

### F-04 · `AUD-20260909-claude-md-diag-base-url-value-is-stale-in-the-safe-direction`

- **axis:** consistency
- **claim:** `CLAUDE.md` states the cloud environment ships `DIAG_BASE_URL=http://158.178.210.252:8001` (a VM terminated 2026-06-16); the field now reads `http://141.145.193.91:8001`.
- **evidence:** `echo $DIAG_BASE_URL` → `http://141.145.193.91:8001`. `CLAUDE.md` § "PM-side session capabilities": *"as of 2026-08-20 the cloud environment still ships `http://158.178.210.252:8001`, the x86 micro **terminated 2026-06-16**"* (`BL-20260818-DIAG-BASE-URL-POINTS-AT-TERMINATED-VM`).
- **expected vs actual:** **Expected:** the doc's dated claim matches the field, or is marked as expired. **Actual:** the var was updated to the *current* live VM at some point after 2026-08-20 and the doc was not. **Field beats comment.** The remaining defect is smaller than documented but real: it is still raw-IP-over-plain-HTTP, which the proxy drops, so the value is inert either way and `diag_fetch.sh` carries the session. The backlog row `BL-20260818-DIAG-BASE-URL-POINTS-AT-TERMINATED-VM` is now **misnamed** — it no longer points at a terminated VM.
- **population:** n=1 env var, measured once this session.
- **blast_radius:** docs. Low severity, recorded because Phase 0c requires treating dated claims as expired until re-measured, and this is the sample that failed.
- **tier:** 1
- **detector:** `check_canonical_doc_coherence.py` already carries a `dead VM IP single-source` check **which passes** — so it detects a *dead* IP in the docs but not a *stale assertion about an env var's value*. The general detector is hard (the repo cannot read the cloud env), so the honest form is: **no detector possible for the env's value from inside the repo**; what IS detectable is that a backlog row's `id` asserts a condition its own `detail` no longer supports.
- **disposition:** proposed — Tier-1 doc fix + rename/close the backlog row.

### F-05 · `AUD-20260909-four-required-guards-have-no-failure-path-evidence-anywhere`

**The sharpest Phase 0 finding.**

- **axis:** gauges
- **claim:** Four guards that run inside the **required** `guards` merge context have neither a self-test nor any test file, so their greens have never been shown capable of turning red — including `provenance-consumer-guard`, promoted to required precisely because a write-only provenance signal let a **+$247,683.78** manufactured-PnL figure accumulate unnoticed.
- **evidence:**
  ```
  $ grep -rn "provenance_consumer\|check_provenance" tests/
        (no output)
  $ grep -rln "check_writer_conformance" tests/          # POSITIVE CONTROL, same probe
  tests/test_writer_conformance_guard.py
  $ for g in check_strategy_coverage check_news_feed_coverage check_workflow_failure_swallow; \
      do grep -rln "$g" tests/; done
        (no output for any of the three)

  $ grep -n "REQUIRED_CONTEXTS=" .github/workflows/branch-protection-sync.yml
  137:  REQUIRED_CONTEXTS='["pytest-collect","pytest-run","guards"]'
  ```
  All four run inside `guards`: `run_guards.py:1907` (`check_provenance_consumers.py --verbose`), `:1952` (`check_strategy_coverage.py --check`), `:1493` (`check_news_feed_coverage.py`), `:988` (`check_workflow_failure_swallow.py`).
- **expected vs actual:** **Expected** — the repo's own stated standard, from `branch-protection-sync.yml`'s promotion comment for `diagnostic-provenance-guard`: *"the workflow SELF-TESTS its own failure path on every run"*. **Actual** — `provenance-consumer-guard` was promoted to a required gate on 2026-07-30 with no self-test and no test file, and **still has neither**. `check_workflow_failure_swallow.py` is self-referentially bad: its subject *is* silently-swallowed CI failure.
- **population:** 79 guards → 25 with no runnable failure-path self-test → of those, 21 have a pytest file that runs in the required `pytest-run` context → **n=4** have neither. *Stated limit: it was NOT verified that each of those 21 pytest files plants a violation — only that a file naming the guard exists. Treat those 21 as unproven, not disproven.*
- **blast_radius:** **accounting** (provenance-consumer) · observability (the other three)
- **tier:** 1
- **detector:** F-01's coverage checker, with a hard rule that a guard reachable from `run_guards.py` may **not** be exemption-listed if its blast radius is accounting or money-at-risk.
- **disposition:** proposed — Tier-1.

### F-06 · `AUD-20260909-schema-fidelity-watches-8-of-26-production-tables`

- **axis:** gauges
- **claim:** `check_test_schema_fidelity.py` grades only 8 of the 26 tables production creates, so a fixture declaring a phantom column on any of the other 18 is silent **by construction, not by measurement** — and the guard's clean line states its *file* denominator while never stating its *table* denominator.
- **evidence:**
  ```
  $ grep -n "^WATCH" scripts/ci/check_test_schema_fidelity.py
  WATCH = {"trades","order_packages","signals","position_telemetry",
           "backtest_results","balance_snapshots","prop_tickets","prop_fills"}

  (independent parse of every CREATE TABLE + ALTER TABLE ADD COLUMN in src/**.py)
  PRODUCTION TABLES (n=26) ... NOT WATCHED (n=18): _ingest_meta,
    account_context_snapshots, backtest_sweeps, bybit_transaction_log,
    daily_risk_state, dataset_builds, db_pulls, device_tokens, exchange_fills,
    exchange_funding, experiment_runs, insights_history, insights_usage,
    learning_progress, model_registry, prop_account_status, strategy_versions,
    training_cycle
  ```
- **expected vs actual:** **Expected** (from `test-schema-fidelity: clean over 1096 test file(s)`) — the fixture-schema class is closed. **Actual** — closed over 8/26 = **30.8%** of production tables. `daily_risk_state` is the per-account daily-PnL / equity-high store; `exchange_fills` is the broker-truth store the IBKR PnL reader depends on.
- **population:** 26 production tables parsed from `src/**/*.py` (`src/units/db/database.py` alone carries **24** `ADD COLUMN` migrations, which were folded in — verified by `trades.reconcile_status` resolving present); 8 watched.
- **blast_radius:** accounting
- **tier:** 1
- **detector:** make the guard print `WATCH` as a fraction of the *discovered* production-table set on every run, and fail when a production table is redeclared in `tests/` while absent from `WATCH` — i.e. make the gap visible rather than silent.
- **disposition:** proposed — Tier-1.

### F-07 · `AUD-20260909-schema-fidelity-prints-clean-over-zero-files`

- **axis:** gauges (unprovenanced diagnostic, sub-class C)
- **claim:** Run with no argument, `check_test_schema_fidelity.py` prints the word "clean" over a **zero denominator**.
- **evidence:**
  ```
  $ python3 scripts/ci/check_test_schema_fidelity.py
  test-schema-fidelity: clean over 0 test file(s)     RC=0
  $ python3 scripts/ci/check_test_schema_fidelity.py --all
  test-schema-fidelity: clean over 1096 test file(s)  RC=0
  ```
- **expected vs actual:** **Expected** — a run over nothing reports *nothing to check*, distinguishable from a clean sweep. **Actual** — both print `clean over N test file(s)` with the same verb; only the integer separates them, and a human scanning for "clean" cannot tell them apart. Mitigated in CI (`run_guards.py:1336-1341` gates the step on `^tests/.*\.py$`), so this bites the human running the command bare.
- **population:** 2 invocations of 1 script; n=1096 in the `--all` arm.
- **blast_radius:** observability
- **tier:** 1
- **detector:** `diagnostic-provenance-guard` already owns this class and its declared scope is `scripts/{ml,research,ops,macro,reports}/` + the guard scripts. **That this site survives is either a scope gap or an unfired rule, and which was NOT established.** Stated as a limit, not a conclusion — resolving it is itself the follow-up.
- **disposition:** proposed — Tier-1.

### F-08 · `AUD-20260909-the-audit-skills-own-motivating-numbers-are-stale`

**A finding against the instrument that commissioned this audit.**

- **axis:** consistency / recurrence
- **claim:** Two of the three measured claims `SKILL.md` uses to justify its own existence are stale, one of them describing a class that is now **fully closed** — so a session reading the skill would open an audit on a swept class.
- **evidence:** independent re-measure with a working positive control:
  ```
  prod tables: 26 | order_packages cols n=18 | has 'id': False
  trades has reconcile_status: True          <- migrations WERE folded in
  test files with CREATE TABLE: 119
  order_packages.id declarers n= 0
  POSITIVE CONTROL — plant "id INTEGER PRIMARY KEY," into
    tests/ml/datasets/test_account_context.py's order_packages CREATE TABLE:
    [('order_packages', True, ['id'])]      <- detected
  ```
  The stale assertions:
  ```
  SKILL.md:52  - **20 test files** still declare `order_packages.id` ...
  SKILL.md:57  - **10 of 41 guard scripts** have a failure-path self-test.
  SKILL.md:136   Report the ratio (was 10/41 on 2026-08-20).
  run_guards.py:1332  # The fix at the time swept the pairs tests; 20 other files
                      # still declare it (measured 2026-08-20).
  ```
- **expected vs actual:** **Expected** per SKILL.md — 20 offending files; 10 of 41 guards self-tested. **Actual** — **0** offending files over a 119-file denominator, and **54 of 79** guards self-tested. The `order_packages.id` class the skill cites as *"the class was never swept"* **has since been swept**, and nothing recorded that.
- **population:** 1096 files under `tests/`; 119 containing a `CREATE TABLE`; 26 production tables; 79 guard files.
- **blast_radius:** docs — but it misdirects every future audit, which is the skill's whole purpose.
- **tier:** 1
- **detector:** **no detector is possible for a frozen integer in a prose skill file.** The durable fix is to make `SKILL.md` quote the *command* (`check_test_schema_fidelity.py --all`, and F-01's coverage script) rather than a number, so the figure cannot go stale independently of the measurement that produced it.
- **disposition:** proposed — Tier-1.

### F-09 · `AUD-20260909-sixteen-selftests-never-exercise-the-guard-entrypoint`

- **axis:** gauges
- **claim:** 16 of the 44 `--self-test` guards assert only on an internal grading function's return value and never on `main()`/the process exit code, so a regression in the wiring between *finding computed* and *job fails* would not be caught by the self-test.
- **evidence:** partition of each self-test region on `subprocess.run|_rc_out|_rc(|main([|main(argv|returncode` → **28** assert on process/`main()`, **16** assert only on an internal return: `check_artifact_caveats`, `check_decision_answers`, `check_document_index`, `check_guard_glob_coverage`, `check_matrix_bracket_values`, `check_matrix_config_agreement`, `check_matrix_corpus_agreement`, `check_open_items`, `check_recurrence_ledger`, `check_register_ids`, `check_risk_basis_agreement`, `check_role_pack_operating_layer`, `check_soak_registered`, `check_stated_population`, `check_wip_ceiling`, `check_workflow_trigger_reachability`.
- **expected vs actual:** **Expected** — a self-test proves *the guard* fails on a plant. **Actual** — for 16 of 44 it proves *a function inside the guard* returns a finding on a plant; the last hop is unasserted. **All 16 `main()` bodies were read and each propagates correctly today**, so this is a *weak* green, not a *false* one — a structural weakness in the instrument, not a live break.
- **population:** the 44 guards declaring `--self-test`; all 16 `main()` bodies read by hand.
- **blast_radius:** observability
- **tier:** 1
- **detector:** require each self-test to include at least one assertion on `main([...])`'s return or a `subprocess` return code — statically checkable inside F-01's coverage script.
- **disposition:** proposed — Tier-1.

### F-10 · `AUD-20260909-required-vs-advisory-is-clean` — **VERIFIED NON-ISSUE**

Recorded because the skill requires a clean result to state what would have counted as a finding.

- **claim tested:** that a money-at-risk or order-path guard sits in a non-required (advisory) context, so its green gates nothing.
- **evidence:** `REQUIRED_CONTEXTS='["pytest-collect","pytest-run","guards"]'` with `ENFORCE_ADMINS=true`. Of the 4 workflows triggering on `pull_request`, only `repo-inventory` is non-required — and it self-declares `# ADVISORY ONLY — this workflow never fails the PR`. `qty-legalization-guard`, `dry-run-guard`, `strategy-risk-guard`, `pairs-sizing-basis-guard`, `risk-basis-agreement`, `tp-venue-cap-single-owner`, `provenance-consumer-guard` and `collapsed-state-guard` are **all** inside the required `guards` job.
- **expected ≠ actual is NOT met**, so this is not a finding.
- ⚠️ **STATED LIMIT, and it is a real one:** only the **declared** spec in `branch-protection-sync.yml` was verified — **not the live branch-protection state on GitHub**. `api.github.com` is 403 at the proxy and no MCP tool in this session reads branch protection. If `BRANCH_PROTECTION_TOKEN` has lapsed, the live required set could have drifted from that file while every "required" claim above still rests on the declaration. **This is carried as an open item, not as a closed one.**

---

## INADMISSIBLE GREENS — instruments whose output is NOT audit evidence

Phase 0's required output. Established by measurement, not by inspection of names.

| # | instrument | why its green proves nothing |
|---|---|---|
| 1 | `scripts/check_provenance_consumers.py` | Required gate since 2026-07-30. **No self-test, no test file.** It is the declared preventer of the fabricated-PnL class; its green is unproven on the exact axis it was promoted for. |
| 2 | `scripts/check_strategy_coverage.py` | Same — no self-test, no test file, inside the required `guards` job. |
| 3 | `scripts/ci/check_news_feed_coverage.py` | Same. |
| 4 | `scripts/ci/check_workflow_failure_swallow.py` | Same — and its subject *is* silently-swallowed CI failure. |
| 5 | the other 21 in bucket (c) — incl. `check_qty_legalization_guard`, `check_dry_run_in_diff`, `check_strategy_risk_field_in_diff`, `check_canonical_db_resolver`, `check_new_table_wiring`, `check_writer_conformance`, `check_silent_empty_in_diff` | No failure-path self-test. Each **has** a pytest file, but those tests were **not** verified to plant a violation — so **unproven, not disproven**. Several are order-path/money-adjacent. |
| 6 | `scripts/ci/check_guard_glob_coverage.py` | Passes its own self-test; runs in **no** workflow and **no** registry. It has never produced a CI green at all (F-02). |
| 7 | `check_selftest_wiring.py`'s `16/16 covered` | Answers *are registered self-tests invoked?*, not *do guards have self-tests?*. Never quote it as a coverage figure. |
| 8 | `check_test_schema_fidelity.py: clean over 1096 test file(s)` | True and independently reproduced — but only over **8 of 26** production tables (F-06). Admissible only for those 8. |
| 9 | the same guard's `clean over 0 test file(s)` | Zero denominator printed with the word "clean" (F-07). |
| 10 | `repo-inventory` green | Advisory by design. Gates nothing. |
| 11 | the 16 self-tests in F-09 | Prove the grading logic fires; do not prove the exit code follows. Weak, not false. |
| 12 | **a `SKIP` from any of the 39 diff-scoped guards** | A PR's `guards` green means *"no **relevant** guard failed"* — never *"all 92 checked this"*. State which fired before citing one (F-03). |
| 13 | `SKILL.md` lines 52 / 57 / 136 | Stale by measurement (F-08). Do not carry those numbers into any report. |


---

## Phase 3.3 — INDEPENDENCE. **The most important section of this audit.**

The axis asks: *can each claim be falsified by evidence its own producer does not
control?* It is the pass that would have caught `protection_coverage`. **It caught
the same shape again, in a different module, and this time on the mechanism whose
entire job is to be the independent falsifier.**

### F-11 · `AUD-20260909-closed-flat-invariant-reads-a-failed-exchange-read-as-flat` — 🔴 **money-at-risk, Tier-2**

- **claim:** The closed→exchange-flat invariant — the one mechanism that can independently contradict *"this trade is closed"* — returns `0.0` (flat ⇒ no violation) when the exchange read **fails**. Its "no violations" verdict cannot be distinguished from blindness, and it is blinded by exactly the condition it exists to catch.
- **evidence:** `src/runtime/closed_flat_invariant.py:243-244`
  ```python
  def _residual_from_positions(positions: Optional[Iterable[Any]], ...) -> float:
      if not positions:
          return 0.0
  ```
  Its input is `clients.py::account_open_positions`, **whose own docstring says the opposite is required**: *"`None` on any failure path so callers can distinguish 'no positions' (`[]`) from 'could not read' (`None`)"* — including *"**IB only:** an EMPTY snapshot from a Gateway that is NOT verified logged-in"*. `_exchange_residual_qty` also returns `0.0` at `:210`, `:223`, `:232` (account unresolvable; `account_open_positions` raised; `fetcher()` raised). The caller reads `if residual == 0.0: continue` as *no violation*.
  **Positive control that the convention is otherwise honoured:** `src/runtime/hourly_report.py:335` — `open_count = len(positions) if isinstance(positions, list) else None`.
- **expected vs actual:** **Expected** — a failed or unverifiable exchange read grades `unknown` and does **not** clear the invariant. **Actual** — 4 distinct failure paths *and* an explicit `None` sentinel all collapse to `0.0`.
- **population:** 5 of 5 early-return sites in the module's exchange-read path. Of the 2 consumers of `account_open_positions` in `src/` outside `order_monitor`, **1 is correct and 1 collapses**.
- **detector:** register `closed_flat.residual_state` in `check_collapsed_states.py::CONTRACTS` with `flat` / `residual` / `could_not_look`. **That guard already exists and already fails on a state no consumer branches on** — this needs a registration, not a new instrument.
- **tier:** 2 · **disposition:** proposed (Tier-2 — the operator decides; not enacted)

### F-12 · `AUD-20260909-closed-flat-window-is-shorter-than-its-own-invocation-period` — 🔴 money-at-risk

- **claim:** The invariant looks back **60 s** of `closed_at` but is invoked once per trader tick, whose period is measurably **≥ 101.9 s** — so on *every* tick there is a window in which a trade can close and be examined by nobody. **By arithmetic, not by failure.**
- **evidence:** `closed_flat_invariant.py:62` `DEFAULT_WINDOW_SECONDS = 60`; `_closed_flat_wiring.py:114` calls `check()` with **no** `window_seconds` override; scheduling is sleep-**after**, not period-targeting (`src/main.py:1124`, `end_time` computed *after* the tick body).
  Two independent live measurements:
  - `/api/diag/tick_cost` → `mean_ms 64327.0`, `max_ms 78320.8` ⇒ implied period **124.3 s** mean / 138.3 s max.
  - **Durable and not produced by the tick timer** — `/api/bot/logs?level=warn&limit=1000`, 888 rows spanning 2026-08-26 → 2026-09-09. Inter-arrival gaps of the once-per-tick `strategy_builder exception` row: `n=140 mean=278.7s median=296.1s min=101.9s`, and **gaps ≤ 60 s: 0 of 140 = 0.0%**.
- **expected vs actual:** **Expected** — a lookback ≥ the invocation period, so consecutive checks tile the timeline. **Actual** — ≥ **41.1%** of every period is unexamined at the tightest observed bound, **51.7%** at the `tick_cost` mean.
- **population:** window = 1 constant, never overridden (1 of 1 call sites). Period: n=4 ticks (`tick_cost`) **and** n=140 gaps over 14.7 days. ⚠️ *The 140-gap series only fires while MGC candles are unavailable, so its upper tail overstates the period; **only its minimum (101.9 s) is a sound bound**, and that is what the claim uses.*
- **detector:** derive the window from the caller's own cadence (`max(DEFAULT, since_last * 1.5)`), publish it on a `closed_flat_coverage` soak, and add a CI test failing any `check()` invocation whose window is not cadence-derived.
- **tier:** 2 · **disposition:** proposed

### F-13 · `AUD-20260909-invariant-violations-has-no-read-surface-and-its-alert-never-pages` — money-at-risk (gated)

- **claim:** The falsifier's **output** is unreadable from every surface a session or the operator has: `runtime_logs/invariant_violations.jsonl` is **not** on the diag `log_file` allowlist, and the alert is emitted at `Level.WARN`, which `outcomes` explicitly excludes from Telegram.
- **evidence:** `closed_flat_invariant.py:55` writes `invariant_violations.jsonl`; `:371` `report(channel, level=Level.WARN, ...)`; `outcomes.py:76` `_TELEGRAM_LEVELS = {Level.ERROR, Level.CRITICAL}`. Allowlist scan: 57 names vs 23 `runtime_logs/*.jsonl` writers in `src/` → **6 unreadable**, of which 4 have an alternate surface and **`invariant_violations` has none** (its only mention outside `src/runtime/` is `enable_closed_flat_invariant.sh:135`, an SSH-only `tail -f` instruction). Live WARN feed n=888 over 14.7 d: **`closed_flat_invariant` rows = 0**, with the feed's own composition (857 `strategy_builder` / 31 `pairs_half_open`) proving it is non-empty.
- **F-11 + F-12 + F-13 compose, and the composition is the finding:** the invariant cannot see a failed read, cannot cover ~half its own timeline, writes where nobody can look, and pages at a level that never reaches the operator. **Its 14.7-day zero is not evidence of anything.** There is no planted-violation self-test and no `controls_ok`.
- **detector:** extend `check_new_table_wiring.py`'s logic to jsonl — a module writing `runtime_logs/<name>.jsonl` must have `<name>` in `diag._LOG_FILES` or a registered alternate reader.
- **tier:** 1 · **disposition:** proposed

### F-14 · `AUD-20260909-silent-empty-guard-covers-13-percent-of-its-own-class`

- **claim:** `silent-empty-guard` protects 4 path prefixes + 3 files, while the class it names lives overwhelmingly outside them — and **55 `# allow-silent:` overrides sit in files the guard never reads**, i.e. authors suppressed a guard that would never have fired.
- **evidence:** `_PROTECTED_PREFIXES = ("src/web/api/", "src/units/db/", "scripts/macro/", "scripts/research/")`. AST scan of every non-test `.py` for a broad handler returning an empty/falsy value:
  ```
  broad-except handlers returning empty/falsy: n=476
    INSIDE guard scope : 64          OUTSIDE : 412  (86.6%)
  top uncovered:  29 src/runtime/order_monitor.py · 19 src/units/accounts/clients.py
                  18 src/runtime/execution_diagnostics.py · 15 src/units/accounts/ib_client.py
  `# allow-silent:` markers (non-test):  IN scope 137  ·  OUT of scope 55
    incl. 17 in clients.py, 2 in risk.py (:566/:612 — the sizing/equity reads)
  ```
  **F-11's own two sites carry `# allow-silent:` markers and are never scanned.**
- **expected vs actual:** **Expected** — the guard's path scope matches the concept it declares. **Actual** — **86.6%** of the class is outside it, including every one of the repo's *own* named instances. This is the `check_strategy_risk_field_in_diff.py` shape — *guard boundary ≠ concept boundary* — one guard over.
- **detector:** two parts. (a) widen the prefixes to `src/runtime/`, `src/units/accounts/`, `src/prop/`, `src/core/` — **diff-scoped, so existing sites stay grandfathered and no PR reds on day one**. (b) **a check that fails when an `# allow-silent:` marker appears in a file the guard cannot scan** — a suppression for a guard that cannot fire is proof of a scope/concept mismatch, and is the cheapest possible detector for this class.
- **tier:** 1 · **disposition:** proposed

### F-15 · `AUD-20260909-collapsed-state-guard-credits-tests-and-docstrings-as-consumers`

- **claim:** `collapsed-state-guard`'s central check — *"every declared state is branched on by a consumer"* — is satisfied by **token presence anywhere in any `.py` file, `tests/` and docstrings included**, so for 28 of 129 declared contract-states the only evidence anything reads them is a test assertion or a comment.
- **evidence:** `_states_in` matches `re.search(rf"[\"']{state}[\"']", body)` over the whole file; the consumer loop iterates `_py_files()`, which **does not exclude `tests/`**. Re-running the guard's own computation over its own 2180-file set: **28 of 129 states across 9 contracts** have only test-or-docstring evidence. Worked example — `position_telemetry.finality_source`: `stamped` / `derived_join` / `not_final` each have exactly two "consumers": one test file, and `diag.py:2705/2707/2709` **all inside a docstring**. The guard nevertheless prints `ok position_telemetry.finality_source: 3 consumer(s), all states read`.
- **expected vs actual:** **Expected** — 1 unread contract reported (the grandfathered one). **Actual** — 8 further contracts pass on test-or-docstring evidence alone, against the guard's own docstring: *"A state nothing reads IS the collapse."*
- **detector:** split the consumer count into production / test / docstring and print all three; fail (or grandfather with a printed debt list, as it already does) when production-consumer count is 0. The docstring exclusion can reuse the `ast` walk already in `_import_line_numbers`.
- **tier:** 1 · **disposition:** proposed

### F-16 · `AUD-20260909-diag-journal-silently-ignores-every-filter-it-does-not-implement`

- **claim:** `/api/diag/journal` — the surface sessions use to check money-path claims — accepts only `table` and `limit`, **silently discards every other query parameter**, returns a bare list with no request echo, and answers 200.
- **evidence:** live, with a positive control:
  ```
  == POSITIVE CONTROL: limit IS honoured ==
  ?table=trades&limit=1 -> 1 row  ids=[5610]
  ?table=trades&limit=3 -> 3 rows ids=[5610, 5609, 5608]
  == IGNORED PARAMS: all return identical rows ==
    ?where=id%3D5258 · ?account_id=ib_paper · ?status=open · ?symbol=MES
    · ?nonsense_param=1   ->  ALL [(5610,'AVAXUSDT'), (5609,'SLV'), (5608,'SLV')]
  ```
  **The auditing agent was itself misled by it**: three different `where=` values for trades 5258/5573/408 all returned trade 5610.
- **expected vs actual:** **Expected** — an unsupported filter is a 400, or the envelope reports `filter_state: ignored_unknown_column`. **The repo has already built exactly this** — `db_explorer.filter_state`, a registered three-state contract. **Actual** — 200, no envelope, no signal.
- **population:** 25 diag GET routes; **3 return a bare `list[...]`** (`/audit`, `/journal`, `/timers`); 22 return an envelope. 5 of 5 probed non-implemented params silently dropped.
- **blast_radius:** accounting — a session attributes one trade's fields to another.
- **detector:** register `diag_journal.filter_state` with `db_explorer`'s three states; generalise as a CI check that no `@router.get` in `diag.py` returns a bare `list[...]`.
- **tier:** 1 · **disposition:** proposed — ⚠️ **routes to MI-221/MI-222, who hold `diag.py`. Not edited here.**

### F-17 · `AUD-20260909-journal-read-failure-reports-flat-and-disarms-the-netting-guard-together` — 🔴 money-at-risk

- **claim:** A **single** trade-journal read failure makes the intent layer size a **full** open (position reads flat) **and simultaneously disarms the no-pyramiding netting guard** (reads "no open trade"), and the only trace is a `logger.warning` on a surface with ~30 min retention.
- **evidence:** `coordinator.py:2003` → `current_net_position_qty(...)` → persisted as `pkg.meta["execution_delta"]["current_qty"]` at `:2025`. Producer `positions.py:194-200`: `except Exception: logger.warning(... "(treating as flat)"); return 0.0`. The guard consulted three lines later at `coordinator.py:2058` is `has_open_trade_for_strategy`, `positions.py:127-133` — **same sqlite file, same broad except, `return False`**, docstring: *"a missing journal or read failure returns `False` … → don't block"*. The account+symbol sibling `coordinator.py:_has_open_position` (`:96-105`) logs **nothing** at all.
- **expected vs actual:** **Expected** — an unreadable journal degrades to refuse/hold, or at minimum stamps a read-state so a downstream reader can tell flat from unread. **Actual** — the quantity *and its guard* fail permissive on the same underlying failure, and `execution_delta` persists `current_qty: 0.0` with no read-state. **`0.0` is a real value (genuinely flat), so this is not even detectable after the fact.**
- **population:** 3 journal-read helpers on the intent/dispatch path; **3 of 3 fail permissive to a falsy value**, 2 of 3 log to journald only, 1 of 3 logs nothing, **0 of 3 emit a durable read-state**.
- **detector:** register `position_read.state` (`measured` / `journal_absent` / `unreadable`) with producer `positions.py`, and carry it into `execution_delta` so `/api/diag/audit_query` can count it.
- **tier:** 2 · **disposition:** proposed

### UNFALSIFIABLE VERDICTS — ranked by blast radius

| # | published verdict | why nothing can contradict it | surface that would falsify it |
|---|---|---|---|
| 1 | **"No closed→exchange-flat violations"** | Produced by the same code that reads the exchange, and a failed read produces the *clean* value (F-11). Output has no read surface; alert never pages (F-13). **0 rows in 888 WARN entries over 14.7 d with no positive control anywhere** — no synthetic exercise, no `controls_ok`, no planted-violation self-test. | a `closed_flat_coverage` soak writing `{trades_examined, window_seconds, residual_state, exchange_read_state}` per invocation. One row with `exchange_read_state=could_not_look` and `trades_examined>0` contradicts it immediately. |
| 2 | **Every close is examined by the invariant** | Coverage is published nowhere; the 60 s-vs-101.9 s gap is derivable only by joining `tick_cost` to a source constant (F-12). | the same soak, carrying `window_start`/`window_end`. Two consecutive rows whose windows do not touch is the contradiction. |
| 3 | **`execution_delta.current_qty` is the real net position** | `0.0` is emitted for genuinely-flat *and* for unreadable, persisted with no read-state; the distinguishing WARNING lives only in journald at ~30 min retention (F-17). | a `read_state` field beside `current_qty` — already persisted via the signals dual-write, so `/api/diag/audit_query` reads it for free. |
| 4 | **`RiskManager` refusals are complete** | `approve()` is a bare `bool` discarding `evaluate()`'s reason; equity/daily-state reads degrade to `None` under `# allow-silent` markers the guard never scans. Refusals are journaled — **an approval taken on an unreadable equity is not.** | stamp the equity read-state onto the **approval** path, not only the refusal path. |
| 5 | **IB `protection_coverage`** | It *does* have an independent surface (`/api/diag/ib_open_orders`) — **but that surface is knowingly biased toward confirming it**: its envelope ships `stale_read_caveat: "may include orders already cancelled by another client"`, because `_open_trades` reads ib_insync's accumulated cache and `reqAllOpenOrders` only ADDS. **It over-reports resting legs — the exact direction that would hide the original defect.** | prune the local cache against each `reqAllOpenOrders`, or serve from a fresh client per call. Removing `stale_read_caveat` is already the fix's stated done-condition. |
| 6 | **"34 contracts … clean"** (`collapsed-state-guard`) | evidence is token presence in any `.py`, tests and docstrings included (F-15). | split the consumer count three ways and print all three. |
| 7 | **"No new silent-empty read paths"** | the guard reads **13.4%** of the class; 55 suppressions sit in files it never opens (F-14). A green is a statement about `src/web/api/`, **not** about the order path. | widen the prefixes + add the misplaced-marker check. |
| 8 | **A `/api/diag/journal` result answers the question asked** | bare list, no echo, unknown params dropped (F-16). | envelope + `filter_state`, the pattern `db_explorer` already ships. |

### Verified NON-issues on this axis — stated with the probe that could have returned a positive

- **`has_protective_orders` used for naked-detection anyway** (explicitly hunted): **0 non-test, non-docstring call sites in `src/`**. `order_monitor.py:7925/:8308` name it only to say the sweep does *not* use it. **Positive control:** the same grep finds 8 live call sites in `tests/test_ib_naked_rearm.py`. **No finding.**
- **Live protection coverage, all three venues, computed from raw order rows rather than read off a verdict.** IB `ib_paper` @16:46Z: MHG 30 long / 30 STP / 30 LMT · MGC 32 short / 32 / 32 · MES 15 long / 15 / 15 — **3/3 fully two-sided**. Alpaca @16:45Z: `alpaca_paper` 9 positions, `alpaca_portfolio` 7 — **16/16 symbols at exactly 1.00× on both the stop and the target side**. What would have counted: any symbol with `stop_qty < pos_qty` or `tp_qty == 0`. **No finding — and this is the audit's strongest positive result.**
- **The Bybit `rows[0]` book-selection reducer** (`BL-20260908-…-A-HEDGE-BOOK-READS-FLAT`): **FIXED** — `_bybit_position_protection` now calls `_bybit_book.select_position_row(rows)`, a five-state pure function that refuses loudly when `not selection.is_usable` and carries `position_idx: None` meaning *"we named no book"*. ⚠️ **`CLAUDE.md`'s row saying the fix is "written out … and NOT applied" is STALE.** Field beats comment.

---

## Phase 3.5 — LIVENESS. The zombie hunt.

### F-18 · `AUD-20260909-commit-to-main-is-rate-limited-so-the-registers-do-not-land` — 🔴 **live right now**

- **claim:** The shared `commit-to-main` action cannot open its PR because the account's GraphQL rate limit is exhausted, so **every scheduled producer that lands a receipt through it pushes to an `automation/*` branch that is never merged** — `DUE.md`, `READOUT.md`, `PROBES.json` and the strategy-review packets are stale on `main` while their workflows report as "running".
- **evidence:** run 34344242030 (constraint-readout, 11:11:33Z):
  ```
  * [new branch] HEAD -> automation/constraint-readout-34344242030-1
  ##[warning]commit-to-main: gh pr create attempt 1/3 failed:
       GraphQL: API rate limit already exceeded for user ID 119055177.
  ##[error]commit-to-main: could not open a PR ... after 3 attempts.
       THE ROWS ARE NOT LOST — they are pushed to automation/...
  ```
  Identical at run 34338586205 (due-list, 10:08:13Z).
  **Independently re-verified by the audit lead, from git rather than from the agent:**
  ```
  $ git branch -r | grep -c 'origin/automation/'                      -> 164
  $ git branch -r --no-merged origin/main | grep -c 'origin/automation/' -> 164   (ALL unmerged)
  $ git show origin/main:docs/claude/PROBES.json | jq -r .generated_at
       2026-09-07T10:31:05  (TODAY IS 2026-09-09)
  READOUT.md: "Generated 2026-09-08T11:25:27" · DUE.md: "Generated 2026-09-08T05:10:04"
  ```
  ⚠️ **Third, strongest corroboration: the audit lead hit the identical limit this session** — `update_pull_request` on #11571 returned `API rate limit already exceeded for user ID 119055177`. This is not a historical log entry; it is live.
- **expected vs actual:** **Expected** — a daily register render lands on `main`, and `READOUT.md`/`DUE.md` describe today. **Actual** — the render is pushed to a throwaway branch, the PR is refused, and **164 unmerged receipt branches have accumulated**. Note the session brief in `CLAUDE.md` is rendered *from these very files*, so **the brief every session reads at startup is being served from stale registers.**
- **population:** 2 failing runs read in full of 8 daily-cron failures observed; 164 orphan branches, 164 of 164 unmerged.
- **detector:** a guard reading `DUE.json`/`CONSTRAINT.json` `generated_at` **on `main`** against wall clock, failing when older than 2× the declared cadence — i.e. **grade the landed artifact, not the run conclusion**. `check_digest_liveness.py` already has this shape for the digest; nothing has it for DUE/READOUT/PROBES.
- **tier:** 1 · **disposition:** proposed — **flagged loudly; this is degrading now.**

### F-19 · `AUD-20260909-crons-fire-on-a-systematic-4-4h-lag-not-erratically`

- **claim:** `CLAUDE.md`'s *"scheduled workflows fire LATE and ERRATICALLY"* both understates and **mis-describes** it: daily crons fire on a **systematic ~4.4 h lag stable to within ±5 min per workflow across days**, and hourly crons fire at **~25% of their declared rate**.
- **evidence:** `event=schedule`, window 2026-09-07T19:08Z → 2026-09-09T16:38Z (45.5 h):
  ```
  purge-artifacts      3:00Z -> 07:52:56 (+292.9m) ; 07:55:48 (+295.8m)
  replay-pregate       4:00Z -> 08:40:19 (+280.3m) ; 08:44:40 (+284.7m)
  probes               5:20Z -> 09:48:37 (+268.6m) ; 09:51:41 (+271.7m)
  due-list             5:50Z -> 10:05:54 (+255.9m) ; 10:08:13 (+258.2m)
  constraint-readout   6:05Z -> 11:07:32 (+302.5m) ; 11:11:33 (+306.6m)
  hourly rate (actual/expected): error-feed-digest 0.24 · work-digest 0.26
                                  work-decision-commit 0.44 · session-reaper 0.66
  6-hourly are near-nominal:      broker-bracket-reconcile 7/7.6 · pr-queue-watch 7/7.6
  ```
- **expected vs actual:** **Expected** — jitter around the declared minute. **Actual** — a near-constant +4.4 h offset (min +2.0 h, max +5.2 h), **reproducible day over day**, plus a ~75% drop rate on sub-6-hourly schedules. A systematic offset is a *different, more tractable* problem than jitter — it can be corrected by re-declaring the cron; jitter cannot.
- **population:** n=23 daily-cron firings across 13 workflows; n=100 scheduled runs across 21 workflows; denominator = 26 workflows declaring a `schedule:`. ⚠️ **5 weeklies fell outside the window and are UNMEASURED, not clean.**
- **detector:** a check that reads each declared cron, pulls its `event=schedule` history, and fails when median lateness exceeds a threshold **or** actual/expected firings fall below a floor. Nothing measures either today; every consumer reasons from the cron string.
- **tier:** 1 · **disposition:** proposed

### F-20 · `AUD-20260909-exchange-map-omits-the-live-futures-venue` — 🔴 money-at-risk

- **claim:** `integrator.py::EXCHANGE_MAP` has **zero production call sites** and **omits `interactive_brokers`** — the exchange behind the only live futures account — so the CI contract that *"every integration declares its management caps"* is structurally blind to the venue it most matters for.
- **evidence:** `rg -rn "EXCHANGE_MAP\[" src/ scripts/` → **0 hits** (positive control: `EXCHANGE_MANAGEMENT_CAPS` **is** read at `health.py:376`). Real dispatch is hardcoded strings in `execute.py:804/1226/1304/3028`. `EXCHANGE_MAP = {bybit, breakout, oanda, alpaca}` vs **5** distinct `exchange:` values across 11 accounts in `accounts.yaml`. The guard `tests/test_ltmgmt_p5_contract_ci.py:59` iterates **MAP → CAPS only**; there is no assertion that every exchange in `accounts.yaml` appears in `EXCHANGE_MAP`. `interactive_brokers` backs `ib_paper` (`mode: live`, 9 strategies) and `ib_live`.
- **expected vs actual:** **Expected** per `ROADMAP.md:718` (S-AUDIT-E explicitly **KEPT** it as the integration registry) — it enumerates the integrations. **Actual** — 4 of 5, and the missing one is the live futures venue. A new IB management op could ship with no caps entry and the guard would pass.
- **detector:** add the reverse assertion — every `exchange:` in `accounts.yaml` must appear in `EXCHANGE_MAP`. **It fails today, which is the point.**
- **tier:** 1 · **disposition:** proposed

### F-21 · `AUD-20260909-thirty-six-pages-in-45-hours-from-one-root-cause`

- **claim:** `claude-run-failure-alert.yml` fired on **36 scheduled-run failures across 12 workflows in 45.5 h (~19/day)**, the large majority sharing the single `commit-to-main` rate-limit cause of F-18 — the desensitized-alarm P1 `CLAUDE.md` names as itself a first-class bug.
- **evidence:** watch list = 34 entries; joined to the 100-run window: `error-feed-digest` 8/11 failed · `pr-queue-watch` 6/7 · `session-reaper` 6/10 · constraint-readout 2/2 · due-list 2/2 · probes 2/2 · strategy-review 2/2 · replay-pregate 2/2 · **36 failures across 12 of 34 watched workflows**.
- **expected vs actual:** **Expected**, from the workflow's own header (*"an alarm that fires on everything is itself a P1 bug"*) — a page is rare and actionable. **Actual** — ~19 pages/day, ≥5 workflows failing for one shared cause.
- **detector:** de-duplicate by cause before paging (group on the failing step's error signature; page once per distinct cause per window, with a count), **and grade the alarm's own health** — a check failing when watched-workflow failures exceed N/day.
- **tier:** 1 · **disposition:** proposed

### F-22..F-27 — the remaining liveness findings, in brief

| id | claim | pop. | tier |
|---|---|---|---|
| **F-22** `log-advisory-scores-defined-never-called` | `Coordinator.log_advisory_scores` has **zero production call sites** (tests only) while `/api/diag/log_file?name=advisory_decisions` documents it as that file's producer — and the file has not been written in **76 days** (last `2026-06-25`), *after* advisory heads went live 2026-08-02/08-04. Same-run controls fresh within hours. | 1 of 56 log surfaces | 1 |
| **F-23** `prop-ticket-risk-soak-dead-10-days` | `prop_ticket_risk_soak.jsonl` unwritten since `2026-08-30T16:16Z` (946 B total) while `OI-20260831-PROP-RISK-GATE-ENFORCE-ARMED-BUT-HAS-NEVER-CAPPED` reads that same emptiness as *the gate has not tripped*. **Opposite facts.** 4 sibling soaks fresh within 2 days. ⚠️ Verdict is **COULD-NOT-PROBE**: read-only, cannot separate "no tickets emitted" from "writer not reached". | 946 B; 10 d | 1 |
| **F-24** `package-leg-coverage-frozen-22-days` | `package_leg_coverage_state.json` frozen at `2026-08-18T08:41Z`, served as current, **with no field on the payload saying when it was written**. Producer reachable at `main.py:1007`; registered as a `collapsed-state-guard` contract — which proves states are *branched on*, never that the producer still runs. | 22 d | 1 |
| **F-25** `eight-deploy-units-outside-the-diag-allowlist` | 8 of 47 `deploy/` units absent from `_CANONICAL_UNITS`, so unreadable on `/api/diag/services` and 400 on `/api/diag/journalctl`. **2 of 8 have recorded provenance** (`ict-heartbeat.*`, deliberately retired); **6 do not** — including `ict-ib-gateway-reset.*`, which `CLAUDE.md` calls *"the one deterministic restart the whole design relied on"*. Deliberate and accidental exclusions are indistinguishable. | 47 / 40 / 8 | 1 |
| **F-26** `four-registered-workflows-have-no-file-and-no-deletion-commit` | GitHub holds **144** active workflow registrations against **140** files on disk. The 4 orphans have **no deletion commit in a 4563-commit clone**; `ping-relay.yml` was registered from an unmerged branch on 2026-09-04 and left registered. | 144/140/4 | 1 |
| **F-27** `nothing-prunes-automation-branches-on-a-schedule` | Both pruners are demand-only (no `schedule:`); `prune-landed-branches.yml` has **240 runs, 10 of 10 most recent `skipped`** on unrelated issues. It runs constantly and prunes nothing — which is why F-18's 164 branches stand. ⚠️ The honest detector here is a **ceiling check, not a prune**: with F-18 unfixed, pruning would delete unlanded receipts. | 240 runs; 164 branches | 1 |

Plus two docs-tier: **`velotrade`** is described in 2 docs as a registered `EXCHANGE_MAP` entry and appears **0 times** in `src/`+`config/` (control: `breakout` in 56 src files); and **14 of 87** dispatchable `system-actions` options have no row in the doc's allowlist **table**, several of them journal-mutating (`rebuild-pnl-from-bybit`, `reset-daily-risk-state`).

### Two corrections the liveness agent made against itself, recorded rather than dropped

1. It first flagged `scope-overlap-audit.yml` as having **no trigger**; refuted — `:69 pull_request_target:`, which its regex missed. **LIVE.**
2. It first read the failure-alert watch list from a **truncated** `awk` window and concluded `probes`/`due-list`/`error-feed-digest`/`replay-pregate-nightly` were unwatched, contradicting `OI-20260902`. Re-read of the full block: **all four are watched** (`:192/:215/:219/:226`). The finding is the paging **volume** (F-21), not a coverage gap.

*Both are recorded because a probe that returns a clean negative for want of range is the exact failure this audit is about.*

---

## Phase 3.7 — MODULARITY. Can one change be half-applied?

**The axis's own result first, because it reframes every finding under it.** Change
amplification is **not** rising across the board — the *account* and *broker* axes are
genuinely modular and falling (broker: 28 → 13 → 12 files, bucket (ii) = **0**; account
adds carry bucket (ii) = 0 on both recent examples). **The defect is specific to the
strategy axis**, and it is entirely in bucket (ii): hand-maintained registries holding
facts `config/strategies.yaml` already contains.

| change-kind | commit | total | (i) truth | (ii) **underived registries** | (iii) tests+docs |
|---|---|---|---|---|---|
| strategy — new type | `8157d1ec` 2026-05-14 `ict_scalp_5m` | 13 | 6 | 4 | 3 |
| strategy — 3 variants | `e4a9ffd3` 2026-07-21 | 15 | 3 | 9 | 3 |
| strategy — 3 variants | `ad18c115` 2026-07-22 | **6** | 3 | 3 | 0 ← **half-applied, see F-31** |
| strategy — 1 variant | `cc4efb1c` 2026-07-28 `ict_scalp_mgc_15m` | **17** | 3 | **10** | 4 |
| account (existing broker) | `2b605a38` 2026-06-24 | 4 | 3 | **0** | 1 |
| broker | `505356a5` 2026-06-11 Alpaca | 12 | 7 | **0** | 5 |

**Bucket (i) is flat at 3 for a variant-add. The rise 13 → 17 is entirely bucket (ii), and
it happened while the work got strictly smaller** (a symbol re-target vs building a whole
strategy engine).

⚠️ **17 is a LOWER BOUND on today's cost, not an estimate of it.** No strategy has been
added since 2026-07-28 — 43 days — so the curve is *trailing*. Since then the repo has
gained further per-leg registers (`SUNSET-DISPOSITIONS.json`, `config/lever_reachability.json`,
`config/training_population.yaml`, the work store).

### F-28 · `AUD-20260909-rollback-builder-registry-is-39-legs-short` — 🔴 **money-at-risk**

**The single most consequential modularity finding: a documented rollback that is itself an outage.**

- **claim:** The sanctioned rollback `MULTI_STRATEGY_INTENT_LAYER=false` routes signal generation to a **second, parallel builder registry holding 16 of 55 strategies** — so exercising it would silently stop **35 of 45 `execution: live` legs (78%)**, logged as a `warning` and skipped. No test asserts the two registries agree.
- **evidence — re-verified by the audit lead from AST, not taken from the agent:**
  ```
  pipeline._STRATEGY_BUILDERS n = 16
  execution:live legs MISSING from the rollback registry: 35 of 45 (78%)
  sample: ada_pullback_2h, eth_pullback_2h, gdx_pullback_1d, gld_pullback_1d,
          gld_pullback_1h, iaum_pullback_1d, ict_scalp_avax_5m, ict_scalp_eth_15m
  ```
  `pipeline.py:506-509` — the behaviour on a leg the rollback registry lacks:
  ```python
  builder = _STRATEGY_BUILDERS.get(strategy_name)
  if builder is None:
      logger.warning("Multiplexer: unknown strategy '%s' — skipping", strategy_name)
      continue
  ```
  `pipeline.py:671-684` documents the path as the rollback: *"export `MULTI_STRATEGY_INTENT_LAYER=false` to fall back to it **without a code change**"*. `CLAUDE.md` lists this var as *"the core intent-aggregation switch, **default on**"*.
  `grep -rn "_STRATEGY_BUILDERS" tests/ | grep -i "parity\|== set"` → **no output. No parity assertion exists.**
- **expected vs actual:** **Expected** — a rollback documented as *"revert without a redeploy"* reverts **behaviour**, not **coverage**. **Actual** — the primary registry grew 16 → 55 across 2026-05 → 2026-07 and the rollback registry was never followed, so **the rollback is now a 78% capability outage that presents as ordinary log noise.** The failure mode is the worst available: it looks like it worked.
- **population:** 55 primary builders · 16 legacy · 39 gap · **35 of the gap `execution: live`**, out of 45 live legs.
- **detector:** a test asserting `set(_default_intent_builders()) == set(pipeline._STRATEGY_BUILDERS)`. **Better — and this is the modularity fix rather than the detector — delete `_STRATEGY_BUILDERS` and have `multiplexed_signal_builder` call `_resolve_builders()`, collapsing two registries into one.** Either way the parity test is what fails if it recurs.
- **tier:** 1 for the parity test; **the rollback path itself is Tier-2** · **disposition:** proposed

### F-29 · `AUD-20260909-unknown-strategy-priority-is-inverted-and-now-live-contended` — 🔴 money-at-risk, **Tier-3**

**F-32 from the 2026-08-20 audit is unremediated and has escalated from theoretical to live.**

- **claim:** `_UNKNOWN_STRATEGY_PRIORITY = 10` is still documented as *"deliberately below the in-scope strategies"* while **45 of 50 mapped legs sit below 10** — and five *declared, `execution: live`* legs are now **absent** from the map. Omission does not merely fail to be safe; **it wins the arbitration.**
- **evidence — re-verified by the audit lead by AST over `intents.py` + `strategies.yaml`:**
  ```
  DEFAULT_PRIORITIES n = 50 | _UNKNOWN_STRATEGY_PRIORITY = 10
  histogram: {0: 41, 1: 1, 2: 1, 3: 1, 5: 1, 10: 1, 20: 1, 30: 1, 40: 1, 50: 1}
  legs strictly BELOW the fallback 10:  45 of 50
  declared: 55 | execution:live: 45
  live legs ABSENT from DEFAULT_PRIORITIES -> gdx_pullback_1d, iaum_pullback_1d,
                       scha_trend_long_1d, slv_pullback_1d, splg_trend_long_1d
  ```
  And the contention is **live, not hypothetical**:
  ```
  alpaca_options_paper SLV: [('slv_trend_1h', 0), ('slv_pullback_1d', ABSENT->10)]
  alpaca_paper         SLV: [('slv_trend_1h', 0), ('slv_pullback_1d', ABSENT->10)]
  alpaca_portfolio     SLV: [('slv_trend_1h', 0), ('slv_pullback_1d', ABSENT->10)]
  ```
  `intents.py:648-652` — `effective_priority()` falls back to the constant; it is a real tiebreak term at `intents.py:1454`. `intent_multiplexer.py:587` passes `DEFAULT_PRIORITIES.get(name)` → `None` for an absent leg.
  The constant's own comment: *"Picked deliberately below the in-scope strategies so a misconfigured new strategy never silently overrides Turtle Soup / VWAP."* **Its actual effect is the inverse of its stated purpose.**
- **expected vs actual:** **Expected** — an unlisted strategy loses every contest. **Actual** — it beats 45 of 50 declared legs, and on 3 accounts it beats a specific named competitor on a shared symbol. *The contended accounts are paper-class, so no real money rides the specific instance — but the mechanism is account-agnostic.*
- **detector — two parts, and the second is the transferable one:**
  1. `check_strategy_coverage.py` gains a fourth invariant: every `execution: live` strategy has a `DEFAULT_PRIORITIES` entry or a dated `priority_exempt:`.
  2. **A distribution assertion on the constant itself** — fail if `_UNKNOWN_STRATEGY_PRIORITY` is not strictly below `min(DEFAULT_PRIORITIES.values())`. **This is the generic detector for "a default whose fail-safety depends on a distribution that has since moved"**, which is the class, not the instance.
- **tier:** **3** — changing a leg's arbitration priority is an order-routing change · **disposition:** proposed, **operator decides**

### F-30 · `AUD-20260909-daily-loss-pct-is-two-units-under-one-name` — 🔴 money-at-risk

**The `risk_pct` twin, left unfixed when `risk_pct` was single-homed on 2026-08-20.**

- **claim:** `daily_loss_pct` carries a **FRACTION** in production and a **PERCENT** in nine harness files, under the identical name and identical CLI flag — so passing the live value `--daily-loss-pct 0.05` to a backtest simulates a daily-loss halt **100× tighter** than live.
- **evidence:**
  ```
  src/units/accounts/risk.py:788      return self.daily_loss_pct * float(eq)        # FRACTION
  scripts/backtest_system.py:1357     <= -abs(daily_loss_pct) / 100.0 * day_start   # PERCENT
  scripts/backtest_system.py:1850     p.add_argument("--daily-loss-pct", default=3.0)

  live daily_loss_pct by account: bybit_1/2/portfolio 0.05, ib_paper 0.05, ib_live 0.05,
    alpaca_* 0.05, alpaca_live 0.1, breakout_1 0.03      <- all FRACTIONS
  config/prop_rulesets/breakout.yaml:26  daily_loss_pct: 0.03   # [CONFIRMED — FAQ]

  scripts/ci/check_risk_basis_agreement.py:70
      _RISK_FLAGS = ("--risk-pct", "--base-risk-pct")     <- this flag is not in it
  ```
- **population:** **9 of 9** harness files declaring `--daily-loss-pct` default to `3.0`; **11 of 11** accounts declare it as a fraction. Denominator: **78** parameters appearing in both a config file and a harness CLI (measured over 498 harness flags × 468 config keys).
- **blast_radius:** money-at-risk — research authorises Tier-3 promotions, and a prop-ruleset evaluation is the gate on **real-money prop routing**.
- **detector:** extend `_RISK_FLAGS` to a `FLAG_UNITS` table covering every flag whose name matches a `*_pct` key in `config/**.yaml`, each declaring `UNIT_FRACTION`/`UNIT_PERCENT`. **The guard's existing `FILE_UNITS` + `KNOWN_DIVERGENCES` machinery already does the grading — only its flag list is narrow.**
- **tier:** 1 · **disposition:** proposed

### F-31 · `AUD-20260909-no-harness-models-quantization-or-refusal` — 🔴 money-at-risk

**This is the assumption the skill warns "hides longest", and it is confirmed present.**

- **claim:** The R-normalization premise that makes a risk default *"not matter"* is **structurally untestable** here. `scripts/backtest_system.py` — 2068 lines, the fleet's core harness every walkforward and panel routes through — computes a raw float qty with **no whole-contract floor, no `min_qty`, no whole-share floor and no sub-1 refusal**, while **24 of 52 routed legs (46.2%)** run on a venue that applies all of them.
- **evidence:**
  ```
  scripts/backtest_system.py:919-924
      def _risk_qty(bal, rpct, entry_px, sl_px) -> float:
          stop_dist = abs(entry_px - sl_px)
          if stop_dist <= 0 or bal <= 0 or rpct <= 0: return 0.0
          return (bal * (rpct / 100.0)) / stop_dist        # <- no floor, no refusal

  $ grep -n "floor\|min_qty\|contracts\|whole" scripts/backtest_system.py | grep -v round
  (no qty-quantization site; every hit is round() on a reporting field)

  $ grep -rln "qty_legalize\|whole_unit\|WHOLE_UNIT_QTY" scripts/ src/backtest/ ml/
  scripts/check_qty_legalization_guard.py        <- a guard
  scripts/ci/check_collapsed_states.py           <- a guard
  scripts/research/pairs_dollar_lots.py          <- not a backtest harness

  src/units/accounts/risk.py:986-988
      force_whole = is_futures or bool(whole_units)
      eff_min_qty = 1.0 if force_whole else self.min_qty
  ```
- **expected vs actual:** **Expected** — a harness whose defence of its risk default is *"it's normalized"* must model the paths where the **trade set** is a function of the risk level. **Actual** — of 150 files in the guard's declared harness fleet, **1** imports the legalizer, and it is not a backtest harness. Below a threshold the trade does not shrink, **it does not happen** — and the refused trades are absent from a population the harness never claimed to enumerate. **It fails in the flattering direction: small risk reads as safe when it means "this leg does not trade".**
- **population:** 150 files in `SCAN_GLOBS`, 1 models quantization. **24 of 52 routed legs** on `alpaca` (whole shares) or `interactive_brokers` (whole contracts, strict sub-1 refusal).
- **detector:** **no full detector is possible from CI** — *"does the harness model refusal"* is a semantic property. **The tractable half is detectable:** fail any file in `SCAN_GLOBS` that computes a qty from a risk basis and does not import `src.units.accounts.qty_legalize` — the single-owner shape `check_qty_legalization_guard.py` already enforces elsewhere. And state the residual in the harness's own output as a `quantization_modelled: false` field, so a reader cannot mistake a clean backtest for one that priced the floor.
- **tier:** 1 · **disposition:** proposed

### F-32 · `AUD-20260909-guard-scope-narrower-than-concept-three-cases-PROVEN-BY-PERTURBATION`

**Not inferred from source — the identical line was added to different paths and the verdicts differ.**

```
$ python3 scripts/check_strategy_risk_field_in_diff.py probe.diff   # same line, 3 paths
  - src/runtime/foo.py:1 — strategy_risk_pct reference re-introduced
  EXIT=1        # scripts/ and ml/ NOT flagged

$ python3 scripts/check_env_gate_in_diff.py env.diff                # same gate, 4 paths
  - src/runtime/foo.py:1 — env-gate 'NEW_FEATURE_ENABLED'
  EXIT=1        # src/core/, src/news/, src/prop/ NOT flagged
```

| guard | concept it protects | paths scanned | the gap that matters |
|---|---|---|---|
| `check_strategy_risk_field_in_diff` | *"risk lives at the account level and **nowhere else**"* | `config/strategies.yaml`, `src/` | `scripts/` (150 harness files) + `ml/` exempt. **Research authorises Tier-3 changes to production, so a rule that binds production and exempts research does not bind the decision.** Unchanged since the 2026-08-20 audit named it. |
| `check_env_gate_in_diff` | the Prime Directive — no default-off flag before a required capability | `src/{runtime,units,web}` | **7 of 15 gate-bearing `src/` files uncovered (46.7%)** — including `src/news/news_score.py`, which holds `NEWS_VETO_ENABLED`, a var `CLAUDE.md` itself calls *"a LIVE trade-blocking gate … for every account incl. real money"*. `src/core/coordinator.py` — the order path — is outside it too. |
| `check_diagnostic_provenance` | *"a diagnostic must carry the provenance of what it printed"* | `scripts/{ml,research,ops,…}/`, `ml/…` | **all of `src/` excluded by an explicit comment** whose stated reason (*"computes a value for another machine"*) is **false for `order_monitor.py`**, whose over-cover page is a Telegram CRITICAL a human acts on — and which `CLAUDE.md` records as having *"named a cause no code path tested"* until 2026-09-02. **54 `src/` modules emit operator-facing text; 0 are in scope.** |

- **detector:** a **concept-scope** guard, distinct from `check_guard_glob_coverage.py` — which asks only whether a guard is *triggered by files it reads*, so a guard reading nothing outside its own scope passes it while remaining narrower than its concept. Each guard declares `CONCEPT_DOMAIN` beside its scan scope; the meta-guard fails when `CONCEPT_DOMAIN − scan_scope` is non-empty without a dated, reasoned exemption. **`check_api_tier_policy.py` already models the answer**: its one out-of-scope route is enumerated in `docs/api-tier-policy.md:97` with the reason attached.
- **tier:** 1 · **disposition:** proposed

### F-33..F-35 — the rest, in brief

| id | claim | population |
|---|---|---|
| **F-33** `harness-atr-stop-mult-default-matches-47pct-of-live` | 9 harness files hardcode `--atr-stop-mult 2.5`; only **21 of 44** legs declaring the key run at 2.5 live — a default inherited from the pre-e35 era simulating a stop 25% wider on 14 legs and 67% wider on 6 more. **Three divergent legs route to `bybit_2` = real money.** `check_harness_lever_coupling.py` verifies the key is *classified*, never that the *value* agrees. |
| **F-34** `strategy-add-touches-ten-underived-registries` | 10 of the 17 files are hand-maintained registries holding facts `strategies.yaml` already contains, and the guard covers **3 of them**. **The natural experiment is visible in the outcome:** `strategy_descriptions.json` (guarded) is **55/55 complete**; `strategy_changelog.json` (unguarded, same shape) is **28/55**. The registries are not *derived*, they are *remembered* — and memory's measured failure rate here is **49%**. |
| **F-35** `roster-pinned-tests-broke-main-and-the-pattern-persists` | 3 tests hold a hand-copied full roster. A strategy add that omitted them **broke `main` for every open PR on 2026-07-22**; the repair commit `3bb6a235` says so in its own message. `tests/test_strategy_registry.py:238` still reads `assert len(strategies) == 55` beneath a 24-line hand-maintained changelog comment (bumped 45→48→51→54→55). ⚠️ **The proposed fix is a detector REMOVAL, deliberately:** the census detects nothing a diff of `strategies.yaml` would not show, and its only measured effect was an outage. |

### Verified NON-issues on this axis — each with the probe that could have returned a positive

The **account** axis (bucket ii = 0 on both recent adds; **8 of 401** `src/` files carry an account literal in executable code) · the **broker** axis (amplification *falling* 28 → 13 → 12, bucket ii = 0) · `src/core/instrument_class.py` (derived from `instruments.yaml` — the correct pattern, and it *replaced* a drifted parallel map) · `strategy_descriptions.json` **55/55** · `_LOG_FILES` (18/18 soaks present, and a miss returns HTTP 400 **naming the allowed set** — loud and safe) · `check_api_tier_policy.py`'s one out-of-scope route is explicitly enumerated in the policy doc.

⚠️ **Stated method limit on the hardcoded-enumeration probe.** It counts string *constants* in executable code via AST, excluding docstrings and (invisible to AST) comments. Raw vs stripped over 401 `src/` files: **accounts 85 → 8**, strategy names 77 → 18, symbols 123 → 31. **The 10× gap on accounts is the measure of how much an unstripped grep is prose** — the previous audit's "12+ files with scattered account rosters" was measuring text. It does not see a name built at runtime or read from config, so a clean file here is *"no literal found"*, **not** *"not coupled"*.

---

## Phase 3.1 / 3.2 — BEHAVIOR. Invariants over live data. **The core pass.**

> **Instruments proven BEFORE use, per Phase 0c:** `scripts/ops/system_invariants.py --self-test`
> → **28/28** with the real `src.units.accounts.ib_client` classifier imported (not the fallback);
> `scripts/ops/exit_path_coverage.py --self-test` → **19/19**. Both exist and both are sound.
> **Neither has a scheduled caller.**

### The headline result, stated first because it is the one that matters most

**NO LIVE POSITION IS NAKED.** Every one of the **24 (account, symbol) groups** across the
25 open journal rows — IB + Bybit + Alpaca, read 2026-09-09T16:47Z — carries a resting stop
**≥** its position size.

*What would have counted as a violation:* any group with `stop_qty < position_qty`, or a
`could_not_look` read folded into "holds". **Neither occurred.** The one blind account
(`ib_live`, `mode: dry_run`, no positions) is reported **UNGRADEABLE, not passed**.

`system_invariants.py` on the **full** book: **0 FAIL · 6 pass · 1 NOT-MEASURED**
(`INV-EXIT-INTERVAL`, correctly refusing to pass on n=3 after a 16:42:27Z process restart).

### INVARIANT TABLE

| # | invariant | verdict | population | if UNGRADEABLE — what blocked it |
|---|---|---|---|---|
| **1a** | stop ≥ position (nothing naked) | ✅ **HOLDS** | 24 groups / 25 open rows, 3 venues | — (`ib_live` excluded as blind and *named*) |
| **1b** | stop ≤ position (exactly sized) | 🔴 **VIOLATED** | same 24; **23 at exactly 1.000×**, `bybit_1`/ADAUSDT at **1.7929×** | — |
| **1c** | declared `take_profit_1` has a resting target | ✅ HOLDS | all 24 groups; `INV-PROTECT-TARGET` pass n=3 on IB | — |
| **1d** | protection graded **per BOOK** on Bybit | ⚠️ **UNGRADEABLE** | 12 resting legs / 5 positions, 3 Bybit accounts | `/api/diag/bybit_open_orders` **omits `position_idx` on every order** while emitting it on every position (F-43). Hedge mode is armed, so a per-symbol grade is a side-blind sum — **it is not reported as per-book coverage** |
| **2** | journal ⇄ exchange qty reconcile, netting-aware | ✅ HOLDS | `INV-JOURNAL-EXCHANGE` pass n=24. ADAUSDT reconciles **exactly**: 79227.0 + 628.0 = 79855.0 | 3 could-not-read + 2 exchange-only accounts excluded **and named**, not counted as passing |
| **3** | order-package leg coherence | 🔴 **VIOLATED** | 3 live packages / **4 open legs stranded, one on real-money `bybit_2`** (F-39); the detector's own latch is 85% stale (F-38) | trade `4195` could not be joined to a package by either source — **excluded, not assumed clean** |
| **4** | signal accounting closes | ✅ HOLDS | 24 h window, **44,434 audit rows paged in full** (not the 1000-row cap). 16,677 `pipeline_result`; **21 actionable, all dispatched; 0 of 21** lacked an order package within 120 s | — |
| **5** | every declared-live account placed or refused | ✅ HOLDS (27 d) | 9 declared-live accounts; 7 accounted in 24 h. `alpaca_live` refuses **40/40** over 27 d with causes (F-45) | `breakout_1` **UNGRADEABLE on this surface** — a prop account executes via `prop_tickets`, not `trades`, so 0 rows is *expected*, not silence. The prop tables were not read, so it is graded **neither way** |
| **6** | PnL provenance | ⚠️ **HOLDS on disclosure, VIOLATED on labelling** | n=558 closed non-backtest `pnl NOT NULL`, classified by the canonical `provenance.classify_pnl`. **0 fabricated rows; 0 fabricated rows lacking an anchor.** But see F-40 | — |
| **7** | every three-state field can say *"we did not look"* | 🔴 **VIOLATED (1 new)** | `check_collapsed_states.py` clean on 34 contracts (1 grandfathered). Live payload audit found `exchange_positions` **unregistered and collapsing 3 causes into one null** (F-44) | — |

### F-36 · `AUD-20260909-intent-reduce-orphans-an-oversized-protective-leg` — 🔴 **money-at-risk, Tier-2**

**And it is the root cause of 64 of the 112 rows in the operator ERROR feed.**

- **claim:** `apply_intent_reduce_partial_close` decrements `trades.position_size` on the parent row and **never resizes that row's own tracked venue protective legs**, leaving a reduce-only stop sized up to **101.8×** the row it protects and the symbol **179.3%** over-covered.
- **evidence:**
  ```
  bybit_1 / ADAUSDT, live at 2026-09-09T16:47Z
    trade 5479  position_size 79227.0   sl_order_id be88c2fd-…e504f1  -> venue qty 79227.0  ✓
    trade 5417  position_size    628.0   sl_order_id 33c587c3-…b890   -> venue qty 63943.0  ✗ 101.82×
    trade 5464  CLOSED 63315.0  intent_reduce_executed  2026-09-04T12:27:53Z
    journal 79227.0 + 628.0 = 79855.0 == exchange position   (reconciles EXACTLY)
    resting stops 79227.0 + 63943.0 = 143170.0 / 79855.0 = 1.7929
    63943 − 63315 = 628  ==  5417's CURRENT position_size
    first over-cover page 2026-09-04T12:28:34Z  =  the reduce + 41 seconds
  grep over apply_intent_reduce_partial_close (133 lines):
    sl_order_id 0 | tp_order_id 0 | modify_open_order 0 | set_trading_stop 0 | amend 0
  ```
  The leg was still 63,943.0 **after a trailing amend on 2026-09-08** — the amend followed the tracked id and preserved the wrong size.
- **expected vs actual:** **Expected** 5417's tracked stop to rest at 628.0, its own size. **Actual** 63,943.0.
- **population:** all 24 (account, symbol) groups in the live open book — **23 exactly-sized, 1 broken**. Class incidence: `exit_reason='intent_reduce_executed'` **n=21 of 1000** journal rows (2026-08-13 → 09-09) across **bybit_1 (15), bybit_portfolio (3), `bybit_2` (3 — REAL MONEY)**. **7 of the 8** distinct (account, symbol) pairs that had an intent-reduce later produced an over-cover page; **`bybit_1`/BNBUSDT had no reduce and is exactly sized — the negative control.**
- ⚠️ *Today's standing instance is on a paper-class account. The mechanism is account-agnostic and has fired 3× on real-money `bybit_2`; those rows have since closed, so there is no standing real-money instance right now.*
- **detector:** extend `system_invariants.py` with **`INV-PROTECT-LEG-MATCHES-ROW`** — for every open trade carrying a non-null `sl_order_id`/`tp_order_id`, assert the venue leg's qty equals **that row's** `position_size`. **Strictly stronger than the existing symbol-level `INV-PROTECT-OVERCOVER`**, which passes whenever siblings happen to sum correctly, and stronger than `exit_path_coverage.py`, which graded 5417 `bStop LIVE` **on presence alone**.
- **tier:** 2 · **disposition:** proposed

### F-37 · `AUD-20260909-system-invariants-default-population-truncation`

**The audit suite's own instrument, reporting a truncated query as *"we could not look"*.**

- **claim:** Run against the source its own docstring names, `system_invariants.py` sees **1 of 25** open positions and reports the three protection invariants `not_measured n=0` — indistinguishable from a blind read — because `/api/bot/positions` defaults `include_paper=False`.
- **evidence:** same instant, one query parameter apart:
  ```
  # /api/bot/positions                      -> n=1
  [NOT-MEASURED] INV-PROTECT-STOP       n=0
  [NOT-MEASURED] INV-PROTECT-OVERCOVER  n=0
  [NOT-MEASURED] INV-PROTECT-TARGET     n=0
  7 invariants: 0 FAIL, 5 NOT-MEASURED, 2 pass

  # /api/bot/positions?include_paper=true   -> n=25
  [pass] INV-PROTECT-STOP n=3  [pass] INV-PROTECT-OVERCOVER n=3  [pass] INV-PROTECT-TARGET n=3
  7 invariants: 0 FAIL, 1 NOT-MEASURED, 6 pass
  ```
  `dashboard.py:834` — `include_paper: bool = Query(False)`. `system_invariants.py:193` docstring names the route **without the parameter**. **No fetch helper is committed.**
- **expected vs actual:** **Expected** the suite's population to be the open book (25 rows / 24 groups). **Actual** 1 row / 1 group, **with the loss reported as `not_measured` rather than as a truncated query**. 4 of 7 invariants flip verdict on the parameter.
- ⚠️ **This is a RECURRENCE and the two causes are indistinguishable in the output.** The 2026-08-23 audit recorded the *same* 4-of-7 `not_measured` symptom (`docs/audits/full-system-audit-2026-08-23.md:240`) and attributed it to an `ib_paper` `could_not_look`. **Today `ib_paper` read cleanly and the symptom reproduced from a different cause.**
- **detector:** a **fourth state — `population_truncated`** — refusing rather than reporting `not_measured` when the positions payload contains zero paper-class rows while `exchange_positions` shows paper positions; plus a committed `fetch_payloads.sh` pinning `include_paper=true`. A self-test fixture must produce that state, not `not_measured`.
- **tier:** 1 · **disposition:** proposed

### F-38 · `AUD-20260909-package-leg-latch-cannot-clear-once-legs-close`

- **claim:** `package_leg_coverage`'s clear path is **unreachable** for any package whose legs have all closed, so **17 of 20 latched entries (85%)** describe conditions that no longer exist and can never be removed.
- **evidence:** `package_leg_coverage.py:346` clears via `state.pop(pkg_id, None)` **inside a loop over `verdicts`**, and `_read_journal` (`:135`) builds `verdicts` from `WHERE status='open'` — so a package with no open leg is **absent from `verdicts`, and `state.pop` never runs on it**.
  ```
  flagged packages n=20  Counter({'stranded': 19, 'divergent': 1})
  LIVE (still have an open leg)                    n=3
  UNCLEARABLE (no open leg -> latch immortal)      n=17     oldest since 2026-08-18 (22 days)
  signal-to-noise 3/20
  ```
- **detector:** a self-test that latches a package, closes its last leg, re-runs `run_package_leg_check`, and asserts the key is gone. **The fix is to prune keys absent from `verdicts`; the test is what makes it permanent.**
- **tier:** 1 · **disposition:** proposed

### F-39 · `AUD-20260909-live-stranded-package-legs-including-real-money` — 🔴 money-at-risk

- **claim:** Three order packages are **closed** while still holding **four open legs** the monitor cannot manage, one of them on real-money `bybit_2`.
- **evidence:**
  ```
  pkg-32a5162bbbdd4ef6 stranded SPY     "package closed (exchange_flat_reconciled) with 1 open leg" -> 4347 alpaca_paper
  pkg-785d9a600c894c81 stranded TLT     "package closed (exchange_flat_reconciled) with 1 open leg" -> 5265 alpaca_paper
  pkg-293021e2e84a48db stranded XRPUSDT "package closed (reconciler_filled) with 2 open legs"
        -> 5474 bybit_2 (REAL MONEY, open since 2026-09-08), 5475 bybit_portfolio
  ```
  All four confirmed still open in `/api/bot/positions?include_paper=true` at 16:47Z. **5474 is the one real-money position in the whole book.**
- **detector:** promote `package_leg_coverage.verdict ∈ {stranded, divergent}` **restricted to live legs** into `system_invariants.py` as a FAIL, so it lands in the suite's exit code rather than only in a latch file whose live rows are **15%** of its contents.
- **tier:** 2 · **disposition:** proposed

### F-40 · `AUD-20260909-totalpnlmeasured-sums-estimated-rows-too` — accounting

**A field whose name asserts the exact thing `CLAUDE.md`'s provenance section exists to prevent.**

- **claim:** `/api/bot/performance` publishes `totalPnlMeasured` beside `pnlMeasuredCount`, and the field sums MEASURED **and** ESTIMATED rows — a quantity over **444** rows presented under a label naming the **77**.
- **evidence:**
  ```
  demo.totalTrades 558 | pnlMeasuredCount 77 | pnlEstimatedCount 367 | pnlUnverifiedCount 114
  demo.totalPnl         = 216367.1372
  demo.totalPnlMeasured = 217827.5617
  performance.py:490   total_pnl_measured = 0.0   # sum of pnl over MEASURED+ESTIMATED rows only
  performance.py:816   # `totalPnlMeasured` above sums MEASURED **and** ESTIMATED.
  ```
  Independent classification with the repo's own `provenance.classify_pnl`, population *closed, non-backtest, `pnl NOT NULL`*, **n=558**:

  | bucket | n | sum pnl |
  |---|---|---|
  | **measured** | 104 | **−24,255.32** |
  | estimated | 352 | +239,771.26 |
  | unverified | 102 | −1,109.40 |
  | **mixed total** | 558 | **+214,406.55** |

- **expected vs actual:** **Expected** `totalPnlMeasured` to sum the 77 rows its sibling counts. **Actual** it sums 444. **The sign of the book flips on the filter — broker-truth PnL is NEGATIVE, the headline is POSITIVE** — which is precisely the population hazard `CLAUDE.md` documents, reproduced *inside a field whose name asserts the opposite*. The only disclosure lives in a **source comment no API consumer can read**.
- ⚠️ **Coverage here is 104/558 = 18.6% on a 30-day window; it is NOT comparable to `CLAUDE.md`'s 42.9%, which is lifetime at n=1151. Different populations.**
- **detector:** rename the wire field to `totalPnlMeasuredAndEstimated` (or emit both), and add a `provenance-consumer-guard` case asserting **any key matching `*Measured*` sums exactly the population its sibling `*MeasuredCount` counts**.
- **tier:** 1 · **disposition:** proposed

### F-41 · `AUD-20260909-cash-settlement-soak-has-zero-rows-on-its-only-armed-account` — 🔴 money-at-risk

- **claim:** The T+1 cash-settlement gate is armed and **binding on `alpaca_live` alone**, and its soak contains **zero rows for that account** while writing rows for the three accounts the allowlist **excludes**.
- **evidence:**
  ```
  soak rows n=31   window 2026-08-31T13:30Z -> 2026-09-08T13:36Z
  Counter({('alpaca_paper','not_allowlisted'):13, ('alpaca_portfolio','not_allowlisted'):12,
           ('alpaca_paper','not_apply'):2, ('alpaca_portfolio','not_apply'):2,
           ('alpaca_options_paper','not_apply'):1, ('alpaca_options_paper','not_allowlisted'):1})
  alpaca_live rows n=0        allowlisted rows anywhere n=0
  ```
  Against `CLAUDE.md`'s own contract for this knob: *"THE ALLOWLIST SCOPES THE BINDING, NEVER THE MEASUREMENT — every alpaca account is evaluated and annotated, so the rows a reviewer needs before widening actually exist."*
- **population:** all 31 rows. ⚠️ **Positive control present and it passes** — the writer is demonstrably working (31 rows, 3 accounts, most recent 2026-09-08), so this is an **account-specific absence, not a dead writer**. What could **not** be determined from this surface is whether the annotate path never reaches `alpaca_live` or the write is suppressed. **Both contradict the documented contract; neither is folded into the other.**
- **detector:** an invariant asserting **every account named in a `*_ACCOUNTS` allowlist appears in that knob's soak within one cadence window** — the general form of *"the allowlist scopes the binding, never the measurement"*, which is currently prose in three separate `CLAUDE.md` rows and **checked nowhere**.
- **tier:** 1 to investigate (any change to the gate is Tier-2) · **disposition:** proposed

### F-42 · `AUD-20260909-operator-error-feed-is-91-percent-standing-repeats`

- **claim:** **102 of 112** rows in the operator CRITICAL/ERROR feed are protection-condition **re-pages on 26 standing conditions**, one of which has re-paged **22 times over 5 days** without changing.
- **evidence:**
  ```
  feed n=112  oldest 2026-08-26T00:33Z  newest 2026-09-09T16:25Z   (NOT truncated: limit 1000)
     64 bybit_over_cover      20 alpaca_target_naked      9 ib_stop_over_cover
      5 alpaca_partial_stop_coverage   4 ib_target_naked   7 api_call bybit_place_order_failed
  distinct conditions: 26 ; top = ('bybit_over_cover','bybit_1','ADAUSDT') ×22
  standing 5 days 3:57:03
  ```
- **expected vs actual:** **Expected** the reserved operator channel to carry news. **Actual** 91.1% is the same unresolved detect-only conditions on a 6 h cooldown, **with no escalation and no disposition path**. ⚠️ **The durable latch that fixed the previous instance (202 of 376) is working correctly** — these repeats come from conditions *standing for days*, which is a different defect. **And the top condition's cause is F-36.**
- **detector:** an **ageing/escalation rule keyed on standing duration** rather than a fixed cooldown — a condition unchanged past N cooldowns stops re-paging and is instead required to carry a disposition. **`close_wedge_standing.is_unclearable` already uses exactly this shape on the close path.**
- **tier:** 2 · **disposition:** proposed

### F-43..F-46 — the rest

| id | claim | population | tier |
|---|---|---|---|
| **F-43** `bybit-diag-orders-drop-positionidx` | `/api/diag/bybit_open_orders` emits `position_idx` on the **positions** half (`clients.py:1828`) and drops it on the **orders** half (`_shape_order`, `:1929` — 15 keys, no `positionIdx`). With hedge mode armed, **the surface built to contradict the side-blind coverage verdict cannot itself attribute a leg to a book.** This is why invariant 1d is UNGRADEABLE rather than passed. ⚠️ **Routes to MI-221/MI-222 — not edited here.** | all 12 resting legs / 5 positions, 3 Bybit accounts, all `read_state: orders_read`; 3 of 5 positions carry idx 1 or 2 | 1 |
| **F-44** `exchange-positions-null-cannot-say-why` | `/api/diag/exchange_positions` carries **no `read_state`** and never populates `error`, so *"not a position venue"*, *"gated because `mode: dry_run`"* and *"credential/API failure"* are **one indistinguishable value**. Sibling routes `/api/diag/{ib,bybit,alpaca}_open_orders` all carry an explicit `read_state`. ⚠️ `count: null` (never `0`) on a blind read **is correct** — the collapse is in the *reason*, not the count. | all 11 accounts; **3 read `null` for 3 different reasons** | 1 |
| **F-45** `alpaca-live-cannot-size-a-single-share` 🔴 | `alpaca_live`'s only routed real-money leg is refused at sizing on **every** signal — `sized_qty=0 with balance=200.10` under whole-share rounding, TLT near $82. **`OI-20260831-ALPACA-LIVE-…-HAS-NEVER-TRADED` is therefore structurally unreachable, not pending.** Both declared execution gates read permissive; **the refusal is arithmetic.** `silent_refusal_alert` buckets `dry_run_sizing_skip` as `policy_skipped` and **cannot** raise this. | **40 of 40 rejected** over 27 days (n=40 of a 1000-row window); 25 the same deterministic cause | **3** |
| **F-46** `eleven-of-twentyone-open-trades-are-price-only` | **11 of 21** gradeable open trades can be closed only by price reaching a level fixed at entry; **exactly one** has a live decision-driven exit. `no_exit_path=0` (nothing is wholly unclosable) and 9 `unknown` are reported as unknown, **not as covered**. ⚠️ **This is 21 of the true 25** — trades 4350/4347/4195/4194 fall outside the 1000-row journal cap and were **not graded**; stated as a coverage gap, not folded into the clean count. **The detector already exists and is correct (`exit_path_coverage.py`, 19/19); what is missing is that nothing runs it.** | 21 open non-backtest trades | 3 |

---

## Coverage contract (Phase 1) — updated as the program runs

**Behavioral coverage (primary):** _in progress — see the BEHAVIOR axis._
**Reading coverage (secondary, honest):** _in progress; what was NOT reached will be stated explicitly._

## Phase 3.10 — system-review coverage ledger

| item | CITED / RE-ESTABLISHED | where |
|---|---|---|
| strategy promotion/demotion readiness | _pending_ | |
| ML training-cycle + soak health | _pending_ | |
| soak status | _pending_ | |
| flags raised | _pending_ | |
| backlog drive (burn-down + population) | _pending_ | |
