"""The Python route (tools/run_python.py, 0.7.0): runs the committed workflows without Alteryx.

1. Synthetic data, line adapter, stages 00 to 03 through the runner: return code 0, the ENGINE.txt marker says python,
   the log ends ALL DONE, and the checks, reconciliations and burning-cost tables equal Designer's stored run
   (tests/reconciliation/designer_twin). Stages 04a to 07 are covered by test_designer_twin.py (same executor).
2. Stage files written by Alteryx (marker "alteryx") are refused: starting at 04 returns 2 and runs nothing.
3. Bad arguments (variant, adapter, stage order) return 2.
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_python  # noqa: E402

TWIN = ROOT / "tests/reconciliation/designer_twin"
OUT = ROOT / "data/output/synth"
NEW_INFO = {"A18", "A19"}   # 0.7.0 limit-source INFO records: not in the Designer run of 0.6


def call(*args) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run_python.main(list(args))
    return rc, buf.getvalue()


def same_checks(f: str) -> bool:
    d = pd.read_csv(TWIN / f"{f}.csv")
    s = pd.read_csv(OUT / "checks" / f"{f}.csv")
    s = s[~s.code.isin(NEW_INFO)].reset_index(drop=True)
    return d.equals(s)


def same_recon(f: str) -> bool:
    m = pd.read_csv(TWIN / f"{f}.csv").merge(pd.read_csv(OUT / "checks" / f"{f}.csv"), on=["stage", "table", "measure"],
                                              how="outer", suffixes=("_d", "_s"))
    return bool(m.notna().all().all() and ((m.value_d - m.value_s).abs() <= 1e-4).all())


def same_table(f: str) -> bool:
    d = pd.read_csv(TWIN / f"{f}.csv")
    p = next((q for q in (OUT / "results" / f"{f}.csv", OUT / "checks" / f"{f}.csv") if q.exists()), None)
    if p is None:
        return False
    s = pd.read_csv(p)
    if set(d.columns) != set(s.columns) or len(d) != len(s):       # columns matched by name
        return False
    good = True
    for c in d.columns:
        if pd.api.types.is_numeric_dtype(d[c]):
            good &= bool(np.allclose(d[c], s[c], rtol=1e-9, atol=1e-6, equal_nan=True))
        else:
            good &= bool((d[c].fillna("").astype(str) == s[c].fillna("").astype(str)).all())
    return good


def report(name: str, good: bool) -> bool:
    print(f"{'PASS' if good else 'FAIL'}  {name}")
    return good


def main() -> int:
    ok = True
    rc, out = call("synth", "01a", "00", "03")
    log = (OUT / "python_run.log").read_text(encoding="utf-8")
    marker = (OUT / "stage" / run_python.MARKER).read_text(encoding="utf-8")
    ok &= report("runner synth 01a 00 -> 03: return code 0, marker python, log ALL DONE",
                 rc == 0 and marker.startswith("python") and "ALL DONE" in log and "Stages 00 to 03 ran." in out)
    if rc != 0:
        print(out)
    for f in ("checks_00", "checks_01a", "checks_02", "checks_03"):
        ok &= report(f"{f} equals Designer's run", same_checks(f))
    for f in ("recon_01a", "recon_02", "recon_03"):
        ok &= report(f"{f} equals Designer's run", same_recon(f))
    for f in ("out_bc_summary", "out_bc_by_year", "out_annualisation", "out_scenario_applied"):
        ok &= report(f"{f} equals Designer's run", same_table(f))

    # stage files written by Alteryx: refused before anything runs
    (OUT / "stage" / run_python.MARKER).write_text("alteryx 06/11/2026 10:00\n", encoding="utf-8")
    before = (OUT / "python_run.log").stat().st_mtime_ns
    rc, out = call("synth", "01a", "04", "07")
    ok &= report("start at 04 after an Alteryx stage 00: refused (return code 2, nothing run)",
                 rc == 2 and "Run from stage 00" in out and (OUT / "python_run.log").stat().st_mtime_ns == before)
    (OUT / "stage" / run_python.MARKER).write_text(marker, encoding="utf-8")

    bad = [(("demo",), "Unknown variant"), (("synth", "01d"), "Unknown adapter"),
           (("synth", "01a", "05", "02"), "comes after"), (("synth", "01a", "01b"), "Unknown stage")]
    for args, msg in bad:
        rc, out = call(*args)
        ok &= report(f"bad arguments {' '.join(args)}: return code 2 ({msg})", rc == 2 and msg in out)

    steps = run_python.plan("01c", "00", "07")
    ok &= report("run order 00, 01c, 02, 03, 04a, 04, 05, 06, 07",
                 [c for c, _ in steps] == ["00", "01c", "02", "03", "04a", "04", "05", "06", "07"])
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
