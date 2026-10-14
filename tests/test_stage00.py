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
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
