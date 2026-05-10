"""`market_raw` dataset family (S-AI-WS5-B-PART-1).

Dispatches to the adapter named in `--adapter` (default `csv`)
and emits canonical `market_raw` rows under the standard versioned
layout.

Leakage discipline: `market_raw` carries no labels;
`leakage_test_status: n/a`. Downstream `market_features` / regime
label datasets that derive features from these bars own their own
leakage tests.
"""
from __future__ import annotations

from typing import Any, ClassVar, Iterator, Mapping

from ..adapters import CANONICAL_SCHEMA, get_adapter
from ..builder import DatasetBuilder
from ..metadata import LeakageStatus


class MarketRawBuilder(DatasetBuilder):
    family: ClassVar[str] = "market_raw"
    builder_version: ClassVar[str] = "v1"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.NOT_APPLICABLE
    label_version: ClassVar[str] = "n/a"
    schema: ClassVar[Mapping[str, type]] = CANONICAL_SCHEMA

    def iter_rows(
        self,
        *,
        adapter: str = "csv",
        **adapter_kwargs: Any,
    ) -> Iterator[Mapping[str, Any]]:
        adapter_inst = get_adapter(adapter)
        yield from adapter_inst.iter_bars(**adapter_kwargs)
