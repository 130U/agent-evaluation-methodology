"""Prospective G4 event policy; never reaccepts any previous study record.

Accept only the observed, numbered CLI network-reconnect notice, before the
single final answer. All remaining events must satisfy the original strict
state machine. The CLI's reported usage is not a provider-request audit.
"""
from copy import deepcopy
import re

from .subscription_client import _usage, _validate_events

POLICY_ID = "g4-numbered-body-decoding-reconnect-v1"
RECONNECT = re.compile(
    r"Reconnecting\.\.\. ([1-5])/5 \(stream disconnected before completion: "
    r"Transport error: network error: error decoding response body\)"
)


def validate_recovered_events(events):
    """Return answer, startup warnings, and explicitly retained recovery events."""
    if not isinstance(events, list):
        raise ValueError("A complete recorded event list is required")
    filtered, recoveries = [], []
    in_turn, answer_seen, completed = False, False, False
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError("Event must be an object")
        kind = event.get("type")
        if kind == "error":
            message = event.get("message")
            match = RECONNECT.fullmatch(message) if isinstance(message, str) else None
            if (set(event) != {"type", "message"} or not match or not in_turn
                or answer_seen or completed or int(match.group(1)) != len(recoveries) + 1):
                raise ValueError("Unapproved, misplaced or nonsequential recovery event")
            recoveries.append({"event_index": index, "attempt": int(match.group(1)),
                "announced_limit": 5, "event": deepcopy(event)})
            continue
        filtered.append(event)
        if kind == "turn.started":
            in_turn = True
        elif kind == "turn.completed":
            completed = True
        elif kind == "item.completed" and isinstance(event.get("item"), dict):
            if event["item"].get("type") == "agent_message":
                answer_seen = True
    answer, warnings = _validate_events(filtered)
    # Do not accept an event stream whose reported usage is malformed or missing.
    _usage(events)
    return answer, warnings, recoveries
