from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from paper_review_lib import (
    CodexRunner,
    HarnessError,
    find_root,
    load_config,
    load_json,
    make_run_id,
    manuscript_snapshot,
    mark_hierarchy_not_applicable,
    merge_claim_map,
    merge_invariant_output,
    merge_layer_output,
    merge_review_output,
    record_revisions,
    record_verifications,
    reset_layer_ledgers,
    reverse_dependencies,
    route_all_claims,
    route_claim,
    status_summary,
    utc_now,
    update_state,
    write_json,
)
from validators import evaluate, render_text
from admission import load_request, require_admission, save_request
from provenance import append_event
from coherence import build_inventory, run_coherence, select_sections


RUN_PLANS = {
    "discuss": ["CLAIM_MAPPING", "ADVERSARIAL_REVIEW", "discussion packet"],
    "review": [
        "CLAIM_MAPPING",
        "INVARIANT_MAPPING",
        "MACRO_CONTRACT",
        "HIERARCHICAL_REVIEW",
        "GRANULAR_LANGUAGE_REVIEW",
        "INDEPENDENT_REVIEW",
        "ISSUE_SYNTHESIS",
        "ADVERSARIAL_REVIEW",
        "FINAL_INTEGRITY_AUDIT",
    ],
    "revise": ["REVISION"],
    "verify": ["TARGETED_VERIFICATION"],
    "optimize": [
        "INVARIANT_MAPPING",
        "MACRO_CONTRACT",
        "HIERARCHICAL_REVIEW",
        "GRANULAR_LANGUAGE_REVIEW",
        "REVISION",
        "TARGETED_VERIFICATION",
        "FINAL_INTEGRITY_AUDIT",
    ],
    "full": [
        "CLAIM_MAPPING",
        "INVARIANT_MAPPING",
        "MACRO_CONTRACT",
        "HIERARCHICAL_REVIEW",
        "GRANULAR_LANGUAGE_REVIEW",
        "INDEPENDENT_REVIEW",
        "ISSUE_SYNTHESIS",
        "ADVERSARIAL_REVIEW",
        "REVISION",
        "TARGETED_VERIFICATION",
        "FINAL_INTEGRITY_AUDIT",
        "DETERMINISTIC_VALIDATION",
    ],
}

# Preserve the compatibility phase label while exposing the actual new stages
# in dry-run plans. The runtime follows these stages in granular_control.
for _mode in ("review", "optimize", "full"):
    _index = RUN_PLANS[_mode].index("GRANULAR_LANGUAGE_REVIEW") + 1
    RUN_PLANS[_mode][_index:_index] = [
        "COHERENCE_PARAGRAPH", "LOCAL_LOGIC_RECOVERY", "LOCAL_COVERAGE_CHALLENGE",
        "REMOTE_RELATION_VERIFICATION", "ADAPTIVE_SENTENCE_RECOVERY", "COHERENCE_BOTTOM_UP",
    ]


class Workflow:
    def __init__(self, root: Path):
        self.root = root
        self.config = load_config(root)
        self.runner = CodexRunner(root, self.config)

    @property
    def claims_ledger(self) -> dict[str, Any]:
        return load_json(self.root / ".review" / "claims.json")

    @property
    def issues_ledger(self) -> dict[str, Any]:
        return load_json(self.root / ".review" / "issues.json")

    @property
    def invariant_ledgers(self) -> dict[str, dict[str, Any]]:
        return {
            "terminology": load_json(self.root / ".review" / "terminology.json"),
            "notation": load_json(self.root / ".review" / "notation.json"),
            "data": load_json(self.root / ".review" / "data_consistency.json"),
            "argument_graph": load_json(self.root / ".review" / "argument_graph.json"),
            "claim_consistency": load_json(self.root / ".review" / "claim_consistency.json"),
            "redundancy": load_json(self.root / ".review" / "redundancy.json"),
        }

    def map_claims(self, force: bool = False) -> dict[str, Any]:
        ledger = self.claims_ledger
        if ledger["claims"] and not force:
            return ledger
        update_state(self.root, phase="CLAIM_MAPPING")
        run_id = make_run_id("claim-map")
        update_state(self.root, active_run_id=run_id)
        assignment = {
            "task": "Map all material manuscript Claims and coverage into the required output schema.",
            "main_tex": self.config["main_tex"],
            "manuscript_roots": self.config["manuscript_roots"],
            "bibliography_files": self.config.get("bibliography_files", []),
            "existing_claims": ledger["claims"],
        }
        output = self.runner.run("claim_mapper", assignment, run_id)
        merged = merge_claim_map(self.root, output)
        update_state(self.root, last_run_id=run_id, active_run_id=None)
        return merged

    def map_invariants(self, force: bool = False) -> dict[str, dict[str, Any]]:
        ledgers = self.invariant_ledgers
        snapshot = manuscript_snapshot(self.root)
        if not force and all(
            ledger["status"] == "CURRENT"
            and ledger["source_snapshot"] is not None
            and ledger["source_snapshot"] == snapshot
            for ledger in ledgers.values()
        ):
            return ledgers
        claims = [
            claim for claim in self.map_claims()["claims"] if claim["status"] != "REMOVED"
        ]
        update_state(self.root, phase="INVARIANT_MAPPING")
        run_id = make_run_id("invariant-map")
        update_state(self.root, active_run_id=run_id)
        output = self.runner.run(
            "invariant_mapper",
            {
                "task": (
                    "Map cross-paper invariants into terminology, notation, data, argument, "
                    "Claim-consistency, and redundancy structures."
                ),
                "main_tex": self.config["main_tex"],
                "manuscript_roots": self.config["manuscript_roots"],
                "bibliography_files": self.config.get("bibliography_files", []),
                "claims": claims,
                "source_snapshot": snapshot,
                "constraints": [
                    "Map semantic facts; do not decide discipline-specific scientific truth.",
                    "Use exact locations and do not invent definitions, data, support, or intent.",
                    "Semantic similarity is diagnostic and never a universal hard failure.",
                ],
            },
            run_id,
        )
        merged = merge_invariant_output(self.root, output, run_id)
        update_state(self.root, last_run_id=run_id, active_run_id=None)
        return {
            "terminology": merged["terminology_registry"],
            "notation": merged["notation_registry"],
            "data": merged["data_registry"],
            "argument_graph": merged["argument_graph"],
            "claim_consistency": merged["claim_consistency"],
            "redundancy": merged["redundancy_diagnostics"],
        }

    def macro_control(self) -> dict[str, Any]:
        claims = [
            claim for claim in self.map_claims()["claims"] if claim["status"] != "REMOVED"
        ]
        invariants = self.map_invariants()
        update_state(self.root, phase="MACRO_CONTRACT")
        run_id = make_run_id("macro-contract")
        update_state(self.root, active_run_id=run_id)
        output = self.runner.run(
            "macro_architect",
            {
                "task": "Establish and audit the binding manuscript-wide contract before lower-layer review.",
                "main_tex": self.config["main_tex"],
                "manuscript_roots": self.config["manuscript_roots"],
                "claims": claims,
                "terminology_registry": invariants["terminology"]["payload"],
                "notation_registry": invariants["notation"]["payload"],
                "argument_graph": invariants["argument_graph"]["payload"],
                "claim_consistency": invariants["claim_consistency"]["payload"],
                "required_dimensions": [
                    "title",
                    "abstract",
                    "section and subsection names",
                    "conclusion",
                    "core logic chain",
                    "terminology",
                    "notation",
                ],
            },
            run_id,
        )
        ledger = merge_layer_output(self.root, "macro_architect", output, run_id)
        update_state(self.root, last_run_id=run_id, active_run_id=None)
        return ledger

    def hierarchy_control(self, granularity: str) -> dict[str, Any]:
        self.coherence_inventory = build_inventory(self.root)
        if self.coherence_inventory["errors"]:
            raise HarnessError("Source inventory blocked: " + "; ".join(self.coherence_inventory["errors"]))
        if granularity == "MACRO_ONLY":
            return mark_hierarchy_not_applicable(
                self.root, "The user explicitly selected macro-only review"
            )
        contract = load_json(self.root / ".review" / "global_contract.json")
        if contract["status"] != "CURRENT":
            raise HarnessError("Hierarchy review requires a CURRENT global contract")
        claims = [
            claim
            for claim in self.claims_ledger["claims"]
            if claim["status"] != "REMOVED"
        ]
        update_state(self.root, phase="HIERARCHICAL_REVIEW")
        run_id = make_run_id("hierarchy-review")
        update_state(self.root, active_run_id=run_id)
        output = self.runner.run(
            "hierarchy_reviewer",
            {
                "task": "Audit the section hierarchy top-down against the frozen global contract.",
                "main_tex": self.config["main_tex"],
                "granularity": granularity,
                "global_contract": contract["payload"]["contract"],
                "claims": claims,
                "source_sections": select_sections(self.coherence_inventory, granularity),
                "source_binding_rule": "Return exactly one hierarchy node per source_sections entry. Copy its id into source_unit_id. Preserve source parent relations; synthetic Front matter/Abstract nodes are valid sections.",
                "scope_rule": (
                    "Create SECTION nodes only."
                    if granularity == "SECTION"
                    else "Create SECTION and SUBSECTION nodes."
                ),
            },
            run_id,
        )
        ledger = merge_layer_output(self.root, "hierarchy_reviewer", output, run_id)
        update_state(self.root, last_run_id=run_id, active_run_id=None)
        return ledger

    def granular_control(self, granularity: str) -> dict[str, Any]:
        contract = load_json(self.root / ".review" / "global_contract.json")
        structure = load_json(self.root / ".review" / "structure.json")
        hierarchy_ready = structure["status"] == "CURRENT" or (
            granularity == "MACRO_ONLY" and structure["status"] == "NOT_APPLICABLE"
        )
        if contract["status"] != "CURRENT" or not hierarchy_ready:
            raise HarnessError("Language review requires valid global and hierarchy states")
        inventory = getattr(self, "coherence_inventory", None) or build_inventory(self.root)
        registry = run_coherence(self, inventory, granularity)
        if granularity in {"PARAGRAPH", "SENTENCE", "ADAPTIVE"}:
            # Keep the old language ledger as a compatibility view of the reviewed
            # contracts; all authoritative IDs/edges live in coherence_registry.
            sources = {u["id"]: u for u in inventory["units"]}
            parents = {n["source_unit_id"]: n["id"] for n in structure["payload"]["nodes"]}
            units = []
            for record in registry["records"]:
                source = sources[record["node_id"]]
                if source["level"] not in {"PARAGRAPH", "SENTENCE"}:
                    continue
                parent = source["parent_id"]
                if source["level"] == "SENTENCE":
                    parent = sources[parent]["parent_id"]
                units.append({
                    "id": f"U{len(units)+1:04d}", "parent_node_id": parents[parent],
                    "parent_contract_path": None, "level": source["level"],
                    "location": source["location"], "purpose_alignment": record["parent_alignment"],
                    "transition_in": record["transition_in"], "transition_out": record["transition_out"],
                    "terminology_alignment": ", ".join(record["terminology_ids"]),
                    "notation_alignment": ", ".join(record["notation_ids"]),
                    "humanizer_patterns": record["humanizer_patterns"],
                    "recommended_action": record["recommended_action"],
                    "status": "REVISE" if record["status"] == "GAP" else record["status"],
                })
            output = {"agent": "language_coherence_reviewer", "reviewed_claims": [],
                      "granularity": granularity, "humanizer_skill": "humanizer", "units": units,
                      "issues": [], "review_notes": ["Derived from coherence_registry.json; canonical unit IDs and findings are retained there."]}
            return merge_layer_output(self.root, "language_coherence_reviewer", output, registry["run_id"])
        update_state(self.root, phase="GRANULAR_LANGUAGE_REVIEW")
        run_id = make_run_id("language-coherence")
        update_state(self.root, active_run_id=run_id)
        output = self.runner.run(
            "language_coherence_reviewer",
            {
                "task": "Apply $humanizer and audit local language/coherence without editing.",
                "main_tex": self.config["main_tex"],
                "granularity": granularity,
                "humanizer_skill": self.config.get("language_review_skill", "humanizer"),
                "global_contract": contract["payload"]["contract"],
                "hierarchy": (
                    structure["payload"]["nodes"]
                    if structure["status"] == "CURRENT"
                    else []
                ),
                "adaptive_rule": (
                    "Review every paragraph; inspect sentences only in title, abstract, conclusion, "
                    "Core Claims, transitions, and flagged high-risk units."
                    if granularity == "ADAPTIVE"
                    else "Use exactly the selected granularity."
                ),
            },
            run_id,
        )
        ledger = merge_layer_output(
            self.root, "language_coherence_reviewer", output, run_id
        )
        update_state(self.root, last_run_id=run_id, active_run_id=None)
        return ledger

    def final_audit(self, granularity: str) -> dict[str, Any]:
        update_state(self.root, phase="FINAL_INTEGRITY_AUDIT")
        run_id = make_run_id("final-integrity-audit")
        update_state(self.root, active_run_id=run_id)
        output = self.runner.run(
            "final_integrity_auditor",
            {
                "task": "Independently audit the current manuscript across all selected layers.",
                "main_tex": self.config["main_tex"],
                "granularity": granularity,
                "global_contract": load_json(
                    self.root / ".review" / "global_contract.json"
                ),
                "hierarchy": load_json(self.root / ".review" / "structure.json"),
                "granular_review": load_json(
                    self.root / ".review" / "granular_review.json"
                ),
                "multiscale_coherence": load_json(self.root / ".review/coherence_registry.json"),
                "claims": [
                    claim
                    for claim in self.claims_ledger["claims"]
                    if claim["status"] != "REMOVED"
                ],
                "invariants": self.invariant_ledgers,
                "constraints": [
                    "Do not read revisions.json or reviser run outputs.",
                    "Do not edit files.",
                    "Lower-layer wording must remain aligned with every parent constraint.",
                ],
            },
            run_id,
        )
        ledger = merge_layer_output(
            self.root, "final_integrity_auditor", output, run_id
        )
        update_state(self.root, last_run_id=run_id, active_run_id=None)
        return ledger

    def _review_assignments(
        self, selected_claims: list[dict[str, Any]], include_challenger: bool
    ) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for claim in selected_claims:
            for agent in route_claim(claim):
                if agent == "challenger" and not include_challenger:
                    continue
                if agent != "challenger" and include_challenger:
                    continue
                grouped.setdefault(agent, []).append(claim)
        return {
            agent: {
                "task": "Independently review only the assigned Claims and relevant manuscript context.",
                "main_tex": self.config["main_tex"],
                "claims": claims,
                "argument_graph": load_json(
                    self.root / ".review" / "argument_graph.json"
                ),
                "claim_consistency": load_json(
                    self.root / ".review" / "claim_consistency.json"
                ),
                "data_consistency": load_json(
                    self.root / ".review" / "data_consistency.json"
                ),
                "constraints": [
                    "Do not read .review/runs from other agents.",
                    "Do not edit files.",
                    "Every finding must include an exact verification criterion.",
                ],
            }
            for agent, claims in grouped.items()
        }

    def review(
        self,
        selected_ids: list[str] | None = None,
        granularity: str = "ADAPTIVE",
        *,
        run_final_audit: bool = True,
    ) -> list[str]:
        claims = [
            claim for claim in self.map_claims()["claims"] if claim["status"] != "REMOVED"
        ]
        reset_layer_ledgers(self.root)
        self.map_invariants(force=True)
        self.macro_control()
        self.hierarchy_control(granularity)
        self.granular_control(granularity)
        if selected_ids:
            selected = [claim for claim in claims if claim["id"] in set(selected_ids)]
            missing = sorted(set(selected_ids) - {claim["id"] for claim in selected})
            if missing:
                raise HarnessError(f"Unknown Claim IDs: {', '.join(missing)}")
        else:
            selected = claims

        completed_agents: list[str] = ["invariant_mapper", "macro_architect"]
        if granularity != "MACRO_ONLY":
            completed_agents.append("hierarchy_reviewer")
        completed_agents.append("language_coherence_reviewer")
        update_state(self.root, phase="INDEPENDENT_REVIEW")
        review_id = make_run_id("review")
        update_state(self.root, active_run_id=review_id)
        regular = self.runner.run_parallel(
            self._review_assignments(selected, include_challenger=False), review_id
        )
        for agent in sorted(regular):
            merge_review_output(self.root, regular[agent])
            completed_agents.append(agent)
        update_state(
            self.root, phase="ISSUE_SYNTHESIS", last_run_id=review_id, active_run_id=None
        )

        adversarial = self._review_assignments(selected, include_challenger=True)
        if adversarial:
            update_state(self.root, phase="ADVERSARIAL_REVIEW")
            challenge_id = make_run_id("challenge")
            update_state(self.root, active_run_id=challenge_id)
            results = self.runner.run_parallel(adversarial, challenge_id)
            for agent in sorted(results):
                merge_review_output(self.root, results[agent])
                completed_agents.append(agent)
            update_state(
                self.root,
                phase="ISSUE_SYNTHESIS",
                last_run_id=challenge_id,
                active_run_id=None,
            )
        if run_final_audit:
            self.final_audit(granularity)
            completed_agents.append("final_integrity_auditor")
        return completed_agents

    def discuss(self, selected_ids: list[str] | None = None) -> dict[str, Any]:
        claims = self.map_claims()["claims"]
        if selected_ids:
            selected = [claim for claim in claims if claim["id"] in set(selected_ids)]
            missing = sorted(set(selected_ids) - {claim["id"] for claim in selected})
            if missing:
                raise HarnessError(f"Unknown Claim IDs: {', '.join(missing)}")
        else:
            selected = [
                claim
                for claim in claims
                if claim["centrality"] == "CORE" or claim["strength"] in {"STRONG", "EXTREME"}
            ]
        if not selected:
            raise HarnessError("No Claims are available for discussion")
        update_state(self.root, phase="ADVERSARIAL_REVIEW")
        assignments: dict[str, dict[str, Any]] = {
            "argument_reviewer": {
                "task": "Prepare the evidence and argument side of a discussion packet. Do not edit.",
                "main_tex": self.config["main_tex"],
                "claims": selected,
                "constraints": ["Do not read prior run outputs", "Return structured Issues only"],
            },
            "challenger": {
                "task": "Prepare the strongest challenge side of a discussion packet. Do not repair or edit.",
                "main_tex": self.config["main_tex"],
                "claims": selected,
                "constraints": ["Do not read prior run outputs", "Return structured Issues only"],
            },
        }
        run_id = make_run_id("discuss")
        update_state(self.root, active_run_id=run_id)
        outputs = self.runner.run_parallel(assignments, run_id)
        packets = []
        argument_issues = outputs.get("argument_reviewer", {}).get("issues", [])
        challenge_issues = outputs.get("challenger", {}).get("issues", [])
        for current_claim in selected:
            related_argument = [
                item for item in argument_issues if item.get("claim_id") == current_claim["id"]
            ]
            related_challenges = [
                item for item in challenge_issues if item.get("claim_id") == current_claim["id"]
            ]
            decision_points = [
                {
                    "problem": item["problem"],
                    "required_action": item["required_action"],
                    "verification_criterion": item["verification_criterion"],
                }
                for item in related_argument + related_challenges
            ]
            packets.append(
                {
                    "claim_id": current_claim["id"],
                    "statement": current_claim["statement"],
                    "scope": current_claim["scope"],
                    "evidence": current_claim["evidence"],
                    "argument_findings": related_argument,
                    "challenges": related_challenges,
                    "author_decision_required": any(
                        item.get("status") == "NEEDS_AUTHOR"
                        or item.get("severity") in {"BLOCKER", "MAJOR"}
                        for item in related_argument + related_challenges
                    ),
                    "decision_points": decision_points,
                }
            )
        packet = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": utc_now(),
            "claims": packets,
        }
        packet_path = self.root / ".review" / "runs" / run_id / "artifacts" / "discussion-packet.json"
        write_json(packet_path, packet)
        for agent in sorted(outputs):
            merge_review_output(self.root, outputs[agent])
        update_state(
            self.root, phase="ISSUE_SYNTHESIS", last_run_id=run_id, active_run_id=None
        )
        return {
            "agents": sorted(outputs),
            "packet": str(packet_path.relative_to(self.root)),
            "claims": len(packets),
        }

    def revise(self, severities: set[str] | None = None) -> int:
        issues = self.issues_ledger["issues"]
        targets = [
            issue
            for issue in issues
            if issue["status"] == "OPEN"
            and (severities is None or issue["severity"] in severities)
        ]
        if not targets:
            return 0
        claims_by_id = {claim["id"]: claim for claim in self.claims_ledger["claims"]}
        relevant_ids = {issue["claim_id"] for issue in targets if issue.get("claim_id")}
        relevant = [claims_by_id[claim_id] for claim_id in sorted(relevant_ids) if claim_id in claims_by_id]
        update_state(self.root, phase="REVISION")
        run_id = make_run_id("revision")
        update_state(self.root, active_run_id=run_id)
        assignment = {
            "task": "Address each assigned Issue with the smallest scientifically defensible manuscript change.",
            "main_tex": self.config["main_tex"],
            "manuscript_roots": self.config["manuscript_roots"],
            "issues": targets,
            "claims": relevant,
            "global_contract": load_json(self.root / ".review" / "global_contract.json"),
            "hierarchy": load_json(self.root / ".review" / "structure.json"),
            "multiscale_coherence": load_json(self.root / ".review/coherence_registry.json"),
            "selected_granularity": load_json(
                self.root / ".review" / "granular_review.json"
            ),
            "terminology_registry": load_json(self.root / ".review" / "terminology.json"),
            "notation_registry": load_json(self.root / ".review" / "notation.json"),
            "data_consistency": load_json(
                self.root / ".review" / "data_consistency.json"
            ),
            "argument_graph": load_json(self.root / ".review" / "argument_graph.json"),
            "claim_consistency": load_json(
                self.root / ".review" / "claim_consistency.json"
            ),
            "constraints": [
                "Do not set any Issue to RESOLVED.",
                "Use NEEDS_AUTHOR instead of inventing evidence or intent.",
                "Preserve unrelated user changes.",
            ],
        }
        output = self.runner.run("reviser", assignment, run_id)
        record_revisions(self.root, output, run_id)
        update_state(self.root, last_run_id=run_id, active_run_id=None)
        return len(output["revisions"])

    def verify(self) -> int:
        targets = [
            issue for issue in self.issues_ledger["issues"] if issue["status"] == "CLAIMED_FIXED"
        ]
        if not targets:
            return 0
        claims = [
            claim
            for claim in self.claims_ledger["claims"]
            if claim["status"] != "REMOVED"
        ]
        claim_by_id = {claim["id"]: claim for claim in claims}
        direct_ids = {issue["claim_id"] for issue in targets if issue.get("claim_id")}
        affected_ids = reverse_dependencies(claims, direct_ids)
        relevant_claims = [claim_by_id[claim_id] for claim_id in affected_ids if claim_id in claim_by_id]
        update_state(self.root, phase="TARGETED_VERIFICATION")
        run_id = make_run_id("verification")
        update_state(self.root, active_run_id=run_id)
        sanitized_issues = [
            {
                key: value
                for key, value in issue.items()
                if key
                in {
                    "id",
                    "claim_id",
                    "location",
                    "severity",
                    "category",
                    "problem",
                    "why_it_matters",
                    "required_action",
                    "verification_criterion",
                }
            }
            for issue in targets
        ]
        assignment = {
            "task": "Verify each original criterion against the current manuscript independently.",
            "main_tex": self.config["main_tex"],
            "issues": sanitized_issues,
            "claims": relevant_claims,
            "argument_graph": load_json(self.root / ".review" / "argument_graph.json"),
            "claim_consistency": load_json(
                self.root / ".review" / "claim_consistency.json"
            ),
            "data_consistency": load_json(
                self.root / ".review" / "data_consistency.json"
            ),
            "constraints": [
                "Do not read revisions.json or reviser run outputs.",
                "Do not infer what the reviser intended.",
                "Do not edit files.",
            ],
        }
        output = self.runner.run("verifier", assignment, run_id)
        record_verifications(self.root, output, run_id)
        update_state(self.root, last_run_id=run_id, active_run_id=None)
        return len(output["verifications"])

    def validate(self, final: bool) -> dict[str, Any]:
        update_state(self.root, phase="DETERMINISTIC_VALIDATION")
        report = evaluate(self.root, final=final)
        if report["passed"] and final:
            update_state(
                self.root,
                phase="ACCEPT",
                active=False,
                stop_gate_enabled=False,
                stop_gate_attempts=0,
                last_validation_passed=True,
                blocked_reason=None,
            )
        else:
            update_state(self.root, last_validation_passed=report["passed"])
        return report

    def optimize(self, granularity: str) -> dict[str, Any]:
        blockers = [
            issue
            for issue in self.issues_ledger["issues"]
            if issue["severity"] == "BLOCKER" and issue["status"] != "RESOLVED"
        ]
        if blockers:
            raise HarnessError("Optimization is blocked until unresolved BLOCKER Issues are addressed")
        claims = [
            claim for claim in self.map_claims()["claims"] if claim["status"] != "REMOVED"
        ]
        reset_layer_ledgers(self.root)
        self.map_invariants(force=True)
        self.macro_control()
        self.hierarchy_control(granularity)
        self.granular_control(granularity)
        targets = [
            claim
            for claim in claims
            if claim["centrality"] == "CORE" or "interpretive" in claim["type"]
        ]
        update_state(self.root, phase="INDEPENDENT_REVIEW")
        run_id = make_run_id("optimize-review")
        update_state(self.root, active_run_id=run_id)
        output = self.runner.run(
            "argument_reviewer",
            {
                "task": "Review argument architecture and communication for optimization; report semantic risks as higher severity.",
                "main_tex": self.config["main_tex"],
                "claims": targets,
                "argument_graph": load_json(
                    self.root / ".review" / "argument_graph.json"
                ),
                "claim_consistency": load_json(
                    self.root / ".review" / "claim_consistency.json"
                ),
                "data_consistency": load_json(
                    self.root / ".review" / "data_consistency.json"
                ),
                "constraints": ["Do not edit", "Do not read prior run outputs"],
            },
            run_id,
        )
        merge_review_output(self.root, output)
        update_state(self.root, last_run_id=run_id, active_run_id=None)
        revised = self.revise()
        verified = self.verify()
        if revised:
            self.map_claims(force=True)
            self.map_invariants(force=True)
            self.macro_control()
        self.hierarchy_control(granularity)
        self.granular_control(granularity)
        audit = self.final_audit(granularity)
        return {"revised": revised, "verified": verified, "final_audit": audit["status"]}

    def full(self, max_rounds: int, granularity: str) -> dict[str, Any]:
        update_state(
            self.root,
            active=True,
            stop_gate_enabled=True,
            stop_gate_attempts=0,
            blocked_reason=None,
        )
        self.map_claims()
        self.review(granularity=granularity)
        last_report: dict[str, Any] | None = None
        for round_id in range(1, max_rounds + 1):
            update_state(self.root, round=round_id)
            actionable = [
                issue for issue in self.issues_ledger["issues"] if issue["status"] == "OPEN"
            ]
            revised = self.revise() if actionable else 0
            self.verify()
            if revised:
                self.map_claims(force=True)
                self.map_invariants(force=True)
                self.macro_control()
            self.hierarchy_control(granularity)
            self.granular_control(granularity)
            self.final_audit(granularity)
            last_report = self.validate(final=True)
            if last_report["passed"]:
                return last_report
            remaining_actionable = [
                issue
                for issue in self.issues_ledger["issues"]
                if issue["status"] in {"OPEN", "CLAIMED_FIXED"}
            ]
            if not remaining_actionable:
                reason = "Validation failed, but no machine-actionable Issue remains; author input or configuration is required."
                update_state(
                    self.root,
                    phase="BLOCKED",
                    active=False,
                    stop_gate_enabled=False,
                    blocked_reason=reason,
                )
                raise HarnessError(reason + "\n" + render_text(last_report))
        reason = f"Review did not converge within {max_rounds} rounds; manuscript is not accepted."
        update_state(
            self.root,
            phase="BLOCKED",
            active=False,
            stop_gate_enabled=False,
            blocked_reason=reason,
        )
        raise HarnessError(reason + ("\n" + render_text(last_report) if last_report else ""))


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orchestrate the Codex Paper Review Harness")
    parser.add_argument("--root", type=Path, help="paper repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")

    route_parser = subparsers.add_parser("route")
    route_parser.add_argument("--claim", action="append", default=[])

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--final", action="store_true")

    activate_parser = subparsers.add_parser("activate")
    activate_parser.add_argument("--stop-gate", action="store_true")
    subparsers.add_parser("deactivate")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--mode", choices=sorted(RUN_PLANS), required=True)
    run_parser.add_argument("--request", required=True, help="canonical request ID")
    run_parser.add_argument("--claim", action="append", default=[])
    run_parser.add_argument("--max-rounds", type=int)
    run_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    active_request: dict[str, Any] | None = None
    try:
        root = find_root(args.root)
        if args.command == "status":
            _print_json(status_summary(root))
            return 0
        if args.command == "route":
            claims = load_json(root / ".review" / "claims.json")["claims"]
            if args.claim:
                claims = [claim for claim in claims if claim["id"] in set(args.claim)]
            _print_json(route_all_claims(claims))
            return 0
        if args.command == "activate":
            _print_json(
                update_state(
                    root,
                    active=True,
                    stop_gate_enabled=bool(args.stop_gate),
                    stop_gate_attempts=0,
                    blocked_reason=None,
                )
            )
            return 0
        if args.command == "deactivate":
            _print_json(
                update_state(root, active=False, stop_gate_enabled=False, stop_gate_attempts=0)
            )
            return 0
        workflow = Workflow(root)
        if args.command == "validate":
            report = workflow.validate(final=args.final)
            print(render_text(report))
            return 0 if report["passed"] else 1
        if args.command == "run":
            active_request = load_request(root, args.request)
            expected_intent = args.mode.upper()
            if active_request["intent"] != expected_intent:
                raise HarnessError(
                    f"Request intent {active_request['intent']} does not match mode {expected_intent}"
                )
            admission = require_admission(root, active_request)
            request_claims = active_request["scope"].get("claim_ids", [])
            selected_claims = args.claim or request_claims
            granularity = active_request["granularity"].get("level")
            if args.dry_run:
                _print_json(
                    {
                        "request_id": active_request["request_id"],
                        "admission": admission,
                        "mode": args.mode,
                        "plan": RUN_PLANS[args.mode],
                        "claims": selected_claims,
                        "granularity": granularity,
                        "max_rounds": args.max_rounds or workflow.config["max_rounds"],
                        "will_call_codex": False,
                    }
                )
                return 0
            active_request["status"] = "EXECUTING"
            save_request(root, active_request)
            append_event(
                root,
                "workflow.started",
                {"mode": args.mode, "plan": RUN_PLANS[args.mode]},
                session_id=active_request["source"].get("session_id"),
                turn_id=active_request["source"].get("turn_id"),
                request_id=active_request["request_id"],
                phase="INGEST",
                artifact_refs=[
                    {"path": f".review/requests/{active_request['request_id']}.json"},
                    {"path": f".review/admission/{active_request['request_id']}.json"},
                ],
            )
            update_state(
                root,
                active=True,
                active_request_id=active_request["request_id"],
                stop_gate_enabled=False,
                blocked_reason=None,
            )
            if args.mode == "discuss":
                result: Any = workflow.discuss(selected_claims or None)
            elif args.mode == "review":
                result = {
                    "agents": workflow.review(
                        selected_claims or None, granularity or "ADAPTIVE"
                    )
                }
            elif args.mode == "revise":
                result = {"revisions": workflow.revise()}
            elif args.mode == "verify":
                result = {"verifications": workflow.verify()}
            elif args.mode == "optimize":
                result = workflow.optimize(granularity or "ADAPTIVE")
            else:
                rounds = args.max_rounds or int(workflow.config["max_rounds"])
                if rounds < 1:
                    raise HarnessError("max_rounds must be at least 1")
                result = workflow.full(rounds, granularity or "ADAPTIVE")
            if args.mode != "full":
                update_state(
                    root,
                    active=False,
                    active_request_id=None,
                    last_request_id=active_request["request_id"],
                    active_run_id=None,
                    stop_gate_enabled=False,
                )
            active_request["status"] = "COMPLETED"
            save_request(root, active_request)
            append_event(
                root,
                "workflow.completed",
                result,
                session_id=active_request["source"].get("session_id"),
                turn_id=active_request["source"].get("turn_id"),
                request_id=active_request["request_id"],
                run_id=load_json(root / ".review" / "state.json").get("last_run_id"),
                phase=load_json(root / ".review" / "state.json").get("phase"),
                artifact_refs=[
                    {"path": ".review/state.json"},
                    {"path": ".review/claims.json"},
                    {"path": ".review/issues.json"},
                    {"path": ".review/revisions.json"},
                    {"path": ".review/verifications.json"},
                    {"path": ".review/terminology.json"},
                    {"path": ".review/notation.json"},
                    {"path": ".review/data_consistency.json"},
                    {"path": ".review/argument_graph.json"},
                    {"path": ".review/claim_consistency.json"},
                    {"path": ".review/redundancy.json"},
                    {"path": ".review/global_contract.json"},
                    {"path": ".review/structure.json"},
                    {"path": ".review/granular_review.json"},
                    {"path": ".review/final_audit.json"},
                    {"path": f".review/requests/{active_request['request_id']}.json"},
                    {"path": f".review/admission/{active_request['request_id']}.json"},
                ],
            )
            _print_json(result)
            return 0
    except HarnessError as exc:
        if active_request is not None:
            active_request["status"] = "BLOCKED"
            save_request(root, active_request)
            update_state(
                root,
                active=False,
                active_request_id=None,
                last_request_id=active_request["request_id"],
                active_run_id=None,
                phase="BLOCKED",
                blocked_reason=str(exc),
            )
            append_event(
                root,
                "workflow.blocked",
                {"error": str(exc)},
                session_id=active_request["source"].get("session_id"),
                turn_id=active_request["source"].get("turn_id"),
                request_id=active_request["request_id"],
                phase="BLOCKED",
                artifact_refs=[
                    {"path": ".review/state.json"},
                    {"path": f".review/requests/{active_request['request_id']}.json"},
                    {"path": f".review/admission/{active_request['request_id']}.json"},
                ],
            )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
