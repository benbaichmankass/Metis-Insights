"""The transient-market-data classifier must match EVERY builder, not most.

BL-20260825-TRANSIENT-CLASSIFIER-MISSES-THE-VARIANT-FAMILIES.

`intent_multiplexer` reclassifies a no-candle builder exception from ERROR to
WARN, because a candle fetch returning None is a transient outage (usually an
IB circuit-breaker backoff) and the per-tick page it would otherwise produce is
the desensitized-alarm P1. The classifier matched the substring
`"no candle data returned"`, and its comment asserted "the phrase is identical
across all ~30 builders".

IT WAS NOT. Two of the 35 raise sites — `_ict_scalp_variant_builder` and
`_trend_donchian_variant_builder`, i.e. two whole strategy FAMILIES — raise
`"<name>: no candle data for symbol=... timeframe=..."`, with no "returned".
Every variant leg therefore fell through to ERROR during exactly the outages
the reclassification exists to quieten.

MEASURED 2026-08-25 over the full 401-row ERROR+ feed (2026-08-20T08:16Z ->
2026-08-25T20:06Z): 240 rows, **100% of them `ict_scalp_mgc_15m`** — a variant
leg — while `mgc_trend_1h` on the SAME symbol at the SAME cadence (193 vs 189
evals over an aligned 6h window) contributed ZERO. That reads like one broken
leg and is not: the trader journal at 20:05:43Z shows the breaker tripping and
`get_ohlcv` failing for MES 1d and MES 15m in the same seconds, with
`mes_trend_long_1d` raising — all correctly WARN'd and so invisible in the
ERROR feed. The outage was fleet-wide; only the mis-graded family was audible.

THIS TEST IS OVER THE CORPUS, DELIBERATELY. Asserting the classifier against
one remembered phrasing is what failed; asserting it against every raise site
in the file is what cannot drift when the next builder is written with
different wording.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from src.runtime.intent_multiplexer import _is_transient_market_data_error

_REPO = pathlib.Path(__file__).resolve().parents[1]
_BUILDERS = _REPO / "src/runtime/strategy_signal_builders.py"

# Every f-string literal in the builder corpus that carries the no-candle
# phrase. `{...}` placeholders are rendered to a stand-in so the message reads
# the way it would at runtime.
_LINE = re.compile(r'f"([^"]*no candle data[^"]*)"', re.IGNORECASE)


def _messages() -> list[str]:
    src = _BUILDERS.read_text(encoding="utf-8")
    return [re.sub(r"\{[^}]*\}", "X", m) for m in _LINE.findall(src)]


def test_the_corpus_is_not_empty():
    """The control. A regex that silently matches nothing would make every
    assertion below pass vacuously — a clean negative with no denominator is
    exactly the shape RULE ONE warns about."""
    msgs = _messages()
    assert len(msgs) >= 30, f"only found {len(msgs)} raise sites — probe is broken"


def test_every_no_candle_raise_site_classifies_transient():
    """The whole point: no builder family may fall through to an ERROR page."""
    missed = [m for m in _messages()
              if not _is_transient_market_data_error(RuntimeError(m))]
    assert not missed, (
        "these no-candle messages are NOT graded transient, so their builders "
        f"page at ERROR on every tick of a routine outage: {missed}"
    )


def test_both_phrasings_are_present_in_the_corpus():
    """Pins the actual defect. If a future sweep normalises every message to
    one phrasing, this test should be DELETED deliberately rather than left
    passing by accident — its value is proving the classifier survives BOTH."""
    msgs = _messages()
    assert any("no candle data returned" in m for m in msgs), "the majority form"
    assert any("no candle data for symbol" in m and "returned" not in m
               for m in msgs), (
        "the variant-family form — if this is gone, the corpus was normalised "
        "and this test has served its purpose"
    )


@pytest.mark.parametrize("msg", [
    "ict_scalp_mgc_15m: no candle data for symbol=MGC timeframe=15m.",
    "mgc_trend_1h: no candle data returned for symbol=MGC ...",
    "NO CANDLE DATA returned for symbol=X",
])
def test_the_live_messages_classify_transient(msg):
    assert _is_transient_market_data_error(RuntimeError(msg))


class TestItStaysNarrow:
    def test_a_genuine_builder_bug_still_pages(self):
        assert not _is_transient_market_data_error(
            RuntimeError("KeyError: 'close' while computing ADX"))

    def test_a_non_RuntimeError_never_matches(self):
        """The type gate is half the narrowness. `require_candles` raises
        ValueError for an absent DataFrame, which is a caller bug, not a
        market-data outage."""
        assert not _is_transient_market_data_error(
            ValueError("Strategy 'x': candles_df is required but was not "
                       "provided."))

    def test_it_does_not_match_unrelated_prose(self):
        assert not _is_transient_market_data_error(
            RuntimeError("candle data looks fine"))
