"""Run exactly one frozen G2 arm; B2 follows a sealed B1, never a retry.

No model call on import. Existing incomplete/failed arms stop the whole stage.
Independent research interpretation is separate from technical completion.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from register_subscription_g2 import (MANIFEST_ID, MANIFEST_NAME, PROTOCOL, SETTINGS, SNAPSHOT,
    official_tasks, load, sha, within, write_new)
from tau_feedback.contracts import canonical_hash


def verify_manifest(root, manifest_path, *, task_loader=official_tasks):
    root, path = Path(root).resolve(), within(root, manifest_path)
    digest = sha(path)
    if digest != path.with_suffix(".sha256").read_text(encoding="utf-8").split()[0]:
        raise ValueError("G2 registration digest differs")
    manifest = load(path)
    if any(manifest.get(key) != value for key, value in SETTINGS.items()):
        raise ValueError("G2 exact settings differ; new settings require a new study")
    if manifest.get("snapshot_path") != SNAPSHOT or manifest.get("protocol_path") != PROTOCOL:
        raise ValueError("G2 snapshot/protocol identity differs")
    hashes = manifest["source_hashes"]
    required = {PROTOCOL, "scripts/register_subscription_g2.py", "scripts/run_subscription_g2.py",
        "src/tau_feedback/subscription_optimization.py", "src/tau_feedback/subscription_client.py",
        "src/tau_feedback/subscription_http_client.py", "src/tau_feedback/gepa_milestones.py",
        "requirements.lock", "experiments/runtime_sources.json", "experiments/gepa_sources.json",
        "experiments/cli_model_catalog_sol.json", "STRUCTURAL_GROUPS.json"}
    if not required <= set(hashes):
        raise ValueError("Missing mandatory G2 source binding")
    for name, expected in hashes.items():
        if sha(within(root, name)) != expected:
            raise ValueError("Frozen G2 source changed: " + name)
    snapshot = within(root, manifest["snapshot_path"])
    if sha(snapshot) != snapshot.with_suffix(".zip.sha256").read_text(encoding="utf-8").split()[0]:
        raise ValueError("G2 source ZIP digest differs")
    import hashlib
    with zipfile.ZipFile(snapshot) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("Duplicate frozen ZIP member")
        for name, expected in hashes.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("G2 source ZIP member changed: " + name)
        if archive.read(path.relative_to(root).as_posix()) != path.read_bytes():
            raise ValueError("Source ZIP embeds another manifest")
    tasks = {task.id: task for task in task_loader()}
    groups = {tid: group["group_id"] for group in load(root / "STRUCTURAL_GROUPS.json") for tid in group["task_ids"]}
    if set(manifest["frozen_tasks"]) != set(SETTINGS["task_selection"]):
        raise ValueError("Frozen G2 task roles differ")
    for role, expected in SETTINGS["task_selection"].items():
        row = manifest["frozen_tasks"][role]
        payload = tasks[expected["task_id"]].model_dump(mode="json")
        if (row.get("task_id") != expected["task_id"] or row.get("structural_group") != expected["structural_group"]
            or groups[expected["task_id"]] != expected["structural_group"]
            or row.get("payload") != payload or row.get("payload_sha256") != canonical_hash(payload)):
            raise ValueError("G2 frozen task payload/group differs")
    return manifest, digest


def usage(directory):
    """Known subtotals stay available after failure; absent ledgers are null."""
    path = directory / "client/budget.json"
    keys = {"cli_invocations": "calls_reserved", "input_tokens": "known_input_tokens",
            "output_tokens": "known_output_tokens", "unknown_usage_calls": "unknown_usage_calls"}
    if not path.exists():
        return {**{key: None for key in keys}, "client_seconds": None, "ledger_available": False}
    data = load(path)
    result = {key: data.get(original) for key, original in keys.items()}
    if any(type(v) is not int or v < 0 for v in result.values()):
        raise ValueError("Invalid persisted G2 client ledger")
    seconds = data.get("wall_seconds_since_client_start")
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Missing/invalid G2 wall-clock accounting")
    return {**result, "client_seconds": seconds, "ledger_available": True,
            "halt_reason": data.get("halt_reason")}


def stage_summary(destination, manifest):
    rows = []
    for arm in manifest["arm_order"]:
        directory = destination / arm
        if not directory.exists():
            rows.append({"arm": arm, "status": "not_run", "usage": None})
            continue
        outcome = load(directory / "arm_outcome.json") if (directory / "arm_outcome.json").exists() else {"status": "incomplete"}
        try:
            ledger = usage(directory)
        except (OSError, ValueError, TypeError) as exc:
            ledger = {"ledger_available": False, "error": str(exc)}
        rows.append({"arm": arm, **outcome, "usage": ledger})
    known = [r["usage"] for r in rows if r["usage"] and r["usage"].get("ledger_available")]
    unavailable = [r["arm"] for r in rows if r["status"] != "not_run" and not r["usage"].get("ledger_available")]
    return {"manifest_id": MANIFEST_ID, "planned_arms": 2, "arm_order": manifest["arm_order"], "arms": rows,
        "stopped": (destination / "STOP.json").exists(), "known_cost_subtotals": {
            key: sum(v[key] for v in known) for key in ("cli_invocations", "input_tokens", "output_tokens", "unknown_usage_calls", "client_seconds")},
        "arms_with_unavailable_usage": unavailable, "prior_g1_n1_and_probe_costs_included": False,
        "scope": "Exploratory development cases, unmatched stochastic arms; no causal effect, average uplift or generalization claim"}


def seal_arm(directory):
    files = {p.relative_to(directory).as_posix(): sha(p) for p in directory.rglob("*") if p.is_file()}
    write_new(directory / "seal.json", {"files_sha256": files})


def verify_complete_arm(directory, arm, digest, manifest, *, sealed=True):
    if sealed:
        seal = load(directory / "seal.json")
        current = {p.relative_to(directory).as_posix(): sha(p) for p in directory.rglob("*") if p.is_file() and p.name != "seal.json"}
        if seal.get("files_sha256") != current:
            raise ValueError("Completed G2 arm changed after sealing")
    outcome, execution = load(directory / "arm_outcome.json"), load(directory / "optimizer/execution.json")
    if (outcome.get("status") != "complete" or outcome.get("arm") != arm or outcome.get("manifest_sha256") != digest
        or execution.get("status") != "complete" or execution.get("arm") != arm
        or execution.get("client_halt_reason") or execution.get("adapter_failure") or execution.get("reflection_failure")):
        raise ValueError("G2 arm has incomplete or swallowed failure state")
    if (execution.get("completion") != load(directory / "optimizer/milestones/classification.json")
        or execution.get("best_candidate_sha256") != canonical_hash(load(directory / "optimizer/best_candidate.json"))
        or outcome.get("completion") != execution["completion"]):
        raise ValueError("G2 endpoint/candidate evidence differs")
    ledger = usage(directory)
    if (not ledger["ledger_available"] or ledger.get("halt_reason") or ledger["unknown_usage_calls"]
        or outcome.get("usage") != ledger):
        raise ValueError("Completed G2 arm has missing/unknown/changed cost")
    limits = manifest["client_limits_per_arm"]
    if (load(directory / "client/budget.json").get("limits") != limits
        or ledger["cli_invocations"] > limits["max_calls"] or ledger["input_tokens"] > limits["max_input_tokens"]
        or ledger["output_tokens"] > limits["max_output_tokens"] or ledger["client_seconds"] > limits["max_seconds"]):
        raise ValueError("Completed G2 arm exceeds or changes its shared budget")
    return outcome


def run(root, manifest_path, arm, *, client_factory=None, arm_runner=None, task_loader=official_tasks):
    if arm not in SETTINGS["arm_order"]:
        raise ValueError("Select B1 or B2")
    root = Path(root).resolve()
    manifest, digest = verify_manifest(root, manifest_path, task_loader=task_loader)
    destination = root / "results/g2" / MANIFEST_ID
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "STOP.json").exists():
        raise RuntimeError("G2 is stopped; never retry or proceed to another arm")
    lock = destination / "active.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"arm": arm, "pid": os.getpid(), "utc": dt.datetime.now(dt.timezone.utc).isoformat()}))
    directory, client = destination / arm, None
    try:
        if directory.exists():
            # Valid completed work may be inspected but is never executed again.
            verify_complete_arm(directory, arm, digest, manifest)
            return {"status": "already_complete_no_rerun", "arm": arm, "summary": stage_summary(destination, manifest)}
        if arm == "B1" and (destination / "B2").exists():
            raise RuntimeError("B2 cannot precede B1")
        if arm == "B2":
            verify_complete_arm(destination / "B1", "B1", digest, manifest)
        directory.mkdir(exist_ok=False)
        write_new(directory / "arm_outcome.json", {"arm": arm, "status": "started", "manifest_sha256": digest})
        if client_factory is None:
            from tau_feedback.subscription_http_client import HttpCliTextClient
            client_factory = HttpCliTextClient
        if arm_runner is None:
            from tau_feedback.subscription_optimization import run_gepa_arm
            arm_runner = run_gepa_arm
        from tau_feedback.gepa_adapter import EpisodeInput
        client = client_factory(root, directory / "client", model=manifest["model"], effort=manifest["effort"],
                                **manifest["client_limits_per_arm"])
        inputs = {role: EpisodeInput(row["task_id"], row["payload"]) for role, row in manifest["frozen_tasks"].items()}
        result = arm_runner(client=client, trainset=[inputs["train"]], valset=[inputs["dev_validation"]],
            seed_strategy=manifest["seed_strategy"], output=directory / "optimizer", arm=arm,
            episode_limits=manifest["episode_limits"], **manifest["optimizer"])
        execution = load(directory / "optimizer/execution.json")
        if execution.get("status") != "complete" or result.best_candidate != load(directory / "optimizer/best_candidate.json"):
            raise ValueError("Returned GEPA result is not its recorded complete endpoint")
        outcome = {"arm": arm, "status": "complete", "manifest_sha256": digest,
                   "completion": execution["completion"], "usage": usage(directory),
                   "semantic_interpretation": "Independent research review required; technical completion is not a benefit"}
        (directory / "arm_outcome.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verify_complete_arm(directory, arm, digest, manifest, sealed=False)
        seal_arm(directory)
        verify_complete_arm(directory, arm, digest, manifest)
        return outcome
    except BaseException as exc:
        error = {"arm": arm, "status": "stopped", "manifest_sha256": digest,
                 "error_type": type(exc).__name__, "error": str(exc), "no_retry_or_replacement": True}
        if directory.exists() and not (directory / "seal.json").exists():
            (directory / "arm_outcome.json").write_text(json.dumps(error, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_new(destination / "STOP.json", error)
        raise
    finally:
        try:
            # Status summaries can be refreshed; sealed arm evidence is never changed.
            (destination / "summary.json").write_text(json.dumps(stage_summary(destination, manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            lock.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=SETTINGS["arm_order"])
    args = parser.parse_args(argv)
    print(json.dumps(run(ROOT, MANIFEST_NAME, args.arm), ensure_ascii=False))


if __name__ == "__main__":
    main()
