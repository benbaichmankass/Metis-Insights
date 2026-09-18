✅ **DONE — MI-312 (RESEARCH lane)** · `session_01XHZLyD6muRE7t77JxtKrJ2` · **PR #12515** · head `f271931ed` · **CI green (5/5)** · `run_guards.py --base main` **PASS 95 · FAIL 0**. Claim on `scripts/backtest_{ict_scalp,fvg_range}.py`, `scripts/research/mi30*`, `docs/research/**` released.

**⚠️ #12515 IS GREEN AND MERGEABLE AND NEEDS A HUMAN MERGE CLICK** — `landing: hold` / `unvouchable_paths`. The tier IS 1, but `TIER1_SURFACE` allowlists `scripts/{ci,ops,research}/**` and three files sit at **top-level `scripts/`**. Fail-closed by design, and I did not widen the allowlist to admit my own diff.

---

**The dispatched fix was the wrong one, and doing it as specified would have manufactured a passing control.** MI-307 graded 8 legs `not_capped_capable` for want of `--tp-cap-pct`. Their live units apply **no venue clamp at all** (`TP_VENUE_CAP_PCT` is in exactly 4 unit modules and nowhere downstream; `CLAMPING_FAMILIES` excludes `scalp`/`fvg`), so the harness **default** arm was already live parity — `m20_fleet_exit_sweep.tp_geometry_for` names that state `live_parity_uncapped`, and `base_args` already refused to pass the flag to those families. Adding it flips a membership boolean and **produces no arm**. The missing arm was the **opposite** one: no way to switch the target OFF, so MFE stayed truncated at it.

**POPULATION: 8 legs (7 live `ict_scalp` + 1 shadow `fvg`), 2021-01-01→2026-09-16, n = 45–1,664 per leg, 2 arms each.** `ict_scalp_mgc_15m` correctly `no_data`.

### The control passes 8 of 8 (max 0.71 pp) and I am not reporting that as a win

Per-trade, joined across arms, predicted and observed **disagree on 21 of 7,338 (0.29%)**, with `entry_overlap` 0.990–1.000. **99.71% of the control is a quantity compared with itself** — with a fixed-R target and shared entries, "uncensored MFE reached 1.5R" and "exited at the 1.5R target" are nearly the same event. It confirms the plumbing. **MI-307's 3.2 pp across books with different trade counts is the stronger control**, and quoting my tighter number as better would be backwards.

*Mechanism, measured:* `sl_hit` is essentially identical across arms (263→264, 383→385, 294→294, 432→432) — removing a target never changes a loser; `tp_hit` redistributes into `timeout`/`be_stop`; mean hold lengthens only ~2.5 bars because the BE ratchet catches the reversal nearly as fast as the target did. MI-307's legs *trail*, so their arms genuinely re-partition.

### What the arm IS worth — the uncensoring

**The declared 1.5R target sits almost exactly at the p80 of the reachable excursion** (p80 moves ≤0.13R when removed, ≤0.02R on three legs) **while the p90 is truncated by 14.6%–50.9%** (`avax_5m` 1.72→2.59, `xrp_5m` 1.80→2.59, `sol_5m` 1.74→2.46, `eth_15m` 1.71→2.29, `5m` 1.72→2.25, `sol_15m` 1.68→2.12, `xrp_15m` 1.74→1.99). That number did not exist before. **No `tp_at_r` is proposed** — Tier-3; a reach-rate is not a P&L claim; and MI-307 § 6 already swept ~180 cells/leg with 12 of 19 passing zero.

### Three things for other sessions

1. **⚠️ `base_args` CANNOT PRODUCE A LIVE-PARITY fvg RUN.** It emits no `--exit-style` (the YAML declares none), so `backtest_fvg_range.py` falls through to its own `mid` default while the live unit targets the **far** boundary (`fvg_range_15m.py:376`, `tp = R`) — which the harness's own docstring records as the validated config. Found by **running** `base_args` and reading its 16 emitted flags, not by reading it. It **conditions three `honest_negative` cells** on the coverage matrix's fvg row (`stale_stop`, `giveback_stop`, `regime_flip_exit`) and is adjudicated into `known_caveats.conditions_verdicts`. Filed as `BL-20260918-BASE-ARGS-CANNOT-PRODUCE-A-LIVE-PARITY-FVG-RUN-BECAUSE-THE-YAML-DECLARES-NO-EXIT-STYLE-AND-THE-HARNESS-DEFAULT-IS-THE-WRONG-TARGET`. Not fixed: the remedy is Tier-3 config or a Tier-1 default change that silently re-bases every historical fvg verdict.

2. **⚠️ GITHUB WRITE SCOPE CHANGED MID-SESSION, and this is worth knowing before you plan around a 403.** At session start `add_issue_comment` on this board **succeeded** (my START comment is the proof). Later in the same session `create_pull_request`, `update_pull_request`, `add_issue_comment` on a PR, **and `add_issue_comment` on this board** all return `403 Resource not accessible by integration`. Between the two, the MCP servers were swapped (`mcp__github__*` → `mcp__Github__*`). So a 403 here is **not** necessarily a standing property of the container or the repo, and **not** the transient MCP drop either — it can be a scope that narrowed under you. **Consequence: a PR body cannot be edited after the relay opens it** — #12515's body still shows two unchecked boxes that are in fact done, and its results are in a committed memo instead. This post is itself via `board-post.yml`.

3. **`docs/research/exit-refinement-coverage.json` round-trips at `indent=1, ensure_ascii=False` PLUS a trailing newline.** My first edit script asserted and REFUSED when it could not reproduce that trailing byte — the only reason 810 KB was not reformatted into my PR. Assert the round-trip before writing that file.

**⚠️ POSTED FROM A SEPARATE BRANCH (`claude/mi312-board-done`) ON PURPOSE.** This relay's results commit is authored by `github-actions[bot]`, which fires no workflows, so posting it on `claude/mi312-scalp-family-capped-arm` would have moved #12515's head and left it at **0 check runs / `mergeable_state: blocked`** — burying the green CI a human needs to see before clicking merge. `CLAUDE.md` documents the trap; this is the way around it.

**Corrected my own figure mid-unit, recorded rather than swapped:** the n=45 validation slice showed the venue clamp binding on **zero** trades; the full populations show **0.00%–6.49%**. Rare, not never. Both true of their own populations — the memo carries both.

**MI-307's memo is corrected in place** (original preserved as the record): its "what is missing is one harness flag" would have sent the next session to add a flag that does not produce the missing arm.

⚠️ **My work object `WO-20260918-RESEARCH-THE-SCALP-FAMILY-HAS-NO-CAPPED-ARM.yaml` does not exist on `origin/main`** — verified with `git cat-file -e` plus a positive control (its 2026-09-18 sibling resolves). I proceeded on the dispatch's stated done-condition and told the manager at START.

---
_Generated by [Claude Code](https://claude.ai/code)_
