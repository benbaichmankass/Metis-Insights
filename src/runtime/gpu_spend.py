"""GPU-burst spend ledger — the committed source of truth for M19 Tier-1 spend.

The spot-GPU burst tier (`docs/research/T1-gpu-burst-spend-SPEC.md`) trains models on
a rented spot GPU in short bursts. Every run's cost is recorded in a **committed**
JSON ledger (`comms/gpu_spend_ledger.json`) so:

1. the burst workflow reads **month-to-date** spend here as its **hard gate** — it
   refuses to launch if `month_to_date + est_run_cost > budget_usd_per_month`;
2. it appends one entry per run **after teardown** (actual GPU-hours × rate);
3. the dashboard surfaces per-session cost + the running monthly total vs the cap
   (via `GET /api/bot/gpu/spend`), so the operator can see exactly what each training
   session costs.

A **file** (not a DB table) because the Actions burst workflow writes it via a git
commit — same channel as `comms/reports/`. Stdlib-only, best-effort read: a
missing/garbled ledger degrades to an empty summary, never raises to the API.

Cost is authoritative-by-record: `cost_usd` is whatever the workflow computed at
teardown (actual billed GPU-hours × the pod's rate); `gpu_hours`/`rate_usd_per_hour`
are carried for display + auditing. The month bucket is the UTC `YYYY-MM` of
`ended_at` (fallback `started_at`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.utils.paths import repo_root

LEDGER_ENV = "GPU_SPEND_LEDGER"
_DEFAULT_BUDGET_USD = 10.0


def ledger_path() -> Path:
    """Resolve the ledger file: ``$GPU_SPEND_LEDGER`` → ``<repo>/comms/gpu_spend_ledger.json``."""
    env = os.environ.get(LEDGER_ENV)
    if env:
        return Path(env)
    return Path(repo_root()) / "comms" / "gpu_spend_ledger.json"


def load_ledger(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """The raw ledger dict. Missing/garbled → a default empty ledger (never raises)."""
    p = Path(path) if path is not None else ledger_path()
    if not p.is_file():
        return {"budget_usd_per_month": _DEFAULT_BUDGET_USD, "provider": None, "runs": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"budget_usd_per_month": _DEFAULT_BUDGET_USD, "provider": None, "runs": []}
    if not isinstance(data, dict):
        return {"budget_usd_per_month": _DEFAULT_BUDGET_USD, "provider": None, "runs": []}
    data.setdefault("runs", [])
    data.setdefault("budget_usd_per_month", _DEFAULT_BUDGET_USD)
    return data


def _month_of(run: dict[str, Any]) -> str | None:
    ts = run.get("ended_at") or run.get("started_at")
    if not isinstance(ts, str) or len(ts) < 7:
        return None
    return ts[:7]  # UTC YYYY-MM


def _run_cost(run: dict[str, Any]) -> float:
    """Prefer the recorded `cost_usd`; else derive gpu_hours × rate; else 0.0."""
    cost = run.get("cost_usd")
    try:
        if cost is not None:
            return float(cost)
    except (TypeError, ValueError):
        pass
    try:
        return float(run.get("gpu_hours") or 0.0) * float(run.get("rate_usd_per_hour") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def summarize_spend(
    path: str | os.PathLike[str] | None = None,
    *,
    current_month: str | None = None,
) -> dict[str, Any]:
    """Roll the ledger up for the API / dashboard.

    ``current_month`` (``YYYY-MM``) selects the "this month" bucket; callers pass the
    real month (the module never reads a wall clock — keeps it deterministic/testable).
    When omitted, the month bucket totals are still returned but ``current_month_usd``
    is ``0.0`` with ``current_month`` ``None``.
    """
    ledger = load_ledger(path)
    budget = float(ledger.get("budget_usd_per_month") or _DEFAULT_BUDGET_USD)
    raw_runs = ledger.get("runs") or []

    runs_out: list[dict[str, Any]] = []
    by_month: dict[str, dict[str, Any]] = {}
    lifetime = 0.0
    for run in raw_runs:
        if not isinstance(run, dict):
            continue
        cost = _run_cost(run)
        month = _month_of(run)
        lifetime += cost
        if month:
            b = by_month.setdefault(month, {"month": month, "usd": 0.0, "runs": 0})
            b["usd"] += cost
            b["runs"] += 1
        runs_out.append(
            {
                "run_id": run.get("run_id"),
                "started_at": run.get("started_at"),
                "ended_at": run.get("ended_at"),
                "experiment": run.get("experiment"),
                "gpu_type": run.get("gpu_type"),
                "gpu_hours": run.get("gpu_hours"),
                "rate_usd_per_hour": run.get("rate_usd_per_hour"),
                "cost_usd": round(cost, 4),
                "status": run.get("status"),
                "month": month,
            }
        )

    # Newest-first, with a running month-to-date cumulative for the display.
    runs_out.sort(key=lambda r: (r.get("ended_at") or r.get("started_at") or ""), reverse=True)
    month_cum: dict[str, float] = {}
    for r in reversed(runs_out):  # chronological to accumulate
        m = r.get("month")
        if m:
            month_cum[m] = month_cum.get(m, 0.0) + float(r["cost_usd"])
            r["cumulative_month_usd"] = round(month_cum[m], 4)
        else:
            r["cumulative_month_usd"] = None

    cur_usd = round(by_month.get(current_month, {}).get("usd", 0.0), 4) if current_month else 0.0
    cur_runs = by_month.get(current_month, {}).get("runs", 0) if current_month else 0

    return {
        "present": True,
        "provider": ledger.get("provider"),
        "currency": ledger.get("currency", "USD"),
        "budget_usd_per_month": budget,
        "current_month": current_month,
        "current_month_usd": cur_usd,
        "current_month_runs": cur_runs,
        "budget_remaining_usd": round(max(0.0, budget - cur_usd), 4) if current_month else None,
        "over_budget": (cur_usd > budget) if current_month else False,
        "lifetime_usd": round(lifetime, 4),
        "run_count": len(runs_out),
        "by_month": sorted(by_month.values(), key=lambda b: b["month"], reverse=True),
        "runs": runs_out,
    }


def would_exceed_budget(est_run_cost_usd: float, current_month: str, path: str | os.PathLike[str] | None = None) -> bool:
    """The burst-workflow hard gate: True if this run would push the month past budget."""
    s = summarize_spend(path, current_month=current_month)
    return (s["current_month_usd"] + float(est_run_cost_usd)) > s["budget_usd_per_month"]


def record_run(run: dict[str, Any], *, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Append one run entry to the ledger and persist. Returns the updated ledger.

    Called by the burst workflow after teardown. ``run`` should carry at least
    ``run_id``, ``started_at``, ``ended_at``, ``gpu_hours``, ``rate_usd_per_hour``
    (and/or a precomputed ``cost_usd``), ``experiment``, ``status``. Best-effort
    ``cost_usd`` is filled from gpu_hours × rate when absent.
    """
    p = Path(path) if path is not None else ledger_path()
    ledger = load_ledger(p)
    entry = dict(run)
    entry.setdefault("cost_usd", round(_run_cost(entry), 4))
    ledger.setdefault("runs", []).append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ledger
