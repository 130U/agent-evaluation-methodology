"""Independent boundary tests; these are not model-performance experiments."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tau_feedback.contracts import (
    RequestBinding, strict_json, validate_bound_result, validate_nl_result,
)
from tau_feedback.projection import project_action_context, validate_evidence_references


def payload(expected, values=None, **extra):
    values = [True] * len(expected) if values is None else values
    return json.dumps({"results": [
        {"expectedOutcome": assertion, "metExpectation": value,
         "reasoning": "A structural test, not a true semantic judgment."}
        for assertion, value in zip(expected, values)
    ], **extra})


class ContractRedTeam(unittest.TestCase):
    def test_no_assertions_is_not_applicable_even_without_model_output(self):
        self.assertEqual(validate_nl_result([], "").status, "not_applicable")

    def test_expected_assertions_and_empty_results_are_unknown(self):
        self.assertEqual(validate_nl_result(["must confirm"], '{"results":[]}').status, "unknown")

    def test_complete_negative_is_fail_not_unknown(self):
        self.assertEqual(validate_nl_result(["A", "B"], payload(["A", "B"], [True, False])).status, "fail")

    def test_reordering_preserves_identity(self):
        self.assertEqual(validate_nl_result(["A", "B"], payload(["B", "A"])).status, "pass")

    def test_duplicate_expected_text_uses_multiset(self):
        self.assertEqual(validate_nl_result(["A", "A"], payload(["A", "A"])).status, "pass")
        self.assertEqual(validate_nl_result(["A", "A"], payload(["A", "B"])).status, "unknown")

    def test_repeating_one_result_does_not_cover_another(self):
        self.assertEqual(validate_nl_result(["A", "B"], payload(["A", "A"])).status, "unknown")

    def test_truthy_nonbooleans_are_unknown(self):
        for value in (1, 0, "true", "false", [], {}, None):
            with self.subTest(value=value):
                self.assertEqual(validate_nl_result(["A"], payload(["A"], [value])).status, "unknown")

    def test_nested_duplicate_key_is_rejected(self):
        raw = '{"results":[{"expectedOutcome":"A","metExpectation":false,"metExpectation":true,"reasoning":"x"}]}'
        self.assertEqual(validate_nl_result(["A"], raw).status, "unknown")

    def test_nonfinite_literal_rejected(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                strict_json('{"metadata":' + token + '}')

    def test_numeric_overflow_must_not_create_nonfinite_value(self):
        # parse_constant catches Infinity but Python also creates inf from a
        # finite-looking JSON exponent. The accepted data must remain finite.
        for token in ("1e400", "-1e400"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                strict_json('{"metadata":' + token + '}')

    def test_unpaired_unicode_surrogate_is_unknown(self):
        self.assertEqual(validate_nl_result(["A"], "\ud800").status, "unknown")

    def test_request_binding_detects_each_record_identity_change(self):
        binding = RequestBinding.create(run_id="run-1", task_id="task-1",
            trajectory=[{"role": "user", "content": "x"}], expected=["A"], evaluator_revision="rev")
        for field in ("run_id", "task_id", "trajectory_sha256", "criteria_sha256", "evaluator_revision"):
            with self.subTest(field=field):
                result = validate_bound_result(["A"], payload(["A"]), requested=binding,
                                               received=replace(binding, **{field: "different"}))
                self.assertEqual(result.status, "unknown")

    def test_local_criteria_mismatch_is_rejected(self):
        binding = RequestBinding.create(run_id="r", task_id="t", trajectory=[], expected=["A"], evaluator_revision="rev")
        self.assertEqual(validate_bound_result(["B"], payload(["B"]), requested=binding,
                                               received=binding).status, "unknown")

    def test_structural_binding_does_not_certify_semantics_or_raw_origin(self):
        # Deliberately false prose still meets the declared structural contract.
        # A same-binding replacement is not detectable: this is a documented
        # limitation and must not be promoted to a source-authentication claim.
        binding = RequestBinding.create(run_id="r", task_id="t", trajectory=[], expected=["A"], evaluator_revision="rev")
        raw = '{"results":[{"expectedOutcome":"A","metExpectation":true,"reasoning":"I made this up."}]}'
        self.assertEqual(validate_bound_result(["A"], raw, requested=binding, received=binding).status, "pass")


class ProjectionRedTeam(unittest.TestCase):
    def setUp(self):
        # Native tau ToolCall/ToolMessage model_dump field names, verified in
        # pinned data_model/message.py and utils/llm_utils.py.
        self.messages = [
            {"role": "user", "content": "Check my order", "raw_data": {"secret": "PRIVATE-RAW"}},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-1", "name": "get_order_details", "arguments": {"order_id": "O1"},
                 "requestor": "assistant", "raw_data": "PRIVATE-CALL-METADATA"}]},
            {"role": "tool", "id": "call-1", "content": "Order is delivered",
             "requestor": "assistant", "error": False},
            {"role": "assistant", "content": "I can help with a return."},
            {"role": "user", "content": "FUTURE-ONLY-SECRET"},
        ]
        self.tools = [{"type": "function", "function": {"name": "get_order_details",
                       "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}}}}]

    def view(self, messages=None, action_index=3):
        return project_action_context(messages=self.messages if messages is None else messages,
            action_index=action_index, policy="Obtain confirmation before mutation.", tool_schemas=self.tools)

    def test_raw_metadata_and_future_are_excluded(self):
        serialized = json.dumps(self.view())
        for secret in ("PRIVATE-RAW", "PRIVATE-CALL-METADATA", "FUTURE-ONLY-SECRET"):
            self.assertNotIn(secret, serialized)

    def test_future_and_hidden_metadata_mutations_leave_view_identical(self):
        changed = deepcopy(self.messages)
        changed[4] = {"role": "user", "content": "DIFFERENT-FUTURE", "hidden_task": "another target"}
        changed[0]["raw_data"] = {"ground_truth": "DIFFERENT-PRIVATE"}
        self.assertEqual(self.view(), self.view(changed))

    def test_visible_fact_and_action_mutations_change_view(self):
        changed = deepcopy(self.messages)
        changed[2]["content"] = "Order is pending"
        self.assertNotEqual(self.view()["view_sha256"], self.view(changed)["view_sha256"])
        changed = deepcopy(self.messages)
        changed[3]["content"] = "I have cancelled the order."
        self.assertNotEqual(self.view()["view_sha256"], self.view(changed)["view_sha256"])

    def test_native_tool_result_id_is_preserved(self):
        result = self.view()["history"][2]["message"]
        self.assertEqual(result.get("id", result.get("tool_call_id")), "call-1")

    def test_mismatched_tool_response_rejected(self):
        changed = deepcopy(self.messages)
        changed[2]["id"] = "UNREQUESTED-CALL"
        with self.assertRaises(ValueError):
            self.view(changed)

    def test_user_only_tool_information_is_rejected_or_excluded(self):
        changed = [
            {"role": "user", "content": None, "tool_calls": [
                {"id": "private-1", "name": "user_lookup", "arguments": {"secret": "PRIVATE-USER-TOOL"}, "requestor": "user"}]},
            {"role": "tool", "id": "private-1", "content": "PRIVATE-USER-TOOL-RESULT", "requestor": "user"},
            {"role": "user", "content": "I need help."},
            {"role": "assistant", "content": "How may I help?"},
        ]
        try:
            result = self.view(changed)
        except ValueError:
            return  # Rejecting unsupported dual-control input is in scope.
        self.assertNotIn("PRIVATE-USER-TOOL", json.dumps(result))

    def test_audio_mode_is_rejected_as_unsupported(self):
        changed = deepcopy(self.messages)
        changed[0]["is_audio"] = True
        changed[0]["content"] = "BASE64-AUDIO"
        with self.assertRaises(ValueError):
            self.view(changed)

    def test_projection_is_defensively_copied(self):
        before = deepcopy(self.messages)
        projected = self.view()
        projected["history"][1]["message"]["tool_calls"][0]["arguments"]["order_id"] = "changed"
        self.assertEqual(self.messages, before)

    def test_reference_presence_is_not_entailment(self):
        ok, reason = validate_evidence_references(self.view(), [{"source": "history", "message_index": 0, "quote": "Check my order"}])
        self.assertTrue(ok)
        self.assertIn("semantics_unverified", reason)

    def test_future_reference_is_rejected(self):
        self.assertFalse(validate_evidence_references(self.view(), [{"source": "history", "message_index": 4, "quote": "FUTURE"}])[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
