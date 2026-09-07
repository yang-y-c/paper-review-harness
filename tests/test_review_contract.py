from __future__ import annotations

import sys
import unittest
from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "scripts"))

from paper_review_lib import HarnessError  # noqa: E402
from review_contract import _validate_contract_shape, effective_granularity  # noqa: E402


def contract(*, assurance="STANDARD", paragraph=True, sentence="RISK_ADAPTIVE", revision="REVIEW_ONLY"):
    return {
        "selection_source": "USER_EXPLICIT",
        "dimensions": {
            "global_structure": True,
            "claims": True,
            "section_logic": True,
            "terminology": True,
            "notation": True,
            "data_consistency": True,
            "paragraph_logic": paragraph,
            "sentence_logic": sentence,
            "redundancy": False,
            "language": False,
            "citations": False,
            "scientific_validity": False,
        },
        "assurance": {
            "overall": assurance,
            "core_claims": assurance,
            "ordinary_content": assurance,
        },
        "revision": {
            "strategy": revision,
            "structural_checkpoint": revision == "STAGED_REVISION",
            "stop_before_detail_on_structural_major": revision == "STAGED_REVISION",
        },
        "budget": {
            "profile": "BALANCED",
            "max_token_amplification": None,
            "low_risk_sampling_rate": 0.05,
        },
    }


class ReviewContractTests(unittest.TestCase):
    def test_fast_structural_profile_stays_subsection(self):
        value = contract(assurance="FAST", paragraph=False, sentence="OFF")
        _validate_contract_shape(value, "REVIEW")
        self.assertEqual(effective_granularity(value), "SUBSECTION")

    def test_standard_paragraph_profile_is_adaptive(self):
        value = contract(assurance="STANDARD", paragraph=True, sentence="RISK_ADAPTIVE")
        self.assertEqual(effective_granularity(value), "ADAPTIVE")

    def test_strict_profile_is_sentence(self):
        value = contract(assurance="STRICT", paragraph=True, sentence="RISK_ADAPTIVE")
        self.assertEqual(effective_granularity(value), "SENTENCE")

    def test_fast_cannot_silently_request_full_sentence_review(self):
        value = contract(assurance="FAST", paragraph=True, sentence="FULL")
        with self.assertRaisesRegex(HarnessError, "FAST"):
            _validate_contract_shape(value, "REVIEW")

    def test_fixed_academic_core_cannot_be_disabled(self):
        value = contract()
        value["dimensions"]["notation"] = False
        with self.assertRaisesRegex(HarnessError, "Fixed academic core"):
            _validate_contract_shape(value, "REVIEW")

    def test_read_only_intent_cannot_request_revision(self):
        value = contract(revision="STAGED_REVISION")
        with self.assertRaisesRegex(HarnessError, "Read-only"):
            _validate_contract_shape(value, "REVIEW")

    def test_full_intent_cannot_hide_as_review_only(self):
        value = contract(revision="REVIEW_ONLY")
        with self.assertRaisesRegex(HarnessError, "Edit-capable"):
            _validate_contract_shape(value, "FULL")


if __name__ == "__main__":
    unittest.main()
