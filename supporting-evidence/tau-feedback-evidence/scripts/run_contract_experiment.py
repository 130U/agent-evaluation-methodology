"""Controlled-output experiments using the installed, unchanged tau2 classes.

Only the generate() boundary is mocked. No LLM is queried and no new Agent
trajectory is generated. Cases are contract tests, not naturally sampled errors.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from tau_feedback.contracts import RequestBinding, strict_json, validate_nl_result, validate_bound_result

def variants(expected):
    rows = [{"expectedOutcome": x, "metExpectation": True,
             "reasoning": "Controlled output; not a semantic judgment."} for x in expected]
    def raw(items):
        return json.dumps({"results": items})
    yield "valid_all_true", raw(rows), "pass", False
    failed = deepcopy(rows)
    failed[0]["metExpectation"] = False
    yield "valid_one_false", raw(failed), "fail", False
    yield "valid_reordered", raw(list(reversed(rows))), "pass", False
    yield "missing_results", "{}", "unknown", False
    yield "empty_results", raw([]), "unknown", False
    yield "partial_results", raw(rows[:-1]), "unknown", False
    duplicated = deepcopy(rows)
    if len(rows) > 1 and len(set(expected)) > 1:
        duplicated[-1] = deepcopy(rows[0])
    else:
        duplicated.append(deepcopy(rows[0]))
    yield "duplicate_outcome", raw(duplicated), "unknown", False
    wrong = deepcopy(rows)
    wrong[0]["expectedOutcome"] = "UNRELATED ASSERTION - injected identity mismatch"
    yield "wrong_outcome", raw(wrong), "unknown", False
    string = deepcopy(rows)
    string[0]["metExpectation"] = "true"
    yield "string_boolean", raw(string), "unknown", False
    integer = deepcopy(rows)
    integer[0]["metExpectation"] = 1
    yield "integer_boolean", raw(integer), "unknown", False
    yield "malformed_json", '{"results":', "unknown", False
    yield "wrong_top_level", "[]", "unknown", False
    yield "duplicate_json_key", '{"results":[],"results":' + json.dumps(rows) + '}', "unknown", False
    yield "cross_run_swap", raw(rows), "unknown", True

def schema_baseline(raw):
    try:
        p = strict_json(raw)
        rows = p["results"]
        return (isinstance(rows, list) and all(
            isinstance(r, dict) and isinstance(r.get("expectedOutcome"), str)
            and type(r.get("metExpectation")) is bool
            and isinstance(r.get("reasoning"), str) and bool(r["reasoning"].strip())
            for r in rows))
    except (ValueError, KeyError, TypeError):
        return False

def main():
    from tau2.data_model.message import AssistantMessage
    from tau2.domains.retail.environment import get_tasks
    from tau2.evaluator.evaluator_nl_assertions import NLAssertionsEvaluator
    tasks = get_tasks(None)
    selected = [t for t in tasks if t.evaluation_criteria.nl_assertions]
    results = []
    for task in selected:
        expected = task.evaluation_criteria.nl_assertions
        binding = RequestBinding.create(run_id=f"controlled-{task.id}",task_id=task.id,
            trajectory=[],expected=expected,evaluator_revision="2174a603f6d014ef94473ffa95957f6ce27100db")
        for kind, raw, expected_status, swap in variants(expected):
            error, reward, returned = None, None, None
            with patch("tau2.evaluator.evaluator_nl_assertions.generate",
                       return_value=AssistantMessage(role="assistant", content=raw)):
                try:
                    out = NLAssertionsEvaluator.calculate_reward(task, [])
                    reward, returned = out.reward, len(out.nl_assertions)
                except Exception as exc:
                    error = type(exc).__name__
            schema_ok = schema_baseline(raw)
            count_ok = schema_ok and len(json.loads(raw)["results"]) == len(expected)
            identity = validate_nl_result(expected, raw)
            start = time.perf_counter_ns()
            bound = validate_bound_result(expected, raw, requested=binding,
                received=replace(binding, run_id="different-run") if swap else binding)
            elapsed_ns = time.perf_counter_ns() - start
            results.append({"task_id":task.id,"case":kind,"expected_count":len(expected),
                "raw_sha256":hashlib.sha256(raw.encode()).hexdigest(),
                "expected_contract_status":expected_status,
                "upstream_reward":reward,"upstream_returned_count":returned,"upstream_exception":error,
                "schema_accepts":schema_ok,"count_accepts":count_ok,
                "identity_contract":identity.to_dict(),"bound_contract":bound.to_dict(),
                "guard_duration_ns":elapsed_ns})
    # Empty expectations are a distinct control: no judge call may be made.
    empty_controls = []
    for task in tasks:
        if task.evaluation_criteria.nl_assertions:
            continue
        with patch("tau2.evaluator.evaluator_nl_assertions.generate",side_effect=AssertionError("unexpected model call")):
            out = NLAssertionsEvaluator.calculate_reward(task, [])
        empty_controls.append({"task_id":task.id,"upstream_reward":out.reward,
                               "guard_status":validate_nl_result([], "{}").status})
    summary = {}
    for kind in sorted({r["case"] for r in results}):
        rows = [r for r in results if r["case"] == kind]
        summary[kind] = {"n":len(rows),"upstream_pass":sum(r["upstream_reward"]==1 for r in rows),
            "upstream_fail":sum(r["upstream_reward"]==0 for r in rows),
            "upstream_exception":sum(r["upstream_exception"] is not None for r in rows),
            "schema_accept":sum(r["schema_accepts"] for r in rows),
            "count_accept":sum(r["count_accepts"] for r in rows),
            "identity_accept":sum(r["identity_contract"]["status"] in {"pass","fail"} for r in rows),
            "bound_accept":sum(r["bound_contract"]["status"] in {"pass","fail"} for r in rows),
            "bound_status":dict(Counter(r["bound_contract"]["status"] for r in rows))}
    mismatches = [r for r in results if r["bound_contract"]["status"] != r["expected_contract_status"]]
    report = {"experiment":"E1_controlled_outputs_real_tau2_classes","upstream_commit":binding.evaluator_revision,
        "new_model_calls":0,"new_agent_runs":0,"tasks_with_assertions":len(selected),
        "case_count":len(results),"empty_expectation_controls":empty_controls,
        "contract_mismatch_count":len(mismatches),"case_summary":summary,
        "guard_median_microseconds":statistics.median(r["guard_duration_ns"] for r in results)/1000,
        "rows":results,"limitations":["Model output is deliberately controlled.",
        "Empty trajectory is used to isolate parsing, not judge semantics.",
        "No claim of natural fault frequency or Agent performance improvement.",
        "Cross-run integrity requires trusted caller metadata; hashes are not authentication.",
        "Nonboolean and duplicate-key cases extend the earlier AST checks and are exploratory."]}
    output = ROOT / "results"
    output.mkdir(exist_ok=True)
    (output / "contract_experiment.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in {"rows","empty_expectation_controls","limitations"}},indent=2))
    if mismatches:
        raise SystemExit("Contract mismatches remain")

if __name__ == "__main__":
    main()
