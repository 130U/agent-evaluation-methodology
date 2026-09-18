"""Offline, post-run descriptive report. Does not select cards or call a model.

Validates the registered cohort, every review/gate and sealed pre-gate labels.
The report is a derived analysis, not a new experiment registration or a
semantic gold standard. Original outputs are never overwritten.
"""
import argparse
from collections import Counter
import datetime as dt
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from tau_feedback.n1_audit import (NativeRuntime, _inputs, _locked_selection,
    _locked_independent_labels, _verify_gate, _write_new, sha, summarize,
    verify_manifest)
from tau_feedback.subscription_evidence import load


def build(root, manifest_path):
    root = Path(root).resolve()
    m, digest, folder = verify_manifest(root, manifest_path)
    if (folder / "active.lock").exists() or (folder / "STOP.json").exists():
        raise ValueError("A stopped or active stage cannot be reported as complete")
    runtime = NativeRuntime(root, m)
    selection, selection_hash = _locked_selection(root, m, digest, folder, runtime)
    labels_hash = _locked_independent_labels(folder, selection, selection_hash)
    gates = {}
    for unit, pool in zip(m["units"], selection["pools"]):
        directory = folder / "gate" / unit["unit_id"]
        inputs = _inputs(root, unit, runtime)
        row = _verify_gate(root, directory, inputs, pool, selection_hash, m,
                           digest=digest, labels_hash=labels_hash)
        for decision in row["decisions"]:
            gates[(unit["unit_id"], decision["native_index"])] = decision
    summary = summarize(folder, m)
    if (summary["review_execution_counts"] != {"complete": len(m["units"])}
        or summary["gate_execution_counts"] != {"complete": len(m["units"])}):
        raise ValueError("Every registered phase unit must have completed")
    independent = folder / "independent_review"
    labels = load(independent / "labels.json")
    mapping = load(independent / "HOST_MAPPING_DO_NOT_GIVE_REVIEWERS.json")
    mapped = {c["card_id"]: c for c in mapping["cards"]}
    cards, joint = [], Counter()
    dims = ("claim_support", "agent_responsibility", "correction_observability")
    for label in labels["cards"]:
        card = mapped[label["card_id"]]
        decision = gates[(card["unit_id"], card["native_index"])]
        values = [label[k] for k in dims]
        aggregate = ("supported" if all(v == "supported" for v in values) else
                     "unsupported" if "unsupported" in values else "insufficient")
        joint[(aggregate, decision["decision"])] += 1
        cards.append({"card_id": card["card_id"], "unit_id": card["unit_id"],
            "native_index": card["native_index"], "diagnosis": card["diagnosis"],
            "labels": label, "combined_label": aggregate,
            "gate_decision": decision["decision"], "judge_called": decision["judge_called"],
            "gate_evidence": decision})
    phase_usage = {}
    for phase in ("review", "gate"):
        ledgers = [load(folder / phase / u["unit_id"] / "ledger.json") for u in m["units"]]
        phase_usage[phase] = {key: sum(l[key] for l in ledgers) for key in
            ("calls", "known_input_tokens", "known_output_tokens", "unknown_usage_calls")}
    results = {"schema_version": 1, "analysis_created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "manifest_id": m["manifest_id"], "manifest_sha256": digest,
        "analysis_script_sha256": sha(Path(__file__)), "selection_sha256": selection_hash,
        "independent_labels_sha256": labels_hash, "status": "complete_descriptive_audit",
        "summary": summary, "phase_usage": phase_usage, "independent_cards": cards,
        "all_observed_agent_diagnoses_labeled": (
            len(cards) == summary["diagnosis_denominators"]["source_counts_among_valid_reviews"]["agent"]
            and summary["diagnosis_denominators"]["not_sampled_agent_cards_among_valid_reviews"] == 0),
        "sample_cross_tab": [{"combined_label": label, "gate_decision": decision, "count": count}
                             for (label, decision), count in sorted(joint.items())],
        "combined_label_rule": "All three supported -> supported; any unsupported -> unsupported; otherwise insufficient. This is a derived presentation rule, not the gate's decision rule.",
        "reviewers": [{"path": r["path"], "sha256": r["sha256"],
            **{k: load(independent / r["path"])[k] for k in ("reviewer", "prior_familiarity")}}
            for r in labels["reviewer_records"]],
        "limits": ["Research-agent labels are not human gold or calibrated truth.",
            "Stratified diagnostic cards are not a random sample of independent tasks.",
            "Accepted does not establish useful; rejected does not establish useless.",
            "No Agent update or held-out performance gain is measured in this stage.",
            "Original 24 dispositions and generation cost remain separate from the 18 audited trajectories."]}
    # Output hash inventory covers all frozen phase and annotation records;
    # mutable progress reports are not substituted for original evidence.
    paths = [folder / "selection.json", folder / "selection.sha256"]
    for sub in ("review", "gate", "independent_review"):
        paths.extend(p for p in (folder / sub).rglob("*") if p.is_file())
    results["evidence_hashes"] = {p.relative_to(root).as_posix(): sha(p) for p in sorted(paths)}
    return folder, results


def markdown(v):
    s, d = v["summary"], v["summary"]["diagnosis_denominators"]
    lines = ["# N1-R1：已完成轨迹的原生诊断审计", "", "本报告由封存记录生成，不调用模型。", "",
        f"- 审计轨迹：{s['trajectory_denominator']}；任务：{s['task_denominator']}。",
        f"- 原始总体处置：{json.dumps(s.get('parent_execution_counts', {}), ensure_ascii=False)}。",
        f"- reviewer 有效性：{json.dumps(s['review_validity_counts'], ensure_ascii=False)}。",
        f"- 有效 review 中诊断总数：{d['native_diagnoses_among_valid_reviews']}；来源：{json.dumps(d['source_counts_among_valid_reviews'], ensure_ascii=False)}。",
        f"- 进入固定池的 Agent 诊断：{d['selected_cards_among_valid_reviews']}；未抽取：{d['not_sampled_agent_cards_among_valid_reviews']}。",
        f"- 准入判断：{json.dumps(d['gate_decision_counts'], ensure_ascii=False)}；无需模型的结构弃权：{d['structural_abstentions_without_model']}。",
        "", "## 预先封存的研究代理标签与 gate", "",
        "三个维度全部 supported 才合并为 supported；任一 unsupported 为 unsupported，其余为 insufficient。合并仅用于展示。", "",
        "| 合并标签 | gate 决定 | 卡数 |", "|---|---|---:|"]
    lines += [f"| {r['combined_label']} | {r['gate_decision']} | {r['count']} |" for r in v["sample_cross_tab"]]
    label_scope = ("本批卡片覆盖审计轨迹中实际生成的全部 Agent 诊断，无诊断因抽样上限被排除。"
                   if v["all_observed_agent_diagnoses_labeled"] else "这组卡按预定分层规则抽取，并非诊断总体的随机样本。")
    lines += ["", label_scope + "标签由研究代理提供，不能将表内比例外推为基准总体准确率。", "",
        "## 新增模型调用成本", "", "| 阶段 | CLI | 输入 tokens | 输出 tokens | 未知用量调用 |",
        "|---|---:|---:|---:|---:|"]
    lines += [f"| {phase} | {u['calls']} | {u['known_input_tokens']} | {u['known_output_tokens']} | {u['unknown_usage_calls']} |"
              for phase, u in v["phase_usage"].items()]
    lines += ["", "原始轨迹生成、既往探针与研究代理标注费用分别处理，不伪记为本阶段的零成本。", "",
        "## 解释范围", "", "本阶段只观察诊断与准入，未修改 Agent 策略。被接纳不等于一定有用，被拒收也不等于没有改进价值；不能据此报告任务成功率或优化收益。", "",
        "逐卡原文、三个标签维度、依据、模型决定、审计分母及哈希见 N1_FINDINGS.json。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="experiments/N1_R1_MANIFEST.json")
    args = parser.parse_args()
    folder, result = build(ROOT, ROOT / args.manifest)
    targets = [folder / "N1_FINDINGS.json", folder / "N1_FINDINGS.md"]
    if any(p.exists() for p in targets):
        raise ValueError("Never overwrite an issued report; create an explicit analysis revision")
    _write_new(targets[0], result)
    with targets[1].open("x", encoding="utf-8") as stream:
        stream.write(markdown(result))
    print(json.dumps({"report": str(targets[0]), "sha256": sha(targets[0]),
                      "usage": result["summary"]["usage"]}))
