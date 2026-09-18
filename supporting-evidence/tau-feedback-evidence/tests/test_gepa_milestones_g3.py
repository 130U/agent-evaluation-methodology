"""G3 official pinned GEPA execution with fake episodes/reflection; no model."""
from __future__ import annotations

from dataclasses import replace
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import gepa
from gepa.utils.stop_condition import MaxCandidateProposalsStopper
from gepa.strategies.instruction_proposal import InstructionProposalSignature
from tau_feedback.gepa_adapter import EpisodeInput, EpisodeResult, TauFeedbackAdapter
from tau_feedback.gepa_milestones_g3 import G3Milestones, MilestoneError


class Bomb:
    def __str__(self):
        raise AssertionError("Privileged payload must not be read or serialized")


class PoisonedEvents(G3Milestones):
    def _observe(self, name, event):
        event = dict(event)
        for key in ("dataset", "reflective_dataset", "inputs", "outputs", "trajectories", "prompts",
                    "raw_lm_outputs", "state", "final_state", "outputs_by_val_id", "scores_by_val_id"):
            event[key] = Bomb()
        super()._observe(name, event)


class G3MilestoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fake-gepa-milestones-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.counter = 0
        self.calls, self.reflection_calls = [], []
        for target in ("subprocess.Popen",
                       "tau_feedback.subscription_client._create_windows_process",
                       "tau_feedback.subscription_client.CliTextClient.generate"):
            guard = patch(target, side_effect=AssertionError("Real model/process forbidden in G3 unit tests"))
            guard.start()
            self.addCleanup(guard.stop)

    def execute(self, mode="improve", *, max_calls=8, recorder_class=G3Milestones, batch_size=1,
                skip_perfect_score=False, acceptance_criterion="strict_improvement"):
        self.counter += 1
        directory = self.root / str(self.counter)
        recorder = recorder_class(directory / "milestones", max_metric_calls=max_calls, skip_perfect_score=False)
        def episode(example, *, strategy, capture_traces):
            self.calls.append((example.key, capture_traces))
            score = 0.0
            if mode == "perfect" or mode == "best_seed" and example.key.startswith("validation"):
                score = 1.0
            elif mode in {"improve", "best_seed"} and strategy == "Improved general strategy.":
                score = 1.0
            return EpisodeResult(score, {"status": "fixture"}, [{"content": "fixture"}], {"reason": "Check policy."})
        def reflection(prompt):
            self.reflection_calls.append(prompt)
            if mode == "reflection_error":
                raise RuntimeError("Fake reflection failure")
            return "```\nImproved general strategy.\n```"
        class Adapter(TauFeedbackAdapter):
            def make_reflective_dataset(self, *args, **kwargs):
                if mode == "dataset_error":
                    raise ValueError("Fake dataset failure")
                return super().make_reflective_dataset(*args, **kwargs)
        with redirect_stdout(io.StringIO()):
            result = gepa.optimize(seed_candidate={"strategy": "Follow the policy."},
                trainset=[EpisodeInput(f"training-{i}", {}) for i in range(batch_size)],
                valset=[EpisodeInput(f"validation-{i}", {}) for i in range(batch_size)],
                adapter=Adapter(episode), reflection_lm=reflection, reflection_minibatch_size=batch_size,
                callbacks=[recorder], stop_callbacks=[MaxCandidateProposalsStopper(1)], max_metric_calls=max_calls,
                skip_perfect_score=skip_perfect_score, acceptance_criterion=acceptance_criterion, raise_on_exception=True, use_merge=False, cache_evaluation=False,
                display_progress_bar=False, write_agent_state=False, run_dir=str(directory / "gepa"))
        return recorder, result

    def test_accepted_and_rejected_generated_counts_come_from_proposal_events(self):
        for mode, endpoint, pool_count in (("improve", "candidate_accepted_best_new", 2),
                                            ("reject", "candidate_rejected", 1),
                                            ("best_seed", "candidate_accepted_best_seed", 2)):
            with self.subTest(mode=mode):
                recorder, result = self.execute(mode)
                summary = recorder.classify_complete(result)
                self.assertEqual(summary["endpoint"], endpoint)
                self.assertEqual(summary["generated_candidates"], 1)
                self.assertEqual(summary["proposal_starts"], 1)
                self.assertEqual(summary["optimizer_iterations"], 1)
                self.assertEqual(len(result.candidates), pool_count)
                self.assertEqual(summary["total_metric_calls"], 3 if mode == "reject" else 4)

    def test_perfect_parent_really_reflects_then_strictly_rejects(self):
        recorder, result = self.execute("perfect")
        summary = recorder.classify_complete(result)
        self.assertEqual(summary["schema_version"], "gepa-milestones-g3-v1")
        self.assertEqual(summary["endpoint"], "candidate_rejected")
        self.assertTrue(summary["parent_all_perfect"])
        self.assertIs(summary["registered_skip_perfect_score"], False)
        self.assertEqual(summary["skip_reasons"], [])
        self.assertEqual(summary["generated_candidates"], 1)
        self.assertEqual(summary["proposal_starts"], 1)
        self.assertEqual(summary["accepted_candidates"], 0)
        self.assertEqual(summary["rejected_candidates"], 1)
        self.assertEqual(summary["total_metric_calls"], 3)
        self.assertEqual(len(self.reflection_calls), 1)
        self.assertEqual(result.best_candidate, {"strategy": "Follow the policy."})
        # Persisted facts must stay identical to actual captured callback facts.
        ends = [e for e in recorder.events if e["event"] == "evaluation_end"]
        self.assertTrue(ends[0]["all_perfect"])
        self.assertEqual([e["score_sum"] for e in ends], [1.0, 1.0])
        persisted = [json.loads(line) for line in (recorder.output / "events.jsonl").read_text().splitlines()]
        self.assertEqual(persisted, recorder.events)

    def test_false_setting_is_explicit_and_type_checked_before_directory_write(self):
        for value in (True, None, 0, "", "false"):
            with self.subTest(value=value):
                target = self.root / ("bad-" + repr(value))
                with self.assertRaises(MilestoneError):
                    G3Milestones(target, max_metric_calls=8, skip_perfect_score=value)
                self.assertFalse(target.exists())
        with self.assertRaises(TypeError):
            G3Milestones(self.root / "omitted", max_metric_calls=8)

    def test_actual_upstream_skip_is_not_a_g3_completed_endpoint(self):
        recorder, result = self.execute("perfect", skip_perfect_score=True)
        self.assertEqual(self.reflection_calls, [])
        self.assertTrue(any(e["event"] == "evaluation_skipped" for e in recorder.events))
        with self.assertRaises(MilestoneError):
            recorder.classify_complete(result)

    def test_actual_nonstrict_acceptance_of_perfect_parent_is_rejected(self):
        recorder, result = self.execute("perfect", acceptance_criterion="improvement_or_equal")
        self.assertEqual(len(self.reflection_calls), 1)
        self.assertTrue(any(e["event"] == "candidate_accepted" for e in recorder.events))
        with self.assertRaises(MilestoneError):
            recorder.classify_complete(result)

    def test_two_and_three_task_batch_counts_are_consistent(self):
        for size in (2, 3):
            for mode, multiplier in (("improve", 4), ("perfect", 3)):
                with self.subTest(mode=mode, batch_size=size):
                    recorder, result = self.execute(mode, max_calls=4 * size, batch_size=size)
                    summary = recorder.classify_complete(result)
                    self.assertEqual(summary["total_metric_calls"], multiplier * size)
                    self.assertEqual(summary["generated_candidates"], 1)

    def test_metric_budget_after_seed_is_distinct_from_completed_proposal(self):
        recorder, result = self.execute(max_calls=1)
        summary = recorder.classify_complete(result)
        self.assertEqual(summary["endpoint"], "baseline_only_metric_budget")
        self.assertEqual(summary["optimizer_iterations"], 0)
        self.assertEqual(summary["generated_candidates"], 0)

    def test_swallowed_native_reflection_failure_cannot_be_classified_complete(self):
        recorder, result = self.execute("reflection_error")
        self.assertEqual(len(self.reflection_calls), 2, "Native GEPA retry, all calls fake")
        with self.assertRaises(MilestoneError):
            recorder.classify_complete(result)
        self.assertIsNotNone(recorder.failure)
        self.assertFalse((recorder.output / "classification.json").exists())

    def test_swallowed_native_dataset_failure_cannot_be_classified_complete(self):
        recorder, result = self.execute("dataset_error")
        with self.assertRaises(MilestoneError):
            recorder.classify_complete(result)

    def test_swallowed_extractor_failure_after_model_return_is_rejected(self):
        with patch.object(InstructionProposalSignature, "output_extractor", side_effect=ValueError("Fake extractor failure")):
            recorder, result = self.execute()
        self.assertEqual(len(self.reflection_calls), 2)
        self.assertEqual(sum(e["event"] == "proposal_start" for e in recorder.events), 1)
        self.assertEqual(sum(e["event"] == "proposal_end" for e in recorder.events), 0)
        with self.assertRaises(MilestoneError):
            recorder.classify_complete(result)

    def test_missing_duplicate_or_unknown_skip_events_fail_closed(self):
        for modification in ("missing_end", "duplicate_proposal", "unexpected_skip", "wrong_metric", "rejection_score"):
            with self.subTest(modification=modification):
                recorder, result = self.execute("reject" if modification == "rejection_score" else "improve")
                if modification == "missing_end":
                    recorder.events.pop()
                elif modification == "duplicate_proposal":
                    proposal = next(e for e in recorder.events if e["event"] == "proposal_end")
                    recorder.events.insert(1, dict(proposal))
                elif modification == "unexpected_skip":
                    recorder.events.insert(-1, {"event": "evaluation_skipped", "iteration": 1})
                elif modification == "rejection_score":
                    next(e for e in recorder.events if e["event"] == "candidate_rejected")["new_score"] = 1.0
                else:
                    result = replace(result, total_metric_calls=7)
                with self.assertRaises(MilestoneError):
                    recorder.classify_complete(result)

    def test_callback_recording_failure_is_latched_despite_upstream_exception_isolation(self):
        recorder, result = self.execute()
        with patch.object(Path, "open", side_effect=OSError("Fake metadata I/O failure")):
            recorder.on_iteration_start({"iteration": 2, "state": Bomb()})
        self.assertIsNotNone(recorder.failure)
        with self.assertRaises(MilestoneError):
            recorder.classify_complete(result)

    def test_privileged_payloads_are_never_inspected_or_serialized(self):
        recorder, result = self.execute(recorder_class=PoisonedEvents)
        summary = recorder.classify_complete(result)
        self.assertEqual(summary["endpoint"], "candidate_accepted_best_new")
        text = (recorder.output / "events.jsonl").read_text(encoding="utf-8")
        for disallowed in ('"dataset":', '"state":', '"inputs":', '"outputs":', '"prompts":', '"raw_lm_outputs":',
                           '"trajectory":', "Improved general strategy.", "Follow the policy."):
            self.assertNotIn(disallowed, text)
        self.assertEqual(len([json.loads(line) for line in text.splitlines()]), len(recorder.events))

    def test_result_candidate_mismatch_and_metric_overrun_are_rejected(self):
        for modification in ("candidate", "cap"):
            with self.subTest(modification=modification):
                recorder, result = self.execute()
                if modification == "candidate":
                    result = replace(result, candidates=[result.candidates[0], {"strategy": "Changed after the callback."}])
                else:
                    recorder.max_metric_calls = 3
                with self.assertRaises(MilestoneError):
                    recorder.classify_complete(result)


if __name__ == "__main__":
    unittest.main()
