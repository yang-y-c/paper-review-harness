# Claim rules

## What enters the ledger

Create a Claim for every statement that materially carries novelty, correctness, scope, performance, interpretation, or conclusion. Consolidate repeated wording in Abstract, body, and Conclusion under one Claim ID rather than creating duplicates.

Each Claim records:

- exact statement and every manuscript location;
- one or more types: `theoretical`, `algorithmic`, `numerical`, `interpretive`, `presentation`;
- centrality: `CORE`, `SUPPORTING`, or `LOCAL`;
- strength: `NORMAL`, `STRONG`, or `EXTREME`;
- explicit scope and dependencies;
- evidence already present in the manuscript or supplied material;
- strong terms and review history.

## Classification

- `CORE`: removing or materially narrowing it changes the paper's main contribution.
- `SUPPORTING`: required to establish or interpret a Core Claim.
- `LOCAL`: bounded observation or presentation-level assertion.
- `STRONG`: uses completeness, necessity/sufficiency, exactness, universality, uniqueness, optimality, or strong causal/comparative language.
- `EXTREME`: combines such language with a Core contribution or an unbounded domain.

Strong-term detection is a routing signal, not proof of overclaiming. Record an adversarial review rather than automatically weakening the Claim.

## Evidence discipline

Evidence must identify an inspectable manuscript location, theorem, algorithm, table, figure, appendix, dataset, or explicitly supplied artifact. “Well known,” “obvious,” model intuition, and the reviser's explanation are not evidence entries.

If evidence is missing, retain the Claim and create an Issue or mark it `NEEDS_AUTHOR`; never synthesize a citation, proof, result, or assumption.

## Dependency direction

`dependencies` lists Claims required by the current Claim. When Claim A changes, verify A and every Claim that directly or transitively depends on A.
