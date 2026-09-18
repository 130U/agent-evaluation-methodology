"""Fetch the locked GEPA ZIP and verify every source file before installation.

Standard library only. No Python source is imported or executed from the ZIP.
Existing mismatched files are errors, never repaired or overwritten. Use
--verify-only for a read-only, offline check of an existing vendor checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "15ee314f9c7d34ec153b809d401f42f55c4dcd76"
REPOSITORY = "gepa-ai/gepa"
URL = f"https://codeload.github.com/{REPOSITORY}/zip/{COMMIT}"
SOURCE_COUNT = 587
CHUNK_SIZE = 1024 * 1024


def relative_parts(value):
    """Reject traversal and Windows aliases before joining any untrusted path."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"Invalid relative source path: {value!r}")
    path = PurePosixPath(value)
    parts = value.split("/")
    if path.is_absolute() or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Unsafe source path: {value!r}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    for part in parts:
        if (any(ord(char) < 32 or char in ':<>"|?*' for char in part)
                or part.endswith((".", " ")) or part.split(".")[0].upper() in reserved):
            raise ValueError(f"Ambiguous Windows source path: {value!r}")
    return tuple(parts)


def safe_path(root, *parts):
    """Keep writes under root and refuse symlinks/junctions along the path."""
    root = Path(root).resolve()
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {target}")
    current = root
    for part in target.relative_to(root).parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError(f"Linked source path is not allowed: {current}")
    return target


def read_lock(root):
    path = safe_path(root, "experiments", "gepa_sources.json")
    raw = path.read_bytes()
    lock = json.loads(raw.decode("utf-8"))
    if (lock.get("repository") != REPOSITORY or lock.get("commit") != COMMIT
            or lock.get("url") != URL or len(lock.get("files", [])) != SOURCE_COUNT):
        raise ValueError("GEPA source lock does not match the pinned repository/commit/file count")
    if (type(lock.get("zip_bytes")) is not int or lock["zip_bytes"] <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", lock.get("zip_sha256", ""))):
        raise ValueError("Invalid ZIP size or hash in source lock")
    names = set()
    for entry in lock["files"]:
        relative_parts(entry["path"])
        key = entry["path"].casefold()
        if key in names:
            raise ValueError(f"Duplicate/case-colliding locked path: {entry['path']}")
        names.add(key)
        if (type(entry.get("bytes")) is not int or entry["bytes"] < 0
                or not re.fullmatch(r"[0-9a-f]{40}", entry.get("git_blob_sha", ""))
                or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", ""))):
            raise ValueError(f"Invalid locked file identity: {entry['path']}")
    return lock, raw


def verify_stream(stream, *, expected_bytes, sha256, git_blob_sha=None, label="file"):
    content_hash = hashlib.sha256()
    blob_hash = hashlib.sha1(b"blob " + str(expected_bytes).encode("ascii") + b"\0")
    received = 0
    while chunk := stream.read(CHUNK_SIZE):
        received += len(chunk)
        if received > expected_bytes:
            raise ValueError(f"File exceeds locked size: {label}")
        content_hash.update(chunk)
        blob_hash.update(chunk)
    if (received != expected_bytes or content_hash.hexdigest() != sha256
            or (git_blob_sha is not None and blob_hash.hexdigest() != git_blob_sha)):
        raise ValueError(f"Source size/hash mismatch (existing content is never overwritten): {label}")


def verify_source(path, entry):
    if not path.is_file():
        raise ValueError(f"Expected a regular source file: {path}")
    with path.open("rb") as stream:
        verify_stream(stream, expected_bytes=entry["bytes"], sha256=entry["sha256"],
                      git_blob_sha=entry["git_blob_sha"], label=entry["path"])


def preflight_checkout(root, lock, lock_raw, *, require_complete):
    """Validate all existing targets before creating any source files."""
    missing = []
    for entry in lock["files"]:
        path = safe_path(root, "vendor", "gepa", *relative_parts(entry["path"]))
        if os.path.lexists(path):
            verify_source(path, entry)
        else:
            missing.append(entry["path"])
    copied_lock = safe_path(root, "vendor", "gepa", "SOURCE_MANIFEST.json")
    if os.path.lexists(copied_lock) and copied_lock.read_bytes() != lock_raw:
        raise ValueError("Existing vendor/gepa/SOURCE_MANIFEST.json differs from source lock")
    if require_complete and (missing or not copied_lock.exists()):
        raise ValueError(f"Incomplete GEPA checkout: {len(missing)} missing sources; run without --verify-only")
    return missing


def verify_zip(archive, lock):
    with archive.open("rb") as stream:
        verify_stream(stream, expected_bytes=lock["zip_bytes"], sha256=lock["zip_sha256"],
                      label="UPSTREAM_SOURCE.zip")
    prefix = f"gepa-{COMMIT}/"
    expected = {entry["path"]: entry for entry in lock["files"]}
    seen, members = set(), {}
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            name = info.filename
            if not name.startswith(prefix):
                raise ValueError(f"Unexpected ZIP root: {name!r}")
            relative = name[len(prefix):].rstrip("/")
            mode = info.external_attr >> 16
            if (info.flag_bits & 1 or stat.S_ISLNK(mode)
                    or stat.S_IFMT(mode) not in (0, stat.S_IFDIR, stat.S_IFREG)):
                raise ValueError(f"Encrypted or special ZIP member: {name!r}")
            if not relative:
                if name != prefix or not info.is_dir():
                    raise ValueError(f"Invalid ZIP root entry: {name!r}")
                continue
            relative_parts(relative)
            key = relative.casefold()
            if key in seen:
                raise ValueError(f"Duplicate/case-colliding ZIP member: {name!r}")
            seen.add(key)
            if info.is_dir():
                if info.file_size != 0:
                    raise ValueError(f"Nonempty ZIP directory: {name!r}")
                continue
            entry = expected.get(relative)
            if entry is None or info.file_size != entry["bytes"]:
                raise ValueError(f"Unexpected source/size in ZIP: {name!r}")
            with source.open(info) as stream:
                verify_stream(stream, expected_bytes=entry["bytes"], sha256=entry["sha256"],
                              git_blob_sha=entry["git_blob_sha"], label=relative)
            members[relative] = name
    if set(members) != set(expected):
        raise ValueError("ZIP does not contain exactly the locked GEPA source files")
    return members


def download_archive(root, archive, lock):
    archive.parent.mkdir(parents=True, exist_ok=True)
    # The temporary download and final archive both remain inside the workspace.
    with tempfile.TemporaryFile(dir=archive.parent) as staging:
        request = urllib.request.Request(URL, headers={"User-Agent": "tau-feedback-research/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            received = 0
            while chunk := response.read(CHUNK_SIZE):
                received += len(chunk)
                if received > lock["zip_bytes"]:
                    raise ValueError("Download exceeds locked ZIP size")
                staging.write(chunk)
        staging.seek(0)
        verify_stream(staging, expected_bytes=lock["zip_bytes"], sha256=lock["zip_sha256"],
                      label="downloaded GEPA ZIP")
        staging.seek(0)
        safe_path(root, "vendor", "gepa", "UPSTREAM_SOURCE.zip")
        # Exclusive creation cannot replace a file created by another process.
        with archive.open("xb") as output:
            shutil.copyfileobj(staging, output, length=CHUNK_SIZE)


def bootstrap(root=ROOT, *, verify_only=False):
    root = Path(root).resolve()
    lock, lock_raw = read_lock(root)
    missing = preflight_checkout(root, lock, lock_raw, require_complete=verify_only)
    archive = safe_path(root, "vendor", "gepa", "UPSTREAM_SOURCE.zip")
    if not archive.exists():
        if verify_only:
            raise ValueError("Missing locked ZIP; --verify-only never downloads")
        download_archive(root, archive, lock)
    members = verify_zip(archive, lock)
    if not verify_only:
        # Recheck after download before creating any source files. No extractall:
        # only independently validated, explicitly locked regular files are used.
        missing = preflight_checkout(root, lock, lock_raw, require_complete=False)
        with zipfile.ZipFile(archive) as source:
            for relative in missing:
                target = safe_path(root, "vendor", "gepa", *relative_parts(relative))
                target.parent.mkdir(parents=True, exist_ok=True)
                safe_path(root, "vendor", "gepa", *relative_parts(relative))
                with source.open(members[relative]) as incoming, target.open("xb") as output:
                    shutil.copyfileobj(incoming, output, length=CHUNK_SIZE)
        copied_lock = safe_path(root, "vendor", "gepa", "SOURCE_MANIFEST.json")
        if not copied_lock.exists():
            with copied_lock.open("xb") as stream:
                stream.write(lock_raw)
        preflight_checkout(root, lock, lock_raw, require_complete=True)
    return {"status": "verified", "mode": "verify_only" if verify_only else "bootstrap",
            "commit": COMMIT, "source_files": len(lock["files"]), "created_files": 0 if verify_only else len(missing),
            "source_bytes": sum(entry["bytes"] for entry in lock["files"]),
            "zip_bytes": lock["zip_bytes"], "zip_sha256": lock["zip_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Offline, read-only verification; no download or repair")
    args = parser.parse_args()
    print(json.dumps(bootstrap(verify_only=args.verify_only), ensure_ascii=False))


if __name__ == "__main__":
    main()
