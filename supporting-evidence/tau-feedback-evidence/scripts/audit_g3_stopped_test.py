"""Read-only audit of the terminal G3 STOP, separate from complete-test audit.

Added after the stop. A rejected call is accounted for, never reaccepted.
"""
from collections import Counter
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from register_g3_pilot import ID, verify, load, sha, write_new
from run_g3_pilot import verify_arm
from report_g3_test import verify_selection, bind_test
from tau_feedback.contracts import canonical_hash
from tau_feedback.subscription_client import _validate_events, _usage, _strict_json
from tau_feedback.subscription_backend import PROTOCOL, response_schema
from jsonschema import Draft202012Validator


def audit():
    manifest, digest = verify()
    stage = ROOT / "results/g3" / ID
    for arm in manifest["arm_order"]:
        verify_arm(stage / arm, arm, digest)
    policies, policy_digest = verify_selection(stage, digest)
    summary, completed, episode_hashes = bind_test(ROOT, stage, manifest, digest, policies, policy_digest)
    if summary["status"] != "stopped":
        raise ValueError("This audit applies only to the recorded STOP")
    directory = stage / "test"
    outcomes, roles, statuses, totals, hashes, rejections = {}, Counter(), Counter(), Counter(), {}, []
    def bind(path):
        hashes[path.relative_to(ROOT).as_posix()] = sha(path)
    bind(stage / "STOP.json")
    bind(directory / "summary.json")
    for path in sorted((directory / "client").glob("*/outcome.json")):
        folder = path.parent
        outcome, request = load(path), load(folder / "request.json")
        if (not outcome["usage_complete"] or outcome["exit_code"] != 0
            or outcome["call_id"] != folder.name or request["call_id"] != folder.name
            or request["role"] != outcome["role"]
            or outcome["model"] != manifest["model"] or outcome["effort"] != manifest["effort"]
            or request["exe_sha256"] != manifest["cli_exe_sha256"]
            or outcome["model_catalog_sha256"] != manifest["client_catalog_sha256"]
            or not outcome["process_cleanup"]["cleanup_complete"]
            or outcome["process_cleanup"]["active_processes"] != 0):
            raise ValueError("CLI identity/configuration/usage/cleanup differs")
        raw = (folder / "events.jsonl").read_bytes()
        if raw != (folder / "stdout.bin").read_bytes() or (folder / "stderr.txt").read_bytes().strip():
            raise ValueError("Raw stream differs or stderr is nonempty")
        events = [_strict_json(line.decode("utf-8")) for line in raw.splitlines()]
        if (_usage(events) != outcome["usage"]
            or request["prompt"] != (folder / "prompt.txt").read_text(encoding="utf-8")
            or request["schema"] != load(folder / "schema.json")):
            raise ValueError("Raw prompt/schema/usage replay differs")
        if outcome["status"] == "complete":
            answer, warnings = _validate_events(events)
            value = _strict_json(answer)
            if (canonical_hash(value) != canonical_hash(load(folder / "final.json"))
                or canonical_hash(value) != canonical_hash(outcome["output"])
                or warnings != outcome["warnings"]):
                raise ValueError("Accepted final/event output differs")
            Draft202012Validator(request["schema"]).validate(value)
        elif outcome["status"] == "rejected":
            try:
                _validate_events(events)
            except ValueError as exc:
                if outcome["error"] != f"ValueError: {exc}":
                    raise ValueError("Recorded rejection differs from replay") from exc
            else:
                raise ValueError("Recorded rejected stream is unexpectedly accepted")
            errors = [event for event in events if event["type"] == "error"]
            if not errors or "output" in outcome:
                raise ValueError("Rejected transport must retain error and no accepted output")
            rejections.append({"call_id": folder.name, "role": outcome["role"],
                "events": errors, "recorded_error": outcome["error"],
                "final_present_but_not_accepted": outcome["final_present"],
                "exit_code": outcome["exit_code"], "usage": outcome["usage"],
                "process_cleanup_complete": outcome["process_cleanup"]["cleanup_complete"]})
        else:
            raise ValueError("Unexpected terminal call status")
        outcomes[folder.name] = (outcome, request)
        roles[outcome["role"]] += 1
        statuses[outcome["status"]] += 1
        for key in ("input_tokens", "output_tokens"):
            totals[key] += outcome["usage"][key]
        for child in folder.iterdir():
            if child.is_file():
                bind(child)
    budget = load(directory / "client/budget.json")
    if (budget["calls_reserved"] != len(outcomes) or budget["unknown_usage_calls"] != 0
        or budget["known_input_tokens"] != totals["input_tokens"]
        or budget["known_output_tokens"] != totals["output_tokens"]):
        raise ValueError("Shared budget differs from all individual calls")
    bind(directory / "client/budget.json")
    used, rejected_units = set(), []
    for row in summary["units"]:
        target = ROOT / row["relative_directory"]
        path = target / "calls.jsonl"
        bind(path)
        records = [_strict_json(line) for line in path.read_text(encoding="utf-8").splitlines()]
        unit_cost = Counter()
        if len(records) != row["outcome"]["cli_invocations"]:
            raise ValueError("Episode call count differs")
        for index, record in enumerate(records):
            failed = bool(record.get("error_type"))
            call_id = record["error"].split(": ", 1)[0] if failed else record["cli_call_id"]
            if call_id in used or call_id not in outcomes or record["call_index"] != index:
                raise ValueError("Duplicate/unbound environment call")
            used.add(call_id)
            outcome, request = outcomes[call_id]
            prompt = PROTOCOL + "\nAUTHORIZED_CONVERSATION_JSON\n" + json.dumps(record["request"], ensure_ascii=False, allow_nan=False)
            if (request["prompt"] != prompt or request["role"] != record["role"]
                or request["schema"] != response_schema(bool(record["request"]["tools"]))):
                raise ValueError("Environment input differs from actual CLI call")
            if failed:
                if (outcome["status"] != "rejected" or row["outcome"]["status"] != "error"
                    or index != len(records) - 1 or outcome["error"] not in record["error"]
                    or "structured_output" in record or "cli_usage" in record):
                    raise ValueError("Rejected call improperly accepted or not terminal")
                rejected_units.append(row["unit"])
            elif (outcome["status"] != "complete" or outcome["output"] != record["structured_output"]
                    or outcome["usage"] != record["cli_usage"]):
                raise ValueError("Environment output differs from actual CLI call")
            for key in ("input_tokens", "output_tokens"):
                unit_cost[key] += outcome["usage"][key]
        if any(unit_cost[k] != row["outcome"][k] for k in ("input_tokens", "output_tokens")):
            raise ValueError("Episode usage does not include all attempted calls")
    if (used != set(outcomes) or statuses != {"complete": 272, "rejected": 1}
        or rejected_units != [19] or len(completed) != 18 or summary["unrun_units"] != 17):
        raise ValueError("Terminal evidence differs from the specific recorded stop")
    result = {"manifest_sha256": digest, "final_strategies_sha256": policy_digest,
        "verified_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "implementation_timing": "post_stop_read_only_audit", "test_status": "stopped",
        "audit_sources_sha256": {p.relative_to(ROOT).as_posix(): sha(p)
            for p in (Path(__file__), ROOT / "scripts/report_g3_test.py")},
        "call_status_counts": dict(statuses), "roles": dict(roles),
        "environment_calls_bound_to_actual_prompt": len(used),
        "evaluated_units": len(completed), "attempted_ungraded_units": 1, "unrun_units": 17,
        "known_tokens": dict(totals), "unknown_usage_calls": 0,
        "rejected_units": rejected_units, "rejections": rejections,
        "evidence_hashes": hashes, "episode_evidence_sha256": episode_hashes,
        "boundary": "Transport and accounting consistency, including one rejected network-reconnect stream; no retry or reacceptance; no proof of provider-request count, semantic truth, OS isolation or independent producer authentication"}
    write_new(ROOT / "research/G3_TEST_STOP_REVIEW.json", result)
    return {k: v for k, v in result.items() if k not in ("evidence_hashes", "episode_evidence_sha256")}


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False))
