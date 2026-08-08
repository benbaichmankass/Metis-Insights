"""Bybit DEMO accounts must be dialled on the demo host, and a 100%-failed
account must not read as an empty book.

Regression cover for BL-20260807-BYBIT-DEMO-FILLS-NEVER-PULLED.

The bug had two halves and each alone was survivable; together they made a
silent, weeks-long data outage that systemd reported as success:

  1. ``config/accounts.yaml`` declares ``demo: true`` on ``bybit_1`` and
     ``bybit_portfolio``. Bybit serves demo trading from ``api-demo.bybit.com``
     and rejects a demo key on mainnet with ``retCode 10003``. The order path
     honoured this; both cost-sweep pullers hand-rolled their own mainnet
     ``ccxt.bybit(...)`` and did not.
  2. ``fetch_fills_window`` caught every per-target error and returned ``[]``,
     so "could not read this account at all" was indistinguishable from "this
     account had no fills" — and the run summary printed ``ran=3/3``, counting
     ATTEMPTS as successes.

Measured consequence: ``/api/bot/pnl/exchange`` served 16 symbols over 14 days
with **zero** rows from ``bybit_1``, the account carrying the largest losses in
the book — reported as clean zeros, with no exchange truth to check any journal
close against.
"""
from __future__ import annotations

import pytest

from src.config.accounts_loader import account_is_demo
from src.runtime.exchange_fills_puller import (
    FillsWindowUnavailable,
    fetch_fills_window,
)
from src.runtime.exchange_funding_puller import (
    FundingWindowUnavailable,
    fetch_funding_window,
)


# ── the account flag ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (True, True), (False, False),
    ("true", True), ("True", True), ("1", True), ("yes", True),
    ("false", False), ("no", False), (None, False),
])
def test_account_is_demo_parses_yaml_shapes(raw, expected):
    """The string-tolerant reading — `bool("false")` is True and would have
    routed a MAINNET account to the demo host."""
    assert account_is_demo({"demo": raw}) is expected


def test_account_is_demo_defaults_false_when_absent():
    assert account_is_demo({}) is False


def test_live_roster_carries_the_demo_flag():
    """The roster must EXPOSE demo, not just enumerate the account.

    The 2026-07-29 roster fix enumerated bybit_1/bybit_portfolio and still gave
    them zero coverage, because being listed is not the same as being reachable.
    """
    from src.runtime.exchange_accounts import live_bybit_fill_accounts

    by_id = {a.account_id: a for a in live_bybit_fill_accounts()}
    if not by_id:                      # config unreadable in this env
        pytest.skip("no live Bybit accounts resolvable here")
    assert by_id["bybit_1"].demo is True, "bybit_1 is demo: true in accounts.yaml"
    assert by_id["bybit_2"].demo is False, "bybit_2 is real money on mainnet"


# ── the client builder ───────────────────────────────────────────────────────

def test_builder_refuses_rather_than_silently_using_mainnet(monkeypatch):
    """A ccxt without demo routing must RAISE, never fall back.

    A silent mainnet fallback reproduces the original bug exactly: demo keys to
    the live host, retCode 10003, indistinguishable from an empty book.
    """
    import src.runtime.bybit_ccxt as mod

    class _FakeBybit:
        def __init__(self, cfg):
            self.urls = {"api": "https://api.bybit.com"}
        # deliberately no enable_demo_trading

    fake_ccxt = type("ccxt", (), {"bybit": _FakeBybit, "__version__": "4.0.0"})
    monkeypatch.setitem(__import__("sys").modules, "ccxt", fake_ccxt)

    with pytest.raises(mod.DemoRoutingUnsupported):
        mod.build_bybit_client(
            api_key="k", api_secret="s", category="linear", demo=True,
        )


def test_builder_enables_demo_only_for_demo_accounts(monkeypatch):
    import src.runtime.bybit_ccxt as mod
    calls: list[bool] = []

    class _FakeBybit:
        def __init__(self, cfg):
            self.urls = {"api": "https://api.bybit.com"}

        def enable_demo_trading(self, enable):
            calls.append(enable)
            self.urls["api"] = "https://api-demo.bybit.com"

    fake_ccxt = type("ccxt", (), {"bybit": _FakeBybit, "__version__": "4.5.71"})
    monkeypatch.setitem(__import__("sys").modules, "ccxt", fake_ccxt)

    demo_client = mod.build_bybit_client(
        api_key="k", api_secret="s", category="linear", demo=True,
    )
    assert calls == [True]
    assert "api-demo" in demo_client.urls["api"]

    calls.clear()
    live_client = mod.build_bybit_client(
        api_key="k", api_secret="s", category="linear", demo=False,
    )
    assert calls == [], "a real-money account must never be routed to demo"
    assert "api-demo" not in live_client.urls["api"]


# ── zero coverage is not an empty book ───────────────────────────────────────

def _auth_error(*_a, **_k):
    raise RuntimeError(
        'bybit {"retCode":10003,"retMsg":"API key is invalid."}'
    )


def test_total_failure_raises_instead_of_returning_empty():
    """THE regression. This is the exact shape that ran nightly for weeks."""
    with pytest.raises(FillsWindowUnavailable) as exc:
        fetch_fills_window(_auth_error, account_id="bybit_1", days=7)
    assert "bybit_1" in str(exc.value)
    assert "10003" in str(exc.value), "the underlying cause must survive"


def test_total_failure_raises_for_every_symbol_failing():
    with pytest.raises(FillsWindowUnavailable):
        fetch_fills_window(
            _auth_error, account_id="bybit_1", days=7,
            symbols=["BTC/USDT:USDT", "AVAX/USDT:USDT"],
        )


def test_partial_failure_still_returns_rows():
    """Partial coverage stays best-effort — only a WIPEOUT is exceptional."""
    def _half_broken(symbol, since, limit, params):
        if symbol == "AVAX/USDT:USDT":
            raise RuntimeError("synthetic")
        return [{
            "id": f"{symbol}-1", "order": "o1", "symbol": symbol,
            "side": "buy", "amount": 1.0, "price": 2.0,
            "timestamp": 1_700_000_000_000, "fee": {"cost": 0.1, "currency": "USDT"},
        }]

    rows = fetch_fills_window(
        _half_broken, account_id="bybit_1", days=7,
        symbols=["BTC/USDT:USDT", "AVAX/USDT:USDT"],
    )
    assert len(rows) == 1


def test_genuinely_empty_book_is_not_an_error():
    """No fills is a valid answer and must stay distinguishable from a failure."""
    assert fetch_fills_window(
        lambda *a, **k: [], account_id="bybit_1", days=7,
    ) == []


# ── a capped page is not a complete history ──────────────────────────────────

def _trade(i):
    return {
        "id": f"e{i}", "order": "o1", "symbol": "AVAX/USDT:USDT",
        "side": "buy", "amount": 1.0, "price": 2.0,
        "timestamp": 1_700_000_000_000 + i,
        "fee": {"cost": 0.0, "currency": "USDT"},
    }


def test_full_page_is_declared_as_capped(caplog):
    """A deep pull that fills the page must SAY it was truncated.

    `fetch_fills_window` issues ONE fetch_my_trades call per target with no
    pagination. Without this declaration a result of exactly PAGE_LIMIT reads
    identically to one that exhausted the venue's retention — and a retention
    measurement built on that reads wrong in the direction that looks like good
    news (the unasserted-denominator failure mode, sub-class C).
    """
    from src.runtime.exchange_fills_puller import PAGE_LIMIT

    page = [_trade(i) for i in range(PAGE_LIMIT)]
    with caplog.at_level("WARNING"):
        rows = fetch_fills_window(
            lambda *a, **k: page, account_id="bybit_1", days=90,
        )
    assert len(rows) == PAGE_LIMIT
    assert any("PAGE-CAPPED" in r.message for r in caplog.records), (
        "a full page must be declared, not silently returned as complete"
    )


def test_short_page_is_not_declared_capped(caplog):
    from src.runtime.exchange_fills_puller import PAGE_LIMIT

    page = [_trade(i) for i in range(PAGE_LIMIT - 1)]
    with caplog.at_level("WARNING"):
        fetch_fills_window(lambda *a, **k: page, account_id="bybit_1", days=90)
    assert not any("PAGE-CAPPED" in r.message for r in caplog.records)


def test_page_limit_is_the_limit_actually_requested():
    """The declared ceiling must be the one sent to the venue."""
    from src.runtime.exchange_fills_puller import PAGE_LIMIT

    seen = []

    def _capture(symbol, since, limit, params):
        seen.append(limit)
        return []

    fetch_fills_window(_capture, account_id="bybit_1", days=7)
    assert seen == [PAGE_LIMIT]


# ── a deep window must be WALKED, not asked for in one call ──────────────────
#
# Bybit V5 caps the queryable RANGE at 7 days but retains 2 years. A single
# call with since = now-90d returns the 7-day slice [now-90d, now-83d] — the
# window is MOVED, not widened. Measured 2026-08-08: `--days 90` returned 0
# fills on all three accounts while `--days 7` returned 63 / 3 / 13.

def test_deep_window_is_split_into_capped_chunks():
    from datetime import datetime, timedelta, timezone

    from src.runtime.exchange_fills_puller import MAX_RANGE_DAYS

    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    windows = []

    def _capture(symbol, since, limit, params):
        windows.append((since, params.get("endTime")))
        return []

    fetch_fills_window(_capture, account_id="bybit_1", days=90, now=now)

    assert len(windows) == 13, "90 days needs ceil(90/7)=13 chunks, not 1"
    cap_ms = MAX_RANGE_DAYS * 86_400_000
    for since, end in windows:
        assert end is not None, "endTime must be sent, not left to a venue default"
        assert 0 < end - since <= cap_ms, "a chunk exceeded the venue's range cap"
    # The walk must TILE: contiguous, and covering the whole requested range.
    for (_, prev_end), (nxt_since, _) in zip(windows, windows[1:]):
        assert nxt_since == prev_end, "chunks must be contiguous — no gap, no overlap"
    assert windows[0][0] == int((now - timedelta(days=90)).timestamp() * 1000)
    assert windows[-1][1] == int(now.timestamp() * 1000)


def test_short_window_still_issues_exactly_one_call():
    """The ordinary operational pull must not change shape."""
    from datetime import datetime, timezone

    calls = []
    fetch_fills_window(
        lambda s, since, limit, params: calls.append(since) or [],
        account_id="bybit_1", days=7,
        now=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc),
    )
    assert len(calls) == 1


def test_wider_window_can_never_return_fewer_fills():
    """THE property whose violation exposed the bug.

    A 7-day window is nested inside a 90-day one, so the wider pull cannot
    return fewer fills. Live it returned 63 vs 0 — proof the range was never
    honoured. This pins the invariant against a venue that only ever serves the
    first chunk.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

    def _venue(symbol, since, limit, params):
        # One fill per day of real history, 60 days deep.
        out = []
        for d in range(60):
            ts = int((now - timedelta(days=d)).timestamp() * 1000)
            if since <= ts <= params["endTime"]:
                out.append({
                    "id": f"day-{d}", "order": "o1", "symbol": "AVAX/USDT:USDT",
                    "side": "buy", "amount": 1.0, "price": 2.0, "timestamp": ts,
                    "fee": {"cost": 0.0, "currency": "USDT"},
                })
        return out

    narrow = fetch_fills_window(_venue, account_id="bybit_1", days=7, now=now)
    wide = fetch_fills_window(_venue, account_id="bybit_1", days=90, now=now)
    assert len(wide) >= len(narrow)
    assert len(narrow) == 8 and len(wide) == 60, (len(narrow), len(wide))


def test_walk_dedupes_across_chunk_boundaries():
    """A fill on a boundary is returned by both adjacent chunks — count it once."""
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    boundary_ms = int((now - timedelta(days=7)).timestamp() * 1000)

    def _venue(symbol, since, limit, params):
        if since <= boundary_ms <= params["endTime"]:
            return [{
                "id": "on-the-seam", "order": "o1", "symbol": "AVAX/USDT:USDT",
                "side": "buy", "amount": 1.0, "price": 2.0,
                "timestamp": boundary_ms, "fee": {"cost": 0.0, "currency": "USDT"},
            }]
        return []

    rows = fetch_fills_window(_venue, account_id="bybit_1", days=14, now=now)
    assert [r["exec_id"] for r in rows] == ["on-the-seam"]


def test_one_bad_chunk_does_not_lose_the_others():
    from datetime import datetime, timezone

    calls = {"n": 0}

    def _flaky(symbol, since, limit, params):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("synthetic chunk failure")
        return [{
            "id": f"c{calls['n']}", "order": "o1", "symbol": "AVAX/USDT:USDT",
            "side": "buy", "amount": 1.0, "price": 2.0,
            "timestamp": 1_700_000_000_000 + calls["n"],
            "fee": {"cost": 0.0, "currency": "USDT"},
        }]

    rows = fetch_fills_window(
        _flaky, account_id="bybit_1", days=21,
        now=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc),
    )
    assert len(rows) == 2, "a single failed chunk must not discard the good ones"


def test_every_chunk_failing_still_raises():
    """A deep walk that reads nothing anywhere is zero coverage, not an empty book."""
    from datetime import datetime, timezone

    with pytest.raises(FillsWindowUnavailable):
        fetch_fills_window(
            _auth_error, account_id="bybit_1", days=90,
            now=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc),
        )


def test_funding_total_failure_raises_too():
    with pytest.raises(FundingWindowUnavailable):
        fetch_funding_window(_auth_error, account_id="bybit_1", days=30)
