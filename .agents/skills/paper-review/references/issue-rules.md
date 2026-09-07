# Issue rules

Every finding must state a falsifiable problem and a verification criterion. Avoid vague severity labels.

## Severity

- `BLOCKER`: invalidates acceptance, a Core Claim, a proof/result, or ethical/reproducibility integrity.
- `MAJOR`: materially weakens correctness, scope, evidence, or reproducibility but has a plausible bounded repair.
- `MINOR`: localized ambiguity, inconsistency, missing qualification, or presentation defect that does not alter the central result.
- `STYLE`: wording or formatting only; no scientific meaning changes.

## Required fields

`claim_id`, `location`, `severity`, `category`, `problem`, `why_it_matters`, `required_action`, `verification_criterion`, `source_agent`, and `status` are mandatory. A finding without exact evidence/location is incomplete.

## Status transitions

```text
OPEN -> CLAIMED_FIXED -> RESOLVED
  |          |
  |          +-> OPEN       (PARTIAL or FAIL)
  |          +-> OPEN + new Issue (NEW_PROBLEM)
  +-> NEEDS_AUTHOR
```

`WONT_FIX` requires an explicit author decision and rationale; it is not equivalent to passing. Do not delete or overwrite prior revision and verification records.

## Merge rules

Merge only findings that identify the same Claim, location, defect, and required verification. Preserve the higher defensible severity and all source agents. Do not merge distinct failure mechanisms merely because they affect the same paragraph.

When reviewers disagree, record the disagreement in `notes` or separate Issues. The harness must not manufacture consensus.
