# Agent Evaluation Methodology

**ICFA: connecting evaluation feedback to evidence, action origin, and the component an agent optimizer can actually change.**

This research project develops **Intervention-Constrained Feedback Admission (ICFA)** and implements it around τ2-bench retail tasks and GEPA prompt optimization. The central contribution is a concrete feedback-admission mechanism: bind a diagnosis to its execution evidence, establish who produced the criticized behavior, and determine whether it belongs within the permitted intervention.

The result is a research and evaluation stack spanning structural checks, action-time evidence projection, source attribution, prompt-candidate execution, semantic review, and acceptance decisions. It makes the path from an evaluation result to a strategy update inspectable.

[ICFA research report](core/04-icfa-report/README.md) · [Experiment report](core/03-tau-feedback-evidence/RESEARCH_REPORT.md) · [Implementation and reproduction](supporting-evidence/tau-feedback-evidence/README.md)

## Research contribution

A failed task can generate a plausible diagnosis while pointing the optimizer toward the wrong component. ICFA formalizes the distinction between **whether a diagnosis is supported, whether its target is controllable, and whether the proposed update deserves adoption**.

The project's specific contribution is the combination of **evidence binding, execution-origin checks, and explicit intervention scope** in the τ2–GEPA feedback interface:

1. **Evaluation integrity.** Check assertion coverage, record identity, field types, and request binding before using the score as evidence.
2. **Action-time evidence.** Project the policy, messages, and tool information available at the criticized action, and review the diagnosis against that view.
3. **Origin and intervention.** Distinguish model-generated actions, framework-inserted behavior, other actors, and unknown origin; determine eligibility for the permitted prompt change.
4. **Candidate acceptance.** Execute proposed strategies and examine task outcomes, policy-grounded responses, regressions, and cost before selection.

The [system specification](supporting-evidence/tau-feedback-evidence/research/SYSTEM_SPECIFICATION.md) connects these decisions to implemented interfaces, inputs, outputs, and recorded candidate cases.

## Quantified research evidence

| Study | Recorded result | Evidence |
| --- | --- | --- |
| Historical trace audit | **1,824 archived runs** across **four models and 114 tasks**, checked against frozen source files and hashes | [Offline findings](supporting-evidence/tau-feedback-evidence/results/OFFLINE_FINDINGS.md) |
| Evaluation-integrity experiment | **120 valid controls retained; 440 predefined structural faults marked unknown**, using the upstream evaluation classes | [E1 results](supporting-evidence/tau-feedback-evidence/results/OFFLINE_FINDINGS.md) |
| Strict state replay | **1,536 eligible historical runs replayed**, with **zero disagreements in the database score component** | [E3 results](supporting-evidence/tau-feedback-evidence/results/OFFLINE_FINDINGS.md) |
| Action-origin controls | **12 tasks × three prompt strategies = 36 initialization checks**, all retaining the same framework-generated opening without model calls | [E4 source review](core/03-tau-feedback-evidence/E4_ACTION_ORIGIN_REVIEW.md) |

These results cover trace auditing, controlled evaluator inputs, database-state replay, and initialization provenance.

The origin study provides a particularly clear research finding: an opening message appeared in the assistant trajectory but was inserted by the framework before the first agent-model call. Prompt changes left it unchanged. This locates a concrete mismatch between the visible role label and the component a prompt optimizer controls.

## Implementation and evidence

The public package includes [structural contracts](supporting-evidence/tau-feedback-evidence/src/tau_feedback/contracts.py), [diagnosis admission](supporting-evidence/tau-feedback-evidence/src/tau_feedback/admission.py), [action-origin classification](supporting-evidence/tau-feedback-evidence/src/tau_feedback/action_origin.py), optimization adapters, acceptance checks, and component tests.

Research reports link claims to protocols, result summaries, and review records. [Published trajectories](supporting-evidence/tau-feedback-evidence/results/G3_PUBLIC_TRAJECTORIES.json) preserve policy-relevant dialogue, tool arguments and results, and semantic assessments. The [export manifest](supporting-evidence/tau-feedback-evidence/PUBLIC_EXPORT_MANIFEST.json) records the public evidence package.

## Reproduce the offline studies

Use Windows and Python 3.12:

```powershell
git clone https://github.com/130U/agent-evaluation-methodology.git
cd agent-evaluation-methodology\supporting-evidence\tau-feedback-evidence
```

Complete the [pinned dependency and archive setup](supporting-evidence/tau-feedback-evidence/README.md), then run:

```powershell
.venv\Scripts\uv.exe --cache-dir vendor\gepa\.uv-cache run --no-project --python .venv\Scripts\python.exe python -B -X utf8 scripts\run_offline.py
.venv\Scripts\python.exe -B -X utf8 scripts\reproduce_origin_initialization.py
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
```

These entry points execute the offline audits, initialization controls, and public component tests without model calls. See the [reproduction guide](supporting-evidence/tau-feedback-evidence/PUBLIC_REPRODUCTION_BOUNDARY.md) for artifact preparation and experiment setup.

## Research foundations and attribution

[τ2-bench](https://arxiv.org/abs/2506.07982) supplies the interaction environment and evaluators; [GEPA](https://arxiv.org/abs/2507.19457) supplies reflective prompt optimization. [Agents' Last Exam](https://arxiv.org/abs/2606.05405v2) informs the emphasis on verifiable evaluation.

Upstream τ2-bench and GEPA retain their MIT licenses. Pinned sources and license notices are preserved in [THIRD_PARTY_NOTICES](supporting-evidence/tau-feedback-evidence/THIRD_PARTY_NOTICES.md).

