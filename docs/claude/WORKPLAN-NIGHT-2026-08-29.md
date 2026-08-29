# Overnight workplan — 2026-08-29 → 30 · **M20 Active Trade Management**

**Operator-authorised unattended run.** Scope, autonomy envelope and priority were all
set by the operator in-conversation on 2026-08-29 (see § 0). This plan is **subordinate
to** [`WORKPLAN-2026-08-29.md`](WORKPLAN-2026-08-29.md) — it does not supersede it, it
schedules a night's work against its Lane B.

**Written to be executable without me.** Every item states its tier, its stop condition,
and what "done" looks like as an *observable*, not as an intention. A fresh session can
pick this up cold; § 8 is the paste-ready prompt.

---

## 0. DECIDED — operator, 2026-08-29, in-conversation. Do not re-litigate.

| # | DECIDED | reversal |
|---|---|---|
| **N-D1** | **Merge #10431 tonight** (B9 decisions applied). It gates N2 — the base-arm pin changes what a re-sweep measures. | — (executed) |
| **N-D2** | **Autonomy envelope: Tier-1 merge + free-runner dispatch + Tier-2 on the TRAINER VM.** Tier-3 remains never-autonomous. | Operator narrows it. ⚠️ See the self-imposed trainer guardrails in § 1 — the envelope was granted wider than requested. |
| **N-D3** | **Re-sweep ALL 41 legs**, not just the 18 contaminated, so the corpus shares one convention and old-vs-new is a clean diff. | — |
| **N-D4** | **M20 governs tonight.** `WORKPLAN-2026-08-29.md`'s D4 (P1/P2 promotion gate first) **stands as the next DAYTIME priority** — deferred, not cancelled. | Operator re-prioritises. |

⚠️ **The 23 CLEAN legs are a CORRECTNESS CONTROL, not filler.** They must come back
**numerically identical** to the committed corpus. If they do not, the pin changed
something it should not have and **N7's diff is void** — that outcome is a finding, not a
nuisance, and it outranks everything else in this plan.

---

## 1. Hard guardrails — binding for the whole night

**Never, under any reading of the envelope:**

- **No Tier-3.** No `config/strategies.yaml` lever edits, no risk/sizing change, no
  account-mode flip, no live promotion. Every lever outcome tonight lands as a
  **proposal**, never a declare. (The two config deletions in #10431 were separately
  operator-approved and are already merged; that authorisation does **not** extend.)
- **No live-VM mutation.** No `system-actions` beyond Tier-1 reads, no env flip, no
  service restart, no deploy.
- **Not mine to write:** `docs/claude/OPEN-ITEMS.json` and the three review backlogs
  (`health-` / `performance-` / `ml-review-backlog.json`). The trainer session and
  `/system-review` (#10414) own those. Findings are handed over on board #6927 instead.

**Trainer VM — self-imposed, tighter than the envelope granted.** The operator widened
autonomy to include trainer Tier-2; a sibling session (`01UTcLyMtHKYkhMiMKk1asbv`) is
actively working that box and it is now load-bearing for `ict-orderflow-capture`, whose
data is **forward-only and unbackfillable**.

1. **Claim `🔒 VM-LANE` on #6927 before any trainer action**, and queue behind an open
   claim (FIFO, running never preempted). An open claim by the sibling ⇒ do not dispatch.
2. **Do not touch** the orderflow capture, its unit, its output path
   (`datasets-out/market_microstructure/…`), the diag unit allowlist, or
   `publish_trainer_mirror.sh`.
3. **Run no disk cleanup and delete nothing under `datasets-out/`.** The capture writes
   into that same tree. Disk, **INHERITED not measured by me** — from the sibling
   session's read-only relays #10422/#10423 on 2026-08-29: **42G used of 45G, 3.8G free
   (92%)**, of which `/home/ubuntu/ict-trading-bot` alone is 28G. ⚠️ Two free-space
   readings a day apart (3.9G → 3.8G) are **two points, not a fill-rate trend** — do not
   project from them.
4. **Prefer free GitHub runners.** Per `vm-resource-management.md` the trainer is a
   single scarce core; every item below is runner-shaped. **The trainer envelope is
   expected to go unused** — that is the intended outcome, not a shortfall.

**Merge protocol on EVERY merge** (`CLAUDE-RULES-CANONICAL` § Multi-session coordination):
board tail read **proven by a short or empty page** → `🔒 CLAIM` on #6927 → CI green on
the head being merged → merge → **verify against `origin/main`, never off the merge
event** → `🔓 RELEASE`. ⚠️ **Arming auto-merge IS merging** — the claim goes first.

**Stop and wait for the operator if:** a guard fails in a way that needs a Tier-3 call ·
the sibling session flags a conflict on the board · the re-sweep's CLEAN-leg control
fails · anything would require editing `config/strategies.yaml` · two consecutive
attempts at the same item fail.

---

## 2. Why these items, in M20's own terms

`ROADMAP.md` M20 (**Active Trade Management**, renamed from *Exit Refinement* by operator
directive 2026-08-20) is explicit that the milestone is **not** exit-timing alone: *"the
bracket must carry a predictive expectation at entry, and active monitoring may revise it
in EITHER direction — extending a target on an outperforming trade is in scope; every
lever screened before this reframe could only cut a trade short."*

Tonight's items map onto that reframe rather than onto the old cut-it-short framing:

| item | M20 clause it serves |
|---|---|
| **N2/N7** re-sweep + verdict diff | the **bracket's** predictive expectation — measured against production's real exit for the first time |
| **N4** extension soak | the **extend-the-winner** half, which is the part the rename exists to protect |
| **N5** time-stop proposal | a revision lever production does not have; evidence assembled, decision left to the operator |
| **N3** matrix detector | the milestone's **done-condition** is the coverage matrix; a matrix that silently misreports is not a done-condition |

---

## 3. The night, in order

Ordered so nothing blocked stalls the rest. **N2 is kicked FIRST because it is the long
pole** — it accrues while N3–N6 proceed.

### N1 — Land #10431 · Tier 1 · IN FLIGHT
Auto-merge armed 20:43:45Z under the claim posted 20:42Z.
**Done when:** `origin/main` carries the squash **and** a direct read of
`scripts/research/e35_bracket_geometry_sweep.py` on `main` shows the `NO_BAR_COUNT_EXIT`
pin at the base-arm call site. Then `🔓 RELEASE`.
**If red:** it is mine to drive to green — the same head was already green at 13,482
passed, so a failure is most likely base-branch; verify that before assuming.

### N2 — D2: full 41-leg e35 re-sweep at the corrected base arm · Tier 1 · runners
**Gated on N1** — dispatching before the pin lands measures the old convention and wastes
the night. `e35-bracket-sweep.yml`, `workflow_dispatch`, `only=""` (every runnable leg),
defaults otherwise (`days=1830`, `gate_top=2`, `singles_only=false`, `max_parallel=6`).
**Done when:** the run completes and its per-leg artifacts are collected.

⚠️ **CORRECTED 2026-08-29T20:57Z — this row previously said "do not overwrite
`docs/research/e35-bracket-corpus.jsonl` in place; write the new rows beside it". THAT
INSTRUCTION IS UNENFORCEABLE and following it would have wasted time at 03:00.** The
workflow's corpus job **unions into that file IN PLACE and commits+pushes to the ref it
ran on** — run #5's own commit message reads `corpus …/e35-bracket-corpus.jsonl:
2204 -> 2219 rows (15 added, 15 superseded)`. Rows sharing a `measurement_key` are
**superseded**, which is exactly the "before" state N7 needs. Found by reading that
commit message, not by it going wrong.

**Git already solves it — but only because the baseline was named BEFORE the sweep
committed.** It is:

> **N7's BEFORE = `git show a986ac3:docs/research/e35-bracket-corpus.jsonl`**
> Measured at that sha: **8,211 rows · 8,211 unique `measurement_key` (zero duplicates)
> · 41 legs · 8,199 `measured` + 12 `inert_equals_base`.**

The sweep runs on `main`, so its corpus commit lands as a child of `a986ac3` and the
before/after pair is a plain two-sha diff. Also posted on board #6927 at 20:57Z so the
baseline survives this file.

⚠️ The committed corpus is still the ONLY durable copy of the measured cells
(`BL-20260823-E35-SWEEP-EVIDENCE-HAS-NO-DURABLE-PATH`) — which is *why* the before-sha
must be named rather than assumed recoverable.

### N3 — B10: the `bracket_geometry` staleness detector · Tier 1
The gap that let the matrix carry 8 cells as `passed_unshipped` while they were live on
real money all day. `matrix-config-agreement` grades 4 levers and `bracket_geometry` is
not one — correctly, since `_arms()` tests key *presence* and every leg always declares
`tp_r`/`atr_stop_mult`. **The right detector is VALUE agreement:** the cell id encodes the
values (`tp3_sm2` → `tp_r=3.0`, `atr_stop_mult=2.0`), so a `shipped` cell can be checked
against the declare.
**Done when:** the guard is registered in `run_guards.py`, is **green on the current
tree**, and has a self-test that fails on a synthetic mismatch. Ship the guard and the
reconciliation together — a guard whose first CI run is red is the pattern
`check_matrix_config_agreement.py`'s own header warns against.

### N4 — B5: re-read `target_extension_soak` · Tier 1
**⚠️ Read `expectation_state` BESIDE `extension_state`, and quote "rows written", never
"times evaluated."** An all-sentinel result means the expected composition, **not** a dead
lever — `evaluate_extension` returns `EXT_NO_EXPECTATION` before the approach gate and
`not_approaching` is excluded from `_LOGGED_STATES`, so real-target legs log **nothing**
until price approaches. This exact over-read was made and corrected on 2026-08-29
(`BL-20260826-TARGET-EXTENSION-SOAK-IS-100PCT-SENTINEL-AND-CANNOT-YET-OBSERVE-THE-LEVER`).
**Done when:** the row count, the leg composition, and whether any of the ~10 real-target
legs approached its target are stated with the population. **A null result is a valid
outcome** — say so plainly rather than manufacturing a finding.

### N5 — D1b: assemble the time-stop proposal · Tier 1 (evidence only)
Four legs are **CLEAN and blocked** — `mes_trend_long_1d`, `tlt_pullback_1d`,
`gld_pullback_1h`, `eth_pullback_2h`. Their base arm **was** live-parity, and a shorter
hold still beat it at the gate (`gld_pullback_1h` `tp6_sm1.5_to24` walks forward **6/6**).
That is the uncontaminated case that production is missing a revision lever.
**Done when:** a `docs/design/` proposal exists stating the evidence, the population, what
a live bar-count exit would have to look like, and what would falsify it.
**⚠️ Proposal only. Tier-3. Do not implement, do not declare.**

### N6 — B6, split · Tier 1 prep only
Six cells carry no B4 interaction (`qqq_trend_long_1d` vol_trail · `mhg_pullback_1d`
stale12 · `ict_scalp_sol_15m` exit_ladder · `trend_donchian_eth_prop` exit_head_ml · the
shadow-fleet stale_stop + trail_decay batch) → assemble as a **Tier-3 proposal packet**.
Two cells are trail3 on `tlt_pullback_1h` / `uso_trend_1h`, validated at an
`atr_stop_mult` **B4 already moved** → **re-sweep those two at the new stop** before they
can be proposed at all.
**⚠️ B9 adds a precondition B6 did not have:** check each candidate leg's timeout status
first. `tlt_pullback_1h` and `uso_trend_1h` are CLEAN; `mhg_pullback_1d` is CLEAN;
`ict_scalp_sol_15m` and the shadow-fleet batch are **absent from the e35 corpus** and so
**ungraded on that axis — which is not the same as clean.**

### N7 — D2 deliverable: the old-vs-new verdict diff · Tier 1
**Gated on N2.** Produce and commit the per-leg, per-cell verdict diff, **BEFORE =
`git show a986ac3:docs/research/e35-bracket-corpus.jsonl`** (8,211 rows / 41 legs), AFTER
= the corpus at `main` once the sweep's own commit lands. See N2's correction — the file
is unioned in place, so the diff is a two-sha comparison, not two files.
**Run the CLEAN-leg control FIRST** (§ 0): 23 legs must reproduce identically. State the
control's result before any other number.
**⚠️ Applying any resulting verdict change to `config/strategies.yaml` is Tier-3 and is
NOT tonight's work.** The deliverable is the diff and its reading.

### N8 — Wrap · Tier 1
Sprint log (`sprint-format`), `doc-freshness`, workplan updated with what actually
happened, `✅ DONE` on #6927, findings for the backlogs **handed over on the board** rather
than filed. Report on both loud OPEN-ITEMS rows without editing the file.

---

## 4. Cadence

Self check-in **every ~45–60 min** (`send_later`). Each: read board tail → check any
in-flight run/PR → advance or re-arm **silently** if nothing changed. No user ping unless
a stop condition in § 1 fires. Never `sleep`-poll; never idle on a red PR I own.

---

## 5. What I expect to be able to say in the morning

| | claim | how it will be evidenced |
|---|---|---|
| 1 | The e35 corpus is measured against production's real exit | the 23-leg CLEAN control reproducing identically, stated first |
| 2 | The matrix can no longer silently misreport a shipped bracket | a registered guard, green, with a failing self-test case |
| 3 | Whether the extend-the-winner lever has fired yet | rows written, with population — **including "none, and here is the denominator"** |
| 4 | The time-stop question is decidable | a proposal with its falsifier named |

**What I will NOT be able to say:** that any lever shipped, that any verdict was applied,
or that live behaviour changed. None of that is autonomous.

---

## 6. Known traps, carried forward so they are not re-learned at 03:00

- **`curl` to `api.github.com` returns 403 from this sandbox** and a naive parse turns the
  refusal into a fake "pending". Use `mcp__github__*`. Walked into once on 2026-08-29.
- **A full page of board comments is not the tail.** Only a **short or empty** page proves
  the end.
- **`inert_equals_base` rows carry `net_total_r: null`.** Coercing them to `0.0` invents
  findings — it produced 8 spurious ones on 2026-08-29 before the arithmetic caught it.
- **`timeout_bars=0` is not "no timeout"** — it exits on the entry bar, silently.
- **A negative needs a denominator.** Never grade `clean` off silence; show the probe can
  find a positive.
- **Verify a merge by reading `origin/main`**, never off the merge event.
- **Never push to a branch armed for auto-merge** — a new head SHA strands the arm and
  invalidates its CI. A follow-up PR is the way (this correction is one).
- **A plan instruction can be unenforceable.** N2's original "write the rows beside it"
  described behaviour the workflow does not have. Check what the tooling actually does
  before writing an instruction that depends on it — reading run #5's commit message cost
  one minute and would have cost a confused hour at 03:00.

---

## 7. Deferred, explicitly

**B7** (20 pending cells) · **B8** (evidence durability — same root as Lane T) ·
**P1/P2** (per N-D4, next daytime priority) · **Lane A** (calendar-bound; the soak-watch
timer fires Monday 14:30Z on its own and needs nobody).

---

## 8. Paste-ready prompt for a fresh session

```
Read docs/claude/WORKPLAN-NIGHT-2026-08-29.md first — it is tonight's standing plan and
carries the operator's autonomy envelope in § 0 and its guardrails in § 1. Then
docs/claude/WORKPLAN-2026-08-29.md (the parent plan) and docs/claude/OPEN-ITEMS.json
(READ ONLY — the trainer session owns that file tonight).

Post a ▶️ START on board issue #6927 before your first substantive tool call, proving the
tail with a short or empty page.

Scope: M20 Active Trade Management, items N2-N8. Tier-1 merge + free-runner dispatch are
autonomous; Tier-2 on the trainer is authorised but self-restricted per § 1 and expected
to go unused. NEVER Tier-3: no config/strategies.yaml lever edit, no live promotion.

Start at the first item in § 3 whose "done when" is not yet observable. Do not restart B9
— it is measured and landed (#10430, 2ed2f21; decisions applied in #10431).
```
