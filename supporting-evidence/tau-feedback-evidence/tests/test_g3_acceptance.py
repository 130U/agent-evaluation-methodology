"""Pure G3 acceptance contracts. No model, CLI or experiment files are used."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tau_feedback.g3_acceptance import decide_strategy

SEED = "Follow the fixed policy."
CANDIDATE = "Follow the fixed policy and communicate verified outcomes."
IDS = ["validation-a", "validation-b"]


def row(task, strategy, passed=True, tokens=100):
    return {"task_id": task,
            "strategy_sha256": hashlib.sha256(strategy.encode("utf-8")).hexdigest(),
            "native_reward": int(passed), "structural_status": "pass" if passed else "fail",
            "semantic_status": "pass" if passed else "fail", "user_validity": "pass",
            "critical_violation": False, "input_tokens": tokens, "output_tokens": 10}


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.baseline = [row(IDS[0], SEED), row(IDS[1], SEED, False)]
        self.candidate = [row(task, CANDIDATE) for task in IDS]

    def decide(self, **kwargs):
        values = dict(seed_strategy=SEED, candidate_strategy=CANDIDATE, gepa_selected_new=True,
                      baseline_rows=self.baseline, candidate_rows=self.candidate, validation_ids=IDS)
        values.update(kwargs)
        return decide_strategy(**values)

    def test_complete_qualified_improvement_is_accepted_without_mutating_inputs(self):
        before = deepcopy((self.baseline, self.candidate, IDS))
        result = self.decide()
        self.assertEqual(result["decision"], "accept_candidate")
        self.assertEqual(result["final_strategy"], CANDIDATE)
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["counts"]["baseline"]["qualified"], 1)
        self.assertEqual(result["counts"]["candidate"]["qualified"], 2)
        self.assertEqual(result["validation_token_cost"]["candidate_to_baseline_ratio"], 1.0)
        self.assertEqual(before, (self.baseline, self.candidate, IDS))

    def test_row_order_is_irrelevant_but_all_task_identities_are_required(self):
        self.assertTrue(self.decide(candidate_rows=list(reversed(self.candidate)))["accepted"])
        for which in ("baseline_rows", "candidate_rows"):
            result = self.decide(**{which: []})
            self.assertEqual(result["final_strategy"], SEED)
            self.assertIn(which.split("_")[0] + "_validation_incomplete", result["reasons"])

    def test_gepa_rejected_candidate_is_never_revived_by_better_validation(self):
        result = self.decide(gepa_selected_new=False)
        self.assertFalse(result["accepted"])
        self.assertIn("gepa_did_not_select_new_candidate", result["reasons"])

    def test_absent_candidate_after_gepa_rejection_retains_seed(self):
        result = self.decide(candidate_strategy=None, candidate_rows=None, gepa_selected_new=False)
        self.assertEqual(result["decision"], "keep_seed")
        self.assertIsNone(result["candidate_strategy_sha256"])
        self.baseline[0]["strategy_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.decide(candidate_strategy=None, candidate_rows=None, gepa_selected_new=False)

    def test_inconsistent_gepa_selection_or_nonboolean_flag_raises(self):
        with self.assertRaises(ValueError):
            self.decide(candidate_strategy=None, candidate_rows=[])
        for flag in (None, 0, 1, "true"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.decide(gepa_selected_new=flag)

    def test_same_text_is_not_an_accepted_new_strategy(self):
        rows = [row(task, SEED) for task in IDS]
        result = self.decide(candidate_strategy=SEED, candidate_rows=rows)
        self.assertIn("candidate_text_unchanged", result["reasons"])
        self.assertFalse(result["accepted"])

    def test_equal_qualified_count_and_regression_retain_seed(self):
        self.candidate[0] = row(IDS[0], CANDIDATE, False)
        result = self.decide()
        self.assertIn("qualified_count_not_strictly_higher", result["reasons"])
        self.assertEqual(result["regressed_or_unresolved_task_ids"], [IDS[0]])

    def test_unknown_judgements_in_either_arm_block_promotion(self):
        for arm in ("baseline_rows", "candidate_rows"):
            for field, unknown in (("native_reward", None), ("native_reward", float("nan")),
                                   ("structural_status", "unknown"), ("semantic_status", "unknown"),
                                   ("user_validity", "unknown"), ("critical_violation", None)):
                values = deepcopy(self.baseline if arm == "baseline_rows" else self.candidate)
                values[1][field] = unknown
                with self.subTest(arm=arm, field=field):
                    result = self.decide(**{arm: values})
                    self.assertFalse(result["accepted"])
                    self.assertIn(arm.split("_")[0] + "_judgements_unknown", result["reasons"])

    def test_missing_judgement_is_unknown_not_an_implicit_pass(self):
        del self.candidate[0]["critical_violation"]
        self.assertFalse(self.decide()["accepted"])

    def test_new_critical_violation_is_reported(self):
        self.candidate[1]["critical_violation"] = True
        result = self.decide()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["new_critical_violation_task_ids"], [IDS[1]])

    def test_same_existing_critical_violation_is_not_invented_as_new(self):
        self.baseline = [row(task, SEED, False) for task in IDS]
        self.baseline[1]["critical_violation"] = True
        self.candidate[1] = row(IDS[1], CANDIDATE, False)
        self.candidate[1]["critical_violation"] = True
        result = self.decide()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["new_critical_violation_task_ids"], [])
        self.assertEqual(result["counts"]["candidate"]["qualified"], 1)

    def test_token_ratio_includes_both_fields_and_accepts_exact_cap(self):
        for r in self.baseline:
            r.update(input_tokens=90, output_tokens=10)
        for r in self.candidate:
            r.update(input_tokens=100, output_tokens=25)
        self.assertTrue(self.decide()["accepted"])
        self.assertEqual(self.decide()["validation_token_cost"]["candidate_to_baseline_ratio"], 1.25)
        self.candidate[0]["output_tokens"] += 1
        result = self.decide()
        self.assertFalse(result["accepted"])
        self.assertIn("validation_token_ratio_exceeds_1_25", result["reasons"])

    def test_missing_or_nonfinite_cost_never_counts_as_zero(self):
        for arm in ("baseline_rows", "candidate_rows"):
            for missing in (None, float("nan"), float("inf"), float("-inf")):
                values = deepcopy(self.baseline if arm == "baseline_rows" else self.candidate)
                values[0]["input_tokens"] = missing
                result = self.decide(**{arm: values})
                self.assertFalse(result["accepted"])
                self.assertIn("validation_token_cost_unknown", result["reasons"])
        del self.candidate[0]["output_tokens"]
        self.assertIn("validation_token_cost_unknown", self.decide()["reasons"])

    def test_zero_baseline_cost_denominator_always_retains_seed(self):
        for r in self.baseline + self.candidate:
            r.update(input_tokens=0, output_tokens=0)
        result = self.decide()
        self.assertFalse(result["accepted"])
        self.assertIn("baseline_token_denominator_zero", result["reasons"])
        self.assertIsNone(result["validation_token_cost"]["candidate_to_baseline_ratio"])

    def test_duplicate_unknown_or_wrong_strategy_identity_raises(self):
        variants = [self.candidate + [self.candidate[0]], deepcopy(self.candidate), deepcopy(self.candidate)]
        variants[1][0]["task_id"] = "outside-manifest"
        variants[2][0]["strategy_sha256"] = "0" * 64
        for values in variants:
            with self.assertRaises(ValueError):
                self.decide(candidate_rows=values)

    def test_invalid_labels_numeric_types_or_score_contradiction_raise(self):
        for field, bad in (("native_reward", True), ("native_reward", 0.5),
                           ("critical_violation", "false"), ("semantic_status", "ok"),
                           ("input_tokens", -1), ("input_tokens", True), ("output_tokens", 1.5),
                           ("native_reward", 0)):
            values = deepcopy(self.candidate)
            values[0][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                self.decide(candidate_rows=values)

    def test_exact_two_string_validation_ids_and_valid_strategy_required(self):
        for ids in ([], [IDS[0]], IDS + ["third"], [IDS[0], IDS[0]], [1, 2]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.decide(validation_ids=ids)
        for text in ("", " ", "x" * 6001, None):
            with self.subTest(text_length=len(text) if text else 0), self.assertRaises(ValueError):
                self.decide(seed_strategy=text)

    def test_explicit_structural_fail_with_native_one_is_never_qualified(self):
        self.candidate[1]["structural_status"] = "fail"
        result = self.decide()
        self.assertEqual(result["counts"]["candidate"]["qualified"], 1)
        self.assertFalse(result["accepted"])


if __name__ == "__main__":
    unittest.main()
