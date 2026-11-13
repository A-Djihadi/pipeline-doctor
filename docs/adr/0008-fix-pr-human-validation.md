# ADR-0008 : Fix proposals as draft PRs from a bot-owned fork, merged only by a human

- Status: proposed
- Date: 2026-11-13
- Amends: ADR-0001 §1 ("read-only")

## Context

Today the agent posts a diagnosis (cause, evidence, suggested fix in prose) to Slack. The v2
roadmap item asks it to **propose the fix as a pull request**, with a **human validation step
before merge**. This is the first time pipeline-doctor produces code that could end up in a
repository, so the threat model changes:

- The fix is written by a small local model (qwen2.5-coder:3b by default) from a **CI log, which
  is untrusted input**: anything a test prints can steer the model (prompt injection).
- A pull request branch is **executed by CI**. On GitHub, a PR from a branch of the *same*
  repository runs with the repository's secrets and a read/write-capable token; a PR from a
  *fork* runs without secrets, with a read-only token, and can be held until a maintainer
  clicks "Approve and run".
- A token that can push a branch to the repository can usually also merge through the API.
  "Never auto-merge" must be guaranteed by GitHub, not only by our code.
- ADR-0001: zero dependency. A GitHub App would give scoped installation tokens, but minting
  them requires signing an RS256 JWT, which the standard library cannot do.
- GitHub documents that fine-grained PATs cannot be used by repository collaborators nor to
  contribute to public repositories the user is not a member of; only classic PATs can.

## Options considered

1. **Same-repo branch, owner's fine-grained PAT** (`Contents: write`, `Pull requests: write`).
   Simple, but the LLM-written branch runs in CI with repository secrets before any human has
   read it; the token can merge; the owner cannot approve a PR authored with his own token.
2. **Same-repo branch, machine user as collaborator.** Merge is gated by a ruleset, but the
   branch still runs with secrets, and the bot needs a classic PAT with write on the repo.
3. **Bot-owned fork + draft PR to upstream, machine user with no rights on upstream.** The bot
   writes only to its own fork. It cannot push to, merge into, or change settings of the
   upstream repository — structurally. Fork-PR CI gets no secrets and a read-only token, and the
   owner can require approval before it even runs.
4. **GitHub App.** Best scoping and signed commits, but needs an RSA/JWT dependency (ADR-0001)
   or an external token broker. Rejected for v2.
5. **No PR, Slack only with a diff** ("propose" mode). Zero write, zero risk; the human applies
   the patch by hand.

## Decision

1. **Three modes, opt-in, default unchanged.** `PD_FIX_MODE=off` (default — exact v1
   behaviour), `propose` (generate and guard a patch, post the diff to Slack, journal it — no
   write anywhere), `pr` (same, then open a draft PR). Option 5 is `propose`; Option 3 is `pr`.
2. **`pr` mode uses a bot-owned fork (Option 3).** A dedicated machine user (`PD_FIX_FORK`,
   e.g. `pd-bot/pipeline-doctor`) owns a fork of the monitored repository and is **not** a
   collaborator on it. Its classic PAT (`PD_FIX_TOKEN`) has scope `public_repo` only — never
   `repo` (for a public target), never `workflow`, never `admin:*`. It is distinct from the
   read token `GITHUB_TOKEN`; the agent refuses to start in `pr` mode if both are equal.
3. **No local clone, no code execution.** The patch is computed in memory from file contents
   read through the API at the failing commit. pipeline-doctor never runs the target project's
   build or tests; the PR's own CI (fork context, no secrets) is the verification.
4. **The model edits by exact search/replace**, not free-form diffs: it returns
   `{"edits": [{"path", "search", "replace"}], "summary", "confidence"}`. Every `search` must
   occur exactly once in a file the agent offered; anything else rejects the whole proposal.
5. **Deterministic guards, all mandatory; any violation rejects the proposal (fail closed):**
   - eligibility: run on the upstream repo (not a fork PR), branch not prefixed
     `pipeline-doctor/` (loop guard), diagnosis from the LLM (not rules fallback), category in
     `{compilation, dependency, tests}`, every evidence line present verbatim in the log, no
     disagreement with the rules category, confidence ≥ 0.6, failing SHA still the branch tip;
   - inputs: candidate files only from the log and the category manifests, existing regular
     files (mode `100644`), ≤ 20 000 chars, UTF-8; a file in which the sanitizer finds a secret
     is never sent to the model nor edited;
   - path denylist: CI definitions (`.github/**`, `Jenkinsfile*`, `.gitlab-ci.yml`,
     `.circleci/**`), secret-like files (`.env*`, `*.pem`, `*.key`, …), lockfiles, `CODEOWNERS`,
     `.gitmodules`, `.gitattributes`, `.pre-commit-config.yaml`;
   - output: ≤ 3 files, ≤ 40 changed lines, `redact()` finds 0 secrets in the patched files and
     in the diff, no `[REDACTED` placeholder, no bypass pattern (`UNSAFE_FIX`, moved to `rules.py`) in added
     lines;
   - volume: ≤ 1 open fix PR per base branch (`pr` mode, checked against GitHub),
     ≤ `PD_FIX_MAX_PER_DAY` (default 3) proposals per repository per 24 h (both modes).
6. **Draft only, never merged by the agent.** The PR is opened with `draft: true`; if GitHub
   refuses a draft the agent aborts rather than opening a ready PR. The write module exposes no
   merge, approve, ready-for-review, label or comment operation. Title and commit message are
   deterministic templates; model text appears in the PR body only inside fenced blocks.
7. **Human validation = the GitHub review flow**, enforced server-side by owner settings:
   (a) the owner reads the draft diff, (b) approves the fork-PR workflow run
   (Settings › Actions › "Require approval for all external contributors"), (c) marks the PR
   ready, approves and merges — or closes it. A ruleset on the default branch requires a PR,
   one approval and the CI checks. The bot cannot do (b) or (c): it has no rights on upstream.
8. **The LangGraph `human_review` node is unchanged** (interactive CLI only); the unattended
   agent's human gate is the PR review, which survives restarts without a durable checkpointer.

## Consequences

- ADR-0001 §1 is amended: the agent still never writes to the monitored repository or CI
  system; in opt-in `pr` mode its only write is to its own fork plus opening a draft PR. ADR-0004
  is unchanged: the fix is generated by Ollama; repository files sent to it are sanitized first.
- "Never auto-merge" holds even if our code is wrong or the bot token leaks: the bot has no write
  access to upstream. A leaked `public_repo` token can still act as the bot on public repos
  (comments, PRs, its own repos): mitigated by a 90-day expiry, chmod 600 storage, revocation.
- Owner actions are required before `pr` mode works (machine user, fork, PAT, Actions approval
  setting, ruleset) — listed in the spec. `propose` mode needs only `Contents: read` added to
  the existing read token.
- A 3B model will often produce no applicable patch; `no_fix` / `rejected` are expected,
  journaled outcomes, not errors. Acceptance is measured by the PR outcome (merged / closed).
- Private target repositories, Jenkins-originated fixes, multi-commit fixes, signed commits and
  tracing fix generation in Langfuse are out of scope for v2.
- To verify before implementation: `POST /repos/{fork}/merge-upstream` behaviour when the fork
  lacks the base branch; draft PR creation from a fork with a classic `public_repo` token.
