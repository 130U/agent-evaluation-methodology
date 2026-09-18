"""Re-grade historical DB components in the pinned current retail environment.

This is a version-migration diagnostic, not reproduction of unavailable old
source and not an Agent optimization experiment. All model calls are excluded.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--limit",type=int)
    args=parser.parse_args()
    from loguru import logger
    logger.remove()
    from pydantic import TypeAdapter
    from tau2.data_model.message import Message
    from tau2.data_model.tasks import Task
    from tau2.domains.retail.environment import get_environment
    from tau2.evaluator.evaluator_env import EnvironmentEvaluator
    adapter = TypeAdapter(list[Message])
    results=[]
    golden_cache={}
    started=time.perf_counter()
    for path in sorted((ROOT/"data/archives").glob("*retail*.json")):
        data=json.loads(path.read_text(encoding="utf-8"))
        tasks={t["id"]:t for t in data["tasks"]}
        for sim in data["simulations"]:
            if args.limit is not None and len(results)>=args.limit:
                break
            row={"archive":path.name,"run_id":sim["id"],"task_id":sim["task_id"],"trial":sim.get("trial"),
                 "archive_db_reward":(sim.get("reward_info") or {}).get("reward_breakdown",{}).get("DB")}
            try:
                task=Task.model_validate(tasks[sim["task_id"]])
                key=json.dumps(tasks[sim["task_id"]],sort_keys=True)
                if key not in golden_cache:
                    env=get_environment()
                    init=task.initial_state
                    env.set_state(initialization_data=init.initialization_data if init else None,
                        initialization_actions=init.initialization_actions if init else None,
                        message_history=init.message_history if init else [])
                    failures=[]
                    for action in task.evaluation_criteria.actions or []:
                        try:
                            env.make_tool_call(tool_name=action.name,requestor=action.requestor,**action.arguments)
                        except Exception as exc:
                            failures.append({"action":action.name,"error_type":type(exc).__name__,"error":str(exc)})
                    golden_cache[key]=failures
                row["gold_errors"]=golden_cache[key]
                if row["gold_errors"]:
                    row["status"]="reference_preflight_exception"
                else:
                    messages=adapter.validate_python(sim["messages"])
                    with patch("socket.socket.connect",side_effect=RuntimeError("Network disabled for replay")):
                        try:
                            score=EnvironmentEvaluator.calculate_reward(get_environment,task,messages,strict_replay=True)
                            row.update(status="strict_replayed",strict_db_reward=score.db_check.db_reward)
                        except Exception as exc:
                            row.update(strict_error_type=type(exc).__name__,strict_error=str(exc))
                            try:
                                score=EnvironmentEvaluator.calculate_reward(get_environment,task,messages,strict_replay=False)
                                row.update(status="nonstrict_only",nonstrict_db_reward=score.db_check.db_reward)
                            except Exception as soft_exc:
                                row.update(status="replay_error",nonstrict_error_type=type(soft_exc).__name__,nonstrict_error=str(soft_exc))
            except Exception as exc:
                row.update(status="setup_or_schema_error",error_type=type(exc).__name__,error=str(exc))
            results.append(row)
            if len(results)%100==0:
                print(json.dumps({"completed":len(results),"seconds":round(time.perf_counter()-started,1)}),flush=True)
        if args.limit is not None and len(results)>=args.limit:
            break
    summary={"runs":len(results),"status":dict(Counter(r["status"] for r in results)),
             "strict_db_disagreements":sum(r["strict_db_reward"]!=r["archive_db_reward"] for r in results if "strict_db_reward" in r),
             "nonstrict_db_disagreements":sum(r["nonstrict_db_reward"]!=r["archive_db_reward"] for r in results if "nonstrict_db_reward" in r),
             "elapsed_seconds":time.perf_counter()-started}
    report={"experiment":"E3_historical_trajectory_current_environment_db_replay",
            "current_runtime_commit":"2174a603f6d014ef94473ffa95957f6ce27100db",
            "strict_replay_scope":"Official strict state replay checks modifying tool outputs; it does not reexecute every read-only call.",
            "preflight_scope":"An exception pauses our regrading under the registered rule; it does not prove the reference or historical score invalid.",
            "original_commit_not_recovered":True,"summary":summary,"rows":results,
            "new_agent_runs":0,"new_model_calls":0}
    output=ROOT/"results"
    output.mkdir(exist_ok=True)
    name="replay_smoke.json" if args.limit is not None else "replay_experiment.json"
    (output/name).write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2))

if __name__=="__main__":
    main()
