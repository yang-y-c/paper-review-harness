from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False))


def deny(reason: str) -> None:
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


def locate_root(cwd: str) -> Path | None:
    here = Path(cwd).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".review" / "state.json").is_file():
            return candidate
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        emit({})
        return 0
    root = locate_root(str(payload.get("cwd") or Path.cwd()))
    if root is None:
        emit({})
        return 0
    state = json.loads((root / ".review" / "state.json").read_text(encoding="utf-8"))
    if not state.get("active") or state.get("phase") == "REVISION":
        emit({})
        return 0
    config = json.loads((root / ".review" / "config.json").read_text(encoding="utf-8"))
    manuscript_roots = [(root / item).resolve() for item in config["manuscript_roots"]]
    tool_input = payload.get("tool_input") or {}
    command = str(tool_input.get("command") or "")
    paths = re.findall(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", command, re.MULTILINE)
    touches_manuscript = False
    for raw_path in paths:
        candidate = Path(raw_path.strip())
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        for manuscript_root in manuscript_roots:
            try:
                candidate.relative_to(manuscript_root)
                touches_manuscript = True
                break
            except ValueError:
                continue
        if touches_manuscript:
            break
    if touches_manuscript:
        deny(
            f"Manuscript edits are blocked during phase {state.get('phase')}. Move the harness to REVISION and use the reviser role."
        )
    else:
        emit({})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
