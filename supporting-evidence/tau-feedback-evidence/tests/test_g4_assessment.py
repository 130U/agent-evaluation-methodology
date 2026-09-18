from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tau_feedback.g4_assessment import assess_candidate, paired_candidate_summary, choose_final
from tau_feedback.g4_paired import text_hash


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.units = [{"task_id": t, "simulation_seed": s} for t in ("a", "b") for s in (1, 2)]
        def rows(text):
            return [{**u, "strategy_sha256": text_hash(text), "native_reward": 1, "structural_status": "pass",
                "semantic_status": "pass", "user_validity": "pass", "critical_violation": False,
                "input_tokens": 100, "output_tokens": 0} for u in self.units]
        self.base, self.child = rows("Seed"), rows("Child")
        self.base[0]["semantic_status"] = "fail"

    def assess(self, selected=True):
        return assess_candidate(seed_strategy="Seed", candidate_strategy="Child", gepa_selected_new=selected,
            baseline_rows=self.base, candidate_rows=self.child, units=self.units)

    def test_gain_can_be_measured_without_resurrecting_gepa_rejection(self):
        value = self.assess(False)
        self.assertFalse(value["accepted"])
        self.assertEqual(value["qualified_gain"], 1)
        self.assertTrue(self.assess()["accepted"])

    def test_regression_unknown_cost_and_critical_each_block_adoption(self):
        for change in ({"semantic_status": "fail"}, {"semantic_status": "unknown"},
                       {"input_tokens": None}, {"critical_violation": True}):
            with self.subTest(change=change):
                old = deepcopy(self.child)
                self.child[1].update(change)
                self.assertFalse(self.assess()["accepted"])
                self.child = old

    def test_seed_swaps_and_duplicates_are_not_paired_observations(self):
        self.child[1]["simulation_seed"] = 1
        with self.assertRaises(ValueError):
            self.assess()

    def test_inactive_pairs_are_not_evidence_of_no_effect(self):
        value = self.assess()
        report = paired_candidate_summary([{"batch": i, "input_treatment_active": False,
                                            "B1": value, "B2": value} for i in range(1, 5)])
        self.assertEqual(report["active_pairs"], 0)
        self.assertIsNone(report["active_pair_mean_delta"])
        self.assertEqual(report["identification"], "insufficient_active_complete_pairs")

    def test_no_dropped_batch_and_deterministic_selection_tie(self):
        entries = [{"batch": i, "strategy": "Child", "assessment": self.assess()} for i in range(1, 5)]
        self.assertEqual(choose_final("Seed", entries)["selected_batch"], 1)
        with self.assertRaises(ValueError):
            choose_final("Seed", entries[:-1])


if __name__ == "__main__":
    unittest.main()
