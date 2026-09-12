MI-278 U5 arming note — this file exists to arm CI, and that is its whole purpose.

`claude-pr-automerge` opened PR #12031 under `GITHUB_TOKEN` because the arming
file rode the same push as the content. GitHub does not fire workflows for a
`GITHUB_TOKEN` push, so the PR was born carrying exactly one check — the
workflow's own `open-and-automerge` — and no `pull_request` event. A PR in that
state reads as "CI has not started yet" and never will.

The documented remedy is one ordinary commit, which this is. The durable fix is
to open the PR yourself FIRST and push the arming file second; that ordering is
in CLAUDE.md and it is what U14 (#12019) did correctly an hour earlier. It did
not transfer here because the arming files were written before the push rather
than after it.
