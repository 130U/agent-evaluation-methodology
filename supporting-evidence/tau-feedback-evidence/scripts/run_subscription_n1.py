"""Run one registered N1 phase. Never registers or silently resumes experiments."""
import argparse
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from tau_feedback.n1_audit import run_phase

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="experiments/N1_MANIFEST.json")
    parser.add_argument("--phase", choices=("review", "gate"), required=True)
    parser.add_argument("--max-new-units", type=int, default=2)
    args = parser.parse_args()
    run_phase(ROOT, ROOT / args.manifest, args.phase, args.max_new_units)
