# Paper Review Harness

## Scope

The manuscript paths and main TeX file are defined in `.review/config.json`. Use the repository-local `paper-review` skill for manuscript discussion, review, revision, correction, verification, or acceptance work.

User-facing work enters through the repository-local `paper-review-operator` skill. It converts natural language into `.review/requests/<request-id>.json`, obtains confirmation for edit-capable modes, and runs programmatic admission before execution. Never ask the user to author the structured JSON and never bypass admission with a direct state edit.

## Hard rules

1. Scientific correctness and traceable evidence take precedence over wording and formatting.
2. Never silently strengthen or weaken a scientific claim. Record every material scope change against its Claim ID.
3. Reviewer, mapper, challenger, and verifier work is read-only. Manuscript edits are allowed only during the `REVISION` phase and must be performed by the `reviser` role.
4. A reviser may move an Issue only from `OPEN` or `NEEDS_AUTHOR` to `CLAIMED_FIXED`. It may never set `RESOLVED`.
5. Only an independent verifier `PASS` may move an Issue to `RESOLVED`. The verifier must not receive the reviser's explanation.
6. Do not invent data, citations, proofs, assumptions, lemmas, experimental results, or author intent. Use `NEEDS_AUTHOR` when required evidence is absent.
7. Claims using global, minimum, complete, exhaustive, automatic, exact, rigorous, unique, necessary, sufficient, optimal, universal, or equivalent language require adversarial review.
8. `BLOCKER` and `MAJOR` Issues affecting a `CORE` Claim prevent acceptance.
9. Compilation success is necessary when compilation is enabled, but never sufficient for acceptance.
10. Never delete an unresolved Issue to make a Gate pass. Preserve history in the ledgers and run artifacts.
11. Reviewers must not see one another's reports before their independent passes finish.
12. Modified Claims trigger verification of the changed Claim, its reverse dependencies, and affected sections. Do not rerun a full review unless a configured global-review trigger fires.
13. Preserve `.review/logs/events.jsonl`, request/admission records, run manifests, and conversation provenance. Never rewrite history to make a Gate pass.
14. Do not persist credentials in structured inputs or logs. Secret-like fields must be redacted; raw content is represented by a SHA-256 hash.
15. For REVIEW, OPTIMIZE, and FULL, solidify `global_contract.json` before hierarchy or prose work. Every lower-layer record and edit must remain traceable to its parent purpose, theme anchors, canonical terms, notation, and Claim scope.
16. Never infer review granularity. Require an explicit choice of macro-only, section, subsection, paragraph, sentence, or adaptive depth before admission.
17. Language and paragraph-logic review must load `$humanizer`. Apply it in embedded mode without changing scientific facts, citations, numbers, formulas, symbol meanings, qualifiers, or author voice.
18. Acceptance requires `final_audit.json` status `PASS`; a final auditor may not claim PASS with a failed dimension or a remaining audit Issue.

## Validation

Before declaring the manuscript accepted, run:

```text
python scripts/review.py validate --final
```

This command runs the deterministic validators. Acceptance requires exit code 0 and `.review/state.json` phase `ACCEPT`. If validation cannot establish a required fact, report the manuscript as not accepted.
