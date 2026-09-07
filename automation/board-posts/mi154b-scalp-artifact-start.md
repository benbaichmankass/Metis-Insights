▶️ **START** — MI-154b · session `session_01GNkN16mQBnSVNRLweSXNWP` (sub-session of manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`)

**Branch:** `claude/mi154b-scalp-artifact-20260907`
**Work object:** `WO-20260906-NO-5M-OR-15M-SCALP-EXIT-HEAD` · checklist item `MI-154-SCALP-EXIT-HEAD-ARTIFACT`

**Scope I am touching:**
- READ: `/api/diag/shadow_stats` (live, read-only), trainer VM via the push-triggered `trainer-diag-relay`
- WRITE (Tier-1, trainer-side): train + export a 5m/15m `ict_scalp` exit-head artifact via the EXISTING `scripts/ml/export_exit_head.py`; docs under `docs/research/`; `.github/pr-landing/` + `.github/pr-automerge-requests/`

**NOT touching:** `#11140` (manager-held, Tier-3), cell `status` in `docs/research/exit-refinement-coverage.json` (Tier-3), `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml`, the order path, `ICT_SCALP_EXIT_HEAD_MODE`. No promotion past `shadow`. No live-VM SSH.

⚠️ **Heads-up for the manager, verified independently this session (not taken from my predecessor's doc):**
The briefed done-condition — *artifact visible on `shadow_stats` AND a leg admitted with `decision_state != not_scored`* — is **structurally unreachable while #11140 is unmerged**:
- `decision_state` appears in **0 `.py` files** on main (positive control: `EXIT_LOOP_DECOUPLE_DISABLED` → 4 files, so the probe works).
- The only production call site of `maybe_score_exit_head` is `src/units/strategies/trend_donchian.py:802`. The `ict_scalp` unit has none.
- `/api/diag/shadow_stats` → `shadow.py::stats` aggregates `shadow_predictions.jsonl` via `iter_records(log)` — it enumerates models that have **SCORED**, not artifacts that exist.

So no artifact I publish can appear on that feed until the consumer lands. I am proceeding with the half that **is** unblocked (train + export + publish to shadow), and will report the observation gap plainly rather than reporting the lane done.
