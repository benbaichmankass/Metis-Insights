"""Dataset family registry (WS3 + WS5-A + WS5-B-PART-1 + WS5-B-PART-2 + WS5-C + WS5-C-FU + WS5-D + WS5-E + WS5-F)."""
from __future__ import annotations

from typing import Mapping, Type

from .builder import DatasetBuilder
from .families.account_context import AccountContextBuilder
from .families.backtest_results import BacktestResultsBuilder
from .families.conviction_meta import ConvictionMetaBuilder
from .families.execution_quality import ExecutionQualityBuilder
from .families.exit_candidates import ExitCandidatesBuilder
from .families.market_features import MarketFeaturesBuilder
from .families.market_raw import MarketRawBuilder
from .families.review_journal import ReviewJournalBuilder
from .families.setup_candidates import SetupCandidatesBuilder
from .families.setup_labels import SetupLabelsBuilder
from .families.setup_labels_audit import SetupLabelsAuditBuilder
from .families.trade_outcomes import TradeOutcomesBuilder

FAMILY_REGISTRY: Mapping[str, Type[DatasetBuilder]] = {
    AccountContextBuilder.family: AccountContextBuilder,
    BacktestResultsBuilder.family: BacktestResultsBuilder,
    ConvictionMetaBuilder.family: ConvictionMetaBuilder,
    ExecutionQualityBuilder.family: ExecutionQualityBuilder,
    ExitCandidatesBuilder.family: ExitCandidatesBuilder,
    MarketFeaturesBuilder.family: MarketFeaturesBuilder,
    MarketRawBuilder.family: MarketRawBuilder,
    ReviewJournalBuilder.family: ReviewJournalBuilder,
    SetupCandidatesBuilder.family: SetupCandidatesBuilder,
    SetupLabelsBuilder.family: SetupLabelsBuilder,
    SetupLabelsAuditBuilder.family: SetupLabelsAuditBuilder,
    TradeOutcomesBuilder.family: TradeOutcomesBuilder,
}


def list_families() -> list[str]:
    return sorted(FAMILY_REGISTRY.keys())


def get_builder(family: str) -> DatasetBuilder:
    try:
        cls = FAMILY_REGISTRY[family]
    except KeyError as e:
        raise KeyError(
            f"unknown family {family!r}; known: {list_families()}"
        ) from e
    return cls()
