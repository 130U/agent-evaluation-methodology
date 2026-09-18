"""Bind real reviewer/gate sidecars to the CLI ledger before GEPA can use them.

These checks detect stale/swapped/changed artifacts, not a malicious writer
replacing all records. Only already complete calls can enter the host store.
Nothing here certifies the semantic accuracy of a model's diagnosis.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .admission import AdmissionRecord
from .contracts import canonical_hash, strict_json
from .pre_admission_adapter import BoundAdmission, EpisodeBinding, HostEvidence


def load(path):
    return strict_json(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _within(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Evidence path escapes its research root")
    return path


def verify_cli_call(client, call_id, *, role, model_sidecar):
    """Read the actual client files, not only a caller-supplied call-id string."""
    matches = [r for r in client.records if r.get("call_id") == call_id]
    if len(matches) != 1:
        raise ValueError("CLI call identity is missing or ambiguous")
    current = matches[0]
    directory = _within(current["directory"], client.output)
    if directory.name != call_id:
        raise ValueError("CLI call directory does not match its identity")
    request, outcome = load(directory / "request.json"), load(directory / "outcome.json")
    if outcome != current:
        raise ValueError("Disk CLI outcome differs from the current ledger")
    if (outcome.get("status") != "complete" or outcome.get("role") != role
        or outcome.get("call_id") != call_id or outcome.get("usage_complete") is not True):
        raise ValueError("CLI call was not fully accepted for this role")
    if request.get("role") != role or request.get("call_id") != call_id:
        raise ValueError("CLI request identity/role differs")
    sidecar = _within(model_sidecar, client.root)
    side_request, side_outcome = load(sidecar / "request.json"), load(sidecar / "outcome.json")
    if (side_outcome.get("status") != "complete" or side_outcome.get("usage_complete") is not True
        or side_outcome.get("cli_call_id") != call_id or side_outcome.get("role") != role
        or side_outcome.get("usage") != outcome.get("usage")):
        raise ValueError("Model sidecar is not bound to this complete CLI call")
    for field in ("prompt", "schema", "role"):
        if side_request.get(field) != request.get(field):
            raise ValueError("Sidecar and CLI request differ: " + field)
    if (canonical_hash(side_outcome.get("output")) != canonical_hash(outcome.get("output"))
        or canonical_hash(load(directory / "final.json")) != canonical_hash(outcome.get("output"))):
        raise ValueError("Sidecar/final/CLI outputs differ")
    # The accepted client already checks events, stderr, usage and owned-tree
    # cleanup. Hash those raw records too, so later mutation invalidates resolve.
    required = ["request.json", "outcome.json", "final.json", "events.jsonl", "stderr.txt"]
    return [directory / name for name in required] + [sidecar / "request.json", sidecar / "outcome.json"]


def choose_agent_pool(diagnoses, *, trajectory_sha256, salt, cap):
    if type(cap) is not int or cap <= 0 or not isinstance(salt, str) or not salt:
        raise ValueError("Freeze a positive diagnosis cap and nonempty sampling salt")
    agents = [(i, deepcopy(d)) for i, d in enumerate(diagnoses) if d.get("source") == "agent"]
    ranked = sorted(agents, key=lambda item: canonical_hash([salt, trajectory_sha256, item[0]]))
    selected = sorted(ranked[:cap], key=lambda item: item[0])
    return [d for _, d in selected], [i for i, _ in selected], len(agents)


class SubscriptionEvidenceStore:
    def __init__(self, client, *, output):
        self.client = client
        self.output = _within(output, client.root)
        self.output.mkdir(parents=True, exist_ok=False)
        self.entries = {}

    def add(self, *, binding: EpisodeBinding, policy, messages, tool_schemas,
            review, native_indices, gate_directories=(), protected_literals=(), source_artifacts=(), arm):
        if arm not in {"B1", "B2"} or binding.episode_id in self.entries:
            raise ValueError("Use one fresh evidence binding per episode")
        diagnoses = review.require_valid_diagnoses()
        if (not isinstance(native_indices, list) or any(type(i) is not int or not 0 <= i < len(diagnoses) for i in native_indices)
            or len(set(native_indices)) != len(native_indices)):
            raise ValueError("Invalid native diagnosis indices")
        pool = [diagnoses[i] for i in native_indices]
        if (any(d.get("source") != "agent" for d in pool)
            or canonical_hash(pool) != binding.diagnoses_sha256
            or canonical_hash(messages) != binding.trajectory_sha256):
            raise ValueError("Host binding does not match the original diagnosis pool/trajectory")
        review_dir = _within(review.sidecar_directory, self.client.root)
        review_outcome, review_binding = load(review_dir / "outcome.json"), load(review_dir / "binding.json")
        if (review_outcome.get("status") != "valid" or review_outcome.get("cli_call_id") != review.call_id
            or review_outcome.get("diagnoses") != list(diagnoses)
            or review_outcome.get("native_review") != review.native_review):
            raise ValueError("Reviewer object differs from its immutable sidecar")
        expected = {"task_id": binding.task_key, "task_sha256": binding.task_payload_sha256,
                    "simulation_id": binding.episode_id, "trajectory_sha256": binding.trajectory_sha256,
                    "policy_sha256": canonical_hash(policy)}
        if any(review_binding.get(k) != v for k, v in expected.items()):
            raise ValueError("Native review belongs to another task/episode/trajectory/policy")
        artifacts = verify_cli_call(self.client, review.call_id, role="llm_judge_review",
                                    model_sidecar=review_dir / "model_call")
        if load(review_dir / "native_review.json") != review.native_review:
            raise ValueError("Parsed native review file differs from the reviewer object")
        # Pure parser replay from the accepted model text; no second judgement.
        from tau2.evaluator.review_llm_judge import _parse_review_response
        raw = load(review_dir / "model_call/outcome.json")["output"]["text"]
        summary, agent_error, user_error, critical_user_error, has_errors, errors = _parse_review_response(raw)
        parsed = {"summary": summary, "agent_error": agent_error, "user_error": user_error,
                  "critical_user_error": critical_user_error, "has_errors": has_errors,
                  "errors": [e.model_dump(mode="json") for e in errors]}
        if any(review.native_review.get(k) != v for k, v in parsed.items()):
            raise ValueError("Native review differs from the accepted model text parser replay")
        artifacts += [review_dir / name for name in ("outcome.json", "binding.json", "native_review.json")]
        artifacts += [_within(p, self.client.root) for p in source_artifacts]
        if (arm == "B1" and gate_directories) or (arm == "B2" and len(gate_directories) != len(pool)):
            raise ValueError("Gate count does not match the arm and selected native pool")
        admissions = []
        for index, directory in enumerate(gate_directories):
            directory = _within(directory, self.client.root)
            outcome, request = load(directory / "outcome.json"), load(directory / "request.json")
            if outcome.get("status") != "complete":
                raise ValueError("Gate sidecar is not complete")
            expected_gate = {"diagnosis_sha256": canonical_hash(pool[index]),
                             "trajectory_sha256": binding.trajectory_sha256,
                             "policy_sha256": canonical_hash(policy), "tools_sha256": canonical_hash(tool_schemas)}
            if (outcome.get("binding") != expected_gate or request.get("binding") != expected_gate
                or request.get("diagnosis") != pool[index]):
                raise ValueError("Gate belongs to another diagnosis/view")
            record = AdmissionRecord(**outcome["admission"])
            calls = outcome.get("judge_calls")
            if not isinstance(calls, list) or len(calls) != (1 if record.judge_called else 0):
                raise ValueError("Gate judge-call count is inconsistent")
            call_id = None
            if calls:
                call_id = calls[0]["cli_call_id"]
                artifacts += verify_cli_call(self.client, call_id, role="feedback_admission",
                                             model_sidecar=calls[0]["directory"])
                model_outcome = load(Path(calls[0]["directory"]) / "outcome.json")
                if model_outcome["output"] != record.verdict:
                    raise ValueError("Saved admission verdict differs from the actual model output")
            admissions.append(BoundAdmission(binding, index, record, call_id))
            artifacts += [directory / "outcome.json", directory / "request.json"]
        evidence = HostEvidence(binding, policy, deepcopy(messages), deepcopy(tool_schemas),
                                tuple(admissions), tuple(protected_literals))
        path = self.output / (canonical_hash(asdict(binding)) + ".json")
        hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
        value = {"binding": asdict(binding), "arm": arm, "native_indices": native_indices,
                 "evidence": asdict(evidence), "artifact_sha256": hashes,
                 "boundary": "Host-only evidence; not model reflection input"}
        save(path, value)
        self.entries[binding.episode_id] = (deepcopy(evidence), path, hashlib.sha256(path.read_bytes()).hexdigest(), hashes)
        return evidence

    def resolve(self, binding):
        if binding.episode_id not in self.entries:
            raise ValueError("No completed host evidence for this episode")
        evidence, path, digest, hashes = self.entries[binding.episode_id]
        if evidence.binding != binding or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Host evidence binding or saved record changed")
        for name, expected in hashes.items():
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
                raise ValueError("A bound source artifact changed: " + name)
        return deepcopy(evidence)
