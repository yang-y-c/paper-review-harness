# Cross-paper invariants and scientific-validator boundary

Use this reference during invariant mapping, revision, verification, or acceptance.

## Structured semantic facts

`invariant_mapper` reads the manuscript and Claim Ledger once, then solidifies:

- `.review/terminology.json`: one concept ID, canonical term, aliases, definitions, and occurrences;
- `.review/notation.json`: one notation ID, symbol, meaning, type/domain, definitions, and occurrences;
- `.review/data_consistency.json`: repeated material values under explicit units and conditions;
- `.review/argument_graph.json`: source-to-target support, dependency, qualification, and contradiction edges;
- `.review/claim_consistency.json`: canonical Claim statement/scope and every Abstract/body/Conclusion occurrence;
- `.review/redundancy.json`: exact duplication and contribution duplication, plus non-gating semantic-similarity notes.

The mapper performs semantic interpretation. Deterministic validators then enforce the recorded facts. This is a hard workflow gate over structured semantic judgments; it is not a proof that the mapper's scientific interpretation is true.

## Machine-enforced invariants

- Canonical terms, aliases, concepts, notation IDs, and symbols cannot collide.
- A registered occurrence cannot use a forbidden term or conflicting meaning/type.
- A repeated material value marked `MATCH` must have exactly the canonical value, unit, and conditions. Legitimate variation requires `AUTHORIZED_VARIATION` plus a reason.
- Every active Claim appears exactly once in Claim consistency and at least once in the argument graph.
- Every CORE Claim has a body source and a traceable support ancestor.
- Claim dependencies must exist as directed paths in the argument graph, and support edges cannot form a cycle.
- Abstract and Conclusion occurrences cannot be broader, contradictory, unmapped, or stale relative to the canonical Claim.
- A revision makes all six ledgers `STALE`; acceptance is blocked until they are remapped against the new manuscript hash.
- Duplicate contribution labels for the same Claim and unjustified MAJOR exact duplicates block acceptance. Semantic similarity alone never does.

Do not repair a registry conflict by silently changing the concept, symbol meaning, data condition, Claim scope, or evidence relationship. Create an Issue and use `NEEDS_AUTHOR` when the manuscript does not determine a safe resolution.

## Discipline-specific scientific validators

The generic harness does not encode group closure, finite-element rank, experiment reproduction, or another field-specific truth test. Configure those under `.review/config.json`:

```json
{
  "scientific_validators": [
    {
      "id": "rank_consistency",
      "command": ["python", "validators/rank_consistency.py", "--root", "{root}"],
      "required": true,
      "timeout_seconds": 120
    }
  ]
}
```

The command runs only during final validation, from the repository root, without a shell. It must return one raw JSON object on stdout:

```json
{
  "validator_id": "rank_consistency",
  "status": "PASS",
  "summary": "All reported ranks match the supplied matrices.",
  "evidence": ["artifacts/rank-report.json"]
}
```

Available command placeholders are `{root}`, `{main_tex}`, `{main_dir}`, and `{main_name}`. A required validator blocks acceptance on nonzero exit, timeout, invalid JSON/schema, ID mismatch, `FAIL`, or `NEEDS_AUTHOR`. An optional validator is diagnostic only.
