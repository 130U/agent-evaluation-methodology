"""Full measurement persistence and failed-score accounting, fake episode only."""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from tau_feedback.g4_paired import MeasurementRunner, verify_measurement, ResearchPause
from tau_feedback.gepa_adapter import EpisodeInput
from tau_feedback.contracts import canonical_hash
from tau_feedback.subscription_evidence import save, load


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        from tau2.data_model.tasks import Task, UserScenario, EvaluationCriteria
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = SimpleNamespace(root=self.root, halt_reason=None, unknown_usage_calls=0,
                                     _budget_reason=lambda **kw: None)
        task = Task(id="fixture", user_scenario=UserScenario(instructions="A fake test."),
                    evaluation_criteria=EvaluationCriteria(actions=[], nl_assertions=[]))
        self.example = EpisodeInput(task.id, task.model_dump(mode="json"))
        self.runner = MeasurementRunner(self.client, output=self.root / "measure", allowed_tasks={task.id: self.example.payload},
            max_episodes=2, episode_limits={"max_steps": 40, "max_errors": 6, "simulation_seconds": 900}, max_strategy_chars=6000)

    def fake(self, client, task, *, output, simulation_seed, strategy, **kw):
        output.mkdir()
        request = {"task_id": task.id, "task_sha256": canonical_hash(task.model_dump(mode="json")),
                   "run_id": "fake-run", "simulation_seed": simulation_seed, "strategy": strategy}
        outcome = {"status": "evaluated", "run_id": "fake-run", "task_id": task.id, "native_reward": 1.0,
            "structural_outcome": {"status": "pass", "reason": "complete_structural_evaluation"},
            "client_halt_reason": None, "usage_incomplete": False, "unknown_usage_calls": 0,
            "input_tokens": 10, "output_tokens": 2}
        save(output / "request.json", request)
        save(output / "outcome.json", outcome)
        save(output / "trajectory.json", [])
        save(output / "simulation.json", {"id": "fake-run", "task_id": task.id, "messages": [],
                                         "reward_info": {"reward": 1.0}})
        (output / "calls.jsonl").write_text("", encoding="utf-8")
        return outcome

    def test_saved_measurement_replays_and_detects_source_tampering(self):
        with patch("tau_feedback.subscription_episode.run_subscription_episode", side_effect=self.fake):
            result = self.runner.run(self.example, strategy="Follow policy.", seed=233, phase="validation", candidate_id="fixture")
        self.assertEqual(result.score, 1)
        row = self.runner.rows[0]
        verify_measurement(row, self.example, "Follow policy.", 233)
        path = Path(row["directory"]) / "environment/request.json"
        value = load(path)
        value["simulation_seed"] = 239
        save(path, value)
        with self.assertRaises(ResearchPause):
            verify_measurement(row, self.example, "Follow policy.", 233)

    def test_error_is_attempted_record_and_latches_no_retry(self):
        with patch("tau_feedback.subscription_episode.run_subscription_episode", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.runner.run(self.example, strategy="Follow policy.", seed=233, phase="validation", candidate_id="fixture")
        self.assertEqual(self.runner.attempted, 1)
        self.assertEqual(self.runner.rows[0]["status"], "stopped")
        with self.assertRaises(ResearchPause):
            self.runner.run(self.example, strategy="Follow policy.", seed=233, phase="validation", candidate_id="fixture")


if __name__ == "__main__":
    unittest.main()
