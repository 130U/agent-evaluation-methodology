"""Pure fixed-denominator G3 reporting tests; no experiment/model access."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tau_feedback.g3_reporting import aggregate_test


def schedule():
    result = []
    for seed in (103, 107):
        for task in ("t0", "t1", "t2", "t3", "t4", "t5"):
            for arm in ("B0", "B1", "B2"):
                result.append(dict(unit=len(result) + 1, arm=arm, task_id=task, simulation_seed=seed))
    return result


def completed(unit, success=True):
    return {**unit, "execution_status": "returned", "semantic_status": "pass",
        "user_validity": "pass", "critical_violation": False,
        "outcome": {"task_id": unit["task_id"], "status": "evaluated", "native_reward": int(success),
            "structural_outcome": {"status": "pass" if success else "fail"},
            "cli_invocations": 2, "input_tokens": 100, "output_tokens": 10,
            "elapsed_seconds": 4.5, "unknown_usage_calls": 0, "usage_incomplete": False}}


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.schedule = schedule()
        self.rows = [completed(unit) for unit in self.schedule]

    def report(self, rows=None, **kwargs):
        return aggregate_test(self.schedule, self.rows if rows is None else rows, **kwargs)

    def find(self, arm, task="t0", seed=103):
        return next(row for row in self.rows if (row["arm"], row["task_id"], row["simulation_seed"]) == (arm, task, seed))

    def fail(self, arm, task="t0", seed=103):
        row = self.find(arm, task, seed)
        row["outcome"]["native_reward"] = 0
        row["outcome"]["structural_outcome"]["status"] = "fail"

    def test_full_success_fixed_denominators_and_no_input_mutation(self):
        before = deepcopy((self.schedule, self.rows))
        report = self.report()
        self.assertEqual(report["planned_units"], 36)
        self.assertEqual(report["distinct_tasks"], 6)
        for arm in ("B0", "B1", "B2"):
            self.assertEqual(report["arms"][arm]["official"]["success"], 12)
            self.assertEqual(report["arms"][arm]["qualification"]["success_rate_assigned"], 1)
            self.assertEqual(report["arms"][arm]["official_repeated_success"]["pass_power_2"], 1)
        self.assertEqual(report["test_usage"]["known_subtotals"]["cli_invocations"], 72)
        self.assertEqual(report["test_usage"]["known_subtotals"]["input_tokens"], 3600)
        self.assertTrue(report["test_usage"]["complete"])
        self.assertEqual(before, (self.schedule, self.rows))
        self.assertIn("post_registration", report["implementation_timing"])

    def test_no_rows_remain_36_unrun_not_36_failures(self):
        report = self.report([])
        for arm in report["arms"].values():
            self.assertEqual(arm["official"]["not_run"], 12)
            self.assertEqual(arm["official"]["failure"], 0)
            self.assertIsNone(arm["official_repeated_success"]["pass_power_2"])
        self.assertEqual(report["paired_B2_minus_B1"]["official"]["unresolved"], 12)
        self.assertEqual(report["test_usage"]["accounted_records"], 0)

    def test_partial_assignment_does_not_shrink_success_rate_denominator(self):
        report = self.report([self.rows[0]])
        row = report["arms"]["B0"]["qualification"]
        self.assertEqual(row["assigned"], 12)
        self.assertEqual(row["success_rate_assigned"], 1 / 12)
        self.assertEqual(row["not_run"], 11)
        self.assertFalse(row["all_outcomes_known"])

    def test_official_score_and_semantic_qualification_remain_separate(self):
        self.find("B2")["critical_violation"] = True
        self.find("B1")["semantic_status"] = "fail"
        self.find("B0")["user_validity"] = "fail"
        report = self.report()
        for arm in report["arms"].values():
            self.assertEqual(arm["official"]["success"], 12)
            self.assertEqual(arm["qualification"]["failure"], 1)
            self.assertEqual(arm["qualification"]["success"], 11)
        self.assertEqual(len(report["arms"]["B2"]["critical_violation_units"]), 1)

    def test_missing_semantic_evidence_is_unknown_even_with_official_failure(self):
        self.fail("B1")
        del self.find("B1")["semantic_status"]
        report = self.report()
        arm = report["arms"]["B1"]
        self.assertEqual(arm["official"]["failure"], 1)
        self.assertEqual(arm["qualification"]["unknown"], 1)
        self.assertEqual(arm["qualification"]["failure"], 0)
        pair = report["paired_B2_minus_B1"]
        self.assertEqual(pair["official"]["repair"], 1)
        self.assertEqual(pair["qualification"]["repair"], 0)
        self.assertEqual(pair["qualification"]["unresolved"], 1)

    def test_interrupted_error_and_started_units_are_not_unrun_or_failed(self):
        for execution, outcome in (("started", {}), ("interrupted_ungraded", {}),
                                   ("returned", {"status": "error"})):
            value = {**self.schedule[0], "execution_status": execution, "outcome": outcome}
            report = self.report([value])
            arm = report["arms"]["B0"]["official"]
            self.assertEqual((arm["interrupted"], arm["not_run"], arm["failure"]), (1, 11, 0))
            self.assertEqual(report["test_usage"]["incomplete_records"], 1)

    def test_explicit_unrun_placeholder_cannot_carry_results(self):
        value = {**self.schedule[0], "execution_status": "not_run"}
        self.assertEqual(self.report([value])["arms"]["B0"]["official"]["not_run"], 12)
        value["outcome"] = {"status": "evaluated"}
        with self.assertRaises(ValueError):
            self.report([value])

    def test_pass_power_two_requires_both_successes_not_either(self):
        self.fail("B2")
        report = self.report()
        repeat = report["arms"]["B2"]["official_repeated_success"]
        self.assertEqual(repeat["tasks"][0]["outcomes"], ["failure", "success"])
        self.assertEqual(repeat["tasks"][0]["pass_power_2"], 0)
        self.assertEqual(repeat["pass_power_2"], 5 / 6)

    def test_incomplete_pair_is_not_estimated_or_dropped_from_denominator(self):
        self.rows.remove(self.find("B2"))
        report = self.report()
        repeat = report["arms"]["B2"]["qualified_repeated_success"]
        self.assertEqual(repeat["task_denominator"], 6)
        self.assertEqual(repeat["estimable_tasks"], 5)
        self.assertIsNone(repeat["tasks"][0]["pass_power_2"])
        self.assertIsNone(repeat["pass_power_2"])
        self.assertIsNone(report["paired_B2_minus_B1"]["qualification"]["paired_difference"])

    def test_paired_repairs_and_regressions_use_task_and_seed_not_list_order(self):
        self.fail("B1", "t0", 103)
        self.fail("B1", "t1", 107)
        self.fail("B2", "t3", 103)
        report = self.report(list(reversed(self.rows)))
        for pair in report["paired_B2_minus_B1"].values():
            self.assertEqual((pair["repair"], pair["regression"], pair["unresolved"]), (2, 1, 0))
            self.assertEqual(pair["paired_difference"], 1 / 12)
            self.assertEqual(pair["assigned_success_rate_difference"], 1 / 12)

    def test_unknown_reward_is_not_confirmed_failure_or_repair(self):
        for missing in (None, float("nan")):
            self.find("B1")["outcome"]["native_reward"] = missing
            report = self.report()
            self.assertEqual(report["arms"]["B1"]["official"]["unknown"], 1)
            self.assertEqual(report["paired_B2_minus_B1"]["official"]["repair"], 0)
            self.assertEqual(report["paired_B2_minus_B1"]["official"]["unresolved"], 1)
            json.dumps(report, allow_nan=False)

    def test_unknown_cost_preserves_known_subtotals_and_incomplete_flag(self):
        row = self.find("B1")
        row["outcome"]["input_tokens"] = None
        row["outcome"]["unknown_usage_calls"] = 1
        row["outcome"]["usage_incomplete"] = True
        cost = self.report()["arms"]["B1"]["test_usage"]
        self.assertFalse(cost["complete"])
        self.assertEqual(cost["known_subtotals"]["input_tokens"], 1100)
        self.assertEqual(cost["missing_records_by_metric"]["input_tokens"], 1)
        self.assertEqual(cost["known_subtotals"]["unknown_usage_calls"], 1)
        self.assertEqual(cost["currency"], "unavailable")

    def test_optimization_cost_is_separate_and_absence_is_unknown(self):
        ledger = dict(cli_invocations=10, input_tokens=1234, output_tokens=99,
                      unknown_usage_calls=0, client_seconds=11, ledger_available=True)
        report = self.report(optimization_usage={"B1": ledger})
        self.assertTrue(report["optimization_usage"]["B1"]["complete"])
        self.assertFalse(report["optimization_usage"]["B2"]["complete"])
        self.assertFalse(report["optimization_usage"]["B2"]["provided"])
        self.assertEqual(report["test_usage"]["known_subtotals"]["cli_invocations"], 72)
        self.assertEqual(report["optimization_usage"]["B1"]["known_subtotals"]["cli_invocations"], 10)

    def test_duplicate_mismatched_unknown_and_nested_identities_raise(self):
        variants = [self.rows + [self.rows[0]]]
        for field, value in (("unit", 99), ("unit", True), ("arm", "B2"),
                             ("task_id", "other"), ("simulation_seed", "103")):
            rows = deepcopy(self.rows)
            rows[0][field] = value
            variants.append(rows)
        rows = deepcopy(self.rows)
        rows[0]["outcome"]["task_id"] = "other"
        variants.append(rows)
        for rows in variants:
            with self.assertRaises(ValueError):
                self.report(rows)

    def test_malformed_schedule_cannot_hide_missing_groups_or_repetitions(self):
        variants = [self.schedule[:-1], self.schedule + [self.schedule[0]]]
        for field, value in (("unit", 2), ("arm", "B1"), ("simulation_seed", 999), ("task_id", "seventh")):
            value_schedule = deepcopy(self.schedule)
            value_schedule[0][field] = value
            variants.append(value_schedule)
        for value in variants:
            with self.assertRaises(ValueError):
                aggregate_test(value, [])

    def test_invalid_status_cost_and_contradictory_official_score_raise(self):
        for field, value in (("semantic_status", "ok"), ("critical_violation", "false")):
            rows = deepcopy(self.rows)
            rows[0][field] = value
            with self.assertRaises(ValueError):
                self.report(rows)
        for field, value in (("input_tokens", -1), ("cli_invocations", True), ("native_reward", 0)):
            rows = deepcopy(self.rows)
            rows[0]["outcome"][field] = value
            with self.assertRaises(ValueError):
                self.report(rows)


if __name__ == "__main__":
    unittest.main()
