from __future__ import annotations

from copy import deepcopy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from coherence import (  # noqa: E402
    POLICY, build_inventory, gate_failures, run_coherence, schedule, trace,
    validate_records,
)
from paper_review_lib import HarnessError, load_json, merge_invariant_output, merge_layer_output, write_json  # noqa: E402
from review import Workflow  # noqa: E402
from test_harness import invariant_output, macro_output, hierarchy_output  # noqa: E402
from coherence_fixtures import FixtureRunner, bind_hierarchy  # noqa: E402


class CoherenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / ".review", self.root / ".review")
        (self.root / "manuscript").mkdir()
        self.source = self.root / "manuscript/main.tex"
        self.source.write_text(
            "\\documentclass{article}\n\\begin{document}\n\\section{Method}\n"
            "We describe the setup.\n\nTherefore, the bound holds.\n"
            "\\subsection{Conditions}\nThe scope is local.\n"
            "\\section{Results}\nThe observations agree.\n\\end{document}\n", encoding="utf-8")
        from test_harness import claim
        claims = load_json(self.root / ".review/claims.json")
        claims["claims"] = [claim("C01", centrality="CORE")]
        write_json(self.root / ".review/claims.json", claims)
        merge_invariant_output(self.root, invariant_output(), "test-invariants")
        merge_layer_output(self.root, "macro_architect", macro_output(), "test-macro")
        merge_layer_output(self.root, "hierarchy_reviewer", hierarchy_output(), "test-hierarchy")
        self.inventory = bind_hierarchy(self.root)
        self.workflow = Workflow(self.root)
        self.workflow.runner = FixtureRunner()

    def tearDown(self):
        self.temp.cleanup()

    def run_review(self, mode="ADAPTIVE"):
        self.inventory = bind_hierarchy(self.root, mode)
        return run_coherence(self.workflow, self.inventory, mode)

    def test_adaptive_chain_and_reverse_pass(self):
        registry = self.run_review()
        self.assertEqual([p["stage"] for p in registry["passes"]], ["PARAGRAPH", "SENTENCE", "BOTTOM_UP"])
        self.assertEqual(len(registry["scheduler"]["selected_sentence_ids"]), 1)
        target = registry["scheduler"]["selected_sentence_ids"][0]
        chain = trace(registry, target)["chains"][0]["chain"]
        self.assertEqual([n["level"] for n in chain], ["PAPER", "SECTION", "PARAGRAPH", "SENTENCE"])
        self.assertIn("INFERENCE_CONNECTOR", chain[-1]["routing"]["reasons"])
        self.assertTrue(all(not errors for errors in gate_failures(self.root).values()), gate_failures(self.root))

    def test_stable_ids_after_unrelated_paragraph_insertion(self):
        old = {u["text"]: u["id"] for u in self.inventory["units"]}
        self.source.write_text(self.source.read_text().replace("We describe", "An inserted paragraph.\n\nWe describe"), encoding="utf-8")
        new = {u["text"]: u["id"] for u in build_inventory(self.root)["units"]}
        for text, uid in old.items():
            self.assertEqual(new[text], uid)

    def test_source_change_invalidates_gates(self):
        self.run_review()
        self.source.write_text(self.source.read_text() + "% later edit\n", encoding="utf-8")
        self.assertTrue(gate_failures(self.root)["G24"])

    def test_routing_tamper_is_recomputed(self):
        registry = self.run_review()
        registry["scheduler"]["routes"][0]["score"] = 99
        write_json(self.root / ".review/coherence_registry.json", registry)
        self.assertTrue(gate_failures(self.root)["G26"])

    def test_missing_paragraph_record_blocks_coverage(self):
        registry = self.run_review()
        missing = next(r for r in registry["records"] if r["node_id"].startswith("P") and r["node_id"] != "PAPER")
        registry["records"].remove(missing)
        write_json(self.root / ".review/coherence_registry.json", registry)
        self.assertTrue(gate_failures(self.root)["G25"])

    def test_omitted_hierarchy_source_fails_before_language(self):
        structure = load_json(self.root / ".review/structure.json")
        structure["payload"]["nodes"].pop()
        write_json(self.root / ".review/structure.json", structure)
        with self.assertRaisesRegex(HarnessError, "Source/hierarchy coverage"):
            run_coherence(self.workflow, self.inventory, "ADAPTIVE")
        self.assertFalse(self.workflow.runner.calls)

    def test_wrong_hierarchy_parent_rejected(self):
        structure = load_json(self.root / ".review/structure.json")
        structure["payload"]["nodes"][1]["parent_id"] = None
        write_json(self.root / ".review/structure.json", structure)
        with self.assertRaisesRegex(HarnessError, "parent mismatch"):
            run_coherence(self.workflow, self.inventory, "ADAPTIVE")

    def test_bottom_up_missing_child_fails(self):
        registry = self.run_review()
        output = deepcopy(registry["passes"][-1]["output"])
        output["bottom_up"][-1]["child_ids"] = []
        with self.assertRaisesRegex(HarnessError, "child coverage"):
            validate_records(self.root, output, "BOTTOM_UP", [r["node_id"] for r in output["bottom_up"]], self.inventory, registry["records"])

    def test_bottom_up_parent_before_child_fails(self):
        registry = self.run_review()
        output = deepcopy(registry["passes"][-1]["output"])
        output["bottom_up"].reverse()
        with self.assertRaisesRegex(HarnessError, "children before parents"):
            validate_records(self.root, output, "BOTTOM_UP", [r["node_id"] for r in output["bottom_up"]], self.inventory, registry["records"])

    def test_source_parent_tamper_cannot_pass(self):
        registry = self.run_review()
        next(u for u in registry["inventory"]["units"] if u["level"] == "SENTENCE")["parent_id"] = "PAPER"
        write_json(self.root / ".review/coherence_registry.json", registry)
        self.assertTrue(gate_failures(self.root)["G24"])

    def test_critical_sentence_requires_inference(self):
        registry = self.run_review()
        output = deepcopy(registry["passes"][1]["output"])
        output["records"][0]["inference"] = ""
        with self.assertRaisesRegex(HarnessError, "expose inference"):
            validate_records(self.root, output, "SENTENCE", registry["scheduler"]["selected_sentence_ids"], self.inventory, registry["records"])

    def test_unknown_registry_reference_rejected(self):
        registry = self.run_review()
        output = deepcopy(registry["passes"][0]["output"])
        output["records"][0]["terminology_ids"] = ["T999"]
        with self.assertRaisesRegex(HarnessError, "Unknown coherence terminology_ids"):
            validate_records(self.root, output, "PARAGRAPH", [r["node_id"] for r in output["records"]], self.inventory, registry["records"])

    def test_explicit_sentence_mode_covers_all_sentences(self):
        registry = self.run_review("SENTENCE")
        all_sentences = [u for u in self.inventory["units"] if u["level"] == "SENTENCE"]
        self.assertEqual(len(registry["scheduler"]["selected_sentence_ids"]), len(all_sentences))

    def test_paragraph_limit_records_skipped_sentence_reasons(self):
        registry = self.run_review("PARAGRAPH")
        self.assertFalse(registry["scheduler"]["selected_sentence_ids"])
        self.assertTrue(all(r["decision"] == "DEPTH_LIMIT" for r in registry["scheduler"]["routes"]))

    def test_section_gap_propagates_to_sentences(self):
        structure = load_json(self.root / ".review/structure.json")
        structure["payload"]["nodes"][0]["status"] = "REVISE"
        plan = schedule(self.inventory, structure, [], [], "ADAPTIVE", POLICY)
        scoped = [r for r in plan["routes"] if "SECTION_GAP" in r["reasons"]]
        self.assertEqual(len(scoped), 3)
        self.assertTrue(all(r["selected"] for r in scoped))

    def test_granular_workflow_projects_legacy_view(self):
        self.workflow.coherence_inventory = self.inventory
        ledger = self.workflow.granular_control("ADAPTIVE")
        self.assertEqual(ledger["status"], "CURRENT")
        self.assertEqual(len(ledger["payload"]["units"]), 5)
        self.assertTrue(all(not v for v in gate_failures(self.root).values()))

    def test_literal_includes_and_nested_heading(self):
        (self.root / "manuscript/part.tex").write_text("\\subsection{Included}\nA local statement.", encoding="utf-8")
        self.source.write_text("\\section{Outer}\n\\input{part}\nTail.", encoding="utf-8")
        inventory = build_inventory(self.root)
        self.assertFalse(inventory["errors"])
        self.assertEqual(len(inventory["source_files"]), 2)
        child = next(u for u in inventory["units"] if u["text"] == "Included")
        parent = next(u for u in inventory["units"] if u["text"] == "Outer")
        self.assertEqual(child["parent_id"], parent["id"])

    def test_dynamic_tex_and_recursive_includes_are_not_certified(self):
        self.source.write_text("\\iftrue\nText.\n\\fi\n\\input{main}", encoding="utf-8")
        errors = build_inventory(self.root)["errors"]
        self.assertTrue(any("conditional" in e for e in errors))
        self.assertTrue(any("recursive" in e for e in errors))

    def test_display_math_does_not_create_fake_paragraph_or_sentence_boundaries(self):
        self.source.write_text("\\section{Math}\nA result is\n\\begin{equation}\nx=1.\n\ny=2.\n\\end{equation}\nunder the stated conditions.", encoding="utf-8")
        inventory = build_inventory(self.root)
        self.assertEqual(sum(u["level"] == "PARAGRAPH" for u in inventory["units"]), 1)
        self.assertEqual(sum(u["level"] == "SENTENCE" for u in inventory["units"]), 1)

    def test_multiline_structural_macro_blocks_coverage_certification(self):
        self.source.write_text("\\newcommand{\\mysection}[1]{\n\\section{#1}\n}\n\\begin{document}\n\\mysection{Hidden}\nText.\n\\end{document}", encoding="utf-8")
        self.assertTrue(any("structure-producing" in e for e in build_inventory(self.root)["errors"]))


if __name__ == "__main__":
    unittest.main()
