"""Reproducible archive-audit checks; Python standard library only.

The first 42 tests preserve the previously executed independent checks:
8 reward classifications + 5 coverage cases + 24 archive metric/denominator
checks + 5 in-memory full-audit cases. Additional tests challenge strict
denominators and compare fresh output with both raw inputs and the saved report.

Run: python -B -X utf8 -m unittest discover -s tests -p test_archive_audit.py -v
No network, model calls, temporary files, or report writes are performed.
"""

from collections import Counter, defaultdict
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("archive_audit_under_test", ROOT / "scripts/audit_archives.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
MODELS = ("claude-3-7-sonnet-20250219", "gpt-4.1-2025-04-14",
          "gpt-4.1-mini-2025-04-14", "o4-mini-2025-04-16")


def fixture():
    tasks = [{"id": str(i), "evaluation_criteria": {
        "reward_basis": ["DB"], "nl_assertions": None, "communicate_info": []}}
        for i in range(2)]
    return {"info": {"num_trials": 4}, "tasks": tasks, "simulations": [
        {"id": f"{task}-{trial}", "task_id": str(task), "trial": trial, "messages": [],
         "reward_info": {"reward": 1.0, "reward_basis": ["DB"],
                         "reward_breakdown": {"DB": 1.0}, "nl_assertions": []}}
        for task in range(2) for trial in range(4)]}


class MemoryPath:
    """Minimal read-only Path interface: all controlled faults stay in memory."""

    name = "controlled_in_memory.json"

    def __init__(self, data):
        self.raw = json.dumps(data).encode("utf-8")

    def read_bytes(self):
        return self.raw


def audit_memory(data):
    path = MemoryPath(data)
    raw = path.raw
    manifest = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "git_blob_sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                "url": "controlled-in-memory"}
    return AUDIT.audit_file(path, manifest, fixture()["tasks"], None)


class RewardClassificationTests(unittest.TestCase):
    def test_null_reward(self):
        self.assertEqual(AUDIT.classify_reward({"reward_info": {"reward": None}})[0], "null_reward")

    def test_boolean_not_numeric_reward(self):
        self.assertEqual(AUDIT.classify_reward({"reward_info": {"reward": True}})[0], "invalid_reward_type")

    def test_fraction_is_nonbinary(self):
        self.assertEqual(AUDIT.classify_reward({"reward_info": {"reward": 0.5}})[0], "nonbinary_reward")

    def test_nan_is_nonfinite(self):
        self.assertEqual(AUDIT.classify_reward({"reward_info": {"reward": float("nan")}})[0], "nonfinite_reward")

    def test_zero_is_failure(self):
        self.assertEqual(AUDIT.classify_reward({"reward_info": {"reward": 0}}), ("binary_failure", 0))

    def test_one_is_success(self):
        self.assertEqual(AUDIT.classify_reward({"reward_info": {"reward": 1}}), ("binary_success", 1))

    def test_missing_reward_info(self):
        self.assertEqual(AUDIT.classify_reward({})[0], "missing_reward_info")

    def test_missing_reward(self):
        self.assertEqual(AUDIT.classify_reward({"reward_info": {}})[0], "missing_reward")


class CoverageTests(unittest.TestCase):
    @staticmethod
    def item(identity, met=True):
        return {"nl_assertion": identity, "met": met, "justification": "controlled fixture"}

    def check(self, results):
        return AUDIT.coverage({"nl_assertions": ["A", "B"]}, {"nl_assertions": results},
                              "nl_assertions", "nl_assertions", "nl_assertion")

    def test_legitimate_reordering(self):
        self.assertEqual(self.check([self.item("B"), self.item("A")])["issues"], [])

    def test_duplicate_does_not_cover_missing_identity(self):
        self.assertIn("identity_or_multiplicity_mismatch", self.check([self.item("A"), self.item("A")])["issues"])

    def test_empty_response_is_flagged_when_expected(self):
        self.assertIn("empty_results", self.check([])["issues"])

    def test_string_true_is_not_boolean(self):
        self.assertIn("invalid_or_missing_met_type", self.check([self.item("A", "true"), self.item("B")])["issues"])

    def test_no_expected_nl_is_not_missing(self):
        result = AUDIT.coverage({"nl_assertions": None}, {}, "nl_assertions", "nl_assertions", "nl_assertion")
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["issues"], [])


class StrictDenominatorMixin:
    def assert_strict_denominator(self, report, observed=8, tasks=2):
        self.assertEqual(report["simulation_count"], observed)
        self.assertEqual(len(report["run_rows"]), observed)
        self.assertEqual(report["expected_simulation_count"], 8)
        self.assertEqual(sum(report["reward_counts"].values()), observed)
        for value in report["pass_hat_k"].values():
            self.assertIsNone(value["value"])
            self.assertEqual(value["total_task_denominator"], tasks)
            self.assertTrue(value["unestimable_task_ids"])


class FullAuditBoundaryTests(StrictDenominatorMixin, unittest.TestCase):
    def test_complete_positive_control(self):
        self.assertEqual(audit_memory(fixture())["pass_hat_k"]["4"]["value"], 1.0)

    def test_missing_trial_retains_assigned_denominator(self):
        data = fixture()
        data["simulations"].pop()
        self.assert_strict_denominator(audit_memory(data), observed=7)

    def test_null_reward_retains_run(self):
        data = fixture()
        data["simulations"][0]["reward_info"]["reward"] = None
        report = audit_memory(data)
        self.assertEqual(report["reward_counts"]["null_reward"], 1)
        self.assert_strict_denominator(report)

    def test_nonbinary_reward_retains_run(self):
        data = fixture()
        data["simulations"][0]["reward_info"]["reward"] = 0.5
        report = audit_memory(data)
        self.assertEqual(report["reward_counts"]["nonbinary_reward"], 1)
        self.assert_strict_denominator(report)

    def test_duplicate_trial_does_not_replace_missing_trial(self):
        data = fixture()
        data["simulations"][1]["trial"] = 0
        self.assert_strict_denominator(audit_memory(data))


class ArchiveInputsMixin:
    @classmethod
    def setUpClass(cls):
        archive_dir = ROOT / "data/archives"
        entries = json.loads((archive_dir / "DOWNLOAD_MANIFEST.json").read_text(encoding="utf-8"))
        if len(entries) != 4:
            raise AssertionError("Require the four pinned archives; do not silently skip archive tests")
        task_path = ROOT / "sources/tau2/data/tau2/domains/retail/tasks.json"
        current_tasks = json.loads(task_path.read_text(encoding="utf-8"))
        policy = task_path.with_name("policy.md").read_text(encoding="utf-8")
        cls.raw_by_model, cls.audit_by_model = {}, {}
        for entry in entries:
            path = ROOT / entry["path"]
            raw = json.loads(path.read_text(encoding="utf-8"))
            model = raw["info"]["agent_info"]["llm"]
            cls.raw_by_model[model] = raw
            cls.audit_by_model[model] = AUDIT.audit_file(path, entry, current_tasks, policy)
        if set(cls.raw_by_model) != set(MODELS):
            raise AssertionError("Unexpected or duplicate model archive")


class ArchiveMetricTests(ArchiveInputsMixin, unittest.TestCase):
    pass


def metric_test(model, k):
    def test(self):
        raw = self.raw_by_model[model]
        grouped = defaultdict(list)
        for sim in raw["simulations"]:
            grouped[sim["task_id"]].append(sim["reward_info"]["reward"])
        task_scores = []
        for task in raw["tasks"]:
            # Independent enumeration uses raw rewards, not audit task_rows or comb().
            subsets = list(itertools.combinations(grouped[task["id"]], k))
            self.assertTrue(subsets)
            task_scores.append(sum(all(reward == 1 for reward in subset) for subset in subsets) / len(subsets))
        expected = sum(task_scores) / len(raw["tasks"])
        self.assertAlmostEqual(self.audit_by_model[model]["pass_hat_k"][str(k)]["value"], expected, places=12)
    return test


def reward_total_test(model):
    def test(self):
        report = self.audit_by_model[model]
        self.assertEqual(len(self.raw_by_model[model]["simulations"]),
                         sum(sum(t["reward_counts"].values()) for t in report["task_rows"]))
    return test


def expected_slots_test(model):
    def test(self):
        raw, report = self.raw_by_model[model], self.audit_by_model[model]
        self.assertEqual(len(report["run_rows"]), len(raw["tasks"]) * raw["info"]["num_trials"])
    return test


for model_index, model in enumerate(MODELS, start=1):
    for k in range(1, 5):
        setattr(ArchiveMetricTests, f"test_model_{model_index}_pass_hat_{k}_by_exhaustive_subsets", metric_test(model, k))
    setattr(ArchiveMetricTests, f"test_model_{model_index}_all_rewards_accounted_for", reward_total_test(model))
    setattr(ArchiveMetricTests, f"test_model_{model_index}_all_expected_slots_present", expected_slots_test(model))


class AdditionalStrictDenominatorTests(StrictDenominatorMixin, unittest.TestCase):
    def test_absent_reward_info_retains_run(self):
        data = fixture()
        del data["simulations"][0]["reward_info"]
        report = audit_memory(data)
        self.assertEqual(report["reward_counts"]["missing_reward_info"], 1)
        self.assert_strict_denominator(report)

    def test_absent_reward_field_retains_run(self):
        data = fixture()
        del data["simulations"][0]["reward_info"]["reward"]
        report = audit_memory(data)
        self.assertEqual(report["reward_counts"]["missing_reward"], 1)
        self.assert_strict_denominator(report)

    def test_duplicate_simulation_id_invalidates_groups(self):
        data = fixture()
        data["simulations"][4]["id"] = data["simulations"][0]["id"]
        report = audit_memory(data)
        self.assertEqual(report["duplicate_simulation_ids"], {"0-0": 2})
        self.assert_strict_denominator(report)

    def test_unknown_task_retains_orphan_run_and_expected_task(self):
        data = fixture()
        data["simulations"][0]["task_id"] = "unknown"
        report = audit_memory(data)
        self.assertEqual({t["task_id"] for t in report["task_rows"]}, {"0", "1", "unknown"})
        self.assert_strict_denominator(report, tasks=3)


class ArchiveOutputRegressionTests(ArchiveInputsMixin, unittest.TestCase):
    def test_reward_counts_independent_raw_enumeration(self):
        for model in MODELS:
            with self.subTest(model=model):
                raw, report = self.raw_by_model[model], self.audit_by_model[model]
                self.assertTrue(all(type(s["reward_info"]["reward"]) in (int, float)
                                    and s["reward_info"]["reward"] in (0, 1) for s in raw["simulations"]))
                expected = Counter("binary_success" if s["reward_info"]["reward"] == 1 else "binary_failure"
                                   for s in raw["simulations"])
                self.assertEqual(dict(expected), report["reward_counts"])

    def test_fresh_analysis_exactly_matches_saved_archive_results(self):
        saved = json.loads((ROOT / "results/archive_audit.json").read_text(encoding="utf-8"))
        saved_by_model = {x["metadata"]["agent_info"]["llm"]: x for x in saved["archives"]}
        self.assertEqual(set(saved_by_model), set(MODELS))
        for model in MODELS:
            with self.subTest(model=model):
                self.assertEqual(self.audit_by_model[model], saved_by_model[model])


if __name__ == "__main__":
    unittest.main()
