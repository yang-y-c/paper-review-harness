from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from admission import (
    canonicalize_request,
    confirm_request,
    load_request,
    require_admission,
    save_request,
)
from paper_review_lib import (
    CodexRunner,
    HarnessError,
    find_root,
    load_config,
    load_json,
    make_run_id,
    record_revisions,
    reset_layer_ledgers,
    update_state,
)
from provenance import append_event
from review import Workflow


LAYERED_INTENTS = {"REVIEW", "OPTIMIZE", "FULL"}
EDIT_INTENTS = {"REVISE", "OPTIMIZE", "FULL"}
FIXED_CORE = {
    "global_structure",
    "claims",
    "section_logic",
    "terminology",
    "notation",
    "data_consistency",
}


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _read_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    return args.text_file.read_text(encoding="utf-8")


def normalize(root: Path, natural_language: str, session_id: str | None, turn_id: str | None) -> dict[str, Any]:
    runner = CodexRunner(root, load_config(root))
    run_id = make_run_id("contract-intake")
    draft = runner.run(
        "intake_coordinator",
        {
            "task": "Normalize the paper task into an explicit user-facing review contract. Do not execute it.",
            "natural_language_request": natural_language,
            "repository_configuration": load_config(root),
            "contract_rule": (
                "For REVIEW/OPTIMIZE/FULL always emit review_contract. Fixed core dimensions are mandatory. "
                "If the user explicitly specifies dimensions, assurance, revision strategy and budget/profile, "
                "use selection_source USER_EXPLICIT. Otherwise propose a concrete contract with selection_source "
                "SKILL_PROPOSED and confirmation_required=true. Never silently choose maximum assurance."
            ),
        },
        run_id,
        audit_context={"session_id": session_id, "turn_id": turn_id, "request_id": None},
    )
    if draft["intent"] not in LAYERED_INTENTS:
        raise HarnessError("Contract-aware intake is for REVIEW, OPTIMIZE, or FULL; use legacy control for this intent")
    contract = draft.get("review_contract")
    if contract is None:
        raise HarnessError("Layered intake did not produce review_contract")
    _validate_contract_shape(contract, draft["intent"])
    request = canonicalize_request(
        root,
        draft,
        natural_language,
        session_id=session_id,
        turn_id=turn_id,
    )
    request["review_contract"] = contract
    if contract["selection_source"] == "SKILL_PROPOSED":
        request["confirmation"] = {
            "required": True,
            "confirmed": False,
            "confirmed_at": None,
            "confirmed_by": None,
        }
        request["status"] = "READY" if not request["missing_information"] else "DRAFT"
    save_request(root, request)
    append_event(
        root,
        "review_contract.normalized",
        {"request_id": request["request_id"], "contract": contract},
        session_id=session_id,
        turn_id=turn_id,
        request_id=request["request_id"],
        actor="paper-review-operator",
        artifact_refs=[{"path": f".review/requests/{request['request_id']}.json"}],
    )
    return {"request": request, "summary": summarize(request), "next_action": next_action(request)}


def _validate_contract_shape(contract: dict[str, Any], intent: str) -> None:
    dimensions = contract["dimensions"]
    disabled = sorted(name for name in FIXED_CORE if dimensions.get(name) is not True)
    if disabled:
        raise HarnessError("Fixed academic core dimensions cannot be disabled: " + ", ".join(disabled))
    revision = contract["revision"]["strategy"]
    if intent not in EDIT_INTENTS and revision != "REVIEW_ONLY":
        raise HarnessError("Read-only intent requires revision.strategy=REVIEW_ONLY")
    if intent in EDIT_INTENTS and revision == "REVIEW_ONLY":
        raise HarnessError("Edit-capable intent cannot use REVIEW_ONLY; use REVIEW intent instead")
    if contract["assurance"]["overall"] == "FAST" and dimensions["sentence_logic"] == "FULL":
        raise HarnessError("FAST assurance cannot request full-sentence logic; choose STANDARD or STRICT")


def effective_granularity(contract: dict[str, Any]) -> str:
    dimensions = contract["dimensions"]
    assurance = contract["assurance"]["overall"]
    if dimensions["sentence_logic"] == "FULL" or assurance == "STRICT":
        return "SENTENCE"
    if dimensions["paragraph_logic"] or dimensions["sentence_logic"] == "RISK_ADAPTIVE":
        return "ADAPTIVE"
    return "SUBSECTION"


def summarize(request: dict[str, Any]) -> dict[str, Any]:
    contract = request.get("review_contract")
    if not contract:
        return {"contract": "MISSING"}
    dimensions = contract["dimensions"]
    enabled = [
        name
        for name, value in dimensions.items()
        if value is True or value in {"RISK_ADAPTIVE", "FULL"}
    ]
    disabled = [name for name, value in dimensions.items() if value is False or value == "OFF"]
    return {
        "intent": request["intent"],
        "enabled_checks": enabled,
        "disabled_optional_checks": disabled,
        "assurance": contract["assurance"],
        "revision_strategy": contract["revision"]["strategy"],
        "budget": contract["budget"],
        "effective_granularity": effective_granularity(contract),
        "selection_source": contract["selection_source"],
    }


def next_action(request: dict[str, Any]) -> str:
    if request["missing_information"]:
        return "Resolve the listed material unknowns and normalize again."
    contract = request.get("review_contract")
    if not contract:
        return "Normalize this task with the contract-aware intake before execution."
    if request["confirmation"]["required"] and not request["confirmation"]["confirmed"]:
        return f"Show the review contract and ask the user to confirm {request['request_id']}."
    return f"Run contract admission and start {request['request_id']}."


def _require_contract_confirmation(request: dict[str, Any]) -> dict[str, Any]:
    contract = request.get("review_contract")
    if not contract:
        raise HarnessError("Request has no review_contract; normalize it with scripts/review_contract.py intake")
    _validate_contract_shape(contract, request["intent"])
    if contract["selection_source"] == "SKILL_PROPOSED" and not request["confirmation"]["confirmed"]:
        raise HarnessError("A skill-proposed review contract must be shown to and confirmed by the user before execution")
    if request["confirmation"]["required"] and not request["confirmation"]["confirmed"]:
        raise HarnessError("Request requires user confirmation before execution")
    return contract


def _issue_versions(workflow: Workflow) -> dict[str, str]:
    return {issue["id"]: issue["updated_at"] for issue in workflow.issues_ledger["issues"]}


def _current_structural_issues(workflow: Workflow, baseline: dict[str, str]) -> list[dict[str, Any]]:
    """Return only severe Issues created or re-triggered by this checkpoint pass."""
    return [
        issue
        for issue in workflow.issues_ledger["issues"]
        if issue["status"] == "OPEN"
        and issue["severity"] in {"BLOCKER", "MAJOR"}
        and (issue["id"] not in baseline or issue["updated_at"] != baseline[issue["id"]])
    ]


def _revise_targets(workflow: Workflow, targets: list[dict[str, Any]]) -> int:
    """Revise exactly the checkpoint findings, never unrelated historical Issues."""
    if not targets:
        return 0
    claims_by_id = {claim["id"]: claim for claim in workflow.claims_ledger["claims"]}
    relevant_ids = {issue["claim_id"] for issue in targets if issue.get("claim_id")}
    relevant = [claims_by_id[cid] for cid in sorted(relevant_ids) if cid in claims_by_id]
    update_state(workflow.root, phase="STRUCTURAL_REVISION")
    run_id = make_run_id("structural-revision")
    update_state(workflow.root, active_run_id=run_id)
    assignment = {
        "task": "Address only these structural/core checkpoint Issues with the smallest scientifically defensible manuscript change.",
        "main_tex": workflow.config["main_tex"],
        "manuscript_roots": workflow.config["manuscript_roots"],
        "issues": targets,
        "claims": relevant,
        "global_contract": load_json(workflow.root / ".review/global_contract.json"),
        "hierarchy": load_json(workflow.root / ".review/structure.json"),
        "terminology_registry": load_json(workflow.root / ".review/terminology.json"),
        "notation_registry": load_json(workflow.root / ".review/notation.json"),
        "data_consistency": load_json(workflow.root / ".review/data_consistency.json"),
        "argument_graph": load_json(workflow.root / ".review/argument_graph.json"),
        "claim_consistency": load_json(workflow.root / ".review/claim_consistency.json"),
        "constraints": [
            "Do not address Issues outside the supplied checkpoint list.",
            "Do not set any Issue to RESOLVED.",
            "Use NEEDS_AUTHOR instead of inventing evidence or intent.",
            "Preserve unrelated user changes and defer local prose polish until the detail stage.",
        ],
    }
    output = workflow.runner.run("reviser", assignment, run_id)
    record_revisions(workflow.root, output, run_id)
    update_state(workflow.root, last_run_id=run_id, active_run_id=None)
    return len(output["revisions"])


def structural_checkpoint(workflow: Workflow, granularity: str, max_rounds: int) -> dict[str, Any]:
    """Stabilize slow structural variables before expensive paragraph/sentence review."""
    attempts = []
    for round_id in range(1, max_rounds + 1):
        baseline = _issue_versions(workflow)
        reset_layer_ledgers(workflow.root)
        workflow.map_claims(force=True)
        workflow.map_invariants(force=True)
        workflow.macro_control()
        workflow.hierarchy_control("SUBSECTION" if granularity != "MACRO_ONLY" else "MACRO_ONLY")
        structural = _current_structural_issues(workflow, baseline)
        attempts.append({"round": round_id, "open_structural_issues": [i["id"] for i in structural]})
        if not structural:
            return {"status": "STABLE", "attempts": attempts}
        revised = _revise_targets(workflow, structural)
        workflow.verify()
        if revised == 0:
            return {
                "status": "BLOCKED",
                "attempts": attempts,
                "reason": "Current structural issues remain but no machine revision was produced",
            }
    return {
        "status": "BLOCKED",
        "attempts": attempts,
        "reason": "Structural checkpoint did not converge within max_rounds",
    }


def _profile_review(workflow: Workflow, request: dict[str, Any], *, final_audit: bool = True) -> list[str]:
    contract = request["review_contract"]
    granularity = effective_granularity(contract)
    selected = request["scope"].get("claim_ids") or None
    return workflow.review(selected, granularity=granularity, run_final_audit=final_audit)


def _staged_optimize(workflow: Workflow, request: dict[str, Any]) -> dict[str, Any]:
    contract = request["review_contract"]
    granularity = effective_granularity(contract)
    max_rounds = request["constraints"].get("max_rounds") or int(workflow.config["max_rounds"])
    checkpoint = structural_checkpoint(workflow, granularity, max_rounds)
    if checkpoint["status"] != "STABLE":
        raise HarnessError(checkpoint["reason"])
    optimized = workflow.optimize(granularity)
    return {"status": "COMPLETED", "structural_checkpoint": checkpoint, "optimization": optimized}


def _staged_full(workflow: Workflow, request: dict[str, Any]) -> dict[str, Any]:
    contract = request["review_contract"]
    granularity = effective_granularity(contract)
    max_rounds = request["constraints"].get("max_rounds") or int(workflow.config["max_rounds"])
    checkpoint = structural_checkpoint(workflow, granularity, max_rounds)
    if checkpoint["status"] != "STABLE":
        raise HarnessError(checkpoint["reason"])

    last_report = None
    for round_id in range(1, max_rounds + 1):
        update_state(workflow.root, round=round_id)
        agents = _profile_review(workflow, request, final_audit=True)
        actionable = [i for i in workflow.issues_ledger["issues"] if i["status"] == "OPEN"]
        revised = workflow.revise() if actionable else 0
        verified = workflow.verify()
        if revised:
            workflow.map_claims(force=True)
            workflow.map_invariants(force=True)
            workflow.macro_control()
            workflow.hierarchy_control(granularity)
            workflow.granular_control(granularity)
            workflow.final_audit(granularity)
        last_report = workflow.validate(final=True)
        if last_report["passed"]:
            return {
                "status": "ACCEPT",
                "structural_checkpoint": checkpoint,
                "rounds": round_id,
                "agents": agents,
                "revised": revised,
                "verified": verified,
                "validation": last_report,
            }
        remaining = [
            i
            for i in workflow.issues_ledger["issues"]
            if i["status"] in {"OPEN", "CLAIMED_FIXED"}
        ]
        if not remaining:
            raise HarnessError(
                "Validation failed with no machine-actionable Issue; author input or configuration is required"
            )
    raise HarnessError(f"Contract-driven full review did not converge within {max_rounds} rounds")


def execute(root: Path, request_id: str, dry_run: bool = False) -> dict[str, Any]:
    request = load_request(root, request_id)
    if request["intent"] not in LAYERED_INTENTS:
        raise HarnessError("Contract runner executes REVIEW, OPTIMIZE, or FULL layered tasks only")
    contract = _require_contract_confirmation(request)
    admission = require_admission(root, request)
    plan = {
        "request_id": request_id,
        "intent": request["intent"],
        "review_contract": contract,
        "effective_granularity": effective_granularity(contract),
        "staged_revision": contract["revision"]["strategy"] == "STAGED_REVISION",
    }
    if dry_run:
        return {"admission": admission, "plan": plan, "will_call_codex": False}

    workflow = Workflow(root)
    request["status"] = "EXECUTING"
    save_request(root, request)
    append_event(
        root,
        "contract_workflow.started",
        plan,
        request_id=request_id,
        actor="review-contract-runner",
    )
    intent = request["intent"]
    strategy = contract["revision"]["strategy"]
    if intent == "REVIEW":
        result: Any = {"agents": _profile_review(workflow, request, final_audit=True)}
    elif intent == "OPTIMIZE" and strategy == "STAGED_REVISION":
        result = _staged_optimize(workflow, request)
    elif intent == "FULL" and strategy == "STAGED_REVISION":
        result = _staged_full(workflow, request)
    elif intent == "OPTIMIZE":
        result = workflow.optimize(effective_granularity(contract))
    elif intent == "FULL":
        rounds = request["constraints"].get("max_rounds") or int(workflow.config["max_rounds"])
        result = workflow.full(rounds, effective_granularity(contract))
    else:
        raise HarnessError(f"Unsupported contract execution intent: {intent}")
    request["status"] = "COMPLETED"
    save_request(root, request)
    append_event(
        root,
        "contract_workflow.completed",
        {"request_id": request_id},
        request_id=request_id,
        actor="review-contract-runner",
    )
    return {"plan": plan, "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Contract-aware Paper Review Harness entrypoint")
    parser.add_argument("--root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("intake")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file", type=Path)
    p.add_argument("--session-id")
    p.add_argument("--turn-id")

    p = commands.add_parser("confirm")
    p.add_argument("request_id")
    p.add_argument("--actor", default="user")

    p = commands.add_parser("show")
    p.add_argument("request_id")

    p = commands.add_parser("start")
    p.add_argument("request_id")
    p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    try:
        root = find_root(args.root)
        if args.command == "intake":
            _json(normalize(root, _read_text(args), args.session_id, args.turn_id))
        elif args.command == "confirm":
            request = confirm_request(root, args.request_id, args.actor)
            _json({"request": request, "summary": summarize(request), "next_action": next_action(request)})
        elif args.command == "show":
            request = load_request(root, args.request_id)
            _json({"request_id": args.request_id, "summary": summarize(request), "next_action": next_action(request)})
        elif args.command == "start":
            _json(execute(root, args.request_id, args.dry_run))
        return 0
    except HarnessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
