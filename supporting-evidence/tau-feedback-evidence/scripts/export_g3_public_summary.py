"""Create a new allowlisted G3 result view after the local final report exists.

Does not export raw CLI events, authentication, machine paths or model catalog.
Derived hashes identify local evidence; they cannot authenticate omitted logs.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from register_g3_pilot import ID, verify, load, sha, write_new
from run_g3_pilot import verify_arm
from report_g3_test import verify_selection, bind_test
from export_public_package import public_text


REPORT_FIELDS = ("schema_version", "implementation_timing", "execution_status", "planned_units",
    "distinct_tasks", "repetitions_per_task", "model_seed_controlled", "arms", "units",
    "paired_B2_minus_B1", "test_usage", "optimization_usage", "limitations", "strategy_sha256",
    "identical_final_strategies", "selection_decisions", "optimization_completions", "review_mode",
    "review_disagreements", "critical_user_error_units", "shared_test_ledger", "transport_audit",
    "post_registration_reporting_sources", "selection_implementation", "review_process_disclosure")
JUDGMENT_FIELDS = ("arm", "task_id", "strategy_sha256", "native_reward", "structural_status",
                   "semantic_status", "user_validity", "critical_violation", "input_tokens", "output_tokens")


def public_view(report, manifest, policies, report_hash):
    """Explicit fields only, without modifying original private records."""
    value = {key: deepcopy(report[key]) for key in REPORT_FIELDS}
    value.update(public_schema="g3-derived-public-summary-v1", original_report_sha256=report_hash,
        manifest_id=ID, original_manifest_sha256=report["manifest_sha256"],
        original_final_strategies_sha256=report["final_strategies_sha256"],
        configuration={key: deepcopy(manifest[key]) for key in ("model", "effort", "transport", "arm_order",
            "splits", "optimizer", "gepa_behavior", "episode_limits", "client_limits_per_arm", "test_client_limits",
            "test_schedule")},
        optimization_judgments=[{key: deepcopy(row[key]) for key in JUDGMENT_FIELDS}
            for row in policies["optimization_judgments"]],
        final_strategies={arm: {"text": decision["final_strategy"], "sha256": policies["strategy_sha256"][arm],
                               "decision": decision["decision"], "reasons": decision.get("reasons", [])}
            for arm, decision in policies["arms"].items()},
        review_hashes={"optimization": [{"reviewer": r["reviewer"], "sha256": r["sha256"]} for r in policies["reviews"]],
                       "test": [{"reviewer": r["reviewer"], "sha256": r["sha256"]} for r in report["test_reviews"]]},
        publication_status="local_derived_summary_not_published",
        evidence_boundary="Public synthetic tasks; raw CLI records, full catalog and original snapshots omitted. Hashes alone do not independently authenticate those missing artifacts. AI reviews are not human gold.")
    return value


def run():
    manifest, digest = verify()
    stage = ROOT / "results/g3" / ID
    arm_outcomes = {arm: verify_arm(stage / arm, arm, digest) for arm in manifest["arm_order"]}
    policies, policy_digest = verify_selection(stage, digest)
    summary, _, evidence = bind_test(ROOT, stage, manifest, digest, policies, policy_digest)
    report_path = ROOT / "results/G3_TEST_REPORT.json"
    report_hash = sha(report_path)
    if report_hash != report_path.with_suffix(".sha256").read_text().split()[0]:
        raise ValueError("Final report digest changed")
    report = load(report_path)
    if (report["manifest_sha256"] != digest or report["final_strategies_sha256"] != policy_digest
            or report["execution_status"] != summary["status"] or report["episode_evidence_sha256"] != evidence):
        raise ValueError("Final report covers different experiment evidence")
    for review in report["test_reviews"]:
        if sha(review["path"]) != review["sha256"]:
            raise ValueError("Test review changed after final report")
    for name, expected in report["post_registration_reporting_sources"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("Reporting implementation changed after final report")
    disclosure = report["review_process_disclosure"]
    if (disclosure["path"] != "research/G3_REVIEW_PROCESS_NOTE.md"
            or sha(ROOT / disclosure["path"]) != disclosure["sha256"]):
        raise ValueError("Review-process disclosure changed after final report")
    result = public_view(report, manifest, policies, report_hash)
    mechanism = {}
    for arm, arm_outcome in arm_outcomes.items():
        directory = stage / arm / "optimizer"
        admission = load(directory / "adapter/admission-events-0001.json")
        origins = load(directory / "adapter/origin-events-0001.json")
        requests = [load(p) for p in sorted((directory / "episodes").glob("episode-*/environment/request.json"))]
        strategies = {hashlib.sha256(r["strategy"].encode()).hexdigest(): r["strategy"] for r in requests}
        mechanism[arm] = {"completion": arm_outcome["completion"],
            "strategies_actually_executed": [{"sha256": h, "text": text} for h, text in strategies.items()],
            "parent_feedback": [{"task_id": r["binding"]["task_key"], "native_count": r["native_count"],
                "selected_indices": r["selected_indices"], "semantic_judge_calls": len(r["judge_call_ids"])} for r in admission],
            "origin_routes": [{"semantic_selected": r["semantic_selected"], "final_selected": r["final_selected"],
                "routes": [{"diagnosis_index": q["diagnosis_index"], "turn_idx": q["turn_idx"],
                            "origin": q["origin"], "route": q["route"]} for q in r["routes"]]} for r in origins],
            "semantic_gate_precedes_origin_intersection": arm == "B2",
            "original_arm_outcome_sha256": sha(stage / arm / "arm_outcome.json")}
    result["mechanism_execution"] = mechanism
    result["public_summary_exporter_sha256"] = sha(Path(__file__))
    target = ROOT / "results/G3_PUBLIC_SUMMARY.json"
    content = (json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    checked, changes = public_text(ROOT, "results/G3_PUBLIC_SUMMARY.json", content, {})
    if changes or checked != content:
        raise ValueError("Public summary unexpectedly needed path/text sanitization")
    write_new(target, result)
    target.with_suffix(".sha256").write_text(sha(target) + "\n", encoding="utf-8")
    return {"path": str(target), "sha256": sha(target), "status": summary["status"], "published": False}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
