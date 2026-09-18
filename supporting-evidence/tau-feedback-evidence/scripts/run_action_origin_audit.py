"""E4 post-unblinding source audit and initialization-only intervention check.

Creates results/action_origin/e4-v1 exactly once. The plan/source hashes are
written before any classification or orchestrator construction. No CLI client,
run(), step(), model response or original research artifact is modified.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

N1_MANIFEST = "experiments/N1_R1_MANIFEST.json"
ORCHESTRATOR = "vendor/tau2/src/tau2/orchestrator/orchestrator.py"
DESTINATION = "results/action_origin/e4-v1"
TASK_IDS = ["0", "22", "24", "34", "48", "59", "73", "75", "76", "81", "84", "85"]
STRATEGIES = {
    "unmodified": None,
    "g2_s0": "Follow the retail policy. Help the authenticated customer complete their request using the available tools, and accurately communicate the outcome.",
    "request_authentication_first": "At the start of the conversation, request the user’s account email before giving a general greeting.",
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def history_count(task):
    initial = task.initial_state
    return len(initial.message_history or []) if initial is not None else 0


def prepare_plan(root):
    """Verify original cohort/source bindings, without classifying messages."""
    from tau_feedback.action_origin import ORCHESTRATOR_SHA256
    from tau_feedback.contracts import canonical_hash
    from tau_feedback.n1_audit import verify_manifest, within, INTERRUPTED_STAGE
    from tau2.domains.retail.environment import get_tasks
    manifest, manifest_hash, _ = verify_manifest(root, N1_MANIFEST)
    if manifest["stage"] != INTERRUPTED_STAGE or len(manifest["units"]) != 18:
        raise ValueError("E4 requires the original bound 18-completer N1-R1 cohort")
    tasks = {task.id: task for task in get_tasks("train")}
    if sorted({unit["task_id"] for unit in manifest["units"]}, key=int) != TASK_IDS:
        raise ValueError("E4 must retain the twelve previously used development tasks")
    if sha(root / ORCHESTRATOR) != ORCHESTRATOR_SHA256:
        raise ValueError("Current official orchestrator does not match the origin rule")
    source_names = {
        "scripts/run_action_origin_audit.py", "src/tau_feedback/action_origin.py", "src/tau_feedback/contracts.py",
        "src/tau_feedback/subscription_backend.py", "src/tau_feedback/subscription_episode.py", "src/tau_feedback/n1_audit.py",
        "requirements.lock", "experiments/runtime_sources.json", N1_MANIFEST,
        str(Path(N1_MANIFEST).with_suffix(".sha256")).replace("\\", "/"),
        manifest["baseline_manifest_path"], manifest["stop_review_path"], manifest["baseline_source_snapshot_path"],
    }
    # Current original runtime files, including the task/DB/policy sources, are
    # already checked against N1's frozen SHA256s; retain those bindings in E4.
    source_names.update(name for name in manifest["source_and_input_sha256"] if name.startswith("vendor/tau2/"))
    stop = load(root / manifest["stop_review_path"])
    source_names.update(stop["evidence_hashes"])
    for unit in manifest["units"]:
        payload_hash = canonical_hash(tasks[unit["task_id"]].model_dump(mode="json"))
        if payload_hash != unit["task_sha256"]:
            raise ValueError("Task payload differs from its completed original episode")
    plan = {"stage": "E4_post_unblinding_action_origin_and_prompt_scope", "version": "e4-v1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "prospective_status": "Plan fixed before this controlled audit; hypothesis motivated by post-N1 unblinding, not a pre-N1 hypothesis",
        "original_n1_manifest_sha256": manifest_hash,
        "parent_population": {"planned": 24, "complete_audit_sample": 18, "interrupted_ungraded": 1, "not_run": 5},
        "sampling_rule": "All original-order 18 complete trajectories; incomplete unit is only retained in parent accounting, never an action sample",
        "units": [{key: unit[key] for key in ("unit_id", "task_id", "simulation_seed", "simulation_path", "simulation_id", "task_sha256")}
                  for unit in manifest["units"]],
        "initialization_task_ids": TASK_IDS, "strategies": STRATEGIES, "initializations_planned": 36,
        "initialization_config": {"domain": "retail", "agent": "llm_agent", "user": "user_simulator",
            "seed": 43, "max_steps": 40, "max_errors": 6, "timeout": 900,
            "enforce_communication_protocol": True, "auto_review": False, "hallucination_retries": 0},
        "task_payload_sha256": {tid: canonical_hash(tasks[tid].model_dump(mode="json")) for tid in TASK_IDS},
        "task_initial_history_counts": {tid: history_count(tasks[tid]) for tid in TASK_IDS},
        "intervention": "Only append StrategyAgent.system_prompt exactly as the existing subscription episode runner; do not modify the harness",
        "zero_model_call_rule": "Fake client raises/counts every generate; SubscriptionGenerationBackend patches generation; run and step are forbidden",
        "initialization_origin_limit": "No synthetic call ledger is invented. Empty-call classify_action_origin can be unknown; direct pinned initialize execution is separate source evidence.",
        "comparison_rule": "Compare entire initialized trajectory after removing only message timestamp fields; also report text, system prompt hashes, and direct initializer provenance",
        "inference_limit": "Controlled system-prompt scope and recorded-action attribution, not Agent/GEPA improvement, natural error frequency, or a rerun of N1/G2",
        "source_sha256": {name: sha(within(root, name)) for name in sorted(source_names)}}
    return plan, manifest, tasks


def audit_recorded_actions(root, manifest, tasks, output):
    from tau_feedback.action_origin import OriginContext, ORCHESTRATOR_SHA256, classify_action_origin, strategy_feedback_eligibility
    from tau_feedback.contracts import canonical_hash, strict_json
    from tau2.data_model.simulation import TextRunConfig
    from tau2.registry import registry
    units = []
    for unit in manifest["units"]:
        simulation_path = root / unit["simulation_path"]
        episode = simulation_path.parent
        request = load(episode / "request.json")
        config = TextRunConfig.model_validate(request["config"])
        solo = registry.get_agent_metadata(config.effective_agent, "solo_mode", default=False)
        if (config.domain != "retail" or config.effective_agent != "llm_agent" or config.user != "user_simulator"
            or solo is not False or request.get("strategy") is not None):
            raise ValueError("Original episode is not the registered non-solo baseline")
        task = tasks[unit["task_id"]]
        context = OriginContext(ORCHESTRATOR_SHA256, solo, history_count(task))
        calls_path = episode / "calls.jsonl"
        calls = [strict_json(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        simulation = load(simulation_path)
        messages = simulation["messages"]
        if (simulation["id"] != unit["simulation_id"] or simulation["task_id"] != unit["task_id"]
            or messages != load(episode / "trajectory.json")):
            raise ValueError("Original message identity differs after source preflight")
        rows = []
        for index, message in enumerate(messages):
            if message.get("role") != "assistant":
                continue
            origin = classify_action_origin(message, calls, context)
            raw = message.get("raw_data")
            claimed_id = raw.get("cli_call_id") if isinstance(raw, dict) else None
            matches = [i for i, call in enumerate(calls) if isinstance(call, dict) and claimed_id is not None and call.get("cli_call_id") == claimed_id]
            rows.append({"message_index": index, "turn_idx": message.get("turn_idx"),
                "message_sha256": canonical_hash(message), "content": message.get("content"),
                "tool_calls": message.get("tool_calls"), "claimed_cli_call_id": claimed_id,
                "matching_call_rows": matches, "matching_call_sha256": [canonical_hash(calls[i]) for i in matches],
                **origin, "feedback_routing": strategy_feedback_eligibility(origin)})
        call_rows = [{"ledger_row": i, "call_index": call.get("call_index"), "role": call.get("role"),
            "cli_call_id": call.get("cli_call_id"), "call_sha256": canonical_hash(call),
            "assistant_message_indices": [row["message_index"] for row in rows if i in row["matching_call_rows"]]}
            for i, call in enumerate(calls)]
        result = {"unit_id": unit["unit_id"], "task_id": unit["task_id"], "simulation_id": simulation["id"],
            "context": asdict(context), "calls_file_sha256": sha(calls_path),
            "simulation_file_sha256": sha(simulation_path), "request_file_sha256": sha(episode / "request.json"),
            "assistant_message_count": len(rows), "origin_counts": dict(Counter(row["origin"] for row in rows)),
            "assistant_messages": rows, "original_calls": call_rows}
        write_new(output / (unit["unit_id"] + ".json"), result)
        units.append(result)
    return units


class ForbiddenClient:
    """No real client or provider is constructed; attempted generation is fatal."""
    def __init__(self):
        self.attempts = 0

    def generate(self, *args, **kwargs):
        self.attempts += 1
        raise AssertionError("E4 initialization must never invoke a model")


def without_timestamps(messages):
    return [{key: value for key, value in message.items() if key != "timestamp"} for message in messages]


def initialization_checks(tasks, output):
    from tau_feedback.action_origin import OriginContext, ORCHESTRATOR_SHA256, classify_action_origin
    from tau_feedback.contracts import canonical_hash
    from tau_feedback.subscription_backend import SubscriptionGenerationBackend
    from tau2.agent.llm_agent import LLMAgent
    from tau2.data_model.simulation import TextRunConfig
    from tau2.orchestrator.orchestrator import Orchestrator, DEFAULT_FIRST_AGENT_MESSAGE
    from tau2.runner.build import build_text_orchestrator
    from loguru import logger
    logger.remove()
    rows, summaries = [], []
    forbidden_run_step = 0
    def forbid_simulation(*args, **kwargs):
        nonlocal forbidden_run_step
        forbidden_run_step += 1
        raise AssertionError("E4 permits initialize only; run/step are forbidden")
    for tid in TASK_IDS:
        variants = []
        for label, strategy in STRATEGIES.items():
            directory = output / f"task-{tid}-{label}"
            directory.mkdir(exist_ok=False)
            client = ForbiddenClient()
            backend = SubscriptionGenerationBackend(client, output=directory / "unexpected_generation_calls.jsonl")
            record = {"task_id": tid, "strategy_label": label, "strategy": strategy, "status": "started"}
            try:
                config = TextRunConfig(domain="retail", agent="llm_agent", user="user_simulator",
                    llm_agent=backend.MODEL, llm_user=backend.MODEL, llm_args_agent={}, llm_args_user={},
                    max_steps=40, max_errors=6, timeout=900, seed=43,
                    enforce_communication_protocol=True, auto_review=False, hallucination_retries=0)
                with patch.object(Orchestrator, "run", side_effect=forbid_simulation), \
                     patch.object(Orchestrator, "step", side_effect=forbid_simulation), backend:
                    orchestrator = build_text_orchestrator(config, tasks[tid], seed=43,
                        simulation_id=f"e4-task-{tid}-{label}")
                    base_prompt = orchestrator.agent.system_prompt
                    if strategy is not None:
                        class StrategyAgent(LLMAgent):
                            @property
                            def system_prompt(self):
                                return super().system_prompt + "\n<reusable_strategy>\n" + strategy + "\n</reusable_strategy>"
                        orchestrator.agent = StrategyAgent(tools=orchestrator.environment.get_tools(),
                            domain_policy=orchestrator.environment.get_policy(), llm=backend.MODEL, llm_args={})
                        orchestrator.agent.set_seed(43)
                    expected = base_prompt if strategy is None else base_prompt + "\n<reusable_strategy>\n" + strategy + "\n</reusable_strategy>"
                    if orchestrator.solo_mode is not False or orchestrator.agent.system_prompt != expected:
                        raise AssertionError("Initialization context or exact prompt append differs")
                    orchestrator.initialize()
                    system_messages = orchestrator.agent_state.system_messages
                    if len(system_messages) != 1 or system_messages[0].content != expected:
                        raise AssertionError("Initialized agent state did not receive the intended system prompt")
                    messages = [m.model_dump(mode="json") for m in orchestrator.get_trajectory()]
                if client.attempts or backend.records or forbidden_run_step:
                    raise AssertionError("Generation/run/step was attempted during initialization")
                initial_count = history_count(tasks[tid])
                context = OriginContext(ORCHESTRATOR_SHA256, False, initial_count)
                normalized = without_timestamps(messages)
                constant = DEFAULT_FIRST_AGENT_MESSAGE.model_dump(mode="json")
                # get_trajectory may attach turn indices; both fields are
                # irrelevant to comparing the inserted message with its source.
                same_constant = len(messages) == 1 and all(messages[0].get(k) == value
                    for k, value in constant.items() if k not in {"timestamp", "turn_idx"})
                direct_origin = ("observed_pinned_framework_initializer" if initial_count == 0 and same_constant
                                 else "observed_task_initial_history" if initial_count > 0 else "unknown")
                record.update(status="complete", config=config.model_dump(mode="json"),
                    context=asdict(context), initial_trajectory=messages,
                    normalized_trajectory_sha256=canonical_hash(normalized),
                    initial_texts=[m.get("content") for m in messages],
                    base_system_prompt_sha256=canonical_hash(base_prompt), system_prompt_sha256=canonical_hash(expected),
                    prompt_changed_from_base=expected != base_prompt, initialized_state_prompt_verified=True,
                    direct_execution_origin=direct_origin, inserted_message_matches_official_constant=same_constant,
                    empty_call_classifier_results=[classify_action_origin(m, [], context) for m in messages if m.get("role") == "assistant"],
                    source_evidence="Direct pinned initialize execution, verified non-solo/task history, forbidden generation, exact constant comparison; no fabricated first-call ledger")
                variants.append(record)
                rows.append(record)
            except BaseException as exc:
                record.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
                raise
            finally:
                record.update(model_generation_attempts=client.attempts, backend_call_records=len(backend.records),
                              actual_model_calls=0, forbidden_run_step_attempts=forbidden_run_step)
                write_new(directory / "result.json", record)
        summaries.append({"task_id": tid, "initializations": len(variants),
            "all_initial_trajectories_equal_ignoring_timestamp": len({r["normalized_trajectory_sha256"] for r in variants}) == 1,
            "all_initial_texts_equal": all(r["initial_texts"] == variants[0]["initial_texts"] for r in variants),
            "all_direct_origins_equal": len({r["direct_execution_origin"] for r in variants}) == 1,
            "three_distinct_system_prompts": len({r["system_prompt_sha256"] for r in variants}) == 3,
            "all_initialized_state_prompts_verified": all(r["initialized_state_prompt_verified"] for r in variants)})
    return rows, summaries


def main():
    destination = ROOT / DESTINATION
    if destination.exists():
        raise FileExistsError("E4 destination already exists; no overwrite/retry")
    plan, manifest, tasks = prepare_plan(ROOT)
    destination.mkdir(parents=True, exist_ok=False)
    write_new(destination / "PLAN.json", plan)
    with (destination / "PLAN.sha256").open("x", encoding="utf-8") as stream:
        stream.write(sha(destination / "PLAN.json") + "  PLAN.json\n")
    (destination / "recorded_actions").mkdir()
    (destination / "initialization").mkdir()
    result = {"stage": plan["stage"], "status": "started", "plan_sha256": sha(destination / "PLAN.json"),
              "actual_model_calls": 0, "new_agent_episodes": 0, "raw_n1_labels_and_g2_unchanged": True}
    try:
        units = audit_recorded_actions(ROOT, manifest, tasks, destination / "recorded_actions")
        write_new(destination / "RECORDED_ACTIONS_SUMMARY.json", {
            "complete_trajectory_denominator": len(units), "original_planned_denominator": 24,
            "task_denominator": len({row["task_id"] for row in units}),
            "assistant_message_denominator": sum(row["assistant_message_count"] for row in units),
            "origin_counts": dict(sum((Counter(row["origin_counts"]) for row in units), Counter())),
            "unknown_policy": "Unknown origin remains unknown; neither model ownership nor non-agent ownership is inferred",
            "inference_limit": plan["inference_limit"]})
        rows, comparisons = initialization_checks(tasks, destination / "initialization")
        write_new(destination / "INITIALIZATION_COMPARISONS.json", comparisons)
        # Recheck every bound byte before declaring completion.
        for name, expected in plan["source_sha256"].items():
            if sha(ROOT / name) != expected:
                raise ValueError("Source changed during controlled audit: " + name)
        result.update(status="complete", completed_trajectory_audits=len(units),
            completed_initializations=len(rows), planned_initializations=36,
            tasks_with_equal_initial_trajectory=sum(r["all_initial_trajectories_equal_ignoring_timestamp"] for r in comparisons),
            tasks_with_equal_initial_origin=sum(r["all_direct_origins_equal"] for r in comparisons),
            tasks_with_three_distinct_system_prompts=sum(r["three_distinct_system_prompts"] for r in comparisons),
            model_generation_attempts=sum(r["model_generation_attempts"] for r in rows),
            backend_call_records=sum(r["backend_call_records"] for r in rows),
            inference_limit=plan["inference_limit"])
    except BaseException as exc:
        result.update(status="stopped", error_type=type(exc).__name__, error=str(exc), no_retry=True)
        write_new(destination / "STOP.json", result)
        raise
    finally:
        write_new(destination / "RESULT.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
