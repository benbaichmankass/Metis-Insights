# MI-126 — an absent leg is NOT_OBSERVED, not a measured zero

Object: `WO-20260905-SUNSET-PASS-MANUFACTURES-A-ZERO-FROM-AN-ABSENCE` ⚠️ **which does
not exist on `main`** — the contract path named in this unit's dispatch is absent from
the repo. Registry row: `pending-20260905T020449Z` in `docs/claude/work/SESSIONS.json`.

Implements the repair `MI-124-never-firing-legs-diagnosis.md` (371ceaa6, #11019) points
at. **This document is the PR description** — `update_pull_request` 403s from this
session, so PR #11020 carries a boilerplate body (§7).

⚠️ **NOTHING HERE RETIRES, DISABLES OR SHADOWS ANY LEG.** The operator has answered
twice that nothing is to be retired. This is a measurement repair, and its effect is to
REMOVE eight retirement proposals.

## 1. The defect

`scripts/ops/sunset_pass.py:288` read:

```python
life = lifetime.get(name, 0 if lifetime_state == "read" else None)
```

defended by a comment claiming:

> `/api/bot/performance` lists every strategy with any closed trade, so under `read` an
> absent leg genuinely closed ZERO — a real measurement.

**That claim is false.** `src/web/api/routers/performance.py:324-326` carries
`AND t.pnl IS NOT NULL`, so the capture lists every strategy with a **pnl-bearing**
close, not with *any* close. A leg whose every close landed `pnl NULL` is simply absent:

```
every close has pnl NULL  ->  absent from /api/bot/performance
                          ->  lifetime.get(name, 0) yields 0
                          ->  basis "never_closed_lifetime"
                          ->  verdict retire_candidate
                          ->  note "has never closed a single trade in its life"
```

The comment was the load-bearing part. It is what persuaded a reader the default was an
observation, so it is corrected in the same change rather than left standing — in the
inline comment **and** in the module docstring's "THE LIFETIME READ IS THREE-STATE"
section, which carried the identical false claim.

## 2. The change

`lifetime_state` describes THE CAPTURE and was being used to describe an individual LEG.
Each leg now carries its own `leg_lifetime_state`:

| state | meaning |
|---|---|
| `observed` | the leg is IN a `read` capture; its count is real |
| `not_observed` | the capture was read and this leg is NOT in it — **we did not observe a pnl-bearing close**, which is NOT "it never closed a trade" |
| `not_read` / `unreadable` | we did not look at all |

`life` is `lifetime.get(name)` — `None` whenever the leg was not measured, never a
manufactured `0`. The state rides through to the verdict, into `evidence.leg_lifetime_state`,
into the basis `lifetime_not_observed`, and into `population.legs_lifetime_not_observed`.

**The branch is narrowed, not disabled.** A leg present in the capture reading zero is
still a `retire_candidate` on `never_closed_lifetime` — asserted as a positive control in
the test, so the fix cannot degenerate into switching the basis off.

**An absent leg still falls through to `persistently_silent`**, deliberately. That basis
rests on the M7 packet's own `n_closed`, and `scripts/ml/strategy_review_packet.py:577`
increments it regardless of whether `pnl` is present — so it is a genuinely independent
measurement, not the same absence in disguise. Suppressing it too would have been an
over-correction. (It cannot fire today regardless: `MIN_PASSES_FOR_INDEX_BASIS` is 3 and
two packet dates exist.)

## 3. Before / after — population stated

Both runs against the live `GET /api/bot/performance?window=all`, read 2026-09-05, over
the same two committed packet dates (`2026-09-01`, `2026-09-02`).

**Population: 52 enabled strategy legs graded · 46 present in the capture · 11 absent.**

| verdict | before | after |
|---|---|---|
| `retire_candidate` | **9** | **1** |
| `watch` | 40 | 48 |
| `not_assessed` | 3 | 3 |

The eight that stopped being candidates are exactly the absent ones — `gdx_pullback_1d`,
`gld_pullback_1d`, `iaum_pullback_1d`, `mes_trend_long_1d`, `scha_trend_long_1d`,
`splg_trend_long_1d`, `spy_trend_long_1d`, `tqqq_trend_long_1d` — every one previously
`never_closed_lifetime` on a manufactured `life=0`, now `watch` / `lifetime_not_observed`
with `life=None`.

**What happens to all eleven absent legs:**

| leg | after | basis |
|---|---|---|
| `gdx_pullback_1d` · `gld_pullback_1d` · `iaum_pullback_1d` · `mes_trend_long_1d` · `scha_trend_long_1d` · `splg_trend_long_1d` · `spy_trend_long_1d` · `tqqq_trend_long_1d` | `watch` | `lifetime_not_observed` |
| `trend_donchian_eth_prop` · `trend_donchian_sol_prop` | `not_assessed` | `prop_routed` (an earlier branch, unchanged) |
| `turtle_soup` | **still `retire_candidate`** | `unrouted` |

`turtle_soup` staying a candidate is correct and deliberate: `unrouted` is
absence-independent evidence — the leg is declared in `strategies.yaml` and routed to no
account in `accounts.yaml` — so that verdict never rested on the capture. (MI-124 §3
separately notes it is also `execution: shadow`; that is a different finding, already
filed, and not addressed here.)

## 4. Is `/api/bot/performance` the right source at all?

**No — not for this question.** Measured directly against `trade_journal.db::trades` via
`GET /api/bot/db/table/trades?filter_col=strategy_name&filter_op=eq`, **asserting
`filter_state: "applied"` on every response** before trusting a count (an unknown column
silently returns the whole-table count), lifetime, non-backtest, paged at the route's
500-row cap:

| strategy | closed | closed w/ pnl | closed w/ `pnl NULL` |
|---|---|---|---|
| `iwm_trend_long_1d` **(control)** | 7 | 4 | 3 |
| `qqq_trend_long_1d` **(control)** | 2 | 2 | 0 |
| `trend_donchian_sol` **(control)** | 1 | 1 | 0 |
| `gdx_pullback_1d` | **2** | 0 | 2 |
| `gld_pullback_1d` | **3** | 0 | 3 |
| `iaum_pullback_1d` | **1** | 0 | 1 |
| `scha_trend_long_1d` | **1** | 0 | 1 |
| `spy_trend_long_1d` | **1** | 0 | 1 |
| `mes_trend_long_1d` | 0 | 0 | 0 |
| `splg_trend_long_1d` | 0 | 0 | 0 |
| `tqqq_trend_long_1d` | 0 | 0 | 0 |
| `turtle_soup` | 0 | 0 | 0 |

**The positive controls passed** — all three non-zero on both counts — so the zeros in
this table are a measurement and not a silent query. The first probe attempt FAILED its
control (the route caps `limit` at 500 and returned HTTP 422, which the JSON parse turned
into empty rows); the control caught it, which is the entire reason it is there.

**Five legs have real closed trades and zero pnl-bearing ones.** The packet's
*"has never closed a single trade in its life"* was therefore false for five of the ten
it proposed retiring — MI-124's count, independently reproduced here.

`iwm_trend_long_1d` is the clincher, and it is a leg the packet PASSED as `watch`: **7
closes, 4 with pnl, 3 with `pnl NULL`.** It is visible to the capture only because some
of its closes happened to get pnl stamped. It differs from the five in luck, not in kind.

**Conclusion.** The `pnl IS NOT NULL` filter is *correct for a performance view* — an
unpriced close has no PnL to average, and stripping it would corrupt the route's own
consumers. It is wrong as an *"ever closed"* oracle. **The route is not changed; the
caller stops treating it as one.** A direct `trade_journal.db::trades` count is the
honest source for "did this leg ever close", and the `lifetime_not_observed` note now
says so at the point a reader needs it. Wiring that count in is a larger change than this
unit and is deliberately not attempted.

⚠️ Note the four legs with genuinely zero closes. For them the packet's literal claim was
true — but the pass had no basis in the capture for knowing it, which is the point. Being
accidentally right is not a measurement.

## 5. The test, and its mutation evidence

`test_sunset_pass_never_proposes_a_leg_absent_from_the_lifetime_capture` grades two legs,
`absent` and `measured_zero`, **identical in routing and in gate history, differing only
in whether the capture carries them** — so the assertion cannot pass by accident. It
requires the absent leg to be no candidate, to carry `not_observed`, and to keep
`lifetime_closed_trades is None`; and, as a positive control, the present-and-zero leg to
still be a `retire_candidate`.

**Verified by mutation, not by being green once.** Restoring the exact pre-fix lines:

```python
life = lifetime.get(name, 0 if lifetime_state == "read" else None)
elif lifetime_state == "read" and (life or 0) == 0:
```

```
E  AssertionError: a leg ABSENT from the capture was proposed for retirement on that
E  absence; got verdict=retire_candidate basis=never_closed_lifetime
FAILED tests/test_phase_g_sunset_and_pull.py::test_self_tests_pass[scripts/ops/sunset_pass.py]
FAILED tests/test_phase_g_sunset_and_pull.py::test_sunset_pass_never_proposes_a_leg_absent_from_the_lifetime_capture
2 failed, 11 passed
```

The fix was then restored and the working tree verified byte-identical to `HEAD`.

The in-file `--self-test` gained the same two checks (12/12). ⚠️ Its pre-existing check
*"a leg with zero lifetime closes is a retire_candidate"* **encoded the defect as correct
behaviour** — its `silent` leg was absent from the `lifetime` map — so it was corrected to
place `silent` IN the capture at 0, with the absent case added beside it. A test asserting
the bug is why the bug survived review.

## 6. Guards

`python3 scripts/ci/run_guards.py --base main` → **PASS 64 · FAIL 0 · SKIP 23**.

⚠️ `layer-guard` initially exited 127 — `lint-imports` was absent from this container, not
a finding about the diff. It passes once `pip install import-linter` provides the binary.
Named rather than reported green. Every other relevant guard ran.

## 7. Landing note — why PR #11020's own description is boilerplate

The same mechanism MI-124 §9 recorded, reproduced exactly. From this session
`add_issue_comment`, `create_pull_request` and `update_pull_request` all returned
`403 Resource not accessible by integration`, while `issue_read` / `list_pull_requests`
succeeded — the write-scope boundary, not the transient MCP drop, so backoff does not
clear it. `claude-pr-automerge.yml` fires on any push to a `claude/**` branch touching
`.github/pr-automerge-requests/`, and the Tier-1 protocol requires that file, so pushing
the mandated landing pair opened **#11020** with `title = head-commit subject` and a
two-line body before anything could supply the real ones — and `update_pull_request` then
403s, so it cannot be corrected in place.

Already filed as
`BL-20260905-AUTOMERGE-RELAY-WINS-THE-RACE-WITH-PR-OPENER-SO-EVERY-TIER-1-PR-GETS-A-BOILERPLATE-BODY`.
**Not re-filed** — this is that row recurring, and this document is the workaround it
predicts.

Per MI-124 §9b, the board-post relay's artifacts under `automation/` sit OUTSIDE
`TIER1_SURFACE` and would make `check_pr_landing.py` refuse `landing: "self"`; they are
branch-only and are `git rm`'d once consumed, which removes them from the net three-dot
diff. The removal commit is an ordinary one, which also arms CI — a `github-actions[bot]`
commit fires no workflows and would leave the PR showing zero checks.

## 8. Out of scope, deliberately

The pnl-stamping defects MI-124 also found are real and are **why** these closes are
pnl-null, but they are a different unit and folding them in would make this change
unreviewable:

- alpaca reconciler closes leaving `pnl NULL` on gdx/gld/iaum/scha/spy —
  `BL-20260807-BULK-RECONCILER-CLOSE-NO-EXIT-NO-PNL`,
  `BL-20260825-EXIT-PROVENANCE-IS-STRUCTURED-BY-EXIT-PATH-SIX-PATHS-AT-ZERO`
- trade 4350 + 7 orphaned packages on `mes_trend_long_1d`, which has held a position 33
  days and therefore cannot re-enter — `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`

Also untouched: `/api/bot/performance`'s SQL, `config/strategies.yaml`,
`config/accounts.yaml`, `config/risk_caps.yaml`, every order path, and
`docs/claude/SUNSET-DISPOSITIONS.json`.
