from copy import deepcopy
import unittest

import test_coherence as fixtures
from coherence import gate_failures
from local_recovery import validate_local
from logic_graph import (
    _all_contexts, _material, _validate_edge_output, invalidate_relations,
    relation_failures, run_relations,
)
from paper_review_lib import HarnessError, load_json


class LogicGraphTests(unittest.TestCase):
    setUp = fixtures.CoherenceTests.setUp
    tearDown = fixtures.CoherenceTests.tearDown
    run_review = fixtures.CoherenceTests.run_review

    def test_local_recovery_reads_whole_risky_paragraph(self):
        self.source.write_text("\\section{Method}\nFirst observation. Second observation. Third observation. "
                               "Fourth observation. Fifth observation. Sixth observation. Seventh observation. "
                               "Therefore, the bound holds.\n", encoding="utf-8")
        registry = self.run_review()
        groups = [g for g in registry["logic_graph"]["local_graphs"] if g["group"]["scale"] == "SENTENCE"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["mapped"]["nodes"]), 8)
        self.assertEqual(len(groups[0]["mapped"]["relations"]), 7)
        # Local sentence relations come from one context recovery, not pair mapping.
        for call in self.workflow.runner.calls:
            if call["task"] == "LOGIC_RELATION_MAPPING":
                self.assertFalse(any(m["candidate"]["scale"] == "SENTENCE" for m in call["candidates"]))

    def test_attachment_cycle_rejected(self):
        registry = self.run_review()
        item = next(g for g in registry["logic_graph"]["local_graphs"] if len(g["mapped"]["nodes"]) > 1)
        mapped = deepcopy(item["mapped"])
        first, second = mapped["nodes"][:2]
        first["primary_parent_id"] = second["node_id"]
        first["attachment_status"] = "ATTACHED"
        with self.assertRaisesRegex(HarnessError, "cycle"):
            validate_local(self.root, item["group"], mapped)

    def test_coverage_challenge_cannot_omit_nodes(self):
        registry = self.run_review()
        item = registry["logic_graph"]["local_graphs"][0]
        challenge = deepcopy(item["challenge"])
        challenge["checks"].pop()
        with self.assertRaisesRegex(HarnessError, "every unit"):
            validate_local(self.root, item["group"], item["mapped"], challenge)

    def test_verifier_and_challenger_do_not_receive_mapper_reasoning(self):
        self.run_review()
        calls = self.workflow.runner.calls
        for call in calls:
            if call["task"] == "LOCAL_COVERAGE_CHALLENGE":
                self.assertTrue(all("rationale" not in a for a in call["attachments"]))
                self.assertTrue(all("rationale" not in a and "confidence" not in a for a in call["proposed_edges"]))
            if call["task"] == "LOGIC_RELATION_VERIFICATION":
                self.assertTrue(all("mapper" not in c and "rationale" not in c and "confidence" not in c for c in call["candidates"]))

    def test_fabricated_evidence_rejected(self):
        registry = self.run_review()
        relation = registry["logic_graph"]["relations"][0]
        proposal = deepcopy(relation["mapper"])
        proposal["source_evidence"]["text"] = "Invented text."
        edge = next(c for c in registry["logic_graph"]["candidate_graph"]["candidate_edges"] if c["id"] == relation["id"])
        material = _material(_all_contexts(self.root, self.inventory, registry["records"]), edge)
        with self.assertRaisesRegex(HarnessError, "source span"):
            _validate_edge_output(self.root, {"agent": "argument_reviewer", "relations": [proposal]}, [material], False)

    def test_wrong_scale_relation_rejected(self):
        registry = self.run_review()
        relation = next(r for r in registry["logic_graph"]["relations"] if r["scale"] == "SECTION")
        proposal = deepcopy(relation["mapper"])
        proposal["relation"] = "CAUSE_EFFECT"
        edge = next(c for c in registry["logic_graph"]["candidate_graph"]["candidate_edges"] if c["id"] == relation["id"])
        material = _material(_all_contexts(self.root, self.inventory, registry["records"]), edge)
        with self.assertRaisesRegex(HarnessError, "this scale"):
            _validate_edge_output(self.root, {"agent": "argument_reviewer", "relations": [proposal]}, [material], False)

    def test_ordinary_relation_dispute_is_diagnostic(self):
        original = self.workflow.runner.run
        def uncertain(agent, assignment, run_id, **kwargs):
            output = original(agent, assignment, run_id, **kwargs)
            if assignment["task"] == "LOGIC_RELATION_VERIFICATION":
                normal = {c["candidate"]["id"] for c in assignment["candidates"] if not c["candidate"]["critical"]}
                for verdict in output["verifications"]:
                    if verdict["candidate_id"] in normal:
                        verdict["status"] = "FAIL"
            return output
        self.workflow.runner.run = uncertain
        registry = self.run_review()
        self.assertTrue(any(r["status"] == "DISPUTED" and not r["critical"] for r in registry["logic_graph"]["relations"]))
        self.assertFalse(gate_failures(self.root)["G28"])

    def test_critical_disagreement_blocks_and_records_issue(self):
        original = self.workflow.runner.run
        def uncertain(agent, assignment, run_id, **kwargs):
            output = original(agent, assignment, run_id, **kwargs)
            if assignment["task"] == "LOGIC_RELATION_VERIFICATION":
                for row in output["verifications"]:
                    row["status"] = "UNCERTAIN"
            return output
        self.workflow.runner.run = uncertain
        self.run_review()
        self.assertTrue(gate_failures(self.root)["G28"])
        self.assertTrue(any(i["severity"] == "BLOCKER" for i in load_json(self.root / ".review/issues.json")["issues"]))

    def test_confirmed_relations_reused_without_new_verification(self):
        registry = self.run_review()
        self.workflow.runner.calls.clear()
        again = run_relations(self.workflow, self.inventory, registry["records"], "unchanged")
        self.assertEqual(again["reused_count"], len(again["relations"]))
        self.assertFalse(any(c["task"] == "LOGIC_RELATION_VERIFICATION" for c in self.workflow.runner.calls))

    def test_incremental_invalidation_preserves_unaffected_relations(self):
        self.run_review()
        self.source.write_text(self.source.read_text().replace("The observations agree.", "The observations differ."), encoding="utf-8")
        invalidate_relations(self.root)
        relations = load_json(self.root / ".review/logic/relations.json")["relations"]
        self.assertTrue(any(r["status"] == "STALE" for r in relations))
        self.assertTrue(any(r["status"] == "CONFIRMED" for r in relations))

    def test_same_run_cannot_count_as_independent_verification(self):
        registry = self.run_review()
        edge = registry["logic_graph"]["relations"][0]
        edge["verifier_run_id"] = edge["mapper_run_id"]
        self.assertTrue(any("independent" in e for e in relation_failures(self.root, registry)))


if __name__ == "__main__":
    unittest.main()
