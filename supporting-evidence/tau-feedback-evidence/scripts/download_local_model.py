"""Fetch official, hash-pinned runtime and open weights into this project only."""
from __future__ import annotations
import hashlib
import argparse
import json
from pathlib import Path
import shutil
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def sha256(path):
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        while chunk:=stream.read(8*1024*1024):
            digest.update(chunk)
    return digest.hexdigest()

def download(url,target,size,digest):
    if target.exists():
        if target.stat().st_size==size and sha256(target)==digest:
            print(json.dumps({"file":target.name,"status":"already_verified"}),flush=True)
            return
        raise ValueError(f"Existing target has wrong hash: {target.name}")
    if shutil.disk_usage(ROOT).free<size*2+512*1024*1024:
        raise RuntimeError("Insufficient free disk space for bounded download")
    temporary=target.with_suffix(target.suffix+".partial")
    target.parent.mkdir(parents=True,exist_ok=True)
    request=urllib.request.Request(url,headers={"User-Agent":"tau-feedback-research/0.1"})
    started=time.perf_counter()
    previous=started
    received=0
    with urllib.request.urlopen(request,timeout=60) as response,temporary.open("wb") as output:
        while chunk:=response.read(1024*1024):
            output.write(chunk)
            received+=len(chunk)
            if received>size:
                raise ValueError("Response exceeds pinned size")
            if time.perf_counter()-previous>15:
                print(json.dumps({"file":target.name,"received_mb":round(received/1e6),"total_mb":round(size/1e6)}),flush=True)
                previous=time.perf_counter()
    if received!=size or sha256(temporary)!=digest:
        raise ValueError("Downloaded asset does not match pinned hash")
    temporary.replace(target)
    print(json.dumps({"file":target.name,"status":"verified","seconds":round(time.perf_counter()-started,1)}),flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",default="experiments/local_model_assets.json")
    args=parser.parse_args()
    plan=json.loads((ROOT/args.manifest).read_text(encoding="utf-8"))
    rt=plan["runtime"]
    archive=ROOT/"vendor/llama.cpp"/Path(rt["url"]).name
    download(rt["url"],archive,rt["size"],rt["sha256"])
    destination=archive.parent/rt["release"]
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            target=destination/item.filename
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Unsafe archive member")
        package.extractall(destination)
    model=plan["model"]
    url=f"https://huggingface.co/{model['repository']}/resolve/{model['revision']}/{model['file']}?download=true"
    download(url,ROOT/"models"/model["file"],model["size"],model["sha256"])
    print(json.dumps({"status":"local_assets_ready","new_model_calls":0}),flush=True)

if __name__=="__main__":
    main()
