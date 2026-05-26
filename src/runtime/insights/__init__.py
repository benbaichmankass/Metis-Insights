"""AI Analyst generator package (M13 S1).

The router (``src/web/api/routers/insights.py``) is a cache-only read
path. This package is the writer-side: it joins trade/signal/health
state, builds prompts, calls the Anthropic API, and writes the cache
files the router serves. It is invoked by the
``ict-insights-generator`` systemd timer (every ~10 min), NEVER by
the request path.

Public surface:

- ``generate(endpoint, **kwargs)`` — generate one endpoint's cache.
  Returns the written payload, or ``None`` if the generator is
  disabled or the Anthropic call failed (in which case the
  previous cache file is left untouched).
- ``main(argv)`` — CLI entry; ``python -m src.runtime.insights …``.
"""
from __future__ import annotations

from src.runtime.insights.generator import generate, main

__all__ = ["generate", "main"]
