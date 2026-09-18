"""Prospective recovered-event transport; previous clients and studies unchanged.

The generate method is a separately versioned copy of the original bounded
method, with only event-policy selection and recovery/accounting metadata added.
Budgets, HTTP configuration, Windows process ownership, raw-byte persistence,
final/event/schema identity, stderr rejection and failure latching are reused.
No retry is initiated by this wrapper; any recovered attempt is internal to CLI.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from jsonschema import Draft202012Validator

from .subscription_client import (
    CliBudgetExceeded, CliCallError, CliResult, EXE, EXE_SHA256,
    MODEL_CATALOG_SHA256, _file_hash, _run_bounded, _strict_json, _usage,
    _validate_schema, _write_json, clean_environment, TREE_CLEANUP_SECONDS,
)
from .subscription_http_client import HttpCliTextClient
from .recovered_events import POLICY_ID, validate_recovered_events

ORIGINAL_GENERATE_SOURCE_SHA256 = "235f7484271c0f008b6164aaaa845b3dc9ab1eb1515e6c46b26ab64450db06d3"


class RecoveringHttpCliTextClient(HttpCliTextClient):
    def generate(self, prompt: str, schema: dict, *, role: str) -> CliResult:
        with self._lock:
            if self.halt_reason:
                raise CliBudgetExceeded("Client halted: " + self.halt_reason)
            reason = self._budget_reason(before=True)
            if reason:
                self.halt_reason = reason
                self._persist_budget()
                raise CliBudgetExceeded(reason)
            if not isinstance(prompt, str) or not prompt.strip() or not isinstance(role, str) or not role.strip():
                raise ValueError("Nonempty prompt and role are required")
            schema = _validate_schema(schema)
            call_id = f"{self.calls + 1:06d}-{uuid.uuid4().hex}"
            directory = self.output / call_id
            directory.mkdir(exist_ok=False)
            clean = Path(tempfile.mkdtemp(prefix="tau-cli-text-"))
            schema_path, final_path = clean / "response.schema.json", clean / "final.json"
            settings = self.build_configuration(clean)
            command = [str(EXE), "exec", "--model", self.model, "--ignore-user-config", "--strict-config",
                       "--ephemeral", "--skip-git-repo-check", "--cd", str(clean), "--json", "--color", "never",
                       "--output-schema", str(schema_path), "--output-last-message", str(final_path)]
            for setting in settings:
                command += ["-c", setting]
            command.append("-")
            timeout = min(self.request_timeout, self.max_seconds - (time.monotonic() - self.started))
            record = {"call_id": call_id, "role": role, "status": "reserved", "model": self.model,
                      "model_catalog_sha256": MODEL_CATALOG_SHA256,
                      "effort": self.effort, "directory": str(directory), "clean_directory": str(clean),
                      "usage": None, "usage_complete": False, "invocation_started": False,
                      "generation_token_hard_cap": False, "timeout_seconds": timeout,
                      "process_control": "windows_job_list_at_creation; regular-file stdio; kill owned tree on all exits",
                      "cleanup_wait_limit_seconds": TREE_CLEANUP_SECONDS,
                      "timeout_note": "Execution wait is bounded; OS startup/scheduling/disk I/O are not hard real-time."}
            record.update(event_policy=POLICY_ID,
                              reported_usage_scope="CLI reported tokens; provider requests and reconnect overhead are not independently observed")
            self.calls += 1
            self.records.append(record)
            raw_stdout = raw_stderr = b""
            exit_code, cause, final_raw = None, None, None
            started = time.monotonic()
            try:
                _write_json(directory / "request.json", {**record, "prompt": prompt, "schema": schema,
                    "command": command, "exe_sha256": EXE_SHA256,
                    "environment_policy": "OS/runtime/proxy allowlist; no inherited CODEX/OPENAI variables; no credential copy"}, exclusive=True)
                (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
                _write_json(directory / "schema.json", schema, exclusive=True)
                _write_json(schema_path, schema, exclusive=True)
                self._persist_budget()
                if _file_hash(EXE) != EXE_SHA256:
                    raise ValueError("CLI executable SHA256 differs from the pinned binary")
                timeout = min(timeout, self.max_seconds - (time.monotonic() - self.started))
                if timeout <= 0:
                    raise CliBudgetExceeded("Global deadline reached before subprocess start")
                record["invocation_started"] = True
                process = _run_bounded(command, input=prompt.encode("utf-8"),
                    timeout=timeout, cwd=clean, env=clean_environment(), shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    stdout_path=directory / "stdout.bin", stderr_path=directory / "stderr.txt")
                raw_stdout, raw_stderr, exit_code = process.stdout, process.stderr, process.returncode
                record["process_cleanup"] = getattr(process, "process_cleanup", None)
            except subprocess.TimeoutExpired as exc:
                raw_stdout, raw_stderr, cause = exc.stdout or b"", exc.stderr or b"", exc
                record["process_cleanup"] = getattr(exc, "process_cleanup", None)
            except BaseException as exc:
                cause = exc
                raw_stdout, raw_stderr = getattr(exc, "stdout", None) or b"", getattr(exc, "stderr", None) or b""
                record["process_cleanup"] = getattr(exc, "process_cleanup", None)
            elapsed = time.monotonic() - started
            self.elapsed_seconds += elapsed
            # Persist every byte, including partial timeout output, before interpretation.
            try:
                (directory / "stdout.bin").write_bytes(raw_stdout)
                (directory / "events.jsonl").write_bytes(raw_stdout)
                (directory / "stderr.txt").write_bytes(raw_stderr)
                if final_path.exists():
                    final_raw = final_path.read_bytes()
                    (directory / "final.json").write_bytes(final_raw)
                events, parse_errors = [], []
                for index, line in enumerate(raw_stdout.splitlines()):
                    try:
                        events.append(_strict_json(line.decode("utf-8")))
                    except Exception as exc:
                        parse_errors.append({"line": index + 1, "error": str(exc)})
                _write_json(directory / "events.parsed.json", {"events": events, "parse_errors": parse_errors})
                try:
                    usage = _usage(events)
                    record.update(usage=usage, usage_complete=not parse_errors)
                    self.input_tokens += usage["input_tokens"]
                    self.output_tokens += usage["output_tokens"]
                except ValueError as exc:
                    self.unknown_usage_calls += 1
                    record["usage_error"] = str(exc)
                    if cause is None:
                        cause = exc
                if parse_errors:
                    if record["usage"] is not None:
                        self.unknown_usage_calls += 1
                    cause = cause or ValueError("Malformed JSONL event stream")
                if cause is None:
                    if exit_code != 0:
                        raise ValueError(f"CLI exit code {exit_code}")
                    if raw_stderr.strip():
                        raise ValueError("Unapproved stderr output")
                    answer, warnings, reconnects = validate_recovered_events(events)
                    if final_raw is None:
                        raise ValueError("Missing final output file")
                    parsed = _strict_json(final_raw.decode("utf-8"))
                    # Python equates True with 1; JSON type identity must survive.
                    if not isinstance(parsed, dict) or json.dumps(parsed, sort_keys=True, allow_nan=False) != json.dumps(
                        _strict_json(answer), sort_keys=True, allow_nan=False
                    ):
                        raise ValueError("Final file and event output differ or are not objects")
                    Draft202012Validator(schema).validate(parsed)
                    record.update(output=parsed, warnings=warnings, reconnects=reconnects,
                                      recovery_status="recovered" if reconnects else "clean")
                    reason = self._budget_reason(before=False)
                    if reason:
                        raise CliBudgetExceeded(reason)
            except BaseException as exc:
                cause = cause or exc
            if record["usage"] is None and "usage_error" not in record:
                self.unknown_usage_calls += 1
                record["usage_error"] = "Usage unavailable after audit/transport failure"
            record.update(elapsed_seconds=elapsed, exit_code=exit_code, final_present=final_raw is not None)
            if cause is not None:
                self.halt_reason = f"{type(cause).__name__}: {cause}"
                record.update(status="timeout" if isinstance(cause, subprocess.TimeoutExpired) else "rejected",
                              error=self.halt_reason)
            else:
                record["status"] = "complete"
            try:
                _write_json(directory / "outcome.json", record)
                self._persist_budget()
            except BaseException as exc:
                self.halt_reason = f"Audit persistence failure: {exc}"
                raise CliCallError(self.halt_reason) from exc
            if cause is not None:
                if isinstance(cause, (KeyboardInterrupt, SystemExit)):
                    raise cause
                error_class = CliBudgetExceeded if isinstance(cause, CliBudgetExceeded) else CliCallError
                raise error_class(f"{call_id}: {self.halt_reason}; artifacts: {directory}") from cause
            return CliResult(record["output"], record["usage"], elapsed, call_id)
