"""Regression for accepted training child that is not GEPA's final best."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import finalize_g3_strategies as original
import finalize_g3_strategies_v2 as revised


class SelectionBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / "results/g3" / revised.ID
        self.seed, self.child = "Follow policy.", "Follow policy and track disclosed goals."
        self.rows = {}
        self.directories = []
        for index, strategy in enumerate((self.seed, self.seed, self.child, self.child), 1):
            task = ("16", "19")[(index - 1) % 2]
            directory = self.stage / "B1/optimizer/episodes" / f"episode-{index:04d}" / "environment"
            directory.mkdir(parents=True)
            self.write(directory / "request.json", {"strategy": strategy})
            self.write(directory / "outcome.json", {"task_id": task, "native_reward": 1,
                "structural_outcome": {"status": "pass"}, "input_tokens": 100, "output_tokens": 10, "run_id": str(index)})
            self.rows[directory.relative_to(self.root).as_posix()] = {"semantic_status": "pass", "user_validity": "pass",
                "critical_violation": False, "critical_user_error": False}
            self.directories.append(directory)
        self.write(self.stage / "B1/optimizer/best_candidate.json", {"strategy": self.seed})
        self.manifest = {"arm_order": ["B1"], "seed_strategy": self.seed, "splits": {"validation": ["16", "19"]}}
        self.a, self.b = self.root / "a.json", self.root / "b.json"
        self.write(self.a, {})
        self.write(self.b, {})
        for name in ("scripts/finalize_g3_strategies.py", "research/G3_SELECTION_BINDING_AMENDMENT.md"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("test-fixture", encoding="utf-8")

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def execute(self, module):
        with patch.object(module, "ROOT", self.root), patch.object(module, "verify", return_value=(self.manifest, "manifest")), \
             patch.object(module, "verify_arm"), patch.object(module, "read_review", side_effect=[("a", self.rows), ("b", self.rows)]):
            return module.run(self.a, self.b)

    def test_original_rejects_realizable_seed_best_child_validation_pairing(self):
        with self.assertRaisesRegex(ValueError, "candidate row belongs to another strategy"):
            self.execute(original)
        self.assertFalse((self.stage / "FINAL_STRATEGIES.json").exists())

    def test_revised_preserves_child_evidence_without_reselecting_it(self):
        self.execute(revised)
        final = revised.load(self.stage / "FINAL_STRATEGIES.json")
        decision = final["arms"]["B1"]
        self.assertEqual(decision["final_strategy"], self.seed)
        self.assertEqual(decision["decision"], "keep_seed")
        self.assertFalse(decision["gepa_selected_new"])
        self.assertEqual(decision["counts"]["candidate"]["qualified"], 2)
        self.assertEqual(decision["candidate_strategy_sha256"], hashlib.sha256(self.child.encode()).hexdigest())
        self.assertIn("gepa_did_not_select_new_candidate", decision["reasons"])
        self.assertIn("qualified_count_not_strictly_higher", decision["reasons"])
        self.assertEqual(len(final["optimization_judgments"]), 4)

    def test_selected_child_can_still_pass_unchanged_acceptance(self):
        self.write(self.stage / "B1/optimizer/best_candidate.json", {"strategy": self.child})
        path = self.directories[1] / "outcome.json"
        value = revised.load(path)
        value.update(native_reward=0, structural_outcome={"status": "fail"})
        self.write(path, value)
        self.execute(revised)
        decision = revised.load(self.stage / "FINAL_STRATEGIES.json")["arms"]["B1"]
        self.assertEqual(decision["decision"], "accept_candidate")
        self.assertTrue(decision["gepa_selected_new"])

    def test_ambiguous_children_are_not_silently_collapsed(self):
        rows = [{"strategy_sha256": "one"}, {"strategy_sha256": "two"}]
        with self.assertRaisesRegex(ValueError, "multiple validation candidate"):
            revised.bind_candidate_for_acceptance(self.seed, self.seed, rows)

    def test_request_must_match_recorded_candidate_hash(self):
        directory = self.directories[2]
        row = {"relative_directory": directory.relative_to(self.root).as_posix(), "strategy_sha256": "wrong"}
        with patch.object(revised, "ROOT", self.root), self.assertRaisesRegex(ValueError, "bound strategy hash"):
            revised.bind_candidate_for_acceptance(self.seed, self.seed, [row])


if __name__ == "__main__":
    unittest.main()
