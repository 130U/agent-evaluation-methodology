"""G4 holdout-entry counterexamples; pure files and no model calls."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
import run_g4_test as entry
from tau_feedback.contracts import canonical_hash
from tau_feedback.g4_assessment import assess_candidate, choose_final
from tau_feedback.subscription_evidence import save


class G4StageReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "results/g4/fixture"
        self.stage.mkdir(parents=True)
        self.digest = "a" * 64
        self.seed = "Follow the fixed retail policy."
        self.manifest = {"seed_strategy": self.seed, "validation_seeds": [233, 239],
            "splits": {"validation": ["v"]},
            "frozen_tasks": {"validation": [{"task_id": "v", "payload": {"id": "v"}}]}}
        patcher = patch.object(entry, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        guard = patch.object(entry.RecoveringHttpCliTextClient, "generate",
                             side_effect=AssertionError("Model calls forbidden in launch review"))
        guard.start()
        self.addCleanup(guard.stop)

    def lock(self, selection, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        save(path, value)
        selection["source_sha256"][path.relative_to(self.root).as_posix()] = entry.sha(path)

    def selection(self):
        selection = {"status": "selection_complete", "manifest_sha256": self.digest,
            "source_sha256": {}, "review_sources": {"red": "review-red.json", "related": "review-related.json"},
            "baseline_rows": [], "arms": {}}
        self.lock(selection, self.stage / "RAW_COMPLETE.json", {"status": "raw_execution_complete"})
        for reviewer, name in selection["review_sources"].items():
            self.lock(selection, self.root / name, {"reviewer": reviewer, "manifest_sha256": self.digest, "items": []})
        units = [{"task_id": "v", "simulation_seed": s} for s in [233, 239]]
        for arm in ["B1", "B2"]:
            candidates = []
            for batch in range(1, 5):
                folder = self.stage / "candidates" / f"batch-{batch}" / arm / "optimizer"
                self.lock(selection, folder / "adapter/actual_proposed_candidate.json", {"strategy": self.seed})
                self.lock(selection, folder / "execution.json", {"status": "complete", "gepa_selected_new": False})
                assessment = assess_candidate(seed_strategy=self.seed, candidate_strategy=self.seed,
                    gepa_selected_new=False, baseline_rows=[], candidate_rows=[], units=units)
                candidates.append({"batch": batch, "strategy": self.seed, "gepa_selected_new": False,
                                   "candidate_rows": [], "assessment": assessment})
            selection["arms"][arm] = {"candidates": candidates, "selected": choose_final(self.seed, candidates)}
        return selection

    def test_zero_reviewed_validation_rows_must_not_start_holdout(self):
        selection = self.selection()
        with self.assertRaises(ValueError):
            entry.selected_policies(self.manifest, self.digest, selection, self.stage)

    def test_independent_critical_user_error_blocks_even_known_nonqualified_row(self):
        selection = self.selection()
        directory = self.stage / "shared/baseline_validation/measurements/measurement-0001"
        environment = directory / "environment"
        environment.mkdir(parents=True)
        request = {"run_id": "run-fixture", "task_id": "v", "task_sha256": canonical_hash({"id": "v"}),
                   "strategy": self.seed, "simulation_seed": 233}
        outcome = {"run_id": "run-fixture", "task_id": "v", "status": "evaluated", "native_reward": 1,
                   "structural_outcome": {"status": "pass", "reason": "complete_structural_evaluation"},
                   "client_halt_reason": None, "usage_incomplete": False, "unknown_usage_calls": 0,
                   "input_tokens": 100, "output_tokens": 5}
        for name, value in [("request.json", request), ("outcome.json", outcome), ("trajectory.json", []),
                            ("simulation.json", {"id": "run-fixture", "task_id": "v", "messages": [], "reward_info": {"reward": 1}})]:
            save(environment / name, value)
        (environment / "calls.jsonl").write_text("", encoding="utf-8")
        artifacts = {str(p.resolve()): entry.sha(p) for p in environment.iterdir()}
        measurement = {"status": "complete", "measurement_id": 1, "task_id": "v", "task_payload_sha256": canonical_hash({"id": "v"}),
            "simulation_seed": 233, "phase": "baseline_validation_1", "candidate_id": "S0",
            "strategy_sha256": entry.text_hash(self.seed), "directory": str(directory),
            "actual_new_episode": True, "score": 1.0, "run_id": "run-fixture", "artifacts_sha256": artifacts}
        save(directory / "measurement.json", measurement)
        for path in [directory / "measurement.json", *environment.iterdir()]:
            selection["source_sha256"][path.relative_to(self.root).as_posix()] = entry.sha(path)
        review = {"review_id": "review-1", "artifact_sha256": {p.relative_to(self.root).as_posix(): entry.sha(p) for p in environment.iterdir()},
                  "semantic_status": "pass", "user_validity": "fail", "critical_violation": False,
                  "critical_user_error": True, "evidence": ["Synthetic review contract only."]}
        for reviewer, name in selection["review_sources"].items():
            self.lock(selection, self.root / name, {"reviewer": reviewer, "manifest_sha256": self.digest, "items": [review]})
        row = {"measurement_directory": directory.relative_to(self.root).as_posix(), "review_id": "review-1",
            "task_id": "v", "simulation_seed": 233, "strategy_sha256": entry.text_hash(self.seed),
            "native_reward": 1, "structural_status": "pass", "semantic_status": "pass", "user_validity": "fail",
            "critical_violation": False, "input_tokens": 100, "output_tokens": 5}
        with self.assertRaises(ValueError):
            entry.verify_reviewed_row(row, expected_strategy=self.seed, manifest=self.manifest, selection=selection)


if __name__ == "__main__":
    unittest.main()
