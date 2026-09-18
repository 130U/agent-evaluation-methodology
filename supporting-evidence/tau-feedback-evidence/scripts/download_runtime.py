"""Fetch byte-verified upstream source without changing upstream code."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

def main():
    plan = json.loads((ROOT / "experiments/runtime_sources.json").read_text(encoding="utf-8"))
    out = ROOT / "vendor/tau2"
    out.mkdir(parents=True, exist_ok=True)
    def download(entry):
        relative = Path(entry["path"])
        target = out / relative
        if not target.resolve().is_relative_to(out.resolve()):
            raise ValueError("Invalid source path")
        url = f"https://raw.githubusercontent.com/{plan['repository']}/{plan['commit']}/{entry['path']}"
        for attempt in range(3):
            try:
                if target.exists():
                    data = target.read_bytes()
                else:
                    req = urllib.request.Request(url, headers={"User-Agent": "tau-feedback-research/0.1"})
                    with urllib.request.urlopen(req, timeout=30) as response:
                        data = response.read()
                digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                if digest != entry["sha"] or len(data) != entry["size"]:
                    raise ValueError(f"Source mismatch: {entry['path']}")
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                return {"path": entry["path"], "sha256": hashlib.sha256(data).hexdigest()}
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(download, plan["files"]))
    (out / "VERIFIED_SOURCES.json").write_text(json.dumps({"commit":plan["commit"],"files": records},indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status":"verified", "files":len(records), "commit":plan["commit"]}))

if __name__ == "__main__":
    main()
