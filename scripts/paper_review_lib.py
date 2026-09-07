from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from provenance import append_event, redact, sha256_file, sha256_value


class HarnessError(RuntimeError):
    pass


CATEGORY_PREFIX = {
    "theory": "TH",
    "algorithm": "AL",
    "numerical": "NU",
    "argument": "AR",
    "evidence": "EV",
    "consistency": "CO",
    "presentation": "PR",
    "production": "PD",
}

AGENT_SCHEMA = {
    "intake_coordinator": "request-draft.schema.json",
    "invariant_mapper": "invariant-output.schema.json",
    "macro_architect": "macro-output.schema.json",
    "hierarchy_reviewer": "hierarchy-output.schema.json",
    "language_coherence_reviewer": "language-output.schema.json",
    "final_integrity_auditor": "final-audit-output.schema.json",
    "claim_mapper": "claim-map-output.schema.json",
    "theory_reviewer": "review-output.schema.json",
    "algorithm_reviewer": "review-output.schema.json",
    "numerical_reviewer": "review-output.schema.json",
    "argument_reviewer": "review-output.schema.json",
    "challenger": "review-output.schema.json",
    "reviser": "revision-output.schema.json",
    "verifier": "verification-output.schema.json",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def find_root(start: str | Path | None = None) -> Path:
    here = Path(start or Path.cwd()).resolve()
    if here.is_file():
        here = here.parent
    for candidate in (here, *here.parents):
        if (candidate / ".review" / "config.json").is_file():
            return candidate
    raise HarnessError("Cannot find a repository root containing .review/config.json")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HarnessError(f"Required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HarnessError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"Expected a JSON object in {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def load_config(root: Path) -> dict[str, Any]:
    config = load_json(root / ".review" / "config.json")
    required = {
        "main_tex",
        "manuscript_roots",
        "codex_command",
        "max_rounds",
        "max_parallel_reviewers",
        "gates",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise HarnessError(f"Config is missing fields: {', '.join(missing)}")
    return config


def load_state(root: Path) -> dict[str, Any]:
    return load_json(root / ".review" / "state.json")


def update_state(root: Path, **changes: Any) -> dict[str, Any]:
    state = load_state(root)
    state.update(changes)
    write_json(root / ".review" / "state.json", state)
    return state


def schema_path(root: Path, name: str) -> Path:
    path = root / ".review" / "schemas" / name
    if not path.is_file():
        raise HarnessError(f"Schema is missing: {path}")
    return path


def _json_pointer(value: Any, fragment: str) -> Any:
    if not fragment or fragment == "#":
        return value
    if not fragment.startswith("#/"):
        raise HarnessError(f"Unsupported JSON Schema fragment: {fragment}")
    current = value
    for encoded in fragment[2:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise HarnessError(f"Unresolvable JSON Schema fragment: {fragment}")
        current = current[token]
    return current


def bundled_schema(root: Path, schema_name: str) -> dict[str, Any]:
    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#"):
            target_name, separator, fragment_value = reference.partition("#")
            target = load_json(schema_path(root, target_name))
            fragment = f"#{fragment_value}" if separator else ""
            replacement = expand(deepcopy(_json_pointer(target, fragment)))
            siblings = {key: expand(item) for key, item in value.items() if key != "$ref"}
            if siblings:
                return {"allOf": [replacement, siblings]}
            return replacement
        return {key: expand(item) for key, item in value.items()}

    return expand(load_json(schema_path(root, schema_name)))


def validate_with_schema(root: Path, schema_name: str, value: dict[str, Any]) -> None:
    schema = load_json(schema_path(root, schema_name))
    registry = Registry()
    for candidate in (root / ".review" / "schemas").glob("*.json"):
        contents = load_json(candidate)
        registry = registry.with_resource(candidate.name, Resource.from_contents(contents))
    validator = Draft202012Validator(
        schema, format_checker=FormatChecker(), registry=registry
    )
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        rendered = []
        for error in errors[:20]:
            location = ".".join(str(part) for part in error.absolute_path) or "$"
            rendered.append(f"{location}: {error.message}")
        if len(errors) > 20:
            rendered.append(f"... {len(errors) - 20} more errors")
        raise HarnessError(f"{schema_name} validation failed:\n" + "\n".join(rendered))


def resolve_repo_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise HarnessError(f"Path escapes repository root: {relative}") from exc
    return candidate


def route_claim(claim: dict[str, Any]) -> list[str]:
    if claim.get("status") == "REMOVED":
        return []
    agents: set[str] = set()
    claim_types = set(claim.get("type", []))
    if "theoretical" in claim_types:
        agents.add("theory_reviewer")
    if "algorithmic" in claim_types:
        agents.add("algorithm_reviewer")
    if "numerical" in claim_types:
        agents.add("numerical_reviewer")
    if claim.get("centrality") == "CORE" or claim_types & {"interpretive", "presentation"}:
        agents.add("argument_reviewer")
    if claim.get("strength") in {"STRONG", "EXTREME"}:
        agents.add("challenger")
    return sorted(agents)


def route_all_claims(claims: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    routed: dict[str, list[str]] = {}
    for claim in claims:
        routed[claim["id"]] = route_claim(claim)
    return routed


def reverse_dependencies(claims: Iterable[dict[str, Any]], changed_ids: Iterable[str]) -> list[str]:
    reverse: dict[str, set[str]] = {}
    for claim in claims:
        for dependency in claim.get("dependencies", []):
            reverse.setdefault(dependency, set()).add(claim["id"])
    affected = set(changed_ids)
    queue = list(affected)
    while queue:
        current = queue.pop(0)
        for dependent in sorted(reverse.get(current, set())):
            if dependent not in affected:
                affected.add(dependent)
                queue.append(dependent)
    return sorted(affected)


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def issue_fingerprint(issue: dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(issue.get("claim_id") or ""),
            str(issue.get("category") or ""),
            _normalized_text(str(issue.get("location") or "")),
            _normalized_text(str(issue.get("problem") or "")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def next_prefixed_id(existing: Iterable[str], prefix: str, marker: str = "-") -> str:
    expression = re.compile(rf"^{re.escape(prefix)}{re.escape(marker)}?(\d+)$")
    highest = 0
    for identifier in existing:
        match = expression.match(identifier)
        if match:
            highest = max(highest, int(match.group(1)))
    separator = marker if marker else ""
    return f"{prefix}{separator}{highest + 1:03d}"


def merge_claim_map(root: Path, output: dict[str, Any]) -> dict[str, Any]:
    validate_with_schema(root, AGENT_SCHEMA["claim_mapper"], output)
    ledger_path = root / ".review" / "claims.json"
    ledger = load_json(ledger_path)
    existing = {claim["id"]: claim for claim in ledger.get("claims", [])}
    incoming_ids = {claim["id"] for claim in output["claims"]}
    for claim in output["claims"]:
        current = existing.get(claim["id"], {})
        merged = dict(claim)
        merged["status"] = current.get("status", "MAPPED")
        if merged["status"] == "REMOVED":
            merged["status"] = "MAPPED"
        merged["reviewed_by"] = sorted(set(current.get("reviewed_by", [])))
        if merged["strength"] in {"STRONG", "EXTREME"}:
            merged["adversarial_status"] = current.get("adversarial_status", "PENDING")
            if merged["adversarial_status"] == "NOT_REQUIRED":
                merged["adversarial_status"] = "PENDING"
        else:
            merged["adversarial_status"] = "NOT_REQUIRED"
        existing[claim["id"]] = merged
    removed_ids = set(existing) - incoming_ids
    for claim_id in removed_ids:
        note = "Not present in the latest mapper output; retained for ledger history."
        if note not in existing[claim_id].setdefault("notes", []):
            existing[claim_id]["notes"].append(note)
        existing[claim_id]["status"] = "REMOVED"
        existing[claim_id]["adversarial_status"] = "NOT_REQUIRED"
    ledger = {
        "schema_version": 1,
        "coverage": output["coverage"],
        "claims": sorted(existing.values(), key=lambda item: item["id"]),
    }
    validate_with_schema(root, "claims.schema.json", ledger)
    write_json(ledger_path, ledger)
    return ledger


def _canonical_issue(
    issue: dict[str, Any], agent: str, existing_ids: set[str]
) -> dict[str, Any]:
    prefix = CATEGORY_PREFIX[issue["category"]]
    proposed = str(issue.get("id") or "")
    if proposed in existing_ids or not re.match(r"^[A-Z]{2}-\d{3,}$", proposed):
        proposed = next_prefixed_id(existing_ids, prefix)
    existing_ids.add(proposed)
    timestamp = utc_now()
    return {
        "id": proposed,
        "claim_id": issue.get("claim_id"),
        "location": issue["location"],
        "severity": issue["severity"],
        "category": issue["category"],
        "problem": issue["problem"],
        "why_it_matters": issue["why_it_matters"],
        "required_action": issue["required_action"],
        "verification_criterion": issue["verification_criterion"],
        "source_agents": [agent],
        "status": issue.get("status", "OPEN"),
        "verification_status": "NOT_RUN",
        "created_at": timestamp,
        "updated_at": timestamp,
        "notes": list(issue.get("notes", [])),
    }


def merge_review_output(root: Path, output: dict[str, Any]) -> dict[str, Any]:
    validate_with_schema(root, "review-output.schema.json", output)
    agent = output["agent"]
    claims_path = root / ".review" / "claims.json"
    claims_ledger = load_json(claims_path)
    claims_by_id = {claim["id"]: claim for claim in claims_ledger["claims"]}
    for claim_id in output["reviewed_claims"]:
        if claim_id not in claims_by_id:
            raise HarnessError(f"{agent} reviewed unknown Claim {claim_id}")
        claim = claims_by_id[claim_id]
        claim["reviewed_by"] = sorted(set(claim.get("reviewed_by", [])) | {agent})
        if claim["status"] == "MAPPED":
            claim["status"] = "UNDER_REVIEW"
        if agent == "challenger":
            claim["adversarial_status"] = "REVIEWED"
            if output["issues"]:
                claim["status"] = "CHALLENGED"
    validate_with_schema(root, "claims.schema.json", claims_ledger)
    write_json(claims_path, claims_ledger)

    issues_path = root / ".review" / "issues.json"
    ledger = load_json(issues_path)
    existing_ids = {item["id"] for item in ledger["issues"]}
    by_fingerprint = {issue_fingerprint(item): item for item in ledger["issues"]}
    severity_rank = {"STYLE": 0, "MINOR": 1, "MAJOR": 2, "BLOCKER": 3}
    for issue in output["issues"]:
        claim_id = issue.get("claim_id")
        if claim_id is not None and claim_id not in claims_by_id:
            raise HarnessError(f"{agent} reported an Issue for unknown Claim {claim_id}")
        fingerprint = issue_fingerprint(issue)
        if fingerprint in by_fingerprint:
            current = by_fingerprint[fingerprint]
            current["source_agents"] = sorted(set(current["source_agents"]) | {agent})
            if severity_rank[issue["severity"]] > severity_rank[current["severity"]]:
                current["severity"] = issue["severity"]
            current["updated_at"] = utc_now()
            for note in issue.get("notes", []):
                if note not in current.setdefault("notes", []):
                    current["notes"].append(note)
            if current["status"] == "RESOLVED":
                current["status"] = "OPEN"
                current["verification_status"] = "NOT_RUN"
                current.setdefault("notes", []).append(
                    f"Reopened after a new independent finding from {agent}."
                )
            continue
        canonical = _canonical_issue(issue, agent, existing_ids)
        ledger["issues"].append(canonical)
        by_fingerprint[fingerprint] = canonical
    ledger["issues"].sort(key=lambda item: item["id"])
    validate_with_schema(root, "issues.schema.json", ledger)
    write_json(issues_path, ledger)
    return ledger


LAYER_LEDGER = {
    "macro_architect": ("global_contract.json", "global-contract.schema.json"),
    "hierarchy_reviewer": ("structure.json", "structure.schema.json"),
    "language_coherence_reviewer": ("granular_review.json", "granular-review.schema.json"),
    "final_integrity_auditor": ("final_audit.json", "final-audit.schema.json"),
}

INVARIANT_LEDGER = {
    "terminology_registry": ("terminology.json", "terminology-registry.schema.json"),
    "notation_registry": ("notation.json", "notation-registry.schema.json"),
    "data_registry": ("data_consistency.json", "data-registry.schema.json"),
    "argument_graph": ("argument_graph.json", "argument-graph.schema.json"),
    "claim_consistency": ("claim_consistency.json", "claim-consistency.schema.json"),
    "redundancy_diagnostics": ("redundancy.json", "redundancy-diagnostics.schema.json"),
}

GENERATED_MANUSCRIPT_SUFFIXES = {
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".run.xml",
    ".synctex.gz",
    ".toc",
}


def manuscript_snapshot(root: Path) -> dict[str, Any]:
    """Hash configured manuscript inputs while excluding ordinary build products."""
    config = load_config(root)
    candidates: dict[str, Path] = {}
    configured = [config["main_tex"]]
    configured.extend(config.get("manuscript_roots", []))
    configured.extend(config.get("figure_roots", []))
    configured.extend(config.get("evidence_roots", []))
    configured.extend(config.get("bibliography_files", []))
    generated_pdf = resolve_repo_path(root, config["main_tex"]).with_suffix(".pdf")
    for relative in configured:
        path = resolve_repo_path(root, relative)
        paths = [path] if path.is_file() else path.rglob("*") if path.is_dir() else []
        for candidate in paths:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            try:
                relative_path = resolved.relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            lowered = resolved.name.casefold()
            if resolved == generated_pdf or any(
                lowered.endswith(suffix) for suffix in GENERATED_MANUSCRIPT_SUFFIXES
            ):
                continue
            candidates[relative_path] = resolved
    files = [
        {"path": relative, "sha256": sha256_file(path)}
        for relative, path in sorted(candidates.items())
    ]
    return {"sha256": sha256_value(files), "files": files}


def _empty_invariant_ledger() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "NOT_RUN",
        "run_id": None,
        "updated_at": None,
        "source_snapshot": None,
        "payload": None,
    }


def reset_layer_ledgers(root: Path) -> None:
    for file_name, schema_name in [
        ("global_contract.json", "global-contract.schema.json"),
        ("structure.json", "structure.schema.json"),
        ("granular_review.json", "granular-review.schema.json"),
        ("final_audit.json", "final-audit.schema.json"),
    ]:
        ledger = {
            "schema_version": 1,
            "status": "NOT_RUN",
            "run_id": None,
            "updated_at": None,
            "payload": None,
        }
        validate_with_schema(root, schema_name, ledger)
        write_json(root / ".review" / file_name, ledger)
    for file_name, schema_name in INVARIANT_LEDGER.values():
        ledger = _empty_invariant_ledger()
        validate_with_schema(root, schema_name, ledger)
        write_json(root / ".review" / file_name, ledger)
    append_event(root, "layers.reset", {"reason": "new layered review request"})


def invalidate_invariant_ledgers(root: Path, reason: str, run_id: str | None = None) -> None:
    artifacts: list[dict[str, str]] = []
    for file_name, schema_name in INVARIANT_LEDGER.values():
        path = root / ".review" / file_name
        ledger = load_json(path)
        if ledger["status"] == "CURRENT":
            ledger["status"] = "STALE"
            ledger["updated_at"] = utc_now()
            validate_with_schema(root, schema_name, ledger)
            write_json(path, ledger)
            artifacts.append({"path": str(path.relative_to(root))})
    if artifacts:
        append_event(
            root,
            "invariants.invalidated",
            {"reason": reason},
            run_id=run_id,
            artifact_refs=artifacts,
        )


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(parent) for parent in graph.get(node, [])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _normalized_duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        normalized = _normalized_text(value)
        if normalized in seen:
            duplicates.add(value)
        seen.add(normalized)
    return sorted(duplicates)


def _graph_reachable(
    starts: set[str], targets: set[str], adjacency: dict[str, set[str]]
) -> bool:
    queue = list(starts)
    visited = set(starts)
    while queue:
        current = queue.pop(0)
        if current in targets:
            return True
        for neighbor in adjacency.get(current, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return False


def validate_invariant_output(root: Path, output: dict[str, Any]) -> None:
    validate_with_schema(root, AGENT_SCHEMA["invariant_mapper"], output)
    claims = [
        claim
        for claim in load_json(root / ".review" / "claims.json")["claims"]
        if claim["status"] != "REMOVED"
    ]
    claim_by_id = {claim["id"]: claim for claim in claims}
    known_claims = set(claim_by_id)
    reviewed = set(output["reviewed_claims"])
    if reviewed != known_claims:
        raise HarnessError(
            "Invariant mapper must cover every active Claim; "
            f"missing={sorted(known_claims - reviewed)}, unknown={sorted(reviewed - known_claims)}"
        )

    terminology = output["terminology_registry"]
    concepts = terminology["concepts"]
    concept_ids = [item["id"] for item in concepts]
    if len(concept_ids) != len(set(concept_ids)):
        raise HarnessError("Terminology registry contains duplicate concept IDs")
    duplicate_terms = _normalized_duplicates(item["canonical_term"] for item in concepts)
    term_owners: dict[str, set[str]] = {}
    for item in concepts:
        for term in [item["canonical_term"], *item["aliases"]]:
            term_owners.setdefault(_normalized_text(term), set()).add(item["id"])
    collisions = sorted(term for term, owners in term_owners.items() if len(owners) > 1)

    notation = output["notation_registry"]
    symbols = notation["symbols"]
    notation_ids = [item["id"] for item in symbols]
    if len(notation_ids) != len(set(notation_ids)):
        raise HarnessError("Notation registry contains duplicate notation IDs")
    duplicate_symbols = _normalized_duplicates(item["canonical_form"] for item in symbols)

    data_registry = output["data_registry"]
    data_records = data_registry["records"]
    data_ids = [item["id"] for item in data_records]
    if len(data_ids) != len(set(data_ids)):
        raise HarnessError("Data registry contains duplicate data IDs")
    unknown_data_claims = sorted(
        {
            claim_id
            for item in data_records
            for claim_id in item["claim_ids"]
            if claim_id not in known_claims
        }
    )
    if unknown_data_claims:
        raise HarnessError(f"Data registry references unknown Claims: {unknown_data_claims}")

    graph = output["argument_graph"]
    nodes = graph["nodes"]
    edges = graph["edges"]
    node_ids = [item["id"] for item in nodes]
    edge_ids = [item["id"] for item in edges]
    if len(node_ids) != len(set(node_ids)) or len(edge_ids) != len(set(edge_ids)):
        raise HarnessError("Argument graph contains duplicate node or edge IDs")
    known_nodes = set(node_ids)
    known_data = set(data_ids)
    unknown_endpoints = sorted(
        {
            endpoint
            for edge in edges
            for endpoint in [edge["source_id"], edge["target_id"]]
            if endpoint not in known_nodes
        }
    )
    if unknown_endpoints:
        raise HarnessError(f"Argument graph references unknown nodes: {unknown_endpoints}")
    unknown_node_claims = sorted(
        {
            item["claim_id"]
            for item in nodes
            if item["claim_id"] is not None and item["claim_id"] not in known_claims
        }
    )
    unknown_node_data = sorted(
        {data_id for item in nodes for data_id in item["data_ids"] if data_id not in known_data}
    )
    if unknown_node_claims or unknown_node_data:
        raise HarnessError(
            f"Argument graph has unknown Claim/Data references: claims={unknown_node_claims}, data={unknown_node_data}"
        )
    claim_nodes = {
        claim_id: {item["id"] for item in nodes if item["claim_id"] == claim_id}
        for claim_id in known_claims
    }
    missing_claim_nodes = sorted(claim_id for claim_id, ids in claim_nodes.items() if not ids)
    dependency_relations = {"DEPENDS_ON", "SUPPORTS", "DERIVES", "VALIDATES"}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in known_nodes}
    reverse_adjacency: dict[str, set[str]] = {node_id: set() for node_id in known_nodes}
    for edge in edges:
        if edge["relation"] in dependency_relations:
            adjacency[edge["source_id"]].add(edge["target_id"])
            reverse_adjacency[edge["target_id"]].add(edge["source_id"])
    argument_cycle = _has_cycle({key: sorted(value) for key, value in adjacency.items()})
    broken_dependencies = sorted(
        f"{dependency}->{claim['id']}"
        for claim in claims
        for dependency in claim["dependencies"]
        if dependency in claim_nodes
        and not _graph_reachable(claim_nodes[dependency], claim_nodes[claim["id"]], adjacency)
    )
    support_types = {"ASSUMPTION", "PREMISE", "DERIVATION", "DATA", "EVIDENCE", "REFERENCE"}
    orphan_core_claims: list[str] = []
    node_by_id = {item["id"]: item for item in nodes}
    for claim in claims:
        if claim["centrality"] != "CORE":
            continue
        starts = claim_nodes.get(claim["id"], set())
        queue = list(starts)
        visited = set(starts)
        supported = False
        while queue:
            current = queue.pop(0)
            for source in reverse_adjacency.get(current, set()):
                supported = supported or node_by_id[source]["type"] in support_types
                if source not in visited:
                    visited.add(source)
                    queue.append(source)
        if not supported:
            orphan_core_claims.append(claim["id"])

    consistency = output["claim_consistency"]
    records = consistency["records"]
    record_ids = [item["claim_id"] for item in records]
    record_mapping_invalid = (
        len(record_ids) != len(set(record_ids)) or set(record_ids) != known_claims
    )
    for record in records:
        claim = claim_by_id.get(record["claim_id"])
        if claim is None:
            continue
        if record["canonical_statement"] != claim["statement"]:
            raise HarnessError(
                f"Claim consistency statement differs from Claim Ledger for {claim['id']}"
            )
        if record["canonical_scope"] != claim["scope"]:
            raise HarnessError(
                f"Claim consistency scope differs from Claim Ledger for {claim['id']}"
            )
        unknown_support = sorted(set(record["support_node_ids"]) - known_nodes)
        if unknown_support:
            raise HarnessError(
                f"Claim consistency references unknown support nodes for {claim['id']}: {unknown_support}"
            )

    redundancy = output["redundancy_diagnostics"]
    unknown_redundancy_claims = sorted(
        {
            claim_id
            for item in redundancy["contribution_duplicates"]
            for claim_id in item["claim_ids"]
            if claim_id not in known_claims
        }
    )
    if unknown_redundancy_claims:
        raise HarnessError(
            f"Redundancy diagnostics reference unknown Claims: {unknown_redundancy_claims}"
        )

    semantic_conflict = bool(
        duplicate_terms
        or collisions
        or duplicate_symbols
        or terminology["unregistered_critical_terms"]
        or notation["unregistered_critical_symbols"]
        or data_registry["unmapped_material_values"]
        or consistency["unmapped_claim_occurrences"]
        or missing_claim_nodes
        or argument_cycle
        or broken_dependencies
        or orphan_core_claims
        or record_mapping_invalid
        or any(item["status"] != "CLEAR" for item in concepts)
        or any(
            occurrence["usage"] == "CANONICAL"
            and _normalized_text(occurrence["surface_form"])
            != _normalized_text(item["canonical_term"])
            or occurrence["usage"] == "ALIAS"
            and _normalized_text(occurrence["surface_form"])
            not in {_normalized_text(alias) for alias in item["aliases"]}
            or occurrence["usage"] not in {"CANONICAL", "ALIAS"}
            or occurrence["meaning_alignment"] != "MATCH"
            for item in concepts
            for occurrence in item["occurrences"]
        )
        or any(
            {_normalized_text(item["canonical_term"]), *(_normalized_text(x) for x in item["aliases"])}
            & {_normalized_text(x) for x in item["forbidden_variants"]}
            for item in concepts
        )
        or any(
            _normalized_text(occurrence["surface_form"])
            in {_normalized_text(x) for x in item["forbidden_variants"]}
            for item in concepts
            for occurrence in item["occurrences"]
        )
        or any(item["status"] != "CLEAR" for item in symbols)
        or any(
            occurrence["alignment"] != "MATCH"
            or occurrence["meaning"] != item["meaning"]
            or occurrence["domain_or_type"] != item["domain_or_type"]
            for item in symbols
            for occurrence in item["occurrences"]
        )
        or any(item["status"] != "CLEAR" for item in data_records)
        or any(
            occurrence["relation"] in {"CONFLICT", "NEEDS_AUTHOR"}
            or (
                occurrence["relation"] == "MATCH"
                and (
                    occurrence["value"] != item["canonical_value"]
                    or occurrence["unit"] != item["unit"]
                    or occurrence["conditions"] != item["conditions"]
                )
            )
            or (
                occurrence["relation"] == "AUTHORIZED_VARIATION"
                and not (
                    occurrence["justification"]
                    and occurrence["justification"].strip()
                )
            )
            for item in data_records
            for occurrence in item["occurrences"]
        )
        or any(item["status"] != "PASS" for item in records)
        or any(
            claim_by_id.get(item["claim_id"], {}).get("centrality") == "CORE"
            and (not item["support_node_ids"] or not item["body_locations"])
            for item in records
        )
        or any(
            not any(
                occurrence["role"] in {"BODY", "RESULT", "DISCUSSION"}
                for occurrence in item["occurrences"]
            )
            for item in records
        )
        or any(
            not occurrence["synchronized"]
            or occurrence["scope_relation"]
            in {"BROADER", "CONTRADICTORY", "UNMAPPED"}
            for item in records
            for occurrence in item["occurrences"]
        )
        or any(
            item["classification"] != "INTENTIONAL" and item["severity"] == "MAJOR"
            for item in redundancy["exact_duplicates"]
        )
        or any(
            item["classification"] == "INTENTIONAL"
            and not (item["justification"] and item["justification"].strip())
            for item in redundancy["exact_duplicates"]
        )
        or any(not item["distinct"] for item in redundancy["contribution_duplicates"])
    )
    if semantic_conflict and not output["issues"]:
        raise HarnessError(
            "Invariant mapper reported unresolved facts without a structured Issue"
        )


def merge_invariant_output(root: Path, output: dict[str, Any], run_id: str) -> dict[str, Any]:
    validate_invariant_output(root, output)
    merge_review_output(
        root,
        {
            "agent": "invariant_mapper",
            "reviewed_claims": output["reviewed_claims"],
            "issues": output["issues"],
            "review_notes": output["review_notes"],
        },
    )
    snapshot = manuscript_snapshot(root)
    timestamp = utc_now()
    artifacts: list[dict[str, str]] = []
    result: dict[str, Any] = {}
    for output_key, (file_name, schema_name) in INVARIANT_LEDGER.items():
        ledger = {
            "schema_version": 1,
            "status": "CURRENT",
            "run_id": run_id,
            "updated_at": timestamp,
            "source_snapshot": snapshot,
            "payload": output[output_key],
        }
        validate_with_schema(root, schema_name, ledger)
        path = root / ".review" / file_name
        write_json(path, ledger)
        artifacts.append({"path": str(path.relative_to(root))})
        result[output_key] = ledger
    append_event(
        root,
        "invariants.solidified",
        {"run_id": run_id, "source_sha256": snapshot["sha256"]},
        run_id=run_id,
        actor="invariant_mapper",
        artifact_refs=artifacts,
    )
    return result


def validate_layer_invariants(root: Path, agent: str, output: dict[str, Any]) -> None:
    known_claims = {
        item["id"]
        for item in load_json(root / ".review" / "claims.json")["claims"]
        if item["status"] != "REMOVED"
    }
    unknown_claims = sorted(set(output["reviewed_claims"]) - known_claims)
    if unknown_claims:
        raise HarnessError(f"{agent} references unknown Claims: {unknown_claims}")
    if agent in {"macro_architect", "final_integrity_auditor"} and set(
        output["reviewed_claims"]
    ) != known_claims:
        missing_claims = sorted(known_claims - set(output["reviewed_claims"]))
        raise HarnessError(f"{agent} did not cover every mapped Claim: {missing_claims}")
    if agent == "macro_architect":
        contract = output["contract"]
        terminology = load_json(root / ".review" / "terminology.json")
        notation = load_json(root / ".review" / "notation.json")
        if terminology["status"] != "CURRENT" or notation["status"] != "CURRENT":
            raise HarnessError("Macro contract requires CURRENT terminology and notation registries")
        expected_terms = {
            item["id"] for item in terminology["payload"]["concepts"]
        }
        actual_terms = {item["concept_id"] for item in contract["terminology"]}
        expected_notation = {
            item["id"] for item in notation["payload"]["symbols"]
        }
        actual_notation = {item["notation_id"] for item in contract["notation"]}
        if actual_terms != expected_terms or actual_notation != expected_notation:
            raise HarnessError(
                "Macro terminology/notation contract must exactly cover the frozen registries"
            )
        concepts_by_id = {
            item["id"]: item for item in terminology["payload"]["concepts"]
        }
        for item in contract["terminology"]:
            source = concepts_by_id[item["concept_id"]]
            if (
                item["canonical"] != source["canonical_term"]
                or item["meaning"] != source["meaning"]
                or item["allowed_variants"] != source["aliases"]
                or item["forbidden_variants"] != source["forbidden_variants"]
                or item["first_definition"] != source["definition_locations"][0]
            ):
                raise HarnessError(
                    f"Macro terminology entry {item['concept_id']} drifts from its registry"
                )
        notation_by_id = {
            item["id"]: item for item in notation["payload"]["symbols"]
        }
        for item in contract["notation"]:
            source = notation_by_id[item["notation_id"]]
            if (
                item["symbol"] != source["symbol"]
                or item["canonical_form"] != source["canonical_form"]
                or item["meaning"] != source["meaning"]
                or item["domain_or_type"] != source["domain_or_type"]
                or item["first_definition"] != source["definition_locations"][0]
            ):
                raise HarnessError(
                    f"Macro notation entry {item['notation_id']} drifts from its registry"
                )
        logic = contract["logic_chain"]
        identifiers = [item["id"] for item in logic]
        if len(identifiers) != len(set(identifiers)):
            raise HarnessError("Macro logic chain contains duplicate IDs")
        known = set(identifiers)
        unknown = sorted(
            {dependency for item in logic for dependency in item["depends_on"] if dependency not in known}
        )
        if unknown:
            raise HarnessError(f"Macro logic chain references unknown nodes: {unknown}")
        if _has_cycle({item["id"]: item["depends_on"] for item in logic}):
            raise HarnessError("Macro logic chain contains a dependency cycle")
        unknown_logic_claims = sorted(
            {
                claim_id
                for item in logic
                for claim_id in item["claim_ids"]
                if claim_id not in known_claims
            }
        )
        if unknown_logic_claims:
            raise HarnessError(
                f"Macro logic chain references unknown Claims: {unknown_logic_claims}"
            )
        unresolved = (
            [
                dimension
                for dimension in ["title", "abstract", "conclusion"]
                if contract[dimension]["status"] != "PASS"
            ]
            + [item["id"] for item in logic if item["status"] != "SUPPORTED"]
            + [item["symbol"] for item in contract["notation"] if item["conflicts"]]
        )
        if unresolved and not output["issues"]:
            raise HarnessError(
                "Macro contract has unresolved dimensions but provides no structured Issues"
            )
    elif agent == "hierarchy_reviewer":
        nodes = output["nodes"]
        identifiers = [item["id"] for item in nodes]
        if len(identifiers) != len(set(identifiers)):
            raise HarnessError("Hierarchy contains duplicate node IDs")
        known = set(identifiers)
        unknown_parents = sorted(
            {
                item["parent_id"]
                for item in nodes
                if item["parent_id"] is not None and item["parent_id"] not in known
            }
        )
        if unknown_parents:
            raise HarnessError(f"Hierarchy references unknown parents: {unknown_parents}")
        graph = {
            item["id"]: [item["parent_id"]] if item["parent_id"] is not None else []
            for item in nodes
        }
        if _has_cycle(graph):
            raise HarnessError("Hierarchy contains a parent cycle")
        if output["granularity"] == "SECTION" and any(
            item["level"] != "SECTION" for item in nodes
        ):
            raise HarnessError("SECTION granularity cannot contain SUBSECTION nodes")
        contract = load_json(root / ".review" / "global_contract.json")
        if contract["status"] != "CURRENT":
            raise HarnessError("Hierarchy cannot be solidified without a CURRENT global contract")
        anchors = set(contract["payload"]["contract"]["theme"]["anchors"])
        unknown_anchors = sorted(
            {anchor for item in nodes for anchor in item["theme_anchors"] if anchor not in anchors}
        )
        if unknown_anchors:
            raise HarnessError(f"Hierarchy introduces unknown theme anchors: {unknown_anchors}")
        node_claims = {claim for item in nodes for claim in item["claim_ids"]}
        unknown_node_claims = sorted(node_claims - known_claims)
        if unknown_node_claims:
            raise HarnessError(
                f"Hierarchy nodes reference unknown Claims: {unknown_node_claims}"
            )
    elif agent == "language_coherence_reviewer":
        structure = load_json(root / ".review" / "structure.json")
        structure_ready = structure["status"] == "CURRENT" or (
            output["granularity"] == "MACRO_ONLY"
            and structure["status"] == "NOT_APPLICABLE"
        )
        if not structure_ready:
            raise HarnessError("Language review requires a valid hierarchy state")
        expected_granularity = (
            structure["payload"]["granularity"]
            if structure["status"] == "CURRENT"
            else "MACRO_ONLY"
        )
        if output["granularity"] != expected_granularity:
            raise HarnessError(
                "Language review granularity does not match the solidified hierarchy"
            )
        known_nodes = (
            {item["id"] for item in structure["payload"]["nodes"]}
            if structure["status"] == "CURRENT"
            else set()
        )
        unknown_nodes = sorted(
            {
                item["parent_node_id"]
                for item in output["units"]
                if item["parent_node_id"] is not None
                and item["parent_node_id"] not in known_nodes
            }
        )
        if unknown_nodes:
            raise HarnessError(f"Language units reference unknown hierarchy nodes: {unknown_nodes}")
        bad_parents = [
            item["id"]
            for item in output["units"]
            if (item["parent_node_id"] is None) == (item["parent_contract_path"] is None)
        ]
        if bad_parents:
            raise HarnessError(
                f"Language units must reference exactly one parent constraint: {bad_parents}"
            )
        allowed_levels = {
            "MACRO_ONLY": {"HEADING", "PARAGRAPH"},
            "SECTION": {"HEADING"},
            "SUBSECTION": {"HEADING"},
            "PARAGRAPH": {"HEADING", "PARAGRAPH"},
            "SENTENCE": {"HEADING", "PARAGRAPH", "SENTENCE"},
            "ADAPTIVE": {"HEADING", "PARAGRAPH", "SENTENCE"},
        }[output["granularity"]]
        invalid_levels = sorted(
            {item["level"] for item in output["units"] if item["level"] not in allowed_levels}
        )
        if invalid_levels:
            raise HarnessError(
                f"Language output exceeds selected granularity: {invalid_levels}"
            )
    elif agent == "final_integrity_auditor":
        required = {
            "TITLE",
            "ABSTRACT",
            "SECTION_NAMES",
            "CONCLUSION",
            "LOGIC_CHAIN",
            "TERMINOLOGY",
            "NOTATION",
            "ARGUMENT_GRAPH",
            "DATA_CONSISTENCY",
            "CLAIM_CONSISTENCY",
            "REDUNDANCY",
            "HIERARCHY",
            "LANGUAGE",
            "CLAIM_SCOPE",
        }
        dimensions = [item["dimension"] for item in output["checks"]]
        missing = sorted(required - set(dimensions))
        duplicates = sorted(
            dimension for dimension in set(dimensions) if dimensions.count(dimension) > 1
        )
        if missing or duplicates:
            raise HarnessError(
                f"Final audit dimensions invalid; missing={missing}, duplicates={duplicates}"
            )
        if output["result"] == "PASS" and (
            output["issues"]
            or any(item["status"] != "PASS" for item in output["checks"])
            or any(not item["evidence"] for item in output["checks"])
        ):
            raise HarnessError(
                "Final audit PASS requires all checks PASS, exact evidence, and no Issues"
            )
        if output["result"] != "PASS" and not output["issues"]:
            raise HarnessError("A non-PASS final audit must report at least one structured Issue")


def merge_layer_output(
    root: Path, agent: str, output: dict[str, Any], run_id: str
) -> dict[str, Any]:
    if agent not in LAYER_LEDGER:
        raise HarnessError(f"Unknown structured layer agent: {agent}")
    validate_with_schema(root, AGENT_SCHEMA[agent], output)
    if output.get("agent") != agent:
        raise HarnessError(f"Layer output declares {output.get('agent')}, expected {agent}")
    validate_layer_invariants(root, agent, output)
    merge_review_output(
        root,
        {
            "agent": agent,
            "reviewed_claims": output["reviewed_claims"],
            "issues": output["issues"],
            "review_notes": output["review_notes"],
        },
    )
    file_name, schema_name = LAYER_LEDGER[agent]
    status = output["result"] if agent == "final_integrity_auditor" else "CURRENT"
    ledger = {
        "schema_version": 1,
        "status": status,
        "run_id": run_id,
        "updated_at": utc_now(),
        "payload": output,
    }
    validate_with_schema(root, schema_name, ledger)
    ledger_path = root / ".review" / file_name
    write_json(ledger_path, ledger)
    append_event(
        root,
        "layer.solidified",
        {"agent": agent, "status": status, "run_id": run_id},
        run_id=run_id,
        actor=agent,
        artifact_refs=[{"path": str(ledger_path.relative_to(root))}],
    )
    return ledger


def mark_granular_not_applicable(root: Path, reason: str) -> dict[str, Any]:
    ledger = {
        "schema_version": 1,
        "status": "NOT_APPLICABLE",
        "run_id": None,
        "updated_at": utc_now(),
        "payload": None,
    }
    validate_with_schema(root, "granular-review.schema.json", ledger)
    write_json(root / ".review" / "granular_review.json", ledger)
    append_event(root, "layer.skipped", {"layer": "GRANULAR_LANGUAGE", "reason": reason})
    return ledger


def mark_hierarchy_not_applicable(root: Path, reason: str) -> dict[str, Any]:
    ledger = {
        "schema_version": 1,
        "status": "NOT_APPLICABLE",
        "run_id": None,
        "updated_at": utc_now(),
        "payload": None,
    }
    validate_with_schema(root, "structure.schema.json", ledger)
    write_json(root / ".review" / "structure.json", ledger)
    append_event(root, "layer.skipped", {"layer": "HIERARCHY", "reason": reason})
    return ledger


def record_revisions(root: Path, output: dict[str, Any], run_id: str) -> dict[str, Any]:
    validate_with_schema(root, "revision-output.schema.json", output)
    issues_path = root / ".review" / "issues.json"
    issues_ledger = load_json(issues_path)
    issues_by_id = {issue["id"]: issue for issue in issues_ledger["issues"]}
    revisions_path = root / ".review" / "revisions.json"
    revisions_ledger = load_json(revisions_path)
    revision_ids = {revision["id"] for revision in revisions_ledger["revisions"]}
    for proposed in output["revisions"]:
        issue = issues_by_id.get(proposed["issue_id"])
        if issue is None:
            raise HarnessError(f"Reviser referenced unknown Issue {proposed['issue_id']}")
        if issue["status"] not in {"OPEN", "NEEDS_AUTHOR"}:
            raise HarnessError(
                f"Reviser cannot change Issue {issue['id']} from status {issue['status']}"
            )
        status = proposed["revision_status"]
        if status == "CLAIMED_FIXED" and not proposed["changed_files"]:
            raise HarnessError(
                f"CLAIMED_FIXED revision for {issue['id']} must list at least one changed file"
            )
        issue["status"] = status
        issue["verification_status"] = "NOT_RUN"
        issue["updated_at"] = utc_now()
        record = dict(proposed)
        record.update(
            {
                "id": next_prefixed_id(revision_ids, "R", marker=""),
                "created_at": utc_now(),
                "run_id": run_id,
            }
        )
        revision_ids.add(record["id"])
        revisions_ledger["revisions"].append(record)
    validate_with_schema(root, "issues.schema.json", issues_ledger)
    validate_with_schema(root, "revisions.schema.json", revisions_ledger)
    write_json(issues_path, issues_ledger)
    write_json(revisions_path, revisions_ledger)
    if output["revisions"]:
        invalidate_invariant_ledgers(
            root, "manuscript revision requires invariant remapping", run_id
        )
    return revisions_ledger


def record_verifications(root: Path, output: dict[str, Any], run_id: str) -> dict[str, Any]:
    validate_with_schema(root, "verification-output.schema.json", output)
    issues_path = root / ".review" / "issues.json"
    issues_ledger = load_json(issues_path)
    issues_by_id = {issue["id"]: issue for issue in issues_ledger["issues"]}
    existing_ids = set(issues_by_id)
    verifications_path = root / ".review" / "verifications.json"
    ledger = load_json(verifications_path)
    verification_ids = {item["id"] for item in ledger["verifications"]}
    for proposed in output["verifications"]:
        issue = issues_by_id.get(proposed["issue_id"])
        if issue is None:
            raise HarnessError(f"Verifier referenced unknown Issue {proposed['issue_id']}")
        if issue["status"] != "CLAIMED_FIXED":
            raise HarnessError(
                f"Verifier may only verify CLAIMED_FIXED Issues; {issue['id']} is {issue['status']}"
            )
        result = proposed["result"]
        if result == "NEW_PROBLEM" and not proposed["new_issues"]:
            raise HarnessError("NEW_PROBLEM verification must include at least one new Issue")
        if result != "NEW_PROBLEM" and proposed["new_issues"]:
            raise HarnessError("new_issues are allowed only when verification result is NEW_PROBLEM")
        issue["verification_status"] = result
        issue["status"] = "RESOLVED" if result == "PASS" else "OPEN"
        issue["updated_at"] = utc_now()
        record = {
            "id": next_prefixed_id(verification_ids, "V", marker=""),
            "issue_id": issue["id"],
            "result": result,
            "rationale": proposed["rationale"],
            "evidence_locations": proposed["evidence_locations"],
            "created_at": utc_now(),
            "run_id": run_id,
        }
        verification_ids.add(record["id"])
        ledger["verifications"].append(record)
        for new_issue in proposed["new_issues"]:
            canonical = _canonical_issue(new_issue, "verifier", existing_ids)
            issues_ledger["issues"].append(canonical)
            issues_by_id[canonical["id"]] = canonical
    issues_ledger["issues"].sort(key=lambda item: item["id"])
    validate_with_schema(root, "issues.schema.json", issues_ledger)
    validate_with_schema(root, "verifications.schema.json", ledger)
    write_json(issues_path, issues_ledger)
    write_json(verifications_path, ledger)
    return ledger


def load_agent_profile(root: Path, agent: str) -> dict[str, Any]:
    path = root / ".codex" / "agents" / f"{agent}.toml"
    if not path.is_file():
        raise HarnessError(f"Custom agent profile is missing: {path}")
    with path.open("rb") as handle:
        profile = tomllib.load(handle)
    if profile.get("name") != agent:
        raise HarnessError(f"Agent profile {path} has mismatched name")
    return profile


class CodexRunner:
    def __init__(self, root: Path, config: dict[str, Any]):
        self.root = root
        self.config = config

    def _run_manifest(self, run_id: str) -> Path:
        run_dir = self.root / ".review" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "run.json"
        if not path.exists():
            write_json(
                path,
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "created_at": utc_now(),
                    "status": "RUNNING",
                    "artifact_layout": "agents/<invocation_id>/{input,output,events,stderr,invocation}",
                },
            )
        return path

    def _audit_context(self) -> dict[str, str | None]:
        context: dict[str, str | None] = {
            "session_id": None,
            "turn_id": None,
            "request_id": None,
        }
        try:
            request_id = load_state(self.root).get("active_request_id")
            context["request_id"] = request_id
            if request_id:
                request = load_json(
                    self.root / ".review" / "requests" / f"{request_id}.json"
                )
                context["session_id"] = request.get("source", {}).get("session_id")
                context["turn_id"] = request.get("source", {}).get("turn_id")
        except HarnessError:
            pass
        return context

    def _finalize_run(self, run_id: str) -> dict[str, Any]:
        path = self._run_manifest(run_id)
        run_dir = path.parent
        invocations = []
        for candidate in sorted((run_dir / "agents").glob("*/invocation.json")):
            invocations.append(load_json(candidate))
        failed = any(item.get("status") != "COMPLETED" for item in invocations)
        manifest = load_json(path)
        manifest.update(
            {
                "completed_at": utc_now(),
                "status": "FAILED" if failed else "COMPLETED",
                "invocations": [
                    {
                        "invocation_id": item["invocation_id"],
                        "agent": item["agent"],
                        "status": item["status"],
                        "exit_code": item["exit_code"],
                    }
                    for item in invocations
                ],
            }
        )
        write_json(path, manifest)
        append_event(
            self.root,
            "run.completed" if not failed else "run.failed",
            manifest,
            run_id=run_id,
            **self._audit_context(),
            artifact_refs=[{"path": str(path.relative_to(self.root))}],
        )
        return manifest

    @staticmethod
    def _event_metadata(text: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {"event_count": 0, "thread_id": None, "usage": None}
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            metadata["event_count"] += 1
            metadata["thread_id"] = event.get("thread_id", metadata["thread_id"])
            if event.get("usage") is not None:
                metadata["usage"] = event["usage"]
        return metadata

    def run(
        self,
        agent: str,
        assignment: dict[str, Any],
        run_id: str,
        invocation_id: str | None = None,
        finalize_run: bool = True,
        audit_context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        profile = load_agent_profile(self.root, agent)
        schema = schema_path(self.root, AGENT_SCHEMA[agent])
        self._run_manifest(run_id)
        invocation_id = invocation_id or agent
        context = audit_context or self._audit_context()
        invocation_dir = self.root / ".review" / "runs" / run_id / "agents" / invocation_id
        invocation_dir.mkdir(parents=True, exist_ok=True)
        output_path = invocation_dir / "output.json"
        event_path = invocation_dir / "events.jsonl"
        stderr_path = invocation_dir / "stderr.log"
        input_path = invocation_dir / "input.json"
        invocation_path = invocation_dir / "invocation.json"
        effective_schema_path = invocation_dir / "effective-schema.json"
        write_json(effective_schema_path, bundled_schema(self.root, AGENT_SCHEMA[agent]))
        prompt = (
            f"You are executing the `{agent}` role for the Paper Review Harness.\n"
            "Follow the repository AGENTS.md and paper-review skill. The role contract is supplied "
            "as developer instructions. Complete only this assignment and return the schema-shaped JSON.\n\n"
            "ASSIGNMENT JSON:\n"
            + json.dumps(assignment, ensure_ascii=False, indent=2)
        )
        instructions = str(profile.get("developer_instructions", ""))
        configured_command = self.config.get("codex_command", "codex")
        if isinstance(configured_command, str):
            command_prefix = [configured_command]
        elif isinstance(configured_command, list) and configured_command and all(
            isinstance(item, str) for item in configured_command
        ):
            command_prefix = configured_command
        else:
            raise HarnessError("codex_command must be a command string or a non-empty argument array")
        args = command_prefix + [
            "exec",
            "--ephemeral",
            "--color",
            "never",
            "--json",
            "--cd",
            str(self.root),
            "--sandbox",
            str(profile.get("sandbox_mode", "read-only")),
            "--output-schema",
            str(effective_schema_path),
            "--output-last-message",
            str(output_path),
            "-c",
            "developer_instructions=" + json.dumps(instructions),
            "-",
        ]
        started_at = utc_now()
        structured_input = {
            "schema_version": 1,
            "run_id": run_id,
            "invocation_id": invocation_id,
            "agent": agent,
            "started_at": started_at,
            "assignment": redact(assignment),
            "assignment_sha256": sha256_value(assignment),
            "profile": str((self.root / ".codex" / "agents" / f"{agent}.toml").relative_to(self.root)),
            "profile_sha256": sha256_file(self.root / ".codex" / "agents" / f"{agent}.toml"),
            "schema": str(schema.relative_to(self.root)),
            "schema_sha256": sha256_file(schema),
            "effective_schema": str(effective_schema_path.relative_to(self.root)),
            "effective_schema_sha256": sha256_file(effective_schema_path),
            "command": redact(args),
            "provenance": context,
        }
        write_json(input_path, structured_input)
        append_event(
            self.root,
            "agent.started",
            structured_input,
            run_id=run_id,
            **context,
            actor=agent,
            artifact_refs=[{"path": str(input_path.relative_to(self.root))}],
        )
        environment = os.environ.copy()
        environment["PAPER_REVIEW_CHILD"] = "1"

        def persist_invocation(
            *,
            status: str,
            exit_code: int | None,
            event_text: str,
            stderr_text: str,
            error: str | None = None,
        ) -> dict[str, Any]:
            event_path.write_text(event_text, encoding="utf-8")
            stderr_path.write_text(stderr_text, encoding="utf-8")
            invocation = {
                "schema_version": 1,
                "run_id": run_id,
                "invocation_id": invocation_id,
                "agent": agent,
                "started_at": started_at,
                "completed_at": utc_now(),
                "status": status,
                "exit_code": exit_code,
                **self._event_metadata(event_text),
                "error": error,
                "artifacts": {
                    "input": str(input_path.relative_to(self.root)),
                    "output": str(output_path.relative_to(self.root)),
                    "events": str(event_path.relative_to(self.root)),
                    "stderr": str(stderr_path.relative_to(self.root)),
                    "effective_schema": str(effective_schema_path.relative_to(self.root)),
                },
            }
            write_json(invocation_path, invocation)
            return invocation

        try:
            completed = subprocess.run(
                args,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self.root,
                env=environment,
                capture_output=True,
                timeout=int(self.config.get("codex_timeout_seconds", 1800)),
                check=False,
            )
        except FileNotFoundError as exc:
            message = f"Codex executable not found: {command_prefix[0]}"
            invocation = persist_invocation(
                status="FAILED", exit_code=None, event_text="", stderr_text=message, error=message
            )
            append_event(
                self.root,
                "agent.failed",
                invocation,
                run_id=run_id,
                **context,
                actor=agent,
                artifact_refs=[{"path": str(invocation_path.relative_to(self.root))}],
            )
            if finalize_run:
                self._finalize_run(run_id)
            raise HarnessError(message) from exc
        except subprocess.TimeoutExpired as exc:
            message = f"{agent} timed out"
            event_text = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr_text = exc.stderr if isinstance(exc.stderr, str) else message
            invocation = persist_invocation(
                status="FAILED",
                exit_code=None,
                event_text=event_text,
                stderr_text=stderr_text or message,
                error=message,
            )
            append_event(
                self.root,
                "agent.failed",
                invocation,
                run_id=run_id,
                **context,
                actor=agent,
                artifact_refs=[{"path": str(invocation_path.relative_to(self.root))}],
            )
            if finalize_run:
                self._finalize_run(run_id)
            raise HarnessError(message) from exc
        invocation = persist_invocation(
            status="COMPLETED" if completed.returncode == 0 else "FAILED",
            exit_code=completed.returncode,
            event_text=completed.stdout,
            stderr_text=completed.stderr or "",
            error=None if completed.returncode == 0 else f"exit code {completed.returncode}",
        )
        if completed.returncode != 0:
            append_event(
                self.root,
                "agent.failed",
                invocation,
                run_id=run_id,
                **context,
                actor=agent,
                artifact_refs=[{"path": str(invocation_path.relative_to(self.root))}],
            )
            if finalize_run:
                self._finalize_run(run_id)
            raise HarnessError(
                f"{agent} failed with exit code {completed.returncode}; see {event_path}"
            )
        try:
            output = load_json(output_path)
            validate_with_schema(self.root, AGENT_SCHEMA[agent], output)
        except HarnessError as exc:
            invocation["status"] = "FAILED"
            invocation["error"] = str(exc)
            write_json(invocation_path, invocation)
            append_event(
                self.root,
                "agent.output_rejected",
                invocation,
                run_id=run_id,
                **context,
                actor=agent,
                artifact_refs=[
                    {"path": str(invocation_path.relative_to(self.root))},
                    {"path": str(output_path.relative_to(self.root))},
                ],
            )
            if finalize_run:
                self._finalize_run(run_id)
            raise
        append_event(
            self.root,
            "agent.completed",
            invocation,
            run_id=run_id,
            **context,
            actor=agent,
            artifact_refs=[
                {"path": str(invocation_path.relative_to(self.root))},
                {"path": str(output_path.relative_to(self.root))},
                {"path": str(event_path.relative_to(self.root))},
                {"path": str(stderr_path.relative_to(self.root))},
            ],
        )
        if finalize_run:
            self._finalize_run(run_id)
        return output

    def run_parallel(
        self, assignments: dict[str, dict[str, Any]], run_id: str
    ) -> dict[str, dict[str, Any]]:
        if not assignments:
            return {}
        workers = min(
            len(assignments), max(1, int(self.config.get("max_parallel_reviewers", 4)))
        )
        results: dict[str, dict[str, Any]] = {}
        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        self.run,
                        agent,
                        assignment,
                        run_id,
                        f"{index:02d}-{agent}",
                        False,
                    ): agent
                    for index, (agent, assignment) in enumerate(
                        sorted(assignments.items()), start=1
                    )
                }
                for future in as_completed(futures):
                    agent = futures[future]
                    results[agent] = future.result()
        finally:
            self._finalize_run(run_id)
        return results


def make_run_id(label: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{label}"


def status_summary(root: Path) -> dict[str, Any]:
    state = load_state(root)
    claims = load_json(root / ".review" / "claims.json")["claims"]
    issues = load_json(root / ".review" / "issues.json")["issues"]
    counts: dict[str, int] = {}
    for issue in issues:
        key = f"{issue['severity']}:{issue['status']}"
        counts[key] = counts.get(key, 0) + 1
    request_id = state.get("active_request_id") or state.get("last_request_id")
    granularity = None
    if request_id:
        request_path = root / ".review" / "requests" / f"{request_id}.json"
        if request_path.is_file():
            granularity = load_json(request_path).get("granularity", {}).get("level")
    return {
        "phase": state["phase"],
        "round": state["round"],
        "active": state["active"],
        "claims": sum(1 for claim in claims if claim["status"] != "REMOVED"),
        "core_claims": sum(
            1
            for claim in claims
            if claim["status"] != "REMOVED" and claim["centrality"] == "CORE"
        ),
        "issues": len(issues),
        "issue_counts": dict(sorted(counts.items())),
        "last_validation_passed": state.get("last_validation_passed", False),
        "blocked_reason": state.get("blocked_reason"),
        "active_request_id": state.get("active_request_id"),
        "last_request_id": state.get("last_request_id"),
        "granularity": granularity,
        "layers": {
            "terminology_registry": load_json(root / ".review" / "terminology.json")["status"],
            "notation_registry": load_json(root / ".review" / "notation.json")["status"],
            "data_consistency": load_json(root / ".review" / "data_consistency.json")["status"],
            "argument_graph": load_json(root / ".review" / "argument_graph.json")["status"],
            "claim_consistency": load_json(root / ".review" / "claim_consistency.json")["status"],
            "redundancy_diagnostics": load_json(root / ".review" / "redundancy.json")["status"],
            "macro_contract": load_json(root / ".review" / "global_contract.json")["status"],
            "hierarchy": load_json(root / ".review" / "structure.json")["status"],
            "granular_language": load_json(root / ".review" / "granular_review.json")["status"],
            "final_audit": load_json(root / ".review" / "final_audit.json")["status"],
        },
    }
