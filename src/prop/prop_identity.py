"""The single canonical "is this a prop account?" predicate.

BL-20260628-PROP-ISPROP-PREDICATE-DRIFT. Three divergent copies of this test
had grown across ``src/prop/``, each recognizing a DIFFERENT subset of the
prop-account signals:

  * ``account_rulesets.py`` — ``backtest_ruleset != "standard"`` OR
    ``exchange == "breakout"`` (case-SENSITIVE); blind to ``account_class`` /
    ``type``.
  * ``telegram_report_handler.py`` — ``exchange`` / ``account_class`` only.
  * ``prop_journal.py`` — the full four-signal union.

Divergent copies of one rule are exactly the drift class that produced the
recurring sizing bug (see ``docs/sizing-legalization-DESIGN.md`` § 6): a fix or
a new signal added at one site silently doesn't reach the others. This module
is the one home for the rule; every caller routes through it.

The predicate is the **union** (the superset the journal already used): an
account is prop if ANY of these hold. Recognizing more accounts as prop is the
fail-safe direction — a prop account MIS-classified as ``standard`` is the
dangerous error (it would size against the wrong ruleset and leak into the
real-money/paper KPIs the prop journal is meant to isolate), whereas the
reverse merely over-scopes a prop-only helper. Case-insensitive and null-safe
throughout.

Prop-account model + the account→ruleset binding this feeds:
``docs/integrations/prop-accounts-architecture-DESIGN.md`` and
``src/prop/account_rulesets.py``.
"""
from __future__ import annotations

import logging
from typing import Any, List, Mapping, Optional

logger = logging.getLogger(__name__)


def is_prop_account(account: Mapping[str, Any]) -> bool:
    """True when *account* (a config dict) is a prop-firm account.

    Any ONE of these signals classifies it as prop:

      * ``exchange == "breakout"`` — the prop manual-bridge connector key;
      * ``account_class == "prop"`` — the funding-category axis;
      * ``type == "prop"`` — the legacy account-type tag;
      * ``backtest_ruleset`` set to anything other than ``"standard"`` — an
        explicit prop ruleset binding.

    All comparisons are case-insensitive and tolerate missing keys / non-str
    values. A non-mapping input is ``False`` (never raises).
    """
    if not isinstance(account, Mapping):
        return False
    if str(account.get("exchange", "")).strip().lower() == "breakout":
        return True
    if str(account.get("account_class", "")).strip().lower() == "prop":
        return True
    if str(account.get("type", "")).strip().lower() == "prop":
        return True
    spec = account.get("backtest_ruleset")
    if spec and str(spec).strip().lower() != "standard":
        return True
    return False


def declared_prop_account_ids(
    *, live_only: bool = False,
) -> Optional[List[str]]:
    """Every prop account id declared in ``accounts.yaml``.

    The enumeration counterpart of :func:`is_prop_account`. It exists because
    the ``load_accounts_dict()`` + ``is_prop_account`` walk had already been
    written twice (``prop_journal._prop_scope``,
    ``telegram_report_handler.default_prop_account``) and a third copy is how
    the drift this module's docstring describes starts over one level up.

    ``live_only`` narrows to accounts declared ``mode: live`` — the ones the
    bot actually emits tickets for. A ``dry_run`` prop account has no live
    exposure to protect, so a guard that nags about it is noise.

    **The return is three-state, deliberately** (``docs/CLAUDE-RULES-CANONICAL.md``
    § "Collapsed states"): ``None`` means *we could not look* — ``accounts.yaml``
    failed to load — while ``[]`` means *we looked and there are no prop
    accounts*. Collapsing the two would let a config-read failure present as
    "this system has no prop accounts", which for a safety guard is the
    dangerous direction: it reads as a clean negative and the caller stops
    asking. Callers that genuinely cannot act on the difference may treat
    ``None`` as empty, but they must say so at the call site.

    Order follows ``accounts.yaml``; ids are returned verbatim (never
    lower-cased — an account id is a key, not a classification signal).
    """
    try:
        from src.config.accounts_loader import load_accounts_dict

        accts = load_accounts_dict() or {}
    except Exception as exc:  # noqa: BLE001 — a config read must not raise here
        logger.warning("prop_identity: accounts.yaml load failed: %s", exc)
        return None

    out: List[str] = []
    for aid, a in accts.items():
        if not isinstance(a, Mapping) or not is_prop_account(a):
            continue
        if live_only and str(a.get("mode", "")).strip().lower() != "live":
            continue
        out.append(str(aid))
    return out


__all__ = ["is_prop_account", "declared_prop_account_ids"]
