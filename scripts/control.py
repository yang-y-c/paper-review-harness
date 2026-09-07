from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from admission import (
    canonicalize_request,
    confirm_request,
    evaluate_admission,
    load_request,
)
from paper_review_lib import (
    CodexRunner,
    HarnessError,
    find_root,
    load_config,
    load_json,
    make_run_id,
    status_summary,
)
from provenance import (
    ProvenanceError,
    append_event,
    materialize_conversation,
    read_events,
    verify_event_chain,
)
from review import main as review_main


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _read_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    return args.text_file.read_text(encoding="utf-8")


def intake(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    natural_language = _read_text(args)
    runner = CodexRunner(root, load_config(root))
    run_id = make_run_id("intake")
    draft = runner.run(
        "intake_coordinator",
        {
            "task": "Normalize the natural-language request without executing it.",
            "natural_language_request": natural_language,
            "repository_configuration": load_config(root),
        },
        run_id,
        audit_context={"session_id": args.session_id, "turn_id": args.turn_id, "request_id": None},
    )
    request = canonicalize_request(
        root,
        draft,
        natural_language,
        session_id=args.session_id,
        turn_id=args.turn_id,
    )
    return {
        "request": request,
        "next_action": _next_action(request),
        "note": "The user supplied natural language; the skill and intake agent created this structure.",
    }


def _next_action(request: dict[str, Any]) -> str:
    if request["missing_information"]:
        return "Resolve only the listed material missing information, then normalize again."
    if request["confirmation"]["required"] and not request["confirmation"]["confirmed"]:
        return f"Show the normalized scope and constraints, then confirm {request['request_id']}."
    if request["status"] in {"READY", "CONFIRMED"}:
        return f"Run admission, then start {request['request_id']} if all checks pass."
    if request["status"] == "EXECUTING":
        return "View status or monitor the active workflow."
    return "View status and trace artifacts."


def monitor_snapshot(root: Path) -> dict[str, Any]:
    summary = status_summary(root)
    state = load_json(root / ".review" / "state.json")
    run_id = state.get("last_run_id")
    invocations: list[dict[str, Any]] = []
    if run_id:
        agents_dir = root / ".review" / "runs" / run_id / "agents"
        if agents_dir.is_dir():
            for manifest in sorted(agents_dir.glob("*/invocation.json")):
                data = load_json(manifest)
                invocations.append(
                    {
                        "invocation_id": data["invocation_id"],
                        "agent": data["agent"],
                        "status": data["status"],
                        "started_at": data["started_at"],
                        "completed_at": data["completed_at"],
                    }
                )
    events = read_events(root)
    latest = events[-1] if events else None
    return {
        **summary,
        "active_request_id": state.get("active_request_id"),
        "active_run_id": state.get("active_run_id"),
        "last_run_id": run_id,
        "agent_invocations": invocations,
        "last_event": {
            key: latest.get(key)
            for key in ["sequence", "timestamp", "event_type", "actor", "request_id", "run_id"]
        }
        if latest
        else None,
    }


def _requests(root: Path) -> list[dict[str, Any]]:
    directory = root / ".review" / "requests"
    if not directory.is_dir():
        return []
    result = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        request = load_json(path)
        result.append(
            {
                "request_id": request["request_id"],
                "created_at": request["created_at"],
                "intent": request["intent"],
                "status": request["status"],
                "next_action": _next_action(request),
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operate and trace the Paper Review Harness")
    parser.add_argument("--root", type=Path, help="paper repository root")
    commands = parser.add_subparsers(dest="command", required=True)

    intake_parser = commands.add_parser("intake", help="turn natural language into a request")
    source = intake_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file", type=Path)
    intake_parser.add_argument("--session-id")
    intake_parser.add_argument("--turn-id")

    confirm_parser = commands.add_parser("confirm", help="confirm an edit-capable request")
    confirm_parser.add_argument("request_id")
    confirm_parser.add_argument("--actor", default="user")

    admission_parser = commands.add_parser("admit", help="evaluate deterministic admission")
    admission_parser.add_argument("request_id")

    start_parser = commands.add_parser("start", help="start an admitted request")
    start_parser.add_argument("request_id")
    start_parser.add_argument("--dry-run", action="store_true")

    guide_parser = commands.add_parser("guide", help="show the next safe action")
    guide_parser.add_argument("--request")

    commands.add_parser("status", help="show a one-shot workflow snapshot")
    logic_parser = commands.add_parser("logic", help="inspect source-grounded multiscale chains and logic gates")
    logic_parser.add_argument("--node")
    monitor_parser = commands.add_parser("monitor", help="follow workflow snapshots")
    monitor_parser.add_argument("--interval", type=float, default=5.0)
    monitor_parser.add_argument("--timeout", type=float, default=300.0)
    monitor_parser.add_argument("--once", action="store_true")

    trace_parser = commands.add_parser("trace", help="materialize conversation provenance")
    trace_parser.add_argument("--session", required=True)
    commands.add_parser("verify-log", help="verify the audit hash chain")
    commands.add_parser("list", help="list normalized requests")

    args = parser.parse_args(argv)
    try:
        root = find_root(args.root)
        command_payload = {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
            if key != "root"
        }
        append_event(
            root,
            "control.invoked",
            command_payload,
            session_id=getattr(args, "session_id", None),
            turn_id=getattr(args, "turn_id", None),
            actor="paper-review-operator",
        )
        if args.command == "intake":
            print_json(intake(root, args))
            return 0
        if args.command == "confirm":
            request = confirm_request(root, args.request_id, args.actor)
            print_json({"request": request, "next_action": _next_action(request)})
            return 0
        if args.command == "admit":
            report = evaluate_admission(root, load_request(root, args.request_id))
            print_json(report)
            return 0 if report["allowed"] else 1
        if args.command == "start":
            request = load_request(root, args.request_id)
            arguments = [
                "--root",
                str(root),
                "run",
                "--mode",
                request["intent"].lower(),
                "--request",
                request["request_id"],
            ]
            if args.dry_run:
                arguments.append("--dry-run")
            return review_main(arguments)
        if args.command == "guide":
            if args.request:
                request = load_request(root, args.request)
                print_json({"request_id": request["request_id"], "next_action": _next_action(request)})
            else:
                items = _requests(root)
                print_json(
                    {
                        "next_action": items[0]["next_action"] if items else "Describe the paper task in natural language for intake.",
                        "latest_request": items[0] if items else None,
                    }
                )
            return 0
        if args.command == "status":
            print_json(monitor_snapshot(root))
            return 0
        if args.command == "logic":
            from coherence import trace, gate_failures
            result = trace(load_json(root / ".review/coherence_registry.json"), args.node)
            result["gate_failures"] = gate_failures(root)
            print_json(result)
            return 0
        if args.command == "monitor":
            if args.interval <= 0 or args.interval > 60:
                raise HarnessError("monitor interval must be greater than 0 and no more than 60 seconds")
            deadline = time.monotonic() + args.timeout
            while True:
                snapshot = monitor_snapshot(root)
                print(json.dumps(snapshot, ensure_ascii=False), flush=True)
                if args.once or not snapshot["active"] or time.monotonic() >= deadline:
                    return 0
                time.sleep(args.interval)
        if args.command == "trace":
            print_json(materialize_conversation(root, args.session))
            return 0
        if args.command == "verify-log":
            report = verify_event_chain(root)
            print_json(report)
            return 0 if report["valid"] else 1
        if args.command == "list":
            print_json({"requests": _requests(root)})
            return 0
    except (HarnessError, ProvenanceError) as exc:
        try:
            root = find_root(args.root)
            append_event(root, "control.failed", {"command": args.command, "error": str(exc)})
        except (HarnessError, ProvenanceError):
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
