"""Bounded, loopback-only llama.cpp process owned by this research run."""
from __future__ import annotations
from contextlib import AbstractContextManager
import json
import hashlib
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import threading
import urllib.error
import urllib.request
import zipfile

import psutil

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise urllib.error.HTTPError(req.full_url,code,"Local API redirects are disabled",headers,fp)

def file_hash(path):
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        while chunk:=stream.read(8*1024*1024):
            digest.update(chunk)
    return digest.hexdigest()

class LocalRuntime(AbstractContextManager):
    def __init__(self,root:Path,*,context=4096,threads=4,slots=1,timeout=120,
                 assets_manifest="experiments/local_model_assets.json",reasoning="off",reasoning_budget=-1):
        self.root=root.resolve()
        self.assets_manifest=(self.root/assets_manifest).resolve()
        if not self.assets_manifest.is_relative_to(self.root):
            raise ValueError("Model manifest must be inside this research workspace")
        self.context=context
        if reasoning not in {"on","off","auto"} or type(reasoning_budget) is not int or reasoning_budget < -1:
            raise ValueError("Invalid explicit reasoning configuration")
        self.reasoning=reasoning
        self.reasoning_budget=reasoning_budget
        self.threads=threads
        self.slots=slots
        self.timeout=timeout
        self.process=None
        self.log_stream=None
        self.command=[]
        self.memory_before=None
        self.port=None
        self.api_key=secrets.token_urlsafe(32)
        self.monitor_stop=threading.Event()
        self.monitor_thread=None
        self.stop_reason=None
        self.minimum_available_bytes=None
        self.peak_server_rss_bytes=0
        # Explicitly bypass proxy environment variables for loopback traffic.
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

    def request(self,path,payload=None,timeout=None):
        if self.stop_reason:
            raise RuntimeError(self.stop_reason)
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("Owned local model server is not running")
        if not path.startswith("/") or "://" in path:
            raise ValueError("Only relative local API paths are allowed")
        request=urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type":"application/json","Authorization":"Bearer "+self.api_key})
        with self.opener.open(request,timeout=timeout or self.timeout) as response:
            return json.load(response)

    def __enter__(self):
        plan=json.loads(self.assets_manifest.read_text(encoding="utf-8"))
        model=self.root/"models"/plan["model"]["file"]
        executable=self.root/"vendor/llama.cpp"/plan["runtime"]["release"]/"llama-server.exe"
        if not model.is_file() or model.stat().st_size!=plan["model"]["size"]:
            raise RuntimeError("Verified model asset is not ready")
        if file_hash(model)!=plan["model"]["sha256"]:
            raise RuntimeError("Model SHA-256 differs from pinned asset")
        rt=plan["runtime"]
        if "sha256" in rt:
            archive=self.root/"vendor/llama.cpp"/Path(rt["url"]).name
            if file_hash(archive)!=rt["sha256"]:
                raise RuntimeError("Runtime archive SHA-256 differs from pinned asset")
            with zipfile.ZipFile(archive) as package:
                for item in package.infolist():
                    if item.is_dir():
                        continue
                    target=(executable.parent/item.filename).resolve()
                    if not target.is_relative_to(executable.parent.resolve()):
                        raise RuntimeError("Invalid runtime member path")
                    if file_hash(target)!=hashlib.sha256(package.read(item)).hexdigest():
                        raise RuntimeError(f"Extracted runtime member changed: {item.filename}")
        self.memory_before=psutil.virtual_memory().available
        # Small pilot only; do not let startup consume the user's final margin.
        if self.memory_before < 2*1024**3:
            raise RuntimeError("Less than 2 GiB free RAM; defer local model startup")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1",0))
            self.port=probe.getsockname()[1]
        self.command=[str(executable),"-m",str(model),"--alias","tau-local-qwen",
            "--host","127.0.0.1","--port",str(self.port),"-c",str(self.context),
            "--parallel",str(self.slots),"--threads",str(self.threads),
            "--threads-batch",str(self.threads),"--cache-ram","0","-b","256","-ub","64",
            "-ngl","0","--cache-type-k","q8_0","--cache-type-v","q8_0",
            "--flash-attn","on","--jinja","--reasoning",self.reasoning,"--reasoning-budget",str(self.reasoning_budget),"--no-context-shift",
            "--no-agent","--no-webui","--offline","--cors-origins","localhost",
            "--no-cors-credentials"]
        logs=self.root/"results/local_pilot"
        logs.mkdir(parents=True,exist_ok=True)
        self.log_path=logs/f"server-c{self.context}-s{self.slots}-{time.time_ns()}.log"
        self.log_stream=self.log_path.open("w",encoding="utf-8")
        try:
            environment=os.environ.copy()
            environment["LLAMA_API_KEY"]=self.api_key
            self.process=subprocess.Popen(self.command,cwd=executable.parent,stdout=self.log_stream,
                stderr=subprocess.STDOUT,env=environment,
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            self.monitor_thread=threading.Thread(target=self._monitor_memory,daemon=True)
            self.monitor_thread.start()
            deadline=time.monotonic()+self.timeout
            while time.monotonic()<deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f"Model server exited {self.process.returncode}; inspect {self.log_path.name}")
                if psutil.virtual_memory().available < 512*1024**2:
                    raise RuntimeError("Available RAM below 512 MiB; stop owned model process")
                try:
                    if self.request("/health",timeout=2).get("status")=="ok":
                        return self
                except (urllib.error.URLError,TimeoutError,ConnectionError):
                    pass
                time.sleep(0.5)
            raise TimeoutError("Local model readiness timeout")
        except BaseException:
            self.__exit__(None,None,None)
            raise

    def _monitor_memory(self):
        low_count=0
        while not self.monitor_stop.wait(2):
            if self.process is None or self.process.poll() is not None:
                return
            available=psutil.virtual_memory().available
            self.minimum_available_bytes=min(available,self.minimum_available_bytes or available)
            try:
                self.peak_server_rss_bytes=max(self.peak_server_rss_bytes,psutil.Process(self.process.pid).memory_info().rss)
            except (psutil.Error,TypeError):
                pass
            low_count=low_count+1 if available<512*1024**2 else 0
            if low_count>=3:
                self.stop_reason="Available RAM below 512 MiB for three consecutive 2-second observations"
                self.process.terminate()
                return

    def __exit__(self,*args):
        self.monitor_stop.set()
        if self.monitor_thread is not None:
            self.monitor_thread.join(timeout=3)
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
        finally:
            if self.log_stream is not None:
                self.log_stream.close()
        return False
