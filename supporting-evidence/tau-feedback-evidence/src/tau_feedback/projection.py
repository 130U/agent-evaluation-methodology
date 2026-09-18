"""Build an action-time view without gold tasks or future messages.

This implements an information boundary inspired by prior work (including
CAST). It is not a semantic fault-attribution algorithm.
"""
from __future__ import annotations
from copy import deepcopy
from typing import Any
from .contracts import canonical_hash

def _visible_message(message: dict[str, Any]) -> dict[str, Any]:
    role = message.get("role")
    if role not in {"user", "assistant", "tool"}:
        raise ValueError(f"Unsupported transcript role: {role!r}")
    if message.get("is_audio") or message.get("audio") is not None or message.get("audio_content") is not None:
        raise ValueError("Audio inputs are unsupported")
    if (role == "user" and message.get("tool_calls")) or message.get("requestor") == "user":
        raise ValueError("User-side tools are unsupported by this retail-only projection")
    if message.get("content") is not None and not isinstance(message["content"], str):
        raise ValueError("Only text half-duplex messages are supported")
    # Do not forward raw model responses, usage metadata, or arbitrary extras.
    allowed = {"id", "role", "content", "tool_calls", "tool_call_id", "name", "error", "requestor"}
    result = {k: deepcopy(v) for k, v in message.items() if k in allowed}
    calls = result.get("tool_calls")
    if calls is not None:
        if not isinstance(calls, list):
            raise ValueError("tool_calls must be a list")
        if any(not isinstance(call, dict) or call.get("requestor") == "user" for call in calls):
            raise ValueError("Only agent tool calls are supported")
        result["tool_calls"] = [
            {k: deepcopy(v) for k, v in call.items() if k in {"id", "name", "arguments", "requestor"}}
            for call in calls
        ]
    return result

def _validate_tool_links(history: list[dict], action: dict) -> None:
    pending = set()
    seen = set()
    for message in [*history, action]:
        if message["role"] == "tool":
            identifier = message.get("id", message.get("tool_call_id"))
            if message.get("id") is not None and message.get("tool_call_id") is not None and message["id"] != message["tool_call_id"]:
                raise ValueError("Conflicting tool response identifiers")
            if not isinstance(identifier, str) or identifier not in pending:
                raise ValueError("Tool response has no matching pending agent call")
            pending.remove(identifier)
        else:
            if pending:
                raise ValueError("Agent-visible history contains an unresolved tool call")
            for call in message.get("tool_calls") or []:
                identifier = call.get("id")
                if not isinstance(identifier, str) or not identifier or identifier in seen:
                    raise ValueError("Missing or duplicate tool call ID")
                if not isinstance(call.get("name"), str) or not isinstance(call.get("arguments"), dict):
                    raise ValueError("Malformed native tool call")
                pending.add(identifier)
                seen.add(identifier)

def project_action_context(*, messages: list[dict], action_index: int,
                           policy: str, tool_schemas: list[dict]) -> dict:
    if type(action_index) is not int or not 0 <= action_index < len(messages):
        raise ValueError("Invalid action index")
    if messages[action_index].get("role") != "assistant":
        raise ValueError("The reviewed action must belong to the agent")
    if not isinstance(policy, str) or not isinstance(tool_schemas, list):
        raise ValueError("Explicit policy and tool definitions are required")
    history = [_visible_message(m) for m in messages[:action_index]]
    action = _visible_message(messages[action_index])
    _validate_tool_links(history, action)
    view = {
        "schema_version": "action-view-v1",
        "action_index": action_index,
        "policy": policy,
        "tools": deepcopy(tool_schemas),
        "history": [{"message_index": i, "message": m} for i, m in enumerate(history)],
        "action": action,
    }
    return {**view, "view_sha256": canonical_hash(view)}

def validate_evidence_references(view: dict, references: list[dict]) -> tuple[bool, str]:
    """Check quote location only; entailment requires an independent judge.

    A matching quote cannot establish whether a diagnosis follows from it.
    """
    if not isinstance(references, list) or not references:
        return False, "missing_references"
    for ref in references:
        if not isinstance(ref, dict):
            return False, "invalid_reference"
        quote = ref.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            return False, "empty_quote"
        if ref.get("source") == "policy":
            if quote not in view["policy"]:
                return False, "policy_quote_mismatch"
        elif ref.get("source") == "history":
            index = ref.get("message_index")
            if type(index) is not int or not 0 <= index < view["action_index"]:
                return False, "nonprior_evidence"
            content = view["history"][index]["message"].get("content") or ""
            if quote not in content:
                return False, "history_quote_mismatch"
        else:
            return False, "unsupported_evidence_source"
    return True, "references_located_semantics_unverified"
