"""Post-N1 intervention-scope check, independent of the frozen semantic gate.

Supports only the pinned text orchestrator and the project's recorded CLI
adapter. Caller must independently validate source and episode artifact hashes.
This is an accidental-misbinding check, not cryptographic producer identity.
Neither role='assistant' nor matching text alone establishes model ownership.
The intervention is changing StrategyAgent.system_prompt, not the harness.
"""
from dataclasses import dataclass
from .contracts import strict_json

ORCHESTRATOR_SHA256 = "ff6c2631a02b68a3341b1ca3d9a6122bb43ab872ee6acc960379c78015eca420"
INITIAL_GREETING = "Hi! How can I help you today?"


@dataclass(frozen=True)
class OriginContext:
    orchestrator_sha256: str
    solo_mode: bool
    initial_history_count: int
    intervention: str = "agent_system_prompt_only"


def classify_action_origin(message, calls, context):
    """Return model_agent/framework_seed/non_agent/unknown, never infer by text.

    A model-owned action remains subject to evidence and semantic checks; this
    result alone does not make any associated diagnosis correct or useful.
    """
    def result(origin, editable, reason, call_id=None):
        return {"origin": origin, "strategy_editable": editable, "reason": reason,
                "cli_call_id": call_id, "intervention": context.intervention}
    unknown = lambda reason: result("unknown", None, reason)
    if (not isinstance(context, OriginContext)
        or context.orchestrator_sha256 != ORCHESTRATOR_SHA256
        or context.intervention != "agent_system_prompt_only"
        or type(context.solo_mode) is not bool
        or type(context.initial_history_count) is not int or context.initial_history_count < 0):
        # Avoid relying on the closure's context field for invalid context types.
        return {"origin": "unknown", "strategy_editable": None,
                "reason": "unsupported_or_unverified_run_context", "cli_call_id": None,
                "intervention": getattr(context, "intervention", None)}
    if not isinstance(message, dict) or not isinstance(calls, list):
        return unknown("missing_message_or_call_ledger")
    if message.get("role") in ("user", "tool"):
        return result("non_agent", False, "message_role_outside_agent_policy")
    if message.get("role") != "assistant":
        return unknown("missing_or_unrecognized_message_role")
    raw = message.get("raw_data")
    if (context.solo_mode is False and context.initial_history_count == 0
        and message.get("turn_idx") == 0 and raw is not None):
        return unknown("generation_metadata_conflicts_with_pinned_initializer")
    if isinstance(raw, dict) and isinstance(raw.get("cli_call_id"), str):
        call_id = raw["cli_call_id"]
        matches = [c for c in calls if isinstance(c, dict) and c.get("cli_call_id") == call_id]
        if len(matches) != 1:
            return unknown("missing_or_duplicate_call_identity")
        record = matches[0]
        value = record.get("structured_output")
        if (record.get("role") != "agent_response" or record.get("error_type")
            or raw.get("transport") != "codex_cli_structured_action"
            or record.get("transport") != raw["transport"]
            or not isinstance(value, dict) or raw.get("output") != value):
            return unknown("call_role_status_or_output_binding_mismatch")
        try:
            expected_calls = [{"id": f"cli_{call_id}_{i}", "name": c["name"],
                               "arguments": strict_json(c["arguments_json"]), "requestor": "assistant"}
                              for i, c in enumerate(value["tool_calls"])]
            if (value["kind"] not in {"message", "tool_calls"}
                or (value["kind"] == "message" and (expected_calls or not isinstance(value["content"], str)))
                or (value["kind"] == "tool_calls" and (not expected_calls or value["content"] is not None))
                or any(not isinstance(c["arguments"], dict) for c in expected_calls)
                or message.get("content") != value["content"]
                or (message.get("tool_calls") or []) != expected_calls):
                return unknown("rendered_action_differs_from_recorded_model_output")
        except (KeyError, TypeError, ValueError):
            return unknown("malformed_recorded_output")
        return result("model_agent", True, "unique_recorded_agent_generation_matches_action", call_id)
    if raw is not None:
        return unknown("unrecognized_generation_metadata")
    # This is a positive provenance rule, not `turn_idx == 0` alone. The pinned
    # fresh/non-solo branch inserts the constant before the first user call.
    first = calls[0] if calls and isinstance(calls[0], dict) else {}
    first_request = first.get("request", {})
    requested_messages = first_request.get("messages", []) if isinstance(first_request, dict) else []
    if (context.solo_mode is False and context.initial_history_count == 0
        and type(message.get("turn_idx")) is int and message["turn_idx"] == 0
        and message.get("content") == INITIAL_GREETING and not message.get("tool_calls")
        and message.get("usage") is None and type(message.get("cost")) in (int, float) and message["cost"] == 0
        and type(first.get("call_index")) is int and first["call_index"] == 0
        and first.get("role") == "user_simulator_response"
        and isinstance(first_request, dict)
        and first_request.get("participant") == "user_simulator_response"
        and isinstance(requested_messages, list) and requested_messages and isinstance(requested_messages[-1], dict)
        and requested_messages[-1].get("role") == "user"
        and requested_messages[-1].get("content") == INITIAL_GREETING):
        return result("framework_seed", False, "pinned_fresh_non_solo_initializer_precedes_model_calls")
    return unknown("no_verified_generation_or_framework_origin")


def strategy_feedback_eligibility(origin):
    """A new routing precheck, not a retroactive alteration of N1/G2 gates."""
    if origin.get("origin") == "model_agent" and origin.get("strategy_editable") is True:
        return "continue_semantic_review"
    if origin.get("strategy_editable") is False:
        return "route_to_harness_or_non_agent_review"
    return "abstain_origin_unknown"
