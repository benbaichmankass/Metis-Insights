# AUD-8 — lane brief (E75 system audit, 2026-09-29)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Dispatch brief written by AUD-LEAD session_01XDNUbSGVQoJCGqtWEW1Fzj.

## Common brief (every AUD lane)

WHO YOU ANSWER TO. You are lane AUD-8 of the E75 system audit. You were dispatched by the audit lead session_01XDNUbSGVQoJCGqtWEW1Fzj (AUD-LEAD). The lead was dispatched by the manager, session_01KAQRJxRbRwpYTkPgBjNVyQ, which acts with the operator's authority. The operator's GO, 2026-09-29 ~09:50Z, verbatim: "Launch now (Recommended)". The audit's shape was decided on 2026-09-27, row E75, decisions D7–D10. Your record on main is checklist row AUD-8 and row E75 in docs/claude/work/MANAGER-CHECKLIST.json. They are landing in PR #14050; if that PR has not merged yet, the E75 row's 2026-09-27 note already on main is the record. Check once, then work. Rules: docs/CLAUDE-RULES-CANONICAL.md § "Lanes answer to the manager" and § "Reading is never gated". Read CLAUDE.md first.

THE REPO IS PUBLIC. Never print, commit or quote secrets, tokens, account numbers or personal data. For anything secret-shaped, give the location and the kind, redacted.

YOUR ONE QUESTION: Is the 2026-09-21 reset's promise true TODAY, measured: that an item comes due on the operator's own page and stays there until routed?

SPEC. docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md §4.8 (method) and §3/§5 (constraints and finding schema). Read both.

DO NOT REDO THE 2026-09-27 CODE AUDIT. Report: docs/audits/code-audit-2026-09-27.md. Lane files: docs/audits/code-audit-2026-09-27/CA-*.{md,findings.jsonl}. Where your area overlaps it, cite the CA finding id; do not re-derive it. Your value is what CA did NOT do: live-data traces, runtime behaviour, cross-component seams, the operating model, and anything CA listed under §9 "Not covered". Also check whether CA's merged fixes are OBSERVED working on the fleet. Merged, deployed and observed are three different facts; say which one you established. Overlap for your area: CA-B09 compared skills and docs against code. CLAUDE.md itself says the pipeline 'pull' is built but NOT connected (A3/A7). MEASURE: (a) `python3 scripts/ops/pipeline.py` due/unrouted report over docs/claude/work/PIPELINE.jsonl, with the count and the oldest due item; (b) whether render_section_0() is actually called by what serves the operator's brief. Read /api/bot/work/brief live (curl https://ict-bot.duckdns.org/api/bot/work/brief, or via the diag relay) and check that section 0 lists the due items; (c) a count of references to archived registers (session-board.json, SESSIONS.json, MANAGER-LEASE.json, MERGE-QUEUE.json, OPEN-ITEMS.json, DUE.md, the four review backlogs) in live skills, workflows, scripts and docs outside docs/archive, compared with the E29 baseline of 40 paths across ~60 files on 2026-09-22; (d) checklist rows whose state contradicts reality (in_flight with an archived lane; landed_unproven with no named observation); (e) whether the /api/bot/work/checklist page serves the current main. Note that the SessionStart hook still advertises /health-review as the 'MASTER SYSTEM REVIEW' and cites archived backlog files: is that a stale-instruction finding?

LIVE DATA. See docs/CLAUDE-RULES-CANONICAL.md § "AUTONOMY MANDATE".
- Live trader: open a GitHub issue labelled `vm-diag-request`, titled `[diag-request] <path under /api/diag/>`. Leave the BODY EMPTY, or put exactly one path per line. Prose in the body breaks the run. Poll the issue's comments.
- Trainer VM: an issue labelled `trainer-vm-diag-request`, with body `cmd: |` followed by indented bash.
- Skills: .claude/skills/diag-data, vm-ops, git-actions.
- On 2026-09-27 the diag token returned HTTP 401 from ~14:45Z. If a relay fails, record it as could-not-look, with the issue number. Never infer what a read would have shown. Close each diag issue once you have read it.
- GitHub MCP tools drop intermittently. Retry with backoff: 2s, 4s, 8s, 16s.

FINDINGS. One JSON object per line in docs/audits/system-audit-2026-09-29/AUD-8.findings.jsonl, with exactly these keys:
id ("SA-AUD-8-<slug>"), axis, severity (critical|high|medium|low|info), claim (one falsifiable sentence), evidence (the exact command, query or issue #, AND its actual output, trimmed), expected, actual, population (what was measured over, with n), blast_radius (money-at-risk|accounting|observability|docs), tier (1|2|3), detector (the guard, self-test or alarm that would catch a recurrence, or the stated reason none is possible), proposed_fix (exact files and change, or "judgment: <options>"), ca_ref (CA id it extends, or null).
- Prose-only findings are rejected.
- Verified non-issues also go in, with severity "info" and a claim starting "NON-ISSUE:". A clean negative needs a denominator, and a probe shown able to find a positive.
- Severity: critical = real money at risk now; high = money-at-risk or accounting wrong on a live path.

REPORT. docs/audits/system-audit-2026-09-29/AUD-8.md with these sections:
(1) the question and a one-paragraph answer;
(2) findings table;
(3) COVERAGE, stated both ways: behavioural (capabilities exercised end to end against real data / capabilities asserted) and reading (files read, and files explicitly NOT read);
(4) could-not-look list;
(5) spend note.

YOU ONLY FIND. Do not fix anything. Do not touch src/, config/, deploy/, .github/workflows/, tests/ or scripts/ except in a throwaway scratch copy. Do not write docs/claude/work/PIPELINE.jsonl or the checklist; the lead files. Do not place, cancel or modify any order. Do not dispatch any system-action other than a Tier-1 read.

LANDING. Tier 1, self-landing.
1. Make branch audit/sa-2026-09-29-aud8 from origin/main.
2. Register your .md in docs/DOCUMENT-INDEX.md: run `python3 scripts/ci/check_document_index.py` and add the exact one line it prints for your file (do NOT run --restamp). Commit your two files, the index line, plus the three landing files:
   - .github/pr-landing/audit-sa-2026-09-29-aud8.json: {"tier":1,"landing":"self","why":"<one line: audit findings docs only>"}
   - .github/pr-automerge-requests/audit-sa-2026-09-29-aud8.txt: one line.
   - the merge-slot claim: `python3 scripts/ops/claim_merge_slot.py --branch-claim --branch audit/sa-2026-09-29-aud8 --held-by <your session id> --purpose "E75 AUD-8 findings"`
3. Push and open a draft PR titled "audit(E75): AUD-8 findings — <n> findings".
4. Do NOT subscribe to the PR, and do not wait on CI. The lead and the manager land it.
5. Commit early. If you are near your ceiling or your session window, land what you have with the gaps stated. Partial and landed beats complete and lost.

CEILING: $15. You cannot read your own dollar meter, so budget by work. Keep live-relay round trips to what the question needs. Read excerpts, not whole large files.

WHEN DONE:
1. Report to the lead: mcp tool create_trigger(persistent_session_id="session_01XDNUbSGVQoJCGqtWEW1Fzj", name="AUD-8 done", prompt="AUD-8 landed: see PR <n>", initiation="own_followup"). Then fire_trigger(trigger_id) with NO text argument.
2. End with a final message of at most 10 lines: PR number; counts by severity; each critical/high as one line with id and claim; could-not-look items.
