"""Deterministic role outputs for workflow tests, not scientific review evidence."""
from copy import deepcopy

from coherence import build_inventory
from paper_review_lib import load_json, write_json


class FixtureRunner:
    def __init__(self):
        self.calls = []

    def run(self, agent, assignment, run_id, **kwargs):
        self.calls.append(deepcopy(assignment))
        if assignment["task"] in {"LOCAL_LOGIC_RECOVERY", "LOCAL_COVERAGE_CHALLENGE"}:
            group = assignment["group"]
            ids = group["unit_ids"]
            if assignment["task"] == "LOCAL_COVERAGE_CHALLENGE":
                return {"agent": agent, "group_id": group["id"], "checks": [
                    {"node_id": uid, "status": "PASS", "detail": "Fixture: no missing antecedent."} for uid in ids], "additional_relations": []}
            contexts = {c["id"]: c for c in assignment["source_context"]}
            edges = []
            for source, target in zip(ids, ids[1:]):
                relation = next(r["name"] for r in assignment["ontology"]["relations"] if group["scale"] in r["allowed_scale"])
                row = {"source_id": source, "target_id": target, "relation": relation,
                       "rationale": "Fixture local attachment.", "confidence": 0.8}
                for endpoint, uid in [("source", source), ("target", target)]:
                    unit = contexts[uid]["source_units"][0]
                    row[endpoint + "_evidence"] = {"unit_id": unit["id"], "start": 0, "end": len(unit["text"]), "text": unit["text"]}
                edges.append(row)
            return {"agent": agent, "group_id": group["id"], "nodes": [
                {"node_id": uid, "primary_parent_id": ids[i-1] if i else None, "additional_parent_ids": [],
                 "attachment_status": "ATTACHED" if i else "ROOT", "rationale": "Fixture antecedent."}
                for i, uid in enumerate(ids)], "relations": edges}
        if assignment["task"].startswith("LOGIC_RELATION_"):
            verifying = assignment["task"] == "LOGIC_RELATION_VERIFICATION"
            rows = []
            for material in assignment["candidates"]:
                edge = material["candidate"]
                relation = material.get("proposed_relation") or next(
                    r["name"] for r in assignment["ontology"]["relations"] if edge["scale"] in r["allowed_scale"])
                if not verifying and "CORE_CLAIM_SUPPORT" in edge["reasons"]:
                    relation = "SUPPORTS"
                row = {"candidate_id": edge["id"], "relation": relation,
                       "rationale": "Fixture: original source establishes this relation.", "confidence": 0.8}
                for endpoint in ("source", "target"):
                    unit = material[endpoint]["source_units"][0]
                    row[endpoint + "_evidence"] = {"unit_id": unit["id"], "start": 0,
                                                   "end": len(unit["text"]), "text": unit["text"]}
                if verifying:
                    row.update(status="PASS", counterfactual="Fixture: removal leaves the stated prerequisite unavailable.")
                rows.append(row)
            return {"agent": agent, "verifications" if verifying else "relations": rows}
        stage = assignment["stage"]
        result = {"agent": agent, "stage": stage,
                  "humanizer_skill": "NOT_APPLICABLE" if stage == "BOTTOM_UP" else "humanizer",
                  "reviewed_claims": [], "records": [], "bottom_up": [], "issues": [], "review_notes": []}
        known = {r["node_id"] for r in assignment["parent_contracts"]}
        if stage == "BOTTOM_UP":
            # A fresh bottom-up fixture reports all actual direct reviewed children.
            all_units = {u["id"]: u for u in assignment["source_context"]}
            # Selected sentences also occur in parent contracts; reconstruct their
            # parent relation from the scheduler (source_context excludes them here).
            for route in assignment["scheduler"]["routes"]:
                if route["node_id"] in known:
                    all_units[route["node_id"]] = {"id": route["node_id"], "parent_id": route["paragraph_id"]}
            for unit in assignment["target_units"]:
                result["bottom_up"].append({
                    "node_id": unit["id"],
                    "child_ids": [u["id"] for u in all_units.values() if u["parent_id"] == unit["id"] and u["id"] in known],
                    "established_output": "Fixture: the child outputs establish the stated result.",
                    "supports_parent": "Fixture: supports the parent purpose under stated assumptions.",
                    "evidence": [unit["location"]], "status": "PASS", "issue_ids": [],
                })
        else:
            for unit in assignment["target_units"]:
                result["records"].append({
                    "node_id": unit["id"], "purpose": "Fixture: establish the local result",
                    "rhetorical_role": "DERIVATION", "inputs": ["Stated assumptions"],
                    "outputs": ["Bounded local result"], "claim_ids": ["C01"] if "bound" in unit["text"] or len(assignment["target_units"]) == 1 else [], "terminology_ids": [],
                    "main_claim": "Bounded local result", "topic": "Fixture topic",
                    "sentence_type": "INFERENCE" if stage == "SENTENCE" else "NOT_APPLICABLE",
                    "notation_ids": [], "argument_node_ids": [], "premises": ["Stated assumptions"],
                    "inference": "Fixture deduction", "conclusion": "Fixture bounded result",
                    "discourse_relation": "CONSEQUENCE", "transition_in": "Uses previous assumptions",
                    "transition_out": "Provides the next premise", "parent_alignment": "Serves the parent purpose",
                    "risk_signals": [], "status": "PASS", "issue_ids": [],
                    "humanizer_patterns": [], "recommended_action": "",
                })
        return result


def bind_hierarchy(root, mode="ADAPTIVE"):
    from coherence import select_sections
    inventory = build_inventory(root)
    structure = load_json(root / ".review/structure.json")
    template = deepcopy(structure["payload"]["nodes"][0])
    selected = select_sections(inventory, mode)
    identifiers = {u["id"]: f"N{i+1:03d}" for i, u in enumerate(selected)}
    structure["payload"]["nodes"] = [
        {**deepcopy(template), "id": identifiers[u["id"]], "source_unit_id": u["id"],
         "parent_id": identifiers.get(u["parent_id"]), "heading": u["text"], "location": u["location"],
         "level": "SECTION" if u["parent_id"] == "PAPER" else "SUBSECTION"}
        for u in selected
    ]
    structure["payload"]["granularity"] = mode
    write_json(root / ".review/structure.json", structure)
    return inventory
