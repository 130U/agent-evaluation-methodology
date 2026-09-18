"""Bounded Codex CLI text transport; no tau task or optimizer logic.

Uses the user's existing CLI sign-in without opening/copying credentials. The
CLI has no verified generation-token hard cap: limits are checked before and
after calls, and unknown usage permanently stops this client. Known token
counters are subtotals, never a zero-cost claim for failed requests.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

from jsonschema import Draft202012Validator


# Public copy: an unset value is an unusable path, never a searched executable.
EXE = Path(os.environ.get("TAU_CODEX_EXE", "__SET_TAU_CODEX_EXE__"))
EXE_SHA256 = "960c111d47afd61669954b9df9e56083e302edbfa3ef6962d81dcc14a30051dc"
MODEL = "gpt-5.6-sol"
EFFORT = "low"
MODEL_CATALOG_PATH = Path(__file__).resolve().parents[2] / "experiments/cli_model_catalog_sol.json"
MODEL_CATALOG_SHA256 = "cb9176ce010cff15088dbb99147cb46379de5941400ff62368d73594734d35ad"
HOST_DISABLED_WARNING = "Code Mode is unavailable because code-mode host is disabled. Code mode will fail closed; enable `features.code_mode_host` and install `codex-code-mode-host`."
LOCAL_CONFIG_WARNING_PATH = os.environ.get("TAU_CODEX_CONFIG_PATH", "__SET_TAU_CODEX_CONFIG_PATH__")
KNOWN_WARNINGS = frozenset({
    'Under-development features enabled: skip_host_skill_discovery. Under-development features are incomplete and may behave unpredictably. To suppress this warning, set `suppress_unstable_features_warning = true` in ' + LOCAL_CONFIG_WARNING_PATH + ".",
    HOST_DISABLED_WARNING,
})
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "shell_snapshot", "memories", "multi_agent", "multi_agent_v2",
    "goals", "apps", "plugins", "remote_plugin", "hooks", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "computer_use", "skill_mcp_dependency_install", "skill_search",
    "code_mode", "code_mode_host", "view_image", "image_generation", "tool_suggest", "sleep_tool",
    "workspace_dependencies", "recommended_plugins", "in_app_browser", "in_app_local_automation",
)
ENV_ALLOWLIST = frozenset({
    "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
    "LOCALAPPDATA", "PATH", "PATHEXT", "COMSPEC", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
    "USERNAME", "USERDOMAIN", "COMPUTERNAME", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
})
USAGE_KEYS = frozenset({"input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                        "output_tokens", "reasoning_output_tokens"})
TREE_CLEANUP_SECONDS = 0.5


class CliCallError(RuntimeError):
    """A recorded call was rejected; no automatic retry is permitted."""


class CliBudgetExceeded(CliCallError):
    """A call would exceed, or has exceeded, the shared client budget."""


@dataclass(frozen=True)
class CliResult:
    output: dict
    usage: dict
    elapsed_seconds: float
    call_id: str


def _create_windows_process(kernel, command, cwd, env, startup, process_info, creationflags):
    """Single native launch boundary, replaceable by Python-only test fixtures."""
    import ctypes
    environment = ctypes.create_unicode_buffer(
        "\0".join(f"{key}={value}" for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0"
    )
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
    if not kernel.CreateProcessW(str(command[0]), command_line, None, None, True,
                                 creationflags | 0x00080000 | 0x00000400,
                                 environment, str(cwd), ctypes.byref(startup), ctypes.byref(process_info)):
        raise ctypes.WinError(ctypes.get_last_error())


class _WindowsJobProcess:
    """Windows 10+ process tree owned from creation; never uses broad PID killing.

    PROC_THREAD_ATTRIBUTE_JOB_LIST assigns the job before the first thread runs.
    Only the three duplicated stdio handles are inherited. The job handle is not
    inherited, and neither BREAKAWAY_OK nor SILENT_BREAKAWAY_OK is enabled.
    Sources: Microsoft UpdateProcThreadAttribute / Job Objects documentation.
    """

    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt
        self.ctypes, self.wt = ctypes, wt
        self.kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.job = self.process = self.thread = None
        self.pid = None

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wt.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_limit", wt.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wt.DWORD), ("scheduling", wt.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("user_time", "kernel_time", "period_user_time", "period_kernel_time")] + [
                        (name, wt.DWORD) for name in ("page_faults", "total_processes", "active_processes", "terminated_processes")]

        class StartupInfo(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("reserved", wt.LPWSTR), ("desktop", wt.LPWSTR), ("title", wt.LPWSTR)] + [
                (name, wt.DWORD) for name in ("x", "y", "x_size", "y_size", "x_chars", "y_chars", "fill", "flags")] + [
                ("show", wt.WORD), ("reserved_size", wt.WORD), ("reserved_bytes", ctypes.c_void_p),
                ("stdin", wt.HANDLE), ("stdout", wt.HANDLE), ("stderr", wt.HANDLE)]

        class StartupInfoEx(ctypes.Structure):
            _fields_ = [("startup", StartupInfo), ("attributes", ctypes.c_void_p)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [("process", wt.HANDLE), ("thread", wt.HANDLE), ("pid", wt.DWORD), ("tid", wt.DWORD)]

        self.Accounting, self.StartupInfoEx, self.ProcessInfo = Accounting, StartupInfoEx, ProcessInfo
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wt.LPCWSTR], wt.HANDLE),
            "SetInformationJobObject": ([wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD], wt.BOOL),
            "QueryInformationJobObject": ([wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p], wt.BOOL),
            "TerminateJobObject": ([wt.HANDLE, wt.UINT], wt.BOOL),
            "GetCurrentProcess": ([], wt.HANDLE),
            "DuplicateHandle": ([wt.HANDLE, wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.HANDLE), wt.DWORD, wt.BOOL, wt.DWORD], wt.BOOL),
            "InitializeProcThreadAttributeList": ([ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)], wt.BOOL),
            "UpdateProcThreadAttribute": ([ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], wt.BOOL),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
            "CreateProcessW": ([wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
                                ctypes.c_void_p, wt.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p], wt.BOOL),
            "WaitForSingleObject": ([wt.HANDLE, wt.DWORD], wt.DWORD),
            "GetExitCodeProcess": ([wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL),
            "CloseHandle": ([wt.HANDLE], wt.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes, function.restype = arguments, result
        self.job = kernel.CreateJobObjectW(None, None)
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def start(self, command, streams, *, cwd, env, creationflags):
        import msvcrt
        ctypes, kernel, wt = self.ctypes, self.kernel, self.wt
        inherited = []
        attributes = None
        initialized = False
        try:
            current = kernel.GetCurrentProcess()
            for stream in streams:
                duplicate = wt.HANDLE()
                if not kernel.DuplicateHandle(current, msvcrt.get_osfhandle(stream.fileno()), current,
                                               ctypes.byref(duplicate), 0, True, 2):
                    raise ctypes.WinError(ctypes.get_last_error())
                inherited.append(duplicate.value)
            size = ctypes.c_size_t()
            kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            attributes = ctypes.create_string_buffer(size.value)
            if not kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            initialized = True
            handles = (wt.HANDLE * 3)(*inherited)
            jobs = (wt.HANDLE * 1)(self.job)
            for key, value in ((0x00020002, handles), (0x0002000D, jobs)):
                if not kernel.UpdateProcThreadAttribute(attributes, 0, key, value, ctypes.sizeof(value), None, None):
                    raise ctypes.WinError(ctypes.get_last_error())
            startup, process_info = self.StartupInfoEx(), self.ProcessInfo()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.startup.flags = 0x00000100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = inherited
            startup.attributes = ctypes.cast(attributes, ctypes.c_void_p)
            _create_windows_process(kernel, command, cwd, env, startup, process_info, creationflags)
            self.process, self.thread, self.pid = process_info.process, process_info.thread, process_info.pid
        finally:
            if initialized:
                kernel.DeleteProcThreadAttributeList(attributes)
            for handle in inherited:
                kernel.CloseHandle(handle)

    def wait(self, timeout):
        milliseconds = min(0xFFFFFFFE, max(0, math.ceil(timeout * 1000)))
        result = self.kernel.WaitForSingleObject(self.process, milliseconds)
        if result == 0x00000102:
            return None
        if result != 0:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        code = self.wt.DWORD()
        if not self.kernel.GetExitCodeProcess(self.process, self.ctypes.byref(code)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return code.value

    def stop_tree(self):
        started = time.monotonic()
        info = {"method": "windows_job_list_at_creation", "stdio": "regular_files",
                "pid": self.pid, "cleanup_wait_limit_seconds": TREE_CLEANUP_SECONDS,
                "cleanup_complete": False, "active_processes": None}
        if not self.kernel.TerminateJobObject(self.job, 1):
            info["cleanup_error"] = str(self.ctypes.WinError(self.ctypes.get_last_error()))
        else:
            deadline = started + TREE_CLEANUP_SECONDS
            while True:
                accounting = self.Accounting()
                if not self.kernel.QueryInformationJobObject(self.job, 1, self.ctypes.byref(accounting),
                                                             self.ctypes.sizeof(accounting), None):
                    info["cleanup_error"] = str(self.ctypes.WinError(self.ctypes.get_last_error()))
                    break
                info["active_processes"] = accounting.active_processes
                if accounting.active_processes == 0:
                    info["cleanup_complete"] = True
                    break
                if time.monotonic() >= deadline:
                    info["cleanup_error"] = "Owned job did not become empty within cleanup allowance"
                    break
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        info["cleanup_seconds"] = time.monotonic() - started
        return info

    def close(self):
        for attribute in ("thread", "process", "job"):
            handle = getattr(self, attribute, None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, attribute, None)


def _read_snapshot(path):
    """Read a fixed regular-file extent, never wait for a descendant's pipe EOF."""
    with Path(path).open("rb") as stream:
        return stream.read(os.fstat(stream.fileno()).st_size)


def _run_bounded(command, *, input, timeout, cwd, env, shell, creationflags,
                 stdout_path, stderr_path):
    """Windows-only bounded wait plus at most 0.5 s tree-cleanup polling.

    This is not a hard real-time guarantee: OS startup, scheduling and disk I/O
    can overrun. There is no PIPE/communicate drain, unbounded wait, retry, or
    fallback to launching outside the owned job if setup is unsupported.
    """
    if os.name != "nt" or shell:
        raise RuntimeError("This pinned transport requires Windows and shell=False")
    started = time.monotonic()
    job, cause, returncode, cleanup = None, None, None, None
    # Handles stay open until the owned tree has been terminated on every path,
    # including normal parent exit with descendants still running.
    with tempfile.TemporaryFile() as stdin, Path(stdout_path).open("wb") as stdout, Path(stderr_path).open("wb") as stderr:
        stdin.write(input)
        stdin.seek(0)
        try:
            job = _WindowsJobProcess()
            if time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            job.start(command, (stdin, stdout, stderr), cwd=cwd, env=env, creationflags=creationflags)
            remaining = max(0, timeout - (time.monotonic() - started))
            returncode = job.wait(remaining)
            if returncode is None:
                raise subprocess.TimeoutExpired(command, timeout)
        except BaseException as exc:
            cause = exc
        finally:
            if job is not None:
                try:
                    cleanup = job.stop_tree()
                    if not cleanup["cleanup_complete"] and cause is None:
                        cause = RuntimeError(cleanup.get("cleanup_error", "Owned tree cleanup incomplete"))
                except BaseException as exc:
                    cleanup = {"method": "windows_job_list_at_creation", "cleanup_complete": False,
                               "cleanup_error": str(exc)}
                    cause = cause or exc
                finally:
                    job.close()
    raw_stdout, raw_stderr = _read_snapshot(stdout_path), _read_snapshot(stderr_path)
    if cause is not None:
        cause.stdout, cause.stderr, cause.process_cleanup = raw_stdout, raw_stderr, cleanup
        raise cause
    result = subprocess.CompletedProcess(command, returncode, raw_stdout, raw_stderr)
    result.process_cleanup = cleanup
    return result


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Non-finite JSON constant: {value}")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def _write_json(path, value, *, exclusive=False):
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def clean_environment():
    """Mirror the proven smoke allowlist, never copy CODEX/OPENAI variables."""
    return {key: value for key, value in os.environ.items() if key.upper() in ENV_ALLOWLIST}


def configuration(clean):
    """Whole-table permissions are required by the tested alpha CLI parser."""
    if hashlib.sha256(MODEL_CATALOG_PATH.read_bytes()).hexdigest() != MODEL_CATALOG_SHA256:
        raise ValueError("Pinned model catalog changed")
    result = [
        'approval_policy="never"', 'default_permissions="tau-text-only"',
        'web_search="disabled"', 'project_doc_max_bytes=0',
        f'model_reasoning_effort="{EFFORT}"',
        'model_catalog_json=' + json.dumps(MODEL_CATALOG_PATH.as_posix()),
        'permissions={tau-text-only={filesystem={":root"="deny",":minimal"="read",'
        + json.dumps(clean.as_posix()) + '="read"},network={enabled=false}}}',
        'apps._default.enabled=false', 'memories.use_memories=false',
        'memories.generate_memories=false', 'features.skip_host_skill_discovery=true',
    ]
    return result + [f"features.{feature}=false" for feature in DISABLED_FEATURES]


def _validate_schema(schema):
    # Snapshot before validation; callers cannot mutate a pending request.
    copy = _strict_json(json.dumps(schema, allow_nan=False))
    if not isinstance(copy, dict) or copy.get("type") != "object" or copy.get("additionalProperties") is not False:
        raise ValueError("Schema must describe an object with additionalProperties=false")

    def inspect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(child, str) or not child.startswith("#")
                ):
                    raise ValueError("External schema references are prohibited")
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(copy)
    Draft202012Validator.check_schema(copy)
    return copy


def _usage(events):
    completed = [event for event in events if isinstance(event, dict) and event.get("type") == "turn.completed"]
    if len(completed) != 1:
        raise ValueError("Exactly one turn.completed usage record is required")
    usage = completed[0].get("usage")
    if not isinstance(usage, dict) or not {"input_tokens", "output_tokens"} <= usage.keys() or usage.keys() - USAGE_KEYS:
        raise ValueError("Missing or unknown usage fields")
    if any(type(value) is not int or value < 0 for value in usage.values()):
        raise ValueError("Usage values must be nonnegative integers, not booleans")
    if usage.get("cached_input_tokens", 0) > usage["input_tokens"]:
        raise ValueError("Cached tokens exceed input tokens")
    return dict(usage)


def _validate_events(events):
    state = "initial"
    seen_ids, warnings = set(), set()
    answer = None
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Event must be an object")
        kind = event.get("type")
        if kind == "thread.started" and state == "initial":
            if set(event) != {"type", "thread_id"} or not isinstance(event["thread_id"], str) or not event["thread_id"]:
                raise ValueError("Invalid thread.started")
            state = "thread"
        elif kind == "turn.started" and state == "thread" and set(event) == {"type"}:
            state = "turn"
        elif kind == "item.completed" and state in {"thread", "turn"} and set(event) == {"type", "item"}:
            item = event["item"]
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in seen_ids:
                raise ValueError("Invalid or duplicate item ID")
            seen_ids.add(item["id"])
            if item.get("type") == "error" and state == "thread":
                message = item.get("message")
                if set(item) != {"id", "type", "message"} or not isinstance(message, str) or message not in KNOWN_WARNINGS or message in warnings:
                    raise ValueError("Unapproved CLI error/warning")
                warnings.add(message)
            elif item.get("type") == "agent_message" and state == "turn":
                if set(item) != {"id", "type", "text"} or not isinstance(item["text"], str):
                    raise ValueError("Invalid final agent message")
                answer = item["text"]
                state = "answer"
            else:
                raise ValueError("Tool, unknown item, or misplaced warning: " + str(item.get("type")))
        elif kind == "turn.completed" and state == "answer" and set(event) == {"type", "usage"}:
            state = "complete"
        else:
            raise ValueError("Unknown, duplicate, or out-of-order event: " + str(kind))
    if state != "complete" or answer is None:
        raise ValueError("Incomplete event stream")
    if HOST_DISABLED_WARNING not in warnings:
        raise ValueError("Required Code Mode host-disabled startup warning missing")
    return answer, sorted(warnings)


class CliTextClient:
    def __init__(self, root: Path, output: Path, *, model=MODEL, effort=EFFORT,
                 max_calls=100, max_input_tokens=500_000, max_output_tokens=20_000,
                 max_seconds=1800, request_timeout=120):
        if model != MODEL or effort != EFFORT:
            raise ValueError("This transport is pinned to gpt-5.6-sol / low")
        for name, value in {"max_calls": max_calls, "max_input_tokens": max_input_tokens,
                            "max_output_tokens": max_output_tokens}.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name, value in {"max_seconds": max_seconds, "request_timeout": request_timeout}.items():
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        self.root, self.output = Path(root).resolve(), Path(output).resolve()
        if not self.output.is_relative_to(self.root):
            raise ValueError("Persistent audit output must stay inside the research root")
        self.output.mkdir(parents=True, exist_ok=True)
        self.model, self.effort = model, effort
        self.max_calls, self.max_input_tokens, self.max_output_tokens = max_calls, max_input_tokens, max_output_tokens
        self.max_seconds, self.request_timeout = float(max_seconds), float(request_timeout)
        self.records = []
        self.calls = self.input_tokens = self.output_tokens = self.unknown_usage_calls = 0
        self.elapsed_seconds = 0.0
        self.halt_reason = None
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._persist_budget(exclusive=True)

    def build_configuration(self, clean):
        """Per-client configuration hook; prior studies use their frozen source ZIP."""
        return configuration(clean)

    def _persist_budget(self, *, exclusive=False):
        _write_json(self.output / "budget.json", {
            "model": self.model, "effort": self.effort, "exe_sha256": EXE_SHA256,
            "calls_reserved": self.calls, "known_input_tokens": self.input_tokens,
            "known_output_tokens": self.output_tokens, "unknown_usage_calls": self.unknown_usage_calls,
            "call_elapsed_seconds": self.elapsed_seconds, "wall_seconds_since_client_start": time.monotonic() - self.started,
            "halt_reason": self.halt_reason,
            "limits": {"max_calls": self.max_calls, "max_input_tokens": self.max_input_tokens,
                       "max_output_tokens": self.max_output_tokens, "max_seconds": self.max_seconds,
                       "request_timeout": self.request_timeout},
            "generation_token_hard_cap": False,
            "limits_note": "Token limits can overshoot in one request; unknown cost halts, never zero-cost retry.",
        }, exclusive=exclusive)

    def _budget_reason(self, *, before):
        if before and self.calls >= self.max_calls:
            return "max_calls exhausted"
        compare = (lambda total, cap: total >= cap) if before else (lambda total, cap: total > cap)
        if compare(self.input_tokens, self.max_input_tokens):
            return "input token budget exhausted"
        if compare(self.output_tokens, self.max_output_tokens):
            return "output token budget exhausted"
        if time.monotonic() - self.started >= self.max_seconds:
            return "global wall-clock budget exhausted"
        return None

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
                    answer, warnings = _validate_events(events)
                    if final_raw is None:
                        raise ValueError("Missing final output file")
                    parsed = _strict_json(final_raw.decode("utf-8"))
                    # Python equates True with 1; JSON type identity must survive.
                    if not isinstance(parsed, dict) or json.dumps(parsed, sort_keys=True, allow_nan=False) != json.dumps(
                        _strict_json(answer), sort_keys=True, allow_nan=False
                    ):
                        raise ValueError("Final file and event output differ or are not objects")
                    Draft202012Validator(schema).validate(parsed)
                    record.update(output=parsed, warnings=warnings)
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
