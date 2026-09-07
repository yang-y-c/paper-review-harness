from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False))


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
    if os.environ.get("PAPER_REVIEW_CHILD") == "1":
        emit({})
        return 0
    root = locate_root(str(payload.get("cwd") or Path.cwd()))
    if root is None:
        emit({})
        return 0
    state_path = root / ".review" / "state.json"
    config_path = root / ".review" / "config.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not state.get("active") or not state.get("stop_gate_enabled"):
        emit({})
        return 0
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "validators.py"), "--root", str(root), "--final", "--json"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=int(config.get("compile_timeout_seconds", 180)) + 30,
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        report = {
            "passed": False,
            "checks": [
                {
                    "id": "S00",
                    "status": "FAIL",
                    "detail": (completed.stdout + "\n" + completed.stderr)[-3000:],
                }
            ],
        }
    if completed.returncode == 0 and report.get("passed"):
        emit({})
        return 0
    attempts = int(state.get("stop_gate_attempts", 0)) + 1
    state["stop_gate_attempts"] = attempts
    failures = [
        f"{item.get('id')}: {item.get('detail')}"
        for item in report.get("checks", [])
        if item.get("status") == "FAIL"
    ]
    reason = "The manuscript is not accepted. Resolve these hard-gate failures:\n" + "\n".join(failures[:20])
    maximum = int(config.get("max_stop_continuations", 2))
    if attempts <= maximum:
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        emit({"decision": "block", "reason": reason})
        return 0
    state.update(
        {
            "phase": "BLOCKED",
            "active": False,
            "stop_gate_enabled": False,
            "blocked_reason": reason,
        }
    )
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit(
        {
            "systemMessage": "Paper Review Harness reached its safe continuation limit and ended BLOCKED, not ACCEPTED. "
            + reason
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
