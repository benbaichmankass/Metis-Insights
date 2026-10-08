"""breakout_1's feed may be disabled only once the account is retired."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[2] / "scripts/ops/breakout_login_check_action.sh").read_text()


def test_breakout_1_feed_disable_is_gated_on_a_retired_roster():
    block = SRC.split('"${EXEC_MODE}" = "feed-disable-timer" ]; then', 1)[1].split("exit 0", 1)[0]
    assert 'feed-disable-timer" ] \\' in block
    assert '== [] and a.get("mode") != "live"' in block
    assert 'FEED_UNIT="ict-prop-feed"' in block  # the non-templated unit
    assert "refused for breakout_1" in block      # enable / unretired still refused
