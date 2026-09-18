"""Validate source and archive bytes against pinned GitHub tree identities."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def verify():
    counts={}
    for plan_name,base,flatten in [("runtime_sources.json",ROOT/"vendor/tau2",False),
                                    ("archive_sources.json",ROOT/"data/archives",True)]:
        plan=json.loads((ROOT/"experiments"/plan_name).read_text(encoding="utf-8"))
        for entry in plan["files"]:
            relative=Path(entry["path"])
            path=base/(relative.name if flatten else relative)
            if not path.resolve().is_relative_to(base.resolve()):
                raise ValueError("Invalid manifest path")
            data=path.read_bytes()
            blob=hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
            if blob!=entry["sha"] or len(data)!=entry["size"]:
                raise ValueError(f"Pinned source mismatch: {entry['path']}")
        counts[plan_name]=len(plan["files"])
    return counts

if __name__=="__main__":
    print(json.dumps({"status":"verified","counts":verify()}))
