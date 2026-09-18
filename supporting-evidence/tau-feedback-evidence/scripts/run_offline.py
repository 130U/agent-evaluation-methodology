"""Execute the API-free evidence pipeline; downloads/install are separate."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--skip-replay",action="store_true",help="Skip only the slow current-environment migration diagnostic")
    args=parser.parse_args()
    out=ROOT/"results"
    logdir=out/"logs"
    logdir.mkdir(parents=True,exist_ok=True)
    stages=[("source_verification",["scripts/verify_sources.py"]),
            ("tests",["-m","unittest","discover","-s","tests","-v"]),
            ("archive_audit",["scripts/audit_archives.py"]),
            ("contract_experiment",["scripts/run_contract_experiment.py"]),
            ("projection_experiment",["scripts/run_projection_experiment.py"])]
    if not args.skip_replay:
        stages.append(("replay_experiment",["scripts/replay_archives.py"]))
        stages.append(("findings_report",["scripts/render_findings.py"]))
    records=[]
    for name,arguments in stages:
        start=time.perf_counter()
        log=logdir/f"{name}.txt"
        with log.open("w",encoding="utf-8") as stream:
            result=subprocess.run([sys.executable,"-X","utf8",*arguments],cwd=ROOT,
                stdout=stream,stderr=subprocess.STDOUT,timeout=1800,check=False)
        record={"stage":name,"returncode":result.returncode,"seconds":time.perf_counter()-start,
                "log":str(log.relative_to(ROOT)).replace('\\','/')}
        records.append(record)
        print(json.dumps(record),flush=True)
        if result.returncode:
            break
    paths=[*ROOT.glob("scripts/*.py"),*ROOT.glob("src/tau_feedback/*.py"),
           *ROOT.glob("tests/*.py"),*ROOT.glob("experiments/*"),ROOT/"requirements.lock"]
    hashes={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if p.is_file()}
    manifest={"created_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"python":platform.python_version(),
              "platform":platform.platform(),"stages":records,"source_hashes":hashes,
              "new_model_calls":0,"new_agent_trajectories":0,
              "end_to_end_gepa_experiment_status":"not_part_of_offline_reproduction",
              "replay_requested":not args.skip_replay,
              "success":len(records)==len(stages) and all(r["returncode"]==0 for r in records)}
    (out/"EXECUTION_MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    if not manifest["success"]:
        raise SystemExit(1)

if __name__=="__main__":
    main()
