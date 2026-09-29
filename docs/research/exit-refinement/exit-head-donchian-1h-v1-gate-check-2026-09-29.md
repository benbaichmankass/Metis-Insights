# exit-head-donchian-1h-v1 — exit-refinement gate-check, 2026-09-29

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Lane: `EXITHEAD-DONCHIAN-GATE`, dispatched by the manager (checklist row,
> session `session_01CPyNx1pUaNYfmvVd4cdZ7y`). Tier-1 — **this is a synthesis
> of already-landed evidence, not a new experiment.** No config, artifact
> stage, or model-registry change is made here; a promotion/flip stays Tier-3
> and out of scope.

## 0. The question, and the answer

**Does `exit-head-donchian-1h-v1` pass its `.claude/skills/exit-refinement/SKILL.md`
gate (E0/E1/E1.5 + live-parity), net of the full cost stack?**

**FAIL — a formal, decisive honest negative, not underpowered.** This is not a
new measurement: every number below already landed (`research/results/
RQ-20260927-001/`, `research/results/RQ-20260927-002/`, and the live-parity
AUC re-sweep recorded in `docs/research/exit-refinement-coverage.json`). What
had not yet happened is applying the pipeline's own pre-registered gate
formula to all three together in one place. Doing that now reverses the
framing this gate-check was dispatched under: the manager's dispatch note
(and ml-review W7, PR #14069, which does not cite `RQ-20260927-002` anywhere
in `comms/reviews/ml-review-20260929-095500.json`) describes "the existing
OOS re-grade" as **"a marginal pass (+1.3127R, n=119)."** That figure
(`RQ-20260927-001`) is real, but it is **superseded** by a later, stronger,
already-landed test (`RQ-20260927-002`) that the marginal-pass framing does
not mention. Per RULE ONE, the framing this gate-check was dispatched under
is not itself verification — checking it against the field (the landed
`research/results/` files) reverses it.

## 1. The gate, verbatim

`docs/research/M20-exit-head-PROGRAM.md` lines 72–75, **GATE E1→E2 (per
family)**:

> OOS AUC materially > 0.55 AND the τ-policy beats the best hard rule on
> net_R AND maxDD in the walk-forward, AND the live-trade validation set
> agrees in sign. Anything else = honest negative, the hard rules stand.

Three independent sub-conditions, all required. All three are already
measured for this artifact/leg. All three fail.

## 2. Sub-condition 1 — OOS AUC materially > 0.55: FAIL

`docs/research/exit-refinement-coverage.json`, `trend_donchian`/`BTCUSDT`/`1h`
row, `exit_head_ml.status = "shipped_gate_failed"`, live-parity re-sweep
2026-08-14 (trainer relay #9206, `tp_geometry: live_parity`,
`tp_cap_pct=0.099`, 23 usable folds): **`n_oos=311`, `auc=0.5403`,
`beats_actual=14/23`, `beats_hard=17/23`.** `train_exit_head.py:349-352`'s own
gate requires `mean_auc>0.55` AND `beats_actual*3>=u*2` (≥16/23) AND
`beats_hard*3>=u*2` (≥16/23) — **two of three fail** (AUC below the bar,
`beats_actual` two short); only `beats_hard` clears. This is the SAME
artifact/leg re-measured on the geometry it actually runs in production
(the earlier E1 pass that shipped it, 2026-07-12 #6211/#6216/#6217, was
measured on a no-take-profit book — a different geometry from what production
places, per the coverage row's own caveat). The coverage row itself already
records this as `shipped_gate_failed` (not `shipped`) precisely because the
gate does not reproduce at live parity.

## 3. Sub-condition 2 — τ-policy beats the best hard rule on net_R AND maxDD
   in the walk-forward: FAIL

This is the sub-condition the manager's dispatch note under-cited. Two
walk-forward units exist for this exact artifact/leg (`trend_donchian`
BTCUSDT 1h), both net of the full cost stack **by construction** (identical
per-trade `fee_r` computed on both the head-on and head-off arm via
`src.runtime.execution_costs`, same convention both units share):

### 3a. `RQ-20260927-001` — single embargoed split

`research/results/RQ-20260927-001/36298075573.jsonl`. Walk-forward retrain
(embargoed split `2025-01-06T16:00:00Z`, 7-day embargo, trained on 1,158 of
1,662 original trades), replayed on **n=119** genuinely out-of-sample
BTCUSDT trades (clears its own pre-registered power floor, n≥64.07).
`recovered_R_oos = +1.3127` (**+0.011R/trade**). PASS under
`RULE-RQ0927-001-EXIT-HEAD-OOS-ATTRIBUTION` — but the unit's own registered
caveat states plainly: *"Single walk-forward split (one train/test boundary),
not multiple folds — the pre-registered rule is a sign test at one split, not
a robustness-across-splits claim."* This is the figure cited in this gate
check's dispatch note as "the existing OOS re-grade."

### 3b. `RQ-20260927-002` — 3-fold embargoed walk-forward (the robustness test `-001` itself called for)

`research/results/RQ-20260927-002/36302273043.jsonl`. Same retrain recipe as
`-001`, re-run at **3** independent, sequential, embargoed train cutoffs
(7-day embargo each) instead of one, per the operator's explicit 2026-09-27
wave-6 ask for a multi-split walk-forward (`MANAGER-CHECKLIST.json` row R4).
Pre-registered rule `RULE-RQ0927-002-EXIT-HEAD-OOS-MULTIFOLD` (registered
**before** the run): PASS requires ≥2 of 3 folds to clear the n≥64 power
floor AND pooled `mean_recovered_R_per_trade > 0` AND ≥2 of the establishable
folds individually positive; otherwise FAIL.

**Landed result:** all 3 folds cleared the floor (n=347 each, **n=1041
pooled** — over 16× the pre-registered minimum). **Pooled
`recovered_R_oos = -14.6597`** (mean **-0.01408R/trade**), and only **1 of 3**
folds individually positive:

| fold | test_start | recovered_R_oos |
|---|---|---|
| 1 | 2022-10-29 | -13.2047 |
| 2 | 2023-12-20 | -20.6382 |
| 3 | 2025-03-26 | **+19.1832** |

**Both FAIL branches of the pre-registered rule are independently met**
(pooled mean ≤ 0, AND fewer than 2/3 folds positive). Fold 3's test window
(2025-03-26 onward) contains `-001`'s single split (2025-01-06 onward) — the
one favourable split `-001` happened to draw is fold 3, the only positive
fold of three. `-001`'s own stated caveat ("not a robustness-across-splits
claim") is now directly confirmed: the favourable split was not
representative of the other two, earlier folds. `attributable_to: strategy`
— **the head does not generalize across splits.**

**This REVERSES `-001`'s single-split PASS as promotion evidence.** Per
`PI-20260926-96NDZSOD-0002`'s own terminal note (a separate, earlier-caught
grading bug in the original `RQ-20260922-007` unit, already resolved): *"cite
`RQ-20260927-001` instead [of `RQ-20260922-007`]"* — that guidance is now
itself superseded one step further: **cite `RQ-20260927-002` for "does this
generalize," which is exactly the question a promotion decision needs
answered**, per `RQ-20260927-002`'s own `research/queue/RQ-20260927-002.yaml`
`related:` field (*"the single-split unit this one extends to multiple
folds"*) and its explicit non-superseding note that `-001`'s figure "stands
as its own (weaker) data point," not the one to promote on.

**maxDD** is not separately reported in either walk-forward unit's landed
JSONL (both report `recovered_R_oos` / per-fold R deltas only, not a maxDD
series) — this gate-check does not fabricate a maxDD comparison; the net_R
half of sub-condition 2 alone is sufficient to fail it, and a missing maxDD
number is not read as a pass.

## 4. Sub-condition 3 — live-trade validation set agrees in sign: FAIL / no support

`comms/reviews/w5n-exit-head-reconcile-20260926.json` (MEASURED, trades
table, all 6202 rows, 2026-09-26T21:19Z): **11 exit_head closes ever** on
this artifact/leg — 5 on `bybit_2` (2026-07-12..08-25, **sum -1.13 USD**), 0
on `bybit_portfolio` (the twin-dedup bug, see §5), 6 on `bybit_1`. **None
after 2026-09-15.** The only live-trade record that exists is small and
negative on the real-money account, not positive. There is no live-trade
evidence agreeing in sign with a promotion case.

## 5. Precondition already cleared (moot given §§2–4, stated for completeness)

`PI-20260926-96NDZSOD-0001` (twin-dedup mirror divergence, the ONE precondition
every prior pipeline record named as independently blocking re-promotion,
Tier-2): fix merged (PR #13076, `4a8b29e2c`). **Independently re-verified this
session** by reading `src/runtime/exit_head_shadow.py` on `origin/main`
(`dbb1b7b19`) directly: `_SEEN: dict = {}` (line 100) with `cached =
_SEEN.get(seen_key)` (line 497) and `_SEEN[seen_key] = record` (line 552) —
the dict-cache fix (not the old set-based "mark seen, return None on repeat")
is present on `main` today, not merely claimed. ml-review W7 (PR #14069,
`comms/reviews/ml-review-20260929-095500.json`, not yet merged as of this
writing) additionally reports a live-behavior observation (per-bar cached
scores held constant across the same closed bar) confirming the fix is also
deployed and running, not just merged — this gate-check did not re-run that
live observation itself and defers to W7's own record for the
deployed/observed claim specifically. **This does not change the verdict**:
even with the precondition cleared, §§2–4 fail the gate on their own,
independent of the dedup fix.

## 6. Live parity — which legs would consume the head, and would it be inert

`config/strategies.yaml` declares `exit_head_model: exit-head-donchian-1h-v1`
on **three** legs (grep-verified, lines 220/1176/1255): `trend_donchian`
(BTCUSDT 1h → `bybit_2` real money + `bybit_portfolio` mirror, Stage 2),
`trend_donchian_sol` (SOLUSDT → operator-supervised prop manual bridge),
`trend_donchian_eth` (ETHUSDT → same prop bridge). All three share **one**
`model_id`, so a stage flip is not scoped to a single leg — promoting the
artifact arms all three at once.

`src/runtime/exit_head_apply.py::exit_head_verdict()` gates on a single
condition (line 94): `rec.get("stage") == "advisory"`. The mirrored artifact
(`runtime_logs/trainer_mirror/exit_head/exit-head-donchian-1h-v1.json`) is
**`stage=shadow`** — confirmed byte-identical trainer/live-VM
(`trainer-vm-diag #13023`, sha256 match, `mtime 2026-09-24T22:32:51Z`, per
`PI-20260926-6M9R1WSA-0001`, unchanged since; ml-review W7 reports no
promotion/demotion action this cycle). **The head is inert on all three legs
today** — it scores and logs (`shadow_predictions.jsonl`) but never closes a
real position. It would stop being inert, on all three legs simultaneously
including real-money `bybit_2`/`bybit_portfolio`, the moment any session
flips `stage` to `advisory` (an artifact-JSON edit + publish, per
`export_exit_head.py`'s own docstring — there is no registry entry for this
model, so `ml/cli.py promote-stage` does not apply here). **Given the FAIL
verdict above, this gate-check does not recommend that flip.**

## 7. What this changes and does not change

- **No config, artifact-stage, or model-registry change.** The artifact
  stays `shadow`, exactly as it is today; nothing here alters live behavior
  on any leg or account.
- **Updates `docs/research/exit-refinement-coverage.json`'s
  `trend_donchian`/`BTCUSDT`/`1h` row** (`exit_head_ml` field) to append this
  gate-check's synthesis and the `RQ-20260927-002` corroboration, per the
  skill's own binding rule ("a sweep or training run whose verdict isn't in
  the matrix didn't happen") — the matrix's `status` was already
  `shipped_gate_failed` from the 2026-08-14 AUC re-sweep; this gate-check
  adds the independent, later, recovered_R-based confirmation of the same
  disposition rather than changing the status value.
- **Files `PI-20260929-HIANGATE-0001`** recording this formal gate verdict as
  a durable, re-checkable record (see the pipeline JSON for `due_when`).
- The SOL/ETH legs' own `exit_head_ml` coverage rows (`shipped_gate_failed`
  and `shipped` respectively) are **not** re-measured here — this gate-check's
  new evidence (`RQ-20260927-001`/`-002`) is BTCUSDT-specific by construction
  (both units retrain and replay only `trend_donchian`'s own
  `datasets-out/exit_head/1h/donchian/rows.jsonl` population). Because
  promotion is per-artifact (§6), the BTCUSDT FAIL is sufficient on its own
  to withhold promotion for all three legs; it is not evidence about SOL/ETH
  specifically.

## 8. Answer to the manager, in the requested shape

- **FAIL** (not underpowered — `n=1041` pooled OOS trades against a
  pre-registered floor of 264; the AUC re-sweep's `n_oos=311` also clears its
  own gate's implicit minimum).
- **n / folds / net R:** `RQ-20260927-002`, 3 folds (n=347 each, 1041
  pooled), **pooled `recovered_R_oos = -14.6597`** (mean -0.01408R/trade),
  1/3 folds positive — reverses `RQ-20260927-001`'s single-split
  `+1.3127R` (n=119), which mapped onto the one positive fold. Both net of
  the full cost stack by construction.
- **PR:** see this document's landing PR (Tier-1, evidence + `research/**`
  only, self-landed).
- **The exact Tier-3 change a PASS would have supported:** flip
  `runtime_logs/trainer_mirror/exit_head/exit-head-donchian-1h-v1.json`'s
  `stage` field from `shadow` to `advisory` (artifact-JSON edit + republish,
  not `ml promote-stage` — no registry entry exists for this model) — which
  would arm the head to close real positions on `trend_donchian` (`bybit_2` +
  `bybit_portfolio` mirror) and the two prop-bridge legs simultaneously.
  **Not recommended on this evidence.**
