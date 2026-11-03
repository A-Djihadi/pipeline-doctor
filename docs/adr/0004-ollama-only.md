# ADR-0004 : Ollama only — no cloud model escalation

- Status: accepted
- Date: 2026-11-03

## Context
Session 4 implemented a cloud escalation: when the local model's confidence fell below a
threshold, the agent would fall back to a cloud model (DeepSeek, then Claude). That work was
tried and then reverted (PR #5, commit `8dda991`): the repository is public and the goal is a
self-hosted, zero-cost, privacy-preserving diagnostic agent. A cloud fallback reintroduces API
keys, recurring cost, and a data-exfiltration risk that contradicts ADR-0001, whose "local
first, escalate on low confidence" step was never meant to become a hard requirement.

## Decision
1. **Ollama only** : every diagnosis is produced by a local model; no cloud model is ever called.
2. **Zero cloud API keys** : no DeepSeek, Claude, or any other provider key is accepted,
   documented, or configured.
3. **Simplicity and confidentiality win** over the marginal accuracy gain of a larger cloud
   model: the local model's quality is sufficient for CI log diagnosis.
4. **Low-confidence results are reported as-is** (with the confidence score) instead of
   triggering an escalation.

## Consequences
- No API keys, no recurring cost, and no build log ever leaves the host — the privacy and cost
  properties of ADR-0001 now hold for all inputs, including low-confidence ones.
- A low-confidence local diagnosis may occasionally be less precise than a large cloud model;
  this trade-off is accepted and documented so a future session does not re-attempt escalation.
- ADR-0001's "local first, cloud on low confidence" step is superseded: it is now "local only".
