"""Independent episode boundary tests; fake transport, no real model or score study.

Official task/message/agent types and the actual local generation adapter are
used. The orchestration loop and evaluator are controlled test doubles except
where the official builder itself is explicitly exercised. Temporary artifacts
are separate from every registered experiment and are not research results.
"""
from __future__ import annotations

from copy import deepcopy
import json
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

from tau_feedback.episode import run_episode, structural_outcome
from tau_feedback.local_backend import LocalGenerationBackend
from tau2.agent.llm_agent import LLMAgent
from tau2.data_model.message import UserMessage
from tau2.data_model.simulation import (
    RewardInfo, SimulationRun, TerminationReason, TextRunConfig,
)
from tau2.data_model.tasks import (
    Action, Description, EvaluationCriteria, RewardType, Task, UserScenario,
)
from tau2.runner.build import build_text_orchestrator


LIMITS = {
    "max_calls": 8, "max_generated_tokens": 2048, "max_seconds": 30,
    "max_request_seconds": 10, "agent_temperature": 0,
    "agent_max_tokens": 32, "user_temperature": 0, "user_max_tokens": 32,
    "max_steps": 4, "max_errors": 2, "simulation_seconds": 15,
}
POLICY = "FIXED_POLICY: get explicit consent before changing an order."
SECRET_SCENARIO = "UNIT_HIDDEN_SCENARIO_CANARY"
SECRET_ACTION = "UNIT_REFERENCE_ACTION_CANARY"
SECRET_ARGUMENT = "UNIT_REFERENCE_ARGUMENT_CANARY"
SECRET_GOLD = "UNIT_HIDDEN_ASSERTION_CANARY"
SECRET_DESCRIPTION = "UNIT_EVALUATOR_DESCRIPTION_CANARY"


def task(*, expected=None, basis=None):
    return Task(
        id="unit-task-only",
        description=Description(notes=SECRET_DESCRIPTION),
        user_scenario=UserScenario(instructions=SECRET_SCENARIO),
        evaluation_criteria=EvaluationCriteria(
            actions=[Action(action_id="unit-reference", name=SECRET_ACTION,
                            arguments={"private": SECRET_ARGUMENT})],
            nl_assertions=expected,
            reward_basis=basis if basis is not None else [RewardType.DB],
        ),
    )


def completion(content="Unit-test assistant output"):
    return {"model": "tau-local-qwen", "choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": content}}],
        "usage": {"completion_tokens": 3, "prompt_tokens": 11}}


def nl_record(expected, values=None):
    values = [True] * len(expected) if values is None else values
    content = json.dumps({"results": [
        {"expectedOutcome": text, "metExpectation": value,
         "reasoning": "Controlled structural fixture, not a semantic judgment."}
        for text, value in zip(expected, values)
    ]})
    return {"role": "nl_assertions_eval", "response": completion(content)}


def outcome_simulation(reward=1.0, termination="user_stop"):
    return SimpleNamespace(
        termination_reason=TerminationReason(termination),
        reward_info=None if reward is None else SimpleNamespace(reward=reward),
    )


class FakeRuntime:
    """An in-process transport with no sockets, process or model."""
    def __init__(self, response=None):
        self.response = completion() if response is None else response
        self.requests = []

    def request(self, path, payload, timeout=None):
        self.requests.append((path, deepcopy(payload), timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return deepcopy(self.response)


class FakeEnvironment:
    def __init__(self):
        self.tools = []

    def get_tools(self):
        return self.tools

    def get_user_tools(self, **kwargs):
        return []

    def get_policy(self):
        return POLICY


class ControlledOrchestrator:
    """One visible message through the real actor and fake model transport."""
    def __init__(self):
        self.environment = FakeEnvironment()
        self.agent = LLMAgent(tools=self.environment.get_tools(),
            domain_policy=POLICY, llm=LocalGenerationBackend.MODEL,
            llm_args={"temperature": 0, "max_tokens": 32})
        self.original_agent = self.agent
        self.messages = []

    def run(self):
        visible = UserMessage(role="user", content="Please help with my order.")
        self.messages.append(visible)
        reply, _ = self.agent.generate_next_message(visible, self.agent.get_init_state())
        self.messages.append(reply)
        return SimulationRun(id="unit-simulation", task_id="unit-task-only",
            start_time="2026-09-17T00:00:00Z", end_time="2026-09-17T00:00:01Z",
            duration=1.0, termination_reason=TerminationReason.USER_STOP,
            messages=list(self.messages))

    def get_trajectory(self):
        return self.messages


class EpisodeFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tau-episode-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "attempt"
        self.runtime = FakeRuntime()
        self.orchestrator = ControlledOrchestrator()

    def invoke(self, *, example=None, strategy=None, review=False,
               evaluator=None, reviewer=None):
        example = example or task()
        evaluation = ({"side_effect": evaluator} if evaluator is not None else
                      {"return_value": RewardInfo(reward=1.0)})
        review_args = {"side_effect": reviewer} if reviewer is not None else {}
        with patch("tau2.runner.build.build_text_orchestrator", return_value=self.orchestrator) as builder, \
             patch("tau2.evaluator.evaluator.evaluate_simulation", **evaluation) as score, \
             patch("tau2.runner.batch.run_auto_review", **review_args) as native_review:
            row = run_episode(self.runtime, example, output=self.output, seed=17,
                              limits=deepcopy(LIMITS), strategy=strategy, review=review)
        return row, builder, score, native_review

    def artifact(self, name):
        return json.loads((self.output / name).read_text(encoding="utf-8"))


class ActorBoundaryTests(EpisodeFixture):
    def test_official_builder_keeps_hidden_task_out_of_baseline_actor(self):
        example = task(expected=[SECRET_GOLD])
        config = TextRunConfig(domain="retail", agent="llm_agent", user="user_simulator",
            llm_agent=LocalGenerationBackend.MODEL, llm_user=LocalGenerationBackend.MODEL)
        with patch("tau2.runner.build.build_environment", return_value=FakeEnvironment()):
            built = build_text_orchestrator(config, example, seed=17, simulation_id="unit-build")
        self.assertIs(type(built.agent), LLMAgent)
        self.assertNotIn("task", vars(built.agent))
        # This proves the fixture actually contains hidden user information.
        self.assertIn(SECRET_SCENARIO, str(built.user.instructions))
        actor_state = built.agent.get_init_state().model_dump_json()
        for secret in (SECRET_SCENARIO, SECRET_ACTION, SECRET_ARGUMENT,
                       SECRET_GOLD, SECRET_DESCRIPTION):
            self.assertNotIn(secret, actor_state)
        self.assertIn(POLICY, actor_state)

    def test_baseline_uses_original_agent_and_no_hidden_transport_input(self):
        row, builder, _, reviewer = self.invoke(example=task(expected=[SECRET_GOLD]))
        self.assertEqual(row["status"], "evaluated")
        self.assertIs(self.orchestrator.agent, self.orchestrator.original_agent)
        config = builder.call_args.args[0]
        self.assertEqual(config.agent, "llm_agent")
        self.assertFalse(config.auto_review)
        self.assertEqual(config.hallucination_retries, 0)
        reviewer.assert_not_called()
        self.assertEqual(len(self.runtime.requests), 1)
        serialized = json.dumps(self.runtime.requests[0][1])
        for secret in (SECRET_SCENARIO, SECRET_ACTION, SECRET_ARGUMENT,
                       SECRET_GOLD, SECRET_DESCRIPTION):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("<reusable_strategy>", serialized)
        self.assertEqual(self.runtime.requests[0][1]["messages"][0]["content"],
                         self.orchestrator.original_agent.system_prompt)

    def test_strategy_is_exact_append_with_unchanged_policy_tools_and_model(self):
        strategy = "Check consent before mutations. Preserve exact tool identifiers."
        original = self.orchestrator.original_agent
        original_prompt = original.system_prompt
        self.invoke(strategy=strategy)
        actor = self.orchestrator.agent
        self.assertEqual(actor.system_prompt, original_prompt +
                         "\n<reusable_strategy>\n" + strategy + "\n</reusable_strategy>")
        self.assertEqual(actor.domain_policy, original.domain_policy)
        self.assertEqual(actor.tools, original.tools)
        self.assertEqual(actor.llm, original.llm)
        self.assertEqual(actor.llm_args, original.llm_args)
        sent_prompt = self.runtime.requests[0][1]["messages"][0]["content"]
        self.assertEqual(sent_prompt, actor.system_prompt)

    def test_original_task_and_limits_are_not_mutated(self):
        example = task(expected=[SECRET_GOLD])
        before_task, before_limits = example.model_dump(mode="json"), deepcopy(LIMITS)
        self.invoke(example=example, strategy="Follow the fixed policy.")
        self.assertEqual(example.model_dump(mode="json"), before_task)
        self.assertEqual(LIMITS, before_limits)

    def test_evaluator_receives_original_task_and_strict_replay(self):
        example = task()
        _, _, evaluate, _ = self.invoke(example=example)
        args = evaluate.call_args.kwargs
        self.assertIs(args["task"], example)
        self.assertTrue(args["strict_replay"])
        self.assertFalse(args["solo_mode"])
        self.assertEqual(args["domain"], "retail")
        self.assertEqual(args["simulation"].policy, POLICY)

    def test_existing_attempt_cannot_be_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "outcome.json"
        sentinel.write_text("immutable sentinel", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.invoke()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "immutable sentinel")
        self.assertEqual(self.runtime.requests, [])


class StructuralOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.expected = ["Ask for consent", "Explain the consequence"]
        self.example = task(expected=self.expected, basis=[RewardType.DB, RewardType.NL_ASSERTION])

    def test_expected_nl_without_call_is_unknown_even_with_native_one(self):
        result = structural_outcome(self.example, outcome_simulation(), [])
        self.assertEqual(result["status"], "unknown")

    def test_empty_nl_results_are_unknown_not_vacuous_success(self):
        record = nl_record([])
        result = structural_outcome(self.example, outcome_simulation(), [record])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "coverage_count_mismatch")

    def test_missing_or_repeated_assertion_identity_is_unknown(self):
        for assertions in ([self.expected[0]], [self.expected[0]] * 2,
                           [self.expected[0], "Unrelated assertion"]):
            with self.subTest(assertions=assertions):
                result = structural_outcome(self.example, outcome_simulation(), [nl_record(assertions)])
                self.assertEqual(result["status"], "unknown")

    def test_nl_retry_or_multiple_responses_is_unknown(self):
        result = structural_outcome(self.example, outcome_simulation(),
                                    [nl_record(self.expected), nl_record(self.expected)])
        self.assertEqual(result["status"], "unknown")

    def test_nl_error_is_unknown_even_if_response_present(self):
        record = nl_record(self.expected)
        record["error"] = "Unit timeout"
        self.assertEqual(structural_outcome(self.example, outcome_simulation(), [record])["status"], "unknown")

    def test_invalid_nl_json_or_types_are_unknown(self):
        bad_contents = ("not json", None, '{"results":[]}',
                        nl_record(self.expected, [1, True])["response"]["choices"][0]["message"]["content"])
        for content in bad_contents:
            record = {"role": "nl_assertions_eval", "response": completion(content)}
            with self.subTest(content=content):
                self.assertEqual(structural_outcome(self.example, outcome_simulation(), [record])["status"], "unknown")

    def test_complete_valid_nl_and_binary_reward_are_preserved(self):
        for reward, values, status in ((1.0, [True, True], "pass"),
                                       (0.0, [True, False], "fail"),
                                       (0.0, [True, True], "fail")):
            with self.subTest(reward=reward):
                result = structural_outcome(self.example, outcome_simulation(reward),
                                            [nl_record(self.expected, values)])
                self.assertEqual(result["status"], status)

    def test_explicit_nl_failure_cannot_pass_with_contradictory_native_one(self):
        result = structural_outcome(self.example, outcome_simulation(1.0),
                                    [nl_record(self.expected, [True, False])])
        self.assertIn(result["status"], {"fail", "unknown"},
                      "A complete negative NL judgment must not be upgraded to pass")

    def test_no_nl_expectations_is_not_missing_evaluation(self):
        for expected in (None, []):
            example = task(expected=expected, basis=[RewardType.DB, RewardType.NL_ASSERTION])
            with self.subTest(expected=expected):
                self.assertEqual(structural_outcome(example, outcome_simulation(), [])["status"], "pass")

    def test_no_nl_expectations_cannot_upgrade_other_component_failure(self):
        example = task(expected=[], basis=[RewardType.DB, RewardType.NL_ASSERTION])
        self.assertEqual(structural_outcome(example, outcome_simulation(0.0), [])["status"], "fail")

    def test_nongating_nl_field_does_not_create_missing_gating_call(self):
        example = task(expected=self.expected, basis=[RewardType.DB])
        self.assertEqual(structural_outcome(example, outcome_simulation(), [])["status"], "pass")

    def test_missing_or_nonbinary_reward_is_unknown(self):
        for reward in (None, 0.5, -1, 2, float("nan"), float("inf")):
            with self.subTest(reward=reward):
                self.assertEqual(structural_outcome(task(), outcome_simulation(reward), [])["status"], "unknown")

    def test_premature_termination_never_becomes_pass_from_native_reward(self):
        for reason in TerminationReason:
            if reason in (TerminationReason.AGENT_STOP, TerminationReason.USER_STOP):
                continue
            with self.subTest(reason=reason):
                result = structural_outcome(task(), outcome_simulation(1.0, reason.value), [])
                self.assertEqual(result["status"], "fail")


class FailureRetentionTests(EpisodeFixture):
    def test_evaluation_failure_retains_pre_evaluation_simulation_and_trajectory(self):
        row, _, _, _ = self.invoke(evaluator=RuntimeError("unit evaluator failure"))
        self.assertEqual((row["status"], row["failure_phase"]), ("error", "evaluation"))
        self.assertEqual(row["structural_outcome"]["status"], "unknown")
        simulation = self.artifact("simulation.json")
        self.assertIsNone(simulation["reward_info"])
        self.assertEqual(len(simulation["messages"]), 2)
        self.assertEqual(len(self.artifact("trajectory.json")), 2)
        self.assertEqual(self.artifact("outcome.json"), row)
        self.assertEqual(row["model_calls"], 1)

    def test_transport_failure_retains_partial_trajectory_and_charged_attempt(self):
        self.runtime.response = TimeoutError("unit transport timeout")
        row, _, evaluate, _ = self.invoke()
        self.assertEqual((row["status"], row["failure_phase"]), ("error", "simulation"))
        self.assertEqual(row["error_type"], "TimeoutError")
        self.assertEqual(row["structural_outcome"]["status"], "unknown")
        evaluate.assert_not_called()
        self.assertEqual(len(self.artifact("trajectory.json")), 1)
        self.assertEqual(row["model_calls"], 1)
        self.assertEqual(row["charged_generated_tokens"], LIMITS["agent_max_tokens"])
        self.assertEqual(row["usage_incomplete_calls"], 1)
        record = json.loads((self.output / "calls.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["error_type"], "TimeoutError")
        self.assertEqual(self.artifact("outcome.json"), row)

    def test_native_review_failure_retains_completed_scoring_and_trajectory(self):
        row, _, _, reviewer = self.invoke(review=True, reviewer=RuntimeError("unit review failure"))
        reviewer.assert_called_once()
        self.assertEqual((row["status"], row["failure_phase"]), ("error", "native_review"))
        self.assertEqual(row["native_reward"], 1.0)
        self.assertEqual(row["structural_outcome"]["status"], "pass")
        self.assertEqual(self.artifact("simulation.json")["reward_info"]["reward"], 1.0)
        self.assertEqual(len(self.artifact("trajectory.json")), 2)

    def test_guarded_unknown_is_persisted_separately_from_native_success(self):
        example = task(expected=[SECRET_GOLD], basis=[RewardType.DB, RewardType.NL_ASSERTION])
        row, _, _, _ = self.invoke(example=example)
        self.assertEqual(row["status"], "evaluated")
        self.assertEqual(row["native_reward"], 1.0)
        self.assertEqual(row["structural_outcome"]["status"], "unknown")
        self.assertEqual(self.artifact("outcome.json")["structural_outcome"]["status"], "unknown")

    def test_build_failure_has_an_explicit_outcome_and_empty_trajectory(self):
        # Construction failures must remain visible to an outcome-based
        # denominator; a request file alone is not a completed attempt record.
        with patch("tau2.runner.build.build_text_orchestrator",
                   side_effect=RuntimeError("unit environment build failure")):
            try:
                run_episode(self.runtime, task(), output=self.output, seed=17,
                            limits=deepcopy(LIMITS))
            except RuntimeError:
                pass  # Inspect durable evidence even when the API propagates.
        self.assertTrue((self.output / "request.json").exists())
        self.assertTrue((self.output / "outcome.json").exists(),
                        "Construction failure disappears from outcome-based denominators")
        row = self.artifact("outcome.json")
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["structural_outcome"]["status"], "unknown")
        self.assertEqual(row["model_calls"], 0)
        self.assertEqual(self.artifact("trajectory.json"), [])


if __name__ == "__main__":
    unittest.main()
