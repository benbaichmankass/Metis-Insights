"""Pipeline-result Telegram formatter — extracted from pipeline.py (PR-8 / D1).

Builds the collapsable HTML/plain sections for the per-tick "Pipeline result"
operator message. Pure formatting: no DB reads, no exchange calls, no side
effects. All three helpers here are also re-exported from pipeline.py for
back-compat with existing import sites.
"""
from __future__ import annotations

from typing import Any, Dict

from src.units.ui.telegram_format import Section, kv_block


def _signal_meta(signal: Dict[str, Any]) -> Dict[str, Any]:
    meta = signal.get("meta") if isinstance(signal, dict) else None
    return meta if isinstance(meta, dict) else {}


def _extract_order_package_fields(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Pull entry / sl / tp / direction off *signal* with the same
    precedence as ``_signal_to_order_package``.

    Returns ``None`` for any field that isn't present so the renderer
    can show ``—`` rather than fabricating a value. Used only for the
    operator-facing Telegram envelope; never as a sizing input.
    """
    meta = _signal_meta(signal)
    entry = signal.get("entry_price") or signal.get("price") or meta.get("price")
    sl = signal.get("stop_loss") or meta.get("stop_loss") or meta.get("sl")
    tp = signal.get("take_profit") or meta.get("take_profit") or meta.get("tp")
    side = (signal.get("side") or "").lower()
    direction = "long" if side == "buy" else ("short" if side == "sell" else None)
    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "direction": direction,
        "confidence": signal.get("confidence") or meta.get("confidence"),
    }


def _pipeline_result_sections(
    *, signal: Dict[str, Any], result: Dict[str, Any], strategy: str,
) -> list:
    """Build the collapsable detail sections for the per-tick Telegram
    "Pipeline result" message.

    Sections are stable in shape so the operator can predict where to
    look:

    1. **Strategy** — name + signal confidence + meta keys.
    2. **Order package** — entry / sl / tp / direction / qty when the
       signal carried them; explicit "(not generated)" otherwise.
    3. **Multi-account dispatch** — per-account result list when the
       multi_account path ran.
    4. **Why & next step** — only when status indicates a failure;
       echoes the reason string and the operator-actionable hint
       (e.g. the `set-account-mode` system-action to flip out of dry mode).
    """
    sections: list = []
    status = result.get("status", "unknown")
    reason = result.get("reason")
    meta = _signal_meta(signal)

    # 1. Strategy detail
    strat_rows = [
        ("Strategy", strategy),
        ("Symbol", signal.get("symbol")),
        ("Side", signal.get("side")),
        ("Qty (signal)", signal.get("qty")),
        ("Confidence", signal.get("confidence") or meta.get("confidence")),
    ]
    sections.append(Section(
        summary=f"Strategy — {strategy}",
        body=kv_block(strat_rows),
        priority=10,
    ))

    # 2. Order package detail (entry / sl / tp / direction). The
    # "not generated" body is only meaningful when the strategy
    # actually fired (side ∈ {'buy', 'sell'}) — on no-signal ticks
    # there's no package to show and the section adds noise. CP-18 P3.
    pkg = _extract_order_package_fields(signal)
    side_actionable = str(signal.get("side", "")).strip().lower() in ("buy", "sell")
    if any(v is not None for v in (pkg["entry"], pkg["sl"], pkg["tp"])):
        pkg_rows = [
            ("Direction", pkg["direction"]),
            ("Entry",     pkg["entry"]),
            ("Stop loss", pkg["sl"]),
            ("Take profit", pkg["tp"]),
            ("Confidence", pkg["confidence"]),
        ]
        sections.append(Section(
            summary="Order package — generated",
            body=kv_block(pkg_rows),
            priority=20,
        ))
    elif side_actionable:
        sections.append(Section(
            summary="Order package — not generated",
            body=(
                "Signal did not carry entry/sl/tp at the top level; the "
                "legacy single-client validation path ran instead of the "
                "multi-account dispatch fast-path."
            ),
            priority=20,
        ))

    # 3. Multi-account dispatch (only when that path ran)
    multi = result.get("multi_account_results")
    if isinstance(multi, list) and multi:
        lines = []
        for r in multi:
            if not isinstance(r, dict):
                continue
            acc = r.get("name") or r.get("account") or r.get("account_id") or "?"
            err = r.get("error")
            st = "ok" if err is None else (str(err) or "error")
            qty = r.get("sized_qty") if r.get("sized_qty") is not None else r.get("qty")
            line = f"{acc}: {st}"
            if qty is not None and err is None:
                line += f" qty={qty}"
            lines.append(line)
        sections.append(Section(
            summary=f"Accounts dispatched — {len(multi)}",
            body="\n".join(lines) or "(empty)",
            priority=30,
        ))

    # 4. Failure remediation hint
    if status in {"failed_validation", "failed_exchange",
                  "failed_dispatch", "error"}:
        hint_lines = []
        if reason:
            hint_lines.append(f"Reason: {reason}")
        if reason and "account_mode_dry_run" in str(reason):
            hint_lines.append(
                "Action: this account is in dry_run mode "
                "(config/accounts.yaml `mode: dry_run`). Flip it via the "
                "`set-account-mode` system-action (account=<name> mode=live) "
                "— the only sanctioned wire — to start live execution."
            )
        sections.append(Section(
            summary=f"Why & next step — {status}",
            body="\n".join(hint_lines) or "(no detail)",
            priority=5,
        ))

    return sections
