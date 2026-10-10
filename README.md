# Agent Evaluation Methodology

**Study which evaluation feedback can justify a specific change to an agent.**

A May–September 2026 study of τ2-bench retail tasks and GEPA prompt optimization. The project develops **ICFA — Intervention-Constrained Feedback Admission**: checks that connect a diagnosis to its evidence, the component responsible, and the component an optimization run is allowed to change.

本研究围绕“什么反馈能够指导什么修改”，将评价完整性、行动时信息、行为来源与更新验收分别核查。

[Research report · 完整研究报告](core/04-icfa-report/README.md) · [Experiments](core/03-tau-feedback-evidence/RESEARCH_REPORT.md) · [Code and reproduction](supporting-evidence/tau-feedback-evidence/README.md)

## The research question

A failed task does not by itself establish who caused the failure or which change would fix it. A diagnosis may use information the agent never saw, attribute a framework-generated message to the model, or overlook a regression that the official task score does not measure.

ICFA examines the step between evaluation and prompt revision. It builds on τ2-bench's interaction environment and GEPA's reflective optimizer; the intervention studied here is a prompt change, not model-weight training.

## How feedback is admitted

1. **Check evaluation integrity.** Verify required assertions, record identity, types, and run binding; preserve an unknown result when evidence is incomplete.
2. **Check action-time evidence.** Test the diagnosis against the policy, messages, and tool information available when the action occurred.
3. **Check origin and control.** Distinguish model-generated actions from framework-inserted behavior, and route feedback according to the permitted intervention.
4. **Evaluate the proposed update.** Run candidate strategies and examine both task outcomes and semantic regressions before adopting a change.

Passing an admission check gives a suggestion eligibility for further review. It does not establish that the diagnosis is correct or that the resulting update will improve performance.

## Evidence and outcomes

| Study | Recorded result | What it supports |
|---|---|---|
| [E1 · Evaluation integrity](supporting-evidence/tau-feedback-evidence/results/OFFLINE_FINDINGS.md) | 120 valid controls retained; 440 predefined malformed or mismatched inputs marked unknown | Detection of specified structural faults, rather than a natural error rate |
| [E4 · Action origin](core/03-tau-feedback-evidence/E4_ACTION_ORIGIN_REVIEW.md) | 12 tasks × 3 prompt strategies produced the same framework-inserted opening in 36 initialization checks, without model calls | A concrete behavior outside the prompt intervention's control |
| [C1 · Synthetic admission calibration](core/03-tau-feedback-evidence/C1_SYNTHETIC_RESULT.md) | All 5 supported cards retained and all 3 unsupported cards rejected; 1 of 4 insufficient-evidence cards still passed the combined gate | Local component behavior on 12 fixed synthetic cards |
| [G3 · Candidate pilot](core/03-tau-feedback-evidence/G3_PILOT_RESULT.md) | 14 optimization episodes and 2 candidates; all final groups retained the initial strategy. Final testing stopped after 18 scored runs and 1 unscored interruption | Candidate-generation and acceptance records, including a semantic regression |
| [G4 · Shared-feedback study](core/03-tau-feedback-evidence/G4_STOP_RESULT.md) | Stopped after 4 of 120 planned optimization episodes because of a critical simulated-user error; no candidate proposal or final test followed | A documented validity failure and preserved stopping decision |

**The study has not established an improvement in agent success rate, generalization, or optimization cost.** The latest endpoint is G4's stopped study together with C1's completed component calibration. Full denominators, failures, and unexecuted stages are retained in the [project status](core/03-tau-feedback-evidence/PROJECT_STATUS.md).

## Read or reproduce

Start with the [ICFA report](core/04-icfa-report/README.md) for the method and its theoretical limits. The [experiment report](core/03-tau-feedback-evidence/RESEARCH_REPORT.md) connects each conclusion to its protocol, result, and review record.

For the no-model E0–E3 reproduction, clone the repository and enter the public package:

```powershell
git clone https://github.com/130U/agent-evaluation-methodology.git
cd agent-evaluation-methodology\supporting-evidence\tau-feedback-evidence
```

Follow the [Windows / Python 3.12 setup](supporting-evidence/tau-feedback-evidence/README.md), which downloads the pinned upstream sources, archives, and locked dependencies. After that setup:

```powershell
.venv\Scripts\uv.exe --cache-dir vendor\gepa\.uv-cache run --no-project --python .venv\Scripts\python.exe python -B -X utf8 scripts\run_offline.py
.venv\Scripts\python.exe -B -X utf8 scripts\reproduce_origin_initialization.py
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

These entry points cover offline audits, initialization controls, and public component tests. They do not recreate the author's historical model interactions.

## Public evidence boundary

The public package is a documented derivative export. It includes implementation, protocols, result summaries, selected trajectories, and review materials. Original CLI event logs, authentication, model catalog snapshots, and some frozen execution artifacts are excluded.

Published trajectories support semantic inspection; a hash cannot reconstruct or independently authenticate an unavailable original log. Fresh model experiments require a new registration and the reproducer's own access. See the [reproduction boundary](supporting-evidence/tau-feedback-evidence/PUBLIC_REPRODUCTION_BOUNDARY.md) and [publication policy](docs/PUBLICATION_AND_PRIVACY_BOUNDARY.md).

## Research foundations

- [τ2-bench](https://arxiv.org/abs/2506.07982): task environment, retail policy, tools, and interaction traces.
- [GEPA](https://arxiv.org/abs/2507.19457): reflective prompt optimization and candidate testing.
- [Agents' Last Exam](https://arxiv.org/abs/2606.05405v2): motivation to examine what an evaluation result actually verifies.

The project's specific contribution is the binding of feedback evidence and action origin to an explicit intervention scope. Its [related-work review](supporting-evidence/tau-feedback-evidence/research/FULLTEXT_NOVELTY_CHECK.md) discusses the limits of that claim.
