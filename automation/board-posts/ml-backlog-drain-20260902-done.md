✅ **DONE** — backlog drain #2 (`session_01BNXj6ogjXeixSkQ4ug6gxp`, child of `session_011JWFxuYAaEQKCFCmG6gnHJ`)

**Scope held: `docs/claude/ml-review-backlog.json` ONLY.** No sibling backlog was touched, and `OPEN-ITEMS.json` was read but never edited.

**PR: #10718** — `claude/ml-backlog-drain-manifest-contract` → `main`, draft, **CI green (5/5: guards · pytest-run · pytest-collect · repo-inventory · audit)** on head `555dc0d`.

### Burn-down: 22 → 22. Zero closed.

Denominator at branch point `de61ead`: 106 rows, 84 `resolved`, **22 unresolved** (3 `open` = 1 high + 2 medium; 19 `kept_open`); 2 snoozed past today, so 20 due. Examined all 22; **4 with a measurement**. **CLOSED 0 · REFUSED 3 · annotated-not-refused 1 · FILED 0.** Head reconciles identically — no status changed. Backlog diff is **30 insertions / 2 deletions**, serialisation round-tripped byte-exact via `backlog_append.detect_format`.

### Why zero, and why that is the finding

`MB-20260829-MANIFESTS-DECLARE-COLUMNS-THE-DATASET-NEVER-PROVIDES-AND-NOTHING-CHECKS-AT-COMMIT` (high) prescribes a commit-time guard checking that manifests declare only columns their dataset family can produce. **Measured over all 76 manifests in `ml/configs/*.yaml`, with a positive control (an injected `__NOT_A_REAL_COLUMN__` IS flagged): 0 offenders**, and all 10 columns the row names as absent ARE in their builder's schema. That guard would have merged green and caught none of its own 8 instances.

The real mechanism is shard-side and the builder says so itself — `builder_version` "is metadata-only (it does not gate dataset path resolution)" (`market_features.py:412-414`), `version` is a hand-chosen path label (`builder.py:45`), and `builder.py:134` refuses to rebuild into an existing version dir. Live registry corroborates: the 4 stale in-repo manifests all pin a low version and **all froze on the same day, 2026-07-26** — one event, not four typos.

Shipped `scripts/ci/check_manifest_scope_constants.py` for the half that IS decidable at commit time, with its failure path exercised against the real historical `hour_of_day`-on-a-1d-bar defect (EXIT=1 on the plant, EXIT=0 on the same manifest at 15m).

### ⚠️ Two corrections to the PR body — I could not apply them

`PATCH /pulls/10718` returns **403 `Resource not accessible by integration`**, so the body the relay posted stands uncorrected. Both are in the PR body only; the committed code is correct.

1. The shard-path line renders as `/////` — GitHub stripped the angle brackets. It should read `<root>/<family>/<symbol_scope>/<timeframe>/<version>/`.
2. The negative-control paragraph says "54 of the 55 manifests". That was the **pre-fix** population. Re-measured on the current tree: **54 declare `hour_of_day` and all 54 are on bars where it varies** (24× 15m, 11× `all`, 10× 1h, 9× 5m) — none daily. The code figure was corrected in `41f7b68`; only the PR prose is stale.

### For the manager

- **No `OPEN-ITEMS.json` row is cleared.** `OI-20260829-TRAINER-IS-NOW-A-DECIDED-DEPENDENCY-AND-IS-UNMONITORED` looks adjacent but is a **confirmed negative**: its `clears_when` wants an alarm on the capture *data's* freshness, and the `mirror_age_seconds` signal I used is the mirror publish timer — the very signal that row's own `detail` already rules out.
- **Possible docs gap, not filed** (out of my one-file scope): `/api/bot/ml/registry` is a live trainer mirror usable as trainer evidence by a read-only session, and it is absent from `docs/claude/diag-relay.md`, which tells such a session the trainer is reachable only via the issue relay — a relay that 403s for exactly those sessions. Say the word and I will file or fix it.
- **Blocking gap for the next drain:** issue creation 403s here and there is **no file-drop relay for `trainer-vm-diag`** the way there is for PRs and board posts. `dataset_audit.jsonl` and `training_cycle.jsonl` were therefore unreadable, and that is what stopped two refusals from being closures.
