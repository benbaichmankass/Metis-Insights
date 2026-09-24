"""E42 — the tick's fetch set is the UNION of roster-implied and declared symbols.

``config/accounts.yaml::symbols`` is an ADDITIVE DATA-PULL list; the
``strategies:`` roster is the single source of truth for what trades. Until
2026-09-22 ``_resolve_tick_symbols`` built the fetch set from the pull lists
ALONE, so a rostered leg whose symbol nobody had declared got no candles, no
signal and no order while reading as wired — a third execution gate in
everything but name (Prime Directive rule 6).

The two properties these tests exist to hold down:

1. **Additive, never subtractive.** Every symbol the old derivation returned is
   still returned. This is what makes the change safe to deploy against live
   money: no leg that trades today can stop.
2. **A stale pull list never stops a rostered leg.** The roster's symbol is
   fetched AND accepted by the intent layer, because those are two separate
   refusals one layer apart and fixing only the first turns a silent
   no-trade into a ``ValueError`` on the live path.

The existing tick-symbol tests live in ``tests/test_ib_sizing_and_data.py``
(``TestMultiSymbolResolution``) and are deliberately left alone — they pin the
DECLARED half, which this change does not touch.
"""

import pytest

from src.main import _resolve_tick_symbols


class _FakeAcct:
    def __init__(self, *, exchange, strategies, symbols=None, configured=True):
        self.exchange = exchange
        self.strategies = strategies
        self.symbols = symbols
        self.configured = configured


def _patch(monkeypatch, accounts, strategies_cfg):
    monkeypatch.setattr("src.units.accounts.load_accounts", lambda *a, **k: accounts)
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config", lambda *a, **k: strategies_cfg
    )


STRATS = {
    "xrp_pullback_2h": {"symbols": ["XRPUSDT"]},
    "avax_pullback_2h": {"symbols": ["AVAXUSDT"]},
    "vwap": {"symbols": ["BTCUSDT"]},
    "no_symbols_leg": {"timeframe": "1h"},
}


class TestRosterImpliedSymbolsAreFetched:
    def test_rostered_symbol_absent_from_pull_list_is_still_fetched(self, monkeypatch):
        """THE DEFECT. The real 2026-09-22 shape, on a real-money account.

        ``bybit_2`` rosters ``avax_pullback_2h`` while ``AVAXUSDT`` is absent
        from its ``symbols:``. Before this change the fetch set was
        ``[BTCUSDT, XRPUSDT]`` and the AVAX leg placed nothing, forever, with
        no error anywhere.
        """
        _patch(monkeypatch, [
            _FakeAcct(exchange="bybit",
                      strategies=["xrp_pullback_2h", "avax_pullback_2h"],
                      symbols=["BTCUSDT", "XRPUSDT"]),
        ], STRATS)
        got = _resolve_tick_symbols({"SYMBOL": "BTCUSDT"})
        assert "AVAXUSDT" in got
        assert got == ["BTCUSDT", "XRPUSDT", "AVAXUSDT"]

    def test_declared_orphans_are_kept(self, monkeypatch):
        """The 21 orphans are legitimate data pulls, not drift to clean up.

        The union must not become an intersection: a declared symbol no leg
        trades stays in the fetch set.
        """
        _patch(monkeypatch, [
            _FakeAcct(exchange="bybit", strategies=["vwap"],
                      symbols=["BTCUSDT", "ADAUSDT", "ETHUSDT"]),
        ], STRATS)
        got = _resolve_tick_symbols({"SYMBOL": "BTCUSDT"})
        assert got == ["BTCUSDT", "ADAUSDT", "ETHUSDT"]

    def test_declared_order_is_preserved_and_roster_appends(self, monkeypatch):
        """Additive means APPENDED — the declared list keeps its ordering, so
        the change can only ever lengthen the result."""
        _patch(monkeypatch, [
            _FakeAcct(exchange="bybit", strategies=["avax_pullback_2h"],
                      symbols=["ETHUSDT", "ADAUSDT"]),
        ], STRATS)
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == [
            "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT",
        ]

    def test_empty_roster_account_still_opts_out(self, monkeypatch):
        """``strategies: []`` is the account-level opt-out and is UNCHANGED.

        A roster that is explicitly empty implies nothing and the account is
        skipped entirely — its declared symbols do not enter the fetch set,
        exactly as before.
        """
        _patch(monkeypatch, [
            _FakeAcct(exchange="bybit", strategies=["vwap"], symbols=["BTCUSDT"]),
            _FakeAcct(exchange="interactive_brokers", strategies=[], symbols=["MES"]),
        ], STRATS)
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT"]

    def test_unconfigured_account_still_excluded(self, monkeypatch):
        _patch(monkeypatch, [
            _FakeAcct(exchange="bybit", strategies=["avax_pullback_2h"],
                      symbols=["BTCUSDT"], configured=False),
        ], STRATS)
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT"]


class TestFailSafe:
    def test_unreadable_strategies_config_degrades_to_declared_not_empty(
        self, monkeypatch
    ):
        """FAIL-SAFE, and this is the branch that decides whether the change is
        deployable: a strategies.yaml that will not load must fall back to the
        OLD behaviour (declared symbols), never to an empty fetch set.
        """
        def _boom(*a, **k):
            raise RuntimeError("strategies.yaml is corrupt")

        monkeypatch.setattr("src.units.accounts.load_accounts", lambda *a, **k: [
            _FakeAcct(exchange="bybit", strategies=["avax_pullback_2h"],
                      symbols=["BTCUSDT", "XRPUSDT"]),
        ])
        monkeypatch.setattr("src.units.strategies.load_strategy_config", _boom)
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT", "XRPUSDT"]

    def test_rostered_strategy_missing_from_config_is_survived(self, monkeypatch):
        """A roster naming a strategy strategies.yaml does not carry must not
        raise on the live path. The CI guard reports it as
        ``strategy_unknown``; the tick just gets nothing extra."""
        _patch(monkeypatch, [
            _FakeAcct(exchange="bybit", strategies=["ghost_leg", "no_symbols_leg"],
                      symbols=["BTCUSDT"]),
        ], STRATS)
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT"]


class TestTheOtherRefusalOneLayerDown:
    """``supported_symbols()`` is a whitelist that RAISES, so widening the
    fetch set without widening it moves the failure rather than fixing it."""

    def test_roster_symbol_is_accepted_by_the_intent_layer(self, monkeypatch):
        from src.runtime import intents

        monkeypatch.setattr(
            "src.config.accounts_loader.load_accounts_dict",
            lambda *a, **k: {"bybit_2": {"strategies": ["avax_pullback_2h"],
                                         "symbols": ["BTCUSDT"]}},
        )
        monkeypatch.setattr(
            "src.units.strategies.load_strategy_config", lambda *a, **k: STRATS
        )
        intents._reset_config_symbols_cache()
        try:
            assert "AVAXUSDT" in intents.supported_symbols()
            # And the refusal it guards no longer fires for that symbol.
            intents.StrategyIntent(strategy="avax_pullback_2h", symbol="AVAXUSDT",
                                   side="long", target_qty=1.0)
        finally:
            intents._reset_config_symbols_cache()

    def test_an_undeclared_unrostered_symbol_is_still_refused(self, monkeypatch):
        """NEGATIVE CONTROL — the whitelist still rejects a typo. A widening
        that accepted everything would be indistinguishable from deleting it."""
        from src.runtime import intents

        monkeypatch.setattr(
            "src.config.accounts_loader.load_accounts_dict",
            lambda *a, **k: {"bybit_2": {"strategies": ["avax_pullback_2h"],
                                         "symbols": ["BTCUSDT"]}},
        )
        monkeypatch.setattr(
            "src.units.strategies.load_strategy_config", lambda *a, **k: STRATS
        )
        intents._reset_config_symbols_cache()
        try:
            assert "AVAXUSDTT" not in intents.supported_symbols()
            with pytest.raises(ValueError):
                intents.StrategyIntent(strategy="avax_pullback_2h",
                                       symbol="AVAXUSDTT", side="long",
                                       target_qty=1.0)
        finally:
            intents._reset_config_symbols_cache()

    def test_unreadable_strategies_config_never_narrows_the_whitelist(
        self, monkeypatch
    ):
        from src.runtime import intents

        def _boom(*a, **k):
            raise RuntimeError("nope")

        monkeypatch.setattr(
            "src.config.accounts_loader.load_accounts_dict",
            lambda *a, **k: {"a": {"strategies": ["avax_pullback_2h"],
                                   "symbols": ["SPY"]}},
        )
        monkeypatch.setattr("src.units.strategies.load_strategy_config", _boom)
        intents._reset_config_symbols_cache()
        try:
            got = intents.supported_symbols()
            assert intents.SUPPORTED_SYMBOLS <= got
            assert "SPY" in got  # the declared half still stands
        finally:
            intents._reset_config_symbols_cache()


class TestLiveConfigIsUnchangedByThisPR:
    """THE DEPLOY-SAFETY CLAIM, asserted against the REAL config rather than a
    fixture — the change is a NO-OP on today's tree, and a future config edit
    that made it not one would fail here rather than surprising a deploy."""

    def test_union_adds_nothing_and_removes_nothing_today(self):
        import yaml

        from src.main import _EXCHANGE_DEFAULT_SYMBOL, _symbols_for_account

        accounts = (yaml.safe_load(open("config/accounts.yaml", encoding="utf-8"))
                    or {}).get("accounts") or {}
        strategies = (yaml.safe_load(open("config/strategies.yaml", encoding="utf-8"))
                      or {}).get("strategies") or {}

        class _A:
            pass

        before, after = ["BTCUSDT"], ["BTCUSDT"]
        for _name, cfg in accounts.items():
            cfg = cfg or {}
            if cfg.get("enabled") is False:
                continue
            roster = None if "strategies" not in cfg else list(cfg.get("strategies") or [])
            if roster is not None and not roster:
                continue
            syms = list(cfg.get("symbols") or [])
            if not syms:
                d = _EXCHANGE_DEFAULT_SYMBOL.get(str(cfg.get("exchange") or "").lower())
                syms = [d] if d else []
            a = _A()
            a.strategies = roster
            implied = _symbols_for_account(a, strategies)
            for s in syms:
                if s not in before:
                    before.append(s)
            for s in list(syms) + implied:
                if s not in after:
                    after.append(s)

        assert [s for s in before if s not in after] == [], "the union removed a symbol"
        added = [s for s in after if s not in before]
        assert added == [], (
            "the union ADDS %r to the live fetch set. That is allowed and safe "
            "— but it means the pull list is stale, so land the "
            "roster-symbol-reachability finding with it rather than only "
            "updating this assertion." % added
        )
        assert len(after) == 23, "expected the 2026-09-22 fetch set of 23 symbols"
