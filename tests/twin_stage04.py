"""Compare Designer's stage-04 outputs (10,000 iterations, SYNTH) with the numpy oracle of tests/test_stage04.py.

    python tests/twin_stage04.py <folder holding Designer's out_sim_summary.csv, out_sim_iterations.csv,
                                  out_sim_quantiles.csv>
The oracle reads the simulator's stage-00..03 staging for SYNTH (data/output/synth/stage), which the Designer twin
of stages 00-03 has shown to be identical to Designer's.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_stage04 as T  # noqa: E402

T.OUT = ROOT / "data/output/synth"
T.CTL = ROOT / "data/synthetic/XoL_Control_synthetic.xlsx"


def main() -> int:
    d = Path(sys.argv[1])
    if (d / "out_sev_table.csv").exists():       # Designer's stage-04a grid (stage 03 here empties the staged one)
        T.TAB = pd.read_csv(d / "out_sev_table.csv", encoding="utf-8-sig")
    e = T.oracle()
    summ = pd.read_csv(d / "out_sim_summary.csv", encoding="utf-8-sig").set_index("option_id")
    its = pd.read_csv(d / "out_sim_iterations.csv", encoding="utf-8-sig")
    qs = pd.read_csv(d / "out_sim_quantiles.csv", encoding="utf-8-sig")
    n = int(summ.iterations.iloc[0])
    T.close("alpha", [summ.alpha.iloc[0]], [e["alpha"]])
    T.close("lambda", [summ["lambda"].iloc[0]], [e["lam"]])
    for o, x in e["out"].items():
        g = its[its.option_id == o].sort_values("iter")
        T.close(f"option {o} recovery by iteration ({n})", g.recovery, x["rec"], tol=1e-7)
        T.close(f"option {o} mean", [summ.loc[o, "mean_recovery"]], [x["rec"].mean()], tol=1e-7)
        srt = np.sort(x["rec"])
        q = qs[qs.option_id == o].set_index("q").recovery
        T.close(f"option {o} quantiles", q.values, [srt[max(1, math.ceil(round(v, 3) * n)) - 1] for v in q.index],
                tol=1e-7)
    for name, ok in T.results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(summ[["l_label", "lambda", "alpha", "mean_recovery", "sd_recovery", "p90", "p99", "expected_cost_det",
                "credibility_z", "det_to_stoch_below", "expected_loss_blended", "expected_loss_pppy"]].to_string())
    return 0 if all(ok for _, ok in T.results) else 1


if __name__ == "__main__":
    sys.exit(main())
