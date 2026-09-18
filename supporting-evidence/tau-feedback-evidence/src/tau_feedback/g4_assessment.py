"""G4 prospective pure qualification, adoption and paired descriptive analysis."""
from copy import deepcopy
from .g3_acceptance import _rows, _counts, _cost, _strategy


def keyed_rows(rows, units):
    identities = [(u["task_id"], u["simulation_seed"]) for u in units]
    if len(identities) != len(set(identities)) or not identities:
        raise ValueError("Unique nonempty task/seed schedule required")
    keys = [f"{task}@{seed}" for task, seed in identities]
    transformed = []
    for row in rows:
        identity = (row.get("task_id"), row.get("simulation_seed"))
        if identity not in identities:
            raise ValueError("Review outside assigned validation schedule")
        value = deepcopy(row)
        value["task_id"] = keys[identities.index(identity)]
        transformed.append(value)
    return transformed, keys


def assess_candidate(*, seed_strategy, candidate_strategy, gepa_selected_new,
                     baseline_rows, candidate_rows, units):
    if type(gepa_selected_new) is not bool:
        raise ValueError("Explicit GEPA selection required")
    seed_hash = _strategy(seed_strategy, "seed")
    child_hash = _strategy(candidate_strategy, "candidate", optional=True)
    b, ids = keyed_rows(baseline_rows, units)
    c, _ = keyed_rows(candidate_rows, units)
    baseline = _rows(b, ids, seed_hash, "baseline")
    child = _rows(c, ids, child_hash, "candidate")
    counts = {"baseline": _counts(baseline, ids), "candidate": _counts(child, ids)}
    bcost, ccost = _cost(baseline, ids), _cost(child, ids)
    regressions = [key for key in ids if key in baseline and baseline[key]["qualified"]
                   and (key not in child or not child[key]["qualified"])]
    reasons = []
    if not gepa_selected_new:
        reasons.append("gepa_did_not_select_new_candidate")
    if candidate_strategy is None or candidate_strategy == seed_strategy:
        reasons.append("missing_or_unchanged_candidate")
    for label in ("baseline", "candidate"):
        if counts[label]["missing_tasks"] or counts[label]["unknown_evaluation"]:
            reasons.append(label + "_incomplete_or_unknown")
    if counts["candidate"]["qualified"] <= counts["baseline"]["qualified"]:
        reasons.append("no_strict_qualified_gain")
    if regressions:
        reasons.append("baseline_qualified_unit_regressed_or_unknown")
    if counts["candidate"]["critical_violations"]:
        reasons.append("candidate_critical_violation")
    if bcost is None or ccost is None or bcost == 0:
        reasons.append("cost_ratio_unknown")
    elif ccost * 4 > bcost * 5:
        reasons.append("validation_token_ratio_over_1_25")
    complete = all(not counts[label]["missing_tasks"] and not counts[label]["unknown_evaluation"]
                   for label in counts)
    return {"schema_version": "g4-acceptance-v1", "accepted": not reasons, "reasons": reasons,
        "seed_strategy_sha256": seed_hash, "candidate_strategy_sha256": child_hash,
        "gepa_selected_new": gepa_selected_new, "counts": counts, "regressions": regressions,
        "qualified_gain": counts["candidate"]["qualified"] - counts["baseline"]["qualified"] if complete else None,
        "validation_token_cost": {"baseline_total": bcost, "candidate_total": ccost,
            "ratio": ccost / bcost if bcost and ccost is not None else None, "max_ratio": 1.25},
        "complete_qualified_evaluation": complete}


def choose_final(seed_strategy, candidates):
    """All four registered opportunities must be supplied, even without a child."""
    if len(candidates) != 4 or [c["batch"] for c in candidates] != [1, 2, 3, 4]:
        raise ValueError("Keep all four ordered opportunity records")
    eligible = []
    for item in candidates:
        if item["assessment"]["accepted"]:
            if _strategy(item["strategy"], "candidate") != item["assessment"]["candidate_strategy_sha256"]:
                raise ValueError("Selected strategy differs from measured candidate")
            eligible.append(item)
    eligible.sort(key=lambda c: (-c["assessment"]["counts"]["candidate"]["qualified"],
        c["assessment"]["validation_token_cost"]["candidate_total"], c["batch"]))
    selected = eligible[0] if eligible else None
    text = selected["strategy"] if selected else seed_strategy
    return {"strategy": text, "strategy_sha256": _strategy(text, "final"),
        "selected_batch": selected["batch"] if selected else None,
        "decision": "accept_candidate" if selected else "keep_seed",
        "eligible_batches": [c["batch"] for c in eligible]}


def paired_candidate_summary(pairs, assigned_batches=(1, 2, 3, 4)):
    if [p["batch"] for p in pairs] != list(assigned_batches):
        raise ValueError("Every assigned batch needs an explicit result")
    results = []
    for pair in pairs:
        if type(pair["input_treatment_active"]) is not bool:
            raise ValueError("Input-defined treatment activation is mandatory")
        one, two = pair.get("B1"), pair.get("B2")
        complete = bool(one and two and one["complete_qualified_evaluation"] and two["complete_qualified_evaluation"])
        delta = None
        if complete:
            n1, n2 = one["counts"]["candidate"]["expected"], two["counts"]["candidate"]["expected"]
            if n1 != n2 or not n1:
                raise ValueError("Unequal paired quality denominators")
            delta = (two["counts"]["candidate"]["qualified"] - one["counts"]["candidate"]["qualified"]) / n1
        results.append({"batch": pair["batch"], "treatment_active": pair["input_treatment_active"],
            "complete_pair": complete, "qualified_rate_delta_B2_minus_B1": delta})
    all_complete = all(r["complete_pair"] for r in results)
    active = [r for r in results if r["treatment_active"]]
    active_complete = bool(active) and all(r["complete_pair"] for r in active)
    return {"assigned_pairs": len(results), "active_pairs": len(active), "pairs": results,
        "all_pair_mean_delta": sum(r["qualified_rate_delta_B2_minus_B1"] for r in results) / len(results) if all_complete else None,
        "active_pair_mean_delta": sum(r["qualified_rate_delta_B2_minus_B1"] for r in active) / len(active) if active_complete else None,
        "identification": "conditional_small_sample_comparison" if active_complete else "insufficient_active_complete_pairs",
        "inference": "Descriptive paired batches on four reused tasks; no significance, equivalence or population claim."}
