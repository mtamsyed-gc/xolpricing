"""The simulator must reproduce the stored Designer outputs (tests/reconciliation/designer_twin)."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/alteryx"))
import xol_common as C  # noqa: E402
import build_stage00  # noqa: E402
import build_stage01  # noqa: E402
import sim  # noqa: E402

TWIN = ROOT / "tests/reconciliation/designer_twin"
OUT = ROOT / "data/output/synth/checks"


def main() -> int:
    ok = True
    sim.run(build_stage00.build(C.SYNTH))
    sim.run(build_stage01.build_line(C.SYNTH))
    for f in ("checks_00", "checks_01a", "recon_01a"):
        ok &= compare(f)
    sim.run(build_stage01.build_claimant(C.SYNTH))
    for f in ("checks_01b", "recon_01b"):
        ok &= compare(f)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def compare(f) -> bool:
    d, s = pd.read_csv(TWIN / f"{f}.csv"), pd.read_csv(OUT / f"{f}.csv")
    if f.startswith("checks"):
        good = d.equals(s)
    else:
        m = d.merge(s, on=["stage", "table", "measure"], how="outer", suffixes=("_d", "_s"))
        good = m.notna().all().all() and ((m.value_d - m.value_s).abs() <= 1e-4).all()
    print(f"{'PASS' if good else 'FAIL'}  {f} (Designer 2026.2 vs simulator)")
    return bool(good)


if __name__ == "__main__":
    sys.exit(main())
