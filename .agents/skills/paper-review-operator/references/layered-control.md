# Layered manuscript control

Use this contract for `REVIEW`, `OPTIMIZE`, and `FULL`.

## User-facing review contract

Do not reduce the task to one granularity switch. The user-facing contract has four independent parts:

1. **Dimensions** — what to check.
2. **Assurance** — how much independent verification to buy with tokens.
3. **Revision strategy** — when edits may occur.
4. **Budget** — economy/balanced/max-quality posture and any explicit numerical cap.

The fixed academic core is always enabled: global structure, Claims, section logic, terminology, notation/definitions, and data consistency. These ledgers are relatively stable and high-value. Optional dimensions include paragraph logic, sentence logic, redundancy, language, citations, and scientific validity.

## Assurance profiles

- `FAST`: lowest-cost structural/core review. It is appropriate for early drafts and broad diagnosis. Full-sentence logic is not permitted. If the user explicitly requests paragraph or risk-adaptive sentence logic, the effective logical depth rises accordingly and the summary must disclose that cost increase.
- `STANDARD`: balanced default when the user chooses it. Covers paragraphs and uses risk-adaptive sentence expansion for important logic. Critical Claims and important relations receive independent checking under the existing logic machinery.
- `STRICT`: pre-submission/high-assurance profile. Uses full-sentence coverage and the strongest available logic verification path.

Assurance describes reliability/cost, not scientific truth probability. Never silently upgrade a user to STRICT.

## Derived compatibility granularity

`granularity` remains an internal compatibility field, derived from the contract:

- structural/core only -> `SUBSECTION`;
- paragraph logic or risk-adaptive sentence logic -> `ADAPTIVE`;
- full-sentence logic or STRICT -> `SENTENCE`.

Scales are cumulative. Do not ask the user to choose one scale as though section, paragraph, and sentence review were mutually exclusive.

## Staged revision

For broad edit-capable work, `STAGED_REVISION` is the recommended proposal but must be user-confirmed. Its rule is:

```text
Snapshot V1
  -> Claim/invariant/global/section review
  -> structural Major/Blocker?
       yes: revise structure -> independently verify -> rebuild slow state
       no:  continue
  -> paragraph/argument/logic review
  -> local/detail revision
  -> targeted verification
  -> final audit and deterministic gate
```

The purpose is to avoid spending expensive paragraph/sentence tokens on text that is likely to be structurally rewritten. A review pass remains bound to one immutable manuscript snapshot; this is checkpointed batch revision, not edit-as-you-read mutation.

`BATCH_AFTER_FULL_REVIEW` preserves the previous behavior: finish the full selected review first, then revise. `REVIEW_ONLY` never edits. `TARGETED_REVISION` only addresses already identified Issues.

## Binding hierarchy and persistent state

1. `invariant_mapper` writes terminology, notation, data-consistency, argument-graph, Claim-consistency, and redundancy ledgers.
2. `macro_architect` writes `.review/global_contract.json`.
3. `hierarchy_reviewer` writes `.review/structure.json` with section/subsection purpose, incoming premise, outgoing result, Claims, and transitions.
4. Optional elastic logic work expands from paragraphs into risky sentences according to the confirmed contract. The authoritative multiscale structures remain in `.review/coherence_registry.json` and `.review/logic/`.
5. Domain reviewers inspect Claim validity when included by the contract.
6. The reviser receives parent ledgers and may not repair local prose by violating them.
7. After any edit, affected Claims/invariants/logic relations become stale and are rebuilt or revalidated before acceptance.
8. `final_integrity_auditor` performs the selected final cross-layer review.

No lower layer may weaken a parent constraint to make a local passage look acceptable. A material change to the global contract requires a new user decision, not a silent downstream edit.

## Cost principle

Treat the core registries as persistent slow variables and the local sentence/paragraph logic graph as elastic fast variables. Token savings should come mainly from reducing deep logic verification on low-risk content, not from deleting terminology, notation, Claim, data, or global-structure state.

Ordinary awkward transitions, backtracking, and long-range cognitive load remain diagnostics. Missing critical support, invalid graph references, scope contradictions, and unresolved critical disputes may block acceptance. Do not equate a risk score, graph edge, or model attention weight with proof of scientific correctness.
