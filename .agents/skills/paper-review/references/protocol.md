# Workflow protocol

## State machine

```text
INGEST -> CLAIM_MAPPING -> INVARIANT_MAPPING -> MACRO_CONTRACT -> HIERARCHICAL_REVIEW
       -> COHERENCE_PARAGRAPH -> LOCAL_LOGIC_RECOVERY -> LOCAL_COVERAGE_CHALLENGE
       -> LOGIC_RELATION_VERIFICATION -> ADAPTIVE_SENTENCE_RECOVERY -> COHERENCE_BOTTOM_UP
       -> INDEPENDENT_REVIEW -> ISSUE_SYNTHESIS
       -> ADVERSARIAL_REVIEW -> REVISION -> TARGETED_VERIFICATION
       -> FINAL_INTEGRITY_AUDIT -> DETERMINISTIC_VALIDATION -> ACCEPT
```

Any failed Gate returns work to `REVISION`, `TARGETED_VERIFICATION`, or `NEEDS_AUTHOR`. Reaching the configured maximum rounds produces `BLOCKED`; it never produces `ACCEPT`.

## Mode contracts

### Discuss

Operate on selected Claims. Use claim mapper when the Claim does not exist, argument reviewer for contribution/logic questions, and challenger for strong or central Claims. Produce a discussion packet containing: current Claim, supporting evidence, strongest challenge, unresolved choice, and the consequence of each defensible choice. Do not edit manuscript or close Issues.

### Review

1. Map Claims and coverage.
2. Map cross-paper invariant registries and the argument graph.
3. Route each Claim by type and risk.
4. Run theory, algorithm, numerical, and argument reviewers independently and concurrently when useful.
5. Merge findings by evidence and verification criterion. Preserve substantive disagreements.
6. Run challenger on every `STRONG` or `EXTREME` Claim.

Routing:

```text
theoretical -> theory_reviewer
algorithmic -> algorithm_reviewer
numerical -> numerical_reviewer
CORE -> argument_reviewer
STRONG or EXTREME -> challenger
```

### Revise

Prioritize by scientific layer, then severity: validity before argument architecture, communication, and production. A revision record must list the Issue, changed files, changed locations, affected Claims, change type, and summary. Its only successful status is `CLAIMED_FIXED`.

### Verify

Use a Codex session distinct from the reviser. Input only the original Issue, its verification criterion, the relevant Claim, and the current manuscript. Results are `PASS`, `PARTIAL`, `FAIL`, or `NEW_PROBLEM`. Only `PASS` resolves the Issue.

### Optimize

Run only after higher-layer Blockers are absent. Optimization may reduce repetition, improve section logic, clarify notation, tighten claims to evidence, and fix presentation. It may not silently introduce stronger universality, causality, novelty, optimality, or numerical precision.

## Targeted versus global review

After ordinary edits, verify changed Claims, all reverse dependencies, and affected sections. Trigger global review when any of these occurs:

- a `CORE` Claim changes;
- theorem assumptions or quantifiers change;
- a numerical definition changes, affecting related results;
- Abstract, Introduction contribution framing, or Conclusion scope changes;
- changed manuscript files exceed `global_review_change_threshold`;
- all Blockers are cleared and final acceptance is requested.

## Independence boundaries

- Reviewers do not receive peer reports during their first pass.
- Challenger does not repair the Claim.
- Reviser does not close Issues.
- Verifier does not receive the revision rationale.
- Deterministic validators do not call an LLM.
