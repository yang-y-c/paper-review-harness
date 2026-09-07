from __future__ import annotations

import json
import sys
from pathlib import Path


SCHEMAS = {
    "claim_mapper": "claim-map-output.schema.json",
    "invariant_mapper": "invariant-output.schema.json",
    "macro_architect": "macro-output.schema.json",
    "hierarchy_reviewer": "hierarchy-output.schema.json",
    "language_coherence_reviewer": "language-output.schema.json",
    "final_integrity_auditor": "final-audit-output.schema.json",
    "theory_reviewer": "review-output.schema.json",
    "algorithm_reviewer": "review-output.schema.json",
    "numerical_reviewer": "review-output.schema.json",
    "argument_reviewer": "review-output.schema.json",
    "challenger": "review-output.schema.json",
    "reviser": "revision-output.schema.json",
    "verifier": "verification-output.schema.json",
}


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False))


def block(reason: str) -> None:
    emit({"decision": "block", "reason": reason})


def locate_root(cwd: str) -> Path | None:
    here = Path(cwd).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".review" / "config.json").is_file():
            return candidate
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        block(f"Hook input is not valid JSON: {exc}")
        return 0
    agent = str(payload.get("agent_type") or "").replace("-", "_")
    if agent not in SCHEMAS:
        emit({})
        return 0
    message = payload.get("last_assistant_message")
    if not isinstance(message, str) or not message.strip():
        block(f"{agent} must return a non-empty raw JSON result.")
        return 0
    try:
        output = json.loads(message)
    except json.JSONDecodeError as exc:
        block(
            f"{agent} output is not raw valid JSON ({exc}). Return the required schema without Markdown fences."
        )
        return 0
    root = locate_root(str(payload.get("cwd") or Path.cwd()))
    if root is None:
        block("Cannot locate .review/config.json to validate the subagent output.")
        return 0
    sys.path.insert(0, str(root / "scripts"))
    try:
        from paper_review_lib import validate_with_schema

        validate_with_schema(root, SCHEMAS[agent], output)
    except Exception as exc:
        block(f"{agent} output does not satisfy {SCHEMAS[agent]}: {exc}")
        return 0
    emit({})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
