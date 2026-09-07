from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any

from paper_review_lib import (
    AGENT_SCHEMA,
    HarnessError,
    load_config,
    load_json,
    load_state,
    resolve_repo_path,
    utc_now,
    validate_with_schema,
    write_json,
)
from provenance import append_event, redact


ACTION_INTENTS = {"DISCUSS", "REVIEW", "REVISE", "VERIFY", "OPTIMIZE", "FULL"}
EDIT_INTENTS = {"REVISE", "OPTIMIZE", "FULL"}
LAYERED_INTENTS = {"REVIEW", "OPTIMIZE", "FULL"}
INTENT_AGENTS = {
    "DISCUSS": {"claim_mapper", "argument_reviewer", "challenger"},
    "REVIEW": set(AGENT_SCHEMA) - {"reviser", "verifier", "intake_coordinator"},
    "REVISE": {"reviser"},
    "VERIFY": {"verifier"},
    "OPTIMIZE": {
        "invariant_mapper",
        "macro_architect",
        "hierarchy_reviewer",
        "language_coherence_reviewer",
        "argument_reviewer",
        "reviser",
        "verifier",
        "final_integrity_auditor",
    },
    "FULL": set(AGENT_SCHEMA) - {"intake_coordinator"},
}


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonicalize_request(
    root: Path,
    draft: dict[str, Any],
    natural_language: str,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    validate_with_schema(root, "request-draft.schema.json", draft)
    timestamp = utc_now()
    request_id = f"REQ-{timestamp[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
    intent = draft["intent"]
    constraints = dict(draft["constraints"])
    confirmation_required = bool(draft["confirmation_required"] or intent in EDIT_INTENTS)
    request = {
        "schema_version": 1,
        "request_id": request_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "source": {
            "session_id": session_id,
            "turn_id": turn_id,
            "natural_language_sha256": _text_hash(natural_language),
            "natural_language_excerpt": redact(natural_language[:1000]),
        },
        "intent": intent,
        "scope": draft["scope"],
        "objectives": draft["objectives"],
        "constraints": constraints,
        "granularity": draft["granularity"],
        "assumptions": draft["assumptions"],
        "missing_information": draft["missing_information"],
        "confirmation": {
            "required": confirmation_required,
            "confirmed": not confirmation_required,
            "confirmed_at": timestamp if not confirmation_required else None,
            "confirmed_by": "policy" if not confirmation_required else None,
        },
        "status": "READY" if not draft["missing_information"] else "DRAFT",
        "normalization_notes": draft["normalization_notes"],
    }
    validate_with_schema(root, "request.schema.json", request)
    path = root / ".review" / "requests" / f"{request_id}.json"
    write_json(path, request)
    append_event(
        root,
        "request.normalized",
        request,
        session_id=session_id,
        turn_id=turn_id,
        request_id=request_id,
        artifact_refs=[{"path": str(path.relative_to(root))}],
    )
    return request


def load_request(root: Path, request_id: str) -> dict[str, Any]:
    request = load_json(root / ".review" / "requests" / f"{request_id}.json")
    validate_with_schema(root, "request.schema.json", request)
    return request


def save_request(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    request["updated_at"] = utc_now()
    validate_with_schema(root, "request.schema.json", request)
    write_json(root / ".review" / "requests" / f"{request['request_id']}.json", request)
    return request


def confirm_request(root: Path, request_id: str, actor: str = "user") -> dict[str, Any]:
    request = load_request(root, request_id)
    if request["missing_information"]:
        raise HarnessError("Request still has material missing_information and cannot be confirmed")
    request["confirmation"] = {
        "required": request["confirmation"]["required"],
        "confirmed": True,
        "confirmed_at": utc_now(),
        "confirmed_by": actor,
    }
    request["status"] = "CONFIRMED"
    save_request(root, request)
    append_event(
        root,
        "request.confirmed",
        {"confirmed_by": actor},
        session_id=request["source"].get("session_id"),
        turn_id=request["source"].get("turn_id"),
        request_id=request_id,
        artifact_refs=[
            {"path": str((root / ".review" / "requests" / f"{request_id}.json").relative_to(root))}
        ],
    )
    return request


def _check(checks: list[dict[str, str]], identifier: str, passed: bool, detail: str) -> None:
    checks.append({"id": identifier, "status": "PASS" if passed else "FAIL", "detail": detail})


def evaluate_admission(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    config = load_config(root)
    state = load_state(root)
    checks: list[dict[str, str]] = []
    schema_ok = True
    try:
        validate_with_schema(root, "request.schema.json", request)
    except HarnessError:
        schema_ok = False
    _check(checks, "A01", schema_ok, "Canonical request satisfies request.schema.json")
    intent = request.get("intent")
    _check(checks, "A02", intent in ACTION_INTENTS, f"Intent {intent!r} is executable")
    try:
        main_tex = resolve_repo_path(root, config["main_tex"])
        main_exists = main_tex.is_file()
    except HarnessError:
        main_exists = False
    _check(checks, "A03", main_exists, f"Main manuscript exists: {config['main_tex']}")
    objectives = request.get("objectives", [])
    objective_ok = bool(objectives) and all(item.get("success_criteria") for item in objectives)
    _check(checks, "A04", objective_ok, "Every objective has measurable success criteria")
    no_missing = not request.get("missing_information")
    _check(checks, "A05", no_missing, "No material input is missing")
    confirmation = request.get("confirmation", {})
    confirmation_ok = intent not in EDIT_INTENTS or (
        confirmation.get("required") and confirmation.get("confirmed")
    )
    _check(checks, "A06", confirmation_ok, "Edit-capable work has explicit user confirmation")
    constraints = request.get("constraints", {})
    edit_permission = intent not in EDIT_INTENTS or constraints.get("allow_manuscript_edits") is True
    _check(checks, "A07", edit_permission, "Manuscript edit permission matches the intent")
    evidence_ok = constraints.get("evidence_policy") == "NO_FABRICATION"
    _check(checks, "A08", evidence_ok, "Evidence policy forbids fabrication")
    allowed = set(constraints.get("allowed_changes", []))
    forbidden = set(constraints.get("forbidden_changes", []))
    _check(checks, "A09", not allowed.intersection(forbidden), "Allowed and forbidden changes do not conflict")
    required_agents = INTENT_AGENTS.get(str(intent), set())
    missing_agents = [
        agent for agent in sorted(required_agents) if not (root / ".codex" / "agents" / f"{agent}.toml").is_file()
    ]
    missing_schemas = [
        AGENT_SCHEMA[agent]
        for agent in sorted(required_agents)
        if not (root / ".review" / "schemas" / AGENT_SCHEMA[agent]).is_file()
    ]
    _check(
        checks,
        "A10",
        not missing_agents and not missing_schemas,
        "Required agent profiles and output schemas are present"
        if not missing_agents and not missing_schemas
        else f"Missing agents={missing_agents}, schemas={missing_schemas}",
    )
    active_owner = state.get("active_request_id")
    no_conflict = not state.get("active") or active_owner in {None, request.get("request_id")}
    _check(checks, "A11", no_conflict, f"No conflicting active request (current={active_owner})")
    rounds = constraints.get("max_rounds") or config.get("max_rounds")
    _check(checks, "A12", isinstance(rounds, int) and 1 <= rounds <= 100, "Round limit is between 1 and 100")
    compilation_ready = not (
        intent == "FULL" and config.get("gates", {}).get("require_compilation")
    ) or bool(config.get("build_command"))
    _check(checks, "A13", compilation_ready, "Required final compilation has a configured build command")
    granularity = request.get("granularity", {})
    granularity_ok = intent not in LAYERED_INTENTS or (
        granularity.get("level") is not None and (
            granularity.get("explicit") is True or granularity.get("level") == "ADAPTIVE"
        )
    )
    _check(
        checks,
        "A14",
        granularity_ok,
        "Layered review uses the disclosed ADAPTIVE baseline or an explicit depth override",
    )
    codex_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    humanizer_path = codex_root / "skills" / "humanizer" / "SKILL.md"
    humanizer_ok = intent not in LAYERED_INTENTS or (
        not config.get("require_humanizer", True) or humanizer_path.is_file()
    )
    _check(
        checks,
        "A15",
        humanizer_ok,
        f"Required humanizer skill is available: {humanizer_path}",
    )
    invariant_artifacts = [
        ("terminology.json", "terminology-registry.schema.json"),
        ("notation.json", "notation-registry.schema.json"),
        ("data_consistency.json", "data-registry.schema.json"),
        ("argument_graph.json", "argument-graph.schema.json"),
        ("claim_consistency.json", "claim-consistency.schema.json"),
        ("redundancy.json", "redundancy-diagnostics.schema.json"),
    ]
    missing_invariants = [
        name
        for ledger, schema in invariant_artifacts
        for name, path in [
            (ledger, root / ".review" / ledger),
            (schema, root / ".review" / "schemas" / schema),
        ]
        if not path.is_file()
    ]
    invariant_ready = intent not in LAYERED_INTENTS or not missing_invariants
    _check(
        checks,
        "A16",
        invariant_ready,
        "Invariant ledger templates and schemas are present"
        if invariant_ready
        else f"Missing invariant artifacts={missing_invariants}",
    )
    scientific = config.get("scientific_validators", [])
    scientific_ids: list[str] = []
    scientific_config_ok = isinstance(scientific, list)
    if scientific_config_ok:
        for item in scientific:
            if not isinstance(item, dict):
                scientific_config_ok = False
                break
            identifier = item.get("id")
            command = item.get("command")
            timeout = item.get("timeout_seconds", 300)
            if (
                not isinstance(identifier, str)
                or re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", identifier) is None
                or identifier in scientific_ids
                or not isinstance(command, list)
                or not command
                or not all(isinstance(part, str) and part for part in command)
                or not isinstance(item.get("required", True), bool)
                or not isinstance(timeout, int)
                or not 1 <= timeout <= 3600
            ):
                scientific_config_ok = False
                break
            scientific_ids.append(identifier)
    scientific_config_ok = intent != "FULL" or scientific_config_ok
    _check(
        checks,
        "A17",
        scientific_config_ok,
        "Scientific-validator configuration is structurally executable",
    )
    coherence_error = None
    if intent in LAYERED_INTENTS:
        try:
            from coherence import build_inventory, policy_from
            from logic_graph import ontology
            policy_from(config)
            ontology(root)
            for schema in ("coherence-output.schema.json", "coherence-registry.schema.json",
                           "logic-graph.schema.json", "local-logic-output.schema.json",
                           "logic-coverage-output.schema.json", "logic-map-output.schema.json",
                           "logic-verify-output.schema.json"):
                if not (root / ".review/schemas" / schema).is_file():
                    raise HarnessError(f"Missing multiscale schema:{schema}")
            errors = build_inventory(root)["errors"]
            if errors:
                raise HarnessError("; ".join(errors))
        except (HarnessError, OSError, ValueError, KeyError) as exc:
            coherence_error = str(exc)
    _check(checks, "A18", coherence_error is None,
           coherence_error or "Multiscale ontology, policy, schemas and source inventory are ready")
    report = {
        "schema_version": 1,
        "admission_id": f"ADM-{uuid.uuid4().hex[:12]}",
        "request_id": request.get("request_id"),
        "evaluated_at": utc_now(),
        "allowed": all(item["status"] == "PASS" for item in checks),
        "checks": checks,
    }
    validate_with_schema(root, "admission.schema.json", report)
    path = root / ".review" / "admission" / f"{request.get('request_id', 'invalid')}.json"
    write_json(path, report)
    append_event(
        root,
        "admission.evaluated",
        report,
        session_id=request.get("source", {}).get("session_id"),
        turn_id=request.get("source", {}).get("turn_id"),
        request_id=request.get("request_id"),
        artifact_refs=[{"path": str(path.relative_to(root))}],
    )
    return report


def require_admission(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    report = evaluate_admission(root, request)
    if not report["allowed"]:
        failed = [f"{item['id']}: {item['detail']}" for item in report["checks"] if item["status"] == "FAIL"]
        raise HarnessError("Admission denied:\n" + "\n".join(failed))
    return report
