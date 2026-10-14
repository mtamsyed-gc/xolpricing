"""Stage 00 in the simulator: clean run on the synthetic Control, then a deliberately broken copy must raise
exactly the expected checks."""
from __future__ import annotations

import csv
import pickle
import shutil
import sys
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/alteryx"))
import xol_common as C  # noqa: E402
import build_stage00  # noqa: E402
import sim  # noqa: E402

TEST = C.Variant("TEST", ROOT / "data/output/test/wf", "..\\..\\..\\..\\", "data/output/test/XoL_Control_bad.xlsx",
                 "data/synthetic/line", "data/synthetic/claimant", "data/output/test")


def run(v):
    wf = build_stage00.build(v)
    sim.run(wf)
    with open(ROOT / v.out_dir / "checks/checks_00.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows, [m for m in sim.MESSAGES if m[0] == "Error"]


def find_row(ws, col, value):
    for r in range(5, ws.max_row + 1):
        if ws.cell(row=r, column=col).value == value:
            return r
    raise KeyError(value)


def main() -> int:
    ok = True
    rows, errs = run(C.SYNTH)
    codes = {r["code"] for r in rows}
    print("synthetic:", codes, "errors:", len(errs))
    ok &= codes == {"OK"} and not errs
    with open(ROOT / "data/output/synth/stage/ctl_params.yxdb", "rb") as f:
        fields, prow = pickle.load(f)
    p = prow[0]
    print("typed params sample:", {k: (p[k], fields[k]) for k in ("quote_year", "treaty_start", "epi_quote_year",
                                                                  "treaty_basis", "iterations")})
    ok &= fields["quote_year"] == "Int64" and p["quote_year"] == 2026 and fields["treaty_start"] == "Date"

    # broken copy
    bad = ROOT / TEST.control
    bad.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / C.SYNTH.control, bad)
    wb = load_workbook(bad)
    ps = wb["Params"]
    ps.cell(row=find_row(ps, 1, "country"), column=3).value = None            # P01
    ps.cell(row=find_row(ps, 1, "quote_year"), column=3).value = "2026x"      # P02
    ps.cell(row=find_row(ps, 1, "treaty_basis"), column=3).value = "XX"       # P03
    ps.delete_rows(find_row(ps, 1, "seed"))                                   # P04
    ps.cell(row=ps.max_row + 1, column=1).value = "mystery_param"             # P05
    ws = wb["Weights"]
    ws.cell(row=5, column=3).value = 0                                         # T03 (custom weight blank)
    lay = wb["Layers"]
    lay.cell(row=5, column=6).value = None                                     # L01 (PP opt 1 AP blank)
    lay.cell(row=6, column=7).value = None                                     # L02 (PP opt 2 limit blank)
    comp = wb["Completion"]
    comp.cell(row=5, column=5).value = 0.95                                    # T04
    tr = wb["Trend"]
    tr.delete_rows(find_row(tr, 1, 2023))                                      # T02 (experience year)
    wb.save(bad)
    rows, errs = run(TEST)
    codes = sorted({r["code"] for r in rows} - {"OK"})
    expect = ["L01", "L02", "P01", "P02", "P03", "P04", "P05", "T02", "T03", "T04"]
    print("broken:", codes, "STOP errors raised:", len(errs))
    ok &= codes == expect and len(errs) > 0
    ok &= scenarios()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def load_stage(v, name):
    import pandas as pd
    with open(ROOT / v.out_dir / f"stage/{name}.yxdb", "rb") as f:
        fields, rows = pickle.load(f)
    return pd.DataFrame(rows, columns=list(fields))


def set_param(wb, pid, value):
    ps = wb["Params"]
    ps.cell(row=find_row(ps, 1, pid), column=3).value = value


def scenarios() -> bool:
    """Scenario overrides: scenario 1 adds 2pp to every trend residual, scenario 2 multiplies IBNR factors by 1.1;
    scenario 3 (added here) sets a parameter, multiplies an INT parameter, keys on one year; scenario 4 is broken."""
    import numpy as np
    sv = C.Variant("SCN", ROOT / "data/output/scn/wf", "..\\..\\..\\..\\", "data/output/scn/XoL_Control_scn.xlsx",
                   "data/synthetic/line", "data/synthetic/claimant", "data/output/scn")
    ctl = ROOT / sv.control
    ctl.parent.mkdir(parents=True, exist_ok=True)
    base_tr = load_stage(C.SYNTH, "ctl_trend").set_index("year")
    base_cp = load_stage(C.SYNTH, "ctl_completion")
    good = True

    def run_scn(sid, extra=()):
        shutil.copy(ROOT / C.SYNTH.control, ctl)
        wb = load_workbook(ctl)
        set_param(wb, "scenario_id", str(sid))
        sc = wb["Scenarios"]
        for row in extra:
            r = sc.max_row + 1
            for c, val in enumerate(row, 1):
                sc.cell(row=r, column=c).value = val
        wb.save(ctl)
        return run(sv)

    rows, errs = run_scn(1)
    tr = load_stage(sv, "ctl_trend").set_index("year")
    r1 = bool(np.allclose(tr.residual_pct, base_tr.residual_pct.fillna(0) + 0.02)) and not errs
    print(f"{'PASS' if r1 else 'FAIL'}  scenario 1: trend residual + 0.02 on every year")
    rows, errs = run_scn(2)
    cp = load_stage(sv, "ctl_completion")
    ib = (base_cp.component == "IBNR").to_numpy()
    r2 = bool(np.allclose(cp.selected_factor[ib], base_cp.selected_factor[ib] * 1.1) and
              np.allclose(cp.selected_factor[~ib], base_cp.selected_factor[~ib])) and not errs
    print(f"{'PASS' if r2 else 'FAIL'}  scenario 2: IBNR factors x 1.1, others unchanged")
    rows, errs = run_scn(3, [(3, "mixed", "Params", "param_id=incidence_adj", "value", "SET", "1.15"),
                             (3, "mixed", "Params", "param_id=iterations", "value", "MULT", "0.5"),
                             (3, "mixed", "Trend", "year=2024", "residual_pct", "SET", "0.1"),
                             (3, "mixed", "Trend", "year=2024", "residual_pct", "ADD", "0.01"),
                             (3, "mixed", "Layers", "option_id=1", "ap", "MULT", "1.5")])
    pr = load_stage(sv, "ctl_params").iloc[0]
    tr = load_stage(sv, "ctl_trend").set_index("year")
    ly = load_stage(sv, "ctl_layers")
    ap_pp1 = ly[(ly.cover == "PP") & (ly.option_id == 1)].ap.iloc[0]
    r3 = (pr.incidence_adj == 1.15 and pr.iterations == 5000 and abs(tr.residual_pct[2024] - 0.11) < 1e-12 and
          abs(tr.residual_pct[2023] - base_tr.residual_pct[2023]) < 1e-12 and ap_pp1 == 150000 and not errs)
    ap = list(csv.DictReader(open(ROOT / sv.out_dir / "checks/out_scenario_applied.csv", encoding="utf-8")))
    r3 &= [float(a["rows_matched"]) for a in ap] == [1, 1, 1, 1, 3]     # option_id=1: PP, EV and SL rows
    r3 &= bool(ly[(ly.cover == "SL") & (ly.option_id == 1)].ap.isna().all())     # MULT leaves a blank blank
    print(f"{'PASS' if r3 else 'FAIL'}  scenario 3: SET / MULT params, SET then ADD on one trend year, "
          f"MULT on a layer AP (option 1, both covers keyed); rows matched listed")
    rows, errs = run_scn(4, [(4, "bad", "Trend", "ALL", "year_typo", "ADD", "0.01"),
                             (4, "bad", "Completion", "ALL", "selected_factor", "MULT", "-1"),
                             (4, "bad", "Trend", "year=1999", "residual_pct", "ADD", "0.01")])
    codes = sorted({r["code"] for r in rows} - {"OK"})
    r4 = codes == ["X02", "X03", "X04"] and len(errs) > 0
    print(f"{'PASS' if r4 else 'FAIL'}  scenario 4 broken: {codes}")
    rows, errs = run_scn(9)
    r5 = "X01" in {r["code"] for r in rows}
    print(f"{'PASS' if r5 else 'FAIL'}  scenario 9 has no rows: X01")
    good &= r1 and r2 and r3 and r4 and r5
    return good


if __name__ == "__main__":
    sys.exit(main())
