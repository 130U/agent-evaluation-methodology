"""Post-registration replay of frozen G4 selection; never launches a model.

Only a complete, sealed cohort is eligible. Review inputs are the two mapped
reviews (reviewer red/related), not the anonymous packets or partial STOP audit.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
import register_g4_paired as registration
import run_g4_test as test_entry
from tau_feedback.contracts import canonical_hash
from tau_feedback.g4_assessment import assess_candidate, choose_final
from tau_feedback.g4_paired import text_hash, verify_measurement
from tau_feedback.gepa_adapter import EpisodeInput
from tau_feedback.subscription_optimization import official_complete_score

FILES = ("request.json", "outcome.json", "trajectory.json", "simulation.json", "calls.jsonl")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def within(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Source path escapes research project")
    return path


def relative(root, path):
    return within(root, path).relative_to(Path(root).resolve()).as_posix()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def verify_manifest(root, manifest_path):
    """Same frozen settings, source bytes and snapshot, with an explicit path."""
    root, path = Path(root).resolve(), within(root, manifest_path)
    digest = sha(path)
    if path.with_suffix(".sha256").read_text().split() != [digest]:
        raise ValueError("G4 manifest hash differs")
    manifest = load(path)
    if any(manifest.get(k) != v for k, v in registration.SETTINGS.items()):
        raise ValueError("Registered G4 settings differ")
    sources = manifest["source_hashes"]
    for name, expected in sources.items():
        if sha(within(root, name)) != expected:
            raise ValueError("Frozen G4 source changed: " + name)
    archive = within(root, manifest["snapshot_path"])
    if archive.with_suffix(".zip.sha256").read_text().split() != [sha(archive)]:
        raise ValueError("G4 source archive changed")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate snapshot members")
        for name, expected in sources.items():
            if hashlib.sha256(bundle.read(name)).hexdigest() != expected:
                raise ValueError("Frozen snapshot source differs")
        if bundle.read(relative(root, path)) != path.read_bytes():
            raise ValueError("Snapshot embeds another manifest")
    for split, ids in manifest["splits"].items():
        rows = manifest["frozen_tasks"][split]
        if [r["task_id"] for r in rows] != ids:
            raise ValueError("Frozen task order differs")
        for row in rows:
            if canonical_hash(row["payload"]) != row["payload_sha256"]:
                raise ValueError("Frozen task payload hash differs")
    return manifest, digest


def require_idle_complete(stage, manifest, digest):
    if not stage.is_dir() or any((stage / name).exists() for name in (
        "active.lock", "STOP.json", "test", "FINAL_STRATEGIES.json",
        "FINAL_STRATEGIES.sha256", "SELECTION_BLOCKED.json")):
        raise ValueError("G4 is active, stopped, already selected/tested or blocked")
    raw = load(stage / "RAW_COMPLETE.json")
    if raw.get("status") != "raw_execution_complete" or raw.get("manifest_sha256") != digest:
        raise ValueError("Complete original G4 cohort required")
    parents = [{"batch": i, "seed": seed, "complete_episodes": len(manifest["splits"]["dev"])}
               for i, seed in enumerate(manifest["parent_seeds"], 1)]
    candidates = [{"batch": i, "arm": arm, "status": "complete",
                   "actual_candidate_episodes": len(manifest["splits"]["dev"]) + 2 * len(manifest["splits"]["validation"])}
                  for i, order in enumerate(manifest["arm_order_by_batch"], 1) for arm in order]
    if raw.get("parent_batches") != parents or raw.get("candidate_runs") != candidates:
        raise ValueError("RAW_COMPLETE does not cover every fixed parent and proposal")


def add_source(selection, root, path):
    path = within(root, path)
    name, digest = relative(root, path), sha(path)
    if name in selection["source_sha256"] and selection["source_sha256"][name] != digest:
        raise ValueError("Source changed during selection analysis")
    selection["source_sha256"][name] = digest


def bind_stage(selection, root, directory):
    seal = directory / "seal.json"
    recorded = load(seal)["files_sha256"]
    actual = {p.relative_to(directory).as_posix(): sha(p) for p in directory.rglob("*")
              if p.is_file() and p != seal}
    if actual != recorded:
        raise ValueError("Original stage seal file set or bytes changed")
    add_source(selection, root, seal)
    for name in recorded:
        path = within(root, directory / name)
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Stage artifact escapes its sealed directory")
        add_source(selection, root, path)


def environment_record(root, directory, task, strategy, seed):
    request, outcome = load(directory / "request.json"), load(directory / "outcome.json")
    if (request.get("task_id") != task["task_id"] or request.get("task_sha256") != task["payload_sha256"]
        or request.get("simulation_seed") != seed or request.get("strategy") != strategy
        or request.get("run_id") != outcome.get("run_id")):
        raise ValueError("Actual environment request differs from fixed identity")
    official_complete_score(outcome)
    simulation, trajectory = load(directory / "simulation.json"), load(directory / "trajectory.json")
    if (simulation["id"] != outcome["run_id"] or simulation["task_id"] != task["task_id"]
        or simulation["messages"] != trajectory or simulation["reward_info"]["reward"] != outcome["native_reward"]):
        raise ValueError("Simulation/trajectory/outcome identity differs")
    return {"environment_directory": relative(root, directory), "task_id": task["task_id"],
            "simulation_seed": seed, "strategy_sha256": text_hash(strategy),
            "artifact_sha256": {relative(root, directory / name): sha(directory / name) for name in FILES},
            "outcome": outcome}


def measurement_records(root, directory, task_sets, strategy, candidate_id):
    rows = load(directory / "index.json")
    expected = {(phase, task["task_id"], seed): task for phase, tasks, seed in task_sets for task in tasks}
    found, output = set(), []
    expected_paths = {f"measurement-{i:04d}" for i in range(1, len(expected) + 1)}
    if {p.name for p in directory.iterdir() if p.is_dir()} != expected_paths or len(rows) != len(expected):
        raise ValueError("Measurement cohort directory count differs")
    for i, row in enumerate(rows, 1):
        key = row["phase"], row["task_id"], row["simulation_seed"]
        path = directory / f"measurement-{i:04d}"
        if key not in expected or key in found or row.get("measurement_id") != i:
            raise ValueError("Missing, duplicate or unexpected measurement unit")
        if Path(row["directory"]).resolve() != path.resolve() or row["candidate_id"] != candidate_id:
            raise ValueError("Measurement directory/candidate identity differs")
        # Frozen verifier authenticates all hashes before reading artifacts. First
        # constrain each path to this environment, including optional sidecars.
        if any(not within(root, name).is_relative_to((path / "environment").resolve())
               for name in row["artifacts_sha256"]):
            raise ValueError("Measurement artifact outside its own environment")
        task = expected[key]
        verify_measurement(row, EpisodeInput(task["task_id"], task["payload"]), strategy, key[2])
        record = environment_record(root, path / "environment", task, strategy, key[2])
        record.update(measurement_directory=relative(root, path), phase=row["phase"])
        output.append(record)
        found.add(key)
    if found != set(expected):
        raise ValueError("Measurement schedule incomplete")
    return output


def read_cohort(root, manifest, selection, stage):
    dev, val = manifest["frozen_tasks"]["dev"], manifest["frozen_tasks"]["validation"]
    seed_strategy, all_records = manifest["seed_strategy"], []
    for batch, seed in enumerate(manifest["parent_seeds"], 1):
        directory = stage / "shared" / f"batch-{batch}"
        bind_stage(selection, root, directory)
        shared = load(directory / "shared_parents.json")
        if (shared["strategy_sha256"] != text_hash(seed_strategy) or shared["simulation_seed"] != seed
            or set(shared["records"]) != {t["task_id"] for t in dev}
            or {k: canonical_hash(v) for k, v in shared["records"].items()} != shared["record_sha256"]):
            raise ValueError("Shared parent identities or record hashes differ")
        episodes = directory / "episodes"
        if {p.name for p in episodes.iterdir() if p.is_dir()} != ({"host_evidence"} | {f"episode-{i:04d}" for i in range(1, len(dev) + 1)}):
            raise ValueError("Shared parent episode count differs")
        for i, task in enumerate(dev, 1):
            folder = episodes / f"episode-{i:04d}"
            record = environment_record(root, folder / "environment", task, seed_strategy, seed)
            runner = load(folder / "runner_outcome.json")
            if (runner.get("status") != "complete" or runner.get("task_id") != task["task_id"]
                or runner.get("run_id") != record["outcome"]["run_id"]
                or runner.get("score") != official_complete_score(record["outcome"])):
                raise ValueError("Parent runner did not complete this actual episode")
            record["phase"] = "shared_parent"
            all_records.append(record)
    baseline_dir = stage / "shared/baseline_validation"
    bind_stage(selection, root, baseline_dir)
    baseline = measurement_records(root, baseline_dir / "measurements",
        [(f"baseline_validation_{i}", val, seed) for i, seed in enumerate(manifest["validation_seeds"], 1)], seed_strategy, "S0")
    all_records.extend(baseline)
    candidates = {"B1": [], "B2": []}
    for batch in range(1, 5):
        pair_path = stage / "candidates" / f"batch-{batch}/PAIR_INPUT.json"
        add_source(selection, root, pair_path)
        pair = load(pair_path)
        if pair.get("batch") != batch:
            raise ValueError("Wrong pair metadata identity")
        for arm in ("B1", "B2"):
            directory = stage / "candidates" / f"batch-{batch}" / arm
            bind_stage(selection, root, directory)
            folder = directory / "optimizer"
            actual, execution = load(folder / "adapter/actual_proposed_candidate.json"), load(folder / "execution.json")
            if set(actual) != {"strategy"} or not isinstance(actual["strategy"], str) or not actual["strategy"].strip():
                raise ValueError("Invalid actual candidate text")
            strategy = actual["strategy"]
            if len(strategy) > manifest["max_strategy_chars"]:
                raise ValueError("Candidate exceeds frozen text cap")
            best = load(folder / "gepa_best_candidate.json")
            if (execution.get("status") != "complete" or execution.get("arm") != arm
                or execution.get("candidate_id") != f"batch-{batch}-{arm}"
                or execution.get("actual_proposed_candidate_sha256") != text_hash(strategy)
                or type(execution.get("gepa_selected_new")) is not bool
                or execution["gepa_selected_new"] != (best != {"strategy": seed_strategy})
                or best not in ({"strategy": seed_strategy}, actual)
                or execution.get("actual_candidate_episodes") != len(dev) + 2 * len(val)
                or execution.get("reflection_attempts") != 1
                or execution.get("completion", {}).get("generated_candidates") != 1
                or any(execution.get(k) for k in ("reflection_failure", "adapter_failure", "client_halt_reason"))):
                raise ValueError("Candidate execution does not establish the fixed completed proposal")
            if pair.get(arm + "_records_sha256") != execution["input_records_sha256"]:
                raise ValueError("Pair inputs differ from actual reflection execution")
            records = measurement_records(root, folder / "measurements",
                [("candidate_train", dev, manifest["parent_seeds"][batch - 1])] +
                [(f"candidate_validation_{i}", val, seed) for i, seed in enumerate(manifest["validation_seeds"], 1)],
                strategy, f"batch-{batch}-{arm}")
            all_records.extend(records)
            candidates[arm].append({"batch": batch, "strategy": strategy,
                "gepa_selected_new": execution["gepa_selected_new"],
                "records": [r for r in records if r["phase"].startswith("candidate_validation_")]})
        if pair.get("input_treatment_active") is not (pair["B1_records_sha256"] != pair["B2_records_sha256"]):
            raise ValueError("Pair activation differs from fixed input-hash definition")
        shared = load(stage / "shared" / f"batch-{batch}/shared_parents.json")
        if pair.get("common_parent_record_sha256") != shared["record_sha256"]:
            raise ValueError("Pair does not bind the original shared parents")
    if len(all_records) != manifest["planned_actual_optimization_episodes"]:
        raise ValueError("Complete registered cohort denominator required")
    return all_records, baseline, candidates


def bind_reviews(root, manifest_digest, selection, records):
    expected = {tuple(sorted(r["artifact_sha256"].items())): r for r in records}
    if len(expected) != len(records):
        raise ValueError("Duplicate cohort evidence identities")
    by_reviewer, critical = {}, []
    for reviewer, name in selection["review_sources"].items():
        add_source(selection, root, name)
        review = load(within(root, name))
        if review.get("reviewer") != reviewer or review.get("manifest_sha256") != manifest_digest:
            raise ValueError("Review identity differs")
        # The host can bind original anonymous labels, map and packet here.
        for origin, digest in review.get("source_sha256", {}).items():
            if sha(within(root, origin)) != digest:
                raise ValueError("Original review provenance changed")
            add_source(selection, root, origin)
        indexed, ids = {}, set()
        for item in review["items"]:
            key = tuple(sorted(item["artifact_sha256"].items()))
            review_id = item.get("review_id")
            if not isinstance(review_id, str) or not review_id or review_id in ids or key in indexed or key not in expected:
                raise ValueError("Duplicate, missing or foreign mapped review identity")
            for field in ("semantic_status", "user_validity"):
                if item.get(field) not in {"pass", "fail", "unknown"}:
                    raise ValueError("Explicit review status required")
            for field in ("critical_violation", "critical_user_error"):
                if field not in item or (item[field] is not None and type(item[field]) is not bool):
                    raise ValueError("Explicit boolean/null review flag required")
            if not isinstance(item.get("evidence"), list) or not item["evidence"]:
                raise ValueError("Review evidence required")
            if item["critical_user_error"] is True:
                critical.append({"reviewer": reviewer, "review_id": review_id,
                    "environment_directory": expected[key]["environment_directory"],
                    "artifact_sha256": item["artifact_sha256"]})
            indexed[key] = item
            ids.add(review_id)
        if set(indexed) != set(expected):
            raise ValueError("Both independent reviews must cover the entire complete cohort")
        by_reviewer[reviewer] = indexed
    for key in expected:
        if by_reviewer["red"][key]["review_id"] != by_reviewer["related"][key]["review_id"]:
            raise ValueError("Mapped reviews disagree on opaque unit identity")
    if critical:
        error = test_entry.CriticalUserReview("Independent cohort review flags critical user error; prohibit selection and holdout")
        error.flags = critical
        raise error
    return by_reviewer


def reviewed_rows(records, reviews):
    result = []
    for record in records:
        key = tuple(sorted(record["artifact_sha256"].items()))
        left, right = reviews["red"][key], reviews["related"][key]
        outcome = record["outcome"]
        row = {k: record[k] for k in ("measurement_directory", "task_id", "simulation_seed", "strategy_sha256")}
        row.update(review_id=left["review_id"], native_reward=outcome["native_reward"],
            structural_status=outcome["structural_outcome"]["status"],
            input_tokens=outcome["input_tokens"], output_tokens=outcome["output_tokens"])
        for field in ("semantic_status", "user_validity", "critical_violation"):
            a, b = left[field], right[field]
            row[field] = a if type(a) is type(b) and a == b else (None if field == "critical_violation" else "unknown")
        result.append(row)
    return result


def build_selection(root, manifest, digest, stage, review_sources, *, analysis_metadata):
    root, stage = Path(root).resolve(), within(root, stage)
    require_idle_complete(stage, manifest, digest)
    if set(review_sources) != {"red", "related"} or len(set(review_sources.values())) != 2:
        raise ValueError("Two distinct mapped review sources required")
    selection = {"status": "selection_complete", "manifest_sha256": digest,
        "review_sources": {k: relative(root, v) for k, v in review_sources.items()},
        "source_sha256": {}, "post_registration_analysis": analysis_metadata, "arms": {}}
    add_source(selection, root, stage / "RAW_COMPLETE.json")
    records, baseline, candidates = read_cohort(root, manifest, selection, stage)
    reviews = bind_reviews(root, digest, selection, records)
    selection["reviewed_cohort_episodes"] = len(records)
    selection["baseline_rows"] = reviewed_rows(baseline, reviews)
    units = [{"task_id": tid, "simulation_seed": seed} for seed in manifest["validation_seeds"]
             for tid in manifest["splits"]["validation"]]
    for arm in ("B1", "B2"):
        items = []
        for candidate in candidates[arm]:
            item = {k: candidate[k] for k in ("batch", "strategy", "gepa_selected_new")}
            item["candidate_rows"] = reviewed_rows(candidate["records"], reviews)
            item["assessment"] = assess_candidate(seed_strategy=manifest["seed_strategy"],
                candidate_strategy=item["strategy"], gepa_selected_new=item["gepa_selected_new"],
                baseline_rows=selection["baseline_rows"], candidate_rows=item["candidate_rows"], units=units)
            items.append(item)
        selection["arms"][arm] = {"candidates": items, "selected": choose_final(manifest["seed_strategy"], items)}
    # Use the unchanged consumer as final contract authority, including row replay.
    if Path(test_entry.ROOT).resolve() != root:
        raise ValueError("Frozen holdout verifier must refer to the same project root")
    test_entry.selected_policies(manifest, digest, selection, stage)
    return selection


def finalize(root, manifest_path, review_sources):
    root = Path(root).resolve()
    manifest_path = within(root, manifest_path)
    manifest, digest = verify_manifest(root, manifest_path)
    stage = root / "results/g4" / manifest["manifest_id"]
    require_idle_complete(stage, manifest, digest)
    script = Path(__file__).resolve()
    metadata = {"script": relative(root, script), "script_sha256": sha(script),
        "analysis_started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "registered_at_utc": manifest["registered_at_utc"],
        "implemented_after_registration": True,
        "scope": "Replay frozen assessment, consensus and selection formulas; no analysis-rule change; no model call"}
    try:
        selection = build_selection(root, manifest, digest, stage, review_sources, analysis_metadata=metadata)
        for path in (script, manifest_path, manifest_path.with_suffix(".sha256")):
            add_source(selection, root, path)
        # Revalidate all evidence immediately before write-once output.
        require_idle_complete(stage, manifest, digest)
        test_entry.selected_policies(manifest, digest, selection, stage)
        output = stage / "FINAL_STRATEGIES.json"
        write_new(output, selection)
        with output.with_suffix(".sha256").open("x", encoding="utf-8") as stream:
            stream.write(sha(output) + "\n")
        return {"status": "selection_complete", "path": relative(root, output), "sha256": sha(output), "model_calls": 0}
    except BaseException as exc:
        blocked = {"status": "selection_blocked", "manifest_sha256": digest,
            "post_registration_analysis": metadata, "error_type": type(exc).__name__, "error": str(exc),
            "critical_user_flags": getattr(exc, "flags", []), "model_calls": 0,
            "review_sources": {k: {"path": relative(root, v), "sha256": sha(within(root, v))}
                               for k, v in review_sources.items() if within(root, v).is_file()}}
        if isinstance(exc, test_entry.CriticalUserReview):
            write_new(stage / "STOP.json", {**blocked, "status": "stopped", "phase": "independent_review", "no_retry": True})
        write_new(stage / "SELECTION_BLOCKED.json", blocked)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=registration.MANIFEST)
    parser.add_argument("--review-red", required=True, help="Mapped red review, project-relative path")
    parser.add_argument("--review-related", required=True, help="Mapped related review, project-relative path")
    args = parser.parse_args()
    print(json.dumps(finalize(ROOT, args.manifest, {"red": args.review_red, "related": args.review_related}), ensure_ascii=False))


if __name__ == "__main__":
    main()
