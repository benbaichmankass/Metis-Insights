#!/usr/bin/env python3
"""Render a consolidated /system-report JSON into a self-contained report.

PURE renderer: consolidated JSON in -> report.html (responsive) + report.md
out, plus an append to the report index manifest. Does NOT gather any data,
call any API, or import any ``src.*`` module — the master skill
(``.claude/skills/system-review/SKILL.md``; ``system-report`` is a back-compat
alias) assembles the JSON; this script only formats it. Stdlib-only (matches
scripts/daily_heartbeat.py) so it runs even when the bot venv is unavailable.

The JSON shape is documented in
``comms/schema/system_report_response.template.json``; the report spec is
``docs/reports/system-report-DESIGN.md``.

Usage:
    python3 scripts/reports/render_system_report.py <consolidated.json> \
        [--out-dir comms/reports] [--no-index]

The output is written to ``<out-dir>/<window>/<UTC-ts>/{report.html,report.md,
report.json}`` and the index at ``<out-dir>/index.json`` is updated
(newest-first). Prints the written HTML path on success.

The produced HTML is **responsive** (mobile-first, single desktop breakpoint)
and **self-contained** (embedded CSS, no external assets) so the GitHub raw
link renders standalone on a phone or a desktop browser.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DASH = "—"  # em-dash for null values (repo rendering rule)

_GRADE_DOT = {
    "healthy": "\U0001f7e2",      # green
    "ok": "\U0001f7e2",
    "caution": "\U0001f7e1",      # yellow
    "watch": "\U0001f7e1",
    "mixed": "\U0001f7e1",
    "investigate": "\U0001f534",  # red
    "concern": "\U0001f534",
}


# ---------------------------------------------------------------------------
# Value formatting (null -> em-dash, never 0 / "unknown")
# ---------------------------------------------------------------------------

def _f(value: Any) -> str:
    """Format a scalar; None/missing -> em-dash."""
    if value is None or value == "":
        return DASH
    return html.escape(str(value))


def _money(value: Any) -> str:
    if value is None:
        return DASH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _f(value)
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:,.2f}"


def _pct(value: Any) -> str:
    if value is None:
        return DASH
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return _f(value)


def _num(value: Any, places: int = 2) -> str:
    if value is None:
        return DASH
    try:
        return f"{float(value):,.{places}f}"
    except (TypeError, ValueError):
        return _f(value)


def _dot(grade: Any) -> str:
    if not grade:
        return ""
    return _GRADE_DOT.get(str(grade).lower(), "")


def _pnl_class(value: Any) -> str:
    try:
        return "pos" if float(value) >= 0 else "neg"
    except (TypeError, ValueError):
        return ""


def _trend_arrow(trend: Any) -> str:
    return {"up": "↑", "down": "↓", "flat": "→"}.get(str(trend or "").lower(), "")


# ---------------------------------------------------------------------------
# HTML sections
# ---------------------------------------------------------------------------

_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  background:#0e1117;color:#e6e6e6}
.wrap{max-width:980px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.2em 0}
h2{font-size:1.15rem;margin:1.6em 0 .4em;padding-bottom:.25em;border-bottom:1px solid #2a2f3a}
h3{font-size:1rem;margin:1.1em 0 .3em;color:#cdd3df}
.meta{color:#9aa4b2;font-size:.86rem}
.grade{display:inline-block;padding:.15em .55em;border-radius:999px;background:#1b2230;font-weight:600}
.cards{display:grid;grid-template-columns:1fr;gap:10px;margin:.6em 0}
.card{background:#161b24;border:1px solid #232a36;border-radius:10px;padding:12px}
.card .k{color:#9aa4b2;font-size:.78rem;text-transform:uppercase;letter-spacing:.03em}
.card .v{font-size:1.25rem;font-weight:700;margin-top:.15em}
.pos{color:#4ec07a}.neg{color:#e2607a}
.pill{display:inline-block;font-size:.74rem;padding:.1em .5em;border-radius:6px;background:#222a38;margin-right:.3em}
.pill.real{background:#1d3b2a;color:#7ee0a4}
.pill.paper{background:#2b2f1d;color:#d6dd7e}
.pill.prop{background:#2a213b;color:#c39ee0}
ul.pri{margin:.4em 0;padding-left:1.1em}
ul.pri li{margin:.25em 0}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:.86rem;margin:.4em 0}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #232a36;white-space:nowrap}
th{color:#9aa4b2;font-weight:600}
details{background:#13171f;border:1px solid #232a36;border-radius:8px;margin:.4em 0;padding:.2em .6em}
details summary{cursor:pointer;padding:.45em 0;font-weight:600}
details .body{padding:.3em 0 .7em}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 14px;font-size:.86rem;margin:.3em 0}
.kv .k{color:#9aa4b2}
.muted{color:#9aa4b2}
.sev-concern{color:#e2607a}.sev-watch{color:#d6dd7e}.sev-ok{color:#4ec07a}
.section-empty{color:#6b7480;font-style:italic;font-size:.88rem}
footer{margin-top:2em;padding-top:1em;border-top:1px solid #2a2f3a;color:#6b7480;font-size:.8rem}
@media(min-width:680px){.cards{grid-template-columns:repeat(4,1fr)}.wrap{padding:28px}}
"""


def _kpi(label: str, value: str, cls: str = "") -> str:
    return f'<div class="card"><div class="k">{html.escape(label)}</div><div class="v {cls}">{value}</div></div>'


def _section_health(report: dict) -> str:
    health = report.get("health") or {}
    findings = health.get("findings") or {}
    services = (report.get("consolidated") or {}).get("services") or []
    rows = []
    for name, data in findings.items():
        if not isinstance(data, dict):
            continue
        status = data.get("status")
        sev = {"concern": "sev-concern", "watch": "sev-watch", "ok": "sev-ok"}.get(str(status), "muted")
        rows.append(
            f"<tr><td>{html.escape(str(name))}</td>"
            f'<td class="{sev}">{_dot(status)} {_f(status)}</td>'
            f"<td>{_f(data.get('note'))}</td></tr>"
        )
    assessment = health.get("overall_assessment")
    out = [f'<h2>System &amp; technical health <span class="grade">{_dot(assessment)} {_f(assessment)}</span></h2>']
    if services:
        out.append('<div class="tablewrap"><table><tr><th>Service</th><th>State</th><th>Since</th></tr>')
        for s in services:
            out.append(
                f"<tr><td>{_f(s.get('unit'))}</td><td>{_f(s.get('state'))}/{_f(s.get('sub_state'))}</td>"
                f"<td>{_f(s.get('active_enter_iso'))}</td></tr>"
            )
        out.append("</table></div>")
    if rows:
        out.append('<div class="tablewrap"><table><tr><th>Check</th><th>Status</th><th>Note</th></tr>')
        out.extend(rows)
        out.append("</table></div>")
    else:
        out.append('<p class="section-empty">No health findings in this report.</p>')
    return "\n".join(out)


def _class_block(title: str, cls: str, data: dict | None) -> str:
    data = data or {}
    out = [f'<h3><span class="pill {cls}">{title}</span></h3>']
    out.append('<div class="cards">')
    out.append(_kpi("Window P&L", _money(data.get("window_pnl")), _pnl_class(data.get("window_pnl"))))
    prior = data.get("prior_window_pnl")
    out.append(_kpi("Prior window", f'{_money(prior)} {_trend_arrow(data.get("trend"))}', _pnl_class(prior)))
    if cls == "prop":
        out.append(_kpi("Fills reported", _f(data.get("fills_reported"))))
        out.append(_kpi("Un-acted tickets", _f(data.get("unacted_tickets"))))
    else:
        out.append(_kpi("Trades", _f(data.get("trades"))))
        out.append(_kpi("Win rate", _pct(data.get("win_rate"))))
    out.append("</div>")
    if cls != "prop":
        out.append('<div class="cards">')
        out.append(_kpi("Expectancy", _money(data.get("expectancy"))))
        out.append(_kpi("Profit factor", _num(data.get("profit_factor"))))
        out.append(_kpi("Max drawdown", _money(data.get("max_drawdown"))))
        out.append(_kpi("Wins", _f(data.get("wins"))))
        out.append("</div>")
        per = data.get("per_strategy") or []
        if per:
            out.append('<div class="tablewrap"><table><tr><th>Strategy</th><th>Trades</th><th>Win%</th><th>P&L</th></tr>')
            for r in per:
                out.append(
                    f"<tr><td>{_f(r.get('name'))}</td><td>{_f(r.get('trades'))}</td>"
                    f"<td>{_pct(r.get('win_rate'))}</td>"
                    f'<td class="{_pnl_class(r.get("pnl"))}">{_money(r.get("pnl"))}</td></tr>'
                )
            out.append("</table></div>")
    else:
        rd = data.get("rule_distance") or {}
        out.append('<div class="cards">')
        out.append(_kpi("Daily-loss cushion", _money(rd.get("daily_loss_remaining"))))
        out.append(_kpi("Static-DD cushion", _money(rd.get("static_dd_remaining"))))
        out.append(_kpi("Tickets emitted", _f(data.get("tickets_emitted"))))
        out.append("</div>")
    return "\n".join(out)


def _section_trading(report: dict) -> str:
    cons = report.get("consolidated") or {}
    pbc = cons.get("pnl_by_class") or {}
    out = ["<h2>Trading activity &amp; performance</h2>",
           '<p class="muted">Real, paper and prop are reported separately and never blended.</p>']
    out.append(_class_block("REAL MONEY", "real", pbc.get("real")))
    out.append(_class_block("PAPER", "paper", pbc.get("paper")))
    out.append(_class_block("PROP", "prop", pbc.get("prop")))

    dossiers = cons.get("trade_dossiers") or []
    cov = cons.get("dossier_coverage") or {}
    out.append("<h3>Per-trade decision dossiers</h3>")
    if cov:
        out.append(
            f'<p class="muted">{_f(cov.get("full_dossiers"))} full dossiers, '
            f'{_f(cov.get("summarized_trades"))} summarized ({_f(cov.get("rule"))}).</p>'
        )
    if not dossiers:
        out.append('<p class="section-empty">No trades in this window.</p>')
        return "\n".join(out)
    for d in dossiers:
        grade = (d.get("claude_grade") or {})
        cls = {"real_money": "real", "paper": "paper", "prop": "prop"}.get(str(d.get("account_class")), "")
        summary = (
            f'<span class="pill {cls}">{_f(d.get("account_class"))}</span> '
            f'{_f(d.get("symbol"))} {_f(d.get("direction"))} · {_f(d.get("strategy"))} · '
            f'<span class="{_pnl_class(d.get("pnl"))}">{_money(d.get("pnl"))}</span> '
            f'· grade {_f(grade.get("grade"))}'
        )
        meta = d.get("meta") or {}
        ms = d.get("model_scores") or {}
        ms_str = ", ".join(
            f"{html.escape(str(mid))}:{_f((mv or {}).get('stage'))}={_num((mv or {}).get('score'), 3)}"
            for mid, mv in ms.items()
        ) or DASH
        body = ['<div class="body"><div class="kv">']
        for k, v in (
            ("trade_id", d.get("trade_id")), ("account", d.get("account")),
            ("opened", d.get("opened_at")), ("closed", d.get("closed_at")),
            ("hold (s)", d.get("hold_seconds")),
            ("entry", d.get("entry_price")), ("exit", d.get("exit_price")),
            ("stop", d.get("stop_loss")), ("target", d.get("take_profit")),
            ("qty", d.get("qty")), ("pnl %", d.get("pnl_percent")),
            ("close reason", d.get("close_reason")),
            ("setup", meta.get("setup_type")), ("killzone", meta.get("killzone")),
            ("bias", meta.get("bias")),
            ("entry quality", grade.get("entry_quality")),
            ("exit quality", grade.get("exit_quality")),
            ("risk mgmt", grade.get("risk_management")),
        ):
            body.append(f'<div class="k">{html.escape(k)}</div><div>{_f(v)}</div>')
        body.append(f'<div class="k">model scores</div><div>{ms_str}</div>')
        body.append(f'<div class="k">signal logic</div><div>{_f(d.get("signal_logic"))}</div>')
        body.append(f'<div class="k">grade rationale</div><div>{_f(grade.get("rationale"))}</div>')
        body.append("</div></div>")
        out.append(f"<details><summary>{summary}</summary>{''.join(body)}</details>")
    return "\n".join(out)


def _section_market(report: dict) -> str:
    rows = (report.get("consolidated") or {}).get("market_context") or []
    out = ["<h2>Market context</h2>"]
    if not rows:
        out.append('<p class="section-empty">No market context captured.</p>')
        return "\n".join(out)
    out.append('<div class="tablewrap"><table>'
               '<tr><th>Symbol</th><th>Open</th><th>Close</th><th>High</th><th>Low</th><th>% chg</th><th>Note</th></tr>')
    for r in rows:
        out.append(
            f"<tr><td>{_f(r.get('symbol'))}</td><td>{_f(r.get('open'))}</td><td>{_f(r.get('close'))}</td>"
            f"<td>{_f(r.get('high'))}</td><td>{_f(r.get('low'))}</td>"
            f'<td class="{_pnl_class(r.get("pct_change"))}">{_pct(r.get("pct_change"))}</td>'
            f"<td>{_f(r.get('note'))}</td></tr>"
        )
    out.append("</table></div>")
    return "\n".join(out)


def _section_ml(report: dict) -> str:
    ml = report.get("ml") or {}
    models = ml.get("model_status") or []
    out = [f'<h2>ML / models <span class="grade">{_dot(ml.get("overall_assessment"))} '
           f'{_f(ml.get("overall_assessment"))}</span></h2>']
    if not models:
        out.append('<p class="section-empty">No model status in this report.</p>')
    else:
        out.append('<div class="tablewrap"><table>'
                   '<tr><th>Model</th><th>Stage</th><th>Last metric</th><th>Trend</th><th>Shadow/drift</th><th>Note</th></tr>')
        for m in models:
            lt = m.get("last_training") or {}
            ls = m.get("live_shadow") or {}
            out.append(
                f"<tr><td>{_f(m.get('model_id'))}</td><td>{_f(m.get('stage'))}</td>"
                f"<td>{_f(lt.get('headline_metric'))}</td><td>{_f(lt.get('trend_vs_prior_run'))}</td>"
                f"<td>{_f(ls.get('drift'))}</td><td>{_f(m.get('note'))}</td></tr>"
            )
        out.append("</table></div>")
    recs = ml.get("promotion_recommendations") or []
    if recs:
        out.append("<h3>Promotion / demotion recommendations (Tier-3)</h3><ul class='pri'>")
        for r in recs:
            out.append(
                f"<li>{_f(r.get('direction'))} <b>{_f(r.get('model_id'))}</b> "
                f"{_f(r.get('current_stage'))}→{_f(r.get('proposed_stage'))}: {_f(r.get('evidence'))}</li>"
            )
        out.append("</ul>")
    return "\n".join(out)


def _section_actions(report: dict) -> str:
    cons = report.get("consolidated") or {}
    pri = cons.get("operator_priorities") or []
    out = ["<h2>Actions &amp; backlog</h2>"]
    if pri:
        out.append("<h3>Operator priorities</h3><ul class='pri'>")
        for p in pri:
            flag = " ⚠️ operator" if p.get("operator_action_required") else ""
            out.append(
                f"<li><b>{_f(p.get('title'))}</b> "
                f'<span class="pill">{_f(p.get("source_review"))} · T{_f(p.get("tier"))}</span>{flag}'
                f"<br><span class='muted'>{_f(p.get('detail'))}</span></li>"
            )
        out.append("</ul>")
    bs = cons.get("backlog_summary") or {}
    if bs:
        out.append('<div class="cards">')
        for dom in ("health", "performance", "ml"):
            d = bs.get(dom) or {}
            open_ = d.get("open")
            total = d.get("total")
            drained = d.get("drained")
            # Lead with the precise, always-computable open/total; drained is a
            # secondary "progress this window" line (may be 0).
            if total is not None:
                head = f"{_f(open_)} open / {_f(total)} total"
            else:
                head = f"{_f(open_)} open"
            if drained:
                head += f" · {_f(drained)} drained"
            out.append(_kpi(f"{dom} backlog", head))
        out.append("</div>")
    t3 = cons.get("tier3_proposals_pending") or []
    if t3:
        out.append("<h3>Tier-3 proposals awaiting approval</h3><ul class='pri'>")
        for p in t3:
            out.append(f"<li><span class='pill'>{_f(p.get('source_review'))}</span> {_f(p.get('summary'))}</li>")
        out.append("</ul>")
    notes = cons.get("cross_review_notes") or []
    if notes:
        out.append("<h3>Cross-review notes</h3><ul class='pri'>")
        out.extend(f"<li>{_f(n)}</li>" for n in notes)
        out.append("</ul>")
    return "\n".join(out)


def _section_review_coverage(report: dict) -> str:
    """Render the review-coverage block — strategy promotion, ML training health,
    soak status, flags. Proves the review covered its mandate (2026-06-23)."""
    rc = (report.get("consolidated") or {}).get("review_coverage") or {}
    out = ["<h2>Review coverage</h2>"]
    if not rc:
        out.append('<p class="section-empty">No review-coverage block — '
                   'promotion / training / soak assessment not recorded.</p>')
        return "\n".join(out)
    sp = rc.get("strategy_promotion") or {}
    out.append("<h3>Strategy promotion / demotion</h3><ul class='pri'>")
    for r in (sp.get("ready_to_promote") or []):
        out.append(f"<li>PROMOTE <b>{_f(r.get('name'))}</b> — {_f(r.get('evidence'))}</li>")
    for r in (sp.get("demote_or_kill") or []):
        out.append(f"<li>{_f(r.get('gate'))} <b>{_f(r.get('name'))}</b> — {_f(r.get('evidence'))}</li>")
    out.append(f"<li class='muted'>{_f(sp.get('summary'))}</li></ul>")
    mh = rc.get("ml_training_health") or {}
    out.append("<h3>ML training health</h3><div class='cards'>")
    out.append(_kpi("Cycles since last", _f(mh.get("cycles_since_last_review"))))
    out.append(_kpi("Dataset builds OK", _f(mh.get("dataset_builds_ok"))))
    out.append("</div>")
    if mh.get("summary"):
        out.append(f"<p class='muted'>{_f(mh.get('summary'))}</p>")
    soaks = rc.get("soak_status") or []
    if soaks:
        out.append('<div class="tablewrap"><table>'
                   '<tr><th>Soak</th><th>State</th><th>Detail</th></tr>')
        for s in soaks:
            out.append(f"<tr><td>{_f(s.get('soak'))}</td><td>{_f(s.get('state'))}</td>"
                       f"<td>{_f(s.get('detail'))}</td></tr>")
        out.append("</table></div>")
    flags = rc.get("flags_raised") or []
    out.append("<h3>Flags raised</h3>")
    if flags:
        out.append("<ul class='pri'>" + "".join(f"<li>🚩 {_f(x)}</li>" for x in flags) + "</ul>")
    else:
        out.append('<p class="muted">None — nothing degrading this review.</p>')
    return "\n".join(out)


_MON_BADGE = {
    "soaking": "⏳",
    "awaiting-data": "🧱",
    "awaiting-decision": "🗳️",
    "verify": "🔁",
}


def _section_monitoring(report: dict) -> str:
    """Render the Monitoring section — backlog items that need more time
    (soaking / awaiting data) or a decision (gate-met / operator-gated).
    These are the deferred-with-reason items the review is actively tracking
    rather than acting on this run."""
    rows = (report.get("consolidated") or {}).get("monitoring") or []
    out = ["<h2>Monitoring <span class='muted'>— soaking / awaiting decision</span></h2>"]
    if not rows:
        out.append('<p class="muted">Nothing under active monitoring.</p>')
        return "\n".join(out)
    out.append('<div class="tablewrap"><table>'
               '<tr><th>Item</th><th>Domain</th><th>State</th>'
               '<th>What it’s waiting on</th><th>Next check</th></tr>')
    for r in rows:
        cat = _f(r.get("category"))
        badge = _MON_BADGE.get(r.get("category"), "•")
        out.append(
            f"<tr><td><code>{_f(r.get('item_id'))}</code></td>"
            f"<td>{_f(r.get('domain'))}</td>"
            f"<td>{badge} {cat}</td>"
            f"<td>{_f(r.get('detail'))}</td>"
            f"<td>{_f(r.get('next_check'))}</td></tr>"
        )
    out.append("</table></div>")
    return "\n".join(out)


def render_html(report: dict) -> str:
    cons = report.get("consolidated") or {}
    is_audit = str(report.get("window") or "") == "audit"
    title = ("Full-system audit report" if is_audit
             else f"System report — {report.get('window', '')}")
    header = [
        f"<h1>{html.escape(title)}</h1>",
        f'<p class="meta">Generated {_f(report.get("reviewed_at"))} · '
        f'window {_f(report.get("window_start"))} → {_f(report.get("window_end"))}</p>',
        f'<p><span class="grade">Roll-up: {_dot(cons.get("roll_up_grade"))} '
        f'{_f(cons.get("roll_up_grade"))}</span></p>',
    ]
    if cons.get("headline"):
        header.append(f"<p>{_f(cons.get('headline'))}</p>")
    # An audit report is a governance pass, not a trading window: the trade /
    # market / ML / review-coverage sections don't apply, so they're skipped
    # (rendering them would print misleading em-dash trading blocks). The audit
    # narrative rides in actions (findings + fixes + Tier-3 queue + cross notes),
    # health (the audit axes as findings), and monitoring (remaining / handed-off).
    sections = [_section_actions(report), _section_health(report)]
    if not is_audit:
        sections += [
            _section_trading(report),
            _section_market(report),
            _section_ml(report),
            _section_review_coverage(report),
        ]
    sections.append(_section_monitoring(report))
    footer_kind = "full-system-audit" if is_audit else "system-report"
    body = "\n".join([
        *header,
        *sections,
        f'<footer>report_id {_f(report.get("report_id"))} · reviewer {_f(report.get("reviewer"))} '
        f'· prior {_f(report.get("prior_report_id"))} · '
        f'ICT Trading Bot {footer_kind}</footer>',
    ])
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><div class=\"wrap\">{body}</div></body></html>"
    )


# ---------------------------------------------------------------------------
# Markdown twin (lightweight — for chat/diff readability)
# ---------------------------------------------------------------------------

def render_md(report: dict) -> str:
    cons = report.get("consolidated") or {}
    pbc = cons.get("pnl_by_class") or {}
    is_audit = str(report.get("window") or "") == "audit"
    lines = [
        ("# Full-system audit report" if is_audit
         else f"# System report — {report.get('window', '')}"),
        "",
        f"- Generated: {report.get('reviewed_at', DASH)}",
        f"- Window: {report.get('window_start', DASH)} → {report.get('window_end', DASH)}",
        f"- Roll-up grade: {cons.get('roll_up_grade', DASH)}",
        "",
        cons.get("headline", ""),
        "",
    ]
    # P&L-by-class is a trading-window concept; an audit report omits it.
    if not is_audit:
        lines.append("## P&L by class")
        for cls in ("real", "paper", "prop"):
            d = pbc.get(cls) or {}
            lines.append(
                f"- **{cls}**: window {_money(d.get('window_pnl'))} "
                f"(prior {_money(d.get('prior_window_pnl'))}, {d.get('trend', DASH)})"
            )
    lines += ["", "## Operator priorities"]
    for p in (cons.get("operator_priorities") or []):
        lines.append(f"{p.get('rank', '-')}. {p.get('title', '')} — {p.get('detail', '')}")
    rc = cons.get("review_coverage") or {}
    if rc:
        lines += ["", "## Review coverage"]
        sp = rc.get("strategy_promotion") or {}
        lines.append(f"- Strategy promotion: {sp.get('summary', DASH)}")
        mh = rc.get("ml_training_health") or {}
        lines.append(f"- ML training health: {mh.get('summary', DASH)}")
        for s in (rc.get("soak_status") or []):
            lines.append(f"- Soak `{s.get('soak', DASH)}`: {s.get('state', DASH)} — {s.get('detail', '')}")
        for fl in (rc.get("flags_raised") or []):
            lines.append(f"- 🚩 {fl}")
    mon = cons.get("monitoring") or []
    if mon:
        lines += ["", "## Monitoring (soaking / awaiting decision)"]
        for m in mon:
            lines.append(
                f"- `{m.get('item_id', DASH)}` [{m.get('domain', DASH)} · "
                f"{m.get('category', DASH)}] {m.get('detail', '')}"
                + (f" (next: {m.get('next_check')})" if m.get('next_check') else "")
            )
    lines += ["", f"_report_id {report.get('report_id', DASH)}_"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Writing + index
# ---------------------------------------------------------------------------

def _ts_slug(report: dict) -> str:
    raw = report.get("reviewed_at")
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else datetime.now(timezone.utc)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _repo_root_for(out_dir: Path) -> Path:
    """Best-effort repo root so index paths match the router's resolver.

    The reports router resolves artifacts as ``repo_root() / rel_path``, so
    the index must store paths relative to the repo root. Walk up from the
    output dir looking for a ``.git`` marker; fall back to the dir two levels
    above ``out_dir`` (the ``comms/reports`` -> repo-root assumption).
    """
    cur = out_dir.resolve()
    for parent in (cur, *cur.parents):
        if (parent / ".git").exists():
            return parent
    # Fallback: out_dir is expected to be <repo>/comms/reports.
    return out_dir.resolve().parent.parent


def _update_index(out_dir: Path, entry: dict) -> None:
    index_path = out_dir / "index.json"
    data: dict[str, Any] = {"schema_version": 1, "reports": []}
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8")) or data
        except (OSError, json.JSONDecodeError):
            data = {"schema_version": 1, "reports": []}
    reports = [r for r in data.get("reports", []) if r.get("id") != entry["id"]]
    reports.insert(0, entry)
    data["reports"] = reports
    index_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_report(report: dict, out_dir: Path, update_index: bool = True) -> dict:
    window = str(report.get("window") or "since-last")
    slug = _ts_slug(report)
    rpt_dir = out_dir / window / slug
    rpt_dir.mkdir(parents=True, exist_ok=True)

    html_path = rpt_dir / "report.html"
    md_path = rpt_dir / "report.md"
    json_path = rpt_dir / "report.json"
    html_path.write_text(render_html(report), encoding="utf-8")
    md_path.write_text(render_md(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    root = _repo_root_for(out_dir)

    def rel(p: Path) -> str:
        return str(p.resolve().relative_to(root)) if _is_under(p, root) else str(p)

    cons = report.get("consolidated") or {}
    entry = {
        "id": report.get("report_id") or f"RPT-{slug}-{window}",
        "window": window,
        "generated_at": report.get("reviewed_at"),
        "window_start": report.get("window_start"),
        "window_end": report.get("window_end"),
        "roll_up_grade": cons.get("roll_up_grade") or report.get("overall_assessment"),
        "headline": cons.get("headline"),
        "html_path": rel(html_path),
        "json_path": rel(json_path),
        "md_path": rel(md_path),
    }
    if update_index:
        _update_index(out_dir, entry)
    return {"html": str(html_path), "md": str(md_path), "json": str(json_path), "index_entry": entry}


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a consolidated system-report JSON to HTML + MD.")
    ap.add_argument("json_path", help="Path to the consolidated report JSON.")
    ap.add_argument("--out-dir", default="comms/reports", help="Report artifact root (default: comms/reports).")
    ap.add_argument("--no-index", action="store_true", help="Do not update index.json.")
    args = ap.parse_args(argv)

    try:
        report = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read consolidated JSON {args.json_path}: {exc}", file=sys.stderr)
        return 1

    result = write_report(report, Path(args.out_dir), update_index=not args.no_index)
    print(result["html"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
