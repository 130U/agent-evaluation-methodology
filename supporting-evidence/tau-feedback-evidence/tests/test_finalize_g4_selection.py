"""Fake sealed 120-episode cohorts only; real process/model entry points blocked."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
import finalize_g4_selection as finalizer
from tau_feedback.subscription_evidence import save


class FinalizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="g4-selection-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stage = self.root / "results/g4/fixture"
        self.stage.mkdir(parents=True)
        self.digest = "a" * 64
        self.manifest = {"manifest_id": "fixture", "registered_at_utc": "2026-09-17T00:00:00Z",
            "seed_strategy": "Follow policy.", "parent_seeds": [211, 223, 227, 229], "validation_seeds": [233, 239],
            "splits": {"dev": ["d1", "d2", "d3", "d4"], "validation": ["v1", "v2", "v3", "v4"]},
            "max_strategy_chars": 6000, "planned_actual_optimization_episodes": 120,
            "arm_order_by_batch": [["B1", "B2"], ["B2", "B1"], ["B1", "B2"], ["B2", "B1"]]}
        self.manifest["frozen_tasks"] = {split: [{"task_id": tid, "payload": {"id": tid},
            "payload_sha256": finalizer.canonical_hash({"id": tid})} for tid in ids]
            for split, ids in self.manifest["splits"].items()}
        for target in ("subprocess.Popen", "subprocess.run", "tau_feedback.subscription_recovery_client._run_bounded",
                       "tau_feedback.subscription_recovery_client.RecoveringHttpCliTextClient.generate"):
            guard = patch(target, side_effect=AssertionError("Model/process forbidden"))
            guard.start(); self.addCleanup(guard.stop)
        guard = patch.object(finalizer.test_entry, "ROOT", self.root)
        guard.start(); self.addCleanup(guard.stop)
        self.labels = []
        self.make_cohort()

    def seal(self, directory):
        path = directory / "seal.json"
        save(path, {"files_sha256": {p.relative_to(directory).as_posix(): finalizer.sha(p)
            for p in directory.rglob("*") if p.is_file() and p != path}})

    def environment(self, directory, task, strategy, seed, fail=False):
        directory.mkdir(parents=True)
        rid = finalizer.relative(self.root, directory)
        reward = 0 if fail else 1
        request = {"task_id": task["task_id"], "task_sha256": task["payload_sha256"],
            "strategy": strategy, "simulation_seed": seed, "run_id": rid}
        outcome = {"status": "evaluated", "run_id": rid, "task_id": task["task_id"], "native_reward": reward,
            "structural_outcome": {"status": "fail" if fail else "pass", "reason": "complete_structural_evaluation"},
            "client_halt_reason": None, "usage_incomplete": False, "unknown_usage_calls": 0,
            "input_tokens": 100, "output_tokens": 5}
        for name, value in (("request.json", request), ("outcome.json", outcome), ("trajectory.json", []),
                            ("simulation.json", {"id": rid, "task_id": task["task_id"], "messages": [], "reward_info": {"reward": reward}})):
            save(directory / name, value)
        (directory / "calls.jsonl").write_text("", encoding="utf-8")
        artifacts = {finalizer.relative(self.root, directory / n): finalizer.sha(directory / n) for n in finalizer.FILES}
        self.labels.append({"review_id": f"opaque-{len(self.labels)+1:03d}", "artifact_sha256": artifacts,
            "semantic_status": "fail" if fail else "pass", "user_validity": "pass", "critical_violation": False,
            "critical_user_error": False, "evidence": ["Entirely fabricated contract fixture; no actual episode."]})
        return outcome

    def measurements(self, directory, sets, strategy, candidate_id):
        directory.mkdir(parents=True)
        rows = []
        for phase, tasks, seed in sets:
            for task in tasks:
                i = len(rows) + 1
                path = directory / f"measurement-{i:04d}"
                outcome = self.environment(path / "environment", task, strategy, seed,
                    fail=candidate_id == "S0" and task["task_id"] == "v1")
                row = {"status": "complete", "measurement_id": i, "task_id": task["task_id"],
                    "task_payload_sha256": task["payload_sha256"], "simulation_seed": seed, "phase": phase,
                    "candidate_id": candidate_id, "strategy_sha256": finalizer.text_hash(strategy),
                    "directory": str(path), "actual_new_episode": True, "score": float(outcome["native_reward"]),
                    "run_id": outcome["run_id"], "artifacts_sha256": {str(p.resolve()): finalizer.sha(p)
                        for p in (path / "environment").iterdir()}}
                save(path / "measurement.json", row); rows.append(row)
        save(directory / "index.json", rows)

    def make_cohort(self):
        m = self.manifest
        dev, val = m["frozen_tasks"]["dev"], m["frozen_tasks"]["validation"]
        shared_hashes = {}
        for batch, seed in enumerate(m["parent_seeds"], 1):
            directory = self.stage / "shared" / f"batch-{batch}"
            (directory / "episodes/host_evidence").mkdir(parents=True)
            records = {t["task_id"]: {"contract_fixture": t["task_id"]} for t in dev}
            shared_hashes[batch] = {k: finalizer.canonical_hash(v) for k, v in records.items()}
            save(directory / "shared_parents.json", {"strategy_sha256": finalizer.text_hash(m["seed_strategy"]),
                "simulation_seed": seed, "records": records, "record_sha256": shared_hashes[batch]})
            for i, task in enumerate(dev, 1):
                folder = directory / "episodes" / f"episode-{i:04d}"
                outcome = self.environment(folder / "environment", task, m["seed_strategy"], seed)
                save(folder / "runner_outcome.json", {"status": "complete", "task_id": task["task_id"],
                    "run_id": outcome["run_id"], "score": float(outcome["native_reward"])})
            self.seal(directory)
        baseline = self.stage / "shared/baseline_validation"
        self.measurements(baseline / "measurements", [(f"baseline_validation_{i}", val, s)
            for i, s in enumerate(m["validation_seeds"], 1)], m["seed_strategy"], "S0")
        self.seal(baseline)
        for batch in range(1, 5):
            for arm in ("B1", "B2"):
                directory = self.stage / "candidates" / f"batch-{batch}" / arm
                folder = directory / "optimizer"
                (folder / "adapter").mkdir(parents=True)
                strategy = f"Candidate {arm} {batch}: follow policy carefully."
                save(folder / "adapter/actual_proposed_candidate.json", {"strategy": strategy})
                save(folder / "gepa_best_candidate.json", {"strategy": strategy})
                save(folder / "execution.json", {"status": "complete", "arm": arm, "candidate_id": f"batch-{batch}-{arm}",
                    "actual_proposed_candidate_sha256": finalizer.text_hash(strategy), "gepa_selected_new": True,
                    "actual_candidate_episodes": 12, "reflection_attempts": 1, "completion": {"generated_candidates": 1},
                    "reflection_failure": None, "adapter_failure": None, "client_halt_reason": None, "input_records_sha256": arm})
                self.measurements(folder / "measurements", [("candidate_train", dev, m["parent_seeds"][batch-1])] +
                    [(f"candidate_validation_{i}", val, s) for i, s in enumerate(m["validation_seeds"], 1)], strategy, f"batch-{batch}-{arm}")
                self.seal(directory)
            save(self.stage / "candidates" / f"batch-{batch}/PAIR_INPUT.json", {"batch": batch,
                "common_parent_record_sha256": shared_hashes[batch], "B1_records_sha256": "B1", "B2_records_sha256": "B2",
                "input_treatment_active": True})
        save(self.stage / "RAW_COMPLETE.json", {"status": "raw_execution_complete", "manifest_sha256": self.digest,
            "parent_batches": [{"batch": i, "seed": s, "complete_episodes": 4} for i, s in enumerate(m["parent_seeds"], 1)],
            "candidate_runs": [{"batch": i, "arm": arm, "status": "complete", "actual_candidate_episodes": 12}
                for i, order in enumerate(m["arm_order_by_batch"], 1) for arm in order]})
        self.review_sources = {"red": "red.json", "related": "related.json"}
        for reviewer, name in self.review_sources.items():
            save(self.root / name, {"reviewer": reviewer, "manifest_sha256": self.digest, "items": self.labels})

    def build(self):
        return finalizer.build_selection(self.root, self.manifest, self.digest, self.stage,
            self.review_sources, analysis_metadata={"fixture": True})

    def change_review(self, change):
        path = self.root / "related.json"
        value = finalizer.load(path); change(value["items"]); save(path, value)

    def test_full_120_and_72_validation_rows_replay_frozen_consumer(self):
        selected = self.build()
        self.assertEqual(selected["reviewed_cohort_episodes"], 120)
        self.assertEqual(len(selected["baseline_rows"]), 8)
        self.assertEqual(sum(len(c["candidate_rows"]) for arm in selected["arms"].values() for c in arm["candidates"]), 64)
        for arm in ("B1", "B2"):
            self.assertEqual(selected["arms"][arm]["selected"]["selected_batch"], 1)
        policies = finalizer.test_entry.selected_policies(self.manifest, self.digest, selected, self.stage)
        self.assertEqual(set(policies), {"B0", "B1", "B2"})

    def test_missing_review_rejected(self):
        self.change_review(lambda items: items.pop())
        with self.assertRaisesRegex(ValueError, "entire complete cohort"): self.build()

    def test_source_tamper_and_foreign_review_rejected(self):
        self.change_review(lambda items: items[0]["artifact_sha256"].update({"foreign.json": "0"*64}))
        with self.assertRaisesRegex(ValueError, "foreign mapped"): self.build()
        path = self.stage / "shared/batch-1/episodes/episode-0001/environment/trajectory.json"
        path.write_text("[null]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stage seal"): self.build()

    def test_disagreement_unknown_recomputed_not_pass(self):
        def change(items):
            for item in items:
                if any("baseline_validation" in p for p in item["artifact_sha256"]):
                    item["semantic_status"] = "unknown"
        self.change_review(change)
        selected = self.build()
        self.assertTrue(all(r["semantic_status"] == "unknown" for r in selected["baseline_rows"]))
        self.assertTrue(all(a["selected"]["decision"] == "keep_seed" for a in selected["arms"].values()))

    def test_critical_parent_review_records_stop_and_no_final_selection(self):
        self.change_review(lambda items: items[0].update(critical_user_error=True))
        script = self.root / "scripts/finalize_g4_selection.py"
        script.parent.mkdir(); script.write_text("# fixture analysis script\n")
        manifest_path = self.root / "manifest.json"; save(manifest_path, self.manifest)
        with patch.object(finalizer, "verify_manifest", return_value=(self.manifest, self.digest)), patch.object(finalizer, "__file__", str(script)):
            with self.assertRaises(finalizer.test_entry.CriticalUserReview):
                finalizer.finalize(self.root, manifest_path, self.review_sources)
        self.assertTrue((self.stage / "STOP.json").is_file())
        self.assertTrue((self.stage / "SELECTION_BLOCKED.json").is_file())
        self.assertFalse((self.stage / "FINAL_STRATEGIES.json").exists())

    def test_incomplete_stopped_active_and_existing_output_rejected(self):
        for name in ("STOP.json", "active.lock", "FINAL_STRATEGIES.json", "FINAL_STRATEGIES.sha256", "SELECTION_BLOCKED.json"):
            path = self.stage / name; path.write_text("{}")
            with self.assertRaises(ValueError): self.build()
            path.unlink()
        path = self.stage / "RAW_COMPLETE.json"
        value = finalizer.load(path); value["candidate_runs"].pop(); save(path, value)
        with self.assertRaisesRegex(ValueError, "every fixed"): self.build()

    def test_write_once_final_output_and_hash(self):
        script = self.root / "scripts/finalize_g4_selection.py"
        script.parent.mkdir(); script.write_text("# fixture analysis script\n")
        path = self.root / "manifest.json"; save(path, self.manifest)
        path.with_suffix(".sha256").write_text(finalizer.sha(path))
        with patch.object(finalizer, "verify_manifest", return_value=(self.manifest, self.digest)), patch.object(finalizer, "__file__", str(script)):
            outcome = finalizer.finalize(self.root, path, self.review_sources)
            self.assertEqual(outcome["model_calls"], 0)
            output = self.stage / "FINAL_STRATEGIES.json"
            self.assertEqual(output.with_suffix(".sha256").read_text().split(), [finalizer.sha(output)])
            before = output.read_bytes()
            with self.assertRaises(ValueError): finalizer.finalize(self.root, path, self.review_sources)
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
