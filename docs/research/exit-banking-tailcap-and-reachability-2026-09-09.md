# The banking zero is not tail-bound, and the producer is built — two premises corrected

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> **MI-209b** · object [`WO-20260908-EXIT-MECHANICS-THE-TRAILING-AND-BANKING-HALF`](../claude/work/objects/WO-20260908-EXIT-MECHANICS-THE-TRAILING-AND-BANKING-HALF.yaml) (closed 2026-09-09) · cycle `CY-20260906-TRADING-TRUTH`
>
> Third and final document in the chain. Read in order:
> [MI-188b](exit-trailing-and-banking-measurement-2026-09-08.md) (the measurement) →
> [MI-209](exit-trailing-banking-broker-truth-2026-09-09.md) (the broker-truth restriction) → this.

**MEASUREMENT ONLY.** No parameter proposed, no exit-matrix cell re-graded, no lever
armed, no live position touched. Every read a GET.

This answers a manager follow-up that arrived mid-flight on MI-209 and raised two
specific concerns. **Both rest on premises that are wrong, and saying so is the
finding** — but the second concern turned out to have a real, narrower version
underneath it, which is settled here.

---

## 1. "The zero-partials claim rests on a truncated window" — NO. It rests on a complete census.

**The concern, verbatim:** *"its soak reads are TAIL-CAPPED and it says so … So the
'zero partials ever banked' claim rests on a truncated window, not the lifetime.
That is precisely the kind of conclusion that flips when the denominator changes."*

**The premise is false. The banking zero was never soak-derived, in either document.**

| probe | source | population |
|---|---|---|
| `notes.partial_closes` non-empty | `trades` | **complete census, n=5589** |
| `notes.partial_closes` key present at all | `trades` | complete census, n=5589 |
| `notes.original_position_size` present | `trades` | complete census, n=5589 |
| `exit_reason` contains `partial` / `tp1` | `trades` | complete census, n=5589 |
| `take_profit_2 > 0` | `trades` | complete census, n=5589 |
| `meta.tp2` present (the ladder precondition) | `order_packages` | **complete census, n=4500** |

Every one is **0**, except `meta.tp2` at **3 of 4500**. `total` was echoed by the API
and rows-fetched asserted equal to it before any of these was computed. **That is the
lifetime of the journal, not a window** — so there is no denominator here that could
change and flip the conclusion.

MI-188b cited a soak tail for exactly one claim, and it is a *different* claim:
`exit_ladder_soak`'s `n_rungs = 0`. Its own §3 says so, and says the `meta.tp2` census
is what makes that airtight. Neither document ever grounded the banking zero in a soak.

## 2. But the tail-cap question is real for one file, and here is how much it bounds

Rather than leave "tail-capped" as an unquantified worry, I measured **what fraction
of each file the 1000-record cap actually covers** — file size from the endpoint's own
`size_bytes`, divided by the mean record size of the tail.

**MEASURED (sizes, read 2026-09-09 via `GET /api/diag/log_file?name=…&lines=1000`) ·
ESTIMATED (lifetime record counts — a size ÷ mean-record-size extrapolation; the tail's
mean record size may not represent the whole file, so treat the coverage figures as
approximate and directional, not exact):**

| soak | size | tail | mean rec | est. lifetime | **tail covers** | tail window |
|---|---:|---:|---:|---:|---:|---|
| `exit_ladder_soak` | 656,242 B | 1000 | 633 B | ~1,036 | **~96.5 %** | 2026-06-22 → 2026-09-09 |
| `exit_lever_soak` | 1,415,768 B | 1000 | 363 B | ~3,897 | **~25.7 %** | 2026-08-31 → 2026-09-09 |

**So the two files are in completely different positions and must not be spoken of as
one "tail cap":**

* **`exit_ladder_soak` is ~96.5 % covered.** Its `n_rungs = 0` reading is within ~36
  records of the lifetime **and** is independently bounded by the complete `meta.tp2`
  census. It is not meaningfully tail-bound, and lifting the cap could not change the
  banking conclusion.
* **`exit_lever_soak` is only ~25.7 % covered** — this is the one where the cap
  genuinely matters. **But it bears on the inverted-`mode` finding
  (`BL-20260908-EXIT-LEVER-SOAK-MODE-IS-A-HARDCODED-LITERAL-AND-READS-AS-A-MODE`),
  not on banking**, which no soak feeds.

**Both soaks are ALIVE, not dead.** Measured against MI-188b's readings 23 hours
earlier: `exit_lever_soak` 1,376,985 → **1,415,768 B**, `exit_ladder_soak` 641,866 →
**656,242 B**. Neither is in the `not_writing` state the soak-alarm rule exists to
catch.

### The cap cannot be lifted from a session, and this is substantiated rather than assumed

⚠️ An impossibility claim gets more scepticism than a success claim, so here is what I
**ran or read**, not what I assumed (`checked: src/web/api/routers/diag.py`,
`checked: scripts/ops/pull_logs.sh`, `checked: .github/workflows/system-actions.yml`):

| path | why it cannot deliver the lifetime |
|---|---|
| `GET /api/diag/log_file` | `n = _clamp(lines, _DEFAULT_LIMIT, _MAX_LIMIT)` with `_MAX_LIMIT = 1000` (`diag.py:1815`, `:876`) — **tail-only, and no `offset` parameter exists** on this route, unlike `audit_query` |
| `vm-diag-snapshot` relay | issues a path-validated `curl` against `/api/diag/*` — same route, same cap |
| `system-actions` → `pull-latest-logs` | `scripts/ops/pull_logs.sh` emits a **fixed** bundle (status.json, heartbeat, journalctl, `signal_audit` tail). Neither soak is in it, and the workflow truncates the bundle to **50 KB** before posting — two orders of magnitude below a 1.4 MB file |
| `trainer-vm-diag` relay | arbitrary bash, but on the **trainer** VM, which holds a synced copy of the journal DB and **not** the live VM's `runtime_logs/` |

**The remedy is a paging parameter on `log_file`, not a cleverer query** — the same
shape as `audit_query`, which already has `limit` + `offset` for exactly this reason.
Filed; **not** built here (this unit is measurement, and that route is Tier-2 surface).

## 3. "The producer is unbuilt" — NO. It is built end to end and has never fired.

**The concern, verbatim:** *"its 'banking half has zero partials because the producer is
unbuilt' is a CAUSAL claim. Check whether the producer is genuinely unbuilt, or merely
never fired — those are different facts and the distinction decides whether banking is a
missing capability or a dormant one."*

**The distinction is the right one to draw, and the answer is DORMANT, not missing.**
The chain is complete at every hop — traced by reading each site, not inferred:

| hop | site | state |
|---|---|---|
| producer | `src/units/strategies/turtle_soup.py:537` — returns `{"action":"close","close_qty_pct":partial_pct,"reason":"tp1_partial"}` | **built** |
| validator | `src/runtime/strategy_verdict.py:142` — accepts `close_qty_pct` in `(0, 1]` | **built** |
| interpreter | `src/runtime/monitor_verdict.py:164` — emits `KIND_PARTIAL_CLOSE` | **built** |
| effectuator | `src/runtime/order_monitor.py:814` → `_apply_partial_close` | **built** |
| venue capability | `src/units/accounts/clients.py:705` — `partial_close` granted to `bybit` | **built** |

So *"unbuilt producer"* is the wrong description and would send the next session to
write code that already exists — the `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` shape.

### What actually holds it shut: FIVE independent gates, each sufficient alone

1. **Only one strategy can emit the verdict.** `close_qty_pct` is produced at exactly
   one site in all of `src/units/strategies/` — `turtle_soup.py`. (The other four hits
   for that string are the validator's own parameter checks.)
2. ⚠️ **`turtle_soup`'s own YAML sets `tp2: None` AND `partial_pct: None`** — measured
   in `config/strategies.yaml` on 2026-09-09. The partial branch is guarded by
   `if not on_tp2_runner and tp2 is not None`, so **it is unreachable from this leg's own
   configuration even if everything else were green.** *This gate is not named in either
   earlier document and is the one closest to the code.*
3. **That strategy is `execution: shadow`** (`enabled: true`, so it evaluates and logs,
   and never places).
4. **It has 3 rows in the entire history** — 2026-05-10, 2026-06-02, 2026-07-01, all
   `bybit_1`: 2 `rejected`, 1 `exchange_rejected`. It has never held a position, so it
   has never reached a TP at all.
5. **The ladder precondition has been met 3 times ever** — `meta.tp2` on 3 of 4500
   packages, all turtle_soup, statuses `orphaned` ×2 / `rejected` ×1.

**The honest one-line answer:** *banking is a built, wired, venue-supported capability
with exactly one producer, and that producer is switched off in three independent ways.*
Whether it SHOULD be reachable is a Tier-3 question and is **not** proposed here.

---

## 4. Which of MI-188b's numbers I could confirm, could not, and which changed

Asked for explicitly. Stated as three separate outcomes, never collapsed.

**CONFIRMED** (re-derived independently on a fresh census, not read off its doc):

| claim | MI-188b (09-08) | MI-209 (09-09) |
|---|---|---|
| `is_backtest` = 0 on every row | yes | yes (0 of 5589) |
| decision population definition + provenance split | 1475, 39.1 % measured | 1502, **38.9 %** measured |
| packages whose stop demonstrably moved | 140 / 2098 (6.7 %) | **146 / 2131 (6.85 %)** |
| moves favourable / adverse | 140 / 0 | **146 / 0** |
| ungradeable packages (*we cannot look*) | 2369 | **2369** |
| partial bank, complete census | 0 | **0** (+ one probe it did not run) |
| `meta.tp2` packages | 3 of 4467 | **3 of 4500** |
| `exit_ladder_soak` `n_rungs = 0` throughout the tail | yes | yes, and now **~96.5 % lifetime-covered** |

**CHANGED** — one, and it is the important one:

* **The trail-vs-TP ordering.** *"The trail ends trades slightly more often than the
  declared TP (33 vs 32)"* reverses to **14 vs 17** once restricted to
  `provenance.is_measured` as the object's `done_condition` requires, and to **12 vs 17**
  after grading where the stop-outs landed. **Neither ordering is established** at that
  n. Full detail in [MI-209 §3](exit-trailing-banking-broker-truth-2026-09-09.md).

**COULD NOT CONFIRM** — stated rather than quietly dropped:

* **`position_telemetry` n = 175.** It reads **193** today. That is a growing table
  (+18 in ~23 h, consistent with ordinary trading), so this is a moving count and not a
  disagreement — but I did not verify the 175 *as of its own read time* and cannot.
* **The lifetime content of either soak.** §2 bounds the coverage; it does not read the
  uncovered records. `exit_lever_soak`'s remaining ~74 % is genuinely unread.
* **MI-188b's `trades` n = 5550 / `order_packages` n = 4467 as of 2026-09-08.** Today's
  5589 / 4500 are consistent with one day of trading, but a past census cannot be
  re-taken.

## 5. What is still NOT established

- The trailing **amend count** (only a lower bound on *packages*, ≥146 since 2026-06-18).
- Whether the trail or the declared TP ends more trades.
- Whether trailing **helps** — no lever-OFF arm exists
  (`BL-20260814-NINE-SHIPPED-LEVERS-NEVER-GRADED-AGAINST-THEIR-OWN-ABSENCE`).
- **~74 % of `exit_lever_soak`'s lifetime**, and therefore whether its inverted-`mode`
  finding holds across the whole file or only the last 9 days.
- Anything before 2026-06-18 (53 % of packages carry no entry-time stop).

## Reproducing

```bash
curl -s "https://ict-bot.duckdns.org/api/bot/db/table/trades?limit=500&offset=N&order_by=id&order_dir=asc"
bash scripts/ops/diag_fetch.sh 'log_file?name=exit_lever_soak&lines=1000'   # size_bytes is in the payload
bash scripts/ops/diag_fetch.sh 'log_file?name=exit_ladder_soak&lines=1000'
```

Coverage = `size_bytes / (tail_bytes / tail_records)`. Page `offset` to `total` and
assert fetched == `total` before calling anything a census.
