"""Export a deterministic, allowlisted N1-R1 derivative; never alter evidence.

Reads sealed local evidence and writes only two new public-summary artifacts.
It does not publish, invoke a model, or claim hashes independently authenticate
unpublished raw calls. Existing output files cause refusal before any write.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STAGE = "results/n1/n1-r1-retail-subscription-http-v1"
PINNED = {
    f"{STAGE}/N1_FINDINGS.json": "739a7fb7d16f990fabad0b8fbb7fbfce5f90aaa08b1c435d0eb60f7787968530",
    "research/N1_R1_RESULT_REVIEW.json": "ddb437350eb8bc18b1e58539ff4882cfd79b57081863a816a45ffe87b17bcbc4",
    "research/N1_R1_RESULT_REVIEW.md": "fc32a2ed1d5a3012e242057c9993860bde58dd13ed381cff9070be178367f004",
    f"{STAGE}/independent_review/labels.json": "4b4dccc56f24f83ce41ad96aaabcd1f53b15954f3d2fee5401064e2f8a24208a",
}
DIMS = ("claim_support", "agent_responsibility", "correction_observability")
OUTPUTS = ("results/N1_R1_PUBLIC_SUMMARY.json", "results/N1_R1_FINDINGS.md")
EXCERPTS = {
    "card-0001": ("action.content", "Which order contains the Fleece Jacket?"),
    "card-0002": ("action.content", "Which order ID contains the Fleece Jacket you want to exchange?"),
    "card-0003": ("action.content", "Hi! How can I help you today?"),
    "card-0004": ("action.content", "Please provide the order ID, including the leading # symbol, and identify the items you no longer need."),
    "card-0005": ("action.tool_name", "get_user_details"),
    "card-0006": ("action.content", "Please tell me which order is the older one and choose its cancellation reason:"),
    "card-0007": ("action.content", "for the reason “no longer needed,”"),
    "card-0008": ("action.tool_name", "transfer_to_human_agents"),
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Evidence path escaped project")
    return path


def check_hashes(root, hashes):
    for name, expected in hashes.items():
        if sha(local(root, name)) != expected:
            raise ValueError("Sealed evidence differs: " + name)


def assert_public_text(text):
    # Defense in depth for this fixed allowlist, not a universal PII classifier.
    patterns = (
        r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", r"/Users/", r"/home/", r"\\\\[^\s]+\\",
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"\b(?:credit_card|gift_card|paypal)_\d+\b",
        r"\b[a-z]+_[a-z]+_\d+\b", r"\bsk-[A-Za-z0-9_-]{12,}\b",
    )
    if any(re.search(pattern, text) for pattern in patterns):
        raise ValueError("Public derivative contains a disallowed literal")


def build(root):
    root = Path(root).resolve()
    check_hashes(root, PINNED)
    findings = load(root / STAGE / "N1_FINDINGS.json")
    review = load(root / "research/N1_R1_RESULT_REVIEW.json")
    label_path = root / STAGE / "independent_review/labels.json"
    labels = load(label_path)
    check_hashes(root, findings["evidence_hashes"])
    check_hashes(root, review["evidence_sha256"])
    if sha(label_path) != findings["independent_labels_sha256"]:
        raise ValueError("Final labels are not the labels bound by the results")
    if review["status"] != "verified_descriptive_results_with_confirmed_provenance_misattribution":
        raise ValueError("Unexpected independent review disposition")
    first = {}
    for record in labels["reviewer_records"]:
        path = local(root / STAGE / "independent_review", record["path"])
        if sha(path) != record["sha256"]:
            raise ValueError("First annotation changed")
        data = load(path)
        first[data["reviewer"]] = {c["card_id"]: c for c in data["cards"]}
    if set(first) != {"red_team", "related_work"}:
        raise ValueError("Unexpected annotation identities")
    final = {c["card_id"]: c for c in labels["cards"]}
    cards = []
    for row in findings["independent_cards"]:
        card_id = row["card_id"]
        if row["labels"] != final[card_id]:
            raise ValueError("Published row would differ from locked labels")
        match = re.fullmatch(r"task-(\d+)-replicate-(\d+)", row["unit_id"])
        if not match:
            raise ValueError("Unexpected task/replicate identifier")
        packet = load(root / STAGE / "independent_review/packets" / f"{card_id}.json")
        if row["diagnosis"] != packet["diagnosis"]:
            raise ValueError("Diagnosis differs from independently reviewed packet")
        ref, excerpt = EXCERPTS[card_id]
        action = packet["action_view"]["action"]
        visible = action.get("content") or "" if ref == "action.content" else " ".join(
            c["name"] for c in action.get("tool_calls") or [])
        if excerpt not in visible:
            raise ValueError("Short public excerpt is not in the indexed action")
        verdict = row["gate_evidence"]["verdict"]
        diagnosis = row["diagnosis"]
        # Only these selected fields cross the public-output boundary. In
        # particular, never copy gate.references, raw calls, or complete views.
        cards.append({
            "card_id": card_id, "task_id": match.group(1), "replicate_id": int(match.group(2)),
            "turn_idx": diagnosis["turn_idx"],
            "diagnosis_metadata": {k: diagnosis[k] for k in ("source", "severity", "error_type", "error_tags")},
            "diagnosis_verbatim": {k: diagnosis[k] for k in ("reasoning", "correct_behavior")},
            "action_excerpt": {"reference": ref, "quote": excerpt},
            "first_research_agent_labels": {
                who: {k: first[who][card_id][k] for k in ("localization", *DIMS)} for who in sorted(first)},
            "final_research_agent_labels": {k: final[card_id][k] for k in ("localization", *DIMS)},
            "adjudication_note": labels["adjudication_notes"].get(card_id),
            "combined_label": row["combined_label"],
            "gate": {"decision": row["gate_decision"], "judge_called": row["judge_called"],
                     **{k: verdict[k] for k in ("claim_supported", "agent_responsible", "correction_observable", "reason")}},
        })
    cards.sort(key=lambda c: c["card_id"])
    if {c["card_id"] for c in cards} != set(EXCERPTS) or len(cards) != 8:
        raise ValueError("Expected all eight diagnostics exactly once")
    combined = Counter(c["combined_label"] for c in cards)
    decisions = Counter(c["gate"]["decision"] for c in cards)
    if combined != {"insufficient": 6, "unsupported": 2} or decisions != {"accept": 1, "reject": 5, "abstain": 2}:
        raise ValueError("Unexpected frozen counts")
    summary = findings["summary"]
    if (summary["trajectory_denominator"], summary["task_denominator"]) != (18, 12):
        raise ValueError("Unexpected cohort")
    usage = {phase: {k: values[k] for k in ("calls", "known_input_tokens", "known_output_tokens", "unknown_usage_calls")}
             for phase, values in findings["phase_usage"].items()}
    total = {k: sum(v[k] for v in usage.values()) for k in next(iter(usage.values()))}
    if total != {"calls": 26, "known_input_tokens": 414889, "known_output_tokens": 11209, "unknown_usage_calls": 0}:
        raise ValueError("Unexpected recorded cost")
    public = {
        "schema_version": "n1-r1-public-derivative-v1",
        "study": "N1-R1: descriptive feedback-diagnosis audit",
        "manifest_id": findings["manifest_id"],
        "evidence_as_of_utc": findings["analysis_created_at_utc"],
        "upstream_tau2_commit": "2174a603f6d014ef94473ffa95957f6ce27100db",
        "model_configuration": {"requested_model": "gpt-5.6-sol", "reasoning_effort": "low", "transport": "HTTP-configured subscription CLI", "model_sampling_seed_controlled": False},
        "denominators": {
            "parent_planned_trajectories": 24, "parent_evaluated": 18, "parent_interrupted_ungraded": 1,
            "parent_not_run": 5, "audited_trajectories": 18, "distinct_tasks": 12,
            "tasks_observed_twice": 6, "tasks_observed_once": 6,
            "native_review_calls": 18, "format_valid_native_reviews": 18,
            "native_diagnoses": 8, "agent_diagnoses": 8, "user_diagnoses": 0,
            "gate_units_completed": 18, "gate_model_calls": 8, "empty_agent_pool_units": 12,
            "diagnosis_bearing_trajectories": 6, "independently_labeled_diagnoses": 8,
            "unsampled_agent_diagnoses": 0, "fully_supported_diagnoses": 0,
        },
        "gate_counts": dict(decisions), "combined_label_counts": dict(combined),
        "combined_label_rule": findings["combined_label_rule"],
        "cross_tab": [{k: row[k] for k in ("combined_label", "gate_decision", "count")} for row in findings["sample_cross_tab"]],
        "phase_usage": usage, "audit_usage_total": total,
        "cost_scope": "CLI-reported known token subtotals including prompt overhead; not money or provider request counts. G1 generation, probes and researcher work are excluded.",
        "annotation": {
            "reviewer_type": "research agents, not human gold or calibrated truth",
            "prior_familiarity": "Both initial reviewers and the adjudicator had prior project context; not complete blinding.",
            "first_labels_unchanged": True, "labels_locked_before_gate": True,
            "labels_locked_at_utc": labels["locked_at_utc"],
            "earliest_gate_request_local_metadata_utc": review["label_sequence"]["earliest_gate_request_mtime_utc"],
            "temporal_evidence_limit": "Local file metadata and enforced execution order; no independent trusted timestamp.",
            "initial_dimension_agreement": {"agreed": 20, "total": 24, "not_accuracy": True},
        },
        "diagnoses": cards,
        "post_unblinding_action_origin_finding": {
            "card_id": "card-0003", "task_id": "81", "replicate_id": 29, "turn_idx": 0,
            "gate_decision_preserved": "accept", "locked_labels_preserved": True,
            "action_excerpt": "Hi! How can I help you today?",
            "gate_claim_excerpt": "The actor controlled that response",
            "initial_state": None,
            "source_fact": "The pinned tau2 fresh, non-solo initializer deep-copies DEFAULT_FIRST_AGENT_MESSAGE into the trajectory and schedules the user first.",
            "call_sequence_fact": "The first recorded CLI call is user_simulator_response; the first agent_response corresponds to turn 2 and already requests authentication.",
            "intervention_scope": "Changing StrategyAgent.system_prompt does not replace the orchestrator's fixed turn-0 message.",
            "conclusion": "Visible assistant-role text need not be generated or controllable by the optimized strategy. This accepted diagnosis misattributes the framework greeting in this configuration.",
            "timing": "Separate post-unblinding source analysis; not a revised blind label or a new gate result.",
            "source_url": "https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/orchestrator/orchestrator.py#L626",
            "frequency_or_originality_claim": False,
        },
        "interpretation": [
            "Both independently unsupported cards were rejected; this is not an estimate of overall accuracy.",
            "No diagnosis was supported in all three annotation dimensions, so preservation/recall of fully supported feedback is unestablished.",
            "Cards 1 and 2 contain executable order-lookup advice while fault and responsibility remain insufficient; one abstention and one rejection do not establish that the advice is useless.",
            "The only accepted card differs from the locked insufficient label and additionally has a post-unblinding, source-verified control-attribution defect.",
            "No Agent strategy update, downstream gain, generalization, causal net effect or recovery of the original G1 continuation gate was measured.",
            "The 18-trajectory cohort depends on original run order, availability and interruption; repeated tasks and diagnoses are not independent random observations.",
        ],
        "disclosure": {
            "artifact_type": "Allowlisted derivative of locally sealed evidence; not the complete experiment archive.",
            "diagnosis_text": "The reasoning and correct_behavior fields are verbatim. Case names/order identifiers already present in these benchmark diagnoses are retained; full profiles and account/payment identifiers are excluded.",
            "omitted": ["raw CLI calls/events/requests", "full conversations", "full policies", "full judge reference objects", "account credentials and account profiles", "absolute filesystem paths"],
            "raw_call_records_publicly_provided_here": False,
            "hashes_independently_authenticate_unpublished_calls": False,
            "reproducibility_limit": "Source hashes allow comparison only when the corresponding original evidence is available. This derivative and hashes alone cannot independently authenticate unpublished calls or fully reproduce their execution.",
            "publication_action": "Prepared locally; this exporter does not publish to GitHub.",
        },
        "source_commitments_sha256": {
            "N1_FINDINGS.json": PINNED[f"{STAGE}/N1_FINDINGS.json"],
            "independent_result_review.json": PINNED["research/N1_R1_RESULT_REVIEW.json"],
            "independent_result_review.md": PINNED["research/N1_R1_RESULT_REVIEW.md"],
            "locked_labels.json": PINNED[f"{STAGE}/independent_review/labels.json"],
            "registered_manifest": findings["manifest_sha256"],
            "first_labels_red_team.json": labels["reviewer_records"][0]["sha256"],
            "first_labels_related_work.json": labels["reviewer_records"][1]["sha256"],
        },
        "local_hash_verification": {"findings_evidence_files_checked": len(findings["evidence_hashes"]), "independent_review_sources_checked": len(review["evidence_sha256"]), "model_calls_by_exporter": 0},
    }
    assert_public_text(json.dumps(public, ensure_ascii=False))
    return public


def markdown(data):
    lines = [
        "# N1-R1：反馈诊断审计公开派生摘要", "",
        "**完整执行不等于机制有效。** 18 条已完成轨迹产生 8 条原生诊断；gate 给出 5 次拒收、2 次弃权、1 次接受。独立研究代理标签中，0 条在事实支持、Agent 责任和修正可观察性三个维度均获支持。本阶段没有修改 Agent 策略，也没有测得下游收益。", "",
        "## 分母与成本", "",
        "原 G1 计划仍为 24 条：18 条已评价、1 条中断未评价、5 条未运行。N1-R1 审计其中全部 18 条，涉及 12 题（6 题各两次、6 题各一次）。18 次 review 中，6 条轨迹产生全部 8 条诊断；其余 12 个空池没有 gate 模型调用。18 个 gate 单元完成只包含 8 次实际 gate 调用。", "",
        "| 阶段 | CLI 调用 | 输入 tokens | 输出 tokens | 未知用量调用 |", "|---|---:|---:|---:|---:|",
    ]
    for phase, values in data["phase_usage"].items():
        lines.append(f"| {phase} | {values['calls']} | {values['known_input_tokens']:,} | {values['known_output_tokens']:,} | {values['unknown_usage_calls']} |")
    lines += ["| 合计 | 26 | 414,889 | 11,209 | 0 |", "",
        "用量来自 CLI 记录，包含提示开销；不是货币费用或底层供应商请求次数。旧 G1 生成、探针及研究代理工作另计。", "",
        "## 标签、准入与解释边界", "",
        "研究代理标签不是人工金标准，也未经过概率校准。标注者及裁决者有既往项目熟悉度，并非完全盲法。两份首轮标签原件未改；差异在 gate 前另行裁决并保留说明。最终标签锁定于 11:49:02 UTC，最早 gate 请求本地记录为 11:49:42 UTC；这不是第三方可信时间戳。", "",
        "| 最终合并标签 | reject | abstain | accept |", "|---|---:|---:|---:|",
        "| unsupported | 2 | 0 | 0 |", "| insufficient | 3 | 2 | 1 |", "| supported | 0 | 0 | 0 |", "",
        "合并规则为三维均 supported 才记 supported，任一 unsupported 则记 unsupported，其余为 insufficient；这是展示规则，不是 gate 决策规则。两张 unsupported 被拒收不等于总体准确率；没有全维度支持的诊断，也无法估计有效反馈的保留率或召回率。", "",
        "卡 1/2 的逐单查找建议可执行，但原动作是否构成应归责的错误仍缺证据。gate 分别给弃权和拒收，两次均承认可执行性；不能把未准入解释为建议无用，更不能宣称已测得有用反馈损失。", "",
        "## 揭盲后确认的来源问题", "",
        "唯一 accept 是卡 3（task81 / replicate29 / turn0）的固定问候。原标签保持 insufficient。揭盲后，固定版本源码与本地原始调用链共同表明：该句由 orchestrator 的 DEFAULT_FIRST_AGENT_MESSAGE 直接插入，首个模型调用属于用户模拟器；首个 Agent 模型响应在 turn2，已经请求认证。gate 却声称该句由 actor 控制。", "",
        "因此，可见的 assistant 消息不必然由可优化策略生成。仅修改 StrategyAgent.system_prompt 无法替换框架的固定开场。这是额外的 post-unblinding 来源分析，未回改标签或 gate；不是原创首次、普遍频率或策略增益主张。", "",
        f"[固定官方初始化源码]({data['post_unblinding_action_origin_finding']['source_url']})", "",
        "## 八条原诊断及结果", "",
        "下列诊断与修正字段逐字保留，因此其中的指控是被审查的模型主张，不是本摘要认可的事实。完整三维首轮标签、裁决说明和 gate 原理由见 [派生 JSON](N1_R1_PUBLIC_SUMMARY.json)。", "",
    ]
    for c in data["diagnoses"]:
        labels = c["final_research_agent_labels"]
        lines += [f"### {c['card_id']} · task{c['task_id']} / replicate{c['replicate_id']} / turn{c['turn_idx']}", "",
            f"最终三维标签（事实 / 责任 / 修正）：**{labels['claim_support']} / {labels['agent_responsibility']} / {labels['correction_observability']}**；gate：**{c['gate']['decision']}**。", "",
            f"动作短引：`{c['action_excerpt']['quote']}`", "", "**原诊断 reasoning：**", "",
            c["diagnosis_verbatim"]["reasoning"], "", "**原修正 correct_behavior：**", "",
            c["diagnosis_verbatim"]["correct_behavior"], ""]
    lines += ["## 公开范围与复核限制", "",
        "这是本地封存材料的白名单派生摘要。它不包含完整 CLI 调用、事件或请求，完整对话、整段政策、账号/付款资料、凭据或绝对路径。原诊断中必要的公开基准案例姓名与订单标识保留；没有复制完整工具结果或 gate 引用对象。", "",
        "**原始调用未在这里公开，仅给出哈希不足以独立认证调用真实性或完整复现实验。** 哈希只能在取得相应原始材料后用于比对。该局限也适用于固定开场案例中的本地调用记录；公开源码可独立检查初始化分支，不能单凭它证明一条未公开调用的实际执行。", "",
        "本研究代理审计不建立因果净效应、泛化收益或旧 G1 继续门通过。18 条轨迹受既定顺序、可用性和中断影响；重复任务与同轨迹诊断不独立。导出器只准备本地文件，没有向 GitHub 发布。", "",
        "### 来源承诺（SHA-256）", "",
    ]
    for name, digest in data["source_commitments_sha256"].items():
        lines.append(f"- {name}: `{digest}`")
    text = "\n".join(lines) + "\n"
    assert_public_text(text)
    return text


def main():
    paths = [ROOT / name for name in OUTPUTS]
    if any(path.exists() for path in paths):
        raise FileExistsError("Refuse to overwrite either existing public derivative")
    data = build(ROOT)
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    rendered = markdown(data)
    for path, text in zip(paths, (encoded, rendered)):
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    if load(paths[0]) != data or paths[1].read_text(encoding="utf-8") != rendered:
        raise RuntimeError("Public derivative readback differs")
    print(json.dumps({"outputs": {name: sha(ROOT / name) for name in OUTPUTS}, "model_calls": 0,
                      "diagnoses": len(data["diagnoses"]), "published": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
