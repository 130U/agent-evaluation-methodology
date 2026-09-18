"""G3-only metadata classifier with explicitly disabled perfect-score skipping.

This is a separate study version: the frozen G2 classifier is unchanged.
Recording, failure latching, metadata projection and callbacks are inherited
without editing events. Classification retains the original fresh/single-round,
strict-improvement, full-validation, score, metric and returned-result checks.
The only permitted transition change is a perfect parent reaching reflection;
it must then be rejected because bounded scores cannot strictly improve.

The pinned optimization_start callback does not expose skip_perfect_score.
The constructor records the caller's explicit registered setting, not an
independent observation of that engine flag. The runner must pass False to
both this class and gepa.optimize; observed skips are always rejected.
Runner/client/reflection failure latches remain mandatory. No model is invoked.
"""
from __future__ import annotations

import json
from pathlib import Path

from .gepa_milestones import GEPAMilestones, MilestoneError, _strategy_hash


class G3Milestones(GEPAMilestones):
    def __init__(self, output: Path, *, max_metric_calls: int, skip_perfect_score: bool):
        if skip_perfect_score is not False:
            raise MilestoneError("G3 requires explicit skip_perfect_score=False")
        self.skip_perfect_score = False
        super().__init__(output, max_metric_calls=max_metric_calls)

    def _classify(self, result):
        def require(condition, message):
            if not condition:
                raise MilestoneError(message)
        require(self.skip_perfect_score is False, "G3 requires explicitly disabled perfect-score skipping")
        events = self.events
        relevant = [e for e in events if e["event"] != "budget_updated"]
        names = [e["event"] for e in relevant]
        base = ["optimization_start", "valset_evaluated"]
        round_start = ["iteration_start", "candidate_selected", "minibatch_sampled", "evaluation_start", "evaluation_end"]
        proposed = ["reflective_dataset_built", "proposal_start", "proposal_end", "evaluation_start", "evaluation_end"]
        tail = ["iteration_end", "optimization_end"]
        alternatives = {
            "baseline_only_metric_budget": base + ["optimization_end"],
            "candidate_rejected": base + round_start + proposed + ["candidate_rejected"] + tail,
            "candidate_accepted": base + round_start + proposed + ["valset_evaluated", "candidate_accepted"] + tail,
        }
        matches = [kind for kind, sequence in alternatives.items() if names == sequence]
        require(len(matches) == 1, "Incomplete, duplicate or unexpected GEPA event sequence")
        endpoint = matches[0]
        start, seed, end = relevant[0], relevant[1], relevant[-1]
        budget = [e for e in events if e["event"] == "budget_updated"]
        total = end["total_metric_calls"]
        require(total <= self.max_metric_calls, "Metric cap was exceeded")
        require(type(result.total_metric_calls) is int and result.total_metric_calls == total, "Result metric count differs from callback")
        require(seed["iteration"] == seed["candidate_idx"] == 0 and seed["count"] == seed["total"] == start["valset_size"],
                "Missing full seed validation")
        by_name = lambda name: [e for e in relevant if e["event"] == name]
        proposals = by_name("proposal_end")
        accepts = by_name("candidate_accepted")
        rejects = by_name("candidate_rejected")
        # Pool size is only a consistency check, never a generated-proposal count.
        require(len(result.candidates) == 1 + len(accepts), "Candidate pool conflicts with acceptance events")
        require(_strategy_hash(result.candidates[0]) == start["seed_sha256"], "Seed candidate changed")
        require(type(result.best_idx) is int and result.best_idx == end["best_candidate_idx"], "Best candidate index differs")
        expected_metrics = seed["count"]
        if endpoint == "baseline_only_metric_budget":
            require(not budget and total == seed["count"] == self.max_metric_calls and end["last_iteration_index"] == -1,
                    "A zero-iteration exit needs an exhausted seed-evaluation budget")
        else:
            require(end["last_iteration_index"] == 0 and all(e.get("iteration", 1) == 1 for e in relevant[2:-1]),
                    "Only one fresh iteration is supported")
            selected, sample = by_name("candidate_selected")[0], by_name("minibatch_sampled")[0]
            require(selected["candidate_idx"] == 0 and sample["trainset_size"] == start["trainset_size"], "Wrong parent or training metadata")
            begins, finishes = by_name("evaluation_start"), by_name("evaluation_end")
            require(len(begins) == len(finishes), "Unfinished training evaluation")
            for index, (begin, finish) in enumerate(zip(begins, finishes)):
                expected_idx = 0 if index == 0 else None
                require(begin["candidate_idx"] == finish["candidate_idx"] == expected_idx and
                        begin["is_seed_candidate"] == finish["is_seed_candidate"] == (index == 0) and
                        begin["capture_traces"] and finish["has_trajectories"] and
                        begin["batch_size"] == finish["batch_size"] == sample["batch_size"],
                        "Inconsistent captured training evaluation")
                expected_metrics += finish["batch_size"]
            iteration_end = by_name("iteration_end")[0]
            require(iteration_end["proposal_accepted"] == bool(accepts), "Iteration acceptance flag conflicts")
            if finishes[0]["all_perfect"]:
                require(not accepts, "A perfect parent cannot pass strict improvement")
            if accepts:
                validation = by_name("valset_evaluated")[1]
                require(accepts[0]["new_candidate_idx"] == validation["candidate_idx"] == 1 and
                        validation["count"] == validation["total"] == start["valset_size"], "Accepted child lacks full validation")
                require(finishes[1]["score_sum"] > finishes[0]["score_sum"], "Strict improvement was not met")
                require(_strategy_hash(result.candidates[1]) == proposals[0]["proposed_candidate_sha256"], "Accepted candidate differs from generated text")
                expected_metrics += validation["count"]
                endpoint += "_best_new" if result.best_idx == 1 else "_best_seed"
            else:
                require(rejects[0]["old_score"] == finishes[0]["score_sum"] and rejects[0]["new_score"] == finishes[1]["score_sum"] and
                        rejects[0]["new_score"] <= rejects[0]["old_score"], "Rejection conflicts with registered strict criterion")
            require(bool(budget) and budget[-1]["metric_calls_used"] == total, "Final metric budget update is missing")
            running = seed["count"]
            for update in budget:
                running += update["metric_calls_delta"]
                require(update["iteration"] == 1 and update["metric_calls_used"] == running, "Nonmonotonic or missing budget update")
        require(expected_metrics == total, "Episode and metric counts disagree")
        summary = {"schema_version": "gepa-milestones-g3-v1", "registered_skip_perfect_score": False,
                   "parent_all_perfect": by_name("evaluation_end")[0]["all_perfect"] if by_name("evaluation_end") else None, "endpoint": endpoint,
                   "optimizer_iterations": len(by_name("iteration_start")),
                   "proposal_starts": len(by_name("proposal_start")), "generated_candidates": len(proposals),
                   "accepted_candidates": len(accepts), "rejected_candidates": len(rejects),
                   "skip_reasons": [e["reason"] for e in by_name("evaluation_skipped")],
                   "total_metric_calls": total, "best_candidate_idx": result.best_idx,
                   "evidence_scope": "Official callback metadata; not model-call counts or research benefit"}
        (self.output / "classification.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary
