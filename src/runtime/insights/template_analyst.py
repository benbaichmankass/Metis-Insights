"""Rule-based template analyst (M13 S2 — provider-free default mode).

Produces the same envelope shape the LLM-mode generator does
(``{summary_md, grade, signals}``) but without calling any external
API. The dashboard surface, the cache file format, the
``insights_history`` rows, and the ``insights_usage`` rows are all
unchanged — only the prose-generation step is swapped.

Why this exists:
  * Provider-independence — the analyst keeps running whether or not
    an Anthropic / Groq / OpenAI account has credit.
  * Determinism — every claim in the output is computed from a row
    in the input, never paraphrased by a stochastic model. Zero
    hallucination risk by construction.
  * Zero cost — the budget gate becomes vestigial; ``estimated_cost_usd``
    is always 0 in the usage table.

Grade rule (applies to every endpoint):
  * ``good``    — non-empty data window, net PnL ≥ 0, win rate ≥ 50%.
  * ``watch``   — net PnL < 0 OR win rate < 50% OR (no closed trades
                  but pipeline appears alive).
  * ``concern`` — net PnL ≤ -100 USD OR win rate ≤ 25% (≥4 trades)
                  OR critical health checks failing.

When the rule fires no useful data (zero trades AND zero packages AND
no health snapshot), the prose says so explicitly — better than a
made-up "calm market" narrative.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MODEL_ID = "template:v1"


# ---------------------------------------------------------------------------
# Grade rules
# ---------------------------------------------------------------------------


def _grade(
    *,
    trade_count: int,
    win_rate: float | None,
    net_pnl: float,
    health_failing: bool = False,
) -> str:
    if health_failing or net_pnl <= -100.0:
        return "concern"
    if trade_count >= 4 and (win_rate is not None and win_rate <= 0.25):
        return "concern"
    if net_pnl < 0:
        return "watch"
    if win_rate is not None and win_rate < 0.5 and trade_count >= 4:
        return "watch"
    if trade_count == 0:
        # No trades is ambiguous — could be calm market or stuck pipeline.
        # Default to watch so the operator notices.
        return "watch"
    return "good"


# ---------------------------------------------------------------------------
# Number helpers
# ---------------------------------------------------------------------------


def _fmt_usd(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _pct(numer: int, denom: int) -> str:
    if denom <= 0:
        return "n/a"
    return f"{numer / denom * 100:.1f}%"


def _short_id(pkg_id: str | None) -> str:
    if not pkg_id or not isinstance(pkg_id, str):
        return "?"
    return pkg_id[:8] if len(pkg_id) > 8 else pkg_id


# ---------------------------------------------------------------------------
# Per-endpoint templates
# ---------------------------------------------------------------------------


def summary_template(data: dict[str, Any]) -> dict[str, Any]:
    rc = data.get("row_counts") or {}
    rows = data.get("rows") or {}
    trades = rows.get("recent_trades") or []
    pkgs = rows.get("recent_packages") or []

    closed = [t for t in trades if t.get("status") == "closed"]
    pnls = [float(t.get("pnl") or 0.0) for t in closed]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    net_pnl = sum(pnls)
    win_rate = (wins / len(closed)) if closed else None

    by_strategy: Counter[str] = Counter()
    for t in trades:
        if t.get("strategy_name"):
            by_strategy[str(t["strategy_name"])] += 1

    by_exit: Counter[str] = Counter()
    for t in closed:
        if t.get("exit_reason"):
            by_exit[str(t["exit_reason"])] += 1

    worst = min(closed, key=lambda t: float(t.get("pnl") or 0.0), default=None) if closed else None
    best = max(closed, key=lambda t: float(t.get("pnl") or 0.0), default=None) if closed else None

    lines = [
        "## Overall — last 24 hours",
        "",
        f"- Trades (total / closed): **{int(rc.get('trades', 0))} / {len(closed)}**",
        f"- Order packages: **{int(rc.get('order_packages', 0))}**",
        f"- Signals (buy/sell audit events): **{int(rc.get('signals', 0))}**",
        f"- Net PnL on closed trades: **{_fmt_usd(net_pnl)}**",
        f"- Win rate: **{_pct(wins, len(closed))}** ({wins}W / {losses}L)",
    ]

    if by_strategy:
        top_strats = ", ".join(f"`{name}` ({n})" for name, n in by_strategy.most_common(5))
        lines += ["", f"**Active strategies:** {top_strats}"]

    if by_exit:
        top_exits = ", ".join(f"`{r}` ({n})" for r, n in by_exit.most_common(3))
        lines += [f"**Top exit reasons:** {top_exits}"]

    if best is not None and float(best.get("pnl") or 0.0) > 0:
        lines += [
            "",
            (
                f"**Best:** trade #{best.get('id')} — "
                f"`{best.get('strategy_name')}` {best.get('symbol')} "
                f"{best.get('direction')}, PnL {_fmt_usd(float(best.get('pnl') or 0.0))}."
            ),
        ]
    if worst is not None and float(worst.get("pnl") or 0.0) < 0:
        lines += [
            (
                f"**Worst:** trade #{worst.get('id')} — "
                f"`{worst.get('strategy_name')}` {worst.get('symbol')} "
                f"{worst.get('direction')}, PnL {_fmt_usd(float(worst.get('pnl') or 0.0))}, "
                f"exit `{worst.get('exit_reason') or '?'}`."
            )
        ]

    if not trades and not pkgs:
        lines += [
            "",
            "_No trades or order packages in the last 24h._ Pipeline may be quiet "
            "or stuck — check `/api/bot/stats` heartbeat.",
        ]

    signals: list[dict[str, Any]] = []
    if closed and win_rate is not None and win_rate <= 0.25:
        signals.append({
            "kind": "low_win_rate",
            "severity": "high",
            "note": f"Win rate {win_rate * 100:.1f}% over {len(closed)} closed trades.",
        })
    if net_pnl <= -100.0:
        signals.append({
            "kind": "drawdown_threshold",
            "severity": "high",
            "note": f"Net PnL {_fmt_usd(net_pnl)} exceeds the -$100 concern threshold.",
        })
    if not trades and not pkgs:
        signals.append({
            "kind": "no_activity",
            "severity": "medium",
            "note": "No trades or order packages logged in the last 24h.",
        })

    grade = _grade(
        trade_count=len(closed),
        win_rate=win_rate,
        net_pnl=net_pnl,
    )
    return {"summary_md": "\n".join(lines), "grade": grade, "signals": signals}


def recent_template(data: dict[str, Any], limit: int) -> dict[str, Any]:
    rows = (data.get("rows") or {}).get("trades") or []
    closed = [t for t in rows if t.get("status") == "closed"]
    pnls = [float(t.get("pnl") or 0.0) for t in closed]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    net_pnl = sum(pnls)
    win_rate = (wins / len(closed)) if closed else None

    lines = [
        f"## Last {len(rows)} closed trades",
        "",
        f"- Net PnL: **{_fmt_usd(net_pnl)}** — {wins}W / {losses}L "
        f"(win rate {_pct(wins, len(closed))})",
        "",
        "| # | strategy | symbol | dir | pnl | exit |",
        "|---|----------|--------|-----|-----|------|",
    ]
    for t in rows[:limit]:
        lines.append(
            f"| {t.get('id')} | `{t.get('strategy_name') or '?'}` "
            f"| {t.get('symbol') or '?'} | {t.get('direction') or '?'} "
            f"| {_fmt_usd(float(t.get('pnl') or 0.0))} "
            f"| `{t.get('exit_reason') or '?'}` |"
        )

    if not rows:
        lines.append("_No closed trades available._")

    # Detect a losing streak in the last 5 entries.
    signals: list[dict[str, Any]] = []
    tail = [float(t.get("pnl") or 0.0) for t in rows[:5]]
    if len(tail) >= 3 and all(p < 0 for p in tail[:3]):
        signals.append({
            "kind": "losing_streak",
            "severity": "high",
            "note": f"Last 3 closed trades all negative: {[_fmt_usd(p) for p in tail[:3]]}.",
        })

    grade = _grade(trade_count=len(closed), win_rate=win_rate, net_pnl=net_pnl)
    return {"summary_md": "\n".join(lines), "grade": grade, "signals": signals}


def strategy_template(name: str, data: dict[str, Any]) -> dict[str, Any]:
    rc = data.get("row_counts") or {}
    rows = (data.get("rows") or {}).get("trades") or []
    meta = data.get("meta") or {}

    closed = [t for t in rows if t.get("status") == "closed"]
    pnls = [float(t.get("pnl") or 0.0) for t in closed]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    net_pnl = float(meta.get("total_pnl_window") or sum(pnls))
    win_rate = (wins / len(closed)) if closed else None

    by_exit: Counter[str] = Counter()
    for t in closed:
        if t.get("exit_reason"):
            by_exit[str(t["exit_reason"])] += 1
    by_symbol: Counter[str] = Counter()
    for t in rows:
        if t.get("symbol"):
            by_symbol[str(t["symbol"])] += 1

    lines = [
        f"## Strategy `{name}` — last 7 days",
        "",
        f"- Trades (total / closed): **{int(rc.get('trades', 0))} / {len(closed)}**",
        f"- Order packages: **{int(rc.get('order_packages', 0))}**",
        f"- Net PnL (window): **{_fmt_usd(net_pnl)}**",
        f"- Win rate: **{_pct(wins, len(closed))}** ({wins}W / {losses}L)",
    ]
    if by_symbol:
        sym_str = ", ".join(f"`{s}` ({n})" for s, n in by_symbol.most_common(5))
        lines += ["", f"**Symbols traded:** {sym_str}"]
    if by_exit:
        ex_str = ", ".join(f"`{r}` ({n})" for r, n in by_exit.most_common(3))
        lines += [f"**Top exit reasons:** {ex_str}"]
    if not rows:
        lines += [
            "",
            f"_No trades for `{name}` in the last 7 days._",
        ]

    signals: list[dict[str, Any]] = []
    if closed and win_rate is not None and win_rate <= 0.30:
        signals.append({
            "kind": "low_win_rate",
            "severity": "high",
            "note": f"`{name}` win rate {win_rate * 100:.1f}% over {len(closed)} closed trades.",
        })
    # One exit_reason dominating > 70% of closes is unusual.
    if by_exit and len(closed) >= 5:
        top_reason, top_count = by_exit.most_common(1)[0]
        if top_count / len(closed) >= 0.70:
            signals.append({
                "kind": "exit_reason_skew",
                "severity": "medium",
                "note": (
                    f"`{top_reason}` accounts for {top_count}/{len(closed)} closes "
                    f"({top_count / len(closed) * 100:.0f}%)."
                ),
            })

    grade = _grade(trade_count=len(closed), win_rate=win_rate, net_pnl=net_pnl)
    return {"summary_md": "\n".join(lines), "grade": grade, "signals": signals}


def health_template(data: dict[str, Any]) -> dict[str, Any]:
    rows = data.get("rows") or {}
    snap = rows.get("snapshot") or {}
    meta = data.get("meta") or {}
    age = meta.get("age_seconds")

    checks = snap.get("checks") or {}
    if not isinstance(checks, dict):
        checks = {}
    fail = [name for name, c in checks.items()
            if isinstance(c, dict) and c.get("status") not in (None, "ok", "pass", "running")]
    ok_count = sum(
        1 for c in checks.values()
        if isinstance(c, dict) and c.get("status") in ("ok", "pass", "running")
    )

    lines = ["## Health snapshot"]
    if not meta.get("present"):
        lines += ["", "_No `artifacts/health/latest.json` present._"]
    else:
        ts = snap.get("timestamp") or "?"
        lines += [
            "",
            f"- Snapshot timestamp: `{ts}`",
            f"- Age: **{age}s**" if age is not None else "- Age: unknown",
            f"- Checks (ok / total): **{ok_count} / {len(checks)}**",
        ]
        if fail:
            lines += [f"- **Failing checks:** {', '.join(f'`{n}`' for n in fail)}"]
        else:
            lines += ["- All checks green."]

    signals: list[dict[str, Any]] = []
    if fail:
        signals.append({
            "kind": "health_failing",
            "severity": "high",
            "note": f"{len(fail)} check(s) not ok: {', '.join(fail)}.",
        })
    if age is not None and age > 3600:
        signals.append({
            "kind": "stale_snapshot",
            "severity": "medium",
            "note": f"Health snapshot is {age}s old (>1h).",
        })

    grade = _grade(
        trade_count=0,
        win_rate=None,
        net_pnl=0.0,
        health_failing=bool(fail),
    )
    if not meta.get("present"):
        grade = "watch"
    return {"summary_md": "\n".join(lines), "grade": grade, "signals": signals}


# ---------------------------------------------------------------------------
# Dispatcher used by the generator
# ---------------------------------------------------------------------------


def render(
    endpoint: str,
    data: dict[str, Any],
    *,
    strategy_name: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if endpoint == "summary":
        return summary_template(data)
    if endpoint == "recent":
        return recent_template(data, limit)
    if endpoint == "strategy":
        if not strategy_name:
            raise ValueError("strategy endpoint requires strategy_name")
        return strategy_template(strategy_name, data)
    if endpoint == "health":
        return health_template(data)
    raise ValueError(f"unknown endpoint: {endpoint}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
