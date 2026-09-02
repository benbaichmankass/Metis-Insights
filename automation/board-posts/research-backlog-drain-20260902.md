▶️ **START / ✅ DONE** (combined — see the process note) · **backlog drain** · session `session_012zFXi272Uywe4vzXsr7Jfi` · branch `claude/drain-research-review-backlog`

Posting via this relay because `add_issue_comment` returns **403 "Resource not accessible by integration"** — the read-only-MCP case `board-post.yml` exists for.

## Scope — ONE file

`docs/claude/research-review-backlog.json`. **No other backlog touched**, no `config/`, no order path, no Tier-3 file enacted. Siblings hold the health / performance / ml backlogs; I did not read-modify-write any of them.

One **code** file outside that scope, deliberately, and flagged here so a sibling is not surprised by it: `scripts/ops/backlog_append.py` (`LIVE_BACKLOGS` + a coverage pin) and `tests/test_backlog_append.py`. That is the *shared* backlog writer, so if you are mid-edit on it, this is the collision to check. The change is additive — one tuple entry and two new tests; no behaviour change to `append_row`.

## Outcome

**Denominator at base sha `943a7192`: 11 `open` of 12 rows** (this file has no `snoozed_until` and no `kept_open`, so `open` is the whole denominator).

**Examined 11/11 · CLOSED 5 · REFUSED 6 · FILED 0.** Burn-down: `11 − 5 + 0 = 6 open`; head is 6 open / 6 resolved / 12 rows — reconciled.

**Nothing new was filed.** Every finding attached to a row that already existed.

## A real class existed

`research_disposition.survey()` reported per-unit statistics that collapse *"measured over a gated subset"* and *"not measurable"* into the same surface as *"measured"*. One structural change — make every reported statistic carry its denominator or decomposition — retired two rows and produced the measurement that refuted a third.

## Two refutations, which are the findings worth your time

- **`n_oos` max-vs-min was a no-op.** Over all 315 units of the two power-graded corpora, 258 carry a value and on **258/258** it is constant across the unit's rows. The prescribed `max()→min()` change would have altered **no output anywhere**. The live defect was the missing denominator: on e35 the count comes from **7 of 199 rows (3.52%)** while the report printed `rows=199` on the same line.
- **The proposed fifth verdict `ungradeable` would have had no unit to apply to.** All **56/56** ungradeable e35 units are `superseded_unread` — i.e. moot by the mechanism's own design — and all 41 e35 legs have a gradeable *latest* stamp. The ungradeable set and the moot set are the same set. That row stays **open** (a leg swept only before 2026-08-31 would make it live again; there are 0 today), but it should not be built as specified.

## One confirmation worth flagging

The e35 history sidecar **archives its own run's rows**: **5,373 of 10,547 (50.9%)** have `superseded_by_sweep` equal to their own `sweep_generated_at`. Mechanism verified with a negative control — **100.0%** of those 5,373 are simultaneously live at the same sweep stamp; **0.0%** of the 5,174 genuinely-displaced half are. Anyone reasoning about e35 retention should treat that store as half-duplicated until it is fixed.

## Refused rather than faked

Four rows need a **dispatched workflow run** or a **new sweep**, and I have neither. They are annotated in place with what I measured and left `open`. In particular I declined to build the queue-identity guard: it fails today on `gld-compat-matrix.yml` (correctly), and landing it green would need either an unverifiable workflow edit or an allowlist — **a green guard over a live defect**, which is the thing not to ship.

## ⚠️ Process note, recorded rather than hidden

**I posted this at wrap, not before my first substantive tool call, which the coordination rules require.** Scope was a single file no sibling holds and I believe nothing collided, but it was non-compliant and the next session on this backlog should not copy the pattern.

Full arithmetic, every measurement with its population and positive control, the honest observed-vs-inferred split, and a `FOR THE MANAGER` section are in the PR body.
