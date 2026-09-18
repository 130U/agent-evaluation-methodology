"""Validate one new final overlay against the pinned public ALE repository.

Only local proposal files and the two additive index edits are written.
No publication, Git command, model call or current-remote claim is made.
"""
import datetime as dt
import difflib
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_repository_change as base
from export_final_research_package import DESTINATION, inherit_verified


def prepare(root=ROOT):
    root = Path(root).resolve()
    delivery, snapshot = root / DESTINATION, root / "delivery/base"
    overlay = delivery / "repository-overlay"
    outputs = [overlay / "README.md", overlay / base.MANIFEST,
               delivery / "PROPOSED_CHANGE.json", delivery / "REPOSITORY_CHANGE.md"]
    base.require(all(not p.exists() for p in outputs), "Final proposal already exists; no overwrite")
    _, _, inherited = inherit_verified(root)
    entries, raw, tree_bytes = base.read_base(snapshot)
    new_files = base.read_overlay(overlay)
    base.require(not set(new_files).intersection(entries), "Unexpected overwrite of existing repository content")
    modified, manifest = base.additive_files(raw)
    base.require({name: base.sha256(data) for name, data in modified.items()} == inherited["original_existing_edits_sha256"],
                 "ALE/index edits differ from the original bounded additive proposal")
    all_files = {**new_files, **modified}
    validator = runpy.run_path(str(snapshot / "validate_repository.py"), run_name="pinned_validator_definitions")
    validation = base.validate_proposal(all_files, entries, manifest, validator)
    edits = [{"path": name, "before_git_blob_sha1": base.git_blob(raw[name]),
        "before_sha256": base.sha256(raw[name]), "after_git_blob_sha1": base.git_blob(data),
        "after_sha256": base.sha256(data), "before_bytes": len(raw[name]), "after_bytes": len(data),
        "unified_diff": "".join(difflib.unified_diff(raw[name].decode().splitlines(keepends=True),
            data.decode().splitlines(keepends=True), fromfile="a/" + name, tofile="b/" + name))}
        for name, data in modified.items()]
    inventory = [{"path": name, "sha256": base.sha256(data), "git_blob_sha1": base.git_blob(data), "bytes": len(data)}
                 for name, data in sorted(new_files.items())]
    change = {"schema_version": 3, "prepared_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "local_proposal_not_published", "repository": base.REPOSITORY,
        "base_branch": "main", "base_commit": base.BASE_COMMIT, "base_tree": base.BASE_TREE,
        "intended_branch": "codex/tau-feedback-evidence", "intended_pull_request_state": "draft",
        "previous_public_package": inherited,
        "base_snapshot_sha256": {"tree": base.sha256(tree_bytes), **{name: base.sha256(data) for name, data in raw.items()}},
        "preparation_sources_sha256": {name: base.sha256((root / name).read_bytes()) for name in (
            "scripts/prepare_final_repository_change.py", "scripts/prepare_repository_change.py", "scripts/export_final_research_package.py")},
        "existing_file_modifications": edits, "new_files": inventory,
        "totals": {"new_files": len(new_files), "modified_existing_files": 2, "overlay_files": len(all_files),
            "new_file_bytes": sum(map(len, new_files.values())), "overlay_bytes": sum(map(len, all_files.values()))},
        "ale_preservation": {"all_prior_manifest_values_unchanged": True, "prior_required_paths_retained_in_order": True,
            "prior_source_pins_unchanged": True, "readme_change_exactly_one_table_row": True,
            "no_other_existing_remote_path_modified": True},
        "previous_deliveries_unchanged": True, "validation": validation,
        "new_package_isolated_reproduction": "not_performed_by_proposal_tool",
        "prior_approval_not_reused_for_expanded_files": True,
        "published": False, "remote_writes": 0,
        "publication_limit": "Fixed public snapshot only; current remote state and publication authorization require a separate check. No full-repository CI claim."}
    text = "# 最终研究公开包提案（仅本地、尚未发布）\n\n"
    text += f"基点 `{base.REPOSITORY}` / `{base.BASE_COMMIT}`；拟用分支 `codex/tau-feedback-evidence` 和草稿 PR。此工具没有创建它们。新增 {len(new_files)} 个文件，既有 README/manifest 各做一项增量更新，ALE 原值保留。旧 G3 公开包和此前交付不变。\n\n"
    text += "本次文件集合扩大，不能把旧141文件提案或旧包321项测试视为新提案授权/复现。本机完整套件成绩也不能替代本副本的隔离复现。G4按登记STOP、C1为合成组件校准，打包不能补足未识别的性能假设。\n\n## 两个既有文件差异\n\n"
    for row in edits:
        text += "### " + row["path"] + "\n\n```diff\n" + row["unified_diff"] + "```\n\n"
    text += "## 验证范围\n\n逐文件清单、SHA256/Git blob、字节数见 PROPOSED_CHANGE.json。检查包括固定基点字节、增量路径、大小、秘密模式、Python AST/JSON及新增 Markdown 本地链接；引用公开 validator 的固定常量与纯函数，不执行其 Git 枚举/main。不是全仓CI，不检查外部URL或锚点。未公开CLI日志、完整模型目录及源码ZIP的历史重建边界见公开包说明。\n"
    base.require(base.read_overlay(overlay) == new_files, "Final export changed during proposal preparation")
    base.require(base.read_base(snapshot) == (entries, raw, tree_bytes), "Pinned repository snapshot changed")
    proposed = {**{overlay / name: data for name, data in modified.items()},
        delivery / "PROPOSED_CHANGE.json": (json.dumps(change, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(),
        delivery / "REPOSITORY_CHANGE.md": text.encode()}
    for path, data in proposed.items():
        base.require(path.resolve().is_relative_to(delivery.resolve()), "Output escapes final delivery")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    base.require(all(p.read_bytes() == data for p, data in proposed.items()), "Final proposal readback mismatch")
    return {"status": change["status"], "totals": change["totals"], "validation": validation, "published": False}


if __name__ == "__main__":
    print(json.dumps(prepare(), ensure_ascii=False, indent=2))
