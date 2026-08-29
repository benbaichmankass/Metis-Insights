# Correction: the offload model IS at shadow. I reported otherwise.

**2026-08-29 · Tier-1 read.** Corrects a statement I made to the operator earlier
in this session.

## What I said, and why it was wrong

I ran `promote-stage` on `btc-regime-5m-lgbm-flow-v1-offload`, it exited 0 and
printed `to_stage: shadow`, and I then read the registry back and reported the
promotion as **not taken** — because the fields I read said `candidate`.

I read the wrong fields. `ModelRegistry.promote_stage` (`ml/registry/model_registry.py`)
writes `target_deployment_stage` and constructs the updated entry with
`status=current.status` — **status is passed through untouched, by design**. So
`status: candidate` after a stage promotion is the *correct* post-state, not a
failure. Every live consumer reads `target_deployment_stage`:
`strategy_signal_builders` · `coordinator` · `regime_bar_scoring` ·
`ml_vol_verdict` · `advisory_sizing`. Field beats comment — and in this case,
field beats my own diagnostic.

## The live read — trainer-diag #10406

**Population: all 96 registry entries, via `ModelRegistry.list()`.**

```
registry entries: 96
  advisory     3
  candidate   64
  shadow      29
```

**Positive control (the reason this read is worth anything):** the same probe
prints three different values, so `shadow` is a measurement and not a constant.

| control | target_deployment_stage | status | stage_history |
|---|---|---|---|
| `btc-c1-base-v903` | `candidate` | `candidate` | 0 |
| `btc-regime-15m-baseline-v1` | `shadow` | `candidate` | 0 |
| `btc-regime-15m-lgbm-fc-pcv-v2` | **`advisory`** | `candidate` | 1 |

**The offload model:**

```
btc-regime-5m-lgbm-flow-v1-offload
  target_deployment_stage = 'shadow'      <- what consumers read
  status                  = 'candidate'   <- registration status, NOT stage
  len(stage_history)      = 1             <- 0 at registration; 1 = one promotion
```

`stage_history` is the independent confirmation: PR #10390 recorded it **empty**
at registration, so a length of 1 is the promotion event itself. And
`promote_stage` *raises* on a no-op transition, so an exit-0 run printing
`to_stage: shadow` could not have been a model already at shadow.

**It is one of the 29 models a strategy omitting `shadow_model_ids` auto-wires.**
That is the state the operator directed — *"the whole point of shadow is to test
and refine the candidates, leaving it as a candidate without giving it decisions
to make is pointless"* — and it was an explicit Tier-2 OK, not plumbing.

⚠️ **It is at `shadow`, which LOGS predictions and changes no order decision.**
The `shadow → advisory` gate is untouched and remains a separate operator call.

## The trap that caused it is ALREADY FILED — twice

`status` reads `candidate` on **all three** control rows, including the
`advisory` head that influences real-money routing. So the field carries no
stage information anywhere in the fleet, and reading it for stage is wrong for
every model, not just this one.

That is exactly:

BL-20260821-REGISTRY-STATUS-SAYS-CANDIDATE-FOR-EVERY-MODEL

BL-20260823-REGISTRY-STATUS-AXIS-IS-VESTIGIAL

Both **open**. `backlog_search.py` returned them at 0.71 / 0.57 overlap. **No
third row is filed** — this is a duplicate of a known class, and the rule is
that a duplicate gets dropped and the existing row strengthened. What this
session adds as evidence is that the vestigial field has now **caused a
concrete misreport**, not merely sat unread: it is no longer only untidy, it is
actively misleading a reader who has every reason to trust a field named
`status`. There is no supported update path in `backlog_append.py` (append
only), so the evidence is recorded here and the row ids are named above so a
review session can find it.

⚠️ **Denominator note on that claim:** I bucketed the fleet by
`target_deployment_stage`, not by `status`, so I observed `status` on **3 of 96**
rows. All three read `candidate`. The two backlog rows above assert the
fleet-wide version (all 95/96) and I did not independently re-measure it — I am
corroborating them, not re-proving them.
