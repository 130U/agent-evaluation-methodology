"""Export auditable synthetic dialogue views, excluding host/transport metadata.

Post-registration derivative: public task messages and tool results plus the
two AI judgments. Not raw CLI records or an independent authenticity proof.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from register_g3_pilot import ID, verify, load, sha, within, write_new
from run_g3_pilot import verify_arm
from report_g3_test import verify_selection, bind_test
from finalize_g3_strategies import read_review
from export_public_package import public_text

LABEL_FIELDS = ("semantic_status", "user_validity", "critical_violation", "critical_user_error", "evidence")


def project_trajectory(messages):
    """Allowlist chronological public dialogue and preserve tool-call linkage."""
    if not isinstance(messages, list):
        raise ValueError("Trajectory must be a message list")
    output, calls, answered, omitted = [], {}, set(), Counter()
    previous = -1
    for message in messages:
        role, turn, content = message.get("role"), message.get("turn_idx"), message.get("content")
        if (role not in {"assistant", "user", "tool"} or type(turn) is not int or turn <= previous
                or content is not None and not isinstance(content, str)):
            raise ValueError("Unexpected public text trajectory schema")
        if message.get("is_audio") is True:
            raise ValueError("This derivative covers text retail only")
        previous = turn
        row = {"turn_idx": turn, "role": role, "content": content}
        kept = {"turn_idx", "role", "content"}
        if role == "assistant":
            values = message.get("tool_calls") or []
            if not isinstance(values, list):
                raise ValueError("Tool calls must be a list")
            row["tool_calls"] = []
            kept.add("tool_calls")
            for call in values:
                call_id = call.get("id")
                if (not isinstance(call_id, str) or not call_id or call_id in calls
                        or not isinstance(call.get("name"), str) or not call["name"]
                        or not isinstance(call.get("arguments"), dict)):
                    raise ValueError("Missing, duplicated or malformed tool call")
                public_id = f"tool-call-{len(calls) + 1}"
                calls[call_id] = public_id
                row["tool_calls"].append({"id": public_id, "name": call["name"], "arguments": deepcopy(call["arguments"])})
                omitted.update("tool_call." + key for key in call if key not in {"id", "name", "arguments"})
        elif role == "tool":
            call_id = message.get("id")
            if call_id not in calls or call_id in answered or type(message.get("error")) is not bool:
                raise ValueError("Tool response is missing, duplicated or linked to no prior call")
            answered.add(call_id)
            row.update(id=calls[call_id], error=message["error"])
            kept.update(("id", "error"))
        elif message.get("tool_calls"):
            raise ValueError("Retail simulated user unexpectedly invoked tools")
        omitted.update(key for key in message if key not in kept)
        output.append(row)
    if set(calls) != answered:
        raise ValueError("Completed dialogue contains an unanswered tool call")
    return {"messages": output, "tool_calls": len(calls), "omitted_metadata_field_counts": dict(omitted),
            "transformations": ["Message metadata fields explicitly omitted", "Tool call IDs replaced with stable local ordinal IDs; links preserved", "Message content and tool arguments retained from public synthetic retail dialogue"]}


def run():
    manifest, digest = verify()
    stage = ROOT / "results/g3" / ID
    for arm in manifest["arm_order"]:
        verify_arm(stage / arm, arm, digest)
    policies, policy_digest = verify_selection(stage, digest)
    summary, test_dirs, evidence = bind_test(ROOT, stage, manifest, digest, policies, policy_digest)
    public_path = ROOT / "results/G3_PUBLIC_SUMMARY.json"
    public = load(public_path)
    if sha(public_path) != public_path.with_suffix(".sha256").read_text().split()[0]:
        raise ValueError("Public summary changed")
    private_path = ROOT / "results/G3_TEST_REPORT.json"
    report = load(private_path)
    if (sha(private_path) != public["original_report_sha256"] or public["original_manifest_sha256"] != digest
            or report["final_strategies_sha256"] != policy_digest or report["episode_evidence_sha256"] != evidence):
        raise ValueError("Public/private report evidence binding differs")
    optimization_dirs = {p.relative_to(ROOT).as_posix(): p for arm in manifest["arm_order"]
                         for p in (stage / arm / "optimizer/episodes").glob("episode-*/environment")}
    phases = (("optimization", optimization_dirs, policies["reviews"]), ("test", test_dirs, report["test_reviews"]))
    units = {row["relative_directory"]: row for row in summary["units"]}
    rows = []
    for phase, directories, references in phases:
        reviewed = []
        if len(references) != 2:
            raise ValueError("Two identified AI reviews required")
        for reference in references:
            if sha(reference["path"]) != reference["sha256"]:
                raise ValueError("Review changed since it was sealed")
            reviewed.append(read_review(reference["path"], digest, directories))
        if reviewed[0][0] == reviewed[1][0]:
            raise ValueError("Duplicate reviewer identity")
        for relative, directory in sorted(directories.items()):
            request, outcome = load(directory / "request.json"), load(directory / "outcome.json")
            if phase == "test":
                unit = units[relative]
                identity = {"phase": phase, "unit": unit["unit"], "arm": unit["arm"]}
            else:
                identity = {"phase": phase, "arm": directory.relative_to(stage).parts[0],
                            "episode": directory.parent.name}
            row = {**identity, "task_id": request["task_id"], "simulation_seed": request["simulation_seed"],
                "task_payload_sha256": request["task_sha256"],
                "strategy_sha256": hashlib.sha256(request["strategy"].encode()).hexdigest(),
                "native_reward": outcome["native_reward"], "structural_outcome": outcome["structural_outcome"],
                "original_artifact_sha256": {name: sha(directory / name) for name in
                    ("request.json", "outcome.json", "trajectory.json", "simulation.json", "calls.jsonl")},
                "dialogue": project_trajectory(load(directory / "trajectory.json")),
                "independent_ai_reviews": [{"reviewer": name, **{key: deepcopy(judgments[relative][key]) for key in LABEL_FIELDS}}
                                           for name, judgments in reviewed]}
            rows.append(row)
    value = {"schema_version": "g3-public-trajectory-view-v1", "manifest_sha256": digest,
        "final_strategies_sha256": policy_digest, "public_summary_sha256": sha(public_path),
        "experiment_status": summary["status"], "optimization_episodes": len(optimization_dirs),
        "evaluated_test_units": len(test_dirs), "test_assignment_denominator": 36,
        "exporter_sha256": sha(Path(__file__)), "episodes": rows,
        "source_and_privacy": "Dialogue and tool data are generated interactions with the pinned public synthetic tau2 retail benchmark, not real customer records. Host paths, raw_data, timestamps, usage payloads, CLI event streams and authentication are omitted.",
        "interpretation_boundary": "This derived view supports reading semantic evidence against the public policy. It does not recreate model sampling, independently authenticate omitted transport logs, or make AI judgments human gold. Initial assistant role alone is not proof of model generation."}
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    checked, changes = public_text(ROOT, "results/G3_PUBLIC_TRAJECTORIES.json", raw, {})
    if changes or checked != raw:
        raise ValueError("Public trajectory view unexpectedly requires additional sanitization")
    target = ROOT / "results/G3_PUBLIC_TRAJECTORIES.json"
    write_new(target, value)
    target.with_suffix(".sha256").write_text(sha(target) + "\n", encoding="utf-8")
    return {"path": str(target), "sha256": sha(target), "optimization_episodes": len(optimization_dirs),
            "evaluated_test_units": len(test_dirs), "published": False}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
