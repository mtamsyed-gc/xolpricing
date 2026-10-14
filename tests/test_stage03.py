"""Stage 03 in the simulator vs an independent pandas implementation of docs/methodology.md (line mode, RA).

Inputs for the oracle: the stage-02 staging tables and the synthetic Control workbook read directly with pandas.
"""
from __future__ import annotations

import math
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
import build_stage03  # noqa: E402
import sim  # noqa: E402

STAGE = ROOT / "data/output/synth/stage"
RES = ROOT / "data/output/synth/results"
CTL = ROOT / "data/synthetic/XoL_Control_synthetic.xlsx"
BIG = 1e15
results = []


def load(name):
    with open(STAGE / f"{name}.yxdb", "rb") as f:
        fields, rows = pickle.load(f)
    return pd.DataFrame(rows, columns=list(fields))


def sheet(name):
    return pd.read_excel(CTL, sheet_name=name, header=3).dropna(how="all")


def close(name, got, exp, tol=1e-6):
    got, exp = np.asarray(got, dtype=float), np.asarray(exp, dtype=float)
    ok = got.shape == exp.shape and np.allclose(got, exp, rtol=tol, atol=1e-6, equal_nan=True)
    results.append((name, ok))
    if not ok:
        print(f"  MISMATCH {name}\n    got {got[:8]}\n    exp {exp[:8]}")


def oracle():
    prm = sheet("Params").set_index("param_id").value
    q = int(prm["quote_year"])
    years = [int(y) for y in str(prm["years_included"]).split(",")]
    thr_pct = float(prm["output_threshold_pct_min_ap"])
    k_iqr = float(prm["iqr_multiplier"])
    full_n = float(prm["full_cred_n"])
    epi = float(prm["epi_quote_year"])

    tr = sheet("Trend").set_index("year")
    t = tr.residual_pct.fillna(0)
    tp = tr.premium_trend_pct.fillna(0)
    idx_c = (1 + t).cumprod() / (1 + t.iloc[0])
    idx_p = (1 + tp).cumprod() / (1 + tp.iloc[0])
    trend_f = idx_c[q] / idx_c
    trend_f1 = trend_f / (1 + t[q])
    prem_f = idx_p[q] / idx_p

    cp = sheet("Completion")
    cp = cp[cp.ap_band_from.fillna(0) == 0]
    prod = lambda side: cp[cp.side == side].groupby("year").selected_factor.prod()  # noqa: E731
    pre, post_a, post_n = prod("PRE_AP"), prod("POST_AP_AMOUNT"), prod("POST_AP_COUNT")

    lay = sheet("Layers")
    lay = lay[(lay.cover == "PP") & (lay.active == 1)].copy()
    min_ap = lay.ap.min()

    u = load("exp_claimants")
    u["trend_f"] = u.ty.map(trend_f)
    u["pre"] = u.ty.map(pre).fillna(1)
    cap = u.annual_limit.fillna(BIG)
    u["adj"] = np.minimum(u.amount_q * u.trend_f * u.pre, cap)
    u["adj1"] = np.minimum(u.amount_q * u.ty.map(trend_f1) * u.pre, cap)
    big = np.sort(u.loc[u.adj >= thr_pct * min_ap, "adj"].to_numpy())
    n = len(big)
    rank = lambda p: int(p * n) if (p * n).is_integer() else math.floor(p * n) + 1  # noqa: E731
    q1, q3 = big[rank(0.25) - 1], big[rank(0.75) - 1]
    othr = q3 + k_iqr * (q3 - q1)
    u["outlier"] = u.adj > othr

    ex = load("exp_exposure_ty").set_index("ty")
    w = sheet("Weights").set_index("year")
    base = {5: [0.10, 0.15, 0.20, 0.30, 0.25]}[len(years)]
    wraw = pd.Series(base, index=years) * w.months_complete.reindex(years).fillna(12) / 12
    wt = wraw / wraw.sum()

    out = []
    for _, L in lay.iterrows():
        lim = (u.annual_limit.fillna(BIG) - L.ap) if L.limit_basis == "TO_BENEFIT_LIMIT" else L.limit
        xl = np.clip(np.minimum(u.adj - L.ap, lim), 0, None)
        xl1 = np.clip(np.minimum(u.adj1 - L.ap, lim), 0, None)
        xl = np.where(u.adj - L.ap <= 0, 0, xl)
        d = pd.DataFrame({"ty": u.ty, "xl": xl, "xl1": xl1, "n": (xl > 0).astype(float), "out": u.outlier})
        d["xl_c"] = d.xl * d.ty.map(post_a).fillna(1)
        d["n_c"] = d.n * d.ty.map(post_n).fillna(1)
        g = d.groupby("ty").agg(xl=("xl", "sum"), xl_c=("xl_c", "sum"), n=("n", "sum"), n_c=("n_c", "sum"),
                                xl1=("xl1", "sum")).reindex(years, fill_value=0)
        g["xl_non"] = d[~d.out].groupby("ty").xl_c.sum().reindex(years, fill_value=0)
        g["xl_out"] = d[d.out].groupby("ty").xl_c.sum().reindex(years, fill_value=0)
        ely = ex.exposure_ly_full.reindex(years)
        prem = ex.exposure_prem_full.reindex(years) * prem_f.reindex(years)
        g["bc_non"], g["bc_out"] = g.xl_non / ely, g.xl_out / ely
        g["bc"] = g.bc_non + g.bc_out
        g["pct"] = g.xl_c / prem
        g["freq"] = g.n_c / ely * 1000
        nyrs = len(years)
        bc_tot = (wt * g.bc_non).sum() + (g.bc_out / nyrs).sum()
        pct_w = (wt * g.xl_non / prem).sum() + (g.xl_out / prem / nyrs).sum()
        sev = g.xl_c.sum() / g.n_c.sum() if g.n_c.sum() else 0
        lives = epi / (ex.exposure_prem_full[years[-1]] / ex.exposure_ly_full[years[-1]] * prem_f[years[-1]])
        z = min(1.0, math.sqrt(g.n.sum() / full_n))
        out.append(dict(option_id=L.option_id, bc_pppy_total=bc_tot, bc_pct_prem_weighted=pct_w,
                        credibility_z=z, n_xl_claims=g.n.sum(), sens_straight_mean=g.bc.mean(),
                        sens_wfreq_x_sev=(wt * g.freq).sum() / 1000 * sev,
                        sens_sfreq_x_sev=g.freq.mean() / 1000 * sev, bc_min=g.bc.min(), bc_max=g.bc.max(),
                        lev_trend=g.xl.sum() / g.xl1.sum() - 1, lives_quote_year=lives,
                        expected_cost_det=bc_tot * lives, outlier_thr=othr, by_year=g, weights=wt))
    return out


def main() -> int:
    for b in (build_stage00.build(C.SYNTH), build_stage01.build_line(C.SYNTH), build_stage02.build(C.SYNTH),
              build_stage03.build(C.SYNTH)):
        sim.run(b)
        results.append((f"{b.stem}: no STOP", not [m for m in sim.MESSAGES if m[0] == "Error"]))
    exp = oracle()
    got = pd.read_csv(RES / "out_bc_summary.csv").set_index("option_id")
    by = pd.read_csv(RES / "out_bc_by_year.csv")
    for e in exp:
        o = int(e["option_id"])
        g = got.loc[o]
        for col in ("bc_pppy_total", "bc_pct_prem_weighted", "credibility_z", "n_xl_claims", "sens_straight_mean",
                    "sens_wfreq_x_sev", "sens_sfreq_x_sev", "bc_min", "bc_max", "lev_trend", "lives_quote_year",
                    "expected_cost_det", "outlier_thr"):
            close(f"option {o} {col}", [g[col]], [e[col]])
        yb = by[by.option_id == o].set_index("ty")
        close(f"option {o} xl_c by TY", yb.xl_c, e["by_year"].xl_c)
        close(f"option {o} bc_pppy by TY", yb.bc_pppy, e["by_year"].bc)
        close(f"option {o} weights", yb.weight, e["weights"])

    for n, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {n}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
