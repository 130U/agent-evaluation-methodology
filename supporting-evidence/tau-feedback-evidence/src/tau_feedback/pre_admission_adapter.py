"""Host-side bound admission before the unchanged GEPA privacy projection.

Interface:
  binding = EpisodeBinding.create(example, strategy=..., episode_id=...,
                                  messages=raw_messages, diagnoses=agent_pool)
  runner returns EpisodeResult(..., trajectory=trajectory_reference(binding),
                              feedback=native_feedback(binding, agent_pool))
  resolver(binding) -> HostEvidence(binding, policy, messages, tool_schemas,
                                    admissions=(BoundAdmission(...), ...))
  adapter = PreAdmissionAdapter(runner, resolver=resolver, arm="B1" or "B2")

The resolver is trusted, local and read-only: it loads completed gate sidecars,
not another model. Bindings detect swaps, not a malicious producer rewriting
all hashes. B2 validates recorded verdicts by pure contract replay and selects
unchanged native diagnoses; it never generates new diagnoses or runs a judge.
Raw policy/messages/tools never enter GEPA traces or reflective records. Raw
runner output must already be saved by the runner: this adapter exposes only a
fixed output marker. Lexical privacy projection is not semantic redaction proof.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import re
from typing import Callable

from gepa.core.adapter import EvaluationBatch

from .admission import AdmissionRecord, audit_diagnosis, select_admitted
from .contracts import canonical_hash, strict_json
from .gepa_adapter import (
    CapturedEpisode, EpisodeInput, EpisodeResult, FeedbackDecision, TauFeedbackAdapter,
    _collect_private,
)


COMMON_CONTEXT = "A retail agent must follow the fixed policy and use tools correctly."
COMMON_OUTPUT = {"status": "episode_evaluated"}
_DIAGNOSIS_FIELDS = {"source", "error_type", "error_tags", "severity", "turn_idx",
                     "tick_start", "tick_end", "reasoning", "correct_behavior"}


def _copy(value):
    return strict_json(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _strategy_hash(strategy):
    if not isinstance(strategy, str) or not strategy.strip():
        raise ValueError("A nonempty strategy is required")
    return hashlib.sha256(strategy.encode("utf-8")).hexdigest()


def _literals(values):
    if not isinstance(values, (tuple, list)) or any(not isinstance(value, str) or not value for value in values):
        raise ValueError("protected_literals must contain nonempty strings")
    return tuple(values)


def _diagnoses(value):
    result = _copy(value)
    if not isinstance(result, list):
        raise ValueError("Native diagnosis pool must be a list, including an explicit empty list")
    for diagnosis in result:
        if not isinstance(diagnosis, dict) or diagnosis.keys() - _DIAGNOSIS_FIELDS or diagnosis.get("source") != "agent":
            raise ValueError("Use only native Agent diagnoses with known fields; select sources before binding")
        if any(not isinstance(diagnosis.get(key), str) or not diagnosis[key].strip()
               for key in ("reasoning", "correct_behavior")):
            raise ValueError("Native diagnosis requires its unchanged claim and correction")
    return result


@dataclass(frozen=True)
class EpisodeBinding:
    task_key: str
    task_payload_sha256: str
    strategy_sha256: str
    episode_id: str
    trajectory_sha256: str
    diagnoses_sha256: str

    def __post_init__(self):
        for value in (self.task_key, self.episode_id):
            if not isinstance(value, str) or not value:
                raise ValueError("Nonempty task and episode identities are required")
        for name in ("task_payload_sha256", "strategy_sha256", "trajectory_sha256", "diagnoses_sha256"):
            if not isinstance(getattr(self, name), str) or re.fullmatch(r"[0-9a-f]{64}", getattr(self, name)) is None:
                raise ValueError("Binding digests must be lowercase SHA-256 values")

    @classmethod
    def create(cls, example: EpisodeInput, *, strategy: str, episode_id: str,
               messages: list[dict], diagnoses: list[dict]):
        if not isinstance(example, EpisodeInput) or not isinstance(example.payload, Mapping):
            raise TypeError("Bind an EpisodeInput with its actual JSON task payload")
        if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
            raise ValueError("Bind the original message list")
        pool = _diagnoses(diagnoses)
        return cls(example.key, canonical_hash(_copy(dict(example.payload))), _strategy_hash(strategy),
                   episode_id, canonical_hash(_copy(messages)), canonical_hash(pool))


@dataclass(frozen=True)
class BoundAdmission:
    binding: EpisodeBinding
    diagnosis_index: int
    record: AdmissionRecord
    judge_call_id: str | None = None


@dataclass(frozen=True)
class HostEvidence:
    binding: EpisodeBinding
    policy: str
    messages: list[dict]
    tool_schemas: list[dict]
    admissions: tuple[BoundAdmission, ...] = ()
    protected_literals: tuple[str, ...] = ()


def native_feedback(binding: EpisodeBinding, diagnoses: list[dict]) -> dict:
    pool = _diagnoses(diagnoses)
    if canonical_hash(pool) != binding.diagnoses_sha256:
        raise ValueError("Diagnosis pool differs from its binding")
    return {"schema_version": "native-agent-feedback-v1", "review_status": "valid",
            "binding": asdict(binding), "diagnoses": pool}


def trajectory_reference(binding: EpisodeBinding) -> dict:
    return {"schema_version": "host-evidence-reference-v1", "binding": asdict(binding)}


def _read_binding(example, episode, strategy_hash):
    feedback, trajectory = episode.feedback, episode.trajectory
    if (not isinstance(feedback, dict) or set(feedback) != {"schema_version", "review_status", "binding", "diagnoses"}
        or feedback["schema_version"] != "native-agent-feedback-v1" or feedback["review_status"] != "valid"):
        raise ValueError("A valid native-review envelope is required; unknown is not empty feedback")
    if not isinstance(feedback["binding"], dict):
        raise ValueError("Missing episode binding")
    binding = EpisodeBinding(**feedback["binding"])
    if (binding.task_key != example.key or binding.task_payload_sha256 != canonical_hash(dict(example.payload))
        or binding.strategy_sha256 != strategy_hash):
        raise ValueError("Feedback belongs to another task payload or strategy")
    pool = _diagnoses(feedback["diagnoses"])
    if canonical_hash(pool) != binding.diagnoses_sha256:
        raise ValueError("Native diagnosis pool was changed after binding")
    if _copy(trajectory) != trajectory_reference(binding):
        raise ValueError("Trajectory must be this episode's opaque host reference, never raw evidence")
    return binding, pool


def _prepared_feedback(view):
    # Selection already happened on raw host data. This stateless pass-through
    # exists solely to use the frozen B2 adapter's common projection path.
    return FeedbackDecision(view.native_feedback, "bound_pre_admission")


class PreAdmissionAdapter(TauFeedbackAdapter):
    """Explicit resolver bindings; no order-based registry or last-case closure."""

    def __init__(self, runner, *, resolver: Callable[[EpisodeBinding], HostEvidence], arm="B1"):
        if not callable(runner) or not callable(resolver):
            raise TypeError("Explicit runner and host evidence resolver are required")
        if arm not in {"B1", "B2"}:
            raise ValueError("arm must be B1 or B2")
        self.resolver = resolver
        self.admission_events = []

        def reference_runner(example, *, strategy, capture_traces):
            episode = runner(example, strategy=strategy, capture_traces=capture_traces)
            if not isinstance(episode, EpisodeResult):
                raise TypeError("Runner must return EpisodeResult")
            if capture_traces:
                _read_binding(example, episode, _strategy_hash(strategy))
            # Raw outputs can contain privileged summaries/rejected diagnoses;
            # never make them available as Generated Outputs in either arm.
            protected = set(_literals(episode.protected_literals))
            protected.update(_collect_private(episode.output))
            return replace(episode, output=deepcopy(COMMON_OUTPUT),
                           trajectory=episode.trajectory if capture_traces else None,
                           feedback=episode.feedback if capture_traces else None,
                           protected_literals=tuple(sorted(protected)))

        super().__init__(reference_runner, arm=arm,
            feedback_filter=_prepared_feedback if arm == "B2" else None,
            filter_name="bound_pre_admission_v1" if arm == "B2" else None)

    def _resolve(self, binding):
        evidence = deepcopy(self.resolver(binding))
        if not isinstance(evidence, HostEvidence) or evidence.binding != binding:
            raise ValueError("Resolver returned evidence for another episode/task/strategy")
        if not isinstance(evidence.messages, list) or any(not isinstance(message, dict) for message in evidence.messages):
            raise ValueError("Resolver must provide the original message list")
        if canonical_hash(evidence.messages) != binding.trajectory_sha256:
            raise ValueError("Resolver trajectory differs from the captured episode")
        if not isinstance(evidence.policy, str) or not evidence.policy.strip() or not isinstance(evidence.tool_schemas, list):
            raise ValueError("Resolver must provide the original policy and tool schemas")
        _literals(evidence.protected_literals)
        return evidence

    @staticmethod
    def _select(binding, pool, evidence):
        if not isinstance(evidence.admissions, (tuple, list)):
            raise ValueError("Explicit bound admission records are required")
        by_index = {}
        for item in evidence.admissions:
            if (not isinstance(item, BoundAdmission) or item.binding != binding
                or type(item.diagnosis_index) is not int or not 0 <= item.diagnosis_index < len(pool)
                or item.diagnosis_index in by_index):
                raise ValueError("Admission episode binding/index is wrong or duplicated")
            by_index[item.diagnosis_index] = item
        if set(by_index) != set(range(len(pool))):
            raise ValueError("Every native diagnosis needs one completed bound admission record")
        records, expected_hashes, call_ids = [], [], []
        selected_indices = []
        for index, diagnosis in enumerate(pool):
            item = by_index[index]
            record = item.record
            if (not isinstance(record, AdmissionRecord) or type(record.judge_called) is not bool
                or record.decision not in {"accept", "reject", "abstain"}
                or not isinstance(record.reason, str) or not record.reason):
                raise ValueError("Invalid admission record contract")
            if record.judge_called:
                if not isinstance(item.judge_call_id, str) or not item.judge_call_id:
                    raise ValueError("A completed model judgement needs its recorded CLI call ID")
            elif item.judge_call_id is not None:
                raise ValueError("A structural abstention cannot claim a judge call")
            # Replay only the deterministic contract with the already recorded
            # verdict. This callable cannot contact a model or invent a verdict.
            stored_verdict = _copy(record.verdict)
            replay = audit_diagnosis(diagnosis=deepcopy(diagnosis), messages=deepcopy(evidence.messages),
                policy=evidence.policy, tool_schemas=deepcopy(evidence.tool_schemas),
                judge=lambda instruction, payload, value=stored_verdict: json.dumps(value, ensure_ascii=False, allow_nan=False))
            if (replay.diagnosis_sha256 != record.diagnosis_sha256 or replay.view_sha256 != record.view_sha256
                or replay.judge_called != record.judge_called or replay.decision != record.decision):
                raise ValueError("Admission diagnosis/view/decision differs from current bound evidence")
            records.append(record)
            expected_hashes.append(replay.view_sha256)
            call_ids.append(item.judge_call_id)
            if record.decision == "accept":
                selected_indices.append(index)
        return (select_admitted(pool, records, expected_view_hashes=expected_hashes), selected_indices, call_ids)

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        if components_to_update not in ([], ["strategy"]):
            raise ValueError("Only strategy may be updated")
        if not components_to_update:
            return super().make_reflective_dataset(candidate, eval_batch, components_to_update)
        if not isinstance(candidate, dict) or set(candidate) != {"strategy"}:
            raise ValueError("Candidate must contain only strategy")
        candidate_hash = _strategy_hash(candidate["strategy"])
        traces = eval_batch.trajectories
        if traces is None or len(traces) != len(eval_batch.outputs) or len(traces) != len(eval_batch.scores):
            raise ValueError("Aligned captured episodes, outputs and scores are required")
        prepared_traces, prepared_outputs, pending_events = [], [], []
        for trace, output, score in zip(traces, eval_batch.outputs, eval_batch.scores):
            if not isinstance(trace, CapturedEpisode) or trace.candidate_sha256 != candidate_hash:
                raise ValueError("Captured episode belongs to another strategy")
            example, episode = trace.example, trace.episode
            if (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1
                or type(episode.score) not in (int, float) or score != episode.score
                or canonical_hash(output) != canonical_hash(episode.output)):
                raise ValueError("Outputs or scores differ from the captured episode")
            binding, pool = _read_binding(example, episode, candidate_hash)
            evidence = self._resolve(binding)
            if self.arm == "B2":
                chosen, indices, call_ids = self._select(binding, pool, evidence)
            else:
                chosen, indices, call_ids = deepcopy(pool), list(range(len(pool))), []
            # Both arms derive exactly the same privacy literals from the whole
            # native pool before selection, not from gate reasons or decisions.
            protected = {*_literals(example.protected_literals), *_literals(episode.protected_literals),
                         *_literals(evidence.protected_literals)}
            for source in (example.payload, episode.output, pool, evidence.messages):
                protected.update(_collect_private(source))
            safe_example = replace(example, reflection_context=COMMON_CONTEXT,
                                   protected_literals=tuple(sorted(protected)))
            # An empty native pool is the same input condition in both arms;
            # do not introduce an unrelated wording treatment before selection.
            feedback = ({"status": "no_native_diagnoses"} if not pool else
                        ({"diagnoses": chosen} if chosen else None))
            safe_episode = replace(episode, output=deepcopy(COMMON_OUTPUT),
                                   feedback=feedback, protected_literals=tuple(sorted(protected)))
            prepared_traces.append(CapturedEpisode(safe_example, safe_episode, candidate_hash))
            prepared_outputs.append(deepcopy(COMMON_OUTPUT))
            pending_events.append({"binding": asdict(binding), "arm": self.arm,
                "native_count": len(pool), "selected_indices": indices, "judge_call_ids": call_ids})
        prepared = EvaluationBatch(outputs=prepared_outputs, scores=list(eval_batch.scores),
            trajectories=prepared_traces, num_metric_calls=eval_batch.num_metric_calls,
            objective_scores=deepcopy(eval_batch.objective_scores))
        result = super().make_reflective_dataset(candidate, prepared, components_to_update)
        self.admission_events.extend(pending_events)
        return result
