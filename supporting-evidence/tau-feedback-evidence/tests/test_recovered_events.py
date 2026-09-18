"""New policy contract tests only; no process or model is launched."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tau_feedback.recovered_events import validate_recovered_events
from tau_feedback.subscription_client import HOST_DISABLED_WARNING, _validate_events


def stream():
    return [
        {"type": "thread.started", "thread_id": "fixture"},
        {"type": "item.completed", "item": {"id": "warning", "type": "error", "message": HOST_DISABLED_WARNING}},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": '{"reply":"ok"}'}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
    ]


def recovery(attempt=1):
    return {"type": "error", "message": f"Reconnecting... {attempt}/5 (stream disconnected before completion: Transport error: network error: error decoding response body)"}


class RecoveredEventsTests(unittest.TestCase):
    def test_clean_stream_is_identical_to_original(self):
        events = stream()
        before = deepcopy(events)
        answer, warnings, recoveries = validate_recovered_events(events)
        self.assertEqual((answer, warnings), _validate_events(events))
        self.assertEqual(recoveries, [])
        self.assertEqual(events, before)

    def test_one_recovery_is_recorded_original_policy_still_rejects(self):
        events = stream()
        events.insert(3, recovery())
        before = deepcopy(events)
        answer, warnings, recoveries = validate_recovered_events(events)
        self.assertEqual(json.loads(answer), {"reply": "ok"})
        self.assertEqual(recoveries, [{"event_index": 3, "attempt": 1, "announced_limit": 5, "event": recovery()}])
        self.assertEqual(events, before)
        with self.assertRaises(ValueError):
            _validate_events(events)

    def test_five_ordered_recoveries_are_bounded(self):
        events = stream()
        events[3:3] = [recovery(i) for i in range(1, 6)]
        self.assertEqual(len(validate_recovered_events(events)[2]), 5)
        events.insert(8, recovery(6))
        with self.assertRaises(ValueError):
            validate_recovered_events(events)

    def test_unknown_quota_and_modified_messages_rejected(self):
        for event in ({"type": "error", "message": "usage limit exceeded"},
                      {"type": "error", "message": recovery()["message"] + " extra"},
                      {**recovery(), "code": "network"},
                      {"type": "error", "message": 1}):
            with self.subTest(event=event):
                events = stream()
                events.insert(3, event)
                with self.assertRaises(ValueError):
                    validate_recovered_events(events)

    def test_skipped_duplicate_reordered_attempts_rejected(self):
        for attempts in ([2], [1, 1], [1, 3], [2, 1]):
            events = stream()
            events[3:3] = [recovery(n) for n in attempts]
            with self.subTest(attempts=attempts), self.assertRaises(ValueError):
                validate_recovered_events(events)

    def test_recovery_outside_turn_or_after_answer_rejected(self):
        for index in (0, 1, 2, 4, 5):
            events = stream()
            events.insert(index, recovery())
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_recovered_events(events)

    def test_recovery_does_not_hide_missing_or_duplicate_final(self):
        cases = [stream()[:-1], stream() + [stream()[-1]], stream()[:4] + [stream()[3], stream()[4]]]
        for events in cases:
            events.insert(3, recovery())
            with self.subTest(events=events), self.assertRaises(ValueError):
                validate_recovered_events(events)

    def test_recovery_does_not_hide_host_tool_event(self):
        events = stream()
        events[3:3] = [recovery(), {"type": "item.completed", "item": {"id": "tool", "type": "command_execution", "command": "not executed"}}]
        with self.assertRaises(ValueError):
            validate_recovered_events(events)

    def test_missing_host_disabled_warning_still_rejected(self):
        events = stream()
        del events[1]
        events.insert(2, recovery())
        with self.assertRaises(ValueError):
            validate_recovered_events(events)

    def test_malformed_usage_still_rejected(self):
        for usage in ({"input_tokens": True, "output_tokens": 1},
                      {"input_tokens": 1, "output_tokens": -1},
                      {"input_tokens": 1},
                      {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 2}):
            events = stream()
            events.insert(3, recovery())
            events[-1]["usage"] = usage
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                validate_recovered_events(events)

    def test_nonobject_or_nonlist_input_rejected(self):
        for events in (None, iter(stream()), [None], stream() + [True]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                validate_recovered_events(events)


if __name__ == "__main__":
    unittest.main()
