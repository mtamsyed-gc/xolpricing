"""Rebuild every workflow (LIVE and SYNTH variants) and the Control workbooks.

    python tools/alteryx/build_all.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/alteryx"))
import xol_common as C  # noqa: E402
import build_stage00  # noqa: E402
import build_stage01  # noqa: E402
import build_stage02  # noqa: E402
import build_stage03  # noqa: E402

subprocess.run([sys.executable, str(ROOT / "tools/excel/build_control.py")], check=True)
for v in C.VARIANTS:
    for p in (build_stage00.build(v), build_stage01.build_line(v), build_stage01.build_claimant(v),
              build_stage02.build(v), build_stage03.build(v)):
        print("wrote", p.relative_to(ROOT))
