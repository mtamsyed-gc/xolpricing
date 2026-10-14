"""Stage 02 (line mode, RA) in the simulator vs a pandas oracle; indicated factors vs the synthetic truth."""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/alteryx"))
import xol_common as C  # noqa: E402
import build_stage00  # noqa: E402
import build_stage01  # noqa: E402
import build_stage02  # noqa: E402
import sim  # noqa: E402

STAGE = ROOT / "data/output/synth/stage"
SYN = ROOT / "data/synthetic"
FX = {"AED": 1.0, "USD": 3.6725}
VAL = pd.Timestamp("2025-09-30")
YEARS = [2021, 2022, 2023, 2024, 2025]
results = []


def load(name):
    with open(STAGE / f"{name}.yxdb", "rb") as f:
        fields, rows = pickle.load(f)
    return pd.DataFrame(rows, columns=list(fields))


def close(name, got, exp, tol=1e-6):
    got, exp = pd.Series(got, dtype=float).sort_index(), pd.Series(exp, dtype=float).sort_index()
    ok = got.index.equals(exp.index) and np.allclose(got.values, exp.values, rtol=tol, atol=0.01)
    results.append((name, ok))
    if not ok:
        print(f"  MISMATCH {name}\n    got {got.to_dict()}\n    exp {exp.to_dict()}")


def main() -> int:
    for b in (build_stage00.build(C.SYNTH), build_stage01.build_line(C.SYNTH), build_stage02.build(C.SYNTH)):
        sim.run(b)
        results.append((f"{b.stem}: no STOP", not [m for m in sim.MESSAGES if m[0] == "Error"]))

    m = pd.read_csv(SYN / "line/members.csv", parse_dates=["policy_start", "cover_start", "cover_end"])
    c = pd.read_csv(SYN / "line/claims_lines.csv", parse_dates=["treatment_date"], low_memory=False)
    c = c[c.status != "Rejected"].merge(m[["member_id", "policy_id", "policy_start"]], on=["member_id", "policy_id"])
    c["q"] = c.amount * c.currency.map(FX)
    c["ty"] = c.policy_start.dt.year
    c = c[c.ty.isin(YEARS)]
    units = c.groupby(["member_id", "policy_id", "ty"]).q.sum().reset_index()

    ec = load("exp_claimants")
    results.append(("units count", len(ec) == len(units)))
    close("unit amount by TY", ec.groupby("ty").amount_q.sum(), units.groupby("ty").q.sum())
    close("units > 100k by TY", (ec.amount_q > 100_000).groupby(ec.ty).sum(),
          (units.q > 100_000).groupby(units.ty).sum())

    m["prem_q"] = m.written_premium * m.currency.map(FX)
    m["ly"] = ((m.cover_end - m.cover_start).dt.days + 1) / 365
    m["ty"] = m.policy_start.dt.year
    end = m.cover_end.clip(upper=VAL)
    m["earned_q"] = m.prem_q * ((end - m.cover_start).dt.days + 1).clip(lower=0) / ((m.cover_end - m.cover_start).dt.days + 1)
    ex = load("exp_exposure_ty").set_index("ty")
    close("written LY by TY", ex.exposure_ly, m.groupby("ty").ly.sum())
    close("written premium by TY", ex.exposure_prem, m.groupby("ty").prem_q.sum())
    ind_unearned = m.groupby("ty").prem_q.sum() / m.groupby("ty").earned_q.sum()
    close("indicated UNEARNED (premium)", ex.ind_unearned, ind_unearned)
    p24 = m[m.ty == 2024]
    exp_unw = p24.prem_q.sum() / p24[p24.policy_start.dt.month <= 9].prem_q.sum()
    exp_unw_ly = p24.ly.sum() / p24[p24.policy_start.dt.month <= 9].ly.sum()
    close("indicated UNWRITTEN 2025 (premium, LY)", [ex.ind_unwritten_prem[2025], ex.ind_unwritten_ly[2025]],
          [exp_unw, exp_unw_ly])
    close("months elapsed", ex.months_elapsed, pd.Series({2020: 12, 2021: 12, 2022: 12, 2023: 12, 2024: 12, 2025: 9}))

    # sanity against the truth: claims-based ratios for RA 2025 (not equal by design: premium vs claims timing)
    t = json.loads((ROOT / "tests/reconciliation/synthetic_truth.json").read_text())["per_person"]["RA"]
    tw = t["written_cohorts_ultimate"]["2025"]["total"] / t["earned_ultimate"]["2025"]["total"]
    tu = t["ultimate_all_cohorts"]["2025"]["total"] / t["written_cohorts_ultimate"]["2025"]["total"]
    print(f"  RA 2025 indicated unearned {ex.ind_unearned[2025]:.3f} vs truth (claims) {tw:.3f}; "
          f"indicated unwritten {ex.ind_unwritten_prem[2025]:.3f} vs truth (claims) {tu:.3f}")
    results.append(("indicated vs truth within 25%", abs(ex.ind_unearned[2025] / tw - 1) < 0.25
                    and abs(ex.ind_unwritten_prem[2025] / tu - 1) < 0.25))
    for n, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {n}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
