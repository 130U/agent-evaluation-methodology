#!/usr/bin/env python3
"""Validate the repository's lightweight engineering and content contract."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "repository" / "repository-manifest.json"

ALLOWED_TOP_LEVEL = {
    ".gitattributes",
    ".gitignore",
    ".github",
    "README.md",
    "core",
    "docs",
    "projects",
    "scripts",
    "supporting-evidence",
}

FORBIDDEN_PARTS = {"__pycache__", "node_modules"}
FORBIDDEN_TOP_LEVEL_PREFIXES = ("docx_render", "pdf_render", ".tmp_")
BINARY_SUFFIXES = {
    ".docx",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pptx",
    ".tar",
    ".webp",
    ".xlsx",
    ".zip",
}
TEXT_SUFFIXES = {
    ".csv",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    ".json",
    ".json3",
    ".md",
    ".ps1",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = {
    "private key header": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def load_manifest(errors: list[str]) -> dict:
    display_path = MANIFEST_PATH.relative_to(ROOT).as_posix()
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"Missing {display_path}")
        return {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"Invalid {display_path}: {exc}")
        return {}

    if data.get("schema_version") != 1:
        errors.append(f"{display_path} must declare schema_version 1")
    if data.get("repository") != "130U/agent-evaluation-methodology":
        errors.append(f"{display_path} has an unexpected repository identity")
    return data


def repository_files() -> list[Path]:
    """Return tracked files so generated local artifacts do not affect validation."""
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return sorted(
            path
            for path in ROOT.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(ROOT).parts
        )

    return sorted(
        ROOT / item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    )


def is_text_file(path: Path) -> bool:
    return path.name in {".editorconfig", ".gitattributes", ".gitignore"} or path.suffix.lower() in TEXT_SUFFIXES


def check_structure(manifest: dict, files: list[Path], errors: list[str]) -> None:
    actual_top_level = {path.name for path in ROOT.iterdir() if path.name != ".git"}
    unexpected = sorted(actual_top_level - ALLOWED_TOP_LEVEL)
    if unexpected:
        errors.append("Unexpected top-level entries: " + ", ".join(unexpected))

    for name in actual_top_level:
        if name == "tmp" or name.startswith(FORBIDDEN_TOP_LEVEL_PREFIXES):
            errors.append(f"Local-only top-level path must not be committed: {name}")

    for required in manifest.get("required_paths", []):
        if not (ROOT / required).exists():
            errors.append(f"Missing required path from manifest: {required}")

    for path in files:
        relative = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS for part in relative.parts):
            errors.append(f"Forbidden generated path: {relative.as_posix()}")
        if any(" " in part for part in relative.parts):
            errors.append(f"Repository paths must not contain spaces: {relative.as_posix()}")
        if path.stat().st_size == 0:
            errors.append(f"Zero-byte file: {relative.as_posix()}")


def check_sizes(manifest: dict, files: list[Path], errors: list[str], warnings: list[str]) -> None:
    validation = manifest.get("validation", {})
    maximum = int(validation.get("maximum_file_size_mib", 50)) * 1024 * 1024
    warning = int(validation.get("large_file_warning_mib", 10)) * 1024 * 1024

    for path in files:
        size = path.stat().st_size
        relative = path.relative_to(ROOT).as_posix()
        if size > maximum:
            errors.append(f"File exceeds {maximum // (1024 * 1024)} MiB limit: {relative}")
        elif size > warning:
            warnings.append(f"Large file requires deliberate review: {relative} ({size / (1024 * 1024):.1f} MiB)")


def read_text(path: Path, errors: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"Expected UTF-8 text: {path.relative_to(ROOT).as_posix()}")
        return None


def check_secrets(files: list[Path], errors: list[str]) -> None:
    for path in files:
        if path.suffix.lower() in BINARY_SUFFIXES or not is_text_file(path):
            continue
        text = read_text(path, errors)
        if text is None:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"Possible {label} in {path.relative_to(ROOT).as_posix()}")


def normalize_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if " " in target and not target.startswith(("http://", "https://")):
        target = target.split(maxsplit=1)[0]
    return unquote(target.split("#", 1)[0].split("?", 1)[0])


def check_markdown_links(files: list[Path], errors: list[str]) -> None:
    ignored_schemes = ("http://", "https://", "mailto:", "tel:", "data:")
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        relative = path.relative_to(ROOT)
        managed = (
            len(relative.parts) == 1
            or relative.parts[0] in {".github", "docs", "projects"}
            or relative.as_posix()
            in {
                "supporting-evidence/README.md",
                "supporting-evidence/UPLOAD_MANIFEST.md",
            }
        )
        if not managed:
            continue
        text = read_text(path, errors)
        if text is None:
            continue
        for match in MARKDOWN_LINK.finditer(text):
            raw_target = match.group(1)
            target = normalize_link_target(raw_target)
            if not target or target.startswith(ignored_schemes):
                continue
            candidate = ROOT / target.lstrip("/") if target.startswith("/") else path.parent / target
            if not candidate.exists():
                line = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"Broken local link in {path.relative_to(ROOT).as_posix()}:{line}: {raw_target}"
                )


def check_evidence(errors: list[str]) -> None:
    evidence_path = ROOT / "docs/repository/evidence-manifest.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    for entry in evidence["files"]:
        path = ROOT / entry["path"]
        if not path.is_file():
            errors.append(f"Missing frozen evidence: {entry['path']}")
            continue
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            errors.append(f"Frozen evidence hash mismatch: {entry['path']}")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    manifest = load_manifest(errors)
    files = repository_files()

    check_structure(manifest, files, errors)
    check_sizes(manifest, files, errors, warnings)
    check_secrets(files, errors)
    check_markdown_links(files, errors)
    check_evidence(errors)

    print(f"Validated {len(files)} files.")
    for warning in sorted(set(warnings)):
        print(f"WARNING: {warning}")
    for error in sorted(set(errors)):
        print(f"ERROR: {error}", file=sys.stderr)

    if errors:
        print(f"Repository validation failed with {len(set(errors))} error(s).", file=sys.stderr)
        return 1

    print(f"Repository validation passed with {len(set(warnings))} warning(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
