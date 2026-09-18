"""Describe the frozen G4 parent-stage STOP without manufacturing comparisons."""
from collections import Counter
import datetime as dt
import json
from pathlib import Path
from register_g4_paired import ROOT, ID, verify, load, sha, write_new
from tau_feedback.g3_acceptance import _rows
from tau_feedback.g4_paired import text_hash


def rel(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def report():
    manifest, digest = verify()
    stage = ROOT / "results/g4" / ID
    stop = load(stage / "STOP.json")
    if (stage / "active.lock").exists() or stop.get("phase") != "parents" or (stage / "RAW_COMPLETE.json").exists():
        raise ValueError("This descriptive reporter requires the parent-stage STOP")
    if (stage / "candidates").exists() or (stage / "test").exists() or (stage / "FINAL_STRATEGIES.json").exists():
        raise ValueError("Unexpected post-STOP execution; do not silently report a prefix")
    sources = {rel(stage / n): sha(stage / n) for n in ("STOP.json", "progress.json")}
    map_path = ROOT / "research/g4-review/cohort/HOST_MAPPING_DO_NOT_GIVE_REVIEWERS.json"
    mapping = load(map_path)
    sources[rel(map_path)] = sha(map_path)
    for path, expected in mapping["execution_source_sha256"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("Captured stopped execution changed")
    review_paths = {r: ROOT / f"research/G4_COHORT_{r.upper()}_BOUND_REVIEW.json" for r in ("red", "related")}
    reviews = {}
    for reviewer, path in review_paths.items():
        labels = load(path)
        if labels["reviewer"] != reviewer or labels["manifest_sha256"] != digest:
            raise ValueError("Wrong review identity")
        if path.with_suffix(".sha256").read_text().split()[0] != sha(path):
            raise ValueError("Bound review changed")
        for name, expected in labels["source_sha256"].items():
            if sha(ROOT / name) != expected:
                raise ValueError("Original review provenance changed")
        sources.update(labels["source_sha256"])
        sources[rel(path)] = sha(path)
        reviews[reviewer] = {row["review_id"]: row for row in labels["items"]}
    expected_ids = {r["review_id"] for r in mapping["mapping"]}
    if any(set(review) != expected_ids for review in reviews.values()):
        raise ValueError("Incomplete prefix review")
    by_environment = {r["environment_directory"]: r for r in mapping["mapping"]}
    parent_rows, native_flags, gate_decisions = [], [], []
    for batch, seed in enumerate(manifest["parent_seeds"], 1):
        for index, tid in enumerate(manifest["splits"]["dev"], 1):
            directory = stage / "shared" / f"batch-{batch}" / "episodes" / f"episode-{index:04d}"
            row = {"batch": batch, "task_id": tid, "simulation_seed": seed, "assigned": True,
                   "runner_status": "not_run", "environment_status": "not_run", "research_status": "not_run"}
            if directory.exists():
                request = load(directory / "environment/request.json")
                if (request["task_id"] != tid or request["simulation_seed"] != seed
                        or request["strategy"] != manifest["seed_strategy"]):
                    raise ValueError("Parent task/strategy/seed differs")
                runner = load(directory / "runner_outcome.json")
                outcome = load(directory / "environment/outcome.json")
                row.update(runner_status=runner["status"], environment_status=outcome["status"],
                    native_reward=outcome.get("native_reward"), structural_status=outcome["structural_outcome"]["status"],
                    input_tokens=outcome.get("input_tokens"), output_tokens=outcome.get("output_tokens"),
                    strategy_sha256=text_hash(manifest["seed_strategy"]))
                mapped = by_environment.get(rel(directory / "environment"))
                if mapped:
                    pair = [reviews[r][mapped["review_id"]] for r in ("red", "related")]
                    if any(p["artifact_sha256"] != mapped["artifact_sha256"] for p in pair):
                        raise ValueError("Independent review differs from actual five-file source")
                    row.update(review_id=mapped["review_id"], independent_labels={r: reviews[r][mapped["review_id"]]
                        for r in ("red", "related")})
                    for field in ("semantic_status", "user_validity", "critical_violation", "critical_user_error"):
                        left, right = pair[0][field], pair[1][field]
                        row[field] = left if type(left) is type(right) and left == right else (
                            None if field.startswith("critical_") else "unknown")
                    checked = _rows([row], [tid], row["strategy_sha256"], "parent")[tid]
                    row["research_status"] = ("qualified" if checked["qualified"] else
                        "fail" if checked["evaluation_known"] else "unknown")
                else:
                    row["research_status"] = "unreviewed_incomplete_environment"
                native_path = directory / "native_review/native_review.json"
                if native_path.exists():
                    native = load(native_path)
                    native_flags.append({"batch": batch, "task_id": tid,
                        "critical_user_error": native["critical_user_error"],
                        "agent_diagnoses": sum(e.get("source") == "agent" for e in native["errors"]),
                        "source_path": rel(native_path), "sha256": sha(native_path)})
                for p in sorted(directory.glob("admission-*/outcome.json")):
                    gate = load(p)
                    gate_decisions.append({"batch": batch, "task_id": tid,
                        "decision": gate["admission"]["decision"], "source_path": rel(p), "sha256": sha(p),
                        "semantic_accuracy_established": False})
            parent_rows.append(row)
    actual_environments = sum(r["environment_status"] == "evaluated" for r in parent_rows)
    audit_path = ROOT / "results/G4_STOP_TRANSPORT_AUDIT.json"
    audit = load(audit_path)
    if audit["manifest_sha256"] != digest or audit["stop_sha256"] != sha(stage / "STOP.json"):
        raise ValueError("Transport audit binds another STOP")
    sources[rel(audit_path)] = sha(audit_path)
    output = {"schema_version": "g4-parent-stop-report-v1", "manifest_sha256": digest,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "report_implementation_sha256": sha(Path(__file__)),
        "implementation_timing": "Post-registration read-only analysis; formulas from frozen acceptance module",
        "source_sha256": sources, "stop": stop,
        "parent_assignment": {"assigned": 16, "environment_evaluated": actual_environments,
            "runner_states": dict(Counter(r["runner_status"] for r in parent_rows)),
            "research_states": dict(Counter(r["research_status"] for r in parent_rows))},
        "optimization_assignment": {"assigned_episodes": manifest["planned_actual_optimization_episodes"],
            "environment_evaluated": actual_environments,
            "not_run_episodes": manifest["planned_actual_optimization_episodes"] - actual_environments,
            "baseline_validation_assigned": 8, "baseline_validation_run": 0,
            "candidate_proposals_assigned": 8, "candidate_proposals_run": 0,
            "candidate_train_assigned": 32, "candidate_train_run": 0,
            "candidate_validation_assigned": 64, "candidate_validation_run": 0},
        "pair_assignment": [{"batch": b, "status": "not_run", "input_treatment_active": None,
            "qualified_rate_delta_B2_minus_B1": None} for b in range(1, 5)],
        "test": {"status": "not_reached", "maximum_planned_episodes": 36,
            "actual_schedule": None, "actual_episodes": 0, "pass_squared": None},
        "H1": "not_identified_before_candidate_generation",
        "H2": "not_activated_no_selection_or_test",
        "natural_feedback": {"native_flags": native_flags, "gate_decisions": gate_decisions,
            "warning": "Diagnoses from a stopped episode are not all admitted/sampled; gate rejection is not independent correctness"},
        "parent_rows": parent_rows, "transport": {k:v for k,v in audit["audit"].items() if k != "rows"},
        "known_costs": load(stage / "progress.json")["known_costs"],
        "limits": ["Incomplete prospective contrast; no replacement or resumed same-task experiment",
            "No effect, significance, equivalence or natural prevalence estimate from four parents",
            "AI independent first labels with prior project involvement, not human gold or full double blinding",
            "Semantic opportunity labels preserve reviewer disagreements; no silently selected consensus"]}
    target = ROOT / "results/G4_STOP_REPORT.json"
    write_new(target, output)
    target.with_suffix(".sha256").write_text(sha(target) + "\n", encoding="utf-8")
    return {k: output[k] for k in ("parent_assignment", "optimization_assignment", "H1", "H2", "known_costs")}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=2))
