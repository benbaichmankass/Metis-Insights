"""TRANSLATOR / Coordinator — S-008 PR #120.

Central routing layer between the 9 units defined in config/units.yaml.
No unit communicates with another unit directly; all cross-unit data flows
through this class.

Unit interface stubs (filled in by subsequent PRs):
  PR #121 → strategy_order_pkg()   DONE — src/units/strategies/<name>.py
  PR #122 → account_execute()      DONE — src/units/accounts/execute.py
