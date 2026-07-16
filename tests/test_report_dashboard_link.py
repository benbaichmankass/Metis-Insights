"""Guard: the system-report deep-link points at the new dashboard SPA.

Operator directive 2026-07-16 — "update the report links that get sent to the
new website". The Telegram system-report ping's `artifacts.dashboard_link`
must open the report on the **Svelte SPA hosted on GitHub Pages**
(`https://benbaichmankass.github.io/ict-trader-dashboard/?report=<id>`), which
reads the `?report=` query param on load. This test locks that in so the link
can't silently revert to the retired Streamlit base URL.

The SPA + Streamlit apps share the same `?report=` scheme, so only the base
URL differs — this guard keys on the host, not the whole string.
"""
from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATE = _REPO / "comms" / "schema" / "system_report_response.template.json"
_SPA_HOST = "benbaichmankass.github.io/ict-trader-dashboard"
_STREAMLIT_HOST = "ict-trader-dashboard-z67ryan2ttrxjdvk6ozcjc.streamlit.app"


def test_report_template_dashboard_link_targets_spa():
    template = json.loads(_TEMPLATE.read_text())
    link = template["artifacts"]["dashboard_link"]
    assert _SPA_HOST in link, f"dashboard_link must target the SPA, got: {link}"
    assert _STREAMLIT_HOST not in link, "dashboard_link must not point at the retired Streamlit base URL"
    assert "?report=" in link, "dashboard_link must carry the ?report= deep-link param"
