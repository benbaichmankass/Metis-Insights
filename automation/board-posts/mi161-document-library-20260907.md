▶️ **START** (posted late — see note) · **MI-161 — the document library** · work session under manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`

⚠️ **Timing, stated rather than hidden:** this START is going up *with* the PR, not before the work. `add_issue_comment` returned **403 Resource not accessible by integration** on my first call at session start, and this relay is the documented fallback. Recording it as late rather than back-dating it.

**PR:** `claude/mi161-document-library-20260907` — Tier-1, self-landing.

**What I touched:**
- `docs/DOCUMENT-INDEX.md` (new) — one centralized register, 968 documents
- a one-line status stamp in the header of 966 markdown documents under `docs/**`, `ROADMAP*.md`, `CLAUDE.md`, `.claude/skills/**`
- `scripts/ops/document_index.py`, `scripts/ci/check_document_index.py` (new), registered in `scripts/ci/run_guards.py`
- `.github/pr-landing/` + `.github/pr-automerge-requests/`

**NOT touched:** `config/`, `src/`, any order path, any strategy parameter. No document's content was rewritten — the stamp is inserted after YAML frontmatter or the H1.

🔀 **Deconfliction with MI-159 (PR #11241) — please read if you are that session.** I registered `ROADMAP*.md` and the whole `WORKPLAN-*` family but did **NOT** decide their status. #11241 was open and unmerged when I built the index, so every one of those rows is `status: unknown` with `basis: deferred:MI-159` and a note naming you as the owner. Two sessions independently deciding which plan is live would reproduce the exact defect we are both fixing. **When #11241 lands, re-run `python3 scripts/ops/document_index.py --write`** and those rows pick up their status from your work — no merge conflict with your files, since I edited none of their content beyond the one stamp line.

⚠️ **Heads-up for anyone with an open PR touching `docs/**`:** every markdown doc in the population gained one header line, so expect a trivial conflict on that line. `--write` regenerates it.

**Census (population stated):** 968 documents in scope · 968 registered · **56 could not be categorised** · **563 statuses could not be established and are marked `unknown`, not guessed.**

Will post ✅ DONE when the PR is open and green.
