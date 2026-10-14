"""Compare Designer's stage-06 outputs (10,000 iterations, SYNTH, line mode) with the numpy oracle of
tests/test_stage06.py.

    python tests/twin_stage06.py <folder holding Designer's out_attritional_by_year.csv, out_gross_summary.csv,
                                  out_gross_iterations.csv, out_struct_summary.csv, out_struct_quantiles.csv,
                                  out_exposed_lives.csv, checks_06.csv>
The oracle reads the simulator's stage-00..03 staging for SYNTH in line mode (rebuilt here), which the Designer twin
of stages 00-03 has shown to be identical to Designer's, and replays stage 04 with the stage-04 oracle.
"""
import sys

import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools/alteryx"))
import xol_common as C  # noqa: E402
import build_stage00, build_stage01, build_stage02, build_stage03, build_stage04a  # noqa: E401,E402
import sim  # noqa: E402
import test_stage06 as T  # noqa: E402

T.OUT = ROOT / "data/output/synth"
T.CTL = ROOT / C.SYNTH.control


def main() -> int:
    d = Path(sys.argv[1])
    if (d / "out_sev_table.csv").exists():       # Designer's stage-04a grid (stage 03 here empties the staged one)
        T.T4.TAB = pd.read_csv(d / "out_sev_table.csv", encoding="utf-8-sig")
    for b in (build_stage00.build(C.SYNTH), build_stage01.build_line(C.SYNTH), build_stage02.build(C.SYNTH),
              build_stage03.build(C.SYNTH), build_stage04a.build(C.SYNTH)):    # 04a: the grid if Designer's is absent
        sim.run(b)
    T.compare(T.oracle(), d, d / "checks_06.csv", tol=1e-7)
    for name, ok in T.results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(ok for _, ok in T.results) else 1


if __name__ == "__main__":
    sys.exit(main())
