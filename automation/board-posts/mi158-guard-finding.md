🔄 **UPDATE — MI-158** (`session_016Li3hqzjKh3bAX7HYuGu23`) · **PR [#11232](https://github.com/benbaichmankass/Metis-Insights/pull/11232)** is open and **ready** (not draft), Tier-3, `landing: hold`.

**Two findings from this PR that are worth more than the PR itself. Both are traps, not one-offs.**

---

### 1. ⚠️ A "runtime-inert" config key silently downgrades backtest fidelity — and `approximate` blocks cell authoring

`harness-lever-coupling-guard` failed CI on the first push, **correctly**. `tp_intent` was in none of PLAIN / LEVER_FLAG / `_UNREPLAYABLE` / UNMODELLED on 25 enabled harness-classified legs.

The obvious fix — add it to the UNMODELLED registries — is **wrong**, and I nearly took it. `regime_debt_matrix` computes `faithful = not omitted` where `omitted` is *every key outside PLAIN/LEVER_FLAG*, so UNMODELLED is a guard-side allowlist that does **not** stop the degradation. **MEASURED: that route would have flipped 14 of the 42 enabled harness-classified legs from `faithful` to `approximate`** — `gdx_pullback_1d`, `gld_pullback_1h`, `ief_pullback_1d`, `qqq_pullback_1h`, `sol_pullback_2h`, `tlt_pullback_1d`, `mes_trend_long_1d`, `qld_trend_long_1d`, `splg_trend_long_1d`, `tqqq_trend_long_1d`, `trend_donchian_{ada,avax,eth,sol}_4h` — **for a comment-shaped key that changes no behaviour.** `approximate` blocks cell authoring, so it would have quietly closed cell authoring on 14 legs while asserting the harness omits a lever that does not exist.

Correct classification is **PLAIN**, in all three families — the same treatment `model`, `signal_prefixes`, `description` and `shadow_model_ids` already get: keys the harness accounts for *by carrying no behaviour*. After the fix, fidelity is **UNCHANGED on all 42** (0 flips, measured before-vs-after) and the guard passes.

**Generalisation worth carrying:** *any* new `config/strategies.yaml` key — including a pure-provenance one — degrades fidelity on every enabled harness-classified leg that carries it, unless it is added to the PLAIN maps. "Runtime-inert" does **not** imply "ledger-inert". POPULATION for all counts above: the 42 enabled harness-classified legs at `20d7928e`.

### 2. ⚠️ `pr-opener.yml`'s result file is not a reliable success signal — the PR existed with no result written

`automation/pr-results/mi158-apply-reachable-tp.txt` **was never written**, and #11232 **was created anyway**. The `board-post` relay's results commit (`926fcec`) landed on the same branch in the same window and `pr-opener`'s commit-back appears to have lost that race. The relay header already documents the inverse trap (*request file surviving is not evidence of failure*); this is the other direction — **a missing result file is not evidence of a failed PR.** Check `list_pull_requests` by head branch before re-requesting, or a session will open a duplicate PR.

Separately, that header's core warning held exactly as written: **every relay-opened PR starts with ZERO checks** (`GITHUB_TOKEN` recursion prevention). Measured here: `get_check_runs` total_count **0**, combined status `pending` with 0 statuses. The prescribed remedy — *one ordinary push after the PR exists* — worked. I used an **empty** commit deliberately, since the operator approved specific CONTENT and the diff must not move by a byte to arm CI.

---

**CI now:** `guards` ✅ · `repo-inventory` ✅ · `pytest-collect` ✅ · `pytest-run` still running. Locally: **74 guards PASS / 0 FAIL**, and `test_check_harness_lever_coupling.py` 15/15.

⚠️ **Still not merged, therefore still not deployed, therefore not observed.** The done-condition's post-merge live read-back off the fleet has **not** run and I am claiming nothing about what the trader is holding.
