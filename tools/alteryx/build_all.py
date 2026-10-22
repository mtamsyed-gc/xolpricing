"""Rebuild every workflow (LIVE and SYNTH variants), the Control workbooks and the Results workbook template.

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
import build_stage04  # noqa: E402
import build_stage04a  # noqa: E402
import build_stage05  # noqa: E402
import build_stage06  # noqa: E402
import build_stage07  # noqa: E402

subprocess.run([sys.executable, str(ROOT / "tools/excel/build_control.py")], check=True)
subprocess.run([sys.executable, str(ROOT / "tools/excel/build_results.py")], check=True)
sys.path.insert(0, str(ROOT / "tools/synth"))
import merit_fixture  # noqa: E402
merit_fixture.test_control(ROOT / "data/synthetic/XoL_Control_synthetic.xlsx",
                           ROOT / "tests/fixtures/merit_v7/XoL_Control_merit_test.xlsx")   # for tools/check_merit.bat
for v in C.VARIANTS:
    for p in (build_stage00.build(v), build_stage01.build_line(v), build_stage01.build_claimant(v),
              build_stage01.build_ete(v), build_stage02.build(v), build_stage03.build(v), build_stage04a.build(v), build_stage04.build(v),
              build_stage05.build(v), build_stage06.build(v), build_stage07.build(v)):
        print("wrote", p.relative_to(ROOT))
