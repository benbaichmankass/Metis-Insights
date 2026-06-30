"""Signal-research framework (Tier-1, read-only research tooling).

Every module here READS the trade journal / config and WRITES report files
under ``runtime_logs/signal_research/``. Nothing in this package touches the
live order path, ``config/strategies.yaml``, ``config/accounts.yaml``, or any
money-DB write. See ``docs/research/signal-research-framework-DESIGN.md``.

P0 (this commit): the canonical component-vector adapter
(``component_vector``) + the live graded-component edge report
(``scripts/research/component_edge_report.py``).
"""
