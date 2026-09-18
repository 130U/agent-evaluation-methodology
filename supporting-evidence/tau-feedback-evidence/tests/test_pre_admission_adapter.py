"""Fake episodes/stored verdicts only; no model, GEPA optimization or accuracy claim."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gepa.core.adapter import EvaluationBatch
from tau_feedback.admission import audit_diagnosis
from tau_feedback.gepa_adapter import EpisodeInput, EpisodeResult, TauFeedbackAdapter
from tau_feedback.pre_admission_adapter import (
    BoundAdmission, COMMON_CONTEXT, COMMON_OUTPUT, EpisodeBinding, HostEvidence,
    PreAdmissionAdapter, native_feedback, trajectory_reference,
)


CANDIDATE = {"strategy": "Follow the fixed policy and confirm consequential changes."}
POLICY = "Ask for explicit confirmation before changing an address. POLICY_CANARY"
TOOLS = [{"name": "modify_address", "description": "TOOL_SCHEMA_CANARY"}]


def verdict(decision="accept", **changes):
    return {"decision": decision, "claim_supported": decision == "accept",
            "agent_responsible": True, "correction_observable": True,
            "references": [{"source": "policy", "quote": "Ask for explicit confirmation"},
                           {"source": "history", "message_index": 0, "quote": "Please change my address."}],
            "reason": "GATE_REASON_CANARY", **changes}


def diagnosis(reason="The address was changed without confirmation for PRIVATE_PERSON.", **changes):
    return {"source": "agent", "turn_idx": 30, "error_type": "confirmation",
            "severity": "high", "error_tags": ["policy"], "reasoning": reason,
            "correct_behavior": "Ask for explicit confirmation before changing the address.", **changes}


class Fixture:
    def __init__(self, *, key="case-alpha", episode_id="episode-alpha", pool=None, decisions=None):
        self.example = EpisodeInput(key, {"request": "retail", "hidden_answer": "HIDDEN_ANSWER_CANARY",
                                          "user_id": "PRIVATE_PERSON"},
                                    reflection_context={"summary": "UNSAFE_CONTEXT_CANARY"})
        self.messages = [
            {"role": "user", "turn_idx": 10, "content": "Please change my address.",
             "raw_data": {"private": "RAW_HIDDEN_CANARY"}},
            {"role": "assistant", "turn_idx": 30, "content": None,
             "tool_calls": [{"id": "change-1", "name": "modify_address", "arguments": {"address": "NEW_ADDRESS_CANARY"}}]},
            {"role": "tool", "turn_idx": 40, "id": "change-1", "content": "FUTURE_RESULT_CANARY"},
            {"role": "user", "turn_idx": 50, "content": "FUTURE_USER_CANARY"},
        ]
        self.pool = ([diagnosis(), diagnosis("REJECTED_DIAGNOSIS_CANARY")] if pool is None else deepcopy(pool))
        self.binding = EpisodeBinding.create(self.example, strategy=CANDIDATE["strategy"], episode_id=episode_id,
                                             messages=self.messages, diagnoses=self.pool)
        self.fake_judge_calls = []
        records = []
        choices = (["accept", "reject"] if decisions is None else decisions)
        for index, diag in enumerate(self.pool):
            def fake_judge(instruction, payload, value=verdict(choices[index])):
                self.fake_judge_calls.append(deepcopy(payload))
                return json.dumps(value)
            record = audit_diagnosis(diagnosis=diag, messages=self.messages, policy=POLICY,
                                     tool_schemas=TOOLS, judge=fake_judge)
            records.append(BoundAdmission(self.binding, index, record,
                                          f"stored-call-{index}" if record.judge_called else None))
        self.evidence = HostEvidence(self.binding, POLICY, deepcopy(self.messages), deepcopy(TOOLS),
                                     tuple(reversed(records)), ("NEW_ADDRESS_CANARY",))
        self.episode = EpisodeResult(0.0, {"summary": "OUTPUT_SUMMARY_CANARY REJECTED_DIAGNOSIS_CANARY GATE_REASON_CANARY"},
                                     trajectory_reference(self.binding), native_feedback(self.binding, self.pool))
        self.runner_calls, self.resolver_calls = [], []

    def runner(self, example, *, strategy, capture_traces):
        self.runner_calls.append((deepcopy(example), strategy, capture_traces))
        return self.episode

    def resolver(self, binding):
        self.resolver_calls.append(binding)
        if binding != self.binding:
            raise ValueError("Unknown explicit binding")
        return self.evidence

    def adapter(self, arm="B2"):
        return PreAdmissionAdapter(self.runner, resolver=self.resolver, arm=arm)

    def run(self, arm="B2"):
        adapter = self.adapter(arm)
        batch = adapter.evaluate([self.example], CANDIDATE, capture_traces=True)
        result = adapter.make_reflective_dataset(CANDIDATE, batch, ["strategy"])
        return adapter, batch, result["strategy"][0]


class ProjectionAndSelectionTests(unittest.TestCase):
    def test_b1_and_b2_use_the_same_super_projection_with_only_selected_raw_diagnoses(self):
        fixture = Fixture()
        observed = []
        original = TauFeedbackAdapter.make_reflective_dataset
        def spy(adapter, candidate, batch, components):
            observed.append((adapter.arm, deepcopy(batch.trajectories[0].episode.feedback)))
            return original(adapter, candidate, batch, components)
        with patch.object(TauFeedbackAdapter, "make_reflective_dataset", spy):
            _, _, b1 = fixture.run("B1")
            _, _, b2 = fixture.run("B2")
        self.assertEqual(observed, [("B1", {"diagnoses": fixture.pool}),
                                    ("B2", {"diagnoses": fixture.pool[:1]})])
        self.assertEqual(b2["Feedback"]["diagnoses"], b1["Feedback"]["diagnoses"][:1])
        self.assertEqual(b1["Inputs"], b2["Inputs"])
        self.assertEqual(b1["Generated Outputs"], b2["Generated Outputs"])
        self.assertEqual(b1["Score"], b2["Score"])

    def test_rejected_reason_privileged_inputs_and_summary_cannot_bypass_selection(self):
        fixture = Fixture()
        _, batch, record = fixture.run()
        serialized = json.dumps(record)
        for marker in ("REJECTED_DIAGNOSIS_CANARY", "GATE_REASON_CANARY", "POLICY_CANARY", "TOOL_SCHEMA_CANARY",
                       "RAW_HIDDEN_CANARY", "HIDDEN_ANSWER_CANARY", "FUTURE_RESULT_CANARY", "FUTURE_USER_CANARY",
                       "UNSAFE_CONTEXT_CANARY", "OUTPUT_SUMMARY_CANARY", "NEW_ADDRESS_CANARY", "PRIVATE_PERSON",
                       "stored-call", "episode-alpha", "case-alpha"):
            self.assertNotIn(marker, serialized, marker)
        self.assertEqual(record["Generated Outputs"], COMMON_OUTPUT)
        self.assertEqual(record["Inputs"], COMMON_CONTEXT)
        self.assertEqual(batch.outputs, [COMMON_OUTPUT])
        self.assertEqual(batch.trajectories[0].episode.trajectory, trajectory_reference(fixture.binding))
        self.assertNotIn("POLICY_CANARY", json.dumps(batch.trajectories[0].episode.trajectory))

    def test_raw_turn_index_reaches_host_gate_before_projection(self):
        fixture = Fixture()
        self.assertEqual(fixture.fake_judge_calls[0]["action_view"]["action_index"], 1)
        self.assertEqual(fixture.pool[0]["turn_idx"], 30)
        initial_calls = deepcopy(fixture.fake_judge_calls)
        adapter, _, record = fixture.run()
        self.assertEqual(record["Feedback"]["diagnoses"][0]["turn_idx"], "[REDACTED_NUMBER]")
        self.assertEqual(fixture.fake_judge_calls, initial_calls, "Stored decisions are replayed, not re-judged")
        self.assertEqual(adapter.admission_events[0]["selected_indices"], [0])
        self.assertEqual(adapter.admission_events[0]["judge_call_ids"], ["stored-call-0", "stored-call-1"])

    def test_no_accepted_diagnosis_is_explicit_abstention_without_native_fallback(self):
        fixture = Fixture(decisions=["reject", "abstain"])
        adapter, _, record = fixture.run()
        self.assertEqual(record["Feedback"], {"status": "no_admissible_feedback"})
        self.assertEqual(adapter.feedback_events[0]["status"], "no_admissible_feedback")
        self.assertEqual(adapter.admission_events[0]["selected_indices"], [])

    def test_b1_needs_no_admission_records_and_does_not_interpret_empty_as_success(self):
        fixture = Fixture(pool=[])
        adapter, _, record = fixture.run("B1")
        self.assertEqual(record["Feedback"], {"status": "no_native_diagnoses"})
        self.assertEqual(record["Score"], 0.0)
        self.assertEqual(adapter.admission_events[0]["judge_call_ids"], [])
        fixture = Fixture()
        fixture.evidence = replace(fixture.evidence, admissions=())
        self.assertEqual(len(fixture.run("B1")[2]["Feedback"]["diagnoses"]), 2)

    def test_empty_native_pool_is_identical_in_both_arms(self):
        fixture = Fixture(pool=[])
        b1=fixture.run("B1")[2]
        b2=fixture.run("B2")[2]
        self.assertEqual(b1,b2)
        self.assertEqual(b2["Feedback"], {"status": "no_native_diagnoses"})

    def test_validation_requires_no_reviewer_or_resolver_and_strips_raw_output(self):
        fixture = Fixture()
        fixture.episode = replace(fixture.episode, trajectory=None, feedback=None, score=1.0, metric_calls=2)
        adapter = fixture.adapter()
        batch = adapter.evaluate([fixture.example], CANDIDATE)
        self.assertEqual(batch.scores, [1.0])
        self.assertEqual(batch.outputs, [COMMON_OUTPUT])
        self.assertIsNone(batch.trajectories)
        self.assertEqual(batch.num_metric_calls, 2)
        self.assertEqual(fixture.resolver_calls, [])

    def test_original_batch_and_host_evidence_are_unchanged(self):
        fixture = Fixture()
        adapter = fixture.adapter()
        batch = adapter.evaluate([fixture.example], CANDIDATE, capture_traces=True)
        before = deepcopy((batch, fixture.evidence, fixture.episode))
        adapter.make_reflective_dataset(CANDIDATE, batch, ["strategy"])
        self.assertEqual((batch, fixture.evidence, fixture.episode), before)

    def test_explicit_records_support_duplicate_diagnoses_with_distinct_indices(self):
        fixture = Fixture(pool=[diagnosis(), diagnosis()])
        _, _, record = fixture.run()
        self.assertEqual(len(record["Feedback"]["diagnoses"]), 1)

    def test_batch_order_and_gate_record_order_do_not_bind_the_wrong_episode(self):
        first = Fixture()
        second = Fixture(key="case-beta", episode_id="episode-beta", decisions=["reject", "accept"])
        fixtures = {first.binding: first, second.binding: second}
        by_key = {fixture.example.key: fixture for fixture in fixtures.values()}
        adapter = PreAdmissionAdapter(lambda case, **kwargs: by_key[case.key].episode,
                                      resolver=lambda binding: fixtures[binding].evidence, arm="B2")
        batch = adapter.evaluate([second.example, first.example], CANDIDATE, capture_traces=True)
        result = adapter.make_reflective_dataset(CANDIDATE, batch, ["strategy"])["strategy"]
        self.assertIn("REJECTED_DIAGNOSIS_CANARY", result[0]["Feedback"]["diagnoses"][0]["reasoning"])
        self.assertNotIn("REJECTED_DIAGNOSIS_CANARY", result[1]["Feedback"]["diagnoses"][0]["reasoning"])
        self.assertEqual([e["binding"]["episode_id"] for e in adapter.admission_events], ["episode-beta", "episode-alpha"])


class BindingAndVerdictTests(unittest.TestCase):
    def assert_rejected(self, fixture, *, at_evaluate=False):
        adapter = fixture.adapter()
        if at_evaluate:
            with self.assertRaises((ValueError, TypeError)):
                adapter.evaluate([fixture.example], CANDIDATE, capture_traces=True)
        else:
            batch = adapter.evaluate([fixture.example], CANDIDATE, capture_traces=True)
            with self.assertRaises((ValueError, TypeError)):
                adapter.make_reflective_dataset(CANDIDATE, batch, ["strategy"])
        self.assertEqual(adapter.admission_events, [])

    def test_task_payload_or_key_swap_is_rejected(self):
        for changes in ({"key": "other-task"}, {"payload": {"request": "different"}}):
            with self.subTest(changes=changes):
                fixture = Fixture()
                fixture.example = replace(fixture.example, **changes)
                self.assert_rejected(fixture, at_evaluate=True)

    def test_bound_strategy_and_pool_cannot_be_changed(self):
        for field in ("strategy_sha256", "diagnoses_sha256", "trajectory_sha256", "episode_id"):
            with self.subTest(field=field):
                fixture = Fixture()
                fixture.episode.feedback["binding"][field] = "different-episode" if field == "episode_id" else "f" * 64
                self.assert_rejected(fixture, at_evaluate=True)
        fixture = Fixture()
        fixture.episode.feedback["diagnoses"][0]["reasoning"] += " modified"
        self.assert_rejected(fixture, at_evaluate=True)

    def test_raw_or_extended_trajectory_reference_is_rejected(self):
        for raw in ([], {"messages": []}):
            fixture = Fixture()
            fixture.episode = replace(fixture.episode, trajectory=raw)
            self.assert_rejected(fixture, at_evaluate=True)
        fixture = Fixture()
        fixture.episode.trajectory["policy"] = POLICY
        self.assert_rejected(fixture, at_evaluate=True)

    def test_unknown_or_incomplete_native_review_cannot_be_empty_feedback(self):
        for feedback in (None, {}, {"status": "no_errors"}):
            fixture = Fixture()
            fixture.episode = replace(fixture.episode, feedback=feedback)
            self.assert_rejected(fixture, at_evaluate=True)
        fixture = Fixture(pool=[])
        fixture.episode.feedback["review_status"] = "unknown"
        self.assert_rejected(fixture, at_evaluate=True)

    def test_host_evidence_binding_or_transcript_mismatch_is_rejected(self):
        fixture = Fixture()
        fixture.evidence = replace(fixture.evidence, binding=replace(fixture.binding, episode_id="other"))
        self.assert_rejected(fixture)
        fixture = Fixture()
        fixture.evidence.messages[-1]["content"] += " Changed future."
        self.assert_rejected(fixture)

    def test_policy_or_tool_change_invalidates_stored_view(self):
        for changes in ({"policy": POLICY + " New rule."}, {"tool_schemas": TOOLS + [{"name": "new_tool"}]}):
            with self.subTest(changes=changes):
                fixture = Fixture()
                fixture.evidence = replace(fixture.evidence, **changes)
                self.assert_rejected(fixture)

    def test_missing_duplicate_out_of_range_or_boolean_record_index_is_rejected(self):
        for kind in ("missing", "duplicate", "outside", "boolean"):
            with self.subTest(kind=kind):
                fixture = Fixture()
                records = list(fixture.evidence.admissions)
                if kind == "missing":
                    records.pop()
                elif kind == "duplicate":
                    records[0] = records[1]
                else:
                    records[0] = replace(records[0], diagnosis_index=2 if kind == "outside" else True)
                fixture.evidence = replace(fixture.evidence, admissions=tuple(records))
                self.assert_rejected(fixture)

    def test_each_gate_record_is_bound_to_episode_and_diagnosis(self):
        for kind in ("episode", "diagnosis", "view"):
            with self.subTest(kind=kind):
                fixture = Fixture()
                item = fixture.evidence.admissions[0]
                if kind == "episode":
                    item = replace(item, binding=replace(item.binding, episode_id="other"))
                else:
                    item = replace(item, record=replace(item.record, **{kind + "_sha256": "f" * 64}))
                fixture.evidence = replace(fixture.evidence, admissions=(item, fixture.evidence.admissions[1]))
                self.assert_rejected(fixture)

    def test_accept_flag_alone_cannot_override_stored_reject_verdict(self):
        fixture = Fixture()
        item = fixture.evidence.admissions[0]
        self.assertEqual(item.record.decision, "reject")
        item = replace(item, record=replace(item.record, decision="accept"))
        fixture.evidence = replace(fixture.evidence, admissions=(item, fixture.evidence.admissions[1]))
        self.assert_rejected(fixture)

    def test_accepted_record_requires_complete_acceptable_located_verdict(self):
        for changes in (None, {"claim_supported": False}, {"references": [{"source": "policy", "quote": "not in policy"}]},
                        {"references": [{"source": "history", "message_index": 3, "quote": "FUTURE_USER_CANARY"}]}):
            with self.subTest(changes=changes):
                fixture = Fixture()
                item = fixture.evidence.admissions[1]
                self.assertEqual(item.record.decision, "accept")
                replacement = None if changes is None else {**item.record.verdict, **changes}
                item = replace(item, record=replace(item.record, verdict=replacement))
                fixture.evidence = replace(fixture.evidence, admissions=(fixture.evidence.admissions[0], item))
                self.assert_rejected(fixture)

    def test_completed_judge_call_requires_explicit_call_id(self):
        fixture = Fixture()
        item = replace(fixture.evidence.admissions[1], judge_call_id=None)
        fixture.evidence = replace(fixture.evidence, admissions=(fixture.evidence.admissions[0], item))
        self.assert_rejected(fixture)

    def test_structural_abstention_keeps_none_view_and_no_judge_call(self):
        fixture = Fixture(pool=[diagnosis(turn_idx=999)], decisions=["accept"])
        self.assertEqual(fixture.fake_judge_calls, [])
        item = fixture.evidence.admissions[0]
        self.assertIsNone(item.record.view_sha256)
        self.assertFalse(item.record.judge_called)
        _, _, record = fixture.run()
        self.assertEqual(record["Feedback"], {"status": "no_admissible_feedback"})
        fixture.evidence = replace(fixture.evidence, admissions=(replace(item, judge_call_id="fake-call"),))
        self.assert_rejected(fixture)

    def test_invalid_raw_verdict_can_abstain_but_cannot_accept(self):
        fixture = Fixture(pool=[diagnosis()], decisions=["accept"])
        record = audit_diagnosis(diagnosis=fixture.pool[0], messages=fixture.messages, policy=POLICY,
                                 tool_schemas=TOOLS, judge=lambda *_: "not JSON")
        self.assertEqual(record.reason, "invalid_judge_json")
        fixture.evidence = replace(fixture.evidence, admissions=(BoundAdmission(fixture.binding, 0, record, "failed-json-call"),))
        self.assertEqual(fixture.run()[2]["Feedback"], {"status": "no_admissible_feedback"})

    def test_unknown_fields_and_nonagent_pool_are_rejected_before_binding(self):
        for changes in ({"source": "user"}, {"hidden_gold": "secret"}, {"reasoning": ""}, {"correct_behavior": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Fixture(pool=[diagnosis(**changes)])

    def test_evaluation_batch_tampering_is_rejected(self):
        for kind in ("score", "output", "alignment", "strategy"):
            with self.subTest(kind=kind):
                fixture = Fixture()
                adapter = fixture.adapter()
                batch = adapter.evaluate([fixture.example], CANDIDATE, capture_traces=True)
                candidate = CANDIDATE
                if kind == "score":
                    batch.scores[0] = 1.0
                elif kind == "output":
                    batch.outputs[0]["summary"] = "bypass"
                elif kind == "alignment":
                    batch.outputs.append({})
                else:
                    candidate = {"strategy": "Another general strategy."}
                with self.assertRaises(ValueError):
                    adapter.make_reflective_dataset(candidate, batch, ["strategy"])

    def test_empty_component_update_uses_parent_contract_and_does_not_resolve(self):
        fixture = Fixture()
        adapter = fixture.adapter()
        self.assertEqual(adapter.make_reflective_dataset(CANDIDATE, EvaluationBatch([], []), []), {})
        self.assertEqual(fixture.resolver_calls, [])
        with self.assertRaises(ValueError):
            adapter.make_reflective_dataset({"strategy": ""}, EvaluationBatch([], []), [])


if __name__ == "__main__":
    unittest.main()
