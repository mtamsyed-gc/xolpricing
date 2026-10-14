"""Stage 01 adapters in the simulator vs an independent pandas oracle built from the raw synthetic files."""
from __future__ import annotations

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
import sim  # noqa: E402

STAGE = ROOT / "data/output/synth/stage"
SYN = ROOT / "data/synthetic"
FX = {"AED": 1.0, "USD": 3.6725}
VAL = pd.Timestamp("2025-09-30")
results = []


def load(name):
    with open(STAGE / f"{name}.yxdb", "rb") as f:
        fields, rows = pickle.load(f)
    df = pd.DataFrame(rows, columns=list(fields))
    for c, t in fields.items():
        if t == "Date":
            df[c] = pd.to_datetime(df[c])
    return df


def close(name, got, exp, tol=1e-6):
    got, exp = pd.Series(got, dtype=float).sort_index(), pd.Series(exp, dtype=float).sort_index()
    ok = got.index.equals(exp.index) and np.allclose(got.values, exp.values, rtol=tol, atol=0.01)
    results.append((name, ok))
    if not ok:
        print(f"  MISMATCH {name}\n    got {got.to_dict()}\n    exp {exp.to_dict()}")


def errors():
    return [m for m in sim.MESSAGES if m[0] == "Error"]


def line_mode():
    m = pd.read_csv(SYN / "line/members.csv", parse_dates=["policy_start", "policy_end", "cover_start", "cover_end"])
    c = pd.read_csv(SYN / "line/claims_lines.csv", parse_dates=["treatment_date"], low_memory=False)
    c = c[c.status != "Rejected"]
    c["q"] = c.amount * c.currency.map(FX)
    c["yr"] = c.treatment_date.dt.year
    ic = load("int_claims")
    results.append(("L int_claims rows", len(ic) == len(c)))
    ic["yr"] = ic.treatment_date.dt.year
    close("L paid+os by year (quote ccy)", (ic.amount_paid_q + ic.amount_os_q).groupby(ic.yr).sum(),
          c.groupby("yr").q.sum())
    close("L os by year", ic.amount_os_q.groupby(ic.yr).sum(),
          c[c.status == "Outstanding"].groupby("yr").q.sum().reindex(ic.yr.unique(), fill_value=0))
    results.append(("L all claims matched to a cohort", ic.cohort_month.notna().all()))

    im = load("int_members")
    results.append(("L int_members rows", len(im) == len(m)))
    close("L written premium (quote ccy) by cohort year",
          im.written_premium_q.groupby(im.cohort_month.dt.year).sum(),
          (m.written_premium * m.currency.map(FX)).groupby(m.policy_start.dt.year).sum())
    lim = m.annual_limit * m.currency.map(FX)
    close("L annual limit (quote ccy) total", [im.annual_limit.sum()], [lim.sum()])

    ex = load("int_exposure")
    exp = {}
    end = m.cover_end.clip(upper=VAL)
    for y in range(2020, 2026):
        a = m.cover_start.clip(lower=pd.Timestamp(f"{y}-01-01"))
        b = end.clip(upper=pd.Timestamp(f"{y}-12-31"))
        exp[y] = ((b - a).dt.days + 1).clip(lower=0).sum() / 365
    close("L earned life-years by calendar year", ex.earned_ly.groupby(ex.exposure_month.dt.year).sum(), exp)
    close("L written premium by exposure year", ex.written_premium.groupby(ex.exposure_month.dt.year).sum(),
          (m.written_premium * m.currency.map(FX)).groupby(m.cover_start.dt.year).sum())
    prem_q = m.written_premium * m.currency.map(FX)
    days = (m.cover_end - m.cover_start).dt.days + 1
    earned_days = ((end - m.cover_start).dt.days + 1).clip(lower=0)
    close("L earned premium total", [ex.earned_premium.sum()], [(prem_q * earned_days / days).sum()])

    ag = load("int_claims_aggregate")
    close("L aggregate = claims by incurred year", (ag.amount_paid + ag.amount_os).groupby(ag.incurred_month.dt.year).sum(),
          c.groupby("yr").q.sum())


def claimant_mode():
    cl = pd.read_csv(SYN / "claimant/claimants.csv")
    e = pd.read_csv(SYN / "claimant/exposure_monthly.csv", parse_dates=["month"])
    ag = pd.read_csv(SYN / "claimant/claims_aggregate_monthly.csv", parse_dates=["incurred_month"])
    ic = load("int_claims")
    results.append(("C int_claims rows", len(ic) == len(cl)))
    close("C listing paid / os", [ic.amount_paid_q.sum(), ic.amount_os_q.sum()],
          [cl.paid_amount.sum(), cl.os_amount.sum()])
    ex = load("int_exposure")
    close("C earned LY by year", ex.earned_ly.groupby(ex.exposure_month.dt.year).sum(),
          (e.lives_inforce / 12).groupby(e.month.dt.year).sum())
    close("C written LY (lives_new) by year", ex.written_ly.groupby(ex.exposure_month.dt.year).sum(),
          e.lives_new.groupby(e.month.dt.year).sum())
    ia = load("int_claims_aggregate")
    close("C aggregate by year", (ia.amount_paid + ia.amount_os).groupby(ia.incurred_month.dt.year).sum(),
          (ag.paid_amount + ag.os_amount).groupby(ag.incurred_month.dt.year).sum())


def broken_line():
    """Unmapped status (A11), missing optional->required column (A01), 10% unknown members (A03, A13),
    a non-ISO date (A12) must all be caught."""
    import csv
    import shutil
    tdir = ROOT / "data/output/test/line"
    tdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(SYN / "line/run_meta.csv", tdir / "run_meta.csv")
    m = pd.read_csv(SYN / "line/members.csv", dtype=str, keep_default_na=False)
    m.drop(columns=["written_premium"]).to_csv(tdir / "members.csv", index=False)       # A01
    c = pd.read_csv(SYN / "line/claims_lines.csv", dtype=str, keep_default_na=False)
    c.loc[c.index[:5], "status"] = "PART-PAID"                                          # A11
    c.loc[c.index[5], "treatment_date"] = "30/09/2025"                                  # A12
    idx = c.sample(frac=0.10, random_state=1).index
    c.loc[idx, "member_id"] = "GHOST-" + c.loc[idx, "member_id"]                         # A03 / A13
    c.to_csv(tdir / "claims_lines.csv", index=False)
    test = C.Variant("TEST", ROOT / "data/output/test/wf", "..\\..\\..\\..\\", C.SYNTH.control,
                     "data/output/test/line", "data/output/test/claimant", "data/output/test")
    # stage 00 staging for the TEST variant (FX, limits)
    sim.run(build_stage00.build(test))
    sim.run(build_stage01.build_line(test))
    with open(ROOT / "data/output/test/checks/checks_01a.csv", encoding="utf-8") as f:
        codes = {r["code"] for r in csv.DictReader(f)} if (ROOT / "data/output/test/checks/checks_01a.csv").exists() else set()
    return codes


def main() -> int:
    sim.run(build_stage00.build(C.SYNTH))
    sim.run(build_stage01.build_line(C.SYNTH))
    results.append(("L no STOP errors", not errors()))
    line_mode()
    sim.run(build_stage01.build_claimant(C.SYNTH))
    results.append(("C no STOP errors", not errors()))
    claimant_mode()
    # A01 alone (members file without written_premium) - expected to stop the run
    codes = broken_line()
    print("  broken line-mode checks:", sorted(codes))
    results.append(("L broken input: A01 A03 A11 A12 A13 raised", {"A01", "A03", "A11", "A12", "A13"} <= codes))
    for n, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {n}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
