"""`market_sequences` dataset family — causal windows over `market_features` (M19 T1.1).

A thin wrapper family that reads an already-built ``market_features`` dataset and
attaches a per-row causal ``seq_window`` (shape ``(seq_len, n_features)``) via
:func:`ml.datasets.sequence_window.build_causal_windows`. This is the dataset a
deep sequence model (the TCN, :class:`ml.trainers.torch_sequence.TorchSequenceTrainer`)
trains on.

**Why a separate family (not a column on `market_features`):** the window
materialization is isolated here so the shared, load-bearing ``market_features``
builder that ~40 tabular manifests depend on is never touched. This family
*consumes* market_features's output the same way market_features consumes
``market_raw`` (via a ``*_path`` kwarg pointing at the sibling dataset dir).

Build:
    python -m ml.datasets build market_sequences \
        --output-dir <root> --version v001 --source market_features \
        --symbol-scope BTCUSDT --timeframe 15m \
        market_features_path=<root>/market_features/BTCUSDT/15m/v002 \
        seq_len=64 feature_columns=log_return,rolling_log_return_vol,hour_of_day,dayofweek

**Coupling contract:** the windowed ``feature_columns`` (and their ORDER) must
match the consuming manifest's ``trainer_config.feature_columns`` exactly — the
trainer width-checks ``(seq_len, n_features)`` but cannot detect a re-ordering, so
keep the two in lockstep. The default below is the canonical BTC-15m regime set.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from ..builder import DatasetBuilder
from ..metadata import LeakageStatus
from ..sequence_window import SEQ_WINDOW_COLUMN, build_causal_windows

_FAMILY = "market_sequences"

# Canonical per-bar feature channels for the BTC-15m regime TCN. Must match the
# manifest's trainer_config.feature_columns in order (see the coupling contract).
DEFAULT_FEATURE_COLUMNS = ["log_return", "rolling_log_return_vol", "hour_of_day", "dayofweek"]

# Carried through from market_features onto each windowed row. `seq_window` is
# added by this builder; the rest identify the bar + its label for the split /
# trainer / evaluator.
_CARRY_COLUMNS = ("ts", "symbol", "timeframe")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    data_path = path / "data.jsonl"
    if not data_path.is_file():
        raise FileNotFoundError(
            f"market_features data.jsonl not found at {data_path}; "
            "build a market_features dataset first via "
            "`python -m ml.datasets build market_features ...`"
        )
    rows: list[dict[str, Any]] = []
    with data_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                rows.append(json.loads(line))
    return rows


class MarketSequencesBuilder(DatasetBuilder):
    family: ClassVar[str] = _FAMILY
    builder_version: ClassVar[str] = "v1"
    # The window is backward-only (bars <= i); it introduces no forward leakage.
    # The forward LABEL horizon is handled by the CV embargo, same as the tabular
    # market_features head. Inherited passthrough of market_features's own status.
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.PASSED
    label_version: ClassVar[str] = "regime-3class-v1"
    schema: ClassVar[Mapping[str, type]] = {
        "ts": str,
        "symbol": str,
        "timeframe": str,
        "regime_label": str,
        "direction_label": str,
        SEQ_WINDOW_COLUMN: list,
    }

    def iter_rows(
        self,
        *,
        market_features_path: Path | str,
        seq_len: int = 64,
        feature_columns: str | None = None,
        target_columns: str = "regime_label,direction_label",
        symbol_scope: str | None = None,
        timeframe: str | None = None,
        **_ignored: Any,
    ) -> Iterator[Mapping[str, Any]]:
        cols = (
            [c.strip() for c in feature_columns.split(",") if c.strip()]
            if feature_columns
            else list(DEFAULT_FEATURE_COLUMNS)
        )
        targets = [c.strip() for c in str(target_columns).split(",") if c.strip()]
        src_rows = _load_jsonl(Path(market_features_path))
        windowed = build_causal_windows(
            src_rows, feature_columns=cols, seq_len=int(seq_len)
        )
        for row in windowed:
            out: dict[str, Any] = {
                SEQ_WINDOW_COLUMN: row[SEQ_WINDOW_COLUMN],
            }
            for c in _CARRY_COLUMNS:
                if c in row and row[c] is not None:
                    out[c] = row[c]
            for t in targets:
                if t in row and row[t] is not None:
                    out[t] = row[t]
            yield out
