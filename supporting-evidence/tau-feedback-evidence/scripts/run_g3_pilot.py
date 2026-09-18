"""Execute one fresh G3 optimization arm, after source and predecessor checks."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from register_g3_pilot import ID, SETTINGS, verify, load, sha, write_new
from run_subscription_g2 import usage, seal_arm
from tau_feedback.contracts import canonical_hash


def verify_arm(directory, arm, digest):
    seal = load(directory / "seal.json")["files_sha256"]
    current = {p.relative_to(directory).as_posix(): sha(p) for p in directory.rglob("*") if p.is_file() and p.name != "seal.json"}
    if seal != current:
        raise ValueError("Sealed G3 arm differs")
    outcome = load(directory / "arm_outcome.json")
    execution = load(directory / "optimizer/execution.json")
    if (outcome.get("status") != "complete" or outcome.get("arm") != arm or outcome.get("manifest_sha256") != digest
        or execution.get("status") != "complete" or execution.get("client_halt_reason")
        or execution.get("adapter_failure") or execution.get("reflection_failure")):
        raise ValueError("Incomplete G3 arm")
    if (outcome["completion"] != execution["completion"]
        or execution["completion"] != load(directory / "optimizer/milestones/classification.json")
        or execution["best_candidate_sha256"] != canonical_hash(load(directory / "optimizer/best_candidate.json"))
        or outcome["usage"] != usage(directory)):
        raise ValueError("G3 endpoint or cost mismatch")
    return outcome


def run(arm):
    manifest, digest = verify()
    stage = ROOT / "results/g3" / ID
    stage.mkdir(parents=True, exist_ok=True)
    if (stage / "STOP.json").exists():
        raise RuntimeError("G3 stopped; no retry")
    directory = stage / arm
    if directory.exists():
        return verify_arm(directory, arm, digest)
    previous = SETTINGS["arm_order"][:SETTINGS["arm_order"].index(arm)]
    for prior in previous:
        verify_arm(stage / prior, prior, digest)
    lock = stage / "active.lock"
    write_new(lock, {"arm": arm, "pid": os.getpid(), "utc": dt.datetime.now(dt.timezone.utc).isoformat()})
    directory.mkdir()
    write_new(directory / "arm_outcome.json", {"status": "started", "arm": arm, "manifest_sha256": digest})
    try:
        from tau_feedback.subscription_http_client import HttpCliTextClient
        from tau_feedback.g3_optimization import run_g3_arm
        from tau_feedback.gepa_adapter import EpisodeInput
        client = HttpCliTextClient(ROOT, directory / "client", model=manifest["model"], effort=manifest["effort"],
                                   **manifest["client_limits_per_arm"])
        splits = {s: [EpisodeInput(r["task_id"], r["payload"]) for r in rows]
                  for s, rows in manifest["frozen_tasks"].items() if s in {"train", "validation"}}
        result = run_g3_arm(client=client, trainset=splits["train"], valset=splits["validation"],
            seed_strategy=manifest["seed_strategy"], output=directory / "optimizer", arm=arm,
            episode_limits=manifest["episode_limits"], **manifest["optimizer"])
        execution = load(directory / "optimizer/execution.json")
        ledger = usage(directory)
        if (execution["status"] != "complete" or result.best_candidate != load(directory / "optimizer/best_candidate.json")
            or ledger.get("halt_reason") or ledger["unknown_usage_calls"]):
            raise ValueError("Incomplete optimizer or usage")
        outcome = {"status": "complete", "arm": arm, "manifest_sha256": digest,
                   "completion": execution["completion"], "usage": ledger}
        (directory / "arm_outcome.json").write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
        seal_arm(directory)
        return verify_arm(directory, arm, digest)
    except BaseException as exc:
        failure = {"status": "stopped", "arm": arm, "error_type": type(exc).__name__, "error": str(exc),
                   "manifest_sha256": digest, "utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        write_new(stage / "STOP.json", failure)
        (directory / "arm_outcome.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=SETTINGS["arm_order"])
    args = parser.parse_args()
    print(json.dumps(run(args.arm), ensure_ascii=False))
