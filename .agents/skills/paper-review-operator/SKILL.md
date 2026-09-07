---
name: paper-review-operator
description: "Natural-language control plane for the Paper Review Harness. Use when a user wants to discuss, review, revise, verify, optimize, operate, guide, inspect, trace, or monitor a paper workflow without manually creating structured input."
---

# Paper Review Operator

Turn the user's ordinary-language request into the harness contract, operate the workflow, and make its state and provenance understandable. Never ask the user to write JSON, enumerate schema fields, or translate their request into internal terminology.

## Route the request

Classify the current intent before acting:

- **Intake/start**: normalize a new paper task, admit it, and run it.
- **Guide**: explain the smallest next action from actual repository state.
- **View**: show a concise state/request/admission snapshot.
- **Trace**: materialize and explain conversation or run provenance.
- **Monitor**: watch an active run; use recurring product automation only when the user explicitly asks for recurring monitoring.

Read [intake.md](references/intake.md) for a new task, [operations.md](references/operations.md) to run or guide, [provenance.md](references/provenance.md) for audit/trace requests, and [monitoring.md](references/monitoring.md) only for monitoring.

For `REVIEW`, `OPTIMIZE`, or `FULL`, read [layered-control.md](references/layered-control.md). These modes must freeze the terminology, notation, data, argument, Claim-consistency, and redundancy facts before the global manuscript contract and lower-level work. If the user has not selected review depth, ask the granularity question from that reference; never choose silently.

## Non-negotiable behavior

1. Accept natural language. Use `python scripts/control.py intake` to produce and schema-validate the canonical request.
2. Show the user a compact normalized summary, not raw JSON, unless they ask for raw data.
3. Ask only about material missing information. Infer harmless defaults and surface those assumptions.
4. For `REVISE`, `OPTIMIZE`, or `FULL`, obtain confirmation of the normalized scope before `confirm`; never infer edit authorization.
5. Run `admit` before `start`. Do not bypass a failed gate; explain the failed check IDs and the exact remedy.
6. Treat ledgers, admission reports, run manifests, event logs, and provenance documents as the sources of truth. Do not claim progress from conversational memory alone.
7. Never expose secrets found in raw hooks or logs. Prefer materialized provenance, whose payloads are redacted and hash-linked.
8. Do not declare acceptance unless final deterministic validation passes and state is `ACCEPT`.
9. Language, paragraph logic, and local transition review must load and apply `$humanizer` in embedded mode. Its edits may change prose shape, never facts, Claim scope, citations, formulas, terminology meaning, or notation meaning.
10. Treat `G20` data consistency and `G21` Claim consistency as acceptance blockers. Do not present a soft auditor PASS as overriding them.

## Response contract

Lead with current outcome: normalized and awaiting confirmation, admitted and started, blocked by named gates, actively running, or completed. Include the request ID and relevant artifact paths. Explain one next action; do not bury the user in internal machinery.
