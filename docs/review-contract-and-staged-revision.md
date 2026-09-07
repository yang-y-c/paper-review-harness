# Review contracts and staged revision

The harness separates persistent academic state from elastic logic verification.

## Persistent core

These dimensions are always maintained because they are high-value, reusable, and relatively inexpensive compared with deep local logic review:

- global manuscript contract;
- Claim ledger and cross-location Claim consistency;
- section/subsection logic and purpose contracts;
- terminology registry;
- notation/definition registry;
- material data/numerical consistency registry.

Token savings should not come from deleting this state.

## Assurance profiles

### FAST

Use for early drafting and broad structural diagnosis. The default FAST shape is structural/core only, ending at section/subsection logic. It deliberately accepts lower local-logic recall for lower token cost.

### STANDARD

Use for ordinary serious review. It adds paragraph-level logic and risk-adaptive sentence expansion. Important logical relations receive the existing evidence-grounded verification path.

### STRICT

Use for pre-submission review. It expands to full sentence coverage and uses the strongest local logic path. This is the most expensive profile.

The profile is a reliability/cost contract, not a probability that the paper is scientifically correct.

## Revision strategies

### REVIEW_ONLY

Read-only diagnosis.

### BATCH_AFTER_FULL_REVIEW

Complete the selected review first, then revise. This preserves the original behavior and maximizes single-snapshot completeness, but may waste detail-review tokens when the structure later changes.

### STAGED_REVISION

Recommended for broad edit-capable review when the user accepts it:

1. map Claims and persistent invariants;
2. review the global contract and section hierarchy;
3. if any OPEN Major/Blocker exists in this structural/core checkpoint, revise and independently verify it before deeper review;
4. rebuild the affected slow state on the new manuscript snapshot;
5. only after structural stabilization run paragraph/sentence/domain review;
6. revise remaining Issues, verify, and run the final selected gate.

This is checkpointed batch revision, not edit-as-you-read mutation. Every review pass remains bound to an immutable manuscript snapshot.

### TARGETED_REVISION

Only modify already identified Issues.

## Skill/harness boundary

The operator skill is a task compiler. It must not silently select the strongest review. It converts natural language into a `review_contract` containing:

- dimensions (what to check);
- assurance (FAST/STANDARD/STRICT);
- revision strategy;
- budget posture.

When the user has not explicitly fixed those choices, the skill may propose a concrete contract but must mark it `SKILL_PROPOSED`, show it to the user, and obtain confirmation before execution. The harness executes only the confirmed contract.

## Compatibility granularity

The old granularity field remains an internal compatibility mechanism:

- structural/core -> `SUBSECTION`;
- paragraph or risk-adaptive sentence logic -> `ADAPTIVE`;
- full-sentence or STRICT -> `SENTENCE`.

The user should choose review intent and assurance, not internal graph implementation switches.
