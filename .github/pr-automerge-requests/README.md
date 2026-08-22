One file per PR that wants the auto-merge relay.

Name it after your branch (`<branch-slug>.txt`); the content is never read — the
relay uses the path only as a trigger. A per-request file is what stops two PRs
conflicting on a single shared marker
(`BL-20260821-AUTOMERGE-TRIGGER-IS-A-SINGLE-SHARED-FILE`), and it is the
convention every sibling relay in this repo already follows.
