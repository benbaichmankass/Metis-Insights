▶️ **START** — backlog-drain session #2 (health backlog)

**Scope: EXACTLY ONE FILE — `docs/claude/health-review-backlog.json`.** I touch no other backlog file, not `docs/claude/OPEN-ITEMS.json`, no `config/`, no order path, no Tier-3 file.

Also writing (Tier-1, CI tooling only):
- `scripts/ops/check_backlog_criteria.py` — extending the existing guard
- `tests/` + `scripts/ci/run_guards.py` registration if needed

**Denominator at base sha `943a7192`:** 1094 rows — `open` 331 + `kept_open` 187 = **518 unresolved**.

**What I am taking:** criterion 3 of `BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED` — a diff-scoped guard refusing a row that TRANSITIONS INTO `kept_open` carrying no exit condition. Drain session #1 (PR #10707) assessed this row and named criterion 3 as the tractable, compounding half; I am building it, not re-assessing it.

Sibling sessions hold the other backlog files — I am not touching them.
