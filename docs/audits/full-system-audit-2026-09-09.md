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
