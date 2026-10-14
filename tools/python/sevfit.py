"""Per-person severity fitting above the threshold u (stage 04a; scope §6, methodology §8.1).

Runs inside Alteryx's Python tool (the builder embeds this file verbatim in 04a_fit_severity.yxmd) and in the tests.
Needs numpy, pandas and scipy only (all in Designer's base Python environment: Designer 2026.2 ships scipy 1.16).

Data: the fit units of stage 04 - adjusted amounts above u (trended, pre-AP completed, capped at the benefit / policy
limit); a unit is right-censored at its cap when its uncapped amount reaches the cap. Every candidate is fitted by
maximum likelihood to the distribution conditional on X > u (left truncation at u):
    log L = sum_uncensored log f(x) + sum_censored log S(c) - n log S(u)
Candidates (fit_candidates): LOGNORMAL (mu, sigma), GAMMA (shape, scale), WEIBULL (shape, scale), PARETO (alpha;
single-parameter with scale u: the native fit), GPD (xi, sigma; on the excess x - u).
Statistics: log-likelihood, AIC = 2k - 2 log L, BIC = k ln n - 2 log L, KS = largest distance between the fitted
conditional survival and the Kaplan-Meier estimate, mean excess over each AP (fitted with the units' caps and limited
at the largest observed claim M, against the data), survival at fixed thresholds.
Boundary fits: a candidate whose optimum runs to an extreme of its parameter range (|log parameter| > 30, or the GPD
xi at -0.9 / 5) has no proper maximum: above u it imitates another shape (typically a power law). It is reported and
flagged (F06) and left out of the automatic selection; fit_selected can still choose it.
Selection: fit_selected if given, else the lowest AIC among the converged, non-boundary candidates.
Output for the simulation: the selected conditional survival inverted on a grid s_k = 10^(-k/K), k = 0 .. K*D;
stage 04 draws X from U by interpolating ln x linearly in log10 U (exact for the Pareto).
"""
import math

import numpy as np
import pandas as pd
from scipy import integrate, optimize, special

DISTS = ("LOGNORMAL", "GAMMA", "WEIBULL", "PARETO", "GPD")
K_PER_DECADE = 50          # grid points per decade of conditional survival
DECADES = 10               # down to s = 1e-10 (MINSTD's smallest uniform is 4.7e-10; beyond: last segment extended)
MIN_UNCENSORED = 30        # fewer uncensored units: F05 flag
ME_BAND = (0.8, 1.25)      # fitted / empirical mean excess outside: F04 flag (same band as check S05)
NEG = -1e300


# ---------------------------------------------------------------------------------------------- distributions
def _ln_logpdf(x, mu, sg):
    z = (np.log(x) - mu) / sg
    return -np.log(x) - math.log(sg) - 0.5 * math.log(2 * math.pi) - 0.5 * z * z


def _ln_logsf(x, mu, sg):
    return special.log_ndtr(-(np.log(x) - mu) / sg)


def _ga_logpdf(x, a, th):
    return (a - 1) * np.log(x) - x / th - special.gammaln(a) - a * math.log(th)


def _ga_logsf(x, a, th):
    with np.errstate(divide="ignore"):
        return np.log(special.gammaincc(a, np.asarray(x, dtype=float) / th))


def _we_logpdf(x, c, lam):
    r = x / lam
    return math.log(c) - math.log(lam) + (c - 1) * np.log(r) - r ** c


def _we_logsf(x, c, lam):
    return -(np.asarray(x, dtype=float) / lam) ** c


def _gpd_core(y, xi, sg):
    z = xi * np.asarray(y, dtype=float) / sg
    ok = z > -1
    return z, ok


def _gpd_logsf(y, xi, sg):
    y = np.asarray(y, dtype=float)
    if abs(xi) < 1e-12:
        return -y / sg
    z, ok = _gpd_core(y, xi, sg)
    out = np.full(y.shape, -np.inf)
    out[ok] = -np.log1p(z[ok]) / xi
    return out


def _gpd_logpdf(y, xi, sg):
    y = np.asarray(y, dtype=float)
    if abs(xi) < 1e-12:
        return -math.log(sg) - y / sg
    z, ok = _gpd_core(y, xi, sg)
    out = np.full(y.shape, -np.inf)
    out[ok] = -math.log(sg) - (1 / xi + 1) * np.log1p(z[ok])
    return out


class Model:
    """A candidate: parameters theta (natural scale); log density / survival of X (not yet conditional on X > u)."""

    def __init__(self, name, u):
        self.name, self.u = name, u
        self.k = 1 if name == "PARETO" else 2
        self.pnames = {"LOGNORMAL": ("mu", "sigma"), "GAMMA": ("shape", "scale"), "WEIBULL": ("shape", "scale"),
                       "PARETO": ("alpha", ""), "GPD": ("xi", "sigma")}[name]

    def logpdf(self, x, th):
        u, n = self.u, self.name
        if n == "LOGNORMAL":
            return _ln_logpdf(x, *th)
        if n == "GAMMA":
            return _ga_logpdf(x, *th)
        if n == "WEIBULL":
            return _we_logpdf(x, *th)
        if n == "PARETO":
            return math.log(th[0]) - np.log(x) - th[0] * np.log(x / u)
        return _gpd_logpdf(np.asarray(x, dtype=float) - u, *th)

    def logsf(self, x, th):
        u, n = self.u, self.name
        if n == "LOGNORMAL":
            return _ln_logsf(x, *th)
        if n == "GAMMA":
            return _ga_logsf(x, *th)
        if n == "WEIBULL":
            return _we_logsf(x, *th)
        if n == "PARETO":
            return -th[0] * np.log(np.asarray(x, dtype=float) / u)
        return _gpd_logsf(np.asarray(x, dtype=float) - u, *th)

    def cond_logsf(self, x, th):
        """log P(X > x | X > u)."""
        return self.logsf(x, th) - float(self.logsf(np.array([self.u]), th)[0])

    def cond_isf(self, s, th):
        """x with P(X > x | X > u) = s (s in (0, 1]); vectorised, computed in log space."""
        s = np.asarray(s, dtype=float)
        u, n = self.u, self.name
        logp = np.log(s) + float(self.logsf(np.array([u]), th)[0])     # unconditional log survival
        if n == "LOGNORMAL":
            mu, sg = th
            return np.maximum(u, np.exp(mu - sg * special.ndtri_exp(logp)))
        if n == "GAMMA":
            a, sc = th
            return np.maximum(u, sc * special.gammainccinv(a, np.exp(logp)))
        if n == "WEIBULL":
            c, lam = th
            return np.maximum(u, lam * (-logp) ** (1 / c))
        if n == "PARETO":
            return u * np.exp(-np.log(s) / th[0])
        xi, sg = th
        if abs(xi) < 1e-12:
            return u - sg * np.log(s)
        return u + sg / xi * np.expm1(-xi * np.log(s))

    # optimisation on an unconstrained scale
    def to_theta(self, z):
        if self.name == "LOGNORMAL":
            return (z[0], math.exp(z[1]))
        if self.name == "GPD":
            return (z[0], math.exp(z[1]))
        return tuple(math.exp(v) for v in z)

    def starts(self, x, unc):
        lx = np.log(x[unc])
        m, sd = float(lx.mean()), max(float(lx.std()), 0.1)
        xm, xv = float(x[unc].mean()), float(x[unc].var()) or float(x[unc].mean()) ** 2
        a0 = alpha_closed(x, unc, self.u)
        if self.name == "LOGNORMAL":
            return [(m, math.log(sd)), (m - 1.0, math.log(sd * 1.5)), (math.log(self.u), math.log(sd * 2))]
        if self.name == "GAMMA":
            return [(math.log(xm * xm / xv), math.log(xv / xm)), (math.log(0.5), math.log(xm)),
                    (math.log(2.0), math.log(xm / 2))]
        if self.name == "WEIBULL":
            c0 = min(max(math.pi / (math.sqrt(6) * sd), 0.05), 20)
            return [(math.log(c0), m + 0.5772 / c0), (math.log(0.5), math.log(xm)), (math.log(1.0), math.log(xm))]
        if self.name == "GPD":
            return [(1 / a0, math.log(self.u / a0)), (0.1, math.log(xm - self.u)), (0.5, math.log(xm - self.u))]
        return []


def alpha_closed(x, unc, u):
    """Single-parameter Pareto MLE with censoring: n_uncensored / sum ln(x / u) (as the native fit)."""
    sl = float(np.log(x / u).sum())
    return unc.sum() / sl if sl > 0 and unc.sum() > 0 else float("nan")


def loglik(model, th, x, unc):
    with np.errstate(all="ignore"):
        ll = model.logpdf(x[unc], th).sum() + model.logsf(x[~unc], th).sum() \
            - len(x) * float(model.logsf(np.array([model.u]), th)[0])
    return float(ll) if np.isfinite(ll) else NEG


BOUND = 30.0


def fit(model, x, unc):
    """MLE. Returns (theta, log L, converged, boundary)."""
    if model.name == "PARETO":
        a = alpha_closed(x, unc, model.u)
        return ((a,), loglik(model, (a,), x, unc), bool(np.isfinite(a)), False)

    def nll(z):
        if model.name == "GPD" and not (-0.9 < z[0] < 5):
            return 1e300
        if model.name != "GPD" and np.any(np.abs(z) > 50):
            return 1e300
        return -loglik(model, model.to_theta(z), x, unc)

    best = None
    opts = {"xatol": 1e-10, "fatol": 1e-11, "maxiter": 20000, "maxfev": 40000}
    for z0 in model.starts(x, unc):
        r = optimize.minimize(nll, np.array(z0, dtype=float), method="Nelder-Mead", options=opts)
        r = optimize.minimize(nll, r.x, method="Nelder-Mead", options=opts)      # restart: polish
        if best is None or r.fun < best.fun:
            best = r
    ok = bool(best.fun < 1e299)
    th = model.to_theta(best.x)
    z = best.x
    edge = (not (-0.89 < z[0] < 4.99) or abs(z[1]) > BOUND) if model.name == "GPD" else bool(np.any(np.abs(z) > BOUND))
    return (th, -best.fun if ok else NEG, ok, bool(edge))


# ---------------------------------------------------------------------------------------------- statistics
def kaplan_meier(x, unc):
    """Event times t_j and S_KM(t_j) (right limit) of the conditional survival above u."""
    t = np.unique(x[unc])
    at_risk = np.array([(x >= tj).sum() for tj in t], dtype=float)
    d = np.array([((x == tj) & unc).sum() for tj in t], dtype=float)
    return t, np.cumprod(1 - d / at_risk)


def ks_stat(model, th, x, unc):
    t, skm = kaplan_meier(x, unc)
    if len(t) == 0:
        return float("nan")
    sf = np.exp(model.cond_logsf(t, th))
    left = np.concatenate([[1.0], skm[:-1]])
    return float(max(np.abs(skm - sf).max(), np.abs(left - sf).max()))


def km_at(x, unc, pts):
    t, skm = kaplan_meier(x, unc)
    out = []
    for p in pts:
        i = np.searchsorted(t, p, side="right") - 1      # S_KM(p) = product over event times <= p ... survival > p
        out.append(1.0 if i < 0 else float(skm[i]))
    return np.array(out)


def mean_excess(model, th, x, caps, ap, mx):
    """Fitted E[min(X, cap, M) - AP | min(X, cap) > AP], caps of the fit units; and the data's mean excess."""
    above = x > ap
    emp = float((np.minimum(x[above], mx) - ap).mean()) if above.any() else float("nan")
    cs = np.minimum(caps[caps > ap], mx)
    if len(cs) == 0 or not above.any():
        return emp, float("nan")
    s_ap = math.exp(float(model.cond_logsf(np.array([ap]), th)[0]))
    if s_ap <= 0:
        return emp, float("nan")
    num = 0.0
    for c, m in zip(*np.unique(cs, return_counts=True)):
        if c > ap:
            f = lambda v: math.exp(float(model.cond_logsf(np.array([v]), th)[0]))  # noqa: E731
            num += m * integrate.quad(f, ap, c, limit=200, epsabs=0, epsrel=1e-10)[0]
    return emp, num / (len(cs) * s_ap)


def table(model, th):
    k = np.arange(K_PER_DECADE * DECADES + 1)
    lx = np.log(model.cond_isf(10.0 ** (-k / K_PER_DECADE), th))
    return pd.DataFrame({"k": k[:-1].astype("int64"), "lx0": lx[:-1], "lx1": lx[1:]})


# ---------------------------------------------------------------------------------------------- driver
def run(units, ctx, thr):
    """units: adj, adj_uncapped, cap (units above u); ctx: one row u_fit, max_observed, fit_candidates, fit_selected;
    thr: t, t_label, is_ap (thresholds above u). Returns candidates, mean_excess, survival, sev_table, flags."""
    c0 = ctx.iloc[0]
    u, mx = float(c0["u_fit"]), float(c0["max_observed"])
    cand = [d.strip().upper() for d in str(c0["fit_candidates"] or "").split(",") if d.strip()]
    sel_in = "" if pd.isna(c0["fit_selected"]) else str(c0["fit_selected"]).strip().upper()
    units = units.sort_values(["adj", "cap", "adj_uncapped"], kind="mergesort")
    x = units["adj"].to_numpy(dtype=float)
    caps = units["cap"].to_numpy(dtype=float)
    unc = units["adj_uncapped"].to_numpy(dtype=float) < caps
    flags = []

    def flag(code, sev, item, msg):
        flags.append((code, sev, item, msg))

    for d in cand:
        if d not in DISTS:
            flag("F02", "STOP", d, "unknown candidate in fit_candidates (" + ", ".join(DISTS) + ")")
    cand = [d for d in DISTS if d in cand]
    if int(unc.sum()) < MIN_UNCENSORED:
        flag("F05", "STOP" if unc.sum() == 0 else "FLAG", f"u = {u:,.0f}",
             f"{int(unc.sum())} uncensored units above u: the fits rest on few points")
    rows, fits = [], {}
    if unc.sum() > 0:
        for d in cand:
            m = Model(d, u)
            th, ll, okk, edge = fit(m, x, unc)
            fits[d] = (m, th, okk)
            if not okk:
                flag("F01", "FLAG", d, "fit did not converge: candidate left out of the selection")
            elif edge:
                flag("F06", "INFO", d, "boundary fit (parameters at an extreme: no proper maximum above u); "
                                       "left out of the automatic selection")
            rows.append({"dist": d, "n_params": m.k, "p1_name": m.pnames[0], "p1": th[0], "p2_name": m.pnames[1],
                         "p2": th[1] if m.k == 2 else np.nan, "loglik": ll if okk else np.nan,
                         "aic": 2 * m.k - 2 * ll if okk else np.nan,
                         "bic": m.k * math.log(len(x)) - 2 * ll if okk else np.nan,
                         "ks": ks_stat(m, th, x, unc) if okk else np.nan, "converged": int(okk),
                         "boundary": int(edge)})
    cdf = pd.DataFrame(rows, columns=["dist", "n_params", "p1_name", "p1", "p2_name", "p2", "loglik", "aic", "bic",
                                      "ks", "converged", "boundary"])
    good = cdf[(cdf.converged == 1) & (cdf.boundary == 0)]
    selected = ""
    if sel_in:
        if sel_in not in fits or not fits[sel_in][2]:
            flag("F02", "STOP", sel_in, "fit_selected is not a converged candidate")
        else:
            selected = sel_in
    elif len(good):
        selected = good.sort_values(["aic", "dist"]).dist.iloc[0]
    for col in ("aic", "bic", "ks"):
        cdf["rank_" + col] = cdf[col].rank(method="min").astype("float64")
    cdf["selected"] = (cdf.dist == selected).astype("int64")
    if selected and len(good) > 1:
        for col, lab in (("bic", "BIC"), ("ks", "KS")):
            best = good.sort_values([col, "dist"]).dist.iloc[0]
            if best != selected:
                flag("F03", "INFO", selected, f"lowest {lab} is {best}, selected {selected}")
    # mean excess at the APs, survival at the thresholds
    me_rows, sv_rows = [], []
    aps = sorted(set(float(t) for t, a in zip(thr["t"], thr["is_ap"]) if int(a) == 1 and float(t) >= u))
    pts = thr.sort_values("t")
    skm = km_at(x, unc, pts["t"].to_numpy(dtype=float))
    for d in cand:
        if d not in fits or not fits[d][2]:
            continue
        m, th, _ = fits[d]
        for ap in aps:
            emp, fv = mean_excess(m, th, x, caps, ap, mx)
            me_rows.append({"dist": d, "ap": ap, "n_above": int((x > ap).sum()), "mean_excess_empirical": emp,
                            "mean_excess_fitted": fv, "ratio": fv / emp if emp and emp == emp and fv == fv else np.nan})
        sf = np.exp(m.cond_logsf(pts["t"].to_numpy(dtype=float), th))
        for (t, lab), s_k, s_f in zip(zip(pts["t"], pts["t_label"]), skm, sf):
            sv_rows.append({"dist": d, "t": float(t), "t_label": str(lab), "s_km": s_k, "s_fitted": float(s_f)})
    me = pd.DataFrame(me_rows, columns=["dist", "ap", "n_above", "mean_excess_empirical", "mean_excess_fitted", "ratio"])
    sv = pd.DataFrame(sv_rows, columns=["dist", "t", "t_label", "s_km", "s_fitted"])
    for col in ("me_ratio_min", "me_ratio_max"):
        cdf[col] = np.nan
    for i, d in enumerate(cdf.dist):
        r = me[me.dist == d].ratio.dropna()
        if len(r):
            cdf.loc[i, "me_ratio_min"], cdf.loc[i, "me_ratio_max"] = r.min(), r.max()
    for _, r in me[(me.dist == selected) & me.ratio.notna()].iterrows():
        if not ME_BAND[0] <= r.ratio <= ME_BAND[1]:
            flag("F04", "FLAG", f"{selected} AP {r.ap:,.0f}",
                 f"fitted / empirical mean excess {r.ratio:.2f} (outside {ME_BAND[0]}-{ME_BAND[1]})")
    for col, val in (("u_fit", u), ("max_observed", mx), ("n_fit", float(len(x))), ("n_uncensored", float(unc.sum()))):
        cdf[col] = val
    if selected:
        m, th, _ = fits[selected]
        tab = table(m, th)
        tab["dist"] = selected
    else:
        tab = pd.DataFrame({"k": pd.Series([], dtype="int64"), "lx0": pd.Series([], dtype="float64"),
                            "lx1": pd.Series([], dtype="float64"), "dist": pd.Series([], dtype="object")})
        if not any(f[1] == "STOP" for f in flags):
            flag("F02", "STOP", "-", "no candidate converged")
    fl = pd.DataFrame(flags, columns=["code", "severity", "item", "message"]).astype(str)
    me = me.astype({c: "float64" for c in me.columns if c != "dist"})
    sv = sv.astype({c: "float64" for c in sv.columns if c not in ("dist", "t_label")})
    num = [c for c in cdf.columns if c not in ("dist", "p1_name", "p2_name")]
    cdf = cdf.astype({c: "float64" for c in num})
    return cdf, me, sv, tab, fl
