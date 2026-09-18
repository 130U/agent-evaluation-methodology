"""Read-only fixed-denominator C1 report; never revises reference labels."""
from collections import Counter
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from c1_synthetic_calibration import ROOT, OUTPUT, MANIFEST, verify, load, sha, write_new
from audit_recovered_transport import audit_client
from tau_feedback.admission import audit_diagnosis
from tau_feedback.contracts import canonical_hash
from tau_feedback.subscription_evidence import verify_cli_call


def replay_unit(row, card, source):
    directory = OUTPUT / f"unit-{row['unit']:02d}" / "gate"
    gate = load(directory / "outcome.json")
    if gate["status"] != "complete" or gate["admission"] != row["admission"]:
        raise ValueError("Calibration row differs from gate sidecar")
    client = SimpleNamespace(root=ROOT, output=OUTPUT / "client",
        records=[load(p) for p in (OUTPUT / "client").glob("*/outcome.json")])
    seen = []
    def recorded_judge(instruction, payload):
        if seen or len(gate["judge_calls"]) != 1:
            raise ValueError("Synthetic probe must have at most one actual judge")
        call = gate["judge_calls"][0]
        verify_cli_call(client, call["cli_call_id"], role="feedback_admission", model_sidecar=call["directory"])
        request = load(Path(call["directory"]) / "request.json")
        prompt = instruction + "\nADMISSION_EVIDENCE_JSON\n" + json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if request["payload"] != payload or request["prompt"] != prompt:
            raise ValueError("Actual model prompt differs from locked synthetic input projection")
        seen.append(call["cli_call_id"])
        return json.dumps(load(Path(call["directory"]) / "outcome.json")["output"], ensure_ascii=False)
    replay = audit_diagnosis(diagnosis=card["stimulus"]["diagnosis"], messages=card["stimulus"]["messages"],
        policy=source["shared_policy"]["text"], tool_schemas=source["shared_tool_schemas"]["schemas"], judge=recorded_judge)
    if replay.to_dict() != row["admission"] or len(seen) != row["actual_gate_calls"]:
        raise ValueError("Stored admission contract differs from deterministic replay")
    return "semantic_judge" if replay.judge_called else "structural_abstention"


def report():
    manifest = verify()
    seal = load(OUTPUT / "seal.json")["files_sha256"]
    actual = {p.relative_to(OUTPUT).as_posix(): sha(p) for p in OUTPUT.rglob("*")
              if p.is_file() and p.name != "seal.json"}
    if seal != actual:
        raise ValueError("Original C1 execution seal differs")
    results = load(OUTPUT / "results.json")
    by_card = {r["opaque_id"]: r for r in results}
    if len(by_card) != len(results) or set(by_card) - set(manifest["order"]):
        raise ValueError("Duplicate or unassigned calibration result")
    audit = audit_client(OUTPUT / "client") if (OUTPUT / "client/budget.json").exists() else None
    source = load(ROOT / "experiments/C1_CONTROLLED_CASES_DRAFT.json")
    cards = {c["card_id"]: c for c in source["cards"]}
    rows, groups = [], {}
    for reference in manifest["locked_reference"]:
        row = by_card.get(reference["opaque_id"])
        state = row["status"] if row else "not_run"
        semantic = row["semantic_decision"] if row and state == "complete" else None
        label = reference["reference_semantic_truth"]
        groups.setdefault(label, Counter())["assigned"] += 1
        groups[label][semantic or state] += 1
        original = cards[reference["card_id"]]["host_only_truth_proposal"]
        decision_source = replay_unit(row, cards[reference["card_id"]], source) if row and state == "complete" else None
        eligibility = row["strategy_eligibility"] if row and state == "complete" else None
        entry = {**reference, "state": state, "semantic_decision": semantic,
            "actual_strategy_eligibility": eligibility,
            "synthetic_origin": row.get("synthetic_origin") if row else None,
            "compound_admitted": row.get("compound_admitted") if row else None,
            "decision_source": decision_source,
            "expected_editable": original["expected_strategy_eligibility"] == "continue_semantic_review",
            "actual_gate_calls": row.get("actual_gate_calls") if row else None}
        rows.append(entry)
    positive = [r for r in rows if r["reference_semantic_truth"] == "supported" and r["expected_editable"]
                and r["eligibility_reference_agrees"]]
    positive_complete = all(r["state"] == "complete" for r in positive)
    retention = sum(r["compound_admitted"] is True for r in positive) / len(positive) if positive and positive_complete else None
    result = {"schema_version": "c1-synthetic-report-v1", "manifest_sha256": sha(MANIFEST),
        "implementation_sha256": sha(Path(__file__)), "implementation_timing": "read_only_reporting",
        "execution_seal_sha256": sha(OUTPUT / "seal.json"), "synthetic_only": True,
        "planned_cards": len(manifest["order"]), "complete_cards": sum(r["state"] == "complete" for r in rows),
        "native_reviewer_calls": 0, "environment_episodes": 0, "reference_strata": {k: dict(v) for k, v in groups.items()},
        "decision_sources": dict(Counter(r["decision_source"] or r["state"] for r in rows)),
        "editable_supported_retention": {"assigned": len(positive),
            "retained": sum(r["compound_admitted"] is True for r in positive), "complete_rate": retention},
        "rows": rows, "transport_audit": audit,
        "limits": ["AI-authored fixed synthetic cases and AI reference labels; no human gold standard",
            "No natural diagnosis prevalence, natural retention or Agent/GEPA improvement estimate",
            "Origin labels describe synthetic fixture provenance, not real model-generated actions",
            "All planned cards retained; structural abstention, semantic abstention, failure and unrun are separate"]}
    target = ROOT / "results/C1_SYNTHETIC_REPORT.json"
    write_new(target, result)
    target.with_suffix(".sha256").write_text(sha(target) + "\n", encoding="utf-8")
    return {k: v for k, v in result.items() if k not in {"rows", "transport_audit"}}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=2))
