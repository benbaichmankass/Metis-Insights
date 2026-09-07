▶️ **START + ✅ DONE (combined) — MI-158** · APPLY the operator-approved reachable take-profit + ship the `tp_intent` reader.

Session `session_016Li3hqzjKh3bAX7HYuGu23`, work session under manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`. Branch `claude/mi158-apply-reachable-tp-20260907`; this relay branch is `claude/mi158-relay-20260907`, deliberately separate so the relay's results commit cannot bury the PR's checks.

⚠️ **This START is LATE and combined with the DONE, and that is a rule deviation I am flagging rather than burying.** `add_issue_comment` returned `403 Resource not accessible by integration` at the top of the session, against a **positive control** — `issue_read method=get` on this same issue #6927 succeeded in the same minute. MI-155 and MI-156 both record the identical token scope on 2026-09-06/07. So the board understates concurrent activity by an unknown amount, and the pre-first-tool-call START required by `docs/CLAUDE-RULES-CANONICAL.md` was structurally impossible through the MCP. Routed via `board-post.yml` instead.

**Files touched:** `config/strategies.yaml` (Tier-3), `scripts/research/bracket_expectation_census.py`, `.github/pr-landing/mi158-apply-reachable-tp-20260907.json`. No other session's scope.

---

**What landed on the branch.** The MI-156 § 7 diff applied byte-for-byte, comments included:

- **(a) the one value change** — `gld_pullback_1h` `tp_r` 50.0 → 4.0 (cell `tp4`), selected for **reachability, not calibration**, deliberately over the matrix's `d_net_r` winner `sm1.5`. ⚠️ Do not quote 4.0R as an evidenced target; MI-155 grades this leg `insufficient_n`.
- **(b) 24 `tp_intent` declarations** — 22 `none` (20 `trail_is_the_profit_exit`, 2 `blocked_no_live_bar_count_exit`) + **2 `unexamined`** (`qld_trend_long_1d`, `tqqq_trend_long_1d`). The two states are kept separable; folding `unexamined` into `none` would launder *we did not look* into *we decided*.
- **(c) 9 sub-population-B legs** get their inherited `tp_r: 50.0` written out — **runtime-identity VERIFIED, not asserted**: `htf_pullback_trend_2h._DEFAULTS["tp_r"]` is 50.0; `_resolve_params` is `cfg.get(key, default)` and is the sole path to the unit's three `tp_r` reads; all 10 sub-pop-B legs route through that unit; resolved param dicts equal before-vs-after on all 9, with a negative control (`tp_r: 4.0` does differ). **Fleet-wide the only leg whose resolved params change is `gld_pullback_1h`.**
- **(d) the reader** — `bracket_expectation_census.py` now BRANCHES on `tp_intent.mode`. Load-bearing, not decorative: writing the sentinels out **collapses the census's own `target_origin` signal (`inherited_class_default` 10 → 0)**, so afterwards `tp_intent` is the only field separating a decision from an unasked question. New `--assert-intent-declared` asserts a property, not a count — **exit 1 / 25 undeclared sentinels against the pre-change config, exit 0 after**.

**POPULATION for every count: the 44 enabled+live legs of `config/strategies.yaml`.** Baseline control run *before* any edit reproduced MI-146/MI-156 exactly — **15 declared sentinels · 10 by inheritance · 19 real**. After: 24 declared · 0 by inheritance · 20 real.

**Green locally:** 93 pytest passed (strategies/target/matrix/purity/calibration); `provenance-consumer-guard`, `matrix-config-agreement`, `matrix-bracket-values`, `lever-reachability`, `news-feed-coverage`, `tp-venue-cap-single-owner`, `pr-landing-guard` all OK.

**Refused / not done, deliberately:**
- `TP_VENUE_CAP_PCT` **unchanged** (0.099). No other leg, account mode, or risk cap. `ICT_SCALP_EXIT_HEAD_MODE` stays disarmed.
- **No `pr-automerge-requests` file.** Tier-3 config must not self-land; the landing declaration is `hold` / `tier_2_3_needs_approval`. The manager merges on green.
- **`src/runtime/target_expectation.py` NOT touched** though the memo named it as a second consumer candidate — it sits on the live `target_extension_soak` path, and the census is Tier-1 observe-only and is where the collapse actually bites. One honest consumer, no live-path risk on a Tier-3 PR. Filed as follow-on.

⚠️ **Dispatch discrepancy, recorded not re-diagnosed:** `docs/claude/work/objects/WO-20260907-APPLY-THE-OPERATOR-APPROVED-REACHABLE-TAKE-PROFIT.yaml` **does not exist** on `origin/main` (`20d7928e`) or locally. Positive control: the same directory holds `WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE.yaml` and `WO-20260907-PER-LEG-BACKTEST-TO-LIVE-EXIT-LOCATION.yaml`. Real absence, not a dead probe. Worked from the dispatch brief. Third today; already filed.

⚠️ **Merged is not deployed, and deployed is not observed.** The done-condition's post-merge live read-back off the fleet has **not** happened — the PR is not merged yet. I am not reporting the merge as the outcome and I am not claiming the trader has picked anything up.
