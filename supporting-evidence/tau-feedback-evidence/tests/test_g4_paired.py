"""Real pinned GEPA and shared adapter; fake model/environment, no online calls."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from test_pre_admission_adapter import Fixture, CANDIDATE, POLICY
import tau_feedback.g4_paired as g4
from tau_feedback.gepa_adapter import EpisodeInput, EpisodeResult


class FakeMeasurement:
    instances = []
    def __init__(self, client, **kwargs):
        self.rows, self.attempted, self.halt_reason = [], 0, None
        self.max_episodes = kwargs["max_episodes"]
        self.instances.append(self)
    def reserve(self, size):
        if self.attempted + size > self.max_episodes:
            raise g4.ResearchPause("fixture cap")
    def run(self, example, *, strategy, seed, phase, candidate_id):
        self.reserve(1)
        self.attempted += 1
        self.rows.append({"task_id": example.key, "strategy": strategy, "seed": seed,
                          "phase": phase, "candidate_id": candidate_id})
        return EpisodeResult(1.0, {"status": "fake"}, {"real_fixture_attempt": self.attempted}, None)


class FakeClient:
    halt_reason = None
    def __init__(self, strategy):
        self.strategy, self.calls = strategy, []
    def generate(self, prompt, schema, *, role):
        self.calls.append(role)
        return SimpleNamespace(output={"text": f"```\n{self.strategy}\n```"},
            usage={"input_tokens": 1, "output_tokens": 1}, call_id="fixture-only")


class PairedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = Fixture()
        self.fixture.episode = replace(self.fixture.episode, score=1.0)
        path = self.root / "calls.jsonl"
        path.write_text("", encoding="utf-8")
        f = self.fixture
        self.runner = SimpleNamespace(privacy_records=[], simulation_seed=101,
            store=SimpleNamespace(resolve=f.resolver, entries={f.binding.episode_id:
                (None, None, None, {str(path): hashlib.sha256(path.read_bytes()).hexdigest()})}))
        self.parents = g4.SharedParents(self.runner, [f.example], CANDIDATE["strategy"], [f.episode])
        self.val = [EpisodeInput("val", {"id": "val"})]
        self.baseline = {"val": {"score": 1.0, "measurement_id": 1}}

    def adapter(self, arm="B1", name="adapter"):
        return g4.PairedAdapter(parents=self.parents, baseline_validation=self.baseline,
            trainset=[self.fixture.example], valset=self.val, measurement_runner=FakeMeasurement(None, max_episodes=3),
            train_seed=101, validation_seed=211, arm=arm, candidate_id=name, output=self.root / name, policy=POLICY)

    def parent_reflection(self, adapter):
        with patch.object(g4, "verify_measurement"):
            adapter.evaluate(self.val, CANDIDATE, False)
        batch = adapter.evaluate([self.fixture.example], CANDIDATE, True)
        return adapter.make_reflective_dataset(CANDIDATE, batch, ["strategy"])

    def test_both_arms_share_exact_source_but_reflection_treatment_differs(self):
        one, two = self.adapter("B1", "b1"), self.adapter("B2", "b2")
        b1, b2 = self.parent_reflection(one), self.parent_reflection(two)
        self.assertEqual(one.references[1]["shared_parent_bindings"], two.references[1]["shared_parent_bindings"])
        self.assertEqual(one.references[1]["actual_new_episodes"], 0)
        self.assertNotEqual(b1, b2)
        self.assertEqual(b1["strategy"][0]["Inputs"], b2["strategy"][0]["Inputs"])
        self.assertEqual(b2["strategy"][0]["Feedback"], {"status": "no_admissible_feedback"})
        self.assertEqual(self.fixture.runner_calls, [])

    def test_shared_parent_mutation_and_task_swap_are_rejected(self):
        with self.assertRaises(g4.ResearchPause):
            self.parents.get(replace(self.fixture.example, payload={"other": True}), CANDIDATE["strategy"])
        self.parents.records[self.fixture.example.key] = replace(self.fixture.episode, score=0)
        with self.assertRaises(g4.ResearchPause):
            self.parents.get(self.fixture.example, CANDIDATE["strategy"])

    def test_changed_bound_evidence_is_rejected(self):
        self.fixture.evidence.messages[0]["content"] = "changed"
        with self.assertRaises(g4.ResearchPause):
            self.parents.get(self.fixture.example, CANDIDATE["strategy"])

    def test_same_text_child_still_executes_fresh_episode(self):
        adapter = self.adapter()
        self.parent_reflection(adapter)
        adapter.evaluate([self.fixture.example], CANDIDATE, True)
        self.assertEqual(adapter.measurement_runner.attempted, 1)
        self.assertEqual(adapter.references[-1]["actual_new_episodes"], 1)

    def test_second_reflection_latches_failure(self):
        adapter = self.adapter()
        self.parent_reflection(adapter)
        with self.assertRaises(g4.ResearchPause):
            adapter.make_reflective_dataset(CANDIDATE, None, ["strategy"])
        with self.assertRaises(g4.ResearchPause):
            adapter.evaluate([self.fixture.example], CANDIDATE, True)

    def test_candidate_payload_swap_is_rejected(self):
        adapter = self.adapter()
        self.parent_reflection(adapter)
        adapter.evaluate([self.fixture.example], {"strategy": "New generic strategy."}, True)
        with self.assertRaises(g4.ResearchPause):
            adapter.evaluate(self.val, {"strategy": "Different text."}, False)

    def test_real_gepa_rejects_perfect_parent_child_but_all_candidate_measurements_run(self):
        client = FakeClient("Ask for explicit confirmation and follow policy.")
        with patch.object(g4, "MeasurementRunner", FakeMeasurement), patch.object(g4, "verify_measurement"):
            summary = g4.run_paired_proposal(client=client, parents=self.parents,
                baseline_validation=self.baseline, trainset=[self.fixture.example], valset=self.val,
                train_seed=101, validation_seeds=[211, 223], arm="B1", candidate_id="batch1-B1",
                output=self.root / "actual-gepa", episode_limits={}, max_strategy_chars=6000)
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["completion"]["endpoint"], "candidate_rejected")
        self.assertFalse(summary["gepa_selected_new"])
        self.assertEqual(summary["actual_candidate_episodes"], 3)
        self.assertEqual(client.calls, ["gepa_reflection"])
        rows = FakeMeasurement.instances[-1].rows
        self.assertEqual([r["phase"] for r in rows], ["candidate_train", "candidate_validation_1", "candidate_validation_2"])
        self.assertTrue(all(r["strategy"] == client.strategy for r in rows))


if __name__ == "__main__":
    unittest.main()
