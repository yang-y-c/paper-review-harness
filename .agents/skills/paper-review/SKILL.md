---
name: paper-review
description: Claim-centered discussion, independent review, controlled revision, correction, verification, and acceptance gating for academic manuscripts. Use when a repository contains `.review/config.json` or when the user asks to apply the Paper Review Harness; do not use for ordinary prose editing that does not require ledgers or independent verification.
---

# Paper Review Harness

Treat `.review/claims.json` and `.review/issues.json` as the shared state, not reviewer prose. Read `.review/config.json`, `.review/state.json`, and [references/protocol.md](references/protocol.md) before acting.

For layered review or optimization, treat `.review/global_contract.json`, `.review/structure.json`, and `.review/granular_review.json` as binding parent state. Local edits may not drift from their theme, terminology, notation, hierarchy, or Claim-scope constraints. Language and paragraph-logic work must use `$humanizer`.

For a user-facing new task, use `$paper-review-operator` to normalize natural language, create a canonical Request, confirm edit scope when required, and pass deterministic admission. This skill governs the scientific workflow after admission. A child agent executing a supplied assignment must follow its role contract and must not create a second Request.

## Route the request

- **Discuss:** map the relevant Claims, expose disagreements and missing evidence, and ask only author decisions that materially change the science. Do not edit.
- **Review:** run independent reviewers without sharing their findings, then merge structured Issues without silently resolving conflicts.
- **Revise / correct:** read [references/revision-rules.md](references/revision-rules.md). Edit only during `REVISION`; move Issues only to `CLAIMED_FIXED`.
- **Verify:** use a fresh verifier session that receives the Issue, verification criterion, Claim context, and current manuscript—but not the reviser's explanation.
- **Optimize:** first require no unresolved higher-layer `BLOCKER`; then improve argument architecture, scientific communication, and production quality without changing Claim scope unnoticed.
- **Accept:** run `python scripts/review.py validate --final`. It runs the deterministic Gates and advances state to `ACCEPT` only on success. Use `python scripts/validators.py --final` for a read-only diagnostic report.

Read [references/claim-rules.md](references/claim-rules.md) when mapping or changing Claims. Read [references/issue-rules.md](references/issue-rules.md) when reviewing, merging, prioritizing, or closing Issues. Read [references/writing-rules.md](references/writing-rules.md) only for revision or optimization.

## Invariants

- Never invent scientific evidence, citations, proofs, data, or author intent.
- Reviewer, mapper, challenger, and verifier roles are read-only. Only the reviser edits manuscript files.
- A reviser cannot resolve an Issue. Only independent verifier `PASS` can do so.
- Strong or extreme Claims require a challenger pass. A challenger attacks; it does not repair.
- For a full review, use the project custom agents concurrently when available. Do not show one reviewer another reviewer's report before all independent passes finish.
- After a change, verify changed Claims, reverse dependencies, and affected sections. Full re-review is reserved for the triggers in the protocol.
- Keep machine-facing outputs as raw JSON conforming to the referenced schema; do not wrap them in Markdown fences.

Use `python scripts/control.py` for user-facing intake, admission, operation, status, trace, and monitoring. Direct `review.py run` calls require `--request REQ-ID`; do not bypass that requirement or hand-edit a status merely to satisfy validation.
