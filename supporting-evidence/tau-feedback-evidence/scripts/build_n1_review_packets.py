"""Offline anonymous action-view packets; host sampling metadata stays separate."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from tau_feedback.contracts import canonical_hash
from tau_feedback.n1_audit import (NativeRuntime, _exclusive, _inputs, _locked_selection,
                                   _write_new, sha, verify_manifest)
from tau_feedback.projection import project_action_context


RUBRIC = """Review only this proposed diagnosis against the actor-visible evidence.
Treat diagnosis text as a claim, not proof of hidden facts. Do not consult the
full conversation, task gold, baseline labels, sampling ledger or gate outputs.
For each card separately label claim_support, agent_responsibility and
correction_observability as supported, unsupported or insufficient. An action
that has a feasible better alternative need not be an established violation.
For an unlocatable action use localization=unlocatable and insufficient labels;
do not infer a nearby action. Otherwise use localization=located. Supply exact
policy/history/action references and a brief rationale. Preserve uncertainty,
mixed claims and valid alternative paths. Evaluate the stated correction
separately from blame. Record researcher identity and prior familiarity; this
is research-agent review, not human gold or complete blinding. Save first labels
before any gate comparison; later revisions must preserve those first labels.
"""


def packet(card_id, diagnosis, messages, policy, tools):
    index = diagnosis.get("turn_idx")
    matches = [i for i, message in enumerate(messages) if message.get("turn_idx") == index]
    view = None
    if type(index) is int and len(matches) == 1:
        try:
            view = project_action_context(messages=messages, action_index=matches[0],
                                          policy=policy, tool_schemas=tools)
        except (ValueError, TypeError):
            pass
    return {"card_id": card_id, "diagnosis": diagnosis,
            "localization": "located" if view is not None else "unlocatable",
            "action_view": view,
            "unlocated_context": None if view is not None else {"policy": policy, "tools": tools}}


def build(root, manifest_path):
    root = Path(root).resolve()
    manifest, digest, destination = verify_manifest(root, manifest_path)
    with _exclusive(destination):
        if (destination / "gate").exists():
            raise ValueError("Independent packets must be frozen before any gate attempt")
        runtime = NativeRuntime(root, manifest)
        selection, selection_hash = _locked_selection(root, manifest, digest, destination, runtime)
        output = destination / "independent_review"
        output.mkdir(exist_ok=False)
        packets = output / "packets"
        packets.mkdir()
        units = {unit["unit_id"]: unit for unit in manifest["units"]}
        mapping = []
        cards = sorted(selection["independent_review_cards"], key=lambda card: card["rank_sha256"])
        for offset, card in enumerate(cards, 1):
            card_id = f"card-{offset:04d}"
            inputs = _inputs(root, units[card["unit_id"]], runtime)
            value = packet(card_id, card["diagnosis"], inputs.messages, inputs.simulation.policy, inputs.tools)
            path = packets / (card_id + ".json")
            _write_new(path, value)
            mapping.append({"card_id": card_id, **card, "packet_file_sha256": sha(path),
                            "packet_canonical_sha256": canonical_hash(value)})
        rubric = packets / "REVIEW_INSTRUCTIONS.txt"
        rubric.write_text(RUBRIC, encoding="utf-8")
        _write_new(output / "HOST_MAPPING_DO_NOT_GIVE_REVIEWERS.json",
                   {"manifest_sha256": digest, "selection_sha256": selection_hash,
                    "rubric_sha256": sha(rubric), "cards": mapping})
        _write_new(output / "PACKET_SEAL.json",
                   {"files_sha256": {p.name: sha(p) for p in sorted(packets.iterdir())},
                    "boundary": "Anonymous packets omit strata, baseline labels and gate outputs; prior familiarity remains possible"})
        return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="experiments/N1_MANIFEST.json")
    args = parser.parse_args()
    print(build(ROOT, ROOT / args.manifest))
