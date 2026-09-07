from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from paper_review_lib import (
    HarnessError,
    find_root,
    load_config,
    load_json,
    load_state,
    make_run_id,
    manuscript_snapshot,
    resolve_repo_path,
    route_claim,
    validate_with_schema,
    utc_now,
    write_json,
)
from provenance import append_event, redact, sha256_value


@dataclass
class Check:
    id: str
    name: str
    status: str
    detail: str

    @property
    def passed(self) -> bool:
        return self.status in {"PASS", "SKIP"}


def _check(identifier: str, name: str, passed: bool, detail: str) -> Check:
    return Check(identifier, name, "PASS" if passed else "FAIL", detail)


def _schema_checks(root: Path) -> list[Check]:
    targets = [
        ("claims.json", "claims.schema.json"),
        ("issues.json", "issues.schema.json"),
        ("revisions.json", "revisions.schema.json"),
        ("verifications.json", "verifications.schema.json"),
        ("global_contract.json", "global-contract.schema.json"),
        ("structure.json", "structure.schema.json"),
        ("granular_review.json", "granular-review.schema.json"),
        ("final_audit.json", "final-audit.schema.json"),
        ("terminology.json", "terminology-registry.schema.json"),
        ("notation.json", "notation-registry.schema.json"),
        ("data_consistency.json", "data-registry.schema.json"),
        ("argument_graph.json", "argument-graph.schema.json"),
        ("claim_consistency.json", "claim-consistency.schema.json"),
        ("redundancy.json", "redundancy-diagnostics.schema.json"),
    ]
    checks: list[Check] = []
    for file_name, schema_name in targets:
        try:
            validate_with_schema(root, schema_name, load_json(root / ".review" / file_name))
            checks.append(_check("S01", f"Schema: {file_name}", True, "valid"))
        except HarnessError as exc:
            checks.append(_check("S01", f"Schema: {file_name}", False, str(exc)))
    return checks


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicate: set[str] = set()
    for value in values:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return sorted(duplicate)


def _find_dependency_cycle(claims: list[dict[str, Any]]) -> list[str] | None:
    graph = {claim["id"]: claim.get("dependencies", []) for claim in claims}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> list[str] | None:
        if node in visiting:
            index = trail.index(node)
            return trail[index:] + [node]
        if node in visited:
            return None
        visiting.add(node)
        for dependency in graph.get(node, []):
            cycle = visit(dependency, trail + [dependency])
            if cycle:
                return cycle
        visiting.remove(node)
        visited.add(node)
        return None

    for node in graph:
        cycle = visit(node, [node])
        if cycle:
            return cycle
    return None


def _integrity_checks(root: Path) -> list[Check]:
    claims = [
        claim
        for claim in load_json(root / ".review" / "claims.json")["claims"]
        if claim["status"] != "REMOVED"
    ]
    issues = load_json(root / ".review" / "issues.json")["issues"]
    revisions = load_json(root / ".review" / "revisions.json")["revisions"]
    verifications = load_json(root / ".review" / "verifications.json")["verifications"]
    claim_ids = [claim["id"] for claim in claims]
    issue_ids = [issue["id"] for issue in issues]
    known_claims = set(claim_ids)
    known_issues = set(issue_ids)
    checks = [
        _check("S02", "Unique Claim IDs", not _duplicates(claim_ids), str(_duplicates(claim_ids)) or "unique"),
        _check("S03", "Unique Issue IDs", not _duplicates(issue_ids), str(_duplicates(issue_ids)) or "unique"),
    ]
    unknown_dependencies = sorted(
        {
            dependency
            for claim in claims
            for dependency in claim.get("dependencies", [])
            if dependency not in known_claims
        }
    )
    checks.append(
        _check(
            "S04",
            "Claim dependency references",
            not unknown_dependencies,
            str(unknown_dependencies) if unknown_dependencies else "all dependencies exist",
        )
    )
    cycle = _find_dependency_cycle(claims) if not unknown_dependencies else None
    checks.append(
        _check("S05", "Claim dependency graph", cycle is None, " -> ".join(cycle) if cycle else "acyclic")
    )
    bad_issue_claims = sorted(
        issue["id"]
        for issue in issues
        if issue.get("claim_id") is not None and issue["claim_id"] not in known_claims
    )
    checks.append(
        _check(
            "S06",
            "Issue Claim references",
            not bad_issue_claims,
            str(bad_issue_claims) if bad_issue_claims else "all Claim references exist",
        )
    )
    bad_revision_issues = sorted(
        revision["id"] for revision in revisions if revision["issue_id"] not in known_issues
    )
    bad_verification_issues = sorted(
        verification["id"]
        for verification in verifications
        if verification["issue_id"] not in known_issues
    )
    checks.append(
        _check(
            "S07",
            "Revision Issue references",
            not bad_revision_issues,
            str(bad_revision_issues) if bad_revision_issues else "all Issue references exist",
        )
    )
    checks.append(
        _check(
            "S08",
            "Verification Issue references",
            not bad_verification_issues,
            str(bad_verification_issues) if bad_verification_issues else "all Issue references exist",
        )
    )
    invalid_resolved = sorted(
        issue["id"]
        for issue in issues
        if issue["status"] == "RESOLVED" and issue["verification_status"] != "PASS"
    )
    invalid_pass = sorted(
        issue["id"]
        for issue in issues
        if issue["verification_status"] == "PASS" and issue["status"] != "RESOLVED"
    )
    bad_status = sorted(set(invalid_resolved + invalid_pass))
    checks.append(
        _check(
            "S09",
            "Resolution invariant",
            not bad_status,
            str(bad_status) if bad_status else "RESOLVED iff verifier status is PASS",
        )
    )
    return checks


def _gate_checks(root: Path, config: dict[str, Any]) -> list[Check]:
    claims_ledger = load_json(root / ".review" / "claims.json")
    claims = [
        claim for claim in claims_ledger["claims"] if claim["status"] != "REMOVED"
    ]
    coverage = claims_ledger["coverage"]
    issues = load_json(root / ".review" / "issues.json")["issues"]
    global_contract = load_json(root / ".review" / "global_contract.json")
    structure = load_json(root / ".review" / "structure.json")
    granular_review = load_json(root / ".review" / "granular_review.json")
    final_audit = load_json(root / ".review" / "final_audit.json")
    state = load_state(root)
    request_id = state.get("active_request_id") or state.get("last_request_id")
    selected_granularity = None
    if request_id:
        request_path = root / ".review" / "requests" / f"{request_id}.json"
        if request_path.is_file():
            selected_granularity = load_json(request_path).get("granularity", {}).get("level")
    revisions = load_json(root / ".review" / "revisions.json")["revisions"]
    gates = config["gates"]
    claim_by_id = {claim["id"]: claim for claim in claims}
    def unresolved(issue: dict[str, Any]) -> bool:
        return issue["status"] != "RESOLVED"

    checks: list[Check] = []

    checks.append(
        _check(
            "G12",
            "Global manuscript contract solidified",
            not gates.get("require_global_contract", True)
            or global_contract["status"] == "CURRENT",
            global_contract["status"],
        )
    )
    checks.append(
        _check(
            "G13",
            "Hierarchy review completed at selected depth",
            not gates.get("require_hierarchy_review", True)
            or (
                structure["status"] == "NOT_APPLICABLE"
                if selected_granularity == "MACRO_ONLY"
                else structure["status"] == "CURRENT"
            ),
            structure["status"],
        )
    )
    checks.append(
        _check(
            "G14",
            "Humanizer-backed granular review completed",
            not gates.get("require_granular_review", True)
            or granular_review["status"] == "CURRENT",
            granular_review["status"],
        )
    )
    checks.append(
        _check(
            "G15",
            "Final cross-layer integrity audit passed",
            not gates.get("require_final_integrity_audit", True)
            or final_audit["status"] == "PASS",
            final_audit["status"],
        )
    )

    blockers = sorted(issue["id"] for issue in issues if issue["severity"] == "BLOCKER" and unresolved(issue))
    checks.append(
        _check(
            "G04",
            "No unresolved BLOCKER",
            not gates.get("block_open_blocker", True) or not blockers,
            str(blockers) if blockers else "none",
        )
    )
    core_majors = sorted(
        issue["id"]
        for issue in issues
        if issue["severity"] == "MAJOR"
        and unresolved(issue)
        and issue.get("claim_id") in claim_by_id
        and claim_by_id[issue["claim_id"]]["centrality"] == "CORE"
    )
    checks.append(
        _check(
            "G05",
            "No unresolved MAJOR on CORE Claims",
            not gates.get("block_core_major", True) or not core_majors,
            str(core_majors) if core_majors else "none",
        )
    )
    core_without_evidence = sorted(
        claim["id"] for claim in claims if claim["centrality"] == "CORE" and not claim["evidence"]
    )
    checks.append(
        _check(
            "G06",
            "Every CORE Claim has evidence",
            not gates.get("require_core_evidence", True) or not core_without_evidence,
            str(core_without_evidence) if core_without_evidence else "all covered",
        )
    )
    unchallenged = sorted(
        claim["id"]
        for claim in claims
        if claim["strength"] in {"STRONG", "EXTREME"}
        and claim["adversarial_status"] != "REVIEWED"
    )
    checks.append(
        _check(
            "G07",
            "Every strong Claim has adversarial review",
            not gates.get("require_strong_claim_challenge", True) or not unchallenged,
            str(unchallenged) if unchallenged else "all covered",
        )
    )
    severe_revised_issue_ids = {
        revision["issue_id"]
        for revision in revisions
        if claim_by_id.get(
            next(
                (issue.get("claim_id") for issue in issues if issue["id"] == revision["issue_id"]),
                None,
            ),
            {},
        ).get("centrality")
        == "CORE"
        or next(
            (issue["severity"] for issue in issues if issue["id"] == revision["issue_id"]),
            None,
        )
        in {"BLOCKER", "MAJOR"}
    }
    issue_by_id = {issue["id"]: issue for issue in issues}
    unverified = sorted(
        issue_id
        for issue_id in severe_revised_issue_ids
        if issue_id not in issue_by_id
        or issue_by_id[issue_id]["status"] != "RESOLVED"
        or issue_by_id[issue_id]["verification_status"] != "PASS"
    )
    checks.append(
        _check(
            "G08",
            "Severe/Core revisions independently verified",
            not gates.get("require_independent_verification", True) or not unverified,
            str(unverified) if unverified else "all covered",
        )
    )
    checks.append(
        _check(
            "G09",
            "Conclusion Claim mapping declared complete",
            not gates.get("require_conclusion_mapping", True) or coverage["conclusion_mapped"],
            "complete" if coverage["conclusion_mapped"] else "claim mapper reports incomplete coverage",
        )
    )
    abstract_ok = coverage["abstract_mapped"] and coverage["abstract_numerical_claims_mapped"]
    checks.append(
        _check(
            "G10",
            "Abstract and numerical Claim mapping declared complete",
            not gates.get("require_abstract_mapping", True) or abstract_ok,
            "complete" if abstract_ok else "claim mapper reports incomplete coverage",
        )
    )
    missing_reviews: list[str] = []
    for claim in claims:
        expected = {agent for agent in route_claim(claim) if agent != "challenger"}
        missing = sorted(expected - set(claim.get("reviewed_by", [])))
        if missing:
            missing_reviews.append(f"{claim['id']}:{','.join(missing)}")
    checks.append(
        _check(
            "G11",
            "Dynamic reviewer coverage",
            not gates.get("require_routed_review_coverage", True) or not missing_reviews,
            str(missing_reviews) if missing_reviews else "all routed reviewers completed",
        )
    )
    return checks


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _graph_cycle(adjacency: dict[str, set[str]]) -> list[str] | None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> list[str] | None:
        if node in visiting:
            return trail[trail.index(node) :] + [node]
        if node in visited:
            return None
        visiting.add(node)
        for target in sorted(adjacency.get(node, set())):
            cycle = visit(target, trail + [target])
            if cycle:
                return cycle
        visiting.remove(node)
        visited.add(node)
        return None

    for node in sorted(adjacency):
        cycle = visit(node, [node])
        if cycle:
            return cycle
    return None


def _reachable(starts: set[str], targets: set[str], adjacency: dict[str, set[str]]) -> bool:
    queue = list(starts)
    visited = set(starts)
    while queue:
        current = queue.pop(0)
        if current in targets:
            return True
        for target in adjacency.get(current, set()):
            if target not in visited:
                visited.add(target)
                queue.append(target)
    return False


def _invariant_gate_checks(root: Path, config: dict[str, Any]) -> list[Check]:
    gates = config["gates"]
    ledgers = {
        "terminology": load_json(root / ".review" / "terminology.json"),
        "notation": load_json(root / ".review" / "notation.json"),
        "data": load_json(root / ".review" / "data_consistency.json"),
        "argument": load_json(root / ".review" / "argument_graph.json"),
        "claims": load_json(root / ".review" / "claim_consistency.json"),
        "redundancy": load_json(root / ".review" / "redundancy.json"),
    }
    current_snapshot = manuscript_snapshot(root)
    stale = sorted(
        name
        for name, ledger in ledgers.items()
        if ledger["status"] != "CURRENT"
        or ledger["source_snapshot"] is None
        or ledger["source_snapshot"] != current_snapshot
    )
    checks = [
        _check(
            "G16",
            "Cross-paper invariant registries are current",
            not gates.get("require_current_invariants", True) or not stale,
            str(stale) if stale else current_snapshot["sha256"],
        )
    ]
    if stale:
        for identifier, name, setting in [
            ("G17", "Canonical terminology consistency", "require_terminology_consistency"),
            ("G18", "Canonical notation and definition consistency", "require_notation_consistency"),
            ("G19", "Argument graph integrity and CORE support", "require_argument_integrity"),
            ("G20", "Material data consistency", "require_data_consistency"),
            ("G21", "Cross-location Claim consistency", "require_claim_consistency"),
            ("G22", "Structural redundancy integrity", "block_structural_redundancy"),
        ]:
            checks.append(
                _check(
                    identifier,
                    name,
                    not gates.get(setting, True),
                    "blocked until G16 is current",
                )
            )
        return checks

    claims = [
        claim
        for claim in load_json(root / ".review" / "claims.json")["claims"]
        if claim["status"] != "REMOVED"
    ]
    claim_by_id = {claim["id"]: claim for claim in claims}
    known_claims = set(claim_by_id)

    terminology = ledgers["terminology"]["payload"]
    concepts = terminology["concepts"]
    term_ids = [item["id"] for item in concepts]
    canonical_terms = [_normalized(item["canonical_term"]) for item in concepts]
    owners: dict[str, set[str]] = {}
    terminology_failures: list[str] = []
    for item in concepts:
        for term in [item["canonical_term"], *item["aliases"]]:
            owners.setdefault(_normalized(term), set()).add(item["id"])
        if item["status"] != "CLEAR":
            terminology_failures.append(f"{item['id']}:{item['status']}")
        canonical = _normalized(item["canonical_term"])
        aliases = {_normalized(x) for x in item["aliases"]}
        forbidden = {_normalized(x) for x in item["forbidden_variants"]}
        if {canonical, *aliases} & forbidden:
            terminology_failures.append(f"{item['id']}:allowed/forbidden collision")
        for occurrence in item["occurrences"]:
            surface = _normalized(occurrence["surface_form"])
            if (
                occurrence["meaning_alignment"] != "MATCH"
                or occurrence["usage"] == "CANONICAL" and surface != canonical
                or occurrence["usage"] == "ALIAS" and surface not in aliases
                or occurrence["usage"] not in {"CANONICAL", "ALIAS"}
                or surface in forbidden
            ):
                terminology_failures.append(
                    f"{item['id']}@{occurrence['location']}"
                )
    if len(term_ids) != len(set(term_ids)):
        terminology_failures.append("duplicate concept IDs")
    if len(canonical_terms) != len(set(canonical_terms)):
        terminology_failures.append("duplicate canonical terms")
    terminology_failures.extend(
        f"term collision:{term}" for term, concept_ids in owners.items() if len(concept_ids) > 1
    )
    terminology_failures.extend(
        f"unregistered:{item['term']}" for item in terminology["unregistered_critical_terms"]
    )
    global_contract = load_json(root / ".review" / "global_contract.json")
    if global_contract["status"] == "CURRENT":
        contract_terms = {
            item["concept_id"]: item
            for item in global_contract["payload"]["contract"]["terminology"]
        }
        concepts_by_id = {item["id"]: item for item in concepts}
        if set(contract_terms) != set(concepts_by_id):
            terminology_failures.append("global contract does not cover registry IDs")
        for concept_id in set(contract_terms) & set(concepts_by_id):
            contract_item = contract_terms[concept_id]
            source = concepts_by_id[concept_id]
            if (
                contract_item["canonical"] != source["canonical_term"]
                or contract_item["meaning"] != source["meaning"]
                or contract_item["allowed_variants"] != source["aliases"]
                or contract_item["forbidden_variants"] != source["forbidden_variants"]
                or contract_item["first_definition"] != source["definition_locations"][0]
            ):
                terminology_failures.append(f"global contract drift:{concept_id}")
    checks.append(
        _check(
            "G17",
            "Canonical terminology consistency",
            not gates.get("require_terminology_consistency", True)
            or not terminology_failures,
            str(sorted(set(terminology_failures))) if terminology_failures else "clear",
        )
    )

    notation = ledgers["notation"]["payload"]
    symbols = notation["symbols"]
    notation_failures: list[str] = []
    notation_ids = [item["id"] for item in symbols]
    canonical_symbols = [_normalized(item["canonical_form"]) for item in symbols]
    for item in symbols:
        if item["status"] != "CLEAR":
            notation_failures.append(f"{item['id']}:{item['status']}")
        for occurrence in item["occurrences"]:
            if (
                occurrence["alignment"] != "MATCH"
                or occurrence["meaning"] != item["meaning"]
                or occurrence["domain_or_type"] != item["domain_or_type"]
            ):
                notation_failures.append(f"{item['id']}@{occurrence['location']}")
    if len(notation_ids) != len(set(notation_ids)):
        notation_failures.append("duplicate notation IDs")
    if len(canonical_symbols) != len(set(canonical_symbols)):
        notation_failures.append("reused canonical symbols")
    notation_failures.extend(
        f"unregistered:{item['symbol']}"
        for item in notation["unregistered_critical_symbols"]
    )
    if global_contract["status"] == "CURRENT":
        contract_symbols = {
            item["notation_id"]: item
            for item in global_contract["payload"]["contract"]["notation"]
        }
        symbols_by_id = {item["id"]: item for item in symbols}
        if set(contract_symbols) != set(symbols_by_id):
            notation_failures.append("global contract does not cover registry IDs")
        for notation_id in set(contract_symbols) & set(symbols_by_id):
            contract_item = contract_symbols[notation_id]
            source = symbols_by_id[notation_id]
            if (
                contract_item["symbol"] != source["symbol"]
                or contract_item["canonical_form"] != source["canonical_form"]
                or contract_item["meaning"] != source["meaning"]
                or contract_item["domain_or_type"] != source["domain_or_type"]
                or contract_item["first_definition"] != source["definition_locations"][0]
            ):
                notation_failures.append(f"global contract drift:{notation_id}")
    checks.append(
        _check(
            "G18",
            "Canonical notation and definition consistency",
            not gates.get("require_notation_consistency", True) or not notation_failures,
            str(sorted(set(notation_failures))) if notation_failures else "clear",
        )
    )

    data = ledgers["data"]["payload"]
    data_records = data["records"]
    data_ids = [item["id"] for item in data_records]
    known_data = set(data_ids)
    data_failures: list[str] = []
    if len(data_ids) != len(known_data):
        data_failures.append("duplicate data IDs")
    for item in data_records:
        if item["status"] != "CLEAR":
            data_failures.append(f"{item['id']}:{item['status']}")
        unknown = sorted(set(item["claim_ids"]) - known_claims)
        if unknown:
            data_failures.append(f"{item['id']}:unknown Claims {unknown}")
        for occurrence in item["occurrences"]:
            relation = occurrence["relation"]
            if relation == "MATCH" and (
                occurrence["value"] != item["canonical_value"]
                or occurrence["unit"] != item["unit"]
                or occurrence["conditions"] != item["conditions"]
            ):
                data_failures.append(f"{item['id']}@{occurrence['location']}:false MATCH")
            elif relation == "AUTHORIZED_VARIATION" and not (
                occurrence["justification"] and occurrence["justification"].strip()
            ):
                data_failures.append(
                    f"{item['id']}@{occurrence['location']}:unjustified variation"
                )
            elif relation in {"CONFLICT", "NEEDS_AUTHOR"}:
                data_failures.append(f"{item['id']}@{occurrence['location']}:{relation}")
    data_failures.extend(
        f"unmapped:{item['location']}" for item in data["unmapped_material_values"]
    )
    checks.append(
        _check(
            "G20",
            "Material data consistency",
            not gates.get("require_data_consistency", True) or not data_failures,
            str(sorted(set(data_failures))) if data_failures else "clear",
        )
    )

    graph = ledgers["argument"]["payload"]
    nodes = graph["nodes"]
    edges = graph["edges"]
    node_by_id = {item["id"]: item for item in nodes}
    node_ids = [item["id"] for item in nodes]
    edge_ids = [item["id"] for item in edges]
    known_nodes = set(node_by_id)
    argument_failures: list[str] = []
    if len(node_ids) != len(known_nodes) or len(edge_ids) != len(set(edge_ids)):
        argument_failures.append("duplicate graph IDs")
    invalid_refs = sorted(
        {
            endpoint
            for edge in edges
            for endpoint in [edge["source_id"], edge["target_id"]]
            if endpoint not in known_nodes
        }
    )
    if invalid_refs:
        argument_failures.append(f"unknown graph nodes {invalid_refs}")
    invalid_claim_refs = sorted(
        {
            item["claim_id"]
            for item in nodes
            if item["claim_id"] is not None and item["claim_id"] not in known_claims
        }
    )
    invalid_data_refs = sorted(
        {data_id for item in nodes for data_id in item["data_ids"] if data_id not in known_data}
    )
    if invalid_claim_refs or invalid_data_refs:
        argument_failures.append(
            f"unknown Claim/Data refs claims={invalid_claim_refs}, data={invalid_data_refs}"
        )
    claim_nodes = {
        claim_id: {item["id"] for item in nodes if item["claim_id"] == claim_id}
        for claim_id in known_claims
    }
    missing_claim_nodes = sorted(claim_id for claim_id, ids in claim_nodes.items() if not ids)
    if missing_claim_nodes:
        argument_failures.append(f"unmapped Claims {missing_claim_nodes}")
    dependency_relations = {"DEPENDS_ON", "SUPPORTS", "DERIVES", "VALIDATES"}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in known_nodes}
    reverse_adjacency: dict[str, set[str]] = {node_id: set() for node_id in known_nodes}
    if not invalid_refs:
        for edge in edges:
            if edge["relation"] in dependency_relations:
                adjacency[edge["source_id"]].add(edge["target_id"])
                reverse_adjacency[edge["target_id"]].add(edge["source_id"])
    cycle = _graph_cycle(adjacency)
    if cycle:
        argument_failures.append("cycle:" + "->".join(cycle))
    for claim in claims:
        for dependency in claim["dependencies"]:
            if dependency not in claim_nodes or not _reachable(
                claim_nodes[dependency], claim_nodes[claim["id"]], adjacency
            ):
                argument_failures.append(f"broken dependency:{dependency}->{claim['id']}")
    support_types = {"ASSUMPTION", "PREMISE", "DERIVATION", "DATA", "EVIDENCE", "REFERENCE"}
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
                if node_by_id[source]["type"] in support_types:
                    supported = True
                if source not in visited:
                    visited.add(source)
                    queue.append(source)
        if not supported:
            argument_failures.append(f"orphan CORE Claim:{claim['id']}")
    checks.append(
        _check(
            "G19",
            "Argument graph integrity and CORE support",
            not gates.get("require_argument_integrity", True) or not argument_failures,
            str(sorted(set(argument_failures))) if argument_failures else "clear",
        )
    )

    consistency = ledgers["claims"]["payload"]
    records = consistency["records"]
    record_ids = [item["claim_id"] for item in records]
    claim_failures: list[str] = []
    if len(record_ids) != len(set(record_ids)) or set(record_ids) != known_claims:
        claim_failures.append("active Claims are not mapped exactly once")
    for record in records:
        claim = claim_by_id.get(record["claim_id"])
        if claim is None:
            claim_failures.append(f"unknown Claim:{record['claim_id']}")
            continue
        if record["canonical_statement"] != claim["statement"]:
            claim_failures.append(f"statement drift:{claim['id']}")
        if record["canonical_scope"] != claim["scope"]:
            claim_failures.append(f"scope drift:{claim['id']}")
        if record["status"] != "PASS":
            claim_failures.append(f"{claim['id']}:{record['status']}")
        if claim["centrality"] == "CORE" and (
            not record["support_node_ids"] or not record["body_locations"]
        ):
            claim_failures.append(f"CORE trace missing:{claim['id']}")
        if set(record["support_node_ids"]) - known_nodes:
            claim_failures.append(f"unknown support node:{claim['id']}")
        if not any(item["role"] in {"BODY", "RESULT", "DISCUSSION"} for item in record["occurrences"]):
            claim_failures.append(f"no body source:{claim['id']}")
        for occurrence in record["occurrences"]:
            if (
                not occurrence["synchronized"]
                or occurrence["scope_relation"]
                in {"BROADER", "CONTRADICTORY", "UNMAPPED"}
            ):
                claim_failures.append(
                    f"{claim['id']}@{occurrence['location']}:{occurrence['scope_relation']}"
                )
    claim_failures.extend(
        f"unmapped:{item['location']}"
        for item in consistency["unmapped_claim_occurrences"]
    )
    checks.append(
        _check(
            "G21",
            "Cross-location Claim consistency",
            not gates.get("require_claim_consistency", True) or not claim_failures,
            str(sorted(set(claim_failures))) if claim_failures else "clear",
        )
    )

    redundancy = ledgers["redundancy"]["payload"]
    redundancy_failures = [
        f"exact:{item['id']}"
        for item in redundancy["exact_duplicates"]
        if item["severity"] == "MAJOR" and item["classification"] != "INTENTIONAL"
    ]
    redundancy_failures.extend(
        f"intentional duplicate lacks rationale:{item['id']}"
        for item in redundancy["exact_duplicates"]
        if item["classification"] == "INTENTIONAL"
        and not (item["justification"] and item["justification"].strip())
    )
    redundancy_failures.extend(
        f"duplicate contribution:{item['id']}"
        for item in redundancy["contribution_duplicates"]
        if not item["distinct"]
    )
    checks.append(
        _check(
            "G22",
            "Structural redundancy integrity",
            not gates.get("block_structural_redundancy", True)
            or not redundancy_failures,
            str(sorted(set(redundancy_failures)))
            if redundancy_failures
            else "clear; semantic similarity remains diagnostic only",
        )
    )
    return checks


def _format_build_command(command: list[str], root: Path, main_tex: Path) -> list[str]:
    values = {
        "root": str(root),
        "main_tex": str(main_tex),
        "main_dir": str(main_tex.parent),
        "main_name": main_tex.name,
    }
    rendered: list[str] = []
    for part in command:
        value = str(part)
        for key, replacement in values.items():
            value = value.replace("{" + key + "}", replacement)
        rendered.append(value)
    return rendered


def _select_build(config: dict[str, Any], root: Path, main_tex: Path) -> tuple[list[str], Path] | None:
    configured = config.get("build_command", [])
    if configured:
        if not isinstance(configured, list) or not all(isinstance(item, str) for item in configured):
            raise HarnessError("build_command must be an array of command arguments")
        return _format_build_command(configured, root, main_tex), root
    candidates = [
        ("latexmk", ["latexmk", "-pdf", "-interaction=nonstopmode", main_tex.name]),
        ("tectonic", ["tectonic", main_tex.name]),
        ("xelatex", ["xelatex", "-interaction=nonstopmode", main_tex.name]),
        ("pdflatex", ["pdflatex", "-interaction=nonstopmode", main_tex.name]),
    ]
    for executable, command in candidates:
        if shutil.which(executable):
            return command, main_tex.parent
    return None


def _compile_checks(root: Path, config: dict[str, Any], final: bool) -> list[Check]:
    if not final:
        return [Check("G01", "LaTeX compilation", "SKIP", "use --final to compile")]
    if not config["gates"].get("require_compilation", True):
        return [Check("G01", "LaTeX compilation", "SKIP", "disabled in config")]
    try:
        main_tex = resolve_repo_path(root, config["main_tex"])
    except HarnessError as exc:
        return [_check("G01", "LaTeX compilation", False, str(exc))]
    if not main_tex.is_file():
        return [_check("G01", "LaTeX compilation", False, f"main TeX file not found: {main_tex}")]
    try:
        selected = _select_build(config, root, main_tex)
    except HarnessError as exc:
        return [_check("G01", "LaTeX compilation", False, str(exc))]
    if selected is None:
        return [_check("G01", "LaTeX compilation", False, "no configured command or supported TeX engine found")]
    command, cwd = selected
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(config.get("compile_timeout_seconds", 180)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [_check("G01", "LaTeX compilation", False, str(exc))]
    combined = completed.stdout + "\n" + completed.stderr
    log_path = main_tex.parent / f"{main_tex.stem}.log"
    if log_path.is_file():
        combined += "\n" + log_path.read_text(encoding="utf-8", errors="replace")
    unresolved_refs = bool(
        re.search(r"undefined references|reference .+ undefined|rerun to get cross-references right", combined, re.I)
    )
    unresolved_citations = bool(re.search(r"undefined citations|citation .+ undefined", combined, re.I))
    return [
        _check(
            "G01",
            "LaTeX compilation",
            completed.returncode == 0,
            "success" if completed.returncode == 0 else f"exit code {completed.returncode}",
        ),
        _check("G02", "No unresolved references", not unresolved_refs, "none" if not unresolved_refs else "warnings detected"),
        _check("G03", "No unresolved citations", not unresolved_citations, "none" if not unresolved_citations else "warnings detected"),
    ]


def _scientific_validator_checks(
    root: Path, config: dict[str, Any], final: bool
) -> list[Check]:
    configured = config.get("scientific_validators", [])
    if configured == []:
        return [
            Check(
                "G23",
                "Pluggable scientific validators",
                "SKIP",
                "no discipline-specific validator configured",
            )
        ]
    if not final:
        return [
            Check(
                "G23",
                "Pluggable scientific validators",
                "SKIP",
                "use --final to execute configured validators",
            )
        ]
    if not isinstance(configured, list):
        return [_check("G23", "Pluggable scientific validators", False, "configuration must be an array")]
    checks: list[Check] = []
    seen: set[str] = set()
    try:
        main_tex = resolve_repo_path(root, config["main_tex"])
    except HarnessError as exc:
        return [_check("G23", "Pluggable scientific validators", False, str(exc))]
    for entry in configured:
        if not isinstance(entry, dict):
            checks.append(_check("G23", "Scientific validator configuration", False, "entry must be an object"))
            continue
        identifier = str(entry.get("id") or "")
        required_value = entry.get("required", True)
        required = required_value is True
        command = entry.get("command")
        timeout_value = entry.get("timeout_seconds", 300)
        label = f"SV-{identifier or 'invalid'}"
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", identifier)
            or identifier in seen
            or not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
            or not isinstance(required_value, bool)
            or not isinstance(timeout_value, int)
            or not 1 <= timeout_value <= 3600
        ):
            checks.append(
                _check(
                    label,
                    "Scientific validator configuration",
                    False,
                    "id must be unique and command must be a non-empty argument array",
                )
            )
            continue
        seen.add(identifier)
        formatted = _format_build_command(command, root, main_tex)
        completed: subprocess.CompletedProcess[str] | None = None
        output: dict[str, Any] | None = None
        error: str | None = None
        try:
            completed = subprocess.run(
                formatted,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_value,
                check=False,
            )
            if completed.returncode != 0:
                raise HarnessError(f"command exited with {completed.returncode}: {completed.stderr[-1000:]}")
            parsed = json.loads(completed.stdout)
            output = parsed if isinstance(parsed, dict) else None
            if not isinstance(output, dict):
                raise HarnessError("validator stdout must be one JSON object")
            validate_with_schema(root, "scientific-validator-result.schema.json", output)
            if output["validator_id"] != identifier:
                raise HarnessError(
                    f"reported validator_id {output['validator_id']!r} does not match {identifier!r}"
                )
            passed = output["status"] == "PASS"
            detail = f"{output['status']}: {output['summary']}"
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, HarnessError) as exc:
            passed = False
            detail = str(exc)
            error = detail
        validator_run_id = make_run_id(f"scientific-{identifier}")
        stdout = completed.stdout if completed is not None else ""
        stderr = completed.stderr if completed is not None else ""
        run_record = {
            "schema_version": 1,
            "run_id": validator_run_id,
            "validator_id": identifier,
            "executed_at": utc_now(),
            "required": required,
            "source_snapshot": manuscript_snapshot(root),
            "command_sha256": sha256_value(formatted),
            "return_code": completed.returncode if completed is not None else None,
            "stdout_sha256": sha256_value(stdout),
            "stderr_sha256": sha256_value(stderr),
            "stdout": redact(stdout, max_inline_chars=4000),
            "stderr": redact(stderr, max_inline_chars=4000),
            "output": output if error is None else None,
            "error": error,
        }
        validate_with_schema(root, "scientific-validator-run.schema.json", run_record)
        run_path = (
            root
            / ".review"
            / "scientific-validation"
            / validator_run_id
            / "result.json"
        )
        write_json(run_path, run_record)
        append_event(
            root,
            "scientific_validator.completed",
            {
                "validator_id": identifier,
                "required": required,
                "passed": passed,
                "detail": detail,
                "command_sha256": run_record["command_sha256"],
            },
            run_id=validator_run_id,
            actor=f"scientific-validator:{identifier}",
            artifact_refs=[{"path": str(run_path.relative_to(root))}],
        )
        if passed:
            checks.append(_check(label, f"Scientific validator {identifier}", True, detail))
        elif required:
            checks.append(_check(label, f"Scientific validator {identifier}", False, detail))
        else:
            checks.append(
                Check(
                    label,
                    f"Optional scientific validator {identifier}",
                    "SKIP",
                    f"diagnostic failure: {detail}",
                )
            )
    return checks


def evaluate(root: Path, final: bool = False) -> dict[str, Any]:
    from coherence import gate_failures

    config = load_config(root)
    checks: list[Check] = []
    try:
        checks.extend(_schema_checks(root))
        if all(check.passed for check in checks):
            checks.extend(_integrity_checks(root))
        if all(check.passed for check in checks):
            checks.extend(_gate_checks(root, config))
            checks.extend(_invariant_gate_checks(root, config))
            names = {"G24": "Multiscale source and parent contracts current",
                     "G25": "Multiscale coverage and stable parent chains",
                     "G26": "Deterministic risk routing reproduced",
                     "G27": "Bottom-up coverage and critical logic closure",
                     "G28": "Evidence-grounded relation ontology and critical verification"}
            for gate, failures in gate_failures(root).items():
                checks.append(_check(gate, names[gate], not failures,
                                     "; ".join(failures) if failures else "clear"))
    except HarnessError as exc:
        checks.append(_check("S00", "Harness state readable", False, str(exc)))
    checks.extend(_compile_checks(root, config, final))
    checks.extend(_scientific_validator_checks(root, config, final))
    return {
        "passed": all(check.passed for check in checks),
        "final": final,
        "checks": [asdict(check) for check in checks],
    }


def render_text(report: dict[str, Any]) -> str:
    lines = []
    for check in report["checks"]:
        lines.append(f"[{check['status']}] {check['id']} {check['name']}: {check['detail']}")
    lines.append("PASS" if report["passed"] else "FAIL")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Paper Review Harness gates")
    parser.add_argument("--root", type=Path, help="paper repository root")
    parser.add_argument("--final", action="store_true", help="include compilation gates")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        root = find_root(args.root)
        report = evaluate(root, final=args.final)
    except HarnessError as exc:
        report = {"passed": False, "final": args.final, "checks": [asdict(_check("S00", "Harness setup", False, str(exc)))]}
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_text(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
