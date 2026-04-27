# Testing policy

## Local checks

```bash
PYTHONPATH=. pytest --collect-only -q tests
PYTHONPATH=. pytest -q tests
python scripts/secret_scan.py
```

## Remote checks

Delegate these unless explicitly requested locally:

- Full backtests.
- Large data validation.
- Training sessions.
- Live exchange smoke tests.

## Missing dependencies

If tests fail from missing optional packages, report the exact package and do not silently install broad dependency sets.
