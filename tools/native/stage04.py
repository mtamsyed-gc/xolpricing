"""Stage 04 (per-person simulation) in numpy: the same results as the executor running 04_simulate.yxmd, much faster.

    python tools/native/stage04.py <output folder>        e.g. data/output/synth

The stage definition (tools/alteryx/build_stage04.py) stays the source of truth: every rule below is a transcription of
one of its tools, as tools/alteryx/sim.py evaluates it, and the comments name the tool. The module reads the stage files
the executor reads (stage/ctl_params, ctl_layers, bc_adjusted, bc_weights, exp_exposure_ty, bc_summary, sev_table) and
writes the same files with the same fields, types, row order and values:
    results/out_fit.csv, out_sim_summary.csv, out_sim_quantiles.csv, out_sim_iterations.csv,
    checks/checks_04.csv, recon_04.csv,
    stage/res_out_fit, res_out_sim_summary, res_out_sim_quantiles, sim_summary, sim_iterations, sim_gross, chk_04, rec_04.
Messages: one ("Error", "[code] item: message") per STOP check, as the executor's Message tool gives them.

Exactness. Every elementwise operation is the same IEEE operation in the same order as in the executor; logarithms,
exponentials and powers use the math module (the executor's functions), not numpy's own; MINSTD states are exact integers
in both; Summarize's Sum is Python's built-in sum (compensated since Python 3.12), reproduced here by `_sum` / `_gsum`.
tests/test_native_stage04.py checks byte equality of every output file against the executor on each variant.

Inputs outside what this module reproduces (degenerate data the executor handles in its own way: no unit above u, no fit,
no active per-person layer, Null keys, duplicate layer keys or grid rows, a non-integer seed) raise Unsupported; the
runner (tools/run_python.py) then runs the executor for stage 04, so results never depend on which engine ran.
"""
from __future__ import annotations

import csv
import math
import pickle
import sys
from pathlib import Path

import numpy as np

M = 2147483647                  # MINSTD modulus 2^31 - 1
A = 48271                       # MINSTD multiplier
BIG = 1000000000000000          # "no limit" (build_stage04.BIG)
KSEV, DECADES = 50, 10          # severity grid: points per decade, decades (tools/python/sevfit.py)
KD = KSEV * DECADES
JUMP = {j: pow(A, j * (1 << 29), M) for j in (1, 2, 3, 4, 5)}
QUANTILES = [0.005 * i for i in range(1, 200)] + [0.001, 0.999]
Q_GRID = [float(f"{q:.3f}") for q in sorted(QUANTILES)]       # the Text Input "quantile grid" as Designer types it

# output schemas (field -> type, in the order the executor writes them)
D, I, S = "Double", "Int64", "V_WString"
F_FIT = {"t": D, "t_label": S, "n_ge": D, "n_uncensored": D, "n_censored": D, "max_observed": D, "n_fit": I,
         "alpha": D, "alpha_se": D, "u_fit": D, "s_empirical": D, "s_fitted": D, "expected_n": D}
F_GROSS = {"iter": I, "large_gross": D, "large_gross_sl": D}
F_ITER = {"option_id": I, "layer": I, "iter": I, "n_claims_layer": D, "l_label": S, "recovery": D,
          "recovery_aboveM": D, "recovery_band": D, "reinst_usage": D, "large_gross": D}
F_QUANT = {"option_id": I, "layer": I, "l_label": S, "recovery": D, "q": D}
F_SUMM = {"option_id": I, "layer": I, "l_label": S, "max_recovery": D, "mean_recovery": D, "sd_recovery": D, "cov": D,
          "p_zero": D, "mean_above_max_observed": D, "mean_below_max_observed": D, "mean_reinst_usage": D,
          "mean_recovery_band": D, "p50": D, "p75": D, "p90": D, "p95": D, "p99": D, "p995": D, "credibility_z": D,
          "expected_cost_det": D, "lives_q": D, "iterations": I, "band_from": D, "u_fit": D, "below_cutoff": S,
          "n_band": I, "lambda": D, "lambda_band": D, "phi": D, "freq_dist": S, "max_observed": D, "alpha": D,
          "det_to_stoch_below": D, "expected_loss_blended": D, "expected_loss_pppy": D, "sev_dist": S}
F_CHECK = {"stage": S, "code": S, "severity": S, "item": S, "message": S}
F_RECON = {"stage": S, "table": S, "measure": S, "value": D}
PCTS = ((0.5, "p50"), (0.75, "p75"), (0.9, "p90"), (0.95, "p95"), (0.99, "p99"), (0.995, "p995"))


class Unsupported(Exception):
    """Input outside the native path: the caller runs the executor instead."""


# ============================================================================================== files
def _load(stage: Path, name: str):
    with open(stage / f"{name}.yxdb", "rb") as f:
        fields, rows = pickle.load(f)
    return dict(fields), rows


def _text(v):
    """sim.to_text: how the executor writes a non-float value."""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def _write(out: Path, fields: dict, rows: list, csv_rel: str | None = None, yxdb: str | None = None) -> None:
    """The executor's Output tools: CSV (utf-8, floats by repr, Null empty) and the .yxdb stand-in (pickle)."""
    if csv_rel:
        p = out / csv_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(list(fields))
            for r in rows:
                w.writerow(["" if r[n] is None else repr(r[n]) if isinstance(r[n], float) else _text(r[n])
                            for n in fields])
    if yxdb:
        p = out / "stage" / f"{yxdb}.yxdb"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            pickle.dump((dict(fields), [{n: r[n] for n in fields} for r in rows]), f)


# ============================================================================================== arithmetic helpers
def _sum(values) -> float:
    """Summarize Sum: float(sum(non-Null values)) (Python's built-in sum: Neumaier-compensated from 3.12)."""
    return float(sum(v for v in values if v is not None))


def _gsum(vals: np.ndarray, starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Built-in sum() of each group vals[s:s+c] (groups contiguous, in record order), vectorised across groups:
    CPython's float loop (t = f + x; c += (f - t) + x if |f| >= |x| else (x - t) + f; f = t; at the end f += c when c is
    non-zero and finite). Empty groups give 0.0."""
    ng = len(starts)
    f = np.zeros(ng)
    c = np.zeros(ng)
    if ng == 0:
        return f
    cmax = int(counts.max()) if len(counts) else 0
    for j in range(cmax):
        act = counts > j
        if not act.all():
            idx = np.nonzero(act)[0]
            x = vals[starts[idx] + j]
            fi, ci = f[idx], c[idx]
            t = fi + x
            ci = ci + np.where(np.abs(fi) >= np.abs(x), (fi - t) + x, (x - t) + fi)
            f[idx], c[idx] = t, ci
        else:
            x = vals[starts + j]
            t = f + x
            c = c + np.where(np.abs(f) >= np.abs(x), (f - t) + x, (x - t) + f)
            f = t
    fin = (c != 0) & np.isfinite(c)
    return np.where(fin, f + c, f)


def _vmap(fn, arr: np.ndarray) -> np.ndarray:
    """math-module function elementwise (the executor's Log / Log10 / Exp), not numpy's own implementation."""
    return np.fromiter(map(fn, arr.tolist()), dtype=float, count=len(arr))


_POW = {}


def _minstd(start: int, n: int) -> np.ndarray:
    """Sequential MINSTD states along the record order (Multi-Row 'MINSTD stream'): s_1 = a.start mod m, s_i = a.s_(i-1)
    mod m. The executor's floating formula is exact for these integers, so integer arithmetic gives the same states.
    Returned as uniforms state / m (Formula 'uniform')."""
    out = np.empty(n, dtype=np.int64)
    if n == 0:
        return out.astype(float)
    b = 4096
    if b not in _POW:
        p = np.empty(b, dtype=np.int64)
        x = 1
        for i in range(b):
            x = (x * A) % M
            p[i] = x
        _POW[b] = (p, pow(A, b, M))
    p, ab = _POW[b]
    s = int(start)
    for i in range(0, n, b):
        k = min(b, n - i)
        out[i:i + k] = (s * p[:k]) % M
        s = (s * ab) % M
    return out / M


def _sortkey(v):
    return (0,) if v is None else (1, v)


def _layer_loss(claim: np.ndarray, cap: np.ndarray, L: dict) -> np.ndarray:
    """build_stage04.layer_loss: IIF(over <= 0, 0, IIF(over < lim, over, IIF(lim > 0, lim, 0))) * (1 - cop)."""
    if L["lim_basis"] == "TO_BENEFIT_LIMIT":
        lim = np.where(cap >= BIG, float(BIG), cap - L["ap"])
    else:
        lim = np.full(len(claim), float(BIG) if L["limit"] is None else float(L["limit"]))
    over = claim - L["ap"]
    v = np.where(over <= 0, 0.0, np.where(over < lim, over, np.where(lim > 0, lim, 0.0)))
    return v * (1 - L["cop"])


def _layer_loss_1(claim, cap, L):
    """Scalar layer loss (band units, deterministic band cost)."""
    if L["lim_basis"] == "TO_BENEFIT_LIMIT":
        lim = BIG if cap >= BIG else cap - L["ap"]
    else:
        lim = BIG if L["limit"] is None else L["limit"]
    over = claim - L["ap"]
    v = 0 if over <= 0 else (over if over < lim else (lim if lim > 0 else 0))
    return float(v * (1 - L["cop"]))


def _nn(*xs):
    return any(x is None for x in xs)


def _cdf(table_pk) -> np.ndarray:
    """Multi-Row CDF: cdf_0 = 0 + pk_0, cdf_k = cdf_(k-1) + pk_k."""
    out, c = [], 0
    for p in table_pk:
        c = c + p
        out.append(c)
    return np.array(out, dtype=float)


def _ceil(x):
    return int(math.ceil(x))


def _floor(x):
    return int(math.floor(x))


# ============================================================================================== the stage
def run(out_dir) -> list[tuple[str, str]]:
    """Run stage 04 on the stage files in out_dir/stage; returns the Message tool's messages [(kind, text)]."""
    out = Path(out_dir)
    stage = out / "stage"

    # ---------------------------------------------------------------- 04.1 inputs
    _, prow = _load(stage, "ctl_params")
    if len(prow) != 1:
        raise Unsupported(f"ctl_params has {len(prow)} rows")
    P = prow[0]
    p_iter, p_seed, p_cut = P["iterations"], P["seed"], P["fit_cutoff"]
    p_fm, p_inc, p_sl = P["freq_model"], P["incidence_adj"], P["sl_attritional_threshold"]
    p_src, p_below = P["severity_source"], P["below_cutoff"]
    if _nn(p_iter, p_seed, p_fm, p_inc, p_src):
        raise Unsupported("iterations, seed, freq_model, incidence_adj or severity_source is blank")

    _, lrows = _load(stage, "ctl_layers")
    lay = []
    for r in lrows:                                         # Filter "active per-person layers"
        if r["cover"] is None or r["active"] is None:
            raise Unsupported("ctl_layers: blank cover or active")
        if r["cover"] == "PP" and r["active"] == 1:
            lab = r["label"]
            if lab is None:
                oid = r["option_id"]
                if oid is None or not float(oid).is_integer():
                    raise Unsupported("layer label from a non-integer option_id")
                lab = "PP option " + str(int(oid))
            lay.append({"option_id": r["option_id"], "layer": r["layer"], "l_label": lab,
                        "lim_basis": "AMOUNT" if r["limit_basis"] is None else r["limit_basis"],
                        "ap": r["ap"], "limit": r["limit"], "aal": r["aal"], "aad": r["aad"],
                        "cop": 0.0 if r["co_participation_pct"] is None else float(r["co_participation_pct"]),
                        "reinstatements_n": r["reinstatements_n"], "reinstatement_cost_pct": r["reinstatement_cost_pct"],
                        "reinst_pro_rata_time": r["reinst_pro_rata_time"]})
    if not lay:
        raise Unsupported("no active per-person layer")
    keys = [(L["option_id"], L["layer"]) for L in lay]
    if any(k is None for kk in keys for k in kk) or len(set(keys)) != len(keys):
        raise Unsupported("per-person layers with a blank or repeated (option_id, layer)")
    if any(L["ap"] is None for L in lay):
        raise Unsupported("per-person layer without an AP")
    min_ap = min(L["ap"] for L in lay)                      # Summarize "lowest AP"
    u = float(min_ap if p_cut is None else p_cut)           # Formula "threshold u"
    if u <= 0:
        raise Unsupported("threshold u <= 0")
    s0 = p_seed - (M - 1) * _floor(p_seed / (M - 1)) + 1   # Formula "seed state"
    if not float(s0).is_integer():
        raise Unsupported("non-integer seed")
    s0 = int(s0)
    start = {j: (JUMP[j] * s0) % M for j in JUMP}           # stream starts (16-bit split in the definition: exact)
    t_sl = float(u if (p_sl is None or p_sl < u) else p_sl)
    below_mode = "STOP" if (p_below is None or p_below == "") else p_below
    band_exists = u > min_ap
    n_it = int(p_iter)
    if n_it != p_iter or n_it < 1:
        raise Unsupported("iterations not a positive whole number")

    # ---------------------------------------------------------------- 04.2 severity fit
    _, adj = _load(stage, "bc_adjusted")
    for r in adj:
        if r["adj"] is None:
            raise Unsupported("bc_adjusted: blank adj")
    fu = [r for r in adj if r["adj"] > u]                   # Filter "units above u"
    if not fu:
        raise Unsupported("no adjusted unit above u")
    n_unc = [0.0 if r["adj_uncapped"] >= r["cap"] else 1.0 for r in fu]
    n_cens = [1 if r["adj_uncapped"] >= r["cap"] else 0 for r in fu]
    ln_ratio = [math.log(r["adj"] / u) for r in fu]
    n_uncensored, n_censored, sum_ln = _sum(n_unc), _sum(n_cens), _sum(ln_ratio)
    max_observed = max(r["adj"] for r in fu)
    n_fit = len(fu)
    alpha = n_uncensored / sum_ln if (sum_ln > 0 and n_uncensored > 0) else None
    if alpha is None:
        raise Unsupported("no Pareto fit (S01)")
    alpha_se = n_uncensored / sum_ln / math.sqrt(n_uncensored)
    # band units (min AP, u], ranked by acc_key; caps of the fitted units, ranked by acc_key
    bu = [r for r in adj if r["adj"] > min_ap and r["adj"] <= u]
    bands = sorted(bu, key=lambda r: _sortkey(r["acc_key"]))
    n_band = len(bu)
    caps = np.array([r["cap"] for r in sorted(fu, key=lambda r: _sortkey(r["acc_key"]))], dtype=float)
    fit_rows = _out_fit(lay, fu, u, alpha, alpha_se, n_fit, n_uncensored, n_censored, max_observed)
    _write(out, F_FIT, fit_rows, "results/out_fit.csv", "res_out_fit")

    # ---------------------------------------------------------------- 04.3 frequency
    _, wts = _load(stage, "bc_weights")
    _, ex = _load(stage, "exp_exposure_ty")
    _, bsum = _load(stage, "bc_summary")
    if any(r["ty"] is None for r in fu) or any(r["ty"] is None for r in bu):
        raise Unsupported("unit without a treaty year")
    n_u = _group_sums(fu, "ty", "post_cnt_f_")
    n_b = _group_sums(bu, "ty", "post_cnt_f_")
    fy = []
    for w in wts:                                           # Join "weights + exposure"
        if w["g_ty"] is None:
            raise Unsupported("bc_weights: blank g_ty")
        for e in ex:
            if e["ty"] is None:
                raise Unsupported("exp_exposure_ty: blank ty")
            if e["ty"] == w["g_ty"]:
                fy.append({"g_ty": w["g_ty"], "weight": w["weight"], "exposure_ly_full": e["exposure_ly_full"]})
    fy = _left_join(fy, n_u, "n_u")                         # "+ counts": matched rows first, then unmatched
    fy = _left_join(fy, n_b, "n_b")                         # "+ band counts"
    if not fy or not bsum:
        raise Unsupported("no weighted experience year or no burning-cost summary")
    for r in fy:
        if _nn(r["weight"], r["exposure_ly_full"]) or r["exposure_ly_full"] == 0:
            raise Unsupported("blank weight or zero exposure")
        nu = 0 if r["n_u"] is None else r["n_u"]
        nb_ = 0 if r["n_b"] is None else r["n_b"]
        r["freq_u"] = float(nu / r["exposure_ly_full"])
        r["w_freq"] = float(r["weight"] * r["freq_u"])
        r["w_freq_b"] = float(r["weight"] * nb_ / r["exposure_ly_full"])
        r["n_y"] = float(nu)
    fuw = _sum(r["w_freq"] for r in fy)
    fbw = _sum(r["w_freq_b"] for r in fy)
    n_all = _sum(r["n_y"] for r in fy)
    e_all = _sum(r["exposure_ly_full"] for r in fy)
    k_years = _sum(1.0 for _ in fy)
    pearson = []
    for r in fy:                                            # Formula "Pearson terms"
        if n_all > 0:
            ey = r["exposure_ly_full"] * n_all / e_all
            pearson.append(float(math.pow(r["n_y"] - r["exposure_ly_full"] * n_all / e_all, 2) / ey))
        else:
            pearson.append(0.0)
    pearson_sum = _sum(pearson)
    lv = [r["lives_quote_year"] for r in bsum if r["lives_quote_year"] is not None]
    if not lv:
        raise Unsupported("no lives in the quote year")
    lives_q = max(lv)
    lam = float(fuw * lives_q * p_inc)
    band_on = below_mode == "EMPIRICAL" and band_exists and n_band > 0
    lambda_band = float(fbw * lives_q * p_inc) if band_exists else 0.0
    k_max_b = _ceil(fbw * lives_q * p_inc + 10 * math.sqrt(fbw * lives_q * p_inc) + 20)
    phi = float(pearson_sum / (k_years - 1)) if k_years > 1 else 1.0
    nb = p_fm == "NEGBIN" and phi > 1
    nb_r = lam / (phi - 1) if nb else None
    nb_q = 1 / phi if nb else None
    freq_dist = "NEGBIN" if nb else "POISSON"
    k_max = _ceil(lam + 10 * math.sqrt(lam * (phi if nb else 1)) + 20)

    # ---------------------------------------------------------------- 04.4 claim counts by inverse CDF
    pk = []
    for k in range(k_max + 1):
        if nb:
            pk.append(math.exp(nb_r * math.log(nb_q)) if k == 0 else pk[-1] * (nb_r + k - 1) / k * (1 - nb_q))
        else:
            pk.append(math.exp(-lam) if k == 0 else pk[-1] * lam / k)
    u1 = _minstd(start[1], n_it)
    n_claims = np.searchsorted(_cdf(pk), u1, side="left")   # N = #{k: CDF(k) < U} (draws sort before equal CDFs)

    # ---------------------------------------------------------------- 04.5a severity table
    if p_src == "NATIVE":
        l10, lu = math.log(10), math.log(u)
        tab = {k: (lu + k * l10 / (KSEV * alpha), lu + (k + 1) * l10 / (KSEV * alpha)) for k in range(KD)}
        tab_dist = ["PARETO (native)"] if KD else []
    elif p_src == "PYTHON":
        _, srows = _load(stage, "sev_table")
        tab = {}
        for r in srows:
            if r["k"] is None or r["k"] in tab:
                raise Unsupported("sev_table: blank or repeated k")
            tab[r["k"]] = (r["lx0"], r["lx1"])
        tab_dist = [r["dist"] for r in srows]
    else:
        tab, tab_dist = {}, []
    n_tab = float(len(tab)) if tab_dist else None
    sev_dist = tab_dist[0] if tab_dist else None
    lx0 = np.full(KD, np.nan)
    lx1 = np.full(KD, np.nan)
    has = np.zeros(KD, dtype=bool)
    for k, (a0, a1) in tab.items():
        if float(k).is_integer() and 0 <= k < KD:
            if a0 is None or a1 is None:
                raise Unsupported("sev_table: blank lx")
            kk = int(k)
            lx0[kk], lx1[kk], has[kk] = a0, a1, True

    # ---------------------------------------------------------------- 04.5 tail claims
    tot = int(n_claims.sum())
    it_claim = np.repeat(np.arange(1, n_it + 1, dtype=np.int64), n_claims)
    u2 = _minstd(start[2], tot)
    u3 = _minstd(start[3], tot)
    t_sev = (-_vmap(math.log10, u2)) * KSEV
    sev_k = np.minimum(np.floor(t_sev), KD - 1).astype(np.int64)
    cap_rank = np.floor(u3 * n_fit).astype(np.int64) + 1
    keep = has[sev_k]                                       # Join "+ severity grid": claims without a grid row drop
    t_sev, sev_k, cap_rank, it_claim = t_sev[keep], sev_k[keep], cap_rank[keep], it_claim[keep]
    a0, a1 = lx0[sev_k], lx1[sev_k]
    x = _vmap(math.exp, a0 + (t_sev - sev_k) * (a1 - a0))
    s_cap = caps[cap_rank - 1]
    claim = np.where(x < s_cap, x, s_cap)
    claim_below = np.where(claim < max_observed, claim, max_observed)
    claim_sl = np.where(claim > t_sl, claim, 0.0)
    # sim_gross: per iteration, every tail claim and those above t_sl (no claims = 0)
    g_start, g_cnt = _groups(it_claim, n_it)
    large_gross = _gsum(claim, g_start, g_cnt)
    large_gross_sl = _gsum(claim_sl, g_start, g_cnt)
    gross_rows = [{"iter": i + 1, "large_gross": float(large_gross[i]), "large_gross_sl": float(large_gross_sl[i])}
                  for i in range(n_it)]
    _write(out, F_GROSS, gross_rows, None, "sim_gross")

    # ---------------------------------------------------------------- 04.5b band claims (streams 4, 5)
    lam_eff = lambda_band if band_on else 0.0
    pkb = []
    for k in range(k_max_b + 1):
        pkb.append(math.exp(-lam_eff) if k == 0 else pkb[-1] * lam_eff / k)
    u4 = _minstd(start[4], n_it)
    n_claims_b = np.searchsorted(_cdf(pkb), u4, side="left")
    tot_b = int(n_claims_b.sum())
    it_band = np.repeat(np.arange(1, n_it + 1, dtype=np.int64), n_claims_b)
    u5 = _minstd(start[5], tot_b)
    if tot_b:
        b_pick = np.floor(u5 * n_band).astype(np.int64) + 1
        b_adj = np.array([r["adj"] for r in bands], dtype=float)[b_pick - 1]
        b_cap = np.array([r["cap"] for r in bands], dtype=float)[b_pick - 1]
    else:
        b_adj = b_cap = np.empty(0)
    b_below = np.where(b_adj < max_observed, b_adj, max_observed)

    # ---------------------------------------------------------------- 04.6 layers
    # claims in the executor's order: tail claims, then band claims (Union); grouped by iteration in that order
    all_claim = np.concatenate([claim, b_adj])
    all_cap = np.concatenate([s_cap, b_cap])
    all_below = np.concatenate([claim_below, b_below])
    all_it = np.concatenate([it_claim, it_band])
    is_band = np.concatenate([np.zeros(len(claim), dtype=bool), np.ones(tot_b, dtype=bool)])
    order = np.argsort(all_it, kind="stable")
    all_claim, all_cap, all_below, all_it, is_band = (all_claim[order], all_cap[order], all_below[order], all_it[order],
                                                      is_band[order])
    c_start, c_cnt = _groups(all_it, n_it)
    gross_by_it = large_gross                                # "+ large gross" (0 where no tail claim)
    iters = np.arange(1, n_it + 1)
    lay_sorted = sorted(lay, key=lambda L: (_sortkey(L["option_id"]), _sortkey(L["layer"])))
    ito, per_layer = [], []
    for L in lay_sorted:
        xl_c = _layer_loss(all_claim, all_cap, L)
        xlb_c = _layer_loss(all_below, all_cap, L)
        xl = _gsum(xl_c, c_start, c_cnt)
        xl_below = _gsum(xlb_c, c_start, c_cnt)
        xl_band = _gsum(np.where(is_band, xl_c, 0.0), c_start, c_cnt)
        aad = 0 if L["aad"] is None else L["aad"]
        n_r, lim, aal, cost = L["reinstatements_n"], L["limit"], L["aal"], L["reinstatement_cost_pct"]
        agg = aal if aal is not None else (BIG if (n_r is None or lim is None) else (1 + n_r) * lim)
        after = np.where(xl - aad > 0, xl - aad, 0.0)
        rec = np.where(after < agg, after, float(agg))
        aa_f = np.where(xl > 0, rec / np.where(xl > 0, xl, 1.0), 1.0)
        above = (xl - xl_below) * aa_f
        band = xl_band * aa_f
        if cost is None or lim is None or lim <= 0:
            ru = np.zeros(n_it)
        else:
            ru = cost * (rec if n_r is None else np.where(rec < n_r * lim, rec, float(n_r * lim))) / lim
        per_layer.append((L, rec, above, band, ru))
        ncl = c_cnt.astype(float)
        cols = zip(iters.tolist(), ncl.tolist(), rec.tolist(), above.tolist(), band.tolist(), ru.tolist(),
                   gross_by_it.tolist())
        for i, nc, r_, ab, bd, rr, g in cols:
            ito.append({"option_id": L["option_id"], "layer": L["layer"], "iter": i, "n_claims_layer": nc,
                        "l_label": L["l_label"], "recovery": r_, "recovery_aboveM": ab, "recovery_band": bd,
                        "reinst_usage": rr, "large_gross": g})
    _write(out, F_ITER, ito, "results/out_sim_iterations.csv", "sim_iterations")

    # ---------------------------------------------------------------- 04.7 quantiles and summary
    q_rank = [max(1, _ceil(q * n_it)) for q in Q_GRID]
    qrows, pcts = [], []
    for L, rec, above, band, ru in per_layer:
        srt = rec[np.lexsort((iters, rec))]                 # by recovery, then iteration
        pc = {}
        for q, rk in zip(Q_GRID, q_rank):
            v = float(srt[rk - 1])
            qrows.append({"option_id": L["option_id"], "layer": L["layer"], "l_label": L["l_label"], "recovery": v,
                          "q": q})
            pc[q] = v
        pcts.append(pc)
    _write(out, F_QUANT, qrows, "results/out_sim_quantiles.csv", "res_out_sim_quantiles")

    bs_idx = {}
    for r in bsum:
        bs_idx.setdefault((r["option_id"], r["layer"]), []).append(r)
    summ, st_all = [], []
    for (L, rec, above, band, ru), pc in zip(per_layer, pcts):
        rl = rec.tolist()
        sum_r, sum_r2 = _sum(rl), _sum((rec * rec).tolist())
        n_zero = _sum([1.0 if v == 0 else 0.0 for v in rl])
        sum_above, sum_band, sum_ru = _sum(above.tolist()), _sum(band.tolist()), _sum(ru.tolist())
        mean = sum_r / n_it
        var = sum_r2 / n_it - math.pow(sum_r / n_it, 2)
        sd = math.sqrt(var if var > 0 else 0)
        m_above = sum_above / n_it
        m_below = mean - m_above
        st = {"option_id": L["option_id"], "layer": L["layer"], "l_label": L["l_label"], "max_recovery": max(rl),
              "mean_recovery": mean, "sd_recovery": sd, "cov": (sd / mean if sum_r > 0 else None),
              "p_zero": n_zero / n_it, "mean_above_max_observed": m_above, "mean_below_max_observed": m_below,
              "mean_reinst_usage": sum_ru / n_it, "mean_recovery_band": sum_band / n_it}
        for qv, nm in PCTS:
            st[nm] = pc[qv]
        matches = bs_idx.get((L["option_id"], L["layer"]), [])
        if len(matches) > 1:
            raise Unsupported("bc_summary: repeated (option_id, layer)")
        if not matches or sev_dist is None:                 # Join "+ deterministic" / "+ severity distribution"
            continue
        b = matches[0]
        z, det = b["credibility_z"], b["expected_cost_det"]
        st.update({"credibility_z": z, "expected_cost_det": det, "lives_q": lives_q, "iterations": p_iter,
                   "band_from": min_ap, "u_fit": u, "below_cutoff": below_mode, "n_band": n_band, "lambda": lam,
                   "lambda_band": lambda_band, "phi": phi, "freq_dist": freq_dist, "max_observed": max_observed,
                   "alpha": alpha, "sev_dist": sev_dist})
        dts = (None if det is None else det / m_below) if m_below > 0 else None
        blend = None if _nn(z, det) else z * det + (1 - z) * m_below + m_above
        st["det_to_stoch_below"] = None if dts is None else float(dts)
        st["expected_loss_blended"] = None if blend is None else float(blend)
        st["expected_loss_pppy"] = None if blend is None else float(blend / lives_q)
        summ.append(st)
        st_all.append((L, st))
    _write(out, F_SUMM, summ, "results/out_sim_summary.csv", "res_out_sim_summary")
    _write(out, F_SUMM, summ, None, "sim_summary")

    # ---------------------------------------------------------------- 04.8 checks and recon
    chk = [{"stage": "S04", "code": "OK", "severity": "INFO", "item": "-", "message": "stage 04 checks ran"}]

    def add(code, sev, item, msg):
        chk.append({"stage": "S04", "code": code, "severity": sev, "item": item, "message": msg})

    if n_tab is None or n_tab != KD:
        add("S07", "STOP", "severity_source " + p_src,
            "no severity table: run 04a_fit_severity after 03_burning_cost, or set severity_source = NATIVE")
    if lam > 700:
        add("S02", "STOP", "frequency", "lambda > 700: raise fit_cutoff (Poisson table underflows)")
    if p_fm == "NEGBIN" and not nb:
        add("S03", "INFO", "frequency",
            f"NEGBIN requested but the yearly counts are not overdispersed (phi = {phi:.2f}): Poisson used")
    if n_fit < 30:
        add("S04", "FLAG", "severity fit", f"{n_fit:.0f} claims above u: fit is uncertain (see alpha_se)")
    # band checks: layers attaching below u (layer order), each joined to its summary row
    stat = {(L["option_id"], L["layer"]): s for L, s in st_all}
    lb = []
    for L in lay:
        if L["ap"] < u and (L["option_id"], L["layer"]) in stat:
            s = stat[(L["option_id"], L["layer"])]
            if bu:
                xs = [_layer_loss_1(r["adj"], r["cap"], L) for r in bu]
                bcd = lambda_band * _sum(xs) / float(len(bu))
            else:
                bcd = 0
            lb.append((L, s, float(bcd)))
    for L, s, bcd in lb:
        if below_mode == "STOP":
            add("S08", "STOP", L["l_label"],
                f"fit_cutoff {u:.0f} is above this layer's attachment {L['ap']:.0f}: claims between the AP and the "
                "cut-off would not be simulated. Lower fit_cutoff, or set Params below_cutoff to EMPIRICAL (simulate "
                "the band from the data) or IGNORE (leave it out)")
    for L, s, bcd in lb:
        if below_mode == "IGNORE":
            den = s["mean_recovery"] + bcd
            if den == 0:
                raise Unsupported("S09: zero mean recovery and band cost (the executor stops on a division by zero)")
            add("S09", "FLAG", L["l_label"],
                f"below_cutoff = IGNORE: claims between the AP {L['ap']:.0f} and the cut-off {u:.0f} are not in the "
                f"stochastic result (band frequency {lambda_band:.1f} a year, deterministic band cost {bcd:.0f}, "
                f"{100 * bcd / den:.1f}% of this layer): justify, or set EMPIRICAL")
    for L, s, bcd in lb:
        if below_mode == "EMPIRICAL" and band_on:
            if s["mean_recovery"] == 0:
                raise Unsupported("S10: zero mean recovery (the executor stops on a division by zero)")
            add("S10", "INFO", L["l_label"],
                f"below_cutoff = EMPIRICAL: claims between the AP {L['ap']:.0f} and the cut-off {u:.0f} simulated "
                f"from {n_band:.0f} adjusted claims in the data (band frequency {lambda_band:.1f} a year): "
                f"{100 * s['mean_recovery_band'] / s['mean_recovery']:.1f}% of this layer's mean recovery")
    for L, s, bcd in lb:
        if below_mode == "EMPIRICAL" and not band_on:
            add("S11", "FLAG", L["l_label"],
                f"below_cutoff = EMPIRICAL but the data has no adjusted claim between {L['ap']:.0f} and the cut-off "
                f"{u:.0f}: the band contributes nothing; lower fit_cutoff")
    for L, s in st_all:
        d = s["det_to_stoch_below"]
        if d is not None and (d < 0.8 or d > 1.25):
            add("S05", "FLAG", L["l_label"],
                f"deterministic / stochastic (below largest claim) = {d:.2f}: fit may be poor (workshop rule)")
    for L in lay:
        if L["reinst_pro_rata_time"] is not None and L["reinst_pro_rata_time"] == 1:
            add("S06", "FLAG", L["l_label"], "reinstatement pro rata as to time not modelled: amount only")
    rec_all = [r["recovery"] for r in ito]
    recon = [("fit_units", "rows", float(n_fit)),
             ("iterations", "rows", float(n_it)),
             ("iterations", "claims", _sum([float(v) for v in n_claims.tolist()] + [0.0])),
             ("claims", "rows", float(len(claim))),
             ("claims", "amount", _sum(claim.tolist() + [0.0])),
             ("out_sim_iterations", "rows", float(len(ito))),
             ("out_sim_iterations", "recovery", _sum(rec_all + [0.0]))]
    _write(out, F_RECON, [{"stage": "S04", "table": t, "measure": m, "value": v} for t, m, v in recon],
           "checks/recon_04.csv", "rec_04")
    _write(out, F_CHECK, chk, "checks/checks_04.csv", "chk_04")
    return [("Error", f"[{c['code']}] {c['item']}: {c['message']}") for c in chk if c["severity"] == "STOP"]


# ============================================================================================== pieces
def _groups(it: np.ndarray, n_it: int):
    """Start and size of each iteration's run of records (records sorted by iteration)."""
    cnt = np.bincount(it - 1, minlength=n_it)[:n_it] if len(it) else np.zeros(n_it, dtype=np.int64)
    st = np.concatenate([[0], np.cumsum(cnt)[:-1]]).astype(np.int64)
    return st, cnt


def _group_sums(rows, key, field):
    """Summarize Group By key, Sum field: {key: sum} (Null when the group has no value)."""
    g = {}
    for r in rows:
        g.setdefault(r[key], []).append(r[field])
    return {k: (_sum(v) if any(x is not None for x in v) else None) for k, v in g.items()}


def _left_join(rows, right: dict, name: str):
    """Join on g_ty, then Union (left join): matched records first, unmatched after (no right value = Null)."""
    j = [{**r, name: right[r["g_ty"]]} for r in rows if r["g_ty"] in right]
    left = [{**r, name: None} for r in rows if r["g_ty"] not in right]
    return j + left


def _out_fit(lay, fu, u, alpha, alpha_se, n_fit, n_unc, n_cens, max_obs):
    """out_fit: empirical vs fitted exceedances at each AP, exhaustion point and 250k / 500k / 1m / 2m above u."""
    thr = [(float(L["ap"]), "AP " + L["l_label"], 1) for L in lay]
    thr += [(float(L["ap"] + L["limit"]), "exhaust " + L["l_label"], 0) for L in lay
            if L["lim_basis"] == "AMOUNT" and L["limit"] is not None]
    thr += [(float(x), f"{x:,}", 0) for x in (250000, 500000, 1000000, 2000000)]
    thr.sort(key=lambda t: -t[2])                           # Sort t ascending, is_ap descending (stable)
    thr.sort(key=lambda t: t[0])
    seen, uniq = set(), []
    for t in thr:                                           # Unique on t
        if t[0] not in seen:
            seen.add(t[0])
            uniq.append(t)
    adjs = [r["adj"] for r in fu]
    groups = {}
    for t, lab, _ in uniq:                                  # Summarize by ToString(t, 0)
        key = f"{t:.0f}"
        ge = [1.0 if a >= t else 0.0 for a in adjs]
        if key in groups:
            g = groups[key]
            g["ge"] += ge
            g["n"] += len(adjs)
        else:
            groups[key] = {"t": t, "t_label": lab, "ge": ge, "n": len(adjs)}
    rows = []
    for key in sorted(groups):
        g = groups[key]
        t = g["t"]
        if not t > u:
            continue
        n_ge = _sum(g["ge"])
        rows.append({"t": t, "t_label": g["t_label"], "n_ge": n_ge, "n_uncensored": n_unc, "n_censored": n_cens,
                     "max_observed": max_obs, "n_fit": n_fit, "alpha": alpha, "alpha_se": alpha_se, "u_fit": u,
                     "s_empirical": n_ge / g["n"], "s_fitted": math.pow(u / t, alpha),
                     "expected_n": g["n"] * math.pow(u / t, alpha)})
    rows.sort(key=lambda r: r["t"])
    return rows


if __name__ == "__main__":
    for kind, text in run(sys.argv[1]):
        print(f"{kind}: {text}")
