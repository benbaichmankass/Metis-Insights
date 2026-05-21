"""Marks the test suite as a regular package.

Without this file ``tests`` is an implicit namespace package, which loses
name resolution to any *regular* top-level ``tests`` package that a
dependency ships in site-packages. ib_insync pulls in eventkit, whose
wheel ships its own top-level ``tests`` package (aggregate_test.py,
event_test.py, ...); that shadowed this directory and broke
``pytest_plugins = ("tests.fixtures.real_schema_db",)`` in
tests/conftest.py with "No module named 'tests.fixtures'". Making this
directory a regular package restores precedence regardless of what a
dependency dumps into site-packages.
"""
