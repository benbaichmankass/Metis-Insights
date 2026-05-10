"""Allow `python -m ml ...` to dispatch the umbrella CLI."""
from .cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
