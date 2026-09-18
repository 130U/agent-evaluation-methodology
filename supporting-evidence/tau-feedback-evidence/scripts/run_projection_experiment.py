"""Metamorphic checks on real archived messages; no semantic labels or models."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tau_feedback.projection import project_action_context

READ_ONLY_TOOLS = {"find_user_id_by_email","find_user_id_by_name_zip", "get_user_details",
                   "get_order_details", "get_product_details", "list_all_product_types"}

def select_action(messages):
    assistants = [i for i,m in enumerate(messages) if m.get("role") == "assistant"]
    tools = [i for i in assistants if messages[i].get("tool_calls")]
    writes = [i for i in tools if any(c.get("name") not in READ_ONLY_TOOLS
                                    for c in messages[i]["tool_calls"])]
    return (writes or tools or assistants or [None])[0]

def main():
    rows = []
    for path in sorted((ROOT/"data/archives").glob("*retail*.json")):
        archive = json.loads(path.read_text(encoding="utf-8"))
        env = archive["info"]["environment_info"]
        policy = env["policy"]
        tool_schemas = env.get("tool_defs") or []
        for sim in archive["simulations"]:
            messages = sim["messages"]
            idx = select_action(messages)
            result = {"archive":path.name,"run_id":sim["id"],"task_id":sim["task_id"],"action_index":idx,
                      "tool_schema_available":bool(env.get("tool_defs"))}
            if idx is None:
                result.update(status="unavailable",reason="no_agent_action")
                rows.append(result)
                continue
            try:
                def project(msgs, pol=policy):
                    return project_action_context(messages=msgs,action_index=idx,policy=pol,tool_schemas=tool_schemas)
                original = project(messages)
                future = deepcopy(messages)
                future[idx+1:] = [{"role":"user","content":"Injected future user preference: SECRET_FUTURE_731"}]
                extras = deepcopy(messages)
                for m in extras:
                    m["raw_data"] = {"hidden_gold":"SECRET_GOLD_419"}
                    m["hidden_user_instructions"] = "SECRET_PRIVATE_683"
                current = deepcopy(messages)
                current[idx]["content"] = (current[idx].get("content") or "") + " ACTION_CHANGED"
                prior_indices = [i for i,m in enumerate(messages[:idx]) if m.get("role") == "user"]
                prior_changed = None
                if prior_indices:
                    prior = deepcopy(messages)
                    i = prior_indices[0]
                    prior[i]["content"] = (prior[i].get("content") or "") + " OBSERVED_FACT_CHANGED"
                    prior_changed = project(prior) != original
                result.update(status="checked",view_sha256=original["view_sha256"],
                    future_invariant=project(future)==original,
                    hidden_metadata_invariant=project(extras)==original,
                    action_sensitive=project(current)!=original,
                    policy_sensitive=project(messages,policy+" POLICY_CHANGED")!=original,
                    observed_history_sensitive=prior_changed)
            except Exception as exc:
                result.update(status="error",error_type=type(exc).__name__,error=str(exc))
            rows.append(result)
    summary = {"runs":len(rows),"status":dict(Counter(r["status"] for r in rows))}
    for name in ["future_invariant","hidden_metadata_invariant","action_sensitive","policy_sensitive","observed_history_sensitive"]:
        checked = [r[name] for r in rows if name in r and r[name] is not None]
        summary[name] = {"eligible":len(checked),"passed":sum(checked),"failed":len(checked)-sum(checked)}
    report = {"experiment":"E2_archived_transcript_information_boundary", "summary":summary,"rows":rows,
        "new_agent_runs":0,"new_model_calls":0,"limitations":[
            "Metamorphic verification of context construction only; not semantic attribution accuracy.",
            "Archived tool_defs can be absent; an empty list tests serialization, not full historical model inputs.",
            "One predeclared action-selection rule per run; not all possible actions or corruptions.",
            "Same archived tasks recur across model/trial strata; counts are not independent statistical evidence."]}
    out=ROOT/"results"
    out.mkdir(exist_ok=True)
    (out/"projection_experiment.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2))
    if any(r["status"]=="error" for r in rows) or any(v.get("failed",0) for v in summary.values() if isinstance(v,dict)):
        raise SystemExit("Projection checks failed")

if __name__ == "__main__":
    main()
