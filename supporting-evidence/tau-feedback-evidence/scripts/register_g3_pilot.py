"""Register G3 once, before any model call; never modify older manifests."""
from copy import deepcopy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from register_subscription_g2 import (build_manifest as g2_sources, load, sha, within, write_new, S0)
from tau_feedback.contracts import canonical_hash

ID = "g3-retail-proposal-holdout-v1"
MANIFEST = "experiments/G3_PILOT_MANIFEST.json"
PROTOCOL = "research/G3_PILOT_PROTOCOL.md"
SPLIT_REVIEW = "research/NEXT_STUDY_SPLIT_REVIEW.md"
SNAPSHOT = "experiments/snapshots/" + ID + "-source.zip"
SETTINGS = {
    "manifest_id": ID, "stage": "G3_exploratory_proposal_holdout_pilot",
    "model": "gpt-5.6-sol", "effort": "low", "transport": "HttpCliTextClient",
    "seed_strategy": S0, "arm_order": ["B2", "B1"],
    "splits": {"train": ["76", "0"], "validation": ["16", "19"],
               "test": ["36", "40", "62", "68", "77", "79"]},
    "optimizer": {"max_episodes": 8, "diagnosis_cap": 2,
        "sampling_salt": "g3-retail-proposal-holdout-v1", "simulation_seed": 101,
        "optimizer_seed": 0, "max_strategy_chars": 6000},
    "gepa_behavior": {"skip_perfect_score": False, "proposal_limit": 1,
        "acceptance_criterion": "strict_improvement", "cache_evaluation": False,
        "use_merge": False, "write_agent_state": False},
    "episode_limits": {"max_steps": 40, "max_errors": 6, "simulation_seconds": 900},
    "client_limits_per_arm": {"max_calls": 220, "max_input_tokens": 3000000,
        "max_output_tokens": 30000, "max_seconds": 3600, "request_timeout": 120},
    "test_client_limits": {"max_calls": 600, "max_input_tokens": 8000000,
        "max_output_tokens": 60000, "max_seconds": 10800, "request_timeout": 120},
    "test_repetitions": [103, 107], "planned_test_units": 36,
    "retries": 0, "model_seed_controlled": False,
    "selection_rule": "strict qualified validation gain, no observed regression or critical violation, cost ratio <=1.25",
}


def test_schedule():
    rows = []
    arms = ["B0", "B1", "B2"]
    for rep, seed in enumerate(SETTINGS["test_repetitions"]):
        ids = SETTINGS["splits"]["test"][::1 if rep == 0 else -1]
        for index, tid in enumerate(ids):
            offset = (index + rep) % 3
            for arm in arms[offset:] + arms[:offset]:
                rows.append({"unit": len(rows) + 1, "arm": arm, "task_id": tid, "simulation_seed": seed})
    return rows


def tasks():
    from tau2.domains.retail.environment import get_tasks
    return {t.id: t.model_dump(mode="json") for t in get_tasks("base")}


def build():
    prior = g2_sources(ROOT)
    hashes = dict(prior["source_hashes"])
    names = [PROTOCOL, SPLIT_REVIEW, "scripts/register_g3_pilot.py", "scripts/run_g3_pilot.py",
             "scripts/finalize_g3_strategies.py", "scripts/run_g3_test.py",
             "tests/test_g3_optimization.py", "tests/test_gepa_milestones_g3.py",
             "tests/test_g3_acceptance.py", "tests/test_g3_stage_guards.py"]
    for name in names:
        hashes[name] = sha(within(ROOT, name))
    all_tasks = tasks()
    groups = {tid: g["group_id"] for g in load(ROOT / "STRUCTURAL_GROUPS.json") for tid in g["task_ids"]}
    rows, seen_groups = {}, {}
    for split, ids in SETTINGS["splits"].items():
        rows[split] = []
        for tid in ids:
            if groups[tid] in seen_groups and seen_groups[groups[tid]] != split:
                raise ValueError("Structural group crosses a split")
            seen_groups[groups[tid]] = split
            payload = all_tasks[tid]
            rows[split].append({"task_id": tid, "structural_group": groups[tid],
                "payload": payload, "payload_sha256": canonical_hash(payload)})
    return {**deepcopy(SETTINGS), "registered_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tau2_commit": prior["tau2_commit"], "gepa_commit": prior["gepa_commit"],
        "cli_exe_sha256": prior["cli_exe_sha256"], "client_catalog_sha256": prior["client_catalog_sha256"],
        "scope": "AI-curated 2/2/6 pilot; public tasks, one search seed/proposal; no production or SOTA claim",
        "protocol_path": PROTOCOL, "split_review_path": SPLIT_REVIEW,
        "snapshot_path": SNAPSHOT, "source_hashes": hashes, "frozen_tasks": rows,
        "test_schedule": test_schedule(), "test_information_flow": "Task payloads host-only; no test review in optimization",
        "cost_unit": "CLI token usage and wall time; subscription dollars and research-agent cost unavailable"}


def verify(root=ROOT):
    root = Path(root)
    path = root / MANIFEST
    digest = sha(path)
    if digest != path.with_suffix(".sha256").read_text().split()[0]:
        raise ValueError("G3 manifest hash differs")
    manifest = load(path)
    if any(manifest.get(k) != v for k, v in SETTINGS.items()) or manifest["test_schedule"] != test_schedule():
        raise ValueError("G3 registered settings or test order differ")
    for name, expected in manifest["source_hashes"].items():
        if sha(within(root, name)) != expected:
            raise ValueError("Frozen G3 source changed: " + name)
    archive = root / SNAPSHOT
    if sha(archive) != archive.with_suffix(".zip.sha256").read_text().split()[0]:
        raise ValueError("G3 snapshot digest differs")
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.namelist()) != len(set(bundle.namelist())):
            raise ValueError("Duplicate snapshot member")
        for name, expected in manifest["source_hashes"].items():
            if hashlib.sha256(bundle.read(name)).hexdigest() != expected:
                raise ValueError("G3 snapshot member changed: " + name)
        if bundle.read(MANIFEST) != path.read_bytes():
            raise ValueError("G3 snapshot embeds another registration")
    actual = tasks()
    for split, rows in manifest["frozen_tasks"].items():
        if [r["task_id"] for r in rows] != SETTINGS["splits"][split]:
            raise ValueError("Frozen task split differs")
        for row in rows:
            if row["payload"] != actual[row["task_id"]] or canonical_hash(row["payload"]) != row["payload_sha256"]:
                raise ValueError("Frozen task payload differs")
    return manifest, digest


def main():
    path, archive = ROOT / MANIFEST, ROOT / SNAPSHOT
    if any(p.exists() for p in (path, archive, path.with_suffix(".sha256"), archive.with_suffix(".zip.sha256"))):
        raise RuntimeError("Never overwrite G3 registration")
    manifest = build()
    write_new(path, manifest)
    path.with_suffix(".sha256").write_text(sha(path) + "\n", encoding="utf-8")
    names = set(manifest["source_hashes"]) | {MANIFEST, str(Path(MANIFEST).with_suffix(".sha256")).replace("\\", "/")}
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(names):
            bundle.write(within(ROOT, name), arcname=name)
    archive.with_suffix(".zip.sha256").write_text(sha(archive) + "\n", encoding="utf-8")
    verify()
    print(json.dumps({"manifest": MANIFEST, "sha256": sha(path), "snapshot_sha256": sha(archive), "model_calls": 0}))


if __name__ == "__main__":
    main()
