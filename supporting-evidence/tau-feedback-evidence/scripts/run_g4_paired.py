"""Execute one newly registered G4 cohort; never resume stopped/partial output."""
import datetime as dt
import json
import os
from pathlib import Path
from register_g4_paired import ROOT, ID, verify, write_new
from tau_feedback.contracts import canonical_hash
from tau_feedback.gepa_adapter import EpisodeInput
from tau_feedback.g4_paired import SharedParents, MeasurementRunner, run_paired_proposal
from tau_feedback.subscription_evidence import save
from tau_feedback.subscription_optimization import SubscriptionEpisodeRunner
from tau_feedback.subscription_recovery_client import RecoveringHttpCliTextClient


def seal(directory):
    import hashlib
    files = {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in directory.rglob("*") if p.is_file()}
    write_new(directory / "seal.json", {"files_sha256": files})


def run():
    manifest, digest = verify()
    stage = ROOT / "results/g4" / ID
    stage.mkdir(parents=True, exist_ok=False)
    write_new(stage / "active.lock", {"pid": os.getpid(), "manifest_sha256": digest})
    progress = {"status": "started", "manifest_sha256": digest, "parent_batches": [], "candidate_runs": []}
    save(stage / "progress.json", progress)
    clients = []
    phase = "parents"
    try:
        splits = {s: [EpisodeInput(r["task_id"], r["payload"]) for r in rows]
                  for s, rows in manifest["frozen_tasks"].items()}
        train, val = splits["dev"], splits["validation"]
        parents = []
        for batch, seed in enumerate(manifest["parent_seeds"], 1):
            directory = stage / "shared" / f"batch-{batch}"
            directory.mkdir(parents=True)
            client = RecoveringHttpCliTextClient(ROOT, directory / "client", model=manifest["model"],
                effort=manifest["effort"], **manifest["parent_client_limits_per_batch"])
            clients.append(client)
            runner = SubscriptionEpisodeRunner(client, output=directory / "episodes", arm="B2",
                allowed_tasks={e.key: e.payload for e in train}, simulation_seed=seed, max_episodes=len(train),
                diagnosis_cap=manifest["diagnosis_cap"], sampling_salt=manifest["sampling_salt"],
                episode_limits=manifest["episode_limits"], max_strategy_chars=manifest["max_strategy_chars"])
            runner.g3_payloads = {e.key: e.payload for e in train}
            records = []
            for example in train:
                records.append(runner(example, strategy=manifest["seed_strategy"], capture_traces=True))
                print(json.dumps({"stage": "parent", "batch": batch, "task": example.key,
                                  "completed": len(records), "cli_calls": client.calls}), flush=True)
            shared = SharedParents(runner, train, manifest["seed_strategy"], records)
            shared.seal(directory / "shared_parents.json")
            seal(directory)
            parents.append(shared)
            progress["parent_batches"].append({"batch": batch, "seed": seed, "complete_episodes": len(records)})
            save(stage / "progress.json", progress)
        phase = "baseline_validation"
        directory = stage / "shared" / "baseline_validation"
        directory.mkdir(parents=True)
        client = RecoveringHttpCliTextClient(ROOT, directory / "client", model=manifest["model"],
            effort=manifest["effort"], **manifest["baseline_client_limits"])
        clients.append(client)
        baseline = MeasurementRunner(client, output=directory / "measurements", allowed_tasks={e.key: e.payload for e in val},
            max_episodes=2 * len(val), episode_limits=manifest["episode_limits"], max_strategy_chars=manifest["max_strategy_chars"])
        for rep, seed in enumerate(manifest["validation_seeds"], 1):
            for example in (val if rep == 1 else list(reversed(val))):
                baseline.run(example, strategy=manifest["seed_strategy"], seed=seed,
                    phase=f"baseline_validation_{rep}", candidate_id="S0")
                print(json.dumps({"stage": phase, "task": example.key, "repetition": rep,
                                  "completed": baseline.attempted}), flush=True)
        seal(directory)
        shared_val = {r["task_id"]: r for r in baseline.rows if r["phase"] == "baseline_validation_1"}
        phase = "paired_candidates"
        for batch, parent in enumerate(parents, 1):
            pair = {}
            for arm in manifest["arm_order_by_batch"][batch - 1]:
                directory = stage / "candidates" / f"batch-{batch}" / arm
                directory.mkdir(parents=True)
                client = RecoveringHttpCliTextClient(ROOT, directory / "client", model=manifest["model"],
                    effort=manifest["effort"], **manifest["candidate_client_limits_per_arm_batch"])
                clients.append(client)
                outcome = run_paired_proposal(client=client, parents=parent, baseline_validation=shared_val,
                    trainset=train, valset=val, train_seed=manifest["parent_seeds"][batch - 1],
                    validation_seeds=manifest["validation_seeds"], arm=arm, candidate_id=f"batch-{batch}-{arm}",
                    output=directory / "optimizer", episode_limits=manifest["episode_limits"],
                    max_strategy_chars=manifest["max_strategy_chars"], optimizer_seed=manifest["optimizer_seed"])
                seal(directory)
                pair[arm] = outcome
                progress["candidate_runs"].append({"batch": batch, "arm": arm, "status": outcome["status"],
                    "actual_candidate_episodes": outcome["actual_candidate_episodes"]})
                save(stage / "progress.json", progress)
                print(json.dumps({"stage": phase, "batch": batch, "arm": arm, "status": outcome["status"]}), flush=True)
            write_new(stage / "candidates" / f"batch-{batch}" / "PAIR_INPUT.json", {
                "batch": batch, "common_parent_record_sha256": parent.record_hashes,
                "B1_records_sha256": pair["B1"]["input_records_sha256"],
                "B2_records_sha256": pair["B2"]["input_records_sha256"],
                "input_treatment_active": pair["B1"]["input_records_sha256"] != pair["B2"]["input_records_sha256"]})
        verify()
        progress.update(status="raw_execution_complete", semantic_review="pending", final_strategy_selection="pending")
        write_new(stage / "RAW_COMPLETE.json", progress)
        return progress
    except BaseException as exc:
        failure = {"status": "stopped", "phase": phase, "error_type": type(exc).__name__, "error": str(exc),
            "manifest_sha256": digest, "utc": dt.datetime.now(dt.timezone.utc).isoformat(), "no_retry": True}
        write_new(stage / "STOP.json", failure)
        progress.update(failure)
        raise
    finally:
        progress["known_costs"] = {"cli_invocations": sum(c.calls for c in clients),
            "input_tokens": sum(c.input_tokens for c in clients), "output_tokens": sum(c.output_tokens for c in clients),
            "unknown_usage_calls": sum(c.unknown_usage_calls for c in clients),
            "elapsed_client_seconds": sum(c.elapsed_seconds for c in clients)}
        save(stage / "progress.json", progress)
        (stage / "active.lock").unlink(missing_ok=True)


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
