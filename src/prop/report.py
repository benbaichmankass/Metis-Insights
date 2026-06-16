"""Prop-firm report — render a list of per-combo verdicts to Markdown + JSON.

Consumes the verdict dicts produced by :func:`src.prop.evaluator.evaluate`
(one per roster combo) and emits the design §6 output: a pass/fail matrix in
Markdown plus the raw JSON, both written under
``runtime_logs/prop_eval/<UTC-date>/``.

Tier-1 research tooling — no live-path imports.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def _fmt_num(v: Optional[float], prefix: str = "") -> str:
    if v is None:
        return "—"
    return f"{prefix}{v:,.0f}"


def _yn(v: Optional[bool]) -> str:
    if v is None:
        return "—"
    return "✅" if v else "❌"


def _breach_str(breach: Optional[Dict[str, Any]]) -> str:
    if not breach:
        return "—"
    return f"{breach.get('rule', '?')} @ {breach.get('ts', '?')}"


def render_markdown(
    verdicts: Sequence[Dict[str, Any]],
    *,
    ruleset_view: Optional[Dict[str, Any]] = None,
    data_window: Optional[Dict[str, Any]] = None,
    generated_at: Optional[str] = None,
) -> str:
    """Render the ranked verdict list to a Markdown matrix.

    Verdicts are expected pre-ranked (best first); rendered in the order given.
    """
    gen = generated_at or datetime.now(timezone.utc).isoformat()
    lines: List[str] = []
    lines.append("# Prop-firm evaluation matrix")
    lines.append("")

    rs_name = verdicts[0].get("ruleset", "?") if verdicts else (
        (ruleset_view or {}).get("ruleset", "?")
    )
    unconfirmed = any(v.get("unconfirmed") for v in verdicts) or bool(
        (ruleset_view or {}).get("unconfirmed")
    )
    lines.append(f"- **Ruleset:** `{rs_name}`")
    if ruleset_view:
        lims = ruleset_view.get("limits", {})
        ev = ruleset_view.get("evaluation", {})
        lines.append(
            f"- **Limits:** profit target {_fmt_pct(ev.get('profit_target_pct'))}, "
            f"daily-loss {_fmt_pct(lims.get('daily_loss_pct'))}, "
            f"max-DD {_fmt_pct(lims.get('max_drawdown_pct'))} "
            f"({lims.get('drawdown_type', '?')}), "
            f"funded soak {ruleset_view.get('funded_soak_days', '?')}d"
        )
    if data_window:
        lines.append(
            f"- **Data window:** {data_window.get('start', '?')} → {data_window.get('end', '?')}"
            f" ({data_window.get('data_start', '?')} → {data_window.get('data_end', '?')})"
        )
    lines.append(f"- **Generated:** {gen}")
    if unconfirmed:
        lines.append("")
        lines.append(
            "> ⚠️ **UNCONFIRMED RULESET** — one or more ruleset fields are "
            "placeholders, not verified from the prop firm's terms. A pass here "
            "proves nothing about the real evaluation. Verify the numbers first."
        )
    lines.append("")

    # The matrix.
    header = (
        "| Rank | Roster | Eval pass | Days→target | Active days | "
        "Worst-DD | Consistency worst-day | Funded survive | First breach | Net $ |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    lines.append(header)
    lines.append(sep)
    for i, v in enumerate(verdicts, start=1):
        ev = v.get("eval", {})
        fs = v.get("funded_soak", {})
        m = v.get("metrics", {})
        eval_breach = ev.get("first_breach")
        funded_breach = fs.get("first_breach")
        first_breach = eval_breach or funded_breach
        max_dd = m.get("max_drawdown_pct")
        max_dd_str = f"{max_dd:.1f}%" if isinstance(max_dd, (int, float)) else "—"
        lines.append(
            "| {rank} | `{roster}` | {ep} | {dtt} | {act} | {dd} | {cons} | {fs} | {fb} | {net} |".format(
                rank=i,
                roster=v.get("roster", "?"),
                ep=_yn(ev.get("passed")),
                dtt=ev.get("days_to_target") if ev.get("days_to_target") is not None else "—",
                act=ev.get("active_trading_days", "—"),
                dd=max_dd_str,
                cons=_fmt_pct(m.get("consistency_worst_day_share")),
                fs=_yn(fs.get("survived")) if ev.get("passed") else "—",
                fb=_breach_str(first_breach),
                net=_fmt_num(m.get("net_pnl"), prefix="$"),
            )
        )
    lines.append("")
    lines.append(f"*{len(verdicts)} combos evaluated. Headlines below.*")
    lines.append("")
    for i, v in enumerate(verdicts, start=1):
        lines.append(f"{i}. `{v.get('roster', '?')}` — {v.get('headline', '?')}")
    lines.append("")
    return "\n".join(lines)


def render_json(
    verdicts: Sequence[Dict[str, Any]],
    *,
    ruleset_view: Optional[Dict[str, Any]] = None,
    data_window: Optional[Dict[str, Any]] = None,
    generated_at: Optional[str] = None,
) -> str:
    payload = {
        "kind": "prop_eval_matrix",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "ruleset": ruleset_view,
        "data_window": data_window,
        "combos": list(verdicts),
        "count": len(verdicts),
    }
    return json.dumps(payload, indent=2, default=str)
