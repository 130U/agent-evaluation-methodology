"""Read explicit source files and create a NEW public repository overlay.

Never follows a recursive directory allowlist, reads credentials, runs a model,
downloads, touches Git, or edits source files. Run only after the selected
reports are ready for public review. This is a derived package, not the original
frozen subscription experiment. No overwrite/resume of an existing destination.
"""
from __future__ import annotations
import argparse
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = PurePosixPath("supporting-evidence/tau-feedback-evidence")
CORE = PurePosixPath("core/03-tau-feedback-evidence")
NAV = PurePosixPath("projects/tau-feedback-evidence/README.md")
MAX_FILE_BYTES = 50 * 1024 * 1024

# Names are intentionally enumerated. Newly added logs, scripts and results are
# excluded until this list is explicitly reviewed; no results/n1 path is read.
MODULES = """__init__ action_origin admission contracts episode episode_privacy gepa_adapter
gepa_milestones local_backend local_runtime n1_audit pre_admission_adapter
projection subscription_backend subscription_client subscription_episode
subscription_evidence subscription_feedback subscription_http_client
subscription_optimization subscription_reflection""".split()
SCRIPTS = """audit_archives download_archives download_gepa download_local_model
download_runtime replay_archives run_contract_experiment run_projection_experiment
run_offline render_findings verify_sources run_gepa_example build_n1_review_packets
freeze_n1_selection run_subscription_n1 register_subscription_n1r1
summarize_n1_results register_subscription_g2 run_subscription_g2
run_action_origin_audit reproduce_origin_initialization export_n1_public_summary""".split()
# Full subscription transport tests require the unpublished original catalog or
# logs. This public default suite deliberately contains only self-contained
# offline/component tests; its count is not the original full-suite count.
TESTS = """test_archive_audit test_red_team test_admission test_gepa_adapter
test_gepa_milestones test_pre_admission_adapter test_episode_privacy test_episode
test_local_backend test_action_origin""".split()
FIXED_FILES = (
    "pyproject.toml", "requirements.lock", "STRUCTURAL_GROUPS.json",
    "sources/tau2/data/tau2/domains/retail/tasks.json",
    "sources/tau2/data/tau2/domains/retail/policy.md",
    "experiments/archive_sources.json", "experiments/runtime_sources.json", "experiments/gepa_sources.json",
    "experiments/local_model_assets.json", "experiments/local_model_assets_v2.json", "experiments/local_model_assets_v3.json",
    "experiments/OFFLINE_REGISTRATION.md", "experiments/REPLAY_AMENDMENT.md",
    "results/archive_audit.json", "results/contract_experiment.json", "results/projection_experiment.json",
    "results/replay_experiment.json", "results/ARCHIVE_AUDIT.md", "results/OFFLINE_FINDINGS.md",
    "research/RED_TEAM_INITIAL.md", "research/ADMISSION_MECHANISM.md", "research/GEPA_INTEGRATION_NOTES.md",
    "research/SUBSCRIPTION_OPTIMIZATION_INTEGRATION.md", "research/G1_TRANSPORT_RECOVERY_OPTIONS.md",
    "research/N1_R1_METHOD_REVIEW.md", "research/N1_METHOD_REVIEW.md", "research/N1_PACKET_LABEL_REVIEW.md",
    "research/ACTION_ORIGIN_RELATED_WORK.md", "research/G2_EXPLORATORY_CASE_PROTOCOL.md",
)
LICENSE_MAPPING = {"vendor/tau2/LICENSE": PACKAGE / "third_party/TAU2_LICENSE.txt",
                   "vendor/gepa/LICENSE": PACKAGE / "third_party/GEPA_LICENSE.txt"}
# Optional reports are explicit and must be requested individually; their
# presence alone never makes an in-progress report a final result.
OPTIONAL_REPORTS = {
    "RESEARCH_REPORT.md": CORE / "RESEARCH_REPORT.md",
    "PROJECT_STATUS.md": CORE / "PROJECT_STATUS.md",
    "TASK_CARD_2026-09-17.md": CORE / "TASK_CARD.md",
    "FINAL_REPORT.md": CORE / "FINAL_REPORT.md",
    "results/N1_R1_FINDINGS.md": CORE / "N1_R1_FINDINGS.md",
    "results/N1_R1_PUBLIC_SUMMARY.json": PACKAGE / "results/N1_R1_PUBLIC_SUMMARY.json",
    "results/E4_PUBLIC_SUMMARY.json": PACKAGE / "results/E4_PUBLIC_SUMMARY.json",
    "research/E4_ACTION_ORIGIN_REVIEW.md": CORE / "E4_ACTION_ORIGIN_REVIEW.md",
    "research/DEFENSE_BRIEF.md": CORE / "DEFENSE_BRIEF.md",
    "research/G2_EXPLORATORY_RESULT_REVIEW.md": CORE / "G2_EXPLORATORY_RESULT_REVIEW.md",
    "results/G2_PUBLIC_SUMMARY.json": PACKAGE / "results/G2_PUBLIC_SUMMARY.json",
}
EXCLUDED = ["raw subscription client/request/event/stderr/stdout/final logs", "ACL/SID/SDDL records",
    "full CLI model catalog and raw catalog provenance events", "original source ZIPs and original experiment manifests",
    "models and model binaries", "vendor and downloaded archive trees", "authentication, personal config and caches",
    "original N1 review/gate/selection/annotation directories", "unlisted tests, scripts, reports and logs"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_path(root, name):
    path = root / name
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError("Source path escaped or is a symlink: " + name)
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("Source directory is a link: " + name)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Missing, non-file or oversized allowlisted source: " + name)
    return path


def portable_client(text):
    """Change only two module assignments; preserve every function AST."""
    before = ast.parse(text)
    assignments = {n.targets[0].id: n for n in before.body if isinstance(n, ast.Assign)
                   and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
    executable, warnings = assignments["EXE"], assignments["KNOWN_WARNINGS"]
    if not (isinstance(executable.value, ast.Call) and isinstance(executable.value.func, ast.Name)
            and executable.value.func.id == "Path" and len(executable.value.args) == 1
            and isinstance(executable.value.args[0], ast.Constant)
            and str(executable.value.args[0].value).endswith("codex.exe")):
        raise ValueError("Unrecognized original executable assignment; review export transform")
    messages = [n.value for n in ast.walk(warnings.value) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    if len(messages) != 1 or not messages[0].startswith("Under-development features enabled: skip_host_skill_discovery."):
        raise ValueError("Unrecognized exact configuration warning")
    prefix, local_path = messages[0].rsplit(" in ", 1)
    if not local_path.endswith("config.toml."):
        raise ValueError("Unrecognized local warning path")
    replacements = [
        (executable, '# Public copy: an unset value is an unusable path, never a searched executable.\n'
         'EXE = Path(os.environ.get("TAU_CODEX_EXE", "__SET_TAU_CODEX_EXE__"))'),
        (warnings, 'LOCAL_CONFIG_WARNING_PATH = os.environ.get("TAU_CODEX_CONFIG_PATH", "__SET_TAU_CODEX_CONFIG_PATH__")\n'
         'KNOWN_WARNINGS = frozenset({\n    ' + repr(prefix + " in ") + ' + LOCAL_CONFIG_WARNING_PATH + ".",\n'
         '    HOST_DISABLED_WARNING,\n})')]
    lines = text.splitlines(keepends=True)
    for node, value in sorted(replacements, key=lambda pair: pair[0].lineno, reverse=True):
        lines[node.lineno - 1:node.end_lineno] = [value + "\n"]
    output = "".join(lines)
    after = ast.parse(output)
    functions = lambda tree: [ast.dump(n, include_attributes=False) for n in ast.walk(tree)
                              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if functions(before) != functions(after):
        raise ValueError("Public transform changed function behavior")
    for name in ("EXE_SHA256", "MODEL_CATALOG_SHA256", "HOST_DISABLED_WARNING"):
        new = next(n for n in after.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
        if ast.dump(assignments[name], include_attributes=False) != ast.dump(new, include_attributes=False):
            raise ValueError("Public transform changed a pinned hash/required warning")
    return output, ["EXE path -> TAU_CODEX_EXE (no usable default; binary SHA256 unchanged)",
                    "only local config path in exact warning -> TAU_CODEX_CONFIG_PATH; all function ASTs unchanged"]


def public_text(root, name, data, mapping):
    text, changes = data.decode("utf-8-sig"), []
    if name == "src/tau_feedback/subscription_client.py":
        text, changes = portable_client(text)
    if name == "scripts/run_offline.py":
        old = '"end_to_end_gepa_experiment_status":"not_run_model_access_unavailable"'
        if text.count(old) != 1:
            raise ValueError("Review historical offline status before export")
        text = text.replace(old, '"end_to_end_gepa_experiment_status":"not_part_of_offline_reproduction"')
        changes.append("historical model-access status -> offline reproduction scope; original experiment source unchanged")
    if not text.strip() and PurePosixPath(name).name == "__init__.py":
        text = "# Package marker retained in the public export.\n"
        changes.append("empty package marker -> explanatory comment")
    if name.endswith(".md"):
        def link(match):
            label, target = match.group(1), match.group(2).strip("<>")
            if re.match(r"(?:https?://|mailto:|#)", target):
                return match.group(0)
            file_name, _, fragment = target.partition("#")
            candidate = (root / name).parent / file_name
            if candidate.resolve().is_relative_to(root):
                original = candidate.resolve().relative_to(root).as_posix()
                if original in mapping:
                    relative = os.path.relpath(str(mapping[original]), str(mapping[name].parent)).replace("\\", "/")
                    return f"[{label}]({relative}" + ("#" + fragment if fragment else "") + ")"
            return label + "（本地审计材料，未纳入公开包）"
        rewritten = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", link, text)
        if rewritten != text:
            changes.append("remap included Markdown links; label omitted local targets explicitly")
            text = rewritten
    home = str(Path.home())
    # Do not silently rewrite implementation literals other than the reviewed
    # client assignments. Reports/derived JSON may contain local file paths.
    forms = sorted({home, home.replace("\\", "/"), json.dumps(home)[1:-1]}, key=len, reverse=True)
    if not name.endswith(".py"):
        for form in forms:
            if form and form in text:
                text = text.replace(form, "LOCAL_HOME")
                changes.append("local home prefix -> LOCAL_HOME")
    if name.endswith(".json"):
        json.loads(text)
    if name.endswith(".py"):
        ast.parse(text)
    # This bounded check supplements the allowlist, not a promise of universal
    # secret detection. Do not print matching values in errors.
    forbidden = [r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+", r"/" r"Users/[^/\s\"']+",
        r"S-1-5-21-\d+-\d+-\d+", r"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}",
        r"sk-(?:proj-)?[A-Za-z0-9_-]{24,}", r"(?:" r"O:" r"S-1-|D:\([AD];;)"]
    if any(re.search(pattern, text) for pattern in forbidden):
        raise ValueError("Public source contains a blocked local identifier/credential/ACL pattern: " + name)
    output = text.encode("utf-8")
    if not output:
        raise ValueError("Zero-byte public file: " + name)
    if output != data and not changes:
        changes.append("UTF-8 BOM/newline normalization")
    return output, changes


def generated_files(reports=()):
    readme = """# τ-bench 反馈证据研究：公开复现包

这是经白名单导出的衍生副本。`PUBLIC_EXPORT_MANIFEST.json` 记录原始文件与导出文件 SHA256、变换及未公开类别；它不冒充原始冻结实验目录。

## 离线 E0–E3 复现（Windows，Python 3.12）

首次下载公开依赖需要网络；后续离线审计和受控实验不调用模型。

```powershell
python -m venv .venv
.venv\\Scripts\\python.exe scripts\\download_runtime.py
.venv\\Scripts\\python.exe scripts\\download_archives.py
.venv\\Scripts\\python.exe -m pip install -r requirements.lock
.venv\\Scripts\\python.exe -m pip install --no-deps -e vendor\\tau2
.venv\\Scripts\\python.exe -m pip install uv==0.12.15
.venv\\Scripts\\python.exe scripts\\download_gepa.py
.venv\\Scripts\\uv.exe --cache-dir vendor\\gepa\\.uv-cache pip install --python .venv\\Scripts\\python.exe --no-deps --editable vendor\\gepa
.venv\\Scripts\\uv.exe --cache-dir vendor\\gepa\\.uv-cache run --no-project --python .venv\\Scripts\\python.exe python -B -X utf8 scripts\\run_offline.py
```

阅读 [离线结论](results/OFFLINE_FINDINGS.md)、[归档审计](results/ARCHIVE_AUDIT.md) 和 [复现边界](PUBLIC_REPRODUCTION_BOUNDARY.md)。公开默认测试只是可独立运行的离线/组件子集，其运行数量不能替代原研究的全量测试记录。下载后保留上游许可证。

## 不调用模型的动作来源控制复现

完成上述公开源码与依赖准备后运行：

```powershell
.venv\\Scripts\\python.exe -B -X utf8 scripts\\reproduce_origin_initialization.py
```

该入口用官方初始化器检查 12 个开发任务、3 种策略；生成与 run/step 均被阻断。它只复现初始化控制，不需要原始 N1/G1 调用账本，也不会重建自然诊断审计。输出目录已存在时拒绝覆盖；原研究结果与复现结果必须分别保存。
"""
    boundary = """# 公开副本与原始实验的边界

- 本包不包含原始 CLI 请求/事件/stderr、ACL记录、完整模型目录、原始源码 ZIP、认证、权重、vendor 或原始大型归档。未公开的调用证据不能仅凭一个哈希由第三方独立重建。
- 原订阅实验还需要作者的冻结源码和 manifest、原始调用日志、独立检查记录、模型目录快照及其来源。它们没有随本包发布；公开副本的路径变换产生新哈希，因此不能通过原 manifest 的字节一致性验证，也不能直接重跑历史注册脚本。
- 订阅使用复现者自己的可用账号和 CLI 登录状态，不得复制作者凭据。原 EXE SHA256 和目录 SHA256 校验仍存在。公开 client 的 TAU_CODEX_EXE 必须设置为兼容 EXE 的绝对路径，TAU_CODEX_CONFIG_PATH 必须设置为启动 warning 中配置文件的精确路径；未配置时使用不可用占位值，不搜索其他可执行文件，也不允许模糊 warning。
- 路径参数化没有改变 `_validate_events` 或其他函数的 AST。host-disabled warning、未知事件拒收、用量未知即停止、预算和 Windows Job 子进程清理逻辑均保留；这不是 OS 级安全隔离的证明。
- 完整 CLI model catalog 不在公开包中。即使拥有匹配 CLI，也无法只凭公开文件恢复原订阅实验。需要独立获取并核验适用目录、重新预检和登记；不修改本包中的哈希以伪装相同实验。云端模型权重与随机性也未被固定，不能保证逐字相同。
- τ2 零售姓名、邮箱、订单等来自上游公开合成基准，不是真实客户采集。对行动模型隐藏的任务参考信息，对研究者可公开获得；两种角色可见性不能混称私有数据集。
- E0 使用 sources/tau2 下的早期阅读副本，两文件各比固定官方原始字节多一个末尾 LF；导出前逐字核验此唯一差别。它们保留原 E0 输入哈希与复算路径，不能用其哈希冒充官方 Git blob。真正运行时源码仍由 vendor/tau2 下载器按上游 blob 核验。
- 归档历史提交不能取回时，E3 是当前环境迁移诊断，不是历史环境的精确复现。受控故障与组件测试不是自然错误率，也不是 Agent/GEPA 提升。
- 公开文件内原有哈希保留其历史含义。文件是否与原版相同，应查导出清单的 original_sha256/export_sha256；脱敏副本不得使用原哈希认证。
- 发布前仍需核对所选报告的终局状态、远端最新规则、链接和许可证。此导出器不发布、不改根 README、不修改仓库原有 manifest；研究集合入口只应另行增加项目索引。
"""
    notices = """# 第三方来源与许可边界

- [τ2-bench 固定来源](https://github.com/sierra-research/tau2-bench/tree/2174a603f6d014ef94473ffa95957f6ce27100db)，上游 MIT；[原始许可与署名](third_party/TAU2_LICENSE.txt)。公开合成任务中的引文沿用该来源。
- [GEPA 固定来源](https://github.com/gepa-ai/gepa/tree/15ee314f9c7d34ec153b809d401f42f55c4dcd76)，上游 MIT；[原始许可与署名](third_party/GEPA_LICENSE.txt)。
- 本包提供公开来源锁，不重新分发模型权重或 CLI。Qwen 来源锁标注 Apache-2.0；LFM 来源锁标注 LFM Open License v1.0。来源锁不是统一授予许可，不能把所有第三方资产统称 MIT。
- 本导出器不替作者决定原创代码的许可证；应在公开发布前单独确认。代码可见不等于已授予开源许可。
"""
    navigation = """# τ-bench 反馈证据研究

[研究材料](../../core/03-tau-feedback-evidence/README.md) · [代码、来源锁与离线复现](../../supporting-evidence/tau-feedback-evidence/README.md)

研究区分评分完整性、诊断证据质量与最终策略收益；结论范围以各报告的实际实验阶段为准。
"""
    core = """# τ-bench 反馈证据研究

这是研究集合的一个独立项目。[公开复现包](../../supporting-evidence/tau-feedback-evidence/README.md) 提供来源锁、代码与离线结果；[复现边界](../../supporting-evidence/tau-feedback-evidence/PUBLIC_REPRODUCTION_BOUNDARY.md) 列出未公开材料和订阅限制。

本次导出只包含清单中显式选择的报告。离线实验、真实开发轨迹与后续诊断审计属于不同证据层次；不能合并为策略提升结论。
"""
    core += "\n## 研究交付\n\n"
    for name in reports:
        target = OPTIONAL_REPORTS[name]
        if target.parent == CORE:
            core += f"- [{target.stem}]({target.name})\n"
    ignore = ".venv/\n__pycache__/\n*.pyc\nvendor/\nmodels/\ndata/archives/\n.env\n.codex/\nexperiments/*catalog*\nexperiments/snapshots/\nresults/subscription_pilot/\nresults/g1/\nresults/n1/\ndelivery/\n"
    return {PACKAGE / "README.md": readme, PACKAGE / "PUBLIC_REPRODUCTION_BOUNDARY.md": boundary,
            PACKAGE / "THIRD_PARTY_NOTICES.md": notices, PACKAGE / ".gitignore": ignore,
            NAV: navigation, CORE / "README.md": core}


def export(root, reports=()):
    root = Path(root).resolve()
    destination = root / "delivery/repository-overlay"
    if destination.exists():
        raise FileExistsError("Public destination already exists; never overwrite or resume it")
    if len(reports) != len(set(reports)) or set(reports) - OPTIONAL_REPORTS.keys():
        raise ValueError("Only distinct explicitly allowlisted reports may be selected")
    for name in ("tasks.json", "policy.md"):
        suffix = "data/tau2/domains/retail/" + name
        if (root / "sources/tau2" / suffix).read_bytes() != (root / "vendor/tau2" / suffix).read_bytes() + b"\n":
            raise ValueError("Historical reading copy differs beyond its recorded trailing LF: " + name)
    names = [*FIXED_FILES, *(f"src/tau_feedback/{n}.py" for n in MODULES),
             *(f"scripts/{n}.py" for n in SCRIPTS), *(f"tests/{n}.py" for n in TESTS)]
    mapping = {name: PACKAGE / name for name in names}
    mapping.update(LICENSE_MAPPING)
    mapping.update({name: OPTIONAL_REPORTS[name] for name in reports})
    planned, records, original_hashes = {}, [], {}
    for name, target in sorted(mapping.items()):
        if any(c.isspace() for c in target.as_posix()):
            raise ValueError("Repository path contains whitespace")
        raw = source_path(root, name).read_bytes()
        output, changes = public_text(root, name, raw, mapping)
        original_hashes[name] = digest(raw)
        planned[target] = output
        records.append({"source": name, "destination": target.as_posix(), "original_sha256": digest(raw),
                        "export_sha256": digest(output), "transformations": changes})
    for target, text in generated_files(reports).items():
        planned[target] = text.encode("utf-8")
        records.append({"source": None, "destination": target.as_posix(), "original_sha256": None,
                        "export_sha256": digest(planned[target]), "transformations": ["generated public navigation/documentation"]})
    # Refuse a source change during preparation. No source has been written.
    for name, expected in original_hashes.items():
        if digest(source_path(root, name).read_bytes()) != expected:
            raise ValueError("Source changed during export preparation: " + name)
    manifest = {"schema_version": 1, "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "derived_local_overlay_not_published", "original_experiment_unchanged": True,
        "source_paths_are_relative": True, "selected_optional_reports": list(reports), "excluded_categories": EXCLUDED,
        "limitations": "Explicit allowlist and bounded text checks; not a universal privacy/security audit. Original subscription reconstruction is incomplete.",
        "files": records, "manifest_self_hash": "Self and its .sha256 sidecar are not included in the files list; the sidecar hashes this manifest."}
    manifest_path = PACKAGE / "PUBLIC_EXPORT_MANIFEST.json"
    content = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    planned[manifest_path] = content
    planned[PACKAGE / "PUBLIC_EXPORT_MANIFEST.sha256"] = (digest(content) + "  PUBLIC_EXPORT_MANIFEST.json\n").encode("ascii")
    if any(not data or len(data) > MAX_FILE_BYTES for data in planned.values()):
        raise ValueError("Public file is empty or exceeds the repository size cap")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    for target, data in planned.items():
        path = destination / target
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    return {"destination": "delivery/repository-overlay", "files": len(planned), "source_files": len(mapping),
            "manifest_sha256": digest(content), "model_calls": 0, "published": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-report", action="append", default=[], choices=sorted(OPTIONAL_REPORTS))
    args = parser.parse_args(argv)
    print(json.dumps(export(ROOT, args.include_report), ensure_ascii=False))


if __name__ == "__main__":
    main()
