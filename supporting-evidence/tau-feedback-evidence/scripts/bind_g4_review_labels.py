"""Bind saved anonymous first labels to original sources without relabeling."""
import argparse
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
from register_g4_paired import ROOT, verify, sha, load, write_new


def bind(phase, reviewer, label_path):
    _, digest = verify()
    directory = ROOT / "research/g4-review" / phase
    map_path = directory / "HOST_MAPPING_DO_NOT_GIVE_REVIEWERS.json"
    seal_path = directory / "PACKET_SEAL.json"
    mapping, seal = load(map_path), load(seal_path)
    label_path = Path(label_path).resolve()
    if not label_path.is_relative_to(ROOT):
        raise ValueError("Original labels must be within this research project")
    labels = load(label_path)
    if (labels.get("reviewer") != reviewer or labels.get("manifest_sha256") != digest
            or mapping["manifest_sha256"] != digest or seal["manifest_sha256"] != digest
            or labels.get("packet_seal_sha256") != sha(seal_path)
            or mapping["packet_seal_sha256"] != sha(seal_path)):
        raise ValueError("Review identity, registration or packet seal differs")
    if not labels.get("prior_familiarity"):
        raise ValueError("Explicit prior-familiarity disclosure required")
    for name, expected in mapping["execution_source_sha256"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("Captured execution source changed")
    packet_files = directory / "packets"
    if {p.name: sha(p) for p in packet_files.iterdir()} != seal["packet_files_sha256"]:
        raise ValueError("Anonymous packet set changed")
    lookup = {r["review_id"]: r for r in mapping["mapping"]}
    items = labels.get("items", [])
    ids = [r.get("review_id") for r in items]
    if len(ids) != len(lookup) or set(ids) != set(lookup):
        raise ValueError("Every assigned anonymous packet needs one first label")
    mapped = deepcopy(labels)
    for item in mapped["items"]:
        row = lookup[item["review_id"]]
        packet = load(ROOT / row["packet_file"])
        if item.get("packet_sha256") != row["packet_sha256"] or item.get("artifact_sha256") != packet["artifact_sha256"]:
            raise ValueError("Label does not bind exactly this packet's five artifacts")
        for field in ("semantic_status", "user_validity"):
            if item.get(field) not in {"pass", "fail", "unknown"}:
                raise ValueError("Explicit three-way review status required")
        for field in ("critical_violation", "critical_user_error"):
            if field not in item or (item[field] is not None and type(item[field]) is not bool):
                raise ValueError("Explicit critical flag required")
        if not item.get("evidence") or "opportunities" not in item or "general_violations" not in item:
            raise ValueError("Evidence and frozen opportunity labels required")
        item["artifact_sha256"] = row["artifact_sha256"]
    provenance = [label_path, map_path, seal_path, Path(__file__), *packet_files.iterdir()]
    mapped.update(source_sha256={p.resolve().relative_to(ROOT).as_posix(): sha(p) for p in provenance},
        mapping_created_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        mapping_rule="Only artifact basename keys changed to original project-relative paths; all labels and evidence preserved",
        mapping_implementation_timing="Post-registration read-only provenance mapping")
    target = ROOT / "research" / f"G4_{phase.upper()}_{reviewer.upper()}_BOUND_REVIEW.json"
    write_new(target, mapped)
    target.with_suffix(".sha256").write_text(sha(target) + "\n", encoding="utf-8")
    return {"path": str(target), "sha256": sha(target), "items": len(items), "model_calls": 0}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=("cohort", "test"))
    p.add_argument("reviewer", choices=("red", "related"))
    p.add_argument("labels")
    a = p.parse_args()
    print(json.dumps(bind(a.phase, a.reviewer, a.labels), ensure_ascii=False))
