"""Final G4 tests after selection sealing; identical policy hashes run once."""
import json
import os
from pathlib import Path
from register_g4_paired import ROOT, ID, verify, load, sha, write_new
from run_g4_paired import seal
from tau_feedback.g4_assessment import assess_candidate, choose_final
from tau_feedback.g4_paired import MeasurementRunner, text_hash, verify_measurement
from tau_feedback.gepa_adapter import EpisodeInput
from tau_feedback.subscription_evidence import save
from tau_feedback.subscription_recovery_client import RecoveringHttpCliTextClient


class CriticalUserReview(ValueError):
    pass


def verify_stage_seal(directory, selection):
    path = directory / "seal.json"
    name = path.relative_to(ROOT).as_posix()
    if not path.is_file() or selection["source_sha256"].get(name) != sha(path):
        raise ValueError("Selection does not bind the original execution seal")
    recorded = load(path)["files_sha256"]
    current = {p.relative_to(directory).as_posix(): sha(p) for p in directory.rglob("*")
               if p.is_file() and p != path}
    if current != recorded:
        raise ValueError("Sealed execution files changed or file set differs")


def require_complete_validation(rows, manifest):
    expected = {(tid, seed) for tid in manifest["splits"]["validation"] for seed in manifest["validation_seeds"]}
    keys = [(row.get("task_id"), row.get("simulation_seed")) for row in rows]
    if len(keys) != len(expected) or set(keys) != expected:
        raise ValueError("Complete fixed validation task/seed coverage required before test")


def verify_reviewed_row(row, *, expected_strategy, manifest, selection):
    """Replay row provenance; host-provided numbers alone are insufficient."""
    relative = row["measurement_directory"]
    directory = (ROOT / relative).resolve()
    if not directory.is_relative_to(ROOT):
        raise ValueError("Measurement path escapes research project")
    measurement = load(directory / "measurement.json")
    seed_index = manifest["validation_seeds"].index(row["simulation_seed"]) + 1
    expected_phase = ("baseline" if measurement["candidate_id"] == "S0" else "candidate") + f"_validation_{seed_index}"
    if measurement["phase"] != expected_phase:
        raise ValueError("Measurement phase differs from validation repetition")
    task = next((r for r in manifest["frozen_tasks"]["validation"] if r["task_id"] == row["task_id"]), None)
    if task is None:
        raise ValueError("Selection row is outside validation")
    verify_measurement(measurement, EpisodeInput(task["task_id"], task["payload"]),
                       expected_strategy, row["simulation_seed"])
    paths = [directory / "measurement.json"] + [directory / "environment" / n for n in
        ("request.json", "outcome.json", "trajectory.json", "simulation.json", "calls.jsonl")]
    expected_review_hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths[1:]}
    for path in paths:
        if selection["source_sha256"].get(path.relative_to(ROOT).as_posix()) != sha(path):
            raise ValueError("Selection row source was not sealed")
    labels = []
    for reviewer, name in selection["review_sources"].items():
        review = load(ROOT / name)
        if review.get("reviewer") != reviewer or review.get("manifest_sha256") != selection["manifest_sha256"]:
            raise ValueError("Review identity differs")
        matches = [item for item in review["items"] if item["review_id"] == row["review_id"]]
        if len(matches) != 1 or matches[0]["artifact_sha256"] != expected_review_hashes:
            raise ValueError("Review does not bind this actual measurement")
        critical_user = matches[0].get("critical_user_error")
        if critical_user is True:
            raise CriticalUserReview("Independent review flags critical user error; stop before holdout")
        if "critical_user_error" not in matches[0] or (critical_user is not None and type(critical_user) is not bool):
            raise ValueError("Explicit critical-user review flag required")
        labels.append(matches[0])
    outcome = load(directory / "environment/outcome.json")
    expected = {"strategy_sha256": text_hash(expected_strategy), "native_reward": outcome["native_reward"],
        "structural_status": outcome["structural_outcome"]["status"],
        "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"]}
    for field in ("semantic_status", "user_validity", "critical_violation"):
        left, right = labels[0].get(field), labels[1].get(field)
        expected[field] = left if type(left) is type(right) and left == right else (None if field == "critical_violation" else "unknown")
    if any(row.get(key) != value or type(row.get(key)) is not type(value) for key, value in expected.items()):
        raise ValueError("Selection quality/cost row differs from source/review consensus")


def selected_policies(manifest, digest, selection, stage):
    if selection.get("manifest_sha256") != digest or selection.get("status") != "selection_complete":
        raise ValueError("Wrong/incomplete final strategy selection")
    if set(selection.get("arms", {})) != {"B1", "B2"} or set(selection.get("review_sources", {})) != {"red", "related"}:
        raise ValueError("Both arms and separate review sources required")
    for name, expected in selection["source_sha256"].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != expected:
            raise ValueError("Final selection source changed")
    required = {(stage / "RAW_COMPLETE.json").relative_to(ROOT).as_posix(), *selection["review_sources"].values()}
    if not required.issubset(selection["source_sha256"]):
        raise ValueError("Final source set omits cohort or review")
    for reviewer, name in selection["review_sources"].items():
        review = load(ROOT / name)
        if review.get("reviewer") != reviewer or review.get("manifest_sha256") != digest:
            raise ValueError("Independent review is from another study/reviewer")
        for item in review["items"]:
            if item.get("critical_user_error") is True:
                raise CriticalUserReview("Independent cohort review flags critical user error; stop before holdout")
    if load(stage / "RAW_COMPLETE.json").get("status") != "raw_execution_complete":
        raise ValueError("Optimization cohort is incomplete")
    require_complete_validation(selection["baseline_rows"], manifest)
    for batch in range(1, 5):
        verify_stage_seal(stage / "shared" / f"batch-{batch}", selection)
        for arm in ("B1", "B2"):
            verify_stage_seal(stage / "candidates" / f"batch-{batch}" / arm, selection)
    verify_stage_seal(stage / "shared/baseline_validation", selection)
    units = [{"task_id": tid, "simulation_seed": seed} for seed in manifest["validation_seeds"]
             for tid in manifest["splits"]["validation"]]
    policies = {"B0": {"strategy": manifest["seed_strategy"], "strategy_sha256": text_hash(manifest["seed_strategy"]),
                        "selected_batch": None, "decision": "fixed_seed"}}
    require_complete_validation(selection["baseline_rows"], manifest)
    for row in selection["baseline_rows"]:
        if not (ROOT / row["measurement_directory"]).resolve().is_relative_to(stage / "shared/baseline_validation/measurements"):
            raise ValueError("Baseline row is not the shared S0 validation")
        if load(ROOT / row["measurement_directory"] / "measurement.json")["candidate_id"] != "S0":
            raise ValueError("Baseline measurement identity differs")
        verify_reviewed_row(row, expected_strategy=manifest["seed_strategy"], manifest=manifest, selection=selection)
    for arm in ("B1", "B2"):
        candidates = selection["arms"][arm]["candidates"]
        if [c["batch"] for c in candidates] != [1, 2, 3, 4]:
            raise ValueError("All four proposal opportunities required")
        for item in candidates:
            folder = stage / "candidates" / f"batch-{item['batch']}" / arm / "optimizer"
            actual_path = folder / "adapter/actual_proposed_candidate.json"
            execution_path = folder / "execution.json"
            for path in (actual_path, execution_path):
                if path.relative_to(ROOT).as_posix() not in selection["source_sha256"]:
                    raise ValueError("Unbound candidate/optimizer source")
            actual, execution = load(actual_path), load(execution_path)
            if actual != {"strategy": item["strategy"]} or execution.get("status") != "complete":
                raise ValueError("Final selection uses another or incomplete candidate")
            if execution["gepa_selected_new"] != item["gepa_selected_new"]:
                raise ValueError("GEPA selection metadata differs")
            require_complete_validation(item["candidate_rows"], manifest)
            for row in item["candidate_rows"]:
                expected_directory = folder / "measurements"
                if not (ROOT / row["measurement_directory"]).resolve().is_relative_to(expected_directory):
                    raise ValueError("Candidate quality row belongs to another proposal")
                if load(ROOT / row["measurement_directory"] / "measurement.json")["candidate_id"] != f"batch-{item['batch']}-{arm}":
                    raise ValueError("Candidate measurement identity differs")
                verify_reviewed_row(row, expected_strategy=item["strategy"], manifest=manifest, selection=selection)
            recomputed = assess_candidate(seed_strategy=manifest["seed_strategy"], candidate_strategy=item["strategy"],
                gepa_selected_new=item["gepa_selected_new"], baseline_rows=selection["baseline_rows"],
                candidate_rows=item["candidate_rows"], units=units)
            if recomputed != item["assessment"]:
                raise ValueError("Final acceptance calculation differs")
        selected = choose_final(manifest["seed_strategy"], candidates)
        if selected != selection["arms"][arm]["selected"]:
            raise ValueError("Final ranking/strategy differs")
        policies[arm] = selected
    return policies


def test_schedule(policies, task_ids, seeds):
    unique = {}
    for arm in ("B0", "B1", "B2"):
        value = policies[arm]
        digest = text_hash(value["strategy"])
        if value["strategy_sha256"] != digest:
            raise ValueError("Policy text/hash differs")
        unique.setdefault(digest, {"strategy": value["strategy"], "arms": []})["arms"].append(arm)
    keys = list(unique)
    rows = []
    for rep, seed in enumerate(seeds):
        ordered_tasks = task_ids if rep == 0 else list(reversed(task_ids))
        for index, tid in enumerate(ordered_tasks):
            offset = (index + rep) % len(keys)
            for digest in keys[offset:] + keys[:offset]:
                rows.append({"unit": len(rows) + 1, "task_id": tid, "simulation_seed": seed,
                    "strategy_sha256": digest, "arms_sharing_observation": unique[digest]["arms"]})
    return unique, rows


def run():
    manifest, digest = verify()
    stage = ROOT / "results/g4" / ID
    if (stage / "STOP.json").exists() or (stage / "active.lock").exists():
        raise ValueError("Study stopped or another process active")
    selection_path = stage / "FINAL_STRATEGIES.json"
    if sha(selection_path) != selection_path.with_suffix(".sha256").read_text().split()[0]:
        raise ValueError("Final strategy seal differs")
    try:
        policies = selected_policies(manifest, digest, load(selection_path), stage)
    except CriticalUserReview as exc:
        write_new(stage / "STOP.json", {"status": "stopped", "phase": "independent_review", "error": str(exc), "no_retry": True})
        raise
    unique, schedule = test_schedule(policies, manifest["splits"]["test"], manifest["test_seeds"])
    directory = stage / "test"
    directory.mkdir(exist_ok=False)
    write_new(stage / "active.lock", {"pid": os.getpid(), "phase": "test"})
    write_new(directory / "PLAN.json", {"manifest_sha256": digest, "selection_sha256": sha(selection_path),
        "unique_policies": unique, "schedule": schedule, "actual_assigned_units": len(schedule),
        "no_independent_replication_for_shared_arms": True})
    summary = {"status": "started", "assigned_units": len(schedule), "completed_units": 0}
    try:
        client = RecoveringHttpCliTextClient(ROOT, directory / "client", model=manifest["model"],
            effort=manifest["effort"], **manifest["test_client_limits"])
        tasks = {r["task_id"]: EpisodeInput(r["task_id"], r["payload"]) for r in manifest["frozen_tasks"]["test"]}
        runner = MeasurementRunner(client, output=directory / "measurements", allowed_tasks={k: e.payload for k, e in tasks.items()},
            max_episodes=len(schedule), episode_limits=manifest["episode_limits"], max_strategy_chars=manifest["max_strategy_chars"])
        for unit in schedule:
            runner.run(tasks[unit["task_id"]], strategy=unique[unit["strategy_sha256"]]["strategy"],
                seed=unit["simulation_seed"], phase="heldout_test", candidate_id=unit["strategy_sha256"])
            summary["completed_units"] += 1
            save(directory / "progress.json", summary)
            print(json.dumps({**unit, "status": "official_evaluation_complete"}), flush=True)
        summary.update(status="raw_execution_complete", semantic_review="pending")
    except BaseException as exc:
        failure = {"status": "stopped", "phase": "test", "error_type": type(exc).__name__, "error": str(exc), "no_retry": True}
        write_new(stage / "STOP.json", failure)
        summary.update(failure)
        raise
    finally:
        save(directory / "progress.json", summary)
        (stage / "active.lock").unlink(missing_ok=True)
        seal(directory)


if __name__ == "__main__":
    run()
