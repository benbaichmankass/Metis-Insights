"""Risk manager guardrails."""
import pytest

from src.units.accounts.tradovate.config import TradovateConfig
from src.units.accounts.tradovate.exceptions import TradovateRiskRejection
from src.units.accounts.tradovate.models import OrderRequest, OrderSide, RiskLimits
from src.units.accounts.tradovate.risk_manager import RiskManager


_CREDS = {
    "TRADOVATE_USERNAME": "u", "TRADOVATE_PASSWORD": "p",
    "TRADOVATE_APP_ID": "a", "TRADOVATE_APP_VERSION": "1",
    "TRADOVATE_CID": "1", "TRADOVATE_SECRET": "s", "TRADOVATE_DEVICE_ID": "d",
}


def _cfg(**overrides) -> TradovateConfig:
    return TradovateConfig.load({**_CREDS, **overrides})


def _req(symbol="MESM6", side=OrderSide.BUY, qty=1, cid=None):
    return OrderRequest(account_id=1, symbol=symbol, side=side, qty=qty,
                        client_order_id=cid)


def test_whitelist_blocks_unknown_symbol():
    rm = RiskManager(_cfg(TRADOVATE_ALLOWED_SYMBOLS="MESM6"))
    with pytest.raises(TradovateRiskRejection) as e:
        rm.check(_req(symbol="MNQM6"))
    assert e.value.reason == "symbol_not_whitelisted"


def test_whitelist_allows_when_empty():
    rm = RiskManager(_cfg())  # empty whitelist
    rm.check(_req(symbol="ANYTHING"))  # does not raise


def test_max_position_violation():
    rm = RiskManager(_cfg(TRADOVATE_MAX_POS_PER_SYMBOL="1"))
    with pytest.raises(TradovateRiskRejection) as e:
        rm.check(_req(qty=1), current_net_qty=1)
    assert e.value.reason == "max_position_per_symbol"


def test_max_open_orders():
    rm = RiskManager(_cfg(TRADOVATE_MAX_OPEN_ORDERS="1"))
    rm.check(_req(cid="a"))
    rm.register_submitted(_req(cid="a"))
    with pytest.raises(TradovateRiskRejection) as e:
        rm.check(_req(cid="b"))
    assert e.value.reason == "max_open_orders"


def test_duplicate_client_order_id():
    rm = RiskManager(_cfg())
    rm.check(_req(cid="dup"))
    rm.register_submitted(_req(cid="dup"))
    with pytest.raises(TradovateRiskRejection) as e:
        rm.check(_req(cid="dup"))
    assert e.value.reason == "duplicate_client_order_id"


def test_non_positive_qty():
    rm = RiskManager(_cfg())
    with pytest.raises(TradovateRiskRejection) as e:
        rm.check(_req(qty=0))
    assert e.value.reason == "non_positive_qty"


def test_paper_only_blocks_live_env():
    rm = RiskManager(_cfg(TRADOVATE_ENV="live"), paper_only=True)
    with pytest.raises(TradovateRiskRejection) as e:
        rm.check(_req())
    assert e.value.reason == "paper_only_violation"


def test_max_notional_respected():
    limits = RiskLimits(max_position_per_symbol=10, max_open_orders=10, max_notional=100.0)
    rm = RiskManager(_cfg(), limits=limits)
    with pytest.raises(TradovateRiskRejection) as e:
        rm.check(_req(qty=2), latest_price=80.0)
    assert e.value.reason == "max_notional"


def test_register_terminal_decrements():
    rm = RiskManager(_cfg(TRADOVATE_MAX_OPEN_ORDERS="1"))
    rm.check(_req(cid="a"))
    rm.register_submitted(_req(cid="a"))
    rm.register_terminal("a")
    rm.check(_req(cid="b"))  # slot freed
