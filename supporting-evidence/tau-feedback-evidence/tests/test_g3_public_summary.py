"""Ensure new private metadata does not silently enter the public result view."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import export_g3_public_summary as export


class PublicSummaryTests(unittest.TestCase):
    def setUp(self):
        self.report = {key: {} for key in export.REPORT_FIELDS}
        self.report.update(manifest_sha256="manifest", final_strategies_sha256="policies",
            test_reviews=[{"reviewer": "a", "sha256": "hash-a", "path": "PRIVATE_TEST_REVIEW_PATH"}],
            raw_transport="PRIVATE_RAW_RECORDS", unlisted_future_field="PRIVATE_FUTURE_METADATA")
        keys = ("model", "effort", "transport", "arm_order", "splits", "optimizer", "gepa_behavior",
                "episode_limits", "client_limits_per_arm", "test_client_limits", "test_schedule")
        self.manifest = {key: {} for key in keys}
        self.manifest.update(full_model_catalog="PRIVATE_MODEL_CATALOG", source_paths="PRIVATE_SOURCE_PATHS")
        self.policies = {"optimization_judgments": [{**{key: None for key in export.JUDGMENT_FIELDS},
                                                    "relative_directory": "PRIVATE_OPTIMIZATION_PATH"}],
            "arms": {"B0": {"final_strategy": "Follow policy.", "decision": "fixed_baseline"}},
            "strategy_sha256": {"B0": "strategy-hash"},
            "reviews": [{"reviewer": "b", "sha256": "hash-b", "path": "PRIVATE_OPTIMIZATION_REVIEW_PATH"}]}

    def test_private_metadata_is_omitted_and_limits_remain_explicit(self):
        value = export.public_view(self.report, self.manifest, self.policies, "report-hash")
        text = json.dumps(value)
        self.assertNotIn("PRIVATE_", text)
        self.assertEqual(value["publication_status"], "local_derived_summary_not_published")
        self.assertIn("cannot", value["evidence_boundary"].lower().replace("do not", "cannot"))

    def test_derived_export_does_not_mutate_original_reports(self):
        before = deepcopy((self.report, self.manifest, self.policies))
        value = export.public_view(self.report, self.manifest, self.policies, "report-hash")
        value["configuration"]["splits"]["added"] = "public-only"
        value["optimization_judgments"][0]["arm"] = "changed"
        self.assertEqual(before, (self.report, self.manifest, self.policies))


if __name__ == "__main__":
    unittest.main()
