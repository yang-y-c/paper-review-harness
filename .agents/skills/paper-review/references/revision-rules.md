# Revision and verification rules

## Before editing

Read the Issue, verification criterion, affected Claim, dependencies, current manuscript context, six invariant ledgers, and the current global, hierarchy, and granular ledgers. Every local repair must preserve its parent purpose, theme anchors, canonical terminology, notation meaning, data values/conditions, argument dependencies, and Claim scope. Any manuscript edit makes the invariant ledgers stale; remap them before final audit. If a repair requires new evidence, an author choice, or a change to the scientific contribution or global contract, stop and mark `NEEDS_AUTHOR` with the exact missing decision.

## Permitted revision outcomes

- add an argument already justified by available premises;
- narrow a Claim to the actual evidence;
- clarify scope, assumptions, quantifiers, definitions, or notation;
- correct an implementation description against supplied code/results;
- reorganize or rewrite without changing scientific meaning;
- fix citations or numerical transcription using existing sources.

Do not invent a proof, experiment, result, citation, assumption, or explanation of author intent.

The reviser reports `CLAIMED_FIXED` and affected paths. It never edits Issue status to `RESOLVED`.

## Verification

The verifier receives no revision summary. It independently locates the current text and tests the original verification criterion.

- `PASS`: criterion is fully satisfied without creating a material contradiction.
- `PARTIAL`: improvement exists but one or more parts of the criterion remain unmet.
- `FAIL`: the relevant defect remains or the change does not address it.
- `NEW_PROBLEM`: the attempted repair creates a distinct correctness, scope, evidence, or consistency defect.

For `NEW_PROBLEM`, keep the original Issue open unless its own criterion passes, and create a new Issue for the new defect.
