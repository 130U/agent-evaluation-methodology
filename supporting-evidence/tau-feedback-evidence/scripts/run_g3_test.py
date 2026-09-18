"""Execute the preassigned 36 final units only after policy hashes are sealed."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from register_g3_pilot import ID, verify, load, sha, write_new
from run_g3_pilot import verify_arm
from run_subscription_g2 import usage, seal_arm


def run():
    manifest, digest = verify()
    stage = ROOT / "results/g3" / ID
    if (stage / "STOP.json").exists():
        raise RuntimeError("G3 stopped; no final test")
    for arm in manifest["arm_order"]:
        verify_arm(stage / arm, arm, digest)
    policies_path = stage / "FINAL_STRATEGIES.json"
    policy_hash = sha(policies_path)
    if policy_hash != policies_path.with_suffix(".sha256").read_text().split()[0]:
        raise ValueError("Final policy digest changed")
    policies = load(policies_path)
    if policies["manifest_sha256"] != digest or set(policies["arms"]) != {"B0", "B1", "B2"}:
        raise ValueError("Final policies belong to another study")
    for arm, decision in policies["arms"].items():
        if hashlib.sha256(decision["final_strategy"].encode()).hexdigest() != policies["strategy_sha256"][arm]:
            raise ValueError("Strategy text/hash mismatch")
    for review in policies["reviews"]:
        if sha(review["path"]) != review["sha256"]:
            raise ValueError("Selection review changed")
    destination = stage / "test"
    if destination.exists():
        raise RuntimeError("Final test cannot be rerun or resumed")
    lock = stage / "active.lock"
    write_new(lock, {"stage": "test", "pid": os.getpid()})
    destination.mkdir()
    summary = {"status": "started", "manifest_sha256": digest, "final_strategies_sha256": policy_hash,
               "planned_units": 36, "units": [], "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    def persist():
        summary["unrun_units"] = 36 - len(summary["units"])
        summary["attempted_units"] = len(summary["units"])
        summary["evaluated_units"] = sum(r.get("outcome", {}).get("status") == "evaluated" for r in summary["units"])
        summary["attempted_ungraded_units"] = summary["attempted_units"] - summary["evaluated_units"]
        summary["usage"] = usage(destination)
        (destination / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    try:
        from tau_feedback.subscription_http_client import HttpCliTextClient
        from tau_feedback.subscription_episode import run_subscription_episode
        from tau_feedback.subscription_optimization import official_complete_score
        from tau2.data_model.tasks import Task
        client = HttpCliTextClient(ROOT, destination / "client", model=manifest["model"], effort=manifest["effort"],
                                   **manifest["test_client_limits"])
        frozen = {r["task_id"]: r["payload"] for r in manifest["frozen_tasks"]["test"]}
        persist()
        for unit in manifest["test_schedule"]:
            directory = destination / f"unit-{unit['unit']:03d}"
            strategy = policies["arms"][unit["arm"]]["final_strategy"]
            if sha(policies_path) != policy_hash:
                raise ValueError("Final policy changed during test")
            reserved = {**unit, "execution_status": "started", "outcome": {},
                "relative_directory": directory.relative_to(ROOT).as_posix(),
                "strategy_sha256": policies["strategy_sha256"][unit["arm"]]}
            summary["units"].append(reserved)
            persist()
            row = run_subscription_episode(client, Task.model_validate(frozen[unit["task_id"]]),
                output=directory, simulation_seed=unit["simulation_seed"], strategy=strategy,
                **manifest["episode_limits"])
            reserved.update(outcome=row, execution_status="returned")
            persist()
            official_complete_score(row)
            print(json.dumps({"unit_complete": unit, "official_status": row["structural_outcome"]["status"],
                              "calls_so_far": client.calls}), flush=True)
        summary.update(status="complete", completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        persist()
        seal_arm(destination)
        return {"status": summary["status"], "planned_units": 36, "executed_units": len(summary["units"]), "usage": summary["usage"]}
    except BaseException as exc:
        if summary["units"] and summary["units"][-1]["execution_status"] == "started":
            pending = summary["units"][-1]
            pending["execution_status"] = "interrupted_ungraded"
            outcome_path = ROOT / pending["relative_directory"] / "outcome.json"
            if outcome_path.exists():
                pending["outcome"] = load(outcome_path)
        summary.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
        persist()
        write_new(stage / "STOP.json", {"stage": "test", "manifest_sha256": digest,
            "error_type": type(exc).__name__, "error": str(exc), "no_retry": True})
        raise
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
