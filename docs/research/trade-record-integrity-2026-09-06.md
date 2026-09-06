# Trade-record integrity — MI-144 (WO-20260906-THE-RECORD-OF-A-TRADE-DOES-NOT)

Status: IN PROGRESS (skeleton commit — pushed early so the analysis is not lost
to a push denial discovered at the end).

Three defects, one question: **does our record of a trade say what happened?**

- (a) R denominator contamination — `trades.stop_loss` is the FINAL trailed stop.
- (b) `exit_reason` frozen at close time, never re-classified.
- (c) `/api/bot/trades/closed` window behaviour.

Findings follow.
