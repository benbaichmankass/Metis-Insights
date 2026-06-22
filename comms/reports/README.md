# `comms/reports/` — consolidated system reports

Artifacts written by the `/system-report` master skill
(`.claude/skills/system-report/SKILL.md`) via
`scripts/reports/render_system_report.py`. Each report is the executive
synthesis of the three reviews (`/health-review` + `/performance-review` +
`/ml-review`) over a time window.

## Layout

```
comms/reports/
  index.json                       # manifest, newest-first (created on first report)
  <window>/<UTC-ts>/
    report.json                    # the consolidated payload (schema below)
    report.html                    # self-contained responsive HTML (the deliverable link)
    report.md                      # lightweight markdown twin
```

`<window>` ∈ `since-last | daily | weekly | monthly`. `<UTC-ts>` is
`YYYYMMDDThhmmssZ`.

## Consumers

- **API:** `GET /api/bot/reports` (index) + `GET /api/bot/reports/{id}` (one
  report's HTML), file-backed and Tier-1 — `src/web/api/routers/reports.py`.
  The live VM's `ict-git-sync` mirrors `comms/` so the API serves committed
  reports.
- **Dashboard** (desktop) and **Android app** (mobile) render the index as a
  log of links and open the HTML.

## Schema

`comms/schema/system_report_response.template.json`. Spec / design:
`docs/reports/system-report-DESIGN.md`.

Reports are committed (same pattern as `comms/reviews/`) so each has a stable
GitHub link.
