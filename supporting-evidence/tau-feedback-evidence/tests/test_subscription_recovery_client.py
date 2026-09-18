"""Full recovered-client contracts with a fake process; zero model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import tau_feedback.subscription_recovery_client as transport
import tau_feedback.subscription_client as catalog_contract
from tau_feedback.subscription_client import CliBudgetExceeded, CliCallError
from tau_feedback.recovered_events import POLICY_ID
from test_recovered_events import recovery, stream

SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}},
          "required": ["reply"], "additionalProperties": False}


class RecoveredClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="g4-client-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Synthetic config fixture only; no author catalog or model is used.
        self.catalog = self.root / 'synthetic-catalog.json'
        self.catalog.write_bytes(b'{"models":[]}')
        self.counter = 0
        self.original_mkdtemp = tempfile.mkdtemp
        self.patches = [
            patch.object(catalog_contract, 'MODEL_CATALOG_PATH', self.catalog),
            patch.object(catalog_contract, 'MODEL_CATALOG_SHA256', hashlib.sha256(self.catalog.read_bytes()).hexdigest()),
            patch.object(transport, "_file_hash", return_value=transport.EXE_SHA256),
            patch.object(transport.tempfile, "mkdtemp", side_effect=lambda **kw: self.original_mkdtemp(dir=self.root, **kw)),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def client(self, **kwargs):
        self.counter += 1
        return transport.RecoveringHttpCliTextClient(self.root, self.root / f"client-{self.counter}", **kwargs)

    def run_fake(self, client, *, events=None, final=None, stderr=b"", code=0, timeout=False):
        events = deepcopy(stream() if events is None else events)
        raw = ("\n".join(json.dumps(e) for e in events) + "\n").encode()
        def fake(command, **kwargs):
            destination = Path(command[command.index("--output-last-message") + 1])
            destination.write_text(json.dumps({"reply": "ok"} if final is None else final), encoding="utf-8")
            if timeout:
                raise subprocess.TimeoutExpired(command, 1, output=raw, stderr=stderr)
            result = subprocess.CompletedProcess(command, code, raw, stderr)
            result.process_cleanup = {"cleanup_complete": True, "active_processes": 0}
            return result
        with patch.object(transport, "_run_bounded", side_effect=fake) as process:
            result = client.generate("Contract fixture only", SCHEMA, role="test")
        self.assertEqual(process.call_count, 1)
        return result, raw

    def test_recovery_keeps_bytes_usage_and_metadata(self):
        events = stream()
        events.insert(3, recovery())
        client = self.client()
        result, raw = self.run_fake(client, events=events)
        self.assertEqual(result.output, {"reply": "ok"})
        record = client.records[0]
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["event_policy"], POLICY_ID)
        self.assertEqual(record["recovery_status"], "recovered")
        self.assertEqual(record["reconnects"][0]["event"], recovery())
        self.assertEqual((client.calls, client.input_tokens, client.output_tokens, client.unknown_usage_calls), (1, 10, 2, 0))
        path = Path(record["directory"])
        self.assertEqual((path / "events.jsonl").read_bytes(), raw)
        self.assertEqual((path / "stdout.bin").read_bytes(), raw)
        self.assertEqual(json.loads((path / "outcome.json").read_text())["reconnects"], record["reconnects"])
        request = json.loads((path / "request.json").read_text())
        self.assertEqual(request["event_policy"], POLICY_ID)
        self.assertIn('model_provider="tau_http"', request["command"])

    def test_clean_call_has_explicit_empty_recovery_metadata(self):
        client = self.client()
        self.run_fake(client)
        self.assertEqual(client.records[0]["recovery_status"], "clean")
        self.assertEqual(client.records[0]["reconnects"], [])

    def assert_latched(self, client):
        self.assertIsNotNone(client.halt_reason)
        with patch.object(transport, "_run_bounded") as process:
            with self.assertRaises(CliBudgetExceeded):
                client.generate("cannot resume", SCHEMA, role="test")
        process.assert_not_called()

    def test_final_identity_schema_stderr_and_exit_still_reject(self):
        events = stream()
        events.insert(3, recovery())
        variants = [{"final": {"reply": "different"}}, {"stderr": b"unexpected"}, {"code": 1}]
        malformed = deepcopy(events)
        malformed[-2]["item"]["text"] = '{"reply":true}'
        variants.append({"events": malformed, "final": {"reply": True}})
        for variant in variants:
            with self.subTest(variant=variant):
                client = self.client()
                kwargs = {"events": events, **variant}
                with self.assertRaises(CliCallError):
                    self.run_fake(client, **kwargs)
                self.assertEqual(client.calls, 1)
                self.assert_latched(client)

    def test_unknown_error_is_not_recovery(self):
        events = stream()
        events.insert(3, {"type": "error", "message": "usage limit exceeded"})
        client = self.client()
        with self.assertRaises(CliCallError):
            self.run_fake(client, events=events)
        self.assert_latched(client)

    def test_missing_usage_remains_unknown_and_stops(self):
        client = self.client()
        with self.assertRaises(CliCallError):
            self.run_fake(client, events=stream()[:-1])
        self.assertEqual(client.unknown_usage_calls, 1)
        self.assert_latched(client)

    def test_timeout_even_with_complete_stream_is_not_reaccepted(self):
        client = self.client()
        with self.assertRaises(CliCallError):
            self.run_fake(client, timeout=True)
        self.assertEqual(client.records[0]["status"], "timeout")
        self.assertEqual(client.input_tokens, 10)
        self.assert_latched(client)

    def test_budget_is_enforced_after_recovered_call(self):
        client = self.client(max_input_tokens=9)
        events = stream()
        events.insert(3, recovery())
        with self.assertRaises(CliBudgetExceeded):
            self.run_fake(client, events=events)
        self.assertEqual(client.input_tokens, 10)
        self.assert_latched(client)


if __name__ == "__main__":
    unittest.main()
