"""G3 only: an explicit proposal opportunity and source-aware admission.

G1/G2 sources and evidence remain unchanged. GEPA's strict training acceptance
is preserved; a perfect parent may generate a proposal but cannot be improved
on that binary minibatch. Research acceptance is a separate, stricter stage.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

from .action_origin import (ORCHESTRATOR_SHA256, OriginContext,
                            classify_action_origin, strategy_feedback_eligibility)
from .contracts import canonical_hash, strict_json
from .pre_admission_adapter import PreAdmissionAdapter
from .subscription_evidence import load, save
from .subscription_optimization import (SubscriptionEpisodeRunner,
    BudgetedPreAdmissionAdapter, ResearchPause)


def source_routes(binding, pool, evidence, runner):
    """Use bound episode artifacts, not a role label or matching words alone."""
    entry = runner.store.entries[binding.episode_id]
    hashes = entry[3]
    calls_paths = [Path(p) for p in hashes if Path(p).name == "calls.jsonl"]
    if len(calls_paths) != 1:
        raise ResearchPause("Expected one bound environment call ledger")
    path = calls_paths[0]
    if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[str(path)]:
        raise ResearchPause("Bound origin ledger changed")
    calls = [strict_json(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]
    payload = runner.g3_payloads[binding.task_key]
    if canonical_hash(payload) != binding.task_payload_sha256:
        raise ResearchPause("Origin context task differs from the episode binding")
    initial = payload.get("initial_state") or {}
    history = initial.get("message_history") or []
    if not isinstance(history, list):
        raise ResearchPause("Unrecognized initial history")
    context = OriginContext(ORCHESTRATOR_SHA256, False, len(history))
    rows = []
    for index, diagnosis in enumerate(pool):
        turn = diagnosis.get("turn_idx")
        matches = [m for m in evidence.messages if type(turn) is int and m.get("turn_idx") == turn]
        origin = classify_action_origin(matches[0] if len(matches) == 1 else None, calls, context)
        rows.append({"diagnosis_index": index, "turn_idx": turn, "origin": origin,
                     "route": strategy_feedback_eligibility(origin)})
    return rows


class G3Adapter(BudgetedPreAdmissionAdapter):
    """Same policy/context and privacy carrier in both arms; B2 adds routing."""
    def __init__(self, runner, *, output, policy):
        super().__init__(runner, output=output)
        self.policy = policy
        self.origin_events = []

    def _select(self, binding, pool, evidence):
        chosen, indices, calls = PreAdmissionAdapter._select(binding, pool, evidence)
        routes = source_routes(binding, pool, evidence, self.episode_runner)
        allowed = {r["diagnosis_index"] for r in routes if r["route"] == "continue_semantic_review"}
        kept = [i for i in indices if i in allowed]
        self.origin_events.append({"episode_id": binding.episode_id, "routes": routes,
                                   "semantic_selected": indices, "final_selected": kept})
        return [deepcopy(pool[i]) for i in kept], kept, calls

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        try:
            # Call the parent selection/projection once. The old recorder is not
            # used here: save exactly the new records supplied to the optimizer.
            if self.failure:
                raise ResearchPause("Earlier adapter failure")
            result = PreAdmissionAdapter.make_reflective_dataset(
                self, candidate, eval_batch, components_to_update)
            for row in result.get("strategy", []):
                row["Inputs"] = {"task": "Reusable retail strategy", "fixed_public_policy": self.policy}
            self.reflection_batches += 1
            save(self.output / f"reflective-{self.reflection_batches:04d}.json", result)
            save(self.output / f"admission-events-{self.reflection_batches:04d}.json", self.admission_events)
            save(self.output / f"origin-events-{self.reflection_batches:04d}.json", self.origin_events)
            return result
        except BaseException as exc:
            self._failed("make_reflective_dataset", exc)
            raise


def run_g3_arm(*, client, trainset, valset, seed_strategy, output, arm,
               simulation_seed, max_episodes, diagnosis_cap, sampling_salt,
               episode_limits, max_strategy_chars, optimizer_seed=0):
    import gepa
    from gepa.utils.stop_condition import MaxCandidateProposalsStopper
    from tau2.domains.retail.environment import get_environment
    from .gepa_adapter import EpisodeInput, COMMON_REFLECTION_PROMPT
    from .gepa_milestones_g3 import G3Milestones
    from .episode_privacy import scan_instance_text
    from .subscription_reflection import SubscriptionReflection

    examples = [*trainset, *valset]
    if (not trainset or not valset or any(not isinstance(e, EpisodeInput) for e in examples)
        or len({e.key for e in examples}) != len(examples)):
        raise ValueError("Explicit disjoint nonempty train and validation sets required")
    if max_episodes != 2 * (len(trainset) + len(valset)):
        raise ValueError("Reserve the full one-proposal worst-case episode count")
    source = Path(client.root) / "vendor/tau2/src/tau2/orchestrator/orchestrator.py"
    if hashlib.sha256(source.read_bytes()).hexdigest() != ORCHESTRATOR_SHA256:
        raise ResearchPause("Unknown orchestrator source for provenance check")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    runner = SubscriptionEpisodeRunner(client, output=output / "episodes", arm=arm,
        allowed_tasks={e.key: e.payload for e in examples}, simulation_seed=simulation_seed,
        max_episodes=max_episodes, diagnosis_cap=diagnosis_cap, sampling_salt=sampling_salt,
        episode_limits=episode_limits, max_strategy_chars=max_strategy_chars)
    runner.g3_payloads = {e.key: deepcopy(e.payload) for e in examples}
    adapter = G3Adapter(runner, output=output / "adapter", policy=get_environment().get_policy())
    transport = SubscriptionReflection(client, output=output / "reflection")
    milestones = G3Milestones(output / "milestones", max_metric_calls=max_episodes,
                             skip_perfect_score=False)
    scans_directory = output / "reflection_checks"
    scans_directory.mkdir()
    reflection_attempts, reflection_failure = 0, None

    def reflection(prompt):
        nonlocal reflection_attempts, reflection_failure
        try:
            if reflection_attempts:
                raise ResearchPause("Only one registered reflection; no fallback retry")
            reflection_attempts += 1
            scans = [scan_instance_text(prompt, p, label="gepa_prompt") for p in runner.privacy_records]
            save(scans_directory / "prompt.json", scans)
            if any(s["matches"] for s in scans):
                raise ResearchPause("Known instance literal in reflection prompt")
            text = transport(prompt)
            scans = [scan_instance_text(text, p, label="raw_reflection") for p in runner.privacy_records]
            save(scans_directory / "returned.json", scans)
            if any(s["matches"] for s in scans):
                raise ResearchPause("Known instance literal in reflection output")
            return text
        except BaseException as exc:
            reflection_failure = {"error_type": type(exc).__name__, "error": str(exc)}
            save(scans_directory / "failure.json", reflection_failure)
            raise

    summary = {"arm": arm, "status": "started", "proposal_limit": 1,
        "train_ids": [e.key for e in trainset], "validation_ids": [e.key for e in valset],
        "skip_perfect_score": False, "max_episodes": max_episodes,
        "scope": "New G3 pilot; GEPA selection is not final research acceptance"}
    save(output / "execution.json", summary)
    try:
        result = gepa.optimize(seed_candidate={"strategy": seed_strategy}, trainset=trainset, valset=valset,
            adapter=adapter, reflection_lm=reflection, reflection_prompt_template=COMMON_REFLECTION_PROMPT,
            reflection_minibatch_size=len(trainset), candidate_selection_strategy="pareto",
            acceptance_criterion="strict_improvement", skip_perfect_score=False,
            use_merge=False, cache_evaluation=False, max_metric_calls=max_episodes,
            stop_callbacks=[MaxCandidateProposalsStopper(1)], run_dir=str(output / "gepa"),
            callbacks=[milestones], seed=optimizer_seed, raise_on_exception=True,
            display_progress_bar=False, write_agent_state=False)
        if runner.halt_reason or adapter.failure or reflection_failure:
            raise ResearchPause("GEPA returned after a latched failure")
        completion = milestones.classify_complete(result)
        save(output / "best_candidate.json", result.best_candidate)
        save(output / "candidate_pool.json", result.candidates)
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
