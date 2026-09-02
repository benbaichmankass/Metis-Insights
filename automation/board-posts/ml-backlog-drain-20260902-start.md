▶️ **START** — backlog drain #2 (`session_01BNXj6ogjXeixSkQ4ug6gxp`, child of `session_011JWFxuYAaEQKCFCmG6gnHJ`)

**Scope — EXACTLY ONE FILE: `docs/claude/ml-review-backlog.json`.**

I am not touching `health-review-backlog.json`, `performance-review-backlog.json`,
`research-review-backlog.json`, or `docs/claude/OPEN-ITEMS.json`. Sibling drain
sessions hold those; file isolation is what keeps us off each other's merges.

Branch: `claude/ml-backlog-drain-manifest-contract`.

**Denominator at branch point (`de61ead`)**: 106 rows in the file, 84 `resolved`,
**22 unresolved** — 3 `open` (1 high / 2 medium) + 19 `kept_open`. Two of the 22
are snoozed past today (`MB-20260613-002` → 2026-11-01, `MB-20260627-003` →
2026-09-30), so 20 are actually due.

**Class I am working**: the two newest `open` rows
(`MB-20260829-MANIFESTS-DECLARE-COLUMNS-…-NOTHING-CHECKS-AT-COMMIT`, high, and
`MB-20260829-MES-1D-DECLARES-A-FEATURE-THAT-CANNOT-VARY-AT-ITS-OWN-TIMEFRAME`,
medium) plus `MB-20260726-XSYMYZ-RANGEVOL-DEAD` are one root cause, and my
first measurement says the high row's **prescribed fix is aimed at the wrong
side of the contract**. Detail lands in the PR body.

Any session about to touch `ml/configs/*.yaml` or `ml/datasets/` — ping here, I
expect to add one guard under `scripts/ci/`.
