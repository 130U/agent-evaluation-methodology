"""Append reviewed G4/C1 evidence to the immutable, verified G3 public copy.

Default is a read-only plan. --write creates one new local overlay. No model,
network, Git, authentication read or original-run directory traversal is used.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import export_public_package as original
import prepare_repository_change as repository

DESTINATION = "delivery-final-2026-09-17-v2"
PREVIOUS = "delivery-g3-final"
PREVIOUS_PROPOSAL_SHA256 = "c72a2c2babfe973725fe46c9d82edf374d9dd8f37360728c5e4f9483d7c337ed"
PREVIOUS_MANIFEST_SHA256 = "11ceb595881c5dadda1e95f753689e131febaabccea8574222eeb3429a7b97ae"
FINAL_TEXT_REVIEW_SHA256 = "25ce904f86b95d4702107b8b5b60d3c7ff0bc80b5d2e46ff40ce6a1424a9724d"
PACKAGE, CORE = original.PACKAGE, original.CORE
MANIFEST_PATH = PACKAGE / "PUBLIC_EXPORT_MANIFEST.json"
SIDECAR_PATH = PACKAGE / "PUBLIC_EXPORT_MANIFEST.sha256"
CORE_UPDATES = {
    "RESEARCH_REPORT.md": CORE / "RESEARCH_REPORT.md",
    "PROJECT_STATUS.md": CORE / "PROJECT_STATUS.md",
    "TASK_CARD_2026-09-17.md": CORE / "TASK_CARD.md",
    "research/DEFENSE_BRIEF.md": CORE / "DEFENSE_BRIEF.md",
}
MODULES = "g4_assessment g4_paired recovered_events subscription_recovery_client".split()
SCRIPTS = """register_g4_paired run_g4_paired run_g4_test finalize_g4_selection
build_g4_review_packets bind_g4_review_labels report_g4_stopped report_g4_opportunities
audit_recovered_transport c1_synthetic_calibration report_c1_synthetic
export_final_research_package prepare_final_repository_change prepare_repository_change""".split()
TESTS = """test_g4_assessment test_g4_paired test_g4_paired_review test_g4_measurement
test_g4_stage_review test_finalize_g4_selection test_recovered_events test_subscription_recovery_client
test_final_research_export""".split()
FIXED = (
    "research/FINAL_TEXT_REVIEW.md", "research/G4_STOP_RESULT_REVIEW.md",
    "research/G4_PROTOCOL.md", "research/G4_SPLIT_REVIEW.md", "experiments/G4_SPLIT_CANDIDATE.json",
    "research/G4_RECOVERY_CLIENT_REVIEW.md", "research/G4_PAIRED_IMPLEMENTATION_REVIEW.md",
    "research/G4_LAUNCH_REVIEW.md", "research/G4_SELECTION_FINALIZER_REVIEW.md",
    "research/G4_COHORT_RED_BLIND_LABELS.json", "research/G4_COHORT_RELATED_BLIND_LABELS.json",
    "research/g4-review/cohort/PACKET_SEAL.json", "research/g4-review/cohort/packets/COMMON.json",
    "research/g4-review/cohort/packets/REVIEW_INSTRUCTIONS.txt",
    "research/g4-review/cohort/packets/review-2eefdb41c7e71759.json",
    "research/g4-review/cohort/packets/review-6db0fdda658d24f7.json",
    "research/g4-review/cohort/packets/review-9487584d8be36ec6.json",
    "research/g4-review/cohort/packets/review-f7bc2815caa83b21.json",
    "experiments/C1_CONTROLLED_CASES_DRAFT.json", "research/C1_BLIND_PACKET.json",
    "research/C1_BLIND_MAP.json", "research/C1_RED_BLIND_LABELS.json",
    "research/C1_SYNTHETIC_PROTOCOL.md", "research/C1_CONTROLLED_CALIBRATION_DRAFT.md",
    "research/C1_EXECUTION_REVIEW.md", "research/C1_RESULT_REVIEW.md",
)
RESULTS = (
    "results/G4_STOP_REPORT.json", "results/G4_STOP_TRANSPORT_AUDIT.json",
    "results/G4_OPPORTUNITY_REPORT.json", "results/C1_SYNTHETIC_REPORT.json",
)
REPORTS = {"research/G4_STOP_RESULT.md": CORE / "G4_STOP_RESULT.md",
           "research/C1_SYNTHETIC_RESULT.md": CORE / "C1_SYNTHETIC_RESULT.md"}
POST_REPORTERS = (
    "scripts/report_g4_stopped.py", "scripts/report_g4_opportunities.py",
    "scripts/audit_recovered_transport.py", "scripts/report_c1_synthetic.py",
    "scripts/build_g4_review_packets.py", "scripts/bind_g4_review_labels.py",
    "scripts/finalize_g4_selection.py",
)
FROZEN_REVIEW_BYTES = set(FIXED) & {
    "research/G4_COHORT_RED_BLIND_LABELS.json", "research/G4_COHORT_RELATED_BLIND_LABELS.json",
    "research/C1_RED_BLIND_LABELS.json", "research/C1_BLIND_PACKET.json", "research/C1_BLIND_MAP.json",
    "experiments/C1_CONTROLLED_CASES_DRAFT.json",
    *(name for name in FIXED if name.startswith("research/g4-review/cohort/")),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def scan_public(name, raw):
    repository.relative_name(name)
    require(bool(raw) and len(raw) <= original.MAX_FILE_BYTES, "Empty or oversized public file: " + name)
    text = raw.decode("utf-8")
    blocked = [r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+", r"/" + r"Users/[^/\s\"']+",
        r"S-1-5-21-\d+-\d+-\d+", r"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}",
        r"sk-(?:proj-)?[A-Za-z0-9_-]{24,}", r"(?:O:" + r"S-1-|D:\([AD];;)"]
    require(not any(re.search(pattern, text) for pattern in blocked), "Blocked local/secret/ACL pattern in " + name)
    if name.endswith(".py"):
        ast.parse(text, filename=name)
    if name.endswith(".json"):
        json.loads(text)


def inherit_verified(root, *, proposal_sha256=PREVIOUS_PROPOSAL_SHA256, manifest_sha256=PREVIOUS_MANIFEST_SHA256):
    """Read only the already public files enumerated by the locked proposal."""
    root = Path(root).resolve()
    proposal_name = PREVIOUS + "/PROPOSED_CHANGE.json"
    proposal_raw = original.source_path(root, proposal_name).read_bytes()
    require(original.digest(proposal_raw) == proposal_sha256, "Immutable G3 proposal changed")
    proposal = json.loads(proposal_raw)
    require(proposal["base_commit"] == repository.BASE_COMMIT and proposal["base_tree"] == repository.BASE_TREE,
            "Inherited proposal has another repository base")
    folder = root / PREVIOUS / "repository-overlay"
    expected = {row["path"]: row["sha256"] for row in proposal["new_files"]}
    edits = proposal["existing_file_modifications"]
    require({r["path"] for r in edits} == {"README.md", repository.MANIFEST}, "Unexpected existing-file edits in G3")
    require(len(expected) == len(proposal["new_files"]), "Duplicate inherited file")
    expected.update({r["path"]: r["after_sha256"] for r in edits})
    actual_names = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    require(actual_names == set(expected), "Inherited overlay file inventory changed")
    all_files = {}
    for name, digest in expected.items():
        repository.relative_name(name)
        raw = original.source_path(root, PREVIOUS + "/repository-overlay/" + name).read_bytes()
        require(original.digest(raw) == digest, "Inherited public bytes changed: " + name)
        all_files[name] = raw
    manifest_raw = all_files[MANIFEST_PATH.as_posix()]
    require(original.digest(manifest_raw) == manifest_sha256, "Immutable G3 export manifest changed")
    require(all_files[SIDECAR_PATH.as_posix()].decode().strip() == manifest_sha256 + "  PUBLIC_EXPORT_MANIFEST.json",
            "Inherited export sidecar differs")
    manifest = json.loads(manifest_raw)
    records = {r["destination"]: r for r in manifest["files"]}
    require(len(records) == len(manifest["files"]), "Duplicate inherited export record")
    project = {name: raw for name, raw in all_files.items() if name not in {"README.md", repository.MANIFEST}}
    require(set(project) == set(records) | {MANIFEST_PATH.as_posix(), SIDECAR_PATH.as_posix()}, "Unlisted inherited public file")
    for name, record in records.items():
        require(name.startswith(repository.PROJECT_PREFIXES) and original.digest(project[name]) == record["export_sha256"],
                "Inherited manifest file mismatch")
    identity = {"proposal_sha256": proposal_sha256, "export_manifest_sha256": manifest_sha256,
                "original_overlay_files": len(all_files), "source_delivery": PREVIOUS,
                "original_existing_edits_sha256": {r["path"]: r["after_sha256"] for r in edits}}
    return project, records, identity


def redact_text(root, name, raw, mapping):
    output, changes = original.public_text(root, name, raw, mapping)
    if name == "tests/test_subscription_recovery_client.py":
        # Public-only derivative: these process-fake tests must not require the
        # author's deliberately unpublished CLI model catalog. Production hash
        # verification and every original frozen source/test remain unchanged.
        text = output.decode("utf-8")
        replacements = (
            ("import json\n", "import hashlib\nimport json\n"),
            ("import tau_feedback.subscription_recovery_client as transport\n",
             "import tau_feedback.subscription_recovery_client as transport\n"
             "import tau_feedback.subscription_client as catalog_contract\n"),
            ("        self.root = Path(self.temp.name)\n",
             "        self.root = Path(self.temp.name)\n"
             "        # Synthetic config fixture only; no author catalog or model is used.\n"
             "        self.catalog = self.root / 'synthetic-catalog.json'\n"
             "        self.catalog.write_bytes(b'{\"models\":[]}')\n"),
            ("        self.patches = [\n",
             "        self.patches = [\n"
             "            patch.object(catalog_contract, 'MODEL_CATALOG_PATH', self.catalog),\n"
             "            patch.object(catalog_contract, 'MODEL_CATALOG_SHA256', hashlib.sha256(self.catalog.read_bytes()).hexdigest()),\n"),
        )
        for old, new in replacements:
            require(text.count(old) == 1, "Public fake-test fixture anchor changed")
            text = text.replace(old, new, 1)
        output = text.encode("utf-8")
        changes.append("public fake-test fixture: synthetic temporary empty model catalog with matching SHA256; production/frozen source unchanged")
    if not name.endswith(".py"):
        text = output.decode("utf-8")
        root_text, home = str(root), str(Path.home())
        prefixes = {root_text, root_text.replace("\\", "/")}
        if root_text.startswith(home):
            prefixes.update({"LOCAL_HOME" + root_text[len(home):],
                             "LOCAL_HOME" + root_text[len(home):].replace("\\", "/")})
        forms = {value for prefix in prefixes for value in (prefix, json.dumps(prefix, ensure_ascii=False)[1:-1])}
        for value in sorted(forms, key=len, reverse=True):
            if value in text:
                text = text.replace(value, "LOCAL_RESEARCH")
                changes.append("full local research-root prefix -> LOCAL_RESEARCH")
        output = text.encode("utf-8")
    if name in RESULTS:
        value = json.loads(output)
        value["public_derivation"] = {"original_sha256": original.digest(raw),
            "local_paths_redacted": True, "original_reference_hashes_retained": True,
            "raw_subscription_records_published": False,
            "note": "Derived report: original references identify unpublished bytes; they do not authenticate this transformed copy."}
        output = encoded(value)
        changes.append("add explicit derived-report provenance; original data/labels/counts retained")
    scan_public(mapping[name].as_posix(), output)
    if name in FROZEN_REVIEW_BYTES:
        require(output == raw, "Anonymous/synthetic evidence bytes need separate reviewed transformation: " + name)
    return output, changes


def validate_terminal_sources(raw_sources):
    text_review = raw_sources["research/FINAL_TEXT_REVIEW.md"]
    require(original.digest(text_review) == FINAL_TEXT_REVIEW_SHA256, "Final text review seal differs")
    reviewed = dict(re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", text_review.decode("utf-8")))
    require(set(reviewed) == set(CORE_UPDATES) | set(REPORTS), "Final review does not bind all six delivery texts")
    for name, expected in reviewed.items():
        require(original.digest(raw_sources[name]) == expected, "Final reviewed document changed: " + name)
    g4, c1 = (json.loads(raw_sources[n]) for n in ("results/G4_STOP_REPORT.json", "results/C1_SYNTHETIC_REPORT.json"))
    require(g4["stop"]["status"] == "stopped" and g4["stop"]["phase"] == "parents", "Require terminal G4 parent-stage STOP report")
    require(g4["optimization_assignment"]["candidate_proposals_run"] == 0 and g4["test"]["actual_episodes"] == 0,
            "This final evidence interpretation does not support later G4 execution")
    require(c1["synthetic_only"] is True and c1["planned_cards"] == c1["complete_cards"] == 12
            and c1["native_reviewer_calls"] == c1["environment_episodes"] == 0, "Require completed, explicitly synthetic C1")
    for report, field, script in ((g4, "report_implementation_sha256", "scripts/report_g4_stopped.py"),
                                 (c1, "implementation_sha256", "scripts/report_c1_synthetic.py")):
        require(report[field] == original.digest(raw_sources[script]), "Report and post-registration implementation differ")
    seal_name = "research/g4-review/cohort/PACKET_SEAL.json"
    seal = json.loads(raw_sources[seal_name])
    require(seal["manifest_sha256"] == g4["manifest_sha256"], "G4 review packet belongs to another registration")
    for name, digest in seal["packet_files_sha256"].items():
        path = "research/g4-review/cohort/packets/" + name
        require(path in raw_sources and original.digest(raw_sources[path]) == digest, "G4 anonymous packet changed")
    for role in ("RED", "RELATED"):
        labels = json.loads(raw_sources[f"research/G4_COHORT_{role}_BLIND_LABELS.json"])
        require(labels["packet_seal_sha256"] == original.digest(raw_sources[seal_name]), "G4 first labels bind another packet seal")
        require({r["review_id"] for r in labels["items"]} == set(seal["review_ids"]), "Incomplete anonymous first labels")
    mapping = json.loads(raw_sources["research/C1_BLIND_MAP.json"])
    require(mapping["source_sha256"] == original.digest(raw_sources["experiments/C1_CONTROLLED_CASES_DRAFT.json"])
            and mapping["packet_sha256"] == original.digest(raw_sources["research/C1_BLIND_PACKET.json"]), "C1 synthetic packet mapping differs")
    labels = json.loads(raw_sources["research/C1_RED_BLIND_LABELS.json"])
    require(labels["packet_sha256"] == mapping["packet_sha256"], "C1 first labels bind another packet")
    return {"G3": "stopped", "G4": "stopped_in_shared_parents_before_candidates", "C1": "12_fixed_synthetic_cards_complete"}


def build_plan(root=ROOT):
    root = Path(root).resolve()
    destination = root / DESTINATION / "repository-overlay"
    require(not destination.exists(), "Final overlay already exists; never overwrite or resume")
    inherited, previous_records, inherited_identity = inherit_verified(root)
    planned = {PurePosixPath(n): data for n, data in inherited.items() if n not in {str(MANIFEST_PATH), str(SIDECAR_PATH)}}
    records = {str(target): {"source_kind": "inherited_verified_public_file",
        "source": PREVIOUS + "/repository-overlay/" + str(target), "destination": str(target),
        "original_sha256": original.digest(data), "export_sha256": original.digest(data),
        "upstream_original_sha256": previous_records[str(target)]["original_sha256"],
        "upstream_source": previous_records[str(target)]["source"],
        "transformations": ["byte-for-byte inherited from locked public G3 package"],
        "upstream_transformations": previous_records[str(target)]["transformations"]}
        for target, data in planned.items()}
    mapping = {r["source"]: PurePosixPath(name) for name, r in previous_records.items() if r["source"]}
    names = [*FIXED, *RESULTS, *(f"src/tau_feedback/{n}.py" for n in MODULES),
             *(f"scripts/{n}.py" for n in SCRIPTS), *(f"tests/{n}.py" for n in TESTS)]
    require(len(names) == len(set(names)), "Duplicate final export allowlist")
    additions = {name: PACKAGE / name for name in names}
    additions.update(CORE_UPDATES); additions.update(REPORTS)
    for name, target in additions.items():
        require(target not in planned or name in CORE_UPDATES, "Unreviewed replacement of inherited public file: " + name)
    mapping.update(additions)
    raw_sources = {name: original.source_path(root, name).read_bytes() for name in additions}
    source_hashes = {name: original.digest(raw) for name, raw in raw_sources.items()}
    for name in RESULTS:
        sidecar = original.source_path(root, str(PurePosixPath(name).with_suffix(".sha256"))).read_bytes()
        require(sidecar.decode().split()[0] == source_hashes[name], "Sealed report changed: " + name)
        source_hashes[str(PurePosixPath(name).with_suffix(".sha256"))] = original.digest(sidecar)
    experiment_status = validate_terminal_sources(raw_sources)
    for name, target in additions.items():
        raw = raw_sources[name]
        output, changes = redact_text(root, name, raw, mapping)
        planned[target] = output
        records[str(target)] = {"source_kind": "updated_core_document" if name in CORE_UPDATES else "new_allowlisted_source",
            "source": name, "destination": str(target), "original_sha256": original.digest(raw),
            "export_sha256": original.digest(output), "transformations": changes}
        if name in RESULTS:
            sidecar_target = target.with_suffix(".sha256")
            data = (original.digest(output) + "  " + target.name + "\n").encode("ascii")
            planned[sidecar_target] = data
            records[str(sidecar_target)] = {"source_kind": "generated", "source": None, "destination": str(sidecar_target),
                "original_sha256": None, "export_sha256": original.digest(data),
                "transformations": ["hash the public derived report, not the unpublished original"]}
    appendices = {
        PACKAGE / "README.md": """

## 最终阶段：G4 停止与 C1 合成校准

G4 在共享父代阶段按登记规则 STOP，候选比较、最终选择与留出没有运行；H1/H2 未被该阶段识别。完整计划分母、已运行/未运行、两份 AI 复核的分歧与成本见 [G4 停止报告](results/G4_STOP_REPORT.json) 和 [机会报告](results/G4_OPPORTUNITY_REPORT.json)。[匿名对话包](research/g4-review/cohort/packets/REVIEW_INSTRUCTIONS.txt)及两份首轮标签可供独立语义复核；包里的参考任务信息不是 actor 当时已知事实。

[C1 结果](results/C1_SYNTHETIC_REPORT.json)是 12 张固定合成卡的准入组件校准。合成正例保留不等于天然反馈保留率，更不等于 Agent/GEPA 性能收益。合成源、匿名包和独立参照标签均显式标记来源。

本副本新增恢复事件、配对测量、固定验收及假工件测试。旧 G3 包的 321 tests 结果只属于旧包，不能作为本副本已通过的复现记录；新副本的隔离复现须另行运行和保存。发布前的 AST/JSON/链接检查也不等于测试套件或全仓 CI。
""",
        PACKAGE / "PUBLIC_REPRODUCTION_BOUNDARY.md": """

## G4/C1 最终公开范围

- G3 旧公开副本按原字节继承，只有四份核心交付文档及本次导航/边界/导出清单更新，追加 G4/C1 白名单材料。旧本地交付和 ALE 内容未改。
- G4 对话包、COMMON、两份匿名首轮标签和 C1 固定合成源/匿名材料可直接阅读。G4 的 HOST_MAPPING、原始 subscription request/events/stderr/final、完整模型目录、认证及 source ZIP 不发布；报告中的历史源 hash 不能独立重建这些未公开材料。
- 结果 JSON 是脱敏派生副本，PUBLIC_EXPORT_MANIFEST 记录原 hash 和公开 hash；public_derivation 明示其边界。派生报告旁的 .sha256 只认证公开副本。原 packet/首轮标签字节保持不变，不为脱敏重写审查标签。
- G4/C1 历史 reporter、注册器和 finalizer 作为实现证据保留，依赖未公开的原始冻结目录时不能直接重建作者实验；本次新增 finalizer 只接受完整120条cohort，G4的STOP前缀不可进入选择。离线下载、受控核验和自包含假工件测试仍按公开 README 准备依赖后复现。
- 登记后的 packet/mapping/reporter/finalizer 实现 hash 单列，未冒充实验前冻结。云端权重与模型随机性未固定，CLI登录需复现者自己的账号，绝不复制作者凭据。未为运行历史脚本放宽二进制 hash、精确 warning 或事件验证。
- v2 公开副本仅为 recovered-client 假进程测试补充临时合成空模型目录及对应 SHA256 夹具，避免读取故意未公开的作者目录；原冻结测试、生产验证和研究结果不变。它检验客户端控制流，不证明合成目录可运行真实模型。修补的原/公开 hash 在导出清单中逐项记录；前两次隔离失败记录另行保留。
""",
        CORE / "README.md": "\n## 最新终局材料\n\n- [G4 停止与未识别假设](G4_STOP_RESULT.md)\n- [C1 固定合成准入校准](C1_SYNTHETIC_RESULT.md)\n\nG4 STOP 与 C1 组件结果均不能表述为已证实的策略泛化收益。\n",
        PACKAGE / ".gitignore": "\nresults/g4/\nresults/c1/\ndelivery-final-2026-09-17/\ndelivery-final-2026-09-17-v2/\n",
    }
    for target, appendix in appendices.items():
        previous = planned[target]
        output = previous + appendix.encode("utf-8")
        planned[target] = output
        records[str(target)].update(source_kind="inherited_public_document_with_final_appendix", export_sha256=original.digest(output))
        records[str(target)]["transformations"].append("append final G4 STOP/C1 evidence and reproduction boundary")
    manifest = {"schema_version": 3, "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "derived_local_overlay_not_published", "experiment_status": experiment_status,
        "previous_public_package": inherited_identity, "previous_delivery_unchanged": True,
        "original_experiments_unchanged": True, "source_paths_are_relative": True,
        "updated_core_documents": list(CORE_UPDATES), "files": [records[n] for n in sorted(records)],
        "post_registration_reporters": {name: source_hashes[name] for name in POST_REPORTERS},
        "export_sources_sha256": {name: original.digest(original.source_path(root, name).read_bytes()) for name in
            ("scripts/export_final_research_package.py", "scripts/export_public_package.py", "scripts/prepare_repository_change.py")},
        "excluded_categories": [*original.EXCLUDED, "G4 HOST_MAPPING and mapped full-path execution inventories"],
        "verification_status": {"new_package_isolated_reproduction": "not_run_by_exporter",
            "old_321_tests": "belongs_only_to_previous_G3_public_copy_not_this_extended_package"},
        "limitations": "Explicit allowlist and bounded checks; original subscription logs/catalog/cloud weights are unavailable for independent exact historical reproduction.",
        "manifest_self_hash": "Manifest and sidecar excluded from files; sidecar authenticates manifest bytes."}
    content = encoded(manifest)
    planned[MANIFEST_PATH], planned[SIDECAR_PATH] = content, (original.digest(content) + "  PUBLIC_EXPORT_MANIFEST.json\n").encode("ascii")
    for name, raw in planned.items():
        scan_public(str(name), raw)
    entries, base_raw, _ = repository.read_base(root / "delivery/base")
    modified, repo_manifest = repository.additive_files(base_raw)
    validator = runpy.run_path(str(root / "delivery/base/validate_repository.py"), run_name="pinned_validator_definitions")
    validation = repository.validate_proposal({**{str(n): b for n, b in planned.items()}, **modified}, entries, repo_manifest, validator)
    require(not (set(map(str, planned)) & set(entries)), "New project would overwrite existing remote content")
    require(inherit_verified(root)[0] == inherited, "Inherited public package changed during planning")
    for name, digest in source_hashes.items():
        require(original.digest(original.source_path(root, name).read_bytes()) == digest, "Allowlisted source changed during planning")
    return planned, {"status": "validated_local_plan_not_written", "destination": DESTINATION + "/repository-overlay",
        "files": len(planned), "bytes": sum(map(len, planned.values())), "manifest_sha256": original.digest(content),
        "validation": validation, "published": False, "remote_writes": 0, "model_calls": 0}


def write_overlay(root, planned):
    destination = Path(root).resolve() / DESTINATION / "repository-overlay"
    require(not destination.exists(), "Final overlay exists; no overwrite or resume")
    destination.mkdir(parents=True, exist_ok=False)
    for target, raw in planned.items():
        name = repository.relative_name(str(target))
        scan_public(str(name), raw)
        path = destination / str(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
        require(path.read_bytes() == raw, "Output byte readback mismatch")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Create the new overlay after final core documents are ready")
    args = parser.parse_args()
    planned, result = build_plan(ROOT)
    if args.write:
        write_overlay(ROOT, planned)
        result["status"] = "local_overlay_created_not_published"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
