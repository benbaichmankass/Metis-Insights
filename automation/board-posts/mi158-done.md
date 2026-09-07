✅ **DONE — MI-158** (`session_016Li3hqzjKh3bAX7HYuGu23`) · **PR [#11232](https://github.com/benbaichmankass/Metis-Insights/pull/11232) is GREEN on `d0e8182` and waiting on the manager.**

All four required contexts pass on the current head: `guards` ✅ · `pytest-run` ✅ (10:42:25Z) · `pytest-collect` ✅ · `repo-inventory` ✅. Merges cleanly against `main` (`93f2d4c`) — behind by 7, **0 conflicts**, and **none of those 7 touched `config/strategies.yaml`**, so the validation base is sound. Tier-3, `landing: hold`, no `pr-automerge-requests` file: **the manager merges it, it will not self-land.**

**Shipped:** the MI-156 § 7 diff byte-for-byte (230 insertions, 0 deletions) — `gld_pullback_1h` `tp_r` 50.0→4.0 as the only value change; 24 `tp_intent` declarations (22 `none`, 2 `unexamined`, kept separable); 9 sub-pop-B legs' inherited sentinel written out, **runtime-identity verified with positive and negative controls** (resolved param dicts equal on all 9; fleet-wide the only leg whose resolved params change is `gld_pullback_1h`). Plus the reader: `bracket_expectation_census.py` branches on `tp_intent.mode`, with `--assert-intent-declared` exiting **1 / 25 undeclared** on the pre-change config and **0** after.

---

### ⚠️ Both CI failures had ONE root cause, and it is the finding worth carrying

`guards` and `pytest-run` both went red on `fc25c7f`, and both were the **same** defect: `tp_intent` unclassified in `regime_debt_matrix`'s lever maps. `tests/test_check_harness_lever_coupling.py::test_live_config_has_no_coupling_gap` runs that guard against the **real** config, so the guard and the suite are one signal, not two. Confirmed by restoring `regime_debt_matrix.py` to `fc25c7f` and re-running: **1 failed**; on `d0e8182`: **8 passed**. Both green now.

**The trap, generalised — it will catch the next session that adds any config key:** `regime_debt_matrix` computes `faithful = not omitted` where `omitted` is *every* key outside PLAIN/LEVER_FLAG. So the intuitive remedy (add the key to an UNMODELLED registry) does **not** stop the degradation — UNMODELLED is a guard-side allowlist only. **MEASURED: that route would have flipped 14 of 42 enabled harness-classified legs from `faithful` to `approximate`**, and `approximate` blocks cell authoring — quietly closing cell authoring on 14 legs for a key that changes no behaviour. Correct classification is **PLAIN**, the same treatment `model` / `signal_prefixes` / `description` / `shadow_model_ids` already get. Fidelity now **unchanged on all 42** (0 flips, measured).

**"Runtime-inert" does not imply "ledger-inert."** That is the sentence to remember.

### ⚠️ The work object is STILL absent, now checked against the newest main

`WO-20260907-APPLY-THE-OPERATOR-APPROVED-REACHABLE-TAKE-PROFIT.yaml` is **not** on `origin/main` at `93f2d4c` — *after* dispatch PR #11224 merged. Positive controls: 80 `WO-*.yaml` present, three dated 2026-09-07 (`25-OF-44-ENABLED-LIVE-LEGS-HAVE`, `PER-LEG-BACKTEST-TO-LIVE-EXIT-LOCATION`, `THE-COMMITTED-BACKTEST-CANDLE-FIXTURES-ARE-FLAT`). So the dispatch PR landed objects for MI-156 and MI-157 and **not** for MI-158. Recorded, not re-diagnosed — this is the third today.

### ⚠️ What is NOT done

**The post-merge live read-back has not run, because the PR is not merged.** Merged is not deployed and deployed is not observed; I am claiming nothing about what the trader currently holds for `gld_pullback_1h`. Whoever merges this owes `/api/bot/config` (or `scripts/ops/diag_fetch.sh`) afterwards, and should report "the trader has not picked it up yet" rather than reporting the merge as the outcome if that is what they find.

Remaining follow-on, deliberately not done here: a `tp_intent` consumer in `src/runtime/target_expectation.py`. It sits on the live `target_extension_soak` path, and the census is Tier-1 observe-only and is where the collapse actually bites — one honest consumer, no live-path risk on a Tier-3 PR.
