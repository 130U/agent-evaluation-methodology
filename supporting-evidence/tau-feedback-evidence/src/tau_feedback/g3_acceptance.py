"""Pure common G3 validation acceptance; no model, filesystem or authorization.

Rows use task_id, strategy_sha256, native_reward, structural_status,
semantic_status, user_validity, critical_violation, input_tokens, output_tokens.
Identity/schema contradictions raise ValueError. Incomplete evidence, unknown
judgements/costs and ordinary failed criteria retain the unchanged seed.
The caller binds gepa_selected_new to the verified GEPA result and seals the
decision before testing. This function does not authenticate evidence producers.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import math


def _strategy(value, label, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 6000:
        raise ValueError(f"{label} must be a nonempty strategy of at most 6000 characters")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _status(value, field):
    if value is None:
        return "unknown"
    if not isinstance(value, str) or value not in ("pass", "fail", "unknown"):
        raise ValueError(f"Invalid {field}")
    return value


def _tokens(value, field):
    if value is None or type(value) is float and not math.isfinite(value):
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer or unknown")
    return value


def _rows(values, expected_ids, expected_strategy, label):
    if values is None:
        values = []
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{label} rows must be a list or tuple")
    rows = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} row must be a mapping")
        task = value.get("task_id")
        if not isinstance(task, str) or task not in expected_ids or task in rows:
            raise ValueError(f"{label} has a missing, unexpected or duplicate task identity")
        if expected_strategy is None or value.get("strategy_sha256") != expected_strategy:
            raise ValueError(f"{label} row belongs to another strategy")
        reward = value.get("native_reward")
        if type(reward) is float and not math.isfinite(reward):
            reward = None
        if reward is not None and (type(reward) not in (int, float) or reward not in (0, 1)):
            raise ValueError(f"{label} native_reward must be binary or unknown")
        critical = value.get("critical_violation")
        if critical is not None and type(critical) is not bool:
            raise ValueError(f"{label} critical_violation must be boolean or unknown")
        row = {"task_id": task, "native_reward": reward,
               "structural_status": _status(value.get("structural_status"), "structural_status"),
               "semantic_status": _status(value.get("semantic_status"), "semantic_status"),
               "user_validity": _status(value.get("user_validity"), "user_validity"),
               "critical_violation": critical,
               "input_tokens": _tokens(value.get("input_tokens"), "input_tokens"),
               "output_tokens": _tokens(value.get("output_tokens"), "output_tokens")}
        if row["structural_status"] == "pass" and reward == 0:
            raise ValueError(f"{label} structural pass contradicts official reward zero")
        # Structural failure with native reward one can reflect a separate
        # premature-termination guard. It is never counted as qualified.
        row["evaluation_known"] = (reward is not None and critical is not None
            and all(row[field] != "unknown" for field in
                    ("structural_status", "semantic_status", "user_validity")))
        row["qualified"] = (reward == 1 and critical is False
            and all(row[field] == "pass" for field in
                    ("structural_status", "semantic_status", "user_validity")))
        rows[task] = row
    return rows


def _counts(rows, ids):
    return {"expected": len(ids), "provided": len(rows),
            "qualified": sum(row["qualified"] for row in rows.values()),
            "unknown_evaluation": sum(not row["evaluation_known"] for row in rows.values()),
            "critical_violations": sum(row["critical_violation"] is True for row in rows.values()),
            "missing_tasks": [task for task in ids if task not in rows]}


def _cost(rows, ids):
    complete = len(rows) == len(ids) and all(
        row[key] is not None for row in rows.values() for key in ("input_tokens", "output_tokens"))
    return sum(row["input_tokens"] + row["output_tokens"] for row in rows.values()) if complete else None


def decide_strategy(seed_strategy, candidate_strategy, gepa_selected_new,
                    baseline_rows, candidate_rows, validation_ids):
    """Select a candidate only when every preregistered G3 condition is met.

    strategy_sha256 is SHA256 of exact UTF-8 strategy text (not candidate JSON).
    validation_ids must contain exactly two distinct string task IDs. Missing
    rows/judgements/token values are incomplete evidence, not implicit zero.
    Each row supplies task_id and strategy_sha256; outcome fields are:
      native_reward: 0 / 1 / None;
      structural_status, semantic_status, user_validity: pass / fail / unknown;
      critical_violation: True / False / None;
      input_tokens, output_tokens: nonnegative integer / None.
    Missing outcome fields and nonfinite numerical values remain unknown;
    malformed types, duplicate/out-of-set tasks and wrong strategy hashes raise.
    Cost compares validation input + output tokens, never monetary estimates.
    gepa_selected_new is an explicit bool derived from the verified endpoint;
    a rejected GEPA candidate is never revived by this validation function.
    """
    if (not isinstance(validation_ids, (list, tuple)) or len(validation_ids) != 2
        or any(not isinstance(v, str) or not v for v in validation_ids)
        or len(set(validation_ids)) != 2):
        raise ValueError("Freeze exactly two distinct string validation IDs")
    if type(gepa_selected_new) is not bool:
        raise ValueError("gepa_selected_new must be an explicit bool")
    ids = list(validation_ids)
    seed_hash = _strategy(seed_strategy, "seed_strategy")
    candidate_hash = _strategy(candidate_strategy, "candidate_strategy", optional=True)
    if gepa_selected_new and candidate_strategy is None:
        raise ValueError("GEPA cannot select a missing candidate")
    baseline = _rows(baseline_rows, ids, seed_hash, "baseline")
    candidate = _rows(candidate_rows, ids, candidate_hash, "candidate")
    counts = {"baseline": _counts(baseline, ids), "candidate": _counts(candidate, ids)}
    baseline_cost, candidate_cost = _cost(baseline, ids), _cost(candidate, ids)
    reasons = []
    if not gepa_selected_new:
        reasons.append("gepa_did_not_select_new_candidate")
    if candidate_strategy is None:
        reasons.append("candidate_missing")
    elif candidate_strategy == seed_strategy:
        reasons.append("candidate_text_unchanged")
    for label in ("baseline", "candidate"):
        if counts[label]["missing_tasks"]:
            reasons.append(label + "_validation_incomplete")
        if counts[label]["unknown_evaluation"]:
            reasons.append(label + "_judgements_unknown")
    if counts["candidate"]["qualified"] <= counts["baseline"]["qualified"]:
        reasons.append("qualified_count_not_strictly_higher")
    regressions = [task for task in ids if task in baseline and baseline[task]["qualified"]
                   and (task not in candidate or not candidate[task]["qualified"])]
    if regressions:
        reasons.append("baseline_qualified_task_regressed_or_unresolved")
    new_critical = [task for task in ids if task in baseline and task in candidate
                    and candidate[task]["critical_violation"] is True
                    and baseline[task]["critical_violation"] is not True]
    if new_critical:
        reasons.append("new_critical_violation")
    ratio = None
    if baseline_cost is None or candidate_cost is None:
        reasons.append("validation_token_cost_unknown")
    elif baseline_cost == 0:
        reasons.append("baseline_token_denominator_zero")
    else:
        # Equal two-task denominators make the average-cost ratio equal to the
        # ratio of totals. Compare integers exactly at the registered 1.25 cap.
        if candidate_cost * 4 > baseline_cost * 5:
            reasons.append("validation_token_ratio_exceeds_1_25")
        try:
            ratio = candidate_cost / baseline_cost
        except OverflowError:
            reasons.append("validation_token_ratio_not_representable")
    accepted = not reasons
    return {"schema_version": "g3-common-acceptance-v1",
            "final_strategy": candidate_strategy if accepted else seed_strategy,
            "final_strategy_sha256": candidate_hash if accepted else seed_hash,
            "decision": "accept_candidate" if accepted else "keep_seed",
            "accepted": accepted, "gepa_selected_new": gepa_selected_new,
            "seed_strategy_sha256": seed_hash, "candidate_strategy_sha256": candidate_hash,
            "validation_ids": ids, "reasons": reasons, "counts": counts,
            "regressed_or_unresolved_task_ids": regressions,
            "new_critical_violation_task_ids": new_critical,
            "validation_token_cost": {"baseline_total": baseline_cost,
                "candidate_total": candidate_cost, "candidate_to_baseline_ratio": ratio,
                "maximum_ratio": 1.25}}
