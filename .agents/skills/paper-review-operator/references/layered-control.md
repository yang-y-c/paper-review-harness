# Layered manuscript control

Use this contract for `REVIEW`, `OPTIMIZE`, and `FULL`.

## Granularity decision

The user must choose one level:

- `MACRO_ONLY`: title, abstract, headings, conclusion, core logic chain, terminology, and notation only.
- `SECTION`: macro layer plus each top-level section's purpose and adjacency.
- `SUBSECTION`: macro layer plus every section and subsection.
- `PARAGRAPH`: all parent layers plus every paragraph's role and transitions.
- `SENTENCE`: all parent layers plus every sentence. This is slow and produces the largest audit corpus.
- `ADAPTIVE`: paragraph review throughout, with sentence review limited to title, abstract, conclusion, Core Claims, transitions, notation/definition boundaries, and units already flagged as high risk. Recommend this option, but do not select it for the user.

## Binding hierarchy

Run and solidify layers in this order:

1. `invariant_mapper` writes terminology, notation, data-consistency, argument-graph, Claim-consistency, and redundancy ledgers. It interprets the paper once; deterministic validators enforce the resulting facts and manuscript hash.
2. `macro_architect` writes `.review/global_contract.json`. It fixes the theme, research question, actual contribution, scope, title pattern, abstract slots, heading policy, conclusion contract, logic chain, and imports the canonical term/notation registries.
3. `hierarchy_reviewer` writes `.review/structure.json`. Each section or subsection records its parent, purpose, incoming premise, outgoing result, theme anchors, Claims, and transitions.
4. `language_coherence_reviewer` loads `$humanizer` and writes `.review/granular_review.json`. Every inspected unit must point to a hierarchy node and be checked against the global terminology and notation contract.
5. Domain reviewers inspect Claim validity. The reviser receives all parent ledgers and may not repair a local sentence by violating them.
6. After any edit, remap Claims and invariants before rebuilding the macro/hierarchy/language layers.
7. `final_integrity_auditor` writes `.review/final_audit.json` after review or revision. It independently rechecks all macro layers plus argument, data, Claim synchronization, and structural redundancy.

No lower layer may weaken a parent constraint to make a local passage look acceptable. A material change to the global contract requires a new normalized user decision, not a silent downstream edit.
