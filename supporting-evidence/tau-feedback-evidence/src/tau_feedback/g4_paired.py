"""Prospective G4 paired inputs and actual candidate measurements.

The same sealed parent episodes are referenced by both GEPA arms. Logical
metric evaluations and actual model-backed episodes have separate ledgers.
Every proposed child is freshly executed, even when its text equals S0.
This module performs no registration or model calls on import.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from pathlib import Path

from gepa.core.adapter import EvaluationBatch

from .contracts import canonical_hash
from .gepa_adapter import EpisodeInput, EpisodeResult, TauFeedbackAdapter
from .g3_optimization import G3Adapter
from .subscription_evidence import load, save
from .subscription_optimization import ResearchPause, official_complete_score


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_measurement(row, example, strategy, seed):
    if (row["task_id"] != example.key or row["task_payload_sha256"] != canonical_hash(example.payload)
        or row["strategy_sha256"] != text_hash(strategy) or row["simulation_seed"] != seed
        or row["status"] != "complete" or row["actual_new_episode"] is not True):
        raise ResearchPause("Measurement identity differs")
    path = Path(row["directory"])
    if load(path / "measurement.json") != row:
        raise ResearchPause("Measurement differs from its saved metadata")
    for name, digest in row["artifacts_sha256"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
            raise ResearchPause("Measurement artifact changed")
    required = {str((path / "environment" / name).resolve()) for name in
                ("request.json", "outcome.json", "trajectory.json", "simulation.json", "calls.jsonl")}
    if not required.issubset(row["artifacts_sha256"]):
        raise ResearchPause("Required measurement source artifact binding missing")
    request = load(path / "environment/request.json")
    if (request.get("task_id") != example.key or request.get("task_sha256") != canonical_hash(example.payload)
        or request.get("simulation_seed") != seed or request.get("strategy") != strategy
        or request.get("run_id") != row["run_id"]):
        raise ResearchPause("Measurement environment request identity differs")
    outcome = load(path / "environment/outcome.json")
    if official_complete_score(outcome) != row["score"] or outcome["run_id"] != row["run_id"]:
        raise ResearchPause("Measurement score/run differs from official outcome")


class SharedParents:
    """In-memory exact records plus on-disk evidence validation on every use."""
    def __init__(self, runner, examples, strategy, records):
        if len(examples) != len(records) or len({e.key for e in examples}) != len(examples):
            raise ValueError("Unique aligned shared parents required")
        self.runner, self.strategy = runner, strategy
        self.examples = {e.key: deepcopy(e) for e in examples}
        self.records = {e.key: deepcopy(r) for e, r in zip(examples, records)}
        self.record_hashes = {key: canonical_hash(asdict(r)) for key, r in self.records.items()}
        self.evidence_hashes = {key: canonical_hash(asdict(runner.store.resolve(
            self.binding(r)))) for key, r in self.records.items()}

    @staticmethod
    def binding(record):
        from .pre_admission_adapter import EpisodeBinding
        return EpisodeBinding(**record.feedback["binding"])

    def get(self, example, strategy):
        if (strategy != self.strategy or example.key not in self.examples
            or canonical_hash(asdict(example)) != canonical_hash(asdict(self.examples[example.key]))):
            raise ResearchPause("Shared parent task/strategy differs")
        record = self.records[example.key]
        if canonical_hash(asdict(record)) != self.record_hashes[example.key]:
            raise ResearchPause("Shared parent record changed")
        evidence = self.runner.store.resolve(self.binding(record))
        if canonical_hash(asdict(evidence)) != self.evidence_hashes[example.key]:
            raise ResearchPause("Shared parent evidence changed")
        return deepcopy(record)

    def seal(self, path):
        if Path(path).exists():
            raise ValueError("Do not overwrite shared-parent seal")
        for key, example in self.examples.items():
            self.get(example, self.strategy)
        save(path, {"schema_version": "g4-shared-parent-v1", "strategy_sha256": text_hash(self.strategy),
            "simulation_seed": self.runner.simulation_seed,
            "record_sha256": self.record_hashes, "evidence_sha256": self.evidence_hashes,
            "records": {key: asdict(record) for key, record in self.records.items()},
            "scope": "One actual shared source, referenced by both arms; not two independent executions"})


class ParentReferenceRunner:
    def __init__(self, parents, arm):
        self.parents, self.arm = parents, arm
        self.store = parents.runner.store
        self.g3_payloads = {key: e.payload for key, e in parents.examples.items()}
        self.privacy_records = parents.runner.privacy_records
        self.references, self.halt_reason = [], None

    def reserve_batch(self, size):
        if self.halt_reason or type(size) is not int or size < 1:
            raise ResearchPause("Invalid shared-reference batch")

    def __call__(self, example, *, strategy, capture_traces):
        if capture_traces is not True or example.key in self.references:
            raise ResearchPause("Parent reference used twice or without trace capture")
        result = self.parents.get(example, strategy)
        self.references.append(example.key)
        return result


class MeasurementRunner:
    """Actual actor/user/scoring execution; no unused child diagnosis/gate calls."""
    def __init__(self, client, *, output, allowed_tasks, max_episodes, episode_limits,
                 max_strategy_chars, prior_privacy=()):
        self.client, self.output = client, Path(output).resolve()
        if not self.output.is_relative_to(Path(client.root).resolve()):
            raise ValueError("Measurement output outside project")
        self.output.mkdir(parents=True, exist_ok=False)
        self.allowed_tasks = {k: canonical_hash(v) for k, v in allowed_tasks.items()}
        self.max_episodes, self.episode_limits = max_episodes, dict(episode_limits)
        self.max_strategy_chars = max_strategy_chars
        self.privacy_records = list(prior_privacy)
        self.rows, self.attempted, self.halt_reason = [], 0, None

    def reserve(self, size):
        if (self.halt_reason or self.client.halt_reason or self.client.unknown_usage_calls
            or type(size) is not int or size < 1 or self.attempted + size > self.max_episodes
            or self.client._budget_reason(before=True)):
            raise ResearchPause("Measurement batch exceeds cap or has prior failure")

    def run(self, example, *, strategy, seed, phase, candidate_id):
        from tau2.data_model.tasks import Task
        from .subscription_episode import run_subscription_episode
        from .episode_privacy import extract_episode_privacy, scan_instance_text

        self.reserve(1)
        if (not isinstance(example, EpisodeInput) or example.key not in self.allowed_tasks
            or canonical_hash(example.payload) != self.allowed_tasks[example.key]):
            raise ValueError("Unregistered measurement task payload")
        if (not isinstance(strategy, str) or not strategy.strip() or len(strategy) > self.max_strategy_chars
            or type(seed) is not int or seed < 0 or not phase or not candidate_id):
            raise ValueError("Invalid measurement identity/strategy")
        if any(scan_instance_text(strategy, p, label="candidate")["matches"] for p in self.privacy_records):
            raise ResearchPause("Candidate contains known protected instance text")
        self.attempted += 1
        directory = self.output / f"measurement-{self.attempted:04d}"
        directory.mkdir(exist_ok=False)
        row = {"status": "reserved", "measurement_id": self.attempted, "task_id": example.key,
               "task_payload_sha256": canonical_hash(example.payload), "simulation_seed": seed,
               "phase": phase, "candidate_id": candidate_id, "strategy_sha256": text_hash(strategy),
               "directory": str(directory), "actual_new_episode": True}
        save(directory / "measurement.json", row)
        try:
            outcome = run_subscription_episode(self.client, Task.model_validate(example.payload),
                output=directory / "environment", simulation_seed=seed, strategy=strategy, **self.episode_limits)
            score = official_complete_score(outcome)
            if outcome != load(directory / "environment/outcome.json"):
                raise ResearchPause("Saved official outcome differs")
            messages = load(directory / "environment/trajectory.json")
            simulation = load(directory / "environment/simulation.json")
            if (simulation["id"] != outcome["run_id"] or simulation["task_id"] != example.key
                or simulation["messages"] != messages or simulation["reward_info"]["reward"] != outcome["native_reward"]):
                raise ResearchPause("Measurement trajectory/score identity differs")
            privacy = extract_episode_privacy(messages)
            self.privacy_records.append(privacy)
            save(directory / "privacy.json", privacy.to_dict())
            hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (directory / "environment").rglob("*") if p.is_file()}
            row.update(status="complete", score=score, run_id=outcome["run_id"], artifacts_sha256=hashes,
                       semantic_review="required", native_review="not_requested_for_candidate_measurement")
            return EpisodeResult(score, {"status": "official_evaluation_complete"},
                {"schema_version": "g4-measurement-reference-v1", "measurement_id": self.attempted,
                 "run_id": outcome["run_id"], "trajectory_sha256": canonical_hash(messages)},
                None, privacy.literals)
        except BaseException as exc:
            self.halt_reason = f"{type(exc).__name__}: {exc}"
            row.update(status="stopped", reason=self.halt_reason)
            raise
        finally:
            self.rows.append(deepcopy(row))
            save(directory / "measurement.json", row)
            save(self.output / "index.json", self.rows)


class PairedAdapter:
    """Fixed one-proposal lifecycle; no strategy-hash routing of child calls."""
    propose_new_texts = None
    def __init__(self, *, parents, baseline_validation, trainset, valset, measurement_runner,
                 train_seed, validation_seed, arm, candidate_id, output, policy):
        validate_pair_inputs(parents, trainset, valset, train_seed)
        if arm not in {"B1", "B2"} or set(baseline_validation) != {e.key for e in valset}:
            raise ValueError("Explicit arm and complete common validation required")
        self.parents, self.baseline_validation = parents, deepcopy(baseline_validation)
        self.trainset, self.valset = trainset, valset
        self.measurement_runner = measurement_runner
        self.train_seed, self.validation_seed = train_seed, validation_seed
        self.arm, self.candidate_id, self.output = arm, candidate_id, Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.reference_runner = ParentReferenceRunner(parents, arm)
        self.parent_adapter = G3Adapter(self.reference_runner, output=self.output / "parent", policy=policy)
        self.phase, self.reflected, self.failure = 0, False, None
        self.proposed_candidate, self.references = None, []

    def evaluate(self, batch, candidate, capture_traces=False):
        try:
            if self.failure or self.phase > 3:
                raise ResearchPause("Unexpected or failed G4 lifecycle")
            expected = self.valset if self.phase in (0, 3) else self.trainset
            keys = [e.key for e in batch]
            if len(keys) != len(expected) or set(keys) != {e.key for e in expected}:
                raise ResearchPause("Incomplete or repeated G4 batch")
            by_key = {e.key: e for e in expected}
            if any(canonical_hash(asdict(e)) != canonical_hash(asdict(by_key[e.key])) for e in batch):
                raise ResearchPause("Evaluation task payload differs")
            if set(candidate) != {"strategy"} or capture_traces != (self.phase in (1, 2)):
                raise ResearchPause("Unexpected candidate/capture contract")
            if self.phase in (0, 1) and candidate["strategy"] != self.parents.strategy:
                raise ResearchPause("Shared reference is only the registered S0")
            phase = self.phase
            if phase == 0:
                rows = [self.baseline_validation[e.key] for e in batch]
                for e, row in zip(batch, rows):
                    verify_measurement(row, e, candidate["strategy"], self.validation_seed)
                result = EvaluationBatch(outputs=[{"status": "shared_S0_validation"} for _ in rows],
                    scores=[r["score"] for r in rows], trajectories=None, num_metric_calls=len(rows))
            elif phase == 1:
                result = self.parent_adapter.evaluate(batch, candidate, capture_traces=True)
            else:
                if not self.reflected:
                    raise ResearchPause("Child evaluation before reflection")
                if phase == 2:
                    self.proposed_candidate = deepcopy(candidate)
                    save(self.output / "actual_proposed_candidate.json", candidate)
                elif candidate != self.proposed_candidate:
                    raise ResearchPause("Validation candidate differs from generated child")
                self.measurement_runner.reserve(len(batch))
                seed = self.train_seed if phase == 2 else self.validation_seed
                name = "candidate_train" if phase == 2 else "candidate_validation_1"
                def execute(example, *, strategy, capture_traces):
                    return self.measurement_runner.run(example, strategy=strategy, seed=seed,
                        phase=name, candidate_id=self.candidate_id)
                result = TauFeedbackAdapter(execute, arm="B1").evaluate(batch, candidate, capture_traces)
            self.references.append({"phase": phase, "task_ids": keys, "strategy_sha256": text_hash(candidate["strategy"]),
                "actual_new_episodes": len(keys) if phase >= 2 else 0, "logical_metric_evaluations": len(keys),
                "shared_parent_bindings": [asdict(self.parents.binding(self.parents.records[k])) for k in keys] if phase == 1 else [],
                "shared_validation_measurement_ids": [self.baseline_validation[k]["measurement_id"] for k in keys] if phase == 0 else []})
            save(self.output / "evaluation_references.json", self.references)
            self.phase += 1
            return result
        except BaseException as exc:
            self.failure = {"stage": "evaluate", "type": type(exc).__name__, "error": str(exc)}
            save(self.output / "failure.json", self.failure)
            raise

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        try:
            if self.failure or self.phase != 2 or self.reflected or candidate != {"strategy": self.parents.strategy}:
                raise ResearchPause("Only one reflection from exact shared S0 parents is permitted")
            value = self.parent_adapter.make_reflective_dataset(candidate, eval_batch, components_to_update)
            if set(value) != {"strategy"}:
                raise ResearchPause("A strategy reflection dataset is required")
            self.reflected = True
            save(self.output / "reflection_identity.json", {"records_sha256": canonical_hash(value),
                "parent_record_sha256": self.parents.record_hashes, "candidate_id": self.candidate_id,
                "arm": self.arm, "value": value})
            return value
        except BaseException as exc:
            self.failure = {"stage": "reflection", "type": type(exc).__name__, "error": str(exc)}
            save(self.output / "failure.json", self.failure)
            raise


def validate_pair_inputs(parents, trainset, valset, train_seed):
    examples = [*trainset, *valset]
    if (not trainset or not valset or any(not isinstance(e, EpisodeInput) for e in examples)
        or len({e.key for e in examples}) != len(examples)):
        raise ResearchPause("Unique disjoint nonempty training and validation sets required")
    if type(train_seed) is not int or train_seed != parents.runner.simulation_seed:
        raise ResearchPause("Child training seed differs from shared parent seed")
    if {e.key for e in trainset} != set(parents.examples):
        raise ResearchPause("Training tasks differ from shared parent tasks")


def run_paired_proposal(*, client, parents, baseline_validation, trainset, valset,
                        train_seed, validation_seeds, arm, candidate_id, output,
                        episode_limits, max_strategy_chars, optimizer_seed=0):
    """One real GEPA proposal, then fixed evaluation of the actual child."""
    import gepa
    from gepa.utils.stop_condition import MaxCandidateProposalsStopper
    from tau2.domains.retail.environment import get_environment
    from .gepa_adapter import COMMON_REFLECTION_PROMPT
    from .gepa_milestones_g3 import G3Milestones
    from .episode_privacy import scan_instance_text
    from .subscription_reflection import SubscriptionReflection

    validate_pair_inputs(parents, trainset, valset, train_seed)
    if len(validation_seeds) != 2 or len(set(validation_seeds)) != 2:
        raise ValueError("Exactly two different validation simulation seeds required")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    measurement = MeasurementRunner(client, output=output / "measurements",
        allowed_tasks={e.key: e.payload for e in [*trainset, *valset]},
        max_episodes=len(trainset) + 2 * len(valset), episode_limits=episode_limits,
        max_strategy_chars=max_strategy_chars, prior_privacy=parents.runner.privacy_records)
    adapter = PairedAdapter(parents=parents, baseline_validation=baseline_validation,
        trainset=trainset, valset=valset, measurement_runner=measurement,
        train_seed=train_seed, validation_seed=validation_seeds[0], arm=arm,
        candidate_id=candidate_id, output=output / "adapter", policy=get_environment().get_policy())
    transport = SubscriptionReflection(client, output=output / "reflection")
    max_metrics = 2 * (len(trainset) + len(valset))
    milestones = G3Milestones(output / "milestones", max_metric_calls=max_metrics, skip_perfect_score=False)
    reflections, reflection_failure = 0, None
    def reflect(prompt):
        nonlocal reflections, reflection_failure
        try:
            if reflections:
                raise ResearchPause("One reflection only; no retry")
            reflections += 1
            checks = [scan_instance_text(prompt, p, label="gepa_prompt") for p in parents.runner.privacy_records]
            save(output / "reflection_prompt_scan.json", checks)
            if any(c["matches"] for c in checks):
                raise ResearchPause("Known instance literal in reflection prompt")
            text = transport(prompt)
            checks = [scan_instance_text(text, p, label="reflection_return") for p in parents.runner.privacy_records]
            save(output / "reflection_return_scan.json", checks)
            if any(c["matches"] for c in checks):
                raise ResearchPause("Known instance literal in reflection output")
            return text
        except BaseException as exc:
            reflection_failure = str(exc)
            raise

    summary = {"status": "started", "arm": arm, "candidate_id": candidate_id,
        "proposal_opportunities": 1, "model_seed_controlled": False}
    save(output / "execution.json", summary)
    try:
        result = gepa.optimize(seed_candidate={"strategy": parents.strategy}, trainset=trainset, valset=valset,
            adapter=adapter, reflection_lm=reflect, reflection_prompt_template=COMMON_REFLECTION_PROMPT,
            reflection_minibatch_size=len(trainset), candidate_selection_strategy="pareto",
            acceptance_criterion="strict_improvement", skip_perfect_score=False, use_merge=False,
            cache_evaluation=False, max_metric_calls=max_metrics, stop_callbacks=[MaxCandidateProposalsStopper(1)],
            callbacks=[milestones], run_dir=str(output / "gepa"), seed=optimizer_seed,
            raise_on_exception=True, display_progress_bar=False, write_agent_state=False)
        if adapter.failure or measurement.halt_reason or client.halt_reason or reflection_failure:
            raise ResearchPause("Latched execution failure")
        completion = milestones.classify_complete(result)
        save(output / "gepa_best_candidate.json", result.best_candidate)
        save(output / "gepa_candidate_pool.json", result.candidates)
        candidate = adapter.proposed_candidate
        if candidate is None or completion["generated_candidates"] != 1:
            raise ResearchPause("Registered opportunity did not produce exactly one measurable child")
        # Every candidate is measured, irrespective of GEPA acceptance. A child
        # whose text equals S0 is still a fresh treatment output, never cached.
        measured_first = {r["task_id"]: r for r in measurement.rows
                          if r["phase"] == "candidate_validation_1"}
        if measured_first and set(measured_first) != {e.key for e in valset}:
            raise ResearchPause("Partial child validation cannot be filled selectively")
        if measured_first:
            for example in valset:
                verify_measurement(measured_first[example.key], example, candidate["strategy"], validation_seeds[0])
        else:
            measurement.reserve(len(valset))
            for example in valset:
                measurement.run(example, strategy=candidate["strategy"], seed=validation_seeds[0],
                    phase="candidate_validation_1", candidate_id=candidate_id)
        measurement.reserve(len(valset))
        for example in reversed(valset):
            measurement.run(example, strategy=candidate["strategy"], seed=validation_seeds[1],
                phase="candidate_validation_2", candidate_id=candidate_id)
        summary.update(status="complete", completion=completion,
            actual_proposed_candidate_sha256=text_hash(candidate["strategy"]),
            gepa_selected_new=(result.best_candidate != {"strategy": parents.strategy}),
            research_acceptance="pending_separate_semantic_review",
            input_records_sha256=load(output / "adapter/reflection_identity.json")["records_sha256"])
        return summary
    except BaseException as exc:
        summary.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        summary.update(actual_candidate_episodes=measurement.attempted, reflection_attempts=reflections,
            reflection_failure=reflection_failure, adapter_failure=adapter.failure,
            client_halt_reason=client.halt_reason, logical_evaluation_references=adapter.references)
        save(output / "execution.json", summary)
