"""Config-driven enumeration of the Bybit accounts the broker-truth cost
sweep should pull fills + funding for.

Broker-truth cost coverage (rec #7 of ``docs/research/roadmap-toolbox-assessment-2026-07-29.md``):
the fills/funding pullers (``scripts/pull_exchange_fills.py`` /
``scripts/pull_exchange_funding.py``) + their daily systemd timers historically
hard-coded ``--account bybit_2``, so ``bybit_1`` and ``bybit_portfolio`` never
accrued exchange-truth fees/funding even though they trade the Bybit venue daily.
This helper enumerates every LIVE Bybit account from the canonical
``config/accounts.yaml`` so a roster change (a new Bybit account, a symbol edit)
needs no puller/unit edit — it is picked up automatically on the next daily run.

Uses the canonical ``src.config.accounts_loader.load_accounts_dict`` reader (the
``canonical-config-loaders`` CI guard forbids a hand-rolled ``accounts.yaml``
parser). Read-only, side-effect-free.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config.accounts_loader import account_is_demo, load_accounts_dict


@dataclass(frozen=True)
class BybitFillAccount:
    """One live Bybit account the cost sweep should pull for.

    ``key_env`` / ``secret_env`` are the ENV-VAR NAMES holding this account's
    credentials (never the secret values) — resolved from ``accounts.yaml``'s
    ``api_key_env`` field, so each account authenticates with its own key pair.
    ``category`` is the Bybit V5 product category (``linear`` for USDT-margined
    perps, else ``spot``). ``symbols`` is the account's declared instrument list
    (used by the funding pull, which Bybit only serves per-contract).

    ``demo`` mirrors ``accounts.yaml::demo`` — Bybit demo trading lives on a
    SEPARATE host and a demo key is rejected by mainnet with ``retCode 10003``.
    This field was missing until 2026-08-07, which is why the roster fix this
    module shipped (enumerating every live Bybit account instead of hard-coding
    ``bybit_2``) did not actually give ``bybit_1`` / ``bybit_portfolio``
    coverage: they were enumerated, dialled on the wrong host, and failed 100%
    of every run (BL-20260807-BYBIT-DEMO-FILLS-NEVER-PULLED). Enumerating an
    account is not the same as being able to reach it.
    """

    account_id: str
    key_env: str
    secret_env: str
    category: str
    symbols: tuple[str, ...]
    demo: bool = False


def _secret_env_for(key_env: str) -> str:
    """Derive the API-secret env-var name from the API-key env-var name.

    Convention (matches the existing ``pull_exchange_fills_action.sh``):
    ``BYBIT_API_KEY_2`` → ``BYBIT_API_SECRET_2``; ``BYBIT_API_KEY`` →
    ``BYBIT_API_SECRET``. Falls back to appending ``_SECRET`` for any other shape.
    """
    if "API_KEY" in key_env:
        return key_env.replace("API_KEY", "API_SECRET", 1)
    if "KEY" in key_env:
        return key_env.replace("KEY", "SECRET", 1)
    return key_env + "_SECRET"


def live_bybit_fill_accounts(
    path: Path | str | None = None,
) -> list[BybitFillAccount]:
    """Return every ``mode: live`` Bybit account from ``config/accounts.yaml``.

    Includes paper-class Bybit accounts (``bybit_1``, ``bybit_portfolio``) —
    they trade the real Bybit demo/live venue and so have real exchange-side
    fills + funding worth capturing for cost truth. ``dry_run`` accounts are
    excluded (they place no live orders). Returns ``[]`` on any config read
    failure (the canonical loader degrades gracefully), so a caller loops over
    nothing rather than crashing.
    """
    out: list[BybitFillAccount] = []
    for account_id, cfg in load_accounts_dict(path).items():
        if str(cfg.get("exchange", "")).lower() != "bybit":
            continue
        if str(cfg.get("mode", "")).lower() != "live":
            continue
        key_env = str(cfg.get("api_key_env") or "BYBIT_API_KEY")
        secret_env = str(cfg.get("api_secret_env") or _secret_env_for(key_env))
        category = "linear" if str(cfg.get("market_type", "")).lower() == "linear" else "spot"
        raw_symbols = cfg.get("symbols") or []
        symbols = tuple(str(s) for s in raw_symbols) if isinstance(raw_symbols, list) else ()
        out.append(
            BybitFillAccount(
                account_id=str(account_id),
                key_env=key_env,
                secret_env=secret_env,
                category=category,
                symbols=symbols,
                demo=account_is_demo(cfg),
            )
        )
    return out


@dataclass(frozen=True)
class AlpacaFillAccount:
    """One live Alpaca account the cost sweep should pull fills for.

    ``key_env`` / ``secret_env`` name the env vars holding this account's
    credentials; ``env`` (``paper``/``live``) selects the Alpaca host. Alpaca's
    default credential naming is asymmetric (``ALPACA_API_KEY_ID`` /
    ``ALPACA_API_SECRET_KEY``), so the secret env is taken from the explicit
    ``api_secret_env`` or that default — NOT key→secret string-derived.
    """

    account_id: str
    key_env: str
    secret_env: str
    env: str


def live_alpaca_fill_accounts(
    path: Path | str | None = None,
) -> list[AlpacaFillAccount]:
    """Return every ``mode: live`` Alpaca account from ``config/accounts.yaml``.

    The live Alpaca accounts (``alpaca_paper``, ``alpaca_portfolio``,
    ``alpaca_options_paper``) are DISTINCT Alpaca sub-accounts with distinct
    credentials, so their fills never double-count. ``dry_run`` (``alpaca_live``)
    is excluded. Returns ``[]`` on any config read failure.
    """
    out: list[AlpacaFillAccount] = []
    for account_id, cfg in load_accounts_dict(path).items():
        if str(cfg.get("exchange", "")).lower() != "alpaca":
            continue
        if str(cfg.get("mode", "")).lower() != "live":
            continue
        key_env = str(cfg.get("api_key_env") or "ALPACA_API_KEY_ID")
        secret_env = str(cfg.get("api_secret_env") or "ALPACA_API_SECRET_KEY")
        env = str(cfg.get("alpaca_env") or "paper").lower()
        if env not in ("paper", "live"):
            env = "paper"
        out.append(
            AlpacaFillAccount(
                account_id=str(account_id),
                key_env=key_env,
                secret_env=secret_env,
                env=env,
            )
        )
    return out


@dataclass(frozen=True)
class IBFillAccount:
    """One live IBKR account the broker-truth executions pull should cover.

    Unlike the Bybit/Alpaca siblings this carries **no credential env vars**:
    an IB account authenticates through the Gateway session (host/port +
    ``ib_account``), not an API key pair, so there is nothing to resolve from
    the environment. The account dict itself is carried through as ``config`` so
    the puller can hand it straight to
    :func:`src.units.accounts.clients.ib_read_client_for` — the canonical
    read-only client factory — instead of re-deriving host/port/account.
    """

    account_id: str
    ib_account: str
    config: dict


def live_ib_fill_accounts(
    path: Path | str | None = None,
) -> list[IBFillAccount]:
    """Return every ``mode: live`` IBKR account from ``config/accounts.yaml``.

    Today that is ``ib_paper`` alone (``ib_live`` is ``dry_run`` / shelved) —
    the account with **zero** broker-truth PnL coverage until this pull existed
    (IBKR historical-candle coverage is 0% too, so every IB close would
    otherwise be a *declared unmeasured* gap once the Tier-2 sweep change
    stopped it being priced from a mark). Enumerated from config rather than hardcoded so
    a roster change (``ib_live`` going live, a second IB account) is picked up
    with no puller edit — the same rule ``live_bybit_fill_accounts`` follows.

    An account with no ``ib_port`` is skipped: ``ib_read_client_for`` returns
    ``None`` for it, so it could never be pulled anyway. Returns ``[]`` on any
    config read failure (the canonical loader degrades gracefully).
    """
    out: list[IBFillAccount] = []
    for account_id, cfg in load_accounts_dict(path).items():
        if str(cfg.get("exchange", "")).lower() != "interactive_brokers":
            continue
        if str(cfg.get("mode", "")).lower() != "live":
            continue
        if not cfg.get("ib_port"):
            continue
        out.append(
            IBFillAccount(
                account_id=str(account_id),
                ib_account=str(cfg.get("ib_account") or ""),
                config=dict(cfg),
            )
        )
    return out
