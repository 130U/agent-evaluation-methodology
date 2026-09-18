"""Explicit N1-R1 registration; importing is read-only and makes no model call.

Run only after an independently cleared HTTP readiness file exists. This new
registration never resumes G1 or relaxes the original 24-unit N1 registration.
"""
import argparse
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
from tau_feedback.n1_audit import (INTERRUPTED_STAGE, INTERRUPTED_TOTALS, INTERRUPTED_PHASE_LIMITS,
    interrupted_cohort, verify_http_readiness, sha, within, _write_new)
from tau_feedback.subscription_evidence import load

BASELINE = "experiments/G1_SUBSCRIPTION_MANIFEST_V2.json"
STOP_REVIEW = "results/g1/g1-retail-dev-subscription-v2/G1_STOP_INTEGRITY_REVIEW.json"
SOURCE_SNAPSHOT = "experiments/snapshots/g1-retail-dev-subscription-v2-source.zip"
MANIFEST_ID = "n1-r1-retail-subscription-http-v1"


def build_manifest(root, http_readiness_path):
    """Read/verify inputs, derive the entire eligible cohort, return new data.

    No labels are accepted and there is no unit/subset CLI argument. Calling
    this helper intentionally inspects all original STOP-bound completed data.
    """
    root = Path(root).resolve()
    readiness = within(root, http_readiness_path)
    readiness_names = verify_http_readiness(root, readiness)
    cohort = interrupted_cohort(root, baseline_manifest_path=BASELINE,
        stop_review_path=STOP_REVIEW, baseline_source_snapshot_path=SOURCE_SNAPSHOT)
    names = set(cohort["source_paths"]) | set(readiness_names)
    names.update({"requirements.lock", "experiments/runtime_sources.json", "experiments/gepa_sources.json",
        "experiments/cli_model_catalog_sol.json", "experiments/cli_model_catalog_sol.provenance.json",
        "scripts/register_subscription_n1r1.py", "scripts/run_subscription_n1.py", "scripts/freeze_n1_selection.py",
        "scripts/build_n1_review_packets.py", "research/N1_METHOD_REVIEW.md", "research/N1_PACKET_LABEL_REVIEW.md",
        "research/G1_TRANSPORT_RECOVERY_OPTIONS.md", "research/N1_R1_METHOD_REVIEW.md"})
    names.update(p.relative_to(root).as_posix() for p in (root / "src/tau_feedback").glob("*.py"))
    names.update(p.relative_to(root).as_posix() for p in (root / "tests").glob("test_n1*.py"))
    for source_name, prefix in (("runtime_sources.json", "vendor/tau2/"), ("gepa_sources.json", "vendor/gepa/")):
        source = load(root / "experiments" / source_name)
        if not source.get("files"):
            raise ValueError("Pinned upstream manifest must contain files")
        for item in source["files"]:
            name = prefix + item["path"]
            data = within(root, name).read_bytes()
            if source_name == "runtime_sources.json":
                # GitHub tree manifests identify Git blobs, not bare-file SHA1.
                actual = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
                expected = item["sha"]
            else:
                actual, expected = hashlib.sha256(data).hexdigest(), item["sha256"]
            if actual != expected:
                raise ValueError("Current pinned upstream source differs: " + name)
            names.add(name)
    # Bind the current official task interpretation as well as its old request.
    from tau2.domains.retail.environment import get_tasks
    from tau_feedback.contracts import canonical_hash
    tasks = {t.id: t for t in get_tasks("train")}
    for unit in cohort["units"]:
        if canonical_hash(tasks[unit["task_id"]].model_dump(mode="json")) != unit["task_sha256"]:
            raise ValueError("Current official task differs from the original completed episode")
    manifest = {"stage": INTERRUPTED_STAGE, "manifest_id": MANIFEST_ID,
        "registered_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "execution_authorized": True,
        "scope": "Descriptive native-diagnosis audit of every completed trajectory in interrupted G1 v2; no natural optimization benefit comparison",
        "baseline_manifest_path": BASELINE, "baseline_unit_count": 24, "parent_population_count": 24,
        "eligible_review_count": 18, "stop_review_path": STOP_REVIEW, "stop_review_sha256": sha(root / STOP_REVIEW),
        "baseline_source_snapshot_path": SOURCE_SNAPSHOT, "baseline_source_snapshot_sha256": sha(root / SOURCE_SNAPSHOT),
        "http_readiness_path": readiness.relative_to(root).as_posix(), "http_readiness_sha256": sha(readiness),
        **{k: deepcopy(cohort[k]) for k in ("units", "parent_population", "parent_execution_counts", "parent_cost_known_subtotals")},
        "cohort_rule": "All and only original-order execution=evaluated trajectories with complete bound evidence; no score/diagnosis-dependent exclusion",
        "excluded_units": "One infrastructure-interrupted ungraded trajectory and five not run; none executed or silently replaced under this registration",
        "statistical_unit": "Diagnosis nested within trajectory and task; 18 trajectories from 12 tasks, six repeated twice and six once; no independence assumption",
        "selection_limit": "Availability, original fixed order and stopping time select completers; descriptive within-cohort counts cannot estimate the original 24 or natural benchmark frequency",
        "model": "gpt-5.6-sol", "effort": "low", "model_seed_controlled": False,
        "tau2_commit": load(root / "experiments/runtime_sources.json")["commit"],
        "gepa_commit": load(root / "experiments/gepa_sources.json")["commit"],
        "transport": "codex_cli_responses_http_only",
        "transport_note": "HTTP-only is a new postprocessing configuration; original G1 v2 stays stopped. Readiness establishes supported configuration and observed checks, not TLS root cause or network capture. Cloud weights are not pinned; no OS filesystem isolation claim.",
        "per_trajectory_diagnosis_cap": 2, "diagnosis_sampling_salt": "n1-r1-retail-native-pool-20260917-v1",
        "independent_review_sampling_salt": "n1-r1-retail-independent-cards-20260917-v1",
        "per_phase_client_limits": deepcopy(INTERRUPTED_PHASE_LIMITS), "aggregate_thresholds": deepcopy(INTERRUPTED_TOTALS),
        "budget_notes": "Independent fresh review and gate ledgers, caps 1 and 2 per unit. Aggregate thresholds are sums, not hard generation caps; one-call overshoot can occur. Original G1, prior probes and research-agent annotations remain separate costs; unknown usage never zero.",
        "retries": 0, "ordering": "all 18 reviews, sealed selection, anonymous packets, locked first labels/adjudication, then gates",
        "annotation": {"max_cards": 12, "strata": ["user_nonpass_or_qualification_unknown", "qualified_pass", "explicit_fail"],
            "per_stratum_first_pass": 4, "unused_slots": "fixed global hash fill from maximum-two-agent pools only",
            "labels": ["supported", "unsupported", "insufficient"],
            "dimensions": ["claim_support", "agent_responsibility", "correction_observability"],
            "identity_limit": "Anonymous packets hide sampling strata and gate decisions; research-agent reviewers can have prior G1 familiarity and shared model-family bias, so not fully blind human gold"},
        "denominator_rules": "Unknown parser result has null diagnosis counts, not empty; retain valid-empty, user-source, not-sampled, structural-abstain and gate status denominators. Always show 18 audit trajectories alongside original 24 dispositions.",
        "stop_rules": ["transport/event/usage/cleanup failure", "changed or incomplete bound source/labels", "any interrupted audit attempt stops without retry or replacement"],
        "source_and_input_sha256": {name: sha(within(root, name)) for name in sorted(names)}}
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-readiness", required=True, help="Independently reviewed HTTP clearance JSON within the project")
    args = parser.parse_args(argv)
    target = ROOT / "experiments/N1_R1_MANIFEST.json"
    archive = ROOT / "experiments/snapshots" / (MANIFEST_ID + "-source.zip")
    paths = (target, target.with_suffix(".sha256"), archive, archive.with_suffix(".zip.sha256"))
    if any(p.exists() for p in paths):
        raise RuntimeError("Never overwrite or resume a partial N1-R1 registration")
    manifest = build_manifest(ROOT, args.http_readiness)
    target.parent.mkdir(exist_ok=True)
    archive.parent.mkdir(exist_ok=True)
    _write_new(target, manifest)
    with target.with_suffix(".sha256").open("x", encoding="utf-8") as stream:
        stream.write(sha(target) + "  " + target.name + "\n")
    names = set(manifest["source_and_input_sha256"]) | {p.relative_to(ROOT).as_posix() for p in paths[:2]}
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(names):
            bundle.write(within(ROOT, name), arcname=name)
    with archive.with_suffix(".zip.sha256").open("x", encoding="utf-8") as stream:
        stream.write(sha(archive) + "  " + archive.name + "\n")
    print(json.dumps({"registered": MANIFEST_ID, "sha256": sha(target), "snapshot_sha256": sha(archive),
                      "audit_trajectories": 18, "parent_population": 24, "model_calls": 0}))


if __name__ == "__main__":
    main()
