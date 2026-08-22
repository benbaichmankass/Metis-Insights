One file per PR that wants the auto-merge relay.

Name it `<branch-slug>.txt`. The content is never read — the relay uses the path
only as a trigger. A per-request file is what stops two PRs conflicting on a
single shared marker (`BL-20260821-AUTOMERGE-TRIGGER-IS-A-SINGLE-SHARED-FILE`),
and it is the convention every sibling relay in this repo already follows.

⚠️ **The trigger matches `*.txt` only, deliberately — this README must not arm
it.** The first version of the fix globbed `**`, and adding this very file armed
auto-merge on the PR that introduced it (observed 2026-08-22T10:46:15Z). A
trigger path that a documentation edit can fire is a trigger that merges pull
requests nobody asked to merge.
