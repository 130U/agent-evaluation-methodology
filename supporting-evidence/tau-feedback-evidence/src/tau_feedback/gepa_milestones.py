"""Metadata-only official GEPA callbacks for the fresh, single-iteration study.

Use GEPAMilestones(new_directory, max_metric_calls=N) in optimize(callbacks=[...]),
then classify_complete(result) BEFORE publishing a best/complete artifact. This
does not replace runner/client/reflection failure latches. GEPA can swallow both
internal errors and callback errors; incomplete or unexpected event sequences
raise MilestoneError here. No model, state, dataset, trajectory, input/output,
prompt or raw LM response is serialized. Candidate text is reduced to a hash.

Only the pinned study configuration is supported: a fresh strategy-only seed,
one SingleMutation iteration, no merge/cache/resume, full validation, perfect
score 1, strict improvement and one metric call per episode. A different study
needs an explicitly reviewed classifier, not relaxed missing-event checks.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .contracts import canonical_hash


class MilestoneError(RuntimeError):
    pass


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise MilestoneError("Invalid integer event metadata")
    return value


def _boolean(value):
    if type(value) is not bool:
        raise MilestoneError("Invalid boolean event metadata")
    return value


def _score(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise MilestoneError("Invalid benchmark score metadata")
    return float(value)


def _strategy_hash(value):
    if (not isinstance(value, dict) or set(value) != {"strategy"}
        or not isinstance(value["strategy"], str) or not value["strategy"].strip()):
        raise MilestoneError("Expected one nonempty strategy component")
    return canonical_hash(value)


def _components(value):
    if value != ["strategy"]:
        raise MilestoneError("Unexpected mutable components")
    return ["strategy"]


class GEPAMilestones:
    def __init__(self, output: Path, *, max_metric_calls: int):
        self.max_metric_calls = _integer(max_metric_calls, 1)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.events: list[dict] = []
        self.failure: dict | None = None

    def _fail(self, phase, error):
        # Error texts are our bounded contract descriptions, never exception
        # messages supplied by a model or raw GEPA payloads.
        self.failure = self.failure or {"phase": phase, "error_type": type(error).__name__,
                                        "reason": "milestone_recording_or_validation_failed"}
        try:
            (self.output / "failure.json").write_text(json.dumps(self.failure, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass  # In-memory failure remains mandatory at classify time.

    def _observe(self, name, event):
        try:
            if self.failure:
                return
            item = {"event": name, "sequence": len(self.events)}
            if not isinstance(event, dict):
                raise MilestoneError("Expected an official event dictionary")
            if name not in {"optimization_start", "optimization_end"}:
                item["iteration"] = _integer(event["iteration"])
            if name == "optimization_start":
                item.update(trainset_size=_integer(event["trainset_size"], 1),
                            valset_size=_integer(event["valset_size"], 1),
                            seed_sha256=_strategy_hash(event["seed_candidate"]))
                if _score(event["config"]["perfect_score"]) != 1.0:
                    raise MilestoneError("Only perfect_score=1 is registered")
            elif name == "optimization_end":
                item.update(best_candidate_idx=_integer(event["best_candidate_idx"]),
                            last_iteration_index=_integer(event["total_iterations"], -1),
                            total_metric_calls=_integer(event["total_metric_calls"]))
            elif name == "iteration_end":
                item["proposal_accepted"] = _boolean(event["proposal_accepted"])
            elif name == "candidate_selected":
                item.update(candidate_idx=_integer(event["candidate_idx"]), score=_score(event["score"]))
            elif name == "minibatch_sampled":
                item.update(batch_size=_integer(len(event["minibatch_ids"]), 1),
                            trainset_size=_integer(event["trainset_size"], 1))
            elif name in {"evaluation_start", "evaluation_end", "evaluation_skipped"}:
                index = event["candidate_idx"]
                item["candidate_idx"] = None if index is None else _integer(index)
                item["is_seed_candidate"] = _boolean(event["is_seed_candidate"])
                if name == "evaluation_start":
                    item.update(batch_size=_integer(event["batch_size"], 1),
                                capture_traces=_boolean(event["capture_traces"]))
                else:
                    scores = event["scores"]
                    if not isinstance(scores, list) or not scores:
                        raise MilestoneError("Missing score metadata")
                    checked = [_score(v) for v in scores]
                    item.update(batch_size=len(checked), score_sum=sum(checked), all_perfect=all(v == 1 for v in checked))
                    if name == "evaluation_end":
                        item["has_trajectories"] = _boolean(event["has_trajectories"])
                    else:
                        reason = event["reason"]
                        item["reason"] = reason if reason in ("all_scores_perfect", "no_trajectories") else "unknown_skip_reason"
            elif name in {"reflective_dataset_built", "proposal_start"}:
                item["components"] = _components(event["components"])
            elif name == "proposal_end":
                item["proposed_candidate_sha256"] = _strategy_hash(event["new_instructions"])
            elif name == "candidate_accepted":
                item["new_candidate_idx"] = _integer(event["new_candidate_idx"], 1)
            elif name == "candidate_rejected":
                # These are sums across the minibatch, not single-task scores.
                for key in ("old_score", "new_score"):
                    value = event[key]
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise MilestoneError("Invalid rejection score metadata")
                    item[key] = float(value)
                if not isinstance(event["reason"], str):
                    raise MilestoneError("Invalid rejection reason")
                item["reason_sha256"] = canonical_hash(event["reason"])
            elif name == "valset_evaluated":
                item.update(candidate_idx=_integer(event["candidate_idx"]),
                            count=_integer(event["num_examples_evaluated"], 1),
                            total=_integer(event["total_valset_size"], 1),
                            average_score=_score(event["average_score"]))
            elif name == "budget_updated":
                item.update(metric_calls_used=_integer(event["metric_calls_used"]),
                            metric_calls_delta=_integer(event["metric_calls_delta"]))
            elif name == "error":
                item.update(exception_type=type(event["exception"]).__name__,
                            will_continue=_boolean(event["will_continue"]))
            elif name.startswith("merge_"):
                item["unsupported_configuration"] = True
            elif name != "iteration_start":
                raise MilestoneError("Unknown recorder event")
            self.events.append(item)
            with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
        except Exception as exc:
            self._fail(name, exc)

    def classify_complete(self, result) -> dict:
        """Return a bounded endpoint or raise; never infer proposals from pool size."""
        try:
            if self.failure:
                raise MilestoneError("A callback failed; completion cannot be certified")
            return self._classify(result)
        except Exception as exc:
            self._fail("classify_complete", exc)
            if isinstance(exc, MilestoneError):
                raise
            raise MilestoneError("Inconsistent or missing runtime milestones") from exc

    def _classify(self, result):
        def require(condition, message):
            if not condition:
                raise MilestoneError(message)
        events = self.events
        relevant = [e for e in events if e["event"] != "budget_updated"]
        names = [e["event"] for e in relevant]
        base = ["optimization_start", "valset_evaluated"]
        round_start = ["iteration_start", "candidate_selected", "minibatch_sampled", "evaluation_start", "evaluation_end"]
        proposed = ["reflective_dataset_built", "proposal_start", "proposal_end", "evaluation_start", "evaluation_end"]
        tail = ["iteration_end", "optimization_end"]
        alternatives = {
            "baseline_only_metric_budget": base + ["optimization_end"],
            "skipped_all_perfect": base + round_start + ["evaluation_skipped"] + tail,
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
            if endpoint == "skipped_all_perfect":
                skip = by_name("evaluation_skipped")[0]
                require(skip["reason"] == "all_scores_perfect" and skip["all_perfect"] and finishes[0]["all_perfect"] and
                        skip["candidate_idx"] == 0 and skip["is_seed_candidate"] and skip["batch_size"] == sample["batch_size"],
                        "An unexplained or non-perfect skip is not a completed opportunity")
            else:
                require(not finishes[0]["all_perfect"], "Perfect parent unexpectedly reached reflection")
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
        summary = {"schema_version": "gepa-milestones-v1", "endpoint": endpoint,
                   "optimizer_iterations": len(by_name("iteration_start")),
                   "proposal_starts": len(by_name("proposal_start")), "generated_candidates": len(proposals),
                   "accepted_candidates": len(accepts), "rejected_candidates": len(rejects),
                   "skip_reasons": [e["reason"] for e in by_name("evaluation_skipped")],
                   "total_metric_calls": total, "best_candidate_idx": result.best_idx,
                   "evidence_scope": "Official callback metadata; not model-call counts or research benefit"}
        (self.output / "classification.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary


def _callback(name):
    def observe(self, event):
        self._observe(name, event)
    return observe


for _name in ("optimization_start", "optimization_end", "iteration_start", "iteration_end", "candidate_selected",
              "minibatch_sampled", "evaluation_start", "evaluation_end", "evaluation_skipped", "reflective_dataset_built",
              "proposal_start", "proposal_end", "candidate_accepted", "candidate_rejected", "valset_evaluated",
              "budget_updated", "error", "merge_attempted", "merge_accepted", "merge_rejected"):
    setattr(GEPAMilestones, "on_" + _name, _callback(_name))
