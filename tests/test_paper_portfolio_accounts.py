"""S-PAPER-PORTFOLIO — the two live-portfolio-mirror paper accounts.

`bybit_portfolio` and `alpaca_portfolio` exist to mirror the *actual
live-traded portfolio* (bybit_2 / alpaca_live) on paper money, so the real
portfolio's performance + risk plumbing can be read without the small-account
constraints of the real books or the soak-roster noise.

These tests turn the ``ROSTER-SYNC`` comments in ``config/accounts.yaml`` into
an ENFORCED invariant: if the real-money roster changes and the mirror is not
kept in step, CI fails here (guard structure, not judgment — sanctioned by the
canonical rules). ``alpaca_portfolio`` carries ONE operator-approved divergence:
it drops the affordability-only proxies SPLG/IAUM so a big-balance paper book
doesn't double S&P/gold exposure.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ACCOUNTS_YAML = _REPO_ROOT / "config" / "accounts.yaml"

# alpaca_portfolio deliberately omits these affordability proxies (see the
# ROSTER-SYNC comment on the account) — the ONE sanctioned divergence from
# an otherwise-exact alpaca_live mirror.
_ALPACA_PROXY_STRATEGIES = {"splg_trend_long_1d", "iaum_pullback_1d"}
_ALPACA_PROXY_SYMBOLS = {"SPLG", "IAUM"}


def _accounts() -> dict:
    data = yaml.safe_load(_ACCOUNTS_YAML.read_text(encoding="utf-8")) or {}
    return data.get("accounts") or {}


def test_portfolio_accounts_present_and_paper():
    accts = _accounts()
    for aid in ("bybit_portfolio", "alpaca_portfolio"):
        assert aid in accts, f"{aid} missing from config/accounts.yaml"
        cfg = accts[aid]
        assert cfg.get("account_class") == "paper", f"{aid} must be paper money"
        assert cfg.get("paper_role") == "portfolio", (
            f"{aid} must carry paper_role: portfolio (the live-portfolio-mirror "
            "marker the dashboard/Android 'Paper' view scopes to)"
        )
        assert str(cfg.get("mode")) == "live", f"{aid} paper account executes (mode: live)"


def test_bybit_portfolio_key_wiring():
    cfg = _accounts()["bybit_portfolio"]
    assert cfg.get("exchange") == "bybit"
    assert cfg.get("demo") is True, "bybit_portfolio trades the Bybit demo (paper) venue"
    assert cfg.get("api_key_env") == "BYBIT_API_KEY_3", (
        "bybit_portfolio uses the reutilized 'bybit 3' demo keys "
        "(secret BYBIT_API_SECRET_3 auto-derived by clients._derive_secret_env)"
    )


def test_alpaca_portfolio_key_wiring():
    cfg = _accounts()["alpaca_portfolio"]
    assert cfg.get("exchange") == "alpaca"
    assert cfg.get("alpaca_env") == "paper"
    assert cfg.get("api_key_env") == "ALPACA_API_KEY_PAPER_PORTFOLIO"
    assert cfg.get("api_secret_env") == "ALPACA_API_SECRET_KEY_PAPER_PORTFOLIO"


def test_bybit_portfolio_mirrors_bybit_2_exactly():
    accts = _accounts()
    portfolio, real = accts["bybit_portfolio"], accts["bybit_2"]
    assert portfolio.get("strategies") == real.get("strategies"), (
        "bybit_portfolio must mirror bybit_2's roster exactly — keep them in "
        "step (ROSTER-SYNC). If bybit_2's roster changed, update bybit_portfolio."
    )
    assert portfolio.get("symbols") == real.get("symbols"), (
        "bybit_portfolio must mirror bybit_2's symbols exactly (ROSTER-SYNC)."
    )
    # Same sizing basis so the paper read is representative of the live account.
    assert portfolio["risk"].get("leverage") == real["risk"].get("leverage")
    assert portfolio["risk"].get("risk_pct") == real["risk"].get("risk_pct")


def test_alpaca_portfolio_mirrors_alpaca_live_minus_proxies():
    accts = _accounts()
    portfolio, real = accts["alpaca_portfolio"], accts["alpaca_live"]

    live_strats = real.get("strategies") or []
    port_strats = portfolio.get("strategies") or []
    expected_strats = [s for s in live_strats if s not in _ALPACA_PROXY_STRATEGIES]

    # THREE STATES, SPLIT RATHER THAN CONFLATED (2026-08-29, extended 2026-08-31).
    #
    # The purpose of ROSTER-SYNC is that the paper read is REPRESENTATIVE of the
    # live account — every leg real money runs has a paper counterpart accruing
    # a record. The failure it exists to catch is a live leg with NO paper
    # counterpart. Equality expressed that, but equality also asserts the
    # converse (the mirror may run nothing extra), and the converse is what
    # breaks during a STAGED go-live.
    #
    # 1. live EMPTY (2026-08-29 .. 2026-08-31) — the mirror must stay POPULATED.
    # 2. live a SUBSET of the mirror — a staged go-live. Added 2026-08-31, when
    #    alpaca_live went to `['tlt_pullback_1h']`: one plumbing-test leg,
    #    Tier-3 operator-approved, deliberately NOT a roster.
    # 3. live and mirror aligned — the steady state.
    #
    # ⚠️ ASSERTING EQUALITY IN STATE 2 WOULD FORCE alpaca_portfolio DOWN FROM 14
    # LEGS TO 1, destroying the paper research book that the eventual roster
    # selection depends on. That is the SAME harm the 2026-08-29 note records
    # for state 1 — "the invariant would demand the opposite of its own
    # purpose" — one state over. A one-leg live book has almost nothing to be
    # representative OF, and deleting 13 paper legs to match it is a loss, not
    # a sync.
    #
    # ⚠️ WHAT THIS GIVES UP, STATED PLAINLY RATHER THAN QUIETLY RELAXED: the
    # superset direction is no longer asserted, so a leg added to the MIRROR
    # and not to live no longer fails here. That direction was never the
    # representativeness risk (a paper leg with no live counterpart costs
    # research data, not a blind spot on real money), and it is still covered
    # at the INSTRUMENT level by the strict `symbols` equality below, which is
    # UNCHANGED and still exact. The direction that protects real money —
    # a live leg with no paper counterpart — is now asserted in EVERY state,
    # including the empty one, where equality previously asserted nothing.
    assert set(expected_strats) <= set(port_strats), (
        "every alpaca_live leg (minus the affordability proxies "
        f"{_ALPACA_PROXY_STRATEGIES}) must have a counterpart in "
        "alpaca_portfolio, so real money never trades a leg with no paper "
        "record accruing beside it (ROSTER-SYNC). Missing from the mirror: "
        f"{sorted(set(expected_strats) - set(port_strats))}"
    )
    assert port_strats, (
        "alpaca_portfolio must NOT be emptied. While alpaca_live's roster is "
        "empty or staged it is the only place the paper record still accrues. "
        "If you meant to retire the Alpaca research book, do it explicitly, "
        "not by mirroring a live roster that is deliberately small."
    )
    # ⚠️ CONSEQUENCE, recorded rather than hidden: while live is empty or
    # carries only a staging leg, `/api/bot/performance`'s `paperPortfolio`
    # block describes a RESEARCH book, not a mirror of what real money is
    # trading. Do not read it as "the live portfolio on paper" in this window.

    # No proxy strategy leaks in — holds in BOTH states.
    assert _ALPACA_PROXY_STRATEGIES.isdisjoint(set(port_strats))

    expected_syms = [s for s in real.get("symbols") or []
                     if s not in _ALPACA_PROXY_SYMBOLS]
    assert portfolio.get("symbols") == expected_syms, (
        "alpaca_portfolio symbols must be alpaca_live's minus SPLG/IAUM."
    )


def test_paper_role_surfaced_on_config_api():
    """paper_role must be in the /api/bot/config account allowlist so consumers
    can distinguish portfolio-paper from soak-paper (imported lazily so the
    test is skippable where FastAPI isn't installed, e.g. a lint-only env)."""
    try:
        from src.web.api.routers.bot_config import _ACCOUNT_PUBLIC_FIELDS
    except Exception:  # pragma: no cover - FastAPI absent (sandbox/lint)
        import pytest
        pytest.skip("bot_config router not importable in this environment")
    assert "paper_role" in _ACCOUNT_PUBLIC_FIELDS
