"""Sparse candidates and evidence-grounded proposal/verification of logical edges."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import re

from paper_review_lib import (
    HarnessError, load_json, make_run_id, merge_review_output, sha256_value,
    validate_with_schema, write_json, update_state,
)
from provenance import append_event

CRITICAL_SENTENCE_TYPES = {"CLAIM", "PREMISE", "EVIDENCE", "DEFINITION", "ASSUMPTION", "INFERENCE"}


def _read(root: Path, name: str) -> dict:
    return load_json(root / ".review" / name)


def ontology(root: Path) -> dict:
    value = _read(root, "logic_ontology_v1.json")
    if value["version"] != "logic_ontology_v1":
        raise HarnessError("Unsupported logic ontology")
    return value


def candidate_graph(root: Path, inventory: dict, records: list[dict],
                    semantic_candidates: list[dict] | None = None,
                    local_graphs: list[dict] | None = None) -> dict:
    """O(units + references + mapped Claim memberships), never all pairs.

    Semantic retrieval is an optional bounded caller-supplied proposal list. It
    receives exactly the same evidence and independent verification checks.
    """
    units = {u["id"]: u for u in inventory["units"]}
    by_record = {r["node_id"]: r for r in records}
    claims = {c["id"]: c for c in _read(root, "claims.json")["claims"] if c["status"] != "REMOVED"}
    core = {c for c, value in claims.items() if value["centrality"] == "CORE"}
    claim_units: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if units[record["node_id"]]["level"] == "PARAGRAPH":
            for claim in record["claim_ids"]:
                claim_units[claim].append(record["node_id"])
    candidates: dict[tuple[str, str, str], dict] = {}

    def add(source: str, target: str, scale: str, reason: str, critical: bool = False):
        if source == target:
            return
        if source not in units or (target not in units and target not in claims):
            raise HarnessError("Candidate edge references an unknown node")
        if source not in by_record or (target in units and target not in by_record):
            return
        key = (source, target, scale)
        if key in candidates:
            candidates[key]["reasons"] = sorted(set(candidates[key]["reasons"] + [reason]))
            candidates[key]["critical"] |= critical
            return
        candidates[key] = {"id": "R" + sha256_value(list(key))[:16], "source_id": source,
                           "target_id": target, "scale": scale, "reasons": [reason],
                           "critical": critical, "priority": "HIGH" if critical else "NORMAL"}

    children: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for unit in inventory["units"]:
        if unit["id"] in by_record and unit["parent_id"]:
            children[(unit["parent_id"], unit["level"])].append(unit)
            critical = (unit["level"] == "SECTION"
                        or by_record[unit["id"]].get("sentence_type") in CRITICAL_SENTENCE_TYPES
                        or bool(core.intersection(by_record[unit["id"]].get("claim_ids", []))))
            add(unit["id"], unit["parent_id"], "CROSS", "HIERARCHICAL", critical)
    from local_recovery import proposals
    for proposal in proposals(local_graphs or []):
        add(proposal["source_id"], proposal["target_id"], proposal["scale"], "LOCAL_RECOVERY",
            proposal["scale"] == "SECTION" or any(by_record[uid].get("sentence_type") in CRITICAL_SENTENCE_TYPES
                                                   for uid in (proposal["source_id"], proposal["target_id"])))
    for claim_id in sorted(core):
        for uid in claim_units[claim_id]:
            add(uid, claim_id, "CROSS", "CORE_CLAIM_SUPPORT", True)

    labels: dict[str, str] = {}
    duplicates = []
    for u in inventory["units"]:
        if u["level"] != "PARAGRAPH":
            continue
        for label in re.findall(r"\\label\{([^}]+)\}", u["text"]):
            if label in labels:
                duplicates.append(label)
            labels[label] = u["id"]
    unresolved = []
    for u in inventory["units"]:
        if u["level"] != "PARAGRAPH" or u["id"] not in by_record:
            continue
        for ref in re.findall(r"\\(?:eqref|ref|autoref|cref|Cref)\{([^}]+)\}", u["text"]):
            for name in ref.split(","):
                target = labels.get(name.strip())
                if target:
                    add(u["id"], target, "PARAGRAPH", "EXPLICIT_REFERENCE")
                else:
                    unresolved.append({"source_id": u["id"], "reference": name.strip()})
    # Retrieval remains bounded by document size, and cannot create unreviewed nodes.
    semantic_candidates = semantic_candidates or []
    if len(semantic_candidates) > 2 * len(units):
        raise HarnessError("Semantic candidate budget exceeds two proposals per source unit")
    for edge in semantic_candidates:
        add(edge["source_id"], edge["target_id"], edge["scale"], "SEMANTIC_RISK", edge.get("critical", False))
    for edge in candidates.values():
        edge["priority"] = "HIGH" if edge["critical"] else "NORMAL"
    return {"ontology_version": "logic_ontology_v1", "candidate_edges": list(candidates.values()),
            "unresolved_references": unresolved, "duplicate_labels": sorted(set(duplicates)),
            "claim_stars": [{"claim_id": c, "source_unit_ids": claim_units[c],
                             "dependencies": claims[c]["dependencies"], "evidence": claims[c]["evidence"],
                             "locations": claims[c]["locations"], "scope": claims[c]["scope"]} for c in sorted(core)]}


def _contexts(inventory: dict, records: list[dict], claims: dict) -> dict:
    units = {u["id"]: u for u in inventory["units"]}
    contracts = {r["node_id"]: r for r in records}
    descendant_paragraphs = defaultdict(list)
    for unit in inventory["units"]:
        if unit["level"] != "PARAGRAPH":
            continue
        current = unit["parent_id"]
        while current:
            descendant_paragraphs[current].append(unit)
            current = units[current]["parent_id"]
    contexts = {}
    for node_id, contract in contracts.items():
        unit = units[node_id]
        leaves = descendant_paragraphs[node_id] if unit["level"] in {"PAPER", "SECTION"} else [unit]
        if not leaves and unit["level"] == "SECTION" and unit["depth"]:
            leaves = [unit]  # An empty section still has a literal heading.
        # Bounded source context for parent-goal checks; contracts remain explicit.
        selected = leaves if len(leaves) <= 6 else leaves[:3] + leaves[-3:]
        contexts[node_id] = {"id": node_id, "level": unit["level"], "contract": contract,
                             "source_units": selected, "omitted_unit_count": len(leaves) - len(selected),
                             "subtree_sha256": sha256_value([(u["id"], u["text"]) for u in leaves])}
    for node_id, claim in claims.items():
        leaves = [units[r["node_id"]] for r in records if node_id in r.get("claim_ids", [])
                  and units[r["node_id"]]["level"] == "PARAGRAPH"]
        contexts[node_id] = {"id": node_id, "level": "CLAIM", "contract": claim,
                             "source_units": leaves, "omitted_unit_count": 0,
                             "subtree_sha256": sha256_value([(u["id"], u["text"]) for u in leaves])}
    return contexts


def _all_contexts(root: Path, inventory: dict, records: list[dict]) -> dict:
    claims = {c["id"]: {k: v for k, v in c.items() if k not in {"status", "reviewed_by", "adversarial_status", "notes"}}
              for c in _read(root, "claims.json")["claims"] if c["status"] != "REMOVED"}
    return _contexts(inventory, records, claims)


def _material(contexts: dict, edge: dict) -> dict:
    return {"candidate": edge, "source": contexts[edge["source_id"]], "target": contexts[edge["target_id"]]}


def _fingerprint(root: Path, material: dict) -> str:
    stable = deepcopy(material)
    # Source offsets may shift after an unrelated insertion. Content IDs + text
    # remain stable; evidence offsets inside each unit remain valid.
    for endpoint in ("source", "target"):
        stable[endpoint]["source_units"] = [
            {k: u[k] for k in ("id", "parent_id", "level", "text", "path")}
            for u in stable[endpoint]["source_units"]
        ]
    macro = _read(root, "global_contract.json")["payload"]["contract"]
    terms = _read(root, "terminology.json")["payload"]["concepts"]
    symbols = _read(root, "notation.json")["payload"]["symbols"]
    def definitions(entries):
        return [{k: v for k, v in e.items() if k not in {"occurrences", "definition_locations", "status", "conflicts"}}
                for e in entries]
    return sha256_value({"material": stable, "ontology": ontology(root),
                         "global_scope": macro["theme"],
                         "terms": definitions(terms), "notation": definitions(symbols)})


def _relation_allowed(root: Path, scale: str, relation: str) -> bool:
    return any(r["name"] == relation and scale in r["allowed_scale"] for r in ontology(root)["relations"])


def _evidence(root: Path, evidence: dict, endpoint: dict) -> None:
    unit = next((u for u in endpoint["source_units"] if u["id"] == evidence["unit_id"]), None)
    if unit is None:
        raise HarnessError("Evidence span is outside its endpoint subtree/Claim occurrences")
    start, end = evidence["start"], evidence["end"]
    if not 0 <= start < end <= len(unit["text"]) or unit["text"][start:end] != evidence["text"]:
        raise HarnessError("Evidence text does not match the specified source span")
    raw = (root / unit["path"]).read_text(encoding="utf-8-sig")
    if raw[unit["start"] + start:unit["start"] + end] != evidence["text"]:
        raise HarnessError("Evidence quote differs from raw manuscript source")


def _validate_edge_output(root: Path, result: dict, materials: list[dict], verify: bool) -> None:
    schema = "logic-verify-output.schema.json" if verify else "logic-map-output.schema.json"
    validate_with_schema(root, schema, result)
    rows = result["verifications" if verify else "relations"]
    known = {m["candidate"]["id"]: m for m in materials}
    ids = [r["candidate_id"] for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(known):
        raise HarnessError("Relation output does not exactly cover candidate IDs")
    for row in rows:
        material = known[row["candidate_id"]]
        if not _relation_allowed(root, material["candidate"]["scale"], row["relation"]):
            raise HarnessError("Relation name is not allowed at this scale")
        for endpoint in ("source", "target"):
            _evidence(root, row[endpoint + "_evidence"], material[endpoint])
        if verify and material["candidate"]["critical"] and not row["counterfactual"].strip():
            raise HarnessError("Critical relations require a counterfactual check")


def _status(proposal: dict, verification: dict) -> str:
    if verification["status"] == "UNCERTAIN":
        return "UNCERTAIN"
    if verification["status"] == "PASS" and verification["relation"] == proposal["relation"]:
        return "CONFIRMED"
    return "DISPUTED"


def _critical_failure(edge: dict, status: str, relation: str) -> bool:
    if not edge["critical"]:
        return False
    if status != "CONFIRMED" or relation == "NO_RELATION":
        return True
    if "CORE_CLAIM_SUPPORT" in edge["reasons"] and relation != "SUPPORTS":
        return True
    if "HIERARCHICAL" in edge["reasons"] and relation == "ALIGNS_WITH":
        # Merely consistent wording does not establish implementation or support.
        return True
    return False


def _independent_context(endpoint: dict) -> dict:
    context = deepcopy(endpoint)
    context["contract"] = {k: v for k, v in context["contract"].items()
                           if k not in {"status", "risk_signals", "humanizer_patterns", "recommended_action", "parent_alignment"}}
    return context


def run_relations(workflow, inventory: dict, records: list[dict], pass_name: str) -> dict:
    from local_recovery import recover, proposals
    root = workflow.root
    old_path = root / ".review/logic/relations.json"
    old = load_json(old_path) if old_path.is_file() else {"relations": []}
    contexts = _all_contexts(root, inventory, records)
    local_graphs = recover(workflow, inventory, records, contexts, old.get("local_graphs"))
    graph = candidate_graph(root, inventory, records, local_graphs=local_graphs)
    local_proposals = {}
    for row in proposals(local_graphs):
        key = (row["source_id"], row["target_id"], row["scale"])
        local_proposals.setdefault(key, row)
    prior = {r["id"]: r for r in old["relations"]}
    current = []
    pending = []
    materials = {}
    for edge in graph["candidate_edges"]:
        material = _material(contexts, edge)
        fingerprint = _fingerprint(root, material)
        material["fingerprint"] = fingerprint
        materials[edge["id"]] = material
        cached = prior.get(edge["id"])
        if cached and cached["fingerprint"] == material["fingerprint"] and cached["status"] == "CONFIRMED":
            # Still recheck evidence against current source before reusing a verdict.
            _validate_edge_output(root, {"agent": "argument_reviewer", "relations": [cached["mapper"]]}, [material], False)
            _validate_edge_output(root, {"agent": "verifier", "verifications": [cached["verification"]]}, [material], True)
            current.append(cached)
        else:
            pending.append(material)
    run_id = make_run_id("logic-" + pass_name)
    run_dir = root / ".review/runs" / run_id / "logic"
    write_json(run_dir / "candidates.json", graph)
    for index in range(0, len(pending), 24):
        batch = pending[index:index + 24]
        batch_id = f"{run_id}-{index//24:03d}"
        update_state(root, phase="LOGIC_RELATION_MAPPING", active_run_id=batch_id + "-map")
        remote_batch = [m for m in batch if "LOCAL_RECOVERY" not in m["candidate"]["reasons"]]
        mapper = workflow.runner.run("argument_reviewer", {
            "task": "LOGIC_RELATION_MAPPING", "ontology": ontology(root), "candidates": batch,
            "instructions": "Propose one fixed-ontology relation for each candidate, with exact raw source evidence spans and confidence. Confidence is not scientific truth probability. If the candidate relation is not justified, select the closest testable proposal and explain the gap for independent verification.",
        } | {"candidates": remote_batch}, batch_id + "-map", output_schema="logic-map-output.schema.json") if remote_batch else {"agent": "argument_reviewer", "relations": []}
        for material in batch:
            edge = material["candidate"]
            if "LOCAL_RECOVERY" in edge["reasons"]:
                row = local_proposals[(edge["source_id"], edge["target_id"], edge["scale"])]
                mapper["relations"].append({k: v for k, v in row.items() if k not in {"source_id", "target_id", "scale", "mapper_run_id"}} | {"candidate_id": edge["id"]})
        _validate_edge_output(root, mapper, batch, False)
        by_id = {r["candidate_id"]: r for r in mapper["relations"]}
        # Deliberately exclude mapper rationale, confidence, selected evidence and
        # other review reports from the fresh verifier assignment.
        verifier_candidates = [{"candidate": m["candidate"], "source": _independent_context(m["source"]), "target": _independent_context(m["target"]),
                                "proposed_relation": by_id[m["candidate"]["id"]]["relation"]} for m in batch]
        verifier_input = {"task": "LOGIC_RELATION_VERIFICATION", "ontology": ontology(root),
                          "candidates": verifier_candidates,
                          "instructions": "Independently test the candidate relation using original source and its ontology definition. Do not read mapper runs/rationale. Return PASS/FAIL/ALTERNATIVE/UNCERTAIN. For critical edges also test what fails if the source premise/evidence is removed. No prose-smoothness failure alone establishes an argument defect."}
        update_state(root, phase="LOGIC_RELATION_VERIFICATION", active_run_id=batch_id + "-verify")
        verification = workflow.runner.run("verifier", verifier_input, batch_id + "-verify",
                                           output_schema="logic-verify-output.schema.json")
        _validate_edge_output(root, verification, batch, True)
        verifications = {r["candidate_id"]: r for r in verification["verifications"]}
        write_json(run_dir / f"{index//24:03d}-mapper.json", mapper)
        write_json(run_dir / f"{index//24:03d}-verifier-input.json", verifier_input)
        write_json(run_dir / f"{index//24:03d}-verification.json", verification)
        for material in batch:
            edge = material["candidate"]
            proposal, verdict = by_id[edge["id"]], verifications[edge["id"]]
            current.append({**edge, "relation": proposal["relation"], "mapper": proposal,
                            "verification": verdict, "fingerprint": material["fingerprint"],
                            "status": _status(proposal, verdict), "mapper_run_id": local_proposals[(edge["source_id"], edge["target_id"], edge["scale"])]["mapper_run_id"] if "LOCAL_RECOVERY" in edge["reasons"] else batch_id + "-map",
                            "verifier_run_id": batch_id + "-verify"})
    ids = {r["id"] for r in current}
    retired = [{**r, "status": "REMOVED"} for r in old["relations"] if r["id"] not in ids]
    result = {"schema_version": 1, "ontology_version": "logic_ontology_v1",
              "source_snapshot": inventory["source_snapshot"], "run_id": run_id,
              "local_graphs": local_graphs,
              "candidate_graph": graph, "relations": sorted(current, key=lambda r: r["id"]),
              "retired_relations": retired, "reused_count": len(current) - len(pending)}
    validate_with_schema(root, "logic-graph.schema.json", result)
    write_json(run_dir / "relations.json", result)
    write_json(old_path, result)
    write_json(root / ".review/logic/candidate_edges.json", graph)
    write_json(root / ".review/logic/local_graphs.json", {"groups": local_graphs})
    write_json(root / ".review/logic/rhetorical_graph.json", {"relations": [r for r in current if r["scale"] != "CROSS"]})
    write_json(root / ".review/logic/realization_graph.json", {"relations": [r for r in current if r["scale"] == "CROSS"]})
    write_json(root / ".review/logic/critical_sentence_edges.json", {"relations": [r for r in current if materials[r["id"]]["source"]["level"] == "SENTENCE"]})
    disputes = [r for r in current if r["status"] in {"DISPUTED", "UNCERTAIN"}]
    write_json(root / ".review/logic/logic_disputes.json", {"relations": disputes})
    append_event(root, "logic.relations_verified", {"run_id": run_id, "edges": len(current),
                 "reused": result["reused_count"], "disputes": len(disputes)}, run_id=run_id,
                 artifact_refs=[{"path": (run_dir / "relations.json").relative_to(root).as_posix()}])
    update_state(root, last_run_id=run_id, active_run_id=None)
    return result


def escalation_paragraphs(graph: dict, inventory: dict) -> set[str]:
    units = {u["id"]: u for u in inventory["units"]}
    affected = set()
    for edge in graph["relations"]:
        if edge["status"] not in {"DISPUTED", "UNCERTAIN"} and edge["relation"] != "NO_RELATION":
            continue
        for endpoint in (edge["source_id"], edge["target_id"]):
            if endpoint in units and units[endpoint]["level"] == "PARAGRAPH":
                affected.add(endpoint)
            elif endpoint in units and units[endpoint]["level"] == "SECTION":
                affected.update(u["id"] for u in units.values() if u["level"] == "PARAGRAPH" and u["parent_id"] == endpoint)
    return affected


def relation_failures(root: Path, registry: dict) -> list[str]:
    from local_recovery import local_groups, validate_local, proposals
    graph = registry["logic_graph"]
    inventory, records = registry["inventory"], registry["records"]
    if graph["source_snapshot"] != inventory["source_snapshot"]:
        return ["Logical relation graph carries a different source snapshot"]
    expected_groups = local_groups(inventory, records)
    if [item["group"] for item in graph["local_graphs"]] != expected_groups:
        return ["Local context groups do not cover the selected scales"]
    expected = candidate_graph(root, inventory, records, local_graphs=graph["local_graphs"])
    if expected != graph["candidate_graph"]:
        return ["Sparse candidate graph differs from source-grounded recomputation"]
    known = {e["id"]: e for e in expected["candidate_edges"]}
    ids = [r["id"] for r in graph["relations"]]
    if set(ids) != set(known) or len(ids) != len(set(ids)):
        return ["Missing or duplicated relation verdicts"]
    failures = []
    contexts = _all_contexts(root, inventory, records)
    for item in graph["local_graphs"]:
        validate_local(root, item["group"], item["mapped"], item["challenge"])
        expected_fingerprint = sha256_value({"group": item["group"],
                                            "context": [contexts[uid] for uid in item["group"]["unit_ids"]],
                                            "ontology": ontology(root)})
        if item["fingerprint"] != expected_fingerprint:
            failures.append("Local recovery source/contract fingerprint is stale")
        if item["mapper_run_id"] == item["challenge_run_id"]:
            failures.append("Local coverage challenge was not independent")
        critical_ids = set(item["group"]["unit_ids"]) if item["group"]["scale"] == "SECTION" else {
            r["node_id"] for r in records if r.get("sentence_type") in CRITICAL_SENTENCE_TYPES
        }
        failures.extend(f"critical local coverage:{c['node_id']}:{c['status']}"
                        for c in item["challenge"]["checks"] if c["status"] != "PASS" and c["node_id"] in critical_ids)
        failures.extend(f"critical local missing premise:{n['node_id']}"
                        for n in item["mapped"]["nodes"] if n["attachment_status"] == "MISSING_PREMISE" and n["node_id"] in critical_ids)
    local_proposals = {}
    for row in proposals(graph["local_graphs"]):
        key = (row["source_id"], row["target_id"], row["scale"])
        local_proposals.setdefault(key, row)
    for relation in graph["relations"]:
        edge = known[relation["id"]]
        material = _material(contexts, edge)
        if any(relation[k] != v for k, v in edge.items()) or relation["fingerprint"] != _fingerprint(root, material):
            failures.append(f"stale relation:{relation['id']}")
        _validate_edge_output(root, {"agent": "argument_reviewer", "relations": [relation["mapper"]]}, [material], False)
        _validate_edge_output(root, {"agent": "verifier", "verifications": [relation["verification"]]}, [material], True)
        if "LOCAL_RECOVERY" in edge["reasons"]:
            local = local_proposals[(edge["source_id"], edge["target_id"], edge["scale"])]
            expected_proposal = {k: v for k, v in local.items() if k not in {"source_id", "target_id", "scale", "mapper_run_id"}} | {"candidate_id": edge["id"]}
            if expected_proposal != relation["mapper"]:
                failures.append(f"relation differs from local recovery:{edge['id']}")
        if relation["mapper_run_id"] == relation["verifier_run_id"]:
            failures.append(f"non-independent relation verification:{relation['id']}")
        status = _status(relation["mapper"], relation["verification"])
        if status != relation["status"] or relation["relation"] != relation["mapper"]["relation"]:
            failures.append(f"relation verdict drift:{relation['id']}")
        if _critical_failure(edge, status, relation["relation"]):
            failures.append(f"critical relation requires re-review:{relation['id']}:{status}")
    failures.extend(f"duplicate source label:{label}" for label in expected["duplicate_labels"])
    if registry["mode"] in {"PARAGRAPH", "SENTENCE", "ADAPTIVE"}:
        failures.extend(f"unmapped CORE Claim:{star['claim_id']}" for star in expected["claim_stars"] if not star["source_unit_ids"])
    return failures


def materialize_summary(root: Path, registry: dict) -> dict:
    units = {u["id"]: u for u in registry["inventory"]["units"]}
    relations = registry["logic_graph"]["relations"]
    unresolved = [r for r in relations if r["status"] != "CONFIRMED" or r["relation"] == "NO_RELATION"]
    paragraphs = [r for r in registry["records"] if units[r["node_id"]]["level"] == "PARAGRAPH"]
    order = {name: i for i, name in enumerate(["PROBLEM", "BACKGROUND", "GAP", "DEFINITION", "ASSUMPTION", "METHOD", "DERIVATION", "CLAIM", "EVIDENCE", "RESULT", "INTERPRETATION", "LIMITATION", "SUMMARY"])}
    backward = sum(order.get(b["rhetorical_role"], 0) < order.get(a["rhetorical_role"], 0)
                   for a, b in zip(paragraphs, paragraphs[1:])
                   if units[a["node_id"]]["parent_id"] == units[b["node_id"]]["parent_id"])
    long_range = [r for r in relations if r["source_id"] in units and r["target_id"] in units
                  and abs(units[r["source_id"]]["ordinal"] - units[r["target_id"]]["ordinal"]) > 40]
    summary = {"source_snapshot": registry["inventory"]["source_snapshot"], "mode": registry["mode"],
               "section_transition_failures": sum(r["scale"] == "SECTION" for r in unresolved),
               "paragraph_transition_risks": sum(r["scale"] == "PARAGRAPH" for r in unresolved),
               "orphan_sections": [r["source_id"] for r in unresolved if r["scale"] == "CROSS" and units.get(r["source_id"], {}).get("level") == "SECTION"],
               "orphan_core_claims": [s["claim_id"] for s in registry["logic_graph"]["candidate_graph"]["claim_stars"] if not s["source_unit_ids"]],
               "unresolved_reference_count": len(registry["logic_graph"]["candidate_graph"]["unresolved_references"]),
               "high_risk_sentence_count": sum(r["deep_review"] for r in registry["scheduler"]["routes"]),
               "context_sentence_count": sum(r["selected"] and not r["deep_review"] for r in registry["scheduler"]["routes"]),
               "long_range_dependency_count": len(long_range), "backward_transition_count": backward,
               "backtracking_ratio": backward / max(1, len(paragraphs) - 1),
               "disputed_relation_count": len(unresolved),
               "critical_disputes": [r["id"] for r in unresolved if r["critical"]],
               "diagnostics": [r["id"] for r in unresolved if not r["critical"]],
               "note": "Counts describe mapped candidates and model judgments; no aggregate scientific correctness score."}
    write_json(root / ".review/logic/coherence_summary.json", summary)
    return summary


def record_relation_issues(root: Path, graph: dict, records: list[dict]) -> None:
    issues = []
    for edge in graph["relations"]:
        if _critical_failure(edge, edge["status"], edge["relation"]):
            issues.append({"id": "LG-" + str(int(edge["id"][1:13], 16)),
                           "claim_id": edge["target_id"] if edge["target_id"].startswith("C") else None,
                           "location": edge["verification"]["source_evidence"]["unit_id"],
                           "severity": "BLOCKER", "category": "argument",
                           "problem": "Critical logical relation is " + edge["status"] + ": " + edge["id"],
                           "why_it_matters": edge["verification"]["rationale"],
                           "required_action": "Establish the missing prerequisite/support or obtain an author decision; rerun affected-edge verification.",
                           "verification_criterion": "The critical relation has source-grounded independent CONFIRMED status under the fixed ontology.",
                           "status": "NEEDS_AUTHOR" if edge["status"] == "UNCERTAIN" else "OPEN", "notes": [edge["id"]]})
    critical_sentences = {r["node_id"] for r in records if r.get("sentence_type") in CRITICAL_SENTENCE_TYPES}
    for group in graph["local_graphs"]:
        critical_ids = set(group["group"]["unit_ids"]) if group["group"]["scale"] == "SECTION" else critical_sentences
        for check in group["challenge"]["checks"]:
            if check["status"] == "PASS" or check["node_id"] not in critical_ids:
                continue
            issues.append({"id": "LC-" + str(int(sha256_value(check["node_id"])[:12], 16)),
                           "claim_id": None, "location": check["node_id"], "severity": "BLOCKER",
                           "category": "argument", "problem": "Critical attachment coverage: " + check["status"],
                           "why_it_matters": check["detail"],
                           "required_action": "Reconstruct the disputed local attachment against original evidence; repair the missing premise if the source is deficient.",
                           "verification_criterion": "A fresh independent coverage challenge confirms the necessary antecedents and source-grounded attachment.",
                           "status": "NEEDS_AUTHOR" if check["status"] == "UNCERTAIN" else "OPEN", "notes": [group["group"]["id"]]})
    if issues:
        merge_review_output(root, {"agent": "argument_reviewer", "reviewed_claims": [], "issues": issues, "review_notes": []})


def invalidate_relations(root: Path) -> None:
    """Mark only relations touching changed source subtrees/Claim occurrences stale.

    Full context fingerprints are checked again on reuse, catching changed scope,
    registries and contracts even when the source strings are unchanged.
    """
    from coherence import build_inventory
    path = root / ".review/logic/relations.json"
    tree_path = root / ".review/logic/structural_tree.json"
    if not path.is_file() or not tree_path.is_file():
        return
    graph = load_json(path)
    previous = load_json(tree_path)
    current = build_inventory(root)

    def signatures(units):
        by_id = {u["id"]: u for u in units}
        signatures = defaultdict(list)
        for u in units:
            if u["level"] not in {"PARAGRAPH", "SENTENCE"}:
                continue
            parent = u["id"]
            while parent:
                signatures[parent].append((u["id"], u["text"]))
                parent = by_id[parent]["parent_id"]
        return {uid: sha256_value(values) for uid, values in signatures.items()}

    old_hashes, new_hashes = signatures(previous["units"]), signatures(current["units"])
    changed = {uid for uid in set(old_hashes) | set(new_hashes) if old_hashes.get(uid) != new_hashes.get(uid)}
    changed_claims = {star["claim_id"] for star in graph["candidate_graph"]["claim_stars"]
                      if changed.intersection(star["source_unit_ids"])}
    stale = []
    for relation in graph["relations"]:
        if current["errors"] or {relation["source_id"], relation["target_id"]}.intersection(changed | changed_claims):
            relation["status"] = "STALE"
            stale.append(relation["id"])
    write_json(path, graph)
    append_event(root, "logic.relations_invalidated", {"stale_relation_ids": stale,
                 "changed_unit_ids": sorted(changed)}, artifact_refs=[{"path": ".review/logic/relations.json"}])
