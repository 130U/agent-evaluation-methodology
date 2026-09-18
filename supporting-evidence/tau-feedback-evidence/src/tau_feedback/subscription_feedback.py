"""Independent native-review/admission bridges; no acting or GEPA selection.

Native reviewer prompts and parser remain upstream. A strict text envelope is
only the CLI transport. Privileged review inputs and raw gate decisions stay in
audit sidecars; these objects are not reflection-ready feedback. Structural
validity and located quotes do not establish semantic truth.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from pathlib import Path
import threading
import uuid
from unittest.mock import patch

from jsonschema import Draft202012Validator

from .admission import AdmissionRecord, GATE_INSTRUCTION, audit_diagnosis
from .contracts import canonical_hash, strict_json
from .projection import project_action_context


REVIEW_MODEL = "codex-cli/native-review-sol"
REVIEW_ROLE = "llm_judge_review"
ADMISSION_ROLE = "feedback_admission"
UPSTREAM_COMMIT = "2174a603f6d014ef94473ffa95957f6ce27100db"
_REVIEW_LOCK = threading.Lock()
TEXT_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}},
               "required": ["text"], "additionalProperties": False}
GATE_SCHEMA = {
    "type": "object", "properties": {
        "decision": {"type": "string", "enum": ["accept", "reject", "abstain"]},
        **{name: {"type": ["boolean", "null"]} for name in
           ("claim_supported", "agent_responsible", "correction_observable")},
        "references": {"type": "array", "items": {"anyOf": [
            {"type": "object", "properties": {"source": {"type": "string", "const": "policy"}, "quote": {"type": "string"}},
             "required": ["source", "quote"], "additionalProperties": False},
            {"type": "object", "properties": {"source": {"type": "string", "const": "history"}, "quote": {"type": "string"},
                                                "message_index": {"type": "integer", "minimum": 0}},
             "required": ["source", "quote", "message_index"], "additionalProperties": False},
        ]}},
        "reason": {"type": "string", "minLength": 1},
    },
    "required": ["decision", "claim_supported", "agent_responsible", "correction_observable", "references", "reason"],
    "additionalProperties": False,
}
REVIEW_TRANSPORT_INSTRUCTION = """Generate one response to the exact conversation messages below.
The embedded system and user messages contain the native conversation-review
task. Preserve their roles and follow that task. Use only those inputs; use no
host tools, files, network, memory, or other conversations. Return a JSON object
with the single field text. Its value must be the complete native review
response as text, without adding another task or changing the review criteria.
"""


class FeedbackContractError(ValueError):
    """A transport/input contract failed; no retry or implicit valid review."""


@dataclass(frozen=True)
class NativeReviewResult:
    status: str
    native_review: dict | None
    diagnoses: tuple[dict, ...] | None
    call_id: str | None
    reason: str
    sidecar_directory: str

    def require_valid_diagnoses(self) -> tuple[dict, ...]:
        if self.status != "valid" or self.diagnoses is None:
            raise FeedbackContractError("Review is unknown: " + self.reason)
        return deepcopy(self.diagnoses)


def _json_copy(value):
    return strict_json(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _new_directory(client, output):
    destination = Path(output).resolve()
    if getattr(client, "root", None) is not None and not destination.is_relative_to(Path(client.root).resolve()):
        raise FeedbackContractError("Feedback sidecars must stay inside the client research root")
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def _result_metadata(result):
    usage = result.usage
    if (not isinstance(usage, dict) or not {"input_tokens", "output_tokens"} <= usage.keys()
        or any(type(value) is not int or value < 0 for value in usage.values())
        or usage.get("cached_input_tokens", 0) > usage["input_tokens"]):
        raise FeedbackContractError("Missing or invalid usage; the feedback call is not complete")
    if not isinstance(result.call_id, str) or not result.call_id:
        raise FeedbackContractError("Missing CLI call ID")
    if (type(result.elapsed_seconds) not in (int, float) or not math.isfinite(result.elapsed_seconds)
        or result.elapsed_seconds < 0):
        raise FeedbackContractError("Invalid elapsed time")
    return {"cli_call_id": result.call_id, "usage": _json_copy(usage),
            "elapsed_seconds": result.elapsed_seconds}


def _invoke(client, *, prompt, schema, role, output, request_extra=None):
    """Record before calling; failures propagate after saving available records."""
    directory = _new_directory(client, output)
    request = {"role": role, "prompt": prompt, "schema": deepcopy(schema),
               "scope": "research sidecar only; not acting/reflection input", **(request_extra or {})}
    _save(directory / "request.json", request)
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
    row = {"status": "reserved", "role": role, "usage_complete": False}
    before = len(getattr(client, "records", []))
    try:
        result = client.generate(prompt, deepcopy(schema), role=role)
        # Keep the caller-returned fields even if defensive validation rejects.
        row.update(cli_call_id=getattr(result, "call_id", None), usage=getattr(result, "usage", None),
                   elapsed_seconds=getattr(result, "elapsed_seconds", None), output=getattr(result, "output", None))
        _save(directory / "response.json", row)
        if isinstance(getattr(result, "output", None), dict) and isinstance(result.output.get("text"), str):
            (directory / "native_response.txt").write_text(result.output["text"], encoding="utf-8")
        metadata = _result_metadata(result)
        Draft202012Validator(schema).validate(result.output)
        row.update(status="complete", usage_complete=True, **metadata)
        return _json_copy(result.output), metadata
    except BaseException as exc:
        records = getattr(client, "records", [])
        # This is only a link to new ledger entries, never invented usage.
        row.update(status="error", error_type=type(exc).__name__, error=str(exc),
                   client_record_links=[{"call_id": record.get("call_id"), "directory": record.get("directory"),
                                         "role": record.get("role"), "status": record.get("status")}
                                        for record in records[before:]])
        raise
    finally:
        _save(directory / "outcome.json", row)


def _review_contract(raw, native, upstream):
    """Stricter coverage check after the unchanged upstream parser has run."""
    payload = strict_json(upstream._extract_json_from_response(raw))
    if not isinstance(payload, dict) or set(payload) != {"errors", "summary"}:
        raise FeedbackContractError("Native review must explicitly contain errors and summary only")
    if not isinstance(payload["summary"], str) or not isinstance(payload["errors"], list):
        raise FeedbackContractError("Native review errors/summary have invalid types")
    required = {"source", "error_tags", "severity", "turn_idx", "reasoning", "correct_behavior"}
    allowed = required | {"error_type", "tick_start", "tick_end"}
    for error in payload["errors"]:
        if not isinstance(error, dict) or not required <= error.keys() or error.keys() - allowed:
            raise FeedbackContractError("Native diagnosis has missing or unexpected fields")
        if error["source"] not in ("agent", "user"):
            raise FeedbackContractError("Native diagnosis source is unknown")
        severities = ("minor", "critical") if error["source"] == "agent" else ("minor", "critical_helped", "critical_hindered")
        if error["severity"] not in severities:
            raise FeedbackContractError("Native diagnosis severity is invalid")
        if not isinstance(error["error_tags"], list) or not error["error_tags"] or any(
            not isinstance(tag, str) or not tag.strip() for tag in error["error_tags"]
        ):
            raise FeedbackContractError("Native diagnosis tags are invalid")
        if error["turn_idx"] is not None and (type(error["turn_idx"]) is not int or error["turn_idx"] < 0):
            raise FeedbackContractError("Native turn_idx must be an explicit integer or null")
        if any(not isinstance(error[key], str) or not error[key].strip() for key in ("reasoning", "correct_behavior")):
            raise FeedbackContractError("Native diagnosis lacks a claim or correction")
    errors = native["errors"]
    if len(errors) != len(payload["errors"]) or any(error["source"] == "unknown" for error in errors):
        raise FeedbackContractError("Native parser failure or diagnosis coverage mismatch")
    expected = {"agent_error": any(error["source"] == "agent" for error in errors),
                "user_error": any(error["source"] == "user" for error in errors),
                "critical_user_error": any(error["source"] == "user" and error["severity"] in
                                           ("critical_helped", "critical_hindered") for error in errors),
                "has_errors": bool(errors)}
    if any(type(native.get(key)) is not bool or native[key] != value for key, value in expected.items()):
        raise FeedbackContractError("Native review flags conflict with diagnoses")


def review_simulation(client, *, simulation, task, user_info, output: Path) -> NativeReviewResult:
    """Run exactly one upstream half-duplex review, returning explicit validity.

    No mutation of simulation.review; output must be a new directory. Valid empty
    diagnoses are (), whereas an incomplete/malformed review has diagnoses=None.
    Transport/construction errors propagate with sidecars; raw parse failures
    return status='unknown'. Callers must check status before using diagnoses.
    """
    from tau2.data_model.message import AssistantMessage
    from tau2.data_model.simulation import SimulationRun, UserInfo
    from tau2.data_model.tasks import Task
    from tau2.evaluator import review_llm_judge as upstream
    from tau2.utils.llm_utils import to_litellm_messages

    directory = _new_directory(client, output)
    row = {"status": "started", "upstream_commit": UPSTREAM_COMMIT,
           "native_review": None, "diagnoses": None, "cli_call_id": None,
           "scope": "privileged reviewer audit; never acting/reflection input"}
    _save(directory / "outcome.json", row)
    captured = {}
    locked = False
    try:
        if not isinstance(simulation, SimulationRun) or not isinstance(task, Task) or not isinstance(user_info, UserInfo):
            raise FeedbackContractError("Use actual upstream SimulationRun, Task and UserInfo objects")
        if simulation.task_id != task.id:
            raise FeedbackContractError("Simulation and task IDs differ")
        if simulation.mode != "half_duplex" or simulation.messages is None or simulation.ticks is not None:
            raise FeedbackContractError("Only explicit text half-duplex trajectories are supported")
        if not isinstance(simulation.policy, str) or not simulation.policy.strip():
            raise FeedbackContractError("Simulation must retain its actual policy")
        if not isinstance(user_info.global_simulation_guidelines, str) or not user_info.global_simulation_guidelines.strip():
            raise FeedbackContractError("Actual user simulation guidelines are required")
        messages = deepcopy(simulation.messages)
        row.update(task_id=task.id, simulation_id=simulation.id,
                   task_sha256=canonical_hash(task.model_dump(mode="json")),
                   trajectory_sha256=canonical_hash([message.model_dump(mode="json") for message in messages]),
                   policy_sha256=canonical_hash(simulation.policy),
                   user_guidelines_sha256=canonical_hash(user_info.global_simulation_guidelines))
        _save(directory / "binding.json", row)

        def generate(model, messages, tools=None, tool_choice=None, call_name=None, **kwargs):
            if model != REVIEW_MODEL or call_name != REVIEW_ROLE or tools is not None or tool_choice is not None or kwargs:
                raise FeedbackContractError("Unexpected upstream reviewer model, role or generation arguments")
            if captured:
                raise FeedbackContractError("Native review unexpectedly requested more than one generation")
            # Retain upstream system/user content exactly; only add CLI envelope.
            payload = {"messages": to_litellm_messages(messages)}
            prompt = REVIEW_TRANSPORT_INSTRUCTION + "\nNATIVE_REVIEW_MESSAGES_JSON\n" + json.dumps(
                payload, ensure_ascii=False, allow_nan=False)
            captured["started"] = True
            value, metadata = _invoke(client, prompt=prompt, schema=TEXT_SCHEMA, role=REVIEW_ROLE,
                output=directory / "model_call", request_extra={"native_messages": payload["messages"], "model_alias": model})
            captured.update(raw=value["text"], **metadata)
            return AssistantMessage(role="assistant", content=value["text"], cost=None,
                usage={"prompt_tokens": metadata["usage"]["input_tokens"],
                       "completion_tokens": metadata["usage"]["output_tokens"]},
                generation_time_seconds=metadata["elapsed_seconds"],
                raw_data={"transport": "codex_cli_native_review_text", "cli_call_id": metadata["cli_call_id"]})

        locked = _REVIEW_LOCK.acquire(blocking=False)
        if not locked:
            raise FeedbackContractError("Native reviewer patch is already active; use sequential review calls")
        with patch.object(upstream, "generate", generate):
            native = upstream.ConversationReviewer.review(user_info=deepcopy(user_info), task=deepcopy(task),
                full_trajectory=messages, policy=simulation.policy, review_model=REVIEW_MODEL)
        native_dict = native.model_dump(mode="json")
        row.update(native_review=native_dict, cli_call_id=captured.get("cli_call_id"))
        _save(directory / "native_review.json", native_dict)
        try:
            if "raw" not in captured:
                raise FeedbackContractError("Native reviewer returned without its recorded model response")
            _review_contract(captured["raw"], native_dict, upstream)
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError) as exc:
            row.update(status="unknown", reason=f"{type(exc).__name__}: {exc}")
        else:
            row.update(status="valid", reason="native_review_structurally_complete_semantics_unverified",
                       diagnoses=native_dict["errors"])
        return NativeReviewResult(row["status"], deepcopy(native_dict),
            tuple(deepcopy(row["diagnoses"])) if row["diagnoses"] is not None else None,
            row["cli_call_id"], row["reason"], str(directory))
    except BaseException as exc:
        row.update(status="error", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if locked:
            _REVIEW_LOCK.release()
        _save(directory / "outcome.json", row)


def _validate_gate_payload(instruction, payload):
    if instruction != GATE_INSTRUCTION:
        raise FeedbackContractError("Admission instruction differs from the fixed mechanism")
    value = _json_copy(payload)
    if not isinstance(value, dict) or set(value) != {"diagnosis", "action_view"}:
        raise FeedbackContractError("Admission receives only diagnosis and action_view")
    claim, view = value["diagnosis"], value["action_view"]
    if (not isinstance(claim, dict) or set(claim) != {"reasoning", "correct_behavior"}
        or any(not isinstance(text, str) or not text.strip() for text in claim.values())):
        raise FeedbackContractError("Admission claim fields are invalid")
    required = {"schema_version", "action_index", "policy", "tools", "history", "action", "view_sha256"}
    if not isinstance(view, dict) or set(view) != required or not isinstance(view["history"], list):
        raise FeedbackContractError("Admission action view fields are invalid")
    if type(view["action_index"]) is not int or view["action_index"] != len(view["history"]):
        raise FeedbackContractError("Action index must end the supplied visible prefix")
    messages = []
    for index, item in enumerate(view["history"]):
        if (not isinstance(item, dict) or set(item) != {"message_index", "message"}
            or type(item["message_index"]) is not int or item["message_index"] != index):
            raise FeedbackContractError("Admission history has invalid position metadata")
        messages.append(item["message"])
    messages.append(view["action"])
    expected = project_action_context(messages=messages, action_index=view["action_index"],
                                      policy=view["policy"], tool_schemas=view["tools"])
    if canonical_hash(view) != canonical_hash(expected):
        raise FeedbackContractError("Action view hash or field projection differs from the supplied evidence")
    return value


class AdmissionJudge:
    """Callable accepted by audit_diagnosis; records only its projected input.

    It accepts no task/user_info/reward argument. The diagnosis itself may
    mention a hidden fact; that remains an untrusted claim, not visible evidence.
    """

    def __init__(self, client, *, output: Path):
        self.client = client
        self.output = _new_directory(client, output)
        self.records = []

    def __call__(self, instruction: str, payload: dict) -> str:
        value = _validate_gate_payload(instruction, payload)
        directory = self.output / f"{len(self.records) + 1:06d}-{uuid.uuid4().hex}"
        record = {"directory": str(directory), "view_sha256": value["action_view"]["view_sha256"],
                  "claim_sha256": canonical_hash(value["diagnosis"]), "status": "reserved"}
        self.records.append(record)
        prompt = instruction + "\nADMISSION_EVIDENCE_JSON\n" + json.dumps(value, ensure_ascii=False, allow_nan=False)
        try:
            verdict, metadata = _invoke(self.client, prompt=prompt, schema=GATE_SCHEMA, role=ADMISSION_ROLE,
                                       output=directory, request_extra={"payload": value})
            record.update(status="complete", **metadata)
            return json.dumps(verdict, ensure_ascii=False, allow_nan=False)
        except BaseException as exc:
            record.update(status="error", error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            _save(self.output / "calls.json", self.records)


def audit_native_diagnosis(client, *, diagnosis: dict, messages: list[dict], policy: str,
                          tool_schemas: list[dict], output: Path) -> AdmissionRecord:
    """Run admission on raw indexed messages, then save the unselected decision.

    No task object, hidden user instructions, gold, reflection or selection step
    is accepted here. Raw full-transcript content is not written to this gate's
    model request; only the upstream projection crosses the model boundary.
    """
    directory = _new_directory(client, output)
    row = {"status": "started", "scope": "raw admission audit only; not reflection feedback"}
    _save(directory / "outcome.json", row)
    try:
        diagnosis_copy, messages_copy, tools_copy = _json_copy(diagnosis), _json_copy(messages), _json_copy(tool_schemas)
        binding = {"diagnosis_sha256": canonical_hash(diagnosis_copy),
                   "trajectory_sha256": canonical_hash(messages_copy),
                   "policy_sha256": canonical_hash(policy), "tools_sha256": canonical_hash(tools_copy)}
        _save(directory / "request.json", {"binding": binding, "diagnosis": diagnosis_copy})
        judge = AdmissionJudge(client, output=directory / "judge")
        record = audit_diagnosis(diagnosis=diagnosis_copy, messages=messages_copy, policy=policy,
                                 tool_schemas=tools_copy, judge=judge)
        row.update(status="complete", binding=binding, admission=record.to_dict(), judge_calls=judge.records,
                   semantic_accuracy_established=False)
        return record
    except BaseException as exc:
        row.update(status="error", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        _save(directory / "outcome.json", row)
