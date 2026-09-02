✅ **DONE** · backlog drain (ml) · `docs/claude/ml-review-backlog.json` · session `session_01Au13tQ9BaLKsEU7youUomr`

**Draft PR [#10735](https://github.com/benbaichmankass/Metis-Insights/pull/10735)** — one file, +78/−11. Releasing the file. I am not merging and hold no merge slot; merge order is the manager's.

**Result: 22 of 22 unresolved rows examined · 0 CLOSED · 22 REFUSED · 1 FILED.** Reconciled at head: `106 → 107` rows, `22 → 23` unresolved, **0 lost, 0 statuses silently changed, 0 pre-existing keys mutated** (asserted structurally, not by eyeball).

**Zero closures is the honest outcome** — no row's exit condition is satisfied. Four I measured rather than carried forward, and all four came back unmet.

---

### ⚠️ THE MANAGER NEEDS THIS TO MERGE — CI is BLOCKED ON APPROVAL, not on a defect

`get_check_runs` on #10735 returns **`total_count: 0`**, which this repo's own docs warn usually means a merge conflict. **It does not here.** `mergeable_state` is **`blocked`**, not `dirty`, and `actions_list` shows the real state:

> **4 `pull_request` runs exist at head `622cba7c`** — `Guards`, `pytest-collect`, `pytest-run`, `repo-inventory`, all created 03:38:35Z — and **every one concluded `action_required`**, with `triggering_actor: github-actions[bot]`.

An `action_required` run contributes no check run, which is why the check API reads 0. **Someone with write access must approve the workflow runs**; I have no lever for it (`issue_write` / `add_issue_comment` / `create_pull_request` all 403 for this session, and `curl` to `api.github.com` 403s at the proxy).

⚠️ **And the gated runs point at `622cba7c`, while the head is now `d7b67bfc`.** Approving them would validate a stale sha, so the head likely needs a re-trigger too.

**Correcting myself on the record:** I first read the zero checks as the documented `pr-opener` trap (a `GITHUB_TOKEN` push not triggering workflows) and pushed a second commit to "arm CI". That diagnosis was **wrong** — the runs had fired all along and were awaiting approval. The commit itself is a genuine fix (the file's top-level `updated_at` read `2026-08-22T10:20:00+00:00` while the file had been edited at least twice since — the 2026-08-29 triage and ml-drain #1 both left it untouched), so nothing needs reverting, but it did not do what I pushed it for and it moved the head past the gated runs.

---

### What actually moved: the class is real, and it is not the class the register says it is

`BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED` says these rows *state no condition that would ever close them*. **For this backlog that is measurably false: 10 of 10 authors DID state one** — as prose inside an `updates` entry (nine literal `TRIGGER:` lines from the 2026-08-16 sysrev pass), which is a history log no field-reading consumer looks at. The exit conditions were never missing; they were **mislocated**.

Promoted each row's own condition into the field the repo's convention prescribes. **The 9 promotions are provably verbatim** — extraction was programmatic and each string was asserted to be a substring of its own serialised row before writing. The 10th named no threshold, so it got a criterion from my own measurement instead rather than a boilerplate one.

**ml `kept_open` no-exit census: 10 → 0** — the first of the four backlogs to clear it. Corpus **36 → 26**. Positive control on the same derivation the guard uses: baseline **0** → strip one promoted field **1** → strip a second **2** → restore **0**, byte-identical.

⚠️ **The number I will not quote alone:** `check_backlog_criteria.py` holds **two** predicates for "has an exit condition" and they disagree — the census (line 160) accepts any of 15 fields → **0**; the per-row field check (line 247) requires `resolution_criteria` → **8 still lacking**. `migrate_backlog_to_work_objects.py:161` also reads only `resolution_criteria`, so **1 of 3 consumers is satisfied, 2 are not**. I chose semantic honesty over the flattering number. Details in the PR.

---

### Heads-up for whoever owns `BL-20260825-…` (I did not file these — wrong home for an ML backlog)

1. **Criterion 2 is 7 rows, not 8.** `MB-20260720-FCPCV-RETRAIN-NOOP` is already `resolved`. And it is a **de-duplication, not an authoring job** — M16/M23/M24/M25/M29 all already have full `ROADMAP.md` milestone-table entries. Two of the seven are also miscategorised and should NOT go to ROADMAP: `MB-20260721-MES-15M-HEAD-PARKED` is an operator **decision**, `MB-20260726-XSYMYZ-RANGEVOL-DEAD` is a **defect**. Actionable set: **5**.
2. **The two-predicate divergence above** — guard/tooling infra, not ML.

### For the ML/trainer side

`MB-20260726-XSYMYZ-RANGEVOL-DEAD`, filed **2026-07-26**, already names the mechanism that `MB-20260829-MANIFESTS-DECLARE-COLUMNS-…` called "the one genuinely puzzling instance" five weeks later and that **ml-drain #1 re-derived from scratch this morning**: a manifest pinning an old `dataset.version` reads a stale shard. Neither 2026-08-29 row points at it. Verified in-tree independently and cross-checked on a *different* instrument than drain #1 used (live `refusing_manifests_24h` → the same 4 manifests).

⚠️ **One refinement to drain #1's framing** ("every one pins a low version string while its builder has advanced"): that is **3 of 4** — `setup-quality-lgbm-v2` pins `v002` against `builder_version 'v2'`. And `market_features.py:412-414` says `builder_version` *"is metadata-only (it does not gate dataset path resolution)"*, so "behind" is not a defined relation between the two. Safe statement: **3 of the 4 pin `v001`; the fourth pins `v002`.**

### Filed

`MB-20260902-SHADOW-ARCHIVES-SYNCED-SINCE-0816-AND-NO-READER-RESOLVES-THEM` — the rotated shadow-prediction archive sync shipped `a620f2a8` (2026-08-16, #9612) and is **exercised** (30/30 `ok` pulls, archives 2→3, its own `none`/`skipped` states never fire), and **nothing reads them**: 35 files mention `shadow_predictions`, only the rotator and the puller touch a rotated form, and every reader resolves one literal path. So the horizon the sync was built to extend has not moved. Two positive controls behind that negative.

### Local verification

`scripts/ci/run_guards.py` on the **committed** tree: **PASS 34 · FAIL 0**, no uncommitted-work caveat. `pytest` was absent from this sandbox — the sole cause of `artifact-validity-guard` / `operator-owed-guard` failing on a first run — so I installed it (9.1.1) and re-ran rather than shipping a caveat. Both genuinely executed. **No guard or test was weakened.**

### Process

`START` posted **before** the first substantive tool call (`#issuecomment-5503796702`), from a separate branch so the relay's result commit could not become the PR head. Board tail proved by a short page (`perPage=12 page=164` → 7 items), per the board body's own rule. Scope stayed one file: no sibling backlog, no `OPEN-ITEMS.json`, no `ROADMAP.md`, no `config/`, no order-path file.
