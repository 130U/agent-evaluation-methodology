"""Read-only, standard-library audit of four pinned public retail archives.

This recomputes descriptive statistics from stored rewards. It does not run an
agent, invoke a judge, replay an environment, or establish semantic correctness.
Run from any directory: python scripts/audit_archives.py
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys


CURRENT_COMMIT = "2174a603f6d014ef94473ffa95957f6ce27100db"
HISTORICAL_COMMIT = "c30d59aaa71c65f9b9eb6a8f8636b48945028fcf"
MISSING = {"__audit_missing_field__": True}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def classify_reward(sim):
    if "reward_info" not in sim:
        return "missing_reward_info", None
    info = sim["reward_info"]
    if info is None:
        return "null_reward_info", None
    if not isinstance(info, dict):
        return "invalid_reward_info_type", None
    if "reward" not in info:
        return "missing_reward", None
    value = info["reward"]
    if value is None:
        return "null_reward", None
    if type(value) not in (int, float):
        return "invalid_reward_type", None
    if not math.isfinite(value):
        return "nonfinite_reward", None
    if value in (0, 1):
        return "binary_success" if value == 1 else "binary_failure", int(value)
    return "nonbinary_reward", value


def coverage(criteria, reward_info, expected_key, result_key, identity_key):
    """Structural checks only; identity is exact text, with multiplicity retained."""
    expected = criteria.get(expected_key, MISSING)
    returned = reward_info.get(result_key, MISSING)
    if expected == MISSING:
        return {"status": "expectation_unavailable", "issues": []}
    if expected is not None and not isinstance(expected, list):
        return {"status": "invalid_expectation_type", "issues": ["invalid_expectation_type"]}
    expected = expected or []
    if not expected:
        issues = ["unexpected_nonempty_results"] if isinstance(returned, list) and returned else []
        return {"status": "not_applicable", "expected_count": 0,
                "returned_count": len(returned) if isinstance(returned, list) else None,
                "issues": issues}
    result = {"status": "expected", "expected_count": len(expected),
              "returned_count": len(returned) if isinstance(returned, list) else None, "issues": []}
    issues = result["issues"]
    if returned == MISSING:
        issues.append("missing_result_field")
        return result
    if returned is None:
        issues.append("null_results")
        return result
    if not isinstance(returned, list):
        issues.append("invalid_results_type")
        return result
    if not returned:
        issues.append("empty_results")
    expected_ids = Counter(canonical(x) for x in expected)
    returned_ids = Counter(canonical(x.get(identity_key, MISSING)) for x in returned if isinstance(x, dict))
    if len(returned) != len(expected):
        issues.append("count_mismatch")
    if expected_ids != returned_ids:
        issues.append("identity_or_multiplicity_mismatch")
    if any(v > 1 for v in returned_ids.values()):
        issues.append("duplicate_returned_identity")
    if any(v > 1 for v in expected_ids.values()):
        issues.append("duplicate_expected_identity")
    if any(not isinstance(x, dict) for x in returned):
        issues.append("invalid_result_record_type")
    if any(isinstance(x, dict) and type(x.get("met")) is not bool for x in returned):
        issues.append("invalid_or_missing_met_type")
    if any(isinstance(x, dict) and not isinstance(x.get("justification"), str) for x in returned):
        issues.append("invalid_or_missing_justification_type")
    result["met_true"] = sum(isinstance(x, dict) and x.get("met") is True for x in returned)
    result["met_false"] = sum(isinstance(x, dict) and x.get("met") is False for x in returned)
    return result


def error_fields(value, path=""):
    """Return locations of explicit truthy error fields; do not guess from prose."""
    out = []
    if isinstance(value, dict):
        for key, val in value.items():
            location = f"{path}.{key}" if path else key
            if key.lower() in ("error", "errors", "exception") and val:
                out.append(location)
            elif isinstance(val, (list, dict)):
                out.extend(error_fields(val, location))
    elif isinstance(value, list):
        for index, val in enumerate(value):
            out.extend(error_fields(val, f"{path}[{index}]"))
    return out


def compare_tasks(old_tasks, current_tasks):
    old = {str(t["id"]): t for t in old_tasks if isinstance(t, dict) and "id" in t}
    current = {str(t["id"]): t for t in current_tasks if isinstance(t, dict) and "id" in t}
    shared = sorted(old.keys() & current.keys(), key=lambda x: (len(x), x))
    fields = ("description", "user_scenario", "initial_state", "evaluation_criteria")
    changed = {field: [] for field in fields}
    changed.update({"full_task_raw": [], "actions_name_arguments": [], "reward_basis": [],
                    "nl_assertions_null_as_empty": [], "communicate_info_null_as_empty": []})
    # This projection removes schema-only action fields; it is not semantic equivalence.
    def action_projection(t):
        actions = t.get("evaluation_criteria", {}).get("actions") or []
        return [{"name": a.get("name", MISSING), "arguments": a.get("arguments", MISSING)} for a in actions]
    for task_id in shared:
        a, b = old[task_id], current[task_id]
        for field in fields:
            if a.get(field, MISSING) != b.get(field, MISSING):
                changed[field].append(task_id)
        if a != b:
            changed["full_task_raw"].append(task_id)
        if action_projection(a) != action_projection(b):
            changed["actions_name_arguments"].append(task_id)
        ca, cb = a.get("evaluation_criteria", {}), b.get("evaluation_criteria", {})
        if ca.get("reward_basis", MISSING) != cb.get("reward_basis", MISSING):
            changed["reward_basis"].append(task_id)
        for key in ("nl_assertions", "communicate_info"):
            if (ca.get(key) or []) != (cb.get(key) or []):
                changed[f"{key}_null_as_empty"].append(task_id)
    return {"comparison_unit": "same task id; not proof of semantic equivalence",
            "shared_ids": len(shared), "historical_only_ids": sorted(old.keys() - current.keys()),
            "current_only_ids": sorted(current.keys() - old.keys()),
            "historical_task_fields": sorted(set().union(*(t.keys() for t in old.values()))),
            "current_task_fields": sorted(set().union(*(t.keys() for t in current.values()))),
            "changed_counts": {k: len(v) for k, v in changed.items()}, "changed_task_ids": changed}


def task_summary(tasks):
    return {"records": len(tasks), "unique_ids": len({str(t.get("id")) for t in tasks}),
            "reward_basis_counts": dict(Counter("+".join(t.get("evaluation_criteria", {}).get("reward_basis") or []) for t in tasks)),
            "nonempty_nl_tasks": sum(bool(t.get("evaluation_criteria", {}).get("nl_assertions")) for t in tasks),
            "nonempty_communicate_tasks": sum(bool(t.get("evaluation_criteria", {}).get("communicate_info")) for t in tasks),
            "expected_nl_assertions": sum(len(t.get("evaluation_criteria", {}).get("nl_assertions") or []) for t in tasks),
            "expected_communicate_items": sum(len(t.get("evaluation_criteria", {}).get("communicate_info") or []) for t in tasks),
            "canonical_sha256": digest(tasks)}


def audit_file(path, manifest_entry, current_tasks, current_policy):
    raw = path.read_bytes()
    hashes = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
              "git_blob_sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
    if any(hashes[k] != manifest_entry.get(k) for k in hashes):
        raise ValueError(f"Manifest mismatch: {path.name}: {hashes}")
    data = json.loads(raw)
    tasks, sims, info = data["tasks"], data["simulations"], data["info"]
    task_counts = Counter(str(t.get("id")) for t in tasks)
    task_map = {str(t["id"]): t for t in tasks}
    configured = info.get("num_trials")
    groups = defaultdict(list)
    for index, sim in enumerate(sims):
        groups[str(sim.get("task_id"))].append((index, sim))
    anomalies, run_rows, task_rows = [], [], []
    global_rewards, termination, nl_counts, comm_counts = Counter(), Counter(), Counter(), Counter()
    nl_issues, comm_issues = Counter(), Counter()
    simulation_ids = Counter(str(s.get("id")) for s in sims)
    reward_basis_counts = Counter()
    per_type_errors = Counter()
    costs = {name: {"sum": 0, "present_numeric_runs": 0, "other_runs": 0} for name in ("agent_cost", "user_cost")}
    for index, sim in enumerate(sims):
        task_id = str(sim.get("task_id"))
        task = task_map.get(task_id)
        criteria = task.get("evaluation_criteria", {}) if task else {}
        reward_status, reward = classify_reward(sim)
        global_rewards[reward_status] += 1
        ri = sim.get("reward_info") if isinstance(sim.get("reward_info"), dict) else {}
        nl = coverage(criteria, ri, "nl_assertions", "nl_assertions", "nl_assertion")
        comm = coverage(criteria, ri, "communicate_info", "communicate_checks", "info")
        nl_counts[nl["status"]] += 1
        comm_counts[comm["status"]] += 1
        nl_issues.update(nl["issues"])
        comm_issues.update(comm["issues"])
        run_issues = []
        if reward_status not in ("binary_success", "binary_failure"):
            run_issues.append(reward_status)
        if task is None:
            run_issues.append("unknown_task_id")
        basis = ri.get("reward_basis")
        reward_basis_counts[canonical(basis)] += 1
        if basis != criteria.get("reward_basis"):
            run_issues.append("stored_vs_embedded_reward_basis_mismatch")
        breakdown = ri.get("reward_breakdown")
        product = None
        if isinstance(basis, list) and isinstance(breakdown, dict) and all(numeric(breakdown.get(k)) for k in basis):
            product = math.prod(breakdown[k] for k in basis)
            if not numeric(reward) or not math.isclose(product, reward, rel_tol=0, abs_tol=1e-12):
                run_issues.append("stored_reward_breakdown_product_mismatch")
        else:
            run_issues.append("stored_reward_breakdown_not_checkable")
        messages = sim.get("messages")
        if not isinstance(messages, list):
            run_issues.append("invalid_or_missing_messages")
            messages = []
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        tool_error_count = sum(m.get("error") is True for m in tool_messages)
        tool_error_invalid = sum("error" not in m or type(m["error"]) is not bool for m in tool_messages)
        reward_error_paths = error_fields(ri)
        per_type_errors["tool_error_messages"] += tool_error_count
        per_type_errors["runs_with_tool_errors"] += bool(tool_error_count)
        per_type_errors["tool_error_field_unavailable_or_invalid"] += tool_error_invalid
        per_type_errors["runs_with_explicit_reward_error_fields"] += bool(reward_error_paths)
        termination[str(sim.get("termination_reason", "MISSING"))] += 1
        for name, totals in costs.items():
            if numeric(sim.get(name)):
                totals["sum"] += sim[name]
                totals["present_numeric_runs"] += 1
            else:
                totals["other_runs"] += 1
        row = {"archive_index": index, "id": sim.get("id"), "task_id": task_id,
               "trial": sim.get("trial"), "seed": sim.get("seed"), "timestamp": sim.get("timestamp"),
               "termination_reason": sim.get("termination_reason"), "reward_status": reward_status,
               "reward": reward, "stored_breakdown_product": product,
               "nl_coverage": nl, "communicate_coverage": comm,
               "tool_messages": len(tool_messages), "tool_error_messages": tool_error_count,
               "reward_error_paths": reward_error_paths, "issues": run_issues}
        run_rows.append(row)
        all_issues = run_issues + ["nl:" + x for x in nl["issues"]] + ["communicate:" + x for x in comm["issues"]]
        if all_issues:
            anomalies.append({"archive_index": index, "task_id": task_id, "trial": sim.get("trial"), "issues": all_issues})
    for task_id in sorted(task_map.keys() | groups.keys(), key=lambda x: (len(x), x)):
        indexed = groups.get(task_id, [])
        rows = [run_rows[i] for i, _ in indexed]
        trials = [r["trial"] for r in rows]
        rewards = Counter(r["reward_status"] for r in rows)
        issues = []
        if task_id not in task_map:
            issues.append("unknown_task")
        if task_counts.get(task_id) != 1:
            issues.append("missing_or_duplicate_task_definition")
        if type(configured) is not int or configured <= 0:
            issues.append("invalid_configured_trials")
        elif len(rows) != configured or Counter(canonical(x) for x in trials) != Counter(canonical(x) for x in range(configured)):
            issues.append("incomplete_or_duplicate_trial_slots")
        if any(rewards[k] for k in rewards if k not in ("binary_success", "binary_failure")):
            issues.append("nonbinary_or_missing_rewards")
        if any(simulation_ids[str(r["id"])] != 1 or r["id"] is None for r in rows):
            issues.append("duplicate_or_missing_simulation_id")
        c, n = rewards["binary_success"], len(rows)
        scores = {str(k): (math.comb(c, k) / math.comb(n, k) if not issues and n >= k else None) for k in range(1, 5)}
        task_rows.append({"task_id": task_id, "embedded_definition_count": task_counts.get(task_id, 0),
                          "configured_runs": configured, "observed_runs": n, "trials": trials,
                          "run_indices": [r["archive_index"] for r in rows], "reward_counts": dict(rewards),
                          "successes": c, "issues": issues, "pass_hat_k": scores})
    aggregate = {}
    for k in range(1, 5):
        eligible = [t for t in task_rows if t["pass_hat_k"][str(k)] is not None]
        aggregate[str(k)] = {"value": sum(t["pass_hat_k"][str(k)] for t in eligible) / len(task_rows) if len(eligible) == len(task_rows) and task_rows else None,
                             "total_task_denominator": len(task_rows), "estimable_tasks": len(eligible),
                             "unestimable_task_ids": [t["task_id"] for t in task_rows if t["pass_hat_k"][str(k)] is None]}
    env = info.get("environment_info", {})
    policy = env.get("policy")
    timestamps = sorted(s["timestamp"] for s in sims if isinstance(s.get("timestamp"), str))
    return {"filename": path.name, "source_url": manifest_entry["url"], "verified_input": hashes,
            "metadata": {"archive_timestamp": data.get("timestamp"), "simulation_timestamp_min": min(timestamps) if timestamps else None,
                         "simulation_timestamp_max": max(timestamps) if timestamps else None,
                         "embedded_git_commit": info.get("git_commit"), "agent_info": info.get("agent_info"),
                         "user_model": info.get("user_info", {}).get("llm"), "user_llm_args": info.get("user_info", {}).get("llm_args"),
                         "num_trials": configured, "max_steps": info.get("max_steps"), "max_errors": info.get("max_errors"),
                         "seed": info.get("seed"), "domain_name": env.get("domain_name"),
                         "policy_sha256": digest(policy), "policy_equals_current_exact": policy == current_policy,
                         "tool_defs_sha256": digest(env.get("tool_defs"))},
            "tasks": task_summary(tasks), "simulation_count": len(sims),
            "expected_simulation_count": len(tasks) * configured if type(configured) is int else None,
            "unique_simulation_ids": len(simulation_ids),
            "duplicate_simulation_ids": {k: v for k, v in simulation_ids.items() if v > 1},
            "duplicate_task_definitions": {k: v for k, v in task_counts.items() if v > 1},
            "reward_counts": dict(global_rewards), "reward_basis_counts": dict(reward_basis_counts),
            "termination_counts": dict(termination), "error_observations": dict(per_type_errors),
            "nl_coverage_status_counts": dict(nl_counts), "nl_issue_counts": dict(nl_issues),
            "communicate_coverage_status_counts": dict(comm_counts), "communicate_issue_counts": dict(comm_issues),
            "historical_stored_cost_fields": costs,
            "reviewer_field_runs": sum(any("review" in k.lower() for k in sim) for sim in sims),
            "pass_hat_k": aggregate, "task_rows": task_rows, "run_rows": run_rows,
            "run_anomalies": anomalies, "task_group_anomalies": [t for t in task_rows if t["issues"]],
            "historical_vs_current_tasks": compare_tasks(tasks, current_tasks)}


def render(report):
    files = report["archives"]
    lines = ["# 官方公开 retail 归档审计", "", "## 结论与证据边界", "",
             "本报告全量复算四份官方历史归档；没有新运行 Agent、judge、GEPA 或环境重放。原始文件保持只读。结果仅描述这些公开任务和归档配置。",
             "历史运行采用嵌入的 DB＋COMMUNICATE 评分。任务中的 NL 检查另行审计，不追溯套用当前 DB＋NL_ASSERTION 规则。",
             "", "## 输入与历史配置", "",
             f"- 输入归档共 {len(files)} 份、{sum(f['simulation_count'] for f in files)} 次运行；每份字节数、SHA256 和 Git blob SHA 均与下载清单匹配。",
             f"- 托管归档快照：`{CURRENT_COMMIT}`。四份文件内嵌自报运行提交：`{HISTORICAL_COMMIT}`。",
             "- 2026-09-17 查询该历史提交：官方 GitHub commit API 返回 422（No commit found），contents API 返回 404。因此历史源码尚无法独立核实，自报 SHA 不等于已复现环境。",
             "- 文件时间字段未附时区；本报告不推断它们为 UTC。模型名、采样设置、时间范围逐档保存在 JSON。",
             "", "## 历史分数复算", "",
             "对每任务四次归档运行，按 `comb(c,k)/comb(n,k)` 计算 pass^k，再对任务取等权平均。它估计 k 次均成功的比例（对 k 次子集等权），不是至少一次成功的 pass@k。若任务缺次、重复、缺分或非二值，该组保留且全体均值标为不可估；不悄悄删除。",
             "", "| 文件内嵌 Agent 模型 | 归档日期 | 任务×重复 | 成功/运行 | pass^1 | pass^2 | pass^3 | pass^4 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for f in files:
        values = [f['pass_hat_k'][str(k)]['value'] for k in range(1, 5)]
        score_text = " | ".join(f"{x:.4%}" if x is not None else "不可估" for x in values)
        lines.append(f"| {f['metadata']['agent_info']['llm']} | {f['metadata']['archive_timestamp'][:10]} | {f['tasks']['records']}×{f['metadata']['num_trials']} | {f['reward_counts'].get('binary_success', 0)}/{f['simulation_count']} | {score_text} |")
    lines += ["", "用户模拟器均为 `gpt-4.1-2025-04-14`，temperature=0；o4-mini Agent 使用 reasoning_effort=high，其余三个 Agent 的记录为 temperature=0。这些归档不是同时间当前模型排名，也没有随机代表性保证。", "", "## 完整性与错误分母", "",
              "| Agent | 结构异常运行 | 异常任务组 | 有期望NL运行 / NL异常 | 有期望COMMUNICATE运行 / 异常 | 工具error=True消息 / 涉及运行 |",
              "|---|---:|---:|---:|---:|---:|"]
    for f in files:
        nl_bad = sum(bool(r['nl_coverage']['issues']) for r in f['run_rows'])
        comm_bad = sum(bool(r['communicate_coverage']['issues']) for r in f['run_rows'])
        errors = f['error_observations']
        lines.append(f"| {f['metadata']['agent_info']['llm']} | {len(f['run_anomalies'])} | {len(f['task_group_anomalies'])} | {f['nl_coverage_status_counts'].get('expected', 0)} / {nl_bad} | {f['communicate_coverage_status_counts'].get('expected', 0)} / {comm_bad} | {errors.get('tool_error_messages', 0)} / {errors.get('runs_with_tool_errors', 0)} |")
    lines += ["", "NL 检查只对嵌入任务非空断言计入覆盖分母；null/空任务断言归为不适用。结构检查包含数量、逐文本身份及重数、met 布尔类型、justification 字符串。工具返回 error=True 是运行中观察到的错误，不等于数据损坏，也不自动等于最终失败。原始 judge 响应未独立保留，不能将记录字段缺失反推为 judge 当时遗漏。", "",
              f"全部终止标记：{dict(sum((Counter(f['termination_counts']) for f in files), Counter()))}。完整逐任务重复、逐运行奖励分类、异常记录与评分分母见 `archive_audit.json`。", "",
              "## 嵌入任务与当前任务的描述性差异", "",
              "同 task id 对齐仅用于版本比较。`full_task_raw` 包含 schema 差异，不能称为全部语义改变；动作另投影为有序 name＋arguments。null 与空断言列表只在明确标记的比较列中视为相同。", "",
              "| 项目 | 历史嵌入任务 | 当前固定快照 |", "|---|---:|---:|"]
    first = files[0]
    for key, label in (("records", "任务记录"), ("nonempty_nl_tasks", "非空 NL 任务"), ("expected_nl_assertions", "NL 断言条目"), ("nonempty_communicate_tasks", "非空 communicate 任务"), ("expected_communicate_items", "communicate 条目")):
        lines.append(f"| {label} | {first['tasks'][key]} | {report['current_tasks'][key]} |")
    lines += ["", f"四档嵌入任务是否完全一致：{report['cross_archive']['all_embedded_tasks_identical']}。", "",
              f"首档对当前快照的逐字段改变任务数：`{canonical(first['historical_vs_current_tasks']['changed_counts'])}`。完整 ID 清单逐档保留在 JSON。", "",
              f"历史 reward_basis：`{canonical(first['tasks']['reward_basis_counts'])}`；当前：`{canonical(report['current_tasks']['reward_basis_counts'])}`。", "",
              "版本改变本身不证明原任务或原分数错误。任何后续旧轨迹重评分必须明确标为新评价协议下的反事实重评分，并验证任务/环境/工具兼容性；不得把它混入本表或称作历史同协议复现。", "",
              "## 不可识别的结果", "",
              "归档含评价理由，但缺乏独立语义金标准，也未检出 simulation 顶层 reviewer 字段。因此无法估计自然反馈语义正确率、误归因率或反馈准入的泛化收益。结构覆盖完整不证明语义正确；没有结构异常也不证明评价器永不会产生异常。受控故障实验的检出率不能当成自然发生率。", "",
              "## 来源与复现", "",
              f"- [归档目录与托管提交](https://github.com/sierra-research/tau2-bench/tree/{CURRENT_COMMIT}/data/tau2/results/final)",
              f"- [当前任务快照](https://github.com/sierra-research/tau2-bench/blob/{CURRENT_COMMIT}/data/tau2/domains/retail/tasks.json)",
              f"- [当前官方 pass^k 实现](https://github.com/sierra-research/tau2-bench/blob/{CURRENT_COMMIT}/src/tau2/metrics/agent_metrics.py#L113)",
              f"- [历史提交核查端点（本次未找到）](https://api.github.com/repos/sierra-research/tau2-bench/commits/{HISTORICAL_COMMIT})",
              "- 分析登记：`experiments/OFFLINE_REGISTRATION.md`；输入清单：`data/archives/DOWNLOAD_MANIFEST.json`。",
              "- 执行：`.venv/Scripts/python.exe -X utf8 scripts/audit_archives.py`；脚本仅用 Python 标准库，运行阶段不联网。",
              "- JSON 中保留脚本 SHA256、输入哈希、完整分母与源链接。报告时间字段会变化；分析数值应保持确定性。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    archive_dir = root / "data/archives"
    manifest_path = archive_dir / "DOWNLOAD_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = sorted(archive_dir.glob("*retail*.json"))
    if len(paths) != 4 or not isinstance(manifest, list) or len(manifest) != 4:
        raise ValueError("Require exactly four retail files and four completed manifest entries")
    entries = {Path(x["path"]).name: x for x in manifest}
    if len(entries) != 4 or set(entries) != {p.name for p in paths}:
        raise ValueError("Manifest filename set mismatch")
    current_path = root / "sources/tau2/data/tau2/domains/retail/tasks.json"
    current_tasks = json.loads(current_path.read_text(encoding="utf-8"))
    policy_path = current_path.with_name("policy.md")
    policy = policy_path.read_text(encoding="utf-8") if policy_path.exists() else None
    files = [audit_file(p, entries[p.name], current_tasks, policy) for p in paths]
    report = {"schema_version": 1, "analysis": "historical_archive_descriptive_audit",
              "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "registration_sha256": hashlib.sha256((root / "experiments/OFFLINE_REGISTRATION.md").read_bytes()).hexdigest(),
              "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "current_tasks_bytes_sha256": hashlib.sha256(current_path.read_bytes()).hexdigest(),
              "current_tasks_commit": CURRENT_COMMIT, "current_tasks": task_summary(current_tasks),
              "historical_source_lookup": {"checked_date": "2026-09-17", "embedded_commit": HISTORICAL_COMMIT,
                  "commit_api_status": 422, "contents_api_status": 404,
                  "meaning": "metadata self-report verified across files; historical source not independently retrievable in this check",
                  "not_performed_by_this_offline_script": True},
              "metric_policy": {"formula": "per task comb(c,k)/comb(n,k), then equal task mean",
                  "missing_nonbinary_duplicate_policy": "retain all task/run denominators; strict full mean null if any group unestimable",
                  "inference": "descriptive; no independent-run confidence interval or population claims"},
              "cross_archive": {"all_embedded_tasks_identical": len({f['tasks']['canonical_sha256'] for f in files}) == 1,
                  "embedded_commits": sorted({f['metadata']['embedded_git_commit'] for f in files}),
                  "all_policies_identical": len({f['metadata']['policy_sha256'] for f in files}) == 1,
                  "all_tool_defs_identical": len({f['metadata']['tool_defs_sha256'] for f in files}) == 1},
              "archives": files}
    result_dir = root / "results"
    result_dir.mkdir(exist_ok=True)
    (result_dir / "archive_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (result_dir / "ARCHIVE_AUDIT.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({"archives": len(files), "simulations": sum(f['simulation_count'] for f in files),
                      "run_anomalies": sum(len(f['run_anomalies']) for f in files),
                      "task_group_anomalies": sum(len(f['task_group_anomalies']) for f in files),
                      "files": [{"model": f['metadata']['agent_info']['llm'], "successes": f['reward_counts'].get('binary_success', 0),
                                 "pass_hat_k": {k: v['value'] for k, v in f['pass_hat_k'].items()},
                                 "nl_issues": f['nl_issue_counts'], "communicate_issues": f['communicate_issue_counts'],
                                 "errors": f['error_observations']} for f in files]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
