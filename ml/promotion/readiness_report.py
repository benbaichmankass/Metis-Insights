"""Promotion-readiness report (S-MLOPT-S18, M14 Phase 4.3, 2026-06-07).

Closes the loop on Phase 0.4 + Phase 4.2: every model carries a live
PASS/FAIL gate status that the operator can read at a glance instead of
running ``gate-check`` per model and stitching the results together.

The generator is a thin orchestrator on top of
``ml.promotion.stage_guard.run_stage_guard`` — it runs the guard across
the whole registry (regime profile auto-applied via ``thresholds_for``,
OOS-edge baseline auto-selected for regime heads in
``run_stage_guard``), groups the resulting proposals into
``promote`` / ``demote`` / ``hold`` buckets, and renders a JSON +
Markdown report.

**Reports only — never auto-promotes.** The ``shadow → advisory`` flip
stays Tier-3 (operator-gated): a ``promote`` proposal here is an
invitation, not an action. The companion orchestrator
(``scripts/ops/run_promotion_readiness.sh``) consumes the JSON and pings
the operator when any shadow model crosses the ready bar — that ping is
the entire automation surface.

Pure decision-support: this module reads the registry / shadow log /
trade DB and writes a report file. It never registers a model, edits a
manifest, or touches the order path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .gates import GateThresholds
from .stage_guard import Proposal, run_stage_guard


@dataclass(frozen=True)
class ReadinessReport:
    """All proposals for a single sweep, plus the summary the orchestrator pings on."""

    generated_at_utc: datetime
    proposals: tuple[Proposal, ...]
    datasets_root_used: str | None

    @property
    def promote_ready(self) -> tuple[Proposal, ...]:
        return tuple(p for p in self.proposals if p.action == "promote")

    @property
    def demote_proposed(self) -> tuple[Proposal, ...]:
        return tuple(p for p in self.proposals if p.action == "demote")

    @property
    def held(self) -> tuple[Proposal, ...]:
        return tuple(p for p in self.proposals if p.action == "hold")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at_utc": self.generated_at_utc.isoformat(timespec="seconds"),
            "datasets_root_used": self.datasets_root_used,
            "summary": {
                "total": len(self.proposals),
                "promote": [p.model_id for p in self.promote_ready],
                "demote": [p.model_id for p in self.demote_proposed],
                "hold_count": len(self.held),
            },
            "proposals": [p.to_dict() for p in self.proposals],
        }


def build_readiness_report(
    *,
    registry_root: Path | str,
    db_path: Path | str,
    shadow_log: Path | str,
    backfill_log: Path | str | None = None,
    thresholds: GateThresholds | None = None,
    reference_days: float = 30.0,
    current_days: float = 7.0,
    include_demo: bool = False,
    datasets_root: Path | str | None = None,
    now_utc: datetime | None = None,
) -> ReadinessReport:
    """Run the stage guard across the registry and wrap the result.

    ``datasets_root`` is forwarded so shadow-stage models get a real
    purged-WF-CV OOS-edge gate evaluated; without it, those models hold
    on ``oos_edge`` insufficient-data — the report will surface that
    plainly rather than silently treating the gate as "passed".
    """
    proposals = run_stage_guard(
        registry_root=registry_root,
        db_path=db_path,
        shadow_log=shadow_log,
        backfill_log=backfill_log,
        thresholds=thresholds,
        reference_days=reference_days,
        current_days=current_days,
        include_demo=include_demo,
        datasets_root=datasets_root,
    )
    return ReadinessReport(
        generated_at_utc=now_utc or datetime.now(timezone.utc),
        proposals=tuple(proposals),
        datasets_root_used=str(datasets_root) if datasets_root is not None else None,
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _gate_summary_line(proposal: Proposal) -> str:
    """One-line gate verdict: which gates blocked / what edge we have."""
    gate_report = proposal.evidence.get("gate_report") if proposal.evidence else None
    if not isinstance(gate_report, dict):
        return ""
    blocking = gate_report.get("blocking") or []
    if blocking:
        return f"blocking: {', '.join(blocking)}"
    return "all gates pass"


def _proposal_row(p: Proposal) -> str:
    target = p.proposed_stage or "—"
    return (
        f"- **{p.model_id}** ({p.current_stage} → {target}): "
        f"{p.reasons[0] if p.reasons else '(no reason)'}"
    )


def format_markdown(report: ReadinessReport) -> str:
    """Operator-readable Markdown — the artifact the dashboard renders.

    Same shape as the daily backtest SUMMARY.md the trainer already
    publishes: a status header + sections by action, so the operator
    can skim the promote section first and only read further if there's
    a demote/hold worth investigating.
    """
    lines: list[str] = []
    lines.append(
        f"# Promotion readiness — {report.generated_at_utc.isoformat(timespec='seconds')}"
    )
    lines.append("")
    lines.append(
        f"_{len(report.proposals)} models reviewed_ — "
        f"**{len(report.promote_ready)} promote**, "
        f"**{len(report.demote_proposed)} demote**, "
        f"**{len(report.held)} hold**."
    )
    if report.datasets_root_used is None:
        lines.append("")
        lines.append(
            "> ⚠️ No `datasets_root` supplied — shadow-stage models hold "
            "on `oos_edge` insufficient-data. Run on the trainer VM to "
            "compute the offline champion-challenger evidence."
        )
    lines.append("")
    lines.append("## Promote (shadow → advisory)")
    if report.promote_ready:
        for p in report.promote_ready:
            lines.append(_proposal_row(p))
    else:
        lines.append("- _none_")
    lines.append("")
    lines.append("## Demote")
    if report.demote_proposed:
        for p in report.demote_proposed:
            lines.append(_proposal_row(p))
            for reason in p.reasons[1:]:
                lines.append(f"  - {reason}")
    else:
        lines.append("- _none_")
    lines.append("")
    lines.append("## Hold")
    if report.held:
        for p in report.held:
            tail = _gate_summary_line(p)
            extra = f" — {tail}" if tail else ""
            lines.append(
                f"- **{p.model_id}** ({p.current_stage}){extra}"
            )
    else:
        lines.append("- _none_")
    lines.append("")
    lines.append(
        "_Reports only. Promotion past `shadow` is Tier-3 (operator-gated): "
        "run `python -m ml promote-stage <id> --new-stage advisory ...` to act._"
    )
    return "\n".join(lines)


def format_ping_message(report: ReadinessReport) -> str | None:
    """Short Telegram message — emitted only when something is actionable.

    Returns ``None`` when the report is uneventful (everything held, no
    demotes); the orchestrator uses ``None`` as "skip the ping" so a
    quiet day does not spam the operator chat.
    """
    promote = report.promote_ready
    demote = report.demote_proposed
    if not promote and not demote:
        return None
    bits: list[str] = []
    if promote:
        ids = ", ".join(p.model_id for p in promote)
        bits.append(f"PROMOTE-READY: {ids}")
    if demote:
        ids = ", ".join(p.model_id for p in demote)
        bits.append(f"DEMOTE-PROPOSED: {ids}")
    return " | ".join(bits) + " (see promotion-readiness report)"


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def write_report(
    report: ReadinessReport,
    output_dir: Path | str,
    *,
    json_name: str = "report.json",
    md_name: str = "SUMMARY.md",
) -> tuple[Path, Path]:
    """Persist the report as `report.json` + `SUMMARY.md` under ``output_dir``.

    The directory is created if missing. Returns ``(json_path, md_path)``.
    Caller decides the date-bucketing convention (the orchestrator nests
    under ``runtime_logs/trainer_mirror/promotion_readiness/<UTC-date>/``
    so the existing trainer-mirror rsync picks it up).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / json_name
    md_path = out / md_name
    json_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    md_path.write_text(format_markdown(report) + "\n")
    return json_path, md_path
