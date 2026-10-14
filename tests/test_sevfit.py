"""tools/python/sevfit.py (stage 04a) against independent references.

- Log-likelihood: re-computed with scipy.stats distributions (lognorm, gamma, weibull_min, genpareto, pareto), left
  truncation at u and right censoring at caps, at the fitted parameters.
- Recovery: samples of each family drawn above u (seeded), censored at a cap mix; the MLE lands near the true
  parameters, its log-likelihood is at least the true parameters' and is a local maximum.
- Selection: the generating family is within 2 AIC points of the best.
- Grid: the conditional survival (scipy.stats) at each grid point equals 10^(-k/K).
- KS: without censoring, equals scipy.stats.kstest against the conditional CDF.
- Mean excess: equals the closed form for a Pareto with one cap.
- Flags: unknown candidate / bad fit_selected stop (F02); boundary fits (F06); few points (F05).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/python"))
import sevfit as S  # noqa: E402

U = 100_000.0
CAPS = np.array([250_000.0, 500_000.0, 1_000_000.0, 1e15])
results = []


def ok(name, cond):
    results.append((name, bool(cond)))


def scipy_dist(name, th):
    if name == "LOGNORMAL":
        return stats.lognorm(s=th[1], scale=math.exp(th[0]))
    if name == "GAMMA":
        return stats.gamma(a=th[0], scale=th[1])
    if name == "WEIBULL":
        return stats.weibull_min(c=th[0], scale=th[1])
    if name == "PARETO":
        return stats.pareto(b=th[0], scale=U)
    return stats.genpareto(c=th[0], loc=U, scale=th[1])


def ll_ref(name, th, x, unc):
    d = scipy_dist(name, th)
    return float(d.logpdf(x[unc]).sum() + d.logsf(x[~unc]).sum() - len(x) * d.logsf(U))


def sample(name, th, n, rng):
    """X | X > u by inverse transform of the conditional survival, then capped at a random cap of the mix."""
    d = scipy_dist(name, th)
    s_u = d.sf(U)
    v = rng.uniform(size=n)
    x = d.isf(v * s_u)
    cap = CAPS[rng.integers(0, len(CAPS), size=n)]
    unc = x < cap
    return np.minimum(x, cap), x, cap, unc


TRUE = {"LOGNORMAL": (10.8, 1.1), "GAMMA": (1.6, 90_000.0), "WEIBULL": (0.7, 60_000.0), "PARETO": (1.7,),
        "GPD": (0.4, 70_000.0)}


def recovery():
    rng = np.random.default_rng(20261014)
    for name, th0 in TRUE.items():
        x, x_unc, cap, unc = sample(name, th0, 4000, rng)
        m = S.Model(name, U)
        th, ll, conv, edge = S.fit(m, x, unc)
        ok(f"{name}: converged, proper maximum", conv and not edge)
        ok(f"{name}: log L = scipy.stats reference ({ll:.4f})",
           abs(ll - ll_ref(name, th, x, unc)) < 1e-6 * abs(ll))
        ok(f"{name}: log L(MLE) >= log L(true) ({ll - ll_ref(name, th0, x, unc):.2f})",
           ll >= ll_ref(name, th0, x, unc) - 1e-6)
        rel = [abs(a / b - 1) for a, b in zip(th, th0)]
        ok(f"{name}: parameters near the truth ({', '.join(f'{a:.4g}' for a in th)} vs "
           f"{', '.join(f'{b:.4g}' for b in th0)})", max(rel) < 0.15)
        local = True
        for i in range(len(th)):
            for h in (1e-4, -1e-4):
                t2 = list(th)
                t2[i] = th[i] * (1 + h) if th[i] != 0 else h
                local &= S.loglik(m, tuple(t2), x, unc) <= ll + 1e-7
        ok(f"{name}: local maximum", local)
        # selection: the true family is within 2 AIC points of the best
        units = pd.DataFrame({"adj": x, "adj_uncapped": x_unc, "cap": cap})
        ctx = pd.DataFrame([{"u_fit": U, "max_observed": x.max(), "fit_candidates": ",".join(S.DISTS),
                             "fit_selected": None}])
        thr = pd.DataFrame({"t": [U, 150_000.0, 250_000.0], "t_label": ["AP 1", "AP 2", "250,000"], "is_ap": [1, 1, 0]})
        cdf, me, sv, tab, fl = S.run(units, ctx, thr)
        good = cdf[(cdf.converged == 1) & (cdf.boundary == 0)]
        gap = cdf.set_index("dist").aic[name] - good.aic.min()
        ok(f"{name} sample: true family within 2 AIC of the best (gap {gap:.2f}; selected "
           f"{cdf[cdf.selected == 1].dist.iloc[0]})", gap <= 2)


def grid():
    for name, th0 in TRUE.items():
        m = S.Model(name, U)
        tab = S.table(m, th0)
        d = scipy_dist(name, th0)
        k = tab.k.to_numpy()[:400]
        s_ref = d.sf(np.exp(tab.lx0.to_numpy()[:400])) / d.sf(U)
        ok(f"{name}: grid conditional survival = 10^(-k/K) (scipy.stats), k < 400",
           np.allclose(s_ref, 10.0 ** (-k / S.K_PER_DECADE), rtol=1e-7, atol=0))
        ok(f"{name}: grid rows {len(tab)}, lx1 = next lx0", len(tab) == S.K_PER_DECADE * S.DECADES and
           np.allclose(tab.lx1.to_numpy()[:-1], tab.lx0.to_numpy()[1:]))


def ks_and_me():
    rng = np.random.default_rng(7)
    th = (10.8, 1.1)
    d = scipy_dist("LOGNORMAL", th)
    x = d.isf(rng.uniform(size=2000) * d.sf(U))
    unc = np.ones(len(x), dtype=bool)
    m = S.Model("LOGNORMAL", U)
    ref = stats.kstest(x, lambda v: 1 - d.sf(v) / d.sf(U)).statistic
    ok(f"KS without censoring = scipy.stats.kstest ({S.ks_stat(m, th, x, unc):.6f} vs {ref:.6f})",
       abs(S.ks_stat(m, th, x, unc) - ref) < 1e-10)
    # Pareto, one cap b: fitted E[min(X, b) - a | X > a] = a / (alpha - 1) (1 - (a / b)^(alpha - 1))
    a_, b_, al = 150_000.0, 700_000.0, 1.7
    mp = S.Model("PARETO", U)
    xs = np.array([U * 1.5, 200_000.0, 300_000.0])
    caps = np.full(3, b_)
    emp, fv = S.mean_excess(mp, (al,), xs, caps, a_, 1e15)
    ok(f"Pareto mean excess = closed form ({fv:,.2f})",
       abs(fv - a_ / (al - 1) * (1 - (a_ / b_) ** (al - 1))) < 1e-6 * fv)
    ok("empirical mean excess = mean of (x - AP) above AP", abs(emp - np.mean([200_000.0 - a_, 300_000.0 - a_])) < 1e-9)


def flags():
    rng = np.random.default_rng(3)
    x, x_unc, cap, unc = sample("PARETO", (1.6,), 600, rng)
    units = pd.DataFrame({"adj": x, "adj_uncapped": x_unc, "cap": cap})
    thr = pd.DataFrame({"t": [U], "t_label": ["AP 1"], "is_ap": [1]})

    def run(cands, sel=None, units_=units):
        ctx = pd.DataFrame([{"u_fit": U, "max_observed": units_.adj.max(), "fit_candidates": cands,
                             "fit_selected": sel}])
        return S.run(units_, ctx, thr)
    cdf, me, sv, tab, fl = run("LOGNORMAL,PARETO,FOO")
    ok("unknown candidate: F02 STOP", ((fl.code == "F02") & (fl.severity == "STOP") & (fl.item == "FOO")).any())
    cdf, me, sv, tab, fl = run("LOGNORMAL,PARETO", "GPD")
    ok("fit_selected not among the candidates: F02 STOP, no grid", ((fl.code == "F02") & (fl.severity == "STOP")).any()
       and len(tab) == 0)
    cdf, me, sv, tab, fl = run(",".join(S.DISTS))
    sel = cdf[cdf.selected == 1].dist.tolist()
    ok(f"Pareto data: boundary fits flagged F06 ({sorted(fl[fl.code == 'F06'].item)}), never auto-selected "
       f"(selected {sel})", (fl.code == "F06").any() and len(sel) == 1 and
       cdf.set_index("dist").boundary[sel[0]] == 0)
    cdf, me, sv, tab, fl = run("PARETO", units_=units.iloc[:12])
    ok("few uncensored points: F05", (fl.code == "F05").any())
    cdf, me, sv, tab, fl = run("PARETO", "PARETO")
    ok("fit_selected honoured; grid of the selected distribution",
       cdf.set_index("dist").selected["PARETO"] == 1 and (tab.dist == "PARETO").all())


def main() -> int:
    recovery()
    grid()
    ks_and_me()
    flags()
    for n, good in results:
        print(f"{'PASS' if good else 'FAIL'}  {n}")
    return 0 if all(g for _, g in results) else 1


if __name__ == "__main__":
    sys.exit(main())
