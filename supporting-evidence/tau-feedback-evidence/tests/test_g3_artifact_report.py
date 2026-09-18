"""Reporting must reject mislabeled or altered evidence, without model calls."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import report_g3_test as report


class ArtifactBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / "stage"
        self.test = self.stage / "test"
        self.test.mkdir(parents=True)
        self.strategy = "Follow policy."
        self.hash = hashlib.sha256(self.strategy.encode()).hexdigest()
        self.policies = {"arms": {a: {"final_strategy": self.strategy} for a in ("B0", "B1", "B2")},
                         "strategy_sha256": {a: self.hash for a in ("B0", "B1", "B2")}}
        schedule = [{"unit": i + 1, "arm": arm, "task_id": str(task), "simulation_seed": seed}
                    for i, (task, seed, arm) in enumerate((t, s, a) for t in range(6)
                    for s in (103, 107) for a in ("B0", "B1", "B2"))]
        self.manifest = {"test_schedule": schedule, "episode_limits": {"max_steps": 40},
            "frozen_tasks": {"test": [{"task_id": str(i), "payload": {"id": str(i)}} for i in range(6)]}}
        rows = []
        for unit in schedule:
            directory = self.test / f"unit-{unit['unit']:03d}"
            directory.mkdir()
            request = {"run_id": str(unit["unit"]), "task_id": unit["task_id"],
                "simulation_seed": unit["simulation_seed"], "strategy": self.strategy,
                "requested_config": self.manifest["episode_limits"],
                "task_sha256": report.canonical_hash({"id": unit["task_id"]})}
            outcome = {"run_id": str(unit["unit"]), "task_id": unit["task_id"], "status": "evaluated", "native_reward": 1}
            for name, value in (("request.json", request), ("outcome.json", outcome),
                    ("simulation.json", {"reward_info": {"reward": 1}}), ("trajectory.json", [])):
                self.write(directory / name, value)
            (directory / "calls.jsonl").write_text("", encoding="utf-8")
            rows.append({**unit, "execution_status": "returned", "outcome": outcome,
                "relative_directory": directory.relative_to(self.root).as_posix(), "strategy_sha256": self.hash})
        self.summary = {"status": "complete", "manifest_sha256": "manifest", "final_strategies_sha256": "policies",
            "planned_units": 36, "units": rows, "attempted_units": 36, "evaluated_units": 36,
            "attempted_ungraded_units": 0, "unrun_units": 0, "usage": {"fake_usage": 0}}
        self.persist()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def persist(self):
        self.write(self.test / "summary.json", self.summary)
        self.write(self.test / "seal.json", {"files_sha256": {p.relative_to(self.test).as_posix(): report.sha(p)
            for p in self.test.rglob("*") if p.is_file() and p.name != "seal.json"}})

    def bind(self):
        with patch.object(report, "usage", return_value=self.summary["usage"]):
            return report.bind_test(self.root, self.stage, self.manifest, "manifest", self.policies, "policies")

    def test_all_units_bind_to_actual_artifacts(self):
        _, completed, hashes = self.bind()
        self.assertEqual(len(completed), 36)
        self.assertEqual(len(hashes), 181)

    def test_valid_seal_cannot_hide_wrong_executed_strategy(self):
        path = self.test / "unit-001/request.json"
        request = report.load(path)
        request["strategy"] = "Unregistered changed strategy"
        self.write(path, request)
        self.persist()
        with self.assertRaisesRegex(ValueError, "episode request"):
            self.bind()

    def test_summary_cannot_swap_arms_even_if_sealed(self):
        self.summary["units"][0]["arm"] = "B2"
        self.persist()
        with self.assertRaisesRegex(ValueError, "schedule prefix"):
            self.bind()

    def test_changed_trajectory_breaks_seal(self):
        self.write(self.test / "unit-001/trajectory.json", [{"content": "changed"}])
        with self.assertRaisesRegex(ValueError, "seal differs"):
            self.bind()

    def test_raw_reward_mismatch_is_not_hidden_by_summary(self):
        self.write(self.test / "unit-001/simulation.json", {"reward_info": {"reward": 0}})
        self.persist()
        with self.assertRaisesRegex(ValueError, "reward differ"):
            self.bind()

    def test_stopped_denominator_must_include_attempted_unit(self):
        self.summary["status"] = "stopped"
        self.summary["attempted_units"] = 35
        self.write(self.stage / "STOP.json", {"stage": "test", "manifest_sha256": "manifest", "no_retry": True})
        self.persist()
        with self.assertRaisesRegex(ValueError, "denominators differ"):
            self.bind()

    def test_stopped_summary_cannot_hide_existing_unit_as_unrun(self):
        self.summary.update(status="stopped", attempted_units=35, evaluated_units=35, unrun_units=1)
        self.summary["units"].pop()
        self.write(self.stage / "STOP.json", {"stage": "test", "manifest_sha256": "manifest", "no_retry": True})
        self.persist()
        with self.assertRaisesRegex(ValueError, "missing from the attempted-unit ledger"):
            self.bind()

    def test_disagreement_stays_unknown(self):
        rows = [self.summary["units"][0]]
        relative = rows[0]["relative_directory"]
        a = {relative: {"semantic_status": "pass", "user_validity": "pass", "critical_violation": False,
                        "critical_user_error": False}}
        b = {relative: {**a[relative], "semantic_status": "fail"}}
        merged, differences, alarms = report.merge_reviews(rows, {relative: True}, a, b)
        self.assertEqual(merged[0]["semantic_status"], "unknown")
        self.assertEqual(len(differences), 1)
        self.assertEqual(alarms, [])

    def test_critical_user_error_cannot_be_called_valid(self):
        rows = [self.summary["units"][0]]
        relative = rows[0]["relative_directory"]
        a = {relative: {"semantic_status": "pass", "user_validity": "pass", "critical_violation": False,
                        "critical_user_error": True}}
        with self.assertRaisesRegex(ValueError, "contradicts user validity"):
            report.merge_reviews(rows, {relative: True}, a, a)


class RateDenominatorTests(unittest.TestCase):
    def setUp(self):
        from tests.test_g3_reporting import schedule, completed
        self.schedule = schedule()
        self.rows = [completed(unit) for unit in self.schedule]

    def metrics(self, rows):
        value = report.aggregate_test(self.schedule, rows)
        report.add_rate_denominators(value)
        return value

    def test_no_baseline_failures_has_no_repair_rate(self):
        result = self.metrics(self.rows)
        pair = result["paired_B2_minus_B1"]["qualification"]
        self.assertIsNone(pair["repair_rate"]["value"])
        self.assertEqual(pair["regression_rate"]["value"], 0)

    def test_failed_runs_remain_in_cost_numerator(self):
        row = next(r for r in self.rows if r["arm"] == "B1")
        row["outcome"]["native_reward"] = 0
        row["outcome"]["structural_outcome"]["status"] = "fail"
        result = self.metrics(self.rows)
        cost = result["arms"]["B1"]["test_cost_per_verified_success"]
        self.assertEqual(cost["denominator"], 11)
        self.assertEqual(cost["values"]["input_tokens"], 1200 / 11)
        self.assertEqual(result["paired_B2_minus_B1"]["qualification"]["repair_rate"]["value"], 1)

    def test_partial_execution_does_not_report_full_cost_or_repair_rate(self):
        result = self.metrics(self.rows[:3])
        self.assertIsNone(result["arms"]["B0"]["test_cost_per_verified_success"]["values"]["input_tokens"])
        pair = result["paired_B2_minus_B1"]["qualification"]
        self.assertIsNone(pair["regression_rate"]["value"])
        self.assertEqual(pair["regression_rate"]["unresolved_pairs"], 11)


if __name__ == "__main__":
    unittest.main()
