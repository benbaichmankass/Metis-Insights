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

from typing import Any, Mapping


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
