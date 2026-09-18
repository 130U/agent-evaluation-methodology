"""Local adapter contracts only: fake transport, no server or model is started."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from tau_feedback.local_backend import LocalBudgetExceeded, LocalGenerationBackend
from tau_feedback.local_runtime import LocalRuntime
from tau2.data_model.message import SystemMessage, UserMessage


def response(content="OK", *, tokens=2, model="tau-local-qwen", finish="stop"):
    return {"model": model, "choices": [{"finish_reason": finish,
            "message": {"role": "assistant", "content": content}}],
            "usage": {"completion_tokens": tokens, "prompt_tokens": 7}}


class FakeRuntime:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def request(self, path, payload, timeout=None):
        self.requests.append((path, deepcopy(payload), timeout))
        output = next(self.outputs)
        if isinstance(output, BaseException):
            raise output
        return deepcopy(output)


class LocalBackendReview(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.log = Path(self.temporary.name) / "calls.jsonl"
        self.messages = [SystemMessage(role="system", content="Act as a retail assistant."),
                         UserMessage(role="user", content="Help me.")]

    def backend(self, outputs, **kwargs):
        runtime = FakeRuntime(outputs)
        return LocalGenerationBackend(runtime, output=self.log, **kwargs), runtime

    def test_unconfigured_model_rejected_before_transport(self):
        backend, runtime = self.backend([])
        with self.assertRaises(ValueError):
            backend.generate("unconfigured/remote", self.messages)
        self.assertEqual(runtime.requests, [])

    def test_normal_output_uses_real_response_content_and_counts_usage(self):
        backend, runtime = self.backend([response("MODEL_OUTPUT", tokens=3)])
        result = backend.generate(backend.MODEL, self.messages, max_tokens=4, call_name="agent")
        self.assertEqual(result.content, "MODEL_OUTPUT")
        self.assertEqual((backend.generated_tokens, backend.prompt_tokens), (3, 7))
        self.assertEqual(runtime.requests[0][0], "/v1/chat/completions")
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_roles_do_not_carry_messages_across_calls(self):
        backend, runtime = self.backend([response(), response()])
        user_messages = [SystemMessage(role="system", content="USER_ONLY_HIDDEN_PLAN"),
                         UserMessage(role="user", content="Simulate the next user turn.")]
        backend.generate(backend.MODEL, user_messages, call_name="user", max_tokens=4)
        backend.generate(backend.MODEL, self.messages, call_name="agent", max_tokens=4)
        self.assertNotIn("USER_ONLY_HIDDEN_PLAN", json.dumps(runtime.requests[1][1]))
        self.assertEqual([r["role"] for r in backend.records], ["user", "agent"])

    def test_native_tool_arguments_preserve_exact_identifier(self):
        output = response(content=None, finish="tool_calls")
        output["choices"][0]["message"]["tool_calls"] = [{"id": "test-call", "type": "function",
            "function": {"name": "lookup", "arguments": '{"order_id":"#PROBE17"}'}}]
        backend, _ = self.backend([output])
        result = backend.generate(backend.MODEL, self.messages, max_tokens=64)
        self.assertEqual(result.tool_calls[0].arguments["order_id"], "#PROBE17")

    def test_truncation_is_error_but_usage_and_failure_are_recorded(self):
        backend, _ = self.backend([response("partial", tokens=4, finish="length")])
        with self.assertRaises(RuntimeError):
            backend.generate(backend.MODEL, self.messages, max_tokens=4)
        self.assertEqual(backend.generated_tokens, 4)
        row = json.loads(self.log.read_text())
        self.assertIn("error_type", row)
        self.assertEqual(row["response"]["choices"][0]["finish_reason"], "length")

    def test_failed_transport_consumes_call_budget(self):
        backend, runtime = self.backend([TimeoutError("unit timeout"), response()], max_calls=1)
        with self.assertRaises(TimeoutError):
            backend.generate(backend.MODEL, self.messages, max_tokens=4)
        with self.assertRaises(LocalBudgetExceeded):
            backend.generate(backend.MODEL, self.messages, max_tokens=4)
        self.assertEqual(len(runtime.requests), 1)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_missing_usage_must_not_be_free_generation(self):
        output = response(); output.pop("usage")
        backend, runtime = self.backend([output, response()], max_generated_tokens=4)
        try:
            backend.generate(backend.MODEL, self.messages, max_tokens=4)
        except (RuntimeError, ValueError, LocalBudgetExceeded):
            pass
        with self.assertRaises((LocalBudgetExceeded, RuntimeError)):
            backend.generate(backend.MODEL, self.messages, max_tokens=4)
        self.assertEqual(len(runtime.requests), 1)

    def test_timed_out_generation_reserves_unknown_tokens(self):
        backend, runtime = self.backend([TimeoutError("unit timeout"), response()], max_generated_tokens=4)
        with self.assertRaises(TimeoutError):
            backend.generate(backend.MODEL, self.messages, max_tokens=4)
        with self.assertRaises(LocalBudgetExceeded):
            backend.generate(backend.MODEL, self.messages, max_tokens=4)
        self.assertEqual(len(runtime.requests), 1)

    def test_negative_reported_usage_cannot_reduce_budget(self):
        backend, _ = self.backend([response(tokens=-4)])
        with self.assertRaises((RuntimeError, ValueError)):
            backend.generate(backend.MODEL, self.messages, max_tokens=4)
        self.assertGreaterEqual(backend.generated_tokens, 0)

    def test_different_response_model_not_silently_accepted(self):
        backend, _ = self.backend([response(model="unconfigured-model")])
        with self.assertRaises((RuntimeError, ValueError)):
            backend.generate(backend.MODEL, self.messages, max_tokens=4)

    def test_negative_max_tokens_not_sent_to_runtime(self):
        backend, runtime = self.backend([response()])
        with self.assertRaises((RuntimeError, ValueError)):
            backend.generate(backend.MODEL, self.messages, max_tokens=-1)
        self.assertEqual(runtime.requests, [])

    def test_remote_style_kwargs_not_silently_discarded(self):
        backend, runtime = self.backend([response()])
        # Unknown kwargs can silently change the declared experiment. Rejecting
        # remote configuration also makes lack of fallback explicit.
        with self.assertRaises((RuntimeError, ValueError)):
            backend.generate(backend.MODEL, self.messages, max_tokens=4,
                             api_base="https://example.invalid", api_key="UNIT-TEST-DUMMY")
        self.assertEqual(runtime.requests, [])

    def test_generate_patch_is_restored_after_exception(self):
        import tau2.utils.llm_utils as llm_utils
        original = llm_utils.generate
        backend, _ = self.backend([])
        with self.assertRaisesRegex(RuntimeError, "deliberate"):
            with backend:
                self.assertEqual(llm_utils.generate, backend.generate)
                raise RuntimeError("deliberate")
        self.assertIs(llm_utils.generate, original)


class LocalRuntimeReview(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_absolute_remote_path_is_refused(self):
        runtime = LocalRuntime(self.root)
        runtime.process = Mock(); runtime.process.poll.return_value = None
        with self.assertRaises(ValueError):
            runtime.request("https://example.invalid/")

    def test_owned_process_is_required(self):
        runtime = LocalRuntime(self.root)
        with self.assertRaises(RuntimeError):
            runtime.request("/health")

    def test_cross_origin_redirect_is_blocked_by_configured_opener(self):
        runtime = LocalRuntime(self.root)
        redirect = next((h for h in runtime.opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)), None)
        if redirect is None:
            return
        request = urllib.request.Request("http://127.0.0.1:12345/health",
                                         headers={"Authorization": "Bearer UNIT-TEST-DUMMY"})
        try:
            redirected = redirect.redirect_request(request, None, 302, "Found", {}, "https://example.invalid/")
        except (urllib.error.HTTPError, ValueError, RuntimeError):
            return
        self.assertIsNone(redirected, "Configured opener allows a request to leave loopback")

    def test_cleanup_escalates_to_kill_then_closes_log(self):
        runtime = LocalRuntime(self.root)
        runtime.process = Mock(); runtime.process.poll.return_value = None
        runtime.process.wait.side_effect = [subprocess.TimeoutExpired("unit", 10), 0]
        runtime.log_stream = io.StringIO()
        runtime.__exit__(None, None, None)
        runtime.process.terminate.assert_called_once()
        runtime.process.kill.assert_called_once()
        self.assertTrue(runtime.log_stream.closed)

    def test_log_closes_even_if_process_cleanup_raises(self):
        runtime = LocalRuntime(self.root)
        runtime.process = Mock(); runtime.process.poll.return_value = None
        runtime.process.terminate.side_effect = OSError("unit cleanup failure")
        runtime.log_stream = io.StringIO()
        try:
            runtime.__exit__(None, None, None)
        except OSError:
            pass
        self.assertTrue(runtime.log_stream.closed, "Log remains open on cleanup error")

    def assets(self, content=b"good"):
        (self.root / "experiments").mkdir()
        (self.root / "models").mkdir()
        executable_dir = self.root / "vendor/llama.cpp/unit-version"
        executable_dir.mkdir(parents=True)
        (executable_dir / "llama-server.exe").write_bytes(b"unit-placeholder-never-executed")
        (self.root / "models/unit.gguf").write_bytes(content)
        plan = {"model": {"file": "unit.gguf", "size": 4, "sha256": hashlib.sha256(b"good").hexdigest()},
                "runtime": {"release": "unit-version"}}
        (self.root / "experiments/local_model_assets.json").write_text(json.dumps(plan))

    def test_same_size_changed_model_rejected_before_process_start(self):
        self.assets(content=b"evil")
        runtime = LocalRuntime(self.root)
        process = Mock(); process.poll.return_value = None
        with patch("tau_feedback.local_runtime.psutil.virtual_memory", return_value=SimpleNamespace(available=4*1024**3)), \
             patch("tau_feedback.local_runtime.socket.socket") as sock, \
             patch("tau_feedback.local_runtime.subprocess.Popen", return_value=process) as start, \
             patch.object(runtime, "request", return_value={"status": "ok"}):
            sock.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 12345)
            try:
                with self.assertRaises(RuntimeError):
                    runtime.__enter__()
                start.assert_not_called()
            finally:
                runtime.__exit__(None, None, None)

    def test_key_is_environment_only_and_runtime_is_restricted(self):
        self.assets()
        runtime = LocalRuntime(self.root)
        process = Mock(); process.poll.return_value = None
        with patch("tau_feedback.local_runtime.psutil.virtual_memory", return_value=SimpleNamespace(available=4*1024**3)), \
             patch("tau_feedback.local_runtime.socket.socket") as sock, \
             patch("tau_feedback.local_runtime.subprocess.Popen", return_value=process) as start, \
             patch.object(runtime, "request", return_value={"status": "ok"}):
            sock.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 12345)
            try:
                runtime.__enter__()
                self.assertTrue(runtime.api_key not in " ".join(runtime.command), "Auth secret appeared in command")
                self.assertTrue(start.call_args.kwargs["env"].get("LLAMA_API_KEY") == runtime.api_key,
                                "Environment authentication not installed")
                for flag in ("--offline", "--no-agent", "--no-webui"):
                    self.assertIn(flag, runtime.command)
                self.assertEqual(runtime.command[runtime.command.index("--host") + 1], "127.0.0.1")
            finally:
                runtime.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
