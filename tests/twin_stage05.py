"""Compare Designer's stage-05 outputs (per-event XoL, 10,000 iterations, SYNTH, line mode) with the numpy oracle of
tests/test_stage05.py.

    python tests/twin_stage05.py <folder holding Designer's out_ev_events.csv, out_ev_bc_by_year.csv,
                                  out_ev_bc_summary.csv, out_ev_sim_summary.csv, out_ev_iterations.csv,
                                  out_ev_scenarios.csv, checks_05.csv>
The oracle reads the simulator's stage-00..03 staging for SYNTH in line mode (rebuilt here), which the Designer twin
of stages 00-03 has shown to be identical to Designer's.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools/alteryx"))
import xol_common as C  # noqa: E402
import build_stage00, build_stage01, build_stage02, build_stage03  # noqa: E401,E402
import sim  # noqa: E402
import test_stage05 as T  # noqa: E402


def main() -> int:
    d = Path(sys.argv[1])
    for b in (build_stage00.build(C.SYNTH), build_stage01.build_line(C.SYNTH), build_stage02.build(C.SYNTH),
              build_stage03.build(C.SYNTH)):
        sim.run(b)
    out, ctl = ROOT / "data/output/synth", ROOT / C.SYNTH.control
    n = int(pd.read_csv(d / "out_ev_sim_summary.csv").iterations.iloc[0])
    e = T.oracle(out, ctl, n)
    T.compare(e, out, R=d)
    chk = pd.read_csv(d / "checks_05.csv")
    T.results.append((f"{n:,} iterations; no STOP in checks_05", n >= 1000 and not (chk.severity == "STOP").any()))
    for name, ok in T.results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(ok for _, ok in T.results) else 1


if __name__ == "__main__":
    sys.exit(main())
