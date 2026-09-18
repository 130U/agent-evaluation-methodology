"""Public dialogue evidence must preserve actions without host metadata."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from export_g3_public_trajectories import project_trajectory


class PublicTrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {"role": "user", "turn_idx": 1, "content": "Cancel this synthetic order.", "raw_data": "PRIVATE_USER_RAW"},
            {"role": "assistant", "turn_idx": 2, "content": None,
             "tool_calls": [{"id": "PRIVATE_RANDOM_ID", "name": "get_order_details", "arguments": {"order_id": "synthetic-1"},
                             "raw_data": "PRIVATE_CALL_RAW", "requestor": "assistant"}],
             "raw_data": "PRIVATE_MODEL_RAW", "audio_path": "PRIVATE_HOST_PATH", "timestamp": "PRIVATE_TIME"},
            {"role": "tool", "turn_idx": 3, "id": "PRIVATE_RANDOM_ID", "content": "pending", "error": False,
             "host_future_metadata": "PRIVATE_FUTURE_FIELD"}]

    def test_host_metadata_dropped_and_tool_link_retained(self):
        value = project_trajectory(self.messages)
        self.assertNotIn("PRIVATE_", json.dumps(value))
        messages = value["messages"]
        self.assertEqual(messages[1]["tool_calls"][0]["id"], messages[2]["id"])
        self.assertEqual(messages[1]["tool_calls"][0]["arguments"], {"order_id": "synthetic-1"})
        self.assertEqual(messages[2]["content"], "pending")

    def test_projection_does_not_edit_original(self):
        before = deepcopy(self.messages)
        result = project_trajectory(self.messages)
        result["messages"][1]["tool_calls"][0]["arguments"]["order_id"] = "changed"
        self.assertEqual(before, self.messages)

    def test_duplicate_or_missing_response_is_not_published_as_complete(self):
        duplicate = deepcopy(self.messages[-1])
        duplicate["turn_idx"] = 4
        for messages in (self.messages[:-1], self.messages + [duplicate]):
            with self.assertRaises(ValueError):
                project_trajectory(messages)

    def test_reordered_turns_are_not_silently_sorted(self):
        with self.assertRaisesRegex(ValueError, "trajectory schema"):
            project_trajectory([self.messages[1], self.messages[0], self.messages[2]])


if __name__ == "__main__":
    unittest.main()
