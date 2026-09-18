"""Post-registration descriptive report bound to the actual G3 artifacts.

No model calls, selection changes, or writes inside experiment directories.
Complete and stopped tests are reportable; live tests are not final results.
"""
import argparse
import datetime as dt
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from register_g3_pilot import ID, verify, load, sha, write_new, within
from run_g3_pilot import verify_arm
from run_subscription_g2 import usage
from finalize_g3_strategies import read_review
from tau_feedback.contracts import canonical_hash
from tau_feedback.g3_reporting import aggregate_test


def verify_selection(stage, digest):
    path = stage / "FINAL_STRATEGIES.json"
    policy_digest = sha(path)
    if policy_digest != path.with_suffix(".sha256").read_text().split()[0]:
        raise ValueError("Final strategy seal changed")
    value = load(path)
    if value["manifest_sha256"] != digest or set(value["arms"]) != {"B0", "B1", "B2"}:
        raise ValueError("Final strategies do not identify this study")
    for arm, decision in value["arms"].items():
        if hashlib.sha256(decision["final_strategy"].encode()).hexdigest() != value["strategy_sha256"][arm]:
            raise ValueError("Strategy text and digest differ")
    if len(value["reviews"]) != 2 or len({r["reviewer"] for r in value["reviews"]}) != 2:
        raise ValueError("Selection needs two identified reviews")
    for review in value["reviews"]:
        if sha(review["path"]) != review["sha256"]:
            raise ValueError("Optimization review changed after strategy freeze")
    implementation = value.get("selection_implementation")
    if implementation:
        if (sha(within(ROOT, implementation["script"])) != implementation["script_sha256"]
                or sha(within(ROOT, implementation["amendment"])) != implementation["amendment_sha256"]
                or sha(ROOT / "scripts/finalize_g3_strategies.py") != implementation["original_script_sha256"]):
            raise ValueError("Declared post-registration selection fix changed")
    return value, policy_digest


def bind_test(root, stage, manifest, digest, policies, policy_digest):
    """Validate schedule prefix, assigned strategy, task, outcomes and counters."""
    directory = stage / "test"
    summary = load(directory / "summary.json")
    if summary["status"] not in {"complete", "stopped"} or (stage / "active.lock").exists():
        raise ValueError("A live test cannot be represented as a final report")
    if (summary["manifest_sha256"] != digest or summary["final_strategies_sha256"] != policy_digest
            or summary["planned_units"] != 36):
        raise ValueError("Test summary registration or policy binding differs")
    if summary["status"] == "complete":
        seal = load(directory / "seal.json")["files_sha256"]
        current = {p.relative_to(directory).as_posix(): sha(p)
                   for p in directory.rglob("*") if p.is_file() and p.name != "seal.json"}
        if seal != current or (stage / "STOP.json").exists():
            raise ValueError("Completed test seal differs or study has STOP")
    else:
        stop = load(stage / "STOP.json")
        if stop["stage"] != "test" or stop["manifest_sha256"] != digest or not stop["no_retry"]:
            raise ValueError("Stopped test lacks its matching STOP record")
    rows = summary["units"]
    schedule = manifest["test_schedule"]
    if not isinstance(rows, list) or len(rows) > len(schedule):
        raise ValueError("Invalid attempted test coverage")
    if summary["status"] == "complete" and len(rows) != 36:
        raise ValueError("Complete test must have all 36 assigned units")
    reserved_directories = {f"unit-{unit['unit']:03d}" for unit in schedule[:len(rows)]}
    actual_directories = {p.name for p in directory.glob("unit-*") if p.is_dir()}
    if not actual_directories <= reserved_directories:
        raise ValueError("Executed unit directory is missing from the attempted-unit ledger")
    tasks = {r["task_id"]: r["payload"] for r in manifest["frozen_tasks"]["test"]}
    completed = {}
    hashes = {"summary.json": sha(directory / "summary.json")}
    for position, row in enumerate(rows):
        unit = schedule[position]
        if any(type(row.get(k)) is not type(v) or row[k] != v for k, v in unit.items()):
            raise ValueError("Attempted units are not the registered schedule prefix")
        target = directory / f"unit-{unit['unit']:03d}"
        expected_relative = target.relative_to(root).as_posix()
        if row["relative_directory"] != expected_relative:
            raise ValueError("Test unit points at a different artifact directory")
        expected_strategy = policies["strategy_sha256"][unit["arm"]]
        if row["strategy_sha256"] != expected_strategy:
            raise ValueError("Unit strategy differs from frozen selection")
        request_path = target / "request.json"
        if request_path.exists():
            request = load(request_path)
            if (request["task_id"] != unit["task_id"] or request["simulation_seed"] != unit["simulation_seed"]
                    or request["task_sha256"] != canonical_hash(tasks[unit["task_id"]])
                    or request["strategy"] != policies["arms"][unit["arm"]]["final_strategy"]
                    or request["requested_config"] != manifest["episode_limits"]):
                raise ValueError("Actual episode request differs from assignment")
        outcome_path = target / "outcome.json"
        if outcome_path.exists():
            actual = load(outcome_path)
            if actual != row["outcome"] or actual["task_id"] != unit["task_id"]:
                raise ValueError("Summary outcome differs from actual episode")
            if not request_path.exists() or actual["run_id"] != request["run_id"]:
                raise ValueError("Outcome and request run identifiers differ")
        elif row["outcome"]:
            raise ValueError("Summary outcome has no actual artifact")
        if row["outcome"].get("status") == "evaluated":
            if row["execution_status"] != "returned":
                raise ValueError("Evaluated unit lacks a returned execution record")
            for name in ("request.json", "outcome.json", "trajectory.json", "simulation.json", "calls.jsonl"):
                if not (target / name).is_file():
                    raise ValueError("Evaluated episode lacks required evidence")
            simulation = load(target / "simulation.json")
            if simulation["reward_info"]["reward"] != row["outcome"]["native_reward"]:
                raise ValueError("Simulation and reported reward differ")
            completed[expected_relative] = target
        elif row["execution_status"] != "interrupted_ungraded":
            # A returned error can occur before official_complete_score raises.
            if not (row["execution_status"] == "returned" and row["outcome"].get("status") == "error"):
                raise ValueError("Unknown terminal execution state")
        for path in sorted(target.rglob("*")):
            if path.is_file():
                hashes[path.relative_to(directory).as_posix()] = sha(path)
    evaluated = len(completed)
    expected = {"attempted_units": len(rows), "evaluated_units": evaluated,
                "attempted_ungraded_units": len(rows) - evaluated, "unrun_units": 36 - len(rows)}
    if any(type(summary.get(k)) is not int or summary[k] != v for k, v in expected.items()):
        raise ValueError("Summary attempted/evaluated/unrun denominators differ")
    if summary["status"] == "complete" and evaluated != 36:
        raise ValueError("Complete test includes an ungraded unit")
    if summary["usage"] != usage(directory):
        raise ValueError("Test summary and shared cost ledger differ")
    return summary, completed, hashes


def merge_reviews(rows, completed, a, b):
    merged, disagreements, alarms = [], [], []
    for row in rows:
        value = dict(row)
        relative = row["relative_directory"]
        if relative in completed:
            for review in (a[relative], b[relative]):
                if review["critical_user_error"] and review["user_validity"] == "pass":
                    raise ValueError("Critical simulator error contradicts user validity pass")
            for field in ("semantic_status", "user_validity", "critical_violation"):
                same = a[relative][field] == b[relative][field]
                value[field] = a[relative][field] if same else (None if field == "critical_violation" else "unknown")
                if not same:
                    disagreements.append({"unit": row["unit"], "field": field,
                                          "review_a": a[relative][field], "review_b": b[relative][field]})
            if a[relative]["critical_user_error"] or b[relative]["critical_user_error"]:
                alarms.append(row["unit"])
        merged.append(value)
    return merged, disagreements, alarms


def add_rate_denominators(report):
    """Descriptive rates keep unresolved pairs and zero denominators explicit."""
    for arm, result in report["arms"].items():
        qualified = result["qualification"]["success"]
        costs = result["test_usage"]
        complete_cost = costs["complete"] and costs["accounted_records"] == 12
        result["test_cost_per_verified_success"] = {
            "denominator": qualified, "numerator_scope": "All 12 assigned test runs, including failed runs",
            "complete_test_cost": complete_cost,
            "values": {k: costs["known_subtotals"][k] / qualified if complete_cost and qualified else None
                       for k in ("cli_invocations", "input_tokens", "output_tokens", "seconds")}}
        result["confirmed_critical_violation_rate_assigned"] = len(result["critical_violation_units"]) / 12
        if arm in report["optimization_usage"]:
            result["optimization_cost_scope"] = "Reported separately; not excluded from total study costs or billed only to accepted candidates"
    for result in report["paired_B2_minus_B1"].values():
        failures = result["repair"] + result["both_failure"]
        successes = result["regression"] + result["both_success"]
        all_known = result["unresolved"] == 0
        result["repair_rate"] = {"numerator": result["repair"], "known_eligible_pairs": failures,
            "unresolved_pairs": result["unresolved"], "value": result["repair"] / failures if all_known and failures else None,
            "denominator_definition": "B1 failures among all 12 assigned pairs"}
        result["regression_rate"] = {"numerator": result["regression"], "known_eligible_pairs": successes,
            "unresolved_pairs": result["unresolved"], "value": result["regression"] / successes if all_known and successes else None,
            "denominator_definition": "B1 successes among all 12 assigned pairs"}


def run(review_a, review_b, output):
    manifest, digest = verify()
    stage = ROOT / "results/g3" / ID
    outcomes = {arm: verify_arm(stage / arm, arm, digest) for arm in manifest["arm_order"]}
    policies, policy_digest = verify_selection(stage, digest)
    summary, completed, evidence = bind_test(ROOT, stage, manifest, digest, policies, policy_digest)
    transport = {"status": "not_complete", "reason": "test_stopped"}
    if summary["status"] == "complete":
        audit_path = ROOT / "research/G3_TEST_TRANSPORT_REVIEW.json"
        audit = load(audit_path)
        if (audit["manifest_sha256"] != digest or audit["final_strategies_sha256"] != policy_digest
                or audit["completed_units"] != 36 or audit["episode_evidence_sha256"] != evidence):
            raise ValueError("Transport audit covers different final-test evidence")
        for name, expected in {**audit["evidence_hashes"], **audit["audit_sources_sha256"]}.items():
            if sha(within(ROOT, name)) != expected:
                raise ValueError("Transport audit source or raw evidence changed")
        transport = {"status": "complete", "sha256": sha(audit_path),
                     "accepted_cli_calls_replayed": audit["accepted_cli_calls_replayed"]}
    name_a, a = read_review(review_a, digest, completed)
    name_b, b = read_review(review_b, digest, completed)
    if name_a == name_b or Path(review_a).resolve() == Path(review_b).resolve():
        raise ValueError("Final reporting requires two separately identified reviews")
    rows, disagreements, alarms = merge_reviews(summary["units"], completed, a, b)
    report = aggregate_test(manifest["test_schedule"], rows,
                            {arm: outcome["usage"] for arm, outcome in outcomes.items()})
    add_rate_denominators(report)
    # Known totals must reconcile even for interrupted episodes; unavailable cost
    # remains unavailable and is never filled using a guessed provider price.
    totals = report["test_usage"]["known_subtotals"]
    if summary["status"] == "complete" and any(totals[k] != summary["usage"][k]
            for k in ("cli_invocations", "input_tokens", "output_tokens", "unknown_usage_calls")):
        raise ValueError("Per-unit costs do not reconcile with the shared ledger")
    report.update(manifest_sha256=digest, final_strategies_sha256=policy_digest,
        reported_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(), execution_status=summary["status"],
        strategy_sha256=policies["strategy_sha256"],
        identical_final_strategies=len(set(policies["strategy_sha256"].values())) == 1,
        selection_decisions={arm: d["decision"] for arm, d in policies["arms"].items()},
        selection_implementation=policies.get("selection_implementation"),
        optimization_completions={arm: o["completion"] for arm, o in outcomes.items()},
        review_mode="Two separately recorded AI reviews; disagreements become unknown; not human gold or fully blinded; incidental prior-label exposure disclosed",
        review_process_disclosure={"path": "research/G3_REVIEW_PROCESS_NOTE.md",
            "sha256": sha(ROOT / "research/G3_REVIEW_PROCESS_NOTE.md"),
            "scope": "related_work encountered some sealed optimization labels and early test summaries after saving its first six test labels; existing labels retained; no assumption of no influence"},
        test_reviews=[{"reviewer": name_a, "path": str(Path(review_a).resolve()), "sha256": sha(review_a)},
                      {"reviewer": name_b, "path": str(Path(review_b).resolve()), "sha256": sha(review_b)}],
        review_disagreements=disagreements, critical_user_error_units=alarms,
        shared_test_ledger=summary["usage"],
        transport_audit=transport,
        episode_evidence_sha256=evidence,
        post_registration_reporting_sources={str(p.relative_to(ROOT)).replace("\\", "/"): sha(p)
            for p in (Path(__file__), ROOT / "src/tau_feedback/g3_reporting.py")})
    destination = within(ROOT, output)
    if destination.is_relative_to(stage):
        raise ValueError("Report must not modify experiment directories")
    write_new(destination, report)
    destination.with_suffix(".sha256").write_text(sha(destination) + "\n", encoding="utf-8")
    return {"path": str(destination), "sha256": sha(destination), "execution_status": summary["status"],
            "identical_final_strategies": report["identical_final_strategies"],
            "qualified": {arm: r["qualification"] for arm, r in report["arms"].items()}}


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-a", required=True)
    parser.add_argument("--review-b", required=True)
    parser.add_argument("--output", default="results/G3_TEST_REPORT.json")
    args = parser.parse_args()
    print(json.dumps(run(Path(args.review_a), Path(args.review_b), args.output), ensure_ascii=False))
