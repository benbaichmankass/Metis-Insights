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


def _bars(value: Any) -> str:
    """Render a bar COUNT. Bars are discrete -- `_num`'s two decimal places would
    print "1,082.00 bars", implying a precision the quantity does not have.

    None is the dash, never 0: an unmeasured leg and a leg closed on its entry bar
    are different facts.
    """
    if value is None:
        return DASH
    try:
        return f"{round(float(value)):,d}"
    except (TypeError, ValueError):
        return _f(value)


def _bars_ratio(value: Any) -> str:
    """Render bars_held_p90_ratio, flagging a leg past its own resolution threshold.

    The threshold is not arbitrary: BL-20260821-SCALPS-HELD-10-TO-100X-THEIR-DESIGN-HORIZON
    resolves at "p90 bars-held within 3x its backtested horizon", so 3.0 is the row's own
    bar and the render marks it rather than leaving a reader to do the division.

    None renders as the dash -- NOT as 0.0 and NOT as a passing value. "We did not measure
    this leg's hold" and "this leg holds no bars" are opposite statements, and a ratio is the
    place that collapse would be least visible.
    """
    if value is None:
        return DASH
    try:
        r = float(value)
    except (TypeError, ValueError):
        return _f(value)
    return f"🔴 {r:.1f}x" if r > 3.0 else f"{r:.1f}x"


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
    ec = rc.get("execution_capture") or {}
    if ec:
        out.append("<h3>Execution capture <span class='muted'>— did the edge reach the account?</span></h3>")
        rows = ec.get("per_strategy") or []
        if rows:
            out.append('<div class="tablewrap"><table>'
                       '<tr><th>Strategy</th><th>Book</th><th>n</th>'
                       '<th>Round-trip %</th><th>Giveback R</th>'
                       '<th>Hold h (act/exp)</th>'
                       '<th>Bars held med / <b>p90</b> / exp</th>'
                       '<th>p90 &times;</th><th>State</th></tr>')
            for r in rows:
                st = _f(r.get("state"))
                mark = {"anomaly": "🔴", "degraded": "🟡", "ok": "🟢"}.get(r.get("state"), "")
                out.append(
                    f"<tr><td>{_f(r.get('strategy'))}</td><td>{_f(r.get('book'))}</td>"
                    f"<td>{_f(r.get('n_closed'))}</td><td>{_pct(r.get('roundtrippers_pct'))}</td>"
                    f"<td>{_num(r.get('mean_giveback_r'))}</td>"
                    f"<td>{_num(r.get('hold_h_actual'))} / {_num(r.get('hold_h_expected'))}</td>"
                    f"<td>{_bars(r.get('bars_held_median'))} / "
                    f"<b>{_bars(r.get('bars_held_p90'))}</b> / "
                    f"{_bars(r.get('bars_held_expected'))}</td>"
                    f"<td>{_bars_ratio(r.get('bars_held_p90_ratio'))}</td>"
                    f"<td>{mark} {st}</td></tr>"
                )
            out.append("</table></div>")
        for a in (ec.get("anomalies") or []):
            ro = a.get("reviews_open")
            loud = " ⚠️ ESCALATE" if isinstance(ro, (int, float)) and ro >= 2 else ""
            out.append(
                f"<p class='pri'>🔴 <b>{_f(a.get('strategy'))}</b>: {_f(a.get('symptom'))} "
                f"<span class='muted'>(open {_f(ro)} review(s), since {_f(a.get('first_seen'))}, "
                f"{_f(a.get('backlog_id'))}){loud}</span></p>"
            )
        if ec.get("summary"):
            out.append(f"<p class='muted'>{_f(ec.get('summary'))} "
                       f"· dollars reconciled: {_f(ec.get('dollars_reconciled'))}</p>")
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
        ec = rc.get("execution_capture") or {}
        if ec:
            lines.append(f"- Execution capture: {ec.get('summary', DASH)} "
                         f"(dollars reconciled: {ec.get('dollars_reconciled', DASH)})")
            for r in (ec.get("per_strategy") or []):
                if r.get("state") in ("anomaly", "degraded"):
                    lines.append(
                        f"  - `{r.get('strategy', DASH)}` [{r.get('book', DASH)}]: "
                        f"round-trip {_pct(r.get('roundtrippers_pct'))}, "
                        f"giveback {_num(r.get('mean_giveback_r'))}R, "
                        f"hold {_num(r.get('hold_h_actual'))}/{_num(r.get('hold_h_expected'))}h "
                        f"→ {r.get('state')}")
            for a in (ec.get("anomalies") or []):
                ro = a.get("reviews_open")
                loud = " ⚠️ ESCALATE" if isinstance(ro, (int, float)) and ro >= 2 else ""
                lines.append(f"  - 🔴 `{a.get('strategy', DASH)}`: {a.get('symptom', '')} "
                             f"(open {ro} review(s), {a.get('backlog_id', DASH)}){loud}")
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


# Required review_coverage keys — the mechanical half of the Review-coverage
# guard (SKILL.md § "Review-coverage guard"). execution_capture added 2026-07-30
# so an execution-blindspot (the BYBIT_TPSL_MODE=full bracket bug that soaked for
# weeks) can't ship unmeasured.
_REQUIRED_COVERAGE_KEYS = (
    "strategy_promotion", "ml_training_health", "soak_status",
    "execution_capture", "backlog_drive",
    # ── account_reachability: DECLARED MANDATORY SINCE 2026-06-29, ENFORCED BY
    #    NOTHING UNTIL 2026-08-20. SKILL.md said "any of the SIX required keys"
    #    and named it; this tuple had FIVE and omitted it. Its stated motivation
    #    is "the IB gateway was dark across reviews and went unflagged" — and on
    #    2026-08-20 the full-system audit measured the gateway restarting three
    #    times in 33 minutes (only one scheduled) with nothing flagging it. The
    #    guard against exactly that failure was specified and never wired.
    "account_reachability",
    # ── The three below are the 2026-08-20 operator directive: stop building
    #    things half way and leaving them to rust, and stop knocking backlog
    #    items off one at a time without asking what CLASS they belong to.
    "since_last_build_verification",
    "backlog_classes",
    "ml_output_actionability",
    # ── structural_health: operator-directed 2026-08-24. `backlog_classes`
    #    finds patterns in the BACKLOG; this finds them in the RUNNING SYSTEM,
    #    where the defect may have no backlog row at all. Operator: "if we see
    #    that trades aren't closing properly, or that there are bugs that are
    #    not really resolving themselves over time because we're just putting
    #    on band-aids and we need a bigger structural fix -- those are also
    #    things you should be looking for."
    #
    #    Measured the day it was added, over ALL 1324 closed non-backtest
    #    trades (not the window): 64.7% of closes come from cleanup machinery
    #    and 35.3% from a decision; the M20 exit levers -- the entire point of
    #    the exit-refinement program -- had fired 17 times EVER (1.3%). Not one
    #    of those facts was a backlog row, and eight consecutive reviews had
    #    reported the same execution-capture percentage as a metric without
    #    once asking what it was a symptom OF.
    "structural_health",
    # ── unexercised_fixes: operator-directed 2026-08-24. A fix that is DEPLOYED
    #    and a fix that WORKS look identical from every surface we have, and the
    #    difference is only settled by the mechanism firing on a real trade. Two
    #    such fixes shipped on 2026-08-23 alone (#10174's IB transmit fix; the
    #    durable target-naked cooldown), which is what makes this a CLASS and not
    #    a one-off row. Operator: it "should be a live item that each system
    #    review needs to report on and check thoroughly until we see it work
    #    correctly."
    "unexercised_fixes",
)

#: Verdicts a since-last-build row may carry. `UNWIRED` is the finding.
_BUILD_VERDICTS = {"running", "wired_not_yet_exercised", "UNWIRED", "unverifiable"}


def flags_blob_of(rc: dict) -> str:
    """All of `flags_raised[]` as one searchable string.

    Used by the escalation checks: a finding that must be LOUD has to actually
    appear in the flags, not merely exist in its own coverage block.
    """
    return " ".join(str(f) for f in (rc.get("flags_raised") or []))


#: Phrases that make a 'disposition' vacuous. MODULE-LEVEL since 2026-08-30 so
#: it is IMPORTABLE: `scripts/research/research_disposition.py` enforces the same
#: rule on research-result dispositions, and a second hand-typed copy of 'what
#: counts as a non-reason' would be free to drift -- silently WIDENING what
#: passes as a real reason in one of the two places.
_NON_REASONS = (
    "no new evidence", "carried forward", "no time", "didn't look",
    "did not look", "not looked", "unchanged", "as before", "same as",
)


def _validate_review_coverage(report: dict) -> list[str]:
    """Return a list of coverage violations (empty = clean).

    Enforces, mechanically, what the SKILL's Review-coverage guard requires:
    every required key present + non-empty, AND every execution_capture anomaly
    that has survived >=2 reviews is escalated into flags_raised[] (the
    anti-normalization rule — a defect open across sessions must be loud).
    """
    violations: list[str] = []
    rc = (report.get("consolidated") or {}).get("review_coverage") or {}
    if not rc:
        return ["review_coverage block missing/empty"]
    for key in _REQUIRED_COVERAGE_KEYS:
        val = rc.get(key)
        if val is None or val == "" or val == [] or val == {}:
            violations.append(f"review_coverage.{key} missing/empty")
    # ── A REVIEW DISPOSITIONS; IT DOES NOT MERELY TOUCH (2026-08-13, binding) ──
    #
    # The presence check above was satisfiable by a `deferred` list alone, and
    # that is exactly what happened: measured across the three backlogs on
    # 2026-08-13, **75 recorded review touches explicitly said "no new evidence
    # bearing on this item, carried forward unchanged"**. Honest, and not work —
    # the guard was green while the backlog grew +129 net over 30 days.
    #
    # So `backlog_drive` must now show a real DISPOSITION count: rows drained,
    # snoozed (behind a date + trigger) or promoted to ROADMAP. Deferring
    # everything is still permitted — a review can legitimately find nothing
    # actionable — but it must be SAID in `summary`, not achieved by silence.
    #
    # CLAUDE-RULES-CANONICAL § "Backlog governance", rule 5.
    disposed = 0
    for domain in ("health", "performance", "ml"):
        blk = rc.get("backlog_drive", {}).get(domain) or {}
        if not isinstance(blk, dict):
            continue
        for key in ("drained", "snoozed", "promoted"):
            v = blk.get(key)
            if isinstance(v, list):
                disposed += len(v)
        # A deferral must name a real blocker. "Carried forward unchanged" is
        # the shape this rule exists to refuse — it describes the review, not
        # the row.
        for d in (blk.get("deferred") or []):
            reason = str((d or {}).get("reason") or "").strip()
            if not reason:
                violations.append(
                    f"backlog_drive.{domain}: deferred item "
                    f"'{(d or {}).get('id')}' has no reason")
                continue
            low = reason.casefold()
            if any(nr in low for nr in _NON_REASONS):
                violations.append(
                    f"backlog_drive.{domain}: deferred item "
                    f"'{(d or {}).get('id')}' reason {reason[:60]!r} describes the "
                    "REVIEW, not a blocker on the row — name what the row is "
                    "waiting on (soak/data/operator), or dispose of it")
    if disposed == 0:
        summary = str(rc.get("backlog_drive", {}).get("summary") or "").strip()
        # 80 chars is the "you had to actually write a justification" floor,
        # matching the resolution_criteria guard's reasoning: refuse the empty
        # and the vacuous, do not attempt to police prose quality.
        if len(summary) < 80:
            violations.append(
                "backlog_drive: ZERO rows drained/snoozed/promoted this run, and "
                "`summary` does not justify it. A review dispositions; carrying "
                "every row forward unchanged is not backlog work (75 such touches "
                "were recorded while the backlog grew +129 net in 30 days)")

    # ── SINCE-LAST BUILD VERIFICATION (2026-08-20) ────────────────────────
    #
    # "We keep building things out half way and then leaving them to rust."
    # Measured the same day: 103 of 384 tools under scripts/ have nothing that
    # runs them, and `scripts/ops/trainer_dataset_gc.py` -- the retention tool
    # for a 12G dataset tree -- sat unrun with 0 mentions across 7,442 cycle-log
    # rows while its disk climbed to 93%. Every instance of that class in the
    # record was found by accident months later, never by a review.
    #
    # So a review must enumerate what shipped since the previous one and give
    # each piece a VERDICT. A shipped capability that nothing runs is a finding
    # that must be loud, not a row in a list nobody re-reads.
    slbv = rc.get("since_last_build_verification") or {}
    if isinstance(slbv, dict):
        items = slbv.get("items")
        if not isinstance(items, list):
            violations.append(
                "since_last_build_verification.items missing — list every "
                "capability shipped since the previous review, each with a verdict")
        else:
            shipped = slbv.get("count_shipped")
            if isinstance(shipped, int) and shipped != len(items):
                violations.append(
                    f"since_last_build_verification: count_shipped={shipped} but "
                    f"{len(items)} item(s) listed — the enumeration is incomplete")
            for it in items:
                it = it or {}
                v = str(it.get("verdict") or "")
                name = str(it.get("name") or "?")
                if v not in _BUILD_VERDICTS:
                    violations.append(
                        f"since_last_build_verification '{name}': verdict {v!r} not one "
                        f"of {sorted(_BUILD_VERDICTS)}")
                elif v == "UNWIRED" and name not in flags_blob_of(rc):
                    violations.append(
                        f"since_last_build_verification '{name}' is UNWIRED but not "
                        "escalated into flags_raised[] — a capability that shipped and "
                        "does not run is the finding this key exists to surface")
                elif v == "unverifiable" and not str(it.get("reason") or "").strip():
                    violations.append(
                        f"since_last_build_verification '{name}': verdict 'unverifiable' "
                        "with no reason — say what could not be checked and why")

    # ── BACKLOG CLASSES BEFORE BACKLOG ITEMS (2026-08-20) ──────────────────
    #
    # Draining the backlog one row at a time is a treadmill: the same defect
    # CLASS keeps returning under new ids. The 2026-08-20 audit found
    # `order_packages.id` -- the fictional column behind BL-20260810 -- still
    # declared in 20 test fixtures after the "fix" swept only the reporting
    # instance. So the review must read the WHOLE open backlog for patterns
    # FIRST, and name the structural fix, before disposing of individual rows.
    bc = rc.get("backlog_classes") or {}
    if isinstance(bc, dict):
        reviewed = bc.get("total_open_reviewed")
        if not isinstance(reviewed, int) or reviewed <= 0:
            violations.append(
                "backlog_classes.total_open_reviewed missing — state how many OPEN "
                "rows were read for pattern (the whole set, not a sample)")
        classes = bc.get("classes")
        if not isinstance(classes, list):
            violations.append("backlog_classes.classes missing (use [] with a stated "
                              "summary if the open set genuinely shows no class)")
        else:
            for c in classes:
                c = c or {}
                label = str(c.get("class") or "?")
                members = c.get("member_ids") or []
                if len(members) < 2:
                    violations.append(
                        f"backlog_classes '{label}': needs >=2 member_ids — one row is "
                        "an instance, not a class")
                if not str(c.get("structural_fix") or "").strip():
                    violations.append(
                        f"backlog_classes '{label}': no structural_fix — naming a class "
                        "without the fix that retires it is just a nicer-looking backlog")

    # ── THE BACKLOG IS NOT THE ONLY PLACE A DEFECT LIVES (2026-08-24) ─────
    #
    # `backlog_classes` above reads the BACKLOG for patterns. This reads the
    # RUNNING SYSTEM, where the biggest defects have no backlog row at all --
    # they are visible only as a distribution over live data. Three rules,
    # each earned:
    #
    #  1. POPULATION IS THE WHOLE HISTORY, NOT THE WINDOW. A structural trend
    #     is invisible in a 3-day slice; the window is what let successive
    #     reviews report execution-capture as a flat metric.
    #  2. EVERY FINDING CARRIES A TREND. "Is this class shrinking?" is the
    #     whole question -- a defect count that is flat across reviews means
    #     the fixes are not touching the cause, which is the operator's
    #     actual complaint ("bugs that are not really resolving themselves").
    #  3. ONE FALSIFIABLE HYPOTHESIS, STATED AND TESTED. On 2026-08-24 the
    #     review predicted the provenance gap was downstream of janitor
    #     closes; it was REFUTED (janitor 52.0% measured vs decided 27.0%)
    #     and the refutation was the single most valuable output of the pass.
    #     A structural review that only confirms what it already believed has
    #     not tested anything -- the repo's own RULE ONE, applied to itself.
    sh = rc.get("structural_health") or {}
    if isinstance(sh, dict):
        if not str(sh.get("population") or "").strip():
            violations.append(
                "structural_health.population missing — state the denominator AND "
                "that it spans the whole history, not the review window")
        findings = sh.get("findings")
        if not isinstance(findings, list) or not findings:
            violations.append(
                "structural_health.findings missing — name at least one structural "
                "observation, or state explicitly why the system shows none")
        else:
            for f in findings:
                f = f or {}
                label = str(f.get("finding") or "?")[:60]
                if not str(f.get("measured") or "").strip():
                    violations.append(
                        f"structural_health '{label}': no measured — a structural claim "
                        "without a number over a stated population is an opinion")
                if str(f.get("trend") or "") not in (
                        "falling", "flat", "rising", "first_measurement"):
                    violations.append(
                        f"structural_health '{label}': trend must be one of "
                        "falling|flat|rising|first_measurement — a defect class with no "
                        "trend cannot answer whether the fixes are working")
                if not str(f.get("structural_fix") or "").strip():
                    violations.append(
                        f"structural_health '{label}': no structural_fix")
        hyp = sh.get("hypothesis_tested") or {}
        if not isinstance(hyp, dict) or not str(hyp.get("hypothesis") or "").strip():
            violations.append(
                "structural_health.hypothesis_tested.hypothesis missing — state one "
                "falsifiable structural hypothesis this review actually tested")
        elif str(hyp.get("verdict") or "") not in ("supported", "refuted"):
            violations.append(
                "structural_health.hypothesis_tested.verdict must be supported|refuted")
        elif not str(hyp.get("evidence") or "").strip():
            violations.append(
                "structural_health.hypothesis_tested.evidence missing — the numbers "
                "that settled it, either way")

    # ── THE TRAINER BEING GREEN IS NOT THE QUESTION (2026-08-20) ───────────
    #
    # Operator: "just checking that the trainer vm is green isn't enough - we
    # need to verify that the training sessions and backlogs are actually being
    # worked through and producing reliable and actionable results, every day."
    # `ml_training_health` answers "did it run"; this answers "did what it
    # produced get used". On 2026-08-20: 7,442 cycle rows, ~15% non-ok manifest
    # events, and `outcome` totalling {trained: 20, already_complete: 20}.
    moa = rc.get("ml_output_actionability") or {}
    if isinstance(moa, dict):
        for req in ("cycles_in_window", "outputs_consumed_by", "verdict"):
            if not str(moa.get(req) or "").strip() and not isinstance(moa.get(req), (int, list)):
                violations.append(
                    f"ml_output_actionability.{req} missing — a green service is not "
                    "evidence that a usable result was produced or that anything read it")
        v = str(moa.get("verdict") or "")
        if v and v not in ("actionable", "producing_but_unused", "not_producing", "unverifiable"):
            violations.append(
                f"ml_output_actionability.verdict {v!r} not one of "
                "['actionable','producing_but_unused','not_producing','unverifiable']")
        if v in ("producing_but_unused", "not_producing") and "ml" not in flags_blob_of(rc).lower():
            violations.append(
                f"ml_output_actionability.verdict is {v!r} but nothing about ML is in "
                "flags_raised[] — a training fleet whose output nobody consumes is a "
                "loud finding, not a status line")

    # ── A DEPLOYED FIX IS NOT A WORKING FIX (2026-08-24, operator-directed) ──
    #
    # The two are INDISTINGUISHABLE from every surface available: the code is on
    # main, the deploy sha matches, the tests pass — and none of that shows the
    # mechanism ever ran. Only the mechanism firing on a real trade settles it.
    #
    # The motivating pair, both 2026-08-23: #10174's IB transmit fix, and the
    # durable target-naked cooldown. MGC 4773 is the cautionary case — it closed
    # with BOTH bracket legs resting and the target 233.9 points in the money,
    # and STILL exited via the monitor's `tp_cross`. The order book looked like
    # proof and was not.
    #
    # `verdict: exercised` therefore REQUIRES `evidence` — the trade or event in
    # which the mechanism demonstrably acted. Without that the key would be
    # satisfiable by asserting success, which is the failure it exists to stop.
    uxf = rc.get("unexercised_fixes")
    if isinstance(uxf, dict):
        uxf = [uxf]
    if isinstance(uxf, list):
        for i, row in enumerate(uxf):
            if not isinstance(row, dict):
                continue
            label = str(row.get("fix") or f"[{i}]")
            v = str(row.get("verdict") or "")
            if v not in ("exercised", "still_unexercised", "regressed", "unverifiable"):
                violations.append(
                    f"unexercised_fixes[{label}].verdict {v!r} not one of "
                    "['exercised','still_unexercised','regressed','unverifiable']")
            if v == "exercised" and not str(row.get("evidence") or "").strip():
                violations.append(
                    f"unexercised_fixes[{label}] claims 'exercised' with no evidence — "
                    "name the trade or event in which the mechanism demonstrably acted; "
                    "a deployed fix and a working one are otherwise indistinguishable")
            if v in ("still_unexercised", "regressed", "unverifiable") and \
                    "unexercised" not in flags_blob_of(rc).lower() and \
                    label.lower() not in flags_blob_of(rc).lower():
                violations.append(
                    f"unexercised_fixes[{label}].verdict is {v!r} but neither it nor "
                    "'unexercised' appears in flags_raised[] — a fix we cannot show "
                    "working is a standing risk, not a status line")

    # BL-20260821-SCALPS-HELD-10-TO-100X-THEIR-DESIGN-HORIZON: a leg whose p90 hold is past its own
    # resolution threshold (3x the backtested horizon) is a finding, not a cell in a
    # table. Without this the columns are decorative -- the number renders, nobody is
    # obliged to act, and the row gets rediscovered next review. That IS the failure
    # this backlog row describes: it was found, then found again.
    for r in ((rc.get("execution_capture") or {}).get("per_strategy") or []):
        ratio = r.get("bars_held_p90_ratio")
        if not isinstance(ratio, (int, float)):
            continue
        if ratio > 3.0:
            strat = str(r.get("strategy") or "")
            if strat and strat not in flags_blob_of(rc):
                violations.append(
                    f"execution_capture '{strat}' holds p90 {ratio:.1f}x its backtested "
                    "horizon (threshold 3.0x) but is not in flags_raised[] — over-holding "
                    "past the design horizon is the BL-20260821-SCALPS-HELD-10-TO-100X-THEIR-DESIGN-HORIZON finding, not a status line")

    # Anti-normalization: a >=2-review execution-capture anomaly must be escalated.
    flags_blob = " ".join(str(f) for f in (rc.get("flags_raised") or []))
    for a in ((rc.get("execution_capture") or {}).get("anomalies") or []):
        ro = a.get("reviews_open")
        if isinstance(ro, (int, float)) and ro >= 2:
            strat = str(a.get("strategy") or "")
            if strat and strat not in flags_blob:
                violations.append(
                    f"execution_capture anomaly '{strat}' open {ro} reviews but "
                    "not escalated into flags_raised[]")
    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a consolidated system-report JSON to HTML + MD.")
    ap.add_argument("json_path", help="Path to the consolidated report JSON.")
    ap.add_argument("--out-dir", default="comms/reports", help="Report artifact root (default: comms/reports).")
    ap.add_argument("--no-index", action="store_true", help="Do not update index.json.")
    ap.add_argument("--strict", action="store_true",
                    help="Fail (exit 3, write nothing) if the Review-coverage guard is "
                         "violated: a required review_coverage key is missing/empty, or an "
                         "execution_capture anomaly open >=2 reviews is not in flags_raised[].")
    args = ap.parse_args(argv)

    try:
        report = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read consolidated JSON {args.json_path}: {exc}", file=sys.stderr)
        return 1

    violations = _validate_review_coverage(report)
    if violations:
        for v in violations:
            print(f"::warning:: review-coverage: {v}", file=sys.stderr)
        if args.strict:
            print("ERROR: --strict: review-coverage guard failed — fix the payload "
                  "and re-run (nothing written).", file=sys.stderr)
            return 3

    result = write_report(report, Path(args.out_dir), update_index=not args.no_index)
    print(result["html"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
