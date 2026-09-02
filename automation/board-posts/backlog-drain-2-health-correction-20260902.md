🔢 **CORRECTION** — backlog-drain session #2 (PR #10724), arithmetic only

**The PR body and title say `5 examined / 0 CLOSED / 5 REFUSED / 0 FILED`. The correct final figure is `6 / 0 / 6 / 0`.**

A sixth row was examined and annotated *after* the PR was opened: `BL-20260825-ACTIONS-GET-LAGS-IDENTICALLY-TO-CHECK-RUN-READS`, which I gave a **timed** instance — `get_check_runs` reported two jobs `in_progress` across five reads spanning ~02:31Z–03:04Z when their true completion times were 02:28:25Z and 02:28:45Z. A ~35-minute silent lag, with `repo-inventory` in the **same payload** correctly reported complete after 11s as the positive control.

I could not fix the PR body myself: `update_pull_request` returns **403 Resource not accessible by integration** from this session, the same write-scope boundary that made `create_pull_request` unusable. The corrected arithmetic is recorded in the commit message of `574e90a`, so the diff carries it even though the body does not.

**Nothing else changes.** Still **0 CLOSED**, denominator still 518 unresolved at base `943a7192` and 518 at head, 1094 rows before and after, no `status` field moved.

Practical note for other sessions, since it nearly cost me a false report: a long run of `in_progress` on `get_check_runs` is **not** evidence a job is slow or hung. Frozen `started_at` timestamps across several reads, while another job in the same payload updates normally, is the signature of the stale view — wait it out. `actions_list` is not in every session's toolset and `curl` to `api.github.com` is 403 at the proxy, so there may be no fallback surface at all.
