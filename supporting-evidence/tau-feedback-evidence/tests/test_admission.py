"""Admission structure only; fake judges do not establish semantic accuracy."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tau_feedback.admission import audit_diagnosis, select_admitted
from tau_feedback.local_backend import LocalBudgetExceeded


POLICY = "Before changing an address, ask for explicit confirmation."
TOOLS = [{"type": "function", "function": {"name": "modify_address", "parameters": {
    "type": "object", "properties": {"address": {"type": "string"}}, "required": ["address"]}}}]


def diagnosis(**changes):
    return {"source": "agent", "turn_idx": 30,
            "reasoning": "The agent changed the address without confirmation.",
            "correct_behavior": "Ask for confirmation before changing the address.", **changes}


def transcript():
    return [
        {"role": "user", "turn_idx": 10, "content": "I want to change my address.",
         "raw_data": {"hidden": "RAW_HIDDEN_CANARY"}},
        {"role": "assistant", "turn_idx": 20, "content": None,
         "tool_calls": [{"id": "lookup-1", "name": "lookup", "arguments": {}, "requestor": "assistant"}]},
        {"role": "tool", "turn_idx": 25, "id": "lookup-1", "requestor": "assistant", "content": "Current address: 1 Main St"},
        {"role": "assistant", "turn_idx": 30, "content": None,
         "tool_calls": [{"id": "change-1", "name": "modify_address", "arguments": {"address": "2 Main St"}, "requestor": "assistant"}]},
        {"role": "tool", "turn_idx": 40, "id": "change-1", "requestor": "assistant", "content": "FUTURE_RESULT_CANARY"},
        {"role": "user", "turn_idx": 50, "content": "FUTURE_USER_CANARY"},
    ]


def verdict(**changes):
    return {"decision": "accept", "claim_supported": True, "agent_responsible": True,
            "correction_observable": True,
            "references": [{"source": "policy", "quote": POLICY},
                           {"source": "history", "message_index": 0, "quote": "I want to change my address."}],
            "reason": "Test-only verdict; semantic truth was not established.", **changes}


class FakeJudge:
    def __init__(self, output=None, error=None):
        self.output = json.dumps(verdict()) if output is None else output
        self.error = error
        self.calls = []

    def __call__(self, instruction, payload):
        self.calls.append((instruction, deepcopy(payload)))
        if self.error is not None:
            raise self.error
        return self.output


class AdmissionContracts(unittest.TestCase):
    def audit(self, *, diag=None, messages=None, policy=POLICY, judge=None):
        return audit_diagnosis(diagnosis=diagnosis() if diag is None else diag,
                              messages=transcript() if messages is None else messages,
                              policy=policy, tool_schemas=deepcopy(TOOLS), judge=judge or FakeJudge())

    def test_valid_fake_verdict_is_only_a_model_judgement_with_located_references(self):
        record = self.audit()
        self.assertEqual(record.decision, "accept")
        self.assertEqual(record.reason, "model_judgement_with_located_references")
        self.assertTrue(record.judge_called)

    def test_actual_turn_index_is_resolved_instead_of_used_as_array_position(self):
        judge = FakeJudge()
        record = self.audit(judge=judge)
        self.assertEqual(record.decision, "accept")
        view = judge.calls[0][1]["action_view"]
        self.assertEqual(view["action_index"], 3)
        self.assertEqual(view["action"]["tool_calls"][0]["id"], "change-1")

    def test_raw_metadata_and_future_tool_user_messages_never_enter_view(self):
        judge = FakeJudge()
        self.audit(judge=judge)
        payload = json.dumps(judge.calls[0][1])
        for canary in ("RAW_HIDDEN_CANARY", "FUTURE_RESULT_CANARY", "FUTURE_USER_CANARY"):
            self.assertNotIn(canary, payload)

    def test_future_mutation_does_not_change_action_view_hash(self):
        before = self.audit()
        messages = transcript()
        messages[-1]["content"] = "A completely different future preference"
        after = self.audit(messages=messages)
        self.assertEqual(before.view_sha256, after.view_sha256)

    def test_policy_change_changes_action_view_hash(self):
        before = self.audit()
        after = self.audit(policy=POLICY + " New public rule.")
        self.assertNotEqual(before.view_sha256, after.view_sha256)

    def test_nonagent_and_ambiguous_sources_do_not_call_judge(self):
        for source in ("user", "unknown", None):
            with self.subTest(source=source):
                judge = FakeJudge()
                result = self.audit(diag=diagnosis(source=source), judge=judge)
                self.assertEqual(result.decision, "abstain")
                self.assertFalse(judge.calls)

    def test_empty_claim_or_correction_abstains_before_judging(self):
        for field in ("reasoning", "correct_behavior"):
            with self.subTest(field=field):
                judge = FakeJudge()
                result = self.audit(diag=diagnosis(**{field: " "}), judge=judge)
                self.assertEqual(result.decision, "abstain")
                self.assertFalse(judge.calls)

    def test_unknown_hidden_diagnosis_fields_cannot_reenter_selected_feedback(self):
        result = self.audit(diag=diagnosis(hidden_gold={"answer": "PRIVATE_GOLD_CANARY"}))
        self.assertEqual(result.decision, "abstain")

    def test_nonserializable_diagnosis_abstains_instead_of_hashing_exception(self):
        result = self.audit(diag={"source": "agent", "extra": {1, 2}})
        self.assertEqual(result.decision, "abstain")

    def test_nonfinite_diagnosis_value_abstains_instead_of_hashing_exception(self):
        result = self.audit(diag=diagnosis(severity=float("nan")))
        self.assertEqual(result.decision, "abstain")

    def test_nonmapping_message_abstains_instead_of_attribute_error(self):
        messages = transcript()
        messages[0] = None
        result = self.audit(messages=messages)
        self.assertEqual(result.decision, "abstain")

    def test_nonlist_transcript_abstains_instead_of_iteration_error(self):
        result = audit_diagnosis(diagnosis=diagnosis(), messages=None, policy=POLICY,
                                 tool_schemas=TOOLS, judge=FakeJudge())
        self.assertEqual(result.decision, "abstain")

    def test_boolean_message_index_cannot_match_integer_diagnosis_index(self):
        messages = [{"role": "user", "turn_idx": 0, "content": "I want to change my address."},
                    {"role": "assistant", "turn_idx": True, "content": "Address changed."}]
        result = self.audit(diag=diagnosis(turn_idx=1), messages=messages)
        self.assertEqual(result.decision, "abstain")

    def test_missing_duplicate_or_noninteger_diagnosis_index_abstains(self):
        for index in (None, "30", True, 999):
            with self.subTest(index=index):
                self.assertEqual(self.audit(diag=diagnosis(turn_idx=index)).decision, "abstain")
        messages = transcript()
        messages[-1]["turn_idx"] = 30
        self.assertEqual(self.audit(messages=messages).decision, "abstain")

    def test_judge_budget_failure_propagates_for_outer_accounting(self):
        judge = FakeJudge(error=LocalBudgetExceeded("budget exhausted"))
        with self.assertRaises(LocalBudgetExceeded):
            self.audit(judge=judge)
        self.assertEqual(len(judge.calls), 1)

    def test_nonhashable_judge_decision_abstains(self):
        judge = FakeJudge(json.dumps(verdict(decision=["accept"])))
        self.assertEqual(self.audit(judge=judge).decision, "abstain")

    def test_excessively_nested_judge_json_abstains(self):
        judge = FakeJudge("[" * 1500 + "0" + "]" * 1500)
        self.assertEqual(self.audit(judge=judge).decision, "abstain")

    def test_duplicate_keys_and_nonboolean_flags_abstain(self):
        duplicate = json.dumps(verdict())[:-1] + ', "decision":"accept"}'
        self.assertEqual(self.audit(judge=FakeJudge(duplicate)).decision, "abstain")
        for value in (1, "true", None, False):
            with self.subTest(value=value):
                output = json.dumps(verdict(agent_responsible=value))
                self.assertEqual(self.audit(judge=FakeJudge(output)).decision, "abstain")

    def test_future_reference_and_missing_policy_reference_abstain(self):
        future = [{"source": "policy", "quote": POLICY},
                  {"source": "history", "message_index": 4, "quote": "FUTURE_RESULT_CANARY"}]
        self.assertEqual(self.audit(judge=FakeJudge(json.dumps(verdict(references=future)))).decision, "abstain")
        only_history = [{"source": "history", "message_index": 0, "quote": "I want to change my address."}]
        self.assertEqual(self.audit(judge=FakeJudge(json.dumps(verdict(references=only_history)))).decision, "abstain")

    def test_judge_cannot_mutate_evidence_then_validate_its_own_new_quote(self):
        def mutating_judge(instruction, payload):
            payload["action_view"]["policy"] += " INJECTED_FUTURE_GOLD"
            return json.dumps(verdict(references=[{"source": "policy", "quote": "INJECTED_FUTURE_GOLD"}]))
        self.assertEqual(self.audit(judge=mutating_judge).decision, "abstain")

    def test_false_flags_do_not_override_native_nonagent_source(self):
        judge = FakeJudge(json.dumps(verdict(agent_responsible=True)))
        result = self.audit(diag=diagnosis(source="user"), judge=judge)
        self.assertEqual(result.decision, "abstain")
        self.assertFalse(judge.calls)

    def test_selector_rejects_a_record_bound_to_another_diagnosis(self):
        record = self.audit()
        with self.assertRaises(ValueError):
            select_admitted([diagnosis(reasoning="A different claim")], [record])

    def test_selector_requires_expected_view_binding_and_preserves_native_diagnosis(self):
        native = diagnosis()
        record = self.audit(diag=native)
        selected = select_admitted([native], [record], expected_view_hashes=[record.view_sha256])
        self.assertEqual(selected, [native])
        self.assertIsNot(selected[0], native)

    def test_selector_rejects_same_claim_record_from_another_policy_view(self):
        record_a = self.audit()
        record_b = self.audit(policy=POLICY + " Another task's public policy.")
        with self.assertRaises(ValueError):
            select_admitted([diagnosis()], [record_a], expected_view_hashes=[record_b.view_sha256])


if __name__ == "__main__":
    unittest.main()
