# Post-registration binding fix; original source remains frozen.
# Original SHA256: a5c7932771fc28f34c62d0bb6ba1c6a9cea942c7cc7b9d8d07b244f9f717b257
"""Seal final policies after two explicit AI reviews; never open test feedback."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from register_g3_pilot import ID, verify, load, sha, write_new, within
from run_g3_pilot import verify_arm


def read_review(path, manifest_digest, episode_dirs):
    review = load(path)
    if review.get("manifest_sha256") != manifest_digest or not review.get("reviewer"):
        raise ValueError("Review is not identified and bound to G3")
    indexed = {}
    for row in review.get("episodes", []):
        name = row["relative_directory"]
        if name not in episode_dirs or name in indexed:
            raise ValueError("Duplicate or unknown review episode")
        directory = within(ROOT, name)
        expected = {n: sha(directory / n) for n in ("request.json", "outcome.json", "trajectory.json", "simulation.json", "calls.jsonl")}
        if row.get("source_hashes") != expected:
            raise ValueError("Review does not match frozen episode artifacts")
        if (row.get("semantic_status") not in {"pass", "fail", "unknown"}
            or row.get("user_validity") not in {"pass", "fail", "unknown"}
            or (row.get("critical_violation") is not None and type(row["critical_violation"]) is not bool)
            or type(row.get("critical_user_error")) is not bool
            or not isinstance(row.get("evidence"), list) or not row["evidence"]):
            raise ValueError("Review lacks typed judgments and concrete evidence")
        indexed[name] = row
    if set(indexed) != set(episode_dirs):
        raise ValueError("Every completed optimization episode requires review")
    return review["reviewer"], indexed



def bind_candidate_for_acceptance(gepa_best, seed, candidate_rows):
    """Keep GEPA's selection flag separate from the evaluated child's text."""
    if gepa_best != seed or not candidate_rows:
        return gepa_best
    hashes = {row["strategy_sha256"] for row in candidate_rows}
    if len(hashes) != 1:
        raise ValueError("One-proposal study has multiple validation candidate strategies")
    texts = {load(within(ROOT, row["relative_directory"]) / "request.json")["strategy"]
             for row in candidate_rows}
    if len(texts) != 1:
        raise ValueError("Candidate validation requests disagree on strategy text")
    child = next(iter(texts))
    if child == seed or hashlib.sha256(child.encode()).hexdigest() not in hashes:
        raise ValueError("Candidate validation request does not match bound strategy hash")
    return child


def run(review_a, review_b):
    manifest, digest = verify()
    stage = ROOT / "results/g3" / ID
    target = stage / "FINAL_STRATEGIES.json"
    if target.exists() or (stage / "STOP.json").exists() or (stage / "test").exists():
        raise RuntimeError("Cannot replace selection, select after test, or reopen a stopped study")
    episodes = {}
    for arm in manifest["arm_order"]:
        verify_arm(stage / arm, arm, digest)
        for directory in sorted((stage / arm / "optimizer/episodes").glob("episode-*/environment")):
            episodes[directory.relative_to(ROOT).as_posix()] = (arm, directory)
    name_a, a = read_review(review_a, digest, episodes)
    name_b, b = read_review(review_b, digest, episodes)
    if name_a == name_b or Path(review_a).resolve() == Path(review_b).resolve():
        raise ValueError("Two separately identified research-agent reviews required")
    user_alarms = [name for name in episodes if a[name]["critical_user_error"] or b[name]["critical_user_error"]]
    if user_alarms:
        # This concerns simulator fidelity, not ordinary agent task failure.
        # Even a disputed critical alarm cannot silently enter policy selection.
        write_new(stage / "STOP.json", {"stage": "independent_optimization_review",
            "manifest_sha256": digest, "reason": "critical_user_error_flag_requires_adjudication",
            "episode_directories": user_alarms,
            "review_hashes": [sha(review_a), sha(review_b)], "no_retry": True})
        raise RuntimeError("Critical user fidelity alarm in optimization; retain all evidence and stop")
    merged, by_arm = [], {"B1": [], "B2": []}
    for relative, (arm, directory) in episodes.items():
        outcome, request = load(directory / "outcome.json"), load(directory / "request.json")
        values = {}
        for field in ("semantic_status", "user_validity", "critical_violation"):
            values[field] = a[relative][field] if a[relative][field] == b[relative][field] else (None if field == "critical_violation" else "unknown")
        row = {"task_id": outcome["task_id"], "strategy_sha256": hashlib.sha256(request["strategy"].encode()).hexdigest(),
            "native_reward": outcome["native_reward"], "structural_status": outcome["structural_outcome"]["status"],
            "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"], **values,
            "relative_directory": relative, "arm": arm, "run_id": outcome["run_id"]}
        merged.append(row)
        if row["task_id"] in manifest["splits"]["validation"]:
            by_arm[arm].append(row)
    from tau_feedback.g3_acceptance import decide_strategy
    seed = manifest["seed_strategy"]
    seed_hash = hashlib.sha256(seed.encode()).hexdigest()
    decisions = {"B0": {"final_strategy": seed, "decision": "fixed_baseline"}}
    for arm in manifest["arm_order"]:
        candidate = load(stage / arm / "optimizer/best_candidate.json")["strategy"]
        baseline = [r for r in by_arm[arm] if r["strategy_sha256"] == seed_hash]
        candidate_rows = [r for r in by_arm[arm] if r["strategy_sha256"] != seed_hash]
        selected_new = candidate != seed
        candidate = bind_candidate_for_acceptance(candidate, seed, candidate_rows)
        decisions[arm] = decide_strategy(seed, candidate, selected_new, baseline, candidate_rows,
                                         manifest["splits"]["validation"])
    value = {"manifest_sha256": digest, "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "review_mode": "Two AI research agents; disagreement becomes unknown, not human gold",
        "reviews": [{"reviewer": name_a, "path": str(Path(review_a).resolve()), "sha256": sha(review_a)},
                    {"reviewer": name_b, "path": str(Path(review_b).resolve()), "sha256": sha(review_b)}],
        "selection_implementation": {"script": "scripts/finalize_g3_strategies_v2.py",
            "script_sha256": sha(Path(__file__)), "timing": "post_registration_binding_bug_fix",
            "original_script_sha256": sha(ROOT / "scripts/finalize_g3_strategies.py"),
            "amendment": "research/G3_SELECTION_BINDING_AMENDMENT.md",
            "amendment_sha256": sha(ROOT / "research/G3_SELECTION_BINDING_AMENDMENT.md")},
        "optimization_judgments": merged, "arms": decisions,
        "strategy_sha256": {arm: hashlib.sha256(d["final_strategy"].encode()).hexdigest() for arm, d in decisions.items()}}
    write_new(target, value)
    target.with_suffix(".sha256").write_text(sha(target) + "\n", encoding="utf-8")
    return {"path": str(target), "sha256": sha(target), "decisions": {k: v["decision"] for k, v in decisions.items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-a", required=True)
    parser.add_argument("--review-b", required=True)
    args = parser.parse_args()
    print(json.dumps(run(Path(args.review_a), Path(args.review_b)), ensure_ascii=False))
