"""Stage failure accounting; fake clients, no model or user account access."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_g3_test as final_test
import finalize_g3_strategies as selection


class StageGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / "results/g3" / final_test.ID
        self.stage.mkdir(parents=True)

    def test_keyboard_interrupt_is_attempted_ungraded_not_unrun(self):
        from tau2.data_model.tasks import Task, UserScenario, EvaluationCriteria
        task = Task(id="fixture", user_scenario=UserScenario(instructions="fake"),
                    evaluation_criteria=EvaluationCriteria(actions=[], nl_assertions=[]))
        strategy = "Follow policy."
        policies = {"manifest_sha256": "manifest", "arms": {a: {"final_strategy": strategy} for a in ("B0", "B1", "B2")},
            "strategy_sha256": {a: hashlib.sha256(strategy.encode()).hexdigest() for a in ("B0", "B1", "B2")}, "reviews": []}
        path = self.stage / "FINAL_STRATEGIES.json"
        path.write_text(json.dumps(policies), encoding="utf-8")
        path.with_suffix(".sha256").write_text(final_test.sha(path))
        manifest = {"arm_order": ["B2", "B1"], "model": "fake", "effort": "low", "test_client_limits": {},
            "episode_limits": {}, "frozen_tasks": {"test": [{"task_id": "fixture", "payload": task.model_dump(mode="json")}]},
            "test_schedule": [{"unit": 1, "task_id": "fixture", "arm": "B0", "simulation_seed": 103}]}
        with patch.object(final_test, "ROOT", self.root), patch.object(final_test, "verify", return_value=(manifest, "manifest")), \
             patch.object(final_test, "verify_arm"), patch.object(final_test, "usage", return_value={"known_tokens": 12}), \
             patch("tau_feedback.subscription_http_client.HttpCliTextClient", return_value=SimpleNamespace()), \
             patch("tau_feedback.subscription_episode.run_subscription_episode", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                final_test.run()
        summary = json.loads((self.stage / "test/summary.json").read_text())
        self.assertEqual(summary["attempted_units"], 1)
        self.assertEqual(summary["attempted_ungraded_units"], 1)
        self.assertEqual(summary["unrun_units"], 35)
        self.assertEqual(summary["units"][0]["execution_status"], "interrupted_ungraded")
        self.assertTrue((self.stage / "STOP.json").exists())

    def test_training_critical_user_alarm_blocks_policy_freeze(self):
        directory = self.stage / "B2/optimizer/episodes/episode-0001/environment"
        directory.mkdir(parents=True)
        name = directory.relative_to(self.root).as_posix()
        review_a, review_b = self.root / "a.json", self.root / "b.json"
        review_a.write_text("{}")
        review_b.write_text("{}")
        with patch.object(selection, "ROOT", self.root), \
             patch.object(selection, "verify", return_value=({"arm_order": ["B2"]}, "manifest")), \
             patch.object(selection, "verify_arm"), \
             patch.object(selection, "read_review", side_effect=[("a", {name: {"critical_user_error": True}}),
                                                                  ("b", {name: {"critical_user_error": False}})]):
            with self.assertRaisesRegex(RuntimeError, "Critical user fidelity"):
                selection.run(review_a, review_b)
        self.assertFalse((self.stage / "FINAL_STRATEGIES.json").exists())
        self.assertEqual(json.loads((self.stage / "STOP.json").read_text())["episode_directories"], [name])


if __name__ == "__main__":
    unittest.main()
