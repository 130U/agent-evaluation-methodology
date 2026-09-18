"""Read-only raw-call replay for prospectively versioned recovered transport."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tau_feedback.contracts import canonical_hash
from tau_feedback.recovered_events import POLICY_ID, RECONNECT, validate_recovered_events
from tau_feedback.subscription_client import EXE_SHA256, MODEL_CATALOG_SHA256, _strict_json, _usage
from tau_feedback.subscription_http_client import http_configuration
from tau_feedback.subscription_evidence import load


def audit_client(directory):
    directory = Path(directory).resolve()
    budget = load(directory / "budget.json")
    rows, roles, totals = [], Counter(), Counter()
    requests = sorted(directory.glob("*/request.json"))
    if budget["calls_reserved"] != len(requests):
        raise ValueError("Reserved calls and request artifacts differ")
    for path in requests:
        folder = path.parent
        request, outcome = load(path), load(folder / "outcome.json")
        if (request["call_id"] != folder.name or outcome["call_id"] != folder.name
            or request["role"] != outcome["role"] or request["event_policy"] != POLICY_ID
            or outcome["event_policy"] != POLICY_ID or request["exe_sha256"] != EXE_SHA256
            or outcome["model_catalog_sha256"] != MODEL_CATALOG_SHA256
            or request["model"] != budget["model"] or request["effort"] != budget["effort"]):
            raise ValueError("Call identity/model/policy differs")
        command = request["command"]
        settings = [command[i + 1] for i, value in enumerate(command) if value == "-c"]
        if settings != http_configuration(Path(request["clean_directory"])):
            raise ValueError("HTTP/tool-isolation configuration differs")
        if request["prompt"] != (folder / "prompt.txt").read_text(encoding="utf-8") or request["schema"] != load(folder / "schema.json"):
            raise ValueError("Prompt/schema artifact differs")
        raw = (folder / "events.jsonl").read_bytes()
        if raw != (folder / "stdout.bin").read_bytes():
            raise ValueError("Raw byte copies differ")
        events, malformed = [], 0
        for line in raw.splitlines():
            try:
                events.append(_strict_json(line.decode("utf-8")))
            except (ValueError, UnicodeError):
                malformed += 1
        observed = [e for e in events if isinstance(e, dict) and e.get("type") == "error"
                    and isinstance(e.get("message"), str) and RECONNECT.fullmatch(e["message"])]
        try:
            usage = _usage(events)
        except ValueError:
            usage = None
        if usage != outcome["usage"] or outcome["usage_complete"] != (usage is not None and malformed == 0):
            raise ValueError("Raw usage completeness differs")
        if usage:
            totals["input_tokens"] += usage["input_tokens"]
            totals["output_tokens"] += usage["output_tokens"]
        if not outcome["usage_complete"]:
            totals["unknown_usage_calls"] += 1
        accepted = outcome["status"] == "complete"
        if accepted:
            if (malformed or outcome["exit_code"] != 0 or (folder / "stderr.txt").read_bytes().strip()
                or outcome["process_cleanup"]["cleanup_complete"] is not True
                or outcome["process_cleanup"]["active_processes"] != 0):
                raise ValueError("Accepted call has failed stream/cleanup/exit")
            answer, warnings, recovered = validate_recovered_events(events)
            value = _strict_json(answer)
            if (canonical_hash(value) != canonical_hash(load(folder / "final.json"))
                or canonical_hash(value) != canonical_hash(outcome["output"])
                or warnings != outcome["warnings"] or recovered != outcome["reconnects"]
                or outcome["recovery_status"] != ("recovered" if recovered else "clean")):
                raise ValueError("Accepted output/recovery replay differs")
            Draft202012Validator(request["schema"]).validate(value)
        elif outcome["status"] not in {"rejected", "timeout"}:
            raise ValueError("Call has no terminal status")
        roles[request["role"]] += 1
        rows.append({"call_id": folder.name, "role": request["role"], "status": outcome["status"],
            "usage_complete": outcome["usage_complete"], "observed_reconnect_notices": len(observed),
            "accepted_reconnect_notices": len(observed) if accepted else 0,
            "request_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "events_sha256": hashlib.sha256(raw).hexdigest()})
    for key, field in (("input_tokens", "known_input_tokens"), ("output_tokens", "known_output_tokens"),
                       ("unknown_usage_calls", "unknown_usage_calls")):
        if totals[key] != budget[field]:
            raise ValueError("Client usage total differs")
    return {"client_directory": str(directory), "calls": len(rows), "roles": dict(roles),
        "audit_implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "audit_scope": "standalone read-only raw record replay; no model calls or record reacceptance",
        "accepted_calls": sum(r["status"] == "complete" for r in rows),
        "rejected_or_timeout_calls": sum(r["status"] != "complete" for r in rows),
        "observed_reconnect_notices": sum(r["observed_reconnect_notices"] for r in rows),
        "accepted_reconnect_notices": sum(r["accepted_reconnect_notices"] for r in rows),
        "known_usage": dict(totals), "rows": rows,
        "boundary": "CLI reported usage and observed notifications only; no provider-request/reconnect cost visibility"}


if __name__ == "__main__":
    print(json.dumps(audit_client(sys.argv[1]), ensure_ascii=False, indent=2))
