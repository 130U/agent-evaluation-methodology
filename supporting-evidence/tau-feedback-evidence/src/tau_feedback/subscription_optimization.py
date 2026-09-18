"""Actual tau episode/reviewer/gate runner for a separately frozen GEPA study.

This module performs no experiment on import. G1 is unchanged. Infrastructure
failures, incomplete scores and critical-user-review flags stop this runner;
they are never turned into ordinary zero-score feedback. Independent semantic
audit remains necessary even when a native reviewer reports no user errors.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from .contracts import canonical_hash
from .gepa_adapter import EpisodeInput, EpisodeResult
from .pre_admission_adapter import EpisodeBinding, PreAdmissionAdapter, native_feedback, trajectory_reference
from .subscription_episode import run_subscription_episode
from .subscription_evidence import SubscriptionEvidenceStore, choose_agent_pool, load, save
from .subscription_feedback import review_simulation, audit_native_diagnosis


class ResearchPause(RuntimeError):
    """Stop with the attempted unit and known costs retained, without retry."""


def official_complete_score(row):
    if (row.get("status") != "evaluated" or row.get("client_halt_reason")
        or row.get("usage_incomplete") is not False or row.get("unknown_usage_calls") != 0):
        raise ResearchPause("Episode execution or usage is incomplete")
    structural = row.get("structural_outcome", {})
    if structural.get("status") not in {"pass", "fail"}:
        raise ResearchPause("Official evaluation is unknown")
    reward = row.get("native_reward")
    if type(reward) not in (int, float) or not math.isfinite(reward) or reward not in (0, 1):
        raise ResearchPause("Official reward is missing or not binary")
    if structural.get("reason") == "complete_structural_evaluation":
        if (reward == 1) != (structural["status"] == "pass"):
            raise ResearchPause("Official reward and structural outcome disagree")
    elif structural.get("reason") == "premature_termination":
        if structural["status"] != "fail":
            raise ResearchPause("Premature termination cannot be a qualified pass")
    else:
        raise ResearchPause("Unregistered evaluation outcome")
    return float(structural["status"] == "pass")


class SubscriptionEpisodeRunner:
    def __init__(self, client, *, output: Path, arm: str, allowed_tasks: dict,
                 simulation_seed: int, max_episodes: int, diagnosis_cap: int,
                 sampling_salt: str, episode_limits: dict, max_strategy_chars: int):
        if arm not in {"B1", "B2"}:
            raise ValueError("This runner supports the frozen B1/B2 comparison only")
        for name, value in {"max_episodes": max_episodes, "diagnosis_cap": diagnosis_cap,
                            "max_strategy_chars": max_strategy_chars}.items():
            if type(value) is not int or value <= 0:
                raise ValueError(name + " must be a positive integer")
        if type(simulation_seed) is not int or simulation_seed < 0 or not sampling_salt:
            raise ValueError("Explicit simulation seed and sampling salt are required")
        if set(episode_limits) != {"max_steps", "max_errors", "simulation_seconds"}:
            raise ValueError("Specify all episode limits, without generation sampling claims")
        if not allowed_tasks or any(str(k) != str(v.get("id")) for k, v in allowed_tasks.items()):
            raise ValueError("Freeze explicit original task payloads")
        self.client, self.arm = client, arm
        self.output = Path(output).resolve()
        if not self.output.is_relative_to(Path(client.root).resolve()):
            raise ValueError("Runner output must stay inside the research root")
        self.output.mkdir(parents=True, exist_ok=False)
        self.allowed_tasks = {k: canonical_hash(v) for k, v in allowed_tasks.items()}
        self.simulation_seed, self.max_episodes = simulation_seed, max_episodes
        self.diagnosis_cap, self.sampling_salt = diagnosis_cap, sampling_salt
        self.episode_limits, self.max_strategy_chars = dict(episode_limits), max_strategy_chars
        self.attempted, self.halt_reason = 0, None
        self.store = SubscriptionEvidenceStore(client, output=self.output / "host_evidence")
        self.privacy_records = []

    def reserve_batch(self, size):
        if self.halt_reason or self.client.halt_reason or self.client.unknown_usage_calls:
            raise ResearchPause("Runner/client halted or usage is unknown")
        if type(size) is not int or size < 1 or self.attempted + size > self.max_episodes:
            raise ResearchPause("The whole next evaluation batch does not fit the episode cap")
        if self.client._budget_reason(before=True):
            raise ResearchPause("Shared CLI budget cannot start another evaluation batch")
        # Token demand is unknown before generation. This checks the registered
        # episode cap and current resource ledger, not a worst-case token reserve.

    def __call__(self, example: EpisodeInput, *, strategy: str, capture_traces: bool):
        from tau2.data_model.tasks import Task
        from tau2.data_model.simulation import SimulationRun, UserInfo
        from tau2.domains.retail.environment import get_environment
        from tau2.user.user_simulator import get_global_user_sim_guidelines
        from .episode_privacy import extract_episode_privacy, scan_instance_text

        self.reserve_batch(1)
        if (not isinstance(example, EpisodeInput) or example.key not in self.allowed_tasks
            or canonical_hash(example.payload) != self.allowed_tasks[example.key]):
            raise ValueError("Episode is outside the frozen task set or payload changed")
        if not isinstance(strategy, str) or not strategy.strip() or len(strategy) > self.max_strategy_chars:
            raise ResearchPause("Strategy violates the common nonempty/length contract")
        self.attempted += 1
        directory = self.output / f"episode-{self.attempted:04d}"
        directory.mkdir(exist_ok=False)
        record = {"status": "reserved", "attempt": self.attempted, "task_id": example.key,
                  "capture_traces": capture_traces, "arm": self.arm,
                  "strategy_sha256": hashlib.sha256(strategy.encode("utf-8")).hexdigest()}
        save(directory / "runner_outcome.json", record)
        try:
            # A prior episode's instance literal must not reappear in a candidate.
            scans = [scan_instance_text(strategy, p, label="candidate") for p in self.privacy_records]
            save(directory / "prior_instance_scan.json", scans)
            if any(scan["matches"] for scan in scans):
                raise ResearchPause("Candidate contains a protected instance literal")
            task = Task.model_validate(example.payload)
            row = run_subscription_episode(self.client, task, output=directory / "environment",
                    simulation_seed=self.simulation_seed, strategy=strategy, **self.episode_limits)
            score = official_complete_score(row)
            if row != load(directory / "environment/outcome.json"):
                raise ResearchPause("Returned outcome differs from the saved official outcome")
            simulation = SimulationRun.model_validate(load(directory / "environment/simulation.json"))
            messages = [m.model_dump(mode="json") for m in simulation.messages]
            if (simulation.id != row["run_id"] or simulation.task_id != example.key
                or simulation.reward_info is None or simulation.reward_info.reward != row["native_reward"]
                or messages != load(directory / "environment/trajectory.json")):
                raise ResearchPause("Simulation and scored outcome identities differ")
            privacy = extract_episode_privacy(messages)
            self.privacy_records.append(privacy)
            save(directory / "instance_privacy.json", privacy.to_dict())
            if not capture_traces:
                result = EpisodeResult(score, {"status": "official_evaluation_complete"}, None, None,
                                       privacy.literals)
            else:
                user_info = UserInfo(implementation="user_simulator", llm="codex-cli/structured-sol",
                    llm_args={}, global_simulation_guidelines=get_global_user_sim_guidelines())
                review = review_simulation(self.client, simulation=simulation, task=task,
                                            user_info=user_info, output=directory / "native_review")
                diagnoses = review.require_valid_diagnoses()
                if review.native_review["critical_user_error"]:
                    raise ResearchPause("Native review flags critical user error; independent adjudication required")
                pool, indices, full_count = choose_agent_pool(diagnoses,
                    trajectory_sha256=canonical_hash(messages), salt=self.sampling_salt, cap=self.diagnosis_cap)
                save(directory / "sampling.json", {"native_agent_count": full_count, "selected_native_indices": indices,
                    "cap": self.diagnosis_cap, "salt": self.sampling_salt, "not_sampled": full_count - len(pool)})
                binding = EpisodeBinding.create(example, strategy=strategy, episode_id=simulation.id,
                                                 messages=messages, diagnoses=pool)
                tools = [tool.openai_schema for tool in get_environment().get_tools()]
                gates = []
                if self.arm == "B2":
                    for index, diagnosis in enumerate(pool):
                        gate = directory / f"admission-{index:04d}"
                        audit_native_diagnosis(self.client, diagnosis=diagnosis, messages=messages,
                                               policy=simulation.policy, tool_schemas=tools, output=gate)
                        gates.append(gate)
                self.store.add(binding=binding, policy=simulation.policy, messages=messages, tool_schemas=tools,
                    review=review, native_indices=indices, gate_directories=gates,
                    protected_literals=privacy.literals, arm=self.arm,
                    source_artifacts=[directory / "environment" / name for name in
                                      ("request.json", "outcome.json", "simulation.json", "trajectory.json", "calls.jsonl")])
                result = EpisodeResult(score, {"status": "official_evaluation_complete"},
                    trajectory_reference(binding), native_feedback(binding, pool), privacy.literals)
            record.update(status="complete", score=score, run_id=simulation.id,
                          independent_semantic_review="required_before_research_claim")
            return result
        except BaseException as exc:
            self.halt_reason = f"{type(exc).__name__}: {exc}"
            record.update(status="stopped", reason=self.halt_reason)
            raise
        finally:
            save(directory / "runner_outcome.json", record)


class BudgetedPreAdmissionAdapter(PreAdmissionAdapter):
    """Check complete episode batches and save exactly the reflection records."""
    def __init__(self, runner, *, output):
        super().__init__(runner, resolver=runner.store.resolve, arm=runner.arm)
        self.episode_runner = runner
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.reflection_batches = 0
        self.failure = None

    def _failed(self, phase, exc):
        self.failure = self.failure or {"phase": phase, "error_type": type(exc).__name__, "error": str(exc)}
        save(self.output / "failure.json", self.failure)

    def evaluate(self, batch, candidate, capture_traces=False):
        try:
            if self.failure:
                raise ResearchPause("Adapter has a prior failure; no fallback evaluation")
            self.episode_runner.reserve_batch(len(batch))
            return super().evaluate(batch, candidate, capture_traces)
        except BaseException as exc:
            self._failed("evaluate", exc)
            raise

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        try:
            if self.failure:
                raise ResearchPause("Adapter has a prior failure; no fallback reflection data")
            result = super().make_reflective_dataset(candidate, eval_batch, components_to_update)
            self.reflection_batches += 1
            save(self.output / f"reflective-{self.reflection_batches:04d}.json", result)
            save(self.output / f"admission-events-{self.reflection_batches:04d}.json", self.admission_events)
            return result
        except BaseException as exc:
            self._failed("make_reflective_dataset", exc)
            raise


def run_gepa_arm(*, client, trainset, valset, seed_strategy, output, arm,
                 simulation_seed, max_episodes, diagnosis_cap, sampling_salt,
                 episode_limits, max_strategy_chars, optimizer_seed=0):
    """One real GEPA proposal opportunity; caller must freeze its study first.

    This is an execution function, not evidence that the study's G1 gate passed.
    It retains native skip-perfect and strict acceptance behavior. There is no
    retry, changed split, forced proposal or hidden synthetic fallback.
    """
    import gepa
    from gepa.utils.stop_condition import MaxCandidateProposalsStopper
    from .episode_privacy import scan_instance_text
    from .gepa_adapter import COMMON_REFLECTION_PROMPT
    from .gepa_milestones import GEPAMilestones
    from .subscription_reflection import SubscriptionReflection

    if not trainset or not valset or any(not isinstance(e, EpisodeInput) for e in [*trainset, *valset]):
        raise ValueError("Use explicit, nonempty EpisodeInput train/validation sets")
    keys = [e.key for e in [*trainset, *valset]]
    if len(set(keys)) != len(keys):
        raise ValueError("Unique disjoint task IDs are required; dependency-group audit is also required")
    if not isinstance(seed_strategy, str) or not seed_strategy.strip() or len(seed_strategy) > max_strategy_chars:
        raise ValueError("Initial strategy must satisfy the same common length contract")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    runner = SubscriptionEpisodeRunner(client, output=output / "episodes", arm=arm,
        allowed_tasks={e.key: e.payload for e in [*trainset, *valset]}, simulation_seed=simulation_seed,
        max_episodes=max_episodes, diagnosis_cap=diagnosis_cap, sampling_salt=sampling_salt,
        episode_limits=episode_limits, max_strategy_chars=max_strategy_chars)
    adapter = BudgetedPreAdmissionAdapter(runner, output=output / "adapter")
    transport = SubscriptionReflection(client, output=output / "reflection")
    milestones = GEPAMilestones(output / "milestones", max_metric_calls=max_episodes)
    scans_directory = output / "reflection_checks"
    scans_directory.mkdir()
    reflection_attempts = 0
    reflection_failure = None

    def reflection(prompt):
        nonlocal reflection_attempts, reflection_failure
        try:
            if reflection_attempts:
                raise ResearchPause("Only one reflection invocation is registered; no GEPA fallback retry")
            reflection_attempts += 1
            scans = [scan_instance_text(prompt, inventory, label="gepa_prompt") for inventory in runner.privacy_records]
            save(scans_directory / f"prompt-{reflection_attempts:04d}.json", scans)
            if any(scan["matches"] for scan in scans):
                raise ResearchPause("Actual GEPA prompt contains a known instance literal")
            text = transport(prompt)
            scans = [scan_instance_text(text, inventory, label="raw_reflection") for inventory in runner.privacy_records]
            save(scans_directory / f"returned-{reflection_attempts:04d}.json", scans)
            if any(scan["matches"] for scan in scans):
                raise ResearchPause("Reflection output contains a known instance literal")
            return text
        except BaseException as exc:
            reflection_failure = reflection_failure or {"error_type": type(exc).__name__, "error": str(exc)}
            save(scans_directory / "failure.json", reflection_failure)
            raise

    summary = {"arm": arm, "status": "started", "proposal_limit": 1,
               "train_ids": [e.key for e in trainset], "validation_ids": [e.key for e in valset],
               "scope": "Development optimization, not held-out improvement",
               "max_episodes": max_episodes, "same_budget_meaning": "Common caps, not equal realized cost",
               "semantic_review": "Independent review required before interpreting outcomes"}
    save(output / "execution.json", summary)
    try:
        result = gepa.optimize(seed_candidate={"strategy": seed_strategy}, trainset=trainset, valset=valset,
            adapter=adapter, reflection_lm=reflection, reflection_prompt_template=COMMON_REFLECTION_PROMPT,
            reflection_minibatch_size=min(3, len(trainset)), candidate_selection_strategy="pareto",
            acceptance_criterion="strict_improvement", skip_perfect_score=True,
            use_merge=False, cache_evaluation=False, max_metric_calls=max_episodes,
            stop_callbacks=[MaxCandidateProposalsStopper(1)], run_dir=str(output / "gepa"),
            callbacks=[milestones],
            # The pinned GEPA turns validation capture_traces on when this is
            # True, which would generate validation reviewer/gate feedback.
            # Raw episodes already persist in this runner's host directories.
            seed=optimizer_seed, raise_on_exception=True, display_progress_bar=False, write_agent_state=False)
        if runner.halt_reason or adapter.failure or reflection_failure:
            raise ResearchPause("GEPA returned after an adapter/reflection/runner error; keep this arm stopped")
        completion = milestones.classify_complete(result)
        save(output / "best_candidate.json", result.best_candidate)
        summary.update(status="complete", completion=completion,
                       best_candidate_sha256=canonical_hash(result.best_candidate))
        return result
    except BaseException as exc:
        summary.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        summary.update(episode_attempts=runner.attempted, reflection_attempts=reflection_attempts,
                       client_halt_reason=client.halt_reason, adapter_failure=adapter.failure,
                       reflection_failure=reflection_failure)
        save(output / "execution.json", summary)
