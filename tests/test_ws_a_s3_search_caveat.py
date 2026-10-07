"""CA-B04: ws_a_s3 verdicts must not read as search-adjusted significance."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "scripts/research/ws_a_s3_significance.py").read_text()


def test_pass_verdict_is_labelled_unadjusted_and_caveat_is_emitted():
    assert "PASS — genuine candidate" not in SRC
    assert "UNADJUSTED for the S2 search" in SRC
    assert "SEARCH_CAVEAT" in SRC and '"selection_adjusted": False' in SRC
