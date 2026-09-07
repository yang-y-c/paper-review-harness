"""Recover whole local discourse structures, then challenge attachment coverage."""
from __future__ import annotations

from collections import defaultdict

from paper_review_lib import HarnessError, make_run_id, validate_with_schema, write_json, sha256_value


def local_groups(inventory: dict, records: list[dict]) -> list[dict]:
    reviewed = {r["node_id"] for r in records}
    groups = defaultdict(list)
    for unit in inventory["units"]:
        if unit["id"] not in reviewed or unit["level"] == "PAPER":
            continue
        owner = "PAPER" if unit["level"] == "SECTION" else unit["parent_id"]
        groups[(unit["level"], owner)].append(unit["id"])
    return [{"id": "LOCAL-" + sha256_value([scale, owner])[:16], "scale": scale,
             "parent_id": owner, "unit_ids": ids} for (scale, owner), ids in groups.items()]


def validate_local(root, group: dict, mapped: dict, challenge: dict | None = None) -> None:
    validate_with_schema(root, "local-logic-output.schema.json", mapped)
    ids = group["unit_ids"]
    if mapped["group_id"] != group["id"]:
        raise HarnessError("Local graph belongs to a different context group")
    nodes = {n["node_id"]: n for n in mapped["nodes"]}
    if len(nodes) != len(mapped["nodes"]) or set(nodes) != set(ids):
        raise HarnessError("Local graph must cover every supplied unit exactly once")
    adjacency = {}
    relation_pairs = {(r["source_id"], r["target_id"]) for r in mapped["relations"]}
    for node in mapped["nodes"]:
        primary = node["primary_parent_id"]
        parents = ([primary] if primary else []) + node["additional_parent_ids"]
        if len(parents) != len(set(parents)) or any(p not in nodes or p == node["node_id"] for p in parents):
            raise HarnessError("Invalid local antecedent attachment")
        if node["attachment_status"] == "ATTACHED" and primary is None:
            raise HarnessError("Attached unit requires a primary antecedent")
        if node["attachment_status"] == "ROOT" and parents:
            raise HarnessError("Root unit cannot also declare antecedents")
        if any((p, node["node_id"]) not in relation_pairs and (node["node_id"], p) not in relation_pairs for p in parents):
            raise HarnessError("Every attachment needs an explicit typed relation")
        adjacency[node["node_id"]] = parents
    visited, active = set(), set()

    def visit(uid):
        if uid in active:
            raise HarnessError("Local attachment graph contains a cycle")
        if uid in visited:
            return
        active.add(uid)
        for parent in adjacency[uid]:
            visit(parent)
        active.remove(uid)
        visited.add(uid)
    for uid in ids:
        visit(uid)
    if challenge is not None:
        validate_with_schema(root, "logic-coverage-output.schema.json", challenge)
        if challenge["group_id"] != group["id"]:
            raise HarnessError("Coverage challenge belongs to a different group")
        check_ids = [c["node_id"] for c in challenge["checks"]]
        if len(set(check_ids)) != len(check_ids) or set(check_ids) != set(ids):
            raise HarnessError("Coverage challenge must independently inspect every unit")
    rows = mapped["relations"] + (challenge["additional_relations"] if challenge else [])
    if len(rows) > 6 * len(ids):
        raise HarnessError("Local graph exceeds the sparse six-edges-per-unit budget")
    for row in rows:
        if row["source_id"] not in nodes or row["target_id"] not in nodes or row["source_id"] == row["target_id"]:
            raise HarnessError("Local relation endpoint lies outside the supplied context")


def recover(workflow, inventory: dict, records: list[dict], contexts: dict, previous: list[dict] | None = None) -> list[dict]:
    from logic_graph import ontology, _validate_edge_output, _independent_context
    root = workflow.root
    cache = {g["group"]["id"]: g for g in previous or []}
    result = []
    for group in local_groups(inventory, records):
        source_context = [contexts[uid] for uid in group["unit_ids"]]
        fingerprint = sha256_value({"group": group, "context": source_context, "ontology": ontology(root)})
        prior = cache.get(group["id"])
        if (prior and prior["fingerprint"] == fingerprint
                and all(c["status"] == "PASS" for c in prior["challenge"]["checks"])
                and all(n["attachment_status"] != "MISSING_PREMISE" for n in prior["mapped"]["nodes"])):
            result.append(prior)
            continue
        run_id = make_run_id("local-" + group["scale"].lower())
        mapped = workflow.runner.run("argument_reviewer", {
            "task": "LOCAL_LOGIC_RECOVERY", "group": group, "source_context": source_context,
            "ontology": ontology(root),
            "instructions": "Read the entire local context as one discourse structure. Recover the minimal sparse graph; do not enumerate sentence pairs. Every node needs a primary antecedent or an explicit ROOT/MISSING_PREMISE status. Preserve additional supporting parents for multi-premise inference, branches and returns after inserted explanations. Use only the supplied unit IDs and scale-specific ontology; quote exact source spans. For SECTION use all supplied section contracts together; for PARAGRAPH recover the section's paragraph skeleton; for SENTENCE recover this complete paragraph's attachment DAG.",
        }, run_id + "-map", output_schema="local-logic-output.schema.json")
        validate_local(root, group, mapped)
        challenge_input = {
            "task": "LOCAL_COVERAGE_CHALLENGE", "group": group,
            "source_context": [_independent_context(c) for c in source_context], "ontology": ontology(root),
            "attachments": [{k: v for k, v in n.items() if k != "rationale"} for n in mapped["nodes"]],
            "proposed_edges": [{k: r[k] for k in ("source_id", "target_id", "relation")} for r in mapped["relations"]],
            "instructions": "Independently inspect all local units for missing antecedents, wrong attachment, distant reattachment within the context, missing premises and additional parents. Do not read mapper rationale or confidence. Return a check per unit and source-grounded additional relations when needed. Structural root does not by itself mean argumentative independence.",
        }
        challenge = workflow.runner.run("challenger", challenge_input, run_id + "-challenge",
                                        output_schema="logic-coverage-output.schema.json")
        validate_local(root, group, mapped, challenge)
        for row in mapped["relations"] + challenge["additional_relations"]:
            candidate = {"id": "LOCAL", "source_id": row["source_id"], "target_id": row["target_id"],
                         "scale": group["scale"], "critical": group["scale"] in {"SECTION", "SENTENCE"}}
            proposal = {k: v for k, v in row.items() if k not in {"source_id", "target_id"}}
            proposal["candidate_id"] = "LOCAL"
            material = {"candidate": candidate, "source": contexts[row["source_id"]], "target": contexts[row["target_id"]]}
            _validate_edge_output(root, {"agent": "argument_reviewer", "relations": [proposal]}, [material], False)
        item = {"group": group, "fingerprint": fingerprint, "mapper_run_id": run_id + "-map",
                "challenge_run_id": run_id + "-challenge", "mapped": mapped, "challenge": challenge}
        write_json(root / ".review/runs" / run_id / "local_graph.json", item)
        write_json(root / ".review/runs" / run_id / "challenge_input.json", challenge_input)
        result.append(item)
    return result


def proposals(local_graphs: list[dict]) -> list[dict]:
    return [{**row, "scale": item["group"]["scale"], "mapper_run_id": run_id}
            for item in local_graphs
            for rows, run_id in [(item["mapped"]["relations"], item["mapper_run_id"]),
                                 (item["challenge"]["additional_relations"], item["challenge_run_id"])]
            for row in rows]
