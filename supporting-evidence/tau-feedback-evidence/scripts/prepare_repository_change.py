"""Prepare an additive, unpublished repository change from a public overlay.

No Git, network, model calls, source edits or full-repository CI execution.
Run after export_public_package.py. Existing outputs are never overwritten.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import datetime as dt
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import runpy

ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "2821ff225aeefa7ac68ba4210058802510e7af14"
BASE_TREE = "8095ec454723ebe78b25c74ba0c87da9e1f038bd"
REPOSITORY = "130U/agent-evaluation-methodology"
MANIFEST = "docs/repository/repository-manifest.json"
PACKAGE = "supporting-evidence/tau-feedback-evidence"
PROJECT_PREFIXES = (
    "projects/tau-feedback-evidence/",
    "core/03-tau-feedback-evidence/",
    PACKAGE + "/",
)
NEW_REQUIRED = [
    "projects/tau-feedback-evidence/README.md",
    "core/03-tau-feedback-evidence",
    PACKAGE,
    PACKAGE + "/PUBLIC_EXPORT_MANIFEST.json",
]
NEW_PINS = {
    "tau2_bench": {"repository": "sierra-research/tau2-bench", "commit": "2174a603f6d014ef94473ffa95957f6ce27100db"},
    "gepa": {"repository": "gepa-ai/gepa", "commit": "15ee314f9c7d34ec153b809d401f42f55c4dcd76"},
}
BASE_FILES = {
    "README.md": ("README.md", "6c97fadd73a18ebb1de2888b27c87a40955a876b"),
    MANIFEST: ("repository-manifest.json", "318570883b3d77cbae449959eb8f8f039237af15"),
    "scripts/validate_repository.py": ("validate_repository.py", "08673b2e578fa1e15a935253550de715cbc3804d"),
}
PROJECT_ROW = "| [τ-bench 反馈证据研究](projects/tau-feedback-evidence/README.md) | 以 τ2-bench 与固定 GEPA 为基础，研究评分完整性、诊断反馈准入及动作来源边界，提供受控核验、真实开发轨迹审计与复现限制。 |\n"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def relative_name(name):
    require(isinstance(name, str) and bool(name) and "\\" not in name, "Invalid repository path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and str(path) == name and all(p not in (".", "..") for p in path.parts), "Unsafe repository path: " + name)
    require(not any(c.isspace() for c in name) and ":" not in name, "Repository path contains whitespace or a drive: " + name)
    return path


def regular_path(path, boundary):
    require(path.resolve().is_relative_to(boundary.resolve()), "Path escapes its input/output boundary")
    for current in (path, *path.parents):
        require(not current.is_symlink() and not getattr(current, "is_junction", lambda: False)(), "Linked path is forbidden")
        if current == boundary:
            break
    require(path.is_file(), "Expected a regular input file: " + path.name)
    return path


def read_base(base):
    tree_bytes = regular_path(base / "tree.json", base).read_bytes()
    tree = json.loads(tree_bytes)
    require(isinstance(tree, dict) and tree.get("sha") == BASE_TREE and tree.get("truncated") is False, "Wrong or truncated fixed tree")
    require(isinstance(tree.get("tree"), list), "Expected GitHub recursive tree object")
    entries = {}
    for entry in tree["tree"]:
        name = entry["path"]
        relative_name(name)
        require(name not in entries and entry["type"] in ("tree", "blob"), "Duplicate/unsupported fixed tree entry")
        require(entry["mode"] in ("040000", "100644", "100755"), "Unsupported fixed tree mode")
        entries[name] = entry
    raw = {}
    for name, (local, expected) in BASE_FILES.items():
        require(name in entries and entries[name]["type"] == "blob" and entries[name]["sha"] == expected, "Unexpected base Git blob identity: " + name)
        data = regular_path(base / local, base).read_bytes()
        require(len(data) == entries[name]["size"] and git_blob(data) == expected, "Base bytes differ from fixed Git blob: " + name)
        raw[name] = data
    return entries, raw, tree_bytes


def read_overlay(overlay):
    require(overlay.is_dir() and not overlay.is_symlink() and not getattr(overlay, "is_junction", lambda: False)(), "Export overlay missing or linked")
    files = {}
    for path in sorted(overlay.rglob("*")):
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(), "Linked overlay entry")
        if not path.is_file():
            continue
        name = path.relative_to(overlay).as_posix()
        relative_name(name)
        require(name.startswith(PROJECT_PREFIXES), "Only the new project's exported paths are accepted: " + name)
        files[name] = regular_path(path, overlay).read_bytes()
    require(bool(files), "Empty export overlay")
    export_name = PACKAGE + "/PUBLIC_EXPORT_MANIFEST.json"
    sidecar = PACKAGE + "/PUBLIC_EXPORT_MANIFEST.sha256"
    require(export_name in files and sidecar in files, "Export manifest and sidecar are required")
    export = json.loads(files[export_name])
    require(export.get("status") == "derived_local_overlay_not_published", "Unexpected export status")
    require(files[sidecar].decode("ascii").strip() == sha256(files[export_name]) + "  PUBLIC_EXPORT_MANIFEST.json", "Export manifest sidecar mismatch")
    recorded = set()
    for item in export["files"]:
        name = item["destination"]
        require(name not in recorded and name in files, "Missing/duplicate export entry")
        require(sha256(files[name]) == item["export_sha256"], "Export file changed: " + name)
        recorded.add(name)
    require(set(files) == recorded | {export_name, sidecar}, "Unlisted export file")
    for key, lock in (("tau2_bench", "runtime_sources.json"), ("gepa", "gepa_sources.json")):
        source = json.loads(files[PACKAGE + "/experiments/" + lock])
        require(all(source.get(k) == v for k, v in NEW_PINS[key].items()), "Export source pin mismatch")
    return files


def additive_files(raw):
    readme = raw["README.md"].decode("utf-8")
    require("tau-feedback-evidence" not in readme, "Project already indexed")
    lines = readme.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == "| 项目 | 内容概括 |"]
    require(len(headers) == 1, "Expected exactly one project index table")
    end = headers[0] + 1
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    require(any("projects/agents-last-exam/README.md" in line for line in lines[headers[0]:end]), "ALE table row missing")
    lines.insert(end, PROJECT_ROW)
    new_readme = "".join(lines)
    require(new_readme.replace(PROJECT_ROW, "", 1) == readme, "README change must be exactly one appended table row")
    original = json.loads(raw[MANIFEST])
    require(original.get("schema_version") == 1 and original.get("repository") == REPOSITORY, "Unexpected repository manifest")
    updated = deepcopy(original)
    require(not (set(NEW_PINS) & set(updated["source_pins"])), "New source pin key already exists")
    require(not (set(NEW_REQUIRED) & set(updated["required_paths"])), "New required path already exists")
    updated["required_paths"].extend(NEW_REQUIRED)
    updated["source_pins"].update(deepcopy(NEW_PINS))
    restored = deepcopy(updated)
    restored["required_paths"] = restored["required_paths"][:-len(NEW_REQUIRED)]
    for key in NEW_PINS:
        del restored["source_pins"][key]
    require(restored == original, "An existing manifest/ALE value changed")
    return {"README.md": new_readme.encode("utf-8"), MANIFEST: (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}, updated


def validate_proposal(files, entries, manifest, validator):
    """Apply fixed validator constants and pure link normalization to a union view.

    Never call its ROOT-dependent main/repository_files/check_structure helpers.
    The remote tree supplies names only; old file content and CI are not retested.
    """
    available = set(entries) | set(files)
    for name in files:
        available.update(str(p) for p in PurePosixPath(name).parents if str(p) != ".")
    require({n.split("/", 1)[0] for n in available} <= validator["ALLOWED_TOP_LEVEL"], "Unexpected virtual top-level entry")
    for name in manifest["required_paths"]:
        relative_name(name)
        require(name in available, "Missing required path in proposed tree: " + name)
    maximum = int(manifest["validation"]["maximum_file_size_mib"]) * 1024 * 1024
    warning = int(manifest["validation"]["large_file_warning_mib"]) * 1024 * 1024
    warnings = []
    counts = {"files": 0, "python_ast": 0, "json": 0, "markdown_files": 0, "local_link_targets": 0, "external_links_not_fetched": 0}
    for name, data in files.items():
        path = relative_name(name)
        require(not (set(path.parts) & validator["FORBIDDEN_PARTS"]), "Forbidden generated path: " + name)
        require(len(data) > 0 and len(data) <= maximum, "Empty or oversized file: " + name)
        if len(data) > warning:
            warnings.append("Large file requires deliberate review: " + name)
        # This exporter contains UTF-8 text only; scan locks/TOML/sidecars too.
        text = data.decode("utf-8")
        for label, pattern in validator["SECRET_PATTERNS"].items():
            require(pattern.search(text) is None, "Possible " + label + " in " + name)
        counts["files"] += 1
        if path.suffix.lower() == ".py":
            ast.parse(text, filename=name)
            counts["python_ast"] += 1
        if path.suffix.lower() == ".json":
            json.loads(text)
            counts["json"] += 1
        if path.suffix.lower() != ".md":
            continue
        counts["markdown_files"] += 1
        targets = [match.group(1) for match in validator["MARKDOWN_LINK"].finditer(text)]
        targets += re.findall(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)", text, re.MULTILINE)
        for raw in targets:
            target = validator["normalize_link_target"](raw)
            if not target:
                continue
            if target.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
                counts["external_links_not_fetched"] += 1
                continue
            require(not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target) and "\\" not in target, "Unsupported/nonportable Markdown target in " + name)
            candidate = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(str(path.parent), target))
            require(candidate != ".." and not candidate.startswith("../"), "Markdown target escapes proposed tree in " + name)
            require(candidate in available or candidate == ".", "Broken local Markdown link in " + name + ": " + target)
            counts["local_link_targets"] += 1
    return {"status": "local_additive_checks_passed", "counts": counts, "warnings": warnings,
        "uses_pinned_validator": "Exact constants, regexes and pure normalize_link_target; explicit virtual-tree structure/size/secret/link checks replace ROOT-dependent helpers.",
        "additional_checks": "All exported UTF-8 files scanned, all added Markdown locations checked, plus Python AST/JSON parsing and reference-link destinations.",
        "limits": "Not full-repository CI. Existing remote contents, external URL reachability and Markdown fragments are not checked. No Git/network command executed."}


def prepare(root=ROOT):
    root = Path(root).resolve()
    delivery, overlay = root / "delivery", root / "delivery/repository-overlay"
    outputs = [overlay / "README.md", overlay / MANIFEST, delivery / "PROPOSED_CHANGE.json", delivery / "REPOSITORY_CHANGE.md"]
    require(all(not p.exists() for p in outputs), "Proposed output already exists; no overwrite/resume")
    entries, raw, tree_bytes = read_base(delivery / "base")
    new_files = read_overlay(overlay)
    require(not (set(new_files) & set(entries)), "Export would overwrite an existing repository file")
    modified, manifest = additive_files(raw)
    all_files = {**new_files, **modified}
    # Fixed public script has no top-level execution beyond definitions/imports.
    # run_path does NOT invoke its __main__, subprocess, or Git helper.
    validator = runpy.run_path(str(delivery / "base/validate_repository.py"), run_name="pinned_validator_definitions")
    validation = validate_proposal(all_files, entries, manifest, validator)
    edits = []
    for name, data in modified.items():
        edits.append({"path": name, "before_git_blob_sha1": git_blob(raw[name]), "before_sha256": sha256(raw[name]),
            "after_git_blob_sha1": git_blob(data), "after_sha256": sha256(data), "before_bytes": len(raw[name]), "after_bytes": len(data),
            "unified_diff": "".join(difflib.unified_diff(raw[name].decode("utf-8").splitlines(keepends=True), data.decode("utf-8").splitlines(keepends=True), fromfile="a/" + name, tofile="b/" + name))})
    inventory = [{"path": name, "sha256": sha256(data), "git_blob_sha1": git_blob(data), "bytes": len(data)} for name, data in sorted(new_files.items())]
    change = {"schema_version": 1, "prepared_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "local_proposal_not_published", "repository": REPOSITORY, "base_branch": "main", "base_commit": BASE_COMMIT, "base_tree": BASE_TREE,
        "base_snapshot_sha256": {"tree": sha256(tree_bytes), **{name: sha256(data) for name, data in raw.items()}},
        "preparation_script_sha256": sha256(Path(__file__).read_bytes()),
        "existing_file_modifications": edits, "new_files": inventory,
        "totals": {"new_files": len(new_files), "modified_existing_files": len(modified), "overlay_files": len(all_files),
                   "new_file_bytes": sum(map(len, new_files.values())), "overlay_bytes": sum(map(len, all_files.values()))},
        "ale_preservation": {"all_prior_manifest_values_unchanged": True, "prior_required_paths_retained_in_order": True,
            "prior_source_pins_unchanged": True, "readme_change_exactly_one_table_row": True, "no_other_existing_remote_path_modified": True},
        "validation": validation, "published": False, "remote_writes": 0,
        "publication_limit": "Fixed public snapshot only. Current remote main, permissions and full CI must be checked separately before any future publication."}
    markdown = "# 仓库变更提案（尚未发布）\n\n"
    markdown += f"基点：`{REPOSITORY}` / `main` / `{BASE_COMMIT}`，tree `{BASE_TREE}`。本次没有 Git 或网络操作，没有远端写入。\n\n"
    markdown += f"新增 {len(new_files)} 个文件，修改既有文件 2 个；overlay 共 {len(all_files)} 个文件、{sum(map(len, all_files.values())):,} 字节。原 ALE 的 source pins、required paths 和其余 manifest 值原样保留，根 README 仅追加一行 τ 项目索引。\n\n"
    markdown += "## 既有文件差异\n\n"
    for edit in edits:
        markdown += "### " + edit["path"] + "\n\n```diff\n" + edit["unified_diff"] + "```\n\n"
    markdown += "## 新文件清单\n\n| 路径 | 字节 | SHA256 |\n| --- | ---: | --- |\n"
    markdown += "".join(f"| {item['path']} | {item['bytes']} | `{item['sha256']}` |\n" for item in inventory)
    markdown += "\n## 检查范围\n\n已对本提案全部文件执行大小、非空、路径无空白、原仓库秘密模式检查，并解析 Python AST/JSON。全部新增 Markdown 本地链接在 overlay 与固定远端 tree 的联合路径视图中可解析；外部 URL 未联网，片段锚点不在原路径级检查范围。\n\n"
    markdown += "复用哈希固定的公开 validator 常量和纯链接归一化函数；其 ROOT 相关逻辑由明确的联合路径检查替代，没有调用全仓 main、Git 文件枚举或 CI。**这不表示全仓 CI 已通过。** 原仓库内容仅有固定树和两个修改文件的公开快照，未重测所有旧文件。\n\n"
    markdown += "详细计数、警告、每文件 Git blob/SHA256 见 `PROPOSED_CHANGE.json`。当前远端状态及未来发布授权须另行核验；本脚本没有发布功能。\n"
    if validation["warnings"]:
        markdown += "\n### 警告\n\n" + "".join("- " + warning + "\n" for warning in validation["warnings"])
    # Fail before writing if any public source changed while preparing the proposal.
    require(read_overlay(overlay) == new_files, "Export changed during preparation")
    require(read_base(delivery / "base") == (entries, raw, tree_bytes), "Base snapshot changed during preparation")
    proposed = {**{overlay / name: data for name, data in modified.items()},
        delivery / "PROPOSED_CHANGE.json": (json.dumps(change, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8"),
        delivery / "REPOSITORY_CHANGE.md": markdown.encode("utf-8")}
    for path, data in proposed.items():
        require(path.resolve().is_relative_to(delivery.resolve()), "Output escaped delivery")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    require(all(path.read_bytes() == data for path, data in proposed.items()), "Output readback mismatch")
    return {"status": change["status"], "base_commit": BASE_COMMIT, "totals": change["totals"], "validation": validation, "published": False}


if __name__ == "__main__":
    print(json.dumps(prepare(), ensure_ascii=False, indent=2))
