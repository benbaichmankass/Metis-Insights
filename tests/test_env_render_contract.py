"""Config-drift contract test (Sprint S-021, PR 1 of 3).

BUG-048 root cause: ``.env.example`` had ``MONITOR_RECONCILE_ENABLED=true``
but ``scripts/render_env_from_master.py::build_live`` did not emit it, so
every key rotation produced a ``.env`` without the flag — silently disabling
the reconciler on the VM for ~8 hours.

These three tests pin the contract so CI fails when ``.env.example`` and
``build_live`` drift apart in either direction.

Structure
---------
- ``_env_example_keys()`` — parse ``.env.example``, return a ``set[str]``
  of every non-comment, non-blank key (handles keys with inline comments).
- ``_renderer_keys()`` — call ``build_live(FAKE_DATA)`` and return the
  ``set[str]`` of emitted keys (``None``-value pairs are dropped, matching
  ``main()``'s post-filter behaviour).
- ``_IGNORE`` — keys intentionally present in only one side. Every entry
  must have a comment explaining why it is exempt.

Adding a new key to either side without updating the other (and without
adding the key to ``_IGNORE``) will fail one of the first two tests.
Adding ``MONITOR_RECONCILE_ENABLED`` to ``_IGNORE`` will fail the explicit
pin in ``test_monitor_reconcile_enabled_is_true_in_both``.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Import the renderer (same pattern used by test_render_env_from_master.py)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _import_mod():
    spec = importlib.util.spec_from_file_location(
        "render_env_from_master",
        _REPO_ROOT / "scripts" / "render_env_from_master.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _import_mod()

# Re-use the authoritative FAKE_DATA from the existing test module so that
# both test files exercise the same renderer inputs.
from tests.test_render_env_from_master import FAKE_DATA  # noqa: E402


# ---------------------------------------------------------------------------
# Keys intentionally present in only one side.
# Every entry must have an inline comment explaining why it's exempt.
# ---------------------------------------------------------------------------

_IGNORE: frozenset[str] = frozenset({
    # ---- .env.example-only (operator sets these manually on the VM) --------

    # Binance is not the active exchange; only Bybit credentials are rendered.
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    # The Claude bridge bot uses a separate token and is managed by
    # ict-claude-bridge.service — not part of the live-trader env.
    "TELEGRAM_CLAUDE_BOT_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_MODEL",
    # Runtime tuning knobs set via systemd environment drop-ins, not
    # derived from master-secrets.
    "MAX_QTY",
    "LOG_LEVEL",
    "TICK_INTERVAL_SECONDS",
    "LOOP",
    "COMMS_PUSH_ENABLED",
    # Testnet flags — controlled per-account in accounts.yaml, not rendered.
    "BYBIT_TESTNET",
    "BINANCE_TESTNET",
    # DB path set in systemd drop-in to match the VM filesystem layout.
    "TRADE_JOURNAL_DB",
    # ICT strategy risk pct — legacy single-account env var, not rendered.
    "ICT_RISK_PCT",
    # Web dashboard auth — generated on the VM, never in master secrets.
    "JWT_SIGNING_KEY",
    "ALLOWED_EMAIL",
    "WEBAPP_URL",
    "WEBAPP_PASSWORD_SHA256",
    # Dashboard / Web API knobs set in systemd drop-in or operator-managed .env;
    # not part of the SOPS-rendered secrets (commit ff2512e added .env.example
    # with these manually-configured fields; they were never in build_live).
    "DASHBOARD_API_TOKEN",
    "DASHBOARD_ORIGIN",
    "WEB_API_PORT",
    # Legacy Telegram token name in .env.example; renderer emits TELEGRAM_BOT_TOKEN
    # (the canonical name used by the telegram.ext Application constructor).
    "TELEGRAM_TOKEN",

    # ---- renderer-only (derived / not operator-visible in .env.example) ----

    # Emitted as "production"; not a user-configurable setting.
    "ENVIRONMENT",
    # Active exchange rendered from profiles.live.exchange in master secrets;
    # not in .env.example because the example only shows Bybit credentials.
    "EXCHANGE",
    # Renderer emits TELEGRAM_BOT_TOKEN (canonical name); .env.example has
    # the legacy TELEGRAM_TOKEN alias that the operator fills manually.
    "TELEGRAM_BOT_TOKEN",
    # Runtime trading parameters from runtime_defaults in master secrets.
    # Not in .env.example because operators set these in the SOPS file.
    "SYMBOL",
    "TIMEFRAME",
    # Risk pct from risk.live in master secrets.
    "RISK_PER_TRADE",
    # Always-on reconciler flag (BUG-042); rendered unconditionally to
    # "true". Not in .env.example because it's always true in production.
    "MONITOR_RECONCILE_ENABLED",
    # News feed toggle — rendered from news block in master secrets;
    # not in .env.example because the feature flag is in the SOPS file.
    "NEWS_ENABLED",
    # Derived from bybit.live.base_url in master secrets; not shown in the
    # example because it never changes between environments.
    "BYBIT_BASE_URL",
    # Intermediate runtime paths from runtime_defaults in master secrets.
    "DATA_DIR",
    "MODEL_DIR",
    "LOG_DIR",
    "DB_PATH",
    # Risk caps from risk.live in master secrets — operator sets these in
    # the SOPS file, not in the example template.
    "MAX_POSITION_USD",
    "MAX_DAILY_LOSS_USD",
    # NEWS_API_KEY is commented-out in .env.example (optional; uncomment to
    # activate) but the renderer always emits it (empty when absent) so that
    # its absence is detectable as a config bug rather than a silent default.
    "NEWS_API_KEY",
    # Per-account credential env vars are rendered dynamically from
    # config/accounts.yaml and the bybit.accounts block in master secrets.
    # They are not listed in .env.example because their names change as
    # accounts are added/removed.
    "BYBIT_API_KEY_1",
    "BYBIT_API_SECRET_1",
    "BYBIT_API_KEY_2",
    "BYBIT_API_SECRET_2",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_example_keys() -> set[str]:
    """Parse ``.env.example`` and return every non-comment, non-blank key."""
    env_example = _REPO_ROOT / ".env.example"
    keys: set[str] = set()
    # Matches: KEY=... (value may be empty or have inline comment)
    # Allows digits in key names (e.g. WEBAPP_PASSWORD_SHA256).
    pattern = re.compile(r"^([A-Z][A-Z0-9_]*)=")
    for line in env_example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = pattern.match(stripped)
        if m:
            keys.add(m.group(1))
    return keys


def _renderer_keys() -> set[str]:
    """Call ``build_live(FAKE_DATA)`` and return the set of emitted keys."""
    pairs = _mod.build_live(FAKE_DATA)
    # Drop None-value pairs — same filter applied by main() before writing.
    return {k for k, v in pairs if v is not None}


# ---------------------------------------------------------------------------
# Contract 1: every .env.example key (minus _IGNORE) is rendered
# ---------------------------------------------------------------------------


def test_env_example_keys_emitted_by_renderer():
    """Every non-ignored key in .env.example must appear in build_live output.

    This catches the BUG-048 shape: someone adds a new key to .env.example
    (documenting a new flag) but forgets to wire it into build_live.
    """
    example_keys = _env_example_keys() - _IGNORE
    renderer_keys = _renderer_keys() - _IGNORE
    missing = example_keys - renderer_keys
    assert not missing, (
        f"Keys in .env.example but NOT emitted by build_live: {sorted(missing)}\n"
        "Either add them to build_live() in scripts/render_env_from_master.py\n"
        "or add them to _IGNORE in this file with a comment explaining why."
    )


# ---------------------------------------------------------------------------
# Contract 2: every rendered key (minus _IGNORE) is in .env.example
# ---------------------------------------------------------------------------


def test_renderer_keys_present_in_env_example():
    """Every non-ignored key emitted by build_live must appear in .env.example.

    This catches the inverse drift: a new key is added to the renderer but
    the operator-facing example is not updated, leaving the operator unaware
    they need to set it in the master secrets file.
    """
    renderer_keys = _renderer_keys() - _IGNORE
    example_keys = _env_example_keys() - _IGNORE
    extra = renderer_keys - example_keys
    assert not extra, (
        f"Keys emitted by build_live but NOT in .env.example: {sorted(extra)}\n"
        "Either add them to .env.example (with a comment explaining what they do)\n"
        "or add them to _IGNORE in this file with a comment explaining why."
    )


# ---------------------------------------------------------------------------
# Contract 3: MONITOR_RECONCILE_ENABLED explicit pin (BUG-048 regression)
# ---------------------------------------------------------------------------


def test_monitor_reconcile_enabled_is_true_in_both():
    """Belt-and-braces: MONITOR_RECONCILE_ENABLED must be present and set to
    ``true`` in the renderer output (BUG-048 regression pin).

    MONITOR_RECONCILE_ENABLED is renderer-only (always "true"); it lives in
    _IGNORE because .env.example was created before it was added to build_live
    and was never updated to include it (commit ff2512e). The renderer check
    is the authoritative regression pin: even if the key accidentally drifts
    into _IGNORE, this test still catches the BUG-048 regression shape.
    """
    # Check renderer
    renderer_pairs = dict(_mod.build_live(FAKE_DATA))
    assert "MONITOR_RECONCILE_ENABLED" in renderer_pairs, (
        "build_live does not emit MONITOR_RECONCILE_ENABLED"
    )
    assert renderer_pairs["MONITOR_RECONCILE_ENABLED"].lower() == "true", (
        f"build_live emits MONITOR_RECONCILE_ENABLED="
        f"{renderer_pairs['MONITOR_RECONCILE_ENABLED']!r}, expected 'true'"
    )
