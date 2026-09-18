"""Pure G3 descriptive reporting, implemented after execution registration.

This module's later source hash must be recorded separately. It does not claim
to have been frozen before execution, verify artifact hashes, or invoke models.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import math

ARMS = ("B0", "B1", "B2")
IDENTITY = ("unit", "arm", "task_id", "simulation_seed")
STATES = ("success", "failure", "unknown", "interrupted", "not_run")


def _schedule(values):
    if not isinstance(values, (list, tuple)) or len(values) != 36:
        raise ValueError("The registered schedule must contain 36 units")
    by_unit, combinations = {}, set()
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("Schedule entries must be mappings")
        row = {key: value.get(key) for key in IDENTITY}
        if (type(row["unit"]) is not int or row["unit"] not in range(1, 37)
            or row["unit"] in by_unit or row["arm"] not in ARMS
            or not isinstance(row["task_id"], str) or not row["task_id"]
            or type(row["simulation_seed"]) is not int or row["simulation_seed"] < 0):
            raise ValueError("Invalid or duplicate schedule identity")
        combination = (row["arm"], row["task_id"], row["simulation_seed"])
        if combination in combinations:
            raise ValueError("Duplicate arm/task/repetition in schedule")
        combinations.add(combination)
        by_unit[row["unit"]] = row
    tasks = sorted({row["task_id"] for row in by_unit.values()})
    seeds = sorted({row["simulation_seed"] for row in by_unit.values()})
    if len(tasks) != 6 or len(seeds) != 2 or combinations != {
        (arm, task, seed) for arm in ARMS for task in tasks for seed in seeds
    }:
        raise ValueError("Schedule must be six tasks by two repetitions by three arms")
    return by_unit, tasks, seeds


def _label(value):
    if value is None:
        return "unknown"
    if not isinstance(value, str) or value not in ("pass", "fail", "unknown"):
        raise ValueError("Semantic and structural labels must be pass/fail/unknown")
    return value


def _number(value, *, integer=True):
    if value is None or type(value) is float and not math.isfinite(value):
        return None
    valid = type(value) is int if integer else type(value) in (int, float)
    if not valid or value < 0:
        raise ValueError("Invalid nonnegative usage value")
    return value


def _cost(value, *, optimization=False):
    result = {key: _number(value.get(key)) for key in
              ("cli_invocations", "input_tokens", "output_tokens", "unknown_usage_calls")}
    result["seconds"] = _number(value.get("client_seconds" if optimization else "elapsed_seconds"), integer=False)
    flag = value.get("usage_incomplete")
    if flag is not None and type(flag) is not bool:
        raise ValueError("usage_incomplete must be boolean or unknown")
    # Optimization ledgers have ledger_available instead of usage_incomplete.
    available = value.get("ledger_available") if optimization else flag is False
    if optimization and available is not None and type(available) is not bool:
        raise ValueError("ledger_available must be boolean or unknown")
    result["complete"] = (available is True and flag is not True
        and result["unknown_usage_calls"] == 0
        and all(result[key] is not None for key in
                ("cli_invocations", "input_tokens", "output_tokens", "seconds")))
    return result


def _cost_sum(costs):
    keys = ("cli_invocations", "input_tokens", "output_tokens", "unknown_usage_calls", "seconds")
    return {"accounted_records": len(costs),
            "complete": all(c["complete"] for c in costs),
            "incomplete_records": sum(not c["complete"] for c in costs),
            "known_subtotals": {key: sum(c[key] for c in costs if c[key] is not None) for key in keys},
            "missing_records_by_metric": {key: sum(c[key] is None for c in costs) for key in keys},
            "currency": "unavailable"}


def _unit(identity, value):
    if value is None:
        return {**identity, "official": "not_run", "qualification": "not_run",
                "critical_violation": None, "cost": None}
    outcome = value.get("outcome")
    if outcome is None:
        outcome = {}
    if not isinstance(outcome, Mapping):
        raise ValueError("outcome must be a mapping or absent")
    for key in ("task_id", "simulation_seed"):
        if key in outcome and (type(outcome[key]) is not type(identity[key]) or outcome[key] != identity[key]):
            raise ValueError("Nested outcome identity differs from schedule")
    semantic, user = _label(value.get("semantic_status")), _label(value.get("user_validity"))
    critical = value.get("critical_violation")
    if critical is not None and type(critical) is not bool:
        raise ValueError("critical_violation must be boolean or unknown")
    execution = value.get("execution_status")
    if execution not in (None, "started", "returned", "interrupted_ungraded", "not_run"):
        raise ValueError("Unrecognized execution_status")
    status = outcome.get("status")
    if status not in (None, "running", "error", "evaluated"):
        raise ValueError("Unrecognized episode outcome status")
    if execution == "not_run":
        if outcome or semantic != "unknown" or user != "unknown" or critical is not None:
            raise ValueError("Unrun unit cannot carry an outcome or semantic result")
        return _unit(identity, None)
    cost = _cost(outcome)
    result = {**identity, "critical_violation": critical, "cost": cost}
    if execution in ("started", "interrupted_ungraded") or status != "evaluated":
        return {**result, "official": "interrupted", "qualification": "interrupted"}
    reward = outcome.get("native_reward")
    if type(reward) is float and not math.isfinite(reward):
        reward = None
    if reward is not None and (type(reward) not in (int, float) or reward not in (0, 1)):
        raise ValueError("native_reward must be binary or unknown")
    structural = outcome.get("structural_outcome")
    if structural is None:
        structural = {}
    if not isinstance(structural, Mapping):
        raise ValueError("structural_outcome must be a mapping")
    structural = _label(structural.get("status"))
    if structural == "pass" and reward == 0:
        raise ValueError("Structural pass contradicts official reward zero")
    official = "unknown" if reward is None else "success" if reward == 1 else "failure"
    known = official != "unknown" and "unknown" not in (structural, semantic, user) and critical is not None
    qualified = (official == "success" and structural == semantic == user == "pass" and critical is False)
    return {**result, "official": official,
            "qualification": ("success" if qualified else "failure") if known else "unknown"}


def _counts(units, metric):
    counts = Counter(unit[metric] for unit in units)
    return {"assigned": len(units), **{state: counts[state] for state in STATES},
            "success_rate_assigned": counts["success"] / len(units),
            "all_outcomes_known": counts["success"] + counts["failure"] == len(units)}


def _repeat(units, tasks, metric):
    rows = []
    for task in tasks:
        pair = sorted((u for u in units if u["task_id"] == task), key=lambda u: u["simulation_seed"])
        known = sum(u[metric] in ("success", "failure") for u in pair)
        rows.append({"task_id": task, "planned_repetitions": 2, "known_repetitions": known,
                     "outcomes": [u[metric] for u in pair],
                     "pass_power_2": int(all(u[metric] == "success" for u in pair)) if known == 2 else None})
    estimable = sum(r["pass_power_2"] is not None for r in rows)
    both = sum(r["pass_power_2"] == 1 for r in rows)
    return {"task_denominator": 6, "estimable_tasks": estimable, "unresolved_tasks": 6 - estimable,
            "both_success_tasks": both, "pass_power_2": both / 6 if estimable == 6 else None, "tasks": rows}


def _paired(units, tasks, seeds, metric):
    lookup = {(u["arm"], u["task_id"], u["simulation_seed"]): u for u in units}
    changes = []
    for task in tasks:
        for seed in seeds:
            b1, b2 = (lookup[(arm, task, seed)][metric] for arm in ("B1", "B2"))
            if b1 not in ("success", "failure") or b2 not in ("success", "failure"):
                change = "unresolved"
            elif b1 == b2:
                change = "both_success" if b1 == "success" else "both_failure"
            else:
                change = "repair" if b2 == "success" else "regression"
            changes.append({"task_id": task, "simulation_seed": seed, "B1": b1, "B2": b2, "change": change})
    counts = Counter(row["change"] for row in changes)
    net = counts["repair"] - counts["regression"]
    assigned_delta = (sum(row["B2"] == "success" for row in changes)
                      - sum(row["B1"] == "success" for row in changes)) / 12
    return {"assigned_pairs": 12, **{key: counts[key] for key in
            ("repair", "regression", "both_success", "both_failure", "unresolved")},
            "net_known_changes": net, "paired_difference": net / 12 if not counts["unresolved"] else None,
            "assigned_success_rate_difference": assigned_delta,
            "pairs": changes}


def aggregate_test(schedule, rows, optimization_usage=None):
    """Aggregate the registered 36-unit G3 schedule without reading files.

    schedule: 36 dicts with unit:int(1..36), arm:B0/B1/B2, task_id:str,
      simulation_seed:int; exactly six tasks x two seeds x three arms.
    rows: zero or more dicts with the same identity fields, optional
      execution_status (started/returned/interrupted_ungraded/not_run), and
      outcome from run_subscription_episode: status, native_reward,
      structural_outcome.status, cli_invocations, input_tokens, output_tokens,
      elapsed_seconds, unknown_usage_calls, usage_incomplete. Top-level
      semantic_status/user_validity are pass/fail/unknown and
      critical_violation is bool/None. Missing reviews are unknown, not failure.
    optimization_usage: optional {B1: ledger, B2: ledger}; ledger keys are
      cli_invocations/input_tokens/output_tokens/unknown_usage_calls (ints),
      client_seconds (nonnegative finite number), ledger_available (bool).
      Missing/nonfinite cost values stay unknown; no dollars are estimated.

    Duplicate/mismatched identities and malformed schemas raise ValueError.
    A missing unit is not_run. A reserved/ungraded/error unit is interrupted.
    Official success is the evaluated raw binary reward, independently of
    qualification. Qualification additionally requires known structural,
    semantic, user and critical checks, all passing and critical=False.
    Each arm retains N=12. pass_power_2 means BOTH successes, not pass@2;
    it is None for a task with fewer than two known outcomes, and None for
    the six-task aggregate unless all six pairs are estimable. Comparisons
    are descriptive paired outcomes; the function cannot infer causal effects
    or whether the three final strategy texts differ.
    """
    assigned, tasks, seeds = _schedule(schedule)
    if not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list or tuple")
    supplied = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Each result row must be a mapping")
        unit = row.get("unit")
        if type(unit) is not int or unit not in assigned or unit in supplied:
            raise ValueError("Unexpected or duplicate result unit")
        if any(type(row.get(key)) is not type(assigned[unit][key]) or row.get(key) != assigned[unit][key] for key in IDENTITY):
            raise ValueError("Result identity differs from registered schedule")
        supplied[unit] = row
    units = [_unit(assigned[i], supplied.get(i)) for i in sorted(assigned)]
    if optimization_usage is not None and (not isinstance(optimization_usage, Mapping)
        or any(arm not in ("B1", "B2") for arm in optimization_usage)):
        raise ValueError("optimization_usage must map B1/B2 to ledgers")
    optimization = {}
    for arm in ("B1", "B2"):
        ledger = None if optimization_usage is None else optimization_usage.get(arm)
        if ledger is not None and not isinstance(ledger, Mapping):
            raise ValueError("Optimization ledger must be a mapping or unknown")
        optimization[arm] = _cost_sum([_cost(ledger or {}, optimization=True)])
        optimization[arm]["provided"] = ledger is not None
    arms = {}
    for arm in ARMS:
        subset = [u for u in units if u["arm"] == arm]
        arms[arm] = {"assigned_units": 12, "distinct_tasks": 6,
            "official": _counts(subset, "official"), "qualification": _counts(subset, "qualification"),
            "official_repeated_success": _repeat(subset, tasks, "official"),
            "qualified_repeated_success": _repeat(subset, tasks, "qualification"),
            "critical_violation_units": [u["unit"] for u in subset if u["critical_violation"] is True],
            "test_usage": _cost_sum([u["cost"] for u in subset if u["cost"] is not None])}
    return {"schema_version": "g3-test-reporting-v1", "implementation_timing": "post_registration_reporting_implementation",
        "planned_units": 36, "distinct_tasks": 6, "repetitions_per_task": 2,
        "model_seed_controlled": False, "arms": arms, "units": units,
        "paired_B2_minus_B1": {metric: _paired(units, tasks, seeds, metric) for metric in ("official", "qualification")},
        "test_usage": _cost_sum([u["cost"] for u in units if u["cost"] is not None]),
        "optimization_usage": optimization,
        "limitations": ["Twelve runs per arm represent six tasks, not twelve independent tasks.",
            "Unknown, interrupted and unrun outcomes remain distinct from confirmed failures.",
            "Assigned-denominator success rates include incomplete coverage; inspect all five outcome counts.",
            "Pass power two is both-success reliability on two observed repetitions, not pass-at-two.",
            "Six tasks and one optimization run support descriptive results, not stable significance or equivalence.",
            "Identical final strategies make arm differences repeat-run variation, not a strategy intervention.",
            "Known usage subtotals do not estimate missing costs; subscription dollars and research-agent costs are unavailable."]}
