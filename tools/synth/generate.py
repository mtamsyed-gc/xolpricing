"""Synthetic data generator for the XoL pricing tool.

Produces a fictional UAE group-medical portfolio in every input format the tool accepts
(docs/data-contracts.md):

    data/synthetic/line/       members.csv, claims_lines.csv, run_meta.csv           (mode L)
    data/synthetic/claimant/   claimants.csv, exposure_monthly.csv,
                               claims_aggregate_monthly.csv, run_meta.csv            (mode C)
    data/synthetic/ete/        fact_claims.csv, dim_life.csv, fact_member_month.csv, run_manifest_claims.csv (mode E)
    data/templates/            header-only CSV templates for standalone users
    tests/reconciliation/      synthetic_controls.json (observed totals) and
                               synthetic_truth.json   (ultimate values incl. IBNR, unearned, unwritten)

Everything is driven by a fixed seed, so re-running gives identical files. All names are fictional.

Usage:  python tools/synth/generate.py [--seed 20261014] [--out <repo root>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------------------
# Parameters (the "truth" the tool should recover)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Truth:
    valuation_date: str = "2025-09-30"
    first_cohort_year: int = 2020
    last_cohort_start: str = "2025-12-01"      # cohorts after valuation are generated for truth only
    n_schemes_initial: int = 150
    new_schemes_per_year: int = 18
    scheme_lapse_rate: float = 0.08
    scheme_size_median: float = 22.0
    scheme_size_sigma: float = 1.0
    member_retention: float = 0.85
    joiner_rate: float = 0.10
    leaver_rate: float = 0.08
    usd_schemes: int = 8
    usd_per_aed: float = 1 / 3.6725
    base_premium_2021: tuple = (("A", 9000.0), ("B", 5500.0), ("C", 2800.0))
    premium_trend: float = 0.07
    annual_limit: tuple = (("A", 1_000_000.0), ("B", 500_000.0), ("C", 150_000.0))
    plan_mix: tuple = (("A", 0.2), ("B", 0.5), ("C", 0.3))
    plan_cost_factor: tuple = (("A", 1.4), ("B", 1.0), ("C", 0.6))
    claims_trend: float = 0.08                  # FGU severity trend p.a.
    attr_lines_per_ly: float = 1.6              # attritional encounter lines per life-year
    attr_mean_2021: float = 900.0               # AED, plan B
    attr_cv: float = 1.5
    large_freq_per_ly: float = 0.02             # large episodes per life-year (plan B)
    large_xm_2021: float = 40_000.0             # Pareto scale (AED, 2021 money)
    large_alpha: float = 1.6
    attr_report_lag_mean: float = 25.0          # days
    large_report_lag_mean: float = 70.0
    attr_pay_lag_mean: float = 20.0
    large_pay_lag_mean: float = 90.0
    large_case_reserve_factor: float = 0.85     # OS large claims under-reserved at valuation
    rejected_share: float = 0.03
    reversal_share: float = 0.005


T = Truth()
VAL = pd.Timestamp(T.valuation_date)
REF_LAYERS = {"L100xs": (100_000.0, 400_000.0), "L250xs": (250_000.0, 750_000.0)}  # AP, limit (AED as-if)
THRESHOLDS = (50_000.0, 100_000.0, 250_000.0, 500_000.0)


# --------------------------------------------------------------------------------------
# Schemes and members
# --------------------------------------------------------------------------------------
def build_schemes(rng: np.random.Generator) -> pd.DataFrame:
    rows, sid = [], 0
    plans, probs = zip(*T.plan_mix)
    sites = ["Dubai Marina", "Jebel Ali", "Abu Dhabi Mussafah", "Sharjah Industrial", "Al Ain", "DIFC",
             "Ras Al Khaimah Port", "Fujairah Terminal"]
    for year in range(T.first_cohort_year, 2026):
        n_new = T.n_schemes_initial if year == T.first_cohort_year else T.new_schemes_per_year
        for _ in range(n_new):
            sid += 1
            size0 = max(2, int(round(rng.lognormal(np.log(T.scheme_size_median), T.scheme_size_sigma))))
            rows.append(dict(
                scheme_id=f"SCH{sid:04d}",
                first_year=year,
                renew_month=int(rng.integers(1, 13)),
                size0=size0,
                growth=float(rng.normal(0.04, 0.08)),
                plan=str(rng.choice(plans, p=probs)),
                n_sites=int(rng.integers(1, 4)),
                site_pool=",".join(rng.choice(sites, size=3, replace=False)),
            ))
    s = pd.DataFrame(rows)
    usd = rng.choice(s.index[s.first_year == T.first_cohort_year], size=T.usd_schemes, replace=False)
    s["currency"] = "AED"
    s.loc[usd, "currency"] = "USD"
    s["segment"] = np.where(s.size0 <= 50, "SME", "CORPORATE")
    return s


def build_members(rng: np.random.Generator, schemes: pd.DataFrame) -> pd.DataFrame:
    """One row per member cover period (member x policy year). Includes post-valuation cohorts (truth only)."""
    last_start = pd.Timestamp(T.last_cohort_start)
    rows = []
    for sc in schemes.itertuples(index=False):
        roster: list[str] = []
        next_id = 0
        size = sc.size0
        sites = sc.site_pool.split(",")[: sc.n_sites]
        for k, year in enumerate(range(sc.first_year, 2026)):
            pstart = pd.Timestamp(year=year, month=sc.renew_month, day=1)
            if pstart > last_start:
                break
            if k > 0 and rng.random() < T.scheme_lapse_rate:
                break
            pend = pstart + pd.DateOffset(years=1) - pd.Timedelta(days=1)
            if k > 0:
                size = max(2, int(round(size * (1 + sc.growth))))
                roster = [m for m in roster if rng.random() < T.member_retention]
            while len(roster) < size:
                next_id += 1
                roster.append(f"{sc.scheme_id}-{next_id:05d}")
            policy_id = f"{sc.scheme_id}-P{year}"
            span = (pend - pstart).days
            for m in roster:
                cs, ce = pstart, pend
                if rng.random() < T.leaver_rate:
                    ce = pstart + pd.Timedelta(days=int(rng.integers(30, span)))
                rows.append((m, policy_id, sc.scheme_id, pstart, pend, cs, ce, sc.plan, sc.segment,
                             sites[int(rng.integers(0, len(sites)))], sc.currency))
            n_join = rng.binomial(size, T.joiner_rate)
            for _ in range(n_join):
                next_id += 1
                m = f"{sc.scheme_id}-{next_id:05d}"
                cs = pstart + pd.Timedelta(days=int(rng.integers(15, span - 15)))
                rows.append((m, policy_id, sc.scheme_id, pstart, pend, cs, pend, sc.plan, sc.segment,
                             sites[int(rng.integers(0, len(sites)))], sc.currency))
                roster.append(m)
    mem = pd.DataFrame(rows, columns=["member_id", "policy_id", "scheme_id", "policy_start", "policy_end",
                                      "cover_start", "cover_end", "plan", "segment", "site", "currency"])
    limits = dict(T.annual_limit)
    base = dict(T.base_premium_2021)
    mem["annual_limit"] = mem.plan.map(limits)
    years_from_2021 = (mem.policy_start.dt.year - 2021) + (mem.policy_start.dt.month - 1) / 12
    days = (mem.cover_end - mem.cover_start).dt.days + 1
    prem_aed = mem.plan.map(base) * (1 + T.premium_trend) ** years_from_2021 * days / 365
    fx = np.where(mem.currency == "USD", T.usd_per_aed, 1.0)
    mem["written_premium"] = (prem_aed * fx).round(2)
    mem["annual_limit"] = (mem.annual_limit * fx).round(0)
    mem["gender"] = rng.choice(["M", "F"], size=len(mem), p=[0.62, 0.38])
    mem["relation"] = rng.choice(["Principal", "Spouse", "Child"], size=len(mem), p=[0.6, 0.15, 0.25])
    age = rng.integers(0, 64, size=len(mem))
    mem["dob"] = (mem.policy_start - pd.to_timedelta(age * 365 + rng.integers(0, 365, size=len(mem)), unit="D"))
    return mem


# --------------------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------------------
def trend_index(dates: pd.Series) -> np.ndarray:
    yrs = (dates - pd.Timestamp("2021-07-01")).dt.days / 365.25
    return (1 + T.claims_trend) ** yrs.to_numpy()


def build_claims(rng: np.random.Generator, mem: pd.DataFrame) -> pd.DataFrame:
    cf = dict(T.plan_cost_factor)
    m = mem.reset_index(drop=True)
    days = ((m.cover_end - m.cover_start).dt.days + 1).to_numpy()
    ly = days / 365.0
    fac = m.plan.map(cf).to_numpy()

    # --- attritional encounter lines
    n_attr = rng.poisson(T.attr_lines_per_ly * ly)
    idx = np.repeat(np.arange(len(m)), n_attr)
    off = (rng.random(len(idx)) * days[idx]).astype(int)
    tdate = m.cover_start.to_numpy()[idx] + off.astype("timedelta64[D]")
    sig = np.sqrt(np.log(1 + T.attr_cv ** 2))
    mu = np.log(T.attr_mean_2021 * fac[idx]) - sig ** 2 / 2
    attr = pd.DataFrame({"mi": idx, "treatment_date": tdate, "kind": "ATTR"})
    attr["fgu_aed"] = rng.lognormal(mu, sig) * trend_index(attr.treatment_date)
    attr["episode"] = -1
    attr["event_id"] = ""
    attr["cause_code"] = ""

    # --- large episodes (each split into 3-8 lines over the stay)
    n_lg = rng.poisson(T.large_freq_per_ly * ly * np.sqrt(fac))
    eidx = np.repeat(np.arange(len(m)), n_lg)
    eoff = (rng.random(len(eidx)) * days[eidx]).astype(int)
    adm = m.cover_start.to_numpy()[eidx] + eoff.astype("timedelta64[D]")
    ep = pd.DataFrame({"mi": eidx, "admission_date": adm})
    xm = T.large_xm_2021 * trend_index(ep.admission_date)
    ep["total_aed"] = xm * (1 - rng.random(len(ep))) ** (-1 / T.large_alpha)
    ep["los"] = rng.integers(1, 45, size=len(ep))
    ep["discharge_date"] = ep.admission_date + pd.to_timedelta(ep.los, unit="D")
    ep["episode"] = np.arange(len(ep))
    ep["event_id"] = ""
    ep["cause_code"] = ""
    ep = pd.concat([ep, build_events(rng, m)], ignore_index=True)
    ep["episode"] = np.arange(len(ep))
    nl = rng.integers(3, 9, size=len(ep))
    li = np.repeat(np.arange(len(ep)), nl)
    w = rng.dirichlet(np.ones(8), size=len(ep))
    share = np.concatenate([w[i, :k] / w[i, :k].sum() for i, k in enumerate(nl)])
    lg = ep.iloc[li].reset_index(drop=True)
    lg["fgu_aed"] = lg.total_aed.to_numpy() * share
    stay = lg.los.to_numpy()
    lg["treatment_date"] = lg.admission_date + pd.to_timedelta((rng.random(len(lg)) * stay).astype(int), unit="D")
    lg["kind"] = np.where(lg.event_id.str.len() > 0, "EVENT", "LARGE")
    lg.loc[lg.cause_code.str.startswith("EVT"), "kind"] = "EVENT"

    cl = pd.concat([attr, lg[["mi", "treatment_date", "kind", "fgu_aed", "episode", "admission_date",
                              "discharge_date", "event_id", "cause_code"]]], ignore_index=True)
    cl = cl.join(m[["member_id", "policy_id", "scheme_id", "policy_start", "cover_start", "cover_end",
                    "currency", "annual_limit", "segment", "site"]], on="mi")
    cl = cl[cl.treatment_date <= cl.cover_end].copy()   # LOS may run past cover end: drop tail lines
    cl.sort_values(["member_id", "policy_id", "treatment_date"], inplace=True)

    # --- per-member annual benefit limit (in transaction currency)
    fx = np.where(cl.currency == "USD", T.usd_per_aed, 1.0)
    cl["amount_true"] = cl.fgu_aed * fx
    cum = cl.groupby(["member_id", "policy_id"]).amount_true.cumsum()
    prev = cum - cl.amount_true
    cl["amount_true"] = np.clip(np.minimum(cum, cl.annual_limit) - np.minimum(prev, cl.annual_limit), 0, None)
    cl = cl[cl.amount_true > 0].copy()

    # --- reporting and payment
    large = cl.kind != "ATTR"
    rep_mean = np.where(large, T.large_report_lag_mean, T.attr_report_lag_mean)
    pay_mean = np.where(large, T.large_pay_lag_mean, T.attr_pay_lag_mean)
    cl["reported_date"] = cl.treatment_date + pd.to_timedelta(rng.exponential(rep_mean).astype(int), unit="D")
    cl["paid_date"] = cl.reported_date + pd.to_timedelta(rng.exponential(pay_mean).astype(int), unit="D")
    cl["claim_id"] = np.where(large, "E" + cl.episode.astype(str), "A" + np.arange(len(cl)).astype(str))
    # each large-episode line keeps its episode as the claim id; attritional lines are their own claims
    cl["icd_code"] = np.where(large, rng.choice(["I21.4", "C50.9", "S06.5", "N18.6", "O60.1", "I63.9"],
                                                size=len(cl)), rng.choice(["J06.9", "K29.7", "M54.5", "Z00.0",
                                                                           "R51", "L30.9"], size=len(cl)))
    cl.reset_index(drop=True, inplace=True)
    cl["claim_line_id"] = "L" + (cl.index + 1).astype(str).str.zfill(7)
    return cl


def build_events(rng: np.random.Generator, m: pd.DataFrame) -> pd.DataFrame:
    """Three accumulation events. EV1/EV3 carry an event_id; EV2 does not (tests clustering)."""
    specs = [
        ("EV2022-01", "2022-11-20", 4, 60_000.0, 1.4, "EVT-FIRE", True),
        ("EV2023-01", "2023-03-14", 7, 60_000.0, 1.3, "EVT-RTA", True),
        ("", "2024-08-02", 18, None, None, "EVT-FOOD", False),
    ]
    out = []
    for eid, date, n, xm, alpha, cause, supplied in specs:
        dt = pd.Timestamp(date)
        live = m[(m.cover_start <= dt) & (m.cover_end >= dt)]
        sizes = live.groupby(["scheme_id", "site"]).size()
        cand = sizes[sizes >= n * 2]
        big = (cand.sample(1, random_state=int(rng.integers(0, 1e9))).index[0] if len(cand)
               else sizes.idxmax())
        pick = live[(live.scheme_id == big[0]) & (live.site == big[1])].sample(n, random_state=int(rng.integers(0, 1e9)))
        if xm is None:
            tot = rng.uniform(5_000, 40_000, size=n)
        else:
            tot = xm * (1 - rng.random(n)) ** (-1 / alpha)
        los = rng.integers(1, 20, size=n)
        out.append(pd.DataFrame({
            "mi": pick.index.to_numpy(), "admission_date": dt, "total_aed": tot, "los": los,
            "discharge_date": dt + pd.to_timedelta(los, unit="D"),
            "event_id": eid if supplied else "", "cause_code": cause,
        }))
    return pd.concat(out, ignore_index=True)


def add_status_noise(rng: np.random.Generator, cl: pd.DataFrame) -> pd.DataFrame:
    """Rejected lines and reversal pairs (observed data only)."""
    cl = cl.copy()
    cl["status_override"] = ""
    attr = cl.index[cl.kind == "ATTR"]
    rej_src = rng.choice(attr, size=int(len(attr) * T.rejected_share), replace=False)
    rej = cl.loc[rej_src].copy()            # extra submitted lines that were declined (not in truth)
    rej["status_override"] = "Rejected"
    rej["claim_line_id"] = rej.claim_line_id + "X"
    rej["claim_id"] = rej.claim_id + "X"
    rev_src = rng.choice(attr, size=int(len(attr) * T.reversal_share), replace=False)
    rev = cl.loc[rev_src].copy()
    rev["status_override"] = "Reversed"
    rev["amount_true"] = -rev.amount_true
    rev["paid_date"] = rev.paid_date + pd.Timedelta(days=10)
    rev["claim_line_id"] = rev.claim_line_id + "R"
    rebook = cl.loc[rev_src].copy()
    rebook["claim_line_id"] = rebook.claim_line_id + "C"
    rebook["paid_date"] = rebook.paid_date + pd.Timedelta(days=12)
    return pd.concat([cl, rej, rev, rebook], ignore_index=True)


def observe(cl: pd.DataFrame) -> pd.DataFrame:
    """Apply the valuation-date view: unreported lines dropped, unpaid lines outstanding."""
    o = cl[(cl.reported_date <= VAL) & (cl.treatment_date <= VAL)].copy()
    unpaid = o.paid_date > VAL
    o["status"] = np.where(unpaid, "Outstanding", "Paid")
    o.loc[o.status_override == "Rejected", "status"] = "Rejected"
    o.loc[o.status_override == "Reversed", "status"] = "Reversed"
    o["amount"] = o.amount_true
    lg_os = unpaid & (o.kind != "ATTR")
    o.loc[lg_os, "amount"] = o.loc[lg_os, "amount_true"] * T.large_case_reserve_factor
    o.loc[o.status == "Rejected", "amount"] = 0.0
    o.loc[unpaid, "paid_date"] = pd.NaT
    o["amount"] = o.amount.round(2)
    return o


# --------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------
def fmt_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    return df


def write_line_mode(out: Path, mem_obs: pd.DataFrame, obs: pd.DataFrame) -> None:
    p = out / "data/synthetic/line"
    p.mkdir(parents=True, exist_ok=True)
    mcols = ["member_id", "policy_id", "scheme_id", "policy_start", "policy_end", "cover_start", "cover_end",
             "plan", "annual_limit", "segment", "site", "currency", "written_premium", "dob", "gender", "relation"]
    fmt_dates(mem_obs[mcols]).to_csv(p / "members.csv", index=False)
    ccols = ["claim_line_id", "claim_id", "member_id", "policy_id", "treatment_date", "admission_date",
             "discharge_date", "reported_date", "paid_date", "amount", "status", "currency", "event_id",
             "cause_code", "icd_code", "segment"]
    c = obs.copy()
    big = c.kind != "ATTR"
    c.loc[~big, ["admission_date", "discharge_date"]] = pd.NaT
    c.loc[c.kind != "EVENT", "cause_code"] = ""
    c = c.sort_values(["treatment_date", "claim_line_id"])
    fmt_dates(c[ccols]).to_csv(p / "claims_lines.csv", index=False)
    pd.DataFrame([{"valuation_date": T.valuation_date, "listing_threshold": 0, "currency_basis": "ORIGINAL",
                   "data_source_note": "Synthetic portfolio, tools/synth/generate.py"}]).to_csv(p / "run_meta.csv", index=False)


def month_floor(s: pd.Series) -> pd.Series:
    return s.dt.to_period("M").dt.to_timestamp()


def exposure_monthly(mem_obs: pd.DataFrame) -> pd.DataFrame:
    months = pd.date_range("2020-01-01", VAL, freq="MS")
    rows = []
    fx = np.where(mem_obs.currency == "USD", 1 / T.usd_per_aed, 1.0)   # monthly summary expressed in AED
    m = mem_obs.assign(prem_aed=mem_obs.written_premium * fx,
                       days=(mem_obs.cover_end - mem_obs.cover_start).dt.days + 1)
    for seg, g in m.groupby("segment"):
        cs, ce = g.cover_start.to_numpy(), g.cover_end.to_numpy()
        pr, dd = g.prem_aed.to_numpy(), g.days.to_numpy()
        for ms in months:
            me = ms + pd.offsets.MonthEnd(0)
            me = min(me, VAL)
            lo = np.maximum(cs, np.datetime64(ms))
            hi = np.minimum(ce, np.datetime64(me))
            ov = np.clip((hi - lo).astype("timedelta64[D]").astype(int) + 1, 0, None)
            mdays = (me - ms).days + 1
            new = (cs >= np.datetime64(ms)) & (cs <= np.datetime64(me))
            rows.append(dict(month=ms, segment=seg, lives_inforce=round(ov.sum() / mdays, 3),
                             lives_new=int(new.sum()), written_premium=round(pr[new].sum(), 2),
                             earned_premium=round((pr * ov / dd).sum(), 2)))
    return pd.DataFrame(rows)


def write_claimant_mode(out: Path, mem_obs: pd.DataFrame, obs: pd.DataFrame, threshold: float = 50_000.0) -> None:
    p = out / "data/synthetic/claimant"
    p.mkdir(parents=True, exist_ok=True)
    o = obs[obs.status != "Rejected"].copy()
    fx = np.where(o.currency == "USD", 1 / T.usd_per_aed, 1.0)
    o["aed"] = o.amount * fx
    o["paid_aed"] = np.where(o.status.isin(["Paid", "Reversed"]), o.aed, 0.0)
    o["os_aed"] = np.where(o.status == "Outstanding", o.aed, 0.0)
    mp = mem_obs.drop_duplicates(["member_id", "policy_id"]).set_index(["member_id", "policy_id"])
    g = o.groupby(["member_id", "policy_id"]).agg(
        paid_amount=("paid_aed", "sum"), os_amount=("os_aed", "sum"), claim_count=("claim_id", "nunique"),
        first_treatment_date=("treatment_date", "min"), event_id=("event_id", "max")).reset_index()
    g = g.join(mp[["scheme_id", "policy_start", "policy_end", "annual_limit", "currency", "site", "segment"]],
               on=["member_id", "policy_id"])
    g["annual_limit"] = np.where(g.currency == "USD", g.annual_limit / T.usd_per_aed, g.annual_limit).round(0)
    g = g[(g.paid_amount + g.os_amount) >= threshold].copy()
    g["period_start"], g["period_end"] = g.policy_start, g.policy_end
    g["cohort_month"] = month_floor(g.policy_start)
    g["currency"] = "AED"
    cols = ["member_id", "scheme_id", "period_start", "period_end", "cohort_month", "first_treatment_date",
            "paid_amount", "os_amount", "claim_count", "annual_limit", "currency", "event_id", "site", "segment"]
    g[["paid_amount", "os_amount"]] = g[["paid_amount", "os_amount"]].round(2)
    fmt_dates(g[cols].sort_values(["period_start", "member_id"])).to_csv(p / "claimants.csv", index=False)
    fmt_dates(exposure_monthly(mem_obs)).to_csv(p / "exposure_monthly.csv", index=False)
    o["incurred_month"] = month_floor(o.treatment_date)
    o["cohort_month"] = month_floor(o.policy_start)
    agg = o.groupby(["incurred_month", "segment", "cohort_month"]).agg(
        paid_amount=("paid_aed", "sum"), os_amount=("os_aed", "sum"), claim_count=("claim_id", "nunique")).reset_index()
    agg[["paid_amount", "os_amount"]] = agg[["paid_amount", "os_amount"]].round(2)
    fmt_dates(agg).to_csv(p / "claims_aggregate_monthly.csv", index=False)
    pd.DataFrame([{"valuation_date": T.valuation_date, "listing_threshold": threshold, "currency_basis": "AED",
                   "data_source_note": "Synthetic portfolio, claimant listing >= 50,000 AED"}]).to_csv(p / "run_meta.csv", index=False)


def write_ete_sample(out: Path, mem_obs: pd.DataFrame, obs: pd.DataFrame, n_schemes: int = 8) -> None:
    """ETE-named tables for a small-scheme sample (column names per ete-dashboard interim pipeline)."""
    p = out / "data/synthetic/ete"
    p.mkdir(parents=True, exist_ok=True)
    sizes = mem_obs.groupby("scheme_id").size().sort_index()
    keep = list(sizes[sizes <= 120].index[:n_schemes])          # small schemes keep the sample light
    mm = mem_obs[mem_obs.scheme_id.isin(keep)].copy()
    payer, tpa = "Fictional Payer PJSC", "FICTPA"
    mm["LifeKey"] = payer + "|" + tpa + "|" + mm.policy_id + "|" + mm.member_id
    dim = pd.DataFrame({
        "LifeKey": mm.LifeKey, "Payer Name": payer, "TPA": tpa, "PolKey": mm.policy_id, "MemKey": mm.member_id,
        "UWY": mm.policy_start.dt.year, "PolicyStart": mm.policy_start, "PolicyEndAdj": mm.policy_end,
        "CovStart": mm.cover_start, "CovEnd": mm.cover_end, "Master Contract ID": mm.scheme_id,
        "Policy Type": mm.segment, "Plan / Category": mm.plan, "Currency": mm.currency,
        "WrittenPrem_Gross": mm.written_premium, "WrittenPrem_Net": (mm.written_premium * 0.85).round(2),
        "WrittenPrem_GrossNet": (mm.written_premium * 0.9).round(2),
    })
    fmt_dates(dim).to_csv(p / "dim_life.csv", index=False)
    rows = []
    for r in mm.itertuples(index=False):
        end = min(r.cover_end, VAL)
        for ms in pd.date_range(month_floor(pd.Series([r.cover_start]))[0], end, freq="MS"):
            lo, hi = max(ms, r.cover_start), min(ms + pd.offsets.MonthEnd(0), end)
            ed = (hi - lo).days + 1
            if ed <= 0:
                continue
            days = (r.cover_end - r.cover_start).days + 1
            rows.append((f"{payer}|{tpa}|{r.policy_id}|{r.member_id}", r.policy_start.year, payer, tpa, ms, ed,
                         ed / 365, ed / 365, r.written_premium * ed / days, r.written_premium * 0.85 * ed / days))
    fmm = pd.DataFrame(rows, columns=["LifeKey", "UWY", "Payer Name", "TPA", "MonthStart", "EarnedDays",
                                      "EarnedLY_Gross", "EarnedLY_Net", "EarnedPrem_Gross", "EarnedPrem_Net"])
    num = ["EarnedLY_Gross", "EarnedLY_Net", "EarnedPrem_Gross", "EarnedPrem_Net"]
    fmm[num] = fmm[num].round(6)
    fmt_dates(fmm).to_csv(p / "fact_member_month.csv", index=False)
    oc = obs[obs.scheme_id.isin(keep)].copy()
    oc["LifeKey"] = payer + "|" + tpa + "|" + oc.policy_id + "|" + oc.member_id
    fc = pd.DataFrame({
        "ClaimKey": oc.claim_line_id, "Payer Name": payer, "TPA": tpa, "UWY": oc.policy_start.dt.year,
        "LifeKey": oc.LifeKey, "PolKey": oc.policy_id, "MemKey": oc.member_id, "ClaimID": oc.claim_id,
        "MatchStatus": "Matched", "InCover": 1, "InExperience": 1, "StatusClass": oc.status,
        "TreatmentDate": oc.treatment_date, "IncurredMonth": month_floor(oc.treatment_date),
        "ReportedMonth": month_floor(oc.reported_date), "PaidMonth": month_floor(oc.paid_date),
        "PayerShare": oc.amount, "Paid": np.where(oc.status.isin(["Paid", "Reversed"]), oc.amount, 0.0),
        "OS": np.where(oc.status == "Outstanding", oc.amount, 0.0), "ICD Code": oc.icd_code,
    })
    fmt_dates(fc).to_csv(p / "fact_claims.csv", index=False)
    (p / "run_manifest_claims.csv").write_text(          # Merit's run manifest format (Key / Value)
        "Key,Value\n"f"valuation_date,{VAL.date().isoformat()}\nclaims_extract_date,{VAL.date().isoformat()}\n")


def write_templates(out: Path) -> None:
    p = out / "data/templates"
    p.mkdir(parents=True, exist_ok=True)
    heads = {
        "line_members.csv": "member_id,policy_id,scheme_id,policy_start,policy_end,cover_start,cover_end,plan,annual_limit,segment,site,currency,written_premium,dob,gender,relation",
        "line_claims_lines.csv": "claim_line_id,claim_id,member_id,policy_id,treatment_date,admission_date,discharge_date,reported_date,paid_date,amount,status,currency,event_id,cause_code,icd_code,segment",
        "claimant_claimants.csv": "member_id,scheme_id,period_start,period_end,cohort_month,first_treatment_date,paid_amount,os_amount,claim_count,annual_limit,currency,event_id,site,segment",
        "claimant_exposure_monthly.csv": "month,segment,lives_inforce,lives_new,written_premium,earned_premium",
        "claimant_claims_aggregate_monthly.csv": "incurred_month,segment,cohort_month,paid_amount,os_amount,claim_count",
        "run_meta.csv": "valuation_date,listing_threshold,currency_basis,data_source_note",
    }
    for f, h in heads.items():
        (p / f).write_text(h + "\n")


# --------------------------------------------------------------------------------------
# Controls and truth
# --------------------------------------------------------------------------------------
def to_aed(df: pd.DataFrame, col: str) -> np.ndarray:
    return np.where(df.currency == "USD", df[col] / T.usd_per_aed, df[col])


def xl(x: np.ndarray, ap: float, lim: float) -> np.ndarray:
    return np.clip(x - ap, 0, lim)


def per_person_stats(cl: pd.DataFrame, basis: str) -> dict:
    """Per-person accumulation (AED as-if at the generator's fixed rate). basis: RA (policy year) or LO (calendar)."""
    c = cl.assign(aed=to_aed(cl, "amount_true"))
    if basis == "RA":
        c["yr"] = c.policy_start.dt.year
        g = c.groupby(["yr", "member_id", "policy_id"]).aed.sum().reset_index()
    else:
        c["yr"] = c.treatment_date.dt.year
        g = c.groupby(["yr", "member_id"]).aed.sum().reset_index()
    res = {}
    for yr, gg in g.groupby("yr"):
        x = gg.aed.to_numpy()
        r = {"total": round(float(x.sum()), 2)}
        for t in THRESHOLDS:
            r[f"n_gt_{int(t)}"] = int((x > t).sum())
        for name, (ap, lim) in REF_LAYERS.items():
            v = xl(x, ap, lim)
            r[f"{name}_amount"] = round(float(v.sum()), 2)
            r[f"{name}_count"] = int((v > 0).sum())
        res[int(yr)] = r
    return res


def build_truth(cl_all: pd.DataFrame, cl_obs_true: pd.DataFrame, mem_all: pd.DataFrame) -> dict:
    """Ultimate (all claims on all cohorts incl. post-valuation) vs observed components, per basis."""
    written = cl_all[cl_all.policy_start <= VAL]
    earned = written[written.treatment_date <= VAL]
    return {
        "parameters": asdict(T),
        "reference_layers_aed": {k: {"ap": v[0], "limit": v[1]} for k, v in REF_LAYERS.items()},
        "per_person": {
            basis: {
                "ultimate_all_cohorts": per_person_stats(cl_all, basis),
                "written_cohorts_ultimate": per_person_stats(written, basis),
                "earned_ultimate": per_person_stats(earned, basis),
                "reported_at_valuation_true_amount": per_person_stats(cl_obs_true, basis),
            } for basis in ("RA", "LO")
        },
        "notes": [
            "ultimate_all_cohorts - written_cohorts_ultimate = unwritten component (RA cohorts starting after valuation).",
            "written_cohorts_ultimate - earned_ultimate = unearned component (treatment after valuation on written policies).",
            "earned_ultimate - reported_at_valuation = IBNR on earned exposure (unreported lines).",
            "Observed data additionally under-reserves OS large lines by large_case_reserve_factor (IBNER).",
            "Amounts in AED at the generator's fixed USD rate, after benefit limits, before any treaty terms.",
        ],
        "events": {"EV2022-01": "supplied event_id, fire, 4 lives", "EV2023-01": "supplied event_id, RTA, 7 lives",
                   "unlabelled_2024-08-02": "no event_id, food poisoning, 18 lives same scheme/site (cause_code EVT-FOOD)"},
    }


def controls(out: Path) -> dict:
    res = {}
    for f in sorted((out / "data/synthetic").rglob("*.csv")):
        df = pd.read_csv(f, low_memory=False, dtype={"event_id": str, "cause_code": str})
        rel = str(f.relative_to(out))
        entry = {"rows": int(len(df)), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
        for col in ("amount", "written_premium", "paid_amount", "os_amount", "earned_premium", "PayerShare",
                    "EarnedLY_Gross", "lives_inforce"):
            if col in df.columns:
                entry[f"sum_{col}"] = round(float(df[col].sum()), 2)
        if "treatment_date" in df.columns and "amount" in df.columns:
            df["yr"] = df.treatment_date.str[:4]
            entry["amount_by_treatment_year"] = {k: round(float(v), 2) for k, v in df.groupby("yr").amount.sum().items()}
            entry["rows_by_status"] = {k: int(v) for k, v in df.status.value_counts().items()}
        res[rel] = entry
    return res


# --------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2])
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    out = a.out

    schemes = build_schemes(rng)
    mem_all = build_members(rng, schemes)
    cl_all = build_claims(rng, mem_all)
    cl_noise = add_status_noise(rng, cl_all)
    obs = observe(cl_noise)

    mem_obs = mem_all[mem_all.policy_start <= VAL].copy()
    # joiners/leavers after valuation are not yet known
    mem_obs = mem_obs[mem_obs.cover_start <= VAL].copy()
    future_leave = (mem_obs.cover_end < mem_obs.policy_end) & (mem_obs.cover_end > VAL)
    mem_obs.loc[future_leave, "cover_end"] = mem_obs.loc[future_leave, "policy_end"]
    mem_obs.sort_values(["policy_start", "member_id"], inplace=True)

    write_line_mode(out, mem_obs, obs)
    write_claimant_mode(out, mem_obs, obs)
    write_ete_sample(out, mem_obs, obs)
    write_templates(out)

    rec = out / "tests/reconciliation"
    rec.mkdir(parents=True, exist_ok=True)
    obs_true = cl_all[cl_all.claim_line_id.isin(obs.claim_line_id)]
    (rec / "synthetic_truth.json").write_text(json.dumps(build_truth(cl_all, obs_true, mem_all), indent=1, default=str))
    (rec / "synthetic_controls.json").write_text(json.dumps({"seed": a.seed, "valuation_date": T.valuation_date,
                                                             "files": controls(out)}, indent=1))
    print(f"schemes={len(schemes)} member_periods_obs={len(mem_obs)} claim_lines_obs={len(obs)}")


if __name__ == "__main__":
    main()
