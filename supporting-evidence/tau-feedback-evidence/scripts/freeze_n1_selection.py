"""After all reviews, freeze deterministic N1 diagnostic sampling; no model calls."""
import argparse
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["TAU2_DATA_DIR"] = str(ROOT / "vendor/tau2/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from tau_feedback.n1_audit import freeze_selection

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="experiments/N1_MANIFEST.json")
    args = parser.parse_args()
    print(freeze_selection(ROOT, ROOT / args.manifest))
