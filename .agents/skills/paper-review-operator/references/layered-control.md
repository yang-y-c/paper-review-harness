# Layered manuscript control

Use this contract for `REVIEW`, `OPTIMIZE`, and `FULL`.

## Granularity decision

Default to disclosed ADAPTIVE coverage. The following are cumulative coverage profiles, not mutually exclusive logical scales:

- `MACRO_ONLY`: title, abstract, headings, conclusion, core logic chain, terminology, and notation only.
- `SECTION`: macro layer plus each top-level section's purpose and adjacency.
- `SUBSECTION`: macro layer plus every section and subsection.
- `PARAGRAPH`: all parent layers plus every paragraph's role and transitions.
- `SENTENCE`: all parent layers plus every sentence. This is slow and produces the largest audit corpus.
- `ADAPTIVE`: all parent layers and every paragraph; expand risky paragraphs as complete local sentence contexts. Deep-review critical sentences and keep other sentences lightweight. The scheduler records each selection, including relation disagreement and contextual inclusion. Risk scores allocate review budget; they are not truth probabilities.

## Binding hierarchy

Run and solidify layers in this order:

1. `invariant_mapper` writes terminology, notation, data-consistency, argument-graph, Claim-consistency, and redundancy ledgers. It interprets the paper once; deterministic validators enforce the resulting facts and manuscript hash.
2. `macro_architect` writes `.review/global_contract.json`. It fixes the theme, research question, actual contribution, scope, title pattern, abstract slots, heading policy, conclusion contract, logic chain, and imports the canonical term/notation registries.
3. `hierarchy_reviewer` writes `.review/structure.json`. Each section or subsection records its parent, purpose, incoming premise, outgoing result, theme anchors, Claims, and transitions.
4. `language_coherence_reviewer` loads `$humanizer` and maps explicit paragraph contracts. `argument_reviewer` recovers local section/paragraph graphs from complete bounded contexts, and `challenger` independently checks attachment coverage without mapper rationale. The sparse scheduler adds cross-scale/remote candidates. `verifier` independently tests relation proposals using the versioned ontology and literal source spans. Disagreement triggers finer local sentence recovery. The authoritative multiscale tree, contracts, risk plans and reverse pass are in `.review/coherence_registry.json`; `.review/granular_review.json` is a compatibility view.
5. Domain reviewers inspect Claim validity. The reviser receives all parent ledgers and may not repair a local sentence by violating them.
6. After any edit, remap Claims and invariants before rebuilding the macro/hierarchy/language layers.
7. `final_integrity_auditor` writes `.review/final_audit.json` after review or revision. It independently rechecks all macro layers plus argument, data, Claim synchronization, and structural redundancy.

No lower layer may weaken a parent constraint to make a local passage look acceptable. A material change to the global contract requires a new normalized user decision, not a silent downstream edit.

Use `logic/local_graphs.json` for primary/additional antecedents and coverage challenges, `rhetorical_graph.json` for same-scale flow, `realization_graph.json` for support/goal realization, and `logic_disputes.json` for disagreements. Structural CONTAINS edges come only from the source parser. Terms and symbols are horizontal canonical references, not a fifth logic scale.

After edits, affected semantic relations become STALE; unchanged content/contract/ontology fingerprints can reuse independently confirmed relations. The overall source registry must still be refreshed. Ordinary awkward transitions, backtracking and long-range cognitive load remain diagnostics. Missing critical support, invalid graph references and critical disputes block acceptance. Do not equate the graph with model attention weights or with proof of scientific correctness.
