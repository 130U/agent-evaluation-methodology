"""Separately registered synthetic admission calibration; zero native reviews."""
import argparse
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from tau_feedback.contracts import canonical_hash
from tau_feedback.action_origin import OriginContext, classify_action_origin, strategy_feedback_eligibility
from tau_feedback.subscription_evidence import load, save
from tau_feedback.projection import project_action_context
from tau_feedback.subscription_feedback import audit_native_diagnosis
from tau_feedback.subscription_recovery_client import RecoveringHttpCliTextClient
from tau_feedback.subscription_client import EXE_SHA256, MODEL_CATALOG_SHA256
from tau_feedback.recovered_events import POLICY_ID

ID = "c1-synthetic-admission-calibration-v1"
MANIFEST = ROOT / "experiments/C1_SYNTHETIC_MANIFEST.json"
SOURCE = "experiments/C1_CONTROLLED_CASES_DRAFT.json"
PACKET = "research/C1_BLIND_PACKET.json"
MAPPING = "research/C1_BLIND_MAP.json"
LABELS = "research/C1_RED_BLIND_LABELS.json"
OUTPUT = ROOT / "results/c1" / ID
LIMITS = {"max_calls": 12, "max_input_tokens": 350000, "max_output_tokens": 12000,
          "max_seconds": 1200, "request_timeout": 120}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def validate_materials(data, mapping, labels):
    if mapping["source_sha256"] != sha(ROOT / SOURCE) or mapping["packet_sha256"] != sha(ROOT / PACKET):
        raise ValueError("Blind source/packet mapping changed")
    if labels["packet_sha256"] != sha(ROOT / PACKET) or labels["reviewer"] != "red_team":
        raise ValueError("Independent pre-gate labels bind another packet")
    lookup = {item["opaque_id"]: item for item in labels["cards"]}
    if len(lookup) != 12 or len(labels["cards"]) != 12 or set(lookup) != {r["opaque_id"] for r in mapping["mapping"]}:
        raise ValueError("Every fixed synthetic card needs one independent label")
    policy, schemas = data["shared_policy"]["text"], data["shared_tool_schemas"]["schemas"]
    if canonical_hash(policy) != data["shared_policy"]["canonical_text_sha256"]:
        raise ValueError("Shared policy differs")
    if canonical_hash(schemas) != data["shared_tool_schemas"]["canonical_sha256"]:
        raise ValueError("Shared tools differ")
    resolved = []
    for row in mapping["mapping"]:
        card = data["cards"][row["source_card_index"]]
        if card["card_id"] != row["original_card_id"] or canonical_hash(card) != row["source_card_sha256"]:
            raise ValueError("Synthetic card mapping differs")
        if card["native_reviewer_called"] is not False or card["native_call_id"] is not None:
            raise ValueError("Synthetic cards must not claim native review provenance")
        stimulus, binding = card["stimulus"], card["bindings"]
        indices = [i for i, message in enumerate(stimulus["messages"])
                   if message.get("turn_idx") == stimulus["diagnosis"]["turn_idx"]]
        if len(indices) != 1:
            raise ValueError("Synthetic diagnosis needs one exact action index")
        view = project_action_context(messages=stimulus["messages"], policy=policy, tool_schemas=schemas,
                                      action_index=indices[0])
        if (canonical_hash(stimulus["diagnosis"]) != binding["diagnosis_sha256"]
            or canonical_hash(stimulus["messages"]) != binding["messages_sha256"]
            or view["view_sha256"] != binding["view_sha256"]):
            raise ValueError("Synthetic input/view binding changed")
        author, independent = card["host_only_truth_proposal"], lookup[row["opaque_id"]]
        dimensions = [independent[k] for k in ("claim_support", "agent_responsibility", "correction_observability")]
        if any(d not in {"supported", "unsupported", "insufficient"} for d in dimensions):
            raise ValueError("Invalid independent reference label")
        independent_truth = ("supported" if all(d == "supported" for d in dimensions) else
                             "unsupported" if "unsupported" in dimensions else "insufficient")
        decision_map = {"supported": "accept", "unsupported": "reject", "insufficient": "abstain"}
        if independent["semantic_decision"] != decision_map[independent_truth]:
            raise ValueError("Independent dimensions and semantic decision conflict")
        if independent["view_sha256"] != view["view_sha256"] or independent["packet_card_sha256"] != row["packet_card_sha256"]:
            raise ValueError("Independent label binds another action view/card")
        truth = author["semantic_truth"] if author["semantic_truth"] == independent_truth else "disputed"
        resolved.append({"opaque_id": row["opaque_id"], "card_id": card["card_id"],
            "author_semantic_truth": author["semantic_truth"], "independent_semantic_truth": independent_truth,
            "reference_semantic_truth": truth, "author_expected_decision": author["expected_semantic_decision"],
            "independent_expected_decision": independent["semantic_decision"],
            "origin_reference_agrees": author["expected_origin"] == independent["origin"],
            "eligibility_reference_agrees": author["expected_strategy_eligibility"] == independent["strategy_eligibility"]})
    return resolved


def build():
    data, mapping, labels = load(ROOT / SOURCE), load(ROOT / MAPPING), load(ROOT / LABELS)
    reference = validate_materials(data, mapping, labels)
    names = {SOURCE, PACKET, MAPPING, LABELS, "research/C1_SYNTHETIC_PROTOCOL.md",
             "research/C1_CONTROLLED_CALIBRATION_DRAFT.md", "research/C1_EXECUTION_REVIEW.md", "scripts/c1_synthetic_calibration.py",
             "src/tau_feedback/subscription_recovery_client.py", "src/tau_feedback/recovered_events.py",
             "src/tau_feedback/subscription_client.py", "src/tau_feedback/subscription_http_client.py",
             "src/tau_feedback/subscription_evidence.py", "src/tau_feedback/contracts.py"}
    for name, digest in data["source_hashes"].items():
        if sha(ROOT / name) != digest:
            raise ValueError("Synthetic source changed: " + name)
        names.add(name)
    return {"manifest_id": ID, "registered_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stage": "synthetic_admission_calibration", "source_kind": "AI_authored_synthetic_fixtures",
        "model": "gpt-5.6-sol", "effort": "low", "event_policy": POLICY_ID,
        "native_reviewer_calls": 0, "native_call_id": None, "environment_episodes": 0,
        "limits": LIMITS, "runner_retries": 0, "order": [r["opaque_id"] for r in mapping["mapping"]],
        "locked_reference": reference, "exe_sha256": EXE_SHA256, "catalog_sha256": MODEL_CATALOG_SHA256,
        "source_sha256": {name: sha(ROOT / name) for name in sorted(names)},
        "interpretation": "Fixed synthetic reference calibration, not natural diagnosis accuracy or Agent/GEPA uplift"}


def register():
    if MANIFEST.exists() or MANIFEST.with_suffix(".sha256").exists() or OUTPUT.exists():
        raise ValueError("Never overwrite C1 registration or results")
    value = build()
    write_new(MANIFEST, value)
    MANIFEST.with_suffix(".sha256").write_text(sha(MANIFEST) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(MANIFEST), "sha256": sha(MANIFEST), "model_calls": 0}))


def verify():
    if sha(MANIFEST) != MANIFEST.with_suffix(".sha256").read_text().split()[0]:
        raise ValueError("C1 manifest changed")
    manifest = load(MANIFEST)
    for name, digest in manifest["source_sha256"].items():
        if sha(ROOT / name) != digest:
            raise ValueError("Frozen C1 source changed: " + name)
    reference = validate_materials(load(ROOT / SOURCE), load(ROOT / MAPPING), load(ROOT / LABELS))
    if reference != manifest["locked_reference"] or manifest["limits"] != LIMITS:
        raise ValueError("C1 reference/budget changed")
    return manifest


def run():
    manifest = verify()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    write_new(OUTPUT / "PLAN.json", {"manifest_sha256": sha(MANIFEST), "order": manifest["order"],
        "scope": "Synthetic semantic-only probes; deterministic origin eligibility reported separately"})
    client, results = None, []
    try:
        client = RecoveringHttpCliTextClient(ROOT, OUTPUT / "client", model=manifest["model"], effort=manifest["effort"], **manifest["limits"])
        data, mapping = load(ROOT / SOURCE), load(ROOT / MAPPING)
        for unit, row in enumerate(mapping["mapping"], 1):
            card = data["cards"][row["source_card_index"]]
            directory = OUTPUT / f"unit-{unit:02d}"
            directory.mkdir()
            record = {"unit": unit, "opaque_id": row["opaque_id"], "card_id": card["card_id"], "status": "reserved",
                      "synthetic_only": True, "native_reviewer_called": False, "native_call_id": None}
            save(directory / "unit.json", record)
            try:
                s, origin_fixture = card["stimulus"], card["synthetic_origin_fixture"]
                action = next(m for m in s["messages"] if m["turn_idx"] == s["diagnosis"]["turn_idx"])
                origin = classify_action_origin(action, origin_fixture["calls"], OriginContext(**origin_fixture["context"]))
                route = strategy_feedback_eligibility(origin)
                before = client.calls
                decision = audit_native_diagnosis(client, diagnosis=s["diagnosis"], messages=s["messages"],
                    policy=data["shared_policy"]["text"], tool_schemas=data["shared_tool_schemas"]["schemas"], output=directory / "gate")
                if client.halt_reason or client.unknown_usage_calls:
                    raise RuntimeError("C1 transport/usage incomplete")
                record.update(status="complete", semantic_decision=decision.decision, admission=decision.to_dict(),
                    synthetic_origin=origin, strategy_eligibility=route, actual_gate_calls=client.calls - before,
                    compound_admitted=(decision.decision == "accept" and route == "continue_semantic_review"))
            except BaseException as exc:
                record.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
                raise
            finally:
                save(directory / "unit.json", record)
                results.append(record)
            print(json.dumps({"unit": unit, "status": record["status"], "cli_calls": client.calls}), flush=True)
        verify()
        write_new(OUTPUT / "COMPLETE.json", {"status": "complete", "cards": len(results), "model_calls": client.calls,
            "native_reviewer_calls": 0, "input_tokens": client.input_tokens, "output_tokens": client.output_tokens,
            "unknown_usage_calls": client.unknown_usage_calls})
    except BaseException as exc:
        write_new(OUTPUT / "STOP.json", {"status": "stopped", "error_type": type(exc).__name__, "error": str(exc), "no_retry": True,
            "client_initialized": client is not None, "known_cli_invocations": client.calls if client is not None else 0,
            "input_tokens": client.input_tokens if client is not None else None,
            "output_tokens": client.output_tokens if client is not None else None,
            "usage_scope": "No generation called before client construction completes; uninitialized token ledger is unavailable"})
        raise
    finally:
        save(OUTPUT / "results.json", results)
        write_new(OUTPUT / "seal.json", {"files_sha256": {p.relative_to(OUTPUT).as_posix(): sha(p)
            for p in OUTPUT.rglob("*") if p.is_file()}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "verify", "run"))
    args = parser.parse_args()
    if args.mode == "register":
        register()
    elif args.mode == "run":
        run()
    else:
        print(json.dumps({"manifest_id": verify()["manifest_id"], "model_calls": 0}))
