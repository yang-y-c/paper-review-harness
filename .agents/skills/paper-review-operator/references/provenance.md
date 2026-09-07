# Provenance and audit

The canonical event stream is `.review/logs/events.jsonl`. Each event has a monotonic sequence, raw-payload SHA-256, previous hash, event hash, actor, phase, request/run IDs, and artifact references. Secret-like fields are redacted before persistence.

Use:

- Verify tamper evidence: `python scripts/control.py verify-log`
- Materialize a conversation: `python scripts/control.py trace --session SESSION-ID`

Materialization writes:

- `.review/provenance/conversations/<session-id>.json` for machine use.
- `.review/provenance/conversations/<session-id>.md` for human review.

Agent invocations live at `.review/runs/<run-id>/agents/<invocation-id>/` with `input.json`, `output.json`, `events.jsonl`, `stderr.log`, and `invocation.json`. Discipline-specific validator executions live at `.review/scientific-validation/<run-id>/result.json`; the record includes the source snapshot, command/stdout/stderr hashes, redacted output streams, structured result, and failure detail. Verify hashes against current artifacts before making strong provenance claims. The Codex transcript path may be retained as a reference but is not the canonical parsed source because its format is not a stable contract.
