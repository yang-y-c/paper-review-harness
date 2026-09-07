from __future__ import annotations

import json
import sys
from pathlib import Path


def find_root(start: str | None) -> Path | None:
    candidates = []
    if start:
        candidates.append(Path(start).resolve())
    candidates.append(Path.cwd().resolve())
    candidates.append(Path(__file__).resolve().parents[2])
    for initial in candidates:
        for candidate in (initial, *initial.parents):
            if (candidate / ".review" / "config.json").is_file():
                return candidate
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        root = find_root(payload.get("cwd"))
        if root is None:
            print("{}")
            return 0
        sys.path.insert(0, str(root / "scripts"))
        from provenance import append_event, materialize_conversation  # noqa: PLC0415

        event_type = str(payload.get("hook_event_name") or "Unknown")
        session_id = payload.get("session_id")
        append_event(
            root,
            f"hook.{event_type}",
            payload,
            session_id=session_id,
            turn_id=payload.get("turn_id"),
            request_id=payload.get("request_id"),
            run_id=payload.get("run_id"),
            actor="codex-hook",
        )
        if event_type in {"Stop", "SessionEnd"} and session_id:
            materialize_conversation(root, str(session_id))
        print("{}")
    except Exception as exc:  # Hooks must fail open; the fallback remains machine-visible.
        try:
            fallback = Path.cwd() / ".review" / "logs" / "audit-hook-errors.log"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            with fallback.open("a", encoding="utf-8") as handle:
                handle.write(f"{type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
