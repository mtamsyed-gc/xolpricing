"""Stage 04 in the simulator vs an independent numpy implementation (same MINSTD streams, so results are exact).

Runs stages 00-04 on a copy of the synthetic Control workbook with 500 iterations (variant T4) for speed.
Also checks the statistical sense of the result: alpha near the generator's 1.6, the RNG is uniform, and the
stochastic mean is close to the deterministic burning cost.
"""
from __future__ import annotations

import math
import pickle
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/alteryx"))
import xol_common as C  # noqa: E402
import build_stage00, build_stage01, build_stage02, build_stage03, build_stage04, build_stage04a  # noqa: E401,E402
import sim  # noqa: E402

N_ITER = 500
OUT = ROOT / "data/output/t4"
CTL = OUT / "XoL_Control_t4.xlsx"
V = C.Variant("T4", OUT / "wf", "..\\..\\..\\..\\", "data/output/t4/XoL_Control_t4.xlsx",
              "data/synthetic/line", "data/synthetic/claimant", "data/output/t4")
M, A = 2147483647, 48271
results = []


def load(name):
    with open(OUT / "stage" / f"{name}.yxdb", "rb") as f:
        fields, rows = pickle.load(f)
    return pd.DataFrame(rows, columns=list(fields))


def close(name, got, exp, tol=1e-9):
    got, exp = np.asarray(got, dtype=float), np.asarray(exp, dtype=float)
    ok = got.shape == exp.shape and np.allclose(got, exp, rtol=tol, atol=1e-6, equal_nan=True)
    results.append((name, ok))
    if not ok:
        print(f"  MISMATCH {name}\n    got {got.ravel()[:6]}\n    exp {exp.ravel()[:6]}")


def minstd(start, n):
    out, x = np.empty(n), start
    for i in range(n):
        x = (A * x) % M
        out[i] = x
    return out / M


KSEV, DEC = 50, 10        # severity grid: points per decade, decades (tools/python/sevfit.py)
TAB = None                 # a stage-04a grid to use instead of the staged one (Designer twin: its out_sev_table.csv)


def severity(u2, u, alpha, src, tab):
    """NATIVE: the Pareto in closed form. PYTHON: ln x interpolated in log10 U on the stage-04a grid."""
    if src == "NATIVE":
        return u * u2 ** (-1 / alpha)
    tab = tab.sort_values("k")
    lx0, lx1 = tab.lx0.to_numpy(), tab.lx1.to_numpy()
    t = -np.log10(u2) * KSEV
    k = np.minimum(np.floor(t), KSEV * DEC - 1).astype(int)
    return np.exp(lx0[k] + (t - k) * (lx1[k] - lx0[k]))


def oracle(tab=None):
    """tab: the stage-04a severity grid to use (default: the staged sev_table)."""
    prm = pd.read_excel(CTL, sheet_name="Params", header=3).set_index("param_id").value
    seed, iters = int(prm["seed"]), int(prm["iterations"])
    inc = float(prm["incidence_adj"])
    lay = pd.read_excel(CTL, sheet_name="Layers", header=3)
    lay = lay[(lay.cover == "PP") & (lay.active == 1)].copy()
    u = float(prm["fit_cutoff"]) if not pd.isna(prm.get("fit_cutoff")) else lay.ap.min()
    s0 = seed % (M - 1) + 1
    starts = {j: (pow(A, j * (1 << 29), M) * s0) % M for j in (1, 2, 3, 4, 5)}

    adj = load("bc_adjusted")
    fu = adj[adj.adj > u].copy()
    min_ap = lay.ap.min()
    mode = "STOP" if pd.isna(prm.get("below_cutoff")) or str(prm.get("below_cutoff")) == "" else str(prm["below_cutoff"])
    bu = adj[(adj.adj > min_ap) & (adj.adj <= u)].sort_values("acc_key")      # 0.9.0 band (min AP, u]
    unc = fu.adj_uncapped < fu.cap
    alpha = unc.sum() / np.log(fu.adj / u).sum()
    caps = fu.sort_values("acc_key").cap.to_numpy()
    big_m = fu.adj.max()

    w = load("bc_weights").set_index("g_ty").weight
    ex = load("exp_exposure_ty").set_index("ty").exposure_ly_full
    n_u = fu.groupby("ty").post_cnt_f_.sum()
    freq = (n_u.reindex(w.index, fill_value=0) / ex.reindex(w.index)) * w
    lives = load("bc_summary").lives_quote_year.max()
    lam = freq.sum() * lives * inc
    n_b = bu.groupby("ty").post_cnt_f_.sum()
    lam_b = ((n_b.reindex(w.index, fill_value=0) / ex.reindex(w.index)) * w).sum() * lives * inc if u > min_ap else 0.0
    band_on = mode == "EMPIRICAL" and u > min_ap and len(bu) > 0

    n_y, e_y = n_u.reindex(w.index, fill_value=0), ex.reindex(w.index)
    m_pool = n_y.sum() / e_y.sum()
    phi = ((n_y - e_y * m_pool) ** 2 / (e_y * m_pool)).sum() / (len(w) - 1) if len(w) > 1 else 1.0
    nb = prm["freq_model"] == "NEGBIN" and phi > 1
    k_max = math.ceil(lam + 10 * math.sqrt(lam * (phi if nb else 1)) + 20)
    if nb:
        r, qq = lam / (phi - 1), 1 / phi
        pk = [math.exp(r * math.log(qq))]
        for k in range(1, k_max + 1):
            pk.append(pk[-1] * (r + k - 1) / k * (1 - qq))
    else:
        pk = [math.exp(-lam)]
        for k in range(1, k_max + 1):
            pk.append(pk[-1] * lam / k)
    cdf = np.cumsum(pk)
    u1 = minstd(starts[1], iters)
    n_claims = np.searchsorted(cdf, u1, side="left")       # #{k: cdf_k < U}
    tot = int(n_claims.sum())
    u2, u3 = minstd(starts[2], tot), minstd(starts[3], tot)
    src = "NATIVE" if pd.isna(prm.get("severity_source")) else str(prm["severity_source"])
    tab = tab if tab is not None else TAB
    x = severity(u2, u, alpha, src, load("sev_table") if tab is None and src == "PYTHON" else tab)
    cap = caps[np.floor(u3 * len(caps)).astype(int)]
    claim = np.minimum(x, cap)
    below = np.minimum(claim, big_m)
    it_idx = np.repeat(np.arange(iters), n_claims)
    src_flag = np.zeros(len(claim), dtype=int)
    # band claims (streams 4 and 5): Poisson(lambda_band) counts, empirical (amount, cap) draws from the band units
    lam_eff = lam_b if band_on else 0.0
    k_max_b = math.ceil(lam_eff + 10 * math.sqrt(lam_eff) + 20)
    pkb = [math.exp(-lam_eff)]
    for k in range(1, k_max_b + 1):
        pkb.append(pkb[-1] * lam_eff / k)
    u4 = minstd(starts[4], iters)
    n_b_claims = np.searchsorted(np.cumsum(pkb), u4, side="left")
    tot_b = int(n_b_claims.sum())
    if tot_b:
        u5 = minstd(starts[5], tot_b)
        pick = np.floor(u5 * len(bu)).astype(int)
        b_claim = bu.adj.to_numpy()[pick]
        b_cap = bu.cap.to_numpy()[pick]
        claim = np.concatenate([claim, b_claim]); cap = np.concatenate([cap, b_cap])
        below = np.concatenate([below, np.minimum(b_claim, big_m)])
        it_idx = np.concatenate([it_idx, np.repeat(np.arange(iters), n_b_claims)])
        src_flag = np.concatenate([src_flag, np.ones(tot_b, dtype=int)])

    out = {}
    for _, L in lay.iterrows():
        if L.limit_basis == "TO_BENEFIT_LIMIT":
            lim = np.where(cap >= 1e15, 1e15, cap - L.ap)
        else:
            lim = np.full_like(claim, L.limit if not pd.isna(L.limit) else 1e15)
        cop = 0 if pd.isna(L.co_participation_pct) else L.co_participation_pct

        def loss(c):
            over = c - L.ap
            return np.where(over <= 0, 0, np.where(over < lim, over, np.where(lim > 0, lim, 0))) * (1 - cop)
        xl = np.bincount(it_idx, weights=loss(claim), minlength=iters)
        xlb = np.bincount(it_idx, weights=loss(below), minlength=iters)
        xlband = np.bincount(it_idx, weights=loss(claim) * (src_flag == 1), minlength=iters)
        aad = 0 if pd.isna(L.aad) else L.aad
        n_r = L.reinstatements_n
        agg = L.aal if not pd.isna(L.aal) else (1e15 if pd.isna(n_r) or pd.isna(L.limit) else (1 + n_r) * L.limit)
        rec = np.minimum(np.maximum(xl - aad, 0), agg)
        f = np.where(xl > 0, rec / np.where(xl > 0, xl, 1), 1)
        cost = L.reinstatement_cost_pct
        ru = (np.zeros(iters) if pd.isna(cost) or pd.isna(L.limit) else
              cost * (rec if pd.isna(n_r) else np.minimum(rec, n_r * L.limit)) / L.limit)
        band_det = 0.0
        if u > min_ap and len(bu):
            bl = (np.where(bu.cap.to_numpy() >= 1e15, 1e15, bu.cap.to_numpy() - L.ap) if L.limit_basis == "TO_BENEFIT_LIMIT"
                  else np.full(len(bu), L.limit if not pd.isna(L.limit) else 1e15))
            bo = bu.adj.to_numpy() - L.ap
            band_det = lam_b * float(np.mean(np.where(bo <= 0, 0, np.where(bo < bl, bo, np.where(bl > 0, bl, 0))) * (1 - cop)))
        out[int(L.option_id)] = dict(rec=rec, above=(xl - xlb) * f, ru=ru, band=xlband * f, band_raw=xlband, band_det=band_det)
    tail_mask = src_flag == 0
    return dict(src=src, x=x, u2=u2, alpha=alpha, lam=lam, phi=phi, nb=nb, n_claims=n_claims, u1=u1, out=out,
                claims_sum=claim[tail_mask].sum(), claim=claim, it_idx=it_idx, iters=iters, u=u, lives=lives,
                lam_b=lam_b, band_on=band_on, n_band=len(bu), min_ap=min_ap, mode=mode, n_b_claims=n_b_claims)


def negbin():
    """freq_model NEGBIN: the synthetic yearly counts are overdispersed (phi > 1), so the count draws follow a negative
    binomial with mean lambda and variance phi x lambda; exact match to the oracle."""
    global OUT, CTL
    keep = OUT, CTL
    OUT = ROOT / "data/output/t4n"
    CTL = OUT / "XoL_Control_t4n.xlsx"
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy(keep[1], CTL)
    wb = load_workbook(CTL)
    ws = wb["Params"]
    for r in range(5, ws.max_row + 1):
        if ws.cell(r, 1).value == "freq_model":
            ws.cell(r, 3).value = "NEGBIN"
        if ws.cell(r, 1).value == "severity_source":       # this variant also covers the native severity path
            ws.cell(r, 3).value = "NATIVE"
    wb.save(CTL)
    vn = C.Variant("T4N", OUT / "wf", "..\\..\\..\\..\\", "data/output/t4n/XoL_Control_t4n.xlsx",
                   "data/synthetic/line", "data/synthetic/claimant", "data/output/t4n")
    for b in (build_stage00.build(vn), build_stage01.build_line(vn), build_stage02.build(vn), build_stage03.build(vn),
              build_stage04.build(vn)):          # no 04a: the native path needs no fit
        sim.run(b)
    e = oracle()
    summ0 = pd.read_csv(OUT / "results/out_sim_summary.csv")
    results.append((f"NATIVE severity without 04a ({summ0.sev_dist.iloc[0]})", e["src"] == "NATIVE" and
                    (summ0.sev_dist == "PARETO (native)").all()))
    summ = pd.read_csv(OUT / "results/out_sim_summary.csv").set_index("option_id")
    its = pd.read_csv(OUT / "results/out_sim_iterations.csv")
    results.append((f"NEGBIN used (phi {e['phi']:.2f})", e["nb"] and (summ.freq_dist == "NEGBIN").all()))
    close("NEGBIN total claims", [pd.read_csv(OUT / "checks/recon_04.csv").set_index(["table", "measure"]).value[
        ("iterations", "claims")]], [e["n_claims"].sum()])
    for o, d in e["out"].items():
        g = its[its.option_id == o].sort_values("iter")
        close(f"NEGBIN option {o} recovery by iteration", g.recovery, d["rec"])
    n = e["n_claims"]
    results.append((f"NEGBIN counts: mean {n.mean():.1f} vs lambda {e['lam']:.1f}, variance / mean {n.var() / n.mean():.2f}"
                    f" vs phi {e['phi']:.2f}", abs(n.mean() / e["lam"] - 1) < 0.05 and abs(n.var() / n.mean() / e["phi"] - 1) < 0.3))
    OUT, CTL = keep


def variant(name, edit):
    """A copy of the main control (t4) with edit(Params sheet) applied, run 00-04 in a new folder."""
    global OUT, CTL
    keep = OUT, CTL
    OUT = ROOT / f"data/output/{name}"
    CTL = OUT / f"XoL_Control_{name}.xlsx"
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy(keep[1], CTL)
    wb = load_workbook(CTL)
    edit(wb["Params"])
    wb.save(CTL)
    v = C.Variant(name.upper(), OUT / "wf", "..\\..\\..\\..\\", f"data/output/{name}/XoL_Control_{name}.xlsx",
                  "data/synthetic/line", "data/synthetic/claimant", f"data/output/{name}")
    return keep, v


def set_p(ws, pid, value):
    for r in range(5, ws.max_row + 1):
        if ws.cell(r, 1).value == pid:
            ws.cell(r, 3).value = value


def gpd():
    """fit_selected = GPD: stage 04 draws from the GPD grid of 04a; exact match to the oracle on that grid, and the
    draws follow the fitted GPD (conditional survival at the drawn values is uniform)."""
    global OUT, CTL
    keep, v = variant("t4g", lambda ws: set_p(ws, "fit_selected", "GPD"))
    for b in (build_stage00.build(v), build_stage01.build_line(v), build_stage02.build(v), build_stage03.build(v),
              build_stage04a.build(v), build_stage04.build(v)):
        sim.run(b)
        results.append((f"t4g {b.stem}: no STOP", not [m for m in sim.MESSAGES if m[0] == "Error"]))
    e = oracle()
    its = pd.read_csv(OUT / "results/out_sim_iterations.csv")
    summ = pd.read_csv(OUT / "results/out_sim_summary.csv")
    cand = pd.read_csv(OUT / "results/out_fit_candidates.csv").set_index("dist")
    results.append(("GPD selected by fit_selected, used by stage 04", (summ.sev_dist == "GPD").all() and
                    cand.loc["GPD", "selected"] == 1 and cand.selected.sum() == 1))
    for o, d in e["out"].items():
        g = its[its.option_id == o].sort_values("iter")
        close(f"GPD option {o} recovery by iteration", g.recovery, d["rec"])
    xi, sg = cand.loc["GPD", "p1"], cand.loc["GPD", "p2"]
    s_x = (1 + xi * (e["x"] - e["u"]) / sg) ** (-1 / xi)        # fitted conditional survival at the draws
    close("GPD draws: conditional survival at X = U (grid interpolation)", s_x, e["u2"], tol=2e-3)
    OUT, CTL = keep


def band():
    """0.9.0 below_cutoff: with fit_cutoff above two layers' attachments, STOP stops (S08); IGNORE runs with S09 and
    its result equals the oracle without band claims; EMPIRICAL simulates the band (S10), equals the oracle with band
    claims on streams 4 and 5, its band share of the mean matches the deterministic band cost within Monte Carlo
    error, and the layers attaching at or above the cut-off are unchanged between IGNORE and EMPIRICAL."""
    global OUT, CTL
    msgs = {}
    for mode in ("STOP", "IGNORE", "EMPIRICAL"):
        keep, v = variant(f"t4b{mode[0].lower()}", lambda ws, m=mode: (set_p(ws, "fit_cutoff", "150000"), set_p(ws, "below_cutoff", m)))
        for b in (build_stage00.build(v), build_stage01.build_line(v), build_stage02.build(v), build_stage03.build(v),
                  build_stage04a.build(v)):
            sim.run(b)
        try:
            sim.run(build_stage04.build(v))
            msgs[mode] = [m for m in sim.MESSAGES if m[0] == "Error"]
        except sim.SimError as ex:
            msgs[mode] = [("Error", str(ex))]
        chk = pd.read_csv(OUT / "checks/checks_04.csv")
        if mode == "STOP":
            results.append(("STOP: S08 stops, one per layer attaching below the cut-off (2)",
                            any("S08" in m[1] for m in msgs[mode]) and (chk.code == "S08").sum() == 2 and
                            (chk[chk.code == "S08"].severity == "STOP").all()))
        else:
            e = oracle()
            summ = pd.read_csv(OUT / "results/out_sim_summary.csv").set_index("option_id")
            its = pd.read_csv(OUT / "results/out_sim_iterations.csv")
            results.append((f"{mode}: no STOP", not msgs[mode]))
            results.append((f"{mode}: band frequency {e['lam_b']:.2f} = lambda above u + band (oracle)", abs(summ.lambda_band.iloc[0] - e["lam_b"]) < 1e-9
                            and summ.below_cutoff.iloc[0] == mode and summ.band_from.iloc[0] == e["min_ap"] and summ.n_band.iloc[0] == e["n_band"]))
            for o, d in e["out"].items():
                g = its[its.option_id == o].sort_values("iter")
                close(f"{mode} option {o} recovery by iteration", g.recovery, d["rec"])
                close(f"{mode} option {o} band recovery by iteration", g.recovery_band, d["band"])
            code = "S09" if mode == "IGNORE" else "S10"
            results.append((f"{mode}: {code} on the two layers attaching below the cut-off", (chk.code == code).sum() == 2))
            if mode == "IGNORE":
                results.append(("IGNORE: no band claims simulated", e["n_b_claims"].sum() == 0 and summ.mean_recovery_band.abs().max() == 0))
                for o, d in e["out"].items():
                    m = chk[(chk.code == "S09") & (chk.item == summ.loc[o, "l_label"])]
                    if len(m):
                        results.append((f"IGNORE option {o}: S09 quotes the deterministic band cost {d['band_det']:,.0f}",
                                        f"{d['band_det']:.0f}" in m.message.iloc[0]))
                ign = summ
            else:
                results.append(("EMPIRICAL: band claims simulated", e["band_on"] and e["n_b_claims"].sum() > 0))
                for o, d in e["out"].items():
                    if d["band_det"] > 0:        # band loss before the annual AAD / AAL (option 1 carries an AAD in this test)
                        se = d["band_raw"].std() / math.sqrt(len(d["band_raw"]))
                        results.append((f"EMPIRICAL option {o}: mean band layer loss {d['band_raw'].mean():,.0f} vs deterministic band cost "
                                        f"{d['band_det']:,.0f} within 3 SE ({se:,.0f})", abs(d["band_raw"].mean() - d["band_det"]) < 3 * se))
                    else:
                        close(f"EMPIRICAL option {o} (AP at or above the cut-off): equals IGNORE", [summ.loc[o, "mean_recovery"]],
                              [ign.loc[o, "mean_recovery"]])
        OUT, CTL = keep


def stale():
    """Stage 03 empties the fit: 04 after 03 without 04a stops (S07)."""
    global OUT, CTL
    keep, v = variant("t4s", lambda ws: None)
    for b in (build_stage00.build(v), build_stage01.build_line(v), build_stage02.build(v), build_stage03.build(v)):
        sim.run(b)
    try:
        sim.run(build_stage04.build(v))
        msgs = [m for m in sim.MESSAGES if m[0] == "Error"]
    except sim.SimError as ex:
        msgs = [("Error", str(ex))]
    results.append((f"PYTHON without a fresh 04a: S07 stops ({msgs[:1]})", any("S07" in m[1] for m in msgs)))
    OUT, CTL = keep


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / C.SYNTH.control, CTL)
    wb = load_workbook(CTL)
    ws = wb["Params"]
    for r in range(5, ws.max_row + 1):
        if ws.cell(r, 1).value == "iterations":
            ws.cell(r, 3).value = str(N_ITER)
    ly = wb["Layers"]                       # exercise the annual terms: AAD on option 1, 1 paid reinstatement on 2
    hdr = [c.value for c in ly[4]]
    for r in range(5, ly.max_row + 1):
        if ly.cell(r, 1).value == "PP" and ly.cell(r, 2).value == 1:
            ly.cell(r, hdr.index("aad") + 1).value = 1_000_000
        if ly.cell(r, 1).value == "PP" and ly.cell(r, 2).value == 2:
            ly.cell(r, hdr.index("reinstatements_n") + 1).value = 1
            ly.cell(r, hdr.index("reinstatement_cost_pct") + 1).value = 1.0
    wb.save(CTL)
    for b in (build_stage00.build(V), build_stage01.build_line(V), build_stage02.build(V), build_stage03.build(V),
              build_stage04a.build(V), build_stage04.build(V)):
        sim.run(b)
        results.append((f"{b.stem}: no STOP", not [m for m in sim.MESSAGES if m[0] == "Error"]))

    e = oracle()
    summ = pd.read_csv(OUT / "results/out_sim_summary.csv").set_index("option_id")
    its = pd.read_csv(OUT / "results/out_sim_iterations.csv")
    qs = pd.read_csv(OUT / "results/out_sim_quantiles.csv")
    rec = pd.read_csv(OUT / "checks/recon_04.csv").set_index(["table", "measure"]).value
    close("alpha", [summ.alpha.iloc[0]], [e["alpha"]])
    close("lambda", [summ["lambda"].iloc[0]], [e["lam"]])
    close("total claims", [rec[("iterations", "claims")]], [e["n_claims"].sum()])
    close("total claim amount", [rec[("claims", "amount")]], [e["claims_sum"]])
    for o, d in e["out"].items():
        g = its[its.option_id == o].sort_values("iter")
        close(f"option {o} recovery by iteration", g.recovery, d["rec"])
        close(f"option {o} mean / sd", [summ.loc[o, "mean_recovery"], summ.loc[o, "sd_recovery"]],
              [d["rec"].mean(), d["rec"].std()])
        srt = np.sort(d["rec"])
        q = qs[qs.option_id == o].set_index("q").recovery
        exp_q = [srt[max(1, math.ceil(round(qq, 3) * N_ITER)) - 1] for qq in q.index]
        close(f"option {o} quantiles", q.values, exp_q)
        close(f"option {o} above largest observed", [summ.loc[o, "mean_above_max_observed"]], [d["above"].mean()])
        close(f"option {o} reinstatement usage", [summ.loc[o, "mean_reinst_usage"]], [d["ru"].mean()])
    close("dispersion phi", [summ.phi.iloc[0]], [e["phi"]])
    results.append((f"PYTHON severity: {summ.sev_dist.iloc[0]} selected in 04a", e["src"] == "PYTHON" and
                    (summ.sev_dist == "PARETO").all()))
    close("fitted Pareto grid = closed-form Pareto draws", e["x"], e["u"] * e["u2"] ** (-1 / e["alpha"]), tol=1e-11)
    gpd()
    stale()
    negbin()
    band()
    # statistical sense
    a = summ.alpha.iloc[0]
    results.append((f"alpha {a:.3f} within 1.6 +/- 0.15", abs(a - 1.6) < 0.15))
    u1 = e["u1"]
    results.append((f"iteration uniforms mean {u1.mean():.3f}, KS vs U(0,1) small",
                    abs(u1.mean() - 0.5) < 0.03 and np.max(np.abs(np.sort(u1) - np.arange(1, N_ITER + 1) / N_ITER)) < 0.07))
    results.append((f"reinstatement usage used on option 2 ({summ.loc[2, 'mean_reinst_usage']:.3f})",
                    summ.loc[2, "mean_reinst_usage"] > 0))
    r = summ.det_to_stoch_below[[2, 3, 4]]           # option 1 carries a test AAD
    results.append((f"deterministic / stochastic within 0.8-1.25 ({', '.join(f'{x:.2f}' for x in r)})",
                    bool(((r > 0.8) & (r < 1.25)).all())))
    for n, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {n}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
