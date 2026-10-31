"""`leg_corpus` dataset family (ML-LINE-S1-CORPUS): the money legs' Stage-0 trades, pooled by FAMILY, `leg` a feature.

Reads the corpora ``scripts/research/leg_corpus_s1.py`` writes (``<corpus_dir>/<family>/<leg>/{trades.jsonl,meta.json}``)
and emits one row per harness trade. A leg's own corpus is a few hundred trades, so a per-leg model is under-powered;
the family is the training unit and ``leg`` / ``symbol`` are columns (design: docs/plans/ml-pipeline-design-2026-10-29.md
section 1.1). Every gate still reads per leg as well as pooled.

Honesty rules baked in:
  * a leg whose meta.json is not ``state: ok``, or whose bt.json timeframe differs from the config timeframe
    (``timeframe_equal`` false), is SKIPPED, never pooled -- the skip is visible in ``skipped`` on the builder;
  * the label ``net_r`` is the harness's net R of the exit actually traded (fee + slippage + funding); the three cost
    columns ride along as numbers, a missing one stays None (never 0);
  * leakage: rows carry entry-time columns and the outcome ``net_r`` / ``gross_r`` / ``mfe_r`` / ``exit_*`` / ``cost_*``.
    Outcome columns are NOT features -- ``FEATURE_COLUMNS`` is the closed entry-time list a manifest may name.
Read-only; no network; no live DB.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from ..builder import DatasetBuilder
from ..metadata import LeakageStatus

#: entry-time columns a model may use (everything else on a row is identity, outcome or cost).
FEATURE_COLUMNS = ("leg", "symbol", "timeframe", "is_long", "confidence", "adx_at_entry", "regime_weight")
_NUM = ("confidence", "adx_at_entry", "regime_weight", "net_r", "gross_r", "mfe_r",
        "cost_fee_r", "cost_slippage_r", "cost_funding_r")


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


class LegCorpusBuilder(DatasetBuilder):
    family: ClassVar[str] = "leg_corpus"
    builder_version: ClassVar[str] = "v1"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.NOT_APPLICABLE
    label_version: ClassVar[str] = "net_r_exit_as_traded_cost_net_v1"
    default_symbol_scope: ClassVar[str] = "money_legs"
    default_timeframe: ClassVar[str] = "per_family"
    schema: ClassVar[Mapping[str, type]] = {
        "corpus_family": str, "leg": str, "symbol": str, "timeframe": str, "generated_at": str,
        "entry_time": str, "exit_time": str, "is_long": int, "confidence": float, "adx_at_entry": float,
        "regime_weight": float, "exit_reason": str, "net_r": float, "gross_r": float, "mfe_r": float,
        "cost_fee_r": float, "cost_slippage_r": float, "cost_funding_r": float,
    }

    def __init__(self) -> None:
        self.skipped: dict[str, str] = {}

    def iter_rows(self, *, corpus_dir: Path | str, families: str | None = None, **_: Any) -> Iterator[Mapping[str, Any]]:
        root = Path(corpus_dir)
        if not root.is_dir():
            raise FileNotFoundError(f"leg corpus dir not found: {root}")
        want = {f.strip() for f in families.split(",") if f.strip()} if families else None
        self.skipped = {}
        for fam_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            if want is not None and fam_dir.name not in want:
                continue
            for leg_dir in sorted(p for p in fam_dir.iterdir() if p.is_dir()):
                meta_p, tr_p = leg_dir / "meta.json", leg_dir / "trades.jsonl"
                if not meta_p.is_file() or not tr_p.is_file():
                    self.skipped[leg_dir.name] = "no meta/trades"
                    continue
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
                if meta.get("state") != "ok":
                    self.skipped[leg_dir.name] = f"state {meta.get('state')}"
                    continue
                if not meta.get("timeframe_equal"):
                    self.skipped[leg_dir.name] = "bt timeframe != config timeframe"
                    continue
                for ln in tr_p.read_text(encoding="utf-8").splitlines():
                    if not ln.strip():
                        continue
                    t = json.loads(ln)
                    net = _num(t.get("net_r"))
                    if net is None:  # no label -> not a training row
                        continue
                    direction = str(t.get("direction", "")).lower()
                    row: dict[str, Any] = {
                        "corpus_family": fam_dir.name, "leg": meta["leg"], "symbol": meta.get("symbol") or "",
                        "timeframe": str(meta.get("config_timeframe") or ""), "generated_at": meta.get("generated_at") or "",
                        "entry_time": str(t.get("entry_time") or ""), "exit_time": str(t.get("exit_time") or ""),
                        "is_long": 1 if direction in ("long", "buy", "1") else 0,
                        "exit_reason": str(t.get("exit_reason") or ""),
                    }
                    for k in _NUM:
                        row[k] = _num(t.get(k))
                    yield row
