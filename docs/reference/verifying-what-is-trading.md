# Verifying what is ACTUALLY trading — the six-layer mapping

> **Doc status:** `unknown` · category `lookup` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> ⚠️ That stamp is the registry's, and it is about the DOCUMENT's review
> state, not about the measurements below. Every figure here names its
> population and the date it was taken; a `lookup` doc under
> `docs/reference/` computes to `unknown` until the index's own
> assessment pass reaches it (`scripts/ci/check_document_index.py` R6
> fails a hand-upgraded row), and claiming `live` by hand would be
> claiming a review nobody ran.

Operator directive, 2026-09-22:

> *"we need to be able to verify what's actually trading… it's being operated
> from our VM, which means there is a single source of truth — whatever the VM
> is using is de facto the single source of truth for what's actually trading.
> And if we can't verify that, then we need to build the mechanism to verify
> that… there's no reason that that question should just stay open-ended."*

**This document's value is that it says what we CANNOT see.** A layer marked
available means a session can go and read it today; a layer marked unavailable
means any claim about it is an inference, and must be marked as one.

**Do not upgrade a row you have not actually made readable.** Every `AVAILABLE`
below names the surface that serves it, and every caveat is a measurement, not
an impression.

---

## The one command

```bash
# on the live VM (ict-bot-arm), or anywhere with the repo + the artifact
python3 scripts/ops/verify_running_roster.py

# off the VM — the same composition, served
GET https://ict-bot.duckdns.org/api/bot/runtime-config
```

Exit codes are the three `reload_state` values and are never collapsed:
`0 current` · `2 pending_reload` · `1 unknown` — where **`unknown` is a failure
of the OBSERVATION, not a pass and not a fault in the trader.**

It answers layers **4, 5 and 6**, and whether 4 and 5 agree. It does **not**
answer layers 1–3, and it says so in its own output rather than leaving a
reader to assume it covered them.

---

## The six layers, in descending authority

| # | layer | what it is | readable today? |
|---|---|---|---|
| **1** | **The broker** | the positions and orders that actually exist at Bybit / Alpaca. Nothing on the VM can override it. | **AVAILABLE** — `/api/bot/positions` (`unrealizedPnlSource` separates broker truth from a local mark) and `/api/bot/pnl/broker-truth` |
| **2** | **Realised behaviour** | what the system has actually closed | **AVAILABLE** — `/api/bot/trades/closed`, keyed on `pattern` + `account`. The only layer that proves a leg **traded** rather than was configured to |
| **3** | **Decision-level emissions** | what the system decided, before any fill | **PARTIALLY BROKEN** — two measured defects, below |
| **4** | **The process's loaded state** | the roster and per-account gates the running trader **holds in memory** | **AVAILABLE since 2026-09-22 (E31)** — `/api/bot/runtime-config`, `runtime_status.json::process`. **Was NOT AVAILABLE AT ALL before that date** |
| **5** | **The file on the VM** | `config/accounts.yaml` + `config/strategies.yaml` as they sit on disk | **AVAILABLE** — `/api/bot/config` (honestly named `yaml_mode`), `/api/bot/strategies` |
| **6** | **git `main`** | the merged revision | **AVAILABLE**, and the furthest from the truth |

---

## Layer 4 — what was built, and what it actually establishes

**The defect it removed.** Every surface that claimed to report the running
trader was re-reading `config/`. `runtime_status.json` — the one artifact the
trading process itself writes — computed `live` as
`_read_live_per_account(accounts_yaml)` and `strategies` as
`_read_strategy_names(strategies_yaml)`. Both open and parse the YAML. Two
consumers then described those values in the language of runtime truth:

* `/api/bot/config`'s note said *"live_per_account is the pipeline's runtime
  view"*;
* `/api/bot/strategies`' `loaded` column was documented as *"the names the
  running process actually loaded"*.

Neither was true. **The pipeline WRITING the file is not the pipeline USING
those values**, and `ict-git-sync` pulling `main` every ~5 min makes the gap
look like a deploy: the file moves, every surface re-reads it and shows the new
value, and the process may still be running the old one.

Both claims are corrected in place, and the missing capability was **built**
rather than the claim merely deleted.

**What the process actually holds, and it is ASYMMETRIC.** MEASURED 2026-09-22
by reading the code (`src/strategy_registry.py`, `src/runtime/pipeline.py:364`,
`src/units/accounts/__init__.py`, `src/core/coordinator.py:2795`):

| config | how the process holds it | so an edit on the VM takes effect |
|---|---|---|
| `config/strategies.yaml` | `strategy_registry._cache` is populated on the FIRST `load_strategies()` and never invalidated; `pipeline.STRATEGY_ROSTER` / `STRATEGIES` are module-level, resolved at **import** | **only on process restart** |
| `config/accounts.yaml` | `load_accounts()` re-opens the file on every call — once per tick via `main._resolve_tick_symbols`, once per dispatch via `Coordinator.multi_account_execute` | on the next tick, **no restart needed** |

That asymmetry is why the surface reports **per file** and not as one boolean:
the same "the file changed" produces two different answers about what is
trading.

**How it is sourced.** Each loader stamps what it loaded — the path, a
`sha256` over the exact bytes it parsed, a UTC timestamp, and the values it
resolved (strategy names + execution gates; per-account `dry_run` read off the
`RiskManager` the process built, and the routed-strategy list). The per-tick
writer publishes those stamps. **Nothing in layer 4 is re-derived from
`config/`** — the only thing read off disk at report time is a digest, and its
sole purpose is to be compared against the digest the process recorded.

**The field that does the work** is `reload.<file>.reload_state`:

| state | meaning |
|---|---|
| `current` | the process loaded the bytes that are on disk now |
| `pending_reload` | they DIFFER — the merge reached the VM, the process has not taken it |
| `unknown` | one side could not be established. **Not `current`.** |

**What it does NOT establish.** `current` says the process and the VM's disk
agree. It does **not** say the VM's disk matches `main` (that is layer 6 → 5,
and `git_sha` in the same payload is the link), and it says nothing about
whether any leg has traded (layer 2).

⚠️ **It reports; it never decides.** Nothing in the order path consults it, and
nothing may be made to. The stamping call site inside `load_accounts` is
reached from `Coordinator.multi_account_execute` and is wrapped so that a
failure to OBSERVE degrades to "no stamp" — a surface built to detect a
divergence must not be able to cause one.

---

## A roster is a LIST fact, not a behaviour fact

Operator correction, 2026-09-22:

> *"the roster is not a behaviour-observable fact, it is a LIST-observable fact
> based on the list that is the single source of truth. Just because
> something's not trading doesn't mean it's not on the roster — it could just
> be sidelined. The only problem we would notice is if we see something trading
> that's NOT on the roster."*

So:

* **Never** verify a roster cut by watching for the absence of trades. Absence
  of trading is not absence from the roster, and a probe whose positive control
  is silent proves nothing. MEASURED 2026-09-22 over the 200 most recent closed
  trades (`closedAt` 2026-09-13T20:55:29Z → 2026-09-22T08:48:49Z, ~9 days):
  **199 paper, 1 real money** — a single `bybit_2` / `xrp_pullback_2h` close on
  2026-09-14. At ~0.1 real-money trades/day a behavioural answer would take
  weeks, which is why layer 4 is not optional.
* **The layer-2 check is an INVERSE anomaly detector**, not a confirmation:
  alert when a leg that is **not** on an account's list closes a trade on it.
  That is a positive assertion, it needs no positive control, and it fires on
  the case that actually matters. **NOT BUILT** — see below.

---

## What we still cannot see

Stated as gaps rather than quietly omitted.

1. **Layer 3a — `/api/bot/order-packages?limit=201` returns `rows: []` with
   `count: 0`.** Not an error and not a clamp to the 200 cap: an over-limit
   request is INDISTINGUISHABLE from *"there is no data"*, on a read surface
   the SPA uses. MEASURED 2026-09-22 by bisection: limits 50/100/200 return
   50/100/200; 201 and 250 return 0. Collapsed-state class.
2. **Layer 3b — the decision surface is not tracking the system.** The newest
   `/api/bot/order-packages` row read 2026-09-18T15:16:46Z while closed trades
   existed from 2026-09-22. Four days stale.
3. **Layer 3c — `/api/bot/signals` returns 0 rows under every shape tried**
   (bare, `limit=5`, `limit=50`, `hours=720`, `limit=50&hours=2000` — an
   83-day window). Whether that is *no signals* or *a broken read* **was not
   established, and is not being guessed at.**
4. **The inverse anomaly detector of § "A roster is a LIST fact" is NOT
   BUILT.** Layer 2 can be read by hand; nothing watches it.
5. **Layer 4 covers the two config files, not every runtime input.**
   `config/pairs.yaml` (the M22 pairs sleeve's own gate, on an isolated order
   path), `config/regime_policy.yaml` and the env-var surface carry no stamp.
   A `current` verdict is scoped to `accounts.yaml` + `strategies.yaml` and
   says nothing about those.
6. **Layer 4 is per-PROCESS, and only the trading process writes the
   artifact.** `runtime_status.json` is written solely by
   `pipeline.run_pipeline` → `write_status`. The web API is a separate process
   with its own module state; it reports the trader's stamps, never its own.
7. **A stamp from a process that has stopped ticking is still a true record of
   what that process loaded, and is not evidence about now.** `tick_age_seconds`
   and `bot_ticking` ride with every answer for exactly this reason; they are
   not folded into the verdict.

---

## The rule that falls out of this

**A Tier-3 roster change is not `done` when the file changes. It is done when
layer 4 agrees with layer 6.**

Concretely, for a roster cut:

1. merged — the PR is on `main` (layer 6);
2. deployed — `ict-git-sync` pulled it onto the VM (layer 5; `git_sha` in the
   payload);
3. **observed** — `verify_running_roster.py` reports `current` for the file
   that carries the cut, and the per-account roster it prints under *"the
   roster the process holds"* shows the cut legs gone (layer 4).

Say which of the three you reached. This repo has paid more for collapsing
them than for any other mistake.

---

## Related

* `src/runtime/loaded_config.py` — the producer, and both three-state contracts
* `scripts/ops/verify_running_roster.py` — the one command
* `src/web/api/routers/runtime_config.py` — the served form
* [`docs/api-tier-policy.md`](../api-tier-policy.md) — the route's tier
* [`docs/reference/bot-api-reference.md`](bot-api-reference.md) — payload shapes
