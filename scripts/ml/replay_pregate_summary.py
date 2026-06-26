#!/usr/bin/env python3
"""Render a markdown summary of a replay pre-gate fleet report (RG3).

Reads the JSON written by ``scripts/ml/replay_pregate_fleet.py`` (or a
``--json`` dump from ``replay_pregate.py`` for a single model) and prints a
compact markdown table to stdout. Used by the ``replay-pregate-nightly``
workflow to render ``runtime_logs/replay_pregate/latest.md`` and the
issue-comment body. Stdlib-only.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List


def _verdict_emoji(v: str) -> str:
    return {
        "TRUSTWORTHY_SIGNAL": "🟢",
        "NO_EDGE": "🟡",
        "ANTI_PREDICTIVE": "🔴",
    }.get(v, "⚪")


def _rows(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Fleet report: {"results": [...]}; single-model report: the object itself.
    if isinstance(report.get("results"), list):
        return report["results"]
    return [report]


def render(report: Dict[str, Any]) -> str:
    rows = _rows(report)
    out: List[str] = []
    gen = report.get("generated_at", "")
    if gen:
        out.append(f"_Generated: {gen}_\n")
    out.append("| Model | Sym/TF | n | base | AUC | brier_lift | Verdict |")
    out.append("|---|---|---:|---:|---:|---:|---|")
    for r in rows:
        ov = r.get("overall") or {}
        auc = ov.get("auc")
        bl = ov.get("brier_lift")
        verdict = r.get("auc_verdict", "—")
        out.append(
            f"| {r.get('model_id','—')} "
            f"| {r.get('symbol','—')}/{r.get('timeframe','—')} "
            f"| {ov.get('n', r.get('n_scored','—'))} "
            f"| {ov.get('base_rate','—')} "
            f"| {auc if auc is not None else '—'} "
            f"| {bl if bl is not None else '—'} "
            f"| {_verdict_emoji(verdict)} {verdict} |"
        )
    errors = report.get("errors") or []
    if errors:
        out.append("\n**Errors:**")
        for e in errors:
            out.append(f"- `{e.get('model_id','?')}`: {e.get('error','?')}")
    out.append(
        "\n_🟢 AUC≥0.55 (discriminates) · 🟡 0.45–0.55 (no edge) · "
        "🔴 <0.45 (anti-predictive). A head that fails here should not earn a "
        "live-shadow slot; one already shadow-soaking that scores 🔴 is a "
        "demotion candidate._"
    )
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: replay_pregate_summary.py <report.json>", file=sys.stderr)
        return 2
    report = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
