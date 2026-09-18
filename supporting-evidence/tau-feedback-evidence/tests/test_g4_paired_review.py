"""Independent G4 binding counterexamples; fake transport only, no model calls."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import test_g4_paired as base
from tau_feedback.contracts import canonical_hash
from tau_feedback.subscription_evidence import save

g4 = base.g4


class G4PairedReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.PairedTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        for name in ("tau_feedback.subscription_client._create_windows_process",
                     "tau_feedback.subscription_recovery_client._run_bounded",
                     "tau_feedback.subscription_episode.run_subscription_episode"):
            guard = patch(name, side_effect=AssertionError("A real process/episode is forbidden in this review"))
            guard.start()
            self.addCleanup(guard.stop)

    def proposal(self, **changes):
        f = self.fixture
        args = dict(client=base.FakeClient("Follow policy and obtain explicit confirmation."),
                    parents=f.parents, baseline_validation=f.baseline,
                    trainset=[f.fixture.example], valset=f.val,
                    train_seed=101, validation_seeds=[211, 223], arm="B1",
                    candidate_id="review-only", output=f.root / "proposal",
                    episode_limits={}, max_strategy_chars=6000)
        args.update(changes)
        with patch.object(g4, "MeasurementRunner", base.FakeMeasurement), patch.object(g4, "verify_measurement"):
            return g4.run_paired_proposal(**args)

    def test_shared_parent_seed_must_equal_child_training_seed(self):
        with self.assertRaises((ValueError, g4.ResearchPause)):
            self.proposal(train_seed=102)

    def test_repeated_validation_key_is_not_two_observations(self):
        with self.assertRaises((ValueError, g4.ResearchPause)):
            self.proposal(valset=self.fixture.val * 2)

    def test_train_validation_overlap_is_rejected_before_proposal(self):
        example = self.fixture.fixture.example
        with self.assertRaises((ValueError, g4.ResearchPause)):
            self.proposal(valset=[example], baseline_validation={example.key: {"score": 1.0, "measurement_id": 1}})

    def measurement(self, name, request_overrides):
        example = g4.EpisodeInput("fixture", {"id": "fixture"})
        strategy, seed = "Follow the fixed policy.", 211
        directory = self.fixture.root / name
        environment = directory / "environment"
        environment.mkdir(parents=True)
        request = {"run_id": "run-fixture", "task_id": example.key,
                   "task_sha256": canonical_hash(example.payload), "strategy": strategy,
                   "simulation_seed": seed, "requested_config": {}, "config": {"seed": seed}}
        request.update(request_overrides)
        outcome = {"status": "evaluated", "task_id": example.key, "run_id": "run-fixture",
                   "client_halt_reason": None, "usage_incomplete": False, "unknown_usage_calls": 0,
                   "native_reward": 1, "structural_outcome": {"status": "pass", "reason": "complete_structural_evaluation"}}
        save(environment / "request.json", request)
        save(environment / "outcome.json", outcome)
        save(environment / "trajectory.json", [])
        save(environment / "simulation.json", {"id": "run-fixture", "task_id": example.key,
             "messages": [], "reward_info": {"reward": 1}})
        (environment / "calls.jsonl").write_text("", encoding="utf-8")
        row = {"status": "complete", "measurement_id": 1, "task_id": example.key,
               "task_payload_sha256": canonical_hash(example.payload), "simulation_seed": seed,
               "phase": "candidate_validation_1", "candidate_id": "review-only",
               "strategy_sha256": g4.text_hash(strategy), "directory": str(directory),
               "actual_new_episode": True, "score": 1.0, "run_id": "run-fixture",
               "artifacts_sha256": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in environment.iterdir()}}
        save(directory / "measurement.json", row)
        return row, example, strategy, seed

    def test_measurement_request_matches_reuse_identity(self):
        for name, change in (("seed", {"simulation_seed": 999}),
                             ("strategy", {"strategy": "A different actual strategy."}),
                             ("task", {"task_sha256": "0" * 64})):
            with self.subTest(field=name):
                values = self.measurement(name, change)
                with self.assertRaises((ValueError, g4.ResearchPause)):
                    g4.verify_measurement(*values)


if __name__ == "__main__":
    unittest.main()
