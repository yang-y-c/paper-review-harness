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
                {"id": "L01", "role": "PROBLEM", "statement": "Problem", "depends_on": [], "evidence_locations": ["Sec. 1"], "status": "SUPPORTED"},
                {"id": "L02", "role": "METHOD", "statement": "Method", "depends_on": ["L01"], "evidence_locations": ["Sec. 1"], "status": "SUPPORTED"},
                {"id": "L03", "role": "CONCLUSION", "statement": "Result", "depends_on": ["L02"], "evidence_locations": ["Conclusion"], "status": "SUPPORTED"},
            ],
            "terminology": [
                {"canonical": "test result", "meaning": "the stated result", "allowed_variants": [], "forbidden_variants": [], "first_definition": "Abstract"}
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
        "TERMINOLOGY", "NOTATION", "HIERARCHY", "LANGUAGE", "CLAIM_SCOPE",
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
        self.assertTrue(report["passed"], report)

    def test_layered_plan_is_top_down_and_ends_with_final_audit(self) -> None:
        plan = RUN_PLANS["review"]
        self.assertLess(plan.index("MACRO_CONTRACT"), plan.index("HIERARCHICAL_REVIEW"))
        self.assertLess(
            plan.index("HIERARCHICAL_REVIEW"), plan.index("GRANULAR_LANGUAGE_REVIEW")
        )
        self.assertEqual(plan[-1], "FINAL_INTEGRITY_AUDIT")

    def test_layer_outputs_are_schema_validated_and_solidified(self) -> None:
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
