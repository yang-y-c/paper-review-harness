from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_KEY = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie)",
    re.IGNORECASE,
)
INLINE_SECRET_PATTERNS = [
    re.compile(
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)([^\s,;\"']+)"
    ),
    re.compile(
        r"(?i)((?:--?(?:api[-_]?key|access[-_]?token|password|secret|token)|"
        r"(?:api[-_]?key|access[-_]?token|password|secret|token))\s*(?:=|\s)\s*)([^\s,;\"']+)"
    ),
]


class ProvenanceError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact(value: Any, *, max_inline_chars: int = 4000, key: str = "") -> Any:
    if key and SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): redact(
                item_value, max_inline_chars=max_inline_chars, key=str(item_key)
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact(item, max_inline_chars=max_inline_chars) for item in value]
    if isinstance(value, str):
        cleaned = value
        for pattern in INLINE_SECRET_PATTERNS:
            cleaned = pattern.sub(r"\1[REDACTED]", cleaned)
        if len(cleaned) > max_inline_chars:
            return cleaned[:max_inline_chars] + f"…[truncated {len(cleaned) - max_inline_chars} chars]"
        return cleaned
    return value


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _acquire_lock(path: Path, timeout_seconds: float = 3.0) -> int:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ProvenanceError(f"Timed out waiting for audit log lock: {path}")
            time.sleep(0.025)


def _last_event(log_path: Path) -> dict[str, Any] | None:
    if not log_path.is_file():
        return None
    last: dict[str, Any] | None = None
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            last = json.loads(line)
    return last


def append_event(
    root: Path,
    event_type: str,
    payload: Any,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    actor: str = "harness",
    phase: str | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    logs_dir = root / ".review" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "events.jsonl"
    lock_path = logs_dir / ".events.lock"
    raw_hash = sha256_value(payload)
    safe_payload = redact(payload)
    lock_fd = _acquire_lock(lock_path)
    try:
        previous = _last_event(log_path)
        event = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "sequence": int(previous["sequence"]) + 1 if previous else 1,
            "timestamp": utc_now(),
            "session_id": session_id,
            "turn_id": turn_id,
            "request_id": request_id,
            "run_id": run_id,
            "event_type": event_type,
            "actor": actor,
            "phase": phase,
            "payload": safe_payload,
            "raw_payload_sha256": raw_hash,
            "previous_hash": previous["event_hash"] if previous else None,
            "artifact_refs": artifact_refs or [],
        }
        event["event_hash"] = sha256_value(event)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event
    finally:
        os.close(lock_fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def read_events(root: Path) -> list[dict[str, Any]]:
    path = root / ".review" / "logs" / "events.jsonl"
    if not path.is_file():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ProvenanceError(f"Invalid audit JSON at line {number}: {exc}") from exc
    return events


def verify_event_chain(root: Path) -> dict[str, Any]:
    events = read_events(root)
    previous_hash = None
    errors: list[str] = []
    for expected_sequence, event in enumerate(events, start=1):
        if event.get("sequence") != expected_sequence:
            errors.append(
                f"sequence {expected_sequence}: stored sequence is {event.get('sequence')}"
            )
        if event.get("previous_hash") != previous_hash:
            errors.append(f"sequence {expected_sequence}: previous_hash mismatch")
        stored_hash = event.get("event_hash")
        unhashed = dict(event)
        unhashed.pop("event_hash", None)
        computed_hash = sha256_value(unhashed)
        if stored_hash != computed_hash:
            errors.append(f"sequence {expected_sequence}: event_hash mismatch")
        previous_hash = stored_hash
    return {
        "valid": not errors,
        "events": len(events),
        "head_hash": previous_hash,
        "errors": errors,
    }


def _artifact_record(root: Path, reference: Any) -> dict[str, Any]:
    if isinstance(reference, dict):
        relative = reference.get("path")
    else:
        relative = reference
    record: dict[str, Any] = {"path": relative}
    if not isinstance(relative, str):
        return record
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        record["status"] = "OUTSIDE_ROOT"
        return record
    if path.is_file():
        record.update(
            {"status": "PRESENT", "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    else:
        record["status"] = "MISSING"
    return record


def materialize_conversation(root: Path, session_id: str) -> dict[str, Any]:
    selected = [event for event in read_events(root) if event.get("session_id") == session_id]
    request_ids = sorted({item["request_id"] for item in selected if item.get("request_id")})
    run_ids = sorted({item["run_id"] for item in selected if item.get("run_id")})
    artifacts: dict[str, dict[str, Any]] = {}
    for event in selected:
        for reference in event.get("artifact_refs", []):
            record = _artifact_record(root, reference)
            artifacts[str(record.get("path"))] = record
    document = {
        "schema_version": 1,
        "session_id": session_id,
        "generated_at": utc_now(),
        "event_count": len(selected),
        "request_ids": request_ids,
        "run_ids": run_ids,
        "chain_head": selected[-1]["event_hash"] if selected else None,
        "artifacts": sorted(artifacts.values(), key=lambda item: str(item.get("path"))),
        "events": selected,
    }
    destination = root / ".review" / "provenance" / "conversations"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", session_id).strip("._")[:128]
    if not safe_name:
        safe_name = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    json_path = destination / f"{safe_name}.json"
    md_path = destination / f"{safe_name}.md"
    _atomic_write(json_path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    lines = [
        f"# Conversation provenance: {session_id}",
        "",
        f"Generated: {document['generated_at']}",
        f"Events: {document['event_count']}",
        f"Requests: {', '.join(request_ids) if request_ids else 'none'}",
        f"Runs: {', '.join(run_ids) if run_ids else 'none'}",
        f"Chain head: `{document['chain_head'] or 'none'}`",
        "",
        "## Timeline",
        "",
    ]
    for event in selected:
        context = ", ".join(
            value
            for value in [event.get("request_id"), event.get("run_id"), event.get("actor")]
            if value
        )
        lines.append(
            f"- {event['timestamp']} · `{event['event_type']}`"
            + (f" · {context}" if context else "")
            + f" · `{event['event_hash']}`"
        )
    lines.extend(["", "## Artifacts", ""])
    for artifact in document["artifacts"]:
        lines.append(
            f"- `{artifact.get('path')}` · {artifact.get('status')}"
            + (f" · `{artifact.get('sha256')}`" if artifact.get("sha256") else "")
        )
    _atomic_write(md_path, "\n".join(lines) + "\n")
    return {
        "session_id": session_id,
        "json": str(json_path.relative_to(root)),
        "markdown": str(md_path.relative_to(root)),
        "events": len(selected),
    }
