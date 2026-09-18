"""Prepare a separately frozen, purposively selected two-task GEPA case.

Importing performs no registration or model call. Only explicit main() writes.
This does not reopen G1 or authorize a population/causal improvement claim.
"""
from __future__ import annotations
from copy import deepcopy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

MANIFEST_ID = "g2-retail-subscription-http-case-v1"
MANIFEST_NAME = "experiments/G2_SUBSCRIPTION_MANIFEST.json"
PROTOCOL = "research/G2_EXPLORATORY_CASE_PROTOCOL.md"
SNAPSHOT = "experiments/snapshots/" + MANIFEST_ID + "-source.zip"
S0 = "Follow the retail policy. Help the authenticated customer complete their request using the available tools, and accurately communicate the outcome."
SETTINGS = {
    "stage": "G2_exploratory_development_gepa_case", "manifest_id": MANIFEST_ID,
    "execution_authorized": True, "arm_order": ["B1", "B2"], "model": "gpt-5.6-sol", "effort": "low",
    "transport": "HttpCliTextClient", "seed_strategy": S0,
    "task_selection": {"train": {"task_id": "85", "structural_group": "structural_43"},
                       "dev_validation": {"task_id": "75", "structural_group": "structural_35"}},
    "optimizer": {"max_episodes": 4, "diagnosis_cap": 2, "sampling_salt": "g2-retail-case-20260917-v1",
        "simulation_seed": 43, "optimizer_seed": 0, "max_strategy_chars": 6000},
    "episode_limits": {"max_steps": 40, "max_errors": 6, "simulation_seconds": 900},
    "client_limits_per_arm": {"max_calls": 160, "max_input_tokens": 2000000, "max_output_tokens": 20000,
                              "max_seconds": 2400, "request_timeout": 120},
    "aggregate_thresholds": {"cli_invocations": 320, "input_tokens": 4000000,
                             "output_tokens": 40000, "client_seconds_sum": 4800, "episodes": 8},
    "gepa_behavior": {"proposal_opportunities_per_arm": 1, "skip_perfect_score": True,
        "acceptance_criterion": "strict_improvement", "cache_evaluation": False, "use_merge": False,
        "write_agent_state": False, "raise_on_exception": True, "reflection_attempts_at_most": 1},
    "retries": 0, "reuse_g1_episodes": False, "model_seed_controlled": False,
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def within(root, name):
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("G2 path escaped project")
    return path


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def official_tasks():
    from tau2.domains.retail.environment import get_tasks
    return get_tasks("train")


def build_manifest(root, *, task_loader=official_tasks):
    root = Path(root).resolve()
    from tau_feedback.contracts import canonical_hash
    from tau_feedback.subscription_client import EXE_SHA256, MODEL_CATALOG_SHA256
    from tau_feedback.n1_audit import verify_http_readiness
    readiness = "results/subscription_pilot/HTTP_READINESS_REVIEW.json"
    names = set(verify_http_readiness(root, readiness))
    names.update({"requirements.lock", "pyproject.toml", "STRUCTURAL_GROUPS.json", PROTOCOL,
        "scripts/register_subscription_g2.py", "scripts/run_subscription_g2.py", "tests/test_subscription_g2.py",
        "experiments/runtime_sources.json", "experiments/gepa_sources.json",
        "experiments/cli_model_catalog_sol.json", "experiments/cli_model_catalog_sol.provenance.json"})
    names.update(p.relative_to(root).as_posix() for p in (root / "src/tau_feedback").glob("*.py"))
    commits = {}
    for plan, prefix in (("runtime_sources.json", "vendor/tau2/"), ("gepa_sources.json", "vendor/gepa/")):
        source = load(root / "experiments" / plan)
        commits[plan] = source["commit"]
        if not source.get("files"):
            raise ValueError("Empty upstream source lock")
        for item in source["files"]:
            path = within(root, prefix + item["path"])
            data = path.read_bytes()
            if plan == "runtime_sources.json":
                actual = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
                expected = item["sha"]
            else:
                actual, expected = hashlib.sha256(data).hexdigest(), item["sha256"]
            if actual != expected:
                raise ValueError("Pinned upstream source differs: " + prefix + item["path"])
            names.add(prefix + item["path"])
    if commits != {"runtime_sources.json": "2174a603f6d014ef94473ffa95957f6ce27100db",
                   "gepa_sources.json": "15ee314f9c7d34ec153b809d401f42f55c4dcd76"}:
        raise ValueError("G2 requires the registered tau2/GEPA commits")
    if sha(root / "experiments/cli_model_catalog_sol.json") != MODEL_CATALOG_SHA256:
        raise ValueError("Original CLI metadata snapshot changed")
    tasks = {task.id: task for task in task_loader()}
    groups = {tid: group["group_id"] for group in load(root / "STRUCTURAL_GROUPS.json") for tid in group["task_ids"]}
    frozen = {}
    for role, selection in SETTINGS["task_selection"].items():
        tid = selection["task_id"]
        if groups[tid] != selection["structural_group"]:
            raise ValueError("Frozen development task group differs")
        payload = tasks[tid].model_dump(mode="json")
        frozen[role] = {"task_id": tid, "structural_group": groups[tid],
                        "payload": payload, "payload_sha256": canonical_hash(payload)}
    return {**deepcopy(SETTINGS), "registered_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": "Two purposively selected known development tasks; illustrative GEPA execution only, not revived G1 comparison",
        "selection_disclosure": "Selected after G1 outcomes were known; both tasks previously used in development; no blind/held-out claim",
        "inference_limit": "Separate stochastic baselines and diagnosis pools in B1 and B2; no paired causal effect, mean uplift or generalization estimate",
        "b2_interpretation": "Describe filtering only within B2's own original diagnosis pool; do not treat B1 feedback as matched input",
        "cost_limit": "All actor/user/NL/reviewer/gate/reflection calls share each arm's client. Common caps are not equal realized cost; token overshoot is possible; unknown cost is never free.",
        "failure_rule": "Any arm exception stops the whole stage; no retry, replacement, forced proposal or skip-perfect override",
        "frozen_tasks": frozen, "tau2_commit": commits["runtime_sources.json"], "gepa_commit": commits["gepa_sources.json"],
        "cli_exe_sha256": EXE_SHA256, "client_catalog_sha256": MODEL_CATALOG_SHA256,
        "http_readiness_path": readiness, "http_readiness_use": "Prior transport/component clearance only, not authorization or performance evidence for G2",
        "protocol_path": PROTOCOL, "snapshot_path": SNAPSHOT,
        "source_hashes": {name: sha(within(root, name)) for name in sorted(names)}}


def main():
    target, archive = ROOT / MANIFEST_NAME, ROOT / SNAPSHOT
    paths = (target, target.with_suffix(".sha256"), archive, archive.with_suffix(".zip.sha256"))
    if any(path.exists() for path in paths):
        raise RuntimeError("Never replace or resume a partial G2 registration")
    manifest = build_manifest(ROOT)
    target.parent.mkdir(exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    write_new(target, manifest)
    with paths[1].open("x", encoding="utf-8") as stream:
        stream.write(sha(target) + "  " + target.name + "\n")
    names = set(manifest["source_hashes"]) | {p.relative_to(ROOT).as_posix() for p in paths[:2]}
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(names):
            bundle.write(within(ROOT, name), arcname=name)
    with paths[3].open("x", encoding="utf-8") as stream:
        stream.write(sha(archive) + "  " + archive.name + "\n")
    print(json.dumps({"manifest_id": MANIFEST_ID, "sha256": sha(target), "snapshot_sha256": sha(archive), "model_calls": 0}))


if __name__ == "__main__":
    main()
