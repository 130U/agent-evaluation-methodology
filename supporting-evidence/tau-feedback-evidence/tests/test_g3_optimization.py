"""G3 contract tests using explicit fake evidence; no model execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from test_pre_admission_adapter import Fixture, CANDIDATE, POLICY
from tau_feedback.g3_optimization import G3Adapter, source_routes
from tau_feedback.subscription_optimization import ResearchPause


class G3AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = Fixture()
        f = self.fixture
        path = self.root / "calls.jsonl"
        path.write_text("", encoding="utf-8")
        hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()}
        class Runner:
            arm = "B2"
            store = SimpleNamespace(resolve=f.resolver, entries={f.binding.episode_id: (None, None, None, hashes)})
            g3_payloads = {f.example.key: deepcopy(f.example.payload)}
            def __call__(self, *args, **kwargs):
                return f.runner(*args, **kwargs)
            def reserve_batch(self, size):
                pass
        self.runner = Runner()
        self.path = path

    def test_unknown_origin_removes_even_semantically_accepted_feedback(self):
        adapter = G3Adapter(self.runner, output=self.root / "adapter", policy=POLICY)
        batch = adapter.evaluate([self.fixture.example], CANDIDATE, True)
        result = adapter.make_reflective_dataset(CANDIDATE, batch, ["strategy"])
        self.assertEqual(result["strategy"][0]["Feedback"], {"status": "no_admissible_feedback"})
        self.assertEqual(adapter.origin_events[0]["semantic_selected"], [0])
        self.assertEqual(adapter.origin_events[0]["final_selected"], [])
        self.assertEqual(json.loads((self.root / "adapter/reflective-0001.json").read_text()), result)
        self.assertNotIn("HIDDEN_ANSWER_CANARY", json.dumps(result))
        self.assertNotIn("GATE_REASON_CANARY", json.dumps(result))

    def test_b1_does_not_filter_but_receives_identical_public_policy(self):
        self.runner.arm = "B1"
        adapter = G3Adapter(self.runner, output=self.root / "adapter", policy=POLICY)
        batch = adapter.evaluate([self.fixture.example], CANDIDATE, True)
        result = adapter.make_reflective_dataset(CANDIDATE, batch, ["strategy"])
        self.assertEqual(len(result["strategy"][0]["Feedback"]["diagnoses"]), 2)
        self.assertEqual(result["strategy"][0]["Inputs"]["fixed_public_policy"], POLICY)
        self.assertEqual(adapter.origin_events, [])

    def test_mutated_call_ledger_and_task_context_fail_closed(self):
        f = self.fixture
        self.path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ResearchPause):
            source_routes(f.binding, f.pool, f.evidence, self.runner)
        self.path.write_text("", encoding="utf-8")
        self.runner.g3_payloads[f.example.key]["initial_state"] = {"message_history": []}
        with self.assertRaises(ResearchPause):
            source_routes(f.binding, f.pool, f.evidence, self.runner)

    def test_unique_bound_model_action_is_eligible_without_changing_diagnosis(self):
        f = self.fixture
        # Construct a well-formed origin ledger; the binding itself remains
        # separately validated by the real adapter's resolver in production.
        value = {"kind": "message", "content": "Changed it.", "tool_calls": []}
        calls = [{"cli_call_id": "fake-call", "role": "agent_response", "error_type": None,
                  "transport": "codex_cli_structured_action", "structured_output": value}]
        self.path.write_text(json.dumps(calls[0]) + "\n", encoding="utf-8")
        self.runner.store.entries[f.binding.episode_id][3][str(self.path)] = hashlib.sha256(self.path.read_bytes()).hexdigest()
        messages = deepcopy(f.evidence.messages)
        messages[1] = {"role": "assistant", "turn_idx": 30, "content": "Changed it.", "tool_calls": [],
            "raw_data": {"cli_call_id": "fake-call", "transport": "codex_cli_structured_action", "output": value}}
        evidence = SimpleNamespace(messages=messages)
        before = deepcopy(f.pool)
        rows = source_routes(f.binding, f.pool, evidence, self.runner)
        self.assertEqual([r["route"] for r in rows], ["continue_semantic_review"] * 2)
        self.assertEqual(before, f.pool)


if __name__ == "__main__":
    unittest.main()
