"""Download pinned public research artifacts; verify Git blob IDs before use."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    args = p.parse_args()
    plan = json.loads((ROOT / "experiments/archive_sources.json").read_text(encoding="utf-8"))
    out = ROOT / "data/archives"
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for entry in plan["files"][:args.limit]:
        dest = out / Path(entry["path"]).name
        url = f"https://raw.githubusercontent.com/{plan['repository']}/{plan['commit']}/{entry['path']}"
        if dest.exists():
            data = dest.read_bytes()
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "tau-feedback-research/0.1"})
            with urllib.request.urlopen(req, timeout=45) as response:
                data = response.read()
        actual = git_blob_sha(data)
        if actual != entry["sha"] or len(data) != entry["size"]:
            raise ValueError(f"Pinned source mismatch: {entry['path']}")
        json.loads(data)
        if not dest.exists():
            dest.write_bytes(data)
        record = {"path": str(dest.relative_to(ROOT)).replace('\\', '/'), "url": url,
                  "bytes": len(data), "git_blob_sha": actual,
                  "sha256": hashlib.sha256(data).hexdigest(),
                  "verified_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        records.append(record)
        print(json.dumps(record), flush=True)
    (out / "DOWNLOAD_MANIFEST.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
