"""Audit all completed final-test CLI calls against raw local artifacts.

Post-registration read-only implementation; does not evaluate semantic truth.
A stopped test remains reportable without pretending its transport is complete.
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
    if summary["status"] != "complete":
        raise ValueError("Complete transport audit requires a completed test; retain stopped evidence separately")
    directory = stage / "test"
    outcomes, roles, totals, hashes = {}, Counter(), Counter(), {}
    def bind(path):
        hashes[path.relative_to(ROOT).as_posix()] = sha(path)
    for path in sorted((directory / "client").glob("*/outcome.json")):
        folder = path.parent
        outcome, request = load(path), load(folder / "request.json")
        if (outcome["status"] != "complete" or not outcome["usage_complete"]
            or outcome["exit_code"] != 0 or outcome["call_id"] != folder.name
            or request["call_id"] != folder.name or request["role"] != outcome["role"]
            or outcome["model"] != manifest["model"] or outcome["effort"] != manifest["effort"]
            or request["exe_sha256"] != manifest["cli_exe_sha256"]
            or outcome["model_catalog_sha256"] != manifest["client_catalog_sha256"]
            or not outcome["process_cleanup"]["cleanup_complete"]
            or outcome["process_cleanup"]["active_processes"] != 0):
            raise ValueError("CLI identity/status/configuration/cleanup differs")
        raw = (folder / "events.jsonl").read_bytes()
        if raw != (folder / "stdout.bin").read_bytes() or (folder / "stderr.txt").read_bytes().strip():
            raise ValueError("Raw stream differs or stderr is nonempty")
        events = [_strict_json(line.decode("utf-8")) for line in raw.splitlines()]
        answer, warnings = _validate_events(events)
        value = _strict_json(answer)
        if (canonical_hash(value) != canonical_hash(load(folder / "final.json"))
            or canonical_hash(value) != canonical_hash(outcome["output"])
            or warnings != outcome["warnings"] or _usage(events) != outcome["usage"]
            or request["prompt"] != (folder / "prompt.txt").read_text(encoding="utf-8")
            or request["schema"] != load(folder / "schema.json")):
            raise ValueError("Raw final/events/prompt/schema/usage replay differs")
        Draft202012Validator(request["schema"]).validate(value)
        outcomes[folder.name] = (outcome, request)
        roles[outcome["role"]] += 1
        for key in ("input_tokens", "output_tokens"):
            totals[key] += outcome["usage"][key]
        for name in ("request.json", "outcome.json", "final.json", "events.jsonl", "stderr.txt", "prompt.txt", "schema.json"):
            bind(folder / name)
    budget = load(directory / "client/budget.json")
    if (budget["calls_reserved"] != len(outcomes) or budget["unknown_usage_calls"] != 0
        or budget["known_input_tokens"] != totals["input_tokens"]
        or budget["known_output_tokens"] != totals["output_tokens"]):
        raise ValueError("Shared CLI budget differs from individual raw calls")
    bind(directory / "client/budget.json")
    used = set()
    for target in completed.values():
        path = target / "calls.jsonl"
        bind(path)
        records = [_strict_json(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for index, record in enumerate(records):
            call_id = record["cli_call_id"]
            if call_id in used or call_id not in outcomes or record["call_index"] != index or record.get("error_type"):
                raise ValueError("Duplicate/unbound/error environment call")
            used.add(call_id)
            outcome, request = outcomes[call_id]
            prompt = PROTOCOL + "\nAUTHORIZED_CONVERSATION_JSON\n" + json.dumps(record["request"], ensure_ascii=False, allow_nan=False)
            if (request["prompt"] != prompt or request["role"] != record["role"]
                or request["schema"] != response_schema(bool(record["request"]["tools"]))
                or outcome["output"] != record["structured_output"]
                or outcome["usage"] != record["cli_usage"]):
                raise ValueError("Environment input/output differs from actual CLI call")
    if used != set(outcomes):
        raise ValueError("Final test has a CLI call unbound to its 36 episodes")
    result = {"manifest_sha256": digest, "final_strategies_sha256": policy_digest,
        "verified_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "implementation_timing": "post_registration_read_only_audit",
        "audit_sources_sha256": {p.relative_to(ROOT).as_posix(): sha(p)
            for p in (Path(__file__), ROOT / "scripts/report_g3_test.py")},
        "accepted_cli_calls_replayed": len(outcomes), "roles": dict(roles),
        "environment_calls_bound_to_actual_prompt_output": len(used),
        "completed_units": len(completed), "known_tokens": dict(totals), "unknown_usage_calls": 0,
        "evidence_hashes": hashes, "episode_evidence_sha256": episode_hashes,
        "boundary": "Recorded transport/configuration/usage consistency; not semantic truth, full OS isolation, or independent producer authentication"}
    write_new(ROOT / "research/G3_TEST_TRANSPORT_REVIEW.json", result)
    return {k: v for k, v in result.items() if k not in ("evidence_hashes", "episode_evidence_sha256")}


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False))
