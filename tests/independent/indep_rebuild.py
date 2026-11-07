#!/usr/bin/env python3
"""
Clean-room rebuild of the per-person XoL simulation from SPEC.md + param_pack.json only.

Outputs per variant (in the variant folder):
  independent_iterations.csv  option_id, iter, recovery, recovery_aboveM  (seeded exact run)
  independent_summary.csv     one row per layer: exact-run stats, own-RNG Monte Carlo stats, closed form

Usage: python3 -I indep_rebuild.py <variant> [<variant> ...]
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import integrate
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent
UNLIMITED = 1e15
MC_ITER = 200_000
MC_SEED = 20261107

# ----------------------------------------------------------------------------- MINSTD
A = 48271
M = 2**31 - 1


def minstd_start(seed: int, j: int) -> int:
    s0 = 1 + (seed % (M - 1))
    return (s0 * pow(A, j * 2**29, M)) % M


def minstd_stream(seed: int, j: int, n: int) -> np.ndarray:
    """n uniforms state/m from stream j (state_1 = a*start_j mod m, ...). Pure int arithmetic."""
    s = minstd_start(seed, j)
    out = np.empty(n, dtype=np.int64)
    for i in range(n):
        s = (A * s) % M
        out[i] = s
    return out.astype(np.float64) / M


def verify_minstd() -> dict:
    """Self-checks of the generator constants and the a^(2^29) jump."""
    notes = {}
    # a is 48271, not the Park-Miller 16807
    assert A == 48271 and A != 16807
    # a^(2^29) by 29 explicit modular squarings vs pow() with 3-arg modular exponent
    x = A
    for _ in range(29):
        x = (x * x) % M
    assert x == pow(A, 2**29, M)
    notes["a_pow_2_29_mod_m"] = x
    # Fermat / full-period check: a^(m-1) == 1 and a is a primitive root mod m
    assert pow(A, M - 1, M) == 1
    for p in (2, 3, 7, 11, 31, 151, 331):            # prime factors of m-1 = 2*3^2*7*11*31*151*331
        assert pow(A, (M - 1) // p, M) != 1
    notes["primitive_root"] = True
    # Jump consistency: start_j = s0 * a^(j 2^29); so start_2 = start_1 * a^(2^29) and start_3 = start_2 * a^(2^29)
    s0 = 1 + (12345 % (M - 1))
    st = [minstd_start(12345, j) for j in (1, 2, 3)]
    assert st[0] == (s0 * x) % M and st[1] == (st[0] * x) % M and st[2] == (st[1] * x) % M
    # The three streams are disjoint windows of the single full-period cycle, 2^29 apart:
    # stepping start_1 forward 2^29 times would reach start_2 (checked algebraically above, not by looping).
    # Show that using 16807 would change the very first uniform
    s_48271 = (48271 * st[0]) % M
    s_16807 = (16807 * st[0]) % M
    assert s_48271 != s_16807
    notes["first_state_stream1_seed12345_a48271"] = s_48271
    notes["first_state_stream1_seed12345_a16807_would_be"] = s_16807
    notes["start_j_seed12345"] = st
    return notes


# ----------------------------------------------------------------------------- severity model
class Severity:
    """Survival S(x | X > u) and its inverse for PARETO / GPD / LOGNORMAL."""

    def __init__(self, dist: str, u: float, p1: float, p2: float):
        self.dist, self.u, self.p1, self.p2 = dist, u, p1, p2
        if dist == "LOGNORMAL":
            self.mu, self.sigma = p1, p2
            self.zu = (math.log(u) - self.mu) / self.sigma
            self.logS_u = norm.logsf(self.zu)

    def sf(self, x):
        """Conditional survival; = 1 for x < u (every claim is above u)."""
        x = np.asarray(x, dtype=float)
        u = self.u
        if self.dist == "PARETO":
            s = np.where(x >= u, (u / np.maximum(x, u)) ** self.p1, 1.0)
        elif self.dist == "GPD":
            xi, sg = self.p1, self.p2
            s = np.where(x >= u, (1.0 + xi * (np.maximum(x, u) - u) / sg) ** (-1.0 / xi), 1.0)
        elif self.dist == "LOGNORMAL":
            z = (np.log(np.maximum(x, u)) - self.mu) / self.sigma
            s = np.where(x >= u, np.exp(norm.logsf(z) - self.logS_u), 1.0)
        else:
            raise ValueError(self.dist)
        return s

    def isf(self, p):
        """x with S(x | X > u) = p, p in (0,1]."""
        p = np.asarray(p, dtype=float)
        u = self.u
        if self.dist == "PARETO":
            return u * p ** (-1.0 / self.p1)
        if self.dist == "GPD":
            xi, sg = self.p1, self.p2
            return u + sg / xi * (p ** (-xi) - 1.0)
        if self.dist == "LOGNORMAL":
            # log S_LN(x) = log p + log S_LN(u); invert the standard normal log-survival
            logS_x = np.log(p) + self.logS_u
            z = norm.isf(np.exp(logS_x))
            return np.exp(self.mu + self.sigma * z)
        raise ValueError(self.dist)

    def grid(self):
        """x_k, k = 0..500, with S(x_k) = 10^(-k/50); returns lx0 (k=0..499), lx1 (k=1..500)."""
        k = np.arange(501)
        xk = self.isf(10.0 ** (-k / 50.0))
        lx = np.log(xk)
        return xk, lx[:-1], lx[1:]


def build_severity(pp: dict) -> Severity:
    u = float(pp["u_fit"])
    if pp["severity_source"] == "NATIVE":
        return Severity("PARETO", u, float(pp["pareto_alpha_native"]), float("nan"))
    sc = pp["selected_candidate"]
    p1 = float(sc["p1"])
    p2 = float(sc["p2"]) if sc["p2"] not in ("", None) else float("nan")
    return Severity(sc["dist"], u, p1, p2)


# ----------------------------------------------------------------------------- frequency
def count_table(lam: float, phi: float, freq_dist: str) -> np.ndarray:
    kmax = math.ceil(lam + 10 * math.sqrt(lam * (phi if freq_dist == "NEGBIN" else 1.0)) + 20)
    p = np.empty(kmax + 1)
    if freq_dist == "POISSON":
        p[0] = math.exp(-lam)
        for k in range(1, kmax + 1):
            p[k] = p[k - 1] * lam / k
    elif freq_dist == "NEGBIN":
        r = lam / (phi - 1.0)
        q = 1.0 / phi
        p[0] = q ** r
        for k in range(1, kmax + 1):
            p[k] = p[k - 1] * (r + k - 1) / k * (1.0 - q)
    else:
        raise ValueError(freq_dist)
    return np.cumsum(p)          # CDF(k), no renormalisation


def counts_from_uniforms(cdf: np.ndarray, U: np.ndarray) -> np.ndarray:
    # N = #{k : CDF(k) < U}  == searchsorted(cdf, U, side='left')
    return np.searchsorted(cdf, U, side="left").astype(np.int64)


# ----------------------------------------------------------------------------- layer loss
def layer_xl(claim: np.ndarray, cap: np.ndarray, L: dict) -> np.ndarray:
    ap = float(L["ap"])
    cop = 0.0 if L["co_participation_pct"] in (None, "") else float(L["co_participation_pct"])
    if L["limit_basis"] == "TO_BENEFIT_LIMIT":
        lim = np.where(cap >= UNLIMITED, UNLIMITED, cap - ap)
    elif L["limit_basis"] == "AMOUNT":
        lim = np.full_like(claim, UNLIMITED if L["limit"] in (None, "") else float(L["limit"]))
    else:
        raise ValueError(L["limit_basis"])
    over = claim - ap
    xl = np.where(over <= 0, 0.0, np.where(lim > 0, np.minimum(over, lim), 0.0))
    return xl * (1.0 - cop)


def annual_recovery(xl: np.ndarray, iter_idx: np.ndarray, n_iter: int, L: dict) -> np.ndarray:
    aad = 0.0 if L["aad"] in (None, "") else float(L["aad"])
    aal = UNLIMITED if L["aal"] in (None, "") else float(L["aal"])
    tot = np.bincount(iter_idx, weights=xl, minlength=n_iter)    # iterations with no claims -> 0
    return np.minimum(np.maximum(tot - aad, 0.0), aal)


def simulate_layers(claim, cap, M_obs, iter_idx, n_iter, layers):
    """Return dict option_id -> (recovery, recovery_aboveM), vectorised over claims."""
    claim_bM = np.minimum(claim, M_obs)
    out = {}
    for L in layers:
        rec = annual_recovery(layer_xl(claim, cap, L), iter_idx, n_iter, L)
        rec_bM = annual_recovery(layer_xl(claim_bM, cap, L), iter_idx, n_iter, L)
        out[L["option_id"]] = (rec, rec - rec_bM)
    return out


# ----------------------------------------------------------------------------- statistics
def nearest_rank(x_sorted: np.ndarray, q: float) -> float:
    n = len(x_sorted)
    pos = max(1, math.ceil(q * n))
    return float(x_sorted[pos - 1])


def stats(rec: np.ndarray) -> dict:
    s = np.sort(rec)
    n = len(rec)
    return dict(
        mean=float(rec.mean()),
        sd_sample=float(rec.std(ddof=1)),
        sd_pop=float(rec.std(ddof=0)),
        p_zero=float((rec == 0).mean()),
        p50=nearest_rank(s, 0.50), p75=nearest_rank(s, 0.75), p90=nearest_rank(s, 0.90),
        p95=nearest_rank(s, 0.95), p99=nearest_rank(s, 0.99), p995=nearest_rank(s, 0.995),
        se_mean=float(rec.std(ddof=1) / math.sqrt(n)),
    )


# ----------------------------------------------------------------------------- closed form
def closed_form(sev: Severity, caps: np.ndarray, lam: float, M_obs: float, L: dict):
    ap = float(L["ap"])
    cop = 0.0 if L["co_participation_pct"] in (None, "") else float(L["co_participation_pct"])
    tot, tot_bM = 0.0, 0.0
    f = lambda x: float(sev.sf(x))
    for c in caps:
        if L["limit_basis"] == "TO_BENEFIT_LIMIT":
            lim = UNLIMITED if c >= UNLIMITED else c - ap
        else:
            lim = UNLIMITED if L["limit"] in (None, "") else float(L["limit"])
        if lim <= 0:
            continue
        hi = min(ap + lim, c)
        if hi > ap:
            pts = [p for p in (sev.u, M_obs) if ap < p < hi]
            tot += integrate.quad(f, ap, hi, points=pts or None, limit=200)[0]
        hi2 = min(hi, M_obs)
        if hi2 > ap:
            pts = [p for p in (sev.u,) if ap < p < hi2]
            tot_bM += integrate.quad(f, ap, hi2, points=pts or None, limit=200)[0]
    n_fit = len(caps)
    return lam * tot / n_fit * (1 - cop), lam * tot_bM / n_fit * (1 - cop)


# ----------------------------------------------------------------------------- runs
def exact_run(pp: dict, sev: Severity):
    n = int(pp["iterations"])
    seed = int(pp["seed"])
    lam, phi, fd = float(pp["lambda"]), float(pp["phi"]), pp["freq_dist"]
    caps = np.asarray(pp["caps_in_order"], dtype=float)
    n_fit = len(caps)
    assert n_fit == int(pp["n_fit"])
    M_obs = float(pp["max_observed"])

    cdf = count_table(lam, phi, fd)
    U1 = minstd_stream(seed, 1, n)                 # one per iteration, 1..n
    N = counts_from_uniforms(cdf, U1)
    total = int(N.sum())
    U2 = minstd_stream(seed, 2, total)             # one per claim, (iteration, claim) order
    U3 = minstd_stream(seed, 3, total)
    # iteration index (0-based) of each claim, in (iteration, claim index) order
    iter_idx = np.repeat(np.arange(n), N)

    xk, lx0, lx1 = sev.grid()
    t = -50.0 * np.log10(U2)
    k = np.minimum(np.floor(t).astype(np.int64), 499)
    lnX = lx0[k] + (t - k) * (lx1[k] - lx0[k])
    X = np.exp(lnX)
    cap = caps[np.floor(U3 * n_fit).astype(np.int64)]
    claim = np.minimum(X, cap)
    res = simulate_layers(claim, cap, M_obs, iter_idx, n, pp["layers"])
    diag = dict(N_total=total, N_min=int(N.min()), N_max=int(N.max()), N_mean=float(N.mean()),
                kmax=len(cdf) - 1, cdf_tail=float(cdf[-1]), X_max=float(X.max()),
                t_max=float(t.max()), U1_first=float(U1[0]), U2_first=float(U2[0]), U3_first=float(U3[0]),
                n_zero_iter=int((N == 0).sum()))
    return res, diag, (xk, lx0, lx1)


def mc_run(pp: dict, sev: Severity, n_mc: int = MC_ITER, seed: int = MC_SEED):
    rng = np.random.default_rng(seed)
    lam, phi, fd = float(pp["lambda"]), float(pp["phi"]), pp["freq_dist"]
    caps = np.asarray(pp["caps_in_order"], dtype=float)
    M_obs = float(pp["max_observed"])
    if fd == "POISSON":
        N = rng.poisson(lam, n_mc)
    else:
        r = lam / (phi - 1.0)
        q = 1.0 / phi
        N = rng.negative_binomial(r, q, n_mc)      # P(0) = q^r, mean = lam
    total = int(N.sum())
    iter_idx = np.repeat(np.arange(n_mc), N)
    U = rng.random(total)
    X = sev.isf(U)                                  # exact inverse of the conditional survival
    cap = caps[rng.integers(0, len(caps), total)]
    claim = np.minimum(X, cap)
    return simulate_layers(claim, cap, M_obs, iter_idx, n_mc, pp["layers"])


def run_variant(variant: str) -> pd.DataFrame:
    vdir = ROOT / variant
    pp = json.loads((vdir / "param_pack.json").read_text())
    sev = build_severity(pp)
    t0 = time.time()
    exact, diag, (xk, lx0, lx1) = exact_run(pp, sev)
    t1 = time.time()
    mc = mc_run(pp, sev)
    t2 = time.time()

    # grid check for Pareto: x_k = u * 10^(k/(50 alpha))
    if sev.dist == "PARETO":
        kk = np.arange(501)
        ref = sev.u * 10.0 ** (kk / (50.0 * sev.p1))
        assert np.allclose(xk, ref, rtol=1e-12), "Pareto grid mismatch"
    # generic grid check: S(x_k) == 10^(-k/50)
    assert np.allclose(sev.sf(xk), 10.0 ** (-np.arange(501) / 50.0), rtol=1e-8)

    n = int(pp["iterations"])
    it_rows, sum_rows = [], []
    caps = np.asarray(pp["caps_in_order"], dtype=float)
    for L in pp["layers"]:
        oid = L["option_id"]
        rec, rec_aM = exact[oid]
        it_rows.append(pd.DataFrame({"option_id": oid, "iter": np.arange(1, n + 1),
                                     "recovery": rec, "recovery_aboveM": rec_aM}))
        ex = stats(rec)
        m_rec, _ = mc[oid]
        ms = stats(m_rec)
        cf, cf_bM = closed_form(sev, caps, float(pp["lambda"]), float(pp["max_observed"]), L)
        sum_rows.append(dict(
            option_id=oid, label=L["label"],
            exact_mean=ex["mean"], exact_sd_sample=ex["sd_sample"], exact_sd_pop=ex["sd_pop"],
            exact_p_zero=ex["p_zero"], exact_p50=ex["p50"], exact_p90=ex["p90"], exact_p99=ex["p99"],
            exact_p995=ex["p995"], exact_mean_aboveM=float(rec_aM.mean()),
            mc_mean=ms["mean"], mc_sd_sample=ms["sd_sample"], mc_p50=ms["p50"], mc_p90=ms["p90"],
            mc_p99=ms["p99"], mc_p_zero=ms["p_zero"],
            cf_mean=cf, cf_mean_belowM=cf_bM,
            # diagnostics (not required, kept for the report)
            exact_se_mean=ex["se_mean"], mc_se_mean=ms["se_mean"],
            z_exact_vs_mc=(ex["mean"] - ms["mean"]) / math.sqrt(ex["se_mean"] ** 2 + ms["se_mean"] ** 2),
            z_cf_vs_mc=(cf - ms["mean"]) / ms["se_mean"],
            z_cf_vs_exact=(cf - ex["mean"]) / ex["se_mean"],
            exact_cov=ex["sd_sample"] / ex["mean"] if ex["mean"] else float("nan"),
            exact_p75=ex["p75"], exact_p95=ex["p95"],
            mc_mean_aboveM=float(mc[oid][1].mean()),
        ))
    pd.concat(it_rows, ignore_index=True).to_csv(vdir / "independent_iterations.csv", index=False,
                                                 float_format="%.6f")
    summ = pd.DataFrame(sum_rows)
    summ.to_csv(vdir / "independent_summary.csv", index=False)
    diag.update(dict(variant=variant, t_exact=t1 - t0, t_mc=t2 - t1, t_cf=time.time() - t2,
                     sev=f"{sev.dist} u={sev.u} p1={sev.p1} p2={sev.p2}", freq=pp["freq_dist"],
                     x_1=float(xk[1]), x_50=float(xk[50]), x_500=float(xk[500])))
    (vdir / "independent_diag.json").write_text(json.dumps(diag, indent=1))
    print(f"[{variant}] exact {t1-t0:.1f}s mc {t2-t1:.1f}s | N_total={diag['N_total']} zero_iters={diag['n_zero_iter']}")
    print(summ[["option_id", "label", "exact_mean", "mc_mean", "cf_mean", "z_exact_vs_mc", "z_cf_vs_mc"]]
          .to_string(index=False))
    return summ


if __name__ == "__main__":
    notes = verify_minstd()
    (ROOT / "minstd_verification.json").write_text(json.dumps(notes, indent=1))
    print("MINSTD checks passed:", notes)
    for v in sys.argv[1:]:
        run_variant(v)
