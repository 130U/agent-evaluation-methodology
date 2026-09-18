"""Minimal configured GEPA entry point; refuses to run without an explicit factory.

This is an integration example, not an executed optimization experiment. The
factory must return run_configured() keyword arguments, including real runner,
reflection_lm, explicit development/validation sets, budget and fresh run_dir.
It must separately supply the all-role token/call/time ledger required by the
research protocol: max_metric_calls alone is not a total model-call budget.
"""
from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gepa

from tau_feedback.gepa_adapter import COMMON_REFLECTION_PROMPT, EpisodeInput, TauFeedbackAdapter


def run_configured(*, runner, reflection_lm, trainset: list[EpisodeInput],
                   valset: list[EpisodeInput], seed_strategy: str,
                   max_metric_calls: int, run_dir: str, seed: int = 0,
                   arm: str = "B1", feedback_filter=None, filter_name=None,
                   stop_callbacks=None) -> Any:
    if not callable(runner) or not callable(reflection_lm):
        raise ValueError("Configure explicit episode and reflection callables; no placeholder fallback")
    if not trainset or not valset:
        raise ValueError("Explicit nonempty trainset and valset are required")
    if any(not isinstance(x, EpisodeInput) for x in [*trainset, *valset]):
        raise TypeError("Data sets must contain EpisodeInput instances")
    train_ids, val_ids = {x.key for x in trainset}, {x.key for x in valset}
    if len(train_ids) != len(trainset) or len(val_ids) != len(valset) or train_ids & val_ids:
        raise ValueError("Use unique, disjoint development/validation IDs; family separation remains caller responsibility")
    if type(max_metric_calls) is not int or max_metric_calls <= 0:
        raise ValueError("Explicit positive metric budget required")
    destination = Path(run_dir)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Example requires a fresh run_dir; it must not silently resume another arm")
    adapter = TauFeedbackAdapter(runner, arm=arm, feedback_filter=feedback_filter,
                                 filter_name=filter_name)
    return gepa.optimize(
        seed_candidate={"strategy": seed_strategy}, trainset=trainset, valset=valset,
        adapter=adapter, reflection_lm=reflection_lm,
        reflection_prompt_template=COMMON_REFLECTION_PROMPT,
        reflection_minibatch_size=min(3, len(trainset)),
        candidate_selection_strategy="pareto", acceptance_criterion="strict_improvement",
        use_merge=False, cache_evaluation=False, max_metric_calls=max_metric_calls,
        stop_callbacks=stop_callbacks, run_dir=str(destination), seed=seed,
        raise_on_exception=True, display_progress_bar=False,
        write_agent_state=True,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factory", help="Explicit trusted module:function returning configured keyword arguments")
    args = parser.parse_args(argv)
    if not args.factory:
        parser.error("Runner/reflection model are not configured. Refusing to run an optimization or fabricated substitute.")
    module_name, separator, function_name = args.factory.partition(":")
    if not separator or not module_name or not function_name:
        parser.error("--factory must be module:function")
    factory = getattr(importlib.import_module(module_name), function_name)
    config = factory()
    if not isinstance(config, dict):
        raise TypeError("Factory must return run_configured keyword arguments")
    return run_configured(**config)


if __name__ == "__main__":
    main()
