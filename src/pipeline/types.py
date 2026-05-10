"""Stage I/O types for the canonical trade pipeline (WS2).

Frozen dataclasses with light invariant checks in `__post_init__`.
No external dependencies beyond the stdlib.

These types are additive: WS2 lands them without changing the live
runtime call sites. Migration of `OrderPackage` and the existing
coordinator path onto these types is scoped to a follow-up sprint and
gated on operator approval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class StageName(str, Enum):
    """Locked stage names for the canonical trade pipeline.

    Numbering follows `docs/AI-TRADERS-ROADMAP.md` Workstream 2 and
    `docs/architecture/ai-model-platform.md` § Stage map. The
    8-step pipeline in `docs/ARCHITECTURE-CANONICAL.md` maps onto
    this list as documented in `docs/pipeline/stage-contracts.md`.
    """

    INGEST = "ingest"            # 1. Market and account ingest
    NORMALIZE = "normalize"      # 2. Normalization
    CONTEXT = "context"          # 3. Context assembly
    SETUP = "setup"              # 4. Setup detection
    SCORE = "score"              # 5. Opportunity scoring
    RISK = "risk"                # 6. Risk gating (deterministic-only)
    PACKAGE = "package"          # 7. Execution packaging
    ROUTE = "route"              # 8. Broker routing
    CAPTURE = "capture"          # 9. Post-trade capture
    REVIEW = "review"            # 10. Review and feedback


class DecisionVerdict(str, Enum):
    ALLOW = "allow"
    VETO = "veto"
    SCORE_ONLY = "score_only"


class RejectionSource(str, Enum):
    """Which layer produced a VETO.

    DETERMINISTIC rejections are immutable: no upstream allow can
    override them. MODEL rejections are advisory and may be overridden
    only via deployment-tier policy (see WS7) plus explicit operator
    approval. The pipeline must never treat the two as equivalent.
    """

    DETERMINISTIC = "deterministic"
    MODEL = "model"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StageDecision:
    """Outcome of a single pipeline stage.

    `verdict=VETO` requires a `rejection_source`. `score`, when set,
    must lie in `[0, 1]`.
    """

    stage: StageName
    verdict: DecisionVerdict
    rejection_source: RejectionSource | None = None
    reason: str = ""
    score: float | None = None
    model_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verdict == DecisionVerdict.VETO and self.rejection_source is None:
            raise ValueError("VETO decisions must record a rejection_source")
        if self.score is not None and not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score must be in [0,1] when set; got {self.score}")


@dataclass(frozen=True)
class TradeCandidate:
    """Canonical trade-opportunity object.

    Produced by setup detection / scoring (stages 4–5) and consumed
    by risk gating (stage 6). May carry both deterministic strategy
    output and model scores under `model_scores`. Risk gating may
    reject any candidate regardless of any score it carries.
    """

    candidate_id: str
    strategy: str
    symbol: str
    direction: Direction
    entry: float
    stop_loss: float
    take_profit: float | None = None
    confidence: float | None = None
    created_at: datetime = field(default_factory=_now_utc)
    model_scores: Mapping[str, float] = field(default_factory=dict)
    setup_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0,1] when set; got {self.confidence}"
            )
        if self.entry <= 0:
            raise ValueError(f"entry must be positive; got {self.entry}")
        if self.stop_loss <= 0:
            raise ValueError(f"stop_loss must be positive; got {self.stop_loss}")
        for name, score in self.model_scores.items():
            if not (0.0 <= score <= 1.0):
                raise ValueError(
                    f"model_scores[{name!r}] must be in [0,1]; got {score}"
                )


@dataclass(frozen=True)
class ExecutionIntent:
    """Validated, packaged order ready for broker routing.

    Output of execution packaging (stage 7). The presence of this
    object asserts that the source `TradeCandidate` has cleared
    deterministic risk gating and order validation. Models cannot
    construct `ExecutionIntent` as part of the live path; only the
    execution-packaging code (`src/runtime/orders.py`) may.
    """

    intent_id: str
    candidate_id: str
    account_id: str
    symbol: str
    direction: Direction
    quantity: float
    entry: float
    stop_loss: float
    take_profit: float | None = None
    dry_run: bool = True
    created_at: datetime = field(default_factory=_now_utc)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive; got {self.quantity}")
        if self.entry <= 0:
            raise ValueError(f"entry must be positive; got {self.entry}")
        if self.stop_loss <= 0:
            raise ValueError(f"stop_loss must be positive; got {self.stop_loss}")
