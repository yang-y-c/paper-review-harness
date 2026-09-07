from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "scripts"))

from paper_review_lib import (  # noqa: E402
    CodexRunner,
    HarnessError,
    bundled_schema,
    load_json,
    merge_invariant_output,
    merge_review_output,
    merge_layer_output,
    record_revisions,
    record_verifications,
    reverse_dependencies,
    route_claim,
    write_json,
)
from admission import canonicalize_request, confirm_request, evaluate_admission  # noqa: E402
from control import monitor_snapshot  # noqa: E402
from review import RUN_PLANS  # noqa: E402
from provenance import (  # noqa: E402
    append_event,
    materialize_conversation,
    redact,
    verify_event_chain,
)
from validators import evaluate  # noqa: E402


def claim(
    claim_id: str,
    *,
    types: list[str] | None = None,
    centrality: str = "SUPPORTING",
    strength: str = "NORMAL",
    dependencies: list[str] | None = None,
    evidence: list[str] | None = None,
) -> dict:
    return {
        "id": claim_id,
        "statement": f"Statement for {claim_id}",
        "locations": ["Sec. 1"],
        "type": types or ["theoretical"],
        "centrality": centrality,
        "strength": strength,
        "scope": "under the stated assumptions",
        "dependencies": dependencies or [],
        "evidence": evidence if evidence is not None else ["Theorem 1"],
        "strong_terms": ["global"] if strength != "NORMAL" else [],
        "status": "MAPPED",
        "adversarial_status": "PENDING" if strength != "NORMAL" else "NOT_REQUIRED",
        "reviewed_by": [],
        "notes": [],
    }


def review_output(issue_id: str = "TH-001", severity: str = "BLOCKER") -> dict:
    return {
        "agent": "theory_reviewer",
        "reviewed_claims": ["C01"],
        "issues": [
            {
                "id": issue_id,
                "claim_id": "C01",
                "location": "Sec. 1, Theorem 1",
                "severity": severity,
                "category": "theory",
                "problem": "Sufficiency is asserted but not proved.",
                "why_it_matters": "The Core Claim is not established.",
                "required_action": "Prove sufficiency or narrow the Claim.",
                "verification_criterion": "The text proves both directions or removes the equivalence claim.",
                "status": "OPEN",
                "notes": [],
            }
        ],
        "review_notes": [],
    }


def request_draft(intent: str = "REVIEW", allow_edits: bool = False) -> dict:
    return {
        "intent": intent,
        "scope": {"claim_ids": [], "issue_ids": [], "sections": [], "files": []},
        "objectives": [
            {
                "id": "OBJ-01",
                "description": "Evaluate the manuscript against its stated claims.",
                "priority": "MUST",
                "success_criteria": ["All material findings are recorded with verification criteria."],
            }
        ],
        "constraints": {
            "target_venue": None,
            "language": "English",
            "allowed_changes": ["manuscript prose"] if allow_edits else [],
            "forbidden_changes": [] if allow_edits else ["manuscript prose"],
            "preserve": ["scientific meaning"],
            "evidence_policy": "NO_FABRICATION",
            "allow_manuscript_edits": allow_edits,
            "max_rounds": 3,
        },
        "granularity": {
            "level": "ADAPTIVE",
            "explicit": True,
            "rationale": "Selected for the test request.",
            "user_question": None,
        },
        "assumptions": [],
        "missing_information": [],
        "confirmation_required": allow_edits,
        "normalization_notes": [],
    }


def invariant_output() -> dict:
    return {
        "agent": "invariant_mapper",
        "reviewed_claims": ["C01"],
        "terminology_registry": {
            "concepts": [
                {
                    "id": "T01",
                    "canonical_term": "test result",
                    "meaning": "the stated result",
                    "aliases": [],
                    "forbidden_variants": [],
                    "definition_locations": ["Abstract"],
                    "occurrences": [
                        {
                            "location": "Sec. 1",
                            "surface_form": "test result",
                            "usage": "CANONICAL",
                            "meaning_alignment": "MATCH",
                        }
                    ],
                    "status": "CLEAR",
                    "notes": [],
                }
            ],
            "unregistered_critical_terms": [],
        },
        "notation_registry": {
            "symbols": [],
            "unregistered_critical_symbols": [],
        },
        "data_registry": {
            "records": [],
            "unmapped_material_values": [],
        },
        "argument_graph": {
            "nodes": [
                {
                    "id": "AG-N001",
                    "type": "EVIDENCE",
                    "statement": "Theorem 1",
                    "claim_id": None,
                    "data_ids": [],
                    "locations": ["Sec. 1, Theorem 1"],
                },
                {
                    "id": "AG-N002",
                    "type": "CLAIM",
                    "statement": "Statement for C01",
                    "claim_id": "C01",
                    "data_ids": [],
                    "locations": ["Sec. 1"],
                },
            ],
            "edges": [
                {
                    "id": "AG-E001",
                    "source_id": "AG-N001",
                    "target_id": "AG-N002",
                    "relation": "SUPPORTS",
                    "locations": ["Sec. 1, Theorem 1"],
                }
            ],
        },
        "claim_consistency": {
            "records": [
                {
                    "claim_id": "C01",
                    "canonical_statement": "Statement for C01",
                    "canonical_scope": "under the stated assumptions",
                    "support_node_ids": ["AG-N001"],
                    "body_locations": ["Sec. 1"],
                    "occurrences": [
                        {
                            "id": "OCC-001",
                            "location": "Sec. 1",
                            "role": "BODY",
                            "statement": "Statement for C01",
                            "scope_relation": "SAME",
                            "synchronized": True,
                        }
                    ],
                    "status": "PASS",
                    "notes": [],
                }
            ],
            "unmapped_claim_occurrences": [],
        },
        "redundancy_diagnostics": {
            "exact_duplicates": [],
            "contribution_duplicates": [],
            "semantic_similarity_notes": [],
        },
        "issues": [],
        "review_notes": [],
    }


def macro_output() -> dict:
    return {
        "agent": "macro_architect",
        "reviewed_claims": ["C01"],
        "contract": {
            "theme": {
                "topic": "Test topic",
                "research_question": "Does the stated result follow?",
                "scope": "Stated assumptions",
                "core_contribution": "A test result",
                "exclusions": [],
                "anchors": ["test result"],
            },
            "title": {
                "current": "Test",
                "recommended": "Test result under stated assumptions",
                "topic_alignment": "Direct",
                "specificity": "Names the result and scope",
                "style_pattern": "Sentence case noun phrase",
                "status": "PASS",
            },
            "abstract": {
                "problem": "Test problem",
                "gap": "Test gap",
                "objective": "Test objective",
                "method": "Test method",
                "evidence": "Theorem 1",
                "results": "Test result",
                "contribution": "Test contribution",
                "scope_limits": "Stated assumptions",
                "status": "PASS",
            },
            "section_naming": {
                "policy": "Sentence case, concrete purpose",
                "headings": [
                    {
                        "location": "Sec. 1",
                        "current": "Method",
                        "recommended": "Method",
                        "purpose": "State the method",
                        "status": "PASS",
                    }
                ],
            },
            "conclusion": {
                "answers_research_question": "Yes under the assumptions",
                "supported_findings": ["Test result"],
                "scope_consistency": "Consistent",
                "limitations": "Stated assumptions",
                "new_claims": [],
                "status": "PASS",
            },
            "logic_chain": [
                {"id": "L01", "role": "PROBLEM", "statement": "Problem", "depends_on": [], "claim_ids": [], "evidence_locations": ["Sec. 1"], "status": "SUPPORTED"},
                {"id": "L02", "role": "METHOD", "statement": "Method", "depends_on": ["L01"], "claim_ids": [], "evidence_locations": ["Sec. 1"], "status": "SUPPORTED"},
                {"id": "L03", "role": "CONCLUSION", "statement": "Result", "depends_on": ["L02"], "claim_ids": ["C01"], "evidence_locations": ["Conclusion"], "status": "SUPPORTED"},
            ],
            "terminology": [
                {"concept_id": "T01", "canonical": "test result", "meaning": "the stated result", "allowed_variants": [], "forbidden_variants": [], "first_definition": "Abstract"}
            ],
            "notation": [],
            "drift_controls": [
                {"id": "D01", "rule": "Preserve stated assumptions", "applies_to": ["all sections"], "verification": "Every result retains its qualifier"}
            ],
        },
        "issues": [],
        "review_notes": [],
    }


def hierarchy_output() -> dict:
    return {
        "agent": "hierarchy_reviewer",
        "reviewed_claims": ["C01"],
        "granularity": "ADAPTIVE",
        "nodes": [
            {
                "id": "N001",
                "parent_id": None,
                "level": "SECTION",
                "heading": "Method",
                "location": "Sec. 1",
                "purpose": "Establish the method",
                "input_from_previous": "Problem statement",
                "output_to_next": "Result conditions",
                "theme_anchors": ["test result"],
                "claim_ids": ["C01"],
                "transition_in": "Explicit",
                "transition_out": "Explicit",
                "status": "PASS",
            }
        ],
        "issues": [],
        "review_notes": [],
    }


def language_output() -> dict:
    return {
        "agent": "language_coherence_reviewer",
        "reviewed_claims": ["C01"],
        "granularity": "ADAPTIVE",
        "humanizer_skill": "humanizer",
        "units": [
            {
                "id": "U0001",
                "parent_node_id": "N001",
                "parent_contract_path": None,
                "level": "HEADING",
                "location": "Sec. 1 heading",
                "purpose_alignment": "Aligned",
                "transition_in": "Clear",
                "transition_out": "Clear",
                "terminology_alignment": "Canonical",
                "notation_alignment": "No notation",
                "humanizer_patterns": [],
                "recommended_action": "None",
                "status": "PASS",
            }
        ],
        "issues": [],
        "review_notes": [],
    }


def final_audit_output() -> dict:
    dimensions = [
        "TITLE", "ABSTRACT", "SECTION_NAMES", "CONCLUSION", "LOGIC_CHAIN",
        "TERMINOLOGY", "NOTATION", "ARGUMENT_GRAPH", "DATA_CONSISTENCY",
        "CLAIM_CONSISTENCY", "REDUNDANCY", "HIERARCHY", "LANGUAGE",
        "CLAIM_SCOPE",
    ]
    return {
        "agent": "final_integrity_auditor",
        "reviewed_claims": ["C01"],
        "result": "PASS",
        "checks": [
            {"dimension": item, "status": "PASS", "evidence": ["manuscript/main.tex"], "detail": "Aligned"}
            for item in dimensions
        ],
        "issues": [],
        "review_notes": [],
    }


class HarnessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "paper"
        shutil.copytree(HARNESS_ROOT / ".review", self.root / ".review")
        (self.root / "manuscript").mkdir(parents=True)
        (self.root / "manuscript" / "main.tex").write_text(
            "\\documentclass{article}\\begin{document}Test\\end{document}\n",
            encoding="utf-8",
        )
        claims = {
            "schema_version": 1,
            "coverage": {
                "abstract_mapped": True,
                "conclusion_mapped": True,
                "abstract_numerical_claims_mapped": True,
                "scanned_sections": ["Abstract", "Sec. 1", "Conclusion"],
                "unmapped_sections": [],
            },
            "claims": [claim("C01", centrality="CORE")],
        }
        write_json(self.root / ".review" / "claims.json", claims)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dynamic_routing(self) -> None:
        routed = route_claim(
            claim(
                "C07",
                types=["theoretical", "algorithmic"],
                centrality="CORE",
                strength="EXTREME",
            )
        )
        self.assertEqual(
            routed,
            ["algorithm_reviewer", "argument_reviewer", "challenger", "theory_reviewer"],
        )

    def test_reverse_dependencies_are_transitive(self) -> None:
        claims = [
            claim("C01"),
            claim("C02", dependencies=["C01"]),
            claim("C03", dependencies=["C02"]),
        ]
        self.assertEqual(reverse_dependencies(claims, ["C01"]), ["C01", "C02", "C03"])

    def test_reviser_cannot_resolve_issue(self) -> None:
        merge_review_output(self.root, review_output())
        invalid = {
            "agent": "reviser",
            "revisions": [
                {
                    "issue_id": "TH-001",
                    "revision_status": "RESOLVED",
                    "changed_files": ["manuscript/main.tex"],
                    "changed_locations": ["Sec. 1"],
                    "affected_claims": ["C01"],
                    "change_types": ["proof"],
                    "resolution_summary": "Claimed resolution",
                }
            ],
        }
        with self.assertRaises(HarnessError):
            record_revisions(self.root, invalid, "test-run")

    def test_only_verifier_pass_resolves_issue(self) -> None:
        merge_review_output(self.root, review_output())
        revision = {
            "agent": "reviser",
            "revisions": [
                {
                    "issue_id": "TH-001",
                    "revision_status": "CLAIMED_FIXED",
                    "changed_files": ["manuscript/main.tex"],
                    "changed_locations": ["Sec. 1"],
                    "affected_claims": ["C01"],
                    "change_types": ["proof"],
                    "resolution_summary": "Added the missing direction using existing premises.",
                }
            ],
        }
        record_revisions(self.root, revision, "revision-run")
        after_revision = json.loads(
            (self.root / ".review" / "issues.json").read_text(encoding="utf-8")
        )
        self.assertEqual(after_revision["issues"][0]["status"], "CLAIMED_FIXED")
        verification = {
            "agent": "verifier",
            "verifications": [
                {
                    "issue_id": "TH-001",
                    "result": "PASS",
                    "rationale": "Both directions are now explicit in the current manuscript.",
                    "evidence_locations": ["Sec. 1, Theorem 1"],
                    "new_issues": [],
                }
            ],
        }
        record_verifications(self.root, verification, "verification-run")
        after_verification = json.loads(
            (self.root / ".review" / "issues.json").read_text(encoding="utf-8")
        )
        self.assertEqual(after_verification["issues"][0]["status"], "RESOLVED")
        self.assertEqual(after_verification["issues"][0]["verification_status"], "PASS")

    def test_verifier_rejects_unrevised_issue(self) -> None:
        merge_review_output(self.root, review_output())
        verification = {
            "agent": "verifier",
            "verifications": [
                {
                    "issue_id": "TH-001",
                    "result": "PASS",
                    "rationale": "Unsupported attempt.",
                    "evidence_locations": ["Sec. 1"],
                    "new_issues": [],
                }
            ],
        }
        with self.assertRaises(HarnessError):
            record_verifications(self.root, verification, "verification-run")

    def test_validator_blocks_unresolved_blocker(self) -> None:
        merge_review_output(self.root, review_output())
        report = evaluate(self.root, final=False)
        failures = {item["id"] for item in report["checks"] if item["status"] == "FAIL"}
        self.assertIn("G04", failures)

    def test_validator_blocks_unresolved_core_major(self) -> None:
        merge_review_output(self.root, review_output(severity="MAJOR"))
        report = evaluate(self.root, final=False)
        failures = {item["id"] for item in report["checks"] if item["status"] == "FAIL"}
        self.assertIn("G05", failures)

    def test_write_hook_blocks_manuscript_outside_revision(self) -> None:
        state_path = self.root / ".review" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update({"active": True, "phase": "INDEPENDENT_REVIEW"})
        write_json(state_path, state)
        payload = {
            "cwd": str(self.root),
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: manuscript/main.tex\n@@\n-Test\n+Changed\n*** End Patch"
            },
        }
        completed = subprocess.run(
            [sys.executable, str(HARNESS_ROOT / ".codex" / "hooks" / "write_policy.py")],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        response = json.loads(completed.stdout)
        self.assertEqual(
            response["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_write_hook_blocks_absolute_manuscript_path(self) -> None:
        state_path = self.root / ".review" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update({"active": True, "phase": "INDEPENDENT_REVIEW"})
        write_json(state_path, state)
        manuscript = self.root / "manuscript" / "main.tex"
        payload = {
            "cwd": str(self.root),
            "tool_input": {
                "command": f"*** Begin Patch\n*** Update File: {manuscript}\n@@\n-Test\n+Changed\n*** End Patch"
            },
        }
        completed = subprocess.run(
            [sys.executable, str(HARNESS_ROOT / ".codex" / "hooks" / "write_policy.py")],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(json.loads(completed.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_validator_requires_dynamic_reviewer_coverage(self) -> None:
        report = evaluate(self.root, final=False)
        failures = {item["id"] for item in report["checks"] if item["status"] == "FAIL"}
        self.assertIn("G11", failures)

    def test_final_validator_passes_complete_machine_state(self) -> None:
        claims_path = self.root / ".review" / "claims.json"
        claims = json.loads(claims_path.read_text(encoding="utf-8"))
        claims["claims"][0]["reviewed_by"] = ["argument_reviewer", "theory_reviewer"]
        write_json(claims_path, claims)
        config_path = self.root / ".review" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["build_command"] = [sys.executable, "-c", "print('clean build')"]
        write_json(config_path, config)
        merge_invariant_output(self.root, invariant_output(), "invariant-run")
        merge_layer_output(self.root, "macro_architect", macro_output(), "macro-run")
        merge_layer_output(
            self.root, "hierarchy_reviewer", hierarchy_output(), "hierarchy-run"
        )
        merge_layer_output(
            self.root,
            "language_coherence_reviewer",
            language_output(),
            "language-run",
        )
        merge_layer_output(
            self.root,
            "final_integrity_auditor",
            final_audit_output(),
            "audit-run",
        )
        report = evaluate(self.root, final=True)
        # Legacy status flags alone must no longer certify multiscale coverage.
        self.assertFalse(report["passed"])
        self.assertIn("G24", {c["id"] for c in report["checks"] if c["status"] == "FAIL"})
        from coherence import run_coherence
        from coherence_fixtures import FixtureRunner, bind_hierarchy
        from review import Workflow
        workflow = Workflow(self.root)
        workflow.runner = FixtureRunner()
        run_coherence(workflow, bind_hierarchy(self.root), "ADAPTIVE")
        report = evaluate(self.root, final=True)
        self.assertTrue(report["passed"], report)

    def test_layered_plan_is_top_down_and_ends_with_final_audit(self) -> None:
        plan = RUN_PLANS["review"]
        self.assertLess(plan.index("CLAIM_MAPPING"), plan.index("INVARIANT_MAPPING"))
        self.assertLess(plan.index("INVARIANT_MAPPING"), plan.index("MACRO_CONTRACT"))
        self.assertLess(plan.index("MACRO_CONTRACT"), plan.index("HIERARCHICAL_REVIEW"))
        self.assertLess(
            plan.index("HIERARCHICAL_REVIEW"), plan.index("GRANULAR_LANGUAGE_REVIEW")
        )
        self.assertEqual(plan[-1], "FINAL_INTEGRITY_AUDIT")

    def test_layer_outputs_are_schema_validated_and_solidified(self) -> None:
        invariants = merge_invariant_output(
            self.root, invariant_output(), "invariant_mapper-run"
        )
        self.assertEqual(invariants["claim_consistency"]["status"], "CURRENT")
        for agent, output, file_name in [
            ("macro_architect", macro_output(), "global_contract.json"),
            ("hierarchy_reviewer", hierarchy_output(), "structure.json"),
            ("language_coherence_reviewer", language_output(), "granular_review.json"),
            ("final_integrity_auditor", final_audit_output(), "final_audit.json"),
        ]:
            ledger = merge_layer_output(self.root, agent, output, f"{agent}-run")
            self.assertNotEqual(ledger["status"], "NOT_RUN")
            self.assertTrue((self.root / ".review" / file_name).is_file())

    def test_layered_request_without_explicit_granularity_is_denied(self) -> None:
        shutil.copytree(HARNESS_ROOT / ".codex" / "agents", self.root / ".codex" / "agents")
        draft = request_draft()
        draft["granularity"] = {
            "level": None,
            "explicit": False,
            "rationale": "Not specified",
            "user_question": "Choose review granularity.",
        }
        request = canonicalize_request(
            self.root, draft, "Review this paper.", session_id="granularity-session"
        )
        report = evaluate_admission(self.root, request)
        failed = {item["id"] for item in report["checks"] if item["status"] == "FAIL"}
        self.assertIn("A14", failed)

    def test_language_agent_requires_humanizer(self) -> None:
        profile = (
            HARNESS_ROOT / ".codex" / "agents" / "language_coherence_reviewer.toml"
        ).read_text(encoding="utf-8")
        self.assertIn("$humanizer", profile)
        self.assertIn('humanizer_skill": {"const": "humanizer"}', (
            HARNESS_ROOT / ".review" / "schemas" / "language-output.schema.json"
        ).read_text(encoding="utf-8"))

    def test_final_audit_cannot_claim_pass_with_a_failed_dimension(self) -> None:
        output = final_audit_output()
        output["checks"][0]["status"] = "FAIL"
        with self.assertRaises(HarnessError):
            merge_layer_output(
                self.root, "final_integrity_auditor", output, "invalid-audit"
            )

    def test_data_consistency_is_a_hard_gate(self) -> None:
        output = invariant_output()
        output["data_registry"]["records"] = [
            {
                "id": "DTA-01",
                "label": "reported accuracy",
                "kind": "RESULT",
                "canonical_value": "0.91",
                "unit": None,
                "conditions": "test split",
                "source_locations": ["Table 1"],
                "claim_ids": ["C01"],
                "occurrences": [
                    {
                        "location": "Conclusion",
                        "value": "0.93",
                        "unit": None,
                        "conditions": "test split",
                        "relation": "CONFLICT",
                        "justification": None,
                    }
                ],
                "status": "CONFLICT",
                "notes": [],
            }
        ]
        output["issues"] = [
            {
                "id": "CO-101",
                "claim_id": "C01",
                "location": "Table 1 and Conclusion",
                "severity": "MAJOR",
                "category": "consistency",
                "problem": "The same reported result has two values.",
                "why_it_matters": "The evidence and conclusion disagree.",
                "required_action": "Reconcile the value or its conditions.",
                "verification_criterion": "All DTA-01 occurrences agree under the same conditions.",
                "status": "OPEN",
                "notes": [],
            }
        ]
        merge_invariant_output(self.root, output, "data-conflict")
        report = evaluate(self.root, final=False)
        failures = {item["id"] for item in report["checks"] if item["status"] == "FAIL"}
        self.assertIn("G20", failures)

    def test_terminology_collision_is_a_hard_gate(self) -> None:
        output = invariant_output()
        duplicate = dict(output["terminology_registry"]["concepts"][0])
        duplicate.update(
            {
                "id": "T02",
                "meaning": "a different concept",
                "definition_locations": ["Sec. 2"],
                "occurrences": [
                    {
                        "location": "Sec. 2",
                        "surface_form": "test result",
                        "usage": "CANONICAL",
                        "meaning_alignment": "CONFLICT",
                    }
                ],
                "status": "CONFLICT",
            }
        )
        output["terminology_registry"]["concepts"].append(duplicate)
        output["issues"] = [
            {
                "id": "CO-103",
                "claim_id": "C01",
                "location": "Sec. 1 and Sec. 2",
                "severity": "MAJOR",
                "category": "consistency",
                "problem": "One canonical term names two concepts.",
                "why_it_matters": "The manuscript cannot be interpreted consistently.",
                "required_action": "Disambiguate the concepts and freeze their terms.",
                "verification_criterion": "Every canonical term has one concept owner.",
                "status": "OPEN",
                "notes": [],
            }
        ]
        merge_invariant_output(self.root, output, "term-conflict")
        report = evaluate(self.root, final=False)
        failures = {item["id"] for item in report["checks"] if item["status"] == "FAIL"}
        self.assertIn("G17", failures)

    def test_orphan_core_claim_is_an_argument_gate_failure(self) -> None:
        output = invariant_output()
        output["argument_graph"]["nodes"] = [
            output["argument_graph"]["nodes"][1]
        ]
        output["argument_graph"]["edges"] = []
        output["claim_consistency"]["records"][0]["support_node_ids"] = []
        output["claim_consistency"]["records"][0]["status"] = "FAIL"
        output["issues"] = [
            {
                "id": "AR-103",
                "claim_id": "C01",
                "location": "Sec. 1",
                "severity": "BLOCKER",
                "category": "argument",
                "problem": "The CORE Claim has no traceable support node.",
                "why_it_matters": "The central conclusion is structurally unsupported.",
                "required_action": "Link existing evidence or narrow the Claim.",
                "verification_criterion": "C01 has a support path from evidence or premises.",
                "status": "OPEN",
                "notes": [],
            }
        ]
        merge_invariant_output(self.root, output, "orphan-claim")
        report = evaluate(self.root, final=False)
        failures = {item["id"] for item in report["checks"] if item["status"] == "FAIL"}
        self.assertIn("G19", failures)

    def test_notation_type_conflict_is_a_hard_gate(self) -> None:
        output = invariant_output()
        output["notation_registry"]["symbols"] = [
            {
                "id": "N01",
                "symbol": "q",
                "canonical_form": "q",
                "meaning": "state vector",
                "domain_or_type": "vector",
                "definition_locations": ["Sec. 1"],
                "occurrences": [
                    {
                        "location": "Sec. 2",
                        "surface_form": "q",
                        "meaning": "scalar coefficient",
                        "domain_or_type": "scalar",
                        "alignment": "CONFLICT",
                    }
                ],
                "status": "CONFLICT",
                "notes": [],
            }
        ]
        output["issues"] = [
            {
                "id": "CO-104",
                "claim_id": "C01",
                "location": "Sec. 1 and Sec. 2",
                "severity": "MAJOR",
                "category": "consistency",
                "problem": "q changes from vector to scalar.",
                "why_it_matters": "Equations use incompatible types.",
                "required_action": "Use distinct symbols or one stable type.",
                "verification_criterion": "N01 has one meaning and type.",
                "status": "OPEN",
                "notes": [],
            }
        ]
        merge_invariant_output(self.root, output, "notation-conflict")
        report = evaluate(self.root, final=False)
        failures = {item["id"] for item in report["checks"] if item["status"] == "FAIL"}
        self.assertIn("G18", failures)

    def test_claim_scope_drift_is_a_hard_gate(self) -> None:
        output = invariant_output()
        record = output["claim_consistency"]["records"][0]
        record["occurrences"].append(
            {
                "id": "OCC-002",
                "location": "Conclusion",
                "role": "CONCLUSION",
                "statement": "Statement for C01 without restrictions",
                "scope_relation": "BROADER",
                "synchronized": False,
            }
        )
        record["status"] = "FAIL"
        output["issues"] = [
            {
                "id": "CO-102",
                "claim_id": "C01",
                "location": "Conclusion",
                "severity": "MAJOR",
                "category": "consistency",
                "problem": "The Conclusion broadens C01 beyond its canonical scope.",
                "why_it_matters": "The final Claim is not supported by the body.",
                "required_action": "Restore the stated scope or add valid support.",
                "verification_criterion": "The Conclusion is SAME or NARROWER than C01.",
                "status": "OPEN",
                "notes": [],
            }
        ]
        merge_invariant_output(self.root, output, "claim-conflict")
        report = evaluate(self.root, final=False)
        failures = {item["id"] for item in report["checks"] if item["status"] == "FAIL"}
        self.assertIn("G21", failures)

    def test_macro_contract_cannot_rename_a_registry_concept(self) -> None:
        merge_invariant_output(self.root, invariant_output(), "invariant-run")
        output = macro_output()
        output["contract"]["terminology"][0]["canonical"] = "renamed result"
        with self.assertRaises(HarnessError):
            merge_layer_output(self.root, "macro_architect", output, "macro-run")

    def test_revision_invalidates_invariant_registries(self) -> None:
        merge_invariant_output(self.root, invariant_output(), "invariant-run")
        merge_review_output(self.root, review_output())
        record_revisions(
            self.root,
            {
                "agent": "reviser",
                "revisions": [
                    {
                        "issue_id": "TH-001",
                        "revision_status": "CLAIMED_FIXED",
                        "changed_files": ["manuscript/main.tex"],
                        "changed_locations": ["Sec. 1"],
                        "affected_claims": ["C01"],
                        "change_types": ["proof"],
                        "resolution_summary": "Changed the manuscript.",
                    }
                ],
            },
            "revision-run",
        )
        self.assertEqual(
            load_json(self.root / ".review" / "claim_consistency.json")["status"],
            "STALE",
        )

    def test_source_hash_change_blocks_stale_invariants(self) -> None:
        merge_invariant_output(self.root, invariant_output(), "invariant-run")
        manuscript = self.root / "manuscript" / "main.tex"
        manuscript.write_text(
            "\\documentclass{article}\\begin{document}Changed\\end{document}\n",
            encoding="utf-8",
        )
        report = evaluate(self.root, final=False)
        g16 = next(item for item in report["checks"] if item["id"] == "G16")
        self.assertEqual(g16["status"], "FAIL")

    def test_semantic_similarity_is_diagnostic_only(self) -> None:
        output = invariant_output()
        output["redundancy_diagnostics"]["semantic_similarity_notes"] = [
            {
                "locations": ["Abstract", "Conclusion"],
                "diagnosis": "Both restate the central finding.",
                "recommendation": "Retain unless readability suffers.",
            }
        ]
        merge_invariant_output(self.root, output, "similarity-note")
        report = evaluate(self.root, final=False)
        g22 = next(item for item in report["checks"] if item["id"] == "G22")
        self.assertEqual(g22["status"], "PASS")

    def test_pluggable_scientific_validator_contract(self) -> None:
        config_path = self.root / ".review" / "config.json"
        config = load_json(config_path)
        payload = {
            "validator_id": "rank_consistency",
            "status": "PASS",
            "summary": "Matrix ranks agree.",
            "evidence": ["artifact/rank-report.json"],
        }
        config["scientific_validators"] = [
            {
                "id": "rank_consistency",
                "command": [
                    sys.executable,
                    "-c",
                    f"import json; print(json.dumps({payload!r}))",
                ],
                "required": True,
                "timeout_seconds": 30,
            }
        ]
        write_json(config_path, config)
        report = evaluate(self.root, final=True)
        scientific = next(
            item for item in report["checks"] if item["id"] == "SV-rank_consistency"
        )
        self.assertEqual(scientific["status"], "PASS")
        records = list(
            (self.root / ".review" / "scientific-validation").glob("*/result.json")
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(load_json(records[0])["output"]["status"], "PASS")

    def test_required_scientific_validator_failure_blocks(self) -> None:
        config_path = self.root / ".review" / "config.json"
        config = load_json(config_path)
        payload = {
            "validator_id": "data_reproduction",
            "status": "FAIL",
            "summary": "Reported value was not reproduced.",
            "evidence": ["artifacts/reproduction.json"],
        }
        config["scientific_validators"] = [
            {
                "id": "data_reproduction",
                "command": [
                    sys.executable,
                    "-c",
                    f"import json; print(json.dumps({payload!r}))",
                ],
                "required": True,
                "timeout_seconds": 30,
            }
        ]
        write_json(config_path, config)
        report = evaluate(self.root, final=True)
        scientific = next(
            item for item in report["checks"] if item["id"] == "SV-data_reproduction"
        )
        self.assertEqual(scientific["status"], "FAIL")

    def test_subagent_hook_blocks_non_json_output(self) -> None:
        payload = {
            "cwd": str(self.root),
            "agent_type": "theory_reviewer",
            "last_assistant_message": "Everything looks fine.",
        }
        completed = subprocess.run(
            [sys.executable, str(HARNESS_ROOT / ".codex" / "hooks" / "subagent_gate.py")],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(json.loads(completed.stdout)["decision"], "block")

    def test_clean_snapshot_excludes_review_history(self) -> None:
        destination = Path(self.temporary.name) / "snapshot"
        completed = subprocess.run(
            [
                sys.executable,
                str(HARNESS_ROOT / "scripts" / "clean_snapshot.py"),
                "--root",
                str(self.root),
                "--output",
                str(destination),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertTrue((destination / "manuscript" / "main.tex").is_file())
        self.assertFalse((destination / ".review").exists())

    def test_codex_runner_uses_schema_and_captures_output(self) -> None:
        shutil.copytree(HARNESS_ROOT / ".codex" / "agents", self.root / ".codex" / "agents")
        fake = Path(self.temporary.name) / "fake_codex.py"
        fake.write_text(
            """
import json
import pathlib
import sys

args = sys.argv[1:]
output = pathlib.Path(args[args.index('--output-last-message') + 1])
payload = {
    'agent': 'claim_mapper',
    'coverage': {
        'abstract_mapped': True,
        'conclusion_mapped': True,
        'abstract_numerical_claims_mapped': True,
        'scanned_sections': ['Abstract', 'Conclusion'],
        'unmapped_sections': []
    },
    'claims': []
}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(payload), encoding='utf-8')
print(json.dumps({'type': 'turn.completed'}))
""".strip()
            + "\n",
            encoding="utf-8",
        )
        config = json.loads((self.root / ".review" / "config.json").read_text(encoding="utf-8"))
        config["codex_command"] = [sys.executable, str(fake)]
        runner = CodexRunner(self.root, config)
        output = runner.run("claim_mapper", {"task": "test"}, "fake-run")
        self.assertEqual(output["agent"], "claim_mapper")
        invocation = self.root / ".review" / "runs" / "fake-run" / "agents" / "claim_mapper"
        self.assertTrue((invocation / "input.json").is_file())
        self.assertTrue((invocation / "output.json").is_file())
        self.assertTrue((invocation / "events.jsonl").is_file())
        self.assertTrue((invocation / "stderr.log").is_file())
        self.assertTrue((invocation / "invocation.json").is_file())
        self.assertTrue((invocation / "effective-schema.json").is_file())

    def test_codex_output_schemas_are_bundled_without_external_refs(self) -> None:
        schema = bundled_schema(self.root, "macro-output.schema.json")
        encoded = json.dumps(schema)
        self.assertNotIn("review-output.schema.json", encoded)
        self.assertIn('"issues"', encoded)

    def test_natural_language_request_is_canonicalized_and_admitted(self) -> None:
        shutil.copytree(HARNESS_ROOT / ".codex" / "agents", self.root / ".codex" / "agents")
        request = canonicalize_request(
            self.root,
            request_draft(),
            "Please review this manuscript without editing it.",
            session_id="session-1",
            turn_id="turn-1",
        )
        self.assertEqual(request["status"], "READY")
        self.assertFalse(request["confirmation"]["required"])
        report = evaluate_admission(self.root, request)
        self.assertTrue(report["allowed"], report)

    def test_edit_request_is_denied_until_confirmed(self) -> None:
        shutil.copytree(HARNESS_ROOT / ".codex" / "agents", self.root / ".codex" / "agents")
        request = canonicalize_request(
            self.root,
            request_draft("REVISE", allow_edits=True),
            "Revise all open issues.",
            session_id="session-2",
        )
        denied = evaluate_admission(self.root, request)
        failed = {item["id"] for item in denied["checks"] if item["status"] == "FAIL"}
        self.assertIn("A06", failed)
        confirmed = confirm_request(self.root, request["request_id"])
        accepted = evaluate_admission(self.root, confirmed)
        self.assertTrue(accepted["allowed"], accepted)

    def test_audit_log_redacts_secrets_and_detects_tampering(self) -> None:
        append_event(
            self.root,
            "test.first",
            {"authorization": "Bearer private", "value": "visible"},
            session_id="audit-session",
        )
        append_event(self.root, "test.second", {"password": "private"})
        log_path = self.root / ".review" / "logs" / "events.jsonl"
        first = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(first["payload"]["authorization"], "[REDACTED]")
        self.assertNotIn("Bearer private", log_path.read_text(encoding="utf-8"))
        self.assertTrue(verify_event_chain(self.root)["valid"])
        lines = log_path.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace("visible", "changed")
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertFalse(verify_event_chain(self.root)["valid"])

    def test_conversation_provenance_and_monitor_snapshot(self) -> None:
        append_event(
            self.root,
            "hook.UserPromptSubmit",
            {"prompt": "Review the proof"},
            session_id="session-safe",
            artifact_refs=[{"path": "manuscript/main.tex"}],
        )
        result = materialize_conversation(self.root, "session-safe")
        self.assertTrue((self.root / result["json"]).is_file())
        self.assertTrue((self.root / result["markdown"]).is_file())
        snapshot = monitor_snapshot(self.root)
        self.assertEqual(snapshot["last_event"]["event_type"], "hook.UserPromptSubmit")

    def test_redaction_truncates_large_values(self) -> None:
        value = redact(
            {"notes": "x" * 20, "command": "curl --api-key private https://example.test"},
            max_inline_chars=60,
        )
        truncated = redact({"notes": "x" * 20}, max_inline_chars=5)
        self.assertNotIn("private", value["command"])
        self.assertIn("[REDACTED]", value["command"])
        self.assertTrue(truncated["notes"].startswith("xxxxx…"))

    def test_audit_hook_materializes_session_end(self) -> None:
        shutil.copytree(HARNESS_ROOT / "scripts", self.root / "scripts")
        payload = {
            "cwd": str(self.root),
            "hook_event_name": "SessionEnd",
            "session_id": "session/unsafe",
            "turn_id": "turn-9",
            "authorization": "private",
        }
        completed = subprocess.run(
            [sys.executable, str(HARNESS_ROOT / ".codex" / "hooks" / "audit_event.py")],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=self.root,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {})
        provenance_path = (
            self.root / ".review" / "provenance" / "conversations" / "session_unsafe.json"
        )
        self.assertTrue(provenance_path.is_file())
        log = (self.root / ".review" / "logs" / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("private", log)

    def test_control_intake_accepts_natural_language_end_to_end(self) -> None:
        shutil.copytree(HARNESS_ROOT / ".codex" / "agents", self.root / ".codex" / "agents")
        fake = Path(self.temporary.name) / "fake_intake_codex.py"
        draft = request_draft()
        fake.write_text(
            "\n".join(
                [
                    "import json",
                    "import pathlib",
                    "import sys",
                    "args = sys.argv[1:]",
                    "output = pathlib.Path(args[args.index('--output-last-message') + 1])",
                    f"payload = {draft!r}",
                    "output.parent.mkdir(parents=True, exist_ok=True)",
                    "output.write_text(json.dumps(payload), encoding='utf-8')",
                    "print(json.dumps({'type': 'turn.completed', 'thread_id': 'intake-thread'}))",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        config_path = self.root / ".review" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["codex_command"] = [sys.executable, str(fake)]
        write_json(config_path, config)
        completed = subprocess.run(
            [
                sys.executable,
                str(HARNESS_ROOT / "scripts" / "control.py"),
                "--root",
                str(self.root),
                "intake",
                "--text",
                "Review my paper without editing it.",
                "--session-id",
                "natural-session",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["request"]["intent"], "REVIEW")
        self.assertTrue(
            (self.root / ".review" / "requests" / f"{result['request']['request_id']}.json").is_file()
        )


if __name__ == "__main__":
    unittest.main()
