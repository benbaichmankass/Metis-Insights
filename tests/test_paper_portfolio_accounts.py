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

BOTH mirrors are now STRICT EQUALITY (DECIDED 2026-09-21, operator, checklist
row B2) — ``alpaca_portfolio`` was a deliberate SUBSET assertion from
2026-08-29 until then. A successor should read the bybit and alpaca mirror
tests as the SAME mechanism, differing only in the declared proxy carve-out.
REVERSAL: the subset form is recoverable from git (#10393, #10633); reversing
it is an operator decision, not a test edit.
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


def _expected_mirror_roster(alpaca_live_cfg: dict) -> list[str]:
    """What ``alpaca_portfolio``'s roster must be — ONE definition, two readers.

    Both alpaca mirror tests below derive the expectation from here rather than
    re-implementing the proxy filter, so the coverage assertion and the strict
    equality assertion can never drift into disagreeing about what the mirror
    is supposed to carry.
    """
    return [s for s in (alpaca_live_cfg.get("strategies") or [])
            if s not in _ALPACA_PROXY_STRATEGIES]


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


def test_alpaca_portfolio_covers_every_alpaca_live_leg():
    """The REAL-MONEY direction, asserted on its own so it stays green.

    This is the half of ROSTER-SYNC that protects money: no ``alpaca_live``
    leg may trade without a paper counterpart accruing a record beside it. It
    is implied by the strict equality asserted below, and kept as a separate
    test deliberately — while the equality test is RED (see its docstring),
    this one still says out loud that the money-protecting direction holds.
    """
    accts = _accounts()
    portfolio, real = accts["alpaca_portfolio"], accts["alpaca_live"]
    expected = _expected_mirror_roster(real)
    missing = sorted(set(expected) - set(portfolio.get("strategies") or []))
    assert not missing, (
        "every alpaca_live leg (minus the affordability proxies "
        f"{_ALPACA_PROXY_STRATEGIES}) must have a counterpart in "
        "alpaca_portfolio, so real money never trades a leg with no paper "
        f"record accruing beside it (ROSTER-SYNC). Missing: {missing}"
    )


def test_alpaca_portfolio_mirrors_alpaca_live_exactly_minus_proxies():
    """STRICT EQUALITY — the same mechanism as ``bybit_portfolio``/``bybit_2``.

    DECIDED 2026-09-21 (operator, checklist row B2). Reverses the SUBSET
    relaxation taken 2026-08-29/08-31 and restores the ordered-equality
    assertion this test shipped with on 2026-07-16 (#6663).

    WHY. ``docs/plans/OPERATING-PLAN-2026-09-21.md`` makes the mirror's
    net-of-cost window Gate 2's DEMOTION signal. A demotion signal read off a
    roster that is not the live roster is not a demotion signal — it is a
    different book's aggregate wearing the mirror's name. The subset
    assertion could never deliver that, by construction: it permits the
    mirror to run arbitrarily many extra legs.

    WHAT WAS GIVEN UP, and it was put to the operator before this landed: the
    surplus mirror legs stop trading on this book. The operator accepted that
    cost. Do not re-present it as a discovery.

    ⚠️ THE PROXY CARVE-OUT IS REAL AND SURVIVES — it is not a leftover of the
    subset era. ``alpaca_live`` carries ``iaum_pullback_1d`` today (Tier-3,
    operator-approved 2026-09-10) purely so a ~$150 cash book can buy a whole
    share of gold exposure; on a ~$98k paper balance the primary
    ``gld_pullback_1d`` fires directly, so mirroring the proxy would DOUBLE
    gold exposure and pollute the very read this test exists to protect. It
    would also contradict the strict ``symbols`` equality below, which
    excludes SPLG/IAUM by construction. "Exactly" here means exactly
    ``alpaca_live`` minus the two declared affordability proxies, and that is
    why the name still says ``minus_proxies``.

    ⚠️ THE EMPTY-LIVE STATE IS NOW DECIDED: EQUALITY WINS, NO EXCEPTION.
    OPERATOR, 2026-09-21, asked directly and answered directly. If
    ``alpaca_live`` returns to ``strategies: []`` — which Gate 2 can produce by
    demoting the last live leg, and which held 2026-08-29..08-31 — then the
    mirror is empty too.

    THE ARGUMENT AGAINST IT IS KEPT HERE ON PURPOSE, because the cost is real
    and specific and a later session must read a DECISION rather than an
    absence: at that moment the Alpaca demotion signal Gate 2 exists to read
    disappears along with the mirror, and **nothing flags the gap**. An earlier
    draft of this test asserted a non-empty mirror so CI would go red and name
    the question; the operator was shown that and chose the clean rule over the
    standing question. Accepted cost, not an oversight.

    So there is exactly ONE assertion below. Do not reintroduce a non-empty
    guard without a new operator decision — it would be reopening this one.
    """
    accts = _accounts()
    portfolio, real = accts["alpaca_portfolio"], accts["alpaca_live"]
    expected_strats = _expected_mirror_roster(real)
    port_strats = list(portfolio.get("strategies") or [])

    assert port_strats == expected_strats, (
        "alpaca_portfolio must mirror alpaca_live's roster exactly, minus the "
        f"affordability proxies {_ALPACA_PROXY_STRATEGIES} — same mechanism as "
        "test_bybit_portfolio_mirrors_bybit_2_exactly (ROSTER-SYNC, B2 "
        "2026-09-21). Order matters, as it does for bybit.\n"
        f"  expected (alpaca_live minus proxies, n={len(expected_strats)}): {expected_strats}\n"
        f"  actual   (alpaca_portfolio,          n={len(port_strats)}): {port_strats}\n"
        f"  surplus on the mirror ({len(set(port_strats) - set(expected_strats))}): "
        f"{sorted(set(port_strats) - set(expected_strats))}\n"
        f"  missing from the mirror ({len(set(expected_strats) - set(port_strats))}): "
        f"{sorted(set(expected_strats) - set(port_strats))}"
    )

    # No proxy strategy leaks in.
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
