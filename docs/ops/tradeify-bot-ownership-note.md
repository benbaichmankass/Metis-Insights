# Tradeify 247 — bot ownership note (tradeify_1)

> **Doc status:** `live` · category `lookup` · last verified `2026-10-07` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

Operator decision, 2026-10-07 (manager popup), verbatim label:
*"Ownership note ready, nothing else (Recommended)"*. This closes pipeline item
`PI-20261004-OZ9AAZSV-0003`. Source of the question: the Tradeify 247 Funded Trader
Agreement, Annex B.18.1 (the Company may require evidence of ownership or
exclusive licence, *including production of the tool's source files*), clause 8.4
(a live video interview with the risk or compliance team may be a condition of a
payout) and Annex B.18.3 (bots or tools shared among, or executing the same
signals for, multiple traders are prohibited). Re-read the live agreement before
relying on these paraphrases. This note states facts; it is not legal advice.

**Nothing below was verified against Tradeify's systems.** Sections 2 and 3 say
what to run and where; the outputs are produced on demand, not stored here.

---

## 1. Statement (hand-over text)

> The trading software that places orders on my Tradeify 247 account is
> developed and solely owned by me. Its source code is kept in the Git repository
> `https://github.com/benbaichmankass/Metis-Insights`, which I own and which
> contains its full development history. The software is not shared with, sold
> to, or licensed to any other trader. No other account executes its signals
> or follows its trades, to my knowledge. It runs on infrastructure I operate,
> from the `main` branch of that repository, and the orders and fills on my
> Tradeify 247 account are produced by that software's prop executor. I can
> produce the source files, the commit history, the deployment record and the
> account-level trade records on request.
>
> Name: ____________  Date: ____________

The sentence "to my knowledge" is deliberate: the repository is public (§3), so
the owner can state that they have not given anyone access to run it, but cannot
state what a stranger has done with a copy.

## 2. Evidence — how to produce each item

Run from a **full** clone. A shallow clone (the cloud sessions use one) shows a
truncated history and a wrong "first commit"; run `git fetch --unshallow origin`
first and check `git rev-parse --is-shallow-repository` prints `false`.

### 2a. Git history

| Evidence | Command | Verified 2026-10-07 on a full clone |
|---|---|---|
| Repository URL | `git remote get-url origin` | `https://github.com/benbaichmankass/Metis-Insights` (renamed from `ict-trading-bot` on 2026-07-23; the old name redirects) |
| First commit | `git log --reverse --format='%h %ad %an <%ae>' --date=short \| head -1` | `5110d44c6`, 2026-03-22 |
| Latest commits | `git log --format='%h %ad %an %s' --date=short \| head -20` | — |
| Commits by author | `git shortlog -sne HEAD` | see caveat |
| Total commits | `git rev-list --count HEAD` | 8,797 at the time of writing |

Caveat, to be settled **before** handing anything over: the history carries
several author names/emails for the same development effort, plus commits
authored by `Claude` (AI-assisted work, co-authored trailers) and by
`github-actions[bot]` (automation). One author name (`the-lizardking`, ~100
commits) is not identified anywhere in this repository. The operator should
write down which names/emails are theirs (and that the AI and bot commits were
made on their instruction) so the mapping is ready if asked. This note does not
assert that mapping.

GitHub-side evidence the owner can show from their own login: repository
settings (owner, collaborators list, visibility), and the Insights → Contributors
and Traffic → Clones pages.

### 2b. Deployment record

The live trader runs this repository's `main`; a git-sync timer on the trading
VM pulls `main` every ~5 minutes (`CLAUDE.md` § "How work is organised"). Docs
that show the deploy path (they name the services and topology; do not hand over
host addresses unless asked):

- [`docs/reference/vm-topology.md`](../reference/vm-topology.md) — which VM runs what, and the authority split.
- [`docs/deployment.md`](../deployment.md) — how a change reaches the VM.
- [`docs/github-actions-workflows.md`](../github-actions-workflows.md) and [`docs/claude/system-actions.md`](../claude/system-actions.md) — the workflow-driven deploy/restart actions and their run history on GitHub (Actions tab).
- [`docs/runbooks/tradeify-integration.md`](../runbooks/tradeify-integration.md) — the account wiring and go-live steps for this account.

Live proof is a screen-share of the VM's checkout at the commit shown by
`git rev-parse HEAD`, compared with the same commit on the GitHub `main` branch.

### 2c. Account-level evidence

`tradeify_1`'s tickets and fills come from this repository's prop executor
(`src/prop/`, config in `config/accounts.yaml` entry `tradeify_1` and
`config/prop_platforms.yaml`). The prop journal (`src/prop/prop_journal.py`,
tables in the canonical trade journal DB) records the chain:

- `prop_tickets` — one row per order the bot emitted: `ticket_id`, `account_id`
  (= `tradeify_1`), `strategy`, `symbol`, `side`, `entry`, `sl`, `tp`, `qty`,
  `signal_time`, `order_package_id`, `status`, `created_at`.
- `prop_fills` — what the venue reported back: `account_id`, `ticket_id`,
  `external_order_id` (the venue's own order id), `symbol`, `entry_price`,
  `exit_price`, `pnl`, `status`, `opened_at`, `closed_at`.

The link is `prop_fills.ticket_id → prop_tickets.ticket_id`, and
`prop_tickets.order_package_id` ties each ticket to the strategy signal that
produced it. Matching `external_order_id` and timestamps against the account's
own trade history on Tradeify's platform shows that each venue order was
emitted by this software. Read-only access is through the diag relays and Data
Explorer (see `docs/reference/diag-access.md`, `db-wiring` skill). Export only
rows where `account_id = 'tradeify_1'`, and strip anything beyond what is asked.

## 3. Public-repo similarity risk — accepted

The repository is public, so anyone can read the strategy code and could run a
copy. If a third party ran the same signals on another Tradeify 247 account, an
order-similarity scan would see two accounts executing the same signals (the
B.18.3 pattern), and the source being public would not by itself show which
account is the owner's.

**Accepted as low by operator decision, 2026-10-07** ("Ownership note ready,
nothing else"). No change is made to the repository's visibility or contents.
The popup recorded the decision only, not a reason; none is asserted here.

**What would change that assessment** (any one is enough to reopen the decision):

1. A second Tradeify 247 account — anyone's — found running the same signals.
2. Tradeify requests the source files, a similarity explanation, or the 8.4
   interview and the answer would depend on repository visibility.
3. The Tradeify 247 agreement's B.18 wording changes.

If one occurs: file a pipeline row (`scripts/ops/pipeline.py`) rather than
acting in chat; options then include making the repository private and moving
prop-routing parameters out of the public tree.
