"""Origin attribution counterexamples; fabricated ledgers, no model calls."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tau_feedback.action_origin import (OriginContext, ORCHESTRATOR_SHA256,
    INITIAL_GREETING, classify_action_origin, strategy_feedback_eligibility)


class OriginTests(unittest.TestCase):
    def setUp(self):
        self.context = OriginContext(ORCHESTRATOR_SHA256, False, 0)
        self.seed = {"role": "assistant", "turn_idx": 0, "content": INITIAL_GREETING,
                     "tool_calls": None, "raw_data": None, "usage": None, "cost": 0.0}
        self.user = {"call_index": 0, "role": "user_simulator_response", "request": {
            "participant": "user_simulator_response", "messages": [{"role": "user", "content": INITIAL_GREETING}]}}
        value = {"kind": "message", "content": INITIAL_GREETING, "tool_calls": []}
        self.record = {"call_index": 1, "cli_call_id": "case-call", "role": "agent_response",
            "transport": "codex_cli_structured_action", "structured_output": value}
        self.generated = {"role": "assistant", "turn_idx": 2, "content": INITIAL_GREETING,
            "tool_calls": None, "raw_data": {"cli_call_id": "case-call", "output": deepcopy(value),
                                             "transport": "codex_cli_structured_action"}}

    def classify(self, message, calls):
        return classify_action_origin(message, calls, self.context)

    def test_framework_seed_is_routed_outside_strategy(self):
        result = self.classify(self.seed, [self.user])
        self.assertEqual(result["origin"], "framework_seed")
        self.assertEqual(strategy_feedback_eligibility(result), "route_to_harness_or_non_agent_review")

    def test_identical_generated_text_is_not_classified_by_text(self):
        result = self.classify(self.generated, [self.user, self.record])
        self.assertEqual(result["origin"], "model_agent")
        self.assertEqual(strategy_feedback_eligibility(result), "continue_semantic_review")

    def test_seed_needs_positive_context_and_first_call(self):
        for calls in ([], [self.record], [{**self.user, "call_index": 1}]):
            with self.subTest(calls=calls):
                self.assertEqual(self.classify(self.seed, calls)["origin"], "unknown")
        for context in (OriginContext("unverified", False, 0), OriginContext(ORCHESTRATOR_SHA256, True, 0),
                        OriginContext(ORCHESTRATOR_SHA256, False, 1)):
            self.assertEqual(classify_action_origin(self.seed, [self.user], context)["origin"], "unknown")

    def test_forged_generation_cannot_override_initializer(self):
        self.generated["turn_idx"] = 0
        self.assertEqual(self.classify(self.generated, [self.record])["origin"], "unknown")

    def test_missing_duplicate_wrong_role_and_changed_output(self):
        variants = [[], [self.record, self.record], [{**self.record, "role": "user_simulator_response"}],
                    [{**self.record, "error_type": "Interrupted"}]]
        for records in variants:
            self.assertEqual(self.classify(self.generated, records)["origin"], "unknown")
        self.generated["content"] = "Changed after generation"
        self.assertEqual(self.classify(self.generated, [self.record])["origin"], "unknown")

    def test_tool_identity_and_arguments_are_bound(self):
        value = {"kind": "tool_calls", "content": None,
                 "tool_calls": [{"name": "lookup", "arguments_json": '{"id":"A"}'}]}
        self.record["structured_output"] = value
        self.generated.update(content=None, tool_calls=[{"id": "cli_case-call_0", "name": "lookup",
                              "arguments": {"id": "A"}, "requestor": "assistant"}])
        self.generated["raw_data"]["output"] = deepcopy(value)
        self.assertEqual(self.classify(self.generated, [self.record])["origin"], "model_agent")
        self.generated["tool_calls"][0]["arguments"]["id"] = "B"
        self.assertEqual(self.classify(self.generated, [self.record])["origin"], "unknown")

    def test_unknown_does_not_become_non_agent_or_false_editability(self):
        result = self.classify({}, [])
        self.assertIsNone(result["strategy_editable"])
        self.assertEqual(strategy_feedback_eligibility(result), "abstain_origin_unknown")
        self.assertEqual(self.classify({"role": "user"}, [])["origin"], "non_agent")

    def test_malformed_role_or_first_request_abstains(self):
        self.assertEqual(self.classify({"role": []}, [])["origin"], "unknown")
        malformed = {**self.user, "request": []}
        self.assertEqual(self.classify(self.seed, [malformed])["origin"], "unknown")


if __name__ == "__main__":
    unittest.main()
