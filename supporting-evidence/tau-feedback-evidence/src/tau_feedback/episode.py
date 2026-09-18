"""Run the original text simulator and evaluator with a real local backend.

Persists incomplete trajectories before evaluation. Strategy text is the sole
optional actor change; task instructions and reference actions never enter it.
"""
from __future__ import annotations
import datetime as dt
import json
from pathlib import Path
import time
import uuid

from tau_feedback.contracts import canonical_hash, validate_nl_result
from tau_feedback.local_backend import LocalGenerationBackend


def dump(path: Path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")


def structural_outcome(task, simulation, records):
    """Structural benchmark outcome; not a policy/user-semantic audit."""
    from tau2.data_model.tasks import RewardType
    normal=simulation.termination_reason.value in {"agent_stop","user_stop"}
    if not normal:
        return {"status":"fail","reason":"premature_termination"}
    reward=simulation.reward_info
    if reward is None:
        return {"status":"unknown","reason":"missing_reward"}
    criteria=task.evaluation_criteria
    expected=criteria.nl_assertions if criteria else []
    if expected and RewardType.NL_ASSERTION in criteria.reward_basis:
        checks=[r for r in records if r["role"]=="nl_assertions_eval"]
        if len(checks)!=1 or "error" in checks[0]:
            return {"status":"unknown","reason":"missing_or_ambiguous_nl_call"}
        content=checks[0]["response"]["choices"][0]["message"].get("content")
        contract=validate_nl_result(expected,content)
        if contract.status=="unknown":
            return {"status":"unknown","reason":contract.reason,"nl_contract":contract.to_dict()}
        if contract.status=="fail" and reward.reward==1:
            return {"status":"unknown","reason":"nl_result_conflicts_with_aggregate_reward"}
    if reward.reward not in (0,1):
        return {"status":"unknown","reason":"unexpected_nonbinary_reward"}
    return {"status":"pass" if reward.reward==1 else "fail","reason":"complete_structural_evaluation"}


def run_episode(runtime, task, *, output: Path, seed: int, limits: dict,
                strategy: str | None=None, review: bool=False):
    from tau2.agent.llm_agent import LLMAgent
    from tau2.data_model.simulation import TextRunConfig
    from tau2.evaluator.evaluator import EvaluationType,evaluate_simulation
    from tau2.runner.build import build_text_orchestrator
    from tau2.runner.batch import run_auto_review
    # No resume-by-overwrite: every attempted unit has an immutable directory.
    output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    run_id=str(uuid.uuid4())
    backend=LocalGenerationBackend(runtime,output=output/"calls.jsonl",seed=seed,
        max_calls=limits["max_calls"],max_generated_tokens=limits["max_generated_tokens"],
        max_seconds=limits["max_seconds"],max_request_seconds=limits["max_request_seconds"])
    config=TextRunConfig(domain="retail",agent="llm_agent",user="user_simulator",
        llm_agent=backend.MODEL,llm_user=backend.MODEL,
        llm_args_agent={"temperature":limits["agent_temperature"],"max_tokens":limits["agent_max_tokens"]},
        llm_args_user={"temperature":limits["user_temperature"],"max_tokens":limits["user_max_tokens"]},
        max_steps=limits["max_steps"],max_errors=limits["max_errors"],timeout=limits["simulation_seconds"],
        seed=seed,enforce_communication_protocol=True,auto_review=False,hallucination_retries=0)
    metadata={"run_id":run_id,"task_id":task.id,"seed":seed,
        "started_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "task_sha256":canonical_hash(task.model_dump(mode="json")),"limits":limits,
        "strategy":strategy,"config":config.model_dump(mode="json"),
        "actor_receives_hidden_scenario":False,"review_requested":review}
    dump(output/"request.json",metadata)
    row={"run_id":run_id,"task_id":task.id,"seed":seed,"status":"running",
         "structural_outcome":{"status":"unknown","reason":"not_evaluated"},
         "semantic_review_status":"pending_research_agent_review"}
    phase="construction"
    simulation=None
    orchestrator=None
    try:
        orchestrator=build_text_orchestrator(config,task,seed=seed,simulation_id=run_id)
        if strategy is not None:
            if not isinstance(strategy,str) or not strategy.strip():
                raise ValueError("Strategy must be nonempty text or None for the unmodified baseline")
            class StrategyAgent(LLMAgent):
                @property
                def system_prompt(self):
                    return super().system_prompt+"\n<reusable_strategy>\n"+strategy+"\n</reusable_strategy>"
            orchestrator.agent=StrategyAgent(tools=orchestrator.environment.get_tools(),
                domain_policy=orchestrator.environment.get_policy(),llm=backend.MODEL,llm_args=config.llm_args_agent)
        phase="simulation"
        backend.phase_deadline=backend.started+limits["simulation_seconds"]
        with backend:
            simulation=orchestrator.run()
            backend.phase_deadline=None
            simulation.policy=orchestrator.environment.get_policy()
            # Preserve actual trajectory even if downstream scoring fails.
            dump(output/"simulation.json",simulation.model_dump(mode="json"))
            phase="evaluation"
            simulation.reward_info=evaluate_simulation(simulation=simulation,task=task,
                evaluation_type=EvaluationType.ALL,solo_mode=False,domain="retail",strict_replay=True)
            dump(output/"simulation.json",simulation.model_dump(mode="json"))
            row.update(status="evaluated",termination_reason=simulation.termination_reason.value,
                native_reward=simulation.reward_info.reward,
                structural_outcome=structural_outcome(task,simulation,backend.records))
            if review:
                phase="native_review"
                run_auto_review(simulation=simulation,task=task,review_mode="full",review_model=backend.MODEL,
                    user="user_simulator",llm_user=backend.MODEL,llm_args_user=config.llm_args_user,
                    user_persona_config=None,user_voice_settings=None,policy=simulation.policy,is_audio_native=False)
                dump(output/"simulation.json",simulation.model_dump(mode="json"))
    except Exception as exc:
        row.update(status="error",failure_phase=phase,error_type=type(exc).__name__,error=str(exc))
    finally:
        trajectory=orchestrator.get_trajectory() if orchestrator is not None else []
        dump(output/"trajectory.json",[m.model_dump(mode="json") for m in trajectory])
        row.update(elapsed_seconds=time.monotonic()-started,model_calls=len(backend.records),
            generated_tokens=backend.generated_tokens,charged_generated_tokens=backend.charged_generated_tokens,
            prompt_tokens=backend.prompt_tokens,usage_incomplete_calls=backend.usage_incomplete_calls,
            trajectory_messages=len(trajectory))
        dump(output/"outcome.json",row)
    return row
