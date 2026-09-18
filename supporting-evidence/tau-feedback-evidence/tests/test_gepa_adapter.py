"""Fake-episode integration/contract tests. No model or optimization is run."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import inspect
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import gepa
from gepa.core.adapter import EvaluationBatch, invoke_batch_evaluate
from gepa.strategies.instruction_proposal import InstructionProposalSignature
from tau_feedback.gepa_adapter import (
    COMMON_REFLECTION_PROMPT, UPSTREAM_COMMIT, CapturedEpisode, EpisodeFailure,
    EpisodeInput, EpisodeResult, FeedbackDecision, TauFeedbackAdapter,
)


STRATEGY = {"strategy": "Follow the fixed policy and confirm consequential changes."}
FEEDBACK = {"description": "Ask for explicit confirmation before modifying an order.", "source": "agent"}


def example(key="development_case", **kwargs):
    return EpisodeInput(key=key, payload={"request": "a retail workflow"}, **kwargs)


class RecordingRunner:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or EpisodeResult(
            score=0.0, output={"text": "I changed the order."},
            trajectory=[{"role": "assistant", "content": "I changed the order."}],
            feedback=FEEDBACK,
        )

    def __call__(self, case, *, strategy, capture_traces):
        self.calls.append((case, strategy, capture_traces))
        return self.result


def evaluate(runner=None, **adapter_kwargs):
    adapter = TauFeedbackAdapter(runner or RecordingRunner(), **adapter_kwargs)
    batch = adapter.evaluate([example()], STRATEGY, capture_traces=True)
    return adapter, batch


class SourceAndOfficialContractTests(unittest.TestCase):
    def test_import_is_fixed_vendor_checkout(self):
        self.assertEqual(Path(gepa.__file__).resolve(), (ROOT / "vendor/gepa/src/gepa/__init__.py").resolve())
        manifest = json.loads((ROOT / "vendor/gepa/SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["commit"], UPSTREAM_COMMIT)
        self.assertEqual(len(manifest["files"]), 587)
        # All original source bytes are still unchanged after editable installation.
        for entry in manifest["files"]:
            raw = (ROOT / "vendor/gepa" / entry["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"], entry["path"])

    def test_actual_evaluation_batch_and_strategy_only_runner(self):
        runner = RecordingRunner()
        adapter = TauFeedbackAdapter(runner)
        batch = adapter.evaluate([example("first"), example("second")], STRATEGY, capture_traces=True)
        self.assertIsInstance(batch, EvaluationBatch)
        self.assertEqual(len(batch.outputs), 2)
        self.assertEqual(batch.scores, [0.0, 0.0])
        self.assertEqual(batch.num_metric_calls, 2)
        self.assertTrue(all(isinstance(t, CapturedEpisode) for t in batch.trajectories))
        self.assertEqual([call[1:] for call in runner.calls], [(STRATEGY["strategy"], True)] * 2)
        self.assertIsNone(adapter.propose_new_texts)

    def test_official_batch_evaluation_fallback(self):
        adapter = TauFeedbackAdapter(RecordingRunner())
        batches = invoke_batch_evaluate(adapter, [(STRATEGY, [example()]), (STRATEGY, [example()])])
        self.assertEqual([batch.scores for batch in batches], [[0.0], [0.0]])

    def test_no_trace_capture_returns_none(self):
        runner = RecordingRunner(EpisodeResult(1.0, "done", None, None))
        batch = TauFeedbackAdapter(runner).evaluate([example()], STRATEGY)
        self.assertIsNone(batch.trajectories)
        self.assertFalse(runner.calls[0][2])

    def test_trace_capture_requires_trace(self):
        runner = RecordingRunner(EpisodeResult(1.0, "done", None, None))
        with self.assertRaisesRegex(ValueError, "trajectory"):
            TauFeedbackAdapter(runner).evaluate([example()], STRATEGY, capture_traces=True)

    def test_candidate_cannot_change_policy_or_tools(self):
        for candidate in ({}, {"strategy": ""}, {"strategy": None}, {"policy": "new"},
                          {"strategy": "valid", "tools": "new"}):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                TauFeedbackAdapter(RecordingRunner()).evaluate([example()], candidate)

    def test_runner_cannot_mutate_original_case(self):
        original = example()
        def mutating_runner(case, **kwargs):
            case.payload["request"] = "MUTATED"
            return RecordingRunner().result
        TauFeedbackAdapter(mutating_runner).evaluate([original], STRATEGY)
        self.assertEqual(original.payload["request"], "a retail workflow")

    def test_result_is_detached_from_runner_owned_objects(self):
        runner = RecordingRunner()
        adapter, batch = evaluate(runner)
        runner.result.output["text"] = "changed later"
        self.assertEqual(batch.outputs[0]["text"], "I changed the order.")
        adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])

    def test_explicit_metric_call_count(self):
        adapter, batch = evaluate(RecordingRunner(replace(RecordingRunner().result, metric_calls=3)))
        self.assertEqual(batch.num_metric_calls, 3)
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                evaluate(RecordingRunner(replace(RecordingRunner().result, metric_calls=invalid)))

    def test_bad_scores_are_systemic_not_fabricated_zero_or_success(self):
        for invalid in (None, True, "1", math.nan, math.inf, -0.01, 1.01):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                evaluate(RecordingRunner(replace(RecordingRunner().result, score=invalid)))

    def test_declared_episode_failure_retains_attempt_and_details(self):
        def failed(case, **kwargs):
            raise EpisodeFailure("invalid_tool_arguments", "The exact order identifier was changed.")
        adapter, batch = evaluate(failed)
        self.assertEqual(batch.scores, [0.0])
        self.assertEqual(batch.num_metric_calls, 1)
        self.assertEqual(batch.outputs[0]["code"], "invalid_tool_arguments")
        self.assertIn("identifier", batch.trajectories[0].episode.trajectory["details"])
        self.assertEqual(adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])["strategy"][0]["Score"], 0.0)

    def test_systemic_exceptions_propagate(self):
        def broken(case, **kwargs):
            raise RuntimeError("model server not configured")
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            evaluate(broken)

    def test_opaque_task_payload_is_refused_before_runner_call(self):
        runner = RecordingRunner()
        with self.assertRaisesRegex(TypeError, "JSON mapping"):
            TauFeedbackAdapter(runner).evaluate([EpisodeInput("case", object())], STRATEGY)
        self.assertEqual(runner.calls, [])

    def test_bare_string_literal_registry_is_rejected(self):
        runner = RecordingRunner()
        with self.assertRaisesRegex(ValueError, "bare string"):
            TauFeedbackAdapter(runner).evaluate([example(protected_literals="SECRET")], STRATEGY)
        self.assertEqual(runner.calls, [])


class ReflectionBoundaryTests(unittest.TestCase):
    def test_b1_native_feedback_is_default(self):
        adapter, batch = evaluate()
        records = adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])
        self.assertEqual(records["strategy"][0]["Feedback"], FEEDBACK)
        self.assertEqual(adapter.arm, "B1")

    def test_b2_requires_named_injected_filter(self):
        for kwargs in ({"arm": "B2"}, {"arm": "B2", "feedback_filter": lambda view: None},
                       {"arm": "B1", "feedback_filter": lambda view: None}, {"arm": "B3"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TauFeedbackAdapter(RecordingRunner(), **kwargs)

    def test_b2_does_not_change_evaluation_scores_or_outputs(self):
        b1, original = evaluate()
        b2, treatment = evaluate(arm="B2", filter_name="test_only", feedback_filter=lambda view: FeedbackDecision("Filtered advice"))
        self.assertEqual(original.scores, treatment.scores)
        self.assertEqual(original.outputs, treatment.outputs)
        self.assertEqual(original.num_metric_calls, treatment.num_metric_calls)
        self.assertEqual(b2.make_reflective_dataset(STRATEGY, treatment, ["strategy"])["strategy"][0]["Feedback"], "Filtered advice")

    def test_abstention_never_falls_back_to_native_feedback(self):
        adapter, batch = evaluate(arm="B2", filter_name="reject_all_test_only", feedback_filter=lambda view: FeedbackDecision(None, "insufficient evidence"))
        record = adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])["strategy"][0]
        self.assertEqual(record["Feedback"], {"status": "no_admissible_feedback"})
        self.assertEqual(adapter.feedback_events[-1]["status"], "no_admissible_feedback")

    def test_invalid_filter_result_raises_without_fallback(self):
        adapter, batch = evaluate(arm="B2", filter_name="bad_test_only", feedback_filter=lambda view: None)
        with self.assertRaises(TypeError):
            adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])

    def test_private_fields_and_free_text_literals_not_in_reflection(self):
        case = EpisodeInput("case_private", payload={"hidden_gold": {"answer": "GOLD_CANARY"},
                                                    "user_id": "user_93718"},
                            reflection_context="retail case_private for user_93718",
                            protected_literals=("Private Person",))
        output = {"text": "Private Person on #PROBE17 needs GOLD_CANARY", "order_id": "#PROBE17"}
        feedback = {"description": "For user_93718 use GOLD_CANARY then ask for consent.",
                    "evaluation_criteria": {"answer": "GOLD_CANARY"}, "source": "agent"}
        runner = RecordingRunner(EpisodeResult(0.0, output, [{"raw_data": {"answer": "GOLD_CANARY"},
                                                            "content": "case_private #PROBE17"}], feedback))
        adapter = TauFeedbackAdapter(runner)
        batch = adapter.evaluate([case], STRATEGY, capture_traces=True)
        serialized = json.dumps(adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"]))
        for forbidden in ("case_private", "GOLD_CANARY", "user_93718", "#PROBE17", "Private Person", "evaluation_criteria", "raw_data"):
            self.assertNotIn(forbidden, serialized)
        # Redaction affects reflection only: the exact original tool ID is audited.
        self.assertEqual(batch.outputs[0]["order_id"], "#PROBE17")

    def test_filter_receives_common_projected_view_and_is_reprojected(self):
        seen = []
        def filter_(view):
            seen.append(view)
            return FeedbackDecision({"description": "SECRET_CANARY #CASE42"})
        case = EpisodeInput("#CASE42", payload={"gold": "SECRET_CANARY"})
        result = EpisodeResult(0.0, "SECRET_CANARY", [{"answer": "SECRET_CANARY"}], "SECRET_CANARY")
        adapter = TauFeedbackAdapter(RecordingRunner(result), arm="B2", feedback_filter=filter_, filter_name="test_only")
        batch = adapter.evaluate([case], STRATEGY, capture_traces=True)
        records = adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])
        self.assertNotIn("SECRET_CANARY", repr(seen[0]))
        self.assertNotIn("SECRET_CANARY", json.dumps(records))
        self.assertNotIn("#CASE42", json.dumps(records))

    def test_filter_cannot_mutate_unfiltered_output_or_raw_trace(self):
        def mutate(view):
            view.output["text"] = "MUTATED"
            view.trajectory[0]["content"] = "MUTATED"
            return FeedbackDecision("advice")
        adapter, batch = evaluate(arm="B2", feedback_filter=mutate, filter_name="test_only")
        record = adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])["strategy"][0]
        self.assertEqual(record["Generated Outputs"]["text"], "I changed the order.")
        self.assertEqual(batch.trajectories[0].episode.trajectory[0]["content"], "I changed the order.")

    def test_action_positions_survive_common_privacy_projection(self):
        result = EpisodeResult(0.0, {"amount": 139.99},
                               [{"message_index": 4, "step_index": 2, "action_index": 4,
                                 "order_id": "#PROBE17"}], FEEDBACK)
        seen = []
        def filter_(view):
            seen.append(view)
            return FeedbackDecision(view.native_feedback)
        adapter, batch = evaluate(RecordingRunner(result), arm="B2", feedback_filter=filter_, filter_name="test_only")
        record = adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])["strategy"][0]
        self.assertEqual(seen[0].trajectory, [{"message_index": 4, "step_index": 2, "action_index": 4}])
        self.assertEqual(record["Generated Outputs"]["amount"], "[REDACTED_NUMBER]")

    def test_raw_trajectory_is_not_a_side_channel_to_reflection(self):
        result = replace(RecordingRunner().result, trajectory=[{"content": "UNFILTERED_TRACE_CANARY"}])
        for kwargs in ({}, {"arm": "B2", "filter_name": "reject_test_only",
                           "feedback_filter": lambda view: FeedbackDecision(None)}):
            with self.subTest(arm=kwargs.get("arm", "B1")):
                adapter, batch = evaluate(RecordingRunner(result), **kwargs)
                data = adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])
                self.assertNotIn("UNFILTERED_TRACE_CANARY", json.dumps(data))
                self.assertIn("UNFILTERED_TRACE_CANARY", str(batch.trajectories[0].episode.trajectory))

    def test_private_literal_in_current_strategy_refuses_reflection(self):
        candidate = {"strategy": "Remember SECRET_CANARY"}
        adapter = TauFeedbackAdapter(RecordingRunner())
        batch = adapter.evaluate([EpisodeInput("case", payload={"hidden_gold": "SECRET_CANARY"})], candidate, capture_traces=True)
        with self.assertRaisesRegex(ValueError, "protected instance"):
            adapter.make_reflective_dataset(candidate, batch, ["strategy"])

    def test_misaligned_and_wrong_candidate_traces_rejected(self):
        adapter, batch = evaluate()
        with self.assertRaises(ValueError):
            adapter.make_reflective_dataset({"strategy": "Different strategy"}, batch, ["strategy"])
        with self.assertRaises(ValueError):
            adapter.make_reflective_dataset(STRATEGY, replace(batch, scores=[]), ["strategy"])
        with self.assertRaises(ValueError):
            adapter.make_reflective_dataset(STRATEGY, replace(batch, trajectories=None), ["strategy"])

    def test_changed_scores_or_outputs_rejected(self):
        adapter, batch = evaluate()
        with self.assertRaises(ValueError):
            adapter.make_reflective_dataset(STRATEGY, replace(batch, scores=[1.0]), ["strategy"])
        with self.assertRaises(ValueError):
            adapter.make_reflective_dataset(STRATEGY, replace(batch, outputs=["changed"]), ["strategy"])

    def test_components_only_strategy(self):
        adapter, batch = evaluate()
        self.assertEqual(adapter.make_reflective_dataset(STRATEGY, batch, []), {})
        for components in (["policy"], ["strategy", "strategy"], ["strategy", "tools"]):
            with self.subTest(components=components), self.assertRaises(ValueError):
                adapter.make_reflective_dataset(STRATEGY, batch, components)

    def test_common_template_works_with_official_renderer_without_lm(self):
        adapter, batch = evaluate()
        data = adapter.make_reflective_dataset(STRATEGY, batch, ["strategy"])["strategy"]
        InstructionProposalSignature.validate_prompt_template(COMMON_REFLECTION_PROMPT)
        prompt = InstructionProposalSignature.prompt_renderer({
            "current_instruction_doc": STRATEGY["strategy"], "dataset_with_feedback": data,
            "prompt_template": COMMON_REFLECTION_PROMPT,
        })
        self.assertIn(STRATEGY["strategy"], prompt)
        self.assertIn(FEEDBACK["description"], prompt)
        self.assertNotIn("<side_info>", prompt)
        self.assertNotIn("<curr_param>", prompt)


class ExampleEntryPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("gepa_example", ROOT / "scripts/run_gepa_example.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_cli_refuses_unconfigured_runner_without_model_calls(self):
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts/run_gepa_example.py")],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Refusing to run", result.stderr)

    def test_example_arguments_bind_to_real_gepa_signature(self):
        # Signature binding invokes neither an optimizer nor a reflection model.
        inspect.signature(gepa.optimize).bind(
            seed_candidate=STRATEGY, trainset=[example()], valset=[example("validation_case")],
            adapter=TauFeedbackAdapter(RecordingRunner()), reflection_lm=lambda prompt: "unused",
            reflection_prompt_template=COMMON_REFLECTION_PROMPT, reflection_minibatch_size=1,
            candidate_selection_strategy="pareto", acceptance_criterion="strict_improvement",
            use_merge=False, cache_evaluation=False, max_metric_calls=10, stop_callbacks=None,
            run_dir="never-created", seed=0, raise_on_exception=True,
            display_progress_bar=False, write_agent_state=True,
        )

    def test_example_refuses_missing_model_or_overlapping_sets_before_optimize(self):
        config = dict(runner=RecordingRunner(), reflection_lm=None, trainset=[example()],
                      valset=[example("validation_case")], seed_strategy=STRATEGY["strategy"],
                      max_metric_calls=10, run_dir="never-created")
        with self.assertRaises(ValueError):
            self.module.run_configured(**config)
        config.update(reflection_lm=lambda prompt: "unused", valset=[example()])
        with self.assertRaisesRegex(ValueError, "disjoint"):
            self.module.run_configured(**config)


if __name__ == "__main__":
    unittest.main()
