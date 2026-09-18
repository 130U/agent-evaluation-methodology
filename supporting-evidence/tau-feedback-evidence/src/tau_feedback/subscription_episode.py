"""Original tau2 text environment with explicitly declared CLI model transport."""
import datetime as dt
from pathlib import Path
import time
import uuid

from .contracts import canonical_hash
from .episode import dump, structural_outcome
from .subscription_backend import SubscriptionGenerationBackend


def run_subscription_episode(client, task, *, output:Path, simulation_seed:int,
                             max_steps=40, max_errors=6, simulation_seconds=1200, strategy=None):
    from tau2.agent.llm_agent import LLMAgent
    from tau2.data_model.simulation import TextRunConfig
    from tau2.evaluator.evaluator import EvaluationType,evaluate_simulation
    from tau2.runner.build import build_text_orchestrator
    output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    run_id=str(uuid.uuid4())
    backend=SubscriptionGenerationBackend(client,output=output/"calls.jsonl")
    before={"calls":client.calls,"input_tokens":client.input_tokens,"output_tokens":client.output_tokens}
    unknown_before=getattr(client,"unknown_usage_calls",None)
    metadata={"run_id":run_id,"task_id":task.id,"simulation_seed":simulation_seed,
              "model_seed_controlled":False,"sampling_control":"CLI model/effort only; no temperature/seed/token cap",
              "started_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
              "task_sha256":canonical_hash(task.model_dump(mode="json")),
              "strategy":strategy,
              "requested_config":{"max_steps":max_steps,"max_errors":max_errors,"simulation_seconds":simulation_seconds},
              "transport":"codex_cli_structured_action","cost_currency":"unavailable; use recorded usage/time"}
    dump(output/"request.json",metadata)
    row={"run_id":run_id,"task_id":task.id,"status":"running",
         "structural_outcome":{"status":"unknown","reason":"not_evaluated"},
         "semantic_review_status":"pending_research_agent_review"}
    orchestrator=None
    phase="construction"
    try:
        config=TextRunConfig(domain="retail",agent="llm_agent",user="user_simulator",
            llm_agent=backend.MODEL,llm_user=backend.MODEL,llm_args_agent={},llm_args_user={},
            max_steps=max_steps,max_errors=max_errors,timeout=simulation_seconds,seed=simulation_seed,
            enforce_communication_protocol=True,auto_review=False,hallucination_retries=0)
        metadata["config"]=config.model_dump(mode="json")
        dump(output/"request.json",metadata)
        orchestrator=build_text_orchestrator(config,task,seed=simulation_seed,simulation_id=run_id)
        if strategy is not None:
            if not isinstance(strategy,str) or not strategy.strip():
                raise ValueError("Strategy must be nonempty text or None")
            class StrategyAgent(LLMAgent):
                @property
                def system_prompt(self):
                    return super().system_prompt+"\n<reusable_strategy>\n"+strategy+"\n</reusable_strategy>"
            orchestrator.agent=StrategyAgent(tools=orchestrator.environment.get_tools(),
                domain_policy=orchestrator.environment.get_policy(),llm=backend.MODEL,llm_args={})
            orchestrator.agent.set_seed(simulation_seed)
        phase="simulation"
        with backend:
            simulation=orchestrator.run()
            simulation.policy=orchestrator.environment.get_policy()
            dump(output/"simulation.json",simulation.model_dump(mode="json"))
            phase="evaluation"
            simulation.reward_info=evaluate_simulation(simulation=simulation,task=task,
                evaluation_type=EvaluationType.ALL,solo_mode=False,domain="retail",strict_replay=True)
            dump(output/"simulation.json",simulation.model_dump(mode="json"))
            row.update(status="evaluated",termination_reason=simulation.termination_reason.value,
                       native_reward=simulation.reward_info.reward,
                       structural_outcome=structural_outcome(task,simulation,backend.records))
    except Exception as exc:
        row.update(status="error",failure_phase=phase,error_type=type(exc).__name__,error=str(exc))
    finally:
        trajectory=orchestrator.get_trajectory() if orchestrator is not None else []
        dump(output/"trajectory.json",[m.model_dump(mode="json") for m in trajectory])
        def delta(name):
            after=getattr(client,name,None)
            initial=before[name]
            return after-initial if type(after) is int and type(initial) is int and after>=initial else None
        unknown_after=getattr(client,"unknown_usage_calls",None)
        unknown_delta=(unknown_after-unknown_before if type(unknown_after) is int and type(unknown_before) is int
                       and unknown_after>=unknown_before else None)
        row.update(elapsed_seconds=time.monotonic()-started,model_calls=delta("calls"),
                   cli_invocations=delta("calls"),model_call_count_semantics="Reserved CLI invocations; underlying provider request count is not observed",
                   input_tokens=delta("input_tokens"),output_tokens=delta("output_tokens"),
                   client_halt_reason=client.halt_reason,unknown_usage_calls=unknown_delta,
                   usage_incomplete=(unknown_delta is None or unknown_delta>0 or any(delta(name) is None for name in before)),
                   trajectory_messages=len(trajectory),usage_scope="Known CLI token subtotals including prompt overhead; failures may have unknown additional cost",
                   simulation_timeout_scope="Official step boundary; a current CLI request can extend beyond it by the request timeout")
        dump(output/"outcome.json",row)
    return row
