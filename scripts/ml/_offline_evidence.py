#!/usr/bin/env python3
"""Read a leg's OFFLINE edge record, and say plainly what we have.

WHY THIS EXISTS
---------------
The promotion-evidence doctrine (`docs/CLAUDE-RULES-CANONICAL.md`) is explicit:
**edge is proven OFFLINE; live data proves MECHANICS only**, and clause 3 says
*"no gate may require calendar-time accrual to prove edge."* The M7 daily
strategy-review packet violated that by construction — it withheld every verdict
for want of live in-window closes (`MIN_CLOSED_FOR_ACTION` over a 7-day window),
which graded **52 of 52 legs `hold` on eight consecutive days** without ever
proposing an action. `scripts/check_soak_doctrine.py` check D now detects the
seven accrual-gated branches that do it.

`hold` is the wrong word for that state and it is wrong in the dangerous
direction: `hold` reads as *"graded, and nothing needs doing"*, when the truth
is *"nobody has produced the offline evidence this verdict is supposed to rest
on."* Those are opposite facts. This module supplies the distinction.

WHAT IT DOES NOT DO, DELIBERATELY
---------------------------------
It does **not** grade edge, and it does **not** re-point any KILL/DEMOTE
threshold at the offline number. It answers exactly one question — *do we have
an offline record for this leg, and how much does it describe this leg?* —
and leaves every verdict to the caller. Re-pointing the thresholds is a larger
Tier-3 change than the one that was approved.

THE VOCABULARY IS IMPORTED, NEVER RE-DERIVED
--------------------------------------------
`coverage_state` and `basis` are owned by the PRODUCER
(`scripts/ops/build_strategy_evidence.py`). This module imports them. A second
copy of a vocabulary is how the two spellings drift apart, and this repo has
already paid for exactly that: a consumer compared against `"HOLD"` while the
generator emitted `"hold"`, matched nothing, and counted all 52 graded
strategies as actionable (see `NO_ACTION_VERDICT` in the packet).
"""

from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional

_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Import the producer's vocabulary rather than restating it. If the producer is
# unavailable (a stripped checkout), we FAIL LOUD rather than silently falling
# back to a hardcoded copy that can drift — an unreadable vocabulary is
# "we did not look", never "the defaults are fine".
sys.path.insert(0, str(_ROOT / "scripts" / "ops"))
try:  # pragma: no cover - exercised by the import itself
    from build_strategy_evidence import BASIS as PRODUCER_BASIS
    from build_strategy_evidence import COVERAGE_STATES as PRODUCER_COVERAGE_STATES
except Exception as exc:  # pragma: no cover
    raise ImportError(
        "cannot import the offline-evidence vocabulary from its producer "
        "(scripts/ops/build_strategy_evidence.py). Re-deriving it here would "
        "let the two drift, which is the defect this import exists to prevent."
    ) from exc

#: Where the producer writes its records.
EVIDENCE_DIR = _ROOT / "comms" / "strategy_evidence"

#: The verdict the packet emits when a leg has no offline edge record.
#: ⚠️ A FOURTH STATE, never a rename of `hold`. A leg WITH a record that
#: genuinely warrants no action still grades `hold`; this one says the evidence
#: the verdict should rest on does not exist.
NO_OFFLINE_EVIDENCE = "no_offline_evidence"


@dataclass(frozen=True)
class OfflineEvidence:
    """What we know about a leg's offline edge record.

    ⚠️ FOUR STATES, NEVER COLLAPSED — the distinction that matters is between
    *we looked and there is nothing* and *we could not look*:

    * ``faithful``    — a record exists and the harness modelled every declared
      lever. The strongest thing available.
    * ``approximate`` — a record exists but the harness omitted declared levers,
      so the number describes a leg that is **not quite this one**. It is still
      evidence and is deliberately NOT discarded (dropping it throws away a
      usable measurement); `omitted_levers` says what it misses.
    * ``absent``      — **no record**. Nobody has run the producer for this leg.
      This is the day-one state for almost every leg and it is the whole point:
      it is a gap in OUR instrumentation, not a property of the strategy.
    * ``unreadable``  — a record exists and could not be parsed. **We did not
      look.** Never folded into ``absent``, because "nobody ran it" and "it is
      corrupt" have different remedies.
    """

    state: str
    fidelity: Optional[str] = None
    coverage_state: Optional[str] = None
    basis: Optional[str] = None
    net_r_oos: Optional[float] = None
    n_trades_oos: Optional[int] = None
    folds: Optional[int] = None
    folds_positive: Optional[int] = None
    omitted_levers: Optional[list] = None
    config_fingerprint: Optional[str] = None
    detail: Optional[str] = None

    @property
    def has_record(self) -> bool:
        """True when a usable offline record exists for this leg.

        ⚠️ ``approximate`` counts as HAVING a record. Treating it as absent
        would discard a real harness run over real bars; treating it as
        ``faithful`` would launder a weaker number into evidence. It is carried
        with its fidelity attached so a caller can discount it knowingly.
        """
        return self.state in ("faithful", "approximate")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "fidelity": self.fidelity,
            "coverage_state": self.coverage_state,
            "basis": self.basis,
            "net_r_oos": self.net_r_oos,
            "n_trades_oos": self.n_trades_oos,
            "folds": self.folds,
            "folds_positive": self.folds_positive,
            "omitted_levers": self.omitted_levers,
            "config_fingerprint": self.config_fingerprint,
            "detail": self.detail,
        }


def read_offline_evidence(
    strategy: str, evidence_dir: Optional[pathlib.Path] = None
) -> OfflineEvidence:
    """Read ``comms/strategy_evidence/<strategy>.json``.

    Never raises: an unparseable or unexpected record grades ``unreadable``
    rather than propagating, because this runs inside a daily packet build and
    one corrupt file must not take the whole fleet's review down. But it also
    never grades a failure as ``absent`` — see the dataclass docstring.
    """
    base = pathlib.Path(evidence_dir) if evidence_dir is not None else EVIDENCE_DIR
    path = base / f"{strategy}.json"

    if not path.exists():
        return OfflineEvidence(
            state="absent",
            detail=(
                "no record at comms/strategy_evidence/"
                f"{strategy}.json — nobody has run "
                "scripts/ops/build_strategy_evidence.py for this leg"
            ),
        )

    try:
        rec = json.loads(path.read_text())
        if not isinstance(rec, dict):
            raise ValueError(f"record is {type(rec).__name__}, expected object")
    except Exception as exc:
        return OfflineEvidence(
            state="unreadable",
            detail=f"record exists but could not be parsed: {exc}",
        )

    coverage = rec.get("coverage_state")
    basis = rec.get("basis")

    # A record the producer did not actually measure is NOT evidence, and it
    # says so itself. `no_harness` / `harness_failed` / `not_attempted` are the
    # producer's own words for "we know we have no number, and why".
    if coverage != "measured":
        known = coverage in PRODUCER_COVERAGE_STATES
        return OfflineEvidence(
            state="absent",
            coverage_state=coverage,
            basis=basis,
            detail=(
                f"record exists but coverage_state={coverage!r} — the producer "
                "recorded no measurement"
                + ("" if known else " (and that state is not in the producer's vocabulary)")
            ),
        )

    fidelity = rec.get("fidelity")
    if fidelity == "faithful":
        state = "faithful"
    elif fidelity == "approximate":
        state = "approximate"
    else:
        # `measured` with a fidelity the producer never emits is a record we
        # cannot interpret. Grading it `faithful` would be the strongest
        # possible reading of an unknown, which is the wrong direction.
        return OfflineEvidence(
            state="unreadable",
            coverage_state=coverage,
            basis=basis,
            detail=f"coverage_state=measured but fidelity={fidelity!r} is not recognised",
        )

    return OfflineEvidence(
        state=state,
        fidelity=fidelity,
        coverage_state=coverage,
        basis=basis,
        net_r_oos=rec.get("net_r_oos"),
        n_trades_oos=rec.get("n_trades_oos"),
        folds=rec.get("folds"),
        folds_positive=rec.get("folds_positive"),
        omitted_levers=rec.get("omitted_levers"),
        config_fingerprint=rec.get("config_fingerprint"),
        detail=(
            None
            if basis == PRODUCER_BASIS
            else f"basis={basis!r} differs from the producer's {PRODUCER_BASIS!r}"
        ),
    )
