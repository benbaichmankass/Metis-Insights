# CA-B07 — code audit Wave B: tests vs money-path src

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> Lane CA-B07 of the 2026-09-27 code audit · session `session_0176ddjoUxwveWpnJT7Jw9cr` · audited `origin/main` at `cf51e0b` (2026-09-27) · read-only analysis. Nothing in code, config or live state was changed.

**The question:** where does the test suite (`tests/**`, 1,226 files) fail to protect the money path (`src/runtime/order_monitor.py`, `pipeline.py`, `src/core/coordinator.py`, `src/units/accounts/**`, exit/protection modules)?

**The honest headline: line coverage is NOT the gap.** Every money-path module measured this session sits at 71–100% line coverage under its own precise test population (methodology note below). The real gap is qualitative: specific untested branches that already carry MEASURED, filed defects, and — worse — tests that assert the *wrong* oracle, pinning a known bug as correct output. 6 findings below (4 high, 1 medium, 1 cross-reference); the 4 highs are filed to the pipeline as `PI-20260927-JT7JW9CR-0001..0004`.

**Update mid-session:** CA-A02 (pipeline/intents/sizing/costs/gates) and CA-A04 (`src/units/accounts/**`) landed on `main` while this lane was running (commit `6035989`). This report was updated to cross-reference them — see finding 4 below and the revised "what I read" section.

## Methodology note (read before trusting any number below)

A first pass used `pytest -k "order_monitor or coordinator_flow or ..."` (substring match on test *names*) and got badly wrong numbers — e.g. `pipeline.py` read **15%**, `exit_head_shadow.py` **14%**, `alpaca_client.py` **18%**. Re-running against the *exact* file list of tests that `import` each module (`grep -rl "accounts import execute" tests/`, etc.) gave the true numbers: `pipeline.py` **80%**, `exit_head_shadow.py` **55%**, `alpaca_client.py` **81%**. The `-k` substring approach silently excludes any test file whose *name* doesn't happen to contain the module name even though its *body* imports and exercises it — every number in the table below uses the corrected, import-list methodology, and each row states its own test population.

Also corrected in-flight: `--cov=src/runtime/order_monitor` (path syntax) silently collected **zero** coverage against files this repo imports as `src.runtime.order_monitor` (dotted); `--cov=src.runtime.order_monitor` (dotted) is required. A first run reported "module never imported" for every target with the path syntax, which would have been reported as "0% covered, no test" — an artifact, not a finding — had it not been re-checked against a single known-covered test file first.

## Coverage table (money-path modules)

Each row's test population is the exact set of files that `import` the module (via grep on `accounts import X` / `X.py`'s own dotted path), not `-k` substring matching. Commands and full missing-line lists are in the findings' `evidence` fields and the raw pytest-cov output (not committed — reproducible via the commands below).

| module | stmts | covered | population (test files, n) | tests run |
|---|--:|--:|--:|--:|
| `src/runtime/order_monitor.py` | 3,622 | **80%** | 102 files (CA-A01's own count) | 3,673 passed |
| `src/core/coordinator.py` | 979 | **85%** | 67 files | (same run) |
| `src/runtime/pipeline.py` | 362 | **80%** | 27 files | 725 passed |
| `src/runtime/exit_head_shadow.py` | 247 | **55%** | 4 files | (same run) |
| `src/runtime/exit_plan.py` | 157 | 83% | — (subset run, see caveat below) | 799 passed |
| `src/runtime/exit_plan_materializer.py` | 86 | 91% | — | " |
| `src/runtime/exit_plan_realism.py` | 79 | 87% | — | " |
| `src/runtime/exit_anchor.py` | 101 | 88% | — | " |
| `src/runtime/exit_head_apply.py` | 55 | 96% | — | " |
| `src/runtime/exit_levers.py` | 116 | 71% | — | " |
| `src/runtime/protection_price.py` | 57 | **100%** | — | " |
| `src/runtime/protection_reassert.py` | 61 | **100%** | — | " |
| `src/units/accounts/clients.py` | 1,002 | 83% | 69 files | 2,105 passed |
| `src/units/accounts/execute.py` | 911 | 78% | 52 files | " |
| `src/units/accounts/ib_client.py` | 1,622 | 81% | 20 files | " |
| `src/units/accounts/alpaca_client.py` | 765 | 81% | 12 files | " |
| `src/units/accounts/options_lifecycle.py` | 68 | 96% | 2 files | 52 passed |
| `src/units/accounts/options_overlay.py` | 130 | 89% | 1 file | " |
| `src/units/accounts/prop_state_io.py` | 58 | 90% | 1 file | " |
| `src/units/accounts/dup_key_check.py` | 37 | 84% | 1 file | " |
| `src/units/accounts/alpaca_options_data.py` | 75 | 59% | 1 file | " |
| `src/units/accounts/oanda_client.py` | 139 | *not re-measured* | 2 files | — |

**Caveat on the exit_plan/exit_levers/protection_* row group:** these still use the `-k`-filtered 799-test run (kept because the module-name substrings happened to match well and re-running with precise file lists was not worth the added cost given every number in this row group is already ≥71%). Treat these six as a **lower bound** — true coverage is at least this high, likely a few points higher, consistent with the pipeline.py/exit_head_shadow.py correction pattern above.

**What this table does NOT show:** function-level or branch-level coverage. A module at 80% line coverage can — and, per the findings below, does — leave a specific, money-relevant branch completely dark while everything around it is green.

## Findings

Full records: [`CA-B07.findings.jsonl`](CA-B07.findings.jsonl). 4 high, 1 medium, 1 cross-reference. All 4 highs filed to the pipeline (`docs/claude/work/pipeline/`) as `PI-20260927-JT7JW9CR-0001..0004`.

| id | severity | claim (short) | pipeline |
|---|---|---|---|
| `watchdog-flatclose-test-blind-to-cancel` | **high** | The one test that finalizes `_watchdog_stuck_strategies`'s flat-close branch asserts DB-row state only, never that resting broker protection was cancelled — exactly the blind spot behind the already-live critical bug CA-A01-001. | `PI-20260927-JT7JW9CR-0001` |
| `orphan-readfail-untested` | **high** | `_recover_orphan_attribution`'s read-FAILURE branch (CA-A01-019: an exception is treated as "no candidate" and a live orphan gets flattened) has 0 tests; the 2 nearest tests both use a DB whose read succeeds. | `PI-20260927-JT7JW9CR-0002` |
| `protection-price-test-pins-bug` | **high** | `tests/test_protection_price.py` asserts the CA-A01-056 defect (garbage stopLoss string graded fully-covered) as correct output, with a comment acknowledging it. The test will *fail* once the bug is fixed unless someone remembers to flip it. | `PI-20260927-JT7JW9CR-0003` |
| `account-mode-gate-test-never-asserts-mode` | **high** | The sole test file for `_build_account_client` never asserts on `cfg['mode']` — the account-level `mode: live\|dry_run` execution gate (one of CLAUDE.md's own "two execution gates") is fully untested, which is exactly why CA-A04's landed finding (`getattr(acc, "mode", "live")` against a `TradingAccount` that has no `.mode`) reached all 11 real accounts unnoticed. | `PI-20260927-JT7JW9CR-0004` |
| `grep-only-exit-label-refusal-test` | medium | `test_exit_label_on_anchored_price_path.py` and `test_exit_loop_health.py` grep their own module's source text for a constant name instead of exercising behavior (CA-A01-080's own named E-08 gap, still open). | not filed (medium; CA-A01-080 already tracks the underlying code defect) |
| `ca-a03-highs-shared-zero-detector` | info | Cross-reference: CA-A03 measured **1,908 passing tests across 109 files / 44 modules / 13,465 lines and caught 0 of its 22 findings**, including all 4 highs. Not independently re-verified this session. | see `PI-20260927-ENGC5EUD-0001..0004` (CA-A03) |

**On the 4th finding, stated precisely:** CA-A04 already measured and filed the *code* defect (`PI-20260927-KRAKE1TG-0002`) with a proposed detector it did not build. This lane independently confirmed the *test-suite* side: `tests/test_order_monitor_build_account_client.py`'s 2 tests use `MagicMock()` fixtures (one unconstrained, one `spec`-restricted) and check only `cfg['market_type']` — 0 occurrences of `cfg['mode']` anywhere in the file. An unconstrained `MagicMock()` would additionally *mask* this exact bug class even if asserted on, since it auto-vivifies a `.mode` attribute the real `TradingAccount` class does not have.

## Untested-but-critical: the pattern, stated plainly

Every one of the four high findings above is the **same shape**: a test file exists, is well-named, is green, and runs as part of a large passing suite — but it either (a) exercises the *helper function* in isolation while the *real call site* (the watchdog, the reconciler pass) never invokes it, (b) covers the *success* branches of a function while its *exception* branch is silently untested, (c) asserts the *actual buggy output* as the expected one, sometimes with a comment admitting it, or (d) tests an adjacent field thoroughly while never asserting on the one field that actually gates real money. None of these are caught by `--cov-report=term-missing`'s line numbers, because the covered lines genuinely do execute — they just execute the wrong check, assert the wrong field, or run the right check on the wrong call site.

This matches CA-A01's own words exactly: *"A green run is evidence only for what the tests assert."* CA-A01 named two of these branches as untested (chunk refs A-01, B-02) without writing the regression test; CA-A04 (landed mid-session) named a third code defect with a proposed-but-unbuilt detector. This lane wrote three concrete first-hand verifications of the test-suite side (watchdog-flatclose, orphan-readfail, account-mode-gate) above, confirming each with direct reads of the exact test bodies rather than re-trusting a sibling lane's claim.

## What I read

- **Read in full:** `tests/test_monitor_reconciler.py` (all `_watchdog_stuck_strategies` call sites, 1857–2410), `tests/test_reverse_reconciler.py` (all orphan-adopt tests, 137–990), `tests/test_p3_close_wiring.py` (all `_cancel_resting_protection_after_flat` / close-wiring tests, 201–791), `tests/test_protection_price.py` (all `_bybit_position_protection` tests, 230–320), `tests/test_exit_label_on_anchored_price_path.py` (110–150), `tests/test_exit_loop_health.py` (100–430, spot-checked for the same grep pattern), `tests/test_order_monitor_build_account_client.py` (all 56 lines, both tests).
- **Read as sibling-audit cross-reference, not independently re-verified line-by-line:** `docs/audits/code-audit-2026-09-27/CA-A01.md` + `.findings.jsonl` (order_monitor.py, 60 findings), `CA-A03.md` + `.findings.jsonl` (exits/positions/fills/protection/broker-truth, 22 findings). `CA-A02` and `CA-A04` **landed mid-session** (commit `6035989`, merged into this lane's branch); read their `.md` summaries in full and independently re-verified `CA-A04`'s `monitor-dry-run-gate-reads-absent-attribute` claim (confirmed 0 test files construct a real `TradingAccount` alongside `order_monitor` usage, and 0 assertions on `cfg['mode']` in the only relevant test file) — the other 15 `CA-A02`/`CA-A04` findings were read but not independently re-verified.
- **Sampled, not exhaustively read:** the ~39-file population matching the "test greps its own module's source" pattern (`inspect.getsource` / `.read_text()` + string-membership `assert`) across money-path test files — 5 sampled beyond the two confirmed instances; the other ~34 were not read this session (see finding 4's `population` field for the exact caveat).
- **Not read:** `oanda_client.py`'s 2 test files' bodies (only counted); the ~7,300 test files outside the money-path scope (ML, macro, scripts, CI guards — out of this lane's brief); `CA-A02`'s own `pipeline.py`/intents/sizing/gates test-gap question (its findings are code defects — `halt-flag-does-not-stop-pairs-sleeve`, `safe-place-order-guards-have-no-callers`, `net-position-read-failure-reads-flat` — this lane did not independently check whether a test exists for any of the 3).

## What I would check next with more budget

1. **Function/branch-level coverage** (not just line) on `order_monitor.py` and `coordinator.py` — a targeted `coverage.py --branch` run would likely surface more instances of the same "covered line, wrong assertion" shape without needing to hand-read every test.
2. **CA-A02's 3 highs against the test suite** — `halt-flag-does-not-stop-pairs-sleeve`, `safe-place-order-guards-have-no-callers` (0 production call sites for `MAX_DAILY_LOSS`/`MAX_OPEN_POSITIONS`/per-strategy caps), and `net-position-read-failure-reads-flat` — this lane read the findings but did not check test coverage for any of the three; `safe-place-order-guards-have-no-callers` in particular is a strong candidate given a guard with 0 production callers cannot be caught by any test that only exercises the production path.
3. **The remaining ~34 files** in the grep-only-test population, one by one, the way the two confirmed instances were verified here.
4. **A repo-wide sweep for the `assert X == <the input value>` pattern** (a test that asserts its own fixture's input equals the function's output, proving nothing) — not attempted this session; CA-A01/CA-A03 didn't name an instance of this specific shape, but neither did they rule it out.
