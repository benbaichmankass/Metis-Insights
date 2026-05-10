# Env-gate purge Phase-2 — survivor annotations

> **Status:** Tier 2. Operator-acked 2026-05-10.
>
> **Why this doc:** Phase-2 of S-067 follow-up #4 needs inline
> ``# allow-silent: <reason>`` annotations on the two surviving
> ``os.environ.get`` reads under ``src/runtime/`` (PR #659 audit:
> ``docs/audits/env-gate-purge-2026-05-10.md``). The substantive
> deliverable — per-survivor static-AST regression tests asserting
> the gates do NOT bypass ``RiskManager.evaluate`` — ships in
> ``tests/test_env_gate_survivors_no_risk_bypass.py`` (this PR).
>
> The two-line in-place edits below couldn't be pushed via the
> autonomous session's MCP `create_or_update_file` round-trip
> (the live-order-path files are too large for a single push at
> ~50-100 KB and the sandbox has no local git auth). An operator
> with local clone access should apply both patches before merging
> this PR — or land the patches as a follow-up commit on the same
> branch.

## Apply patch 1 — `src/runtime/pipeline.py`

Locate `_multi_account_dispatch_enabled` (≈ line 192-195 today):

```python
    raw = settings.get("MULTI_ACCOUNT_DISPATCH") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("MULTI_ACCOUNT_DISPATCH", "true")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}
```

Add the `# allow-silent: <reason>` annotation on the env-read line:

```diff
     raw = settings.get("MULTI_ACCOUNT_DISPATCH") if isinstance(settings, dict) else None
     if raw is None:
-        raw = os.environ.get("MULTI_ACCOUNT_DISPATCH", "true")
+        raw = os.environ.get("MULTI_ACCOUNT_DISPATCH", "true")  # allow-silent: S-067 fu B — not a live/dry escape hatch; selects multi-account fan-out vs. single-client legacy path. Both branches pass through RiskManager.evaluate (per-account dry_run is the canonical live/dry switch). See docs/audits/env-gate-purge-2026-05-10.md § survivors.
     return str(raw).strip().lower() in {"true", "1", "yes", "on"}
```

## Apply patch 2 — `src/runtime/order_monitor.py`

Locate `_reconcile_enabled` (≈ line 676-682 today):

```python
def _reconcile_enabled() -> bool:
    """Read ``MONITOR_RECONCILE_ENABLED`` at call time so an operator
    flag flip takes effect within the next tick without restarting
    the trader. Default ``false`` for PR 2; PR 3 flips it on."""
    raw = os.environ.get("MONITOR_RECONCILE_ENABLED", "false")
    return str(raw).strip().lower() == "true"
```

Add the annotation on the env-read line:

```diff
 def _reconcile_enabled() -> bool:
     """Read ``MONITOR_RECONCILE_ENABLED`` at call time so an operator
     flag flip takes effect within the next tick without restarting
     the trader. Default ``false`` for PR 2; PR 3 flips it on."""
-    raw = os.environ.get("MONITOR_RECONCILE_ENABLED", "false")
+    raw = os.environ.get("MONITOR_RECONCILE_ENABLED", "false")  # allow-silent: S-067 fu B — not a live/dry escape hatch; gates the orphan-reconciler / package-watchdog cleanup helpers (BUG-042, S-055, S-060), not new-trade evaluation. RiskManager.evaluate runs in pipeline.py regardless of this flag. See docs/audits/env-gate-purge-2026-05-10.md § survivors.
     return str(raw).strip().lower() == "true"
```

## Why annotations on existing lines

The lint guard ``scripts/check_env_gate_in_diff.py`` (PR #659) is
**diff-based** — it only flags ADDED env-gate lines under
protected paths. The two existing call sites are grandfathered;
the annotations don't currently affect CI. But they pre-empt the
trap: if a future PR modifies either line for any reason (rename,
default change, etc.), the diff-scan would re-introduce the line
as "new" and the lint guard would fire. The annotation pre-arms
the override so the next refactor doesn't trip.

This matches the same shape as the silent-empty-guard's
``# allow-silent: <reason>`` mechanism — operators don't have to
learn two override syntaxes (PR #659 deliberately reused the
silent-empty syntax for that reason).

## Verification after applying

```bash
# 1. Lint stays clean — no new env-gate-guard hits.
ruff check src/runtime/pipeline.py src/runtime/order_monitor.py

# 2. The new survivor tests pass (they don't depend on the
# annotations; they're static-AST contract pins).
pytest tests/test_env_gate_survivors_no_risk_bypass.py -q

# 3. The "no NEW survivors" cross-cutting test in the same file
# already grandfathers these two paths, so the annotations are
# additive — they don't introduce a false positive.
```

## Cross-references

* `docs/audits/env-gate-purge-2026-05-10.md` — Phase-1 audit
  (PR #659) that identified the two survivors.
* `scripts/check_env_gate_in_diff.py` — Phase-1 lint guard (PR #659).
* `tests/test_env_gate_survivors_no_risk_bypass.py` — Phase-2
  regression tests (this PR).
* `docs/claude/checkpoints/CP-2026-05-10-04-s067-phase2-followups.md` —
  session ledger that filed this PR.
