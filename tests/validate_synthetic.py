"""Check the synthetic data set against docs/data-contracts.md (required columns, types, adapter checks A01-A09).

This is a reference implementation of the stage-01 checks. The Alteryx adapters must give the same results.
Usage:  python tests/validate_synthetic.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "data/synthetic"
DATES = {"policy_start", "policy_end", "cover_start", "cover_end", "dob", "treatment_date", "admission_date",
         "discharge_date", "reported_date", "paid_date", "period_start", "period_end", "cohort_month",
         "first_treatment_date", "month", "incurred_month", "valuation_date"}

REQUIRED = {
    "line/members.csv": ["member_id", "policy_id", "scheme_id", "policy_start", "policy_end", "cover_start",
                         "cover_end", "currency", "written_premium"],
    "line/claims_lines.csv": ["claim_line_id", "claim_id", "member_id", "treatment_date", "amount", "status",
                              "currency"],
    "claimant/claimants.csv": ["member_id", "period_start", "period_end", "paid_amount", "os_amount", "currency"],
    "claimant/exposure_monthly.csv": ["month", "lives_inforce", "written_premium", "earned_premium"],
    "claimant/claims_aggregate_monthly.csv": ["incurred_month", "paid_amount", "os_amount"],
    "ete/fact_claims.csv": ["ClaimKey", "ClaimID", "LifeKey", "PolKey", "InExperience", "StatusClass",
                            "TreatmentDate", "ReportedMonth", "PaidMonth", "PayerShare"],
    "ete/dim_life.csv": ["LifeKey", "PolicyStart", "PolicyEndAdj", "CovStart", "CovEnd", "Master Contract ID",
                         "Currency", "WrittenPrem_Gross"],
    "ete/fact_member_month.csv": ["LifeKey", "UWY", "MonthStart", "EarnedLY_Gross", "EarnedPrem_Gross"],
}

results: list[tuple[str, str, str]] = []


def check(code: str, ok: bool, msg: str, stop: bool = True) -> None:
    results.append((code, "PASS" if ok else ("STOP" if stop else "FLAG"), msg))


def load(rel: str) -> pd.DataFrame:
    df = pd.read_csv(SYN / rel, low_memory=False, dtype={"event_id": str, "cause_code": str})
    for c in df.columns:
        if c in DATES:
            df[c] = pd.to_datetime(df[c], errors="raise")
    return df


def main() -> int:
    data = {}
    for rel, cols in REQUIRED.items():
        df = load(rel)
        data[rel] = df
        missing = [c for c in cols if c not in df.columns]
        nulls = [c for c in cols if c in df.columns and df[c].isna().any()
                 and c not in ("PaidMonth",)]          # PaidMonth blank for outstanding lines
        check("A01", not missing and not nulls, f"{rel}: required present/non-null (missing={missing}, nulls={nulls})")

    val = pd.Timestamp(pd.read_csv(SYN / "line/run_meta.csv").valuation_date[0])
    m, c = data["line/members.csv"], data["line/claims_lines.csv"]

    bad = m[(m.cover_start > m.cover_end) | (m.policy_start > m.cover_start) | (m.cover_end > m.policy_end)]
    check("A02", bad.empty, f"members: date order breaches = {len(bad)}", stop=False)

    match = c.member_id.isin(m.member_id).mean()
    check("A03", match >= 0.95, f"claims→members match rate = {match:.4%}")

    check("A04", (c.treatment_date <= val).all(), f"treatment_date ≤ valuation ({val.date()})")

    j = c.merge(m[["member_id", "policy_id", "cover_start", "cover_end"]], on=["member_id", "policy_id"], how="left")
    out = j[(j.treatment_date < j.cover_start) | (j.treatment_date > j.cover_end)]
    share = out.amount.abs().sum() / c.amount.abs().sum()
    check("A05", share <= 0.01, f"out-of-cover amount share = {share:.4%}", stop=False)

    net = c.groupby("claim_id").amount.sum()
    check("A06", (net >= -0.005).all(), f"claims with negative net amount = {(net < -0.005).sum()}", stop=False)

    ccy = set(c.currency) | set(m.currency)
    check("A07", ccy <= {"AED", "USD"}, f"currencies = {sorted(ccy)} (synthetic tbl_fx holds AED, USD)")

    cl, agg = data["claimant/claimants.csv"], data["claimant/claims_aggregate_monthly.csv"]
    thr = float(pd.read_csv(SYN / "claimant/run_meta.csv").listing_threshold[0])
    lst = (cl.paid_amount + cl.os_amount).groupby(cl.first_treatment_date.dt.year).sum()
    tot = (agg.paid_amount + agg.os_amount).groupby(agg.incurred_month.dt.year).sum()
    check("A08", (cl.paid_amount + cl.os_amount).min() >= thr and (lst.sum() <= tot.sum()),
          f"claimant listing ≥ {thr:,.0f} and Σ listing {lst.sum():,.0f} ≤ Σ aggregate {tot.sum():,.0f}")

    e = data["claimant/exposure_monthly.csv"]
    e = e.groupby("month", as_index=False)[["lives_new", "lives_inforce"]].sum()   # total over segments
    e["yr"] = e.month.dt.year
    full = e[e.yr.between(2021, 2024)].groupby("yr").agg(new=("lives_new", "sum"), inf=("lives_inforce", "mean"))
    ratio = (full.new / full.inf)
    check("A09", ratio.between(0.6, 1.4).all(), f"lives_new Σ12m / avg in-force by year = {ratio.round(3).to_dict()}",
          stop=False)

    # cross-mode consistency: line-mode totals (AED as-if) = claimant aggregate totals
    usd = 3.6725
    line_aed = (c[c.status != "Rejected"].assign(a=lambda d: d.amount.where(d.currency == "AED", d.amount * usd))).a.sum()
    check("X01", abs(line_aed - tot.sum()) < 1.0, f"line-mode Σ (AED) {line_aed:,.2f} = aggregate Σ {tot.sum():,.2f}")

    ev = c[c.event_id.notna()].groupby("event_id").member_id.nunique().to_dict()
    food = c[c.cause_code == "EVT-FOOD"]
    check("X02", ev == {"EV2022-01": 4, "EV2023-01": 7} and food.member_id.nunique() >= 15,
          f"supplied events {ev}; unlabelled food event lives = {food.member_id.nunique()}")

    fc, dl = data["ete/fact_claims.csv"], data["ete/dim_life.csv"]
    check("X03", fc.LifeKey.isin(dl.LifeKey).all(), "ETE: fact_claims.LifeKey ⊆ dim_life.LifeKey")

    for code, status, msg in results:
        print(f"{code:4} {status:4}  {msg}")
    return 1 if any(s == "STOP" for _, s, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
