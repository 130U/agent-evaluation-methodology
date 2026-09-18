"""Write-once G4 registration, all sources frozen before study model calls."""
from copy import deepcopy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from register_subscription_g2 import build_manifest as previous_sources, load, sha, within, write_new, S0
from tau_feedback.contracts import canonical_hash
from tau_feedback.recovered_events import POLICY_ID

ID = "g4-retail-shared-feedback-v1"
MANIFEST = "experiments/G4_MANIFEST.json"
SNAPSHOT = "experiments/snapshots/" + ID + "-source.zip"
SETTINGS = {
    "manifest_id": ID, "stage": "paired_natural_candidate_quality", "model": "gpt-5.6-sol", "effort": "low",
    "transport": "RecoveringHttpCliTextClient", "event_policy": POLICY_ID, "seed_strategy": S0,
    "splits": {"dev": ["0", "34", "59", "76"], "validation": ["35", "56", "89", "98"],
               "test": ["66", "69", "74", "100", "103", "106"]},
    "parent_seeds": [211, 223, 227, 229], "validation_seeds": [233, 239], "test_seeds": [241, 251],
    "arm_order_by_batch": [["B1", "B2"], ["B2", "B1"], ["B1", "B2"], ["B2", "B1"]],
    "diagnosis_cap": 2, "sampling_salt": ID, "optimizer_seed": 0, "max_strategy_chars": 6000,
    "episode_limits": {"max_steps": 40, "max_errors": 6, "simulation_seconds": 900},
    "parent_client_limits_per_batch": {"max_calls": 180, "max_input_tokens": 3000000,
        "max_output_tokens": 20000, "max_seconds": 3600, "request_timeout": 120},
    "baseline_client_limits": {"max_calls": 220, "max_input_tokens": 3500000,
        "max_output_tokens": 30000, "max_seconds": 5400, "request_timeout": 120},
    "candidate_client_limits_per_arm_batch": {"max_calls": 240, "max_input_tokens": 4000000,
        "max_output_tokens": 35000, "max_seconds": 5400, "request_timeout": 120},
    "test_client_limits": {"max_calls": 800, "max_input_tokens": 12000000,
        "max_output_tokens": 80000, "max_seconds": 14400, "request_timeout": 120},
    "gepa": {"skip_perfect_score": False, "acceptance_criterion": "strict_improvement",
        "max_proposals_per_arm_batch": 1, "cache_evaluation": False, "use_merge": False,
        "write_agent_state": False, "max_logical_metric_calls_per_proposal": 16},
    "planned_actual_optimization_episodes": 120, "max_test_episodes": 36,
    "model_seed_controlled": False, "runner_retries": 0,
    "failure_rule": "Any infrastructure, budget, unknown score, malformed candidate or native critical-user failure stops the study; no replacement or retry",
    "adoption": {"qualified_validation_strict_gain": True, "no_regression_on_baseline_qualified_units": True,
        "candidate_critical_violations": 0, "max_validation_cli_token_ratio": 1.25,
        "requires_gepa_selected_new": True, "tie_order": ["qualified_count_desc", "validation_tokens_asc", "batch_asc"]},
}


def task_payloads():
    from tau2.domains.retail.environment import get_tasks
    return {t.id: t.model_dump(mode="json") for t in get_tasks("base")}


def validate_split(candidate, tasks):
    if candidate["splits"] != SETTINGS["splits"]:
        raise ValueError("Reviewed split differs from registered IDs")
    selected = candidate["selected_tasks"]
    families = []
    exposed = set(candidate["online_exposed_task_ids"])
    for split, ids in SETTINGS["splits"].items():
        if [r["task_id"] for r in selected[split]] != ids:
            raise ValueError("Reviewed task order differs")
        for row in selected[split]:
            if canonical_hash(tasks[row["task_id"]]) != row["task_payload_sha256"]:
                raise ValueError("Reviewed task payload differs")
            families.append(row["family_id"])
            if split != "dev" and exposed.intersection(row["family_members"]):
                raise ValueError("Online exposure intersects holdout closure")
            for tid, expected in row["family_member_payload_sha256"].items():
                if canonical_hash(tasks[tid]) != expected:
                    raise ValueError("Family member payload changed")
    if len(set(families)) != len(families):
        raise ValueError("Family overlap among selected representatives")


def build():
    earlier = previous_sources(ROOT)
    names = set(earlier["source_hashes"])
    names.update({"research/G4_PROTOCOL.md", "research/G4_SPLIT_REVIEW.md",
        "experiments/G4_SPLIT_CANDIDATE.json", "research/G4_RECOVERY_CLIENT_REVIEW.md",
        "research/G4_PAIRED_IMPLEMENTATION_REVIEW.md", "research/G4_LAUNCH_REVIEW.md", "scripts/register_g4_paired.py",
        "scripts/run_g4_paired.py", "scripts/run_g4_test.py", "scripts/probe_recovered_transport.py"})
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_g4*.py"))
    names.update({"tests/test_recovered_events.py", "tests/test_subscription_recovery_client.py"})
    names.update({"results/G4_PRE_REGISTRATION_TESTS.log", "results/G4_PAIRED_REDTEAM_INITIAL.log",
                  "results/G4_STAGE_REDTEAM_INITIAL.log"})
    probe = ROOT / "results/subscription_pilot/recovery_transport_v1"
    if load(probe / "SUMMARY.json").get("status") != "complete":
        raise ValueError("Fresh transport probe is incomplete")
    names.update(p.relative_to(ROOT).as_posix() for p in probe.rglob("*") if p.is_file())
    candidate, tasks = load(ROOT / "experiments/G4_SPLIT_CANDIDATE.json"), task_payloads()
    validate_split(candidate, tasks)
    for name, digest in candidate["source_hashes"].items():
        if sha(within(ROOT, name)) != digest:
            raise ValueError("Split source changed: " + name)
        names.add(name)
    for exposure in candidate["online_exposure"]:
        for item in exposure["evidence"]:
            if sha(within(ROOT, item["path"])) != item["sha256"]:
                raise ValueError("Prior exposure request changed")
            names.add(item["path"])
    rows = {split: [{"task_id": tid, "payload": tasks[tid], "payload_sha256": canonical_hash(tasks[tid])}
                    for tid in ids] for split, ids in SETTINGS["splits"].items()}
    return {**deepcopy(SETTINGS), "registered_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tau2_commit": earlier["tau2_commit"], "gepa_commit": earlier["gepa_commit"],
        "cli_exe_sha256": earlier["cli_exe_sha256"], "client_catalog_sha256": earlier["client_catalog_sha256"],
        "protocol_path": "research/G4_PROTOCOL.md", "snapshot_path": SNAPSHOT, "frozen_tasks": rows,
        "opportunity_task_ids": candidate["opportunity_task_ids"],
        "scope": "Four paired batches on four public development tasks, four validation and six holdout families; no population or production certification",
        "source_hashes": {name: sha(within(ROOT, name)) for name in sorted(names)}}


def verify():
    path = ROOT / MANIFEST
    digest = sha(path)
    if digest != path.with_suffix(".sha256").read_text().split()[0]:
        raise ValueError("G4 manifest hash differs")
    value = load(path)
    if any(value.get(k) != v for k, v in SETTINGS.items()):
        raise ValueError("G4 registered settings differ")
    for name, expected in value["source_hashes"].items():
        if sha(within(ROOT, name)) != expected:
            raise ValueError("Frozen G4 source changed: " + name)
    archive = ROOT / SNAPSHOT
    if sha(archive) != archive.with_suffix(".zip.sha256").read_text().split()[0]:
        raise ValueError("G4 source archive changed")
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.namelist()) != len(set(bundle.namelist())):
            raise ValueError("Duplicate source archive entries")
        for name, expected in value["source_hashes"].items():
            if hashlib.sha256(bundle.read(name)).hexdigest() != expected:
                raise ValueError("Frozen G4 archive member differs")
        if bundle.read(MANIFEST) != path.read_bytes():
            raise ValueError("Snapshot embeds another manifest")
    tasks = task_payloads()
    for rows in value["frozen_tasks"].values():
        for row in rows:
            if row["payload"] != tasks[row["task_id"]] or canonical_hash(row["payload"]) != row["payload_sha256"]:
                raise ValueError("Frozen task differs from installed task")
    return value, digest


def main():
    path, archive = ROOT / MANIFEST, ROOT / SNAPSHOT
    if any(p.exists() for p in (path, path.with_suffix(".sha256"), archive, archive.with_suffix(".zip.sha256"))):
        raise ValueError("G4 registration is write-once")
    write_new(path, build())
    path.with_suffix(".sha256").write_text(sha(path) + "\n", encoding="utf-8")
    names = set(load(path)["source_hashes"]) | {MANIFEST, Path(MANIFEST).with_suffix(".sha256").as_posix()}
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(names):
            bundle.write(within(ROOT, name), arcname=name)
    archive.with_suffix(".zip.sha256").write_text(sha(archive) + "\n", encoding="utf-8")
    value, digest = verify()
    print(json.dumps({"manifest": MANIFEST, "sha256": digest, "sources": len(value["source_hashes"]), "model_calls": 0}))


if __name__ == "__main__":
    main()
