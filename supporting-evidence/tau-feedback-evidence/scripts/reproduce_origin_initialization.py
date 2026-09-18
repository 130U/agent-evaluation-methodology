"""Public, zero-model reproduction of the E4 initialization control only.

Uses public pinned tau2 sources, without the private N1/G1 ledgers. Does not
reconstruct the historical natural-diagnosis audit or claim a new efficacy run.
The original E4 directory is never opened for writing.
"""
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_action_origin_audit import (TASK_IDS, STRATEGIES, initialization_checks,
    sha, write_new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/origin-initialization-reproduction")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT / "results") or output.exists():
        raise ValueError("Choose a new directory under this package's results; no overwrite")
    lock = json.loads((ROOT / "experiments/runtime_sources.json").read_text(encoding="utf-8"))
    if lock["commit"] != "2174a603f6d014ef94473ffa95957f6ce27100db":
        raise ValueError("This control requires the original pinned tau2 commit")
    verified = {}
    for item in lock["files"]:
        path = (ROOT / "vendor/tau2" / item["path"]).resolve()
        if not path.is_relative_to(ROOT / "vendor/tau2"):
            raise ValueError("Upstream source path escaped")
        data = path.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if blob != item["sha"] or len(data) != item["size"]:
            raise ValueError("Upstream source differs: " + item["path"])
        verified[path.relative_to(ROOT).as_posix()] = hashlib.sha256(data).hexdigest()
    from tau2.domains.retail.environment import get_tasks
    from tau_feedback.action_origin import ORCHESTRATOR_SHA256
    if sha(ROOT / "vendor/tau2/src/tau2/orchestrator/orchestrator.py") != ORCHESTRATOR_SHA256:
        raise ValueError("Origin classifier and runtime identity differ")
    tasks = {task.id: task for task in get_tasks("train")}
    if not set(TASK_IDS) <= tasks.keys():
        raise ValueError("Missing prescribed development task")
    output.mkdir(parents=True, exist_ok=False)
    plan = {"scope": "Reproduction of initialization control; not the historical N1 audit",
        "utc": dt.datetime.now(dt.timezone.utc).isoformat(), "tasks": TASK_IDS, "strategies": STRATEGIES,
        "upstream_sha256": verified, "local_sha256": {p: sha(ROOT / p) for p in (
            "scripts/reproduce_origin_initialization.py", "scripts/run_action_origin_audit.py",
            "src/tau_feedback/action_origin.py", "src/tau_feedback/subscription_backend.py")},
        "planned_initializations": 36, "model_calls_allowed": 0}
    write_new(output / "PLAN.json", plan)
    result = {"status": "started", "scope": plan["scope"], "plan_sha256": sha(output / "PLAN.json")}
    try:
        rows, comparisons = initialization_checks(tasks, output)
        write_new(output / "COMPARISONS.json", comparisons)
        result.update(status="complete", initializations=len(rows),
            tasks_with_unchanged_initial_trajectory=sum(r["all_initial_trajectories_equal_ignoring_timestamp"] for r in comparisons),
            tasks_with_distinct_prompts=sum(r["three_distinct_system_prompts"] for r in comparisons),
            actual_model_calls=0, generation_attempts=sum(r["model_generation_attempts"] for r in rows))
    except BaseException as exc:
        result.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        write_new(output / "RESULT.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
